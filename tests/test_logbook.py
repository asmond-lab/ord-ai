import re
from pathlib import Path

import pytest

from ord_ai.boss import BOSS_SIZE
from ord_ai.build import JASS_DIRECTORY, MapData, read_map
from ord_ai.catalog import ActionKind, available_actions
from ord_ai.inputs import OBJECTIVES, exported_techs
from ord_ai.logbook import (
    BELL_LIFE,
    GOAL_ACHIEVED,
    GOAL_FAILED,
    GOAL_IDS,
    GOALS,
    HIGH_RISK_NAVIGATION,
    LOGBOOK_IDS,
    LOGBOOK_SIZE,
    LOGBOOK_TECHS,
    LOW_POINT_NAVIGATION,
    NAVIGATION,
    NAVIGATION_CHOICE,
    QUEST_DONE_IDS,
    QUEST_IDS,
    QUESTS,
    logbook_features,
)
from ord_ai.objectdata import rawcode, rawtext
from ord_ai.observation import SCALAR_COUNT, features, input_size
from ord_ai.protocol import GameState, TechLevel, UnitCount, parse_snapshot
from ord_ai.treasure import TREASURE_SIZE

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_V3_ACTIONS = 568
_HERO = rawcode("h001")
_FUNCTION = re.compile(r"^function (\w+) takes .*?^endfunction$", re.MULTILINE | re.DOTALL)
_TRIGGER = re.compile(r"set GIR=(\w+)\nif not HaveSavedHandle\(Nr,0,\$([0-9a-f]{8})\)")


@pytest.fixture(scope="module")
def map_data() -> MapData:
    return read_map(_MAP_PATH.read_bytes())


def _firsts(pattern: str, text: str) -> list[str]:
    return [str(match.group(1)) for match in re.finditer(pattern, text)]


def _pairs(pattern: str, text: str) -> list[tuple[str, str]]:
    return [(str(match.group(1)), str(match.group(2))) for match in re.finditer(pattern, text)]


def _handlers(script: str) -> dict[str, str]:
    """Nr trigger key (Q000, T000, ...) -> body of the function its action runs."""
    variables = dict(_pairs(r"(?m)^set (\w+)=function (\w+)$", script))
    bodies = {match.group(1): match.group(0) for match in _FUNCTION.finditer(script)}
    return {
        rawtext(int(key, 16)): bodies[variables.get(variable, variable)]
        for variable, key in _pairs(_TRIGGER.pattern, script)
    }


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name} takes")
    return source[start : source.index("\nendfunction", start)]


def test_synthetic_rows_use_no_rawcode_of_the_map(map_data: MapData) -> None:
    # Given
    objects = map_data.objects
    used = set(objects.units) | set(objects.abilities) | set(objects.upgrades)
    used |= set(objects.items)

    # When
    literals = {f"${raw:08x}" for raw in LOGBOOK_IDS}

    # Then
    assert len(LOGBOOK_IDS) == len(LOGBOOK_TECHS)
    assert not {rawtext(raw) for raw in LOGBOOK_IDS} & used
    assert not any(literal in map_data.script for literal in literals)


def test_goals_are_the_missions_bbs_assigns_in_window_order(map_data: MapData) -> None:
    # Given
    handlers = _handlers(map_data.script)

    # When: each mission handler titles its window row with g9s(slot, icon, name).
    titles = [
        _firsts(r'call g9s\(\w+,"[^"]+","([^"]+)"\)', handlers[trigger]) for trigger, _ in GOALS
    ]
    assigned = _pairs(
        r"set lr=(\d+)\ncall TriggerExecute\(LoadTriggerHandle\(Nr,0,\$(\w+)\)\)", map_data.script
    )

    # Then
    assert titles == [[name] for _, name in GOALS]
    assert [(int(slot), rawtext(int(key, 16))) for slot, key in assigned] == [
        (index + 1, trigger) for index, (trigger, _) in enumerate(GOALS)
    ]


def test_quests_are_the_pool_in_trigger_order(map_data: MapData) -> None:
    # Given
    handlers = _handlers(map_data.script)
    bodies = {match.group(1): match.group(0) for match in _FUNCTION.finditer(map_data.script)}

    # When: the handler (or the first function it calls) titles the row with pns.
    names: list[str] = []
    for trigger, _ in QUESTS:
        body = handlers[trigger]
        called = [bodies.get(name, "") for name in _firsts(r"call (\w+)\(", body)]
        found = [_firsts(r'call pns\(\w+\+1,"[^"]+","([^"]+)"\)', text) for text in (body, *called)]
        names.append(next(title for titles in found for title in titles))

    # Then
    assert names == [name for _, name in QUESTS]


