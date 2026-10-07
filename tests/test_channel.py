import queue
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import override

import pytest

from ord_ai.channel import BridgeTimeoutError, StateChannel
from ord_ai.protocol import Command, GameState


class ObservedChannel(StateChannel):
    """Record real reads so the test can await the exact stale-state event."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory, timeout=5.0)
        self.reads: queue.Queue[GameState | None] = queue.Queue()

    @override
    def read_state(self) -> GameState | None:
        result = super().read_state()
        self.reads.put(result)
        return result


def snapshot(episode: int, sequence: int) -> str:
    state = f"ORD_AI|1|STATE|{episode}|{sequence}|1|50|0|0|5|100|20|1|0|1|1"
    return f'call Preload("{state}")\ncall Preload("ORD_AI|1|END|{episode}|{sequence}")\n'


def test_exchange_waits_for_matching_state_after_observing_stale_file(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "state.txt"
    _ = path.write_text(snapshot(1, 0), encoding="utf-8")
    command = Command(episode=2, sequence=1, action=0)
    with ObservedChannel(tmp_path) as channel, ThreadPoolExecutor(max_workers=1) as pool:
        # When
        result = pool.submit(channel.exchange, command)
        stale = channel.reads.get(timeout=5)
        assert stale is not None
        assert stale.episode == 1
        assert (tmp_path / "cmd_2_1.txt").is_file()
        _ = path.write_text(snapshot(2, 1), encoding="utf-8")
        received = result.result(timeout=5)

    # Then
    assert (received.episode, received.sequence) == (2, 1)
    assert not (tmp_path / "cmd_2_1.txt").exists()


def test_exchange_reports_missing_game_instead_of_inventing_state(tmp_path: Path) -> None:
    # Given
    command = Command(episode=2, sequence=0, action=-1)
    # When / Then
    with StateChannel(tmp_path, timeout=0.0) as channel, pytest.raises(BridgeTimeoutError):
        _ = channel.exchange(command)


def ack(episode: int, sequence: int) -> str:
    text = f"ORD_AI|1|CTL|{episode}|{sequence}|-2"
    return f"call BlzSetAbilityTooltip('AZZZ',\"{text}\",0)\n"


def test_an_exit_request_continues_this_channels_own_episode_and_sequence(tmp_path: Path) -> None:
    # Given: the channel drove episode 7 up to sequence 3; the bridge answered sequence 4.
    _ = (tmp_path / "control.txt").write_text(ack(7, 4), encoding="utf-8")
    with StateChannel(tmp_path, timeout=5.0) as channel:
        channel.episode, channel.sequence = 7, 3

        # When
        result = channel.request_exit(timeout=5.0)

    # Then: only the next sequence of this episode, acknowledged, and the command consumed.
    assert result == {"sent": True, "episode": 7, "sequence": 4, "ack": True}
    assert not (tmp_path / "cmd_7_4.txt").exists()


def test_an_exit_request_is_not_acknowledged_by_an_answer_to_another_command(
    tmp_path: Path,
) -> None:
    # Given: control.txt still holds an earlier answer (sequence 3).
    _ = (tmp_path / "control.txt").write_text(ack(7, 3), encoding="utf-8")
    with StateChannel(tmp_path, timeout=5.0) as channel:
        channel.episode, channel.sequence = 7, 3
        result = channel.request_exit(timeout=0.0)

    # Then: the -2 command waits for the bridge; nothing counts as received.
    assert result["ack"] is False
    assert "ORD_AI|1|7|4|-2" in (tmp_path / "cmd_7_4.txt").read_text("utf-8")


def test_an_exit_request_without_an_episode_sends_nothing(tmp_path: Path) -> None:
    with StateChannel(tmp_path, timeout=5.0) as channel:
        result = channel.request_exit(timeout=0.0)
    assert result == {"sent": False, "reason": "no episode on this channel"}
    assert list(tmp_path.glob("cmd_*.txt")) == []
