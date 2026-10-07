"""Player inputs of the supplied map beyond fusions: derive policy actions and bridge routes.

Everything is read from the map: the dispatch tables of war3map.j (``Jos`` handlers for sold,
trained, cast and selected rawcodes; ``KdR`` sync prefixes; ``kIs`` enter regions; ``FMv``
chat commands) and the object data (costs, requirements, researches, ability bases and
fields). The base-game defaults the object files do not repeat (an ability base's order and
target shape, an upgrade base's levels and costs) are the small tables below, taken from the
game's Units/*AbilityFunc.txt, AbilityData.slk and UpgradeData.slk; tests re-check them.

Each input keeps the map's original path: a sell/train/research order on the player's own
building, an ability order on the player's own unit, the map's own BlzSendSyncData payload,
a move into the region, a unit selection, or the registered chat handler.
"""

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Final

from ord_ai.catalog import (
    FIRST_INPUT_CODE,
    GRADUATED,
    RESEARCH,
    Action,
    ActionKind,
    Research,
    TechBound,
    unit_names,
)
from ord_ai.logbook import (
    ADMIRAL_LIFE,
    BELL_LIFE,
    CREEP_STAGE,
    GATE_LIFE,
    GOAL_IDS,
    LOGBOOK_IDS,
    SEA_KING_LIFE,
    SOUL_LIFE,
)
from ord_ai.objectdata import GameObject, MapObjects, rawcode, rawtext, read_strings, resolve
from ord_ai.protocol import ProtocolError
from ord_ai.treasure import (
    CHEST_COUNT,
    SCAN_ABILITY,
    SCAN_READY,
    TREASURE_FLOOR,
    TREASURE_IDS,
    TREASURE_SLOTS,
)

# TECH rows the bridge exported before schema 3; kept first so older records stay readable.
LEGACY_TECHS: Final = ("R00F", "R00E", "R008", "R009", "R01M", "R01N", "R00P")
TABLE_BEGIN: Final = "// BEGIN GENERATED INPUT TABLE (python -m ord_ai.build <map>; do not edit)\n"
TABLE_END: Final = "// END GENERATED INPUT TABLE\n"
_UNLIMITED: Final = 99999.0
_JOS: Final = re.compile(r"call Jos\((\d+),(\$[0-9a-f]{8}|'[^']{4}'),(\w+)\)")
_CODE_VARIABLE: Final = re.compile(r"^set (\w+)=function (\w+)$", re.MULTILINE)
_FUNCTION: Final = re.compile(r"^function (\w+) takes .*?^endfunction$", re.MULTILINE | re.DOTALL)
_SYNC: Final = re.compile(r'call KdR\("(\w+)",(\w+)\)')
_REGION: Final = re.compile(r"call kIs\((\w+(?:\[\w+\])?),(\w+)\)")
_CHAT: Final = re.compile(r'call FMv\("([^"]*)",(true|false),(\w+)\)')
_ADD_ABILITY: Final = re.compile(r"call (?:UnitAddAbility|AgR)\([^,]+,\$([0-9a-f]{8})")
_STOCK: Final = re.compile(r"call AddUnitToStock\(pp\[\w+\],\$([0-9a-f]{8}),")
_HOUSE: Final = re.compile(r"set pp\[\w+\]=CreateUnit\(Player\(\w+\),\$([0-9a-f]{8}),")
_SETUP_BUILDING: Final = re.compile(r"CreateUnit\(Player\(ULR\),\$([0-9a-f]{8}),")
_MOVER: Final = re.compile(r"if GetUnitTypeId\(GetTriggerUnit\(\)\)!=\$([0-9a-f]{8}) then")
_FOOD: Final = re.compile(
    r"^if GetPlayerState\([^\n]*?PLAYER_STATE_RESOURCE_FOOD_USED\)(<=|<)(\$[0-9a-f]+|\d+) then$"
)
_FROM_ROUND: Final = re.compile(r"^if Av<=(\d+) then$", re.MULTILINE)
_BEFORE_ROUND: Final = re.compile(r"^if Av>=(\d+)(?: or| then)", re.MULTILINE)
_CHOICE: Final = re.compile(r"\|cffffae00(\S+) :: ([^|]+)\|r 을\(를\) 선택하셨습니다")
_RANDOM_CHOICE: Final = re.compile(r"\|cffffae00랜덤선택 :: ([^|]+)\|r 항법효과로")
_COMMON_CHOICE: Final = re.compile(r"call ELv\(\w+,mB\[(\d+)\]\)")
_CREATED: Final = re.compile(r"CreateUnit\(Player\(\w+\),\$([0-9a-f]{8}),")
_CALLED: Final = re.compile(r"^call (\w+)\(\w+,\w+\)$", re.MULTILINE)

# Jos dispatch keys of the map's handler tables, and fixed counts the derivation checks.
_SELECTION_KEY: Final = 7
_SPELL_EFFECT_KEY: Final = 9
_GAMBLE_HOUSES: Final = 2
_NAVIGATION_CHOICES: Final = 3


class Target(IntEnum):
    """How the bridge chooses an ability target (the JASS table stores the number)."""

    NONE = 0  # immediate order
    ENEMY_UNIT = 1  # nearest living enemy within the cast range
    ENEMY_POINT = 2  # the position of that enemy
    OWN_UNIT = 3  # the player's best (or weakest) matching unit
    DISPLAY_UNIT = 4  # a recipe-board unit of Player(7) matching the filter
    CASTER_POINT = 5  # the caster's own position


class Shape(StrEnum):
    """Order form of an ability base."""

    NONE = "none"
    UNIT = "unit"
    POINT = "point"


@dataclass(frozen=True, slots=True)
class Base:
    """Game defaults of one ability base the map's player abilities derive from."""

    order: str  # engine order string; ANcl/AAns read theirs from the object (Ncl6/Ans5)
    shape: Shape
    targets: str = ""  # AbilityData.slk targs1 (Targets Allowed default)
    reach: float = 0.0  # AbilityData.slk Rng1 (cast range default)


