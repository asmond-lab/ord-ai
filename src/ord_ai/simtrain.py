"""Simulator training: teacher demonstrations, imitation, then A2C fine-tuning by curriculum.

Everything the policy sees and does is the real-game path: ``features``, ``available_actions``,
``transition_reward``, the catalog from ``build/catalog.json`` and ``save_checkpoint``. Only the
environment differs (the ORD simulator instead of Warcraft III), so a checkpoint written here
loads unchanged with ``ord-ai play``. Simulator results are not real-game results.
"""

import json
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import ClassVar, Final, Literal, TextIO, cast

import torch
from pydantic import BaseModel, ConfigDict
from rich.console import Console
from torch.distributions import Categorical

from ord_ai.action_groups import action_groups, navigation_styles
from ord_ai.catalog import BasicAction, Catalog, available_actions
from ord_ai.checkpoint import Metadata, load_checkpoint, save_checkpoint
from ord_ai.curriculum import STAGES, Stage, next_stage, zero_count_clear
from ord_ai.learning import (
    DISCOUNT,
    ENTROPY_WEIGHT,
    MASKED_LOGIT,
    ActorCritic,
    DemoBatch,
    Experience,
    Learner,
    Rollout,
    discounted_returns,
    generalized_advantages,
)
from ord_ai.observation import features, input_size, transition_reward
from ord_ai.protocol import GameState, ProtocolError
from ord_ai.sim import WINS, SimServer, SimVectorEnv, WinTrace, prepare_root, server_build
from ord_ai.teacher import Teacher

SIM_ROOTS: Final = Path("build/sim")


class SimConfig(BaseModel):
    """Every simulator-training knob, read from ``sim.json`` (all optional)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    device: str = "auto"
    envs: int = 16
    demos: Path = Path("runs/sim-demos")
    demo_episodes: int = 64
    demo_seed: int = 1000
    imitation: Path = Path("runs/sim-imitation")
    epochs: int = 8
    batch_size: int = 1024
    imitation_learning_rate: float = 0.001
    seed: int = 7
    rl: Path = Path("runs/sim-rl")
    rl_start: Path | None = Path("runs/sim-imitation")
    rl_steps: int = 2_000_000
    rollout: int = 32
    rl_learning_rate: float = 0.0001
    rl_seed: int = 200_000
    checkpoint_every: int = 50_000
    bc_every: int = 4
    bc_batch: int = 256
    bc_until: int = 1_000_000
    # Exploration of actions the teacher never takes: the share of every imitation target
    # spread over all allowed actions, and the A2C entropy bonus.
    imitation_smoothing: float = 0.0
    entropy_weight: float = ENTROPY_WEIGHT
    # Imitation-loss weight of the teacher's wait rows (every other action counts 1).
    imitation_wait_weight: float = 1.0
    # "a2c": update every environment each ``rollout`` steps; "ppo": collect ``rollout`` steps
    # of every environment, then ``ppo_epochs`` clipped epochs in ``ppo_minibatch`` rows.
    algorithm: Literal["a2c", "ppo"] = "a2c"
    ppo_epochs: int = 4
    ppo_minibatch: int = 512
    # Reward discount per step. A Hard game is about 30 steps a round, so 0.99 looks about
    # three rounds ahead; boss rounds come every five.
    discount: float = DISCOUNT
    eval_episodes: int = 32
    eval_seed: int = 900_000


BEST_WINDOW: Final = 64
# The A2C rollout the teacher-imitation schedule (``bc_every`` updates) was tuned for.
A2C_CHUNK: Final = 32
_AGREEMENT_CHUNK: Final = 8192
console: Final = Console()


def _say(text: str) -> None:
    console.print(text, markup=False, highlight=False)


def stage_name(stage: Stage) -> str:
    """Folder name of a stage's simulator root, e.g. ``d6i`` for Nightmare with Isekai."""
    return f"d{int(stage.difficulty)}{'i' if stage.isekai else ''}"


