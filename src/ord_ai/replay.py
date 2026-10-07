"""Read Warcraft III replays (.w3g), including Reforged ones, with the standard library only.

Format sources:

* Header, block framing, the game start record, time slots, chat and leave records and the
  classic action layouts follow "w3g_format.txt" / "w3g_actions.txt" (Blue, Nagger and
  others, the community W3G format notes) and the TypeScript parser w3gjs by PBug90
  (github.com/PBug90/w3gjs: ``RawParser``, ``MetadataParser``, ``GameDataParser``,
  ``ActionParser``). From w3gjs: blocks of builds >= 6089 (Reforged) have 12-byte headers
  (u32 compressed size, u32 decompressed size, u32 checksum) instead of 8-byte ones; the
  0x38/0x39 Reforged metadata records (subtype, u32 length, protobuf payload) sit between
  the player list and the game start record; flags of 0x10-0x14 are u16 since 1.13.
* Reforged action layouts that neither source pins for this build were checked against the
  replay itself: every action block (u16 length) must be consumed exactly. Layouts found that
  way (0x77 BlzSendSyncData, 0x78, 0x7A) are marked below; anything else keeps its raw bytes.

The second half maps the human's commands to catalog actions (:func:`convert`) and drives
the simulator with them (:func:`reconstruct`, :func:`presence`) to measure how long a
simulated game stays consistent with the recorded one.
"""

import math
import re
import struct
import zlib
from bisect import bisect_left
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import Path
from typing import Final

from ord_ai.build import MapData
from ord_ai.catalog import RESEARCH, ActionKind, BasicAction, Catalog
from ord_ai.curriculum import ISEKAI_FLOOR, Difficulty, Stage
from ord_ai.objectdata import rawcode, rawtext
from ord_ai.protocol import GameState, ProtocolError, parse_snapshot
from ord_ai.sim import SimServer

MAGIC: Final = b"Warcraft III recorded game\x1a\x00"
REFORGED_BUILD: Final = 6089  # w3gjs: block headers are 12 bytes from this build on
_MULTIPLAYER: Final = 0x8000
_NO_OBJECT: Final = 0xFFFFFFFF
_PRINTABLE: Final = range(0x20, 0x7F)
_CHAT_WITH_MODE: Final = 0x20
_ORDER_IMMEDIATE: Final = 0x10
_ORDER_POINT: Final = 0x11
_ORDER_TARGET: Final = 0x12
_ORDER_ITEM: Final = 0x13
_ORDER_TWO_POINTS: Final = 0x14
_SELECT: Final = 0x16
_GROUP_ASSIGN: Final = 0x17
_GROUP_SELECT: Final = 0x18
_SUBGROUP: Final = 0x19
_BUTTON: Final = 0x7A
_UI_ACTIONS: Final = frozenset({_SELECT, _GROUP_ASSIGN, _GROUP_SELECT, _SUBGROUP, 0x1A, 0x1B, 0x1C})
_SLOT_USED: Final = 2


class Record(IntEnum):
    """Top-level records of the decompressed replay data."""

    PLAYER = 0x16
    LEAVE = 0x17
    START = 0x19
    FIRST_START = 0x1A
    SECOND_START = 0x1B
    THIRD_START = 0x1C
    TIME_SLOT_OLD = 0x1E
    TIME_SLOT = 0x1F
    CHAT = 0x20
    CHECKSUM = 0x22
    UNKNOWN_23 = 0x23
    FORCED_END = 0x2F
    REFORGED_38 = 0x38
    REFORGED_39 = 0x39


ACTION_NAMES: Final = {
    0x01: "pause",
    0x02: "resume",
    0x03: "set_speed",
    0x04: "speed_up",
    0x05: "speed_down",
    0x06: "save_game",
    0x07: "save_finished",
    0x10: "order_immediate",
    0x11: "order_point",
    0x12: "order_target",
    0x13: "order_give_item",
    0x14: "order_two_points",
    0x16: "select",
    0x17: "group_assign",
    0x18: "group_select",
    0x19: "subgroup",
    0x1A: "pre_subselect",
    0x1B: "select_unknown_1b",
    0x1C: "select_item",
    0x1D: "cancel_revival",
    0x1E: "dequeue",
    0x21: "unknown_21",
    0x2D: "unknown_2d",
    0x50: "ally_options",
    0x51: "transfer",
    0x60: "trigger_chat",
    0x61: "escape",
    0x62: "trigger_sync",
    0x66: "hero_skill_menu",
    0x67: "build_menu",
    0x68: "minimap_ping",
    0x69: "continue_b",
    0x6A: "continue_a",
    0x6B: "sync_stored_integer",
    0x75: "unknown_75",
    0x77: "sync_data",  # BlzSendSyncData(prefix, data); layout checked on this replay
    0x78: "unknown_78",  # 17 bytes: object id pair, u32, then 5 bytes; checked
    0x7A: "order_unit",  # 16 bytes: unit, ability id, order/rawcode; a 0x10-0x12 follows
}
# Fixed payload sizes after the action id (1.07+ layouts; 0x10-0x14 with u16 flags).
_FIXED: Final = {
    0x01: 0,
    0x02: 0,
    0x03: 1,
    0x04: 0,
    0x05: 0,
    0x07: 4,
    0x10: 14,
    0x11: 22,
    0x12: 30,
    0x13: 38,
    0x14: 43,
    0x18: 2,
    0x19: 12,
    0x1A: 0,
    0x1B: 9,
    0x1C: 9,
    0x1D: 8,
    0x1E: 5,
    0x21: 8,
    0x2D: 5,
    0x50: 5,
    0x51: 9,
    0x61: 0,
    0x62: 12,
    0x66: 0,
    0x67: 0,
    0x68: 12,
    0x69: 16,
    0x6A: 16,
    0x75: 1,
    0x78: 17,
    0x7A: 16,
}


