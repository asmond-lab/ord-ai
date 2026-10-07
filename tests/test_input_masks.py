from pathlib import Path

import pytest

from ord_ai.build import read_map
from ord_ai.catalog import (
    GRADUATED,
    RESEARCH_GATES,
    Action,
    ActionKind,
    Catalog,
    Research,
    TechBound,
    available_actions,
    kind_of,
)
from ord_ai.objectdata import rawcode, rawtext
from ord_ai.protocol import GameState, TechLevel, UnitCount

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"

_WISP = int.from_bytes(b"e018", "big")
_HERO = int.from_bytes(b"h001", "big")
_DUMMY = int.from_bytes(b"e0IX", "big")
_CASTER = int.from_bytes(b"h05O", "big")
_UNLOCK = int.from_bytes(b"R00G", "big")
_LAB = int.from_bytes(b"R002", "big")


def allowed(state: GameState, action: Action, **changes: object) -> bool:
    supplied = state.model_copy(update=changes)
    catalog = Catalog(map_sha256="test", units=(), actions=(action,))
    return available_actions(supplied, catalog) == (True,)


def techs(**levels: int) -> tuple[TechLevel, ...]:
    return tuple(
        TechLevel(raw_id=int.from_bytes(code.encode(), "big"), level=level)
        for code, level in levels.items()
    )


SUPPLY = Action(
    code=17,
    name="sell: 물품 지원 (h0AX)",
    kind=ActionKind.SELL,
    gold=100,
    techs=(TechBound(raw_id=GRADUATED, minimum=0, maximum=0),),
)
MISSION = Action(
    code=18, name="sell: 미션", kind=ActionKind.SELL, techs=(TechBound(raw_id=_UNLOCK),)
)
TRAIN = Action(code=27, name="train: 유닛도박", kind=ActionKind.TRAIN, gold=200, lumber=1)
RESEARCH = Action(
    code=29,
    name="research: 불멸",
    kind=ActionKind.RESEARCH,
    research=Research(raw_id=_LAB, levels=21, base=100, step=50, lumber_base=1, lumber_step=1),
)
TRAIT = Action(
    code=58, name="ability: T특성강화", kind=ActionKind.ABILITY, casters=(_CASTER,), food=4
)
NAVIGATION = Action(code=292, name="sync: NAVI_1IDX", kind=ActionKind.SYNC, min_round=21)
QUEST = Action(code=307, name="sync: QUST_INDX", kind=ActionKind.SYNC, max_round=9)
PAD = Action(code=313, name="region: pad", kind=ActionKind.REGION, casters=(_WISP,))
TELEPORT = Action(code=310, name="region: teleport", kind=ActionKind.REGION, fighter=True)


@pytest.mark.parametrize(
    ("gold", "levels", "expected"),
    [(100, {}, True), (99, {}, False), (100, {"R01M": 1}, False)],
)
def test_supply_drop_needs_gold_and_gamble_house_1(
    state: GameState, gold: int, levels: dict[str, int], expected: bool
) -> None:
    # When / Then: h0AX is sold only by gamble house 1, before graduation (R01M).
    assert allowed(state, SUPPLY, gold=gold, techs=techs(**levels)) is expected


def test_missions_need_their_unlock_tech(state: GameState) -> None:
    # When
    locked = allowed(state, MISSION)
    unlocked = allowed(state, MISSION, techs=techs(R00G=1))

    # Then
    assert (locked, unlocked) == (False, True)


def test_training_needs_gold_and_lumber(state: GameState) -> None:
    # When
    enough = allowed(state, TRAIN, gold=200, lumber=1)
    no_lumber = allowed(state, TRAIN, gold=200, lumber=0)

    # Then
    assert (enough, no_lumber) == (True, False)


@pytest.mark.parametrize(
    ("level", "gold", "lumber", "expected"),
    [
        (0, 100, 1, True),
        (2, 199, 3, False),
        (2, 200, 3, True),
        (2, 200, 2, False),
        (21, 9999, 99, False),
    ],
)
def test_research_cost_grows_with_level_and_stops_at_the_last(
    state: GameState, level: int, gold: int, lumber: int, expected: bool
) -> None:
    # When / Then: the next level costs base + step * level gold and lumber alike.
    assert allowed(state, RESEARCH, gold=gold, lumber=lumber, techs=techs(R002=level)) is expected


