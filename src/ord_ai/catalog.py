"""Extract policy actions and unit identities from this map's real data."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Annotated, ClassVar, Final, assert_never

from pydantic import BaseModel, ConfigDict, Field

from ord_ai.protocol import GameState, ProtocolError

COLOR: Final = re.compile(r"\|c[0-9a-f]{8}|\|r", re.IGNORECASE)
NAMES: Final = re.compile(
    r"STRING \d+\n// 유닛: ([A-Za-z0-9]{4})[^\n]*, Name[^\n]*\n\{\n(.*?)\n\}",
    re.DOTALL,
)
RECIPE_PATTERN: Final = (
    r"call SaveBoolean\(cv,\$([0-9a-f]+),1,true\)\n",
    r"(.*?)(?=call SaveBoolean\(cv,|endfunction)",
)
REQUIREMENT_PATTERN: Final = (
    r"call SaveInteger\(Bv,Mv,uv,\$([0-9a-f]+)\)\n",
    r"call SaveInteger\(Hv,Mv,uv,([^)\n]+)\)\n",
    r"call SaveInteger\(ov,Mv,uv,(\$[0-9a-f]+|\d+)\)",
)
RECIPES: Final = re.compile("".join(RECIPE_PATTERN), re.DOTALL)
REQUIREMENTS: Final = re.compile("".join(REQUIREMENT_PATTERN))
BASE_ACTIONS: Final = (
    "wait",
    "summon_random_wisp",
    "gamble_wisp_for_lumber",
    "restore_helper_mana",
    "activate_evolution_wisp",
    "send_one_unit_to_story",
    "return_one_unit_to_line",
    "summon_all_random_wisps",
)
RANDOM_WISP: Final = int.from_bytes(b"e0IX", "big")
EVOLUTION_WISP: Final = int.from_bytes(b"e0HP", "big")
# Research levels the map's object data gates behind a tech (upgrade field greq at that
# level): the fifteen unit upgrades need R00H, granted for killing 클리크 (war3map.j
# 16978), for level 13. The map refuses the research until then (tests/test_input_masks.py
# checks this table against the map).
R00H: Final = int.from_bytes(b"R00H", "big")
RESEARCH_GATES: Final = {
    int.from_bytes(code.encode(), "big"): (13, R00H)
    for code in (
        "R000",
        "R002",
        "R003",
        "R004",
        "R007",
        "R00X",
        "R013",
        "R014",
        "R015",
        "R016",
        "R019",
        "R01V",
        "R02C",
        "R02O",
        "R032",
    )
}
# Player buildings the map creates per player: gamble house pp[] and upgrade house 1 rp[].
# Rawcodes, costs, stock and requirements are read from the supplied map's war3map.w3u and
# war3map.w3q (tests/test_shop_actions.py re-checks them against the map file).
GRADUATED: Final = int.from_bytes(b"R01M", "big")
HIGH_GAMBLE_UNLOCK: Final = int.from_bytes(b"R009", "big")
RARE_UNIT_UNLOCK: Final = int.from_bytes(b"R01N", "big")
UPGRADE_COMMON: Final = int.from_bytes(b"R00F", "big")
UPGRADE_SPECIAL: Final = int.from_bytes(b"R00E", "big")
UPGRADE_RARE: Final = int.from_bytes(b"R008", "big")
SHOP_ACTIONS: Final = (
    "gold_gamble_low",
    "gold_gamble_high",
    "upgrade_common",
    "upgrade_special",
    "upgrade_rare",
    "create_rare_unit",
)
DISPLAY_ONLY_RECIPES: Final = frozenset(
    [f"IS{index:02}" for index in range(1, 15)]
    + [f"SP{index:02}" for index in range(1, 9)]
    + [f"SPH{index}" for index in range(1, 5)]
    + ["UU01", "YY26", "TEST"]
)


class BasicAction(IntEnum):
    """Small control actions, separate from rawcode fusion actions."""

    WAIT = 0
    SUMMON = 1
    LUMBER = 2
    MANA = 3
    EVOLVE = 4
    STORY = 5
    LINE = 6
    SUMMON_ALL = 7
    GAMBLE_LOW = 8
    GAMBLE_HIGH = 9
    UPGRADE_COMMON = 10
    UPGRADE_SPECIAL = 11
    UPGRADE_RARE = 12
    CREATE_RARE = 13


class ActionKind(StrEnum):
    """How the bridge performs an action; every kind uses the map's own input path."""

    BASIC = "basic"  # codes 0-7: waits and unit moves
    FUSION = "fusion"  # rawcode codes of the recipe table cv
    SHOP = "shop"  # codes 8-13: gamble house and upgrade house 1 purchases
    SELL = "sell"  # buy a unit-item from one of the player's shops
    TRAIN = "train"  # train a unit at the gamble house
    RESEARCH = "research"  # start a research at an upgrade house or the laboratory
    ABILITY = "ability"  # cast an ability of a player unit
    SYNC = "sync"  # the map's own BlzSendSyncData UI choice (navigation, quest reroll)
    REGION = "region"  # move a unit into a region with an enter trigger
    SELECT = "select"  # select a unit with a selection trigger
    CHAT = "chat"  # run a registered chat command's handler
    ATTACK = "attack"  # order a fighter to attack a 항해목표 objective (map widget)
    TREASURE = "treasure"  # cast A05D 탐색 at a live hidden chest (ord_ai.treasure)


