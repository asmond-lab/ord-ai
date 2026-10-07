"""JSON schema of the dashboard's ``/api/summary`` document."""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class Checkpoint(BaseModel):
    """Saved policy weights: steps from ``metadata.json`` and the weights file's save time."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    steps: int | None
    algorithm: str | None
    saved_at: datetime | None


class Episode(BaseModel):
    """One episode as its transitions recorded it; ``game_time`` is the last one reported."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    episode: int
    steps: int
    max_round: int
    total_reward: float
    status: str
    acceptance: float
    game_time: float | None


class ActionUse(BaseModel):
    """How often one action was chosen and which share of those the map accepted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    code: int
    name: str
    count: int
    acceptance: float


class Unit(BaseModel):
    """One visible player-0 unit in the latest row."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    raw_id: int
    name: str
    x: float
    y: float


class Latest(BaseModel):
    """The newest transition of a run; ``units`` is None when that row has no telemetry."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    step: int
    episode: int
    sequence: int
    action: str
    accepted: bool
    reward: float
    round: int
    story: int
    enemies: int
    status: str
    game_time: float | None
    units: tuple[Unit, ...] | None


class LossPoint(BaseModel):
    """One logged policy update; ``terminal`` marks the update that closed an episode."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    step: int
    round: int
    reward: float
    policy_loss: float
    value_loss: float
    terminal: bool = False


class RunSummary(BaseModel):
    """Everything the dashboard shows about one run directory."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    name: str
    transitions: int
    skipped_lines: int
    updated_at: datetime | None
    telemetry_rows: int
    telemetry_fraction: float
    telemetry_fields: dict[str, int]
    catalog: str | None
    checkpoint: Checkpoint | None
    log_files: tuple[str, ...]
    losses: tuple[LossPoint, ...]
    episodes: tuple[Episode, ...]
    actions: tuple[ActionUse, ...]
    latest: Latest | None


class DashboardSummary(BaseModel):
    """Every run directory under ``runs_dir``, sorted by name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    runs_dir: str
    runs: tuple[RunSummary, ...]