def _wins(stream: TextIO, server: SimServer, dll: Path, stage: Stage, phase: str) -> WinTrace:
    """Archive this server's won games (seed, sent codes, final STATE) to ``stream``."""
    context: dict[str, object] = {
        "phase": phase,
        "stage": stage_name(stage),
        "difficulty": int(stage.difficulty),
        "isekai": stage.isekai,
        "server_build": server_build(),
    }
    return WinTrace(stream, server, dll, context)


def stage_server(stage: Stage) -> Path:
    """Prepare (or refresh) the simulator root for ``stage`` and return its server DLL.

    One root per stage and simulator build, so a run started after the simulator was rebuilt
    does not overwrite DLLs an older run's server holds open.
    """
    return prepare_root(stage, SIM_ROOTS / f"{stage_name(stage)}-{server_build()}")


def metadata(catalog: Catalog, steps: int) -> Metadata:
    """Checkpoint metadata identical to the real-game trainer's."""
    return Metadata(
        catalog_fingerprint=catalog.fingerprint,
        steps=steps,
        input_size=input_size(catalog),
        action_count=len(catalog.actions),
    )


def new_model(catalog: Catalog) -> ActorCritic:
    """A fresh network with the real-game input and action sizes."""
    return ActorCritic(input_size(catalog), len(catalog.actions))


def save_policy(directory: Path, learner: Learner, catalog: Catalog, steps: int) -> None:
    """Write weights, metadata and the catalog the weights belong to."""
    save_checkpoint(directory, learner.model, metadata(catalog, steps))
    _ = (directory / "catalog.json").write_text(catalog.model_dump_json(indent=2), "utf-8")


@dataclass(slots=True)
class Trace:
    """Running totals for the episode an environment is playing."""

    length: int = 0
    reward: float = 0.0
    accepted: int = 0
    uses: Counter[str] = field(default_factory=Counter[str])
    uses_accepted: Counter[str] = field(default_factory=Counter[str])

    def add(self, reward: float, after: GameState, labels: tuple[str, ...] = ()) -> None:
        """Count one step; ``labels`` are the usage groups of the chosen action."""
        self.length += 1
        self.reward += reward
        self.accepted += after.action_ok
        self.uses.update(labels)
        if after.action_ok:
            self.uses_accepted.update(labels)


def usage_labels(catalog: Catalog) -> dict[int, tuple[str, ...]]:
    """Usage-log groups of each action code: the 기록지침 groups and 항법 styles.

    The teacher never takes these actions, so their counts in ``episodes.jsonl`` show what
    reinforcement learning finds by itself.
    """
    labels: dict[int, list[str]] = {}
    for group, codes in action_groups(catalog).items():
        for code in codes:
            labels.setdefault(code, []).append(group)
    for style, codes in navigation_styles(catalog).items():
        for code in codes:
            labels.setdefault(code, []).append(f"navigation_{style}")
    return {code: tuple(names) for code, names in labels.items()}


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """One finished simulator episode, as written to ``episodes.jsonl``."""

    phase: str
    steps: int
    episode: int
    env: int
    difficulty: int
    isekai: bool
    length: int
    round: int
    story: int
    status: str
    enemies: int
    zero_count_clear: bool
    reward: float
    accepted: float
    wall: float
    uses: dict[str, int] = field(default_factory=dict[str, int])
    uses_accepted: dict[str, int] = field(default_factory=dict[str, int])


