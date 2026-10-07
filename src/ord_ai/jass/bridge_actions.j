// Append after ALL original functions, before bridge_functions.j.
function ORDAI_Visible takes unit u returns boolean
    return GetWidgetLife(u) > 0.405 and not IsUnitType(u, UNIT_TYPE_DEAD) and not IsUnitType(u, UNIT_TYPE_STRUCTURE) and not IsUnitHidden(u) and GetUnitAbilityLevel(u, 'Aloc') == 0
endfunction

function ORDAI_Fused takes nothing returns boolean
    if ORDAI_busy and ss == Player(0) and Rs == ORDAI_action then
        set ORDAI_ok = 1
    endif
    // Runs before the mission's own Ys condition (jeR 50081): the fusion that sets Jh[0]
    // is the last one recorded here, so ORDAI_Logbook knows its round (패스트유니크 < 8).
    if ss == Player(0) and not Jh[0] then
        set ORDAI_fastRound = Av
    endif
    return true
endfunction

// A purchase, training or research is accepted only when the engine actually sold,
// started training or started researching it.
function ORDAI_Bought takes nothing returns boolean
    if ORDAI_busy and ORDAI_expect != 0 and (GetUnitTypeId(GetSoldUnit()) == ORDAI_expect or GetResearched() == ORDAI_expect or GetTrainedUnitType() == ORDAI_expect) then
        set ORDAI_ok = 1
    endif
    return false
endfunction

// An ability input is accepted when the expected ability of a Player(0) unit takes effect.
function ORDAI_Spelled takes nothing returns boolean
    if ORDAI_busy and ORDAI_spellExpect != 0 and GetSpellAbilityId() == ORDAI_spellExpect then
        set ORDAI_ok = 1
    endif
    return false
endfunction

// A selection input is accepted when Player(0)'s selection event reports that unit.
function ORDAI_Selected takes nothing returns boolean
    if ORDAI_busy and ORDAI_selectExpect != null and GetTriggerUnit() == ORDAI_selectExpect then
        set ORDAI_ok = 1
    endif
    return false
endfunction

// The map's sync trigger for the same prefix received our payload (ORDAI_Observed then
// checks that its handler recorded the choice).
function ORDAI_Synced takes nothing returns boolean
    if ORDAI_busy and ORDAI_flagAction != 0 and BlzGetTriggerSyncPrefix() == ORDAI_syncPrefix and BlzGetTriggerSyncData() == ORDAI_syncData then
        set ORDAI_synced = true
    endif
    return false
endfunction

// Map state a sync handler sets when it accepts: vV (navigation chosen: UfR 32145, GJs,
// NCv, jFv, bOv) and Dr (quest reroll used: HDR 14414).
function ORDAI_Flag takes string prefix returns boolean
    if prefix == "QUST_INDX" then
        return Dr[0]
    endif
    return vV[0]
endfunction

// Verified against war3map.w3u/w3q: pp[] is gamble house 1 (h062 sells h06F, H0AZ) until
// 35000 high-gamble gold replaces it with gamble house 2 (h08C sells H0B0); rp[] is
// upgrade house 1 (h06Q researches R00F common, R00E special, R008 rare).
function ORDAI_Shop takes integer a returns nothing
    local unit shop = pp[0]
    local integer id = 0
    if a == 8 then
        set id = 'h06F'
    elseif a == 9 then
        set id = 'H0AZ'
    elseif a == 13 then
        set id = 'H0B0'
    else
        set shop = rp[0]
        if a == 10 then
            set id = 'R00F'
        elseif a == 11 then
            set id = 'R00E'
        elseif a == 12 then
            set id = 'R008'
        endif
    endif
    if shop != null and id != 0 then
        set ORDAI_expect = id
        call IssueImmediateOrderById(shop, id)
    endif
    set shop = null
endfunction

// Verified against war3map.w3a: 109 AHwe, 33 Aroa, 8 Arsg, 6 Auhf recipes.
function ORDAI_Order takes integer a returns integer
    if a == 'A005' or a == 'A00E' or a == 'A00N' or a == 'A00Q' or a == 'A012' or a == 'A013' or a == 'A014' or a == 'A015' or a == 'A016' or a == 'A017' or a == 'A018' or a == 'A019' or a == 'A01A' or a == 'A01B' or a == 'A01C' or a == 'A01E' or a == 'A01F' or a == 'A01G' or a == 'A01H' or a == 'A01I' or a == 'A01J' or a == 'A01K' or a == 'A01L' or a == 'A01M' or a == 'A01N' or a == 'A01O' or a == 'A01P' or a == 'A01Q' or a == 'A01R' or a == 'A02M' or a == 'A02T' or a == 'A032' or a == 'A113' then
        return OrderId("roar")
    elseif a == 'A0K2' or a == 'A0K3' or a == 'A0K4' or a == 'A0K6' or a == 'A0K7' or a == 'A19Z' or a == 'A1A0' or a == 'A1A1' then
        return OrderId("summongrizzly")
    elseif a == 'A0QG' or a == 'A19S' or a == 'A19T' or a == 'A19U' or a == 'A19V' or a == 'A19W' then
        return OrderId("unholyfrenzy")
    endif
    return OrderId("waterelemental")
endfunction

function ORDAI_Eligible takes unit u, integer a returns boolean
    if not ORDAI_Visible(u) or IsUnitPaused(u) then
        return false
    endif
    if a >= 1000 then
        return GetUnitAbilityLevel(u, a) > 0 and BlzGetUnitAbilityCooldownRemaining(u, a) <= 0.
    endif
    if GetUnitMoveSpeed(u) <= 0. or GetUnitCurrentOrder(u) == OrderId("move") then
        return false
    endif
    if (a >= 1 and a <= 3) or a == 7 then
        return GetUnitTypeId(u) == 'e0IX'
    elseif a == 4 then
        return GetUnitTypeId(u) == 'e0HP' and kU[0] == null
    elseif GetUnitPointValue(u) <= 0 or BlzGetUnitBaseDamage(u, 0) <= 0 then
        return false
    elseif a == 5 then
        return tP[0] == 0 and xP[0] == 0 and RectContainsUnit(gP[0], u)
    elseif a == 6 then
        // Story arena: four arrival pads below the shared boss, return gate above.
        return GetUnitX(u) >= GetRectMinX(TP[0]) and GetUnitX(u) <= GetRectMaxX(EY) and GetUnitY(u) >= GetRectMinY(TP[2]) and GetUnitY(u) <= GetRectMaxY(EY)
    endif
    return false
endfunction

function ORDAI_Pick takes integer a returns unit
    local unit u
    local unit best = null
    local integer rank
    local integer bestRank = -1
    local integer damage
    local integer bestDamage = -1
    call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_units)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_units, u)
        if ORDAI_Eligible(u, a) then
            set rank = 0
            set damage = 0
            if a == 5 or a == 6 then
                set rank = GetUnitPointValue(u)
                set damage = BlzGetUnitBaseDamage(u, 0)
            endif
            if best == null or rank > bestRank or (rank == bestRank and damage > bestDamage) or (rank == bestRank and damage == bestDamage and GetHandleId(u) < GetHandleId(best)) then
                set best = u
                set bestRank = rank
                set bestDamage = damage
            endif
        endif
    endloop
    set u = best
    set best = null
    return u
endfunction

// Input table, filled by the generated ORDAI_InitInputs: parent 2 kind (1 sell, 2 train,
// 3 research, 4 ability, 5 sync, 6 region, 7 select, 8 chat), 3 rawcode, 4-7 kind data,
// 8 filter count, 9 item count, 10 sync prefix, 11 chat handler, 20+ filters, 40+ items.
function ORDAI_Route takes integer a, integer kind, integer raw, integer p, integer q, integer r, integer s returns nothing
    call SaveInteger(ORDAI_data, 2, a, kind)
    call SaveInteger(ORDAI_data, 3, a, raw)
    call SaveInteger(ORDAI_data, 4, a, p)
    call SaveInteger(ORDAI_data, 5, a, q)
    call SaveInteger(ORDAI_data, 6, a, r)
    call SaveInteger(ORDAI_data, 7, a, s)
endfunction

function ORDAI_Filter takes integer a, integer value returns nothing
    local integer n = LoadInteger(ORDAI_data, 8, a)
    call SaveInteger(ORDAI_data, 20 + n, a, value)
    call SaveInteger(ORDAI_data, 8, a, n + 1)
endfunction

function ORDAI_Item takes integer a, integer value returns nothing
    local integer n = LoadInteger(ORDAI_data, 9, a)
    call SaveInteger(ORDAI_data, 40 + n, a, value)
    call SaveInteger(ORDAI_data, 9, a, n + 1)
endfunction

