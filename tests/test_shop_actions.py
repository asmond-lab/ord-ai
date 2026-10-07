import struct
from pathlib import Path
from typing import cast

import pytest

from ord_ai.catalog import (
    BASE_ACTIONS,
    CREATE_RARE_GOLD,
    GAMBLE_HIGH_GOLD,
    GAMBLE_LOW_GOLD,
    GRADUATED,
    HIGH_GAMBLE_UNLOCK,
    RARE_UNIT_UNLOCK,
    RESEARCH,
    SHOP_ACTIONS,
    UPGRADE_COMMON,
    Action,
    BasicAction,
    Catalog,
    available_actions,
    extract_catalog,
)
from ord_ai.mpq import read_member
from ord_ai.protocol import GameState, TechLevel

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
Objects = dict[str, dict[tuple[str, int], int | float | str]]


def parse_objects(data: bytes, *, leveled: bool) -> Objects:
    """Read a war3map object modification file (version 2): custom field values only."""
    offset = 4
    objects: Objects = {}

    def take(fmt: str) -> int:
        nonlocal offset
        value = cast("int", struct.unpack_from(fmt, data, offset)[0])
        offset += struct.calcsize(fmt)
        return value

    for _table in range(2):
        for _object in range(take("<i")):
            original, custom = data[offset : offset + 4], data[offset + 4 : offset + 8]
            offset += 8
            key = (custom if custom != b"\0\0\0\0" else original).decode("latin1")
            fields = objects.setdefault(key, {})
            for _field in range(take("<i")):
                field = data[offset : offset + 4].decode("latin1")
                offset += 4
                kind = take("<i")
                level = take("<i") if leveled else 0
                if leveled:
                    _ = take("<i")
                if kind == 0:
                    value: int | float | str = take("<i")
                elif kind in (1, 2):
                    value = struct.unpack_from("<f", data, offset)[0]
                    offset += 4
                else:
                    end = data.index(b"\0", offset)
                    value = data[offset:end].decode("utf-8", "replace")
                    offset = end + 1
                offset += 4
                fields[field, level] = value
    return objects


@pytest.fixture(scope="module")
def map_data() -> tuple[str, Objects, Objects]:
    source = _MAP_PATH.read_bytes()
    script = read_member(source, "war3map.j").decode("utf-8").replace("\r\n", "\n")
    units = parse_objects(read_member(source, "war3map.w3u"), leveled=False)
    upgrades = parse_objects(read_member(source, "war3map.w3q"), leveled=True)
    return script, units, upgrades


