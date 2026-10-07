"""The map's 기록지침 (log pose) window as snapshot rows and policy features.

The window has three areas (war3map.j 57091-57111): 항로개척 shows the player's three
personal quests (Q000-Q017, slots wr/Sr), 항법지침 the navigation choice (sync NAVI_1IDX
..NAVI_5IDX, name Bp[player]) and 항해목표 the ten missions T000-T009 that ``bBs`` (38593)
assigns at game start with a completion mark (``rhs(OK[slot],true)``).

The simulator accepts only UNIT and TECH rows between STATE and END, so the bridge
(``ORDAI_Logbook`` in bridge_actions.j) exports these values as TECH rows with the synthetic
ids below; no object of the map uses them (tests/test_logbook.py checks it). The real game
and the simulator run the same bridge, so both read exactly these rows.
"""

import math
from typing import Final

from ord_ai.objectdata import rawcode
from ord_ai.protocol import GameState

# 항해목표 in window order: (mission trigger, name). State row value: 0 open, 1 achieved,
# 2 failed (its round window passed: 패스트유니크 8, 신속한 철거 9, 파괴왕 30).
GOALS: Final = (
    ("T000", "패스트유니크"),
    ("T001", "신속한 철거"),
    ("T002", "트레져 헌터"),
    ("T003", "파괴왕"),
    ("T004", "크립 처치"),
    ("T005", "황금 종"),
    ("T006", "영혼 해방"),
    ("T007", "해왕류 퇴치"),
    ("T008", "정의의 문"),
    ("T009", "해군대장"),
)
GOAL_ACHIEVED: Final = 1
GOAL_FAILED: Final = 2
# 항로개척: the quest pool PB (bBs 38593; Q017 only in Isekai), in trigger order.
QUESTS: Final = (
    ("Q000", "고고학자"),
    ("Q001", "오늘만산다"),
    ("Q002", "목재수집"),
    ("Q003", "도박중독"),
    ("Q004", "도박실패(하급)"),
    ("Q005", "도박실패(중급)"),
    ("Q006", "도박실패(고급)"),
    ("Q007", "MVP 선정"),
    ("Q008", "유닛조합(초월)"),
    ("Q009", "유닛조합(불멸)"),
    ("Q010", "유닛조합(영원)"),
    ("Q011", "유닛조합(제한)"),
    ("Q012", "유닛조합(왜곡)"),
    ("Q013", "유닛조합(아이템)"),
    ("Q014", "콰트로"),
    ("Q015", "트리플"),
    ("Q016", "리사이클"),
    ("Q017", "유닛조합(신비)"),
)
QUEST_SLOTS: Final = 3
RECYCLE_GOAL: Final = 9  # DRs 9312: 리사이클 needs up[player] >= 9 common units sold
# 항법지침: (group, option) in NAVI_1IDX..NAVI_4IDX payload order; the row holds 1 + index.
# 랜덤선택 (NAVI_5IDX, bOv 39357) picks one of these and sets Hp[player].
NAVIGATION: Final = (
    ("연합세력", "일석이조"),
    ("연합세력", "긴급소집"),
    ("연합세력", "특성공학"),
    ("패왕의길", "계엄령"),
    ("패왕의길", "바운티헌터"),
    ("패왕의길", "로얄로더"),
    ("도박광", "카지노"),
    ("도박광", "리스크헷지"),
    ("도박광", "환전소"),
    ("최고의도움", "최대출력"),
    ("최고의도움", "연금술"),
    ("최고의도움", "역발상"),
)
# The user's classification of navigation styles; keep it exactly as given. 연속베팅 is the
# 환전소 option: its building h0BU 환전소 has the editor suffix "(연속베팅)" (wts STRING 2401).
HIGH_RISK_NAVIGATION: Final = frozenset({"카지노", "환전소"})
LOW_POINT_NAVIGATION: Final = frozenset({"일석이조", "바운티헌터", "연금술", "긴급소집"})

