"""Play real-game episodes without learning: the rule teacher or a trained policy."""

from contextlib import ExitStack
from pathlib import Path
from typing import Final, Protocol, final

from rich.console import Console

from ord_ai.catalog import ActionKind, Catalog, available_actions
from ord_ai.channel import BridgeTimeoutError, StateChannel
from ord_ai.events import read_events
from ord_ai.humanplay import WAR3_LOG, BreakWatch, GameWatch
from ord_ai.launcher import GameHost, understands_exit
from ord_ai.learning import Learner
from ord_ai.observation import features, transition_reward
from ord_ai.protocol import GameState
from ord_ai.teacher import Teacher
from ord_ai.telemetry import Telemetry, read_telemetry
from ord_ai.training import TrainSettings, transition_log

DEFAULT_OUTPUT: Final = Path("runs/c2-teacher")
# Consecutive refused chest scans after which a real-game test masks them for the rest of
# the game: every scan was refused in the first real test (0/307, runs/real-ppo5-easy), and
# a greedy policy repeats a refused scan while the chest stays.
SCAN_REFUSAL_LIMIT: Final = 5
# Full bridge telemetry per export (unit handles included), beside transitions.jsonl whose
# ``units`` field keeps only [type, x, y, order]; diagnostics only, policies never read it.
TELEMETRY_ARCHIVE: Final = "telemetry.jsonl"


def telemetry_line(record: Telemetry) -> str:
    """One ``telemetry.jsonl`` line: the whole record, so same-type units stay apart."""
    return record.model_dump_json() + "\n"


class Player(Protocol):
    """Chooses real-game actions; the teacher and a trained policy both fit."""

    def choose(self, state: GameState, catalog: Catalog, mask: tuple[bool, ...]) -> int:
        """Return the index of an allowed catalog action."""
        ...

    def observe(self, result: GameState) -> None:
        """See the map's answer to the last action."""
        ...

    def reset(self) -> None:
        """Forget the previous episode."""
        ...


@final
class PolicyPlayer:
    """A checkpoint policy without weight updates; greedy picks the most likely action.

    After ``SCAN_REFUSAL_LIMIT`` refused chest scans in a row, scans are masked until the
    next game, so the rest of the game still yields comparable play.
    """

    def __init__(self, learner: Learner, greedy: bool) -> None:
        """Wrap ``learner``."""
        self.learner: Learner = learner
        self.greedy: bool = greedy
        self.scan_refusals: int = 0
        self.scans_blocked: bool = False
        self._scanned: bool = False

    def choose(self, state: GameState, catalog: Catalog, mask: tuple[bool, ...]) -> int:
        """Pick by the policy distribution, or its mode when greedy."""
        if self.scans_blocked:
            mask = tuple(
                allowed and action.kind is not ActionKind.TREASURE
                for action, allowed in zip(catalog.actions, mask, strict=True)
            )
        observation = features(state, catalog)
        index = (
            self.learner.best(observation, mask)
            if self.greedy
            else self.learner.choose(observation, mask)
        )
        self._scanned = catalog.actions[index].kind is ActionKind.TREASURE
        return index

    def observe(self, result: GameState) -> None:
        """Count refused chest scans; the policy keeps no other memory between steps."""
        if not self._scanned:
            return
        self.scan_refusals = 0 if result.action_ok == 1 else self.scan_refusals + 1
        self.scans_blocked = self.scans_blocked or self.scan_refusals >= SCAN_REFUSAL_LIMIT

    def reset(self) -> None:
        """A new game may accept scans again."""
        self.scan_refusals, self.scans_blocked, self._scanned = 0, False, False


def record_teacher_episodes(settings: TrainSettings, episodes: int) -> int:
    """Play ``episodes`` real games with the rule teacher; return the number of logged steps."""
    return record_episodes(settings, episodes, Teacher())


def _bind_exit(
    stack: ExitStack, host: GameHost | None, channel: StateChannel, training_map: Path | None
) -> None:
    """Close the game through its own bridge first (control action -2) when the map has it.

    Registered after the channel, so it runs before the channel stops watching.
    """
    if host is not None and training_map is not None and understands_exit(training_map):
        _ = stack.callback(host.close, channel.request_exit)