// BEGIN GENERATED INPUT TABLE (python -m ord_ai.build <map>; do not edit)
function ORDAI_InitInputs takes nothing returns nothing
    set ORDAI_techs[0] = 'R00F'
    set ORDAI_techs[1] = 'R00E'
    set ORDAI_techs[2] = 'R008'
    set ORDAI_techs[3] = 'R009'
    set ORDAI_techs[4] = 'R01M'
    set ORDAI_techs[5] = 'R01N'
    set ORDAI_techs[6] = 'R00P'
    set ORDAI_techs[7] = 'R00G'
    set ORDAI_techs[8] = 'R00H'
    set ORDAI_techs[9] = 'R00I'
    set ORDAI_techs[10] = 'R01S'
    set ORDAI_techs[11] = 'R018'
    set ORDAI_techs[12] = 'R002'
    set ORDAI_techs[13] = 'R003'
    set ORDAI_techs[14] = 'R004'
    set ORDAI_techs[15] = 'R000'
    set ORDAI_techs[16] = 'R019'
    set ORDAI_techs[17] = 'R032'
    set ORDAI_techs[18] = 'R02O'
    set ORDAI_techs[19] = 'R015'
    set ORDAI_techs[20] = 'R00X'
    set ORDAI_techs[21] = 'R013'
    set ORDAI_techs[22] = 'R007'
    set ORDAI_techs[23] = 'R014'
    set ORDAI_techs[24] = 'R016'
    set ORDAI_techs[25] = 'R02C'
    set ORDAI_techs[26] = 'R01V'
    set ORDAI_techs[27] = 'Rhgb'
    set ORDAI_techs[28] = 'Rhra'
    set ORDAI_techs[29] = 'Rhpt'
    set ORDAI_techs[30] = 'Rhfc'
    set ORDAI_techCount = 31
    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_syncs, Player(0), "NAVI_1IDX", false)
    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_syncs, Player(0), "NAVI_2IDX", false)
    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_syncs, Player(0), "NAVI_3IDX", false)
    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_syncs, Player(0), "NAVI_4IDX", false)
    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_syncs, Player(0), "NAVI_5IDX", false)
    call BlzTriggerRegisterPlayerSyncEvent(ORDAI_syncs, Player(0), "QUST_INDX", false)
    // h0AK
    call ORDAI_Route(14, 1, 'h0AK', 'h08C', 0, 0, 0)
    // h0AL
    call ORDAI_Route(15, 1, 'h0AL', 'h08C', 0, 0, 0)
    // H0AW
    call ORDAI_Route(16, 1, 'H0AW', 'h062', 'h08C', 0, 0)
    // h0AX
    call ORDAI_Route(17, 1, 'h0AX', 'h062', 0, 0, 0)
    // H0AU
    call ORDAI_Route(18, 1, 'H0AU', 'h07A', 0, 0, 0)
    // H0AT
    call ORDAI_Route(19, 1, 'H0AT', 'h07A', 0, 0, 0)
    // H0AQ
    call ORDAI_Route(20, 1, 'H0AQ', 'h07A', 0, 0, 0)
    // H0AP
    call ORDAI_Route(21, 1, 'H0AP', 'h07A', 0, 0, 0)
    // H0AO
    call ORDAI_Route(22, 1, 'H0AO', 'h07A', 0, 0, 0)
    // H0AS
    call ORDAI_Route(23, 1, 'H0AS', 'h07A', 0, 0, 0)
    // H0AR
    call ORDAI_Route(24, 1, 'H0AR', 'h07A', 0, 0, 0)
    // h069
    call ORDAI_Route(25, 2, 'h069', 'h062', 'h08C', 0, 0)
    // h06D
    call ORDAI_Route(26, 2, 'h06D', 'h062', 'h08C', 0, 0)
    // h06B
    call ORDAI_Route(27, 2, 'h06B', 'h062', 'h08C', 0, 0)
    // h06C
    call ORDAI_Route(28, 2, 'h06C', 'h062', 'h08C', 0, 0)
    // R002
    call ORDAI_Route(29, 3, 'R002', 'h06Q', 0, 0, 0)
    // R003
    call ORDAI_Route(30, 3, 'R003', 'h06Q', 0, 0, 0)
    // R004
    call ORDAI_Route(31, 3, 'R004', 'h06Q', 0, 0, 0)
    // R000
    call ORDAI_Route(32, 3, 'R000', 'h06Q', 0, 0, 0)
    // R019
    call ORDAI_Route(33, 3, 'R019', 'h06Q', 0, 0, 0)
    // R032
    call ORDAI_Route(34, 3, 'R032', 'h06R', 0, 0, 0)
    // R02O
    call ORDAI_Route(35, 3, 'R02O', 'h06R', 0, 0, 0)
    // R015
    call ORDAI_Route(36, 3, 'R015', 'h06R', 0, 0, 0)
    // R00X
    call ORDAI_Route(37, 3, 'R00X', 'h06R', 0, 0, 0)
    // R013
    call ORDAI_Route(38, 3, 'R013', 'h06R', 0, 0, 0)
    // R007
    call ORDAI_Route(39, 3, 'R007', 'h06R', 0, 0, 0)
    // R014
    call ORDAI_Route(40, 3, 'R014', 'h06R', 0, 0, 0)
    // R016
    call ORDAI_Route(41, 3, 'R016', 'h06R', 0, 0, 0)
    // R02C
    call ORDAI_Route(42, 3, 'R02C', 'h06R', 0, 0, 0)
    // R01V
    call ORDAI_Route(43, 3, 'R01V', 'h06R', 0, 0, 0)
    // Rhgb
    call ORDAI_Route(44, 3, 'Rhgb', 'h06S', 0, 0, 0)
    // Rhra
    call ORDAI_Route(45, 3, 'Rhra', 'h06S', 0, 0, 0)
    // Rhpt
    call ORDAI_Route(46, 3, 'Rhpt', 'h06S', 0, 0, 0)
    // Rhfc
    call ORDAI_Route(47, 3, 'Rhfc', 'h06S', 0, 0, 0)
    // A0EB
    call ORDAI_Route(48, 4, 'A0EB', OrderId("roar"), 0, 0, 0)
    // A0EJ
    call ORDAI_Route(49, 4, 'A0EJ', OrderId("roar"), 0, 0, 0)
    // A0EQ
    call ORDAI_Route(50, 4, 'A0EQ', OrderId("thunderbolt"), 1, 750, 0)
    // A099
    call ORDAI_Route(51, 4, 'A099', OrderId("silence"), 2, 700, 0)
    // A071
    call ORDAI_Route(52, 4, 'A071', OrderId("firebolt"), 1, 1150, 0)
    // A07F
    call ORDAI_Route(53, 4, 'A07F', OrderId("fanofknives"), 0, 0, 0)
    // A0HL
    call ORDAI_Route(54, 4, 'A0HL', OrderId("stomp"), 0, 0, 0)
    // A0HN
    call ORDAI_Route(55, 4, 'A0HN', OrderId("fingerofdeath"), 3, 0, 0)
    // A0HM
    call ORDAI_Route(56, 4, 'A0HM', OrderId("summonphoenix"), 0, 0, 0)
    // A114
    call ORDAI_Route(57, 4, 'A114', OrderId("firebolt"), 4, 0, 0)
    call ORDAI_Filter(57, 'A912')
    // A19R
    call ORDAI_Route(58, 4, 'A19R', OrderId("spiritwolf"), 0, 0, 0)
    // A04L
    call ORDAI_Route(59, 4, 'A04L', OrderId("spiritwolf"), 0, 0, 0)
    // A0TE
    call ORDAI_Route(60, 4, 'A0TE', OrderId("spiritwolf"), 0, 0, 0)
    // A130
    call ORDAI_Route(61, 4, 'A130', OrderId("spiritwolf"), 0, 0, 0)
    // A131
    call ORDAI_Route(62, 4, 'A131', OrderId("silence"), 2, 700, 0)
    // A1AQ
    call ORDAI_Route(63, 4, 'A1AQ', OrderId("spiritwolf"), 0, 0, 0)
    // A0ZE
    call ORDAI_Route(64, 4, 'A0ZE', OrderId("spiritwolf"), 0, 0, 0)
    // A0SA
    call ORDAI_Route(65, 4, 'A0SA', OrderId("spiritwolf"), 0, 0, 0)
    // A1AV
    call ORDAI_Route(66, 4, 'A1AV', OrderId("spiritwolf"), 0, 0, 0)
    // A1B0
    call ORDAI_Route(67, 4, 'A1B0', OrderId("spiritwolf"), 0, 0, 0)
    // A04X
    call ORDAI_Route(68, 4, 'A04X', OrderId("spiritwolf"), 0, 0, 0)
    // AI03
    call ORDAI_Route(69, 4, 'AI03', OrderId("incineratearrowon"), 3, 0, 0)
    // A0Q8
    call ORDAI_Route(70, 4, 'A0Q8', OrderId("spiritwolf"), 0, 0, 0)
    // A0SO
    call ORDAI_Route(71, 4, 'A0SO', OrderId("spiritwolf"), 0, 0, 0)
    // A0OT
    call ORDAI_Route(72, 4, 'A0OT', OrderId("spiritwolf"), 0, 0, 0)
    // A12S
    call ORDAI_Route(73, 4, 'A12S', OrderId("summonphoenix"), 0, 0, 0)
    // A12N
    call ORDAI_Route(74, 4, 'A12N', OrderId("chainlightning"), 2, 700, 0)
    // A12R
    call ORDAI_Route(75, 4, 'A12R', OrderId("charm"), 2, 700, 0)
    // A12O
    call ORDAI_Route(76, 4, 'A12O', OrderId("chemicalrage"), 2, 700, 0)
    // A12Q
    call ORDAI_Route(77, 4, 'A12Q', OrderId("cloudoffog"), 2, 700, 0)
    // A04E
    call ORDAI_Route(78, 4, 'A04E', OrderId("spiritwolf"), 0, 0, 0)
    // A0OO
    call ORDAI_Route(79, 4, 'A0OO', OrderId("spiritwolf"), 0, 0, 0)
    // A0AZ
    call ORDAI_Route(80, 4, 'A0AZ', OrderId("spiritwolf"), 0, 0, 0)
    // A0G7
    call ORDAI_Route(81, 4, 'A0G7', OrderId("stomp"), 0, 0, 0)
    // A12D
    call ORDAI_Route(82, 4, 'A12D', OrderId("spiritwolf"), 0, 0, 0)
    // A0VO
    call ORDAI_Route(83, 4, 'A0VO', OrderId("spiritwolf"), 0, 0, 0)
    // A201
    call ORDAI_Route(84, 4, 'A201', OrderId("thunderbolt"), 1, 1400, 0)
    // A0S2
    call ORDAI_Route(85, 4, 'A0S2', OrderId("spiritwolf"), 0, 0, 0)
    // A13N
    call ORDAI_Route(86, 4, 'A13N', OrderId("spiritwolf"), 0, 0, 0)
    // A0RS
    call ORDAI_Route(87, 4, 'A0RS', OrderId("spiritwolf"), 0, 0, 0)
    // A0SU
    call ORDAI_Route(88, 4, 'A0SU', OrderId("spiritwolf"), 0, 0, 0)
    // A0LO
    call ORDAI_Route(89, 4, 'A0LO', OrderId("spiritwolf"), 0, 0, 0)
    // A0DX
    call ORDAI_Route(90, 4, 'A0DX', OrderId("spiritwolf"), 0, 0, 0)
    // A0FO
    call ORDAI_Route(91, 4, 'A0FO', OrderId("berserk"), 0, 0, 0)
    // A10T
    call ORDAI_Route(92, 4, 'A10T', OrderId("spiritwolf"), 0, 0, 0)
    // A0CK
    call ORDAI_Route(93, 4, 'A0CK', OrderId("spiritwolf"), 0, 0, 0)
    // A0MI
    call ORDAI_Route(94, 4, 'A0MI', OrderId("spiritwolf"), 0, 0, 0)
    // A11X
    call ORDAI_Route(95, 4, 'A11X', OrderId("spiritwolf"), 0, 0, 0)
    // A0KL
    call ORDAI_Route(96, 4, 'A0KL', OrderId("spiritwolf"), 0, 0, 0)
    // A129
    call ORDAI_Route(97, 4, 'A129', OrderId("spiritwolf"), 0, 0, 0)
    // A0PE
    call ORDAI_Route(98, 4, 'A0PE', OrderId("roar"), 0, 0, 0)
    // A0PF
    call ORDAI_Route(99, 4, 'A0PF', OrderId("spiritwolf"), 0, 0, 0)
    // A11Q
    call ORDAI_Route(100, 4, 'A11Q', OrderId("spiritwolf"), 0, 0, 0)
    // A186
    call ORDAI_Route(101, 4, 'A186', OrderId("spiritwolf"), 0, 0, 0)
    // A12I
    call ORDAI_Route(102, 4, 'A12I', OrderId("spiritwolf"), 0, 0, 0)
    // A12Z
    call ORDAI_Route(103, 4, 'A12Z', OrderId("spiritwolf"), 0, 0, 0)
    // A11Y
    call ORDAI_Route(104, 4, 'A11Y', OrderId("spiritwolf"), 0, 0, 0)
    // A0VH
    call ORDAI_Route(105, 4, 'A0VH', OrderId("spiritwolf"), 0, 0, 0)
    // A1CW
    call ORDAI_Route(106, 4, 'A1CW', OrderId("spiritwolf"), 0, 0, 0)
    // A14Q
    call ORDAI_Route(107, 4, 'A14Q', OrderId("antimagicshell"), 0, 0, 0)
    // A14U
    call ORDAI_Route(108, 4, 'A14U', OrderId("spiritwolf"), 0, 0, 0)
    // A14R
    call ORDAI_Route(109, 4, 'A14R', OrderId("berserk"), 0, 0, 0)
    // A193
    call ORDAI_Route(110, 4, 'A193', OrderId("spiritwolf"), 0, 0, 0)
    // A0RN
    call ORDAI_Route(111, 4, 'A0RN', OrderId("spiritwolf"), 0, 0, 0)
    // A126
    call ORDAI_Route(112, 4, 'A126', OrderId("spiritwolf"), 0, 0, 0)
    // A0N2
    call ORDAI_Route(113, 4, 'A0N2', OrderId("fanofknives"), 0, 0, 0)
    // A0BS
    call ORDAI_Route(114, 4, 'A0BS', OrderId("thunderbolt"), 1, 625, 0)
    // A0BT
    call ORDAI_Route(115, 4, 'A0BT', OrderId("spiritwolf"), 0, 0, 0)
    // A0LD
    call ORDAI_Route(116, 4, 'A0LD', OrderId("spiritwolf"), 0, 0, 0)
    // A0LF
    call ORDAI_Route(117, 4, 'A0LF', OrderId("healingwave"), 1, 1600, 0)
    // A12M
    call ORDAI_Route(118, 4, 'A12M', OrderId("dispel"), 0, 0, 0)
    // A0AF
    call ORDAI_Route(119, 4, 'A0AF', OrderId("spiritwolf"), 0, 0, 0)
    // A19C
    call ORDAI_Route(120, 4, 'A19C', OrderId("spiritwolf"), 0, 0, 0)
    // A14M
    call ORDAI_Route(121, 4, 'A14M', OrderId("spiritwolf"), 0, 0, 0)
    // A1BD
    call ORDAI_Route(122, 4, 'A1BD', OrderId("berserk"), 0, 0, 0)
    // A1BK
    call ORDAI_Route(123, 4, 'A1BK', OrderId("spiritwolf"), 0, 0, 0)
    // A11H
    call ORDAI_Route(124, 4, 'A11H', OrderId("firebolt"), 1, 1000, 0)
    // A11G
    call ORDAI_Route(125, 4, 'A11G', OrderId("spiritwolf"), 0, 0, 0)
    // A202
    call ORDAI_Route(126, 4, 'A202', OrderId("thunderbolt"), 1, 1400, 0)
    // A11E
    call ORDAI_Route(127, 4, 'A11E', OrderId("spiritwolf"), 0, 0, 0)
    // A11O
    call ORDAI_Route(128, 4, 'A11O', OrderId("spiritwolf"), 0, 0, 0)
    // A0IR
    call ORDAI_Route(129, 4, 'A0IR', OrderId("spiritwolf"), 0, 0, 0)
    // A0SF
    call ORDAI_Route(130, 4, 'A0SF', OrderId("spiritwolf"), 0, 0, 0)
    // A0SM
    call ORDAI_Route(131, 4, 'A0SM', OrderId("spiritwolf"), 0, 0, 0)
    // A0WS
    call ORDAI_Route(132, 4, 'A0WS', OrderId("spiritwolf"), 0, 0, 0)
    // A0YS
    call ORDAI_Route(133, 4, 'A0YS', OrderId("fingerofdeath"), 3, 0, 0)
    call ORDAI_Filter(133, 'h060')
    // A0T3
    call ORDAI_Route(134, 4, 'A0T3', OrderId("spiritwolf"), 0, 0, 0)
    // A0V0
    call ORDAI_Route(135, 4, 'A0V0', OrderId("spiritwolf"), 0, 0, 0)
    // A11C
    call ORDAI_Route(136, 4, 'A11C', OrderId("spiritwolf"), 0, 0, 0)
    // A0P0
    call ORDAI_Route(137, 4, 'A0P0', OrderId("spiritwolf"), 0, 0, 0)
    // A116
    call ORDAI_Route(138, 4, 'A116', OrderId("spiritwolf"), 0, 0, 0)
    // A0WM
    call ORDAI_Route(139, 4, 'A0WM', 0, 3, 0, 11)
    call ORDAI_Item(139, 'I093')
    call ORDAI_Item(139, 'I092')
    call ORDAI_Item(139, 'I091')
    // A203
    call ORDAI_Route(140, 4, 'A203', OrderId("spiritwolf"), 0, 0, 0)
    // A204
    call ORDAI_Route(141, 4, 'A204', OrderId("roar"), 0, 0, 0)
    // A205
    call ORDAI_Route(142, 4, 'A205', OrderId("howlofterror"), 0, 0, 0)
    // A206
    call ORDAI_Route(143, 4, 'A206', OrderId("taunt"), 0, 0, 0)
    // A0DI
    call ORDAI_Route(144, 4, 'A0DI', OrderId("firebolt"), 4, 0, 0)
    call ORDAI_Filter(144, 'h060')
    call ORDAI_Filter(144, 'h03P')
    call ORDAI_Filter(144, 'h03Q')
    call ORDAI_Filter(144, 'h03X')
    call ORDAI_Filter(144, 'h03L')
    call ORDAI_Filter(144, 'h03U')
    // A0LQ
    call ORDAI_Route(145, 4, 'A0LQ', OrderId("thunderbolt"), 3, 250, 0)
    // A12T
    call ORDAI_Route(146, 4, 'A12T', OrderId("firebolt"), 3, 0, 0)
    call ORDAI_Filter(146, 'A0BA')
    call ORDAI_Filter(146, 'h01C')
    call ORDAI_Filter(146, 'h015')
    // A0DD
    call ORDAI_Route(147, 4, 'A0DD', OrderId("fanofknives"), 0, 0, 0)
    // A0YN
    call ORDAI_Route(148, 4, 'A0YN', OrderId("clusterrockets"), 2, 1000, 0)
    // A0YR
    call ORDAI_Route(149, 4, 'A0YR', OrderId("thunderbolt"), 1, 0, 0)
    // A0YO
    call ORDAI_Route(150, 4, 'A0YO', OrderId("channel"), 0, 0, 0)
    // A0YP
    call ORDAI_Route(151, 4, 'A0YP', OrderId("channel"), 0, 0, 0)
    // A0T5
    call ORDAI_Route(152, 4, 'A0T5', OrderId("thunderclap"), 0, 0, 0)
    // A0WZ
    call ORDAI_Route(153, 4, 'A0WZ', OrderId("channel"), 0, 0, 0)
    // A10P
    call ORDAI_Route(154, 4, 'A10P', OrderId("summonphoenix"), 0, 0, 0)
    // A10O
    call ORDAI_Route(155, 4, 'A10O', OrderId("channel"), 0, 0, 0)
    // A0XC
    call ORDAI_Route(156, 4, 'A0XC', OrderId("unholyfrenzy"), 3, 500, 0)
    call ORDAI_Filter(156, 'h01N')
    call ORDAI_Filter(156, 'h00D')
    // A0QN
    call ORDAI_Route(157, 4, 'A0QN', OrderId("unholyfrenzy"), 3, 800, 0)
    // A0KB
    call ORDAI_Route(158, 4, 'A0KB', OrderId("dispel"), 0, 0, 0)
    // A0KC
    call ORDAI_Route(159, 4, 'A0KC', OrderId("drain"), 0, 0, 0)
    // A081
    call ORDAI_Route(160, 4, 'A081', OrderId("thunderbolt"), 1, 0, 0)
    // A0J2
    call ORDAI_Route(161, 4, 'A0J2', OrderId("repairon"), 3, 0, 1)
    call ORDAI_Filter(161, 'A0BA')
    call ORDAI_Filter(161, 'A0B9')
    call ORDAI_Filter(161, 'h01C')
    call ORDAI_Filter(161, 'h015')
    // A0BZ
    call ORDAI_Route(162, 4, 'A0BZ', OrderId("clusterrockets"), 2, 0, 0)
    // A082
    call ORDAI_Route(163, 4, 'A082', OrderId("thunderclap"), 0, 0, 0)
    // A07T
    call ORDAI_Route(164, 4, 'A07T', OrderId("earthquake"), 2, 0, 0)
    // A0UY
    call ORDAI_Route(165, 4, 'A0UY', OrderId("spiritwolf"), 0, 0, 0)
    // A05D
    call ORDAI_Route(166, 4, 'A05D', OrderId("detectaoe"), 5, 0, 0)
    // A0BY
    call ORDAI_Route(167, 4, 'A0BY', OrderId("magicleash"), 1, 0, 0)
    // A07U
    call ORDAI_Route(168, 4, 'A07U', OrderId("silence"), 2, 0, 0)
    // A0VD
    call ORDAI_Route(169, 4, 'A0VD', OrderId("thunderclap"), 0, 0, 0)
    // A07W
    call ORDAI_Route(170, 4, 'A07W', OrderId("firebolt"), 1, 0, 0)
    // A0KP
    call ORDAI_Route(171, 4, 'A0KP', OrderId("setrally"), 0, 0, 0)
    // A0NV
    call ORDAI_Route(172, 4, 'A0NV', OrderId("shadowsight"), 0, 0, 0)
    // A0NW
    call ORDAI_Route(173, 4, 'A0NW', OrderId("shadowstrike"), 0, 0, 0)
    // AI11
    call ORDAI_Route(174, 4, 'AI11', OrderId("militia"), 0, 0, 0)
    // AI12
    call ORDAI_Route(175, 4, 'AI12', OrderId("monsoon"), 0, 0, 0)
    // AI16
    call ORDAI_Route(176, 4, 'AI16', OrderId("absorb"), 0, 0, 0)
    // AI17
    call ORDAI_Route(177, 4, 'AI17', OrderId("acolyteharvest"), 0, 0, 0)
    // AI21
    call ORDAI_Route(178, 4, 'AI21', OrderId("ambush"), 0, 0, 0)
    // AG00
    call ORDAI_Route(179, 4, 'AG00', OrderId("tranquility"), 0, 0, 0)
    // AG01
    call ORDAI_Route(180, 4, 'AG01', OrderId("tranquility"), 0, 0, 0)
    // AG02
    call ORDAI_Route(181, 4, 'AG02', OrderId("tranquility"), 0, 0, 0)
    // AG03
    call ORDAI_Route(182, 4, 'AG03', OrderId("tranquility"), 0, 0, 0)
    // AG04
    call ORDAI_Route(183, 4, 'AG04', OrderId("tranquility"), 0, 0, 0)
    // AG05
    call ORDAI_Route(184, 4, 'AG05', OrderId("tranquility"), 0, 0, 0)
    // AG06
    call ORDAI_Route(185, 4, 'AG06', OrderId("tranquility"), 0, 0, 0)
    // AG07
    call ORDAI_Route(186, 4, 'AG07', OrderId("tranquility"), 0, 0, 0)
    // AG08
    call ORDAI_Route(187, 4, 'AG08', OrderId("tranquility"), 0, 0, 0)
    // AG09
    call ORDAI_Route(188, 4, 'AG09', OrderId("tranquility"), 0, 0, 0)
    // AG10
    call ORDAI_Route(189, 4, 'AG10', OrderId("tranquility"), 0, 0, 0)
    // AG20
    call ORDAI_Route(190, 4, 'AG20', OrderId("mirrorimage"), 0, 0, 0)
    // AG21
    call ORDAI_Route(191, 4, 'AG21', OrderId("mirrorimage"), 0, 0, 0)
    // AG22
    call ORDAI_Route(192, 4, 'AG22', OrderId("mirrorimage"), 0, 0, 0)
    // AG23
    call ORDAI_Route(193, 4, 'AG23', OrderId("mirrorimage"), 0, 0, 0)
    // AG24
    call ORDAI_Route(194, 4, 'AG24', OrderId("mirrorimage"), 0, 0, 0)
    // AG25
    call ORDAI_Route(195, 4, 'AG25', OrderId("mirrorimage"), 0, 0, 0)
    // AG26
    call ORDAI_Route(196, 4, 'AG26', OrderId("mirrorimage"), 0, 0, 0)
    // AG27
    call ORDAI_Route(197, 4, 'AG27', OrderId("mirrorimage"), 0, 0, 0)
    // AG28
    call ORDAI_Route(198, 4, 'AG28', OrderId("mirrorimage"), 0, 0, 0)
    // AG29
    call ORDAI_Route(199, 4, 'AG29', OrderId("mirrorimage"), 0, 0, 0)
    // AG30
    call ORDAI_Route(200, 4, 'AG30', OrderId("mirrorimage"), 0, 0, 0)
    // A0CI
    call ORDAI_Route(201, 4, 'A0CI', OrderId("antimagicshell"), 0, 0, 0)
    // A0EZ
    call ORDAI_Route(202, 4, 'A0EZ', OrderId("charm"), 0, 0, 0)
    // A0DB
    call ORDAI_Route(203, 4, 'A0DB', OrderId("banish"), 0, 0, 0)
    // A0IY
    call ORDAI_Route(204, 4, 'A0IY', OrderId("chemicalrage"), 0, 0, 0)
    // A0JR
    call ORDAI_Route(205, 4, 'A0JR', OrderId("cloudoffog"), 0, 0, 0)
    // A0BV
    call ORDAI_Route(206, 4, 'A0BV', OrderId("cannibalize"), 0, 0, 0)
    // A0MA
    call ORDAI_Route(207, 4, 'A0MA', OrderId("cyclone"), 0, 0, 0)
    // A700
    call ORDAI_Route(208, 4, 'A700', OrderId("renew"), 0, 0, 0)
    // A13A
    call ORDAI_Route(209, 4, 'A13A', OrderId("thunderbolt"), 3, 0, 0)
    call ORDAI_Filter(209, 'h043')
    call ORDAI_Filter(209, 'h032')
    call ORDAI_Filter(209, 'h030')
    call ORDAI_Filter(209, 'h03G')
    // A09C
    call ORDAI_Route(210, 4, 'A09C', OrderId("incineratearrowon"), 4, 0, 0)
    call ORDAI_Filter(210, 'A0BA')
    call ORDAI_Filter(210, 'h01C')
    call ORDAI_Filter(210, 'h015')
    // A0KA
    call ORDAI_Route(211, 4, 'A0KA', OrderId("thunderclap"), 0, 0, 0)
    // A14A
    call ORDAI_Route(212, 4, 'A14A', OrderId("absorb"), 0, 0, 0)
    // A14B
    call ORDAI_Route(213, 4, 'A14B', OrderId("ambush"), 0, 0, 0)
    // A14C
    call ORDAI_Route(214, 4, 'A14C', OrderId("ancestralspirit"), 0, 0, 0)
    // A14D
    call ORDAI_Route(215, 4, 'A14D', OrderId("acidbomb"), 0, 0, 0)
    // A14E
    call ORDAI_Route(216, 4, 'A14E', OrderId("antimagicshell"), 0, 0, 0)
    // A14F
    call ORDAI_Route(217, 4, 'A14F', OrderId("avatar"), 0, 0, 0)
    // A14G
    call ORDAI_Route(218, 4, 'A14G', OrderId("charm"), 0, 0, 0)
    // A14H
    call ORDAI_Route(219, 4, 'A14H', OrderId("acolyteharvest"), 0, 0, 0)
    // A14I
    call ORDAI_Route(220, 4, 'A14I', OrderId("chainlightning"), 0, 0, 0)
    // A14J
    call ORDAI_Route(221, 4, 'A14J', OrderId("animatedead"), 0, 0, 0)
    // A09G
    call ORDAI_Route(222, 4, 'A09G', OrderId("summonphoenix"), 0, 0, 0)
    // A0B8
    call ORDAI_Route(223, 4, 'A0B8', OrderId("summonphoenix"), 0, 0, 0)
    // A0BA
    call ORDAI_Route(224, 4, 'A0BA', OrderId("summonphoenix"), 0, 0, 0)
    // A0B9
    call ORDAI_Route(225, 4, 'A0B9', OrderId("summonphoenix"), 0, 0, 0)
    // A080
    call ORDAI_Route(226, 4, 'A080', OrderId("summonphoenix"), 0, 0, 0)
    // A0BB
    call ORDAI_Route(227, 4, 'A0BB', OrderId("summonphoenix"), 0, 0, 0)
    // A0OE
    call ORDAI_Route(228, 4, 'A0OE', OrderId("summonphoenix"), 0, 0, 0)
    // A17T
    call ORDAI_Route(229, 4, 'A17T', OrderId("summonphoenix"), 0, 0, 0)
    // A0KQ
    call ORDAI_Route(230, 4, 'A0KQ', OrderId("summonphoenix"), 0, 0, 0)
    // A07Y
    call ORDAI_Route(231, 4, 'A07Y', OrderId("absorb"), 0, 0, 0)
    // A0TJ
    call ORDAI_Route(232, 4, 'A0TJ', OrderId("ambush"), 0, 0, 0)
    // A0K9
    call ORDAI_Route(233, 4, 'A0K9', OrderId("thunderclap"), 0, 0, 0)
    // A0JE
    call ORDAI_Route(234, 4, 'A0JE', OrderId("antimagicshell"), 0, 0, 0)
    // A0YE
    call ORDAI_Route(235, 4, 'A0YE', OrderId("summongrizzly"), 0, 0, 0)
    // A0YZ
    call ORDAI_Route(236, 4, 'A0YZ', OrderId("summongrizzly"), 0, 0, 0)
    // A0X0
    call ORDAI_Route(237, 4, 'A0X0', OrderId("firebolt"), 3, 0, 0)
    // A038
    call ORDAI_Route(238, 4, 'A038', OrderId("blink"), 2, 0, 0)
    // A03A
    call ORDAI_Route(239, 4, 'A03A', OrderId("selfdestruct"), 0, 0, 0)
    // A03I
    call ORDAI_Route(240, 4, 'A03I', OrderId("innerfire"), 3, 500, 4)
    // A03V
    call ORDAI_Route(241, 4, 'A03V', OrderId("roar"), 0, 0, 4)
    // A04W
    call ORDAI_Route(242, 4, 'A04W', OrderId("blink"), 2, 0, 0)
    // A053
    call ORDAI_Route(243, 4, 'A053', OrderId("blink"), 2, 0, 0)
    // A05A
    call ORDAI_Route(244, 4, 'A05A', OrderId("unholyfrenzy"), 3, 750, 4)
    // A05C
    call ORDAI_Route(245, 4, 'A05C', OrderId("blink"), 2, 0, 0)
    // A05P
    call ORDAI_Route(246, 4, 'A05P', OrderId("unholyfrenzy"), 3, 750, 4)
    // A05Q
    call ORDAI_Route(247, 4, 'A05Q', OrderId("blink"), 2, 0, 0)
    // A07D
    call ORDAI_Route(248, 4, 'A07D', OrderId("selfdestruct"), 0, 0, 0)
    // A07V
    call ORDAI_Route(249, 4, 'A07V', OrderId("blizzard"), 2, 0, 0)
    // A07X
    call ORDAI_Route(250, 4, 'A07X', OrderId("acidbomb"), 1, 0, 0)
    // A07Z
    call ORDAI_Route(251, 4, 'A07Z', OrderId("roar"), 0, 0, 4)
    // A087
    call ORDAI_Route(252, 4, 'A087', OrderId("blackarrow"), 1, 2000, 0)
    // A08B
    call ORDAI_Route(253, 4, 'A08B', OrderId("unholyfrenzy"), 3, 750, 4)
    // A08G
    call ORDAI_Route(254, 4, 'A08G', OrderId("bloodlust"), 3, 9999, 4)
    // A08Y
    call ORDAI_Route(255, 4, 'A08Y', OrderId("cripple"), 3, 0, 0)
    // A098
    call ORDAI_Route(256, 4, 'A098', OrderId("blink"), 2, 0, 0)
    // A0BM
    call ORDAI_Route(257, 4, 'A0BM', OrderId("metamorphosis"), 0, 0, 0)
    // A0BO
    call ORDAI_Route(258, 4, 'A0BO', OrderId("unholyfrenzy"), 3, 750, 4)
    // A0CR
    call ORDAI_Route(259, 4, 'A0CR', OrderId("unholyfrenzy"), 3, 750, 4)
    // A0DP
    call ORDAI_Route(260, 4, 'A0DP', OrderId("innerfire"), 3, 500, 4)
    // A0DQ
    call ORDAI_Route(261, 4, 'A0DQ', OrderId("rejuvination"), 3, 900, 4)
    // A0GP
    call ORDAI_Route(262, 4, 'A0GP', OrderId("blink"), 2, 0, 0)
    // A0ID
    call ORDAI_Route(263, 4, 'A0ID', OrderId("blink"), 2, 0, 0)
    // A0IX
    call ORDAI_Route(264, 4, 'A0IX', OrderId("blink"), 2, 0, 0)
    // A0LS
    call ORDAI_Route(265, 4, 'A0LS', OrderId("roar"), 0, 0, 4)
    // A0MF
    call ORDAI_Route(266, 4, 'A0MF', OrderId("blink"), 2, 0, 0)
    // A0NT
    call ORDAI_Route(267, 4, 'A0NT', OrderId("blink"), 2, 0, 0)
    // A0OC
    call ORDAI_Route(268, 4, 'A0OC', OrderId("metamorphosis"), 0, 0, 0)
    // A0OG
    call ORDAI_Route(269, 4, 'A0OG', OrderId("metamorphosis"), 0, 0, 0)
    // A0ON
    call ORDAI_Route(270, 4, 'A0ON', OrderId("metamorphosis"), 0, 0, 0)
    // A0OY
    call ORDAI_Route(271, 4, 'A0OY', OrderId("metamorphosis"), 0, 0, 0)
    // A0PS
    call ORDAI_Route(272, 4, 'A0PS', OrderId("metamorphosis"), 0, 0, 0)
    // A0QQ
    call ORDAI_Route(273, 4, 'A0QQ', OrderId("blink"), 2, 0, 0)
    // A0RB
    call ORDAI_Route(274, 4, 'A0RB', OrderId("blink"), 2, 0, 0)
    // A0UT
    call ORDAI_Route(275, 4, 'A0UT', OrderId("blink"), 2, 0, 0)
    // A0VT
    call ORDAI_Route(276, 4, 'A0VT', OrderId("blink"), 2, 0, 0)
    // A0W7
    call ORDAI_Route(277, 4, 'A0W7', OrderId("blink"), 2, 0, 0)
    // A0W9
    call ORDAI_Route(278, 4, 'A0W9', OrderId("blink"), 2, 0, 0)
    // A0WF
    call ORDAI_Route(279, 4, 'A0WF', OrderId("blink"), 2, 1500, 0)
    // A0XH
    call ORDAI_Route(280, 4, 'A0XH', OrderId("metamorphosis"), 0, 0, 0)
    // A0YH
    call ORDAI_Route(281, 4, 'A0YH', OrderId("blink"), 2, 0, 0)
    // A0YW
    call ORDAI_Route(282, 4, 'A0YW', OrderId("howlofterror"), 0, 0, 4)
    // A0Z8
    call ORDAI_Route(283, 4, 'A0Z8', OrderId("blink"), 2, 0, 0)
    // A0ZS
    call ORDAI_Route(284, 4, 'A0ZS', OrderId("metamorphosis"), 0, 0, 0)
    // A12B
    call ORDAI_Route(285, 4, 'A12B', OrderId("metamorphosis"), 0, 0, 0)
    // A14L
    call ORDAI_Route(286, 4, 'A14L', OrderId("metamorphosis"), 0, 0, 0)
    // A15F
    call ORDAI_Route(287, 4, 'A15F', OrderId("metamorphosis"), 0, 0, 0)
    // A172
    call ORDAI_Route(288, 4, 'A172', OrderId("metamorphosis"), 0, 0, 0)
    // A188
    call ORDAI_Route(289, 4, 'A188', OrderId("metamorphosis"), 0, 0, 0)
    // A1CE
    call ORDAI_Route(290, 4, 'A1CE', OrderId("metamorphosis"), 0, 0, 0)
    // A1CQ
    call ORDAI_Route(291, 4, 'A1CQ', OrderId("blink"), 2, 0, 0)
    // sync
    call ORDAI_Route(292, 5, 0, 1, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 292, "NAVI_1IDX")
    // sync
    call ORDAI_Route(293, 5, 0, 2, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 293, "NAVI_1IDX")
    // sync
    call ORDAI_Route(294, 5, 0, 3, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 294, "NAVI_1IDX")
    // sync
    call ORDAI_Route(295, 5, 0, 1, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 295, "NAVI_2IDX")
    // sync
    call ORDAI_Route(296, 5, 0, 2, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 296, "NAVI_2IDX")
    // sync
    call ORDAI_Route(297, 5, 0, 3, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 297, "NAVI_2IDX")
    // sync
    call ORDAI_Route(298, 5, 0, 1, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 298, "NAVI_3IDX")
    // sync
    call ORDAI_Route(299, 5, 0, 2, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 299, "NAVI_3IDX")
    // sync
    call ORDAI_Route(300, 5, 0, 3, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 300, "NAVI_3IDX")
    // sync
    call ORDAI_Route(301, 5, 0, 1, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 301, "NAVI_4IDX")
    // sync
    call ORDAI_Route(302, 5, 0, 2, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 302, "NAVI_4IDX")
    // sync
    call ORDAI_Route(303, 5, 0, 3, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 303, "NAVI_4IDX")
    // sync
    call ORDAI_Route(304, 5, 0, 1, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 304, "NAVI_5IDX")
    // sync
    call ORDAI_Route(305, 5, 0, 2, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 305, "NAVI_5IDX")
    // sync
    call ORDAI_Route(306, 5, 0, 3, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 306, "NAVI_5IDX")
    // sync
    call ORDAI_Route(307, 5, 0, 0, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 307, "QUST_INDX")
    // sync
    call ORDAI_Route(308, 5, 0, 1, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 308, "QUST_INDX")
    // sync
    call ORDAI_Route(309, 5, 0, 2, 0, 0, 0)
    call SaveStr(ORDAI_data, 10, 309, "QUST_INDX")
    // region
    call ORDAI_Route(310, 6, 0, 0, 0, 0, 0)
    // region
    call ORDAI_Route(311, 6, 0, 0, 0, 0, 0)
    // region
    call ORDAI_Route(312, 6, 0, 0, 0, 0, 0)
    // e018
    call ORDAI_Route(313, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(314, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(315, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(316, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(317, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(318, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(319, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(320, 6, 'e018', 0, 0, 0, 0)
    // e018
    call ORDAI_Route(321, 6, 'e018', 0, 0, 0, 0)
    // e015
    call ORDAI_Route(322, 6, 'e015', 0, 0, 0, 0)
    // e016
    call ORDAI_Route(323, 6, 'e016', 0, 0, 0, 0)
    // e017
    call ORDAI_Route(324, 6, 'e017', 0, 0, 0, 0)
    // e019
    call ORDAI_Route(325, 6, 'e019', 0, 0, 0, 0)
    // e01A
    call ORDAI_Route(326, 6, 'e01A', 0, 0, 0, 0)
    // e01A
    call ORDAI_Route(327, 6, 'e01A', 0, 0, 0, 0)
    // e01A
    call ORDAI_Route(328, 6, 'e01A', 0, 0, 0, 0)
    // e0FJ
    call ORDAI_Route(329, 7, 'e0FJ', 0, 0, 0, 0)
    // chat
    call ORDAI_Route(330, 8, 0, 0, 0, 0, 0)
    call SaveStr(ORDAI_data, 11, 330, "QVs")
    // attack
    call ORDAI_Route(331, 9, 0, 1, 0, 0, 0)
    // attack
    call ORDAI_Route(332, 9, 0, 2, 0, 0, 0)
    // attack
    call ORDAI_Route(333, 9, 0, 3, 0, 0, 0)
    // attack
    call ORDAI_Route(334, 9, 0, 4, 0, 0, 0)
    // attack
    call ORDAI_Route(335, 9, 0, 5, 0, 0, 0)
    // attack
    call ORDAI_Route(336, 9, 0, 6, 0, 0, 0)
    // A05D
    call ORDAI_Route(574, 10, 'A05D', OrderId("detectaoe"), 0, 0, 0)
    // A05D
    call ORDAI_Route(575, 10, 'A05D', OrderId("detectaoe"), 1, 0, 0)
    // A05D
    call ORDAI_Route(576, 10, 'A05D', OrderId("detectaoe"), 2, 0, 0)
    // A05D
    call ORDAI_Route(577, 10, 'A05D', OrderId("detectaoe"), 3, 0, 0)
    // A05D
    call ORDAI_Route(578, 10, 'A05D', OrderId("detectaoe"), 4, 0, 0)
    // A05D
    call ORDAI_Route(579, 10, 'A05D', OrderId("detectaoe"), 5, 0, 0)
    // A05D
    call ORDAI_Route(580, 10, 'A05D', OrderId("detectaoe"), 6, 0, 0)
    // A05D
    call ORDAI_Route(581, 10, 'A05D', OrderId("detectaoe"), 7, 0, 0)
    // A05D
    call ORDAI_Route(582, 10, 'A05D', OrderId("detectaoe"), 8, 0, 0)
    // A05D
    call ORDAI_Route(583, 10, 'A05D', OrderId("detectaoe"), 9, 0, 0)
    // A05D
    call ORDAI_Route(584, 10, 'A05D', OrderId("detectaoe"), 10, 0, 0)
    // A05D
    call ORDAI_Route(585, 10, 'A05D', OrderId("detectaoe"), 11, 0, 0)
    // A05D
    call ORDAI_Route(586, 10, 'A05D', OrderId("detectaoe"), 12, 0, 0)
    // A05D
    call ORDAI_Route(587, 10, 'A05D', OrderId("detectaoe"), 13, 0, 0)
    // A05D
    call ORDAI_Route(588, 10, 'A05D', OrderId("detectaoe"), 14, 0, 0)
    // A05D
    call ORDAI_Route(589, 10, 'A05D', OrderId("detectaoe"), 15, 0, 0)
endfunction

function ORDAI_Rect takes integer a returns rect
    if a == 310 then
        return OP[0]
    endif
    if a == 311 then
        return Zd
    endif
    if a == 312 then
        return Dd
    endif
    if a == 313 then
        return rY
    endif
    if a == 314 then
        return WY
    endif
    if a == 315 then
        return iY
    endif
    if a == 316 then
        return mY
    endif
    if a == 317 then
        return YY
    endif
    if a == 318 then
        return dY
    endif
    if a == 319 then
        return GY
    endif
    if a == 320 then
        return qY
    endif
    if a == 321 then
        return ZY
    endif
    if a == 322 then
        return hm
    endif
    if a == 323 then
        return SY
    endif
    if a == 324 then
        return XY
    endif
    if a == 325 then
        return TY
    endif
    if a == 326 then
        return MY
    endif
    if a == 327 then
        return gY
    endif
    if a == 328 then
        return uY
    endif
    if a == 329 then
        return Zv[0]
    endif
    return null
endfunction
// END GENERATED INPUT TABLE

function ORDAI_Bit takes integer flags, integer bit returns boolean
    return flags / bit - flags / (bit * 2) * 2 == 1
endfunction

// Sell, train and research orders go to the player's own building of the listed type; the
// engine event (SELL, TRAIN_START or RESEARCH_START in ORDAI_Bought) proves the input.
function ORDAI_Building takes integer a returns nothing
    local integer id = LoadInteger(ORDAI_data, 3, a)
    local integer first = LoadInteger(ORDAI_data, 4, a)
    local integer second = LoadInteger(ORDAI_data, 5, a)
    local boolean done = false
    local unit u
    call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_units)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_units, u)
        if not done and GetWidgetLife(u) > 0.405 and (GetUnitTypeId(u) == first or GetUnitTypeId(u) == second) then
            set ORDAI_expect = id
            set done = IssueImmediateOrderById(u, id)
        endif
    endloop
endfunction

// The first carried item whose type holds the ability (items cast through their slot).
function ORDAI_Carried takes integer a, unit u returns item
    local integer n = LoadInteger(ORDAI_data, 9, a)
    local integer slot = 0
    local integer i
    set ORDAI_carried = null
    loop
        exitwhen slot >= UnitInventorySize(u) or ORDAI_carried != null
        set i = 0
        loop
            exitwhen i >= n or ORDAI_carried != null
            if GetItemTypeId(UnitItemInSlot(u, slot)) == LoadInteger(ORDAI_data, 40 + i, a) then
                set ORDAI_carried = UnitItemInSlot(u, slot)
            endif
            set i = i + 1
        endloop
        set slot = slot + 1
    endloop
    return ORDAI_carried
endfunction

// A caster: a living, shown, unpaused Player(0) unit (buildings included) that has the
// ability off cooldown, or carries its item. Mana, stock and the handler stay with the map.
function ORDAI_Ready takes integer a, unit u returns boolean
    local integer id = LoadInteger(ORDAI_data, 3, a)
    if GetWidgetLife(u) <= 0.405 or IsUnitType(u, UNIT_TYPE_DEAD) or IsUnitHidden(u) or IsUnitPaused(u) or IsUnitInGroup(u, ORDAI_tried) then
        return false
    endif
    if ORDAI_Bit(LoadInteger(ORDAI_data, 7, a), 8) then
        return ORDAI_Carried(a, u) != null
    endif
    return GetUnitAbilityLevel(u, id) > 0 and BlzGetUnitAbilityCooldownRemaining(u, id) <= 0.
endfunction

// Casters are tried from the oldest unit (lowest handle) until one accepts the order;
// ORDAI_Cast stops after a few so a spell with many holders stays within the op limit.
function ORDAI_NextCaster takes integer a returns unit
    local unit u
    set ORDAI_caster = null
    call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_units)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_units, u)
        if ORDAI_Ready(a, u) and (ORDAI_caster == null or GetHandleId(u) < GetHandleId(ORDAI_caster)) then
            set ORDAI_caster = u
        endif
    endloop
    return ORDAI_caster
endfunction

// Handler-derived target filters: any listed unit type or ability; bit 2 needs a common
// (UNIT_TYPE_UNDEAD) unit.
function ORDAI_Fits takes integer a, unit t returns boolean
    local integer n = LoadInteger(ORDAI_data, 8, a)
    local integer i = 0
    local integer value
    if ORDAI_Bit(LoadInteger(ORDAI_data, 7, a), 2) and not IsUnitType(t, UNIT_TYPE_UNDEAD) then
        return false
    endif
    if n == 0 then
        return true
    endif
    loop
        exitwhen i >= n
        set value = LoadInteger(ORDAI_data, 20 + i, a)
        if GetUnitTypeId(t) == value or GetUnitAbilityLevel(t, value) > 0 then
            return true
        endif
        set i = i + 1
    endloop
    return false
endfunction

function ORDAI_Within takes integer a, unit caster, unit t returns boolean
    local real reach = I2R(LoadInteger(ORDAI_data, 6, a))
    local real dx = GetUnitX(t) - GetUnitX(caster)
    local real dy = GetUnitY(t) - GetUnitY(caster)
    return reach <= 0. or dx * dx + dy * dy <= reach * reach
endfunction

// Enemy spells: the nearest living, visible, targetable enemy unit within the cast range.
function ORDAI_Enemy takes integer a, unit caster returns unit
    local unit t
    local unit best = null
    local real dx
    local real dy
    local real d
    local real bestD = 0.
    call GroupEnumUnitsInRange(ORDAI_targets, GetUnitX(caster), GetUnitY(caster), 100000., null)
    loop
        set t = FirstOfGroup(ORDAI_targets)
        exitwhen t == null
        call GroupRemoveUnit(ORDAI_targets, t)
        if GetWidgetLife(t) > 0.405 and not IsUnitType(t, UNIT_TYPE_DEAD) and not IsUnitHidden(t) and IsUnitEnemy(t, Player(0)) and IsUnitVisible(t, Player(0)) and not IsUnitType(t, UNIT_TYPE_STRUCTURE) and GetUnitAbilityLevel(t, 'Avul') == 0 and ORDAI_Within(a, caster, t) then
            set dx = GetUnitX(t) - GetUnitX(caster)
            set dy = GetUnitY(t) - GetUnitY(caster)
            set d = dx * dx + dy * dy
            if best == null or d < bestD or (d == bestD and GetHandleId(t) < GetHandleId(best)) then
                set best = t
                set bestD = d
            endif
        endif
    endloop
    set t = best
    set best = null
    return t
endfunction

// Own-unit spells: the player's strongest matching unit (highest point value, then base
// damage, then oldest), or the weakest for spells that consume the target (bit 1). The
// caster itself only when Targets Allowed has "self" (bit 4). Recipe-board targets are
// Player(7) units.
function ORDAI_Friend takes integer a, unit caster, boolean display returns unit
    local integer flags = LoadInteger(ORDAI_data, 7, a)
    local player owner = Player(0)
    local unit t
    local unit best = null
    local integer rank
    local integer bestRank = 0
    local integer damage
    local integer bestDamage = 0
    if display then
        set owner = Player(7)
    endif
    call GroupEnumUnitsOfPlayer(ORDAI_targets, owner, null)
    loop
        set t = FirstOfGroup(ORDAI_targets)
        exitwhen t == null
        call GroupRemoveUnit(ORDAI_targets, t)
        if (t != caster or ORDAI_Bit(flags, 4)) and GetWidgetLife(t) > 0.405 and not IsUnitType(t, UNIT_TYPE_DEAD) and not IsUnitHidden(t) and not IsUnitType(t, UNIT_TYPE_STRUCTURE) and GetUnitAbilityLevel(t, 'Aloc') == 0 and ORDAI_Within(a, caster, t) and ORDAI_Fits(a, t) then
            set rank = GetUnitPointValue(t)
            if ORDAI_Bit(flags, 1) then
                set rank = -rank
            endif
            set damage = BlzGetUnitBaseDamage(t, 0)
            if best == null or rank > bestRank or (rank == bestRank and damage > bestDamage) or (rank == bestRank and damage == bestDamage and GetHandleId(t) < GetHandleId(best)) then
                set best = t
                set bestRank = rank
                set bestDamage = damage
            endif
        endif
    endloop
    set t = best
    set best = null
    set owner = null
    return t
endfunction

// Target classes (inputs.Target): 0 none, 1 enemy unit, 2 enemy position, 3 own unit,
// 4 recipe-board unit, 5 caster position.
// Human Reveal (AHta, the base of A05D 탐색) has no order string: Warcraft III issues it as
// 852270, and every "detectaoe" (852015) cast was refused in the real game (2026-09-30,
// runs/real-ppo5-easy). The simulator issues AHta as "detectaoe", so that stays the fallback.
function ORDAI_PointOrder takes unit u, integer raw, integer order, real x, real y returns boolean
    set ORDAI_pointOrder = 0
    if raw == 'A05D' and IssuePointOrderById(u, 852270, x, y) then
        set ORDAI_pointOrder = 852270
    elseif IssuePointOrderById(u, order, x, y) then
        set ORDAI_pointOrder = order
    endif
    return ORDAI_pointOrder != 0
endfunction

function ORDAI_Issue takes integer a, unit u returns boolean
    local integer order = LoadInteger(ORDAI_data, 4, a)
    local integer target = LoadInteger(ORDAI_data, 5, a)
    local unit t = null
    local boolean issued = false
    if target == 1 or target == 2 then
        set t = ORDAI_Enemy(a, u)
    elseif target == 3 then
        set t = ORDAI_Friend(a, u, false)
    elseif target == 4 then
        set t = ORDAI_Friend(a, u, true)
    elseif target == 5 then
        set t = u
    endif
    if target != 0 and t == null then
        return false
    endif
    if ORDAI_Bit(LoadInteger(ORDAI_data, 7, a), 8) then
        if target == 0 then
            set issued = UnitUseItem(u, ORDAI_Carried(a, u))
        elseif target == 2 or target == 5 then
            set issued = UnitUseItemPoint(u, ORDAI_Carried(a, u), GetUnitX(t), GetUnitY(t))
        else
            set issued = UnitUseItemTarget(u, ORDAI_Carried(a, u), t)
        endif
    elseif target == 0 then
        set issued = IssueImmediateOrderById(u, order)
    elseif target == 2 or target == 5 then
        set issued = ORDAI_PointOrder(u, LoadInteger(ORDAI_data, 3, a), order, GetUnitX(t), GetUnitY(t))
    else
        set issued = IssueTargetOrderById(u, order, t)
    endif
    set t = null
    return issued
endfunction

function ORDAI_Cast takes integer a returns nothing
    local unit u
    local integer tries = 0
    set ORDAI_spellExpect = LoadInteger(ORDAI_data, 3, a)
    call GroupClear(ORDAI_tried)
    loop
        exitwhen tries >= 3
        set u = ORDAI_NextCaster(a)
        exitwhen u == null
        set tries = tries + 1
        call GroupAddUnit(ORDAI_tried, u)
        if ORDAI_Issue(a, u) then
            if LoadInteger(ORDAI_data, 4, a) != 0 then
                set ORDAI_castUnit = u
                set ORDAI_castOrder = LoadInteger(ORDAI_data, 4, a)
                if ORDAI_pointOrder != 0 then
                    set ORDAI_castOrder = ORDAI_pointOrder
                endif
            endif
            exitwhen true
        endif
    endloop
    call GroupClear(ORDAI_tried)
    set u = null
endfunction

// The map's own UI sync: the same prefix and payload its frame buttons send.
function ORDAI_Sync takes integer a returns nothing
    set ORDAI_syncPrefix = LoadStr(ORDAI_data, 10, a)
    set ORDAI_syncData = I2S(LoadInteger(ORDAI_data, 4, a))
    set ORDAI_synced = false
    set ORDAI_flagBefore = ORDAI_Flag(ORDAI_syncPrefix)
    // Armed before sending: an engine may deliver the sync event inside BlzSendSyncData.
    set ORDAI_flagAction = a
    if not BlzSendSyncData(ORDAI_syncPrefix, ORDAI_syncData) then
        set ORDAI_flagAction = 0
    endif
endfunction

// Wisp pads take one idle wisp of the pad's type (oldest first); fighter pads (raw 0) take
// the strongest idle fighter, as the story action does.
function ORDAI_Strongest takes integer raw returns unit
    local unit u
    local unit best = null
    local integer rank
    local integer bestRank = 0
    local integer damage
    local integer bestDamage = 0
    call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_units)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_units, u)
        if ORDAI_Visible(u) and not IsUnitPaused(u) and GetUnitMoveSpeed(u) > 0. and GetUnitCurrentOrder(u) != OrderId("move") and ((raw != 0 and GetUnitTypeId(u) == raw) or (raw == 0 and GetUnitPointValue(u) > 0 and BlzGetUnitBaseDamage(u, 0) > 0)) then
            set rank = 0
            set damage = 0
            if raw == 0 then
                set rank = GetUnitPointValue(u)
                set damage = BlzGetUnitBaseDamage(u, 0)
            endif
            if best == null or rank > bestRank or (rank == bestRank and damage > bestDamage) or (rank == bestRank and damage == bestDamage and GetHandleId(u) < GetHandleId(best)) then
                set best = u
                set bestRank = rank
                set bestDamage = damage
            endif
        endif
    endloop
    set u = best
    set best = null
    return u
endfunction

// Accepted once the move is issued.
function ORDAI_Move takes integer a returns nothing
    local rect r = ORDAI_Rect(a)
    local unit best = ORDAI_Strongest(LoadInteger(ORDAI_data, 3, a))
    if r != null and best != null and IssuePointOrder(best, "move", GetRectCenterX(r), GetRectCenterY(r)) then
        set ORDAI_ok = 1
    endif
    set best = null
    set r = null
endfunction

// Selecting the unit fires the map's EVENT_PLAYER_UNIT_SELECTED path (Jos 7).
function ORDAI_Choose takes integer a returns nothing
    local integer raw = LoadInteger(ORDAI_data, 3, a)
    local rect r = ORDAI_Rect(a)
    local unit u
    set ORDAI_selectExpect = null
    if r == null then
        return
    endif
    call GroupEnumUnitsInRect(ORDAI_targets, r, null)
    loop
        set u = FirstOfGroup(ORDAI_targets)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_targets, u)
        if ORDAI_selectExpect == null and GetUnitTypeId(u) == raw and GetWidgetLife(u) > 0.405 then
            set ORDAI_selectExpect = u
        endif
    endloop
    if ORDAI_selectExpect != null then
        call SelectUnit(ORDAI_selectExpect, false)
        call SelectUnit(ORDAI_selectExpect, true)
    endif
    set r = null
