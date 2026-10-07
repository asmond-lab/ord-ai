from pathlib import Path

import pytest

from ord_ai.catalog import (
    BASE_ACTIONS,
    SHOP_ACTIONS,
    Action,
    Catalog,
    available_actions,
    extract_catalog,
)
from ord_ai.mpq import read_member
from ord_ai.protocol import GameState, UnitCount

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_RANDOM_WISP = int.from_bytes(b"e0IX", "big")
_EVOLUTION_WISP = int.from_bytes(b"e0HP", "big")
# ORDAI_Act routes every code at or above this value to fusion, below it to base actions.
_FUSION_FLOOR = 1000


@pytest.fixture(scope="module")
def map_text() -> tuple[str, str]:
    source = _MAP_PATH.read_bytes()
    script = read_member(source, "war3map.j").decode("utf-8").replace("\r\n", "\n")
    strings = read_member(source, "war3map.wts").decode("utf-8-sig", errors="replace")
    return script, strings


def test_real_catalog_places_summon_all_at_its_wire_code(map_text: tuple[str, str]) -> None:
    # Given
    script, strings = map_text

    # When
    catalog = extract_catalog(script, strings, "test")

    # Then
    action = catalog.actions[7]
    assert (action.code, action.name) == (7, "summon_all_random_wisps")


def test_real_catalog_keeps_fusion_codes_out_of_the_base_range(
    map_text: tuple[str, str],
) -> None:
    # Given
    script, strings = map_text

    # When
    catalog = extract_catalog(script, strings, "test")

    # Then
    fusion_codes = [
        action.code for action in catalog.actions[len(BASE_ACTIONS) : -len(SHOP_ACTIONS)]
    ]
    assert min(fusion_codes) >= _FUSION_FLOOR


@pytest.mark.parametrize(
    ("units", "allowed"),
    [
        ((), False),
        ((UnitCount(raw_id=_EVOLUTION_WISP, count=2),), False),
        ((UnitCount(raw_id=_RANDOM_WISP, count=0),), False),
        ((UnitCount(raw_id=_RANDOM_WISP, count=1),), True),
    ],
)
def test_summon_all_is_available_only_with_a_random_wisp(
    state: GameState, units: tuple[UnitCount, ...], allowed: bool
) -> None:
    # Given
    catalog = Catalog(
        map_sha256="test",
        units=(),
        actions=(Action(code=7, name="summon_all_random_wisps"),),
    )
    supplied = state.model_copy(update={"units": units})

    # When
    mask = available_actions(supplied, catalog)

    # Then
    assert mask == (allowed,)
