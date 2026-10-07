"""Build a training-only copy without modifying the supplied map."""

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from ord_ai.catalog import ActionKind, Catalog, extract_catalog
from ord_ai.curriculum import ISEKAI_FLOOR, Difficulty, Stage
from ord_ai.human import HUMAN_TABLE, render_human_table
from ord_ai.inputs import PlayerInput, derive_inputs, render_input_table, table_in
from ord_ai.missile_art import patch_missile_art
from ord_ai.mpq import read_member, replace_member
from ord_ai.objectdata import MapObjects, parse_objects
from ord_ai.protocol import ProtocolError

SUPPORTED_MAP: Final = "eac74a90a967473196c84446b880387857f50a828083c0e1b5fc02b325663535"
JASS_DIRECTORY: Final = Path(__file__).parent / "jass"
EASY_STAGE: Final = Stage(difficulty=Difficulty.EASY, isekai=False)
# ``python -m ord_ai.build <map>``: the program name and the map path.
_REGENERATE_ARGUMENTS: Final = 2
_LOBBY_DEFAULTS: Final = "integer ORDAI_difficulty = 1\nboolean ORDAI_isekai = false\n"
MAIN: Final = re.compile(
    r"(^function main takes nothing returns nothing\n.*?)(^endfunction)",
    re.DOTALL | re.MULTILINE,
)


