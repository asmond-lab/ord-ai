"""Combat events of the record build (``ord-ai build --kind record``); policies ignore them.

The bridge writes ``events.txt`` beside every state export: the attacks, deaths and summed
damage since the previous export, each with the bridge clock (game seconds) and ``Gv``.
"""

from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict

from ord_ai.protocol import PRELOAD, VERSION, ProtocolError

EVENTS_FILE: Final = "events.txt"
HEAD_FIELDS: Final = 7
END_FIELDS: Final = 8
# ATK/DIE: kind, time, Gv, two units of (handle, type, owner).
UNIT_PAIR_FIELDS: Final = 11
# SPL: time, Gv, caster (handle, type, owner), ability raw id (EVENT_PLAYER_UNIT_SPELL_EFFECT).
SPELL_FIELDS: Final = 9
# DMG: first hit time, (handle, type, owner), damage sum, hits, life now, max life.
DAMAGE_FIELDS: Final = 11
# HP: map global name (jB 해왕류, Hd 황금 종), life now, max life (R2S, three decimals).
HEALTH_FIELDS: Final = 6
# Simulator comparison rows (read-only diagnostics):
# CTX: round, story.
CONTEXT_FIELDS: Final = 5
# LANE: handle, type, x, y, life, max life, current order.
LANE_FIELDS: Final = 10
# SB: (handle, type, owner), life, max life, x, y, A04M, A04N, A04O, A04P, A04R levels.
STORY_BOSS_FIELDS: Final = 15
# MOV: handle, type, move speed, Amov level, paused, hidden, Aloc level.
MOBILITY_FIELDS: Final = 10
STACK_ABILITIES: Final = ("A04M", "A04N", "A04O", "A04P", "A04R")
# Diagnostic build only (--kind diagnostic), one W5R call (war3map.j 8117, the landing move):
# W5I: time, Gv, call id, unit (handle, type, owner), target x, target y, collision, flying.
W5R_CALL_FIELDS: Final = 13
# W5S: call id, stage, x, y, current order; the stages in the order the call records them.
W5R_STAGE_FIELDS: Final = 8
W5R_STAGES: Final = ("in", "x1", "y", "x2", "stop")
# TMG (GNv entry): time, Gv, Av, Ov, Xv, Iv, Ss; TMO (Obv entry): the same with the timer handle.
TIMER_FIELDS: Final = 10


class Unit(BaseModel):
    """A unit as the event saw it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    handle: int
    raw_id: int
    owner: int


class Strike(BaseModel):
    """One attack (``ATK``: attacker hits target) or one death (``DIE``: target, killer)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    time: float
    gv: int
    actor: Unit
    target: Unit


class Spell(BaseModel):
    """One ability effect (``SPL``): the caster and the ability that took effect."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    time: float
    gv: int
    caster: Unit
    ability: int


class LandingStage(BaseModel):
    """The unit right after one step of W5R: ``in`` (before it), ``x1``, ``y``, ``x2``, ``stop``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    stage: str
    x: float
    y: float
    order: int


class Landing(BaseModel):
    """One W5R call; ``stages`` lacks the ones past the per-export cap (counted in dropped)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    call: int
    time: float
    gv: int
    unit: Unit
    target_x: float
    target_y: float
    collision: float
    flying: bool
    stages: tuple[LandingStage, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether every stage from ``in`` to ``stop`` was recorded."""
        return tuple(stage.stage for stage in self.stages) == W5R_STAGES


class TimerEntry(BaseModel):
    """One final-round timer callback entry: ``TMG`` (GNv) or ``TMO`` (Obv).

    ``round``/``next_round``/``phase``/``ended`` are the map's Av, Ov, Xv and Iv at entry;
    ``last`` is Ss (TMG) or the expired timer's handle (TMO).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    kind: str
    time: float
    gv: int
    round: int
    next_round: int
    phase: int
    ended: int
    last: int


class Damage(BaseModel):
    """The damage one unit took since the previous export."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    first_time: float
    target: Unit
    amount: float
    hits: int
    life: float
    max_life: float


class Health(BaseModel):
    """The exact life of a map object the policy sees only as a rounded percent."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    name: str
    life: float
    max_life: float


class Context(BaseModel):
    """Round and story counter at the export (``Av``, ``KP``)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    round: int
    story: int


