import json
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Final, Self, cast

import pytest
import torch

from ord_ai import launcher
from ord_ai.build import (
    EASY_STAGE,
    JASS_DIRECTORY,
    MapData,
    build_map,
    human_bridge_sources,
    inject_bridge,
    read_map,
)
from ord_ai.catalog import Action, ActionKind, Catalog
from ord_ai.channel import StateChannel
from ord_ai.human import HUMAN_FILE, HumanInput, parse_human, read_human, render_human_table
from ord_ai.humanplay import (
    GameWatch,
    HumanPlayError,
    HumanSettings,
    attended_channel,
    human_demonstrations,
    record_human,
)
from ord_ai.launcher import ARGUMENTS_KEY, GAME, LAUNCH_PREFIX, LAUNCHER
from ord_ai.protocol import GameState, ProtocolError, Status, parse_snapshot
from ord_ai.simtrain import Demonstrations

Settings = dict[str, dict[str, dict[str, str]]]

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"


def human_text(episode: int, sequence: int, *inputs: str, dropped: int = 0) -> str:
    rows = [f"ORD_AI|1|HUMAN|{episode}|{sequence}"]
    rows += [f"ORD_AI|1|IN|{item}" for item in inputs]
    rows.append(f"ORD_AI|1|HEND|{episode}|{sequence}|{len(inputs)}|{dropped}")
    return "\n".join(f'\tcall Preload( "{row}" )' for row in rows)


def state_text(
    episode: int, sequence: int, status: Status = Status.PLAYING, gold: int = 0, round_: int = 1
) -> str:
    state = f"ORD_AI|1|STATE|{episode}|{sequence}|{round_}|{gold}|0|0|5|100|20|1|0|{int(status)}|1"
    return f'call Preload("{state}")\ncall Preload("ORD_AI|1|END|{episode}|{sequence}")\n'


def test_parses_mapped_and_unmapped_inputs_with_raw_identifiers() -> None:
    # Given: a gamble, an uncatalogued spell and a chat line that contains the separator.
    text = human_text(4, 9, "8|sell|h06F h062", "|spell|A0XX h001", "|chat|-hi|there")

    # When
    record = parse_human(text)

    # Then
    assert record is not None
    assert (record.episode, record.sequence, record.dropped) == (4, 9, 0)
    assert record.inputs == (
        HumanInput(code=8, kind="sell", raw="h06F h062"),
        HumanInput(code=None, kind="spell", raw="A0XX h001"),
        HumanInput(code=None, kind="chat", raw="-hi|there"),
    )


def test_incomplete_or_inconsistent_inputs_are_not_accepted() -> None:
    # Given
    complete = human_text(4, 9, "8|sell|h06F h062")

    # When / Then: still being written, count mismatch, end of another export.
    assert parse_human(complete.rsplit("\n", 1)[0]) is None
    with pytest.raises(ProtocolError):
        _ = parse_human(complete.replace("HEND|4|9|1|0", "HEND|4|9|2|0"))
    with pytest.raises(ProtocolError):
        _ = parse_human(complete.replace("HEND|4|9", "HEND|4|8"))


def test_only_the_inputs_of_the_acknowledged_export_are_read(tmp_path: Path) -> None:
    # Given
    _ = (tmp_path / HUMAN_FILE).write_text(human_text(4, 9), encoding="utf-8")

    # When / Then
    assert read_human(tmp_path, 4, 10) is None
    assert read_human(tmp_path, 4, 9) is not None
    assert read_human(tmp_path / "missing", 4, 9) is None


class Processes:
    """Battle.net and Warcraft III processes, changed the way they react to the launcher."""

    def __init__(self) -> None:
        self.launcher: int = 1
        self.games: set[int] = set()

    def count(self, image: str) -> int:
        return self.launcher if image == LAUNCHER else len(self.games)

    def pids(self, image: str) -> list[int]:
        return sorted(self.games) if image == GAME else []

    def close(self, pids: set[int]) -> list[dict[str, object]]:
        asked = sorted(pids & self.games)
        self.games -= pids
        return [{"hwnd": pid, "pid": pid, "posted": True, "error": 0} for pid in asked]

    def windows(self, pids: set[int]) -> list[dict[str, object]]:
        return [{"hwnd": pid, "pid": pid, "title": "Warcraft III"} for pid in sorted(pids)]

    def kill_pids(self, pids: set[int]) -> None:
        self.games -= pids

    def run_launcher(self, argument: str | None) -> None:
        if argument == "--exec=shutdown":
            self.launcher = 0
        elif argument == '"--exec=launch W3"':
            self.games.add(1000 + len(self.games) + 1)
        else:
            self.launcher = 1


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> Processes:
    fake = Processes()
    monkeypatch.setattr(launcher, "_count", fake.count)
    monkeypatch.setattr(launcher, "_pids", fake.pids)
    monkeypatch.setattr(launcher, "_request_close", fake.close)
    monkeypatch.setattr(launcher, "_windows", fake.windows)
    monkeypatch.setattr(launcher, "_kill_pids", fake.kill_pids)
    monkeypatch.setattr(launcher, "_run_launcher", fake.run_launcher)
    return fake


