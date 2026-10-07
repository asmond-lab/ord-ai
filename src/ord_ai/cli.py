"""Commands for building, connecting, training and using the offline policy."""

import json
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Final

import torch
import typer
from rich.console import Console

from ord_ai.build import (
    bridge_sources,
    build_map,
    diagnostic_bridge_sources,
    events_bridge_sources,
    read_map,
)
from ord_ai.catalog import Catalog, available_actions
from ord_ai.channel import BridgeTimeoutError, StateChannel
from ord_ai.checkpoint import load_checkpoint, migrate_checkpoint
from ord_ai.curriculum import STAGES, Difficulty, Stage
from ord_ai.dashboard import serve
from ord_ai.humanplay import (
    HUMAN_TIMEOUT,
    TRANSITIONS,
    HumanPlayError,
    HumanSettings,
    human_demonstrations,
    record_human,
)
from ord_ai.launcher import LaunchError, game_running, launch
from ord_ai.learning import DeviceChoice, Learner, select_device
from ord_ai.observation import features
from ord_ai.protocol import ProtocolError
from ord_ai.record import (
    PolicyPlayer,
    default_record_settings,
    record_episodes,
    record_teacher_episodes,
)
from ord_ai.simtrain import (
    DemoSettings,
    EpisodeRecord,
    EvaluationSettings,
    FineTuneSettings,
    ImitationSettings,
    SimConfig,
    collect_demonstrations,
    evaluate,
    fine_tune,
    imitate,
)
from ord_ai.smoke import run_smoke
from ord_ai.training import TrainSettings, train

app: Final = typer.Typer(no_args_is_help=True)
console: Final = Console()
DEFAULT_MAP: Final = Path("ORDR_S2_2.323[R].w3x")
DEFAULT_TRAINING_MAP: Final = Path("build/ORDR_AI_2.323R.w3x")
DEFAULT_CONFIG: Final = Path("training.json")
DEFAULT_SMOKE: Final = Path("runs/smoke")
DEFAULT_RUNS: Final = Path("runs")
DEFAULT_INSTALLED_MAP: Final = (
    Path.home()
    / "Documents"
    / "Warcraft III"
    / "Maps"
    / "Download"
    / "ORD_AI"
    / "ORDR_AI_2.323R.w3x"
)
DEFAULT_BRIDGE: Final = Path.home() / "Documents" / "Warcraft III" / "CustomMapData" / "ORD_AI"
WARCRAFT_MAPS: Final = Path.home() / "Documents" / "Warcraft III" / "Maps"
DEFAULT_CATALOG: Final = Path("build/catalog.json")
DEFAULT_SIM_CONFIG: Final = Path("sim.json")
DEFAULT_REAL_PLAY: Final = Path("runs/real-play")
DEFAULT_RECORD: Final = Path("runs/c2-teacher")
DEFAULT_HUMAN_BUILD: Final = Path("build/ORDR_AI_2.323R_human.w3x")
DEFAULT_EVENTS_BUILD: Final = Path("build/record/ORDR_AI_2.323R.w3x")
DEFAULT_DIAGNOSTIC_BUILD: Final = Path("build/diagnostic/ORDR_AI_2.323R.w3x")
DEFAULT_HUMAN_MAP: Final = DEFAULT_INSTALLED_MAP.with_name("ORDR_AI_2.323R_human.w3x")
DEFAULT_HUMAN_PLAY: Final = Path("runs/human-play")


@app.command("doctor")
def doctor() -> None:
    """Report the actual torch runtime and GPU, without launching a game."""
    console.print(f"PyTorch: {torch.__version__}")
    console.print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        console.print(f"GPU: {torch.cuda.get_device_name(0)}")
    console.print("Training requires the offline training-map copy and Allow Local Files.")


class MapKind(StrEnum):
    """Which bridge ``ord-ai build`` injects."""

    TRAINING = "training"
    HUMAN = "human"
    RECORD = "record"
    DIAGNOSTIC = "diagnostic"


