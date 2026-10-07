import math
from pathlib import Path

import pytest
import torch

from ord_ai.build import MapData, read_map
from ord_ai.catalog import RESEARCH_GATES, Catalog, action_allowed, available_actions
from ord_ai.inputs import exported_techs
from ord_ai.observation import COUNT_SCALE, features
from ord_ai.protocol import GameState, TechLevel, UnitCount

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_STRANGER = int.from_bytes(b"zzzz", "big")


@pytest.fixture(scope="module")
def map_data() -> MapData:
    return read_map(_MAP_PATH.read_bytes())


def _states(map_data: MapData, base: GameState, count: int) -> list[GameState]:
    generator = torch.Generator().manual_seed(7)

    def pick(high: int) -> int:
        return int(torch.randint(0, high + 1, (1,), generator=generator).item())

    catalog = map_data.catalog
    raw_units = [unit.raw_id for unit in catalog.units] + [_STRANGER]
    techs = sorted(
        {
            *exported_techs(map_data.inputs),
            *RESEARCH_GATES,
            *(g for _, g in RESEARCH_GATES.values()),
        }
    )
    golds = (0, 50, 99, 100, 250, 5_000, 35_000, 10**6)
    states: list[GameState] = []
    for _ in range(count):
        units = [
            UnitCount(raw_id=raw_units[pick(len(raw_units) - 1)], count=pick(5))
            for _ in range(pick(40))
        ]
        levels = [TechLevel(raw_id=raw, level=pick(22)) for raw in techs if pick(1) == 1]
        states.append(
            base.model_copy(
                update={
                    "gold": golds[pick(len(golds) - 1)],
                    "lumber": pick(30),
                    "food": pick(12),
                    "round": pick(66),
                    "units": tuple(units),
                    "techs": tuple(levels),
                }
            )
        )
    return states


def _reference_units(state: GameState, catalog: Catalog) -> tuple[float, ...]:
    counts = {unit.raw_id: unit.count for unit in state.units}
    return tuple(
        math.log1p(counts.get(unit.raw_id, 0)) / math.log1p(COUNT_SCALE) for unit in catalog.units
    )


def test_the_mask_plan_equals_the_rule_of_every_action(map_data: MapData, state: GameState) -> None:
    # Given
    catalog = map_data.catalog

    # When / Then: repeated raw ids, unknown units and every gated tech level included.
    for supplied in _states(map_data, state, 300):
        expected = tuple(action_allowed(action, supplied) for action in catalog.actions)
        assert available_actions(supplied, catalog) == expected


def test_unit_features_equal_the_per_unit_formula(map_data: MapData, state: GameState) -> None:
    # Given
    catalog = map_data.catalog
    start = 10

    # When / Then
    for supplied in _states(map_data, state, 100):
        units = features(supplied, catalog)[start : start + len(catalog.units)]
        assert units == _reference_units(supplied, catalog)


def test_a_new_catalog_object_gets_its_own_plan(map_data: MapData, state: GameState) -> None:
    # Given: the same actions with the first one repeated in front.
    catalog = map_data.catalog
    longer = Catalog(
        map_sha256=catalog.map_sha256,
        units=catalog.units,
        actions=(catalog.actions[0], *catalog.actions),
    )

    # When
    first = available_actions(state, catalog)
    second = available_actions(state, longer)

    # Then
    assert second == (first[0], *first)