endfunction

// No native sends chat as a player, so the registered handler runs in this sync trigger's
// context (GetTriggerPlayer is Player(0)), as the chat fusions do. The only chat input,
// QVs (-offline), records its effect in qs[player].
function ORDAI_Chat takes integer a returns nothing
    local boolean before = qs[0]
    call ExecuteFunc(LoadStr(ORDAI_data, 11, a))
    if qs[0] and not before then
        set ORDAI_ok = 1
    endif
endfunction

// 항해목표 units of Player(5) (war3map.j): 5 the admiral o02Q/o02M/o02R that WER 33417 spawns at
// rd once the gate falls; 6 Player(0)'s own creep camp n00E/n00F/n00G (A90A level 1: vXR
// 63407, UVR 31920, d4v 40968). The lowest handle wins, so the choice is deterministic.
function ORDAI_Hostile takes integer target returns unit
    local unit u
    local unit found = null
    local integer raw
    call GroupEnumUnitsOfPlayer(ORDAI_targets, Player(5), null)
    loop
        set u = FirstOfGroup(ORDAI_targets)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_targets, u)
        set raw = GetUnitTypeId(u)
        if GetWidgetLife(u) > 0.405 and not IsUnitType(u, UNIT_TYPE_DEAD) and (found == null or GetHandleId(u) < GetHandleId(found)) then
            if target == 5 and (raw == 'o02Q' or raw == 'o02M' or raw == 'o02R') then
                set found = u
            elseif target == 6 and (raw == 'n00E' or raw == 'n00F' or raw == 'n00G') and GetUnitAbilityLevel(u, 'A90A') == 1 then
                set found = u
            endif
        endif
    endloop
    set u = found
    set found = null
    return u
