"""Collect real map transitions and update a neural policy in bounded rollouts."""

from contextlib import ExitStack
from pathlib import Path
from typing import Annotated, ClassVar, TextIO

import torch
from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console

from ord_ai.boss import BOSS_HEALTH, ROUND_TIME_LEFT
from ord_ai.catalog import Catalog, available_actions
from ord_ai.channel import BridgeTimeoutError, StateChannel
from ord_ai.checkpoint import Metadata, load_checkpoint, save_checkpoint
from ord_ai.diagnostics import arena
from ord_ai.launcher import GameHost
from ord_ai.learning import ActorCritic, DeviceChoice, Experience, Learner, select_device
from ord_ai.observation import features, input_size, transition_reward
from ord_ai.protocol import GameState
from ord_ai.telemetry import Placement, Telemetry, pool_draws, read_telemetry, spawned
from ord_ai.treasure import CHEST_COUNT


class TrainSettings(BaseModel):
    """User-controlled experiment settings, parsed at the CLI boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    catalog: Path
    bridge_directory: Path
    output: Path
    steps: Annotated[int, Field(gt=0)] = 1024
    rollout: Annotated[int, Field(gt=0)] = 32
    timeout: Annotated[float, Field(gt=0)] = 90.0
    learning_rate: Annotated[float, Field(gt=0)] = 0.0003
    seed: int = 7
    device: DeviceChoice = DeviceChoice.AUTO
    resume: Path | None = None
    # Set to relaunch Warcraft III through Battle.net for every new episode (unattended runs).
    training_map: Path | None = None


class TransitionLog(BaseModel):
    """Replayable scalar evidence that a training action reached the game."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
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
    # Replay-comparison fields (simulator report §8-3); absent when the bridge wrote none.
    game_time: float | None = None
    gv: int | None = None
    gv_main: int | None = None
    units: list[Placement] | None = None
    spawned: list[int] | None = None
    pool_draws: list[int] | None = None
    # Live hidden chests (ZC00), when the bridge exports them: a drop follows a caught chest.
    chests: int | None = None
    # Live round bosses (tP[0]) and the boss health ZB00 (0..1000) and round time left ZB01
    # (tenths of a second), when the bridge exports the boss rows (ord_ai.boss).
    bosses: int | None = None
    boss_health: int | None = None
    round_time_left: int | None = None
    # Action 6 comparison rows (ord_ai.diagnostics): fighters in the story arena, moving,
    # and eligible for the return-to-lane action.
    arena: tuple[int, int, int] | None = None


def transition_log(
    step: int,
    action: int,
    reward: float,
    after: GameState,
    telemetry: tuple[Telemetry | None, Telemetry | None],
) -> TransitionLog:
    """Build one replayable row; ``telemetry`` is the (before, after) bridge record pair."""
    before, current = telemetry
    techs = {tech.raw_id: tech.level for tech in after.techs}
    boss_rows = BOSS_HEALTH in techs
    return TransitionLog(
        step=step,
        episode=after.episode,
        sequence=after.sequence,
        action=action,
        accepted=after.action_ok == 1,
        reward=reward,
        round=after.round,
        story=after.story,
        enemies=after.enemies,
        status=after.status.name,
        game_time=None if current is None else current.game_time,
        gv=None if current is None else current.gv,
        # The map seeds Gv lazily; its value right after main starts every episode's replay.
        gv_main=None if current is None or after.sequence != 1 else current.gv_main,
        units=None if current is None else current.placements(),
        spawned=None if current is None else spawned(before, current),
        pool_draws=None if current is None else pool_draws(before, current),
        chests=techs.get(CHEST_COUNT),
        bosses=after.bosses if boss_rows else None,
        boss_health=techs.get(BOSS_HEALTH),
        round_time_left=techs.get(ROUND_TIME_LEFT),
        arena=arena(after),
    )


def _metadata(catalog: Catalog, steps: int) -> Metadata:
    return Metadata(
        catalog_fingerprint=catalog.fingerprint,
        steps=steps,
        input_size=input_size(catalog),
        action_count=len(catalog.actions),
    )


def _learner(settings: TrainSettings, catalog: Catalog) -> Learner:
    torch.set_rng_state(torch.Generator().manual_seed(settings.seed).get_state())
    device = select_device(settings.device)
    model = (
        ActorCritic(input_size(catalog), len(catalog.actions))
        if settings.resume is None
        else load_checkpoint(settings.resume, catalog, device)
    )
    if device == "cuda":
        for generator in torch.cuda.default_generators:
            _ = generator.manual_seed(settings.seed)
    return Learner(model, device, settings.learning_rate)


