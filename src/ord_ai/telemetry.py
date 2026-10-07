"""Replay-comparison records the bridge writes beside each state export; policies ignore them."""

from pathlib import Path
from typing import Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from ord_ai.catalog import EVOLUTION_WISP, RANDOM_WISP
from ord_ai.protocol import PRELOAD, VERSION, ProtocolError

TELEMETRY_FILE: Final = "telemetry.txt"
TIME_FIELDS: Final = 10
POSITION_FIELDS: Final = 8
END_FIELDS: Final = 5
WISPS: Final = frozenset({RANDOM_WISP, EVOLUTION_WISP})

Placement = tuple[int, float, float, int]


class TrackedUnit(BaseModel):
    """One living, visible player-0 unit at the export."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    handle: int
    raw_id: Annotated[int, Field(gt=0)]
    x: float
    y: float
    order: int


class Telemetry(BaseModel):
    """Game time, map RNG state, evolution slot and unit positions of one export."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    episode: int
    sequence: int
    game_time: float
    gv: int
    gv_main: int
    evolution: int
    evolution_handle: int
    units: tuple[TrackedUnit, ...] = ()

    def placements(self) -> list[Placement]:
        """Return ``[type, x, y, order]`` per unit, the report §8-3 ``units`` field."""
        return [(unit.raw_id, unit.x, unit.y, unit.order) for unit in self.units]


def parse_telemetry(text: str) -> Telemetry | None:
    """Parse a complete record; return None while the file is still being written."""
    rows = [
        value.split("|")
        for value in (match.group(1) for match in PRELOAD.finditer(text))
        if value.startswith("ORD_AI|")
    ]
    if not rows or rows[-1][2:3] != ["TEND"]:
        return None
    if any(row[:2] != ["ORD_AI", str(VERSION)] for row in rows):
        raise ProtocolError(detail="Unsupported bridge telemetry version")
    head, ending = rows[0], rows[-1]
    if len(head) != TIME_FIELDS or head[2:3] != ["TIME"]:
        raise ProtocolError(detail="Telemetry must start with exactly one complete TIME record")
    if len(ending) != END_FIELDS or ending[3:5] != head[3:5]:
        raise ProtocolError(detail="Telemetry end does not match its TIME record")
    if any(len(row) != POSITION_FIELDS or row[2:3] != ["POS"] for row in rows[1:-1]):
        raise ProtocolError(detail="Only complete POS records may appear inside telemetry")
    return Telemetry(
        episode=int(head[3]),
        sequence=int(head[4]),
        game_time=float(head[5]),
        gv=int(head[6]),
        gv_main=int(head[7]),
        evolution=int(head[8]),
        evolution_handle=int(head[9]),
        units=tuple(
            TrackedUnit(
                handle=int(row[3]),
                raw_id=int(row[4]),
                x=float(row[5]),
                y=float(row[6]),
                order=int(row[7]),
            )
            for row in rows[1:-1]
        ),
    )


def read_telemetry(directory: Path, episode: int, sequence: int) -> Telemetry | None:
    """Read the record for exactly this acknowledged export, if the bridge wrote it."""
    try:
        text = (directory / TELEMETRY_FILE).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    record = parse_telemetry(text)
    if record is None or (record.episode, record.sequence) != (episode, sequence):
        return None
    return record


def spawned(before: Telemetry | None, after: Telemetry) -> list[int]:
    """Types of player-0 units that are new since ``before``, excluding the wisps.

    A consumed wisp is killed and a new unit is created, so a new handle (or a reused
    handle holding another type) marks a summon, evolution or fusion result.
    """
    previous = {} if before is None else {unit.handle: unit.raw_id for unit in before.units}
    return [
        unit.raw_id
        for unit in after.units
        if previous.get(unit.handle) != unit.raw_id and unit.raw_id not in WISPS
    ]


def pool_draws(before: Telemetry | None, after: Telemetry) -> list[int]:
    """The evolution pool unit drawn during this step, as a zero- or one-item list."""
    drawn = after.evolution != 0 and (
        before is None or before.evolution_handle != after.evolution_handle
    )
    return [after.evolution] if drawn else []