endfunction

// inputs.OBJECTIVES targets: 1 황금 종 Hd (B005), 2 영혼 Bd (B008), 3 정의의 문 Ud (B004),
// 4 해왕류 jB (o02N), 5 해군대장, 6 own creep.
// Warcraft III's compiler rejects returning a destructable or unit as `widget` (the simulator
// and pjass accept it), so destructable and unit targets are handled with their own types.
function ORDAI_ObjectiveWreck takes integer target returns destructable
    if target == 1 then
        return Hd
    elseif target == 2 then
        return Bd
    elseif target == 3 then
        return Ud
    endif
    return null
endfunction

function ORDAI_ObjectiveUnit takes integer target returns unit
    if target == 4 then
        return jB
    elseif target >= 5 then
        return ORDAI_Hostile(target)
    endif
    return null
endfunction

// The strongest idle fighter attacks the objective; accepted once the order is issued (the
// map rewards the kill through its own death triggers).
function ORDAI_Attack takes integer a returns nothing
    local integer target = LoadInteger(ORDAI_data, 4, a)
    local destructable d = ORDAI_ObjectiveWreck(target)
    local unit victim = ORDAI_ObjectiveUnit(target)
    local unit u = ORDAI_Strongest(0)
    if u != null then
        if d != null and GetDestructableLife(d) > 0.405 and IssueTargetOrder(u, "attack", d) then
            set ORDAI_ok = 1
        elseif victim != null and GetWidgetLife(victim) > 0.405 and IssueTargetOrder(u, "attack", victim) then
            set ORDAI_ok = 1
        endif
    endif
    set d = null
    set victim = null
    set u = null
