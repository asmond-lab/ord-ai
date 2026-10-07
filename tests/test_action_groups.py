import re
from pathlib import Path

import pytest

from ord_ai.action_groups import (
    GROUPS,
    ITEM_RECIPES,
    RARE_FUSION_SUFFIX,
    TRANSCENDENT_RECIPES,
    action_groups,
    navigation_styles,
)
from ord_ai.build import MapData, read_map
from ord_ai.catalog import RECIPES, ActionKind, available_actions
from ord_ai.objectdata import rawtext
from ord_ai.protocol import GameState, TechLevel, UnitCount
from ord_ai.teacher import Teacher

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_RESULT = re.compile(r"call SaveInteger\(Qv,Mv,gv,\$([0-9a-f]+)\)")
_TEACHER_CODES = frozenset(range(14))


@pytest.fixture(scope="module")
def map_data() -> MapData:
    return read_map(_MAP_PATH.read_bytes())


def _results(map_data: MapData) -> dict[str, list[str]]:
    """Fusion recipe code -> the unit types it creates (war3map.j recipe table)."""
    return {
        rawtext(int(match.group(1), 16)): [
            rawtext(int(str(value.group(1)), 16)) for value in _RESULT.finditer(match.group(2))
        ]
        for match in RECIPES.finditer(map_data.script)
    }


def test_groups_hold_the_window_actions(map_data: MapData) -> None:
    # Given
    catalog = map_data.catalog
    names = {action.code: action.name for action in catalog.actions}

    # When
    groups = action_groups(catalog)

    # Then
    assert tuple(groups) == GROUPS
    assert groups["navigation"] == frozenset(range(292, 307))
    assert {code for code in groups["voyage"] if code < 1000} == {
        87,
        166,
        307,
        308,
        309,
        311,
        312,
        *range(331, 337),
    }
    assert {code for code in groups["route"] if code < 1000} == {222}
    assert not (groups["buildings"] | groups["route"] | groups["voyage"]) & _TEACHER_CODES
    assert 17 not in groups["buildings"]  # the teacher's 물품 지원 purchase
    assert all(
        names[code].startswith(("sell:", "train:", "research:", "ability:"))
        for code in groups["buildings"]
    )


def test_route_fusions_are_the_quest_conditions(map_data: MapData) -> None:
    # Given: IwR 16880 wants a hero with A07O, jav 50036 one of h067, h08O, H0BK.
    units = map_data.objects.units
    results = _results(map_data)
    fusions = [action for action in map_data.catalog.actions if action.kind is ActionKind.FUSION]

    # When
    transcendent = {
        rawtext(action.code)
        for action in fusions
        if any(
            code[:1] == "H" and "A07O" in units[code].rawcodes("uabi")
            for code in results[rawtext(action.code)]
        )
    }
    item = {
        rawtext(action.code)
        for action in fusions
        if set(results[rawtext(action.code)]) & {"h067", "h08O", "H0BK"}
    }
    rare = {
        rawtext(action.code)
        for action in fusions
        if any("A0B9" in units[code].rawcodes("uabi") for code in results[rawtext(action.code)])
    }

    # Then
    assert transcendent == TRANSCENDENT_RECIPES
    assert item == ITEM_RECIPES
    assert rare == {
        rawtext(action.code) for action in fusions if action.name.endswith(RARE_FUSION_SUFFIX)
    }


def test_navigation_styles_split_the_choices(map_data: MapData) -> None:
    # When
    styles = navigation_styles(map_data.catalog)

    # Then: 298 카지노, 300 환전소 (연속베팅);
    # 292 일석이조, 293 긴급소집, 296 바운티헌터, 302 연금술.
    assert styles == {
        "high_risk": frozenset({298, 300}),
        "low_point": frozenset({292, 293, 296, 302}),
    }


def test_the_teacher_never_picks_an_appended_action(map_data: MapData, state: GameState) -> None:
    # Given: every attack is allowed, nothing the teacher prefers is.
    catalog = map_data.catalog
    teacher = Teacher()
    supplied = state.model_copy(
        update={
            "gold": 0,
            "units": (UnitCount(raw_id=int.from_bytes(b"h001", "big"), count=1),),
            "techs": tuple(
                TechLevel(raw_id=bound.raw_id, level=bound.minimum or 0)
                for action in catalog.actions[568:]
                for bound in action.techs
                if bound.minimum
            ),
        }
    )
    mask = available_actions(supplied, catalog)

    # When
    index = teacher.choose(supplied, catalog, mask)

    # Then
    assert any(mask[568:])
    assert catalog.actions[index].code < 331
