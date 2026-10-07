// Append after bridge_actions.j. Entry point is called via ExecuteFunc from main.
function ORDAI_Parse takes string text returns boolean
    local integer start = 9
    local integer cursor = 9
    local integer field = 0
    local integer value
    local string token
    if SubString(text, 0, 9) != "ORD_AI|1|" or StringLength(text) > 64 then
        return false
    endif
    loop
        exitwhen cursor > StringLength(text)
        if cursor == StringLength(text) or SubString(text, cursor, cursor + 1) == "|" then
            set token = SubString(text, start, cursor)
            set value = S2I(token)
            if I2S(value) != token or (value < 0 and not (field == 2 and value == -1)) then
                return false
            endif
            if field == 0 then
                set ORDAI_inEpisode = value
            elseif field == 1 then
                set ORDAI_inSequence = value
            elseif field == 2 then
                set ORDAI_inAction = value
            else
                return false
            endif
            set field = field + 1
            set start = cursor + 1
        endif
        set cursor = cursor + 1
    endloop
    return field == 3
endfunction

function ORDAI_Load takes string path returns string
    local string old = BlzGetAbilityTooltip('AZZZ', 0)
    local string text
    call BlzSetAbilityTooltip('AZZZ', "", 0)
    call Preloader(path)
    set text = BlzGetAbilityTooltip('AZZZ', 0)
    call BlzSetAbilityTooltip('AZZZ', old, 0)
    return text
endfunction

function ORDAI_Write takes string path, string text returns nothing
    call PreloadGenClear()
    call PreloadGenStart()
    call Preload("\")\ncall BlzSetAbilityTooltip('AZZZ',\"" + text + "\",0)\nreturn//")
    call PreloadGenEnd(path)
endfunction

function ORDAI_Boot takes integer e, integer s, integer phase returns boolean
    local string text = "ORD_AI|1|" + I2S(e) + "|" + I2S(s) + "|" + I2S(phase)
    local string check = "ORD_AI\\boot_" + I2S(e) + "_" + I2S(s) + "_" + I2S(phase) + ".txt"
    call ORDAI_Write("ORD_AI\\boot.txt", text)
    // Preloader returns the first content it read for a path until the game restarts,
    // so verify the write through a path this game instance has never read.
    call ORDAI_Write(check, text)
    return ORDAI_Load(check) == text
endfunction

function ORDAI_Status takes nothing returns integer
    if ORDAI_started and not ks[0] then
        return 3
    elseif ORDAI_started and Ss then
        return 2
    elseif tU and vp[0] != null and xU == null then
        return 1
    endif
    return 0
endfunction