@dataclass(frozen=True, slots=True)
class Header:
    """The 0x44-byte file header of version 1 (patch 1.07 and later)."""

    header_size: int
    compressed_size: int
    header_version: int
    decompressed_size: int
    blocks: int
    product: str  # "W3XP" for The Frozen Throne / Reforged
    version: int  # 10200 and up for Reforged 2.0
    build: int
    multiplayer: bool
    length_ms: int
    checksum: int


@dataclass(frozen=True, slots=True)
class Player:
    """A player record of the lobby list."""

    player_id: int
    name: str
    host: bool
    extra: bytes


@dataclass(frozen=True, slots=True)
class Slot:
    """A slot of the game start record."""

    player_id: int
    download: int
    status: int  # 0 empty, 1 closed, 2 used
    computer: bool
    team: int
    color: int
    race: int
    ai_strength: int
    handicap: int


@dataclass(frozen=True, slots=True)
class Metadata:
    """A Reforged 0x38/0x39 record: its subtype and protobuf payload, kept raw."""

    record: int
    subtype: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class Game:
    """Everything before the first time slot."""

    host: Player
    game_name: str
    speed: int
    map_checksum: int
    map_path: str
    creator: str
    map_sha1: bytes
    slot_count: int
    game_type: int
    private: int
    language: int
    players: tuple[Player, ...]
    metadata: tuple[Metadata, ...]
    slots: tuple[Slot, ...]
    random_seed: int
    select_mode: int
    start_spots: int


@dataclass(frozen=True, slots=True)
class Action:
    """One player action of a time slot; ``raw`` is its complete byte form.

    Only the fields of the action's layout are set. Object ids are the replay's (id1, id2)
    pairs; ``(-1, -1)`` style "no object" pairs are kept as given.
    """

    time_ms: int
    player_id: int
    action_id: int
    raw: bytes
    flags: int = 0
    order: int = 0  # order id or rawcode (sold/trained/researched type, item, ability)
    ability: int = 0  # 0x7A: the ability id
    x: float | None = None
    y: float | None = None
    target: tuple[int, int] | None = None
    item: tuple[int, int] | None = None
    unit: tuple[int, int] | None = None  # 0x7A/0x78/0x19: the object the action names
    objects: tuple[tuple[int, int], ...] = ()
    mode: int = 0  # selection mode, group number, dequeue slot
    value: int = 0
    texts: tuple[str, ...] = ()
    decoded: bool = True

    @property
    def name(self) -> str:
        """The action's name, or ``unknown_XX``."""
        return ACTION_NAMES.get(self.action_id, f"unknown_{self.action_id:02x}")


@dataclass(frozen=True, slots=True)
class Chat:
    """A 0x20 chat record."""

    time_ms: int
    player_id: int
    flags: int
    mode: int
    text: str


@dataclass(frozen=True, slots=True)
class Leave:
    """A 0x17 leave record."""

    time_ms: int
    reason: int
    player_id: int
    result: int
    unknown: int


@dataclass(frozen=True, slots=True)
class Other:
    """Any other record between time slots (start markers, checksums, 0x23, 0x2F)."""

    time_ms: int
    record: int
    raw: bytes


@dataclass(slots=True)
class Replay:
    """A parsed replay."""

    header: Header
    game: Game
    actions: list[Action] = field(default_factory=list[Action])
    chats: list[Chat] = field(default_factory=list[Chat])
    leaves: list[Leave] = field(default_factory=list[Leave])
    others: list[Other] = field(default_factory=list[Other])
    end_ms: int = 0
    trailing: bytes = b""  # undecodable data after the last known record


class _Reader:
    """Little-endian cursor over bytes; every read is bounds-checked."""

    def __init__(self, data: bytes, offset: int = 0) -> None:
        self.data: bytes = data
        self.offset: int = offset

    def left(self) -> int:
        return len(self.data) - self.offset

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ProtocolError(detail=f"Replay data ends inside a field at {self.offset}")
        chunk = self.data[self.offset : self.offset + size]
        self.offset += size
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def f32(self) -> float:
        value: float = struct.unpack("<f", self.take(4))[0]
        return value

    def pair(self) -> tuple[int, int]:
        return self.u32(), self.u32()

    def text(self) -> str:
        end = self.data.find(b"\x00", self.offset)
        if end < 0:
            raise ProtocolError(detail=f"Unterminated string at {self.offset}")
        value = self.data[self.offset : end].decode("utf-8", errors="replace")
        self.offset = end + 1
        return value

    def peek(self) -> int:
        return self.data[self.offset] if self.offset < len(self.data) else -1


def read_header(data: bytes) -> Header:
    """Decode the file header; refuse anything that is not a version-1 replay."""
    if not data.startswith(MAGIC):
        raise ProtocolError(detail="Not a Warcraft III replay")
    reader = _Reader(data, len(MAGIC))
    size, compressed, header_version = reader.u32(), reader.u32(), reader.u32()
    if header_version != 1:
        raise ProtocolError(detail=f"Unsupported replay header version {header_version}")
    decompressed, blocks = reader.u32(), reader.u32()
    product = reader.take(4)[::-1].decode("latin1")
    version, build, flags = reader.u32(), reader.u16(), reader.u16()
    return Header(
        header_size=size,
        compressed_size=compressed,
        header_version=header_version,
        decompressed_size=decompressed,
        blocks=blocks,
        product=product,
        version=version,
        build=build,
        multiplayer=bool(flags & _MULTIPLAYER),
        length_ms=reader.u32(),
        checksum=reader.u32(),
    )


