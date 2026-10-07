from pathlib import Path

import pytest

from ord_ai.build import (
    diagnostic_bridge_sources,
    events_bridge_sources,
    inject_bridge,
    read_map,
    record_landings,
    record_timers,
)
from ord_ai.events import CombatEvents, parse_events, read_events
from ord_ai.protocol import ProtocolError

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"


def events_text(*records: str, episode: int = 7, sequence: int = 3, counts: str = "") -> str:
    rows = [f"ORD_AI|1|EVT|{episode}|{sequence}|120.5|4242"]
    rows += [f"ORD_AI|1|{record}" for record in records]
    strikes = sum(
        record.startswith(("ATK", "DIE", "SPL", "W5I", "W5S", "TMG", "TMO")) for record in records
    )
    hits = sum(record.startswith("DMG") for record in records)
    rows.append(f"ORD_AI|1|EEND|{episode}|{sequence}|{counts or f'{strikes}|{hits}'}|2")
    return "\n".join(f'\tcall Preload( "{row}" )' for row in rows)


ATTACK = "ATK|120.25|4241|1048601|1211122484|0|1048700|1848651824|11"
DEATH = "DIE|120.4|4242|1048700|1848651824|11|1048601|1211122484|0"
DAMAGE = "DMG|120.3|1048700|1848651824|11|350.5|3|0.|1200."


def test_parses_attacks_deaths_and_summed_damage_with_time_and_gv() -> None:
    # When
    record = parse_events(events_text(ATTACK, DEATH, DAMAGE))

    # Then
    assert record is not None
    assert (record.episode, record.sequence, record.game_time, record.gv) == (7, 3, 120.5, 4242)
    assert record.dropped == 2
    (attack,) = record.attacks
    assert (attack.time, attack.gv, attack.actor.owner, attack.target.owner) == (
        120.25,
        4241,
        0,
        11,
    )
    # A death reads killer -> dying unit, like an attack.
    (death,) = record.deaths
    assert (death.actor.handle, death.target.handle) == (1048601, 1048700)
    (damage,) = record.damage
    assert (damage.amount, damage.hits, damage.life, damage.max_life) == (350.5, 3, 0.0, 1200.0)


def test_exact_health_rows_are_kept_outside_the_event_counts() -> None:
    # When: the 해왕류 and the 황금 종 as R2S prints them.
    record = parse_events(
        events_text(ATTACK, "HP|jB|35999998.000|36000000.000", "HP|Hd|6499999.500|6500000.000")
    )

    # Then
    assert record is not None
    assert [(item.name, item.life, item.max_life) for item in record.health] == [
        ("jB", 35999998.0, 36000000.0),
        ("Hd", 6499999.5, 6500000.0),
    ]
    assert len(record.attacks) == 1


def test_simulator_comparison_rows_are_parsed_outside_the_event_counts() -> None:
    # When: round/story, a lane enemy, a story boss with stack levels, an own unit's mobility.
    record = parse_events(
        events_text(
            ATTACK,
            "CTX|35|6",
            "LANE|1048800|1848651824|-512.250|1024.000|880.500|1200.000|851983",
            "SB|1048900|1848651825|6|4500000.000|9000000.000|100.000|-50.000|41|0|3|0|2",
            "MOV|1048601|1211122484|270.000|1|0|0|0",
            "MOV|1048602|1211122485|0.000|0|1|1|1",
        )
    )

    # Then
    assert record is not None
    assert record.context is not None
    assert (record.context.round, record.context.story) == (35, 6)
    (lane,) = record.lane
    assert (lane.handle, lane.x, lane.y, lane.life, lane.order) == (
        1048800,
        -512.25,
        1024.0,
        880.5,
        851983,
    )
    (boss,) = record.story_bosses
    assert (boss.unit.owner, boss.life, boss.max_life) == (6, 4500000.0, 9000000.0)
    assert boss.levels == {"A04M": 41, "A04N": 0, "A04O": 3, "A04P": 0, "A04R": 2}
    assert [
        (item.handle, item.speed, item.amov, item.paused, item.hidden, item.locust)
        for item in record.mobility
    ] == [(1048601, 270.0, 1, False, False, 0), (1048602, 0.0, 0, True, True, 1)]
    assert len(record.attacks) == 1


def test_ability_effects_are_parsed_and_counted_with_the_strikes() -> None:
    # When: one attack and one ability effect (A0BA, cast by an h01I of player 0).
    record = parse_events(events_text(ATTACK, "SPL|846.25|4250|1048650|1747988553|0|1093681729"))

    # Then
    assert record is not None
    (spell,) = record.spells
    assert (spell.time, spell.gv, spell.caster.handle, spell.caster.owner, spell.ability) == (
        846.25,
        4250,
        1048650,
        0,
        1093681729,
    )
    assert len(record.attacks) == 1


