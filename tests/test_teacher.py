import pytest

from ord_ai.catalog import (
    EVOLUTION_WISP,
    GRADUATED,
    RANDOM_WISP,
    SHOP_ACTIONS,
    Action,
    ActionKind,
    BasicAction,
    Catalog,
    Requirement,
    TechBound,
    available_actions,
)
from ord_ai.protocol import GameState, TechLevel, UnitCount
from ord_ai.teacher import (
    COMMON_CHOICE_WISP,
    COMMON_CHOICES,
    CREATE_RARE_CODE,
    EVOLVE_CODE,
    GAMBLE_HIGH_CODE,
    GAMBLE_LOW_CODE,
    LINE_CODE,
    REJECTION_COOLDOWN,
    SHOP_COOLDOWN,
    STORY_CODE,
    SUMMON_ALL_CODE,
    SUMMON_CODE,
    SUPPLY_COOLDOWN,
    SUPPLY_UNIT,
    UPGRADE_CODES,
    WAIT_CODE,
    NoAllowedActionError,
    Teacher,
)

HERO = int.from_bytes(b"h001", "big")
SMALL = Action(code=1500, name="small", units=(Requirement(raw_id=HERO, count=1),))
BIG = Action(code=1400, name="big", units=(Requirement(raw_id=HERO, count=2),), lumber=1)
BASE = tuple(Action(code=code, name=f"base{code}") for code in range(7))
ALL = Action(code=SUMMON_ALL_CODE, name="summon_all_random_wisps")


def catalog(*extra: Action) -> Catalog:
    return Catalog(map_sha256="0" * 64, units=(), actions=BASE + extra)


def code_of(teacher: Teacher, state: GameState, actions: Catalog) -> int:
    return actions.actions[teacher.choose(state, actions, (True,) * len(actions.actions))].code


def with_units(state: GameState, *units: tuple[int, int], **update: int) -> GameState:
    counts = tuple(UnitCount(raw_id=raw_id, count=count) for raw_id, count in units)
    return state.model_copy(update={"units": counts, **update})


def test_prefers_summon_all_when_catalog_has_it(state: GameState) -> None:
    # Given
    actions = catalog(ALL, SMALL)
    wisps = with_units(state, (RANDOM_WISP, 3), (HERO, 5))

    # When
    code = code_of(Teacher(), wisps, actions)

    # Then
    assert code == SUMMON_ALL_CODE


def test_falls_back_to_single_summon_without_code_7(state: GameState) -> None:
    # Given
    wisps = with_units(state, (RANDOM_WISP, 3))

    # When
    code = code_of(Teacher(), wisps, catalog(SMALL))

    # Then
    assert code == SUMMON_CODE


def test_activates_evolution_wisp(state: GameState) -> None:
    # Given
    evolving = with_units(state, (EVOLUTION_WISP, 1))

    # When
    code = code_of(Teacher(), evolving, catalog())

    # Then
    assert code == EVOLVE_CODE


@pytest.mark.parametrize("seed", range(64))
def test_never_returns_masked_action(state: GameState, seed: int) -> None:
    # Given
    actions = catalog(ALL, SMALL, BIG)
    size = len(actions.actions)
    mask = tuple(bool((seed * 2654435761 >> bit) & 1) for bit in range(size))
    mask = mask if any(mask) else (False,) * (size - 1) + (True,)
    busy = with_units(state, (RANDOM_WISP, 2), (EVOLUTION_WISP, 1), (HERO, 3), round=8, enemies=70)

    # When
    index = Teacher().choose(busy, actions, mask)

    # Then
    assert mask[index]


def test_fully_masked_space_is_an_error(state: GameState) -> None:
    # Given
    actions = catalog()

    # When / Then
    with pytest.raises(NoAllowedActionError):
        _ = Teacher().choose(state, actions, (False,) * len(actions.actions))


def test_picks_highest_scoring_fusion(state: GameState) -> None:
    # Given
    ready = with_units(state, (HERO, 2), lumber=1)

    # When
    code = code_of(Teacher(), ready, catalog(SMALL, BIG))

    # Then
    assert code == BIG.code


def test_rejected_fusion_skipped_until_cooldown_ends(state: GameState) -> None:
    # Given
    teacher = Teacher()
    actions = catalog(SMALL, BIG)
    ready = with_units(state, (HERO, 2), lumber=1)
    assert code_of(teacher, ready, actions) == BIG.code
    teacher.observe(ready.model_copy(update={"action_ok": 0}))

    # When
    during = [code_of(teacher, ready, actions) for _ in range(REJECTION_COOLDOWN)]
    after = code_of(teacher, ready, actions)

    # Then
    assert set(during) == {SMALL.code}
    assert after == BIG.code