# Units/*AbilityFunc.txt Order keys and AbilityData.slk targs1/Rng1. Awfb/ACfb (firebolt) and
# Afod (fingerofdeath) keep their order strings in the engine; AHta (Reveal) has no Order key:
# "detectaoe" (852015) is the engine's Reveal order and is not verified in the real game.
BASES: Final = {
    "AHwe": Base("waterelemental", Shape.NONE),
    "Aroa": Base("roar", Shape.NONE),
    "AHtb": Base("thunderbolt", Shape.UNIT, "air,ground,debris,enemy,neutral,organic", 600),
    "AHtc": Base("thunderclap", Shape.NONE),
    "ACsi": Base("silence", Shape.POINT, "air,ground,enemy,organic,neutral", 700),
    "AEfk": Base("fanofknives", Shape.NONE),
    "AOeq": Base("earthquake", Shape.POINT, "ground,structure,debris,enemy,tree", 1000),
    "Awfb": Base("firebolt", Shape.UNIT, "air,ground,organic,enemy,neutral", 800),
    "ACfb": Base("firebolt", Shape.UNIT, "air,ground,organic,enemy,neutral", 800),
    "Afod": Base("fingerofdeath", Shape.UNIT, "air,ground,nonhero,structure,ancient", 800),
    "AHpx": Base("summonphoenix", Shape.NONE),
    "Arsg": Base("spiritwolf", Shape.NONE),
    "ANcl": Base("channel", Shape.NONE, "", 500),
    "AAns": Base("neutralspell", Shape.NONE),
    "AHta": Base("detectaoe", Shape.POINT, "", _UNLIMITED),
    "AIlp": Base("", Shape.UNIT, "ground,air,ward", _UNLIMITED),
    "Amls": Base("magicleash", Shape.UNIT, "air,enemy,organic", 550),
    "ANcs": Base("clusterrockets", Shape.POINT, "air,ground,enemy,neutral,structure", 800),
    "Absk": Base("berserk", Shape.NONE),
    "AOws": Base("stomp", Shape.NONE),
    "Auhf": Base("unholyfrenzy", Shape.UNIT, "air,ground,organic", 500),
    "ANsg": Base("summongrizzly", Shape.NONE),
    "AEbl": Base("blink", Shape.POINT, "", _UNLIMITED),
    "Asds": Base("selfdestruct", Shape.NONE),
    "Ainf": Base("innerfire", Shape.UNIT, "air,ground,friend,neutral,self", 500),
    "Ablo": Base("bloodlust", Shape.UNIT, "air,ground,friend,organic,self,neutral", 600),
    "Acri": Base("cripple", Shape.UNIT, "air,ground,enemy,neutral", 600),
    "Arej": Base("rejuvination", Shape.UNIT, "air,ground,friend,self,organic,neutral", 400),
    "ACbk": Base("blackarrow", Shape.UNIT, "air,ground,enemy,organic,neutral", 600),
    "ACbz": Base("blizzard", Shape.POINT, "", 800),
    "AHbz": Base("blizzard", Shape.POINT, "", 800),
    "ANab": Base("acidbomb", Shape.UNIT, "ground,enemy,neutral,organic,air", 700),
    "Acht": Base("howlofterror", Shape.NONE),
    "AEme": Base("metamorphosis", Shape.NONE),
}
# ORDAI_Input dispatch numbers of the bridge's input table (parent key 2).
JASS_KINDS: Final = {
    ActionKind.SELL: 1,
    ActionKind.TRAIN: 2,
    ActionKind.RESEARCH: 3,
    ActionKind.ABILITY: 4,
    ActionKind.SYNC: 5,
    ActionKind.REGION: 6,
    ActionKind.SELECT: 7,
    ActionKind.CHAT: 8,
    ActionKind.ATTACK: 9,
    ActionKind.TREASURE: 10,
}
_CHANNEL_SHAPES: Final = {0: Shape.NONE, 1: Shape.UNIT, 2: Shape.POINT, 3: Shape.UNIT}
# Human blacksmith and arcane vault bases: the map's player buildings (never in UNIT rows).
BUILDING_BASES: Final = frozenset({"hbla", "hvlt"})


@dataclass(frozen=True, slots=True)
class UpgradeDefault:
    """UpgradeData.slk values a w3q object keeps unless it overrides them."""

    levels: int
    gold: int
    gold_step: int
    lumber: int
    lumber_step: int


UPGRADE_DEFAULTS: Final = {
    "Rugf": UpgradeDefault(1, 100, 0, 150, 0),
    "Rhgb": UpgradeDefault(1, 150, 50, 100, 0),
    "Rhra": UpgradeDefault(3, 100, 75, 50, 125),
    "Rhpt": UpgradeDefault(2, 100, 0, 50, 100),
    "Rhfc": UpgradeDefault(1, 100, 0, 150, 0),
}


@dataclass(frozen=True, slots=True)
class TargetRule:
    """A target choice read from the ability's handler, where Targets Allowed is too loose."""

    target: Target
    filters: tuple[str, ...] = ()  # unit types or abilities the target needs (any of them)
    weakest: bool = False
    common: bool = False  # UNIT_TYPE_UNDEAD: this map's common units