class EpisodeLog:
    """Append episode records and keep a rolling view of recent final rounds."""

    def __init__(self, stream: TextIO, phase: str) -> None:
        """Write to ``stream`` under the phase label."""
        self.stream: TextIO = stream
        self.phase: str = phase
        self.started: float = time.perf_counter()
        self.recent: deque[int] = deque(maxlen=BEST_WINDOW)
        self.count: int = 0

    def write(
        self, steps: int, env: int, stage: Stage, final: GameState, trace: Trace
    ) -> EpisodeRecord:
        """Record ``final`` as the last state of one episode."""
        self.count += 1
        record = EpisodeRecord(
            phase=self.phase,
            steps=steps,
            episode=self.count,
            env=env,
            difficulty=int(stage.difficulty),
            isekai=stage.isekai,
            length=trace.length,
            round=final.round,
            story=final.story,
            status=final.status.name,
            enemies=final.enemies,
            zero_count_clear=zero_count_clear(final),
            reward=round(trace.reward, 4),
            accepted=round(trace.accepted / max(1, trace.length), 4),
            wall=round(time.perf_counter() - self.started, 2),
            uses=dict(sorted(trace.uses.items())),
            uses_accepted=dict(sorted(trace.uses_accepted.items())),
        )
        _ = self.stream.write(json.dumps(asdict(record)) + "\n")
        self.stream.flush()
        self.recent.append(final.round)
        return record

    def mean_round(self) -> float:
        """Mean final round over the most recent episodes."""
        return sum(self.recent) / len(self.recent) if self.recent else 0.0


def sample_actions(
    learner: Learner,
    observations: list[tuple[float, ...]],
    masks: list[tuple[bool, ...]],
    greedy: bool = False,
) -> list[int]:
    """Masked sampling for a batch (row for row ``Learner.choose``), or the most likely action."""
    with torch.no_grad():
        tensor = torch.tensor(observations, dtype=torch.float32, device=learner.device)
        logits, _ = learner.model.evaluate(tensor)
        allowed = torch.tensor(masks, dtype=torch.bool, device=learner.device)
        masked = logits.masked_fill(~allowed, MASKED_LOGIT)
        chosen = masked.argmax(dim=-1) if greedy else Categorical(logits=masked).sample()
        chosen = chosen.cpu()
        return [int(chosen[row].item()) for row in range(chosen.shape[0])]


@dataclass(slots=True)
class _Episode:
    """Teacher decisions of the episode an environment is playing."""

    observations: list[tuple[float, ...]] = field(default_factory=list)
    masks: list[tuple[bool, ...]] = field(default_factory=list)
    actions: list[int] = field(default_factory=list)
    rewards: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DemoSettings:
    """Knobs for :func:`collect_demonstrations`."""

    episodes: int
    envs: int
    base_seed: int


def collect_demonstrations(
    catalog: Catalog, output: Path, stage: Stage, settings: DemoSettings
) -> Path:
    """Let the rule teacher play simulator games and save every decision."""
    episodes, envs, base_seed = settings.episodes, settings.envs, settings.base_seed
    output.mkdir(parents=True, exist_ok=True)
    current = [_Episode() for _ in range(envs)]
    kept: dict[str, list[torch.Tensor]] = {"obs": [], "mask": [], "action": [], "return": []}
    teachers = [Teacher() for _ in range(envs)]
    traces = [Trace() for _ in range(envs)]
    labels = usage_labels(catalog)
    steps = 0
    dll = stage_server(stage)
    with (
        SimServer(dll) as server,
        (output / "episodes.jsonl").open("a", encoding="utf-8") as stream,
        (output / WINS).open("a", encoding="utf-8") as wins,
    ):
        log = EpisodeLog(stream, "teacher")
        vector = SimVectorEnv(server, envs, base_seed, _wins(wins, server, dll, stage, "teacher"))
        states = vector.reset_all()
        while log.count < episodes:
            codes: list[int] = []
            for env, state in enumerate(states):
                mask = available_actions(state, catalog)
                index = teachers[env].choose(state, catalog, mask)
                current[env].observations.append(features(state, catalog))
                current[env].masks.append(mask)
                current[env].actions.append(index)
                codes.append(catalog.actions[index].code)
            afters = vector.step(codes)
            for env, after in enumerate(afters):
                teachers[env].observe(after)
                reward = transition_reward(states[env], after)
                current[env].rewards.append(reward)
                traces[env].add(reward, after, labels.get(codes[env], ()))
                steps += 1
                if not after.terminal:
                    continue
                episode = current[env]
                returns = discounted_returns(episode.rewards, [False] * len(episode.rewards), 0.0)
                kept["obs"].append(torch.tensor(episode.observations, dtype=torch.float32))
                kept["mask"].append(torch.tensor(episode.masks, dtype=torch.bool))
                kept["action"].append(torch.tensor(episode.actions, dtype=torch.long))
                kept["return"].append(torch.tensor(returns, dtype=torch.float32))
                record = log.write(steps, env, stage, after, traces[env])
                _say(
                    " ".join(
                        (
                            f"teacher episode={record.episode}",
                            f"round={record.round}",
                            f"status={record.status}",
                            f"length={record.length}",
                        )
                    )
                )
                current[env] = _Episode()
                teachers[env].reset()
                traces[env] = Trace()
            states = [
                vector.reset(env) if after.terminal else after for env, after in enumerate(afters)
            ]
    dataset = output / "demonstrations.pt"
    torch.save(
        {
            "catalog_fingerprint": catalog.fingerprint,
            "observations": torch.cat(kept["obs"]),
            "masks": torch.cat(kept["mask"]),
            "actions": torch.cat(kept["action"]),
            "returns": torch.cat(kept["return"]),
        },
        dataset,
    )
    return dataset


