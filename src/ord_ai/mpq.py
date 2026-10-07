"""Read and replace members while preserving Warcraft's native archive layout."""

from __future__ import annotations

import bz2
import struct
import zlib
from dataclasses import dataclass
from typing import Final, override

from ord_ai.mpq_crypto import decrypt, encrypt, hash_string
from ord_ai.mpq_tables import Archive as _Archive
from ord_ai.mpq_tables import block_entry as _block
from ord_ai.mpq_tables import hash_entry as _hash_entry
from ord_ai.mpq_tables import read_u16 as _u16
from ord_ai.mpq_tables import read_u32 as _u32

_MAGIC: Final = b"MPQ\x1a"
_HEADER_SIZE: Final = 32
_BLOCK_SIZE: Final = 16
_HASH_SIZE: Final = 16
_SECTOR_TABLE_SIZE: Final = 8
_PROTECTED_HEADER_SIZE: Final = 2_097_410
_PROTECTED_SECTOR_SHIFT: Final = 21
_EXISTS: Final = 0x80000000
_SINGLE_UNIT: Final = 0x01000000
_ENCRYPTED: Final = 0x00010000
_COMPRESSED: Final = 0x00000200
_NORMAL_FLAGS: Final = _EXISTS | _SINGLE_UNIT | _ENCRYPTED | _COMPRESSED
_ZLIB_MARKER: Final = 0x02
_BZIP2_MARKER: Final = 0x10
_TRUE_BLOCKS: Final = {
    "WAR3MAP.J": 4315,
    "WAR3MAP.WTS": 1,
    "WAR3MAP.W3I": 1752,
    "WAR3MAP.W3U": 1759,
    "WAR3MAP.W3A": 1753,
    "WAR3MAP.W3Q": 1757,
}


@dataclass(frozen=True, slots=True)
class MpqError(Exception):
    """An invalid or unsupported MPQ archive condition."""

    reason: str

    @override
    def __str__(self) -> str:
        """Return the archive error description."""
        return self.reason


def _bounded_slice(source: bytes, offset: int, size: int, label: str) -> bytes:
    end = offset + size
    if offset < 0 or size < 0 or end > len(source):
        raise MpqError(reason=f"{label} lies outside the MPQ source")
    return source[offset:end]


def _parse(source: bytes) -> _Archive:
    base = source.find(_MAGIC)
    if base < 0 or base + _HEADER_SIZE > len(source):
        raise MpqError(reason="MPQ archive header not found")
    header_size = _u32(source, base + 4)
    version = _u16(source, base + 12)
    sector_shift = _u16(source, base + 14)
    hash_offset = _u32(source, base + 16)
    block_offset = _u32(source, base + 20)
    hash_count = _u32(source, base + 24)
    block_count = _u32(source, base + 28)
    hash_bytes = _bounded_slice(source, base + hash_offset, hash_count * _HASH_SIZE, "hash table")
    block_bytes = _bounded_slice(
        source, base + block_offset, block_count * _BLOCK_SIZE, "block table"
    )
    return _Archive(
        base=base,
        sector_shift=sector_shift,
        hash_offset=hash_offset,
        block_offset=block_offset,
        hash_count=hash_count,
        block_count=block_count,
        protected=(
            version == 1
            and header_size == _PROTECTED_HEADER_SIZE
            and sector_shift == _PROTECTED_SECTOR_SHIFT
        ),
        hashes=decrypt(hash_bytes, hash_string("(hash table)", 3)),
        blocks=decrypt(block_bytes, hash_string("(block table)", 3)),
    )


def _file_key(name: str) -> int:
    normalized = name.replace("/", "\\")
    return hash_string(normalized.rsplit("\\", 1)[-1], 3)


def _decompress(payload: bytes, expected_size: int) -> bytes:
    if len(payload) >= expected_size:
        return payload[:expected_size]
    if not payload:
        raise MpqError(reason="compressed MPQ member has no compression marker")
    try:
        if payload[0] == _ZLIB_MARKER:
            content = zlib.decompress(payload[1:])
        elif payload[0] == _BZIP2_MARKER:
            content = bz2.decompress(payload[1:])
        else:
            raise MpqError(reason=f"unsupported MPQ compression marker: 0x{payload[0]:02x}")
    except (OSError, zlib.error) as error:
        raise MpqError(reason="invalid compressed MPQ member") from error
    if len(content) != expected_size:
        raise MpqError(reason="decompressed MPQ member has an invalid size")
    return content


