# ORDR learning integration contract

User chose a training-map copy with direct game-state/action integration.
Original map stays byte-identical. Target is offline, single-player, player 0.
This is an initial real-game reinforcement-learning implementation, not a
claim that a policy already wins ORDR.

## Components and ownership

- Archive track: `src/ord_ai/mpq.py`, `src/ord_ai/mpq_crypto.py`,
  `tests/test_mpq.py`. Public API:
  `read_member(source: bytes, name: str) -> bytes`;
  `replace_member(source: bytes, name: str, content: bytes) -> bytes`.
  Preserve other member payloads and the Warcraft wrapper. Handle the supplied
  protected archive explicitly; verify known members before/after replacement.
- JASS track: `src/ord_ai/jass/bridge_globals.j`,
  `src/ord_ai/jass/bridge_actions.j`, `src/ord_ai/jass/bridge_functions.j`.
  Append actions then functions AFTER all original functions; declarations go
  inside original globals and main ends with ExecuteFunc("ORDAI_Init").
- Parent: Python protocol, training, CLI, build integration, tests, documentation.

## On-disk protocol v1

Use the existing map's Preloader / AZZZ tooltip mechanism. No network server.
Command file: `ORD_AI\\command.txt`, relative to the Warcraft III custom data root.
State file: `ORD_AI\\state.txt`.
Do not call original login/save functionality. AZZZ is already used for local
file I/O; dedicated offline training runs must not use the original save system.

Python atomically writes valid JASS:

    function PreloadFiles takes nothing returns nothing
        call BlzSetAbilityTooltip('AZZZ', "ORD_AI|1|episode|sequence|action", 0)
    endfunction

`episode`, `sequence`, `action` are nonnegative decimal integers.
Episode identifies a reset and MUST monotonically increase, including across
Python restarts. Read command.txt, boot.txt and the last snapshot before allocating
the next identifier. Sequence identifies an action. A command is applied at most
once. Python is allowed only one outstanding command.

JASS writes a complete snapshot with PreloadGenStart/Preload/PreloadGenEnd.
The Python reader extracts only strings starting `ORD_AI|` from Preload calls:

    ORD_AI|1|STATE|episode|sequence|round|gold|lumber|food|enemy_count|enemy_limit|death_count|story_stage|boss_count|status|action_ok
    ORD_AI|1|UNIT|unit_rawcode_decimal|count
    ORD_AI|1|END|episode|sequence

UNIT records count living player-0 units, excluding locust/effect dummies and
buildings. All relevant visible combat units and wisps are retained.
`status`: 0 waiting for game setup, 1 playing, 2 win, 3 loss.
`action_ok`: 0 rejected, 1 accepted. State must be exported after the action has
had a fixed game-time observation interval (initially 1.0 second).
END is mandatory: partial writes are never accepted as observations.
Scalar state values come from original globals/native getters, not estimates.
Episode reset and initial startup must distinguish stale observations/commands.

## Actions

0 wait
1 consume one random wisp at the normal summon region
2 consume one random wisp at the lumber gamble region
3 consume one random wisp at the helper mana region
4 consume one evolution wisp at its genuine trigger region
5 send one strongest eligible line combat unit to the story entrance
6 return one strongest eligible story combat unit to the line

IDs 1000+ are original fusion recipe IDs (the actual rawcode decimal value, not
an artificial sequential index). Python has a fixed ordered action catalog and
maps neural-policy indices onto these IDs. Fusion must invoke original validation,
resource consumption, creation and post-fusion hooks, not grant free units.
Actual casting and original chat wrappers are required: invoking ODv alone bypasses
wrapper-specific conditions. The 29 display-only table entries documented in
jass-bridge-notes.md are excluded from the policy vocabulary.
No fabricated actions, cheats, free resources, or random rewards.

## Reset and startup

Action -1 is a control-only reset, never a neural policy action. (Other actions
remain nonnegative.) Commands therefore parse the action as a signed integer.
Investigate actual single-player restart support; never pretend a reset happened.
For a fresh game, consume a new reset command, adopt its episode and sequence,
select easy/no extra modes through the actual setup path when initialized, and
export the resulting initial state. For an existing active episode, a new reset
request must restart the map or explicitly fail; it must not merely relabel state.
No input is sent to online multiplayer sessions.

## Learning and verification

Use the observed transition to calculate reward: newly reached rounds/story,
boss completion, game result, rejected actions; no reward for arbitrary elapsed
wall time. Optimize a masked categorical actor-critic network with PyTorch.
Structural policy observation includes scalar game state plus the fixed unit
catalog count vector. Save action/catalog/version metadata with checkpoints.
Tests cover file protocol, stale/partial state rejection, reward changes, model
updates, and archive round trips. A learning smoke test can use recorded synthetic
transitions only if labeled as such; it never proves real Warcraft integration.