@dataclass(frozen=True, slots=True)
class ImitationSettings:
    """Knobs for :func:`imitate`."""

    epochs: int
    batch_size: int
    learning_rate: float
    device: str
    seed: int
    smoothing: float = 0.0
    wait_weight: float = 1.0


def row_weights(catalog: Catalog, actions: torch.Tensor, wait_weight: float) -> torch.Tensor:
    """Imitation weights per demonstration row: ``wait_weight`` for waits, 1 otherwise."""
    wait = next(i for i, action in enumerate(catalog.actions) if action.code == BasicAction.WAIT)
    return torch.where(actions == wait, wait_weight, 1.0)


def imitate(catalog: Catalog, dataset: Path, output: Path, settings: ImitationSettings) -> float:
    """Behavior-clone the teacher; return the final epoch's action agreement."""
    demos = Demonstrations.load(dataset, catalog)
    observations, masks, actions = demos.observations, demos.masks, demos.actions
    torch.set_rng_state(torch.Generator().manual_seed(settings.seed).get_state())
    learner = Learner(new_model(catalog), settings.device, settings.learning_rate)
    output.mkdir(parents=True, exist_ok=True)
    agreement = 0.0
    with (output / "imitation.jsonl").open("a", encoding="utf-8") as stream:
        for epoch in range(1, settings.epochs + 1):
            order = torch.randperm(len(actions))
            policy_total = value_total = 0.0
            batches = 0
            for start in range(0, len(order), settings.batch_size):
                batch = order[start : start + settings.batch_size]
                rows = demos.batch(batch)
                weights = row_weights(catalog, rows.actions, settings.wait_weight)
                losses = learner.imitate_batch(replace(rows, weights=weights), settings.smoothing)
                policy_total += losses.policy
                value_total += losses.value
                batches += 1
            agreement = teacher_agreement(learner, observations, masks, actions)
            row = {
                "epoch": epoch,
                "policy_loss": policy_total / batches,
                "value_loss": value_total / batches,
                "teacher_agreement": agreement,
                "samples": len(actions),
            }
            _ = stream.write(json.dumps(row) + "\n")
            stream.flush()
            _say(json.dumps(row))
    save_policy(output, learner, catalog, 0)
    return agreement


def teacher_agreement(
    learner: Learner, observations: torch.Tensor, masks: torch.Tensor, actions: torch.Tensor
) -> float:
    """Share of demonstrations where the policy's most likely action is the teacher's."""
    hits = 0
    with torch.no_grad():
        for start in range(0, len(actions), _AGREEMENT_CHUNK):
            chunk = slice(start, start + _AGREEMENT_CHUNK)
            logits, _ = learner.model.evaluate(observations[chunk].to(learner.device))
            allowed = masks[chunk].to(learner.device)
            best = logits.masked_fill(~allowed, MASKED_LOGIT).argmax(dim=-1).cpu()
            hits += int((best == actions[chunk]).sum().item())
    return hits / len(actions)