def _read_block(source: bytes, archive: _Archive, name: str, index: int) -> bytes:
    offset, archived_size, file_size, flags = _block(archive.blocks, index)
    if flags & _EXISTS == 0:
        raise MpqError(reason="MPQ member block is not marked as existing")
    payload = _bounded_slice(source, archive.base + offset, archived_size, "member payload")
    key = _file_key(name)
    if archive.protected:
        if len(payload) < _SECTOR_TABLE_SIZE:
            raise MpqError(reason="protected MPQ member lacks a sector table")
        offsets = decrypt(payload[:_SECTOR_TABLE_SIZE], (key - 1) & 0xFFFFFFFF)
        first, last = _u32(offsets, 0), _u32(offsets, 4)
        if first != _SECTOR_TABLE_SIZE or last != archived_size:
            raise MpqError(reason="protected MPQ member has invalid sector offsets")
        payload = payload[first:last]
    if flags & _ENCRYPTED:
        payload = decrypt(payload, key)
    if flags & _COMPRESSED:
        return _decompress(payload, file_size)
    return payload[:file_size]


def _matching_hash_indexes(archive: _Archive, name: str) -> tuple[int, ...]:
    hash_a, hash_b = hash_string(name, 1), hash_string(name, 2)
    matches: list[int] = []
    for index in range(archive.hash_count):
        entry_a, entry_b, _locale, _platform, _block_index = _hash_entry(archive.hashes, index)
        if entry_a == hash_a and entry_b == hash_b:
            matches.append(index)
    return tuple(matches)


def _member_block(source: bytes, archive: _Archive, name: str) -> tuple[int, bytes]:
    preferred = _TRUE_BLOCKS.get(name.replace("/", "\\").upper())
    candidates = [
        _hash_entry(archive.hashes, index)[4] for index in _matching_hash_indexes(archive, name)
    ]
    if archive.protected and preferred is not None:
        candidates.insert(0, preferred)
    attempted: set[int] = set()
    for block_index in candidates:
        if block_index >= archive.block_count or block_index in attempted:
            continue
        attempted.add(block_index)
        try:
            return block_index, _read_block(source, archive, name, block_index)
        except MpqError:
            continue
    raise MpqError(reason=f"MPQ member not found or unreadable: {name}")


def read_member(source: bytes, name: str) -> bytes:
    """Read a named member from a normal or supplied protected MPQ archive."""
    archive = _parse(source)
    return _member_block(source, archive, name)[1]


def replace_member(source: bytes, name: str, content: bytes) -> bytes:
    """Replace one member without normalizing the engine-specific archive."""
    archive = _parse(source)
    block_index, _ = _member_block(source, archive, name)
    blocks = bytearray(archive.blocks)
    compressed = b"\x02" + zlib.compress(content, level=9)
    plain_payload = compressed if len(compressed) < len(content) else content
    key = _file_key(name)
    payload = encrypt(plain_payload, key)
    if archive.protected:
        offsets = struct.pack("<II", _SECTOR_TABLE_SIZE, len(payload) + _SECTOR_TABLE_SIZE)
        payload = encrypt(offsets, (key - 1) & 0xFFFFFFFF) + payload
    result = bytearray(source)
    replacement_offset = len(result) - archive.base
    result.extend(payload)
    struct.pack_into(
        "<IIII",
        blocks,
        block_index * _BLOCK_SIZE,
        replacement_offset,
        len(payload),
        len(content),
        _NORMAL_FLAGS,
    )
    encrypted_blocks = encrypt(bytes(blocks), hash_string("(block table)", 3))
    block_start = archive.base + archive.block_offset
    result[block_start : block_start + len(encrypted_blocks)] = encrypted_blocks
    struct.pack_into("<I", result, archive.base + 8, len(result) - archive.base)
    return bytes(result)
