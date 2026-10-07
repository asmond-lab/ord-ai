import hashlib
import json
import re
from pathlib import Path

import pytest

from ord_ai.build import JASS_DIRECTORY, MapData, check_input_table, read_map
from ord_ai.catalog import (
    FIRST_INPUT_CODE,
    FUSION_FLOOR,
    GRADUATED,
    RESEARCH,
    ActionKind,
    TechBound,
    extract_catalog,
)
from ord_ai.inputs import (
    BASE_REGIONS,
    BASES,
    CHAT_ACTIONS,
    EXCLUDED_CHATS,
    EXCLUDED_SYNCS,
    FIGHTER_REGIONS,
    JASS_KINDS,
    LEGACY_TECHS,
    UPGRADE_DEFAULTS,
    PlayerInput,
    Target,
    derive_inputs,
    exported_techs,
    render_input_table,
    table_in,
)
from ord_ai.logbook import LOGBOOK_IDS
from ord_ai.objectdata import GameObject, rawcode, rawtext
from ord_ai.treasure import TREASURE_FLOOR, TREASURE_IDS, TREASURE_SLOTS

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_BASE_DATA = Path(
    "D:/OrandOverlay/local-tools/OrandSimulator.Runtime/BaseData/w3-3.0.0.24268/units"
)
# (code, name, units, gold, lumber, extra_conditions) of the 251 schema-2 actions and the
# 467 unit types, hashed from the schema-2 build/catalog.json.
_V2_ACTIONS_SHA = "111528c1ae791b072df2881040a6fe472794bd0cb9dfcf1fef9d9a0fde8e5834"
_V2_UNITS_SHA = "c02cc58b423ef8d56d54cb80a23350c4d12a960035996b70c932261cadb20ee0"
_V2_ACTIONS = 251
_JOS = re.compile(r"call Jos\((\d+),(\$[0-9a-f]{8}|'[^']{4}'),(\w+)\)")


@pytest.fixture(scope="module")
def map_data() -> MapData:
    return read_map(_MAP_PATH.read_bytes())


@pytest.fixture(scope="module")
def inputs(map_data: MapData) -> tuple[PlayerInput, ...]:
    return map_data.inputs


def jos(script: str, key: int) -> list[str]:
    found: list[str] = []
    for match in _JOS.finditer(script):
        raw = str(match.group(2))
        if int(match.group(1)) == key:
            found.append(rawtext(int(raw[1:], 16)) if raw.startswith("$") else raw.strip("'"))
    return found