@pytest.mark.parametrize(
    ("level", "gate", "expected"),
    [(11, 0, True), (12, 0, False), (12, 1, True), (15, 1, True)],
)
def test_unit_upgrades_past_level_12_need_r00h(
    state: GameState, level: int, gate: int, expected: bool
) -> None:
    # When / Then: level 13 of the unit upgrades requires R00H (object data greq 13).
    levels = techs(R002=level, R00H=gate)
    assert allowed(state, RESEARCH, gold=9999, lumber=99, techs=levels) is expected


def test_research_gates_match_the_map_object_data() -> None:
    # Given
    data = read_map(_MAP_PATH.read_bytes())

    # When: every non-empty per-level requirement of a research action in the catalog.
    gates: dict[int, tuple[int, int]] = {}
    for action in data.catalog.actions:
        if action.research is None:
            continue
        fields = data.objects.upgrades[rawtext(action.research.raw_id)].fields
        for (field, level), value in fields.items():
            if field == "greq" and value:
                gates[action.research.raw_id] = (level, rawcode(str(value)))

    # Then
    assert gates == RESEARCH_GATES


@pytest.mark.parametrize(
    ("units", "food", "expected"),
    [
        ((UnitCount(raw_id=_CASTER, count=1),), 4, True),
        ((UnitCount(raw_id=_CASTER, count=1),), 3, False),
        ((UnitCount(raw_id=_CASTER, count=0),), 4, False),
        ((), 4, False),
    ],
)
def test_abilities_need_a_caster_and_trait_points(
    state: GameState, units: tuple[UnitCount, ...], food: int, expected: bool
) -> None:
    # When / Then: food is the map's trait point counter (PLAYER_STATE_RESOURCE_FOOD_USED).
    assert allowed(state, TRAIT, units=units, food=food) is expected


@pytest.mark.parametrize(("round_", "expected"), [(20, False), (21, True), (40, True)])
def test_navigation_opens_at_round_21(state: GameState, round_: int, expected: bool) -> None:
    # When / Then
    assert allowed(state, NAVIGATION, round=round_) is expected


@pytest.mark.parametrize(("round_", "expected"), [(0, True), (9, True), (10, False)])
def test_quest_reroll_closes_at_round_10(state: GameState, round_: int, expected: bool) -> None:
    # When / Then
    assert allowed(state, QUEST, round=round_) is expected


def test_wisp_pads_need_that_wisp_and_teleports_a_fighter(state: GameState) -> None:
    # Given
    wisp = (UnitCount(raw_id=_WISP, count=1),)
    fighter = (UnitCount(raw_id=_HERO, count=1),)
    dummy = (UnitCount(raw_id=_DUMMY, count=3),)

    # When
    pads = (allowed(state, PAD, units=wisp), allowed(state, PAD, units=fighter))
    teleports = (allowed(state, TELEPORT, units=fighter), allowed(state, TELEPORT, units=dummy))

    # Then
    assert pads == (True, False)
    assert teleports == (True, False)


def test_catalogs_without_kinds_keep_their_code_ranges() -> None:
    # Given: schema-2 catalogs and hand-built test actions carry no kind.
    actions = [Action(code=code, name="x") for code in (0, 7, 8, 13, 14, 1093677109)]

    # When
    kinds = [kind_of(action) for action in actions]

    # Then
    assert kinds == [
        ActionKind.BASIC,
        ActionKind.BASIC,
        ActionKind.SHOP,
        ActionKind.SHOP,
        ActionKind.SHOP,
        ActionKind.FUSION,
    ]


def test_an_unknown_small_code_without_a_kind_is_masked(state: GameState) -> None:
    # When / Then: code 14+ without a kind is no base or shop action the mask knows.
    assert allowed(state, Action(code=14, name="x")) is False


def test_a_schema_2_catalog_file_still_loads() -> None:
    # Given
    text = (
        '{"schema_version":2,"map_sha256":"x","units":[],"actions":['
        '{"code":0,"name":"wait","units":[],"gold":0,"lumber":0,"extra_conditions":[]}]}'
    )

    # When
    catalog = Catalog.model_validate_json(text)

    # Then
    assert (catalog.schema_version, kind_of(catalog.actions[0])) == (2, ActionKind.BASIC)
