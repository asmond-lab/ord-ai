"""Record the user's own real-game play of the human-play map, and turn it into demonstrations.

The recorder takes no game action: it answers every export with the no-op ``wait`` (code 0),
because the bridge writes state.txt only in reply to a command. What the player did between
two exports comes from ``human.txt`` (:mod:`ord_ai.human`).

Like the AI's real games, the recorder opens the human-play map directly: Battle.net starts
Warcraft III with ``-loadfile`` (:class:`ord_ai.launcher.GameHost`); a Warcraft III the
recorder did not start stops the launch instead of being closed. The player picks the lobby
options (difficulty, Isekai) and passes the loading screen; no key is sent and no window is
touched. A map opened with ``-loadfile`` can lose
its models about 17 minutes after the game started, when Warcraft III reopens the ladder
maps (War3Log "Opening mod"; 2026-09-29, twice). Such a reopening during a game is logged to
``display_breaks.jsonl``, and the rows from that game time on are left out of demonstrations.
"""

import json
import re
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Final, Protocol, TextIO

import torch
from pydantic import BaseModel, ConfigDict
from rich.console import Console

from ord_ai.catalog import BasicAction, Catalog, available_actions
from ord_ai.channel import BridgeTimeoutError, StateChannel
from ord_ai.human import HumanInput, HumanRecord, read_human
from ord_ai.launcher import GameHost, game_running
from ord_ai.learning import discounted_returns
from ord_ai.observation import features, transition_reward
from ord_ai.protocol import GameState, ProtocolError, Status, parse_snapshot
from ord_ai.telemetry import Telemetry, read_telemetry
from ord_ai.training import transition_log

# A person picks the lobby options and presses through the loading screen: wait long.
HUMAN_TIMEOUT: Final = 1800.0
# Warcraft III writes a fresh log at every start, including the map's JASS compile errors.
WAR3_LOG: Final = Path("Logs/War3Log.txt")
GAME_STARTED: Final = "GameMain Started"
JASS_ERROR: Final = "Map contains invalid Jass scripts"
MAP_OPENED: Final = "Opening mod - "
LOG_TIME: Final = re.compile(r"^(\d{1,2})/(\d{1,2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})\s")
TRANSITIONS: Final = "transitions.jsonl"
DEMONSTRATIONS: Final = "demonstrations.pt"
DISPLAY_BREAKS: Final = "display_breaks.jsonl"


@dataclass(frozen=True, slots=True)
class HumanSettings:
    """What ``ord-ai human-record`` needs."""

    training_map: Path
    bridge_directory: Path
    output: Path
    episodes: int = 1
    timeout: float = HUMAN_TIMEOUT


class HumanPlayError(RuntimeError):
    """Why the recording cannot go on, in Korean for the person at the keyboard."""

    def __init__(self, detail: str) -> None:
        """Keep the explanation."""
        self.detail: str = detail
        super().__init__(detail)


class GameWatch:
    """Raise :class:`HumanPlayError` once the game is closed or rejected the map.

    ``StateChannel`` calls it every couple of seconds while it waits for the bridge, so a map
    that Warcraft III cannot compile ends the recording at once instead of after the timeout.
    The player starts the game, so a game that is not running yet is waited for.
    """

    def __init__(self, log: Path, running: Callable[[], bool] = game_running) -> None:
        """Watch ``log`` (Warcraft III's War3Log.txt) and the game process."""
        self.log: Path = log
        self.running: Callable[[], bool] = running
        self.seen: bool = False

    def __call__(self) -> None:
        """Check the game once."""
        if not self.running():
            if self.seen:
                raise HumanPlayError(detail="Warcraft III가 꺼졌습니다. 기록을 멈춥니다.")
            return
        self.seen = True
        errors = self.compile_errors()
        if errors:
            raise HumanPlayError(
                detail=(
                    "Warcraft III가 기록용 맵 스크립트를 컴파일하지 못해 메인 메뉴로 돌아갔습니다. "
                    f"첫 오류: {errors[0]} (전체 {len(errors)}개, {self.log})"
                )
            )

    def _session(self) -> list[str]:
        """The log lines since Warcraft III's latest start."""
        try:
            lines = self.log.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return []
        starts = [row for row, line in enumerate(lines) if GAME_STARTED in line]
        return lines[starts[-1] :] if starts else lines

    def compile_errors(self) -> list[str]:
        """The JASS compile errors Warcraft III logged since its latest start."""
        recent = self._session()
        return [line.split(JASS_ERROR, 1)[1].strip(" ,") for line in recent if JASS_ERROR in line]

    def openings_after(self, moment: datetime) -> list[tuple[datetime, str]]:
        """Map archives Warcraft III opened later than ``moment``, by the logged local time.

        Warcraft III writes War3Log.txt in delayed chunks, so a line can appear minutes after
        its own time; comparing the logged times keeps an early startup line from counting.
        """
        found: list[tuple[datetime, str]] = []
        for line in self._session():
            stamp = _logged_at(line, moment)
            if MAP_OPENED in line and stamp is not None and stamp > moment:
                found.append((stamp, line))
        return found


