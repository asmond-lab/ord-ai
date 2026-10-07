// Combat-event record build only (ord-ai build --kind record): appended after bridge_actions.j.
// A replay-comparison record like telemetry.txt: the policy never reads it.
// Every line carries the bridge clock (game seconds) and the map random state Gv.

function ORDAI_EventClock takes nothing returns real
    if ORDAI_clock == null then
        return 0.
    endif
    return TimerGetElapsed(ORDAI_clock)
endfunction

function ORDAI_EventStamp takes nothing returns string
    return R2S(ORDAI_EventClock()) + "|" + I2S(Gv)
endfunction

function ORDAI_EventAdd takes string line returns nothing
    if ORDAI_eventCount < 1500 then
        set ORDAI_events[ORDAI_eventCount] = line
        set ORDAI_eventCount = ORDAI_eventCount + 1
    else
        set ORDAI_eventDropped = ORDAI_eventDropped + 1
    endif
endfunction

function ORDAI_Who takes unit u returns string
    return I2S(GetHandleId(u)) + "|" + I2S(GetUnitTypeId(u)) + "|" + I2S(GetPlayerId(GetOwningPlayer(u)))
endfunction

// ATK|time|Gv|attacker handle|type|owner|target handle|type|owner
function ORDAI_EventAttacked takes nothing returns boolean
    call ORDAI_EventAdd("ATK|" + ORDAI_EventStamp() + "|" + ORDAI_Who(GetAttacker()) + "|" + ORDAI_Who(GetTriggerUnit()))
    return false
endfunction

// Damage is summed per target until the next export (one DMG line per damaged unit there).
function ORDAI_EventDamaged takes nothing returns boolean
    local unit target = GetTriggerUnit()
    local integer id = GetHandleId(target)
    if not HaveSavedInteger(ORDAI_hits, 1, id) then
        set ORDAI_hitUnits[ORDAI_hitCount] = target
        set ORDAI_hitCount = ORDAI_hitCount + 1
        call SaveReal(ORDAI_hits, 2, id, ORDAI_EventClock())
    endif
    call SaveInteger(ORDAI_hits, 1, id, LoadInteger(ORDAI_hits, 1, id) + 1)
    call SaveReal(ORDAI_hits, 0, id, LoadReal(ORDAI_hits, 0, id) + GetEventDamage())
    set target = null
    return false
endfunction

// DIE|time|Gv|dying handle|type|owner|killer handle|type|owner (killer 0|0|-1 when none)
function ORDAI_EventDied takes nothing returns boolean
    local unit killer = GetKillingUnit()
    local string by = "0|0|-1"
    if killer != null then
        set by = ORDAI_Who(killer)
    endif
    call ORDAI_EventAdd("DIE|" + ORDAI_EventStamp() + "|" + ORDAI_Who(GetTriggerUnit()) + "|" + by)
    set killer = null
    return false
endfunction

function ORDAI_EventsInit takes nothing returns nothing
    local integer i = 0
    set ORDAI_eventAttack = CreateTrigger()
    set ORDAI_eventDamage = CreateTrigger()
    set ORDAI_eventDeath = CreateTrigger()
    loop
        exitwhen i >= bj_MAX_PLAYER_SLOTS
        call TriggerRegisterPlayerUnitEvent(ORDAI_eventAttack, Player(i), EVENT_PLAYER_UNIT_ATTACKED, null)
        call TriggerRegisterPlayerUnitEvent(ORDAI_eventDamage, Player(i), EVENT_PLAYER_UNIT_DAMAGED, null)
        call TriggerRegisterPlayerUnitEvent(ORDAI_eventDeath, Player(i), EVENT_PLAYER_UNIT_DEATH, null)
        set i = i + 1
    endloop
    call TriggerAddCondition(ORDAI_eventAttack, Condition(function ORDAI_EventAttacked))
    call TriggerAddCondition(ORDAI_eventDamage, Condition(function ORDAI_EventDamaged))
    call TriggerAddCondition(ORDAI_eventDeath, Condition(function ORDAI_EventDied))
endfunction

// Simulator comparison rows (read-only; no rule, input or reward depends on them):
// CTX|round Av|story KP
// LANE|handle|type|x|y|life|max life|order: Player(6) units of lane 0 (A901 level 1, set at
//   every spawn: war3map.j 6239, 51759, 51783, 61841), the units js[0] counts.
// SB|handle|type|owner|life|max life|x|y|A04M|A04N|A04O|A04P|A04R: living enemies inside the
//   story arena of ORDAI_Eligible a == 6 (the story bosses), with the armor-break and spell
//   stack levels the map adds to them (war3map.j 6596, 7622, 18073, 7018, 5513).
// MOV|handle|type|move speed|Amov level|paused|hidden|Aloc level: every living non-structure
//   Player(0) unit (telemetry.txt POS lists the visible ones), so the ORDAI_Eligible conditions
//   (pause, hidden, locust, move speed) can be read per unit.
function ORDAI_Flag01 takes boolean value returns integer
    if value then
        return 1
    endif
    return 0
endfunction