class _Run:
    """Mutable state of one training run: counters, rollout buffer and outputs."""

    def __init__(self, settings: TrainSettings, catalog: Catalog, learner: Learner) -> None:
        self.settings: TrainSettings = settings
        self.catalog: Catalog = catalog
        self.learner: Learner = learner
        self.console: Console = Console()
        self.completed: int = 0
        self.experiences: list[Experience] = []
        self.telemetry: Telemetry | None = None

    def observe_telemetry(self, state: GameState) -> Telemetry | None:
        record = read_telemetry(self.settings.bridge_directory, state.episode, state.sequence)
        previous, self.telemetry = self.telemetry, record
        return previous

    def record(self, log: TextIO, index: int, reward: float, after: GameState) -> None:
        self.completed += 1
        before = self.observe_telemetry(after)
        record = transition_log(
            self.completed,
            self.catalog.actions[index].code,
            reward,
            after,
            (before, self.telemetry),
        )
        _ = log.write(record.model_dump_json(exclude_none=True) + "\n")
        log.flush()

    def update(self, after: GameState, reward: float) -> None:
        bootstrap = 0.0 if after.terminal else self.learner.value(features(after, self.catalog))
        losses = self.learner.update(self.experiences, bootstrap)
        self.experiences.clear()
        # A long run can be killed without cleanup; keep the latest update on disk.
        save_checkpoint(
            self.settings.output, self.learner.model, _metadata(self.catalog, self.completed)
        )
        self.console.print(
            f"step={self.completed} round={after.round} reward={reward:.3f}",
            f"policy_loss={losses.policy:.4f} value_loss={losses.value:.4f}",
        )

    def flush(self, observation: tuple[float, ...]) -> None:
        if self.experiences:
            _ = self.learner.update(self.experiences, self.learner.value(observation))
            self.experiences.clear()

    def reset(self, channel: StateChannel) -> GameState:
        state = channel.reset()
        _ = self.observe_telemetry(state)
        self.console.print(f"reset episode={state.episode}")
        return state

    def loop(self, channel: StateChannel, log: TextIO, relaunching: bool) -> GameState:
        state = self.reset(channel)
        while self.completed < self.settings.steps:
            observation = features(state, self.catalog)
            mask = available_actions(state, self.catalog)
            index = self.learner.choose(observation, mask)
            try:
                after = channel.step(self.catalog.actions[index].code)
            except BridgeTimeoutError:
                if not relaunching:
                    raise
                # The game stopped answering mid-episode: learn from what was collected.
                self.console.print(f"step timeout episode={state.episode}; relaunching")
                self.flush(observation)
                state = self.reset(channel)
                continue
            reward = transition_reward(state, after)
            self.experiences.append(Experience(observation, mask, index, reward, after.terminal))
            self.record(log, index, reward, after)
            state = after
            if len(self.experiences) >= self.settings.rollout or after.terminal:
                self.update(after, reward)
            if after.terminal and self.completed < self.settings.steps:
                self.console.print(f"episode={after.episode} ended {after.status.name}")
                state = self.reset(channel)
        return state


def train(settings: TrainSettings) -> Metadata:
    """Train exclusively from acknowledged Warcraft observations."""
    catalog = Catalog.model_validate_json(settings.catalog.read_text("utf-8"))
    learner = _learner(settings, catalog)
    settings.output.mkdir(parents=True, exist_ok=True)
    _ = (settings.output / "catalog.json").write_text(
        catalog.model_dump_json(indent=2), encoding="utf-8"
    )
    run = _Run(settings, catalog, learner)
    run.console.print(
        f"Real Warcraft training: device={learner.device}, actions={len(catalog.actions)}"
    )
    try:
        with ExitStack() as stack:
            host = (
                None
                if settings.training_map is None
                else stack.enter_context(GameHost(settings.training_map))
            )
            channel = stack.enter_context(
                StateChannel(settings.bridge_directory, settings.timeout, host)
            )
            log = stack.enter_context(
                (settings.output / "transitions.jsonl").open("a", encoding="utf-8")
            )
            state = run.loop(channel, log, relaunching=host is not None)
            run.flush(features(state, catalog))
    finally:
        metadata = _metadata(catalog, run.completed)
        save_checkpoint(settings.output, learner.model, metadata)
    return metadata