# Handler checks (war3map.j line of the check): the target must be one of these.
TARGET_RULES: Final = {
    # rHv 22693: only recipe-board ships (Player(7)) h060, h03P, h03Q, h03X, h03L, h03U.
    "A0DI": TargetRule(Target.DISPLAY_UNIT, ("h060", "h03P", "h03Q", "h03X", "h03L", "h03U")),
    # IAR 16052: a recipe-board or own 'special' unit (A0BA, h01C, h015); copies its type.
    "A09C": TargetRule(Target.DISPLAY_UNIT, ("A0BA", "h01C", "h015")),
    # uiR 62652: a recipe-board or own legend unit (A912).
    "A114": TargetRule(Target.DISPLAY_UNIT, ("A912",)),
    # Jcs 17360: an own 'special' unit.
    "A12T": TargetRule(Target.OWN_UNIT, ("A0BA", "h01C", "h015")),
    # M4R 19882: decomposes an own special or rare unit (A0BA, A0B9, h01C, h015).
    "A0J2": TargetRule(Target.OWN_UNIT, ("A0BA", "A0B9", "h01C", "h015"), weakest=True),
    # CtR 8360: removes an own common unit (UNIT_TYPE_UNDEAD).
    "A0WM": TargetRule(Target.OWN_UNIT, weakest=True, common=True),
    # wjv 64485: eats an own pirate ship h060.
    "A0YS": TargetRule(Target.OWN_UNIT, ("h060",)),
    # Wfv 34117: an own Brook, h01N or h00D.
    "A0XC": TargetRule(Target.OWN_UNIT, ("h01N", "h00D")),
    # NMR 21305: an own h043, h032, h030 or h03G.
    "A13A": TargetRule(Target.OWN_UNIT, ("h043", "h032", "h030", "h03G")),
    # TTs 30801: searches for treasure around the caster.
    "A05D": TargetRule(Target.CASTER_POINT),
}


@dataclass(frozen=True, slots=True)
class Route:
    """What the bridge needs to perform one input; rendered into the JASS input table."""

    kind: ActionKind
    raw_id: int = 0  # sold/trained unit, research, ability, moved unit type, selected unit
    order: str = ""  # ability order string
    target: Target = Target.NONE
    reach: int = 0  # cast range for target search; 0 means anywhere
    filters: tuple[int, ...] = ()
    weakest: bool = False
    common: bool = False
    self_allowed: bool = False
    items: tuple[int, ...] = ()  # item types that carry the ability (cast by using the item)
    buildings: tuple[int, ...] = ()  # shop, gamble house or laboratory unit types
    rect: str = ""  # JASS expression of the region
    prefix: str = ""  # sync prefix
    payload: int = 0  # sync payload, objective number or live chest index
    handler: str = ""  # chat handler function


@dataclass(frozen=True, slots=True)
class PlayerInput:
    """One policy action and the route the bridge takes for it."""

    action: Action
    route: Route


@dataclass(frozen=True, slots=True)
class Excluded:
    """An original input left out of the catalog, with the reason (README table)."""

    kind: str
    inputs: str
    reason: str


# Inputs without game-state effect in a solo offline game (the README lists the same table).
EXCLUDED: Final = (
    Excluded("chat", "-골드/-금/-돈 N, -목재 N (PyR, DIs)", "Resource transfer to another player"),
    Excluded("chat", "-점수판, -점수판흔함 (wHv, GhR)", "Score board display only"),
    Excluded("chat", "-시야 (Pws)", "Camera/sight toggle only"),
    Excluded("chat", "-스토리 (L7v)", "Story record text only"),
    Excluded("chat", "-리롤 (Fcs)", "Prints the remaining reroll count only"),
    Excluded("chat", "-문, -황금종 (mFR, GYR)", "Prints a destructable's life only"),
    Excluded("chat", "흔함 (vav)", "Prints the common unit counts only"),
    Excluded("chat", "-보물표시 (Xqs)", "Adds treasure marker effects only"),
    Excluded("sync", "CMBT_INDX (Flv)", "Toggles recipe UI, vibration, alarms, util board"),
    Excluded("sync", "PAGE_INDX (bBR)", "Quest board page flip"),
    Excluded("sync", "UIPD_*/UIPB_EBCL (beri shop)", "Beri skin/title/code shop and its UI"),
    Excluded("sync", "UIEM_PICK (OeR)", "Emote"),
    Excluded("sync", "CODE_LOAD/CODE_LADD/CODE_SAVE, LogIn", "Save, load and login"),
    Excluded("esc", "END_CINEMATIC (hKR, BNR, FDs, r3v, djR)", "Only hides UI panels"),
    Excluded("dialog", "경단먹이기 dialog (JJv 17183)", "Needs another player's unit"),
    Excluded("region", "32 RACE_ORC waypoints (jm..qd)", "Lane monster pathing"),
    Excluded("region", "GetWorldBounds (fGs)", "Fires for every unit entering the map"),
    Excluded("lobby", "Difficulty u000-u006, Isekai u00C", "Chosen by the curriculum stage"),
)
# Chat handlers and sync prefixes that are policy actions or exclusions (coverage test).
CHAT_ACTIONS: Final = {"QVs": "-offline (오프라인 모드)"}
EXCLUDED_CHATS: Final = frozenset(
    {"PyR", "DIs", "wHv", "GhR", "Pws", "L7v", "Fcs", "mFR", "GYR", "vav", "Xqs"}
)
EXCLUDED_SYNCS: Final = frozenset(
    {"CMBT_INDX", "PAGE_INDX", "UIEM_PICK", "LogIn", "UIPB_EBCL"}
    | {f"UIPD_{name}" for name in ("EBCL", "CBCL", "BYCD", "BYTL", "TLCL", "SKCL")}
)
QUEST_PREFIX: Final = "QUST_INDX"
QUEST_SLOTS: Final = 3
RANDOM_NAVIGATION: Final = "NAVI_5IDX"
# Enter regions that move a player's fighter (the handler takes any non-dummy unit).
FIGHTER_REGIONS: Final = {
    "OP[s1R]": ("OP[0]", "귀환 발판: 시작 위치로 이동"),
    "Zd": ("Zd", "이동 발판: rd로 이동"),
    "Dd": ("Dd", "포네그리프 해독"),
}
# Regions the bridge's base actions already use (fY summon/evolve, CY, aY, nP story, lines).
BASE_REGIONS: Final = frozenset({"fY", "CY", "aY", "nP[s1R]", "EY", "id", "md"})
SELECT_RECT: Final = "Zv[0]"  # ywv 78554: the auto-take-out unit must be in Zv[player]


