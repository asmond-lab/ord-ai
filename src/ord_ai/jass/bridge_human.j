// Human-play build only (ord-ai build --human): inserted after bridge_actions.j, before
// bridge_functions.j. Player(0)'s real inputs are caught through map events, converted to
// catalog codes (-1: no catalog code, kept with its raw identifiers) and written to
// ORD_AI\human.txt at every export (ORDAI_HumanExport), never into state.txt.
// Hashtable parents (ORDAI_data): 200 sold unit, 201 trained unit, 202 research, 203 ability,
// 204 StringHash("<prefix> <data>") of a sync, 205 catalogued fusion codes, 206 enter-region
// handle, 208 selected unit type, 209 attack objective number, 210 live chest index; each
// maps to the catalog code.

// Preload strings end at a double quote and treat a backslash as an escape.
function ORDAI_HumanText takes string text returns string
    local integer i = 0
    local integer size = StringLength(text)
    local string c
    local string clean = ""
    if size > 120 then
        set size = 120
    endif
    loop
        exitwhen i >= size
        set c = SubString(text, i, i + 1)
        if c == "\"" then
            set c = "'"
        elseif c == "\\" then
            set c = "/"
        endif
        set clean = clean + c
        set i = i + 1
    endloop
    return clean
endfunction

// A four-character rawcode as text ("A0XX"), or its number when a byte is not printable.
function ORDAI_HumanId takes integer id returns string
    local string chars = " !_#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[_]^_`abcdefghijklmnopqrstuvwxyz{|}~"
    local integer rest = id
    local integer b
    local integer i = 0
    local string text = ""
    if id <= 0 then
        return I2S(id)
    endif
    loop
        exitwhen i >= 4
        set b = rest - rest / 256 * 256
        set rest = rest / 256
        if b < 32 or b > 126 then
            return I2S(id)
        endif
        set text = SubString(chars, b - 32, b - 31) + text
        set i = i + 1
    endloop
    return text
endfunction

function ORDAI_HumanAdd takes integer found, string kind, string raw returns nothing
    if ORDAI_humanCount >= 1000 then
        set ORDAI_humanDropped = ORDAI_humanDropped + 1
        return
    endif
    set ORDAI_humanCode[ORDAI_humanCount] = found
    set ORDAI_humanKind[ORDAI_humanCount] = kind
    set ORDAI_humanRaw[ORDAI_humanCount] = ORDAI_HumanText(raw)
    set ORDAI_humanCount = ORDAI_humanCount + 1
endfunction

function ORDAI_HumanLookup takes integer parent, integer key returns integer
    if HaveSavedInteger(ORDAI_data, parent, key) then
        return LoadInteger(ORDAI_data, parent, key)
    endif
    return -1
endfunction

// One enter region per catalogued pad; the map's rects exist once the game has started.
function ORDAI_HumanPad takes integer found, rect r returns nothing
    local region area
    if r == null then
        return
    endif
    set area = CreateRegion()
    call RegionAddRect(area, r)
    call TriggerRegisterEnterRegion(ORDAI_humanEnter, area, null)
    call SaveInteger(ORDAI_data, 206, GetHandleId(area), found)
    set area = null
endfunction

// HUMAN TABLE

// A05D 탐색 at (x, y) is chest action k (parent 210) for the nearest chest k of the last
// export's copy (ORDAI_chest*) whose square |dx| <= cP[0], |dy| <= cP[0] holds the point: the
// map's handler may already have zeroed the slot. No such chest: unmapped with the point.
function ORDAI_HumanTreasure takes string caster, real x, real y returns nothing
    local real reach = cP[0]
    local integer k = 0
    local integer best = -1
    local real bestD = 0.
    local real dx
    local real dy
    local string point = R2S(x) + " " + R2S(y)
    loop
        exitwhen k >= ORDAI_chestCount
        set dx = ORDAI_chestX[k] - x
        set dy = ORDAI_chestY[k] - y
        if dx < 0. then
            set dx = -dx
        endif
        if dy < 0. then
            set dy = -dy
        endif
        if dy > dx then
            set dx = dy
        endif
        if dx <= reach and (best < 0 or dx < bestD) then
            set best = k
            set bestD = dx
        endif
        set k = k + 1
    endloop
    if best >= 0 then
        call ORDAI_HumanAdd(ORDAI_HumanLookup(210, best), "spell", "A05D " + caster + " chest" + I2S(best) + " " + point)
    else
        call ORDAI_HumanAdd(-1, "spell", "A05D " + caster + " " + point)
    endif
endfunction

