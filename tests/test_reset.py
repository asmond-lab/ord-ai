from collections.abc import Callable
from pathlib import Path
from typing import override

import pytest

from ord_ai.channel import StateChannel
from ord_ai.protocol import Command, GameState, ProtocolError


class ResetPeer(StateChannel):
    """Replace only the remote acknowledgment; allocation reads real files."""

    def __init__(self, directory: Path, reply: GameState) -> None:
        super().__init__(directory)
        self.reply: GameState = reply

    @override
    def exchange(self, command: Command, nudge: Callable[[], None] | None = None) -> GameState:
        return self.reply.model_copy(
            update={"episode": command.episode, "sequence": command.sequence}
        )


def test_reset_requests_the_reset_after_the_last_consumed_one(
    tmp_path: Path, state: GameState
) -> None:
    # Given: the map consumed reset 51; files from an interrupted run are still present.
    consumed = Command(episode=51, sequence=0, action=0)
    stale = [tmp_path / "reset_24.txt", tmp_path / "cmd_51_3.txt", tmp_path / "boot_51_0_0.txt"]
    _ = (tmp_path / "boot.txt").write_text(consumed.preload_script(), encoding="utf-8")
    for path in stale:
        _ = path.write_text("stale", encoding="utf-8")
    with ResetPeer(tmp_path, state) as channel:
        # When
        result = channel.reset()

    # Then
    assert (result.episode, result.sequence) == (52, 0)
    assert not any(path.exists() for path in stale)


def test_reset_rejects_a_game_that_did_not_restart(tmp_path: Path, state: GameState) -> None:
    # Given
    rejection = state.model_copy(update={"action_ok": 0})

    # When / Then
    with ResetPeer(tmp_path, rejection) as channel, pytest.raises(ProtocolError):
        _ = channel.reset()