@dataclass(frozen=True, slots=True)
class _Script:
    """Indexes of war3map.j used by the derivation."""

    text: str
    functions: dict[str, str]
    code_variables: dict[str, str]
    added: frozenset[str]

    @classmethod
    def read(cls, text: str) -> "_Script":
        return cls(
            text=text,
            functions={match.group(1): match.group(0) for match in _FUNCTION.finditer(text)},
            code_variables=dict(_pairs(_CODE_VARIABLE, text)),
            added=frozenset(_hex(value) for value in _firsts(_ADD_ABILITY, text)),
        )

    def handler(self, variable: str) -> str:
        """The function a code variable points to."""
        return self.code_variables.get(variable, variable)

    def body(self, variable: str) -> str:
        return self.functions.get(self.handler(variable), "")


def _firsts(pattern: re.Pattern[str], text: str) -> list[str]:
    """The first group of every match."""
    return [str(match.group(1)) for match in pattern.finditer(text)]


def _pairs(pattern: re.Pattern[str], text: str) -> list[tuple[str, str]]:
    """The first two groups of every match."""
    return [(str(match.group(1)), str(match.group(2))) for match in pattern.finditer(text)]


def _dispatch(text: str) -> list[tuple[int, str, str]]:
    """``Jos(key, rawcode, handler)`` registrations: 7 select, 9 spell, 10 sell, 11 train."""
    return [
        (int(match.group(1)), str(match.group(2)), str(match.group(3)))
        for match in _JOS.finditer(text)
    ]


def _hex(value: str) -> str:
    return rawtext(int(value, 16))


def _code(value: str) -> str:
    return _hex(value[1:]) if value.startswith("$") else value.strip("'")


def _gold(item: GameObject | None, name: str) -> int:
    value = item.number(name) if item is not None else None
    return int(value) if value is not None else 0


def _requirements(item: GameObject | None) -> tuple[TechBound, ...]:
    if item is None:
        return ()
    return tuple(TechBound(raw_id=rawcode(code)) for code in item.rawcodes("ureq"))


def _house_types(script: _Script) -> tuple[str, str]:
    """Gamble house 1 (created at setup) and gamble house 2 (after 35000 gamble gold)."""
    found = list(dict.fromkeys(_hex(value) for value in _firsts(_HOUSE, script.text)))
    if len(found) != _GAMBLE_HOUSES:
        raise ProtocolError(detail="Expected the two gamble house types of this map")
    return found[0], found[1]


def _setup_buildings(script: _Script) -> tuple[str, ...]:
    """Unit types the map creates for every player at game start (houses, labs, helper)."""
    setup = next(
        (body for body in script.functions.values() if "set rp[ULR]=CreateUnit(" in body), ""
    )
    return tuple(dict.fromkeys(_hex(value) for value in _firsts(_SETUP_BUILDING, setup)))


def _house_two_only(script: _Script, code: str, house_two: str) -> bool:
    """Whether every function stocking ``code`` at pp[] is guarded by or creates house 2."""
    guard = f"${rawcode(house_two):08x}"
    stocking = [
        body
        for body in script.functions.values()
        if any(_hex(value) == code for value in _firsts(_STOCK, body))
    ]
    return bool(stocking) and all(guard in body for body in stocking)


def _unit_name(names: dict[int, str], code: str) -> str:
    return names.get(rawcode(code), code)


def _shop_inputs(
    script: _Script, objects: MapObjects, names: dict[int, str]
) -> list[tuple[Action, Route]]:
    """Unit-items sold (Jos 10) and trained (Jos 11) at the player's own buildings."""
    house_one, house_two = _house_types(script)
    buildings = (*_setup_buildings(script), house_two)
    stocked = frozenset(_hex(value) for value in _firsts(_STOCK, script.text))
    existing = {"h06F", "H0AZ", "H0B0"}  # SHOP_ACTIONS codes 8, 9, 13
    found: list[tuple[Action, Route]] = []
    for key, kind, field_name in ((10, ActionKind.SELL, "useu"), (11, ActionKind.TRAIN, "utra")):
        codes = [_code(raw) for jos, raw, _ in _dispatch(script.text) if jos == key]
        for code in dict.fromkeys(codes):
            if code in existing:
                continue
            sellers = [
                building
                for building in buildings
                if code in objects.units.get(building, GameObject(base="")).rawcodes(field_name)
            ]
            if not sellers and code in stocked:
                sellers = (
                    [house_two]
                    if _house_two_only(script, code, house_two)
                    else [
                        house_one,
                        house_two,
                    ]
                )
            if not sellers:
                raise ProtocolError(detail=f"No player building offers {code}")
            item = objects.units.get(code)
            techs = list(_requirements(item))
            if sellers == [house_one]:
                techs.append(TechBound(raw_id=GRADUATED, minimum=0, maximum=0))
            elif sellers == [house_two]:
                techs.append(TechBound(raw_id=GRADUATED))
            action = Action(
                code=0,
                kind=kind,
                name=f"{kind.value}: {_unit_name(names, code)} ({code})",
                raw_id=rawcode(code),
                gold=_gold(item, "ugol"),
                lumber=_gold(item, "ulum"),
                techs=tuple(techs),
            )
            route = Route(
                kind=kind,
                raw_id=rawcode(code),
                buildings=tuple(rawcode(building) for building in sellers),
            )
            found.append((action, route))
    return found


def _field(item: GameObject, name: str, fallback: int) -> int:
    number = item.number(name)
    return int(number) if number is not None else fallback