@dataclass(frozen=True, slots=True)
class FineTuneSettings:
    """Knobs for :func:`fine_tune`."""

    steps: int
    envs: int
    rollout: int
    learning_rate: float
    base_seed: int
    device: str
    checkpoint_every: int
    demonstrations: Path | None = None
    bc_every: int = 4
    bc_batch: int = 256
    bc_until: int = 0
    entropy_weight: float = ENTROPY_WEIGHT
    smoothing: float = 0.0
    algorithm: str = "a2c"
    ppo_epochs: int = 4
    ppo_minibatch: int = 512
    wait_weight: float = 1.0
    discount: float = DISCOUNT


@dataclass(frozen=True, slots=True)
class Demonstrations:
    """Teacher decisions loaded as tensors, checked against the catalog."""

    observations: torch.Tensor
    masks: torch.Tensor
    actions: torch.Tensor
    returns: torch.Tensor

    @classmethod
    def load(cls, dataset: Path, catalog: Catalog) -> "Demonstrations":
        """Read ``demonstrations.pt``; refuse data recorded with another action catalog."""
        data = cast("dict[str, object]", torch.load(dataset, weights_only=True))
        if data["catalog_fingerprint"] != catalog.fingerprint:
            raise ProtocolError(detail="Demonstrations were recorded with a different catalog")
        return cls(
            cast("torch.Tensor", data["observations"]),
            cast("torch.Tensor", data["masks"]),
            cast("torch.Tensor", data["actions"]),
            cast("torch.Tensor", data["returns"]),
        )

    def batch(self, indices: torch.Tensor) -> DemoBatch:
        """The rows at ``indices``."""
        return DemoBatch(
            self.observations[indices],
            self.masks[indices],
            self.actions[indices],
            self.returns[indices],
        )


@dataclass(slots=True)
class Curriculum:
    """Persistent stage progress of one fine-tuning run (``curriculum.json``)."""

    stage: int = 0
    steps: int = 0
    promotions: list[dict[str, int]] = field(default_factory=list)

    @classmethod
    def load(cls, output: Path) -> "Curriculum":
        """Read the saved progress, or start at stage 0 (Easy)."""
        path = output / "curriculum.json"
        if not path.exists():
            return cls()
        saved = cast("dict[str, object]", json.loads(path.read_text("utf-8")))
        return cls(
            stage=cast("int", saved["stage"]),
            steps=cast("int", saved["steps"]),
            promotions=cast("list[dict[str, int]]", saved["promotions"]),
        )

    def save(self, output: Path) -> None:
        """Write the progress beside the checkpoint."""
        _ = (output / "curriculum.json").write_text(json.dumps(asdict(self), indent=2), "utf-8")


@dataclass(slots=True)
class _Session:
    """State shared by the curriculum stages of one fine-tuning run."""

    learner: Learner
    catalog: Catalog
    output: Path
    log: EpisodeLog
    settings: FineTuneSettings
    completed: int
    demos: Demonstrations | None = None
    updates: int = 0
    best: float = -1.0

    def promoted(self) -> None:
        """Judge ``best/`` on the new stage only: an easier stage's rounds are not comparable."""
        self.best = -1.0
        self.log.recent.clear()

    def after_update(self) -> None:
        """Interleave one teacher-imitation step every ``bc_every`` A2C updates, early on."""
        self.updates += 1
        settings = self.settings
        if (
            self.demos is None
            or self.completed >= settings.bc_until
            or self.updates % settings.bc_every
        ):
            return
        rows = self.demos.batch(torch.randint(len(self.demos.actions), (settings.bc_batch,)))
        weights = row_weights(self.catalog, rows.actions, settings.wait_weight)
        _ = self.learner.imitate_batch(replace(rows, weights=weights), settings.smoothing)