def decompress(data: bytes, header: Header) -> bytes:
    """Concatenate the zlib blocks, cut to the header's decompressed size."""
    reforged = header.build >= REFORGED_BUILD
    reader = _Reader(data, header.header_size)
    parts: list[bytes] = []
    for _ in range(header.blocks):
        # Classic: u16 compressed, u16 decompressed, u32 checksum; Reforged: u32, u32, u32.
        compressed = reader.u32() if reforged else reader.u16()
        _ = reader.take(8 if reforged else 6)
        payload = reader.take(compressed)
        inflater = zlib.decompressobj()
        # Blocks end with a sync flush, not a final block; take everything produced.
        parts.append(inflater.decompress(payload) + inflater.flush())
    return b"".join(parts)[: header.decompressed_size]


def decode_stat_string(encoded: bytes) -> bytes:
    """Undo the lobby string's null-free encoding: a mask byte before every 7 bytes."""
    out = bytearray()
    for start in range(0, len(encoded), 8):
        mask = encoded[start]
        for index, byte in enumerate(encoded[start + 1 : start + 8]):
            out.append(byte if mask & (1 << (index + 1)) else byte - 1)
    return bytes(out)


def _player(reader: _Reader, *, host: bool) -> Player:
    player_id = reader.u8()
    name = reader.text()
    extra = reader.take(reader.u8())
    return Player(player_id=player_id, name=name, host=host, extra=extra)


def _game(reader: _Reader) -> Game:
    _ = reader.u32()  # 0x00000110
    if reader.u8() != 0:
        raise ProtocolError(detail="Replay does not start with the host record")
    host = _player(reader, host=True)
    game_name = reader.text()
    _ = reader.text()  # empty string
    start = reader.offset
    end = reader.data.index(b"\x00", start)
    settings = _Reader(decode_stat_string(reader.data[start:end]))
    reader.offset = end + 1
    speed = settings.u8()
    _ = settings.take(3)  # visibility/observers, teams together, fixed teams/shared/random
    _ = settings.take(5)  # w3gjs skips these five bytes before the map checksum
    map_checksum = settings.u32()
    map_path = settings.text()
    creator = settings.text()
    _ = settings.text()
    map_sha1 = settings.take(min(20, settings.left()))
    slot_count = reader.u32()
    game_type = reader.u8()
    private = reader.u8()
    _ = reader.u16()
    language = reader.u32()
    players = [host]
    while reader.peek() == Record.PLAYER:
        _ = reader.u8()
        players.append(_player(reader, host=False))
        _ = reader.u32()
    metadata: list[Metadata] = []
    while reader.peek() in (Record.REFORGED_38, Record.REFORGED_39):
        record, subtype = reader.u8(), reader.u8()
        metadata.append(Metadata(record, subtype, reader.take(reader.u32())))
    if reader.u8() != Record.START:
        raise ProtocolError(detail="Replay has no game start record")
    body = _Reader(reader.take(reader.u16()))
    count = body.u8()
    slots = tuple(_slot(body) for _ in range(count))
    return Game(
        host=host,
        game_name=game_name,
        speed=speed,
        map_checksum=map_checksum,
        map_path=map_path,
        creator=creator,
        map_sha1=map_sha1,
        slot_count=slot_count,
        game_type=game_type,
        private=private,
        language=language,
        players=tuple(players),
        metadata=tuple(metadata),
        slots=slots,
        random_seed=body.u32(),
        select_mode=body.u8(),
        start_spots=body.u8(),
    )


def _slot(reader: _Reader) -> Slot:
    """A 9-byte slot record (patch 1.07 and later)."""
    player, download, status, computer = reader.u8(), reader.u8(), reader.u8(), reader.u8()
    team, color, race, ai, handicap = (reader.u8() for _ in range(5))
    return Slot(player, download, status, computer == 1, team, color, race, ai, handicap)


def _order(reader: _Reader, base: Action) -> Action:
    flags, order = reader.u16(), reader.u32()
    _ = reader.pair()  # unknown A/B, 0xFFFFFFFF each
    action = replace(base, flags=flags, order=order)
    if base.action_id >= _ORDER_POINT:  # the point form and the forms extending it
        action = replace(action, x=reader.f32(), y=reader.f32())
    if base.action_id in (_ORDER_TARGET, _ORDER_ITEM):
        action = replace(action, target=reader.pair())
    if base.action_id == _ORDER_ITEM:
        action = replace(action, item=reader.pair())
    if base.action_id == _ORDER_TWO_POINTS:
        value = reader.u32()  # second order id
        _ = reader.take(9)
        second = f"{reader.f32()},{reader.f32()}"
        action = replace(action, value=value, texts=(second,))
    return action


def _selection(reader: _Reader, base: Action) -> Action:
    mode, count = reader.u8(), reader.u16()
    return replace(base, mode=mode, objects=tuple(reader.pair() for _ in range(count)))


def _mode_value(reader: _Reader, base: Action) -> Action:
    return replace(base, mode=reader.u8(), value=reader.u32())