def _research_inputs(
    script: _Script, objects: MapObjects, strings: dict[int, str]
) -> list[tuple[Action, Route]]:
    """Researches of the player's upgrade houses and laboratory, besides codes 10-12."""
    existing = {research.raw_id for research in RESEARCH.values()}
    found: list[tuple[Action, Route]] = []
    for building in _setup_buildings(script):
        lab = objects.units.get(building)
        for code in lab.rawcodes("ures") if lab is not None else ():
            if rawcode(code) in existing:
                continue
            upgrade = objects.upgrades[code]
            default = UPGRADE_DEFAULTS.get(upgrade.base)
            if default is None:
                raise ProtocolError(detail=f"No upgrade defaults for base {upgrade.base}")

            research = Research(
                raw_id=rawcode(code),
                levels=_field(upgrade, "glvl", default.levels),
                base=_field(upgrade, "gglb", default.gold),
                step=_field(upgrade, "gglm", default.gold_step),
                lumber_base=_field(upgrade, "glmb", default.lumber),
                lumber_step=_field(upgrade, "glmm", default.lumber_step),
            )
            name = resolve(upgrade.text("gnam", 1) or code, strings)
            action = Action(
                code=0,
                kind=ActionKind.RESEARCH,
                name=f"research: {name} ({code})",
                raw_id=rawcode(code),
                research=research,
            )
            route = Route(
                kind=ActionKind.RESEARCH, raw_id=rawcode(code), buildings=(rawcode(building),)
            )
            found.append((action, route))
    return found


def _returns_inside(lines: list[str]) -> bool:
    """Whether the if-block starting at lines[0] returns before its own endif."""
    depth = 0
    for line in lines:
        if line.startswith("if ") and line.endswith(" then"):
            depth += 1
        elif line == "endif":
            depth -= 1
            if depth == 0:
                return False
        elif line == "return" and depth == 1:
            return True
    return False


def _food_needed(body: str) -> int:
    """Trait points an ability handler demands before it does anything (early return)."""
    lines = body.split("\n")
    for index, line in enumerate(lines):
        match = _FOOD.match(line)
        if match is None or not _returns_inside(lines[index:]):
            continue
        operator, number = match.groups()
        amount = int(number[1:], 16) if number.startswith("$") else int(number)
        return amount + 1 if operator == "<=" else amount
    return 0


def _owners(objects: MapObjects) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for code, unit in objects.units.items():
        for ability in unit.rawcodes("uabi"):
            owners.setdefault(ability, []).append(code)
    return owners


def _player_unit(objects: MapObjects, code: str) -> bool:
    """A unit type the player can own and command: h/H rawcode, not a locust dummy."""
    unit = objects.units.get(code)
    return code[:1] in ("h", "H") and unit is not None and "Aloc" not in unit.rawcodes("uabi")


def _ability_route(code: str, ability: GameObject, items: tuple[int, ...]) -> Route:
    base = BASES.get(ability.base)
    if base is None:
        raise ProtocolError(detail=f"Ability {code} has an unsupported base {ability.base}")
    order = base.order
    shape = base.shape
    if ability.base == "ANcl":
        order = ability.text("Ncl6", 1) or order
        shape = _CHANNEL_SHAPES.get(int(ability.number("Ncl2", 1) or 0), Shape.NONE)
    elif ability.base == "AAns":
        order = ability.text("Ans5", 1) or order
    targets = {token for token in (ability.text("atar", 1) or base.targets).split(",") if token}
    reach = ability.number("aran", 1)
    reach = base.reach if reach is None else reach
    rule = TARGET_RULES.get(code)
    if rule is not None:
        target = rule.target
    elif shape is Shape.NONE:
        target = Target.NONE
    elif shape is Shape.POINT:
        target = Target.ENEMY_POINT
    else:
        friendly = targets & {"friend", "allies", "player", "self"}
        hostile = targets & {"enemy", "enemies", "neutral"}
        target = Target.ENEMY_UNIT if hostile and not friendly else Target.OWN_UNIT
    return Route(
        kind=ActionKind.ABILITY,
        raw_id=rawcode(code),
        order=order,
        target=target,
        reach=0 if target is Target.NONE or reach <= 0 or reach >= _UNLIMITED else int(reach),
        filters=tuple(rawcode(value) for value in rule.filters) if rule else (),
        weakest=rule.weakest if rule else False,
        common=rule.common if rule else False,
        self_allowed="self" in targets,
        items=items,
    )


def _ability_inputs(
    script: _Script,
    objects: MapObjects,
    strings: dict[int, str],
    names: dict[int, str],
    fusions: set[int],
) -> list[tuple[Action, Route]]:
    """Abilities with a spell handler (Jos 9) and other castable player-unit abilities."""
    owners = _owners(objects)
    handlers: dict[str, list[str]] = {}
    for jos, raw, variable in _dispatch(script.text):
        if jos == _SPELL_EFFECT_KEY:
            handlers.setdefault(_code(raw), []).append(variable)
    native = sorted(
        {
            ability
            for unit in objects.units
            if _player_unit(objects, unit)
            for ability in objects.units[unit].rawcodes("uabi")
            if ability not in handlers
            and ability in objects.abilities
            and objects.abilities[ability].base in BASES
        }
    )
    carried = {
        code: tuple(
            rawcode(item) for item, data in objects.items.items() if code in data.rawcodes("iabi")
        )
        for code in handlers
    }
    found: list[tuple[Action, Route]] = []
    for code in [*handlers, *native]:
        if rawcode(code) in fusions:
            continue
        ability = objects.abilities[code]
        route = _ability_route(code, ability, carried.get(code, ()))
        holders = [unit for unit in owners.get(code, []) if _player_unit(objects, unit)]
        buildings = any(objects.units[unit].base in BUILDING_BASES for unit in holders)
        casters = (
            ()
            if code in script.added or buildings or route.items
            else tuple(sorted(rawcode(unit) for unit in holders))
        )
        food = max(
            (_food_needed(script.body(variable)) for variable in handlers.get(code, [])), default=0
        )
        # Required upgrades (areq) are TECH rows; required unit types stay with the map.
        techs = tuple(
            TechBound(raw_id=rawcode(tech))
            for tech in ability.rawcodes("areq")
            if tech in objects.upgrades
        )
        label = resolve(ability.text("anam") or code, strings)
        where = f" @ {_unit_name(names, holders[0])}" if holders else ""
        action = Action(
            code=0,
            kind=ActionKind.ABILITY,
            name=f"ability: {label} ({code}){where}",
            raw_id=rawcode(code),
            casters=casters,
            food=food,
            techs=techs,
        )
        found.append((action, route))
    return found