endfunction

// 기록지침 rows: synthetic TECH ids of ord_ai.logbook LOGBOOK_TECHS, never negative.
function ORDAI_Row takes integer id, integer value returns nothing
    if value < 0 then
        set value = 0
    endif
    call Preload("ORD_AI|1|TECH|" + I2S(id) + "|" + I2S(value))
endfunction

// Mission state: 0 open (or not assigned: slot -1), 1 achieved, 2 failed.
function ORDAI_Goal takes integer slot, boolean achieved, boolean failed returns integer
    if slot < 0 then
        return 0
    elseif achieved then
        return 1
    elseif failed then
        return 2
    endif
    return 0
endfunction

// Life percent 1..100 while alive, 0 when dead or missing.
function ORDAI_Wreck takes destructable d returns integer
    if d == null or GetDestructableLife(d) <= 0. or GetDestructableMaxLife(d) <= 0. then
        return 0
    endif
    return R2I(99. * GetDestructableLife(d) / GetDestructableMaxLife(d)) + 1
endfunction

function ORDAI_Health takes unit u returns integer
    if u == null or GetWidgetLife(u) <= 0.405 or GetUnitState(u, UNIT_STATE_MAX_LIFE) <= 0. then
        return 0
    endif
    return R2I(99. * GetWidgetLife(u) / GetUnitState(u, UNIT_STATE_MAX_LIFE)) + 1