def raw(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def test_gamble_houses_sell_what_the_bridge_buys(map_data: tuple[str, Objects, Objects]) -> None:
    # Given
    _, units, _ = map_data

    # When
    house_one = str(units["h062"]["useu", 0]).split(",")
    house_two = str(units["h08C"]["useu", 0]).split(",")

    # Then
    assert {"h06F", "H0AZ"} <= set(house_one)
    assert "H0B0" in house_two


def test_shop_costs_and_unlocks_match_the_map(map_data: tuple[str, Objects, Objects]) -> None:
    # Given
    _, units, _ = map_data

    # When
    costs = {code: units[code]["ugol", 0] for code in ("h06F", "H0AZ", "H0B0")}
    requirements = {code: units[code]["ureq", 0] for code in ("H0AZ", "H0B0")}

    # Then
    assert costs == {"h06F": GAMBLE_LOW_GOLD, "H0AZ": GAMBLE_HIGH_GOLD, "H0B0": CREATE_RARE_GOLD}
    assert requirements == {"H0AZ": "R009", "H0B0": "R01N"}
    assert (raw("R009"), raw("R01N")) == (HIGH_GAMBLE_UNLOCK, RARE_UNIT_UNLOCK)


def test_upgrade_house_researches_match_the_map(map_data: tuple[str, Objects, Objects]) -> None:
    # Given
    _, units, upgrades = map_data
    researched = str(units["h06Q"]["ures", 0]).split(",")

    # When
    found = {
        research.raw_id.to_bytes(4, "big").decode("ascii"): (
            upgrades[code]["glvl", 0],
            upgrades[code]["gglb", 0],
            upgrades[code]["gglm", 0],
        )
        for research in RESEARCH.values()
        for code in [research.raw_id.to_bytes(4, "big").decode("ascii")]
    }

    # Then
    assert set(found) <= set(researched)
    assert found == {
        code: (research.levels, research.base, research.step)
        for research in RESEARCH.values()
        for code in [research.raw_id.to_bytes(4, "big").decode("ascii")]
    }


def test_script_wires_the_houses_and_graduation(map_data: tuple[str, Objects, Objects]) -> None:
    # Given
    script, _, _ = map_data

    # When / Then: pp[] starts as house 1 and becomes house 2 after 35000 gamble gold,
    # which also researches the graduation flag; rp[] is upgrade house 1.
    assert "set pp[ULR]=CreateUnit(Player(ULR),$68303632," in script
    assert "set rp[ULR]=CreateUnit(Player(ULR),$68303651," in script
    assert "if ts[sKs]>=$88b8 then" in script
    assert "set pp[sKs]=CreateUnit(Player(sKs),$68303843,rKs,WKs,bj_UNIT_FACING)" in script
    assert f"call SetPlayerTechResearched(Player(sKs),${GRADUATED:x},1)" in script
    assert "call Jos(10,$68303646,Lmv)" in script
    assert "call Jos(10,$4830415a,Fmv)" in script
    assert "call Jos(10,$48304230,vYv)" in script


def test_real_catalog_appends_shop_actions_after_every_old_action(
    map_data: tuple[str, Objects, Objects],
) -> None:
    # Given
    script, _, _ = map_data
    source = _MAP_PATH.read_bytes()
    strings = read_member(source, "war3map.wts").decode("utf-8-sig", errors="replace")

    # When
    catalog = extract_catalog(script, strings, "test")

    # Then
    tail = catalog.actions[-len(SHOP_ACTIONS) :]
    assert [(action.code, action.name) for action in tail] == [
        (BasicAction.GAMBLE_LOW + offset, name) for offset, name in enumerate(SHOP_ACTIONS)
    ]
    assert [action.name for action in catalog.actions[: len(BASE_ACTIONS)]] == list(BASE_ACTIONS)
    assert (
        min(action.code for action in catalog.actions[len(BASE_ACTIONS) : -len(SHOP_ACTIONS)])
        >= 1000
    )


SHOP_CATALOG = Catalog(
    map_sha256="test",
    units=(),
    actions=tuple(
        Action(code=BasicAction.GAMBLE_LOW + offset, name=name)
        for offset, name in enumerate(SHOP_ACTIONS)
    ),
)


def mask_for(state: GameState, gold: int, **levels: int) -> dict[str, bool]:
    techs = tuple(TechLevel(raw_id=raw(code), level=level) for code, level in levels.items())
    supplied = state.model_copy(update={"gold": gold, "techs": techs})
    return dict(zip(SHOP_ACTIONS, available_actions(supplied, SHOP_CATALOG), strict=True))


def test_start_of_game_allows_low_gamble_and_first_upgrades(state: GameState) -> None:
    # When
    mask = mask_for(state, gold=10)

    # Then
    assert mask == {
        "gold_gamble_low": True,
        "gold_gamble_high": False,
        "upgrade_common": True,
        "upgrade_special": True,
        "upgrade_rare": True,
        "create_rare_unit": False,
    }


def test_high_gamble_needs_the_round_ten_boss_unlock(state: GameState) -> None:
    # When
    locked = mask_for(state, gold=500)["gold_gamble_high"]
    unlocked = mask_for(state, gold=500, R009=1)["gold_gamble_high"]
    poor = mask_for(state, gold=499, R009=1)["gold_gamble_high"]

    # Then
    assert (locked, unlocked, poor) == (False, True, False)


def test_upgrade_cost_grows_with_level_and_stops_at_the_last_level(state: GameState) -> None:
    # Given: the common upgrade's next level costs 3 + 2 * current level gold.
    common = RESEARCH[BasicAction.UPGRADE_COMMON]
    assert common.raw_id == UPGRADE_COMMON

    # When
    enough = mask_for(state, gold=13, R00F=5)["upgrade_common"]
    short = mask_for(state, gold=12, R00F=5)["upgrade_common"]
    maxed = mask_for(state, gold=10_000, R00F=7)["upgrade_common"]

    # Then
    assert (enough, short, maxed) == (True, False, False)


def test_graduation_swaps_gambles_for_rare_unit_creation(state: GameState) -> None:
    # When
    before_story = mask_for(state, gold=5000, R009=1, R01M=1)
    after_story = mask_for(state, gold=5000, R009=1, R01M=1, R01N=1)
    poor = mask_for(state, gold=4999, R009=1, R01M=1, R01N=1)

    # Then
    assert before_story["create_rare_unit"] is False
    assert after_story["create_rare_unit"] is True
    assert poor["create_rare_unit"] is False
    assert not after_story["gold_gamble_low"]
    assert not after_story["gold_gamble_high"]