def test_navigation_order_matches_the_sync_actions_and_the_bridge(map_data: MapData) -> None:
    # Given
    syncs = [
        action.name
        for action in map_data.catalog.actions
        if action.kind is ActionKind.SYNC and action.name.startswith("sync: NAVI_")
    ]
    bridge = (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8")

    # When
    compared = re.findall(
        r'name == "([^"]+)" then\n\s+return (\d+)', _function(bridge, "ORDAI_Navigation")
    )

    # Then
    assert [name.rsplit(" :: ", 1) for name in syncs[:12]] == [
        [f"sync: NAVI_{index // 3 + 1}IDX {group}", option]
        for index, (group, option) in enumerate(NAVIGATION)
    ]
    assert compared == [(option, str(index + 1)) for index, (_, option) in enumerate(NAVIGATION)]


def test_navigation_styles_keep_the_given_classification() -> None:
    # Then: 연속베팅 is the 환전소 option (h0BU 환전소 editor suffix).
    high, low = HIGH_RISK_NAVIGATION, LOW_POINT_NAVIGATION
    options = {option for _, option in NAVIGATION}
    assert sorted(high) == sorted(["카지노", "환전소"])
    assert sorted(low) == sorted(["일석이조", "바운티헌터", "연금술", "긴급소집"])
    assert not high & low
    assert high | low <= options


def test_the_bridge_exports_every_logbook_row_in_order() -> None:
    # Given
    bridge = (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8")
    functions = (JASS_DIRECTORY / "bridge_functions.j").read_text("utf-8")
    looped = {"ZQ01", "ZQ02", "ZQ04", "ZQ05"}  # 'ZQ00' + slot and 'ZQ03' + slot

    # When
    rows = re.findall(r"call ORDAI_Row\('(\w{4})'", _function(bridge, "ORDAI_Logbook"))
    export = _function(functions, "ORDAI_Export")

    # Then
    assert list(dict.fromkeys(rows)) == [code for code, _ in LOGBOOK_TECHS if code not in looped]
    assert "'ZQ00' + slot" in bridge
    assert "'ZQ03' + slot" in bridge
    assert export.index("call ORDAI_Logbook()") < export.index('"END|"')


def test_snapshot_rows_become_logbook_features() -> None:
    # Given: 신속한 철거 achieved, 패스트유니크 failed, 리사이클 done in slot 2, 환전소 chosen.
    rows = [
        (GOAL_IDS[1], GOAL_ACHIEVED),
        (GOAL_IDS[0], GOAL_FAILED),
        (BELL_LIFE, 50),
        (QUEST_IDS[0], rawcode("Q008")),
        (QUEST_IDS[1], rawcode("Q016")),
        (QUEST_DONE_IDS[1], 1),
        (NAVIGATION_CHOICE, 9),
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
    values = logbook_features(state)

    # Then
    assert len(values) == LOGBOOK_SIZE
    assert values[0:4] == (0.0, 1.0, 1.0, 0.0)  # goal 0 failed, goal 1 achieved
    assert values[21] == pytest.approx(0.5)  # 황금 종 life
    quests = values[30 : 30 + 2 * len(QUESTS)]
    assert [index for index, value in enumerate(quests) if value] == [8, len(QUESTS) + 16]
    navigation = values[30 + 2 * len(QUESTS) + 2 :]
    assert navigation[8] == 1.0
    assert sum(navigation[:12]) == 1.0
    assert navigation[12:16] == (1.0, 0.0, 1.0, 0.0)  # chosen, not random, high risk


def test_features_append_the_logbook_after_the_unit_counts(
    map_data: MapData, state: GameState
) -> None:
    # Given
    catalog = map_data.catalog
    supplied = state.model_copy(update={"techs": (TechLevel(raw_id=GOAL_IDS[4], level=1),)})

    # When
    result = features(supplied, catalog)

    # Then
    assert len(result) == input_size(catalog)
    head = SCALAR_COUNT + len(catalog.units) + LOGBOOK_SIZE
    assert input_size(catalog) == head + TREASURE_SIZE + BOSS_SIZE
    assert result[SCALAR_COUNT + len(catalog.units) + 8] == 1.0  # 크립 처치 achieved


def test_attack_actions_follow_the_first_568_and_mask_on_the_rows(
    map_data: MapData, state: GameState
) -> None:
    # Given
    catalog = map_data.catalog
    attacks = catalog.actions[_V3_ACTIONS : _V3_ACTIONS + len(OBJECTIVES)]
    alive = tuple(TechLevel(raw_id=objective.life, level=100) for objective in OBJECTIVES)
    fighter = (UnitCount(raw_id=_HERO, count=1),)
    playing = state.model_copy(update={"units": fighter, "techs": alive})
    achieved = playing.model_copy(
        update={"techs": (*alive, *(TechLevel(raw_id=raw, level=1) for raw in GOAL_IDS))}
    )

    # When
    window = slice(_V3_ACTIONS, _V3_ACTIONS + len(OBJECTIVES))
    open_mask = available_actions(playing, catalog)[window]
    done_mask = available_actions(achieved, catalog)[window]
    alone = available_actions(playing.model_copy(update={"units": ()}), catalog)[window]

    # Then: codes 331-336, the creep camp stays open after its mission.
    assert [action.code for action in attacks] == list(range(331, 331 + len(OBJECTIVES)))
    assert all(action.kind is ActionKind.ATTACK and action.fighter for action in attacks)
    assert [objective.label for objective in OBJECTIVES if objective.goal is not None] == [
        GOALS[objective.goal][1] for objective in OBJECTIVES if objective.goal is not None
    ]
    assert open_mask == (True,) * len(OBJECTIVES)
    assert done_mask == (False,) * (len(OBJECTIVES) - 1) + (True,)
    assert alone == (False,) * len(OBJECTIVES)
    assert not set(exported_techs(map_data.inputs)) & LOGBOOK_IDS