endfunction

// 1 + ord_ai.logbook NAVIGATION index of the chosen option Bp[0] (set by mjv, GJs, NCv,
// jFv and the round-24 default jXs), 0 while none is chosen.
function ORDAI_Navigation takes nothing returns integer
    local string name = Bp[0]
    if not Bs[0] then
        return 0
    elseif name == "일석이조" then
        return 1
    elseif name == "긴급소집" then
        return 2
    elseif name == "특성공학" then
        return 3
    elseif name == "계엄령" then
        return 4
    elseif name == "바운티헌터" then
        return 5
    elseif name == "로얄로더" then
        return 6
    elseif name == "카지노" then
        return 7
    elseif name == "리스크헷지" then
        return 8
    elseif name == "환전소" then
        return 9
    elseif name == "최대출력" then
        return 10
    elseif name == "연금술" then
        return 11
    elseif name == "역발상" then
        return 12
    endif
    return 0
endfunction

// Export order = LOGBOOK_TECHS: ZG00-ZG09 missions T000-T009, ZP00-ZP09 their progress,
// ZQ00-ZQ07 quests, ZN00-ZN04 navigation. Flags: jeR (Jh), HJR (jh), h1v (Ih), v4s (kh),
// AXv (Vh), CjR (wh), jUs (gh), Vjv (xh), WER (Bh), Cbs (yh); slots th..Nh from bBs.
function ORDAI_Logbook takes nothing returns nothing
    local unit u = ORDAI_Hostile(6)
    local integer stage = 0
    local integer slot = 0
    if u != null then
        if GetUnitTypeId(u) == 'n00E' then
            set stage = 1
        elseif GetUnitTypeId(u) == 'n00F' then
            set stage = 2
        else
            set stage = 3
        endif
    endif
    call ORDAI_Row('ZG00', ORDAI_Goal(th, Jh[0] and ORDAI_fastRound < 8, Av >= 8))
    call ORDAI_Row('ZG01', ORDAI_Goal(Eh, not jh, Av >= 9))
    call ORDAI_Row('ZG02', ORDAI_Goal(nh, not Ih, false))
    call ORDAI_Row('ZG03', ORDAI_Goal(Uh, not kh, Av >= 30))
    call ORDAI_Row('ZG04', ORDAI_Goal(ch, Vh[0], false))
    call ORDAI_Row('ZG05', ORDAI_Goal(Sh, not wh, false))
    call ORDAI_Row('ZG06', ORDAI_Goal(Ah, not gh, false))
    call ORDAI_Row('ZG07', ORDAI_Goal(Mh, not xh, false))
    call ORDAI_Row('ZG08', ORDAI_Goal(oh, not Bh, false))
    call ORDAI_Row('ZG09', ORDAI_Goal(Nh, not yh, false))
    call ORDAI_Row('ZP00', UP)
    call ORDAI_Row('ZP01', 2 * jP + 6)
    call ORDAI_Row('ZP02', ORDAI_Wreck(Hd))
    call ORDAI_Row('ZP03', ORDAI_Wreck(Bd))
    call ORDAI_Row('ZP04', ORDAI_Wreck(Ud))
    call ORDAI_Row('ZP05', ORDAI_Health(jB))
    call ORDAI_Row('ZP06', ORDAI_Health(ORDAI_Hostile(5)))
    call ORDAI_Row('ZP07', stage)
    call ORDAI_Row('ZP08', ORDAI_Health(u))
    call ORDAI_Row('ZP09', Mp)
    loop
        exitwhen slot >= 3
        call ORDAI_Row('ZQ00' + slot, wr[slot])
        set slot = slot + 1
    endloop
    set slot = 0
    loop
        exitwhen slot >= 3
        if Sr[slot] and wr[slot] != 0 then
            call ORDAI_Row('ZQ03' + slot, 1)
        else
            call ORDAI_Row('ZQ03' + slot, 0)
        endif
        set slot = slot + 1
    endloop
    if Dr[0] then
        call ORDAI_Row('ZQ06', 1)
    else
        call ORDAI_Row('ZQ06', 0)
    endif
    call ORDAI_Row('ZQ07', up[0])
    call ORDAI_Row('ZN00', ORDAI_Navigation())
    if Hp[0] then
        call ORDAI_Row('ZN01', 1)
    else
        call ORDAI_Row('ZN01', 0)
    endif
    call ORDAI_Row('ZN02', Dp[0])
    call ORDAI_Row('ZN03', GetUnitAbilityLevel(sp[0], 'A09C'))
    call ORDAI_Row('ZN04', GetItemCharges(Ep[0]))
    set u = null
