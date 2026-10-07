"""Read-only access to one run directory: transitions, catalog names, checkpoint and logs.

Nothing here writes. A recording may be appending to ``transitions.jsonl`` while it is read:
its unfinished last line is skipped and counted.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, ValidationError

from ord_ai.catalog import Catalog
from ord_ai.telemetry import Placement
from ord_ai.web.models import Checkpoint, LossPoint

TRANSITIONS: Final = "transitions.jsonl"
# Simulator training writes one line per finished episode instead (``ord_ai.simtrain``).
SIM_EPISODES: Final = "episodes.jsonl"
# A training update (Rich may wrap it onto two lines) or the end of an episode.
LOG_EVENT_PATTERN: Final = (
    r"step=(?P<step>\d+)\s+round=(?P<round>\d+)\s+reward=(?P<reward>\S+)\s+",
    r"policy_loss=(?P<policy>\S+)\s+value_loss=(?P<value>\S+)|episode=\d+ ended",
)
LOG_EVENT: Final = re.compile("".join(LOG_EVENT_PATTERN))
UTF16_BOMS: Final = (b"\xff\xfe", b"\xfe\xff")


class Row(BaseModel):
    """One ``transitions.jsonl`` line (``ord_ai.training.TransitionLog``), read leniently."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    step: int
    episode: int
    sequence: int
    action: int
    accepted: bool
    reward: float
    round: int
    story: int
    enemies: int
    status: str
    game_time: float | None = None
    gv: int | None = None
    gv_main: int | None = None
    units: list[Placement] | None = None
    spawned: list[int] | None = None
    pool_draws: list[int] | None = None


class SimEpisode(BaseModel):
    """One ``episodes.jsonl`` line (``ord_ai.simtrain.EpisodeRecord``), read leniently."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    phase: str
    steps: int
    episode: int
    difficulty: int
    isekai: bool
    length: int
    round: int
    status: str
    zero_count_clear: bool
    reward: float
    accepted: float


class _Metadata(BaseModel):
    """The ``metadata.json`` fields shown (``ord_ai.checkpoint.Metadata``, without torch)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    steps: int
    algorithm: str


@dataclass(frozen=True, slots=True)
class Stamp:
    """A file at one version: an atomic replace or an append always yields a new stamp."""

    path: Path
    mtime_ns: int
    size: int

    @property
    def modified(self) -> datetime:
        """The file's modification time."""
        return datetime.fromtimestamp(self.mtime_ns / 1e9, tz=UTC)


@dataclass(frozen=True, slots=True)
class Names:
    """Catalog display names for action codes and unit rawcodes."""

    source: str | None
    actions: Mapping[int, str]
    units: Mapping[int, str]

    def action(self, code: int) -> str:
        """Name an action code, falling back to its rawcode."""
        return self.actions.get(code) or _rawcode(code)

    def unit(self, raw_id: int) -> str:
        """Name a unit type, falling back to its rawcode."""
        return self.units.get(raw_id) or _rawcode(raw_id)


NO_NAMES: Final = Names(None, {}, {})


def _rawcode(value: int) -> str:
    text = value.to_bytes(4, "big").decode("latin-1") if 0 <= value < 1 << 32 else ""
    return text if text.isascii() and text.isalnum() else str(value)


def stamp(path: Path) -> Stamp | None:
    """The file's current version, or None when it does not exist."""
    try:
        status = path.stat()
    except FileNotFoundError:
        return None
    return Stamp(path, status.st_mtime_ns, status.st_size)


# Small JSON files are opened once per stamp. On Windows an open read handle makes the trainer's
# atomic replace of metadata.json fail, so the dashboard must not reopen it on every poll.
@lru_cache(maxsize=32)
def _catalog_names(version: Stamp) -> Names:
    catalog = Catalog.model_validate_json(version.path.read_bytes())
    return Names(
        version.path.as_posix(),
        {action.code: action.name for action in catalog.actions},
        {unit.raw_id: unit.name for unit in catalog.units},
    )


@lru_cache(maxsize=32)
def _metadata(version: Stamp) -> _Metadata:
    return _Metadata.model_validate_json(version.path.read_bytes())


def names(path: Path) -> Names | None:
    """Names from a catalog file; None when it is missing, unreadable or not a catalog."""
    version = stamp(path)
    if version is None:
        return None
    try:
        return _catalog_names(version)
    except (OSError, ValidationError):
        return None  # the caller falls back to the next catalog


def checkpoint(directory: Path) -> Checkpoint | None:
    """Steps from ``metadata.json`` and the ``policy.safetensors`` save time, if either exists."""
    metadata = stamp(directory / "metadata.json")
    weights = stamp(directory / "policy.safetensors")
    if metadata is None and weights is None:
        return None
    try:
        saved = None if metadata is None else _metadata(metadata)
    except (OSError, ValidationError):
        saved = None
    return Checkpoint(
        steps=None if saved is None else saved.steps,
        algorithm=None if saved is None else saved.algorithm,
        saved_at=None if weights is None else weights.modified,
    )


def _row(line: bytes) -> Row | None:
    try:
        return Row.model_validate_json(line)
    except ValidationError:
        return None  # the unfinished last line of a file that is still being appended


def rows(path: Path) -> tuple[list[Row], int]:
    """Every complete row of a transitions file and the number of unreadable lines."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return [], 0
    lines = [line for line in data.splitlines() if line.strip()]
    parsed = [row for row in map(_row, lines) if row is not None]
    return parsed, len(lines) - len(parsed)


def _sim_episode(line: bytes) -> SimEpisode | None:
    try:
        return SimEpisode.model_validate_json(line)
    except ValidationError:
        return None  # the unfinished last line of a file that is still being appended


def sim_episodes(path: Path, limit: int) -> list[SimEpisode]:
    """The last ``limit`` complete simulator episodes of a run, oldest first."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    lines = [line for line in data.splitlines() if line.strip()][-limit:]
    return [episode for episode in map(_sim_episode, lines) if episode is not None]


def log_files(run: Path) -> list[Path]:
    """``*.log`` inside the run, then ``<name>.log`` or ``<name>-*.log`` beside it."""
    beside = [
        path
        for path in run.parent.glob("*.log")
        if path.stem == run.name or path.stem.startswith(f"{run.name}-")
    ]
    return [path for path in [*sorted(run.glob("*.log")), *sorted(beside)] if path.is_file()]


def losses(path: Path) -> list[LossPoint]:
    """Policy updates logged as ``step=… policy_loss=… value_loss=…``, in order."""
    data = path.read_bytes()
    text = data.decode("utf-16" if data[:2] in UTF16_BOMS else "utf-8-sig", errors="replace")
    points: list[LossPoint] = []
    for match in LOG_EVENT.finditer(text):
        if match.group("step") is not None:
            points.append(
                LossPoint(
                    step=int(match.group("step")),
                    round=int(match.group("round")),
                    reward=float(match.group("reward")),
                    policy_loss=float(match.group("policy")),
                    value_loss=float(match.group("value")),
                )
            )
        elif points:
            # The episode's final reward drives this update's loss spike.
            points[-1] = points[-1].model_copy(update={"terminal": True})
    return points
