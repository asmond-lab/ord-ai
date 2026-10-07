// Combat-event record build only (ord-ai build --kind record): appended after bridge_globals.j.
integer ORDAI_eventCount = 0
integer ORDAI_eventDropped = 0
string array ORDAI_events
integer ORDAI_hitCount = 0
unit array ORDAI_hitUnits
hashtable ORDAI_hits = InitHashtable()
trigger ORDAI_eventAttack = null
trigger ORDAI_eventDamage = null
trigger ORDAI_eventDeath = null
