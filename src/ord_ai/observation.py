"""Policy features and progress-based rewards from real map transitions."""

import math
from typing import Final, assert_never

from ord_ai.boss import BOSS_SIZE, boss_features
from ord_ai.catalog import Catalog
from ord_ai.logbook import LOGBOOK_SIZE, logbook_features
from ord_ai.protocol import GameState, ProtocolError, Status
from ord_ai.treasure import TREASURE_SIZE, treasure_features

SCALAR_COUNT: Final = 10
LAST_ROUND: Final = 65.0
LAST_STORY: Final = 15.0
RESOURCE_SCALE: Final = 100_000.0
COUNT_SCALE: Final = 100.0
NEW_WORLD_BOUNDARY: Final = 50


def input_size(catalog: Catalog) -> int:
    """Length of ``features`` for this catalog: scalars, unit counts, 기록지침, chests, boss."""
    return SCALAR_COUNT + len(catalog.units) + LOGBOOK_SIZE + TREASURE_SIZE + BOSS_SIZE


def features(state: GameState, catalog: Catalog) -> tuple[float, ...]:
    """Keep unit-count ordering identical across training and inference.

    The 기록지침 window (voyage goals, quests, navigation; ``ord_ai.logbook``) follows the
    unit counts; the hidden chests (``ord_ai.treasure``) and then the round boss and round
    clock (``ord_ai.boss``) come last, so an older checkpoint migrates by appending zero
    input columns.
    """
    scalars = (
        state.round / LAST_ROUND,
        math.log1p(state.gold) / math.log1p(RESOURCE_SCALE),
        math.log1p(state.lumber) / math.log1p(COUNT_SCALE),
        state.food / COUNT_SCALE,
        state.enemies / state.enemy_limit,
        state.enemy_limit / COUNT_SCALE,
        max(0, state.death_count) / COUNT_SCALE,
        state.story / LAST_STORY,
        float(state.bosses),
        float(state.round > NEW_WORLD_BOUNDARY),
    )
    units = [0.0] * len(catalog.units)
    positions = _unit_positions(catalog)
    scale = math.log1p(COUNT_SCALE)
    # Later rows of a raw id win, as in a dict built from the rows.
    for unit in state.units:
        for position in positions.get(unit.raw_id, ()):
            units[position] = math.log1p(unit.count) / scale
    return (
        scalars
        + tuple(units)
        + logbook_features(state)
        + treasure_features(state)
        + boss_features(state)
    )


_POSITIONS: dict[int, tuple[Catalog, dict[int, tuple[int, ...]]]] = {}


def _unit_positions(catalog: Catalog) -> dict[int, tuple[int, ...]]:
    """The feature positions of each catalog unit raw id."""
    cached = _POSITIONS.get(id(catalog))
    if cached is not None and cached[0] is catalog:
        return cached[1]
    positions: dict[int, tuple[int, ...]] = {}
    for position, unit in enumerate(catalog.units):
        positions[unit.raw_id] = (*positions.get(unit.raw_id, ()), position)
    _POSITIONS[id(catalog)] = (catalog, positions)
    return positions


def transition_reward(before: GameState, after: GameState) -> float:
    """Reward progress and outcomes, not wall-clock time or synthetic victories."""
    if before.episode != after.episode or after.sequence != before.sequence + 1:
        raise ProtocolError(detail="Reward requires adjacent observations from the same episode")
    progress = float(max(0, after.round - before.round))
    progress += 5.0 * max(0, after.story - before.story)
    progress += 3.0 * max(0, before.bosses - after.bosses)
    progress += 0.2 * (before.enemies / before.enemy_limit - after.enemies / after.enemy_limit)
    if after.action_ok == 0:
        progress -= 0.05
    match after.status:
        case Status.WIN:
            # The curriculum goal is a clear with no enemies left on the line (unit count 0).
            return progress + (20.0 if after.enemies == 0 else 10.0)
        case Status.LOSS:
            return progress - 10.0
        case Status.PLAYING:
            return progress
        case Status.WAITING:
            raise ProtocolError(detail="A setup snapshot is not a learning transition")
    assert_never(after.status)