class Research(BaseModel):
    """A leveled upgrade: the next level costs ``base + step * current level`` gold."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    raw_id: int
    levels: int
    base: int
    step: int
    lumber_base: int = 0
    lumber_step: int = 0


class TechBound(BaseModel):
    """A tech (upgrade level or unlock flag) the snapshot's TECH rows must satisfy."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    raw_id: int
    minimum: int = 1
    maximum: int | None = None


RESEARCH: Final = {
    BasicAction.UPGRADE_COMMON: Research(raw_id=UPGRADE_COMMON, levels=7, base=3, step=2),
    BasicAction.UPGRADE_SPECIAL: Research(raw_id=UPGRADE_SPECIAL, levels=15, base=5, step=5),
    BasicAction.UPGRADE_RARE: Research(raw_id=UPGRADE_RARE, levels=16, base=10, step=20),
}
GAMBLE_LOW_GOLD: Final = 10
GAMBLE_HIGH_GOLD: Final = 500
CREATE_RARE_GOLD: Final = 5000
# ORDAI_Act routes codes at or above this to fusion recipes (rawcodes), below to BasicAction
# (0-13) or to the bridge input table (FIRST_INPUT_CODE and up).
FUSION_FLOOR: Final = 1000
FIRST_INPUT_CODE: Final = int(BasicAction.CREATE_RARE) + 1


class RequirementKind(StrEnum):
    """Requirement handlers registered in the supplied map."""

    UNIT = "UNIT"
    GOLD = "GOLD"
    WOOD = "WOOD"
    UPUN = "UPUN"
    CHCT = "CHCT"
    SPEC = "SPEC"
    IFCT = "IFCT"
    GREN = "GREN"
    UBAN = "UBAN"
    KING = "KING"
    ITEM = "ITEM"
    SKPT = "SKPT"
    RMAX = "RMAX"
    RDUN = "RDUN"
    PICK = "PICK"


class UnitType(BaseModel):
    """A stable rawcode and its original display name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    raw_id: int
    name: str


class Requirement(BaseModel):
    """A known fusion unit requirement."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    raw_id: int
    count: Annotated[int, Field(gt=0)]


class Action(BaseModel):
    """An action wire code and conservative preconditions the snapshot can show.

    ``units`` must all be present (fusion materials); ``casters`` needs any one of the listed
    unit types; ``fighter`` needs any visible player hero unit. ``techs`` bound TECH rows,
    ``food`` is the trait point count (PLAYER_STATE_RESOURCE_FOOD_USED).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    code: int
    name: str
    units: tuple[Requirement, ...] = ()
    gold: int = 0
    lumber: int = 0
    extra_conditions: tuple[str, ...] = ()
    kind: ActionKind | None = None
    raw_id: int = 0
    casters: tuple[int, ...] = ()
    fighter: bool = False
    food: int = 0
    techs: tuple[TechBound, ...] = ()
    research: Research | None = None
    min_round: int = 0
    max_round: int | None = None


def kind_of(action: Action) -> ActionKind:
    """The action's kind; catalogs before schema 3 leave it implied by the wire code."""
    if action.kind is not None:
        return action.kind
    if action.code >= FUSION_FLOOR:
        return ActionKind.FUSION
    return ActionKind.SHOP if action.code >= BasicAction.GAMBLE_LOW else ActionKind.BASIC


