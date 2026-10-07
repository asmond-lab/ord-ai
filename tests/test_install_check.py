from pathlib import Path

import pytest

from ord_ai.build import bridge_sources, diagnostic_bridge_sources
from ord_ai.catalog import Action, Catalog
from ord_ai.cli import build, check_install_allowed, check_installed
from ord_ai.protocol import ProtocolError

CATALOG = Catalog(
    map_sha256="test",
    units=(),
    actions=tuple(Action(code=code, name=f"a{code}") for code in range(3)),
)


def install(folder: Path, catalog: Catalog, script: str) -> Path:
    _ = (folder / "catalog.json").write_text(catalog.model_dump_json(), "utf-8")
    _ = (folder / "war3map.j").write_text(script, "utf-8")
    return folder / "ORDR_AI_2.323R.w3x"


def test_current_install_passes(tmp_path: Path) -> None:
    _, actions, functions = bridge_sources()
    check_installed(install(tmp_path, CATALOG, actions + functions), CATALOG)


def test_a_diagnostic_record_install_passes(tmp_path: Path) -> None:
    _, actions, functions = diagnostic_bridge_sources()
    check_installed(install(tmp_path, CATALOG, actions + functions), CATALOG)


def test_older_catalog_is_refused(tmp_path: Path) -> None:
    _, actions, functions = bridge_sources()
    older = CATALOG.model_copy(update={"actions": CATALOG.actions[:2]})
    with pytest.raises(ProtocolError, match="older than build"):
        check_installed(install(tmp_path, older, actions + functions), CATALOG)


def test_older_bridge_script_is_refused(tmp_path: Path) -> None:
    _, actions, _ = bridge_sources()
    with pytest.raises(ProtocolError, match="older than build"):
        check_installed(install(tmp_path, CATALOG, actions), CATALOG)


def test_missing_sidecars_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError, match="older than build"):
        check_installed(tmp_path / "ORDR_AI_2.323R.w3x", CATALOG)


def test_install_into_the_game_folder_is_refused_while_the_game_runs(tmp_path: Path) -> None:
    maps = tmp_path / "Maps"
    target = maps / "Download" / "ORD_AI" / "ORDR_AI_2.323R.w3x"
    with pytest.raises(ProtocolError, match="Warcraft III"):
        check_install_allowed(target, maps, lambda: True)
    check_install_allowed(target, maps, lambda: False)
    check_install_allowed(tmp_path / "build" / "ORDR_AI_2.323R.w3x", maps, lambda: True)


def test_build_command_writes_nothing_while_the_game_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maps = tmp_path / "Maps"
    target = maps / "Download" / "ORD_AI" / "ORDR_AI_2.323R.w3x"
    monkeypatch.setattr("ord_ai.cli.WARCRAFT_MAPS", maps)
    monkeypatch.setattr("ord_ai.cli.game_running", lambda: True)
    with pytest.raises(ProtocolError, match="Warcraft III"):
        build(source=tmp_path / "unused.w3x", output=target)
    assert not maps.exists()
