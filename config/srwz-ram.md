# SRWZ RAM addresses (SLPS-25887 VER 1.04)

Verified addresses for cheats, input helpers and state readers. One line each: name, address, width,
meaning, how it was checked. PCSX2 check pending unless the line says otherwise. Same addresses work
in `PS2X_CHEATS` and PCSX2 pnach files (raw EE addresses).

| Name | Address | Width | Meaning | How checked |
|---|---|---|---|---|
| Map cursor x | 0x46bf40 | 8 | Battle map cursor column | snapsearch across 5 snapshots: LEFT x2/x3 lowered it by 2/3 (ours, scenario 1 turn 4) |
| Map cursor y | 0x46bf41 | 8 | Battle map cursor row (DOWN = +1) | same snapshots; DOWN from (13,8) landed on the enemy at (13,9) and the attack started |
| Map unit table | 0x5900f8 | 0xb0 per slot | One slot per unit on the map: our units from 0x5900f8 (Gunleon, then Garrod's Gundam X at 0x5901a8), enemies from 0x5912d8 (slot 48 on). +0x20 unit name (Shift-JIS), +0x64 map x, +0x65 map y (8-bit), +0x6c 03 = on the map, 06 = empty or destroyed (the enemy we destroyed turned 03 -> 06). Enemy slots also hold +0x66/+0x67 and +0x80/+0x81 square copies. | ours, scenario 1 turn 4: names and squares match the screen; targeting enemy slot 0x5912d8 at (13,9) and 0x5914e8 at (23,9) started both battles |