class Catalog(BaseModel):
    """Fixed policy vocabulary; checkpoints must retain this exact order."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = 3
    map_sha256: str
    units: tuple[UnitType, ...]
    actions: tuple[Action, ...]

    @property
    def fingerprint(self) -> str:
        """Hash the policy vocabulary, including its source-map identity."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def unit_names(strings: str) -> dict[int, str]:
    """Unit display names from war3map.wts, keyed by rawcode."""
    normalized = strings.lstrip("\ufeff").replace("\r\n", "\n")
    return {
        int.from_bytes(match.group(1).encode("ascii"), "big"): COLOR.sub("", match.group(2))
        for match in NAMES.finditer(normalized)
    }


def extract_catalog(script: str, strings: str, map_sha256: str) -> Catalog:
    """Read actual fusion tables; never invent recipes or their costs.

    This is the script-derived vocabulary (base actions, fusions, shop actions); the other
    player inputs need the object data and are appended by ``ord_ai.build.read_map``.
    """
    names = unit_names(strings)
    actions = [
        Action(code=index, name=name, kind=ActionKind.BASIC)
        for index, name in enumerate(BASE_ACTIONS)
    ]
    for recipe in RECIPES.finditer(script):
        code_hex, body = recipe.group(1), recipe.group(2)
        if int(code_hex, 16).to_bytes(4, "big").decode("ascii") in DISPLAY_ONLY_RECIPES:
            continue
        results = [
            match.group(1)
            for match in re.finditer(r"call SaveInteger\(Qv,Mv,gv,\$([0-9a-f]+)\)", body)
        ]
        if not results:
            continue
        requirements: Counter[int] = Counter()
        gold = 0
        lumber = 0
        extra: set[str] = set()
        for requirement in REQUIREMENTS.finditer(body):
            kind_hex = requirement.group(1)
            target, count = requirement.group(2), requirement.group(3)
            kind = RequirementKind(int(kind_hex, 16).to_bytes(4, "big").decode("ascii"))
            quantity = int(count[1:], 16) if count.startswith("$") else int(count)
            match kind:
                case RequirementKind.UNIT:
                    requirements[int(target.removeprefix("$"), 16)] += quantity
                    continue
                case RequirementKind.GOLD:
                    gold += quantity
                    continue
                case RequirementKind.WOOD:
                    lumber += quantity
                    continue
                case (
                    RequirementKind.UPUN
                    | RequirementKind.CHCT
                    | RequirementKind.SPEC
                    | RequirementKind.IFCT
                    | RequirementKind.GREN
                    | RequirementKind.UBAN
                    | RequirementKind.KING
                    | RequirementKind.ITEM
                    | RequirementKind.SKPT
                    | RequirementKind.RMAX
                    | RequirementKind.RDUN
                    | RequirementKind.PICK
                ):
                    extra.add(kind.value)
                    continue
            assert_never(kind)
        result_ids = [int(value, 16) for value in results]
        actions.append(
            Action(
                code=int(code_hex, 16),
                kind=ActionKind.FUSION,
                name="fusion: " + " + ".join(names.get(value, str(value)) for value in result_ids),
                units=tuple(
                    Requirement(raw_id=key, count=value)
                    for key, value in sorted(requirements.items())
                ),
                gold=gold,
                lumber=lumber,
                extra_conditions=tuple(sorted(extra)),
            )
        )
    if len(actions) == len(BASE_ACTIONS):
        raise ProtocolError(detail="No original fusion recipes were found in this map")
    # Appended after the fusions so every earlier action keeps its index in old records.
    actions.extend(
        Action(code=BasicAction.GAMBLE_LOW + offset, name=name, kind=ActionKind.SHOP)
        for offset, name in enumerate(SHOP_ACTIONS)
    )
    referenced = {requirement.raw_id for action in actions for requirement in action.units}
    unit_types = tuple(
        UnitType(raw_id=key, name=name)
        for key, name in sorted(names.items())
        if key in referenced or key.to_bytes(4, "big")[:1] in (b"h", b"H") or "위습" in name
    )
    return Catalog(map_sha256=map_sha256, units=unit_types, actions=tuple(actions))


