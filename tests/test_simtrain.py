import io
import json
from contextlib import nullcontext
from pathlib import Path
from typing import cast, override

import pytest
import torch

from ord_ai import simtrain
from ord_ai.build import bridge_sources
from ord_ai.catalog import Action, Catalog
from ord_ai.checkpoint import load_checkpoint
from ord_ai.curriculum import STAGES, Difficulty, Stage
from ord_ai.learning import Learner
from ord_ai.observation import input_size
from ord_ai.protocol import ProtocolError, parse_snapshot
from ord_ai.sim import WINS, SimServer, SimVectorEnv, WinTrace, prepare_root, server_build
from ord_ai.simtrain import (
    Curriculum,
    EpisodeLog,
    FineTuneSettings,
    ImitationSettings,
    Trace,
    fine_tune,
    imitate,
    new_model,
    sample_actions,
    save_policy,
    stage_name,
)

CATALOG = Catalog(
    map_sha256="test",
    units=(),
    actions=tuple(Action(code=code, name=f"a{code}") for code in range(3)),
)


def snapshot(episode: int, sequence: int) -> str:
    state = f"ORD_AI|1|STATE|{episode}|{sequence}|1|50|0|0|5|100|20|1|0|1|1"
    return f'call Preload("{state}")\ncall Preload("ORD_AI|1|END|{episode}|{sequence}")\n'


HELLO: dict[str, object] = {
    "ok": True,
    "map_sha256": "map",
    "catalog_fingerprint": "catalog",
    "bridge_sha256": "script",
}


