"""Snapshot rows that compare the simulator with the real map; the policy never reads them.

Action 6 (return one unit from the story arena to the lane) was accepted 217 of 217 times in
real game 45 but 351 of 4,740 times in the simulator with the same checkpoint. The bridge
accepts it only for a stationary Player(0) fighter inside the story arena rectangle
(``ORDAI_Eligible`` a == 6). ``ORDAI_ArenaDiag`` exports how many fighters are in that
rectangle, how many of them are moving and how many are eligible, in both the real game and
the simulator, so a matched run shows which part of the condition differs.
"""

from typing import Final

from ord_ai.objectdata import rawcode
from ord_ai.protocol import GameState

# Synthetic TECH ids, in export order: (id, meaning). No map object uses them.
DIAGNOSTIC_TECHS: Final = (
    ("ZD00", "Player(0) fighters (point value and base damage > 0) in the story arena"),
    ("ZD01", "of those, units whose current order is move"),
    ("ZD02", "of those, units ORDAI_Eligible accepts for action 6"),
)
DIAGNOSTIC_IDS: Final = frozenset(rawcode(code) for code, _ in DIAGNOSTIC_TECHS)
ARENA_UNITS: Final = rawcode("ZD00")
ARENA_MOVING: Final = rawcode("ZD01")
ARENA_ELIGIBLE: Final = rawcode("ZD02")


def arena(state: GameState) -> tuple[int, int, int] | None:
    """(in arena, moving, eligible), or None when the bridge did not export the rows."""
    techs = {tech.raw_id: tech.level for tech in state.techs}
    if ARENA_UNITS not in techs:
        return None
    return techs[ARENA_UNITS], techs.get(ARENA_MOVING, 0), techs.get(ARENA_ELIGIBLE, 0)
