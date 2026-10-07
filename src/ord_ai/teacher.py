"""Deterministic rule-based teacher policy for ORDR easy mode."""

import re
from typing import Final

from ord_ai.catalog import EVOLUTION_WISP, RANDOM_WISP, Action, ActionKind, BasicAction, Catalog
from ord_ai.protocol import GameState

WAIT_CODE: Final = 0
SUMMON_CODE: Final = 1
EVOLVE_CODE: Final = 4
STORY_CODE: Final = 5
LINE_CODE: Final = 6
SUMMON_ALL_CODE: Final = 7
FUSION_MIN_CODE: Final = 1000
EMERGENCY_RATIO: Final = 0.6
STORY_SAFE_RATIO: Final = 0.3
STORY_MIN_ROUND: Final = 5
STORY_MAX_UNITS: Final = 2
REJECTION_COOLDOWN: Final = 30
REJECTION_BAN: Final = 3
GAMBLE_LOW_CODE: Final = int(BasicAction.GAMBLE_LOW)
GAMBLE_HIGH_CODE: Final = int(BasicAction.GAMBLE_HIGH)
UPGRADE_CODES: Final = (
    int(BasicAction.UPGRADE_COMMON),
    int(BasicAction.UPGRADE_SPECIAL),
    int(BasicAction.UPGRADE_RARE),
)
CREATE_RARE_CODE: Final = int(BasicAction.CREATE_RARE)
# war3map.w3u h0AX 물품 지원 (sold by gamble house 1 h062): 100 gold, stock 2, one more after
# 3600 s; war3map.j WpR pays 30 gold plus a common/uncommon unit (1% pirate ship h060).
SUPPLY_UNIT: Final = int.from_bytes(b"h0AX", "big")
SUPPLY_COOLDOWN: Final = 2880  # 3600 s of decisions: the refused stock is gone for the game
# war3map.j: a common choice wisp (e018) walked into choice region N (kIs rY..ZY, handlers
# gHv, n6R, Dkv, OEv, bIs, lER, y4s, FAv, uNR) becomes common unit mB[N].
COMMON_CHOICE_WISP: Final = int.from_bytes(b"e018", "big")
COMMON_CHOICES: Final = tuple(
    int.from_bytes(code, "big")
    for code in (b"h003", b"h002", b"h001", b"h007", b"h004", b"h008", b"h005", b"h009", b"h006")
)
COMMON_CHOICE_NAME: Final = re.compile(r"region: 흔함선택위습 -> \w+ \(흔함 ([1-9])\)")
# Decisions (about 1.25 s each) to skip a shop action the map refused, e.g. out of stock:
# gamble house stock returns after 11 s (low) and 9 s (high); rare creation after 3600 s.
SHOP_COOLDOWN: Final = {
    GAMBLE_LOW_CODE: 8,
    GAMBLE_HIGH_CODE: 7,
    **dict.fromkeys(UPGRADE_CODES, 8),
    CREATE_RARE_CODE: REJECTION_COOLDOWN,
}


class NoAllowedActionError(RuntimeError):
    """Raised when a mask forbids every catalog action."""

    def __init__(self, size: int) -> None:
        """Record the size of the fully masked action space."""
        self.size: int = size
        super().__init__(f"All {size} actions are masked")


def _completed_recipes(recipes: list[Action], counts: dict[int, int], unit: int) -> int:
    """How many recipes one more ``unit`` would newly satisfy."""
    after = counts | {unit: counts.get(unit, 0) + 1}
    return sum(
        all(after.get(part.raw_id, 0) >= part.count for part in recipe.units)
        and not all(counts.get(part.raw_id, 0) >= part.count for part in recipe.units)
        for recipe in recipes
    )


def common_choice(catalog: Catalog, counts: dict[int, int], allowed: dict[int, int]) -> int | None:
    """Pick the choice region for a common choice wisp, or None without one.

    The chosen common completes the most recipes, else it is the common owned least.
    """
    if counts.get(COMMON_CHOICE_WISP, 0) == 0:
        return None
    options: dict[int, int] = {}
    for action in catalog.actions:
        match = COMMON_CHOICE_NAME.fullmatch(action.name)
        if match is not None and action.code in allowed:
            options[action.code] = COMMON_CHOICES[int(match.group(1)) - 1]
    recipes = [action for action in catalog.actions if action.code >= FUSION_MIN_CODE]
    return max(
        options,
        key=lambda code: (
            _completed_recipes(recipes, counts, options[code]),
            -counts.get(options[code], 0),
            -code,
        ),
        default=None,
    )


def _fusion_score(action: Action) -> float:
    """Prefer recipes that consume more material and resources."""
    return sum(unit.count for unit in action.units) + action.gold / 1000 + 2 * action.lumber