def _mode_unit(reader: _Reader, base: Action) -> Action:
    return replace(base, mode=reader.u8(), unit=reader.pair())


def _transfer(reader: _Reader, base: Action) -> Action:
    return replace(base, mode=reader.u8(), value=reader.u32(), flags=reader.u32())


def _trigger_chat(reader: _Reader, base: Action) -> Action:
    value, flags = reader.pair()
    return replace(base, value=value, flags=flags, texts=(reader.text(),))


def _stored_integer(reader: _Reader, base: Action) -> Action:
    texts = (reader.text(), reader.text(), reader.text())
    return replace(base, texts=texts, value=reader.u32())


def _sync_data(reader: _Reader, base: Action) -> Action:
    texts = (reader.text(), reader.text())
    return replace(base, texts=texts, value=reader.u32())


def _unknown_78(reader: _Reader, base: Action) -> Action:
    unit, value, flags = reader.pair(), reader.u32(), reader.u32()
    return replace(base, unit=unit, value=value, flags=flags, mode=reader.u8())


def _order_unit(reader: _Reader, base: Action) -> Action:
    unit, ability, order = reader.pair(), reader.u32(), reader.u32()
    return replace(base, unit=unit, ability=ability, order=order)


_Decoder = Callable[[_Reader, Action], Action]
_DECODERS: Final[dict[int, _Decoder]] = {
    **dict.fromkeys(range(_ORDER_IMMEDIATE, _ORDER_TWO_POINTS + 1), _order),
    0x16: _selection,
    0x17: _selection,
    0x18: lambda reader, base: replace(base, mode=reader.u8(), value=reader.u8()),
    0x19: lambda reader, base: replace(base, order=reader.u32(), unit=reader.pair()),
    0x1B: _mode_unit,
    0x1C: _mode_unit,
    0x1D: lambda reader, base: replace(base, unit=reader.pair()),
    0x21: lambda reader, base: replace(base, unit=reader.pair()),
    0x1E: lambda reader, base: replace(base, mode=reader.u8(), order=reader.u32()),
    0x2D: _mode_value,
    0x50: _mode_value,
    0x03: lambda reader, base: replace(base, value=reader.u8()),
    0x75: lambda reader, base: replace(base, value=reader.u8()),
    0x06: lambda reader, base: replace(base, texts=(reader.text(),)),
    0x07: lambda reader, base: replace(base, value=reader.u32()),
    0x51: _transfer,
    0x60: _trigger_chat,
    0x62: lambda reader, base: replace(base, unit=reader.pair(), value=reader.u32()),
    0x68: lambda reader, base: replace(base, x=reader.f32(), y=reader.f32(), value=reader.u32()),
    0x6B: _stored_integer,
    0x77: _sync_data,
    0x78: _unknown_78,
    0x7A: _order_unit,
}


def _decode(reader: _Reader, base: Action) -> Action | None:
    """Decode one known action's fields, or None when the layout is unknown."""
    decoder = _DECODERS.get(base.action_id)
    if decoder is not None:
        return decoder(reader, base)
    size = _FIXED.get(base.action_id)
    if size is None:
        return None
    _ = reader.take(size)
    return base


def parse_actions(block: bytes, time_ms: int, player_id: int) -> list[Action]:
    """Decode an action block; the first unknown or malformed action keeps the rest raw."""
    reader = _Reader(block)
    actions: list[Action] = []
    while reader.left() > 0:
        start = reader.offset
        base = Action(time_ms, player_id, reader.u8(), b"")
        try:
            action = _decode(reader, base)
        except ProtocolError:
            action = None
        if action is None:
            actions.append(replace(base, raw=block[start:], decoded=False))
            break
        actions.append(replace(action, raw=block[start : reader.offset]))
    return actions


# Records kept as raw bytes: fixed sizes, or None for a u8 length prefix (checksum).
_OTHER_SIZES: Final[dict[int, int | None]] = {
    Record.FIRST_START: 4,
    Record.SECOND_START: 4,
    Record.THIRD_START: 4,
    Record.CHECKSUM: None,
    Record.UNKNOWN_23: 10,
    Record.FORCED_END: 8,
}


def _chat(reader: _Reader, time: int) -> Chat:
    player_id = reader.u8()
    body = _Reader(reader.take(reader.u16()))
    flags = body.u8()
    mode = body.u32() if flags == _CHAT_WITH_MODE else 0
    return Chat(time, player_id, flags, mode, body.text())


def _leave(reader: _Reader, time: int) -> Leave:
    reason, player_id = reader.u32(), reader.u8()
    return Leave(time, reason, player_id, reader.u32(), reader.u32())


def parse_records(data: bytes, offset: int, replay: Replay) -> None:
    """Walk the records after the game start record, filling ``replay``."""
    reader = _Reader(data, offset)
    time = 0
    while reader.left() > 0:
        start = reader.offset
        record = reader.u8()
        if record in (Record.TIME_SLOT, Record.TIME_SLOT_OLD):
            body = _Reader(reader.take(reader.u16()))
            time += body.u16()
            while body.left() > 0:
                player_id = body.u8()
                replay.actions.extend(parse_actions(body.take(body.u16()), time, player_id))
        elif record == Record.CHAT:
            replay.chats.append(_chat(reader, time))
        elif record == Record.LEAVE:
            replay.leaves.append(_leave(reader, time))
        elif record in _OTHER_SIZES:
            size = _OTHER_SIZES[record]
            raw = reader.take(reader.u8() if size is None else size)
            replay.others.append(Other(time, record, raw))
        else:
            # 0 is the zero padding of the last block; anything else is not understood.
            if record != 0 or any(data[reader.offset :]):
                replay.trailing = data[start:]
            break
    replay.end_ms = time