def test_three_rejections_ban_fusion_for_episode(state: GameState) -> None:
    # Given
    teacher = Teacher()
    actions = catalog(BIG)
    ready = with_units(state, (HERO, 2), lumber=1)
    for _ in range(3):
        while code_of(teacher, ready, actions) != BIG.code:
            teacher.observe(ready)
        teacher.observe(ready.model_copy(update={"action_ok": 0}))

    # When
    later = {code_of(teacher, ready, actions) for _ in range(3 * REJECTION_COOLDOWN)}
    teacher.reset()
    fresh = code_of(teacher, ready, actions)

    # Then
    assert later == {WAIT_CODE}
    assert fresh == BIG.code


def test_story_counter_limits_sends_to_two(state: GameState) -> None:
    # Given
    teacher = Teacher()
    calm = with_units(state, (HERO, 5), round=6, enemies=10)
    first = code_of(teacher, calm, catalog())
    teacher.observe(calm.model_copy(update={"action_ok": 0}))
    for _ in range(2):
        assert code_of(teacher, calm, catalog()) == STORY_CODE
        teacher.observe(calm)

    # When
    code = code_of(teacher, calm, catalog())

    # Then
    assert first == STORY_CODE
    assert code == WAIT_CODE


def test_emergency_returns_story_units(state: GameState) -> None:
    # Given
    teacher = Teacher()
    calm = with_units(state, (HERO, 5), round=6, enemies=10)
    _ = code_of(teacher, calm, catalog())
    teacher.observe(calm)
    flooded = calm.model_copy(update={"enemies": 60})

    # When
    first = code_of(teacher, flooded, catalog())
    teacher.observe(flooded)
    second = code_of(teacher, flooded, catalog())

    # Then
    assert first == LINE_CODE
    assert second == WAIT_CODE


def test_waits_when_no_rule_applies(state: GameState) -> None:
    # Given
    early = with_units(state, (HERO, 1))

    # When
    code = code_of(Teacher(), early, catalog())

    # Then
    assert code == WAIT_CODE


SHOP = catalog(
    *(
        Action(code=BasicAction.GAMBLE_LOW + offset, name=name)
        for offset, name in enumerate(SHOP_ACTIONS)
    )
)


def economy(state: GameState, gold: int, **levels: int) -> GameState:
    techs = tuple(
        TechLevel(raw_id=int.from_bytes(code.encode("ascii"), "big"), level=level)
        for code, level in levels.items()
    )
    return with_units(state, (HERO, 1), gold=gold).model_copy(update={"techs": techs})


def masked_code(teacher: Teacher, state: GameState) -> int:
    return SHOP.actions[teacher.choose(state, SHOP, available_actions(state, SHOP))].code


def test_game_start_spins_the_low_gamble_then_upgrades_common(state: GameState) -> None:
    # Given
    teacher = Teacher()
    start = economy(state, gold=50)
    first = masked_code(teacher, start)

    # When: the gamble house ran out of stock.
    teacher.observe(start.model_copy(update={"action_ok": 0}))
    second = masked_code(teacher, start)

    # Then
    assert (first, second) == (GAMBLE_LOW_CODE, UPGRADE_CODES[0])


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        ({"R00F": 7}, UPGRADE_CODES[1]),
        ({"R00F": 7, "R00E": 15}, UPGRADE_CODES[2]),
        ({"R00F": 7, "R00E": 15, "R008": 16, "R009": 1}, GAMBLE_HIGH_CODE),
        ({"R00F": 7, "R00E": 15, "R008": 16}, WAIT_CODE),
    ],
)
def test_upgrades_go_common_special_rare_then_the_high_gamble(
    state: GameState, levels: dict[str, int], expected: int
) -> None:
    # Given: the low gamble is out of stock.
    teacher = Teacher()
    rich = economy(state, gold=10_000, **levels)
    assert masked_code(teacher, rich) == GAMBLE_LOW_CODE
    teacher.observe(rich.model_copy(update={"action_ok": 0}))

    # When
    code = masked_code(teacher, rich)

    # Then
    assert code == expected


