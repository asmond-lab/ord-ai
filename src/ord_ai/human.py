"""Human-play recording: the ``human.txt`` inputs file and the human bridge's lookup table.

The human build (``ord-ai build --kind human``) takes no AI action. Its bridge catches Player(0)'s
real inputs through map events, converts each to a catalog code and writes the inputs gathered
since the previous export to ``human.txt`` beside ``state.txt``::

    ORD_AI|1|HUMAN|<episode>|<sequence>
    ORD_AI|1|IN|<code or empty>|<kind>|<raw identifiers>
    ORD_AI|1|HEND|<episode>|<sequence>|<count>|<dropped>

An empty code is an input without a catalog code; its raw identifiers are kept.
"""

from pathlib import Path
from typing import Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from ord_ai.catalog import RESEARCH, ActionKind, BasicAction, Catalog
from ord_ai.inputs import PlayerInput, chat_commands, sync_prefixes
from ord_ai.objectdata import rawcode, rawtext
from ord_ai.protocol import PRELOAD, VERSION, ProtocolError

HUMAN_FILE: Final = "human.txt"
HUMAN_TABLE: Final = "// HUMAN TABLE\n"
HEAD_FIELDS: Final = 5
INPUT_FIELDS: Final = 6
END_FIELDS: Final = 7
# ORDAI_Shop's gamble house sales; the upgrade house researches are catalog.RESEARCH.
SHOP_SALES: Final = {
    BasicAction.GAMBLE_LOW: "h06F",
    BasicAction.GAMBLE_HIGH: "H0AZ",
    BasicAction.CREATE_RARE: "H0B0",
}
# Rects of the base moves in ORDAI_Act; pad 1 (fY) also yields 4 for the evolution wisp.
BASE_PADS: Final = (
    (BasicAction.SUMMON, "fY"),
    (BasicAction.LUMBER, "CY"),
    (BasicAction.MANA, "aY"),
    (BasicAction.STORY, "nP[0]"),
    (BasicAction.LINE, "EY"),
)
BRIDGE_PREFIX: Final = "ORD_AI"
# ORDAI_data parents of the human bridge (bridge_human.j).
_PARENTS: Final = {
    ActionKind.SELL: 200,
    ActionKind.TRAIN: 201,
    ActionKind.RESEARCH: 202,
    ActionKind.ABILITY: 203,
}


class HumanInput(BaseModel):
    """One real player input: its catalog code (None without one), event kind and raw ids."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    code: int | None
    kind: str
    raw: str


class HumanRecord(BaseModel):
    """The inputs the bridge gathered before the export of ``episode``/``sequence``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    episode: int
    sequence: int
    inputs: tuple[HumanInput, ...] = ()
    dropped: Annotated[int, Field(ge=0)] = 0


def parse_human(text: str) -> HumanRecord | None:
    """Parse a complete record; return None while the file is still being written."""
    rows = [
        value.split("|")
        for value in (match.group(1) for match in PRELOAD.finditer(text))
        if value.startswith("ORD_AI|")
    ]
    if not rows or rows[-1][2:3] != ["HEND"]:
        return None
    if any(row[:2] != ["ORD_AI", str(VERSION)] for row in rows):
        raise ProtocolError(detail="Unsupported bridge human-input version")
    head, ending = rows[0], rows[-1]
    if len(head) != HEAD_FIELDS or head[2] != "HUMAN":
        raise ProtocolError(detail="Human inputs must start with exactly one HUMAN record")
    if len(ending) != END_FIELDS or ending[3:5] != head[3:5]:
        raise ProtocolError(detail="Human inputs end does not match its HUMAN record")
    body = rows[1:-1]
    if any(len(row) < INPUT_FIELDS or row[2] != "IN" for row in body):
        raise ProtocolError(detail="Only complete IN records may appear inside human inputs")
    if int(ending[5]) != len(body):
        raise ProtocolError(detail="Human inputs count does not match its IN records")
    return HumanRecord(
        episode=int(head[3]),
        sequence=int(head[4]),
        # The raw text is the last field and may itself contain the separator.
        inputs=tuple(
            HumanInput(code=int(row[3]) if row[3] else None, kind=row[4], raw="|".join(row[5:]))
            for row in body
        ),
        dropped=int(ending[6]),
    )