def parse(data: bytes) -> Replay:
    """Parse a whole replay file's bytes."""
    header = read_header(data)
    expanded = decompress(data, header)
    reader = _Reader(expanded)
    replay = Replay(header=header, game=_game(reader))
    parse_records(expanded, reader.offset, replay)
    return replay


def read(path: Path) -> Replay:
    """Parse a replay file (read only)."""
    return parse(path.read_bytes())


def is_rawcode(value: int) -> bool:
    """Whether a u32 order field holds a four-character rawcode rather than an order id."""
    return all(byte in _PRINTABLE for byte in value.to_bytes(4, "big")) and value != _NO_OBJECT


# --- Conversion of replay commands to catalog actions -------------------------------------

SELL_ABILITY: Final = rawcode("Asel")  # shop "Sell Units" button (0x7A ability id)
QUEUE_ABILITY: Final = rawcode("Aque")  # train/research queue button (0x7A ability id)
RANDOM_WISP_TYPE: Final = rawcode("e0IX")
EVOLUTION_WISP_TYPE: Final = rawcode("e0HP")
# Lobby selectors (war3map.j 46549-46585): u000-u005 difficulty 1-6 (AU..OU), u00C Isekai
# toggle LU, u006 start aU. The curriculum stage sets them in the simulator.
LOBBY: Final = {
    **{rawcode(f"u00{index}"): f"difficulty {index + 1}" for index in range(6)},
    rawcode("u00C"): "isekai toggle",
    rawcode("u006"): "start",
}
# Syncs the map sends by itself (login, save-code load), not player inputs.
MAP_SENT_SYNCS: Final = frozenset({"LogIn", "CODE_LOAD", "CODE_LADD", "CODE_SAVE"})
_SHOP_CODES: Final = {
    rawcode("h06F"): BasicAction.GAMBLE_LOW,
    rawcode("H0AZ"): BasicAction.GAMBLE_HIGH,
    rawcode("H0B0"): BasicAction.CREATE_RARE,
    **{research.raw_id: kind for kind, research in RESEARCH.items()},
}
_BASIC_RECTS: Final = {"fY", "CY", "aY", "EY"}
NEAR_PAD: Final = 384.0  # map units around a pad rect that still count as walking onto it
_STORY_RECT: Final = "nP[0]"
_RECT: Final = re.compile(
    r"^set (\w+)=Rect\((-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)\)$", re.MULTILINE
)
_ALIAS: Final = re.compile(r"^set (\w+\[\d+\])=(\w+)$", re.MULTILINE)
_FMV: Final = re.compile(r'call FMv\("([^"]*)",(?:true|false),(\w+)\)')
_RECIPE_HANDLER: Final = re.compile(r'call SaveStr\(ORDAI_data, 1, \$([0-9a-f]{8}), "(\w+)"\)')
_CODE_VARIABLE_LINE: Final = re.compile(r"^set (\w+)=function (\w+)$", re.MULTILINE)
_MICRO_ORDERS: Final = frozenset(
    {"smart", "move", "attack", "holdposition", "stop", "patrol", "attackground"}
)
_ORDER_IDS: Final = {
    851971: "smart",
    851972: "stop",
    851983: "attack",
    851986: "move",
    851990: "patrol",
    851993: "holdposition",
}
_NO_TARGET: Final = (_NO_OBJECT, _NO_OBJECT)

Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Tables:
    """Where each replay command form lands in the catalog (catalog indices)."""

    fusions: dict[int, int]  # recipe ability rawcode -> index
    inputs: dict[tuple[ActionKind, int], int]  # (sell/train/research/ability/select, raw) -> index
    casters: dict[int, tuple[int, ...]]  # ability rawcode -> caster unit types (may be empty)
    shop: dict[int, int]  # sold/researched rawcode of codes 8-13 -> index
    syncs: dict[tuple[str, int], int]  # (prefix, payload) -> index
    chats: dict[str, int | None]  # chat text -> index (chat recipes and chat inputs)
    rects: dict[str, Box]
    regions: tuple[tuple[str, int, int], ...]  # (rect, wisp type or 0 for a fighter, index)
    basics: dict[int, int]  # basic code -> index


def _rects(script: str, names: list[str]) -> dict[str, Box | None]:
    """``set X=Rect(...)`` rectangles by name; ``X[0]`` follows ``set X[0]=Y`` first."""
    rects = {
        str(match.group(1)): (
            float(match.group(2)),
            float(match.group(3)),
            float(match.group(4)),
            float(match.group(5)),
        )
        for match in _RECT.finditer(script)
    }
    aliases = {str(match.group(1)): str(match.group(2)) for match in _ALIAS.finditer(script)}
    return {name: rects.get(aliases.get(name, name)) for name in names}


def _chat_texts(script: str) -> dict[str, list[str]]:
    """Chat texts registered with FMv, by handler function."""
    variables = {
        str(match.group(1)): str(match.group(2)) for match in _CODE_VARIABLE_LINE.finditer(script)
    }
    texts: dict[str, list[str]] = {}
    for match in _FMV.finditer(script):
        handler = variables.get(str(match.group(2)), str(match.group(2)))
        texts.setdefault(handler, []).append(str(match.group(1)))
    return texts