def fine_tune(
    catalog: Catalog, start: Path | None, output: Path, settings: FineTuneSettings
) -> Curriculum:
    """A2C in parallel simulator games, promoting a stage after a unit-count-0 clear."""
    output.mkdir(parents=True, exist_ok=True)
    _ = (output / "training.json").write_text(
        json.dumps(asdict(settings), indent=2, default=str), "utf-8"
    )
    curriculum = Curriculum.load(output)
    resume = output if (output / "policy.safetensors").exists() else start
    if resume == output:
        # curriculum.json is written on promotions only; each checkpoint records its steps.
        saved = Metadata.model_validate_json((output / "metadata.json").read_text("utf-8"))
        curriculum.steps = max(curriculum.steps, saved.steps)
    model = (
        new_model(catalog) if resume is None else load_checkpoint(resume, catalog, settings.device)
    )
    learner = Learner(
        model, settings.device, settings.learning_rate, settings.entropy_weight, settings.discount
    )
    seed = settings.base_seed + curriculum.steps
    with (output / "episodes.jsonl").open("a", encoding="utf-8") as stream:
        demos = (
            None
            if settings.demonstrations is None
            else Demonstrations.load(settings.demonstrations, catalog)
        )
        session = _Session(
            learner, catalog, output, EpisodeLog(stream, "rl"), settings, curriculum.steps, demos
        )
        while session.completed < settings.steps:
            before = curriculum.stage
            promoted = _run_stage(session, before, seed)
            seed += settings.envs * 1000
            curriculum.steps = session.completed
            if promoted is not None:
                curriculum.promotions.append(
                    {"steps": session.completed, "from": before, "to": promoted}
                )
                curriculum.stage = promoted
                session.promoted()
            curriculum.save(output)
    save_policy(output, learner, catalog, session.completed)
    return curriculum


def _run_stage(session: _Session, stage_index: int, seed: int) -> int | None:
    """Train on one stage until the step budget ends or a clear promotes the curriculum."""
    learner, catalog, settings = session.learner, session.catalog, session.settings
    if settings.algorithm == "ppo":
        return _run_stage_ppo(session, stage_index, seed)
    stage = STAGES[stage_index]
    buffers: list[list[Experience]] = [[] for _ in range(settings.envs)]
    traces = [Trace() for _ in range(settings.envs)]
    labels = usage_labels(catalog)
    next_save = session.completed + settings.checkpoint_every
    dll = stage_server(stage)
    with (
        SimServer(dll) as server,
        (session.output / WINS).open("a", encoding="utf-8") as wins,
    ):
        vector = SimVectorEnv(
            server, settings.envs, seed, _wins(wins, server, dll, stage, session.log.phase)
        )
        states = vector.reset_all()
        _say(f"stage {stage_index} {stage_name(stage)} from step {session.completed}")
        while session.completed < settings.steps:
            observations = [features(state, catalog) for state in states]
            masks = [available_actions(state, catalog) for state in states]
            indices = sample_actions(learner, observations, masks)
            codes = [catalog.actions[index].code for index in indices]
            afters = vector.step(codes)
            promoted: int | None = None
            for env, after in enumerate(afters):
                reward = transition_reward(states[env], after)
                traces[env].add(reward, after, labels.get(codes[env], ()))
                buffers[env].append(
                    Experience(observations[env], masks[env], indices[env], reward, after.terminal)
                )
                session.completed += 1
                if len(buffers[env]) >= settings.rollout or after.terminal:
                    bootstrap = 0.0 if after.terminal else learner.value(features(after, catalog))
                    _ = learner.update(buffers[env], bootstrap)
                    session.after_update()
                    buffers[env] = []
                if after.terminal:
                    _ = session.log.write(session.completed, env, stage, after, traces[env])
                    traces[env] = Trace()
                    if next_stage(stage_index, after) != stage_index:
                        promoted = next_stage(stage_index, after)
            if session.completed >= next_save or promoted is not None:
                next_save = session.completed + settings.checkpoint_every
                _checkpoint(session)
            if promoted is not None:
                return promoted
            states = [
                vector.reset(env) if after.terminal else after for env, after in enumerate(afters)
            ]
    return None


