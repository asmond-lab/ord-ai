// Diagnostic record build only (ord-ai build --kind diagnostic): inserted right before the
// original W5R (war3map.j 8117), which calls these between its own unchanged calls. Warcraft III
// compiles a call only after the callee's declaration, so these cannot live in the appended bridge.
// They use only globals and natives and write to the combat-event buffer of bridge_events.j.
// W5I|time|Gv|call id|unit handle|type|owner|target x|target y|collision size|flying
// W5S|call id|stage|x|y|current order (stage in, x1, y, x2, stop)
// W5R keeps the call id in a local and passes it to every stage: SetUnitX/SetUnitY can fire
// region events whose actions call W5R again before the outer call's next stage.

function ORDAI_LandingAdd takes string line returns nothing
    if ORDAI_eventCount < 1500 then
        set ORDAI_events[ORDAI_eventCount] = line
        set ORDAI_eventCount = ORDAI_eventCount + 1
    else
        set ORDAI_eventDropped = ORDAI_eventDropped + 1
    endif
endfunction

function ORDAI_LandingStage takes unit u, integer id, string stage returns nothing
    call ORDAI_LandingAdd("W5S|" + I2S(id) + "|" + stage + "|" + R2S(GetUnitX(u)) + "|" + R2S(GetUnitY(u)) + "|" + I2S(GetUnitCurrentOrder(u)))
endfunction

function ORDAI_LandingBegin takes unit u, real x, real y returns integer
    local real clock = 0.
    local string flying = "0"
    local integer id
    set ORDAI_landingCalls = ORDAI_landingCalls + 1
    set id = ORDAI_landingCalls
    if ORDAI_clock != null then
        set clock = TimerGetElapsed(ORDAI_clock)
    endif
    if IsUnitType(u, UNIT_TYPE_FLYING) then
        set flying = "1"
    endif
    call ORDAI_LandingAdd("W5I|" + R2S(clock) + "|" + I2S(Gv) + "|" + I2S(id) + "|" + I2S(GetHandleId(u)) + "|" + I2S(GetUnitTypeId(u)) + "|" + I2S(GetPlayerId(GetOwningPlayer(u))) + "|" + R2S(x) + "|" + R2S(y) + "|" + R2S(BlzGetUnitCollisionSize(u)) + "|" + flying)
    call ORDAI_LandingStage(u, id, "in")
    return id
endfunction