def _logged_at(line: str, moment: datetime) -> datetime | None:
    """The ``M/D HH:MM:SS.mmm`` time a War3Log line starts with, in ``moment``'s year."""
    matched = LOG_TIME.match(line)
    if matched is None:
        return None
    month, day, hour, minute, second, millisecond = (int(part) for part in matched.groups())
    return moment.replace(
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=second,
        microsecond=millisecond * 1000,
    )


class Channel(Protocol):
    """The part of :class:`StateChannel` the recorder uses."""

    def reset(self) -> GameState:
        """Start an episode and return its first PLAYING state."""
        ...

    def step(self, action: int) -> GameState:
        """Send one action code and return the map's answer."""
        ...


OpenChannel = Callable[[HumanSettings, GameWatch], AbstractContextManager[Channel]]


class GameLauncher(Protocol):
    """The part of :class:`GameHost` a recording uses."""

    def relaunch(self) -> None:
        """Close every Warcraft III and start one with the map."""
        ...


class PlayerPlaysLaunchedGame:
    """The game control of a recording: Battle.net opens the map, the player does the rest."""

    def __init__(self, host: GameLauncher, console: Console) -> None:
        """Launch through ``host`` (a :class:`GameHost` for the human-play map)."""
        self.host: GameLauncher = host
        self.console: Console = console

    def relaunch(self) -> None:
        """Close any Warcraft III and open the human-play map directly."""
        self.host.relaunch()
        self.console.print(
            (
                "Warcraft III가 기록용 맵을 바로 엽니다. 로비에서 난이도·이세계를 고르고 시작한 뒤 "
                "로딩 화면에서 아무 키나 누르세요. 맵이 시작되면 기록이 시작됩니다."
            ),
            markup=False,
        )

    def press_any_key(self) -> None:
        """The player passes the loading screen."""


@contextmanager
def attended_channel(settings: HumanSettings, watch: GameWatch) -> Generator[StateChannel]:
    """The real channel: Battle.net opens the map, no key presses, a long wait.

    The Battle.net arguments are restored on leaving; Warcraft III stays open.
    """
    with GameHost(settings.training_map) as host:
        game = PlayerPlaysLaunchedGame(host, Console())
        with StateChannel(
            settings.bridge_directory, settings.timeout, game, attended=True, watch=watch
        ) as channel:
            yield channel


def _snapshot(directory: Path, state: GameState) -> str:
    """The raw state.txt text of ``state`` (the bridge writes only on command)."""
    text = (directory / "state.txt").read_text(encoding="utf-8-sig")
    if parse_snapshot(text) != state:
        raise ProtocolError(detail="state.txt changed before its snapshot was kept")
    return text


def _inputs(directory: Path, state: GameState) -> HumanRecord:
    record = read_human(directory, state.episode, state.sequence)
    if record is None:
        raise ProtocolError(
            detail="No human.txt for this export: open the human-play map (--kind human)"
        )
    return record


