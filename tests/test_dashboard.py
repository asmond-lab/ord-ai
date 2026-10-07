import http.client
import json
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from ord_ai.catalog import Action, Catalog, UnitType
from ord_ai.dashboard import make_server, summarize
from ord_ai.web.models import Checkpoint, DashboardSummary, RunSummary, Unit
from ord_ai.web.server import HOST, WEB_ROOT

HERO = int.from_bytes(b"h001", "big")
DRAWN = int.from_bytes(b"h00J", "big")
FUSION = int.from_bytes(b"A0GW", "big")
SAVED = datetime(2026, 9, 27, 10, 14, tzinfo=UTC)
TELEMETRY: dict[str, JsonValue] = {
    "game_time": 9.125,
    "gv": 343934971,
    "units": [[HERO, -5888.0, 5664.0, 0]],
    "spawned": [],
    "pool_draws": [],
}


def line(step: int, **fields: JsonValue) -> str:
    row: dict[str, JsonValue] = {
        "step": step,
        "episode": 7,
        "sequence": step,
        "action": 0,
        "accepted": True,
        "reward": 0.0,
        "round": 1,
        "story": 1,
        "enemies": 0,
        "status": "PLAYING",
    }
    return json.dumps(row | fields)


def write_run(runs: Path, name: str, *lines: str) -> Path:
    run = runs / name
    run.mkdir(parents=True)
    text = "".join(f"{row}\n" for row in lines)
    _ = (run / "transitions.jsonl").write_text(text, encoding="utf-8")
    return run