def tables(data: MapData, bridge_actions: str) -> Tables:
    """Index the map's inputs and the bridge's chat-recipe handlers by their replay forms."""
    index_of = {action.code: index for index, action in enumerate(data.catalog.actions)}
    fusions = {
        action.code: index
        for index, action in enumerate(data.catalog.actions)
        if action.kind is ActionKind.FUSION
    }
    inputs: dict[tuple[ActionKind, int], int] = {}
    casters: dict[int, tuple[int, ...]] = {}
    syncs: dict[tuple[str, int], int] = {}
    chats: dict[str, int | None] = {}
    regions: list[tuple[str, int, int]] = []
    texts = _chat_texts(data.script)
    for item in data.inputs:
        index = index_of[item.action.code]
        route = item.route
        match route.kind:
            case ActionKind.SYNC:
                syncs[route.prefix, route.payload] = index
            case ActionKind.CHAT:
                for text in texts.get(route.handler, []):
                    chats[text] = index
            case ActionKind.REGION:
                regions.append((route.rect, route.raw_id, index))
            case _:
                inputs[route.kind, route.raw_id] = index
                if route.kind is ActionKind.ABILITY:
                    casters[route.raw_id] = item.action.casters
    for match in _RECIPE_HANDLER.finditer(bridge_actions):
        code = int(match.group(1), 16)
        chats.update(dict.fromkeys(texts.get(str(match.group(2)), []), fusions.get(code)))
    needed = [*_BASIC_RECTS, _STORY_RECT, *(rect for rect, _, _ in regions)]
    resolved = _rects(data.script, needed)
    return Tables(
        fusions=fusions,
        inputs=inputs,
        casters=casters,
        shop={raw: index_of[int(code)] for raw, code in _SHOP_CODES.items()},
        syncs=syncs,
        chats=chats,
        rects={name: box for name, box in resolved.items() if box is not None},
        regions=tuple(regions),
        basics={int(code): index_of[int(code)] for code in BasicAction},
    )


@dataclass(frozen=True, slots=True)
class Command:
    """A player command of the replay and the catalog action it maps to (None: unmapped)."""

    time_ms: int
    kind: str  # replay-side kind, e.g. "fusion", "shop", "move", "unit-order"
    detail: str  # readable raw form
    index: int | None
    raw: bytes


@dataclass(slots=True)
class Conversion:
    """Every command of one player, plus what the replay says outside the commands."""

    commands: list[Command] = field(default_factory=list[Command])
    lobby: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    map_syncs: int = 0
    ui_actions: int = 0  # selections, hotkeys, subgroups
    unknown: list[Action] = field(default_factory=list[Action])
    types: dict[tuple[int, int], int] = field(default_factory=dict[tuple[int, int], int])
    selected_objects: int = 0
    typed_selected_objects: int = 0


def _name(value: int) -> str:
    if is_rawcode(value):
        return rawtext(value)
    return _ORDER_IDS.get(value, str(value))


def _distance(box: Box, x: float, y: float) -> float:
    return math.hypot(max(box[0] - x, 0.0, x - box[2]), max(box[1] - y, 0.0, y - box[3]))


def _is_hero(raw_id: int) -> bool:
    return raw_id.to_bytes(4, "big")[:1] in (b"h", b"H")


def _move(
    tables: Tables, types: list[int | None], x: float, y: float
) -> list[tuple[str, int | None]]:
    """A point order of the current selection: a pad, a basic rect, or plain micro.

    Players click on or next to a pad; the unit enters the rect on its way. A point outside
    every rect but within ``NEAR_PAD`` of one the selection can use counts for the nearest.
    Untyped units selected together with a random wisp are counted as random wisps (only
    wisps have a use for the summon pads). A selection that also holds the evolution wisp
    gives a second command, evolve, since one bridge step carries one action.
    """
    known = [raw for raw in types if raw is not None]
    untyped = len(types) - len(known)
    wisps = known.count(RANDOM_WISP_TYPE)
    wisps += untyped if wisps else 0
    heroes = any(_is_hero(raw) for raw in known)
    wanted: list[tuple[str, bool, int, str]] = [
        (rect, (wisp in known) if wisp else heroes, index, f"region {rect}")
        for rect, wisp, index in tables.regions
    ]
    for rect, usable, code in (
        ("fY", wisps > 1, BasicAction.SUMMON_ALL),
        ("fY", wisps == 1, BasicAction.SUMMON),
        ("fY", EVOLUTION_WISP_TYPE in known, BasicAction.EVOLVE),
        ("CY", wisps > 0, BasicAction.LUMBER),
        ("aY", wisps > 0, BasicAction.MANA),
        (_STORY_RECT, heroes, BasicAction.STORY),
        ("EY", heroes, BasicAction.LINE),
    ):
        wanted.append((rect, usable, tables.basics[int(code)], f"basic {int(code)} ({rect})"))
    best: tuple[float, str, int] | None = None
    for rect, usable, index, label in wanted:
        box = tables.rects.get(rect)
        if box is None or not usable:
            continue
        distance = _distance(box, x, y)
        if distance <= NEAR_PAD and (best is None or distance < best[0]):
            best = (distance, label if distance == 0 else f"{label} near", index)
    if best is None:
        return [(f"move{' (untyped selection)' if untyped else ''}", None)]
    found: list[tuple[str, int | None]] = [(best[1], best[2])]
    summons = {tables.basics[int(BasicAction.SUMMON)], tables.basics[int(BasicAction.SUMMON_ALL)]}
    if best[2] in summons and EVOLUTION_WISP_TYPE in known:
        found.append((f"basic {int(BasicAction.EVOLVE)} (fY)", tables.basics[BasicAction.EVOLVE]))
    return found