DEFAULT_BUILDS: Final = {
    MapKind.TRAINING: DEFAULT_TRAINING_MAP,
    MapKind.HUMAN: DEFAULT_HUMAN_BUILD,
    MapKind.RECORD: DEFAULT_EVENTS_BUILD,
    MapKind.DIAGNOSTIC: DEFAULT_DIAGNOSTIC_BUILD,
}


@app.command("build")
def build(
    source: Path = DEFAULT_MAP,
    output: Path | None = None,
    difficulty: int = 1,
    isekai: bool = False,
    kind: MapKind = MapKind.TRAINING,
) -> None:
    """Build a training map (lobby: difficulty 1 Easy .. 6 Nightmare) and its catalog.

    --kind human builds the human-play map (the player picks the lobby and plays; the AI
    takes no action and the player's inputs are recorded). --kind record builds the
    real-game record map: the normal bridge plus attack, damage and death events in
    events.txt. --kind diagnostic adds ability effects (SPL) and the steps of the landing
    move W5R (W5I/W5S) to that record. Default output: build/ORDR_AI_2.323R.w3x,
    build/ORDR_AI_2.323R_human.w3x, build/record/ORDR_AI_2.323R.w3x,
    build/diagnostic/ORDR_AI_2.323R.w3x.
    """
    if output is None:
        output = DEFAULT_BUILDS[kind]
    check_install_allowed(output, WARCRAFT_MAPS, game_running)
    stage = Stage(difficulty=Difficulty(difficulty), isekai=isekai)
    catalog = build_map(
        source,
        output,
        stage,
        human=kind == MapKind.HUMAN,
        record="events"
        if kind == MapKind.RECORD
        else "diagnostic"
        if kind == MapKind.DIAGNOSTIC
        else None,
    )
    console.print(f"Training map: {output.resolve()}")
    console.print(f"Catalog: {len(catalog.units)} unit types, {len(catalog.actions)} actions")


@app.command("init")
def initialize(bridge_directory: Path, config: Path = DEFAULT_CONFIG) -> None:
    """Prepare a local editable config; the trainer issues the first reset."""
    settings = TrainSettings(
        catalog=Path("build/catalog.json").resolve(),
        bridge_directory=bridge_directory.resolve(),
        output=Path("runs/ordr").resolve(),
    )
    bridge_directory.mkdir(parents=True, exist_ok=True)
    _ = config.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Configuration: {config.resolve()}")
    console.print("Now load the training map offline, then run: ord-ai train")


@app.command("launch")
def launch_command(training_map: Path = DEFAULT_INSTALLED_MAP) -> None:
    """Open the training map through the logged-in Battle.net client."""
    launch(training_map)
    console.print(f"Warcraft III started with {training_map.resolve()}")


@app.command("inspect")
def inspect(config: Path = DEFAULT_CONFIG) -> None:
    """Print one real snapshot, failing rather than fabricating missing state."""
    settings = TrainSettings.model_validate_json(config.read_text("utf-8"))
    channel = StateChannel(settings.bridge_directory, settings.timeout)
    state = channel.read_state()
    if state is None:
        raise ProtocolError(detail="No complete state file; the training map is not connected")
    console.print_json(state.model_dump_json())


@app.command("train")
def train_command(config: Path = DEFAULT_CONFIG) -> None:
    """Collect real map transitions and update/save the actor-critic policy."""
    settings = TrainSettings.model_validate_json(config.read_text("utf-8"))
    metadata = train(settings)
    console.print_json(metadata.model_dump_json())