def record_episodes(settings: TrainSettings, episodes: int, player: Player) -> int:
    """Play ``episodes`` real games with ``player``; return the number of logged steps.

    Nothing is learned. Rows go to ``<output>/transitions.jsonl`` in the training log format
    plus the bridge's replay fields (game time, ``Gv``, unit positions, spawned units). The
    record build's combat events go to ``events.jsonl`` and the full telemetry (unit
    handles included) to ``telemetry.jsonl``, one line per export, and a map
    Warcraft III reopens during a game is logged to ``display_breaks.jsonl``.
    """
    catalog = Catalog.model_validate_json(settings.catalog.read_text("utf-8"))
    settings.output.mkdir(parents=True, exist_ok=True)
    console = Console()
    breaks = BreakWatch(
        GameWatch(settings.bridge_directory.parents[1] / WAR3_LOG), settings.output, console
    )
    teacher = player
    logged = 0
    with ExitStack() as stack:
        host = (
            None
            if settings.training_map is None
            # A real-game test always ends by closing the game it opened.
            else stack.enter_context(
                GameHost(
                    settings.training_map,
                    close_game=True,
                    war3log=(
                        settings.bridge_directory.parents[1] / WAR3_LOG,
                        settings.output / "war3log",
                    ),
                )
            )
        )
        channel = stack.enter_context(
            StateChannel(settings.bridge_directory, settings.timeout, host)
        )
        _bind_exit(stack, host, channel, settings.training_map)
        log = stack.enter_context(
            (settings.output / "transitions.jsonl").open("a", encoding="utf-8")
        )
        events = stack.enter_context((settings.output / "events.jsonl").open("a", encoding="utf-8"))
        archive = stack.enter_context(
            (settings.output / TELEMETRY_ARCHIVE).open("a", encoding="utf-8")
        )
        # Once this Warcraft III process reopened map archives, the next game restarts in it.
        reopened = False
        for _ in range(episodes):
            state = channel.reset(restart_in_place=reopened)
            reopened = reopened and channel.restarted_in_place
            teacher.reset()
            previous = read_telemetry(settings.bridge_directory, state.episode, state.sequence)
            if previous is not None:
                _ = archive.write(telemetry_line(previous))
                archive.flush()
            console.print(
                f"reset episode={state.episode}"
                + (" (restarted in place)" if channel.restarted_in_place else "")
            )
            breaks.start()
            while not state.terminal and logged < settings.steps:
                index = teacher.choose(state, catalog, available_actions(state, catalog))
                try:
                    after = channel.step(catalog.actions[index].code)
                except BridgeTimeoutError:
                    if host is None:
                        raise
                    console.print(f"step timeout episode={state.episode}; next episode")
                    break
                teacher.observe(after)
                current = read_telemetry(settings.bridge_directory, after.episode, after.sequence)
                combat = read_events(settings.bridge_directory, after.episode, after.sequence)
                if combat is not None:
                    _ = events.write(combat.model_dump_json() + "\n")
                    events.flush()
                if current is not None:
                    _ = archive.write(telemetry_line(current))
                    archive.flush()
                breaks.check(after.episode, current)
                logged += 1
                row = transition_log(
                    logged,
                    catalog.actions[index].code,
                    transition_reward(state, after),
                    after,
                    (previous, current),
                )
                _ = log.write(row.model_dump_json(exclude_none=True) + "\n")
                log.flush()
                previous, state = current, after
            reopened = reopened or breaks.noted
            console.print(f"episode={state.episode} ended {state.status.name} round={state.round}")
    return logged


def default_record_settings(
    bridge_directory: Path, training_map: Path, output: Path = DEFAULT_OUTPUT
) -> TrainSettings:
    """Settings for a replay recording (by default into ``runs/c2-teacher``)."""
    return TrainSettings(
        catalog=Path("build/catalog.json").resolve(),
        bridge_directory=bridge_directory,
        output=output.resolve(),
        steps=200_000,
        # A launch can take 2-3 minutes to load (2026-09-30: 04:43:53 -> 04:46:11); a shorter
        # wait kills a loading game and relaunches it.
        timeout=300.0,
        training_map=training_map,
    )