def test_a_recorded_line_from_before_the_comparison_rows_still_loads() -> None:
    # Given: an events.jsonl line written before CTX/LANE/SB/MOV existed.
    old = (
        '{"episode": 7, "sequence": 3, "game_time": 120.5, "gv": 4242, "attacks": [],'
        ' "deaths": [], "damage": [], "health": [], "dropped": 0}'
    )

    # When
    record = CombatEvents.model_validate_json(old)

    # Then
    assert (record.context, record.lane, record.story_bosses, record.mobility) == (
        None,
        (),
        (),
        (),
    )
    assert record.spells == ()


def test_incomplete_or_inconsistent_events_are_not_accepted(tmp_path: Path) -> None:
    # Given
    complete = events_text(ATTACK)

    # When / Then: still being written, counts that do not match, another export's file,
    # a comparison row with a missing field.
    assert parse_events(complete.rsplit("\n", 1)[0]) is None
    with pytest.raises(ProtocolError):
        _ = parse_events(events_text(ATTACK, counts="2|0"))
    with pytest.raises(ProtocolError):
        _ = parse_events(events_text("SB|1048900|1848651825|6|1.|2.|0.|0.|41|0|3|0"))
    _ = (tmp_path / "events.txt").write_text(complete, encoding="utf-8")
    assert read_events(tmp_path, 7, 4) is None
    assert read_events(tmp_path, 7, 3) is not None


def test_the_record_bridge_writes_events_at_every_export_and_keeps_the_normal_bridge() -> None:
    # Given
    script = read_map(_MAP_PATH.read_bytes()).script

    # When
    record = inject_bridge(script, sources=events_bridge_sources())
    normal = inject_bridge(script)

    # Then: events are exported just before telemetry and registered at init.
    assert "call ORDAI_EventsExport()\n    call ORDAI_Telemetry()" in record
    assert "call ORDAI_InitRecipes()\n    call ORDAI_EventsInit()" in record
    assert 'call Preload(head + "HP|jB|" + R2S(GetWidgetLife(jB))' in record
    assert "R2S(GetDestructableMaxLife(Hd))" in record
    # The comparison rows run in their own thread (operation limit) inside the events export.
    assert 'call ExecuteFunc("ORDAI_EventsDiag")' in record
    for kind in ("CTX|", "LANE|", "SB|", "MOV|"):
        assert f'call Preload(head + "{kind}"' in record
        assert f'"{kind}"' not in normal
    assert "ORDAI_Events" not in normal
    assert "HP|jB|" not in normal


LANDING = "W5I|846.25|4250|12|1048650|1747988553|0|-4480.|2208.|16.|0"


def landing_stages(call: int, *stages: str) -> list[str]:
    return [f"W5S|{call}|{stage}|-4480.|2240.|851972" for stage in stages]


def test_a_w5r_call_keeps_its_stages_in_order_and_a_capped_call_is_incomplete() -> None:
    # Given: call 12 recorded fully; call 13 cut by the per-export cap after "x1".
    full = landing_stages(12, "in", "x1", "y", "x2", "stop")
    cut = [LANDING.replace("|12|", "|13|"), *landing_stages(13, "in", "x1")]

    # When
    record = parse_events(events_text(LANDING, *full, *cut))

    # Then
    assert record is not None
    first, second = record.landings
    assert (first.call, first.unit.handle, first.target_x, first.target_y) == (
        12,
        1048650,
        -4480.0,
        2208.0,
    )
    assert (first.collision, first.flying, first.complete) == (16.0, False, True)
    assert [stage.stage for stage in first.stages] == ["in", "x1", "y", "x2", "stop"]
    assert (first.stages[-1].y, first.stages[-1].order) == (2240.0, 851972)
    assert (second.call, second.complete, len(second.stages)) == (13, False, 2)


def test_a_w5r_call_nested_inside_another_keeps_both_calls_stages_apart() -> None:
    # Given: a region event fires by call 12's SetUnitX calls W5R again (call 13) before
    # call 12's "y" stage; each stage row carries its own call's id.
    outer = landing_stages(12, "in", "x1", "y", "x2", "stop")
    inner = [LANDING.replace("|12|", "|13|"), *landing_stages(13, "in", "x1", "y", "x2", "stop")]

    # When
    record = parse_events(events_text(LANDING, *outer[:2], *inner, *outer[2:]))

    # Then
    assert record is not None
    assert [(call.call, call.complete) for call in record.landings] == [(12, True), (13, True)]