@app.command("record")
def record_command(
    episodes: int = 11,
    bridge_directory: Path = DEFAULT_BRIDGE,
    training_map: Path = DEFAULT_INSTALLED_MAP,
    output: Path = DEFAULT_RECORD,
) -> None:
    """Play real games with the rule teacher and log replay fields (no learning).

    The default output is runs/c2-teacher; pass --output for a new recording.
    """
    check_installed(training_map, built_catalog())
    settings = default_record_settings(bridge_directory, training_map, output)
    steps = record_teacher_episodes(settings, episodes)
    console.print(f"Recorded {steps} steps to {settings.output / 'transitions.jsonl'}")


@app.command("real-play")
def real_play(
    checkpoint: Path,
    episodes: int = 1,
    greedy: bool = False,
    output: Path = DEFAULT_REAL_PLAY,
    training_map: Path = DEFAULT_INSTALLED_MAP,
) -> None:
    """Test a trained checkpoint in real Warcraft III (no learning), logging each step."""
    catalog = built_catalog()
    check_installed(training_map, catalog)
    device = select_device(DeviceChoice.AUTO)
    learner = Learner(load_checkpoint(checkpoint, catalog, device), device)
    settings = default_record_settings(DEFAULT_BRIDGE, training_map).model_copy(
        update={"output": output.resolve()}
    )
    steps = record_episodes(settings, episodes, PolicyPlayer(learner, greedy))
    console.print(f"Played {steps} real steps; log {settings.output / 'transitions.jsonl'}")


@app.command("human-record")
def human_record_command(
    episodes: int = 1,
    output: Path = DEFAULT_HUMAN_PLAY,
    training_map: Path = DEFAULT_HUMAN_MAP,
    bridge_directory: Path = DEFAULT_BRIDGE,
    timeout: float = HUMAN_TIMEOUT,
) -> None:
    """Record your own play of the human-play map (the AI takes no action).

    Battle.net opens the map directly (-loadfile), closing any running Warcraft III; you pick
    the difficulty and Isekai in the lobby and pass the loading screen yourself. Battle.net's
    arguments are restored at the end. Rows are saved after every step.
    """
    settings = HumanSettings(
        training_map=training_map,
        bridge_directory=bridge_directory,
        output=output.resolve(),
        episodes=episodes,
        timeout=timeout,
    )
    try:
        steps = record_human(settings)
    except HumanPlayError as error:
        console.print(f"기록 실패: {error.detail}", markup=False)
        raise typer.Exit(1) from error
    except (LaunchError, BridgeTimeoutError, ProtocolError) as error:
        console.print(f"기록 실패: {error}", markup=False)
        raise typer.Exit(1) from error
    except KeyboardInterrupt:
        console.print("기록을 멈췄습니다(Ctrl+C). 지금까지의 기록은 저장되어 있습니다.")
        raise typer.Exit(130) from None
    console.print(f"기록 완료: {steps}스텝을 {settings.output / TRANSITIONS}에 저장했습니다.")


@app.command("human-demos")
def human_demos(recording: Path = DEFAULT_HUMAN_PLAY) -> None:
    """Turn a human-play recording into demonstrations.pt for imitation."""
    demos = human_demonstrations(recording, built_catalog())
    console.print(
        f"Demonstrations: {demos.dataset.resolve()} ({demos.samples} samples,",
        f"{demos.mapped} player inputs, {demos.unknown} codes not in the catalog)",
    )


@app.command("dashboard")
def dashboard_command(runs: Path = DEFAULT_RUNS, port: int = 8765) -> None:
    """Serve the read-only training dashboard at http://127.0.0.1:<port>/ (this PC only)."""
    serve(runs, port)


@app.command("play")
def play(config: Path = DEFAULT_CONFIG) -> None:
    """Use the configured resume checkpoint without updating its weights."""
    settings = TrainSettings.model_validate_json(config.read_text("utf-8"))
    if settings.resume is None:
        raise ProtocolError(detail="Set resume to a checkpoint directory before running play")
    catalog = Catalog.model_validate_json(settings.catalog.read_text("utf-8"))
    device = select_device(settings.device)
    learner = Learner(load_checkpoint(settings.resume, catalog, device), device)
    with StateChannel(settings.bridge_directory, settings.timeout) as channel:
        state = channel.reset()
        for _ in range(settings.steps):
            index = learner.choose(features(state, catalog), available_actions(state, catalog))
            state = channel.step(catalog.actions[index].code)
            if state.terminal:
                break
    console.print_json(state.model_dump_json())


