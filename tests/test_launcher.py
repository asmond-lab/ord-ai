import json
from pathlib import Path
from typing import cast

import pytest

from ord_ai import launcher
from ord_ai.launcher import (
    ARGUMENTS_KEY,
    GAME,
    LAUNCH_PREFIX,
    LAUNCHER,
    GameHost,
    LaunchError,
    set_launch_arguments,
)

Settings = dict[str, dict[str, dict[str, str]]]


def test_launch_arguments_round_trip_preserves_other_settings(tmp_path: Path) -> None:
    # Given
    config = tmp_path / "Battle.net.config"
    original = {"Client": {"Install": {"Path": "x"}}, "Games": {"w3": {"ServerUid": "w3"}}}
    _ = config.write_text(json.dumps(original), encoding="utf-8")

    # When
    previous = set_launch_arguments(config, '-launch -loadfile "C:/maps/a.w3x"')
    applied = cast("Settings", json.loads(config.read_text("utf-8")))
    _ = set_launch_arguments(config, previous)

    # Then
    assert previous is None
    assert applied["Games"]["w3"][ARGUMENTS_KEY] == '-launch -loadfile "C:/maps/a.w3x"'
    assert json.loads(config.read_text("utf-8")) == original


class Processes:
    """Battle.net and Warcraft III processes, changed the way they react to the launcher."""

    def __init__(self) -> None:
        self.launcher: int = 1
        self.games: set[int] = set()
        self.next_pid: int = 1000
        self.closes_on_request: bool = True
        # What each window shows after WM_CLOSE: the recorded quit prompt or something else.
        self.prompt_distance: float | None = None
        self.quits_on_click: bool = True
        self.clicked: list[int] = []
        self.asked: list[int] = []
        self.killed: list[int] = []

    def count(self, image: str) -> int:
        return self.launcher if image == LAUNCHER else len(self.games)

    def pids(self, image: str) -> list[int]:
        return sorted(self.games) if image == GAME else []

    def kill_pids(self, pids: set[int]) -> None:
        self.killed += sorted(pids & self.games)
        self.games -= pids

    def request_close(self, pids: set[int]) -> list[dict[str, object]]:
        asked = sorted(pids & self.games)
        self.asked += asked
        if self.closes_on_request:
            self.games -= pids
        return [{"hwnd": pid * 10, "pid": pid, "posted": True, "error": 0} for pid in asked]

    def prompt_state(self, window: int) -> float | None:
        return self.prompt_distance if any(window == pid * 10 for pid in self.games) else None

    def click_quit(self, window: int) -> dict[str, object]:
        self.clicked.append(window)
        if self.quits_on_click:
            self.games -= {window // 10}
        return {"hwnd": window, "x": 842, "y": 504, "posted": True, "error": 0}

    def snapshot(self, window: int, path: Path) -> bool:
        # Only windows of games that are still running can be captured.
        if not any(window == pid * 10 for pid in self.games):
            return False
        _ = path.write_bytes(b"BM")
        return True

    def windows(self, pids: set[int]) -> list[dict[str, object]]:
        return [{"hwnd": pid * 10, "pid": pid, "title": "Warcraft III"} for pid in sorted(pids)]

    def run_launcher(self, argument: str | None) -> None:
        if argument == "--exec=shutdown":
            self.launcher = 0
        elif argument == '"--exec=launch W3"':
            self.next_pid += 1
            self.games.add(self.next_pid)
        else:
            self.launcher = 1


def close_records(folder: Path) -> list[dict[str, object]]:
    lines = (folder / "closes.jsonl").read_text("utf-8").splitlines()
    return [cast("dict[str, object]", json.loads(line)) for line in lines]


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> Processes:
    fake = Processes()
    monkeypatch.setattr(launcher, "_count", fake.count)
    monkeypatch.setattr(launcher, "_pids", fake.pids)
    monkeypatch.setattr(launcher, "_kill_pids", fake.kill_pids)
    monkeypatch.setattr(launcher, "_request_close", fake.request_close)
    monkeypatch.setattr(launcher, "_windows", fake.windows)
    monkeypatch.setattr(launcher, "_snapshot_window", fake.snapshot)
    monkeypatch.setattr(launcher, "_prompt_state", fake.prompt_state)
    monkeypatch.setattr(launcher, "_click_quit", fake.click_quit)
    monkeypatch.setattr(launcher, "PROMPT_SECONDS", 0.0)
    monkeypatch.setattr(launcher, "GRACEFUL_CLOSE_SECONDS", 0.0)
    monkeypatch.setattr(launcher, "_run_launcher", fake.run_launcher)
    return fake


@pytest.mark.parametrize(("close_game", "left_running"), [(True, 0), (False, 1)])
def test_a_session_closes_the_game_it_started_only_when_asked(
    tmp_path: Path, processes: Processes, close_game: bool, left_running: int
) -> None:
    # Given
    config = tmp_path / "Battle.net.config"
    _ = config.write_text(json.dumps({"Games": {"w3": {"ServerUid": "w3"}}}), encoding="utf-8")

    # When
    with GameHost(tmp_path / "map.w3x", config, close_game=close_game) as host:
        host.relaunch()

    # Then: the arguments are restored and Battle.net is running again either way.
    assert (processes.launcher, len(processes.games)) == (1, left_running)
    assert json.loads(config.read_text("utf-8")) == {"Games": {"w3": {"ServerUid": "w3"}}}


def test_arguments_a_killed_session_of_another_map_left_behind_are_not_restored(
    tmp_path: Path, processes: Processes
) -> None:
    # Given: a killed AI session left its own -loadfile arguments for the record map.
    config = tmp_path / "Battle.net.config"
    left = f'{LAUNCH_PREFIX}"C:/maps/record.w3x"'
    _ = config.write_text(json.dumps({"Games": {"w3": {ARGUMENTS_KEY: left}}}), encoding="utf-8")

    # When
    with GameHost(tmp_path / "human.w3x", config) as host:
        host.relaunch()

    # Then
    assert len(processes.games) == 1
    assert json.loads(config.read_text("utf-8")) == {"Games": {"w3": {}}}


@pytest.mark.parametrize(
    ("closes_on_request", "method", "killed"), [(True, "graceful", 0), (False, "forced", 1)]
)
def test_closing_the_game_asks_first_and_keeps_its_final_log(
    tmp_path: Path, processes: Processes, closes_on_request: bool, method: str, killed: int
) -> None:
    # Given: a running session game whose War3Log.txt the next launch would replace.
    config = tmp_path / "Battle.net.config"
    _ = config.write_text(json.dumps({"Games": {"w3": {}}}), encoding="utf-8")
    log, folder = tmp_path / "War3Log.txt", tmp_path / "war3log"
    _ = log.write_text("9/30 12:00:00.000  GameMain Started\nlast line\n", encoding="utf-8")
    processes.closes_on_request = closes_on_request

    # When
    with GameHost(tmp_path / "map.w3x", config, close_game=True, war3log=(log, folder)) as host:
        host.relaunch()

    # Then: the first launch had nothing to close; leaving closed the game once.
    (record,) = close_records(folder)
    assert (record["method"], record["bytes"]) == (method, log.stat().st_size)
    assert record["pids"] == processes.asked == [1001]
    audit = cast("dict[str, object]", record["audit"])
    assert audit["asked"] == [{"hwnd": 10010, "pid": 1001, "posted": True, "error": 0}]
    assert audit["windows_before"] == [{"hwnd": 10010, "pid": 1001, "title": "Warcraft III"}]
    # The windows left when the wait ran out are recorded only for a forced close.
    assert ("windows_at_fallback" in audit) is (method == "forced")
    # A forced close first saves each window it is about to terminate.
    if method == "forced":
        (shot,) = cast("list[dict[str, object]]", audit["fallback_snapshots"])
        assert (shot["hwnd"], shot["ok"]) == (10010, True)
        assert (folder / cast("str", shot["file"])).read_bytes() == b"BM"
    else:
        assert "fallback_snapshots" not in audit
    assert (folder / cast("str", record["copy"])).read_text("utf-8") == log.read_text("utf-8")
    assert (len(processes.killed), processes.games) == (killed, set())


def test_a_warcraft_iii_the_session_did_not_start_is_never_closed(
    tmp_path: Path, processes: Processes
) -> None:
    # Given: the user's own Warcraft III (pid 7) is running before the session starts.
    config = tmp_path / "Battle.net.config"
    _ = config.write_text(json.dumps({"Games": {"w3": {}}}), encoding="utf-8")
    processes.games = {7}

    # When
    with (
        pytest.raises(LaunchError, match="not started by this session"),
        GameHost(tmp_path / "map.w3x", config, close_game=True) as host,
    ):
        host.relaunch()

    # Then: nothing was asked to close or terminated, and no game was started.
    assert (processes.asked, processes.killed, processes.games) == ([], [], {7})


def test_relaunch_closes_only_the_game_it_started_before(
    tmp_path: Path, processes: Processes
) -> None:
    # Given: a session game (pid 1001) is running.
    config = tmp_path / "Battle.net.config"
    _ = config.write_text(json.dumps({"Games": {"w3": {}}}), encoding="utf-8")
    with GameHost(tmp_path / "map.w3x", config, close_game=True) as host:
        host.relaunch()

        # When
        host.relaunch()

        # Then: the first game was asked to close and the new one is the session's game.
        assert (processes.asked, processes.games, host.owned) == ([1001], {1002}, {1002})


@pytest.mark.parametrize(
    "case",
    [
        # distance, the click closes the game, method, closed by, clicked
        (0.0, True, "graceful", "prompt_quit", [10010]),
        (0.0, False, "forced", None, [10010]),
        (29.4, True, "forced", None, []),
        (None, True, "forced", None, []),
    ],
)
def test_the_quit_prompt_is_answered_only_where_it_is_recognised(
    tmp_path: Path,
    processes: Processes,
    case: tuple[float | None, bool, str, str | None, list[int]],
) -> None:
    distance, on_click, method, closed_by, clicked = case
    # Given: the session game stays open after WM_CLOSE and shows the prompt (distance 0),
    # another picture (29.4, the ending screen before the prompt) or cannot be captured.
    config = tmp_path / "Battle.net.config"
    _ = config.write_text(json.dumps({"Games": {"w3": {}}}), encoding="utf-8")
    log, folder = tmp_path / "War3Log.txt", tmp_path / "war3log"
    _ = log.write_text("9/30 12:00:00.000  GameMain Started\n", encoding="utf-8")
    processes.closes_on_request = False
    processes.prompt_distance = distance
    processes.quits_on_click = on_click

    # When
    with GameHost(tmp_path / "map.w3x", config, close_game=True, war3log=(log, folder)) as host:
        host.relaunch()

    # Then: only a window showing the prompt is clicked; a click it ignores is re-read.
    (record,) = close_records(folder)
    audit = cast("dict[str, object]", record["audit"])
    assert (record["method"], audit["closed_by"], processes.clicked) == (method, closed_by, clicked)
    (prompt,) = cast("list[dict[str, object]]", audit["prompts"])
    assert (prompt["distance"], prompt["prompt"]) == (distance, distance == 0.0)
    assert ("distance_after_click" in prompt) is (clicked != [] and not on_click)
    assert processes.games == set()


@pytest.mark.parametrize("stranger", [False, True])
def test_the_bridge_is_asked_to_end_only_a_game_of_this_session(
    tmp_path: Path, processes: Processes, stranger: bool
) -> None:
    # Given: the session game runs; with ``stranger`` another Warcraft III (pid 7) runs too.
    config = tmp_path / "Battle.net.config"
    _ = config.write_text(json.dumps({"Games": {"w3": {}}}), encoding="utf-8")
    log, folder = tmp_path / "War3Log.txt", tmp_path / "war3log"
    _ = log.write_text("9/30 12:00:00.000  GameMain Started\n", encoding="utf-8")
    requests: list[str] = []

    def exit_request() -> dict[str, object]:
        requests.append("sent")
        return {"sent": True, "episode": 1, "sequence": 2, "ack": True}

    # When
    with GameHost(tmp_path / "map.w3x", config, close_game=True, war3log=(log, folder)) as host:
        host.relaunch()
        if stranger:
            processes.games.add(7)
        host.close(exit_request)

    # Then: no bridge command while a game of someone else runs; that game is never closed.
    (record,) = close_records(folder)
    control = cast("dict[str, object]", cast("dict[str, object]", record["audit"])["control"])
    assert (requests, control["sent"]) == (([], False) if stranger else (["sent"], True))
    assert processes.games == ({7} if stranger else set())
    assert record["method"] == "graceful"
