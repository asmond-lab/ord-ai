from pathlib import Path

import pytest

from ord_ai.boss import BOSS_HEALTH, ROUND_TIME_LEFT
from ord_ai.catalog import EVOLUTION_WISP, RANDOM_WISP
from ord_ai.protocol import GameState, ProtocolError, TechLevel
from ord_ai.telemetry import (
    TELEMETRY_FILE,
    Telemetry,
    TrackedUnit,
    parse_telemetry,
    pool_draws,
    read_telemetry,
    spawned,
)
from ord_ai.training import transition_log
from ord_ai.treasure import CHEST_COUNT

HERO = int.from_bytes(b"h001", "big")
DRAWN = int.from_bytes(b"h00J", "big")


def record(sequence: int, *units: str, evolution: tuple[int, int] = (0, 0)) -> str:
    rows = [f"ORD_AI|1|TIME|7|{sequence}|12.500|4242|99|{evolution[0]}|{evolution[1]}"]
    rows += [f"ORD_AI|1|POS|{unit}" for unit in units]
    rows.append(f"ORD_AI|1|TEND|7|{sequence}")
    return "\n".join(f'\tcall Preload( "{row}" )' for row in rows)


def unit(handle: int, raw_id: int) -> TrackedUnit:
    return TrackedUnit(handle=handle, raw_id=raw_id, x=0.0, y=0.0, order=0)


def telemetry(*units: TrackedUnit, evolution: tuple[int, int] = (0, 0)) -> Telemetry:
    return Telemetry(
        episode=7,
        sequence=1,
        game_time=1.0,
        gv=1,
        gv_main=1,
        evolution=evolution[0],
        evolution_handle=evolution[1],
        units=units,
    )


def test_parses_game_time_rng_state_and_unit_positions() -> None:
    # Given
    text = record(3, f"1048600|{HERO}|-3776.000|4480.500|851983")

    # When
    parsed = parse_telemetry(text)

    # Then
    assert parsed is not None
    assert (parsed.game_time, parsed.gv, parsed.gv_main) == (12.5, 4242, 99)
    assert parsed.placements() == [(HERO, -3776.0, 4480.5, 851983)]


def test_incomplete_record_is_not_a_record() -> None:
    # Given: the bridge is still writing, so the end line is missing.
    text = record(3).rsplit("\n", 1)[0]

    # When / Then
    assert parse_telemetry(text) is None


def test_mismatched_end_is_rejected() -> None:
    # Given
    text = record(3).replace("TEND|7|3", "TEND|7|4")

    # When / Then
    with pytest.raises(ProtocolError):
        _ = parse_telemetry(text)


def test_only_the_record_for_the_acknowledged_step_is_used(tmp_path: Path) -> None:
    # Given: telemetry from an older export is still on disk.
    _ = (tmp_path / TELEMETRY_FILE).write_text(record(3), encoding="utf-8")

    # When / Then
    assert read_telemetry(tmp_path, 7, 3) is not None
    assert read_telemetry(tmp_path, 7, 4) is None


def test_new_handles_are_spawned_units_but_wisps_are_not() -> None:
    # Given: a wisp walked into the summon region and became a hero.
    before = telemetry(unit(1, RANDOM_WISP), unit(2, HERO))
    after = telemetry(unit(2, HERO), unit(3, HERO), unit(4, RANDOM_WISP))

    # When / Then
    assert spawned(before, after) == [HERO]


def test_a_reused_handle_with_another_type_is_spawned() -> None:
    # Given
    before = telemetry(unit(5, HERO))
    after = telemetry(unit(5, DRAWN))

    # When / Then
    assert spawned(before, after) == [DRAWN]


def test_evolution_draw_is_reported_once_per_new_evolution_unit() -> None:
    # Given
    before = telemetry(unit(1, EVOLUTION_WISP))
    after = telemetry(evolution=(DRAWN, 900))

    # When / Then
    assert pool_draws(before, after) == [DRAWN]
    assert pool_draws(after, after) == []


def test_log_row_carries_replay_fields_only_with_telemetry(state: GameState) -> None:
    # Given
    after = state.model_copy(update={"sequence": 1})
    current = telemetry(unit(1, HERO))

    # When
    with_fields = transition_log(1, 0, 0.0, after, (None, current))
    without = transition_log(1, 0, 0.0, after, (None, None))

    # Then
    assert with_fields.gv == 1
    assert with_fields.gv_main == 1
    assert with_fields.units == [(HERO, 0.0, 0.0, 0)]
    assert without.model_dump(exclude_none=True).keys() == {
        "step",
        "episode",
        "sequence",
        "action",
        "accepted",
        "reward",
        "round",
        "story",
        "enemies",
        "status",
    }


def test_log_row_carries_the_live_chest_count_the_bridge_exports(state: GameState) -> None:
    # Given: the bridge reports 7 live hidden chests.
    after = state.model_copy(update={"techs": (TechLevel(raw_id=CHEST_COUNT, level=7),)})

    # When
    row = transition_log(1, 0, 0.0, after, (None, None))

    # Then
    assert row.chests == 7


def test_log_row_carries_the_round_boss_when_the_bridge_exports_it(state: GameState) -> None:
    # Given: one live boss at 400/1000 health with 12.5 s left, and a state without boss rows.
    rows = (TechLevel(raw_id=BOSS_HEALTH, level=400), TechLevel(raw_id=ROUND_TIME_LEFT, level=125))
    after = state.model_copy(update={"bosses": 1, "techs": rows})

    # When
    row = transition_log(1, 0, 0.0, after, (None, None))
    old = transition_log(1, 0, 0.0, state.model_copy(update={"bosses": 1}), (None, None))

    # Then
    assert (row.bosses, row.boss_health, row.round_time_left) == (1, 400, 125)
    assert (old.bosses, old.boss_health, old.round_time_left) == (None, None, None)
