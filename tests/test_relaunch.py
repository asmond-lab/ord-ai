import queue
import threading
import time
from pathlib import Path

import pytest

from ord_ai.channel import BridgeTimeoutError, StateChannel
from ord_ai.protocol import Command, latest_episode


def snapshot(episode: int, action_ok: int = 1) -> str:
    state = f"ORD_AI|1|STATE|{episode}|0|1|50|0|0|5|100|20|1|0|1|{action_ok}"
    return f'call Preload("{state}")\ncall Preload("ORD_AI|1|END|{episode}|0")\n'


class FakeGame:
    """A game whose fresh VM, like the real map, polls only reset_<boot.txt + 1>.

    It answers that reset after the loading screen is dismissed; hung launches never do.
    """

    def __init__(self, directory: Path, hung_launches: int) -> None:
        self.directory: Path = directory
        self.hung_launches: int = hung_launches
        self.launches: int = 0
        self.keys: queue.Queue[None] = queue.Queue()
        self.answered: threading.Event = threading.Event()

    def relaunch(self) -> None:
        self.launches += 1
        self.keys = queue.Queue()
        if self.launches > self.hung_launches:
            threading.Thread(target=self._answer_after_key, args=(self.keys,), daemon=True).start()

    def press_any_key(self) -> None:
        self.keys.put(None)

    def _answer_after_key(self, keys: queue.Queue[None]) -> None:
        keys.get(timeout=5)
        episode = latest_episode(read_optional(self.directory / "boot.txt")) + 1
        polled = self.directory / f"reset_{episode}.txt"
        deadline = time.monotonic() + 5
        while not polled.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if polled.exists():
            consumed = Command(episode=episode, sequence=0, action=0).preload_script()
            _ = (self.directory / "boot.txt").write_text(consumed, encoding="utf-8")
            _ = (self.directory / "state.txt").write_text(snapshot(episode), encoding="utf-8")
            self.answered.set()


def read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_reset_relaunches_and_dismisses_the_loading_screen(tmp_path: Path) -> None:
    # Given: reset 4 was consumed; a stale running-episode snapshot says episode 9.
    consumed = Command(episode=4, sequence=0, action=0).preload_script()
    _ = (tmp_path / "boot.txt").write_text(consumed, encoding="utf-8")
    _ = (tmp_path / "state.txt").write_text(snapshot(9), encoding="utf-8")
    game = FakeGame(tmp_path, hung_launches=0)
    with StateChannel(tmp_path, timeout=5.0, game=game) as channel:
        channel.nudge_interval = 0.05
        # When
        result = channel.reset()

    # Then: the fresh game answered the only reset it polls.
    assert (result.episode, game.launches) == (5, 1)
    assert game.answered.is_set()


def test_reset_relaunches_again_when_a_launch_never_answers(tmp_path: Path) -> None:
    # Given: the first launch hangs on its loading screen and consumes nothing.
    game = FakeGame(tmp_path, hung_launches=1)
    with StateChannel(tmp_path, timeout=0.3, game=game) as channel:
        channel.nudge_interval = 0.05
        # When
        result = channel.reset()

    # Then: the next fresh game still polls reset_1, so the same id is requested again.
    assert (result.episode, game.launches) == (1, 2)


def test_reset_gives_up_after_repeated_hung_launches(tmp_path: Path) -> None:
    # Given
    game = FakeGame(tmp_path, hung_launches=99)
    with StateChannel(tmp_path, timeout=0.1, game=game) as channel:
        channel.nudge_interval = 0.05
        # When / Then
        with pytest.raises(BridgeTimeoutError):
            _ = channel.reset()
    assert game.launches == 3


def test_nudge_runs_only_while_no_state_arrives(tmp_path: Path) -> None:
    # Given
    presses: list[None] = []
    command = Command(episode=1, sequence=0, action=-1)

    def press() -> None:
        presses.append(None)

    with StateChannel(tmp_path, timeout=0.2) as channel:
        channel.nudge_interval = 0.05
        # When
        with pytest.raises(BridgeTimeoutError):
            _ = channel.exchange(command, nudge=press)

    # Then
    assert len(presses) >= 2


def running_map_restarts(directory: Path, answered: threading.Event) -> None:
    """The running map answers the reset it is sent after the loading screen (RestartGame)."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        for polled in directory.glob("reset_*.txt"):
            episode = int(polled.stem.split("_")[1])
            _ = (directory / "state.txt").write_text(snapshot(episode), encoding="utf-8")
            answered.set()
            return
        time.sleep(0.01)


def test_a_reset_in_place_restarts_the_running_map_without_a_relaunch(tmp_path: Path) -> None:
    # Given: the running game is in episode 6 and restarts itself on a reset.
    _ = (tmp_path / "state.txt").write_text(snapshot(6), encoding="utf-8")
    game = FakeGame(tmp_path, hung_launches=0)
    answered = threading.Event()
    restarter = threading.Thread(target=running_map_restarts, args=(tmp_path, answered))
    with StateChannel(tmp_path, timeout=5.0, game=game) as channel:
        channel.nudge_interval = 0.05
        restarter.start()
        # When
        result = channel.reset(restart_in_place=True)
    restarter.join()

    # Then
    assert (result.episode, game.launches, channel.restarted_in_place) == (7, 0, True)


def test_a_reset_in_place_that_never_answers_falls_back_to_a_relaunch(tmp_path: Path) -> None:
    # Given: the running map hangs on its own restart; a fresh launch answers.
    game = FakeGame(tmp_path, hung_launches=0)
    with StateChannel(tmp_path, timeout=0.3, game=game) as channel:
        channel.nudge_interval = 0.05
        # When
        result = channel.reset(restart_in_place=True)

    # Then
    assert (game.launches, channel.restarted_in_place) == (1, False)
    assert result.episode >= 1
