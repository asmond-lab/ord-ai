// Diagnostic record build only (ord-ai build --kind diagnostic): appended after bridge_events.j.
// Read-only rows in events.txt; no rule, input, mask or reward depends on them.

// SPL|time|Gv|caster handle|type|owner|ability raw id (EVENT_PLAYER_UNIT_SPELL_EFFECT only:
// passive, autocast-without-effect and trigger-made effects do not appear here).
function ORDAI_EventSpell takes nothing returns boolean
    call ORDAI_EventAdd("SPL|" + ORDAI_EventStamp() + "|" + ORDAI_Who(GetTriggerUnit()) + "|" + I2S(GetSpellAbilityId()))
    return false
endfunction

function ORDAI_DiagnosticInit takes nothing returns nothing
    local integer i = 0
    set ORDAI_eventSpell = CreateTrigger()
    loop
        exitwhen i >= bj_MAX_PLAYER_SLOTS
        call TriggerRegisterPlayerUnitEvent(ORDAI_eventSpell, Player(i), EVENT_PLAYER_UNIT_SPELL_EFFECT, null)
        set i = i + 1
    endloop
    call TriggerAddCondition(ORDAI_eventSpell, Condition(function ORDAI_EventSpell))
endfunction