def read_human(directory: Path, episode: int, sequence: int) -> HumanRecord | None:
    """Read the inputs for exactly this acknowledged export, if the bridge wrote them."""
    try:
        text = (directory / HUMAN_FILE).read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return None
    record = parse_human(text)
    if record is None or (record.episode, record.sequence) != (episode, sequence):
        return None
    return record


def _literal(value: int) -> str:
    text = rawtext(value)
    return f"'{text}'" if text.isalnum() else str(value)


def _string(text: str) -> str:
    if '"' in text or "\\" in text:
        raise ProtocolError(detail=f"Unsupported character in JASS string {text!r}")
    return f'"{text}"'


def _lookups(catalog: Catalog, inputs: tuple[PlayerInput, ...]) -> list[str]:
    lines = [
        f"    call SaveInteger(ORDAI_data, 200, {_literal(rawcode(raw))}, {int(code)})"
        for code, raw in SHOP_SALES.items()
    ]
    lines.extend(
        f"    call SaveInteger(ORDAI_data, 202, {_literal(research.raw_id)}, {int(code)})"
        for code, research in RESEARCH.items()
    )
    for item in inputs:
        code, route = item.action.code, item.route
        parent = _PARENTS.get(route.kind)
        if parent is not None:
            lines.append(
                f"    call SaveInteger(ORDAI_data, {parent}, {_literal(route.raw_id)}, {code})"
            )
        elif route.kind is ActionKind.SYNC:
            key = _string(f"{route.prefix} {route.payload}")
            lines.append(f"    call SaveInteger(ORDAI_data, 204, StringHash({key}), {code})")
        elif route.kind is ActionKind.SELECT:
            lines.append(f"    call SaveInteger(ORDAI_data, 208, {_literal(route.raw_id)}, {code})")
        elif route.kind is ActionKind.ATTACK:
            lines.append(f"    call SaveInteger(ORDAI_data, 209, {route.payload}, {code})")
        elif route.kind is ActionKind.TREASURE:
            lines.append(f"    call SaveInteger(ORDAI_data, 210, {route.payload}, {code})")
    lines.extend(
        f"    call SaveBoolean(ORDAI_data, 205, {_literal(action.code)}, true)"
        for action in catalog.actions
        if action.kind is ActionKind.FUSION
    )
    return lines


def _chat_code(inputs: tuple[PlayerInput, ...], script: str) -> list[str]:
    codes = {
        item.route.handler: item.action.code
        for item in inputs
        if item.route.kind is ActionKind.CHAT
    }
    lines = ["function ORDAI_HumanChatCode takes string text returns integer"]
    for text, exact, handler in chat_commands(script):
        if handler not in codes:
            continue
        if not exact:
            raise ProtocolError(detail=f"Catalogued chat {text!r} is not an exact-match command")
        lines.extend(
            (
                f"    if text == {_string(text)} then",
                f"        return {codes[handler]}",
                "    endif",
            )
        )
    lines.extend(("    return -1", "endfunction"))
    return lines


def render_human_table(catalog: Catalog, inputs: tuple[PlayerInput, ...], script: str) -> str:
    """The generated JASS the human bridge needs: code lookups, pads and chat commands.

    ``script`` is the original war3map.j; every sync prefix it uses is captured.
    """
    lines = ["function ORDAI_HumanTable takes nothing returns nothing"]
    lines.extend(_lookups(catalog, inputs))
    register = "    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_humanSync, Player(0), {}, false)"
    lines.extend(
        register.format(_string(prefix))
        for prefix in sync_prefixes(script)
        if prefix != BRIDGE_PREFIX
    )
    lines.extend(("endfunction", "", "function ORDAI_HumanPads takes nothing returns nothing"))
    lines.extend(f"    call ORDAI_HumanPad({int(code)}, {rect})" for code, rect in BASE_PADS)
    lines.extend(
        f"    call ORDAI_HumanPad({item.action.code}, ORDAI_Rect({item.action.code}))"
        for item in inputs
        if item.route.kind is ActionKind.REGION
    )
    lines.extend(("endfunction", ""))
    lines.extend(_chat_code(inputs, script))
    return "\n".join(lines) + "\n"