def _purchase(tables: Tables, ability: int, order: int) -> tuple[str, int | None]:
    index = tables.shop.get(order)
    if index is not None:
        return "shop", index
    kind = "sell" if ability == SELL_ABILITY else "train/research"
    for key in (
        (ActionKind.SELL, order),
        (ActionKind.TRAIN, order),
        (ActionKind.RESEARCH, order),
    ):
        if key in tables.inputs:
            return kind, tables.inputs[key]
    return kind, None


def _button(tables: Tables, action: Action, types: dict[tuple[int, int], int]) -> Command:
    """A 0x7A command-card button: shop/queue purchases, recipes, abilities, unit orders."""
    ability, order = action.ability, action.order
    detail = f"{_name(ability) if ability else '-'} {_name(order)}"
    kind, index = "unit-order", None
    if ability in (SELL_ABILITY, QUEUE_ABILITY):
        kind, index = _purchase(tables, ability, order)
    elif ability in tables.fusions:
        kind, index = "fusion", tables.fusions[ability]
    elif (ActionKind.ABILITY, ability) in tables.inputs:
        kind, index = "ability", tables.inputs[ActionKind.ABILITY, ability]
        caster = types.get(action.unit) if action.unit is not None else None
        allowed = tables.casters.get(ability, ())
        if caster is not None and allowed and caster not in allowed:
            kind, index = "ability (other caster)", None
    elif ability:
        kind = "ability (not in catalog)"
    return Command(action.time_ms, kind, detail, index, action.raw)


def human_player(replay: Replay) -> int:
    """The replay player id of the only human, who must sit in slot 0 (Player(0))."""
    humans = [
        (position, slot)
        for position, slot in enumerate(replay.game.slots)
        if slot.status == _SLOT_USED and not slot.computer
    ]
    if len(humans) != 1 or humans[0][0] != 0:
        raise ProtocolError(detail="Only solo replays with the human in slot 0 are supported")
    return humans[0][1].player_id


def object_types(replay: Replay) -> dict[tuple[int, int], int]:
    """Unit types the replay itself names: 0x19 subgroup records carry (type, object).

    A replay has no handle table. The only type information is the active subgroup's unit
    type of every selection change, so an object's type is known once the player has
    selected it alone or as the first of its subgroup; other objects stay untyped.
    """
    return {
        action.unit: action.order
        for action in replay.actions
        if action.action_id == _SUBGROUP and action.unit is not None
    }


def _order_commands(
    tables: Tables,
    action: Action,
    selection: list[tuple[int, int]],
    types: dict[tuple[int, int], int],
) -> list[Command]:
    """A 0x10-0x14 order without a preceding button record."""
    name = _name(action.order)
    if is_rawcode(action.order):
        kind, index = _purchase(tables, 0, action.order)
        return [Command(action.time_ms, kind, name, index, action.raw)]
    if (
        action.x is not None
        and action.y is not None
        and action.target in (None, _NO_TARGET)
        and name in ("smart", "move", "attack")
    ):
        detail = f"{name} ({action.x:.0f},{action.y:.0f})"
        moves = _move(tables, [types.get(unit) for unit in selection], action.x, action.y)
        return [Command(action.time_ms, kind, detail, index, action.raw) for kind, index in moves]
    kind = "unit-order" if name in _MICRO_ORDERS else "order"
    target = " on object" if action.target not in (None, _NO_TARGET) else ""
    return [Command(action.time_ms, kind, f"{name}{target}", None, action.raw)]


def _selection_changed(
    action: Action, selection: list[tuple[int, int]], groups: dict[int, list[tuple[int, int]]]
) -> None:
    if action.action_id == _SELECT:
        if action.mode == 1:
            selection.extend(unit for unit in action.objects if unit not in selection)
        else:
            selection[:] = [unit for unit in selection if unit not in action.objects]
    elif action.action_id == _GROUP_ASSIGN:
        groups[action.mode] = list(action.objects)
    elif action.action_id == _GROUP_SELECT:
        selection[:] = list(groups.get(action.mode, []))


def _other_command(tables: Tables, action: Action, conversion: Conversion) -> Command | None:
    """Sync data, trigger chat, ESC and queue cancels; None for map-sent syncs."""
    match action.action_id:
        case 0x77:
            prefix, payload = action.texts
            if prefix in MAP_SENT_SYNCS:
                conversion.map_syncs += 1
                return None
            key = (prefix, int(payload)) if payload.isdigit() else None
            index = tables.syncs.get(key) if key is not None else None
            return Command(action.time_ms, "sync", f"{prefix} {payload}", index, action.raw)
        case 0x60:
            text = action.texts[0]
            return Command(action.time_ms, "chat", text, tables.chats.get(text), action.raw)
        case 0x61:
            return Command(action.time_ms, "escape", "ESC", None, action.raw)
        case 0x1E:
            detail = f"cancel {_name(action.order)}"
            return Command(action.time_ms, "dequeue", detail, None, action.raw)
        case _:
            conversion.unknown.append(action)
            return None