def _row(
    step: int,
    before: tuple[GameState, str],
    after: GameState,
    record: HumanRecord,
    telemetry: tuple[Telemetry | None, Telemetry | None],
) -> str:
    state, snapshot = before
    action = next((item.code for item in record.inputs if item.code is not None), 0)
    log = transition_log(step, action, transition_reward(state, after), after, telemetry)
    row: dict[str, object] = {
        **log.model_dump(mode="json", exclude_none=True),
        "human_inputs": [item.model_dump(mode="json") for item in record.inputs],
        "snapshot": snapshot,
    }
    if record.dropped:
        row["human_dropped"] = record.dropped
    return json.dumps(row, ensure_ascii=False) + "\n"


def record_play(
    channel: Channel,
    settings: HumanSettings,
    log: TextIO,
    console: Console,
    breaks: "BreakWatch | None" = None,
) -> int:
    """Log ``settings.episodes`` games the player plays; return the number of rows.

    ``breaks`` logs a map archive Warcraft III reopens during a game (``display_breaks.jsonl``).
    """
    directory = settings.bridge_directory
    logged = 0
    for _ in range(settings.episodes):
        state = channel.reset()
        snapshot = _snapshot(directory, state)
        _ = _inputs(directory, state)
        previous = read_telemetry(directory, state.episode, state.sequence)
        console.print(f"recording episode={state.episode}")
        if breaks is not None:
            breaks.start()
        while not state.terminal:
            try:
                after = channel.step(BasicAction.WAIT)
            except BridgeTimeoutError:
                console.print(f"no answer from the game; episode={state.episode} stops here")
                break
            record = _inputs(directory, after)
            current = read_telemetry(directory, after.episode, after.sequence)
            after_snapshot = _snapshot(directory, after)
            if breaks is not None:
                breaks.check(after.episode, current)
            # Nothing is learned from the lobby or the loading screen.
            if Status.WAITING not in (state.status, after.status):
                logged += 1
                row = _row(logged, (state, snapshot), after, record, (previous, current))
                _ = log.write(row)
                log.flush()
            previous, state, snapshot = current, after, after_snapshot
        console.print(f"episode={state.episode} ended {state.status.name} round={state.round}")
    return logged


class BreakWatch:
    """Notice, once per game, a map archive Warcraft III reopened after the game started.

    The logged time of the War3Log line dates the break; its game time is the current
    export's game time minus the seconds since then (the log is written late, in chunks).
    """

    def __init__(self, watch: GameWatch, output: Path, console: Console) -> None:
        """Write breaks to ``output``/display_breaks.jsonl."""
        self.watch: GameWatch = watch
        self.output: Path = output
        self.console: Console = console
        self.since: datetime = datetime.now().astimezone()
        self.noted: bool = False

    def start(self) -> None:
        """A game has just started."""
        self.since, self.noted = datetime.now().astimezone(), False

    def check(self, episode: int, telemetry: Telemetry | None) -> None:
        """Look for a reopening once more."""
        if self.noted:
            return
        late = self.watch.openings_after(self.since)
        if not late:
            return
        self.noted = True
        logged, line = late[0]
        now = datetime.now().astimezone()
        game_time = (
            None
            if telemetry is None
            else round(telemetry.game_time - (now - logged).total_seconds(), 1)
        )
        entry = {
            "episode": episode,
            "game_time": game_time,
            "logged_at": logged.isoformat(timespec="milliseconds"),
            "noticed_at": now.isoformat(timespec="seconds"),
            "noticed_game_time": None if telemetry is None else telemetry.game_time,
            "log_line": line,
        }
        with (self.output / DISPLAY_BREAKS).open("a", encoding="utf-8") as stream:
            _ = stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.console.print(
            (
                f"게임 중 Warcraft III가 맵 파일을 다시 열었습니다({logged:%H:%M:%S}, 게임 시간 약 "
                f"{game_time}초). 이후 화면(모델)이 깨졌을 수 있어 {DISPLAY_BREAKS}에 기록했습니다."
            ),
            markup=False,
        )


