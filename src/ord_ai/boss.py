"""The round boss and the round clock as snapshot rows and policy features.

A boss round (every 10th round, and every 5th after 50 on Hard and above) ends in defeat
when its round timer ``Vp`` expires while the player's boss lives: ``BUs`` (war3map.j
6831-6843) checks ``tP[p] != 0`` (live round bosses, the snapshot's ``bosses``) and calls
``U5s``. The boss is ``CreateUnit(Player(6), iB[Av], ...)`` at the player's lane (51754,
61807). Neither its health nor the time left was visible, so the bridge (``ORDAI_Boss`` in
bridge_actions.j) exports both as TECH rows with the synthetic ids below; no object of the
map uses them (tests/test_boss.py checks it).
"""

from typing import Final

from ord_ai.objectdata import rawcode
from ord_ai.protocol import GameState

# Synthetic TECH ids of the boss rows, in export order: (id, meaning).
BOSS_TECHS: Final = (
    ("ZB00", "live round boss health, 0..1000 of its maximum (0 without a boss)"),
    ("ZB01", "time left on the round timer Vp, tenths of a second"),
)
BOSS_IDS: Final = frozenset(rawcode(code) for code, _ in BOSS_TECHS)
BOSS_HEALTH: Final = rawcode("ZB00")
ROUND_TIME_LEFT: Final = rawcode("ZB01")
HEALTH_SCALE: Final = 1000
# A boss round lasts 60 s (TimerStart(Vp,60.,...) 35905); longer rounds read as full.
ROUND_TIME_SCALE: Final = 600
BOSS_SIZE: Final = len(BOSS_TECHS)


def boss_features(state: GameState) -> tuple[float, ...]:
    """Boss health fraction, then the round time left over 60 s (capped at 1)."""
    techs = {tech.raw_id: tech.level for tech in state.techs}
    return (
        min(1.0, techs.get(BOSS_HEALTH, 0) / HEALTH_SCALE),
        min(1.0, techs.get(ROUND_TIME_LEFT, 0) / ROUND_TIME_SCALE),
    )