function ORDAI_HumanBought takes nothing returns boolean
    local eventid e = GetTriggerEventId()
    local integer id
    local string building = ORDAI_HumanId(GetUnitTypeId(GetTriggerUnit()))
    if e == EVENT_PLAYER_UNIT_SELL then
        set id = GetUnitTypeId(GetSoldUnit())
        call ORDAI_HumanAdd(ORDAI_HumanLookup(200, id), "sell", ORDAI_HumanId(id) + " " + building)
    elseif e == EVENT_PLAYER_UNIT_TRAIN_START then
        set id = GetTrainedUnitType()
        call ORDAI_HumanAdd(ORDAI_HumanLookup(201, id), "train", ORDAI_HumanId(id) + " " + building)
    elseif e == EVENT_PLAYER_UNIT_RESEARCH_START then
        set id = GetResearched()
        call ORDAI_HumanAdd(ORDAI_HumanLookup(202, id), "research", ORDAI_HumanId(id) + " " + building)
    endif
    set e = null
    return false
endfunction

// Recipe casts are recorded by ORDAI_HumanFused once the map's fusion succeeds. A05D 탐색
// goes to ORDAI_HumanTreasure instead of the caster-point ability code.
function ORDAI_HumanSpelled takes nothing returns boolean
    local integer id = GetSpellAbilityId()
    if id == 'A05D' then
        call ORDAI_HumanTreasure(ORDAI_HumanId(GetUnitTypeId(GetSpellAbilityUnit())), GetSpellTargetX(), GetSpellTargetY())
    elseif not LoadBoolean(ORDAI_data, 205, id) then
        call ORDAI_HumanAdd(ORDAI_HumanLookup(203, id), "spell", ORDAI_HumanId(id) + " " + ORDAI_HumanId(GetUnitTypeId(GetSpellAbilityUnit())))
    endif
    return false
endfunction

// Ys runs after every successful fusion (ODv): Rs is the recipe, ss its player.
function ORDAI_HumanFused takes nothing returns boolean
    local integer found = -1
    if ss == Player(0) then
        if LoadBoolean(ORDAI_data, 205, Rs) then
            set found = Rs
        endif
        call ORDAI_HumanAdd(found, "fusion", ORDAI_HumanId(Rs))
    endif
    return true
endfunction

// Only unit types with a map selection handler; the select input needs the unit in its rect.
function ORDAI_HumanSelected takes nothing returns boolean
    local unit u = GetTriggerUnit()
    local integer found = ORDAI_HumanLookup(208, GetUnitTypeId(u))
    if HaveSavedInteger(ORDAI_data, 208, GetUnitTypeId(u)) then
        if ORDAI_Rect(found) == null or not RectContainsUnit(ORDAI_Rect(found), u) then
            set found = -1
        endif
        call ORDAI_HumanAdd(found, "select", ORDAI_HumanId(GetUnitTypeId(u)))
    endif
    set u = null
    return false
endfunction

function ORDAI_HumanSynced takes nothing returns boolean
    local string key = BlzGetTriggerSyncPrefix() + " " + BlzGetTriggerSyncData()
    call ORDAI_HumanAdd(ORDAI_HumanLookup(204, StringHash(key)), "sync", key)
    return false
endfunction

function ORDAI_HumanChatted takes nothing returns boolean
    local string text = GetEventPlayerChatString()
    call ORDAI_HumanAdd(ORDAI_HumanChatCode(text), "chat", text)
    return false
endfunction

// Pads 1-6 are the base actions' rects (fY: 1 random wisp, 4 evolution wisp; CY 2, aY 3
// random wisp; nP[0] 5 and EY 6 a fighter); input pads keep their table unit type (0: a
// fighter). Any other Player(0) unit entering a pad is kept unmapped as pad<code> <type>.
function ORDAI_HumanEntered takes nothing returns boolean
    local unit u = GetTriggerUnit()
    local integer pad = LoadInteger(ORDAI_data, 206, GetHandleId(GetTriggeringRegion()))
    local integer kind = GetUnitTypeId(u)
    local integer want
    local boolean fits
    if GetOwningPlayer(u) != Player(0) or GetUnitAbilityLevel(u, 'Aloc') > 0 or IsUnitType(u, UNIT_TYPE_STRUCTURE) then
        set u = null
        return false
    endif
    if pad == 1 and kind == 'e0HP' then
        set pad = 4
        set want = 'e0HP'
    elseif pad >= 1 and pad <= 3 then
        set want = 'e0IX'
    elseif pad == 5 or pad == 6 then
        set want = 0
    else
        set want = LoadInteger(ORDAI_data, 3, pad)
    endif
    if want == 0 then
        set fits = kind != 'e0IX' and kind != 'e0HP' and GetUnitPointValue(u) > 0 and BlzGetUnitBaseDamage(u, 0) > 0
    else
        set fits = kind == want
    endif
    if fits then
        call ORDAI_HumanAdd(pad, "region", ORDAI_HumanId(kind))
    else
        call ORDAI_HumanAdd(-1, "region", "pad" + I2S(pad) + " " + ORDAI_HumanId(kind))
    endif
    set u = null
    return false
endfunction