@dataclass(slots=True)
class _PpoColumns:
    """One rollout of every environment, a tensor per time step."""

    observations: list[torch.Tensor] = field(default_factory=list[torch.Tensor])
    masks: list[torch.Tensor] = field(default_factory=list[torch.Tensor])
    actions: list[torch.Tensor] = field(default_factory=list[torch.Tensor])
    log_probabilities: list[torch.Tensor] = field(default_factory=list[torch.Tensor])
    values: list[torch.Tensor] = field(default_factory=list[torch.Tensor])
    rewards: list[torch.Tensor] = field(default_factory=list[torch.Tensor])
    terminals: list[torch.Tensor] = field(default_factory=list[torch.Tensor])


@dataclass(slots=True)
class _PpoGames:
    """The running games of one PPO stage and their per-episode records."""

    vector: SimVectorEnv
    states: list[GameState]
    stage_index: int
    traces: list[Trace]
    labels: dict[int, tuple[str, ...]]


def _state_tensor(states: list[GameState], catalog: Catalog, device: str) -> torch.Tensor:
    rows = [features(state, catalog) for state in states]
    return torch.tensor(rows, dtype=torch.float32, device=device)


def _ppo_step(session: _Session, games: _PpoGames, columns: _PpoColumns) -> int | None:
    """Sample and play one step of every game; return a promoted stage index, if any."""
    learner, catalog = session.learner, session.catalog
    stage = STAGES[games.stage_index]
    observations = _state_tensor(games.states, catalog, learner.device)
    masks = torch.tensor(
        [available_actions(state, catalog) for state in games.states],
        dtype=torch.bool,
        device=learner.device,
    )
    with torch.no_grad():
        logits, values = learner.model.evaluate(observations)
        policy = Categorical(logits=logits.masked_fill(~masks, MASKED_LOGIT))
        actions = policy.sample()
        log_probabilities = cast("torch.Tensor", policy.log_prob(actions))
    codes = [catalog.actions[int(actions[row].item())].code for row in range(actions.shape[0])]
    afters = games.vector.step(codes)
    rewards: list[float] = []
    promoted: int | None = None
    for env, after in enumerate(afters):
        reward = transition_reward(games.states[env], after)
        rewards.append(reward)
        games.traces[env].add(reward, after, games.labels.get(codes[env], ()))
        session.completed += 1
        if after.terminal:
            _ = session.log.write(session.completed, env, stage, after, games.traces[env])
            games.traces[env] = Trace()
            if next_stage(games.stage_index, after) != games.stage_index:
                promoted = next_stage(games.stage_index, after)
    columns.observations.append(observations)
    columns.masks.append(masks)
    columns.actions.append(actions)
    columns.log_probabilities.append(log_probabilities)
    columns.values.append(values)
    columns.rewards.append(torch.tensor(rewards, dtype=torch.float32, device=learner.device))
    columns.terminals.append(torch.tensor([a.terminal for a in afters], device=learner.device))
    games.states = [
        games.vector.reset(env) if after.terminal else after for env, after in enumerate(afters)
    ]
    return promoted