class LaneEnemy(BaseModel):
    """A living enemy of lane 0 (the units the enemy count covers)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    handle: int
    raw_id: int
    x: float
    y: float
    life: float
    max_life: float
    order: int


class StoryBoss(BaseModel):
    """A living enemy in the story arena with its armor-break/spell stack ability levels."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    unit: Unit
    life: float
    max_life: float
    x: float
    y: float
    levels: dict[str, int]


class Mobility(BaseModel):
    """Move speed, ``Amov`` level, pause, hidden and ``Aloc`` level of one living own unit."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    handle: int
    raw_id: int
    speed: float
    amov: int
    paused: bool
    hidden: bool
    locust: int


class CombatEvents(BaseModel):
    """Everything between two exports; ``dropped`` counts events past the per-export cap."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    episode: int
    sequence: int
    game_time: float
    gv: int
    attacks: tuple[Strike, ...] = ()
    deaths: tuple[Strike, ...] = ()
    spells: tuple[Spell, ...] = ()
    damage: tuple[Damage, ...] = ()
    health: tuple[Health, ...] = ()
    context: Context | None = None
    lane: tuple[LaneEnemy, ...] = ()
    story_bosses: tuple[StoryBoss, ...] = ()
    mobility: tuple[Mobility, ...] = ()
    landings: tuple[Landing, ...] = ()
    timers: tuple[TimerEntry, ...] = ()
    dropped: int = 0


def _unit(fields: list[str]) -> Unit:
    return Unit(handle=int(fields[0]), raw_id=int(fields[1]), owner=int(fields[2]))


def _frame(rows: list[list[str]]) -> tuple[list[str], list[str]]:
    """The EVT head and EEND end of a complete file, checked against each other."""
    if any(row[:2] != ["ORD_AI", str(VERSION)] for row in rows):
        raise ProtocolError(detail="Unsupported bridge events version")
    head, ending = rows[0], rows[-1]
    if len(head) != HEAD_FIELDS or head[2:3] != ["EVT"]:
        raise ProtocolError(detail="events.txt must start with one complete EVT record")
    if len(ending) != END_FIELDS or ending[3:5] != head[3:5]:
        raise ProtocolError(detail="events.txt end does not match its EVT record")
    return head, ending


def _diagnostic(
    row: list[str],
) -> Context | LaneEnemy | StoryBoss | Mobility | TimerEntry | None:
    """A CTX, LANE, SB, MOV, TMG or TMO row, or None for any other or incomplete row."""
    kind, size = row[2], len(row)
    if kind in {"TMG", "TMO"} and size == TIMER_FIELDS:
        return TimerEntry(
            kind=kind,
            time=float(row[3]),
            gv=int(row[4]),
            round=int(row[5]),
            next_round=int(row[6]),
            phase=int(row[7]),
            ended=int(row[8]),
            last=int(row[9]),
        )
    if kind == "CTX" and size == CONTEXT_FIELDS:
        return Context(round=int(row[3]), story=int(row[4]))
    if kind == "LANE" and size == LANE_FIELDS:
        return LaneEnemy(
            handle=int(row[3]),
            raw_id=int(row[4]),
            x=float(row[5]),
            y=float(row[6]),
            life=float(row[7]),
            max_life=float(row[8]),
            order=int(row[9]),
        )
    if kind == "SB" and size == STORY_BOSS_FIELDS:
        return StoryBoss(
            unit=_unit(row[3:6]),
            life=float(row[6]),
            max_life=float(row[7]),
            x=float(row[8]),
            y=float(row[9]),
            levels=dict(zip(STACK_ABILITIES, (int(value) for value in row[10:15]), strict=True)),
        )
    if kind == "MOV" and size == MOBILITY_FIELDS:
        return Mobility(
            handle=int(row[3]),
            raw_id=int(row[4]),
            speed=float(row[5]),
            amov=int(row[6]),
            paused=row[7] == "1",
            hidden=row[8] == "1",
            locust=int(row[9]),
        )
    return None


