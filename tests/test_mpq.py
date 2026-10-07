from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ord_ai.mpq import read_member, replace_member
from ord_ai.mpq_crypto import decrypt, encrypt, hash_string

_MAP_PATH = Path(__file__).parents[1] / "ORDR_S2_2.323[R].w3x"
_SOURCE_SHA256 = "eac74a90a967473196c84446b880387857f50a828083c0e1b5fc02b325663535"
_SCRIPT_SHA256 = "9ecc275f549eadc565c7474f7d4449d9c856687e16a8108ef2ba7b586de13785"
_PRESERVED_MEMBERS = (
    "war3map.wts",
    "war3map.w3i",
    "war3map.w3u",
    "war3map.w3a",
    "war3map.w3q",
)


@pytest.mark.parametrize("length", range(12))
def test_crypto_round_trip_preserves_partial_words(length: int) -> None:
    # Given
    content = bytes(range(length))
    key = hash_string("war3map.j", 3)

    # When
    encrypted = encrypt(content, key)
    decrypted = decrypt(encrypted, key)

    # Then
    assert decrypted == content


def test_read_member_selects_genuine_protected_script() -> None:
    # Given
    source = _MAP_PATH.read_bytes()

    # When
    script = read_member(source, "war3map.j")

    # Then
    assert hashlib.sha256(script).hexdigest() == _SCRIPT_SHA256


def test_replace_member_preserves_other_members() -> None:
    # Given
    source = _MAP_PATH.read_bytes()
    preserved = {name: read_member(source, name) for name in _PRESERVED_MEMBERS}
    replacement = b"function main takes nothing returns nothing\nendfunction\n"

    # When
    result = replace_member(source, "war3map.j", replacement)

    # Then
    assert read_member(result, "war3map.j") == replacement
    assert {name: read_member(result, name) for name in _PRESERVED_MEMBERS} == preserved
    assert result[:2048] == source[:2048]


def test_replace_member_preserves_native_archive_layout() -> None:
    # Given: the game reads this protected layout but crashes on its normalization.
    source = _MAP_PATH.read_bytes()
    base = source.index(b"MPQ\x1a")
    replacement = b"function main takes nothing returns nothing\nendfunction\n"
    table_offset = int.from_bytes(source[base + 16 : base + 20], "little")
    table_count = int.from_bytes(source[base + 24 : base + 28], "little")
    hashes = slice(base + table_offset, base + table_offset + table_count * 16)

    # When
    result = replace_member(source, "war3map.j", replacement)

    # Then: preserve format/sector flags and the native engine's hash selection.
    assert result[base : base + 8] == source[base : base + 8]
    assert result[base + 12 : base + 32] == source[base + 12 : base + 32]
    assert result[hashes] == source[hashes]


def test_source_archive_remains_byte_identical() -> None:
    # Given
    source = _MAP_PATH.read_bytes()

    # When
    digest = hashlib.sha256(source).hexdigest()

    # Then
    assert digest == _SOURCE_SHA256
