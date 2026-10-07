import struct
import zlib
from pathlib import Path
from typing import override

import pytest

from ord_ai.catalog import ActionKind, Catalog
from ord_ai.curriculum import Difficulty, Stage
from ord_ai.objectdata import rawcode
from ord_ai.replay import (
    Conversion,
    Replay,
    Tables,
    convert,
    decode_stat_string,
    lobby_stage,
    parse,
    parse_actions,
    presence,
    read,
    read_header,
    reconstruct,
)
from ord_ai.sim import SimServer

REAL_REPLAY = Path("data/human-replays/2026-09-29_1952_god_solo_loss.w3g")
NONE = b"\xff\xff\xff\xff" * 2


def rc(code: str) -> bytes:
    """A rawcode as the little-endian u32 of the replay."""
    return struct.pack("<I", rawcode(code))


def pair(first: int, second: int) -> bytes:
    return struct.pack("<II", first, second)


def encode_stat_string(plain: bytes) -> bytes:
    """Inverse of decode_stat_string: even bytes stay, odd bytes are +1 with a clear bit."""
    out = bytearray()
    for start in range(0, len(plain), 7):
        chunk = plain[start : start + 7]
        mask = 1
        body = bytearray()
        for index, byte in enumerate(chunk):
            if byte % 2 == 0:
                body.append(byte + 1)
            else:
                mask |= 1 << (index + 1)
                body.append(byte)
        out.append(mask)
        out.extend(body)
    return bytes(out)


def game_records(seed: int) -> bytes:
    settings = (
        bytes([2, 0, 0, 0])
        + bytes(5)
        + struct.pack("<I", 0x12345678)
        + b"Maps/Test.w3x\x00Host\x00\x00"
        + bytes(range(20))
    )
    slots = bytes([1, 100, 2, 0, 0, 0, 1, 0, 100]) + bytes([0, 255, 2, 1, 1, 5, 2, 1, 100])
    start = bytes([2]) + slots + struct.pack("<I", seed) + bytes([3, 6])
    return (
        struct.pack("<I", 0x110)
        + b"\x00\x01Host\x00\x02\x00\x00"
        + b"Game\x00\x00"
        + encode_stat_string(settings)
        + b"\x00"
        + struct.pack("<I", 2)
        + bytes([1, 0x20, 0, 0])
        + struct.pack("<I", 0)
        + b"\x38\x03"
        + struct.pack("<I", 3)
        + b"abc"
        + b"\x19"
        + struct.pack("<H", len(start))
        + start
    )


def time_slot(increment: int, *blocks: tuple[int, bytes]) -> bytes:
    body = struct.pack("<H", increment) + b"".join(
        bytes([player]) + struct.pack("<H", len(data)) + data for player, data in blocks
    )
    return b"\x1f" + struct.pack("<H", len(body)) + body


WISP = pair(10, 20)
HERO = pair(11, 21)
FUSION_BUTTON = b"\x7a" + WISP + rc("A01H") + struct.pack("<I", 852102)
FUSION_ORDER = b"\x10" + struct.pack("<HI", 100, 852102) + NONE
SELECT_WISP = b"\x16\x01" + struct.pack("<H", 1) + WISP + b"\x1a" + b"\x19" + rc("e0IX") + WISP
MOVE_TO_PAD = b"\x12" + struct.pack("<HI", 0, 851971) + NONE + struct.pack("<ff", 5.0, 5.0) + NONE
SYNC = b"\x77NAVI_3IDX\x003\x00" + bytes(4)
CHAT = b"\x60" + pair(1, 1) + "바다의협객".encode() + b"\x00"
UNKNOWN = b"\x99\x01\x02\x03"


def replay_bytes(records: bytes, *, build: int = 7000) -> bytes:
    """A Reforged-framed (12-byte block header) replay around ``records``."""
    padded = records + bytes(-len(records) % 8192)
    blocks = b""
    for start in range(0, len(padded), 8192):
        chunk = padded[start : start + 8192]
        packed = zlib.compress(chunk)
        blocks += struct.pack("<III", len(packed), len(chunk), 0) + packed
    header = b"Warcraft III recorded game\x1a\x00" + struct.pack(
        "<5I", 0x44, 0x44 + len(blocks), 1, len(records), len(padded) // 8192
    )
    header += b"PX3W" + struct.pack("<IHHII", 10200, build, 0x8000, 4321, 7)
    return header + blocks