class Teacher:
    """Rule policy with per-episode memory.

    Mutable by design: it tracks accepted story transfers, rejected fusions, refused shop
    purchases and the one-time rare unit creation.
    """

    def __init__(self) -> None:
        """Start with empty episode memory."""
        self._decisions: int = 0
        self._story: int = 0
        self._last: int | None = None
        self._rejected_at: dict[int, int] = {}
        self._rejections: dict[int, int] = {}
        self._shop_blocked_until: dict[int, int] = {}
        self._created_rare: bool = False
        self._supply: int | None = None

    def reset(self) -> None:
        """Forget all memory for a new episode."""
        self._decisions = 0
        self._story = 0
        self._last = None
        self._rejected_at = {}
        self._rejections = {}
        self._shop_blocked_until = {}
        self._created_rare = False

    def observe(self, result: GameState) -> None:
        """Apply map feedback for the last chosen action."""
        code, self._last = self._last, None
        if code is None:
            return
        accepted = result.action_ok == 1
        if accepted and code == STORY_CODE:
            self._story += 1
        elif accepted and code == LINE_CODE:
            self._story = max(0, self._story - 1)
        elif not accepted and code >= FUSION_MIN_CODE:
            self._rejected_at[code] = self._decisions
            self._rejections[code] = self._rejections.get(code, 0) + 1
        elif not accepted and code in SHOP_COOLDOWN:
            self._shop_blocked_until[code] = self._decisions + SHOP_COOLDOWN[code]
        elif accepted and code == CREATE_RARE_CODE:
            self._created_rare = True
        elif not accepted and code == self._supply:
            self._shop_blocked_until[code] = self._decisions + SUPPLY_COOLDOWN

    def choose(self, state: GameState, catalog: Catalog, mask: tuple[bool, ...]) -> int:
        """Return the index of the highest-priority allowed action."""
        self._decisions += 1
        allowed = {
            action.code: index
            for index, (action, ok) in enumerate(zip(catalog.actions, mask, strict=True))
            if ok
        }
        if not allowed:
            raise NoAllowedActionError(size=len(mask))
        code = self._rule(state, catalog, allowed)
        self._last = code
        return allowed[code]

    def _rule(self, state: GameState, catalog: Catalog, allowed: dict[int, int]) -> int:
        """Pick an allowed action code by the fixed rule priority."""
        counts = {unit.raw_id: unit.count for unit in state.units}
        pressure = state.enemies / state.enemy_limit
        fusions = [
            action
            for action in catalog.actions
            if action.code >= FUSION_MIN_CODE
            and action.code in allowed
            and self._fusion_ready(action.code)
        ]
        best = max(fusions, key=lambda action: (_fusion_score(action), -action.code), default=None)
        wisps = counts.get(RANDOM_WISP, 0) > 0
        choice = common_choice(catalog, counts, allowed)
        self._supply = next(
            (
                action.code
                for action in catalog.actions
                if action.kind == ActionKind.SELL and action.raw_id == SUPPLY_UNIT
            ),
            None,
        )
        supply = WAIT_CODE if self._supply is None else self._supply
        story_safe = (
            state.round >= STORY_MIN_ROUND
            and pressure <= STORY_SAFE_RATIO
            and self._story < STORY_MAX_UNITS
        )
        # The user's economy routine: spin the low gold gamble whenever it is in stock,
        # upgrade common, buy every 물품 지원 in stock, then upgrade special and rare units,
        # spin the high gold gamble once the round 10 boss unlocks it, and buy the one rare
        # unit after 35000 gamble gold.
        candidates = (
            (wisps, SUMMON_ALL_CODE),
            (wisps, SUMMON_CODE),
            (choice is not None, WAIT_CODE if choice is None else choice),
            (counts.get(EVOLUTION_WISP, 0) > 0, EVOLVE_CODE),
            (pressure >= EMERGENCY_RATIO and self._story > 0, LINE_CODE),
            (not self._created_rare and self._shop_ready(CREATE_RARE_CODE), CREATE_RARE_CODE),
            (best is not None, WAIT_CODE if best is None else best.code),
            (story_safe, STORY_CODE),
            (self._shop_ready(GAMBLE_LOW_CODE), GAMBLE_LOW_CODE),
            (self._shop_ready(UPGRADE_CODES[0]), UPGRADE_CODES[0]),
            (self._supply is not None and self._shop_ready(supply), supply),
            *((self._shop_ready(code), code) for code in UPGRADE_CODES[1:]),
            (self._shop_ready(GAMBLE_HIGH_CODE), GAMBLE_HIGH_CODE),
            (True, WAIT_CODE),
        )
        return next(
            (code for wanted, code in candidates if wanted and code in allowed),
            min(allowed, key=allowed.__getitem__),
        )

    def _shop_ready(self, code: int) -> bool:
        """Whether a recent refusal (usually empty stock) still blocks this shop action."""
        return self._decisions > self._shop_blocked_until.get(code, 0)

    def _fusion_ready(self, code: int) -> bool:
        """Whether rejection memory currently permits this fusion."""
        if self._rejections.get(code, 0) >= REJECTION_BAN:
            return False
        rejected_at = self._rejected_at.get(code)
        return rejected_at is None or self._decisions > rejected_at + REJECTION_COOLDOWN