// Smart (right-click) or attack orders on a 항해목표 objective (ORDAI_Objective numbers).
function ORDAI_HumanOrdered takes nothing returns boolean
    local integer order = GetIssuedOrderId()
    local destructable d = GetOrderTargetDestructable()
    local unit t = GetOrderTargetUnit()
    local integer raw = 0
    local integer target = 0
    if order != 851971 and order != 851983 then
        set d = null
        set t = null
        return false
    endif
    if t != null then
        set raw = GetUnitTypeId(t)
    endif
    if d != null and d == Hd then
        set target = 1
    elseif d != null and d == Bd then
        set target = 2
    elseif d != null and d == Ud then
        set target = 3
    elseif t != null and t == jB then
        set target = 4
    elseif t != null and GetOwningPlayer(t) == Player(5) and (raw == 'o02Q' or raw == 'o02M' or raw == 'o02R') then
        set target = 5
    elseif t != null and GetOwningPlayer(t) == Player(5) and (raw == 'n00E' or raw == 'n00F' or raw == 'n00G') and GetUnitAbilityLevel(t, 'A90A') == 1 then
        set target = 6
    endif
    if target != 0 then
        call ORDAI_HumanAdd(ORDAI_HumanLookup(209, target), "attack", I2S(order) + " " + I2S(target) + " " + ORDAI_HumanId(raw))
    endif
    set d = null
    set t = null
    return false
endfunction

function ORDAI_HumanArm takes nothing returns nothing
    set ORDAI_humanArmed = true
    call ORDAI_HumanPads()
endfunction

function ORDAI_HumanInit takes nothing returns nothing
    set ORDAI_humanShop = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(ORDAI_humanShop, Player(0), EVENT_PLAYER_UNIT_SELL, null)
    call TriggerRegisterPlayerUnitEvent(ORDAI_humanShop, Player(0), EVENT_PLAYER_UNIT_RESEARCH_START, null)
    call TriggerRegisterPlayerUnitEvent(ORDAI_humanShop, Player(0), EVENT_PLAYER_UNIT_TRAIN_START, null)
    call TriggerAddCondition(ORDAI_humanShop, Condition(function ORDAI_HumanBought))
    set ORDAI_humanSpell = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(ORDAI_humanSpell, Player(0), EVENT_PLAYER_UNIT_SPELL_EFFECT, null)
    call TriggerAddCondition(ORDAI_humanSpell, Condition(function ORDAI_HumanSpelled))
    set ORDAI_humanSelect = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(ORDAI_humanSelect, Player(0), EVENT_PLAYER_UNIT_SELECTED, null)
    call TriggerAddCondition(ORDAI_humanSelect, Condition(function ORDAI_HumanSelected))
    set ORDAI_humanSync = CreateTrigger()
    call TriggerAddCondition(ORDAI_humanSync, Condition(function ORDAI_HumanSynced))
    set ORDAI_humanChat = CreateTrigger()
    call TriggerRegisterPlayerChatEvent(ORDAI_humanChat, Player(0), "", false)
    call TriggerAddCondition(ORDAI_humanChat, Condition(function ORDAI_HumanChatted))
    set ORDAI_humanEnter = CreateTrigger()
    call TriggerAddCondition(ORDAI_humanEnter, Condition(function ORDAI_HumanEntered))
    set ORDAI_humanOrder = CreateTrigger()
    call TriggerRegisterPlayerUnitEvent(ORDAI_humanOrder, Player(0), EVENT_PLAYER_UNIT_ISSUED_TARGET_ORDER, null)
    call TriggerAddCondition(ORDAI_humanOrder, Condition(function ORDAI_HumanOrdered))
    call TriggerAddCondition(Ys, Condition(function ORDAI_HumanFused))
    // Code lookups and the map's sync prefixes (generated table).
    call ORDAI_HumanTable()
endfunction

// The inputs gathered since the previous export, for the same episode and sequence as state.txt.
function ORDAI_HumanExport takes nothing returns nothing
    local string head = "ORD_AI|1|"
    local string found
    local integer i = 0
    call PreloadGenClear()
    call PreloadGenStart()
    call Preload(head + "HUMAN|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence))
    loop
        exitwhen i >= ORDAI_humanCount
        set found = ""
        if ORDAI_humanCode[i] >= 0 then
            set found = I2S(ORDAI_humanCode[i])
        endif
        call Preload(head + "IN|" + found + "|" + ORDAI_humanKind[i] + "|" + ORDAI_humanRaw[i])
        set i = i + 1
    endloop
    call Preload(head + "HEND|" + I2S(ORDAI_replyEpisode) + "|" + I2S(ORDAI_replySequence) + "|" + I2S(ORDAI_humanCount) + "|" + I2S(ORDAI_humanDropped))
    call PreloadGenEnd("ORD_AI\\human.txt")
    set ORDAI_humanCount = 0
    set ORDAI_humanDropped = 0
endfunction