// Run through ExecuteFunc so these loops get their own operation limit.
function ORDAI_EventsDiag takes nothing returns nothing
    local string head = "ORD_AI|1|"
    local unit u
    local rect arena = Rect(GetRectMinX(TP[0]), GetRectMinY(TP[2]), GetRectMaxX(EY), GetRectMaxY(EY))
    call Preload(head + "CTX|" + I2S(Av) + "|" + I2S(KP))
    call GroupEnumUnitsOfPlayer(ORDAI_targets, Player(6), null)
    loop
        set u = FirstOfGroup(ORDAI_targets)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_targets, u)
        if GetWidgetLife(u) > 0.405 and not IsUnitType(u, UNIT_TYPE_DEAD) and GetUnitAbilityLevel(u, 'A901') == 1 then
            call Preload(head + "LANE|" + I2S(GetHandleId(u)) + "|" + I2S(GetUnitTypeId(u)) + "|" + R2S(GetUnitX(u)) + "|" + R2S(GetUnitY(u)) + "|" + R2S(GetWidgetLife(u)) + "|" + R2S(GetUnitState(u, UNIT_STATE_MAX_LIFE)) + "|" + I2S(GetUnitCurrentOrder(u)))
        endif
    endloop
    call GroupEnumUnitsInRect(ORDAI_targets, arena, null)
    call RemoveRect(arena)
    set arena = null
    loop
        set u = FirstOfGroup(ORDAI_targets)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_targets, u)
        if GetWidgetLife(u) > 0.405 and not IsUnitType(u, UNIT_TYPE_DEAD) and IsUnitEnemy(u, Player(0)) and GetUnitX(u) >= GetRectMinX(TP[0]) and GetUnitX(u) <= GetRectMaxX(EY) and GetUnitY(u) >= GetRectMinY(TP[2]) and GetUnitY(u) <= GetRectMaxY(EY) then
            call Preload(head + "SB|" + ORDAI_Who(u) + "|" + R2S(GetWidgetLife(u)) + "|" + R2S(GetUnitState(u, UNIT_STATE_MAX_LIFE)) + "|" + R2S(GetUnitX(u)) + "|" + R2S(GetUnitY(u)) + "|" + I2S(GetUnitAbilityLevel(u, 'A04M')) + "|" + I2S(GetUnitAbilityLevel(u, 'A04N')) + "|" + I2S(GetUnitAbilityLevel(u, 'A04O')) + "|" + I2S(GetUnitAbilityLevel(u, 'A04P')) + "|" + I2S(GetUnitAbilityLevel(u, 'A04R')))
        endif
    endloop
    call GroupEnumUnitsOfPlayer(ORDAI_targets, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_targets)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_targets, u)
        if GetWidgetLife(u) > 0.405 and not IsUnitType(u, UNIT_TYPE_DEAD) and not IsUnitType(u, UNIT_TYPE_STRUCTURE) then
            call Preload(head + "MOV|" + I2S(GetHandleId(u)) + "|" + I2S(GetUnitTypeId(u)) + "|" + R2S(GetUnitMoveSpeed(u)) + "|" + I2S(GetUnitAbilityLevel(u, 'Amov')) + "|" + I2S(ORDAI_Flag01(IsUnitPaused(u))) + "|" + I2S(ORDAI_Flag01(IsUnitHidden(u))) + "|" + I2S(GetUnitAbilityLevel(u, 'Aloc')))
        endif
    endloop
    set u = null
endfunction

// The events since the previous export, for the same episode and sequence as state.txt.
// DMG|first hit time|handle|type|owner|damage sum|hits|life now|max life
function ORDAI_EventsExport takes nothing returns nothing
    local string head = "ORD_AI|1|"
    local unit target
    local integer id
    local integer i = 0
    call PreloadGenClear()
    call PreloadGenStart()
    call Preload(head + "EVT|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence) + "|" + ORDAI_EventStamp())
    loop
        exitwhen i >= ORDAI_eventCount
        call Preload(head + ORDAI_events[i])
        set ORDAI_events[i] = null
        set i = i + 1
    endloop
    set i = 0
    loop
        exitwhen i >= ORDAI_hitCount
        set target = ORDAI_hitUnits[i]
        set id = GetHandleId(target)
        call Preload(head + "DMG|" + R2S(LoadReal(ORDAI_hits, 2, id)) + "|" + ORDAI_Who(target) + "|" + R2S(LoadReal(ORDAI_hits, 0, id)) + "|" + I2S(LoadInteger(ORDAI_hits, 1, id)) + "|" + R2S(GetWidgetLife(target)) + "|" + R2S(GetUnitState(target, UNIT_STATE_MAX_LIFE)))
        set ORDAI_hitUnits[i] = null
        set i = i + 1
    endloop
    // Exact health of the 해왕류 (unit jB, war3map.j 39111) and the 황금 종 (destructable Hd,
    // 81170) for the simulator's health check: the bridge's rounded percent reads 99.
    if jB != null then
        call Preload(head + "HP|jB|" + R2S(GetWidgetLife(jB)) + "|" + R2S(GetUnitState(jB, UNIT_STATE_MAX_LIFE)))
    endif
    if Hd != null then
        call Preload(head + "HP|Hd|" + R2S(GetDestructableLife(Hd)) + "|" + R2S(GetDestructableMaxLife(Hd)))
    endif
    call ExecuteFunc("ORDAI_EventsDiag")
    call Preload(head + "EEND|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence) + "|" + I2S(ORDAI_eventCount) + "|" + I2S(ORDAI_hitCount) + "|" + I2S(ORDAI_eventDropped))
    call PreloadGenEnd("ORD_AI\\events.txt")
    call FlushChildHashtable(ORDAI_hits, 0)
    call FlushChildHashtable(ORDAI_hits, 1)
    call FlushChildHashtable(ORDAI_hits, 2)
    set ORDAI_eventCount = 0
    set ORDAI_hitCount = 0
    set ORDAI_eventDropped = 0
    set target = null
endfunction
