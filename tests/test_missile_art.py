from pathlib import Path

import pytest

from ord_ai.build import build_map, read_map
from ord_ai.missile_art import REPLACEMENT, patch_missile_art
from ord_ai.mpq import read_member
from ord_ai.objectdata import iter_fields, parse_objects

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_SIZE_GROWTH = 206


@pytest.fixture(scope="module")
def original() -> bytes:
    return _MAP_PATH.read_bytes()


def test_only_broken_art_on_missile_weapons_becomes_the_dummy_model(original: bytes) -> None:
    # Given
    w3u = read_member(original, "war3map.w3u")

    # When
    patched, changed = patch_missile_art(w3u)

    # Then: 26 ua1m fields; "" and ".mdl" grow by len("dummy.mdl") or by 5.
    assert len(changed) == 26
    assert {name for _, name in changed} == {"ua1m"}
    assert len(patched) - len(w3u) == _SIZE_GROWTH
    before = parse_objects(w3u, leveled=False)
    after = parse_objects(patched, leveled=False)
    assert before.keys() == after.keys()
    for key, unit in before.items():
        fields = dict(unit.fields)
        if (key, "ua1m") in changed:
            assert fields["ua1m", 0] in {"", ".mdl"}
            assert unit.text("ua1w") in {"missile", "msplash"}
            fields["ua1m", 0] = REPLACEMENT
        assert after[key].fields == fields


def test_broken_art_on_weapons_without_a_missile_is_kept(original: bytes) -> None:
    # Given
    w3u = read_member(original, "war3map.w3u")
    _, changed = patch_missile_art(w3u)

    # When: normal, instant or base-default weapons with the same art.
    kept = {
        entry.key
        for entry in iter_fields(w3u, leveled=False)
        if entry.name == "ua1m" and str(entry.value).strip() in {"", ".mdl"}
    } - {key for key, _ in changed}

    # Then
    units = parse_objects(w3u, leveled=False)
    assert kept
    assert all(units[key].text("ua1w") not in {"missile", "msplash"} for key in kept)


def test_the_built_map_carries_the_patched_units_and_the_same_catalog(
    original: bytes, tmp_path: Path
) -> None:
    # When
    catalog = build_map(_MAP_PATH, tmp_path / "ORDR_AI_2.323R.w3x", record="events")

    # Then: the archive reads back the patched units, keeps the dummy model and the policy
    # vocabulary; the supplied map file is unchanged.
    built = (tmp_path / "ORDR_AI_2.323R.w3x").read_bytes()
    expected, _ = patch_missile_art(read_member(original, "war3map.w3u"))
    assert read_member(built, "war3map.w3u") == expected
    assert read_member(built, "dummy.mdx") == read_member(original, "dummy.mdx")
    assert read_member(built, "war3map.w3a") == read_member(original, "war3map.w3a")
    assert catalog == read_map(original).catalog
    assert _MAP_PATH.read_bytes() == original