def _ppo_update(session: _Session, games: _PpoGames, columns: _PpoColumns) -> None:
    """GAE over the rollout, PPO epochs, then the matching teacher-imitation steps."""
    learner, settings = session.learner, session.settings
    with torch.no_grad():
        _, bootstrap = learner.model.evaluate(
            _state_tensor(games.states, session.catalog, learner.device)
        )
    advantages, returns = generalized_advantages(
        torch.stack(columns.rewards),
        torch.stack(columns.values),
        torch.stack(columns.terminals),
        bootstrap,
        learner.discount,
    )
    rollout = Rollout(
        torch.cat(columns.observations),
        torch.cat(columns.masks),
        torch.cat(columns.actions),
        torch.cat(columns.log_probabilities),
        advantages.flatten(),
        returns.flatten(),
    )
    _ = learner.ppo_update(rollout, settings.ppo_epochs, settings.ppo_minibatch)
    # Keep the teacher-imitation share per sampled step equal to the A2C schedule.
    for _ in range(rollout.actions.shape[0] // A2C_CHUNK):
        session.after_update()


def _run_stage_ppo(session: _Session, stage_index: int, seed: int) -> int | None:
    """Like :func:`_run_stage`, but one PPO update per ``rollout`` steps of every game."""
    settings = session.settings
    stage = STAGES[stage_index]
    next_save = session.completed + settings.checkpoint_every
    dll = stage_server(stage)
    with (
        SimServer(dll) as server,
        (session.output / WINS).open("a", encoding="utf-8") as wins,
    ):
        vector = SimVectorEnv(
            server, settings.envs, seed, _wins(wins, server, dll, stage, session.log.phase)
        )
        games = _PpoGames(
            vector,
            vector.reset_all(),
            stage_index,
            [Trace() for _ in range(settings.envs)],
            usage_labels(session.catalog),
        )
        _say(f"stage {stage_index} {stage_name(stage)} from step {session.completed} (ppo)")
        while session.completed < settings.steps:
            columns = _PpoColumns()
            promoted: int | None = None
            for _ in range(settings.rollout):
                promoted = _ppo_step(session, games, columns)
                if promoted is not None:
                    break
            _ppo_update(session, games, columns)
            if session.completed >= next_save or promoted is not None:
                next_save = session.completed + settings.checkpoint_every
                _checkpoint(session)
            if promoted is not None:
                return promoted
    return None


def _checkpoint(session: _Session) -> None:
    """Save the latest weights, and ``best/`` when the recent mean round is a new high."""
    save_policy(session.output, session.learner, session.catalog, session.completed)
    mean = session.log.mean_round()
    if len(session.log.recent) == session.log.recent.maxlen and mean > session.best:
        session.best = mean
        save_policy(session.output / "best", session.learner, session.catalog, session.completed)
    _say(
        " ".join(
            (
                f"steps={session.completed}",
                f"episodes={session.log.count}",
                f"mean_round={mean:.2f}",
                f"best={session.best:.2f}",
            )
        )
    )


@dataclass(frozen=True, slots=True)
class EvaluationSettings:
    """Knobs for :func:`evaluate`."""

    episodes: int
    envs: int
    base_seed: int
    device: str
    greedy: bool = False


def evaluate(
    catalog: Catalog,
    checkpoint: Path | None,
    output: Path,
    stage: Stage,
    settings: EvaluationSettings,
) -> list[EpisodeRecord]:
    """Play games with a checkpoint (or the teacher when None) without any learning."""
    learner = (
        None
        if checkpoint is None
        else Learner(load_checkpoint(checkpoint, catalog, settings.device), settings.device)
    )
    teachers = [Teacher() for _ in range(settings.envs)]
    traces = [Trace() for _ in range(settings.envs)]
    labels = usage_labels(catalog)
    results: list[EpisodeRecord] = []
    output.parent.mkdir(parents=True, exist_ok=True)
    dll = stage_server(stage)
    with (
        SimServer(dll) as server,
        output.open("w", encoding="utf-8") as stream,
        output.with_name(f"{output.stem}-{WINS}").open("w", encoding="utf-8") as wins,
    ):
        phase = "greedy-eval" if settings.greedy else "eval"
        log = EpisodeLog(stream, "teacher-eval" if learner is None else phase)
        vector = SimVectorEnv(
            server, settings.envs, settings.base_seed, _wins(wins, server, dll, stage, log.phase)
        )
        states = vector.reset_all()
        steps = 0
        while len(results) < settings.episodes:
            masks = [available_actions(state, catalog) for state in states]
            if learner is None:
                indices = [
                    teachers[env].choose(state, catalog, masks[env])
                    for env, state in enumerate(states)
                ]
            else:
                indices = sample_actions(
                    learner, [features(state, catalog) for state in states], masks, settings.greedy
                )
            codes = [catalog.actions[index].code for index in indices]
            afters = vector.step(codes)
            for env, after in enumerate(afters):
                teachers[env].observe(after)
                traces[env].add(
                    transition_reward(states[env], after), after, labels.get(codes[env], ())
                )
                steps += 1
                if after.terminal:
                    results.append(log.write(steps, env, stage, after, traces[env]))
                    traces[env] = Trace()
                    teachers[env].reset()
            states = [
                vector.reset(env) if after.terminal else after for env, after in enumerate(afters)
            ]
    return results[: settings.episodes]
