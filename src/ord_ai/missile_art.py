"""Give generated maps' missile weapons with an empty or ".mdl" projectile art a real model.

In the original map, 26 unit weapons of a missile type have projectile art "" or ".mdl"
(war3map.w3u ua1m). Every attack of such a weapon logs "model creation failed" in
War3Log.txt, and long games reach thousands of failures. Only those art strings change, to
the map's own imported dummy model; weapon type, speed, arc, damage and every other byte of
war3map.w3u stay as they are. The supplied map itself is never modified.
"""

from typing import Final

from ord_ai.objectdata import iter_fields

REPLACEMENT: Final = "dummy.mdl"  # the map imports dummy.mdx and its dummy units use it
BROKEN_ART: Final = frozenset({"", ".mdl"})
MISSILE_WEAPONS: Final = frozenset({"missile", "msplash", "mbounce", "mline", "artillery", "aline"})
ART_TO_WEAPON: Final = {"ua1m": "ua1w", "ua2m": "ua2w"}
_STRING: Final = 3


def patch_missile_art(w3u: bytes) -> tuple[bytes, tuple[tuple[str, str], ...]]:
    """Return war3map.w3u with the broken missile art replaced, and the (unit, field) changed.

    Only a weapon type the map itself sets counts: in this map every broken art whose unit
    keeps the base weapon type is on an instant weapon (base hrif), which fires no missile.
    """
    entries = list(iter_fields(w3u, leveled=False))
    weapons = {
        (entry.key, entry.name): str(entry.value).lower()
        for entry in entries
        if entry.name in ART_TO_WEAPON.values()
    }
    pieces: list[bytes] = []
    changed: list[tuple[str, str]] = []
    cursor = 0
    for entry in entries:
        weapon = ART_TO_WEAPON.get(entry.name)
        if (
            weapon is None
            or entry.kind != _STRING
            or str(entry.value).strip() not in BROKEN_ART
            or weapons.get((entry.key, weapon)) not in MISSILE_WEAPONS
        ):
            continue
        pieces += [w3u[cursor : entry.start], REPLACEMENT.encode("utf-8")]
        cursor = entry.end
        changed.append((entry.key, entry.name))
    pieces.append(w3u[cursor:])
    return b"".join(pieces), tuple(changed)