def _round_limits(body: str) -> tuple[int, int | None]:
    start = _FROM_ROUND.search(body)
    stop = _BEFORE_ROUND.search(body)
    return (
        int(start.group(1)) + 1 if start else 0,
        int(stop.group(1)) - 1 if stop else None,
    )


def _sync_inputs(script: _Script) -> list[tuple[Action, Route]]:
    """Navigation choices NAVI_1IDX..NAVI_5IDX and the quest reroll QUST_INDX."""
    registered = dict(_pairs(_SYNC, script.text))
    found: list[tuple[Action, Route]] = []
    for prefix in sorted(name for name in registered if name.startswith("NAVI_")):
        body = script.body(registered[prefix])
        if prefix == RANDOM_NAVIGATION:
            options = list(dict.fromkeys(_firsts(_RANDOM_CHOICE, script.text)))
            options = [f"랜덤 :: {name}" for name in options]
        else:
            called = [name for name in _firsts(_CALLED, body) if name in script.functions]
            choices = _pairs(_CHOICE, script.functions[called[0]]) if called else []
            options = [f"{group} :: {name}" for group, name in dict.fromkeys(choices)]
        if len(options) != _NAVIGATION_CHOICES:
            raise ProtocolError(detail=f"Unexpected navigation choices for {prefix}")
        minimum, maximum = _round_limits(body)
        for payload, option in enumerate(options, start=1):
            action = Action(
                code=0,
                kind=ActionKind.SYNC,
                name=f"sync: {prefix} {option}",
                min_round=minimum,
                max_round=maximum,
            )
            found.append((action, Route(ActionKind.SYNC, prefix=prefix, payload=payload)))
    if QUEST_PREFIX in registered:
        body = script.body(registered[QUEST_PREFIX])
        minimum, maximum = _round_limits(body)
        if f">={QUEST_SLOTS}" not in body:
            raise ProtocolError(detail="Unexpected quest slot count")
        for slot in range(QUEST_SLOTS):
            action = Action(
                code=0,
                kind=ActionKind.SYNC,
                name=f"sync: {QUEST_PREFIX} 퀘스트 {slot + 1} 변경",
                min_round=minimum,
                max_round=maximum,
            )
            found.append((action, Route(ActionKind.SYNC, prefix=QUEST_PREFIX, payload=slot)))
    return found


def _pad_reward(body: str, names: dict[int, str]) -> str:
    """What a wisp pad gives: common choice mB[k], a fixed unit or a pool draw."""
    common = _COMMON_CHOICE.search(body)
    if common is not None:
        return f"흔함 {common.group(1)}"
    created = _CREATED.search(body)
    if created is not None:
        return _unit_name(names, _hex(created.group(1)))
    return "무작위"


def _region_inputs(script: _Script, names: dict[int, str]) -> list[tuple[Action, Route]]:
    """Wisp pads (the handler needs one wisp type) and the fighter teleports."""
    found: list[tuple[Action, Route]] = []
    for rect, variable in _pairs(_REGION, script.text):
        if rect in BASE_REGIONS:
            continue
        mover = _MOVER.search(script.body(variable))
        if rect in FIGHTER_REGIONS:
            expression, label = FIGHTER_REGIONS[rect]
            action = Action(
                code=0, kind=ActionKind.REGION, name=f"region: {label} ({rect})", fighter=True
            )
            found.append((action, Route(ActionKind.REGION, rect=expression)))
        elif mover is not None:
            wisp = _hex(mover.group(1))
            reward = _pad_reward(script.body(variable), names)
            action = Action(
                code=0,
                kind=ActionKind.REGION,
                name=f"region: {_unit_name(names, wisp)} -> {rect} ({reward})",
                raw_id=rawcode(wisp),
                casters=(rawcode(wisp),),
            )
            found.append((action, Route(ActionKind.REGION, raw_id=rawcode(wisp), rect=rect)))
        else:
            raise ProtocolError(detail=f"Unclassified enter region {rect}")
    return found


def _select_inputs(script: _Script, names: dict[int, str]) -> list[tuple[Action, Route]]:
    """Units whose selection runs a map handler (Jos 7)."""
    found: list[tuple[Action, Route]] = []
    for jos, raw, variable in _dispatch(script.text):
        if jos != _SELECTION_KEY:
            continue
        code = _code(raw)
        if "Zv[" not in script.body(variable):
            raise ProtocolError(detail=f"Unexpected selection handler for {code}")
        action = Action(
            code=0,
            kind=ActionKind.SELECT,
            name=f"select: {_unit_name(names, code)} ({code})",
            raw_id=rawcode(code),
        )
        found.append((action, Route(ActionKind.SELECT, raw_id=rawcode(code), rect=SELECT_RECT)))
    return found


def _chat_inputs(script: _Script) -> list[tuple[Action, Route]]:
    """Chat commands with a game-state effect (the rest are EXCLUDED)."""
    handlers = dict.fromkeys(
        script.handler(match.group(3)) for match in _CHAT.finditer(script.text)
    )
    found: list[tuple[Action, Route]] = []
    for handler, label in CHAT_ACTIONS.items():
        if handler not in handlers:
            raise ProtocolError(detail=f"Chat handler {handler} is not registered")
        minimum, maximum = _round_limits(script.functions[handler])
        action = Action(
            code=0,
            kind=ActionKind.CHAT,
            name=f"chat: {label}",
            min_round=minimum,
            max_round=maximum,
        )
        found.append((action, Route(ActionKind.CHAT, handler=handler)))
    return found