endfunction

// 숨은 보물상자 (ord_ai.treasure): chests are map coordinates Ur[i], Br[i] (0 <= i < Qr), live
// while not both 0; A05D 탐색 (TTs 30801, qps 22634) collects every live chest in the square of
// half-width cP[player] around its target point. The caster is 기록지침 sp[0] (H0C4) while it
// lives, else the oldest living Player(0) h084 with A05D.
function ORDAI_TreasureCaster takes nothing returns unit
    local unit u
    local unit best = null
    if sp[0] != null and GetWidgetLife(sp[0]) > 0.405 and not IsUnitType(sp[0], UNIT_TYPE_DEAD) and GetUnitAbilityLevel(sp[0], 'A05D') > 0 then
        return sp[0]
    endif
    call GroupEnumUnitsOfPlayer(ORDAI_targets, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_targets)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_targets, u)
        if GetUnitTypeId(u) == 'h084' and GetWidgetLife(u) > 0.405 and not IsUnitType(u, UNIT_TYPE_DEAD) and GetUnitAbilityLevel(u, 'A05D') > 0 and (best == null or GetHandleId(u) < GetHandleId(best)) then
            set best = u
        endif
    endloop
    set u = best
    set best = null
    return u
endfunction

// A05D is in the casters' uabi (level 1 from the start) and only R009 (the round-10 boss,
// Gcv 13955) enables it, so the tech gates it; then cooldown and mana decide.
function ORDAI_TreasureReady takes unit caster returns integer
    local integer level
    if caster == null or GetPlayerTechCount(Player(0), 'R009', true) <= 0 then
        return 0
    endif
    set level = GetUnitAbilityLevel(caster, 'A05D')
    if level <= 0 or BlzGetUnitAbilityCooldownRemaining(caster, 'A05D') > 0. or GetUnitState(caster, UNIT_STATE_MANA) < I2R(BlzGetUnitAbilityManaCost(caster, 'A05D', level - 1)) then
        return 0
    endif
    return 1
endfunction

// A coordinate inside IY (avs spawns in Rect(-6016,-2336,3744,6080)) as 0..1000.
function ORDAI_TreasureScale takes real value, real low, real high returns integer
    local integer scaled = R2I((value - low) / (high - low) * 1000. + 0.5)
    if scaled < 0 then
        return 0
    elseif scaled > 1000 then
        return 1000
    endif
    return scaled
endfunction

// Export order = TREASURE_TECHS: ZC00 live count, ZC01 ready, then ZXkk/ZYkk of the first 16
// live chests in slot order (0 when absent). The same list is kept in ORDAI_chest* for the
// chest actions and the human capture, which run before the next export.
function ORDAI_Treasure takes nothing returns nothing
    local unit caster = ORDAI_TreasureCaster()
    local integer i = 0
    local integer n = 0
    local integer id
    loop
        exitwhen i >= Qr
        if Ur[i] != 0. or Br[i] != 0. then
            if n < 16 then
                set ORDAI_chestSlot[n] = i
                set ORDAI_chestX[n] = Ur[i]
                set ORDAI_chestY[n] = Br[i]
            endif
            set n = n + 1
        endif
        set i = i + 1
    endloop
    set ORDAI_chestCount = n
    if n > 16 then
        set ORDAI_chestCount = 16
    endif
    call ORDAI_Row('ZC00', n)
    call ORDAI_Row('ZC01', ORDAI_TreasureReady(caster))
    set i = 0
    loop
        exitwhen i >= 16
        // 'ZX00' + tens * 256 + ones spells ZX00..ZX15 (the third and fourth characters).
        set id = i / 10 * 256 + i - i / 10 * 10
        if i < ORDAI_chestCount then
            call ORDAI_Row('ZX00' + id, ORDAI_TreasureScale(ORDAI_chestX[i], -6016., 3744.))
            call ORDAI_Row('ZY00' + id, ORDAI_TreasureScale(ORDAI_chestY[i], -2336., 6080.))
        else
            call ORDAI_Row('ZX00' + id, 0)
            call ORDAI_Row('ZY00' + id, 0)
        endif
        set i = i + 1
    endloop
    set caster = null
endfunction

// Action 6 diagnostic rows (ord_ai.diagnostics; the policy never reads them): Player(0)
// fighters inside the story arena of ORDAI_Eligible a == 6, those ordered to move, and
// those ORDAI_Eligible accepts.
function ORDAI_ArenaDiag takes nothing returns nothing
    local unit u
    local integer inside = 0
    local integer moving = 0
    local integer eligible = 0
    call GroupEnumUnitsOfPlayer(ORDAI_targets, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_targets)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_targets, u)
        if GetUnitPointValue(u) > 0 and BlzGetUnitBaseDamage(u, 0) > 0 and GetUnitX(u) >= GetRectMinX(TP[0]) and GetUnitX(u) <= GetRectMaxX(EY) and GetUnitY(u) >= GetRectMinY(TP[2]) and GetUnitY(u) <= GetRectMaxY(EY) then
            set inside = inside + 1
            if GetUnitCurrentOrder(u) == OrderId("move") then
                set moving = moving + 1
            endif
            if ORDAI_Eligible(u, 6) then
                set eligible = eligible + 1
            endif
        endif
    endloop
    call ORDAI_Row('ZD00', inside)
    call ORDAI_Row('ZD01', moving)
    call ORDAI_Row('ZD02', eligible)
endfunction

// Round boss rows (ord_ai.boss): the boss is CreateUnit(Player(6), iB[Av], ...) on the
// player's lane (war3map.j 51754, 61807) and BUs (6831) defeats a player whose boss
// still lives when the round timer Vp expires. ZB00: health of the live Player(6) iB[Av]
// unit nearest to Player(0)'s lane center uP[0], 0..1000 (0 without a boss); ZB01: Vp's
// remaining time in tenths of a second.
function ORDAI_Boss takes nothing returns nothing
    local unit u
    local unit best = null
    local real x = GetRectCenterX(uP[0])
    local real y = GetRectCenterY(uP[0])
    local real d
    local real bestD = 0.
    local integer health = 0
    if tP[0] > 0 then
        call GroupEnumUnitsOfPlayer(ORDAI_targets, Player(6), null)
        loop
            set u = FirstOfGroup(ORDAI_targets)
            exitwhen u == null
            call GroupRemoveUnit(ORDAI_targets, u)
            if GetUnitTypeId(u) == iB[Av] and GetWidgetLife(u) > 0.405 and not IsUnitType(u, UNIT_TYPE_DEAD) then
                set d = (GetUnitX(u) - x) * (GetUnitX(u) - x) + (GetUnitY(u) - y) * (GetUnitY(u) - y)
                if best == null or d < bestD then
                    set best = u
                    set bestD = d
                endif
            endif
        endloop
        if best != null and GetUnitState(best, UNIT_STATE_MAX_LIFE) > 0. then
            set health = R2I(GetWidgetLife(best) / GetUnitState(best, UNIT_STATE_MAX_LIFE) * 1000. + 0.5)
        endif
    endif
    call ORDAI_Row('ZB00', health)
    call ORDAI_Row('ZB01', R2I(TimerGetRemaining(Vp) * 10. + 0.5))
    set best = null