def built_catalog(catalog: Path = DEFAULT_CATALOG, source: Path = DEFAULT_MAP) -> Catalog:
    """Load the last build's catalog, refusing a build older than the current bridge or catalog.

    Simulator roots always get the current bridge files, so the catalog must match them.
    """
    loaded = Catalog.model_validate_json(catalog.read_text("utf-8"))
    fresh = read_map(source.read_bytes()).catalog
    built = (catalog.parent / "war3map.j").read_text("utf-8")
    _, actions, functions = bridge_sources()
    if fresh.fingerprint != loaded.fingerprint or actions not in built or functions not in built:
        raise ProtocolError(detail="The build is older than the source; run ord-ai build first")
    return loaded


def check_installed(training_map: Path, catalog: Catalog) -> None:
    """Refuse a real game on an installed map whose catalog or bridge differs from build/.

    ``ord-ai build --output`` writes ``catalog.json`` and ``war3map.j`` beside the map.
    """
    catalog_file, script_file = (
        training_map.parent / "catalog.json",
        training_map.parent / "war3map.j",
    )
    _, actions, functions = bridge_sources()
    _, event_actions, event_functions = events_bridge_sources()
    _, diagnostic_actions, diagnostic_functions = diagnostic_bridge_sources()
    variants = (
        (actions, functions),
        (event_actions, event_functions),
        (diagnostic_actions, diagnostic_functions),
    )
    current = (
        catalog_file.is_file()
        and script_file.is_file()
        and Catalog.model_validate_json(catalog_file.read_text("utf-8")) == catalog
        and any(
            all(part in script_file.read_text("utf-8") for part in variant) for variant in variants
        )
    )
    if not current:
        command = f'ord-ai build --output "{training_map}"'
        raise ProtocolError(detail=f"The installed map is older than build/; run {command}")


def check_install_allowed(output: Path, maps: Path, running: Callable[[], bool]) -> None:
    """Refuse to write into the game's map folders while any Warcraft III process runs."""
    if output.resolve().is_relative_to(maps.resolve()) and running():
        detail = "Warcraft III가 실행 중이라 맵을 설치하지 않았습니다."
        raise ProtocolError(detail=f"{detail} 게임을 닫은 뒤 다시 실행하세요.")


def _stage(difficulty: int, isekai: bool) -> Stage:
    return Stage(difficulty=Difficulty(difficulty), isekai=isekai)


def _summary(records: list[EpisodeRecord]) -> dict[str, float]:
    count = max(1, len(records))
    return {
        "episodes": len(records),
        "mean_round": sum(record.round for record in records) / count,
        "max_round": max((record.round for record in records), default=0),
        "wins": sum(record.status == "WIN" for record in records),
        "zero_count_clears": sum(record.zero_count_clear for record in records),
        "mean_accepted": sum(record.accepted for record in records) / count,
    }


def _sim_config(path: Path) -> SimConfig:
    return SimConfig.model_validate_json(path.read_text("utf-8")) if path.exists() else SimConfig()


@app.command("sim-demos")
def sim_demos(config: Path = DEFAULT_SIM_CONFIG, difficulty: int = 1, isekai: bool = False) -> None:
    """Record rule-teacher games in the simulator for imitation (no learning)."""
    knobs = _sim_config(config)
    settings = DemoSettings(knobs.demo_episodes, knobs.envs, knobs.demo_seed)
    dataset = collect_demonstrations(
        built_catalog(), knobs.demos, _stage(difficulty, isekai), settings
    )
    console.print(f"Demonstrations: {dataset.resolve()}")


