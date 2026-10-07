import re
from pathlib import Path

import pytest

from ord_ai.action_groups import action_groups
from ord_ai.boss import BOSS_SIZE
from ord_ai.build import EASY_STAGE, JASS_DIRECTORY, MapData, human_bridge_sources, read_map
from ord_ai.catalog import ActionKind, available_actions
from ord_ai.human import render_human_table
from ord_ai.inputs import JASS_KINDS, render_input_table
from ord_ai.logbook import LOGBOOK_IDS, LOGBOOK_SIZE
from ord_ai.objectdata import rawcode, rawtext
from ord_ai.observation import SCALAR_COUNT, features, input_size
from ord_ai.protocol import GameState, TechLevel, parse_snapshot
from ord_ai.treasure import (
    CHEST_COUNT,
    CHEST_X,
    CHEST_Y,
    SCAN_READY,
    TREASURE_FLOOR,
    TREASURE_IDS,
    TREASURE_SIZE,
    TREASURE_SLOTS,
    TREASURE_TECHS,
    treasure_features,
)

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_CHESTS = range(TREASURE_FLOOR, TREASURE_FLOOR + TREASURE_SLOTS)


@pytest.fixture(scope="module")
def map_data() -> MapData:
    return read_map(_MAP_PATH.read_bytes())


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name} takes")
    return source[start : source.index("\nendfunction", start)]


def _chests(state: GameState, count: int, ready: int) -> GameState:
    rows = (TechLevel(raw_id=CHEST_COUNT, level=count), TechLevel(raw_id=SCAN_READY, level=ready))
    return state.model_copy(update={"techs": rows})


def test_chest_rows_use_no_rawcode_of_the_map_or_the_logbook(map_data: MapData) -> None:
    # Given
    objects = map_data.objects
    used = set(objects.units) | set(objects.abilities) | set(objects.upgrades)
    used |= set(objects.items)

    # Then
    assert len(TREASURE_IDS) == len(TREASURE_TECHS) == TREASURE_SIZE
    assert not {rawtext(raw) for raw in TREASURE_IDS} & used
    assert not any(f"${raw:08x}" in map_data.script for raw in TREASURE_IDS)
    assert not TREASURE_IDS & LOGBOOK_IDS


def test_chest_actions_are_appended_with_codes_574_to_589(map_data: MapData) -> None:
    # Given
    actions = map_data.catalog.actions

    # When
    chests = actions[TREASURE_FLOOR:]

    # Then: every earlier action keeps its place; index and wire code agree.
    assert len(actions) == TREASURE_FLOOR + TREASURE_SLOTS
    assert [action.code for action in chests] == list(_CHESTS)
    assert all(action.kind is ActionKind.TREASURE for action in chests)
    assert all(action.raw_id == rawcode("A05D") for action in chests)
    assert len({action.name for action in chests}) == TREASURE_SLOTS
    assert actions[TREASURE_FLOOR - 1].code == 336


@pytest.mark.parametrize(
    ("count", "ready", "open_slots"),
    [(0, 1, 0), (3, 1, 3), (3, 0, 0), (40, 1, TREASURE_SLOTS)],
)
def test_chest_masks_need_the_ready_flag_and_a_live_chest(
    map_data: MapData, state: GameState, count: int, ready: int, open_slots: int
) -> None:
    # When
    mask = available_actions(_chests(state, count, ready), map_data.catalog)[TREASURE_FLOOR:]

    # Then
    assert mask == (True,) * open_slots + (False,) * (TREASURE_SLOTS - open_slots)


def test_snapshot_rows_become_scaled_chest_features() -> None:
    # Given: 20 live chests, 탐색 ready, chest 0 at (250, 1000), chest 15 at (1000, 0).
    rows = [
        (CHEST_COUNT, 20),
        (SCAN_READY, 1),
        (CHEST_X[0], 250),
        (CHEST_Y[0], 1000),
        (CHEST_X[15], 1000),
    ]
    text = "\n".join(
        [
            'call Preload("ORD_AI|1|STATE|3|4|25|100|0|0|5|100|0|1|0|1|1")',
            *(f'call Preload("ORD_AI|1|TECH|{raw}|{value}")' for raw, value in rows),
            'call Preload("ORD_AI|1|END|3|4")',
        ]
    )

    # When
    state = parse_snapshot(text)
    assert state is not None
    values = treasure_features(state)

    # Then
    assert len(values) == TREASURE_SIZE
    assert values[:4] == (1.0, 1.0, 0.25, 1.0)
    assert values[-2:] == (1.0, 0.0)
    assert sum(values[4:-2]) == 0.0


