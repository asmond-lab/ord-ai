"""Storm MPQ hashing and word-oriented encryption primitives."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final, override

_MASK: Final = 0xFFFFFFFF
_MAX_HASH_TYPE: Final = 4


@dataclass(frozen=True, slots=True)
class MpqCryptoError(Exception):
    """An invalid request to an MPQ cryptographic primitive."""

    reason: str

    @override
    def __str__(self) -> str:
        """Return the crypto error description."""
        return self.reason


def _make_crypt_table() -> tuple[int, ...]:
    table = [0] * 0x500
    seed = 0x00100001
    for low_index in range(0x100):
        for table_index in range(low_index, 0x500, 0x100):
            seed = (seed * 125 + 3) % 0x2AAAAB
            high = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            table[table_index] = high | (seed & 0xFFFF)
    return tuple(table)


_CRYPT_TABLE: Final = _make_crypt_table()


def hash_string(value: str, hash_type: int) -> int:
    """Return the Storm MPQ hash for an archive name or table key."""
    if not 0 <= hash_type <= _MAX_HASH_TYPE:
        raise MpqCryptoError(reason=f"invalid MPQ hash type: {hash_type}")
    seed_one = 0x7FED7FED
    seed_two = 0xEEEEEEEE
    normalized = value.replace("/", "\\").upper().encode("utf-8")
    for character in normalized:
        seed_one = (_CRYPT_TABLE[(hash_type << 8) + character] ^ (seed_one + seed_two)) & _MASK
        seed_two = (character + seed_one + seed_two + (seed_two << 5) + 3) & _MASK
    return seed_one


def _transform(data: bytes, key: int, *, decrypting: bool) -> bytes:
    transformed = bytearray(data)
    seed_one = key & _MASK
    seed_two = 0xEEEEEEEE
    for offset in range(0, len(data) - 3, 4):
        source_word = struct.unpack_from("<I", data, offset)[0]
        seed_two = (seed_two + _CRYPT_TABLE[0x400 + (seed_one & 0xFF)]) & _MASK
        transformed_word = (source_word ^ (seed_one + seed_two)) & _MASK
        struct.pack_into("<I", transformed, offset, transformed_word)
        plain_word = transformed_word if decrypting else source_word
        seed_one = ((((~seed_one) & _MASK) << 21) + 0x11111111 | (seed_one >> 11)) & _MASK
        seed_two = (plain_word + seed_two + (seed_two << 5) + 3) & _MASK
    return bytes(transformed)


def encrypt(data: bytes, key: int) -> bytes:
    """Encrypt complete 32-bit words and preserve a partial trailing word."""
    return _transform(data, key, decrypting=False)


def decrypt(data: bytes, key: int) -> bytes:
    """Decrypt complete 32-bit words and preserve a partial trailing word."""
    return _transform(data, key, decrypting=True)