def _shop_allowed(kind: BasicAction, state: GameState) -> bool:
    techs = {tech.raw_id: tech.level for tech in state.techs}
    research = RESEARCH.get(kind)
    if research is not None:
        level = techs.get(research.raw_id, 0)
        return level < research.levels and state.gold >= research.base + research.step * level
    # Gamble house 1 sells both gambles; graduation replaces it with gamble house 2.
    graduated = techs.get(GRADUATED, 0) > 0
    unlocked, gold = {
        BasicAction.GAMBLE_LOW: (not graduated, GAMBLE_LOW_GOLD),
        BasicAction.GAMBLE_HIGH: (
            not graduated and techs.get(HIGH_GAMBLE_UNLOCK, 0) > 0,
            GAMBLE_HIGH_GOLD,
        ),
        BasicAction.CREATE_RARE: (
            graduated and techs.get(RARE_UNIT_UNLOCK, 0) > 0,
            CREATE_RARE_GOLD,
        ),
    }[kind]
    return unlocked and state.gold >= gold


def _basic_allowed(kind: BasicAction, state: GameState, counts: dict[int, int]) -> bool:
    match kind:
        case BasicAction.WAIT:
            return True
        case BasicAction.SUMMON | BasicAction.SUMMON_ALL | BasicAction.LUMBER | BasicAction.MANA:
            return counts.get(RANDOM_WISP, 0) > 0
        case BasicAction.EVOLVE:
            return counts.get(EVOLUTION_WISP, 0) > 0
        case BasicAction.STORY | BasicAction.LINE:
            return any(
                raw_id.to_bytes(4, "big")[:1] in (b"h", b"H") and count > 0
                for raw_id, count in counts.items()
            )
        case (
            BasicAction.GAMBLE_LOW
            | BasicAction.GAMBLE_HIGH
            | BasicAction.UPGRADE_COMMON
            | BasicAction.UPGRADE_SPECIAL
            | BasicAction.UPGRADE_RARE
            | BasicAction.CREATE_RARE
        ):
            return _shop_allowed(kind, state)
    assert_never(kind)


def _is_hero_type(raw_id: int) -> bool:
    return raw_id.to_bytes(4, "big")[:1] in (b"h", b"H")


_BASIC_CODES: Final = frozenset(int(member) for member in BasicAction)


def _input_allowed(
    action: Action, state: GameState, counts: dict[int, int], techs: dict[int, int]
) -> bool:
    """Requirements a snapshot can show; stock, mana, cooldowns stay with the map."""
    gold, lumber = action.gold, action.lumber
    research = action.research
    if research is not None:
        level = techs.get(research.raw_id, 0)
        if level >= research.levels:
            return False
        gate = RESEARCH_GATES.get(research.raw_id)
        if gate is not None and level + 1 >= gate[0] and techs.get(gate[1], 0) < 1:
            return False
        gold += research.base + research.step * level
        lumber += research.lumber_base + research.lumber_step * level
    return (
        state.gold >= gold
        and state.lumber >= lumber
        and state.food >= action.food
        and state.round >= action.min_round
        and (action.max_round is None or state.round <= action.max_round)
        and all(counts.get(unit.raw_id, 0) >= unit.count for unit in action.units)
        and (not action.casters or any(counts.get(raw, 0) > 0 for raw in action.casters))
        and (
            not action.fighter
            or any(_is_hero_type(raw) and count > 0 for raw, count in counts.items())
        )
        and all(
            bound.minimum <= techs.get(bound.raw_id, 0)
            and (bound.maximum is None or techs.get(bound.raw_id, 0) <= bound.maximum)
            for bound in action.techs
        )
    )


def action_allowed(action: Action, state: GameState) -> bool:
    """The mask rule for one action; :func:`available_actions` applies it to a catalog."""
    counts = {unit.raw_id: unit.count for unit in state.units}
    techs = {tech.raw_id: tech.level for tech in state.techs}
    kind = kind_of(action)
    match kind:
        case ActionKind.BASIC | ActionKind.SHOP:
            known = action.code in _BASIC_CODES
            return known and _basic_allowed(BasicAction(action.code), state, counts)
        case (
            ActionKind.FUSION
            | ActionKind.SELL
            | ActionKind.TRAIN
            | ActionKind.RESEARCH
            | ActionKind.ABILITY
            | ActionKind.SYNC
            | ActionKind.REGION
            | ActionKind.SELECT
            | ActionKind.CHAT
            | ActionKind.ATTACK
            | ActionKind.TREASURE
        ):
            return _input_allowed(action, state, counts, techs)
    assert_never(kind)