@app.command("sim-imitate")
def sim_imitate(config: Path = DEFAULT_SIM_CONFIG) -> None:
    """Behavior-clone the teacher demonstrations into a real-game-compatible checkpoint."""
    knobs = _sim_config(config)
    settings = ImitationSettings(
        knobs.epochs,
        knobs.batch_size,
        knobs.imitation_learning_rate,
        select_device(DeviceChoice(knobs.device)),
        knobs.seed,
        knobs.imitation_smoothing,
        knobs.imitation_wait_weight,
    )
    dataset = knobs.demos / "demonstrations.pt"
    agreement = imitate(built_catalog(), dataset, knobs.imitation, settings)
    console.print(f"Teacher agreement {agreement:.3f}; checkpoint {knobs.imitation.resolve()}")


@app.command("sim-train")
def sim_train(config: Path = DEFAULT_SIM_CONFIG) -> None:
    """A2C fine-tuning in parallel simulator games along the difficulty curriculum."""
    knobs = _sim_config(config)
    settings = FineTuneSettings(
        knobs.rl_steps,
        knobs.envs,
        knobs.rollout,
        knobs.rl_learning_rate,
        knobs.rl_seed,
        select_device(DeviceChoice(knobs.device)),
        knobs.checkpoint_every,
        knobs.demos / "demonstrations.pt" if knobs.bc_until > 0 else None,
        knobs.bc_every,
        knobs.bc_batch,
        knobs.bc_until,
        knobs.entropy_weight,
        knobs.imitation_smoothing,
        knobs.algorithm,
        knobs.ppo_epochs,
        knobs.ppo_minibatch,
        knobs.imitation_wait_weight,
        knobs.discount,
    )
    curriculum = fine_tune(built_catalog(), knobs.rl_start, knobs.rl, settings)
    console.print_json(data={"stage": curriculum.stage, "steps": curriculum.steps})


@app.command("sim-eval")
def sim_eval(
    output: Path,
    checkpoint: Path | None = None,
    difficulty: int = 1,
    greedy: bool = False,
    config: Path = DEFAULT_SIM_CONFIG,
) -> None:
    """Play simulator games with a checkpoint, or the teacher without one; no learning.

    Stage: difficulty 1-3 plain, 4-6 with Isekai (the curriculum rule).
    """
    knobs = _sim_config(config)
    settings = EvaluationSettings(
        knobs.eval_episodes,
        knobs.envs,
        knobs.eval_seed,
        select_device(DeviceChoice(knobs.device)),
        greedy,
    )
    stage = STAGES[difficulty - 1]
    records = evaluate(built_catalog(), checkpoint, output, stage, settings)
    summary = _summary(records)
    _ = output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    console.print_json(data=summary)


@app.command("migrate-checkpoint")
def migrate_checkpoint_command(source: Path, destination: Path) -> None:
    """Convert a checkpoint from before newer actions or inputs to build/catalog.json.

    Old actions keep their weights by wire code, new ones start at the mean old bias; the
    newer inputs (hidden chests, round boss) get zero weights, so outputs match the old
    policy while they are 0.
    """
    catalog = built_catalog()
    metadata = migrate_checkpoint(source, destination, catalog)
    _ = load_checkpoint(destination, catalog, "cpu")
    console.print_json(metadata.model_dump_json(exclude_none=True))


@app.command("smoke")
def smoke(output: Path = DEFAULT_SMOKE, device: DeviceChoice = DeviceChoice.AUTO) -> None:
    """Test neural updates/checkpoint loading on a synthetic bandit, not ORDR."""
    result = run_smoke(output, device)
    console.print_json(result.model_dump_json())
    if result.probability_after <= result.probability_before or not result.checkpoint_equal:
        raise ProtocolError(detail="Synthetic learning or checkpoint verification failed")
