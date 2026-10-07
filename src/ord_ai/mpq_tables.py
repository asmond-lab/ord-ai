"""Immutable MPQ table metadata and binary field decoding."""

from dataclasses import dataclass
from typing import Final

ENTRY_SIZE: Final = 16


@dataclass(frozen=True, slots=True)
class Archive:
    """Decoded archive table locations and protection mode."""

    base: int
    sector_shift: int
    hash_offset: int
    block_offset: int
    hash_count: int
    block_count: int
    protected: bool
    hashes: bytes
    blocks: bytes


def read_u16(data: bytes | bytearray, offset: int) -> int:
    """Decode one little-endian unsigned 16-bit field."""
    return int.from_bytes(data[offset : offset + 2], "little")


def read_u32(data: bytes | bytearray, offset: int) -> int:
    """Decode one little-endian unsigned 32-bit field."""
    return int.from_bytes(data[offset : offset + 4], "little")


def block_entry(data: bytes | bytearray, index: int) -> tuple[int, int, int, int]:
    """Decode offset, compressed size, original size and flags."""
    offset = index * ENTRY_SIZE
    return (
        read_u32(data, offset),
        read_u32(data, offset + 4),
        read_u32(data, offset + 8),
        read_u32(data, offset + 12),
    )


def hash_entry(data: bytes | bytearray, index: int) -> tuple[int, int, int, int, int]:
    """Decode the two hashes, locale, platform and block index."""
    offset = index * ENTRY_SIZE
    return (
        read_u32(data, offset),
        read_u32(data, offset + 4),
        read_u16(data, offset + 8),
        read_u16(data, offset + 10),
        read_u32(data, offset + 12),
    )