def available_actions(state: GameState, catalog: Catalog) -> tuple[bool, ...]:
    """Mask known missing resources and unlocks; the map validates stock and the rest.

    Evaluates :func:`action_allowed` for every action through a per-catalog plan (the simulator
    trainer masks every game at every step); tests/test_input_masks.py checks both agree.
    """
    plan = _mask_plan(catalog)
    counts = {unit.raw_id: unit.count for unit in state.units}
    techs = {tech.raw_id: tech.level for tech in state.techs}
    hero = any(_is_hero_type(raw) and count > 0 for raw, count in counts.items())
    mask = [False] * len(catalog.actions)
    basic = {code: _basic_allowed(BasicAction(code), state, counts) for code in plan.basic_codes}
    for index, code in plan.basic:
        mask[index] = basic[code]
    gold, lumber, food, round_ = state.gold, state.lumber, state.food, state.round
    for rule in plan.inputs:
        need_gold, need_lumber = rule.gold, rule.lumber
        if rule.research is not None:
            raw, levels, base, step, lumber_base, lumber_step = rule.research
            level = techs.get(raw, 0)
            if level >= levels:
                continue
            if (
                rule.gate is not None
                and level + 1 >= rule.gate[0]
                and techs.get(rule.gate[1], 0) < 1
            ):
                continue
            need_gold += base + step * level
            need_lumber += lumber_base + lumber_step * level
        mask[rule.index] = (
            gold >= need_gold
            and lumber >= need_lumber
            and food >= rule.food
            and round_ >= rule.min_round
            and (rule.max_round is None or round_ <= rule.max_round)
            and all(counts.get(raw, 0) >= count for raw, count in rule.units)
            and (not rule.casters or any(counts.get(raw, 0) > 0 for raw in rule.casters))
            and (not rule.fighter or hero)
            and all(
                minimum <= techs.get(raw, 0) and (maximum is None or techs.get(raw, 0) <= maximum)
                for raw, minimum, maximum in rule.techs
            )
        )
    return tuple(mask)


@dataclass(frozen=True, slots=True)
class _InputRule:
    """The fields of one input action :func:`_input_allowed` reads, as plain values."""

    index: int
    gold: int
    lumber: int
    food: int
    min_round: int
    max_round: int | None
    units: tuple[tuple[int, int], ...]
    casters: tuple[int, ...]
    fighter: bool
    techs: tuple[tuple[int, int, int | None], ...]
    research: tuple[int, int, int, int, int, int] | None
    gate: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class _MaskPlan:
    catalog: Catalog
    basic: tuple[tuple[int, int], ...]
    basic_codes: frozenset[int]
    inputs: tuple[_InputRule, ...]


_PLANS: dict[int, _MaskPlan] = {}


def _mask_plan(catalog: Catalog) -> _MaskPlan:
    plan = _PLANS.get(id(catalog))
    if plan is not None and plan.catalog is catalog:
        return plan
    basic: list[tuple[int, int]] = []
    inputs: list[_InputRule] = []
    for index, action in enumerate(catalog.actions):
        if kind_of(action) in (ActionKind.BASIC, ActionKind.SHOP):
            if action.code in _BASIC_CODES:
                basic.append((index, action.code))
            continue
        research = action.research
        inputs.append(
            _InputRule(
                index=index,
                gold=action.gold,
                lumber=action.lumber,
                food=action.food,
                min_round=action.min_round,
                max_round=action.max_round,
                units=tuple((unit.raw_id, unit.count) for unit in action.units),
                casters=tuple(action.casters),
                fighter=action.fighter,
                techs=tuple((bound.raw_id, bound.minimum, bound.maximum) for bound in action.techs),
                research=None
                if research is None
                else (
                    research.raw_id,
                    research.levels,
                    research.base,
                    research.step,
                    research.lumber_base,
                    research.lumber_step,
                ),
                gate=None if research is None else RESEARCH_GATES.get(research.raw_id),
            )
        )
    plan = _MaskPlan(catalog, tuple(basic), frozenset(code for _, code in basic), tuple(inputs))
    _PLANS[id(catalog)] = plan
    return plan