def test_chest_features_come_after_the_logbook(map_data: MapData, state: GameState) -> None:
    # Given
    catalog = map_data.catalog
    supplied = _chests(state, 4, 1)

    # When
    result = features(supplied, catalog)

    # Then
    start = SCALAR_COUNT + len(catalog.units) + LOGBOOK_SIZE
    assert input_size(catalog) == start + TREASURE_SIZE + BOSS_SIZE == len(result)
    assert result[start : start + 2] == (0.25, 1.0)


def test_the_bridge_exports_the_chest_rows_and_runs_the_chest_actions(
    map_data: MapData,
) -> None:
    # Given
    actions = (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8")
    export = _function((JASS_DIRECTORY / "bridge_functions.j").read_text("utf-8"), "ORDAI_Export")
    treasure = _function(actions, "ORDAI_Treasure")
    table = render_input_table(map_data.inputs)

    # When: ZXkk/ZYkk are 'ZX00'/'ZY00' + tens * 256 + ones in the export loop.
    literals = re.findall(r"call ORDAI_Row\('(\w{4})'", treasure)
    looped = [
        rawcode(base) + slot // 10 * 256 + slot % 10
        for slot in range(TREASURE_SLOTS)
        for base in ("ZX00", "ZY00")
    ]

    # Then
    assert list(dict.fromkeys(literals)) == ["ZC00", "ZC01", "ZX00", "ZY00"]
    assert "set id = i / 10 * 256 + i - i / 10 * 10" in treasure
    assert looped == [raw for pair in zip(CHEST_X, CHEST_Y, strict=True) for raw in pair]
    assert export.index("call ORDAI_Logbook()") < export.index("call ORDAI_Treasure()")
    assert export.index("call ORDAI_Treasure()") < export.index('"END|"')
    kind = JASS_KINDS[ActionKind.TREASURE]
    for slot, code in enumerate(_CHESTS):
        assert (
            f"call ORDAI_Route({code}, {kind}, 'A05D', OrderId(\"detectaoe\"), {slot}, 0, 0)"
            in (table)
        )
    assert "ORDAI_PointOrder(caster, ORDAI_spellExpect, order, Ur[slot], Br[slot])" in actions
    point = _function(actions, "ORDAI_PointOrder")
    assert point.index("852270") < point.index("IssuePointOrderById(u, order, x, y)")


def test_the_human_capture_maps_a_scan_to_the_chest_its_square_holds(
    map_data: MapData,
) -> None:
    # Given
    table = render_human_table(map_data.catalog, map_data.inputs, map_data.script)
    _, capture, _ = human_bridge_sources(EASY_STAGE, table)

    # When
    spelled = _function(capture, "ORDAI_HumanSpelled")
    chosen = _function(capture, "ORDAI_HumanTreasure")

    # Then: chest index k -> code 574 + k; inside the cP[0] square the nearest copied chest
    # (Chebyshev distance) is mapped, outside it the cast stays unmapped with its point.
    for slot, code in enumerate(_CHESTS):
        assert f"call SaveInteger(ORDAI_data, 210, {slot}, {code})" in table
    assert "GetSpellTargetX(), GetSpellTargetY()" in spelled
    assert "local real reach = cP[0]" in chosen
    assert "if dx <= reach and (best < 0 or dx < bestD) then" in chosen
    assert "ORDAI_HumanLookup(210, best)" in chosen
    assert 'call ORDAI_HumanAdd(-1, "spell", "A05D " + caster + " " + point)' in chosen
    assert capture.index("function ORDAI_HumanTreasure") < capture.index(
        "function ORDAI_HumanSpelled"
    )


def test_the_treasure_group_holds_every_scan_action(map_data: MapData) -> None:
    # When
    groups = action_groups(map_data.catalog)

    # Then
    assert groups["treasure"] == frozenset({166, *_CHESTS})