def record_human(settings: HumanSettings, open_channel: OpenChannel = attended_channel) -> int:
    """Record the player's games of the human-play map, opened through Battle.net.

    Any running Warcraft III is closed first; Battle.net's arguments are restored at the end.
    """
    settings.output.mkdir(parents=True, exist_ok=True)
    console = Console()
    watch = GameWatch(settings.bridge_directory.parents[1] / WAR3_LOG)
    with ExitStack() as stack:
        channel = stack.enter_context(open_channel(settings, watch))
        log = stack.enter_context((settings.output / TRANSITIONS).open("a", encoding="utf-8"))
        return record_play(
            channel, settings, log, console, BreakWatch(watch, settings.output, console)
        )


class HumanRow(BaseModel):
    """The fields of a recorded row the demonstrations need."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    episode: int
    reward: float
    human_inputs: tuple[HumanInput, ...]
    snapshot: str
    game_time: float = 0.0


def display_breaks(recording: Path) -> dict[int, float]:
    """The earliest game time per episode from which the display was broken."""
    path = recording / DISPLAY_BREAKS
    if not path.exists():
        return {}
    earliest: dict[int, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = DisplayBreak.model_validate_json(line)
            start = 0.0 if entry.game_time is None else entry.game_time
            earliest[entry.episode] = min(start, earliest.get(entry.episode, start))
    return earliest


class DisplayBreak(BaseModel):
    """One ``display_breaks.jsonl`` line; an unknown game time drops the whole episode."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    episode: int
    game_time: float | None


@dataclass(frozen=True, slots=True)
class HumanDemos:
    """What :func:`human_demonstrations` wrote."""

    dataset: Path
    samples: int
    mapped: int
    unknown: int  # mapped codes the current catalog no longer has


def _episodes(rows: list[HumanRow]) -> list[list[HumanRow]]:
    groups: list[list[HumanRow]] = []
    for row in rows:
        if groups and groups[-1][0].episode == row.episode:
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def human_demonstrations(recording: Path, catalog: Catalog) -> HumanDemos:
    """Write ``<recording>/demonstrations.pt`` in the ``simtrain.Demonstrations`` format.

    One sample per mapped input, observed at the step's starting snapshot; a step without a
    mapped input is one ``wait`` sample. The player's action was possible in the game, so it
    is always allowed in its sample's mask. Returns are discounted per episode. Rows from an
    episode's display break on (``display_breaks.jsonl``) are left out.
    """
    lines = (recording / TRANSITIONS).read_text(encoding="utf-8").splitlines()
    breaks = display_breaks(recording)
    rows = [
        row
        for row in (HumanRow.model_validate_json(line) for line in lines if line.strip())
        if row.game_time < breaks.get(row.episode, float("inf"))
    ]
    if not rows:
        raise ProtocolError(detail=f"No recorded rows in {recording / TRANSITIONS}")
    index = {action.code: position for position, action in enumerate(catalog.actions)}
    observations: list[tuple[float, ...]] = []
    masks: list[list[bool]] = []
    actions: list[int] = []
    returns: list[float] = []
    mapped = unknown = 0
    for episode in _episodes(rows):
        rewards = [row.reward for row in episode]
        for row, value in zip(
            episode, discounted_returns(rewards, [False] * len(rewards), 0.0), strict=True
        ):
            state = parse_snapshot(row.snapshot)
            if state is None:
                raise ProtocolError(detail="A recorded snapshot is incomplete")
            observation = features(state, catalog)
            mask = list(available_actions(state, catalog))
            codes = [item.code for item in row.human_inputs if item.code is not None]
            known = [code for code in codes if code in index]
            mapped += len(known)
            unknown += len(codes) - len(known)
            for code in known or [int(BasicAction.WAIT)]:
                sample = mask.copy()
                sample[index[code]] = True
                observations.append(observation)
                masks.append(sample)
                actions.append(index[code])
                returns.append(value)
    dataset = recording / DEMONSTRATIONS
    torch.save(
        {
            "catalog_fingerprint": catalog.fingerprint,
            "observations": torch.tensor(observations, dtype=torch.float32),
            "masks": torch.tensor(masks, dtype=torch.bool),
            "actions": torch.tensor(actions, dtype=torch.long),
            "returns": torch.tensor(returns, dtype=torch.float32),
        },
        dataset,
    )
    return HumanDemos(dataset, len(actions), mapped, unknown)
