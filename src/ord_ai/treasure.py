"""숨은 보물상자 (hidden treasure chests) as snapshot rows and policy features.

The map keeps the chests as coordinates, not units: real arrays ``Ur[i]`` (x) and ``Br[i]``
(y) for ``0 <= i < Qr`` (war3map.j 332-334); a slot is live while ``Ur[i]!=0 or Br[i]!=0``
and collection zeroes both (qps 22634-22692). ``avs`` (54780) spawns them uniformly in
``IY=Rect(-6016,-2336,3744,6080)`` (81120). Casting A05D 탐색 (TTs 30801) collects every live
chest in the square of half-width ``cP[player]`` around the target point.

Like the 기록지침 rows (``ord_ai.logbook``), the bridge (``ORDAI_Treasure`` in
bridge_actions.j) exports these values as TECH rows with the synthetic ids below; no object
of the map uses them (tests/test_treasure.py checks it). The live chests are listed in
ascending slot index; action ``TREASURE_FLOOR + k`` casts 탐색 at the k-th of them.

Exporting ``Ur/Br`` gives the policy chest positions a human player cannot see (requested).
"""

from typing import Final

from ord_ai.objectdata import rawcode
from ord_ai.protocol import GameState

# The chest actions' wire codes (and catalog indices): TREASURE_FLOOR .. +TREASURE_SLOTS-1.
TREASURE_FLOOR: Final = 574
TREASURE_SLOTS: Final = 16
SCAN_ABILITY: Final = "A05D"
# IY, the spawn rect of avs; positions are exported as 0..POSITION_SCALE inside it.
AREA: Final = (-6016.0, -2336.0, 3744.0, 6080.0)
POSITION_SCALE: Final = 1000

# Synthetic TECH ids of the chest rows, in export order: (id, meaning).
TREASURE_TECHS: Final = (
    ("ZC00", "live chest count (every live slot, may exceed 16)"),
    ("ZC01", "탐색 ready: R009, caster, A05D off cooldown and enough mana (0/1)"),
    *(
        row
        for slot in range(TREASURE_SLOTS)
        for row in (
            (f"ZX{slot:02}", f"live chest {slot} x in IY, 0..1000 (0 when absent)"),
            (f"ZY{slot:02}", f"live chest {slot} y in IY, 0..1000 (0 when absent)"),
        )
    ),
)
TREASURE_IDS: Final = frozenset(rawcode(code) for code, _ in TREASURE_TECHS)
CHEST_COUNT: Final = rawcode("ZC00")
SCAN_READY: Final = rawcode("ZC01")
CHEST_X: Final = tuple(rawcode(f"ZX{slot:02}") for slot in range(TREASURE_SLOTS))
CHEST_Y: Final = tuple(rawcode(f"ZY{slot:02}") for slot in range(TREASURE_SLOTS))
TREASURE_SIZE: Final = 2 + 2 * TREASURE_SLOTS


def treasure_features(state: GameState) -> tuple[float, ...]:
    """Count/16 (capped at 1), scan ready, then x/y of the first 16 live chests (/1000)."""
    techs = {tech.raw_id: tech.level for tech in state.techs}
    positions = tuple(
        techs.get(raw, 0) / POSITION_SCALE
        for pair in zip(CHEST_X, CHEST_Y, strict=True)
        for raw in pair
    )
    return (
        min(1.0, techs.get(CHEST_COUNT, 0) / TREASURE_SLOTS),
        float(techs.get(SCAN_READY, 0) > 0),
        *positions,
    )
