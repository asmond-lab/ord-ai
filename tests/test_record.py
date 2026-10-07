import torch

from ord_ai.catalog import Action, ActionKind, Catalog
from ord_ai.learning import ActorCritic, Learner
from ord_ai.observation import input_size
from ord_ai.protocol import GameState
from ord_ai.record import SCAN_REFUSAL_LIMIT, PolicyPlayer, telemetry_line
from ord_ai.telemetry import Telemetry, parse_telemetry

CATALOG = Catalog(
    map_sha256="test",
    units=(),
    actions=(
        Action(code=0, name="wait"),
        Action(code=574, name="scan chest 0", kind=ActionKind.TREASURE),
    ),
)


def test_the_telemetry_archive_keeps_handles_of_same_type_units() -> None:
    # Given: two units of one type at one spot, told apart only by their handles.
    rows = [
        "ORD_AI|1|TIME|54|1183|1488.125|1167913388|0|0|0",
        "ORD_AI|1|POS|1058710|1211122484|-5888.000|5664.000|0",
        "ORD_AI|1|POS|1058711|1211122484|-5888.000|5664.000|851983",
        "ORD_AI|1|TEND|54|1183",
    ]
    record = parse_telemetry("".join(f'call Preload( "{row}" )\n' for row in rows))
    assert record is not None

    # When
    line = telemetry_line(record)

    # Then: one line that reads back as the same record, handles and orders included.
    assert line.endswith("\n")
    assert line.count("\n") == 1
    restored = Telemetry.model_validate_json(line)
    assert restored == record
    assert [(unit.handle, unit.order) for unit in restored.units] == [
        (1058710, 0),
        (1058711, 851983),
    ]


def scan_loving_player() -> PolicyPlayer:
    model = ActorCritic(input_size(CATALOG), len(CATALOG.actions))
    with torch.no_grad():
        _ = model.policy_head.weight.zero_()
        _ = model.policy_head.bias.copy_(torch.tensor([0.0, 10.0]))
    return PolicyPlayer(Learner(model, "cpu"), greedy=True)


def test_refused_scans_are_masked_for_the_rest_of_the_game(state: GameState) -> None:
    # Given: a greedy policy that always prefers the chest scan, and the game refuses it.
    player = scan_loving_player()
    refused = state.model_copy(update={"action_ok": 0})
    mask = (True, True)

    # When
    chosen: list[int] = []
    for _ in range(SCAN_REFUSAL_LIMIT + 1):
        chosen.append(player.choose(state, CATALOG, mask))
        player.observe(refused)
    player.reset()
    next_game = player.choose(state, CATALOG, mask)

    # Then: the scan is tried LIMIT times, then masked until the next game.
    assert chosen == [1] * SCAN_REFUSAL_LIMIT + [0]
    assert next_game == 1


def test_an_accepted_scan_restarts_the_refusal_count(state: GameState) -> None:
    # Given
    player = scan_loving_player()
    refused = state.model_copy(update={"action_ok": 0})
    accepted = state.model_copy(update={"action_ok": 1})
    mask = (True, True)

    # When: one refusal short of the limit, then an accepted scan, then more refusals.
    for _ in range(SCAN_REFUSAL_LIMIT - 1):
        _ = player.choose(state, CATALOG, mask)
        player.observe(refused)
    _ = player.choose(state, CATALOG, mask)
    player.observe(accepted)
    for _ in range(SCAN_REFUSAL_LIMIT - 1):
        _ = player.choose(state, CATALOG, mask)
        player.observe(refused)

    # Then
    assert player.choose(state, CATALOG, mask) == 1