endfunction

// Chest actions (kind 10): 탐색 at the k-th live chest of the last export. Refused (ORDAI_ok 0)
// when that slot was found since, or no caster or R009; accepted once A05D takes effect
// (ORDAI_Spelled), where the map's own handler collects the chests.
function ORDAI_Scan takes integer a returns nothing
    local integer order = LoadInteger(ORDAI_data, 4, a)
    local integer k = LoadInteger(ORDAI_data, 5, a)
    local integer slot = ORDAI_chestSlot[k]
    local unit caster = ORDAI_TreasureCaster()
    set ORDAI_ok = 0
    if k >= ORDAI_chestCount or slot >= Qr or not (Ur[slot] != 0. or Br[slot] != 0.) or caster == null or GetPlayerTechCount(Player(0), 'R009', true) <= 0 then
        set caster = null
        return
    endif
    set ORDAI_spellExpect = LoadInteger(ORDAI_data, 3, a)
    if ORDAI_PointOrder(caster, ORDAI_spellExpect, order, Ur[slot], Br[slot]) then
        set ORDAI_castUnit = caster
        set ORDAI_castOrder = ORDAI_pointOrder
    endif
    set caster = null
endfunction

function ORDAI_Input takes integer a returns nothing
    local integer kind = LoadInteger(ORDAI_data, 2, a)
    if kind >= 1 and kind <= 3 then
        call ORDAI_Building(a)
    elseif kind == 4 then
        call ORDAI_Cast(a)
    elseif kind == 5 then
        call ORDAI_Sync(a)
    elseif kind == 6 then
        call ORDAI_Move(a)
    elseif kind == 7 then
        call ORDAI_Choose(a)
    elseif kind == 8 then
        call ORDAI_Chat(a)
    elseif kind == 9 then
        call ORDAI_Attack(a)
    elseif kind == 10 then
        call ORDAI_Scan(a)
    endif
endfunction

// The map activates the evolved unit (kU, 152 line kills) at the start location, the middle of
// the lane loop and out of reach of every line creep, where it would idle for the whole game.
// An idle fighter standing there is walked to the summon area aP[0], where summoned units start.
function ORDAI_Deploy takes nothing returns nothing
    local unit u
    local real x = GetStartLocationX(GetPlayerStartLocation(Player(0)))
    local real y = GetStartLocationY(GetPlayerStartLocation(Player(0)))
    local real dx
    local real dy
    if aP[0] == null then
        return
    endif
    call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
    loop
        set u = FirstOfGroup(ORDAI_units)
        exitwhen u == null
        call GroupRemoveUnit(ORDAI_units, u)
        set dx = GetUnitX(u) - x
        set dy = GetUnitY(u) - y
        if dx * dx + dy * dy <= 40000. and ORDAI_Visible(u) and not IsUnitPaused(u) and GetUnitMoveSpeed(u) > 0. and GetUnitCurrentOrder(u) == 0 and GetUnitPointValue(u) > 0 and BlzGetUnitBaseDamage(u, 0) > 0 then
            call IssuePointOrder(u, "attack", GetRectCenterX(aP[0]), GetRectCenterY(aP[0]))
        endif
    endloop
    set u = null
endfunction

function ORDAI_Act takes integer a returns nothing
    local unit u
    local rect destination = null
    local string handler
    if a == 0 then
        set ORDAI_ok = 1
        return
    endif
    if a == 7 then
        // Every eligible random wisp walks to fY; the original enter trigger summons.
        call GroupEnumUnitsOfPlayer(ORDAI_units, Player(0), null)
        loop
            set u = FirstOfGroup(ORDAI_units)
            exitwhen u == null
            call GroupRemoveUnit(ORDAI_units, u)
            if ORDAI_Eligible(u, 7) and IssuePointOrder(u, "move", GetRectCenterX(fY), GetRectCenterY(fY)) then
                set ORDAI_ok = 1
            endif
        endloop
        return
    endif
    if a >= 8 and a <= 13 then
        call ORDAI_Shop(a)
        return
    endif
    if a >= 14 and a < 1000 then
        // Player inputs of the generated table; never the fusion path below.
        call ORDAI_Input(a)
        return
    endif
    if a >= 1000 then
        // Chat recipes require the real GetTriggerPlayer supplied by our sync event.
        set handler = LoadStr(ORDAI_data, 1, a)
        if handler != null and handler != "" then
            call ExecuteFunc(handler)
            return
        endif
        if not LoadBoolean(cv, a, 1) then
            return
        endif
    endif
    set u = ORDAI_Pick(a)
    if u == null then
        return
    endif
    if a >= 1000 then
        // Native casting retains original special wrappers, caster context,
        // one-per-player restrictions and all post-ODv side effects.
        set ORDAI_castOrder = ORDAI_Order(a)
        set ORDAI_castUnit = u
        if ORDAI_castOrder == OrderId("unholyfrenzy") then
            // These six abilities explicitly allow invulnerable player units.
            call IssueTargetOrderById(u, ORDAI_castOrder, u)
        else
            call IssueImmediateOrderById(u, ORDAI_castOrder)
        endif
    else
        if a == 1 or a == 4 then
            set destination = fY
        elseif a == 2 then
            set destination = CY
        elseif a == 3 then
            set destination = aY
        elseif a == 5 then
            set destination = nP[0]
        elseif a == 6 then
            set destination = EY
        endif
        if destination != null and IssuePointOrder(u, "move", GetRectCenterX(destination), GetRectCenterY(destination)) then
            set ORDAI_ok = 1
        endif
    endif
    set u = null
    set destination = null
endfunction

function ORDAI_InitRecipes takes nothing returns nothing
    // All 81 literal-Rs wrappers registered through FMv in this map revision.
    call SaveStr(ORDAI_data, 1, $45543031, "nHs")
    call SaveStr(ORDAI_data, 1, $45543032, "kcs")
    call SaveStr(ORDAI_data, 1, $45543034, "mJR")
    call SaveStr(ORDAI_data, 1, $45543035, "MeR")
    call SaveStr(ORDAI_data, 1, $45543036, "eNv")
    call SaveStr(ORDAI_data, 1, $45543037, "FXs")
    call SaveStr(ORDAI_data, 1, $45543038, "BWs")
    call SaveStr(ORDAI_data, 1, $45543039, "pCs")
    call SaveStr(ORDAI_data, 1, $45543130, "Fis")
    call SaveStr(ORDAI_data, 1, $45543131, "kSs")
    call SaveStr(ORDAI_data, 1, $45543132, "lEv")
    call SaveStr(ORDAI_data, 1, $49463031, "r0v")
    call SaveStr(ORDAI_data, 1, $49463032, "v2R")
    call SaveStr(ORDAI_data, 1, $49463033, "kss")
    call SaveStr(ORDAI_data, 1, $49463034, "n1v")
    call SaveStr(ORDAI_data, 1, $49463035, "Kgv")
    call SaveStr(ORDAI_data, 1, $49463036, "S4R")
    call SaveStr(ORDAI_data, 1, $494d3031, "Nxs")
    call SaveStr(ORDAI_data, 1, $494d3032, "HIs")
    call SaveStr(ORDAI_data, 1, $494d3033, "EBs")
    call SaveStr(ORDAI_data, 1, $494d3034, "tUs")
    call SaveStr(ORDAI_data, 1, $494d3035, "pjv")
    call SaveStr(ORDAI_data, 1, $494d3036, "bKv")
    call SaveStr(ORDAI_data, 1, $494d3037, "Atv")
    call SaveStr(ORDAI_data, 1, $494d3038, "sLR")
    call SaveStr(ORDAI_data, 1, $494d3039, "BsR")
    call SaveStr(ORDAI_data, 1, $494d3130, "KRs")
    call SaveStr(ORDAI_data, 1, $494d3131, "euR")
    call SaveStr(ORDAI_data, 1, $494d3132, "Ims")
    call SaveStr(ORDAI_data, 1, $54523031, "Ats")
    call SaveStr(ORDAI_data, 1, $54523032, "BAs")
    call SaveStr(ORDAI_data, 1, $54523033, "kUR")
    call SaveStr(ORDAI_data, 1, $54523034, "yNR")
    call SaveStr(ORDAI_data, 1, $54523035, "wJR")
    call SaveStr(ORDAI_data, 1, $54523036, "NDR")
    call SaveStr(ORDAI_data, 1, $54523037, "SFv")
    call SaveStr(ORDAI_data, 1, $54523038, "WGs")
    call SaveStr(ORDAI_data, 1, $54523039, "McR")
    call SaveStr(ORDAI_data, 1, $54523130, "pjs")
    call SaveStr(ORDAI_data, 1, $54523131, "XJs")
    call SaveStr(ORDAI_data, 1, $54523132, "yeR")
    call SaveStr(ORDAI_data, 1, $54523133, "ARs")
    call SaveStr(ORDAI_data, 1, $54523134, "SMv")
    call SaveStr(ORDAI_data, 1, $54523135, "ynv")
    call SaveStr(ORDAI_data, 1, $54523136, "Jbv")
    call SaveStr(ORDAI_data, 1, $54523137, "hLs")
    call SaveStr(ORDAI_data, 1, $54523138, "Ekv")
    call SaveStr(ORDAI_data, 1, $54523139, "C7R")
    call SaveStr(ORDAI_data, 1, $54523230, "nXR")
    call SaveStr(ORDAI_data, 1, $54523231, "gev")
    call SaveStr(ORDAI_data, 1, $54523232, "idR")
    call SaveStr(ORDAI_data, 1, $54523233, "fSs")
    call SaveStr(ORDAI_data, 1, $54523234, "aSv")
    call SaveStr(ORDAI_data, 1, $54523235, "oPs")
    call SaveStr(ORDAI_data, 1, $54523236, "APs")
    call SaveStr(ORDAI_data, 1, $54523237, "R9R")
    call SaveStr(ORDAI_data, 1, $54523238, "UnR")
    call SaveStr(ORDAI_data, 1, $54523239, "nnR")
    call SaveStr(ORDAI_data, 1, $54523330, "QRR")
    call SaveStr(ORDAI_data, 1, $59593031, "Rts")
    call SaveStr(ORDAI_data, 1, $59593032, "gIv")
    call SaveStr(ORDAI_data, 1, $59593033, "Sks")
    call SaveStr(ORDAI_data, 1, $59593034, "bGR")
    call SaveStr(ORDAI_data, 1, $59593035, "nSs")
    call SaveStr(ORDAI_data, 1, $59593036, "Y0v")
    call SaveStr(ORDAI_data, 1, $59593037, "tXR")
    call SaveStr(ORDAI_data, 1, $59593038, "p0R")
    call SaveStr(ORDAI_data, 1, $59593039, "qjv")
    call SaveStr(ORDAI_data, 1, $59593130, "hev")
    call SaveStr(ORDAI_data, 1, $59593131, "dts")
    call SaveStr(ORDAI_data, 1, $59593132, "Nfs")
    call SaveStr(ORDAI_data, 1, $59593133, "R5v")
    call SaveStr(ORDAI_data, 1, $59593134, "DJs")
    call SaveStr(ORDAI_data, 1, $59593135, "ifs")
    call SaveStr(ORDAI_data, 1, $59593136, "bnv")
    call SaveStr(ORDAI_data, 1, $59593137, "KER")
    call SaveStr(ORDAI_data, 1, $59593138, "ABs")
    call SaveStr(ORDAI_data, 1, $59593230, "IcR")
    call SaveStr(ORDAI_data, 1, $59593232, "fcs")
    call SaveStr(ORDAI_data, 1, $59593233, "iTv")
    call SaveStr(ORDAI_data, 1, $59593235, "kQR")
endfunction
