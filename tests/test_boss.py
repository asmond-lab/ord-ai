from pathlib import Path

import pytest

from ord_ai.boss import (
    BOSS_HEALTH,
    BOSS_IDS,
    BOSS_SIZE,
    BOSS_TECHS,
    ROUND_TIME_LEFT,
    boss_features,
)
from ord_ai.build import JASS_DIRECTORY, MapData, read_map
from ord_ai.diagnostics import ARENA_ELIGIBLE, ARENA_MOVING, ARENA_UNITS, DIAGNOSTIC_IDS, arena
from ord_ai.logbook import LOGBOOK_IDS
from ord_ai.objectdata import rawtext
from ord_ai.observation import features, input_size
from ord_ai.protocol import GameState, TechLevel
from ord_ai.treasure import TREASURE_IDS

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"


@pytest.fixture(scope="module")
def map_data() -> MapData:
    return read_map(_MAP_PATH.read_bytes())


def test_boss_rows_use_no_rawcode_of_the_map_or_other_synthetic_rows(map_data: MapData) -> None:
    # Given
    objects = map_data.objects
    used = set(objects.units) | set(objects.abilities) | set(objects.upgrades)
    used |= set(objects.items)

    # Then
    assert len(BOSS_IDS) == len(BOSS_TECHS) == BOSS_SIZE
    assert not {rawtext(raw) for raw in BOSS_IDS} & used
    assert not any(f"${raw:08x}" in map_data.script for raw in BOSS_IDS)
    assert not BOSS_IDS & (LOGBOOK_IDS | TREASURE_IDS)


def test_boss_rows_become_scaled_features_at_the_input_tail(
    map_data: MapData, state: GameState
) -> None:
    # Given: the boss at 250/1000 health, 30 s left, and a slow round with 90 s left.
    rows = (TechLevel(raw_id=BOSS_HEALTH, level=250), TechLevel(raw_id=ROUND_TIME_LEFT, level=300))
    boss = state.model_copy(update={"techs": rows})
    slow = state.model_copy(update={"techs": (TechLevel(raw_id=ROUND_TIME_LEFT, level=900),)})

    # When
    values = features(boss, map_data.catalog)

    # Then
    assert len(values) == input_size(map_data.catalog)
    assert values[-BOSS_SIZE:] == (0.25, 0.5)
    assert boss_features(slow) == (0.0, 1.0)


def test_the_bridge_exports_the_boss_rows_before_the_end_row() -> None:
    # Given
    functions = (JASS_DIRECTORY / "bridge_functions.j").read_text("utf-8")
    actions = (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8")
    start = functions.index("function ORDAI_Export takes")
    export = functions[start : functions.index("\nendfunction", start)]
    boss = actions[actions.index("function ORDAI_Boss takes") :]
    boss = boss[: boss.index("\nendfunction")]

    # Then
    assert export.index("call ORDAI_Treasure()") < export.index("call ORDAI_Boss()")
    assert export.index("call ORDAI_Boss()") < export.index('"END|"')
    assert "call ORDAI_Row('ZB00', health)" in boss
    assert "call ORDAI_Row('ZB01', R2I(TimerGetRemaining(Vp) * 10. + 0.5))" in boss
    assert "GetUnitTypeId(u) == iB[Av]" in boss


def test_action_6_diagnostic_rows_are_exported_and_unused_by_the_map(
    map_data: MapData, state: GameState
) -> None:
    # Given
    functions = (JASS_DIRECTORY / "bridge_functions.j").read_text("utf-8")
    start = functions.index("function ORDAI_Export takes")
    export = functions[start : functions.index("\nendfunction", start)]
    rows = (
        TechLevel(raw_id=ARENA_UNITS, level=3),
        TechLevel(raw_id=ARENA_MOVING, level=1),
        TechLevel(raw_id=ARENA_ELIGIBLE, level=2),
    )
    supplied = state.model_copy(update={"techs": rows})

    # Then: exported after the boss rows, read by the log, ignored by the policy input.
    assert export.index("call ORDAI_Boss()") < export.index("call ORDAI_ArenaDiag()")
    assert export.index("call ORDAI_ArenaDiag()") < export.index('"END|"')
    assert not any(f"${raw:08x}" in map_data.script for raw in DIAGNOSTIC_IDS)
    assert not DIAGNOSTIC_IDS & (BOSS_IDS | LOGBOOK_IDS | TREASURE_IDS)
    assert (arena(supplied), arena(state)) == ((3, 1, 2), None)
    assert features(supplied, map_data.catalog) == features(state, map_data.catalog)