class FakeServer(SimServer):
    """Answers batch requests like the simulator without starting a process."""

    def __init__(self) -> None:
        super().__init__(Path("unused.dll"))
        self.requests: list[dict[str, object]] = []

    @override
    def request(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        if request.get("op") == "hello":
            return HELLO
        items = cast("list[object]", request.get("resets", request.get("steps", [request])))
        results = [{"ok": True, "state_text": snapshot(7, index)} for index in range(len(items))]
        return {"ok": True, "results": results, "state_text": snapshot(7, 0)}


def test_bridge_sources_carry_the_stage_lobby_choice() -> None:
    # When
    declarations, _, _ = bridge_sources(Stage(difficulty=Difficulty.NIGHTMARE, isekai=True))

    # Then
    assert "integer ORDAI_difficulty = 6\nboolean ORDAI_isekai = true\n" in declarations
    assert stage_name(Stage(difficulty=Difficulty.NIGHTMARE, isekai=True)) == "d6i"


def test_isekai_below_hell_is_refused_like_the_map() -> None:
    # When / Then
    with pytest.raises(ProtocolError):
        _ = bridge_sources(Stage(difficulty=Difficulty.HARD, isekai=True))


def test_vector_env_gives_each_environment_its_own_seed_sequence() -> None:
    # Given
    server = FakeServer()
    vector = SimVectorEnv(server, 3, base_seed=100)

    # When
    states = vector.reset_all()
    _ = vector.reset(1)
    stepped = vector.step([0, 1, 2])

    # Then
    assert [state.sequence for state in states] == [0, 1, 2]
    assert server.requests[0]["resets"] == [
        {"env": 0, "seed": 100},
        {"env": 1, "seed": 101},
        {"env": 2, "seed": 102},
    ]
    assert server.requests[1] == {"op": "reset", "env": 1, "seed": 104}
    assert len(stepped) == 3


class WinningServer(FakeServer):
    """Environment 1 wins on its second step after a reset; the others keep playing."""

    def __init__(self) -> None:
        super().__init__()
        self.steps: dict[int, int] = {}

    @override
    def request(self, request: dict[str, object]) -> dict[str, object]:
        response = super().request(request)
        if request.get("op") != "step_batch":
            for item in cast("list[dict[str, object]]", request.get("resets", [request])):
                if "env" in item:
                    self.steps[cast("int", item["env"])] = 0
            return response
        for item, result in zip(
            cast("list[dict[str, object]]", request["steps"]),
            cast("list[dict[str, object]]", response["results"]),
            strict=True,
        ):
            env = cast("int", item["env"])
            self.steps[env] += 1
            if env == 1 and self.steps[env] == 2:
                text = cast("str", result["state_text"])
                result["state_text"] = text.replace('|1|0|1|1")', '|1|0|2|1")')
        return response


def test_vector_env_archives_each_win_with_its_seed_and_sent_codes(tmp_path: Path) -> None:
    # Given
    server = WinningServer()
    wins = io.StringIO()
    trace = WinTrace(wins, server, tmp_path / "OrandSimulator.Server.dll", {"phase": "rl"})
    vector = SimVectorEnv(server, 2, base_seed=100, wins=trace)

    # When: env 1 wins on its second step, is reset, then wins again the same way.
    _ = vector.reset_all()
    _ = vector.step([2, 0])
    first = vector.step([1, 2])
    _ = vector.reset(1)
    _ = vector.step([0, 1])
    _ = vector.step([0, 0])

    # Then: one line per win, with that game's seed, its own sent codes and final STATE.
    lines = [cast("dict[str, object]", json.loads(line)) for line in wins.getvalue().splitlines()]
    assert [line["seed"] for line in lines] == [101, 103]
    assert [line["codes"] for line in lines] == [[0, 2], [1, 0]]
    assert [line["length"] for line in lines] == [2, 2]
    assert parse_snapshot(cast("str", lines[0]["final_state"])) == first[1]
    assert lines[0]["env"] == 1
    assert lines[0]["phase"] == "rl"
    assert lines[0]["bridge_sha256"] == "script"
    assert lines[0]["server_dll_sha256"] is None
    assert [request for request in server.requests if request.get("op") == "hello"] == [
        {"op": "hello"}
    ]


def test_sampling_never_returns_a_masked_action() -> None:
    # Given
    learner = Learner(new_model(CATALOG), "cpu")
    observations = [tuple(float(value) for value in range(input_size(CATALOG)))] * 60
    masks = [tuple(column == row % 3 for column in range(3)) for row in range(60)]

    # When
    chosen = sample_actions(learner, observations, masks)

    # Then
    assert chosen == [row % 3 for row in range(60)]


def test_imitation_reproduces_a_learnable_teacher(tmp_path: Path) -> None:
    # Given: the teacher picks the action whose feature is largest.
    generator = torch.Generator().manual_seed(3)
    observations = torch.rand((600, input_size(CATALOG)), generator=generator)
    actions = observations[:, :3].argmax(dim=1)
    dataset = tmp_path / "demonstrations.pt"
    torch.save(
        {
            "catalog_fingerprint": CATALOG.fingerprint,
            "observations": observations,
            "masks": torch.ones((600, 3), dtype=torch.bool),
            "actions": actions,
            "returns": torch.zeros(600),
        },
        dataset,
    )
    settings = ImitationSettings(epochs=60, batch_size=64, learning_rate=0.01, device="cpu", seed=5)

    # When
    agreement = imitate(CATALOG, dataset, tmp_path / "policy", settings)

    # Then: the saved policy loads with the real-game checkpoint check.
    assert agreement > 0.9
    _ = load_checkpoint(tmp_path / "policy", CATALOG, "cpu")


def test_demonstrations_from_another_catalog_are_refused(tmp_path: Path) -> None:
    # Given
    dataset = tmp_path / "demonstrations.pt"
    torch.save({"catalog_fingerprint": "other"}, dataset)
    settings = ImitationSettings(epochs=1, batch_size=8, learning_rate=0.01, device="cpu", seed=5)

    # When / Then
    with pytest.raises(ProtocolError):
        _ = imitate(CATALOG, dataset, tmp_path / "policy", settings)


def test_curriculum_progress_survives_a_restart(tmp_path: Path) -> None:
    # Given
    progress = Curriculum(stage=2, steps=5000, promotions=[{"steps": 4000, "from": 1, "to": 2}])

    # When
    progress.save(tmp_path)
    loaded = Curriculum.load(tmp_path)
    fresh = Curriculum.load(tmp_path / "missing")

    # Then
    assert loaded == progress
    assert (fresh.stage, fresh.steps, fresh.promotions) == (0, 0, [])


def test_preparing_a_root_again_rewrites_only_what_changed(tmp_path: Path) -> None:
    # Given: a simulator layout and a root already prepared from it.
    simulator = tmp_path / "simulator"
    binaries = simulator / "OrandSimulator.Server/bin/Release/net10.0"
    binaries.mkdir(parents=True)
    _ = (binaries / "OrandSimulator.Server.dll").write_bytes(b"server")
    _ = (binaries / "OrandSimulator.Runtime.dll").write_bytes(b"runtime")
    (simulator / "OrandSimulator.Runtime/BaseData").mkdir(parents=True)
    (simulator / "maps").mkdir()
    root = tmp_path / "root"
    dll = prepare_root(Stage(difficulty=Difficulty.EASY, isekai=False), root, simulator)
    running = dll.parent / "OrandSimulator.Runtime.dll"
    first = running.stat().st_mtime_ns
    bridge = root / "OrandSimulator.Runtime/BridgeSource/ord-picker-2323/bridge_globals.j"
    written = bridge.stat().st_mtime_ns

    # When: the server binary changes and the root is prepared again.
    _ = (binaries / "OrandSimulator.Server.dll").write_bytes(b"server v2")
    _ = prepare_root(Stage(difficulty=Difficulty.EASY, isekai=False), root, simulator)

    # Then: the unchanged runtime and bridge files are left alone, the new server is copied.
    assert running.stat().st_mtime_ns == first
    assert bridge.stat().st_mtime_ns == written
    assert dll.read_bytes() == b"server v2"


def test_a_rebuilt_simulator_gets_a_new_build_stamp(tmp_path: Path) -> None:
    # Given
    binaries = tmp_path / "OrandSimulator.Server/bin/Release/net10.0"
    binaries.mkdir(parents=True)
    server = binaries / "OrandSimulator.Server.dll"
    _ = server.write_bytes(b"server")
    before = server_build(tmp_path)

    # When
    _ = server.write_bytes(b"server v2")

    # Then: a new stamp names a new root; the same build keeps its stamp.
    assert server_build(tmp_path) != before
    assert server_build(tmp_path) == server_build(tmp_path)


def test_fine_tuning_resumes_at_the_steps_of_its_last_checkpoint(tmp_path: Path) -> None:
    # Given: the last promotion saved 5000 steps, a later checkpoint 7000.
    Curriculum(stage=2, steps=5000).save(tmp_path)
    save_policy(tmp_path, Learner(new_model(CATALOG), "cpu"), CATALOG, 7000)
    settings = FineTuneSettings(
        steps=7000,
        envs=1,
        rollout=32,
        learning_rate=1e-4,
        base_seed=0,
        device="cpu",
        checkpoint_every=1000,
    )

    # When: the step budget is already spent, so no simulator is started.
    progress = fine_tune(CATALOG, None, tmp_path, settings)

    # Then
    assert (progress.stage, progress.steps) == (2, 7000)


def test_the_episode_log_counts_uses_of_the_self_learned_action_groups() -> None:
    # Given: two navigation choices (one refused) and one plain step.
    state = parse_snapshot(snapshot(7, 1))
    assert state is not None
    accepted = state.model_copy(update={"action_ok": 1})
    refused = state.model_copy(update={"action_ok": 0})
    trace = Trace()
    trace.add(1.0, accepted, ("navigation", "navigation_high_risk"))
    trace.add(0.0, refused, ("navigation",))
    trace.add(0.0, accepted)
    stream = io.StringIO()

    # When
    record = EpisodeLog(stream, "rl").write(
        3, 0, Stage(difficulty=Difficulty.EASY, isekai=False), state, trace
    )

    # Then
    written = cast("dict[str, object]", json.loads(stream.getvalue()))
    assert record.uses == {"navigation": 2, "navigation_high_risk": 1}
    assert written["uses_accepted"] == {"navigation": 1, "navigation_high_risk": 1}


class CountingServer(FakeServer):
    """A fake simulator that numbers each environment's snapshots like the bridge."""

    def __init__(self) -> None:
        super().__init__()
        self.sequences: dict[int, int] = {}

    @override
    def request(self, request: dict[str, object]) -> dict[str, object]:
        if request.get("op") == "hello":
            return HELLO
        default: list[object] = [request]
        items = cast(
            "list[dict[str, object]]", request.get("resets", request.get("steps", default))
        )
        results: list[dict[str, object]] = []
        for item in items:
            env = cast("int", item["env"])
            self.sequences[env] = 0 if "seed" in item else self.sequences[env] + 1
            results.append({"ok": True, "state_text": snapshot(7, self.sequences[env])})
        return {"ok": True, "results": results, "state_text": results[0]["state_text"]}


def counting_server(_dll: Path) -> nullcontext[CountingServer]:
    return nullcontext(CountingServer())


class ClearingServer(CountingServer):
    """Every action ends the game in a unit-count-0 clear, so each game promotes."""

    @override
    def request(self, request: dict[str, object]) -> dict[str, object]:
        response = super().request(request)
        if "resets" in request or "seed" in request or request.get("op") == "hello":
            return response
        for result in cast("list[dict[str, object]]", response["results"]):
            text = cast("str", result["state_text"])
            result["state_text"] = text.replace("|0|0|5|100|20|1|0|1|1", "|0|0|0|100|20|1|0|2|1")
        response["state_text"] = cast("list[dict[str, object]]", response["results"])[0][
            "state_text"
        ]
        return response


def clearing_server(_dll: Path) -> nullcontext[ClearingServer]:
    return nullcontext(ClearingServer())


def unused_root(_stage: Stage) -> Path:
    return Path("unused.dll")


def weights(model: torch.nn.Module) -> dict[str, torch.Tensor]:
    state = cast("dict[str, torch.Tensor]", model.state_dict())
    return {name: value.clone() for name, value in state.items()}


def test_ppo_fine_tuning_updates_and_saves_a_real_game_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: two fake games, rollouts of four steps, sixteen steps in all.
    monkeypatch.setattr(simtrain, "SimServer", counting_server)
    monkeypatch.setattr(simtrain, "stage_server", unused_root)
    torch.set_rng_state(torch.Generator().manual_seed(3).get_state())
    initial = weights(new_model(CATALOG))
    torch.set_rng_state(torch.Generator().manual_seed(3).get_state())
    settings = FineTuneSettings(
        steps=16,
        envs=2,
        rollout=4,
        learning_rate=1e-3,
        base_seed=0,
        device="cpu",
        checkpoint_every=8,
        algorithm="ppo",
        ppo_epochs=2,
        ppo_minibatch=4,
    )

    # When
    progress = fine_tune(CATALOG, None, tmp_path, settings)

    # Then: the saved policy loads through the real-game path and has moved.
    trained = weights(load_checkpoint(tmp_path, CATALOG, "cpu"))
    assert progress.steps == 16
    assert any(not torch.equal(initial[name], trained[name]) for name in initial)


def test_best_is_judged_on_the_current_stage_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: every game is a one-step unit-count-0 clear, so the curriculum climbs to the
    # last stage and then plays there.
    monkeypatch.setattr(simtrain, "SimServer", clearing_server)
    monkeypatch.setattr(simtrain, "stage_server", unused_root)
    settings = FineTuneSettings(
        steps=100,
        envs=1,
        rollout=4,
        learning_rate=1e-3,
        base_seed=0,
        device="cpu",
        checkpoint_every=1,
    )

    # When
    progress = fine_tune(CATALOG, None, tmp_path, settings)

    # Then: best/ needed a full window of games on the last stage after its promotion.
    best = cast(
        "dict[str, object]",
        json.loads((tmp_path / "best" / "metadata.json").read_text("utf-8")),
    )
    last_promotion = progress.promotions[-1]["steps"]
    assert progress.stage == len(STAGES) - 1
    assert cast("int", best["steps"]) - last_promotion >= simtrain.BEST_WINDOW
    # Every won game is archived once, with as many sent codes as the game had steps.
    episodes = [
        cast("dict[str, object]", json.loads(line))
        for line in (tmp_path / "episodes.jsonl").read_text("utf-8").splitlines()
    ]
    wins = [
        cast("dict[str, object]", json.loads(line))
        for line in (tmp_path / WINS).read_text("utf-8").splitlines()
    ]
    won = [episode for episode in episodes if episode["status"] == "WIN"]
    assert won
    assert [len(cast("list[int]", win["codes"])) for win in wins] == [
        episode["length"] for episode in won
    ]
    assert {win["phase"] for win in wins} == {"rl"}
    assert all(set(cast("list[int]", win["codes"])) <= {0, 1, 2} for win in wins)
