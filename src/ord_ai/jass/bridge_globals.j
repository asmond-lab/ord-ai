// Insert inside the original globals block. Functions follow the original script.
boolean ORDAI_enabled = false
boolean ORDAI_busy = false
boolean ORDAI_sending = false
boolean ORDAI_setup = false
boolean ORDAI_started = false
boolean ORDAI_restarting = false
integer ORDAI_episode = 0
integer ORDAI_sequence = -1
integer ORDAI_action = 0
integer ORDAI_ok = 0
integer ORDAI_bootEpisode = 0
integer ORDAI_bootSequence = 0
integer ORDAI_bootPhase = 0
integer ORDAI_inEpisode = 0
integer ORDAI_inSequence = 0
integer ORDAI_inAction = 0
integer ORDAI_replyEpisode = 0
integer ORDAI_replySequence = 0
string ORDAI_wire = ""
timer ORDAI_poll = null
timer ORDAI_observe = null
group ORDAI_units = null
hashtable ORDAI_data = null
integer array ORDAI_types
integer ORDAI_typeCount = 0
unit ORDAI_castUnit = null
integer ORDAI_castOrder = 0
// The order ORDAI_PointOrder last issued (0 when it issued none).
integer ORDAI_pointOrder = 0
trigger ORDAI_sync = null
timer ORDAI_clock = null
integer ORDAI_mainGv = 0
trigger ORDAI_shop = null
integer ORDAI_expect = 0
// Player inputs (bridge_actions.j ORDAI_Input): TECH rows, target search and acceptance.
integer array ORDAI_techs
integer ORDAI_techCount = 0
group ORDAI_targets = null
group ORDAI_tried = null
unit ORDAI_caster = null
item ORDAI_carried = null
trigger ORDAI_spell = null
trigger ORDAI_select = null
trigger ORDAI_syncs = null
integer ORDAI_spellExpect = 0
unit ORDAI_selectExpect = null
string ORDAI_syncPrefix = ""
string ORDAI_syncData = ""
boolean ORDAI_synced = false
boolean ORDAI_flagBefore = false
integer ORDAI_flagAction = 0
// Round of Player(0)'s last fusion before 패스트유니크 (Jh) was decided (ORDAI_Fused).
integer ORDAI_fastRound = 99
// The first 16 live hidden chests of the last export (ORDAI_Treasure): map slot and position.
integer array ORDAI_chestSlot
real array ORDAI_chestX
real array ORDAI_chestY
integer ORDAI_chestCount = 0
// Lobby choice; build.py rewrites these two lines for curriculum stages.
integer ORDAI_difficulty = 1
boolean ORDAI_isekai = false
integer ORDAI_setupWait = 0