def sample() -> Replay:
    records = game_records(seed=0x742C70C9)
    records += b"\x1a" + struct.pack("<I", 1) + b"\x22\x04" + bytes(4)
    records += time_slot(100, (1, SELECT_WISP))
    records += time_slot(250, (1, FUSION_BUTTON + FUSION_ORDER + MOVE_TO_PAD))
    records += time_slot(250, (1, SYNC + CHAT + UNKNOWN))
    records += b"\x17" + struct.pack("<IBII", 1, 1, 13, 2)
    return parse(replay_bytes(records))


def test_stat_string_decoding_undoes_the_lobby_encoding() -> None:
    plain = bytes(range(40))
    assert decode_stat_string(encode_stat_string(plain)) == plain


def test_header_reads_the_reforged_sub_header() -> None:
    header = read_header(replay_bytes(game_records(1)))
    assert (header.product, header.version, header.build) == ("W3XP", 10200, 7000)
    assert header.multiplayer
    assert (header.length_ms, header.checksum, header.header_size) == (4321, 7, 0x44)


def test_game_start_record_gives_players_slots_seed_and_map() -> None:
    replay = sample()
    game = replay.game
    assert (game.host.name, game.game_name, game.map_path, game.creator) == (
        "Host",
        "Game",
        "Maps/Test.w3x",
        "Host",
    )
    assert game.map_checksum == 0x12345678
    assert game.random_seed == 0x742C70C9
    assert [(slot.player_id, slot.computer, slot.team) for slot in game.slots] == [
        (1, False, 0),
        (0, True, 1),
    ]
    assert [(item.record, item.subtype, item.payload) for item in game.metadata] == [
        (0x38, 3, b"abc")
    ]


def test_time_slots_give_actions_their_game_time_and_keep_unknown_bytes() -> None:
    replay = sample()
    names = [(action.time_ms, action.name) for action in replay.actions]
    assert names == [
        (100, "select"),
        (100, "pre_subselect"),
        (100, "subgroup"),
        (350, "order_unit"),
        (350, "order_immediate"),
        (350, "order_target"),
        (600, "sync_data"),
        (600, "trigger_chat"),
        (600, "unknown_99"),
    ]
    unknown = replay.actions[-1]
    assert not unknown.decoded
    assert unknown.raw == UNKNOWN
    assert replay.actions[3].ability == rawcode("A01H")
    assert replay.actions[6].texts == ("NAVI_3IDX", "3")
    assert replay.actions[7].texts == ("바다의협객",)
    assert (replay.actions[5].x, replay.actions[5].y) == (5.0, 5.0)
    assert [(leave.player_id, leave.result, leave.time_ms) for leave in replay.leaves] == [
        (1, 13, 600)
    ]
    assert replay.end_ms == 600
    assert not replay.trailing


def test_a_malformed_action_keeps_the_rest_of_its_block_raw() -> None:
    truncated = b"\x12" + b"\x00" * 5
    actions = parse_actions(b"\x61" + truncated, 10, 1)
    assert [(action.name, action.decoded) for action in actions] == [
        ("escape", True),
        ("order_target", False),
    ]
    assert actions[1].raw == truncated


def fake_tables() -> Tables:
    return Tables(
        fusions={rawcode("A01H"): 20},
        inputs={(ActionKind.SELECT, rawcode("e0FJ")): 30},
        casters={},
        shop={rawcode("h06F"): 8},
        syncs={("NAVI_3IDX", 3): 40},
        chats={"바다의협객": 21},
        rects={"fY": (0.0, 0.0, 10.0, 10.0)},
        regions=(),
        basics={code: code for code in range(14)},
    )


def test_commands_map_fusion_button_pad_move_sync_and_chat() -> None:
    conversion = convert(sample(), fake_tables())
    mapped = [(command.kind, command.index) for command in conversion.commands]
    # The 0x10 order a 0x7A button announces is the same command, not a second one.
    assert mapped == [
        ("fusion", 20),
        ("basic 1 (fY)", 1),
        ("sync", 40),
        ("chat", 21),
    ]
    assert conversion.types == {(10, 20): rawcode("e0IX")}
    assert [action.name for action in conversion.unknown] == ["unknown_99"]