def test_refused_low_gamble_is_retried_after_its_stock_cooldown(state: GameState) -> None:
    # Given: every upgrade is finished, so only the gamble competes with waiting.
    teacher = Teacher()
    done = economy(state, gold=10_000, R00F=7, R00E=15, R008=16)
    assert masked_code(teacher, done) == GAMBLE_LOW_CODE
    teacher.observe(done.model_copy(update={"action_ok": 0}))

    # When
    during = {masked_code(teacher, done) for _ in range(SHOP_COOLDOWN[GAMBLE_LOW_CODE])}
    after = masked_code(teacher, done)

    # Then
    assert during == {WAIT_CODE}
    assert after == GAMBLE_LOW_CODE


def test_rare_unit_is_created_once_after_graduation(state: GameState) -> None:
    # Given
    teacher = Teacher()
    graduated = economy(state, gold=20_000, R00F=7, R00E=15, R008=16, R009=1, R01M=1, R01N=1)
    first = masked_code(teacher, graduated)

    # When
    teacher.observe(graduated)
    later = {masked_code(teacher, graduated) for _ in range(3)}

    # Then
    assert first == CREATE_RARE_CODE
    assert CREATE_RARE_CODE not in later


def choice_action(code: int, number: int) -> Action:
    return Action(code=code, name=f"region: 흔함선택위습 -> XY (흔함 {number})")


def test_a_common_choice_wisp_becomes_the_common_that_completes_a_recipe(
    state: GameState,
) -> None:
    # Given: a recipe that lacks one 흔함 3 (COMMON_CHOICES[2]).
    nami = COMMON_CHOICES[2]
    recipe = Action(code=1600, name="needs nami", units=(Requirement(raw_id=nami, count=2),))
    actions = catalog(choice_action(313, 1), choice_action(315, 3), recipe)
    waiting = with_units(state, (COMMON_CHOICE_WISP, 1), (nami, 1))

    # When
    code = code_of(Teacher(), waiting, actions)

    # Then
    assert code == 315


def test_without_a_recipe_to_complete_the_least_owned_common_is_chosen(
    state: GameState,
) -> None:
    # Given
    actions = catalog(choice_action(313, 1), choice_action(314, 2))
    waiting = with_units(state, (COMMON_CHOICE_WISP, 1), (COMMON_CHOICES[0], 2))

    # When
    code = code_of(Teacher(), waiting, actions)

    # Then
    assert code == 314


SUPPLY = Action(
    code=17,
    name="sell: 물품 지원 (h0AX)",
    kind=ActionKind.SELL,
    raw_id=SUPPLY_UNIT,
    gold=100,
    techs=(TechBound(raw_id=GRADUATED, minimum=0, maximum=0),),
)
SHOP_AND_SUPPLY = Catalog(map_sha256="0" * 64, units=(), actions=(*SHOP.actions, SUPPLY))


def supply_code(teacher: Teacher, state: GameState) -> int:
    mask = available_actions(state, SHOP_AND_SUPPLY)
    return SHOP_AND_SUPPLY.actions[teacher.choose(state, SHOP_AND_SUPPLY, mask)].code


@pytest.mark.parametrize(
    ("levels", "expected"),
    [({"R00F": 3}, UPGRADE_CODES[0]), ({"R00F": 7}, SUPPLY.code)],
)
def test_supply_is_bought_after_the_common_upgrade_before_the_special_one(
    state: GameState, levels: dict[str, int], expected: int
) -> None:
    # Given: the low gamble is out of stock.
    teacher = Teacher()
    rich = economy(state, gold=10_000, **levels)
    assert supply_code(teacher, rich) == GAMBLE_LOW_CODE
    teacher.observe(rich.model_copy(update={"action_ok": 0}))

    # When
    code = supply_code(teacher, rich)

    # Then
    assert code == expected


def test_refused_supply_is_not_retried_while_its_stock_stays_empty(state: GameState) -> None:
    # Given: the low gamble and the supply are both out of stock.
    teacher = Teacher()
    rich = economy(state, gold=10_000, R00F=7)
    refused = rich.model_copy(update={"action_ok": 0})
    assert supply_code(teacher, rich) == GAMBLE_LOW_CODE
    teacher.observe(refused)
    assert supply_code(teacher, rich) == SUPPLY.code
    teacher.observe(refused)

    # When
    codes = {supply_code(teacher, rich) for _ in range(SHOP_COOLDOWN[GAMBLE_LOW_CODE] + 5)}

    # Then: the special upgrade goes on; supply waits for SUPPLY_COOLDOWN decisions.
    assert SUPPLY.code not in codes
    assert UPGRADE_CODES[1] in codes
    assert SHOP_COOLDOWN[GAMBLE_LOW_CODE] < SUPPLY_COOLDOWN