def test_a_w5r_stage_without_its_call_or_a_lost_row_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        _ = parse_events(events_text(*landing_stages(12, "in")))
    # EEND counts every W5I and W5S row with the strikes: one row missing is caught.
    with pytest.raises(ProtocolError):
        _ = parse_events(events_text(LANDING, *landing_stages(12, "in"), counts="3|0"))


def test_the_diagnostic_build_records_w5r_steps_without_moving_its_calls() -> None:
    # Given
    script = read_map(_MAP_PATH.read_bytes()).script

    # When
    diagnostic = record_landings(inject_bridge(script, sources=diagnostic_bridge_sources()))
    record = inject_bridge(script, sources=events_bridge_sources())

    # Then: the four original calls keep their order and arguments, a row after each.
    start = diagnostic.index("function W5R takes unit P5R,real p5R,real r5R returns nothing")
    body = diagnostic[start : diagnostic.index("endfunction", start)].splitlines()[1:]
    assert [line for line in body if "ORDAI_" not in line] == [
        "call SetUnitX(P5R,-10400.)",
        "call SetUnitY(P5R,r5R)",
        "call SetUnitX(P5R,p5R)",
        'call IssueImmediateOrder(P5R,"stop")',
    ]
    # The call id is taken once into a local and passed to every stage (re-entry safe).
    assert body[0] == "local integer ORDAI_w5r=ORDAI_LandingBegin(P5R,p5R,r5R)"
    stages = [line for line in body if line.startswith("call ORDAI_LandingStage(")]
    assert stages == [
        f'call ORDAI_LandingStage(P5R,ORDAI_w5r,"{stage}")' for stage in ("x1", "y", "x2", "stop")
    ]
    # The helpers are declared before W5R (the game compiles a call only after its callee).
    assert diagnostic.index("function ORDAI_LandingBegin") < start
    assert "call ORDAI_EventsInit()\n    call ORDAI_DiagnosticInit()" in diagnostic
    assert "EVENT_PLAYER_UNIT_SPELL_EFFECT" in diagnostic
    assert "ORDAI_Landing" not in record
    assert "SPL|" not in record
    # Control action -2 exists only in the diagnostic build.
    assert "(value == -1 or value == -2)" in diagnostic
    assert "call EndGame(false)" in diagnostic
    assert "EndGame" not in record
    # The -2 branch answers only in control.txt: no state export overwrites the last state.
    branch = diagnostic[diagnostic.index("if ORDAI_action == -2 then") :]
    branch = branch[: branch.index("    endif\n")]
    assert "ORDAI_Export" not in branch
    assert "ORDAI_Observed" not in branch
    assert 'ORDAI_Write("ORD_AI\\\\control.txt"' in branch


def test_final_round_timer_entries_are_parsed_in_order_and_counted() -> None:
    # When: GNv at round 65 after the ending flag, then the delayed Obv (timer 1049000).
    record = parse_events(
        events_text("TMG|1981.5|4300|65|66|1|0|1", "TMO|1981.5|4300|65|66|1|0|1049000")
    )

    # Then
    assert record is not None
    gnv, obv = record.timers
    # Av 65, Ov 66, Xv 1, Iv 0 (the ending has not run) and Ss set.
    assert (gnv.kind, gnv.round, gnv.next_round, gnv.phase, gnv.ended, gnv.last) == (
        "TMG",
        65,
        66,
        1,
        0,
        1,
    )
    assert (obv.kind, obv.time, obv.last) == ("TMO", 1981.5, 1049000)


def test_the_diagnostic_build_records_gnv_and_obv_entries_without_changing_their_bodies() -> None:
    # Given
    script = inject_bridge(
        read_map(_MAP_PATH.read_bytes()).script, sources=diagnostic_bridge_sources()
    )

    # When
    timed = record_timers(record_landings(script))

    # Then: one read-only call first; the original lines follow unchanged.
    for name, call in (("GNv", "ORDAI_TimerTick"), ("Obv", "ORDAI_TimerOnce")):
        header = f"function {name} takes nothing returns nothing\n"
        start = timed.index(header) + len(header)
        body = timed[start : timed.index("endfunction", start)]
        assert body.startswith(f"call {call}()\n")
        original = script[script.index(header) + len(header) :]
        assert body.removeprefix(f"call {call}()\n") == original[: original.index("endfunction")]
    assert timed.index("function ORDAI_TimerTick") < timed.index("function GNv takes")
    assert timed.index("function ORDAI_LandingAdd") < timed.index("function ORDAI_TimerTick")
