"""Summarize collected runs (real game and simulator) and serve them on a local dashboard.

Everything under the runs directory is only read, so the dashboard can run beside a live
recording or training run.
"""

import math
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Final

from rich.console import Console

from ord_ai.web import sources
from ord_ai.web.models import ActionUse, DashboardSummary, Episode, Latest, RunSummary, Unit
from ord_ai.web.server import HOST, bind
from ord_ai.web.sources import NO_NAMES, SIM_EPISODES, TRANSITIONS, Names, Row, SimEpisode

DEFAULT_PORT: Final = 8765
TOP_ACTIONS: Final = 15
# A long simulator run has tens of thousands of episodes; the page shows the newest ones.
SIM_EPISODE_LIMIT: Final = 2000
TELEMETRY: Final = ("game_time", "gv", "gv_main", "units", "spawned", "pool_draws")


def _episodes(rows: list[Row]) -> tuple[Episode, ...]:
    groups: dict[int, list[Row]] = {}
    for row in rows:
        groups.setdefault(row.episode, []).append(row)
    return tuple(
        Episode(
            episode=episode,
            steps=len(group),
            max_round=max(row.round for row in group),
            total_reward=math.fsum(row.reward for row in group),
            status=group[-1].status,
            acceptance=sum(row.accepted for row in group) / len(group),
            game_time=next(
                (row.game_time for row in reversed(group) if row.game_time is not None), None
            ),
        )
        for episode, group in groups.items()
    )


def _sim_episodes(records: list[SimEpisode]) -> tuple[Episode, ...]:
    return tuple(
        Episode(
            episode=record.episode,
            steps=record.length,
            max_round=record.round,
            total_reward=record.reward,
            status=record.status,
            acceptance=record.accepted,
            game_time=None,
        )
        for record in records
    )


def _actions(rows: list[Row], names: Names) -> tuple[ActionUse, ...]:
    chosen = Counter(row.action for row in rows)
    accepted = Counter(row.action for row in rows if row.accepted)
    return tuple(
        ActionUse(
            code=code, name=names.action(code), count=count, acceptance=accepted[code] / count
        )
        for code, count in chosen.most_common(TOP_ACTIONS)
    )


def _latest(row: Row, names: Names) -> Latest:
    return Latest(
        step=row.step,
        episode=row.episode,
        sequence=row.sequence,
        action=names.action(row.action),
        accepted=row.accepted,
        reward=row.reward,
        round=row.round,
        story=row.story,
        enemies=row.enemies,
        status=row.status,
        game_time=row.game_time,
        units=None
        if row.units is None
        else tuple(
            Unit(raw_id=raw_id, name=names.unit(raw_id), x=x, y=y) for raw_id, x, y, _ in row.units
        ),
    )


def _run(directory: Path, fallback_catalog: Path) -> RunSummary:
    catalog = directory / "catalog.json"
    names = sources.names(catalog) or sources.names(fallback_catalog) or NO_NAMES
    version = sources.stamp(directory / TRANSITIONS)
    rows, skipped = sources.rows(directory / TRANSITIONS)
    fields = Counter(name for row in rows for name in TELEMETRY if name in row.model_fields_set)
    telemetry = sum(1 for row in rows if not row.model_fields_set.isdisjoint(TELEMETRY))
    logs = sources.log_files(directory)
    return RunSummary(
        name=directory.name,
        transitions=len(rows),
        skipped_lines=skipped,
        updated_at=None if version is None else version.modified,
        telemetry_rows=telemetry,
        telemetry_fraction=telemetry / len(rows) if rows else 0.0,
        telemetry_fields={name: fields[name] for name in TELEMETRY},
        catalog=names.source,
        checkpoint=sources.checkpoint(directory),
        log_files=tuple(path.as_posix() for path in logs),
        losses=tuple(point for path in logs for point in sources.losses(path)),
        episodes=_episodes(rows)
        if rows
        else _sim_episodes(sources.sim_episodes(directory / SIM_EPISODES, SIM_EPISODE_LIMIT)),
        actions=_actions(rows, names),
        latest=_latest(rows[-1], names) if rows else None,
    )


def summarize(runs_dir: Path) -> DashboardSummary:
    """Summarize every run directory under ``runs_dir`` without writing anything.

    Action and unit names come from ``<run>/catalog.json``, else ``build/catalog.json`` beside
    ``runs_dir``; codes without a name are shown as their rawcode. A missing ``runs_dir`` has
    no runs yet.
    """
    fallback_catalog = runs_dir.parent / "build" / "catalog.json"
    found = runs_dir.iterdir() if runs_dir.is_dir() else ()
    return DashboardSummary(
        runs_dir=runs_dir.as_posix(),
        runs=tuple(_run(path, fallback_catalog) for path in sorted(found) if path.is_dir()),
    )


def make_server(runs_dir: Path, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Bind the dashboard for ``runs_dir`` to 127.0.0.1 only: it has no authentication."""
    return bind(port, lambda: summarize(runs_dir).model_dump_json().encode())


def serve(runs_dir: Path, port: int = DEFAULT_PORT) -> None:
    """Serve the dashboard at ``http://127.0.0.1:<port>/`` until Ctrl+C."""
    console = Console()
    with make_server(runs_dir, port) as server:
        console.print(
            f"ORD AI dashboard: http://{HOST}:{server.server_port}/ (runs: {runs_dir.resolve()})",
            markup=False,
            soft_wrap=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            console.print("Dashboard stopped", markup=False)


if __name__ == "__main__":
    serve(Path("runs"))