@dataclass(frozen=True, slots=True)
class Objective:
    """A 항해목표 target a fighter attacks; ``target`` is ORDAI_Objective's number."""

    target: int
    label: str
    code: str  # rawcodes of the target (destructable or unit types)
    placed: tuple[str, ...]  # war3map.j lines that create it
    life: int  # logbook row with the target's life percent
    goal: int | None  # GOALS index whose state must still be open


# The mission targets of T004-T009 (see ord_ai.logbook). None of them has a player input in
# the map: players walk or blink their units there and attack, so these are attack orders.
OBJECTIVES: Final = (
    Objective(1, "황금 종", "B005", ("set Hd=CreateDestructable($42303035,",), BELL_LIFE, 5),
    Objective(2, "영혼 해방", "B008", ("set Bd=CreateDestructableZ($42303038,",), SOUL_LIFE, 6),
    Objective(3, "정의의 문", "B004", ("set Ud=CreateDestructable($42303034,",), GATE_LIFE, 8),
    Objective(
        4, "해왕류 퇴치", "o02N", ("set jB=CreateUnit(Player(5),$6f30324e,",), SEA_KING_LIFE, 7
    ),
    Objective(
        5,
        "해군대장",
        "o02Q/o02M/o02R",
        tuple(
            f"CreateUnit(Player(5),${rawcode(code):08x},GetRectCenterX(rd),"
            for code in ("o02Q", "o02M", "o02R")
        ),
        ADMIRAL_LIFE,
        9,
    ),
    Objective(
        6,
        "크립 처치",
        "n00E/n00F/n00G",
        tuple(f"CreateUnit(Player(5),${rawcode(code):08x}," for code in ("n00E", "n00F", "n00G")),
        CREEP_STAGE,
        None,
    ),
)


def _objective_inputs(script: _Script) -> list[tuple[Action, Route]]:
    """Attack a 항해목표 target with the strongest fighter while it is alive (and open)."""
    found: list[tuple[Action, Route]] = []
    for objective in OBJECTIVES:
        if not all(line in script.text for line in objective.placed):
            raise ProtocolError(detail=f"The map no longer places {objective.code}")
        techs = [TechBound(raw_id=objective.life)]
        if objective.goal is not None:
            techs.append(TechBound(raw_id=GOAL_IDS[objective.goal], minimum=0, maximum=0))
        action = Action(
            code=0,
            kind=ActionKind.ATTACK,
            name=f"attack: {objective.label} ({objective.code})",
            fighter=True,
            techs=tuple(techs),
        )
        found.append((action, Route(ActionKind.ATTACK, payload=objective.target)))
    return found


# The map globals the chest actions read (Ur/Br/Qr slots, cP square, sp 기록지침, IY rect).
_TREASURE_GLOBALS: Final = (
    "real array Ur",
    "real array Br",
    "integer Qr=0",
    "real array cP",
    "unit array sp",
    "set IY=Rect(-6016.,-2336.,3744.,6080.)",
)


def _treasure_inputs(script: _Script, objects: MapObjects) -> list[tuple[Action, Route]]:
    """탐색 at the k-th live hidden chest; open while the bridge reports it ready and live."""
    if not all(line in script.text for line in _TREASURE_GLOBALS):
        raise ProtocolError(detail="The map no longer keeps hidden chests in Ur/Br/Qr")
    base = BASES[objects.abilities[SCAN_ABILITY].base]
    if base.shape is not Shape.POINT:
        raise ProtocolError(detail=f"{SCAN_ABILITY} is no longer a point-target ability")
    found: list[tuple[Action, Route]] = []
    for slot in range(TREASURE_SLOTS):
        action = Action(
            code=TREASURE_FLOOR + slot,
            kind=ActionKind.TREASURE,
            name=f"탐색: 남은 상자 {slot}",
            raw_id=rawcode(SCAN_ABILITY),
            techs=(
                TechBound(raw_id=SCAN_READY),
                TechBound(raw_id=CHEST_COUNT, minimum=slot + 1),
            ),
        )
        route = Route(
            ActionKind.TREASURE, raw_id=rawcode(SCAN_ABILITY), order=base.order, payload=slot
        )
        found.append((action, route))
    return found


def derive_inputs(
    script: str, strings: str, objects: MapObjects, fusions: set[int]
) -> tuple[PlayerInput, ...]:
    """All non-fusion player inputs of the map, with wire codes from FIRST_INPUT_CODE up.

    The chest actions follow with their fixed codes TREASURE_FLOOR and up.
    """
    indexed = _Script.read(script)
    texts = read_strings(strings)
    names = unit_names(strings)
    families = (
        _shop_inputs(indexed, objects, names)
        + _research_inputs(indexed, objects, texts)
        + _ability_inputs(indexed, objects, texts, names, fusions)
        + _sync_inputs(indexed)
        + _region_inputs(indexed, names)
        + _select_inputs(indexed, names)
        + _chat_inputs(indexed)
        # Appended last: every earlier input keeps its wire code.
        + _objective_inputs(indexed)
    )
    if FIRST_INPUT_CODE + len(families) > TREASURE_FLOOR:
        raise ProtocolError(detail="Player inputs reach the fixed chest action codes")
    numbered = [
        (action.model_copy(update={"code": FIRST_INPUT_CODE + index}), route)
        for index, (action, route) in enumerate(families)
    ]
    return tuple(
        PlayerInput(action=action, route=route)
        for action, route in numbered + _treasure_inputs(indexed, objects)
    )


