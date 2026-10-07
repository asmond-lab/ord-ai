// Diagnostic record build only (ord-ai build --kind diagnostic): inserted right before the
// original GNv (war3map.j 13861), after the W5R helpers whose ORDAI_LandingAdd it reuses.
// Read-only rows of the final-round timer order (GNv periodic trigger vs the delayed Obv
// timer ICv starts at round 65); GNv rows only near the end so the event buffer is not filled.
// TMG|time|Gv|Av|Ov|Xv|Iv|Ss (GNv entry, while Av>=64 or Ss or Iv>0)
// TMO|time|Gv|Av|Ov|Xv|Iv|expired timer handle (Obv entry)

function ORDAI_TimerClock takes nothing returns string
    local real clock = 0.
    if ORDAI_clock != null then
        set clock = TimerGetElapsed(ORDAI_clock)
    endif
    return R2S(clock) + "|" + I2S(Gv) + "|" + I2S(Av) + "|" + I2S(Ov) + "|" + I2S(Xv) + "|" + I2S(Iv)
endfunction

function ORDAI_TimerTick takes nothing returns nothing
    local string ended = "0"
    if Av < 64 and not Ss and Iv <= 0 then
        return
    endif
    if Ss then
        set ended = "1"
    endif
    call ORDAI_LandingAdd("TMG|" + ORDAI_TimerClock() + "|" + ended)
endfunction

function ORDAI_TimerOnce takes nothing returns nothing
    call ORDAI_LandingAdd("TMO|" + ORDAI_TimerClock() + "|" + I2S(GetHandleId(GetExpiredTimer())))
endfunction