def sha(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def field(item: GameObject, name: str, default: int) -> int:
    value = item.number(name)
    return default if value is None else int(value)


def by_raw(inputs: tuple[PlayerInput, ...], kind: ActionKind) -> dict[str, PlayerInput]:
    return {rawtext(item.route.raw_id): item for item in inputs if item.route.kind is kind}


def test_schema_3_keeps_every_schema_2_action_and_unit_in_place(map_data: MapData) -> None:
    # Given
    catalog = map_data.catalog

    # When
    prefix = [
        [
            action.code,
            action.name,
            [[unit.raw_id, unit.count] for unit in action.units],
            action.gold,
            action.lumber,
            list(action.extra_conditions),
        ]
        for action in catalog.actions[:_V2_ACTIONS]
    ]
    units = [unit.model_dump() for unit in catalog.units]

    # Then: old indices keep their actions and the observation vocabulary is unchanged.
    assert catalog.schema_version == 3
    assert sha(prefix) == _V2_ACTIONS_SHA
    assert sha(units) == _V2_UNITS_SHA


def test_new_actions_follow_with_codes_below_the_fusion_floor(map_data: MapData) -> None:
    # Given
    catalog = map_data.catalog
    old = catalog.actions[:_V2_ACTIONS]
    new = catalog.actions[_V2_ACTIONS:]

    # When
    codes = [action.code for action in new]

    # Then: the chest actions keep their fixed codes after the numbered inputs.
    numbered = len(new) - TREASURE_SLOTS
    assert codes == [
        *range(FIRST_INPUT_CODE, FIRST_INPUT_CODE + numbered),
        *range(TREASURE_FLOOR, TREASURE_FLOOR + TREASURE_SLOTS),
    ]
    assert max(codes) < FUSION_FLOOR
    assert not set(codes) & {action.code for action in old}
    assert all(action.kind not in (ActionKind.BASIC, ActionKind.FUSION) for action in new)
    assert len({action.name for action in new}) == len(new)


def test_the_committed_bridge_table_is_the_map_derived_one(
    map_data: MapData, inputs: tuple[PlayerInput, ...]
) -> None:
    # Given
    catalog = map_data.catalog
    source = (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8")
    fusions = {action.code for action in catalog.actions if action.kind is ActionKind.FUSION}

    # When
    check_input_table(map_data)
    derived = derive_inputs(map_data.script, map_data.strings, map_data.objects, fusions)

    # Then
    assert derived == inputs
    assert table_in(source) == render_input_table(inputs)
    assert [item.action for item in inputs] == list(catalog.actions[_V2_ACTIONS:])


def test_catalog_without_object_data_is_the_schema_2_vocabulary(map_data: MapData) -> None:
    # Given
    catalog = map_data.catalog

    # When
    legacy = extract_catalog(map_data.script, map_data.strings, catalog.map_sha256)

    # Then
    assert legacy.actions == catalog.actions[:_V2_ACTIONS]


def test_sold_and_trained_inputs_cover_the_dispatch_table(
    map_data: MapData, inputs: tuple[PlayerInput, ...]
) -> None:
    # Given
    script, objects = map_data.script, map_data.objects
    sells = by_raw(inputs, ActionKind.SELL)
    trains = by_raw(inputs, ActionKind.TRAIN)

    # When
    sold = set(jos(script, 10))
    trained = set(jos(script, 11))

    # Then: shop actions 8, 9, 13 already buy h06F, H0AZ and H0B0.
    assert sold == set(sells) | {"h06F", "H0AZ", "H0B0"}
    assert trained == set(trains)
    for code, item in {**sells, **trains}.items():
        unit = objects.units[code]
        assert item.action.gold == int(unit.number("ugol") or 0)
        assert item.action.lumber == int(unit.number("ulum") or 0)
        required = {rawcode(tech) for tech in unit.rawcodes("ureq")}
        assert required <= {bound.raw_id for bound in item.action.techs}


def test_gamble_house_items_carry_the_house_they_are_sold_at(
    inputs: tuple[PlayerInput, ...],
) -> None:
    # Given
    sells = by_raw(inputs, ActionKind.SELL)

    # When
    houses = {
        code: {bound.raw_id: (bound.minimum, bound.maximum) for bound in item.action.techs}.get(
            GRADUATED
        )
        for code, item in sells.items()
    }

    # Then: supply drop only before graduation; lumber and trait points only after it
    # (h0AK is stocked where pp[] is h08C, 4503/52292); Isekai gamble at both houses.
    assert houses["h0AX"] == (0, 0)
    assert houses["h0AL"] == (1, None)
    assert houses["h0AK"] == (1, None)
    assert houses["H0AW"] is None
    assert sells["H0AW"].route.buildings == (rawcode("h062"), rawcode("h08C"))
    assert {rawtext(building) for item in sells.values() for building in item.route.buildings} <= {
        "h062",
        "h08C",
        "h07A",
    }


def test_researches_cover_every_research_of_the_player_buildings(
    map_data: MapData, inputs: tuple[PlayerInput, ...]
) -> None:
    # Given
    objects = map_data.objects
    researches = by_raw(inputs, ActionKind.RESEARCH)
    listed = [
        code for lab in ("h06Q", "h06R", "h06S") for code in objects.units[lab].rawcodes("ures")
    ]

    # When
    old = {rawtext(research.raw_id) for research in RESEARCH.values()}

    # Then
    assert len(listed) == len(set(listed)) == 22
    assert set(listed) == set(researches) | old
    for code, item in researches.items():
        upgrade = objects.upgrades[code]
        default = UPGRADE_DEFAULTS[upgrade.base]
        research = item.action.research
        assert research is not None
        assert (
            research.levels,
            research.base,
            research.step,
            research.lumber_base,
            research.lumber_step,
        ) == (
            field(upgrade, "glvl", default.levels),
            field(upgrade, "gglb", default.gold),
            field(upgrade, "gglm", default.gold_step),
            field(upgrade, "glmb", default.lumber),
            field(upgrade, "glmm", default.lumber_step),
        )


def test_ability_inputs_cover_the_spell_dispatch_table(
    map_data: MapData, inputs: tuple[PlayerInput, ...]
) -> None:
    # Given
    script, objects, catalog = map_data.script, map_data.objects, map_data.catalog
    abilities = by_raw(inputs, ActionKind.ABILITY)
    fusions = {action.code for action in catalog.actions if action.kind is ActionKind.FUSION}

    # When
    dispatched = set(jos(script, 9))
    native = set(abilities) - dispatched

    # Then: 219 dispatched abilities, 29 of them fusions; 54 castable abilities of player
    # units without a handler (blink, roar, the helper house's rain, poison and sail...).
    assert len(dispatched) == 219
    assert {code for code in dispatched if rawcode(code) in fusions} == dispatched - set(abilities)
    assert len(dispatched - set(abilities)) == 29
    assert len(native) == 54
    assert {"A07V", "A07X", "A07Z"} <= native
    assert all(objects.abilities[code].base in BASES for code in abilities)


@pytest.mark.parametrize(
    ("code", "order", "target"),
    [
        ("A0EZ", "charm", Target.NONE),  # ANcl: Ncl6 is the issued order, aord is not
        ("AG00", "tranquility", Target.NONE),  # AAns: Ans5 is the base order id
        ("A07T", "earthquake", Target.ENEMY_POINT),
        ("A07W", "firebolt", Target.ENEMY_UNIT),
        ("A0HN", "fingerofdeath", Target.OWN_UNIT),  # Targets Allowed: player
        ("A0YN", "clusterrockets", Target.ENEMY_POINT),  # ANcl with Ncl2 2 (point)
        ("A0LF", "healingwave", Target.ENEMY_UNIT),  # ANcl with Ncl2 3, enemies
        ("A05D", "detectaoe", Target.CASTER_POINT),
        ("A0DI", "firebolt", Target.DISPLAY_UNIT),
        ("A09G", "summonphoenix", Target.NONE),
    ],
)
def test_ability_routes_follow_the_object_data(
    inputs: tuple[PlayerInput, ...], code: str, order: str, target: Target
) -> None:
    # When
    route = by_raw(inputs, ActionKind.ABILITY)[code].route

    # Then
    assert (route.order, route.target) == (order, target)


def test_handler_checks_become_target_filters_and_masks(inputs: tuple[PlayerInput, ...]) -> None:
    # Given
    abilities = by_raw(inputs, ActionKind.ABILITY)

    # When
    bullet = abilities["A0WM"].route
    ships = {rawtext(value) for value in abilities["A0DI"].route.filters}
    wisp_maker = abilities["A082"].action
    upgrade = abilities["A0TE"].action

    # Then: the Bullet item kills a common unit (CtR 8360); the shipwright takes board ships
    # (rHv 22693); the choice-wisp maker costs a trait point and needs R009 (YDR, areq);
    # trait upgrades need 4 trait points (BxR-style checks).
    assert {rawtext(item) for item in bullet.items} == {"I091", "I092", "I093"}
    assert (bullet.target, bullet.weakest, bullet.common) == (Target.OWN_UNIT, True, True)
    assert ships == {"h060", "h03P", "h03Q", "h03X", "h03L", "h03U"}
    assert wisp_maker.food == 1
    assert wisp_maker.techs == (TechBound(raw_id=rawcode("R009")),)
    assert upgrade.food == 4


def test_every_registered_input_is_an_action_or_an_exclusion(
    map_data: MapData, inputs: tuple[PlayerInput, ...]
) -> None:
    # Given
    script, catalog = map_data.script, map_data.catalog
    code_variables = dict(re.findall(r"^set (\w+)=function (\w+)$", script, re.MULTILINE))
    actions = (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8")
    fusion_chats = set(re.findall(r'call SaveStr\(ORDAI_data, 1, \$[0-9a-f]+, "(\w+)"\)', actions))
    prefixes = {item.route.prefix for item in inputs if item.route.prefix}
    pads = {
        item.route.rect
        for item in inputs
        if item.route.kind is ActionKind.REGION and item.route.raw_id
    }
    selected = {
        rawtext(item.route.raw_id) for item in inputs if item.route.kind is ActionKind.SELECT
    }

    # When
    registered_syncs = set(re.findall(r'call KdR\("(\w+)",\w+\)', script))
    registered_regions = set(re.findall(r"call kIs\((\w+(?:\[\w+\])?),\w+\)", script))
    chat_handlers = {
        code_variables[str(match.group(1))]
        for match in re.finditer(r'call FMv\("[^"]*",(?:true|false),(\w+)\)', script)
    }

    # Then
    assert registered_syncs == prefixes | EXCLUDED_SYNCS
    assert registered_regions == pads | set(FIGHTER_REGIONS) | BASE_REGIONS
    assert chat_handlers == fusion_chats | set(CHAT_ACTIONS) | EXCLUDED_CHATS
    assert set(jos(script, 7)) == selected
    assert len(catalog.actions) == _V2_ACTIONS + len(inputs)


def test_navigation_and_quest_choices_come_from_their_handlers(
    inputs: tuple[PlayerInput, ...],
) -> None:
    # Given
    syncs = [item for item in inputs if item.route.kind is ActionKind.SYNC]

    # When
    navigation = [item for item in syncs if item.route.prefix.startswith("NAVI_")]
    quest = [item for item in syncs if item.route.prefix == "QUST_INDX"]

    # Then: UfR and its siblings refuse before round 21; HDR only before round 10.
    assert [(item.route.prefix, item.route.payload) for item in navigation] == [
        (f"NAVI_{group}IDX", payload) for group in range(1, 6) for payload in (1, 2, 3)
    ]
    assert {item.action.min_round for item in navigation} == {21}
    assert [item.route.payload for item in quest] == [0, 1, 2]
    assert {item.action.max_round for item in quest} == {9}
    assert "NAVI_3IDX 도박광 :: 환전소" in navigation[8].action.name


def test_exported_tech_rows_cover_every_masked_tech(inputs: tuple[PlayerInput, ...]) -> None:
    # When
    techs = exported_techs(inputs)
    masked = {bound.raw_id for item in inputs for bound in item.action.techs} | {
        item.action.research.raw_id for item in inputs if item.action.research is not None
    }
    # The synthetic 기록지침 rows come from ORDAI_Logbook, not from GetPlayerTechCount.
    masked -= LOGBOOK_IDS | TREASURE_IDS

    # Then
    assert techs[: len(LEGACY_TECHS)] == tuple(rawcode(code) for code in LEGACY_TECHS)
    assert masked <= set(techs)
    assert len(techs) == len(set(techs))


def test_the_bridge_routes_input_codes_before_the_fusion_path() -> None:
    # Given
    source = (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8")
    act = source[source.index("function ORDAI_Act takes") :]
    dispatch = source[source.index("function ORDAI_Input takes") :]

    # When
    kinds = {
        int(match.group(1)): str(match.group(2))
        for match in re.finditer(r"kind == (\d+) then\n\s+call (ORDAI_\w+)\(a\)", dispatch)
    }

    # Then
    assert act.index("if a >= 14 and a < 1000 then") < act.index("if a >= 1000 then")
    assert f"kind >= {JASS_KINDS[ActionKind.SELL]} and kind <= 3 then" in dispatch
    assert kinds == {
        JASS_KINDS[ActionKind.ABILITY]: "ORDAI_Cast",
        JASS_KINDS[ActionKind.SYNC]: "ORDAI_Sync",
        JASS_KINDS[ActionKind.REGION]: "ORDAI_Move",
        JASS_KINDS[ActionKind.SELECT]: "ORDAI_Choose",
        JASS_KINDS[ActionKind.CHAT]: "ORDAI_Chat",
        JASS_KINDS[ActionKind.ATTACK]: "ORDAI_Attack",
        JASS_KINDS[ActionKind.TREASURE]: "ORDAI_Scan",
    }


def _slk(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[int, dict[int, str]] = {}
    row = 0
    for line in path.read_text("utf-8", errors="replace").splitlines():
        if not line.startswith("C;"):
            continue
        column = 0
        value = ""
        for part in line.split(";")[1:]:
            if part.startswith("X"):
                column = int(part[1:])
            elif part.startswith("Y"):
                row = int(part[1:])
            elif part.startswith("K"):
                value = part[1:].strip('"')
        rows.setdefault(row, {})[column] = value
    header = rows.pop(1)
    return {
        cells.get(1, ""): {header.get(key, ""): value for key, value in cells.items()}
        for cells in rows.values()
    }


def _orders() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in _BASE_DATA.glob("*abilityfunc.txt"):
        section = ""
        for line in path.read_text("utf-8", errors="replace").splitlines():
            header = re.fullmatch(r"\[(\w+)\]", line.strip())
            if header:
                section = header.group(1)
            elif line.lower().startswith("order="):
                found[section] = line.split("=", 1)[1].strip()
    return found


@pytest.mark.skipif(not _BASE_DATA.exists(), reason="Warcraft III base data is not available")
def test_base_tables_match_the_game_data() -> None:
    # Given
    abilities = _slk(_BASE_DATA / "abilitydata.slk")
    upgrades = _slk(_BASE_DATA / "upgradedata.slk")
    orders = _orders()
    engine_orders = {"Awfb", "ACfb", "Afod", "AHta", "AIlp", "ANcl", "AAns"}

    # When / Then: Order keys where the base data has them, targs1 and Rng1 defaults.
    for code, base in BASES.items():
        if code not in engine_orders:
            assert orders[code] == base.order, code
        row = abilities[code]
        if base.targets:
            assert set(base.targets.split(",")) <= set(row["targs1"].split(",")), code
    for code, default in UPGRADE_DEFAULTS.items():
        row = upgrades[code]
        assert (default.levels, default.gold, default.gold_step) == (
            int(row["maxlevel"]),
            int(row["goldbase"]),
            int(row["goldmod"]),
        )
        assert (default.lumber, default.lumber_step) == (
            int(row["lumberbase"]),
            int(row["lumbermod"]),
        )
