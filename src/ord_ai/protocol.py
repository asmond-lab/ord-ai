"""Typed, complete-snapshot protocol for the Warcraft Preload bridge."""

import re
from enum import IntEnum
from typing import Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

Nonnegative = Annotated[int, Field(ge=0)]
VERSION: Final = 1
STATE_FIELDS: Final = 16
UNIT_FIELDS: Final = 5
PRELOAD: Final = re.compile(r'call\s+Preload\s*\(\s*"([^"]*)"\s*\)')
COMMAND_TOKEN: Final = re.compile(r"ORD_AI\|1\|(\d+)\|\d+\|-?\d+")


def latest_episode(text: str) -> int:
    """Recover consumed or pending reset identifiers from bridge-owned files."""
    return max((int(match.group(1)) for match in COMMAND_TOKEN.finditer(text)), default=0)


class ProtocolError(RuntimeError):
    """Report an invalid complete bridge message."""

    def __init__(self, detail: str) -> None:
        """Retain the malformed operation's explanation."""
        self.detail: str = detail
        super().__init__(detail)


class Status(IntEnum):
    """Game lifecycle reported by the training map."""

    WAITING = 0
    PLAYING = 1
    WIN = 2
    LOSS = 3


class UnitCount(BaseModel):
    """One living unit type counted by the map."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    raw_id: Annotated[int, Field(gt=0)]
    count: Nonnegative


class TechLevel(BaseModel):
    """One researched player tech level (upgrades and unlock flags) reported by the map."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    raw_id: Annotated[int, Field(gt=0)]
    level: Nonnegative


class GameState(BaseModel):
    """One acknowledged action and its resulting game observation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    episode: Nonnegative
    sequence: Nonnegative
    round: Nonnegative
    gold: Nonnegative
    lumber: Nonnegative
    food: Nonnegative
    enemies: Nonnegative
    enemy_limit: Annotated[int, Field(gt=0)]
    death_count: int
    story: Nonnegative
    bosses: Nonnegative
    status: Status
    action_ok: Annotated[int, Field(ge=0, le=1)]
    units: tuple[UnitCount, ...] = ()
    techs: tuple[TechLevel, ...] = ()

    @property
    def terminal(self) -> bool:
        """Whether this episode has ended in the actual map."""
        return self.status in (Status.WIN, Status.LOSS)


EXIT_ACTION: Final = -2
CONTROL_FILE: Final = "control.txt"
CONTROL_TOKEN: Final = re.compile(r"ORD_AI\|1\|CTL\|(\d+)\|(\d+)\|-2")


class Command(BaseModel):
    """A single sequenced map action; -1 requests an actual map reset.

    -2 (EXIT_ACTION) is control only, never a policy action: the diagnostic record bridge
    acknowledges it in control.txt and ends the game (EndGame) so the client can be closed.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    episode: Annotated[int, Field(gt=0, le=2_000_000_000)]
    sequence: Nonnegative
    action: Annotated[int, Field(ge=EXIT_ACTION, le=2_147_483_647)]

    @property
    def file_name(self) -> str:
        """Name a fresh file per command; Warcraft never re-reads a path it has read."""
        if self.action == -1:
            return f"reset_{self.episode}.txt"
        return f"cmd_{self.episode}_{self.sequence}.txt"

    def preload_script(self) -> str:
        """Encode only validated integers into the local Preloader program."""
        payload = f"ORD_AI|{VERSION}|{self.episode}|{self.sequence}|{self.action}"
        return (
            "function PreloadFiles takes nothing returns nothing\n"
            f"    call BlzSetAbilityTooltip('AZZZ', \"{payload}\", 0)\n"
            "endfunction\n"
        )


def parse_snapshot(text: str) -> GameState | None:
    """Parse a complete snapshot; return None while a write is incomplete."""
    records = (match.group(1) for match in PRELOAD.finditer(text))
    rows = [value.split("|") for value in records if value.startswith("ORD_AI|")]
    if not rows or not any(row[2:3] == ["END"] for row in rows):
        return None
    if any(row[:2] != ["ORD_AI", str(VERSION)] for row in rows):
        raise ProtocolError(detail="Unsupported bridge protocol version")
    header, ending = rows[0], rows[-1]
    if len(header) != STATE_FIELDS or header[2:3] != ["STATE"]:
        raise ProtocolError(detail="Snapshot must start with exactly one complete STATE")
    if len(ending) != UNIT_FIELDS or ending[2:3] != ["END"]:
        raise ProtocolError(detail="Snapshot must finish with END")
    values = [int(value) for value in header[3:]]
    state = GameState(
        episode=values[0],
        sequence=values[1],
        round=values[2],
        gold=values[3],
        lumber=values[4],
        food=values[5],
        enemies=values[6],
        enemy_limit=values[7],
        death_count=values[8],
        story=values[9],
        bosses=values[10],
        status=Status(values[11]),
        action_ok=values[12],
    )
    if (int(ending[3]), int(ending[4])) != (state.episode, state.sequence):
        raise ProtocolError(detail="END does not acknowledge the STATE episode/sequence")
    units: dict[int, int] = {}
    techs: dict[int, int] = {}
    for row in rows[1:-1]:
        if len(row) != UNIT_FIELDS or row[2] not in ("UNIT", "TECH"):
            raise ProtocolError(
                detail="Only complete UNIT and TECH records may appear between STATE and END"
            )
        raw_id, value = int(row[3]), int(row[4])
        seen = units if row[2] == "UNIT" else techs
        if raw_id in seen:
            raise ProtocolError(detail=f"Duplicate {row[2]} record in snapshot")
        seen[raw_id] = value
    return state.model_copy(
        update={
            "units": tuple(UnitCount(raw_id=key, count=count) for key, count in units.items()),
            "techs": tuple(TechLevel(raw_id=key, level=level) for key, level in techs.items()),
        }
    )
