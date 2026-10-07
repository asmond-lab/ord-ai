"""Difficulty ladder for simulator training; nothing here starts a run."""

from enum import IntEnum
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict

from ord_ai.protocol import GameState, Status


class Difficulty(IntEnum):
    """The map's difficulty index ``ls`` set by the selection trigger ``DYR``."""

    EASY = 1
    NORMAL = 2
    HARD = 3
    HELL = 4
    GOD = 5
    NIGHTMARE = 6


# The map accepts modes only from Hell up: l4s refuses the toggle and bBs clears it below.
ISEKAI_FLOOR: Final = Difficulty.HELL


class Stage(BaseModel):
    """One curriculum rung: a difficulty and whether Isekai mode is on."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    difficulty: Difficulty
    isekai: bool


STAGES: Final = tuple(
    Stage(difficulty=difficulty, isekai=difficulty >= ISEKAI_FLOOR) for difficulty in Difficulty
)


def zero_count_clear(final: GameState) -> bool:
    """A win with no enemies left on the player's line, the map's "unit count 0".

    The map stores the final count only after round 65 (``Ks = js``); Easy and Normal end
    earlier, so the line count ``js`` in the winning snapshot is used for every stage.
    """
    return final.status == Status.WIN and final.enemies == 0


def next_stage(stage: int, final: GameState) -> int:
    """Promote after a unit-count-0 clear; the last stage is kept once reached."""
    if zero_count_clear(final):
        return min(stage + 1, len(STAGES) - 1)
    return stage
