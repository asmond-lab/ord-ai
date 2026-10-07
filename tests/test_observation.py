import pytest

from ord_ai.catalog import (
    BASE_ACTIONS,
    Action,
    Catalog,
    Requirement,
    UnitType,
    available_actions,
    extract_catalog,
)
from ord_ai.observation import SCALAR_COUNT, features, input_size, transition_reward
from ord_ai.protocol import GameState, ProtocolError, Status, UnitCount


def test_reward_tracks_progress_without_wall_time(state: GameState) -> None:
    # Given
    after = state.model_copy(update={"sequence": 1, "round": 2, "story": 2})

    # When
    reward = transition_reward(state, after)

    # Then
    assert reward == 6.0


def test_reward_rejects_reset_as_progress(state: GameState) -> None:
    # Given
    after = state.model_copy(update={"episode": 20, "sequence": 1, "round": 10})

    # When / Then
    with pytest.raises(ProtocolError):
        _ = transition_reward(state, after)


@pytest.mark.parametrize(
    ("before_update", "after_update", "reward"),
    [
        ({"bosses": 2}, {"bosses": 1}, 3.0),
        ({"enemies": 50}, {"enemies": 30}, 0.04),
        ({}, {"action_ok": 0}, -0.05),
    ],
    ids=["cleared_boss", "relieved_pressure", "rejected_action"],
)
def test_reward_scales_each_transition_signal(
    state: GameState, before_update: dict[str, int], after_update: dict[str, int], reward: float
) -> None:
    # Given
    before = state.model_copy(update=before_update)
    after = before.model_copy(update={"sequence": 1, **after_update})

    # When
    result = transition_reward(before, after)

    # Then
    assert result == pytest.approx(reward)


@pytest.mark.parametrize(
    ("status", "enemies", "reward"),
    [(Status.WIN, 0, 20.0), (Status.WIN, 3, 10.0), (Status.LOSS, 3, -10.0)],
)
def test_reward_distinguishes_actual_game_outcomes(
    state: GameState, status: Status, enemies: int, reward: float
) -> None:
    # Given: the line keeps its enemy count, so only the outcome is rewarded.
    before = state.model_copy(update={"enemies": enemies})
    after = before.model_copy(update={"sequence": 1, "status": status})

    # When
    result = transition_reward(before, after)

    # Then
    assert result == reward


def test_action_mask_rejects_missing_fusion_materials(state: GameState) -> None:
    # Given
    catalog = Catalog(
        map_sha256="test",
        units=(UnitType(raw_id=100, name="material"),),
        actions=(
            Action(code=0, name="wait"),
            Action(code=1000, name="fusion", units=(Requirement(raw_id=100, count=2),)),
        ),
    )
    supplied = state.model_copy(update={"units": (UnitCount(raw_id=100, count=1),)})

    # When
    mask = available_actions(supplied, catalog)

    # Then
    assert mask == (True, False)


def test_features_use_catalog_order_not_snapshot_order(state: GameState) -> None:
    # Given
    catalog = Catalog(
        map_sha256="test",
        units=(UnitType(raw_id=100, name="first"), UnitType(raw_id=101, name="second")),
        actions=(Action(code=0, name="wait"),),
    )
    supplied = state.model_copy(
        update={"units": (UnitCount(raw_id=101, count=3), UnitCount(raw_id=100, count=1))}
    )

    # When
    result = features(supplied, catalog)

    # Then: unit counts follow the scalars; the 기록지침 features come after them.
    first, second = result[SCALAR_COUNT : SCALAR_COUNT + 2]
    assert second > first > 0
    assert len(result) == input_size(catalog)


def test_catalog_reads_hex_gold_and_unit_counts_from_original_table() -> None:
    # Given
    script = """\
call SaveBoolean(cv,$41303044,1,true)
set Mv=$41303044
call SaveInteger(Qv,Mv,gv,$6830304b)
call SaveInteger(Bv,Mv,uv,$554e4954)
call SaveInteger(Hv,Mv,uv,$68303033)
call SaveInteger(ov,Mv,uv,2)
call SaveInteger(Bv,Mv,uv,$474f4c44)
call SaveInteger(Hv,Mv,uv,$474f4c44)
call SaveInteger(ov,Mv,uv,$1388)
endfunction
"""
    strings = """\
STRING 1
// 유닛: h003 (루피), Name (이름 (unam))
{
루피
}
"""

    # When
    catalog = extract_catalog(script, strings, "test")

    # Then
    recipe = catalog.actions[len(BASE_ACTIONS)]
    assert recipe.gold == 5000
    assert recipe.units == (Requirement(raw_id=int.from_bytes(b"h003", "big"), count=2),)
