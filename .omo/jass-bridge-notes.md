# JASS bridge integration

Insertion: `bridge_globals.j` inside original globals; append `bridge_actions.j`
then `bridge_functions.j` AFTER every original function. Add
`call ExecuteFunc("ORDAI_Init")` at the END of original `main`, not its beginning.
The extra actions file is required (81 original chat-recipe dispatch entries).

Protocol integration decisions:
- Episode IDs MUST strictly increase across process/map restarts. The current
  Python `StateChannel.reset()` uses random IDs and MUST be changed by the parent.
  Read the last consumed episode from boot.txt (including failed reset requests),
  then choose a larger positive int32 ID. Episode 0 is unbound/waiting. Rejecting
  older reset tokens prevents stale disk commands from restarting a new episode.
- Reset can publish status 0 while original setup runs. Wait for status != 0
  AND matching episode/sequence AND action_ok=1 before treating reset as done.
- Internal `ORD_AI\\boot.txt` records the last consumed reset and pending restart
  token via the same AZZZ mechanism. It is not a login/save file.
- A failed native restart is explicitly rejected (action_ok=0) under the REQUEST
  identifiers; the internal active episode is NOT adopted. The client must fail
  reset, not train on this observation. Successful restart is acknowledged ONLY
  by the fresh map instance. Real Warcraft restart remains a runtime check.
- `death_count` is the exact `Es[0]` global: 20 initially, decreasing to zero
  on repeated enemy-limit breaches. It is remaining defeat countdown, NOT
  cumulative deaths. Reward code must not interpret an increasing value as death.
- `story_stage` = KP (current spawned story stage), `boss_count` = tP[0]
  (outstanding line bosses), enemies = js[0], enemy_limit = Hs, round = Av.

Fusion must not invoke ODv alone: original wrappers have one-per-player checks,
spawned companions, abilities and other post-fusion side effects. Chat recipes
run their exact original FMv wrapper in a player-0 sync event via ExecuteFunc.
Ability recipes cast the original abilities: war3map.w3a contains 109 AHwe
(waterelemental), 33 Aroa (roar), 8 Arsg (summongrizzly), and 6 Auhf
(unholyfrenzy, self-target allowed by their actual target flags). Original spell
dispatch invokes Pas or the special wrapper, which performs L2R, ss/Rs/Ps/ps,
Ws/is/rs, ODv -> tcv -> G9s -> T8R -> Ys and wrapper-specific side effects.
Successful ODv is observed via Ys, not inferred from order acceptance. Native
casting retains mana/cooldown and caster eligibility; there are no free grants.
No original unit ability lists had same-base collisions for these recipes.

The other 29 cv entries have neither a matching cast ability nor an original
chat fusion wrapper (IS01..14, SP01..08, SPH1..4, UU01, YY26, TEST). Their tables
also supply the recipe-display UI; membership in cv alone does not make an
executable action. These IDs are rejected rather than inventing a bypass. Parent
may exclude them from the policy catalog; otherwise they return action_ok=0.

Startup uses real selection events on AU (easy) then aU (start). Original DYR,
bBs, TriggerEvaluate(Y), bLR and tIR/Iov run unchanged. The bridge waits until
setup units exist and then until tU, vp[0] and completed intro (xU == null).
Easy itself disables additional modes in bBs; no direct resource/unit grants.

Action routing / observations:
- 1 and 4: e0IX/e0HP move to fY; original qbR/AVR consume and create units.
- 2: e0IX moves to CY (d2s); 3: e0IX moves to aY (LBs).
- 5: strongest movable combat unit in gP[0] moves to nP[0]; original Hcv checks
  tP[0]/xP[0] and transports it to TP[0]. 6: a combat unit in the story footprint
  bounded by the original arrival pads and EY moves to EY; JnR returns it.
- Strongest is point value, then base damage, then lowest handle ID. Common
  combat units have point value 1, so they are NOT excluded by a >=100 cutoff.
  Wisp/caster ties choose lowest handle ID. Paused/dead/hidden units and units
  already moving are not selected for movement; no bridge teleportation.
- Movement acceptance means the genuine move order was accepted. The unit may
  still be travelling at the one-second snapshot; original entrance triggers
  perform consumption/restriction checks upon arrival. Busy movement is not
  reassigned on the following step. Fusion acceptance requires the Ys hook.
- Every step reserves sequence before dispatch and schedules a one-shot 1.0
  game-second observation timer. An unsuccessful outstanding cast is stopped at
  that deadline, preventing a late cast from leaking into the next sequence.
- Snapshot units are actual living, visible player-0 units, excluding structures
  and Aloc units. Counts use a separate bridge hashtable; no original counters,
  groups or fusion arrays are cleared. AZZZ's prior tooltip is restored on reads.
- Loss is started && !ks[0]; victory is started && Ss, with loss taking priority.
  No round-based victory inference. All STATE/UNIT records precede matching END.
- Init refuses file access/action activation unless exactly one active user slot
  exists and it is player 0. No original login/load/save functions are called.

Evidence locations (part:line): fusion core 003:620-768; Pas 014:424 onward;
setup DYR 006:678, bBs 022:793, bLR 022:1193, tIR/Iov 035:202-270;
region registration 047:585/618/681/781 and 013:1628; Hcv 009:1064;
line return JnR 010:1362; Es initialization 023:1653 and depletion 008:1149;
loss U5s 004:1421, wins Ss assignments 009:1800/010:16/33/013:1136.

Verification:
- Installed `.../Warcraft III/_retail_/x86_64/JassHelper/pjass.exe` parsed the
  complete original + appended bridge + main ExecuteFunc insertion successfully:
  86,895 integrated-script lines, zero errors. Used upstream lep/jassdoc common.j
  and Blizzard.j with documentation block comments removed for the old checker.
  Its first pass caught a bridge local `pos` colliding with an original function;
  fixed to `cursor`. No original map edits were needed for compilation.
- Exact source/object comparison passed for all 81 chat dispatch entries and all
  156 castable recipe base orders. Source sizes: globals 28, actions 204,
  functions 225 nonempty noncomment lines.
- A temporary deterministic translator executed the delivered JASS control flow
  with native substitutes. Passed parser bounds, multiplayer refusal, selection
  sequencing, one-second delayed acknowledgment, real Python snapshot parsing,
  duplicate/stale suppression, fresh-VM reset resumption, failed native restart,
  END framing, and terminal-flag checks. This is NOT an engine integration test.
- JASS LSP is not configured. No Warcraft launch/install was performed. Real
  Preloader write/read access, native selection events, movement/region events,
  spell dispatch and RestartGame(false) still require the parent's live test.
  In particular, native restart is requested once, never emulated: only a new
  VM can acknowledge success; the old VM rejects after five game seconds if
  still running. If the engine leaves the map instead, the Python timeout must
  fail reset and require a reload, not relabel the previous observation.