def bridge_sources(stage: Stage = EASY_STAGE) -> tuple[str, str, str]:
    """Return the bridge globals, actions and functions for one lobby choice.

    The simulator loads these same three files, so its episodes start in the same stage.
    """
    if stage.isekai and stage.difficulty < ISEKAI_FLOOR:
        raise ProtocolError(detail="The map allows Isekai mode only from Hell difficulty")
    declarations = (JASS_DIRECTORY / "bridge_globals.j").read_text("utf-8")
    if declarations.count(_LOBBY_DEFAULTS) != 1:
        raise ProtocolError(detail="Bridge globals lost the lobby choice defaults")
    choice = (
        f"integer ORDAI_difficulty = {int(stage.difficulty)}\n"
        f"boolean ORDAI_isekai = {'true' if stage.isekai else 'false'}\n"
    )
    return (
        declarations.replace(_LOBBY_DEFAULTS, choice),
        (JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8"),
        (JASS_DIRECTORY / "bridge_functions.j").read_text("utf-8"),
    )


# Human-play build: exact edits of the normal bridge (each anchor must occur once).
_HUMAN_ACT: Final = (
    "function ORDAI_Act takes integer a returns nothing\n"
    "    local unit u\n"
    "    local rect destination = null\n"
    "    local string handler\n"
)
_HUMAN_ACTION_HOOKS: Final = (
    (
        _HUMAN_ACT,
        _HUMAN_ACT
        + "    if a != 0 then\n"
        + "        // Human-play build: the player acts; only the no-op wait is answered.\n"
        + "        return\n"
        + "    endif\n",
    ),
)
_HUMAN_FUNCTION_HOOKS: Final = (
    # The lobby (difficulty, Isekai, start) is left to the player.
    (
        "        elseif not tU and aU != null and ks[0] then\n",
        "        elseif not ORDAI_human and not tU and aU != null and ks[0] then\n",
    ),
    # Enter regions are created once the map's rects exist.
    (
        "    if tU and vp[0] != null then\n        set ORDAI_started = true\n    endif\n",
        (
            "    if tU and vp[0] != null then\n        set ORDAI_started = true\n    endif\n"
            "    if ORDAI_started and not ORDAI_humanArmed then\n"
            "        call ORDAI_HumanArm()\n"
            "    endif\n"
        ),
    ),
    # No automatic fighter deployment either.
    (
        "            call ORDAI_Deploy()\n            call ORDAI_Act(ORDAI_action)\n",
        "            call ORDAI_Act(ORDAI_action)\n",
    ),
    (
        "    call ORDAI_Telemetry()\n    call FlushChildHashtable",
        "    call ORDAI_HumanExport()\n    call ORDAI_Telemetry()\n    call FlushChildHashtable",
    ),
    (
        "    call ORDAI_InitRecipes()\n",
        "    call ORDAI_InitRecipes()\n    call ORDAI_HumanInit()\n",
    ),
)


def _hooked(source: str, hooks: tuple[tuple[str, str], ...]) -> str:
    for anchor, replacement in hooks:
        if source.count(anchor) != 1:
            raise ProtocolError(detail=f"Human bridge anchor not found once: {anchor!r}")
        source = source.replace(anchor, replacement)
    return source


def human_bridge_sources(stage: Stage, table: str) -> tuple[str, str, str]:
    """The human-play bridge: normal sources, capture code and ``table`` (human.py).

    The actions part carries bridge_human.j after bridge_actions.j, so the three parts load
    wherever the normal ones do (the simulator included).
    """
    declarations, actions, functions = bridge_sources(stage)
    capture = (JASS_DIRECTORY / "bridge_human.j").read_text("utf-8")
    if capture.count(HUMAN_TABLE) != 1:
        raise ProtocolError(detail="bridge_human.j lost its HUMAN TABLE marker")
    return (
        declarations + (JASS_DIRECTORY / "bridge_human_globals.j").read_text("utf-8"),
        _hooked(actions, _HUMAN_ACTION_HOOKS) + "\n" + capture.replace(HUMAN_TABLE, table),
        _hooked(functions, _HUMAN_FUNCTION_HOOKS),
    )


# Combat-event record build: the normal bridge plus bridge_events.j, written at every export.
_EVENT_FUNCTION_HOOKS: Final = (
    (
        "    call ORDAI_Telemetry()\n    call FlushChildHashtable",
        "    call ORDAI_EventsExport()\n    call ORDAI_Telemetry()\n    call FlushChildHashtable",
    ),
    (
        "    call ORDAI_InitRecipes()\n",
        "    call ORDAI_InitRecipes()\n    call ORDAI_EventsInit()\n",
    ),
)


def events_bridge_sources(stage: Stage = EASY_STAGE) -> tuple[str, str, str]:
    """The real-game record bridge: normal sources plus attack, damage and death events.

    Observations, actions and masks are the normal bridge's; the events go to events.txt.
    The simulator keeps loading the normal bridge.
    """
    declarations, actions, functions = bridge_sources(stage)
    return (
        declarations + (JASS_DIRECTORY / "bridge_events_globals.j").read_text("utf-8"),
        actions + "\n" + (JASS_DIRECTORY / "bridge_events.j").read_text("utf-8"),
        _hooked(functions, _EVENT_FUNCTION_HOOKS),
    )


# Diagnostic record build: the record bridge plus ability effects (SPL) and W5R landing steps.
_DIAGNOSTIC_FUNCTION_HOOKS: Final = (
    (
        "    call ORDAI_EventsInit()\n",
        "    call ORDAI_EventsInit()\n    call ORDAI_DiagnosticInit()\n",
    ),
    # Control action -2 (protocol.EXIT_ACTION): accepted by the parser, answered in
    # control.txt, then the map ends the game itself (no in-game quit prompt to answer).
    (
        "(value < 0 and not (field == 2 and value == -1))",
        "(value < 0 and not (field == 2 and (value == -1 or value == -2)))",
    ),
    (
        "    set ORDAI_action = ORDAI_inAction\n    if ORDAI_action == -1 then\n",
        (
            "    set ORDAI_action = ORDAI_inAction\n"
            "    if ORDAI_action == -2 then\n"
            "        set ORDAI_sequence = ORDAI_inSequence\n"
            '        call ORDAI_Write("ORD_AI\\\\control.txt", "ORD_AI|1|CTL|"'
            ' + I2S(ORDAI_inEpisode) + "|" + I2S(ORDAI_inSequence) + "|-2")\n'
            "        set ORDAI_busy = false\n"
            "        call EndGame(false)\n"
            "        return\n"
            "    endif\n"
            "    if ORDAI_action == -1 then\n"
        ),
    ),
)
# war3map.j 8117: W5R's four calls stay in their order with their arguments; the landing
# rows go between them (jass/bridge_landing.j).
_W5R: Final = (
    "function W5R takes unit P5R,real p5R,real r5R returns nothing\n"
    "call SetUnitX(P5R,-10400.)\n"
    "call SetUnitY(P5R,r5R)\n"
    "call SetUnitX(P5R,p5R)\n"
    'call IssueImmediateOrder(P5R,"stop")\n'
    "endfunction\n"
)
_W5R_RECORDED: Final = (
    "function W5R takes unit P5R,real p5R,real r5R returns nothing\n"
    "local integer ORDAI_w5r=ORDAI_LandingBegin(P5R,p5R,r5R)\n"
    "call SetUnitX(P5R,-10400.)\n"
    'call ORDAI_LandingStage(P5R,ORDAI_w5r,"x1")\n'
    "call SetUnitY(P5R,r5R)\n"
    'call ORDAI_LandingStage(P5R,ORDAI_w5r,"y")\n'
    "call SetUnitX(P5R,p5R)\n"
    'call ORDAI_LandingStage(P5R,ORDAI_w5r,"x2")\n'
    'call IssueImmediateOrder(P5R,"stop")\n'
    'call ORDAI_LandingStage(P5R,ORDAI_w5r,"stop")\n'
    "endfunction\n"
)


def diagnostic_bridge_sources(stage: Stage = EASY_STAGE) -> tuple[str, str, str]:
    """The record bridge plus SPL ability effects; W5R is recorded by :func:`record_landings`."""
    declarations, actions, functions = events_bridge_sources(stage)
    return (
        declarations + (JASS_DIRECTORY / "bridge_diagnostic_globals.j").read_text("utf-8"),
        actions + "\n" + (JASS_DIRECTORY / "bridge_diagnostic.j").read_text("utf-8"),
        _hooked(functions, _DIAGNOSTIC_FUNCTION_HOOKS),
    )


# war3map.j 13861 and 22808: GNv and Obv keep their bodies; one read-only call goes first.
_GNV: Final = "function GNv takes nothing returns nothing\n"
_OBV: Final = "function Obv takes nothing returns nothing\n"


def record_timers(script: str) -> str:
    """Put the timer-order helpers before GNv and record GNv and Obv entries.

    Needs :func:`record_landings` first: the helpers reuse its ORDAI_LandingAdd.
    """
    if (
        script.count(_GNV) != 1
        or script.count(_OBV) != 1
        or "function ORDAI_LandingAdd" not in script
    ):
        raise ProtocolError(detail="Expected GNv and Obv exactly once after the W5R helpers")
    helpers = (JASS_DIRECTORY / "bridge_timers.j").read_text("utf-8")
    script = script.replace(_GNV, helpers + "\n" + _GNV + "call ORDAI_TimerTick()\n", 1)
    return script.replace(_OBV, _OBV + "call ORDAI_TimerOnce()\n", 1)


def record_landings(script: str) -> str:
    """Put the landing helpers before the original W5R and record each of its steps."""
    if script.count(_W5R) != 1:
        raise ProtocolError(detail="Expected the original W5R exactly once")
    helpers = (JASS_DIRECTORY / "bridge_landing.j").read_text("utf-8")
    return script.replace(_W5R, helpers + "\n" + _W5R_RECORDED, 1)


def inject_bridge(
    script: str, stage: Stage = EASY_STAGE, sources: tuple[str, str, str] | None = None
) -> str:
    """Append handlers after original definitions and start them at main's end.

    ``sources`` replaces the normal bridge (the human-play build passes its own).
    """
    declarations, actions, functions = bridge_sources(stage) if sources is None else sources
    functions = actions + "\n" + functions
    if "ORDAI_Init" in script or script.count("\nendglobals") != 1:
        raise ProtocolError(detail="Expected one original globals block and no existing AI bridge")
    amended = script.replace("\nendglobals", "\n" + declarations + "\nendglobals", 1)
    amended, replacements = MAIN.subn(
        lambda match: match.group(1) + 'call ExecuteFunc("ORDAI_Init")\n' + match.group(2),
        amended,
    )
    if replacements != 1:
        raise ProtocolError(detail="Expected exactly one main function to initialize the bridge")
    return amended + "\n" + functions + "\n"


@dataclass(frozen=True, slots=True)
class MapData:
    """The supplied map's script, strings, object data, player inputs and catalog."""

    script: str
    strings: str
    objects: MapObjects
    inputs: tuple[PlayerInput, ...]
    catalog: Catalog


def read_map(original: bytes) -> MapData:
    """Read the map and extract its catalog: schema-2 actions, then the player inputs."""
    fingerprint = hashlib.sha256(original).hexdigest()
    script = read_member(original, "war3map.j").decode("utf-8").replace("\r\n", "\n")
    strings = read_member(original, "war3map.wts").decode("utf-8-sig", errors="replace")
    objects = MapObjects(
        units=parse_objects(read_member(original, "war3map.w3u"), leveled=False),
        abilities=parse_objects(read_member(original, "war3map.w3a"), leveled=True),
        upgrades=parse_objects(read_member(original, "war3map.w3q"), leveled=True),
        items=parse_objects(read_member(original, "war3map.w3t"), leveled=False),
    )
    catalog = extract_catalog(script, strings, fingerprint)
    fusions = {action.code for action in catalog.actions if action.kind is ActionKind.FUSION}
    inputs = derive_inputs(script, strings, objects, fusions)
    catalog = catalog.model_copy(
        update={"actions": (*catalog.actions, *(item.action for item in inputs))}
    )
    return MapData(script, strings, objects, inputs, catalog)


def check_input_table(data: MapData) -> None:
    """Refuse a bridge whose committed input table differs from the map-derived one."""
    committed = table_in((JASS_DIRECTORY / "bridge_actions.j").read_text("utf-8"))
    if committed != render_input_table(data.inputs):
        raise ProtocolError(
            detail="bridge_actions.j input table is stale; run python -m ord_ai.build <map>"
        )


def write_input_table(data: MapData) -> None:
    """Replace the generated block of bridge_actions.j with the map-derived one."""
    path = JASS_DIRECTORY / "bridge_actions.j"
    source = path.read_text("utf-8")
    current = table_in(source)
    if not current:
        raise ProtocolError(detail="bridge_actions.j has no generated input table markers")
    _ = path.write_text(source.replace(current, render_input_table(data.inputs)), "utf-8")


def build_map(
    source: Path,
    destination: Path,
    stage: Stage = EASY_STAGE,
    *,
    human: bool = False,
    record: Literal["events", "diagnostic"] | None = None,
) -> Catalog:
    """Patch only a verified source version and emit matching policy metadata.

    ``human`` builds the human-play map instead: its script goes to ``war3map_human.j`` and
    the catalog (the same as the normal build's) is not rewritten. ``record="events"`` builds
    the real-game record map (:func:`events_bridge_sources`) with the normal catalog;
    ``record="diagnostic"`` adds SPL and W5R rows (:func:`diagnostic_bridge_sources`).
    """
    if source.resolve() == destination.resolve():
        raise ProtocolError(detail="The training map destination must differ from the original")
    original = source.read_bytes()
    fingerprint = hashlib.sha256(original).hexdigest()
    if fingerprint != SUPPORTED_MAP:
        raise ProtocolError(
            detail="This bridge supports only the analyzed 2.323[R] map fingerprint"
        )
    data = read_map(original)
    catalog = data.catalog
    check_input_table(data)
    sources = (
        human_bridge_sources(stage, render_human_table(catalog, data.inputs, data.script))
        if human
        else diagnostic_bridge_sources(stage)
        if record == "diagnostic"
        else events_bridge_sources(stage)
        if record == "events"
        else None
    )
    patched_script = inject_bridge(data.script, stage, sources)
    if record == "diagnostic":
        patched_script = record_timers(record_landings(patched_script))
    patched = replace_member(original, "war3map.j", patched_script.encode("utf-8"))
    # Broken missile art (ord_ai.missile_art) floods War3Log with model failures in long games.
    missile_w3u, _ = patch_missile_art(read_member(original, "war3map.w3u"))
    patched = replace_member(patched, "war3map.w3u", missile_w3u)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ = destination.write_bytes(patched)
    if human:
        _ = (destination.parent / "war3map_human.j").write_text(patched_script, encoding="utf-8")
        return catalog
    _ = (destination.parent / "war3map.j").write_text(patched_script, encoding="utf-8")
    _ = (destination.parent / "catalog.json").write_text(
        catalog.model_dump_json(indent=2), encoding="utf-8"
    )
    return catalog


if __name__ == "__main__":
    # Regenerate the bridge input table after a derivation change: python -m ord_ai.build <map>
    if len(sys.argv) != _REGENERATE_ARGUMENTS:
        _ = sys.stderr.write("usage: python -m ord_ai.build <ORDR_S2_2.323[R].w3x>\n")
        raise SystemExit(2)
    write_input_table(read_map(Path(sys.argv[1]).read_bytes()))