def test_the_real_channel_opens_the_map_through_battle_net_and_sends_no_key(
    tmp_path: Path, processes: Processes, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    config = tmp_path / "Battle.net.config"
    _ = config.write_text(json.dumps({"Games": {"w3": {}}}), encoding="utf-8")
    monkeypatch.setattr(launcher, "battle_net_config", lambda: config)

    keys: list[None] = []
    monkeypatch.setattr(launcher, "press_loading_screen_key", lambda: keys.append(None))
    settings = HumanSettings(
        training_map=tmp_path / "human.w3x",
        bridge_directory=tmp_path / "bridge",
        output=tmp_path / "out",
    )

    # When
    with attended_channel(settings, GameWatch(tmp_path / "War3Log.txt", lambda: True)) as channel:
        game = channel.game
        assert game is not None
        game.relaunch()
        game.press_any_key()
        during = cast("Settings", json.loads(config.read_text("utf-8")))["Games"]["w3"]

    # Then: the map was opened with -loadfile, and Battle.net's arguments are restored.
    assert (processes.launcher, len(processes.games)) == (1, 1)
    assert keys == []
    assert during[ARGUMENTS_KEY] == f'{LAUNCH_PREFIX}"{settings.training_map.resolve()}"'
    assert json.loads(config.read_text("utf-8")) == {"Games": {"w3": {}}}


Export = tuple[str, str]


class Launches:
    """How often the recorder asked the player to open the map."""

    def __init__(self) -> None:
        self.count: int = 0

    def relaunch(self) -> None:
        self.count += 1


class FakeBridge:
    """Answers like the human-play bridge: each call writes the next scripted export."""

    def __init__(
        self,
        game: Launches,
        directory: Path,
        exports: list[Export],
        log: Path | None = None,
        startup: str = "",
    ) -> None:
        self.game: Launches = game
        self.directory: Path = directory
        self.exports: list[Export] = exports
        self.sent: list[int] = []
        # When set, step 1 writes the (older) startup lines late and step 2 appends a
        # mid-game map reopening to War3Log.txt.
        self.log: Path | None = log
        self.startup: str = startup

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def _answer(self) -> GameState:
        state, human = self.exports.pop(0)
        _ = (self.directory / "state.txt").write_text(state, encoding="utf-8")
        _ = (self.directory / HUMAN_FILE).write_text(human, encoding="utf-8")
        parsed = parse_snapshot(state)
        assert parsed is not None
        return parsed

    def reset(self) -> GameState:
        self.game.relaunch()
        return self._answer()

    def step(self, action: int) -> GameState:
        self.sent.append(action)
        if self.log is not None and len(self.sent) in {1, 2}:
            late = self.startup if len(self.sent) == 1 else ""
            reopened = war3log_line(timedelta(seconds=1), REOPENED) if len(self.sent) == 2 else ""
            with self.log.open("a", encoding="utf-8") as stream:
                _ = stream.write(late + reopened)
        return self._answer()


REOPENED: Final = "Opening mod - C:/Maps/Download/Season9/(2)EchoIsles.w3x"


def war3log_line(offset: timedelta, text: str) -> str:
    moment = datetime.now().astimezone() + offset
    stamp = f"{moment.month}/{moment.day} {moment:%H:%M:%S}.{moment.microsecond // 1000:03d}"
    return f"{stamp}  {text}\n"


def test_records_the_players_inputs_without_touching_the_game_or_battle_net(
    tmp_path: Path, processes: Processes
) -> None:
    # Given: a game whose first answered step is still in the lobby.
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    exports = [
        (state_text(3, 0, gold=50), human_text(3, 0, "|select|u000")),
        (state_text(3, 1, Status.WAITING), human_text(3, 1)),
        (state_text(3, 2, gold=50), human_text(3, 2)),
        (
            state_text(3, 3),
            human_text(3, 3, "|spell|A0XX h001", "8|sell|h06F h062", "1|region|e0IX"),
        ),
        (state_text(3, 4), human_text(3, 4)),
        (state_text(3, 5, Status.LOSS), human_text(3, 5, "|chat|-gg")),
    ]
    settings = HumanSettings(
        training_map=tmp_path / "map.w3x", bridge_directory=bridge, output=tmp_path / "out"
    )
    bridges: list[FakeBridge] = []
    launches = Launches()

    def open_channel(chosen: HumanSettings, _: GameWatch) -> AbstractContextManager[FakeBridge]:
        bridges.append(FakeBridge(launches, chosen.bridge_directory, list(exports)))
        return bridges[0]

    # When
    logged = record_human(settings, open_channel)

    # Then: only the no-op was sent; rows touching the WAITING export are not written.
    assert bridges[0].sent == [0, 0, 0, 0, 0]
    rows = [
        cast("dict[str, object]", json.loads(line))
        for line in (settings.output / "transitions.jsonl").read_text("utf-8").splitlines()
    ]
    assert logged == len(rows) == 3
    assert [(row["sequence"], row["action"]) for row in rows] == [(3, 8), (4, 0), (5, 0)]
    assert rows[0]["human_inputs"] == [
        {"code": None, "kind": "spell", "raw": "A0XX h001"},
        {"code": 8, "kind": "sell", "raw": "h06F h062"},
        {"code": 1, "kind": "region", "raw": "e0IX"},
    ]
    assert rows[2]["human_inputs"] == [{"code": None, "kind": "chat", "raw": "-gg"}]
    # Each row keeps the raw state.txt of its starting export.
    assert [row["snapshot"] for row in rows] == [exports[index][0] for index in (2, 3, 4)]
    assert rows[2]["status"] == "LOSS"
    # The player was asked once to open the map; no process was started or closed.
    assert launches.count == 1
    assert (processes.launcher, processes.games) == (1, set())
    assert not (settings.output / "display_breaks.jsonl").exists()


def test_a_map_reopened_during_the_game_is_logged_as_a_display_break(
    tmp_path: Path, processes: Processes
) -> None:
    # Given: War3Log arrives late, so the startup lines are written after the game started;
    # a ladder map is then reopened mid-game.
    bridge = tmp_path / "CustomMapData" / "ORD_AI"
    bridge.mkdir(parents=True)
    log = tmp_path / "Logs" / "War3Log.txt"
    log.parent.mkdir()
    _ = log.write_text("", encoding="utf-8")
    started = war3log_line(timedelta(minutes=-2), "GameMain Started")
    opened = war3log_line(timedelta(minutes=-1), "Opening mod - C:/Maps/Download/ORD_AI/map.w3x")
    exports = [(state_text(4, step), human_text(4, step)) for step in range(4)]
    exports.append((state_text(4, 4, Status.LOSS), human_text(4, 4)))
    settings = HumanSettings(
        training_map=tmp_path / "map.w3x", bridge_directory=bridge, output=tmp_path / "out"
    )

    def open_channel(chosen: HumanSettings, _: GameWatch) -> AbstractContextManager[FakeBridge]:
        return FakeBridge(Launches(), chosen.bridge_directory, list(exports), log, started + opened)

    # When
    logged = record_human(settings, open_channel)

    # Then: every row is kept, and the break is noted once for episode 4.
    assert logged == 4
    lines = (settings.output / "display_breaks.jsonl").read_text("utf-8").splitlines()
    assert [cast("dict[str, object]", json.loads(line))["episode"] for line in lines] == [4]
    assert (processes.launcher, processes.games) == (1, set())


def test_a_normal_map_without_human_inputs_is_refused(tmp_path: Path, processes: Processes) -> None:
    # Given: the normal AI map answers, so no human.txt is written.
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    settings = HumanSettings(
        training_map=tmp_path / "map.w3x", bridge_directory=bridge, output=tmp_path / "out"
    )

    def open_channel(chosen: HumanSettings, _: GameWatch) -> AbstractContextManager[FakeBridge]:
        return FakeBridge(Launches(), chosen.bridge_directory, [(state_text(3, 0), "")])

    # When / Then
    with pytest.raises(ProtocolError, match="human-play map"):
        _ = record_human(settings, open_channel)
    assert (processes.launcher, processes.games) == (1, set())


class HungGame:
    """A launched game whose player never leaves the lobby."""

    def __init__(self) -> None:
        self.launches: int = 0
        self.keys: int = 0

    def relaunch(self) -> None:
        self.launches += 1

    def press_any_key(self) -> None:
        self.keys += 1


def test_an_attended_reset_launches_once_and_presses_no_keys(tmp_path: Path) -> None:
    # Given
    game = HungGame()
    with StateChannel(tmp_path, timeout=0.2, game=game, attended=True) as channel:
        channel.nudge_interval = 0.05
        # When / Then: the timeout itself is the behaviour under test.
        with pytest.raises(TimeoutError):
            _ = channel.reset()
    assert (game.launches, game.keys) == (1, 0)


def demo_catalog() -> Catalog:
    return Catalog(
        map_sha256="test",
        units=(),
        actions=(
            Action(code=0, name="wait"),
            Action(code=8, name="gamble", kind=ActionKind.SHOP, gold=10),
        ),
    )


def test_demonstrations_have_one_sample_per_mapped_input(tmp_path: Path) -> None:
    # Given: step 1 has two gambles (not affordable per the mask) and an unmapped spell;
    # step 2 nothing; step 3 a code the catalog does not have.
    def row(sequence: int, reward: float, *inputs: dict[str, object]) -> str:
        data = {
            "episode": 3,
            "sequence": sequence,
            "reward": reward,
            "human_inputs": list(inputs),
            "snapshot": state_text(3, sequence - 1),
        }
        return json.dumps(data) + "\n"

    gamble: dict[str, object] = {"code": 8, "kind": "sell", "raw": "h06F h062"}
    spell: dict[str, object] = {"code": None, "kind": "spell", "raw": "A0XX h001"}
    unknown: dict[str, object] = {"code": 999, "kind": "sell", "raw": "h999 h062"}
    text = row(1, 0.0, gamble, spell, gamble) + row(2, 0.0) + row(3, 1.0, unknown)
    _ = (tmp_path / "transitions.jsonl").write_text(text, encoding="utf-8")
    catalog = demo_catalog()

    # When
    demos = human_demonstrations(tmp_path, catalog)
    loaded = Demonstrations.load(demos.dataset, catalog)

    # Then
    assert (demos.samples, demos.mapped, demos.unknown) == (4, 2, 1)
    assert torch.equal(loaded.actions, torch.tensor([1, 1, 0, 0]))
    assert torch.equal(loaded.masks[:, 1], torch.tensor([True, True, False, False]))
    assert float(loaded.returns[-1]) == pytest.approx(1.0)
    assert loaded.observations.shape[0] == 4


@pytest.fixture(scope="module")
def map_data() -> MapData:
    return read_map(_MAP_PATH.read_bytes())


def test_the_normal_bridge_has_no_capture_code(map_data: MapData) -> None:
    # When
    script = inject_bridge(map_data.script)

    # Then
    assert "ORDAI_human" not in script
    assert "ORDAI_Human" not in script
    assert "human.txt" not in script
    assert len(map_data.catalog.actions) == 590


def test_the_human_bridge_is_the_normal_one_plus_capture(map_data: MapData) -> None:
    # Given
    table = render_human_table(map_data.catalog, map_data.inputs, map_data.script)
    normal = inject_bridge(map_data.script)

    # When
    human = inject_bridge(map_data.script, sources=human_bridge_sources(EASY_STAGE, table))

    # Then: capture, export and lobby hooks are present.
    for needle in (
        "boolean ORDAI_human = true",
        "call ORDAI_HumanInit()",
        "call ORDAI_HumanExport()",
        "call ORDAI_HumanArm()",
        'PreloadGenEnd("ORD_AI\\\\human.txt")',
        "elseif not ORDAI_human and not tU and aU != null and ks[0] then",
        "call SaveInteger(ORDAI_data, 200, 'h06F', 8)",
        'if text == "-offline" then',
        'call BlzTriggerRegisterPlayerSyncEvent(ORDAI_humanSync, Player(0), "CODE_LOAD", false)',
    ):
        assert needle in human
    assert "call ORDAI_Deploy()" not in human
    # Removing the human additions gives back the normal script exactly.
    globals_text = (JASS_DIRECTORY / "bridge_human_globals.j").read_text("utf-8")
    capture = (JASS_DIRECTORY / "bridge_human.j").read_text("utf-8")
    capture = capture.replace("// HUMAN TABLE\n", table)
    restored = human.replace(globals_text, "").replace("\n" + capture, "")
    restored = restored.replace(
        (
            "    if a != 0 then\n"
            "        // Human-play build: the player acts; only the no-op wait is answered.\n"
            "        return\n"
            "    endif\n"
        ),
        "",
    )
    restored = restored.replace("not ORDAI_human and not tU", "not tU")
    restored = restored.replace(
        (
            "    if ORDAI_started and not ORDAI_humanArmed then\n"
            "        call ORDAI_HumanArm()\n"
            "    endif\n"
        ),
        "",
    )
    restored = restored.replace(
        "            call ORDAI_Act(ORDAI_action)\n",
        "            call ORDAI_Deploy()\n            call ORDAI_Act(ORDAI_action)\n",
    )
    restored = restored.replace("    call ORDAI_HumanExport()\n", "")
    restored = restored.replace("    call ORDAI_HumanInit()\n", "")
    assert restored == normal


def test_the_human_build_keeps_the_normal_build_files(tmp_path: Path) -> None:
    # When
    catalog = build_map(_MAP_PATH, tmp_path / "ORDR_AI_2.323R_human.w3x", human=True)

    # Then: its script has its own name and the shared catalog is not rewritten.
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "ORDR_AI_2.323R_human.w3x",
        "war3map_human.j",
    ]
    assert len(catalog.actions) == 590


