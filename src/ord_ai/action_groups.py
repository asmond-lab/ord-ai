"""Catalog action codes grouped by the 기록지침 window area they serve.

Groups (README "기록지침 근거표"):

- ``navigation``: the 항법지침 choices, syncs NAVI_1IDX..NAVI_5IDX.
- ``voyage``: 항해목표 actions: quest reroll QUST_INDX, the mission attacks, the Zd and Dd pads,
  탐색 (트레져 헌터), the 시라호시 talent that sinks the sea king, and rare fusions
  (패스트유니크).
- ``route``: 항로개척 quests that need a player input: transcendent fusions (유닛조합(초월)),
  fusions of the item units (유닛조합(아이템)) and selling a common unit (리사이클).
- ``buildings``: sell/train/research orders on the player's buildings and abilities of the
  helper building 도움소 or the 환전소, except the teacher's supply purchase.
- ``treasure``: 탐색 (A05D) of the hidden chests: the caster-point ability action and the
  chest actions (``ord_ai.treasure``).

Codes 0-13 (the teacher's base and shop actions) are in no group.
"""

from typing import Final

from ord_ai.catalog import FUSION_FLOOR, ActionKind, Catalog, kind_of
from ord_ai.logbook import HIGH_RISK_NAVIGATION, LOW_POINT_NAVIGATION
from ord_ai.objectdata import rawcode, rawtext
from ord_ai.teacher import SUPPLY_UNIT
from ord_ai.treasure import SCAN_ABILITY

GROUPS: Final = ("navigation", "voyage", "route", "buildings", "treasure")
NAVIGATION_PREFIX: Final = "sync: NAVI_"
QUEST_PREFIX: Final = "sync: QUST_INDX"
# FIGHTER_REGIONS pads: Zd leads to rd (정의의 문, 해군대장), Dd is 포네그리프 해독 (after all
# shared missions, mZs 7965).
VOYAGE_REGIONS: Final = ("(Zd)", "(Dd)")
# A05D 탐색 finds the treasures of 트레져 헌터 (UP); A0RS 시라호시 talent deals max-life damage
# to the sea king jB (mOR 52396).
VOYAGE_ABILITIES: Final = frozenset({rawcode("A05D"), rawcode("A0RS")})
RARE_FUSION_SUFFIX: Final = " - 희귀"  # 패스트유니크 (jeR): a rare unit (ability A0B9)
# Q008 유닛조합(초월) (IwR 16880): the fused unit is a hero with ability A07O.
TRANSCENDENT_RECIPES: Final = frozenset({f"TR{index:02}" for index in range(1, 31)} | {"ET02"})
# Q013 유닛조합(아이템) (jav 50036): the fused unit is h067, h08O or H0BK.
ITEM_RECIPES: Final = frozenset({"A011", "ET02", "ET07"})
RECYCLE_ABILITY: Final = rawcode("A09G")  # Q016 리사이클 counts up[] in hHR (sell a common)
SCAN: Final = rawcode(SCAN_ABILITY)
BUILDING_HOLDERS: Final = (" @ 도움소", " @ 환전소")
# 연금술 (A0J2) and 마나포션 (A0UY) are added to the 도움소 Wp by the 최고의도움 choices (jFv).
HELPER_ABILITIES: Final = frozenset({rawcode("A0J2"), rawcode("A0UY")})
_BUILDING_KINDS: Final = (ActionKind.SELL, ActionKind.TRAIN, ActionKind.RESEARCH)


def _groups_of(code: int, name: str, kind: ActionKind, raw_id: int) -> list[str]:
    recipe = rawtext(code) if code >= FUSION_FLOOR else ""
    found: list[str] = []
    if kind is ActionKind.SYNC and name.startswith(NAVIGATION_PREFIX):
        found.append("navigation")
    if (
        (kind is ActionKind.SYNC and name.startswith(QUEST_PREFIX))
        or kind is ActionKind.ATTACK
        or (kind is ActionKind.REGION and name.endswith(VOYAGE_REGIONS))
        or (kind is ActionKind.ABILITY and raw_id in VOYAGE_ABILITIES)
        or (kind is ActionKind.FUSION and name.endswith(RARE_FUSION_SUFFIX))
    ):
        found.append("voyage")
    if recipe in TRANSCENDENT_RECIPES | ITEM_RECIPES or (
        kind is ActionKind.ABILITY and raw_id == RECYCLE_ABILITY
    ):
        found.append("route")
    if (kind in _BUILDING_KINDS and raw_id != SUPPLY_UNIT) or (
        kind is ActionKind.ABILITY
        and (name.endswith(BUILDING_HOLDERS) or raw_id in HELPER_ABILITIES)
    ):
        found.append("buildings")
    if kind is ActionKind.TREASURE or (kind is ActionKind.ABILITY and raw_id == SCAN):
        found.append("treasure")
    return found


def action_groups(catalog: Catalog) -> dict[str, frozenset[int]]:
    """Action codes (not indices) of each group, keyed by ``GROUPS``."""
    members: dict[str, set[int]] = {group: set() for group in GROUPS}
    for action in catalog.actions:
        for group in _groups_of(action.code, action.name, kind_of(action), action.raw_id):
            members[group].add(action.code)
    return {group: frozenset(codes) for group, codes in members.items()}


def navigation_styles(catalog: Catalog) -> dict[str, frozenset[int]]:
    """항법지침 choice codes by the user's style: high-risk and low-point options."""
    styles: dict[str, set[int]] = {"high_risk": set(), "low_point": set()}
    for action in catalog.actions:
        if kind_of(action) is not ActionKind.SYNC or not action.name.startswith(NAVIGATION_PREFIX):
            continue
        option = action.name.rsplit(" :: ", 1)[-1]
        if option in HIGH_RISK_NAVIGATION:
            styles["high_risk"].add(action.code)
        elif option in LOW_POINT_NAVIGATION:
            styles["low_point"].add(action.code)
    return {style: frozenset(codes) for style, codes in styles.items()}
