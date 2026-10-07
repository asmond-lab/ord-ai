from pathlib import Path

import pytest

from ord_ai.curriculum import ISEKAI_FLOOR, STAGES, Difficulty, next_stage, zero_count_clear
from ord_ai.mpq import read_member
from ord_ai.protocol import GameState, Status

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"


def test_ladder_runs_easy_to_nightmare_and_ends_on_nightmare_isekai() -> None:
    # When
    ladder = [(stage.difficulty, stage.isekai) for stage in STAGES]

    # Then
    assert [difficulty for difficulty, _ in ladder] == list(Difficulty)
    assert ladder[-1] == (Difficulty.NIGHTMARE, True)
    assert all(isekai == (difficulty >= ISEKAI_FLOOR) for difficulty, isekai in ladder)


def test_map_allows_modes_only_from_the_isekai_floor() -> None:
    # Given
    script = read_member(_MAP_PATH.read_bytes(), "war3map.j").decode("utf-8")

    # When / Then: both the toggle and the game start refuse modes below Hell (ls 4).
    assert f"if ls<{int(ISEKAI_FLOOR)} then" in script
    assert "지옥 난이도 이상에서만 모드를 선택할 수 있습니다." in script
    assert f"set ls={int(Difficulty.NIGHTMARE)}\n" in script.replace("\r\n", "\n")


@pytest.mark.parametrize(
    ("status", "enemies", "expected"),
    [
        (Status.WIN, 0, True),
        (Status.WIN, 1, False),
        (Status.LOSS, 0, False),
        (Status.PLAYING, 0, False),
    ],
)
def test_only_a_win_with_an_empty_line_counts(
    state: GameState, status: Status, enemies: int, expected: bool
) -> None:
    # Given
    final = state.model_copy(update={"status": status, "enemies": enemies})

    # When / Then
    assert zero_count_clear(final) is expected


def test_zero_count_clear_promotes_one_stage_and_stops_at_the_last(state: GameState) -> None:
    # Given
    clear = state.model_copy(update={"status": Status.WIN, "enemies": 0})
    messy = state.model_copy(update={"status": Status.WIN, "enemies": 3})
    last = len(STAGES) - 1

    # When
    promoted = next_stage(0, clear)
    kept = next_stage(0, messy)
    capped = next_stage(last, clear)

    # Then
    assert (promoted, kept, capped) == (1, 0, last)
