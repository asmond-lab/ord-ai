"""Read the supplied map's object modification files (war3map.w3u/w3a/w3q/w3t).

Only fields the map changes are stored in these files; everything else keeps the base
object's game default. Leveled files (abilities, upgrades) key each value by level.
"""

import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final, cast

from ord_ai.protocol import ProtocolError

Value = int | float | str
_VERSION: Final = 2
_RAWCODE_LENGTH: Final = 4
_TRIGGER_STRING: Final = re.compile(r"TRIGSTR_0*(\d+)")
_STRING_ENTRY: Final = re.compile(r"STRING (\d+)\n(?://[^\n]*\n)?\{\n(.*?)\n\}", re.DOTALL)
_COLOR: Final = re.compile(r"\|c[0-9a-f]{8}|\|r", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class GameObject:
    """One custom or modified object: its base rawcode and changed fields."""

    base: str
    fields: dict[tuple[str, int], Value] = field(default_factory=dict[tuple[str, int], Value])

    def get(self, name: str, level: int = 0) -> Value | None:
        """Return a changed field, or None when the map keeps the base default."""
        return self.fields.get((name, level))

    def text(self, name: str, level: int = 0) -> str:
        """Return a changed string field, or an empty string."""
        value = self.get(name, level)
        return value if isinstance(value, str) else ""

    def number(self, name: str, level: int = 0) -> float | None:
        """Return a changed numeric field, or None when the map keeps the default."""
        value = self.get(name, level)
        return None if isinstance(value, str) or value is None else float(value)

    def rawcodes(self, name: str) -> tuple[str, ...]:
        """Split a comma-separated rawcode list such as uabi, useu or ures."""
        return tuple(code for code in self.text(name).split(",") if len(code) == _RAWCODE_LENGTH)


ObjectTable = dict[str, GameObject]


@dataclass(frozen=True, slots=True)
class MapObjects:
    """The map's unit, ability, upgrade and item modifications."""

    units: ObjectTable
    abilities: ObjectTable
    upgrades: ObjectTable
    items: ObjectTable


@dataclass(frozen=True, slots=True)
class FieldEntry:
    """One stored field; ``start``/``end`` delimit its value bytes (a string without its NUL)."""

    key: str
    base: str
    name: str
    level: int
    kind: int
    value: Value
    start: int
    end: int


def iter_fields(data: bytes, *, leveled: bool) -> Iterator[FieldEntry]:
    """Walk one version-2 object modification file field by field, in file order."""
    offset = 0

    def take(fmt: str) -> int:
        nonlocal offset
        value = cast("int", struct.unpack_from(fmt, data, offset)[0])
        offset += struct.calcsize(fmt)
        return value

    if take("<i") != _VERSION:
        raise ProtocolError(detail="Unsupported object modification file version")
    for _table in range(2):  # original objects, then custom objects
        for _object in range(take("<i")):
            original, custom = data[offset : offset + 4], data[offset + 4 : offset + 8]
            offset += 8
            key = (custom if custom != b"\0\0\0\0" else original).decode("latin1")
            for _field in range(take("<i")):
                name = data[offset : offset + 4].decode("latin1")
                offset += 4
                kind = take("<i")
                level = take("<i") if leveled else 0
                if leveled:
                    _ = take("<i")  # data pointer: which DataA..DataI column
                start = offset
                value: Value
                if kind == 0:
                    value = take("<i")
                    end = offset
                elif kind in (1, 2):
                    value = float(struct.unpack_from("<f", data, offset)[0])
                    offset += 4
                    end = offset
                else:
                    end = data.index(b"\0", offset)
                    value = data[offset:end].decode("utf-8", "replace")
                    offset = end + 1
                offset += 4  # end marker (the object's rawcode)
                yield FieldEntry(
                    key, original.decode("latin1"), name, level, kind, value, start, end
                )
    if offset != len(data):
        raise ProtocolError(detail="Trailing bytes after the object modification tables")


def parse_objects(data: bytes, *, leveled: bool) -> ObjectTable:
    """Parse one version-2 object modification file into objects keyed by rawcode."""
    objects: ObjectTable = {}
    for entry in iter_fields(data, leveled=leveled):
        stored = objects.setdefault(entry.key, GameObject(base=entry.base))
        stored.fields[entry.name, entry.level] = entry.value
    return objects


def read_strings(text: str) -> dict[int, str]:
    """Map war3map.wts string numbers to their text, without color codes."""
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n")
    return {
        int(match.group(1)): _COLOR.sub("", match.group(2))
        for match in _STRING_ENTRY.finditer(normalized)
    }


def resolve(value: str, strings: dict[int, str]) -> str:
    """Replace a TRIGSTR_n reference with its string; color codes are removed."""
    match = _TRIGGER_STRING.fullmatch(value.strip())
    return strings.get(int(match.group(1)), value) if match else _COLOR.sub("", value)


def rawcode(code: str) -> int:
    """Encode a four-character rawcode as the integer the game uses."""
    return int.from_bytes(code.encode("latin1"), "big")


def rawtext(value: int) -> str:
    """Decode an integer rawcode back to its four characters."""
    return value.to_bytes(4, "big").decode("latin1")