def write_catalog(path: Path, actions: dict[int, str], units: dict[int, str]) -> None:
    catalog = Catalog(
        map_sha256="0" * 64,
        units=tuple(UnitType(raw_id=raw_id, name=name) for raw_id, name in units.items()),
        actions=tuple(Action(code=code, name=name) for code, name in actions.items()),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(catalog.model_dump_json(), encoding="utf-8")


def only_run(summary: DashboardSummary) -> RunSummary:
    [run] = summary.runs
    return run


def test_an_unfinished_last_line_is_skipped_and_counted(tmp_path: Path) -> None:
    # Given: the recorder has written half of its third row.
    run = write_run(tmp_path / "runs", "live", line(1), line(2))
    with (run / "transitions.jsonl").open("a", encoding="utf-8") as log:
        _ = log.write(line(3)[:30])

    # When
    live = only_run(summarize(tmp_path / "runs"))

    # Then
    assert (live.transitions, live.skipped_lines) == (2, 1)
    assert live.latest is not None
    assert live.latest.step == 2


def test_a_run_with_only_transitions_has_no_checkpoint_log_or_names(tmp_path: Path) -> None:
    # Given: an old row without telemetry and no catalog, metadata, weights or log anywhere.
    _ = write_run(tmp_path / "runs", "bare", line(1, action=FUSION))

    # When
    bare = only_run(summarize(tmp_path / "runs"))

    # Then
    assert (bare.checkpoint, bare.catalog, bare.log_files, bare.losses) == (None, None, (), ())
    assert [action.name for action in bare.actions] == ["A0GW"]
    assert bare.latest is not None
    assert bare.latest.units is None


def test_a_directory_without_transitions_is_listed_with_no_rows(tmp_path: Path) -> None:
    # Given: a checkpoint-only run such as runs/smoke.
    (tmp_path / "runs" / "smoke").mkdir(parents=True)

    # When
    smoke = only_run(summarize(tmp_path / "runs"))

    # Then
    assert (smoke.name, smoke.transitions, smoke.episodes, smoke.latest) == ("smoke", 0, (), None)
    assert smoke.telemetry_fraction == 0.0


def test_a_missing_runs_directory_has_no_runs(tmp_path: Path) -> None:
    # Given / When
    summary = summarize(tmp_path / "runs")

    # Then
    assert summary.runs == ()


def test_rows_with_and_without_telemetry_are_counted(tmp_path: Path) -> None:
    # Given: one row from before replay fields existed, then two telemetry rows.
    _ = write_run(
        tmp_path / "runs",
        "mixed",
        line(1),
        line(2, gv_main=0, **TELEMETRY),
        line(3, **TELEMETRY),
    )

    # When
    mixed = only_run(summarize(tmp_path / "runs"))

    # Then
    assert (mixed.telemetry_rows, mixed.telemetry_fraction) == (2, 2 / 3)
    assert mixed.telemetry_fields == {
        "game_time": 2,
        "gv": 2,
        "gv_main": 1,
        "units": 2,
        "spawned": 2,
        "pool_draws": 2,
    }


def test_log_updates_are_parsed_and_the_update_closing_an_episode_is_terminal(
    tmp_path: Path,
) -> None:
    # Given: a training log beside the run whose second update Rich wrapped onto two lines.
    runs = tmp_path / "runs"
    _ = write_run(runs, "ordr", line(1))
    log = runs / "ordr-train.log"
    lines = (
        "Real Warcraft training: device=cuda, actions=244",
        "reset episode=4",
        "step=32 round=1 reward=0.000 policy_loss=0.0393 value_loss=0.1094",
        "step=122 round=4 reward=-50.098 policy_loss=-53.2672",
        "value_loss=1974.9868",
        "episode=4 ended LOSS",
        "reset episode=5",
    )
    _ = log.write_text("\n".join(lines), encoding="utf-8")

    # When
    ordr = only_run(summarize(runs))

    # Then
    assert ordr.log_files == (log.as_posix(),)
    assert [(p.step, p.round, p.policy_loss, p.value_loss, p.terminal) for p in ordr.losses] == [
        (32, 1, 0.0393, 0.1094, False),
        (122, 4, -53.2672, 1974.9868, True),
    ]


def test_a_teacher_log_inside_the_run_is_listed_without_losses(tmp_path: Path) -> None:
    # Given: the rule teacher logs resets and endings but never updates a policy.
    run = write_run(tmp_path / "runs", "c2-teacher", line(1))
    _ = (run / "record.log").write_text(
        "reset episode=16\r\nepisode=16 ended LOSS round=30\r\n", encoding="utf-8"
    )

    # When
    teacher = only_run(summarize(tmp_path / "runs"))

    # Then
    assert teacher.log_files == ((run / "record.log").as_posix(),)
    assert teacher.losses == ()


def test_a_utf16_log_from_windows_powershell_is_parsed(tmp_path: Path) -> None:
    # Given: Windows PowerShell 5 redirection writes UTF-16 with a byte order mark.
    run = write_run(tmp_path / "runs", "ordr", line(1))
    _ = (run / "train.log").write_text(
        "step=32 round=1 reward=0.000 policy_loss=0.0393 value_loss=0.1094\n", encoding="utf-16"
    )

    # When
    ordr = only_run(summarize(tmp_path / "runs"))

    # Then
    assert [point.step for point in ordr.losses] == [32]


def test_each_episode_reports_max_round_return_status_acceptance_and_end_time(
    tmp_path: Path,
) -> None:
    # Given: episode 4 is lost in round 3 after a rejected action; its last row has no telemetry.
    _ = write_run(
        tmp_path / "runs",
        "ordr",
        line(1, episode=4, round=1, reward=0.5, game_time=10.0),
        line(2, episode=4, round=3, reward=-0.1, accepted=False, game_time=12.0),
        line(3, episode=4, round=3, reward=-50.0, status="LOSS"),
        line(4, episode=5, round=1),
    )

    # When
    ordr = only_run(summarize(tmp_path / "runs"))

    # Then
    lost, playing = ordr.episodes
    assert (lost.episode, lost.steps, lost.max_round, lost.status) == (4, 3, 3, "LOSS")
    assert lost.total_reward == pytest.approx(-49.6)
    assert (lost.acceptance, lost.game_time) == (2 / 3, 12.0)
    assert (playing.episode, playing.status, playing.acceptance, playing.game_time) == (
        5,
        "PLAYING",
        1.0,
        None,
    )


def test_actions_rank_by_use_with_names_from_the_build_catalog(tmp_path: Path) -> None:
    # Given: the run has no catalog.json, so build/catalog.json beside runs/ names the actions.
    build_catalog = tmp_path / "build" / "catalog.json"
    write_catalog(build_catalog, {0: "wait", 5: "send_one_unit_to_story"}, {})
    _ = write_run(
        tmp_path / "runs",
        "ordr",
        line(1, action=5),
        line(2, action=0),
        line(3, action=5, accepted=False),
        line(4, action=0),
        line(5, action=0),
    )

    # When
    ordr = only_run(summarize(tmp_path / "runs"))

    # Then
    assert ordr.catalog == build_catalog.as_posix()
    assert [(use.name, use.count, use.acceptance) for use in ordr.actions] == [
        ("wait", 3, 1.0),
        ("send_one_unit_to_story", 2, 0.5),
    ]


def test_the_run_catalog_takes_precedence_over_the_build_catalog(tmp_path: Path) -> None:
    # Given: both catalogs name action 0, differently.
    write_catalog(tmp_path / "build" / "catalog.json", {0: "build name"}, {})
    run = write_run(tmp_path / "runs", "ordr", line(1))
    write_catalog(run / "catalog.json", {0: "run name"}, {})

    # When
    ordr = only_run(summarize(tmp_path / "runs"))

    # Then
    assert [use.name for use in ordr.actions] == ["run name"]


def test_the_latest_row_names_its_units_and_keeps_their_positions(tmp_path: Path) -> None:
    # Given: only the hero is in the catalog; the drawn unit keeps its rawcode.
    run = write_run(
        tmp_path / "runs",
        "c2-teacher",
        line(1, **TELEMETRY),
        line(
            2,
            game_time=12.5,
            enemies=33,
            units=[[HERO, -3776.0, 4480.5, 851983], [DRAWN, 0.0, 0.0, 0]],
        ),
    )
    write_catalog(run / "catalog.json", {}, {HERO: "기록지침"})

    # When
    teacher = only_run(summarize(tmp_path / "runs"))

    # Then
    latest = teacher.latest
    assert latest is not None
    assert (latest.step, latest.enemies, latest.game_time) == (2, 33, 12.5)
    assert latest.units == (
        Unit(raw_id=HERO, name="기록지침", x=-3776.0, y=4480.5),
        Unit(raw_id=DRAWN, name="h00J", x=0.0, y=0.0),
    )


def test_checkpoint_steps_come_from_metadata_and_save_time_from_the_weights(
    tmp_path: Path,
) -> None:
    # Given
    run = write_run(tmp_path / "runs", "ordr", line(1))
    metadata = {"schema_version": 1, "algorithm": "masked-a2c", "steps": 1096, "input_size": 477}
    _ = (run / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    weights = run / "policy.safetensors"
    _ = weights.write_bytes(b"weights")
    os.utime(weights, (SAVED.timestamp(), SAVED.timestamp()))

    # When
    ordr = only_run(summarize(tmp_path / "runs"))

    # Then
    assert ordr.checkpoint == Checkpoint(steps=1096, algorithm="masked-a2c", saved_at=SAVED)


@pytest.fixture(scope="module")
def port(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    runs = tmp_path_factory.mktemp("served") / "runs"
    _ = write_run(runs, "ordr", line(1))
    server = make_server(runs, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def fetch(port: int, method: str, path: str, host: str | None = None) -> tuple[int, str, bytes]:
    connection = http.client.HTTPConnection(HOST, port, timeout=5)
    try:
        connection.request(method, path, headers={} if host is None else {"Host": host})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type") or "", response.read()
    finally:
        connection.close()


def test_the_summary_route_returns_every_run_as_json(port: int) -> None:
    # Given / When
    status, content_type, body = fetch(port, "GET", "/api/summary")

    # Then
    assert (status, content_type) == (200, "application/json")
    assert [run.name for run in DashboardSummary.model_validate_json(body).runs] == ["ordr"]


def test_the_root_serves_the_page(port: int) -> None:
    # Given / When
    status, content_type, body = fetch(port, "GET", "/")

    # Then
    assert (status, content_type) == (200, "text/html; charset=utf-8")
    assert body == (WEB_ROOT / "index.html").read_bytes()


def test_page_assets_are_served_with_their_type(port: int) -> None:
    # Given / When
    status, content_type, body = fetch(port, "GET", "/app.js?v=1")

    # Then
    assert (status, content_type) == (200, "text/javascript; charset=utf-8")
    assert body == (WEB_ROOT / "app.js").read_bytes()


@pytest.mark.parametrize(
    "path", ["/missing.js", "/DESIGN.md", "/server.py", "/../pyproject.toml", "/api/summary/x"]
)
def test_unknown_paths_and_package_files_are_not_found(port: int, path: str) -> None:
    # Given / When
    status, _, _ = fetch(port, "GET", path)

    # Then
    assert status == 404


@pytest.mark.parametrize("method", ["POST", "HEAD", "DELETE"])
def test_only_get_is_supported(port: int, method: str) -> None:
    # Given / When
    status, _, _ = fetch(port, method, "/api/summary")

    # Then
    assert status == 501


def test_a_foreign_host_header_is_refused(port: int) -> None:
    # Given: a DNS-rebinding page reaches the loopback server under its own host name.
    # When
    status, _, _ = fetch(port, "GET", "/api/summary", host="attacker.example")

    # Then
    assert status == 403


def test_a_simulator_run_shows_its_newest_episodes(tmp_path: Path) -> None:
    # Given: simulator training writes episodes.jsonl, with a half-written last line.
    run = tmp_path / "runs" / "sim-rl"
    run.mkdir(parents=True)
    records = [
        {
            "phase": "rl",
            "steps": 900 * index,
            "episode": index,
            "env": 0,
            "difficulty": 1,
            "isekai": False,
            "length": 900,
            "round": 30 + index,
            "story": 3,
            "status": "WIN" if index == 2 else "LOSS",
            "enemies": 0,
            "zero_count_clear": index == 2,
            "reward": 1.5,
            "accepted": 0.9,
            "wall": 1.0,
        }
        for index in (1, 2)
    ]
    text = "".join(json.dumps(record) + "\n" for record in records) + '{"phase": "rl", "st'
    _ = (run / "episodes.jsonl").write_text(text, encoding="utf-8")

    # When
    sim = only_run(summarize(tmp_path / "runs"))

    # Then
    assert [(episode.episode, episode.max_round, episode.status) for episode in sim.episodes] == [
        (1, 31, "LOSS"),
        (2, 32, "WIN"),
    ]
    assert sim.transitions == 0