def _selected(tables: Tables, action: Action, conversion: Conversion) -> None:
    """Units added to the selection: lobby selectors and units with a selection trigger."""
    types = conversion.types
    conversion.selected_objects += len(action.objects)
    conversion.typed_selected_objects += sum(unit in types for unit in action.objects)
    for unit in action.objects:
        raw = types.get(unit)
        if raw in LOBBY:
            conversion.lobby.append((action.time_ms, LOBBY[raw]))
        elif raw is not None and (ActionKind.SELECT, raw) in tables.inputs:
            index = tables.inputs[ActionKind.SELECT, raw]
            conversion.commands.append(
                Command(action.time_ms, "select", rawtext(raw), index, action.raw)
            )


def convert(replay: Replay, tables: Tables) -> Conversion:
    """Map every command of the human player to a catalog index where the map allows."""
    player = human_player(replay)
    conversion = Conversion(types=object_types(replay))
    types = conversion.types
    selection: list[tuple[int, int]] = []
    groups: dict[int, list[tuple[int, int]]] = {}
    button: Action | None = None
    for action in replay.actions:
        if action.player_id != player:
            continue
        if button is not None and _ORDER_IMMEDIATE <= action.action_id <= _ORDER_TWO_POINTS:
            same = action.time_ms == button.time_ms and action.order == button.order
            button = None
            if same:
                continue  # the order a 0x7A button record announced
        button = None
        if action.action_id in _UI_ACTIONS:
            conversion.ui_actions += 1
            _selection_changed(action, selection, groups)
            if action.action_id == _SELECT and action.mode == 1:
                _selected(tables, action, conversion)
            continue
        if action.action_id == _BUTTON:
            button = action
            conversion.commands.append(_button(tables, action, types))
        elif _ORDER_IMMEDIATE <= action.action_id <= _ORDER_TWO_POINTS:
            conversion.commands.extend(_order_commands(tables, action, selection, types))
        else:
            command = _other_command(tables, action, conversion)
            if command is not None:
                conversion.commands.append(command)
    return conversion


def lobby_stage(conversion: Conversion) -> Stage:
    """The difficulty the player picked; Isekai is on after an odd number of toggles.

    Toggle clicks after the start click are counted too (the map's l4s ignores them once
    the game started, which a replay cannot show), so the Isekai choice is a best reading.
    """
    difficulty = Difficulty.EASY
    toggles = 0
    for _, choice in conversion.lobby:
        if choice.startswith("difficulty "):
            difficulty = Difficulty(int(choice.removeprefix("difficulty ")))
        elif choice == "isekai toggle":
            toggles += 1
        elif choice == "start":
            break
    return Stage(difficulty=difficulty, isekai=toggles % 2 == 1 and difficulty >= ISEKAI_FLOOR)


# --- Replaying the converted commands in the simulator ------------------------------------

EXTRA_SECONDS: Final = 120.0  # keep simulating this long after the replay ends


@dataclass(frozen=True, slots=True)
class Step:
    """One simulator step: the state it was chosen on, the index sent, and the outcome."""

    time: float  # simulator game time of ``state``
    state: GameState
    index: int
    command: Command | None
    accepted: bool


@dataclass(slots=True)
class Reconstruction:
    """The simulator run driven by a replay's mapped commands."""

    steps: list[Step] = field(default_factory=list[Step])
    final: GameState | None = None
    final_time: float = 0.0
    unsent: int = 0


def _simulated(response: dict[str, object]) -> tuple[GameState, float]:
    text = response.get("state_text")
    time = response.get("game_time")
    state = parse_snapshot(text) if isinstance(text, str) else None
    if state is None or not isinstance(time, int | float):
        raise ProtocolError(detail="Simulator response has no complete state and game time")
    return state, float(time)


def reconstruct(
    commands: list[Command], catalog: Catalog, server: SimServer, seed: int, end_ms: int
) -> Reconstruction:
    """Send each mapped command at the first step at or after its replay time.

    A step is about 1.25 s of game time and carries one action, so commands of a burst are
    sent one per step in replay order (their delay is ``step.time - command.time_ms``).
    """
    pending = deque(command for command in commands if command.index is not None)
    state, time = _simulated(server.request({"op": "reset", "env": 0, "seed": seed}))
    result = Reconstruction()
    while not state.terminal and time <= end_ms / 1000 + EXTRA_SECONDS:
        command = pending.popleft() if pending and pending[0].time_ms <= time * 1000 else None
        index = command.index if command is not None and command.index is not None else 0
        code = catalog.actions[index].code
        after, after_time = _simulated(server.request({"op": "step", "env": 0, "action": code}))
        result.steps.append(Step(time, state, index, command, after.action_ok == 1))
        state, time = after, after_time
    result.final, result.final_time, result.unsent = state, time, len(pending)
    return result


def presence(replay: Replay, run: Reconstruction, catalog: Catalog) -> list[tuple[int, int, bool]]:
    """For every own-unit type the player selected: (time ms, type, present in the sim then).

    The sim state compared is the first one at or after the selection time.
    """
    player = human_player(replay)
    units = {unit.raw_id for unit in catalog.units}
    times = [step.time * 1000 for step in run.steps]
    found: list[tuple[int, int, bool]] = []
    for action in replay.actions:
        if action.player_id != player or action.action_id != _SUBGROUP:
            continue
        raw = action.order
        if raw not in units or not _is_hero(raw) or not run.steps:
            continue
        position = bisect_left(times, action.time_ms)
        if position >= len(run.steps):
            break
        counts = {unit.raw_id: unit.count for unit in run.steps[position].state.units}
        found.append((action.time_ms, raw, counts.get(raw, 0) > 0))
    return found