_SENT_SYNC: Final = re.compile(r'BlzSendSyncData\("(\w+)"')
_REGISTERED_SYNC: Final = re.compile(
    r'BlzTriggerRegisterPlayerSyncEvent\(\w+,Player\(\w+\),"(\w+)"'
)


def sync_prefixes(script: str) -> tuple[str, ...]:
    """Every literal sync prefix the map registers or sends (KdR, its own triggers, UI)."""
    found = _firsts(_SYNC, script) + _firsts(_SENT_SYNC, script) + _firsts(_REGISTERED_SYNC, script)
    return tuple(sorted(dict.fromkeys(found)))


def chat_commands(script: str) -> tuple[tuple[str, bool, str], ...]:
    """``FMv`` chat registrations as (text, exact match, handler function)."""
    indexed = _Script.read(script)
    return tuple(
        (str(match.group(1)), match.group(2) == "true", indexed.handler(str(match.group(3))))
        for match in _CHAT.finditer(script)
    )


def exported_techs(inputs: tuple[PlayerInput, ...]) -> tuple[int, ...]:
    """TECH rows the masks read: the legacy seven, then every tech a new action bounds.

    The synthetic 기록지침 rows (``ord_ai.logbook``) and chest rows (``ord_ai.treasure``) are
    exported by ORDAI_Logbook and ORDAI_Treasure instead.
    """
    techs = [rawcode(code) for code in LEGACY_TECHS]
    for item in inputs:
        research = item.action.research
        techs.extend(bound.raw_id for bound in item.action.techs)
        if research is not None:
            techs.append(research.raw_id)
    synthetic = LOGBOOK_IDS | TREASURE_IDS
    return tuple(tech for tech in dict.fromkeys(techs) if tech not in synthetic)


def _literal(value: int) -> str:
    text = rawtext(value)
    return f"'{text}'" if text.isalnum() else str(value)


def _flags(route: Route) -> int:
    return (
        (1 if route.weakest else 0)
        | (2 if route.common else 0)
        | (4 if route.self_allowed else 0)
        | (8 if route.items else 0)
    )


def _route_lines(item: PlayerInput) -> list[str]:
    code, route = item.action.code, item.route
    kind = JASS_KINDS[route.kind]
    raw = _literal(route.raw_id) if route.raw_id else "0"
    lines = [f"    // {rawtext(route.raw_id) if route.raw_id else route.kind.value}"]
    match route.kind:
        case ActionKind.SELL | ActionKind.TRAIN | ActionKind.RESEARCH:
            first, second = (*route.buildings, 0, 0)[:2]
            shops = ", ".join(_literal(value) if value else "0" for value in (first, second))
            lines.append(f"    call ORDAI_Route({code}, {kind}, {raw}, {shops}, 0, 0)")
        case ActionKind.ABILITY:
            order = f'OrderId("{route.order}")' if route.order else "0"
            data = f"{order}, {int(route.target)}, {route.reach}, {_flags(route)}"
            lines.append(f"    call ORDAI_Route({code}, {kind}, {raw}, {data})")
            lines.extend(
                f"    call ORDAI_Filter({code}, {_literal(value)})" for value in route.filters
            )
            lines.extend(f"    call ORDAI_Item({code}, {_literal(value)})" for value in route.items)
        case ActionKind.SYNC:
            lines.append(f"    call ORDAI_Route({code}, {kind}, 0, {route.payload}, 0, 0, 0)")
            lines.append(f'    call SaveStr(ORDAI_data, 10, {code}, "{route.prefix}")')
        case ActionKind.CHAT:
            lines.append(f"    call ORDAI_Route({code}, {kind}, 0, 0, 0, 0, 0)")
            lines.append(f'    call SaveStr(ORDAI_data, 11, {code}, "{route.handler}")')
        case ActionKind.ATTACK:
            lines.append(f"    call ORDAI_Route({code}, {kind}, 0, {route.payload}, 0, 0, 0)")
        case ActionKind.TREASURE:
            order = f'OrderId("{route.order}")'
            lines.append(
                f"    call ORDAI_Route({code}, {kind}, {raw}, {order}, {route.payload}, 0, 0)"
            )
        case ActionKind.REGION | ActionKind.SELECT:
            lines.append(f"    call ORDAI_Route({code}, {kind}, {raw}, 0, 0, 0, 0)")
        case ActionKind.BASIC | ActionKind.FUSION | ActionKind.SHOP:
            raise ProtocolError(detail="Base, fusion and shop actions have no input route")
    return lines


def render_input_table(inputs: tuple[PlayerInput, ...]) -> str:
    """The generated JASS block of bridge_actions.j for these inputs."""
    techs = exported_techs(inputs)
    lines = [
        TABLE_BEGIN.rstrip("\n"),
        "function ORDAI_InitInputs takes nothing returns nothing",
    ]
    lines.extend(
        f"    set ORDAI_techs[{index}] = {_literal(tech)}" for index, tech in enumerate(techs)
    )
    lines.append(f"    set ORDAI_techCount = {len(techs)}")
    prefixes = dict.fromkeys(item.route.prefix for item in inputs if item.route.prefix)
    lines.extend(
        f'    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_syncs, Player(0), "{prefix}", false)'
        for prefix in prefixes
    )
    for item in inputs:
        lines.extend(_route_lines(item))
    lines.append("endfunction")
    lines.append("")
    lines.append("function ORDAI_Rect takes integer a returns rect")
    for item in inputs:
        if item.route.rect:
            lines.append(f"    if a == {item.action.code} then")
            lines.append(f"        return {item.route.rect}")
            lines.append("    endif")
    lines.append("    return null")
    lines.append("endfunction")
    lines.append(TABLE_END.rstrip("\n"))
    return "\n".join(lines) + "\n"


def table_in(source: str) -> str:
    """The generated block currently in a bridge source, or an empty string."""
    start = source.find(TABLE_BEGIN)
    end = source.find(TABLE_END)
    return source[start : end + len(TABLE_END)] if 0 <= start < end else ""