REJECTED = "Map contains invalid Jass scripts that couldn't be compiled by war3, file: war3map.j"
WAR3_LOG_REJECTED = "\n".join(
    (
        "9/29 20:00:01.000  GameMain Started",
        f"9/29 20:00:05.000  {REJECTED} @ 10, error: old on line 10",
        "9/29 20:00:09.000  GameMain Ended",
        "9/29 20:19:06.101  GameMain Started",
        f"9/29 20:19:15.046  {REJECTED} @ 87778, error: invalid type for specified operator",
        f"9/29 20:19:15.046  {REJECTED} @ 87782, error: unknown compile error on line 87782",
    )
)


def test_the_watch_stops_a_recording_whose_map_warcraft_could_not_compile(tmp_path: Path) -> None:
    # Given: the latest start logged two compile errors (an earlier start logged another).
    log = tmp_path / "War3Log.txt"
    _ = log.write_text(WAR3_LOG_REJECTED, encoding="utf-8")
    watch = GameWatch(log, running=lambda: True)

    # When / Then
    with pytest.raises(HumanPlayError, match="87778") as raised:
        watch()
    assert "전체 2개" in raised.value.detail


def test_the_watch_waits_for_the_player_to_start_the_game_and_stops_once_closed(
    tmp_path: Path,
) -> None:
    # Given: a clean log and a game that is started by the player, then closed.
    log = tmp_path / "War3Log.txt"
    _ = log.write_text("9/29 20:19:06.101  GameMain Started\n", encoding="utf-8")
    running = iter((False, False, True, False))
    watch = GameWatch(log, running=lambda: next(running))

    # When / Then: not started yet (twice), running, then closed.
    watch()
    watch()
    watch()
    with pytest.raises(HumanPlayError, match="꺼졌습니다"):
        watch()


def test_demonstrations_leave_out_rows_after_a_display_break(tmp_path: Path) -> None:
    # Given: episode 5 broke at game time 20; episode 6 has no break.
    def row(episode: int, sequence: int, game_time: float) -> str:
        data: dict[str, object] = {
            "episode": episode,
            "sequence": sequence,
            "reward": 0.0,
            "game_time": game_time,
            "human_inputs": [],
            "snapshot": state_text(episode, sequence - 1),
        }
        return json.dumps(data) + "\n"

    rows = [row(5, 1, 10.0), row(5, 2, 20.0), row(5, 3, 30.0), row(6, 1, 25.0)]
    _ = (tmp_path / "transitions.jsonl").write_text("".join(rows), encoding="utf-8")
    breaks = '{"episode": 5, "game_time": 20.0}\n{"episode": 5, "game_time": 40.0}\n'
    _ = (tmp_path / "display_breaks.jsonl").write_text(breaks, encoding="utf-8")

    # When
    demos = human_demonstrations(tmp_path, demo_catalog())

    # Then: episode 5 keeps only game time 10; episode 6 is whole.
    assert demos.samples == 2