function ORDAI_Telemetry takes nothing returns nothing
    // Replay-comparison record only; the policy never reads it.
    local unit u
    local string head = "ORD_AI|1|"
    local real elapsed = 0.
    local integer evolution = 0
    local integer evolutionHandle = 0
    if ORDAI_clock != null then
        set elapsed = TimerGetElapsed(ORDAI_clock)
    endif
    if kU[0] != null then
        set evolution = GetUnitTypeId(kU[0])
        set evolutionHandle = GetHandleId(kU[0])
    endif
    call PreloadGenClear()
    call PreloadGenStart()
    call Preload(head + "TIME|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence) + "|" + R2S(elapsed) + "|" + I2S(Gv) + "|" + I2S(ORDAI_mainGv) + "|" + I2S(evolution) + "|" + I2S(evolutionHandle))
    call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_units)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_units, u)
        if ORDAI_Visible(u) then
            call Preload(head + "POS|" + I2S(GetHandleId(u)) + "|" + I2S(GetUnitTypeId(u)) + "|" + R2S(GetUnitX(u)) + "|" + R2S(GetUnitY(u)) + "|" + I2S(GetUnitCurrentOrder(u)))
        endif
    endloop
    call Preload(head + "TEND|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence))
    call PreloadGenEnd("ORD_AI\\telemetry.txt")
    set u = null
endfunction

function ORDAI_Export takes nothing returns nothing
    local unit u
    local integer raw
    local integer i = 0
    local string head = "ORD_AI|1|"
    call ORDAI_Telemetry()
    call FlushChildHashtable(ORDAI_data, 0)
    set ORDAI_typeCount = 0
    call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_units)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_units, u)
        if ORDAI_Visible(u) then
            set raw = GetUnitTypeId(u)
            if not HaveSavedInteger(ORDAI_data, 0, raw) then
                set ORDAI_types[ORDAI_typeCount] = raw
                set ORDAI_typeCount = ORDAI_typeCount + 1
            endif
            call SaveInteger(ORDAI_data, 0, raw, LoadInteger(ORDAI_data, 0, raw) + 1)
        endif
    endloop
    call PreloadGenClear()
    call PreloadGenStart()
    call Preload(head + "STATE|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence) + "|" + I2S(Av) + "|" + I2S(GetPlayerState(Player(0), PLAYER_STATE_RESOURCE_GOLD)) + "|" + I2S(GetPlayerState(Player(0), PLAYER_STATE_RESOURCE_LUMBER)) + "|" + I2S(GetPlayerState(Player(0), PLAYER_STATE_RESOURCE_FOOD_USED)) + "|" + I2S(js[0]) + "|" + I2S(Hs) + "|" + I2S(Es[0]) + "|" + I2S(KP) + "|" + I2S(tP[0]) + "|" + I2S(ORDAI_Status()) + "|" + I2S(ORDAI_ok))
    loop
        exitwhen i == ORDAI_typeCount
        set raw = ORDAI_types[i]
        call Preload(head + "UNIT|" + I2S(raw) + "|" + I2S(LoadInteger(ORDAI_data, 0, raw)))
        set i = i + 1
    endloop
    // Upgrade levels, unlock flags and research levels the policy masks read.
    set i = 0
    loop
        exitwhen i == ORDAI_techCount
        call Preload(head + "TECH|" + I2S(ORDAI_techs[i]) + "|" + I2S(GetPlayerTechCount(Player(0), ORDAI_techs[i], true)))
        set i = i + 1
    endloop
    // The 기록지침 window: mission, quest and navigation rows (synthetic TECH ids).
    call ORDAI_Logbook()
    // Hidden chests: live count, 탐색 ready and positions (synthetic TECH ids, ord_ai.treasure).
    call ORDAI_Treasure()
    // Round boss health and the round clock (synthetic TECH ids, ord_ai.boss).
    call ORDAI_Boss()
    // Simulator comparison rows for action 6 (ord_ai.diagnostics), not policy input.
    call ORDAI_ArenaDiag()
    call Preload(head + "END|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence))
    call PreloadGenEnd("ORD_AI\\state.txt")
    set u = null
endfunction

function ORDAI_Observed takes nothing returns nothing
    // Do not let a cast that missed this transition execute in a later sequence.
    if ORDAI_castUnit != null and ORDAI_ok == 0 and GetUnitCurrentOrder(ORDAI_castUnit) == ORDAI_castOrder then
        call IssueImmediateOrder(ORDAI_castUnit, "stop")
    endif
    // A navigation or quest choice counts once the map's handler has recorded it.
    if ORDAI_flagAction != 0 and ORDAI_synced and not ORDAI_flagBefore and ORDAI_Flag(ORDAI_syncPrefix) then
        set ORDAI_ok = 1
    endif
    set ORDAI_flagAction = 0
    set ORDAI_castUnit = null
    call ORDAI_Export()
    set ORDAI_busy = false
endfunction

function ORDAI_RestartFailed takes nothing returns nothing
    // Reaching this callback proves RestartGame did not replace this VM.
    set ORDAI_restarting = false
    set ORDAI_ok = 0
    call ORDAI_Boot(ORDAI_bootEpisode, ORDAI_bootSequence, 0)
    call DisplayTextToPlayer(Player(0), 0., 0., "ORD_AI: native restart failed; reset rejected. Reload the training map.")
    call ORDAI_Observed()
endfunction

function ORDAI_Receive takes nothing returns nothing
    if not ORDAI_sending or GetTriggerPlayer() != Player(0) or BlzGetTriggerSyncData() != ORDAI_wire then
        return
    endif
    set ORDAI_sending = false
    set ORDAI_replyEpisode = ORDAI_inEpisode
    set ORDAI_replySequence = ORDAI_inSequence
    set ORDAI_ok = 0
    set ORDAI_expect = 0
    set ORDAI_spellExpect = 0
    set ORDAI_selectExpect = null
    set ORDAI_flagAction = 0
    set ORDAI_synced = false
    set ORDAI_action = ORDAI_inAction
    if ORDAI_action == -1 then
        set ORDAI_bootEpisode = ORDAI_inEpisode
        set ORDAI_bootSequence = ORDAI_inSequence
        if tU or ORDAI_episode != 0 then
            if ORDAI_Boot(ORDAI_inEpisode, ORDAI_inSequence, 1) then
                set ORDAI_restarting = true
                call TimerStart(ORDAI_observe, 5., false, function ORDAI_RestartFailed)
                call RestartGame(false)
                return
            endif
        elseif ORDAI_Boot(ORDAI_inEpisode, ORDAI_inSequence, 0) then
            set ORDAI_episode = ORDAI_inEpisode
            set ORDAI_sequence = ORDAI_inSequence
            set ORDAI_setup = true
            call ORDAI_Export()
            return
        endif
    else
        // Reserve before dispatch: duplicates cannot re-enter a consuming action.
        set ORDAI_sequence = ORDAI_inSequence
        // The observation is scheduled first, so even an action thread the engine stops
        // (operation limit) still answers this sequence.
        call TimerStart(ORDAI_observe, 1., false, function ORDAI_Observed)
        if ORDAI_Status() == 1 then
            call ORDAI_Deploy()
            call ORDAI_Act(ORDAI_action)
        endif
        return
    endif
    call TimerStart(ORDAI_observe, 1., false, function ORDAI_Observed)
endfunction

// The map's lobby selectors (DYR): difficulty ls 1..6, Isekai toggle LU (l4s), start aU.
function ORDAI_Selector takes integer difficulty returns unit
    if difficulty == 2 then
        return IU
    elseif difficulty == 3 then
        return XU
    elseif difficulty == 4 then
        return nU
    elseif difficulty == 5 then
        return TU
    elseif difficulty == 6 then
        return OU
    endif
    return AU
endfunction

function ORDAI_Select takes unit u returns nothing
    call SelectUnit(u, false)
    call SelectUnit(u, true)
    set ORDAI_setupWait = 4
endfunction

function ORDAI_Tick takes nothing returns nothing
    local string text = ""
    // Lobby settings apply after Init, so re-assert the speed on every tick.
    if GetGameSpeed() != MAP_SPEED_FASTEST then
        call SetGameSpeed(MAP_SPEED_FASTEST)
    endif
    if tU and vp[0] != null then
        set ORDAI_started = true
    endif
    if ORDAI_setup then
        if ORDAI_Status() != 0 then
            set ORDAI_setup = false
            set ORDAI_ok = 1
            call TimerStart(ORDAI_observe, 1., false, function ORDAI_Observed)
        elseif not tU and aU != null and ks[0] then
            // SelectUnit fires the map's original EVENT_PLAYER_UNIT_SELECTED path. Each click
            // waits a second so a toggle (Isekai) is seen before it could be clicked twice.
            if ORDAI_setupWait > 0 then
                set ORDAI_setupWait = ORDAI_setupWait - 1
            elseif ls != ORDAI_difficulty then
                call ORDAI_Select(ORDAI_Selector(ORDAI_difficulty))
            elseif ORDAI_isekai and not Zs[2] then
                call ORDAI_Select(LU)
            else
                call ORDAI_Select(aU)
            endif
        endif
        return
    endif
    if ORDAI_busy then
        return
    endif
    // Each command has its own file: a path already read is never re-read by this game.
    if ORDAI_episode != 0 then
        set text = ORDAI_Load("ORD_AI\\cmd_" + I2S(ORDAI_episode) + "_" + I2S(ORDAI_sequence + 1) + ".txt")
    endif
    if not ORDAI_Parse(text) then
        set text = ORDAI_Load("ORD_AI\\reset_" + I2S(ORDAI_bootEpisode + 1) + ".txt")
        if not ORDAI_Parse(text) then
            if ORDAI_episode != 0 or ORDAI_bootPhase != 1 then
                return
            endif
            set text = ORDAI_Load("ORD_AI\\reset_" + I2S(ORDAI_bootEpisode) + ".txt")
            if not ORDAI_Parse(text) then
                return
            endif
        endif
    endif
    if ORDAI_inAction == -1 then
        if ORDAI_inEpisode <= ORDAI_bootEpisode and not (ORDAI_episode == 0 and ORDAI_bootPhase == 1 and ORDAI_inEpisode == ORDAI_bootEpisode and ORDAI_inSequence == ORDAI_bootSequence) then
            return
        endif
        set ORDAI_bootPhase = 0
    elseif ORDAI_episode == 0 or ORDAI_inEpisode != ORDAI_episode or ORDAI_inSequence <= ORDAI_sequence then
        return
    endif
    set ORDAI_wire = text
    set ORDAI_busy = true
    set ORDAI_sending = true
    if not BlzSendSyncData("ORD_AI", text) then
        set ORDAI_busy = false
        set ORDAI_sending = false
        call DisplayTextToPlayer(Player(0), 0., 0., "ORD_AI: local command synchronization failed.")
    endif
endfunction

function ORDAI_Init takes nothing returns nothing
    local integer i = 0
    local integer users = 0
    local string boot
    if ORDAI_enabled then
        return
    endif
    loop
        exitwhen i >= bj_MAX_PLAYER_SLOTS
        if GetPlayerSlotState(Player(i)) == PLAYER_SLOT_STATE_PLAYING and GetPlayerController(Player(i)) == MAP_CONTROL_USER then
            set users = users + 1
        endif
        set i = i + 1
    endloop
    if users != 1 or GetPlayerSlotState(Player(0)) != PLAYER_SLOT_STATE_PLAYING or GetPlayerController(Player(0)) != MAP_CONTROL_USER or GetLocalPlayer() != Player(0) then
        call DisplayTextToPlayer(GetLocalPlayer(), 0., 0., "ORD_AI: disabled; requires one offline user in player slot 0.")
        return
    endif
    set ORDAI_enabled = true
    set ORDAI_data = InitHashtable()
    set ORDAI_units = CreateGroup()
    set ORDAI_poll = CreateTimer()
    set ORDAI_observe = CreateTimer()
    set ORDAI_sync = CreateTrigger()
    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_sync, Player(0), "ORD_AI", false)
    call TriggerAddAction(ORDAI_sync, function ORDAI_Receive)
    call TriggerAddCondition(Ys, Condition(function ORDAI_Fused))
    set ORDAI_shop = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(ORDAI_shop, Player(0), EVENT_PLAYER_UNIT_SELL, null)
    call TriggerRegisterPlayerUnitEvent(ORDAI_shop, Player(0), EVENT_PLAYER_UNIT_RESEARCH_START, null)
    call TriggerRegisterPlayerUnitEvent(ORDAI_shop, Player(0), EVENT_PLAYER_UNIT_TRAIN_START, null)
    call TriggerAddCondition(ORDAI_shop, Condition(function ORDAI_Bought))
    set ORDAI_targets = CreateGroup()
    set ORDAI_tried = CreateGroup()
    set ORDAI_spell = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(ORDAI_spell, Player(0), EVENT_PLAYER_UNIT_SPELL_EFFECT, null)
    call TriggerAddCondition(ORDAI_spell, Condition(function ORDAI_Spelled))
    set ORDAI_select = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(ORDAI_select, Player(0), EVENT_PLAYER_UNIT_SELECTED, null)
    call TriggerAddCondition(ORDAI_select, Condition(function ORDAI_Selected))
    set ORDAI_syncs = CreateTrigger()
    call TriggerAddCondition(ORDAI_syncs, Condition(function ORDAI_Synced))
    // Routes, TECH rows and the sync prefixes of the generated input table.
    call ORDAI_InitInputs()
    call ORDAI_InitRecipes()
    // Game-time clock for replay comparison: it only advances while the game runs.
    set ORDAI_mainGv = Gv
    set ORDAI_clock = CreateTimer()
    call TimerStart(ORDAI_clock, 1000000., false, null)
    set boot = ORDAI_Load("ORD_AI\\boot.txt")
    if ORDAI_Parse(boot) and (ORDAI_inAction == 0 or ORDAI_inAction == 1) then
        set ORDAI_bootEpisode = ORDAI_inEpisode
        set ORDAI_bootSequence = ORDAI_inSequence
        set ORDAI_bootPhase = ORDAI_inAction
    endif
    call ORDAI_Export()
    call TimerStart(ORDAI_poll, 0.25, true, function ORDAI_Tick)
endfunction