# Synthetic TECH ids of the 기록지침 rows, in export order: (id, meaning).
LOGBOOK_TECHS: Final = (
    *((f"ZG{index:02}", f"항해목표 {name} state") for index, (_, name) in enumerate(GOALS)),
    ("ZP00", "트레져 헌터: treasures found UP"),
    ("ZP01", "트레져 헌터: treasures needed 2*jP+6"),
    ("ZP02", "황금 종 Hd life percent"),
    ("ZP03", "영혼 해방 Bd life percent"),
    ("ZP04", "정의의 문 Ud life percent"),
    ("ZP05", "해왕류 jB life percent"),
    ("ZP06", "해군대장 (o02Q/o02M/o02R) life percent"),
    ("ZP07", "크립 처치: own camp creep stage alive (n00E 1, n00F 2, n00G 3)"),
    ("ZP08", "크립 처치: own camp creep life percent"),
    ("ZP09", "shared missions completed Mp"),
    *((f"ZQ{slot:02}", f"항로개척 slot {slot + 1} quest rawcode") for slot in range(3)),
    *((f"ZQ{slot + 3:02}", f"항로개척 slot {slot + 1} completed") for slot in range(3)),
    ("ZQ06", "quest reroll used Dr"),
    ("ZQ07", "리사이클: common units sold up"),
    ("ZN00", "항법지침 choice: 1 + NAVIGATION index, 0 none"),
    ("ZN01", "항법지침 chosen by 랜덤선택 Hp"),
    ("ZN02", "rare reroll count Dp"),
    ("ZN03", "wildcards left: A09C level of 기록지침 sp"),
    ("ZN04", "환전소 currency: charges of item Ep"),
)
LOGBOOK_IDS: Final = frozenset(rawcode(code) for code, _ in LOGBOOK_TECHS)
GOAL_IDS: Final = tuple(rawcode(f"ZG{index:02}") for index in range(len(GOALS)))
TREASURE_FOUND: Final = rawcode("ZP00")
TREASURE_NEEDED: Final = rawcode("ZP01")
BELL_LIFE: Final = rawcode("ZP02")
SOUL_LIFE: Final = rawcode("ZP03")
GATE_LIFE: Final = rawcode("ZP04")
SEA_KING_LIFE: Final = rawcode("ZP05")
ADMIRAL_LIFE: Final = rawcode("ZP06")
CREEP_STAGE: Final = rawcode("ZP07")
CREEP_LIFE: Final = rawcode("ZP08")
SHARED_DONE: Final = rawcode("ZP09")
QUEST_IDS: Final = tuple(rawcode(f"ZQ{slot:02}") for slot in range(QUEST_SLOTS))
QUEST_DONE_IDS: Final = tuple(rawcode(f"ZQ{slot + 3:02}") for slot in range(QUEST_SLOTS))
REROLL_USED: Final = rawcode("ZQ06")
RECYCLED: Final = rawcode("ZQ07")
NAVIGATION_CHOICE: Final = rawcode("ZN00")
NAVIGATION_RANDOM: Final = rawcode("ZN01")
RARE_REROLLS: Final = rawcode("ZN02")
WILDCARDS: Final = rawcode("ZN03")
EXCHANGE_CURRENCY: Final = rawcode("ZN04")

_PERCENT: Final = 100.0
_CREEP_STAGES: Final = 3.0
_SHARED_GOALS: Final = 8.0  # mZs 7965: the 8th shared completion clears them all
_COUNT_SCALE: Final = 100.0
_QUEST_CODES: Final = tuple(rawcode(code) for code, _ in QUESTS)
LOGBOOK_SIZE: Final = 2 * len(GOALS) + 10 + 2 * len(QUESTS) + 2 + len(NAVIGATION) + 7


def _log_count(value: int) -> float:
    return math.log1p(value) / math.log1p(_COUNT_SCALE)


def logbook_features(state: GameState) -> tuple[float, ...]:
    """The window's state in a fixed order: goals, goal progress, quests, navigation."""
    techs = {tech.raw_id: tech.level for tech in state.techs}
    goals = tuple(
        float(techs.get(raw, 0) == value)
        for raw in GOAL_IDS
        for value in (GOAL_ACHIEVED, GOAL_FAILED)
    )
    needed = techs.get(TREASURE_NEEDED, 0)
    progress = (
        min(1.0, techs.get(TREASURE_FOUND, 0) / needed) if needed > 0 else 0.0,
        *(
            techs.get(raw, 0) / _PERCENT
            for raw in (BELL_LIFE, SOUL_LIFE, GATE_LIFE, SEA_KING_LIFE, ADMIRAL_LIFE)
        ),
        techs.get(CREEP_STAGE, 0) / _CREEP_STAGES,
        techs.get(CREEP_LIFE, 0) / _PERCENT,
        techs.get(SHARED_DONE, 0) / _SHARED_GOALS,
        _log_count(techs.get(TREASURE_FOUND, 0)),
    )
    slots = [
        (techs.get(quest, 0), techs.get(done, 0) > 0)
        for quest, done in zip(QUEST_IDS, QUEST_DONE_IDS, strict=True)
    ]
    active = tuple(float(any(q == code and not d for q, d in slots)) for code in _QUEST_CODES)
    completed = tuple(float(any(q == code and d for q, d in slots)) for code in _QUEST_CODES)
    quests = (
        *active,
        *completed,
        float(techs.get(REROLL_USED, 0) > 0),
        min(1.0, techs.get(RECYCLED, 0) / RECYCLE_GOAL),
    )
    choice = techs.get(NAVIGATION_CHOICE, 0)
    option = NAVIGATION[choice - 1][1] if 0 < choice <= len(NAVIGATION) else ""
    navigation = (
        *(float(choice == index + 1) for index in range(len(NAVIGATION))),
        float(bool(option)),
        float(techs.get(NAVIGATION_RANDOM, 0) > 0),
        float(option in HIGH_RISK_NAVIGATION),
        float(option in LOW_POINT_NAVIGATION),
        _log_count(techs.get(RARE_REROLLS, 0)),
        _log_count(techs.get(WILDCARDS, 0)),
        _log_count(techs.get(EXCHANGE_CURRENCY, 0)),
    )
    return goals + progress + quests + navigation
