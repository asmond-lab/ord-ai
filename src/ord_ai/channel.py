"""Event-driven local file exchange with an offline Warcraft training map."""

from __future__ import annotations

import os
import queue
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, Self, override

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ord_ai.protocol import (
    CONTROL_FILE,
    CONTROL_TOKEN,
    EXIT_ACTION,
    Command,
    GameState,
    ProtocolError,
    Status,
    latest_episode,
    parse_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from watchdog.observers.api import BaseObserver

RELAUNCH_ATTEMPTS: Final = 3


class GameControl(Protocol):
    """Process-level control of the local Warcraft III client."""

    def relaunch(self) -> None:
        """Close the game and start a fresh one with the training map."""
        ...

    def press_any_key(self) -> None:
        """Try to pass the loading screen."""
        ...


class BridgeTimeoutError(TimeoutError):
    """The game did not acknowledge the requested episode and sequence."""

    def __init__(self, command: Command, seconds: float) -> None:
        """Identify the unacknowledged command and its time budget."""
        self.command: Command = command
        self.seconds: float = seconds
        super().__init__(
            " ".join(
                (
                    f"No game acknowledgment for episode={command.episode},",
                    f"sequence={command.sequence} within {seconds:g}s",
                )
            )
        )


class StateEvents(FileSystemEventHandler):
    """Notify the consumer of native filesystem changes without polling."""

    def __init__(self, changes: queue.Queue[None]) -> None:
        """Connect OS events to the waiting client."""
        super().__init__()
        self.changes: queue.Queue[None] = changes

    @override
    def on_any_event(self, event: FileSystemEvent) -> None:
        if Path(os.fsdecode(event.src_path)).name == "state.txt":
            self.changes.put(None)


class StateChannel:
    """Own the observer and mutable sequencing for one local game client."""

    def __init__(
        self,
        directory: Path,
        timeout: float = 90.0,
        game: GameControl | None = None,
        *,
        attended: bool = False,
        watch: Callable[[], None] | None = None,
    ) -> None:
        """Prepare one event subscription and independent episode counter.

        ``attended``: a person drives the lobby and the loading screen, so a relaunched game
        gets no key presses and a reset that times out is not relaunched again.
        """
        self.directory: Path = directory
        self.timeout: float = timeout
        self.game: GameControl | None = game
        self.attended: bool = attended
        # Attended waits call ``watch`` every nudge_interval; it raises when the game is gone.
        self.watch: Callable[[], None] | None = watch
        self.nudge_interval: float = 2.0
        self.episode: int = 0
        self.sequence: int = 0
        self.restarted_in_place: bool = False
        self.changes: queue.Queue[None] = queue.Queue()
        self.observer: BaseObserver = Observer()

    def __enter__(self) -> Self:
        """Subscribe before the first command can produce a state file."""
        self.directory.mkdir(parents=True, exist_ok=True)
        _ = self.observer.schedule(StateEvents(self.changes), str(self.directory), recursive=False)
        self.observer.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop and join the filesystem observer."""
        self.observer.stop()
        self.observer.join()

    def exchange(self, command: Command, nudge: Callable[[], None] | None = None) -> GameState:
        """Write the command to its own file and wait for this command's complete state.

        While waiting, ``nudge`` runs every ``nudge_interval`` seconds without a state change.
        """
        target = self.directory / command.file_name
        temporary = target.with_suffix(".tmp")
        _ = temporary.write_text(command.preload_script(), encoding="utf-8")
        _ = temporary.replace(target)
        deadline = time.monotonic() + self.timeout
        while True:
            snapshot = self.read_state()
            if (
                snapshot is not None
                and snapshot.episode == command.episode
                and snapshot.sequence == command.sequence
                and snapshot.status != Status.WAITING
            ):
                target.unlink(missing_ok=True)
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeTimeoutError(command, self.timeout)
            wait = remaining if nudge is None else min(remaining, self.nudge_interval)
            try:
                _ = self.changes.get(timeout=wait)
            except queue.Empty:
                if nudge is None:
                    raise BridgeTimeoutError(command, self.timeout) from None
                nudge()

    def request_exit(self, timeout: float = 10.0) -> dict[str, object]:
        """Ask this channel's own game to end (control action -2); return what happened.

        The command continues this channel's current episode and sequence (the bridge takes
        only the next sequence of its episode), so it is never rebuilt from another state.
        ``ack`` means only that the bridge received it (control.txt); whether the client
        left the game or exited is for the caller to observe.
        """
        if self.episode == 0:
            return {"sent": False, "reason": "no episode on this channel"}
        command = Command(episode=self.episode, sequence=self.sequence + 1, action=EXIT_ACTION)
        target = self.directory / command.file_name
        temporary = target.with_suffix(".tmp")
        _ = temporary.write_text(command.preload_script(), encoding="utf-8")
        _ = temporary.replace(target)
        record: dict[str, object] = {"sent": True, "episode": command.episode}
        record["sequence"] = command.sequence
        deadline = time.monotonic() + timeout
        while not self._acknowledged(command):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                record["ack"] = False
                return record
            try:
                _ = self.changes.get(timeout=remaining)
            except queue.Empty:
                continue
        target.unlink(missing_ok=True)
        record["ack"] = True
        return record

    def _acknowledged(self, command: Command) -> bool:
        try:
            text = (self.directory / CONTROL_FILE).read_text(encoding="utf-8-sig")
        except (FileNotFoundError, PermissionError):
            return False
        return any(
            (int(match.group(1)), int(match.group(2))) == (command.episode, command.sequence)
            for match in CONTROL_TOKEN.finditer(text)
        )

    def read_state(self) -> GameState | None:
        """Read once; incomplete writes are retried only after a filesystem event."""
        try:
            text = (self.directory / "state.txt").read_text(encoding="utf-8-sig")
        except (FileNotFoundError, PermissionError):
            return None
        return parse_snapshot(text)

    def reset(self, restart_in_place: bool = False) -> GameState:
        """Request a real new map episode, not a renamed running observation.

        With a ``game``, the client is relaunched instead of using the map's own restart,
        which can hang on its loading screen; a launch that is not acknowledged is retried.
        ``restart_in_place`` first asks the running map to restart itself (RestartGame, as
        without a game): after Warcraft III reopened map archives mid-game, a relaunch
        breaks the models again (texture-patch.md, candidate A). A restart that never
        answers falls back to the relaunch. ``restarted_in_place`` tells which one ran.
        """
        self.restarted_in_place = False
        if self.game is None:
            result = self.exchange(self._next_reset())
        elif restart_in_place:
            try:
                result = self.exchange(self._next_reset(), nudge=self.game.press_any_key)
                self.restarted_in_place = True
            except BridgeTimeoutError:
                result = self._relaunch_until_acknowledged(self.game)
        else:
            result = self._relaunch_until_acknowledged(self.game)
        if result.status != Status.PLAYING or result.action_ok != 1:
            raise ProtocolError(detail="The training map rejected the episode reset")
        return result

    def _relaunch_until_acknowledged(self, game: GameControl) -> GameState:
        attempt = 1
        while True:
            game.relaunch()
            # A fresh game polls only reset_<last consumed reset + 1>, so every launch
            # re-derives the request from boot.txt instead of counting its own requests.
            command = self._next_reset(fresh_game=True)
            try:
                return self.exchange(
                    command, nudge=self.watch if self.attended else game.press_any_key
                )
            except BridgeTimeoutError:
                if self.attended or attempt >= RELAUNCH_ATTEMPTS:
                    raise
                attempt += 1

    def _next_reset(self, *, fresh_game: bool = False) -> Command:
        for pattern in ("cmd_*.txt", "reset_*.txt", "boot_*_*.txt"):
            for stale in self.directory.glob(pattern):
                stale.unlink(missing_ok=True)
        try:
            boot = (self.directory / "boot.txt").read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            boot = ""
        consumed = latest_episode(boot)
        if fresh_game:
            highest = consumed
        else:
            # The running map also accepts any newer reset than its active episode.
            highest = max(self.episode, consumed)
            snapshot = self.read_state()
            if snapshot is not None:
                highest = max(highest, snapshot.episode)
        self.episode = highest + 1
        self.sequence = 0
        return Command(episode=self.episode, sequence=0, action=-1)

    def step(self, action: int) -> GameState:
        """Apply exactly one policy action after reset has succeeded."""
        if self.episode == 0:
            raise ProtocolError(detail="reset() must complete before step()")
        self.sequence += 1
        command = Command(episode=self.episode, sequence=self.sequence, action=action)
        return self.exchange(command, nudge=self.watch)