def _landings(rows: list[list[str]]) -> list[Landing]:
    """W5I calls with their W5S stages; a stage must follow its own call's W5I."""
    calls: dict[int, Landing] = {}
    for row in rows:
        if (row[2], len(row)) not in {("W5I", W5R_CALL_FIELDS), ("W5S", W5R_STAGE_FIELDS)}:
            raise ProtocolError(detail=f"Incomplete W5R record: {'|'.join(row)}")
        if row[2] == "W5I":
            calls[int(row[5])] = Landing(
                call=int(row[5]),
                time=float(row[3]),
                gv=int(row[4]),
                unit=_unit(row[6:9]),
                target_x=float(row[9]),
                target_y=float(row[10]),
                collision=float(row[11]),
                flying=row[12] == "1",
            )
            continue
        call = calls.get(int(row[3]))
        if call is None or row[4] not in W5R_STAGES:
            raise ProtocolError(detail=f"W5S record without its W5I call: {'|'.join(row)}")
        stage = LandingStage(stage=row[4], x=float(row[5]), y=float(row[6]), order=int(row[7]))
        calls[call.call] = call.model_copy(update={"stages": (*call.stages, stage)})
    return list(calls.values())


def parse_events(text: str) -> CombatEvents | None:
    """Parse a complete events.txt; return None while it is still being written."""
    rows = [
        value.split("|")
        for value in (match.group(1) for match in PRELOAD.finditer(text))
        if value.startswith("ORD_AI|")
    ]
    if not rows or rows[-1][2:3] != ["EEND"]:
        return None
    head, ending = _frame(rows)
    attacks: list[Strike] = []
    deaths: list[Strike] = []
    spells: list[Spell] = []
    damage: list[Damage] = []
    health: list[Health] = []
    diagnostics: list[Context | LaneEnemy | StoryBoss | Mobility | TimerEntry] = []
    landing_rows = [row for row in rows[1:-1] if row[2] in {"W5I", "W5S"}]
    for row in (row for row in rows[1:-1] if row[2] not in {"W5I", "W5S"}):
        kind = row[2]
        if kind in {"ATK", "DIE"} and len(row) == UNIT_PAIR_FIELDS:
            strike = Strike(
                time=float(row[3]), gv=int(row[4]), actor=_unit(row[5:8]), target=_unit(row[8:11])
            )
            if kind == "ATK":
                attacks.append(strike)
            else:
                # DIE lists the dying unit first, then its killer.
                deaths.append(
                    strike.model_copy(update={"actor": strike.target, "target": strike.actor})
                )
        elif kind == "SPL" and len(row) == SPELL_FIELDS:
            spells.append(
                Spell(
                    time=float(row[3]), gv=int(row[4]), caster=_unit(row[5:8]), ability=int(row[8])
                )
            )
        elif kind == "DMG" and len(row) == DAMAGE_FIELDS:
            damage.append(
                Damage(
                    first_time=float(row[3]),
                    target=_unit(row[4:7]),
                    amount=float(row[7]),
                    hits=int(row[8]),
                    life=float(row[9]),
                    max_life=float(row[10]),
                )
            )
        elif kind == "HP" and len(row) == HEALTH_FIELDS:
            health.append(Health(name=row[3], life=float(row[4]), max_life=float(row[5])))
        elif (diagnostic := _diagnostic(row)) is not None:
            diagnostics.append(diagnostic)
        else:
            raise ProtocolError(detail=f"Unknown or incomplete events.txt record: {'|'.join(row)}")
    landings = _landings(landing_rows)
    timers = [item for item in diagnostics if isinstance(item, TimerEntry)]
    counted = (int(ending[5]), int(ending[6]))
    if counted != (
        len(attacks) + len(deaths) + len(spells) + len(landing_rows) + len(timers),
        len(damage),
    ):
        raise ProtocolError(detail="events.txt record counts do not match its EEND")
    return CombatEvents(
        episode=int(head[3]),
        sequence=int(head[4]),
        game_time=float(head[5]),
        gv=int(head[6]),
        attacks=tuple(attacks),
        deaths=tuple(deaths),
        spells=tuple(spells),
        damage=tuple(damage),
        health=tuple(health),
        context=next((item for item in diagnostics if isinstance(item, Context)), None),
        lane=tuple(item for item in diagnostics if isinstance(item, LaneEnemy)),
        story_bosses=tuple(item for item in diagnostics if isinstance(item, StoryBoss)),
        mobility=tuple(item for item in diagnostics if isinstance(item, Mobility)),
        landings=tuple(landings),
        timers=tuple(timers),
        dropped=int(ending[7]),
    )


def read_events(directory: Path, episode: int, sequence: int) -> CombatEvents | None:
    """Read the events of exactly this acknowledged export, if the record build wrote them."""
    try:
        text = (directory / EVENTS_FILE).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    record = parse_events(text)
    if record is None or (record.episode, record.sequence) != (episode, sequence):
        return None
    return record