def test_micro_moves_and_untyped_selections_stay_unmapped() -> None:
    records = game_records(1)
    select = b"\x16\x01" + struct.pack("<H", 2) + WISP + HERO + b"\x19" + rc("e0IX") + WISP
    far = b"\x12" + struct.pack("<HI", 0, 851971) + NONE + struct.pack("<ff", 900.0, 900.0) + NONE
    near = b"\x12" + struct.pack("<HI", 0, 851971) + NONE + struct.pack("<ff", 5.0, 300.0) + NONE
    records += time_slot(100, (1, select + far + near))
    conversion = convert(parse(replay_bytes(records)), fake_tables())
    assert [(command.kind, command.index) for command in conversion.commands] == [
        ("move (untyped selection)", None),
        # Untyped units selected with a random wisp count as wisps near the summon pad.
        ("basic 7 (fY) near", 7),
    ]
    assert (conversion.selected_objects, conversion.typed_selected_objects) == (2, 1)


def test_lobby_stage_reads_difficulty_and_isekai_toggles_until_start() -> None:
    conversion = Conversion(
        lobby=[(1, "difficulty 5"), (2, "isekai toggle"), (3, "start"), (4, "isekai toggle")]
    )
    assert lobby_stage(conversion) == Stage(difficulty=Difficulty.GOD, isekai=True)
    easy = Conversion(lobby=[(1, "difficulty 1"), (2, "isekai toggle"), (3, "start")])
    assert lobby_stage(easy) == Stage(difficulty=Difficulty.EASY, isekai=False)


def state_text(sequence: int, status: int, ok: int, unit: str = "") -> str:
    rows = [f"ORD_AI|1|STATE|1|{sequence}|1|50|0|0|0|70|0|0|0|{status}|{ok}"]
    if unit:
        rows.append(f"ORD_AI|1|UNIT|{rawcode(unit)}|1")
    rows.append(f"ORD_AI|1|END|1|{sequence}")
    return "\n".join(f'call Preload( "{row}" )' for row in rows)


class FakeServer(SimServer):
    """Answers reset and steps with scripted states; records the codes it was sent."""

    def __init__(self) -> None:
        super().__init__(Path("unused.dll"))
        self.sent: list[int] = []

    @override
    def request(self, request: dict[str, object]) -> dict[str, object]:
        if request["op"] == "reset":
            return {"state_text": state_text(0, 1, 1), "game_time": 1.0}
        action = request["action"]
        assert isinstance(action, int)
        self.sent.append(action)
        step = len(self.sent)
        status = 3 if step == 3 else 1
        return {"state_text": state_text(step, status, 1, "h00K"), "game_time": 1.0 + 1.25 * step}


def test_reconstruct_sends_each_command_once_its_time_has_come() -> None:
    catalog = Catalog.model_validate_json(Path("build/catalog.json").read_text("utf-8"))
    conversion = convert(sample(), fake_tables())
    server = FakeServer()
    run = reconstruct(conversion.commands, catalog, server, seed=1, end_ms=10_000)
    codes = [catalog.actions[index].code for index in (20, 1, 40)]
    # Replay times 0.35 s, 0.35 s and 0.6 s: all due at 1.0 s, one per step.
    assert server.sent == codes
    assert run.final is not None
    assert run.final.status.name == "LOSS"
    assert run.unsent == 1
    assert [step.time for step in run.steps] == [1.0, 2.25, 3.5]


def test_presence_checks_selected_own_units_against_the_simulated_state() -> None:
    catalog = Catalog.model_validate_json(Path("build/catalog.json").read_text("utf-8"))
    records = game_records(1)
    records += time_slot(1500, (1, b"\x19" + rc("h00K") + HERO))
    records += time_slot(1000, (1, b"\x19" + rc("h001") + HERO))
    replay = parse(replay_bytes(records))
    run = reconstruct([], catalog, FakeServer(), seed=1, end_ms=10_000)
    assert presence(replay, run, catalog) == [
        (1500, rawcode("h00K"), True),
        (2500, rawcode("h001"), False),
    ]


@pytest.mark.skipif(not REAL_REPLAY.exists(), reason="the user's replay is not present")
def test_real_replay_header_and_game_record() -> None:
    replay = read(REAL_REPLAY)
    assert (replay.header.version, replay.header.build) == (10200, 7000)
    assert replay.game.map_path.endswith("ORDR_S2_2.323[R].w3x")
    assert replay.end_ms == replay.header.length_ms
    assert all(action.decoded for action in replay.actions)
    assert not replay.trailing
