# Progress log

Newest first. Each entry: what stopped the game, why, what fixed it.

## 2026-10-11 (afternoon): the map runs at ~40 frames/s on PCSX2's microVU (branch srwz-vu1x, not merged)
- **Result:** s01_map_menu, 60 s real time: **3.1 -> ~40 flips/s** (one run each, not a controlled A/B;
  60 VBlank/s). Picture vs the PCSX2 reference: scene PASS 1.2, the same score as the interpreter
  (`local\runs\ckpt-s01_map_menu-scene-1011-135856\scene-diff.png`). Two microVU runs: 301 ticks
  identical (deterministic). Switch off vs baseline c5ee546: 301 ticks identical.
- **How:** PS2Recomp srwz-vu1x 5b0e164. kzvu (KillzoneRecomp `ext/kzvu`, GPL-3) builds PCSX2's microVU
  as a library; CMake `PS2X_KZVU_DIR` + `PS2X_KZVU_PCSX2_DIR` (default empty = not built), run time
  `PS2X_VU1_KZVU=1`. PS2Memory uses kzvu's VU1 buffers; MSCAL/MSCNT go to kzvu; registers are seeded
  from the interpreter state on the first call (so loaded snapshots work). Sources: `local\ext\
  killzone-recomp`, PCSX2 646df006a4 sparse in `local\ext\pcsx2`. Wyn OK'd taking PCSX2 code.
- **Not done:** runtime-reviewer review and merge; snapshots *saved* with the switch on miss microVU's
  registers (save VF/VI/ACC/Q/I/R/clip via kzvuGet*); battle scenes and the other programs (C, D, F,
  G); a controlled A/B; how kzvu sources get to a fresh checkout (submodule or vendoring); build
  scripts (wt-build/verify-merge) passing the two CMake paths.

## 2026-10-11: VU1 speed plan decided; main build fixed
- **VU1 plan (docs/VU1-PLAN.md):** read-only study of the 7 microcode packages and the interpreter.
  Chosen: a VU1 translator in stages (Stage 0 counters + lockstep checker, Stage 1 pre-scheduled
  fast path, Stage 2 generated C++), not per-program HLE, not more interpreter tuning. Wyn:
  approved branch `srwz-vu1x` (park `srwz-vuint`) and Extra for the
  timing analysis.
- **Build failure of 2026-10-09 explained:** verify-merge -Regen ran a ps2_recomp.exe built
  2026-10-08 14:40, before Wall 21, so the regenerated code still called the 2-argument
  `PS2_PMFHL_LW(hi, lo)` against the 4-argument runtime macro (5 C2059 errors in groups 0x12a000
  and 0x12c000). Fix: `tools\build-ps2recomp.bat` first, then `verify-merge.ps1 -Regen -KeepGoing`
  (console `local\runs\verify-vu1prep.console.txt`). Lesson: after a recompiler change, rebuild
  the recompiler before -Regen.
- Note: `C:\devQ\SRWZ-native` is Wyn's backup copy; the live checkout is `C:\dev\SRWZ-native`.
- ~~**Open (wall candidate): fresh-boot LOAD finds no save.**~~ **Withdrawn, not a wall:** a DOWN
  held 30 ticks moves the title cursor two items, so CONTINUE was picked (no mid-map save: "ロード
  ファイルがありません" is correct). Seen in the cold-boot run `local\runs\coldload1sheet.png`
  (tick 3400: CONTINUE lit). **Use DOWN+8 in title menus.**
- **Fresh-boot reload works; checkpoint `s01_end_save` registered** (tick 31350, format 8):
  title snapshot -> START -> DOWN+8 -> CIRCLE (LOAD: slot 1 listed, Rand Lv 6, scenario 1, 5 turns,
  BS 40, funds 15,181; `local\runs\step_L3.png`) -> CIRCLE x2 -> CROSS = intermission main menu
  with the same values (`local\runs\step_L5.png`). Snapshots `local\runs\probe\L1..L5_*.bin`.
  PCSX2 side not paired.

## 2026-10-09 (tooling stream): VU1 interpreter speedups, stopped for a session change
- PS2Recomp branch `srwz-vuint` (off srwz 094f58a, worktree `local\wt-vuint`), not merged.
- Step 1, 89c9b21 (normalizeOperand inline; decoded pair by reference, no copy per pair): exact
  per-VBlank hashes vs plain srwz on s01_map_menu, s01_dialogue_window, s01_enemy_phase; 552/552
  tests; A/B x1.05 (101.0 s vs 96.1 s EE CPU, rounds x0.97-x1.16), so inside the noise.
- Step 2, 056576e (occupancy bitmasks for the flag/store/VF/VI/ACC pipelines; commit scans and
  free-slot searches visit only occupied slots, same order; masks rebuilt after a snapshot load):
  builds, 552/552, s01_map_menu exact. **A/B unfinished** (stopped for the switch); still to do:
  verify dialogue and enemy_phase, then the 3-round A/B.
- Harness: baseline runner `local\runs\bin-srwz-094f58a`; per step, `checkpoint.ps1 verify
  -Exe <baseline> -ExeB <new>` on the three scenes, then an alternating 3-round A/B of `eeCpuMs`
  over 300 deterministic VBlanks from the s01_map_menu snapshot (PS2X_DETERMINISTIC=1, headless).
- Next: step 3 (FMAC flag path without re-decoding per lane; per-pair case pointers and operand
  masks cached at decode time), then the share of main-thread time VU1 still takes
  (`PS2X_HOST_PROFILE`, `tools/profchart.py`; it was ~90% on the s01 map).

## 2026-10-09 (tooling stream): VU1 ahead-of-time recompilation by partial evaluation does not pay
- **Tried (PS2Recomp srwz-vuaot b08cea7, srwz-vuop; parked, not merged):** recompile each VU1
  micro-memory image (SRWZ uses only 3, 399 pairs each) into one C++ function per instruction pair
  that calls the interpreter's own per-pair step (`stepPair`) with the pair's decode as a
  compile-time constant. Exact by construction: identical per-VBlank hashes vs the interpreter on
  s01_dialogue_window, s01_map_menu and s01_enemy_phase, synthetic halt/budget/hand-over tests.
- **Result: slower.** Controlled A/B against plain srwz 094f58a (3 alternating rounds, deterministic
  300 VBlanks): map_menu 116.0 s vs 142.5 s, dialogue 92.6 s vs 113.7 s, x0.81 both.
- **Why:** every pair function carries a full copy of the step logic (~1,200 copies, several MB
  of hot code); instruction-cache misses outweigh what constant folding saves, while the
  interpreter's single loop stays cached. Inlining the opcode switches too made MSVC take over
  7,000 CPU-seconds per image without finishing.
- **Don't retry per-pair partial evaluation.** A VU1 recompiler has to emit compact code (one
  small body per instruction, statically scheduled, no per-pair copy of the generic step). Next:
  compact interpreter speedups (per-pair op pointers and operand masks cached at decode time),
  each A/B-checked against plain srwz.
- Kept from this work, useful anyway: `PS2X_VU1_CODE_DUMP`, the case-group split of
  execUpper/execLower (srwz-vuop), `checkpoint.ps1 verify -ExeB/-EnvA/-EnvB` for equivalence A/Bs.

## 2026-10-08 (night, walls stream)

### STOPPED HERE (2026-10-08 ~23:20 UTC; resume from this block)
- **Scenario 1 done end to end:** map won, town scene, intermission, end-of-scenario save to slot 1
  (Land Lv 6, scenario 1, 5 turns, BS 40, funds 15,181), loaded back in the same session: the
  intermission returns with the same values (local/runs/s01_save_reload.png, intermission_s01.png).
  Save files kept in local/reference/states/s01_end_save/mc0 (BISLPS-25887Q system data, -S0 slot 1),
  plus snapshot_w21.bin (right after the save, written by the srwz-w21 worktree runner).
- **Wall 21 fixed and merged:** srwz c5ee546 (merge of srwz-w21 26f9d60; runtime-reviewer: merge as
  is). Review follow-ups: the parallel multiplies/divides (PMULTW, PMULTUW, PMADDW, PMADDUW, PMSUBW,
  PMULTH, PMADDH, PHMADH, PMSUBH, PHMSBH, PDIVW, PDIVUW, PDIVBW) still fold all lanes into lo/hi and
  never write hi1/lo1 (SRWZ does not use them; open as a wall when a game does); tighten the emitter
  string asserts in ps2_mmi_hilo_tests.cpp.
- **Main build:** being rebuilt by `tools\verify-merge.ps1 -Regen -KeepGoing` at the switch. The first
  try failed because local/recomp/*.toml still pointed at the pre-reinstall Desktop folder
  (C:/Users/Wyn/Desktop/dev/...); 208 tomls repointed to C:/dev/..., backups *.toml.before-pathfix.
  Result: local/runs/verify-w21b.console.txt and the newest local/runs/verify-*/summary.txt. Read it first.
- **Not done yet:**
  - a fresh-boot reload of the slot 1 save (title -> load) on the rebuilt main build;
  - a named checkpoint for s01_end_save (`checkpoint.ps1 save s01_end_save -Snapshot ...` on the new
    build; PCSX2 side not captured);
  - PCSX2 comparison of Riot Jarenchi, the Harmonica cannon and the enemy-phase counters;
  - s01_first_battle_anim scene score moved 0.0 -> 14.0 with plain srwz 094f58a (limit 15): bisect the
    cheat loader / Wall 20 IOP pool / srwz-prof merges; real picture change or capture timing?
  - two isolated black dumps in the intermission (display on the buffer being drawn): dump timing or a
    one-frame flicker, unchecked.
- **How to play forward:** local/runs/probe/step.ps1 (stepx.ps1 adds -Exe for another runner build):
  a few keys from a snapshot (hold 30 ticks, 250-500 ticks apart: menus open slowly here), save and
  exit after -Wait ticks, last frame PNG plus the on-map unit list read from RAM (config/srwz-ram.md).
  Never long blind timed loops (two 30-minute loops were wasted that way).
- **Branches:** srwz-vuint (tooling's VU1 interpreter speedups, tooling entry above), srwz-vuaot parked
  (slower), srwz-cycles and srwz-typed3 parked, srwz-probe and srwz-sprwall debug only.
- GitHub off (no pushes until Wyn says "push").

### Wall 21 FIXED (merged srwz c5ee546): black screen after scenario 1's town scene (2026-10-09)
- After the post-battle town dialogue (BT.HAROLD saloon) the screen stays black: VBlank runs, nothing
  presented, DMA/GIF idle, pc always 0x12de18 (soft-float __muldf3) ra 0x12dc40, no SDK calls. Snapshots
  local/runs/probe/s36_360828 (black starts), s37_363828; logs local/runs/step_s36/s37, calls_c38.txt.
- **Cause (recompiler/runtime, MMI):** `PMFHL.LW` was wrong. The EE takes a full 64-bit product with
  `multu` + `pmfhl.lw` (rd.w0..w3 = LO0.w0, HI0.w0, LO1.w0, HI1.w0, so the low doubleword is
  HI0:LO0); our macro returned {sign-extended LO0, sign-extended HI0}, i.e. the low doubleword was just
  the sign-extended low word. __muldf3 (0x12dc58, via the wrapper 0x12dc00) builds its mantissa product
  from four such products. The caller is __fixunsdfdi-style 0x12b7e8 (x * 2^-32 for the high word),
  reached from 0x1b75e0 (end-of-scenario totals) etc. Its operands on the stack of s37 (wrapper frame
  sp=0x1fffd40): 0x400fe000_00000000 (3.984375) * 0x3df00000_00000000 (2^-32); both mantissas have a
  zero low word, so every partial product's low word is 0, the product came out 0 and the normalise
  loop at 0x12de18 (shift left until > 0x0FFF...F) spun forever. Every earlier double multiply and
  every __muldi3 (0x12b538, also pmfhl.lw) was silently wrong too. The other HI/LO moves had the same
  flaw: PMFHL.UW/SLW/LH/SH ignored LO1/HI1 and mixed words, PMTHL.LW wrote only 32 bits, PMFHI/PMFLO
  moved 64 bits, PMTHI/PMTLO 32 bits.
- **Fix (PS2Recomp srwz-w21 26f9d60):** runtime `ps2_pmfhl_{lw,uw,slw,lh,sh}(hi, lo, hi1, lo1)` and
  `ps2_pmthl_lw` per the EE manual (as PCSX2's interpreter); the recompiler passes ctx->hi1/lo1,
  PMFHI/PMFLO/PMTHI/PMTLO move all 128 bits. Tests: ps2_mmi_hilo_tests (8 cases), 560/560. Regenerated
  code differs in exactly the 5 pmfhl.lw lines (0x12b55c, 0x12dd3c/48/6c/88). Still wrong, unused by
  SRWZ (follow-up): the parallel word multiplies/divides PMULTW/PMULTUW/PMADDW/PMADDUW/PMSUBW/PDIVW/
  PDIVUW only fill LO0/HI0 and mix lanes.
- **Verified:** worktree runner (local/wt-w21, game code regenerated into local/recomp-w21/output) from
  s35_348528 with the same 40 CIRCLE presses as step_s36: the intermission menu (Land, funds 15,181,
  "第1話 ... までクリア!" ticker) is up by tick 360502 and stays live to 370000 (DMA/GIF/flips moving,
  no errors). Run local/runs/w21fix2.*, dumps w21fix2dump, sheet w21fix2sheet.png, frame
  w21fix2_intermission.png; snapshots probe/w21_361500/366000/370000.bin (w21 runner build only).
  s36/s37 are useless for re-testing: they were saved inside the loop with the zero product already in
  registers. Two isolated black dumps (364001, 365001, display fbp 0) in the intermission: probably dump
  timing against the async GS, not checked further.

### Scenario 1 map won (2026-10-08 night)
- Stepping from snapshots one key at a time (local/runs/probe/step.ps1: keys with hold 30, snapshot and
  exit after N ticks, last frame + on-map unit list): menus open ~1000 ticks after the press on this map,
  so timed key loops lose presses (the 30-minute loops sat in a status screen or the move range).
- Turn 5: Gunleon's Riot Jarenchi (range 1-2) destroyed an adjacent enemy (s5_244988 = cursor on the
  target), Garrod's Divider/Harmonica cannon hit the boss Gelava for 3542 (s13_260624), then turn 5
  ended through the map menu (フェイズ終了, はい). Enemy phase: Garrod's counter destroyed a Breaker,
  Gunleon's counter (Bunmer spanner) brought Gelava down (3097), Gain's scripted shot finished him,
  ally reinforcements warped in, map over, town scene (s35_348528). Pictures local/runs/s7_pick,
  s17_pick, s29_pick, s35_pick.png.
- Owed: compare Riot Jarenchi, the Harmonica cannon and the enemy-phase counters with PCSX2 once.
- Map menu items: フェイズ終了 / 検索 / 部隊表 / 作戦目的 / 戦況報告 / 一括フォーメーション / システム / 途中セーブ.

### Our first attack lands (2026-10-08 night)
- Found by snapshot search instead of PCSX2 key paths: the map cursor is two bytes x, y at
  0x46bf40, and the enemy list is records of 0xb0 bytes from 0x591340 with the map x, y at +0x18
  (config/srwz-ram.md). Gunleon stands at (16,8); the enemy record at (13,9) is 4 squares away,
  inside ブンマー・スパナ's 2-4 range.
- From k_143500: LEFT x3, DOWN, then CIRCLE presses (local/runs/probe/atkA1.ps1): target picked,
  battle prep, full battle animation (Gunleon's spanner charge, the enemy destroyed in an
  explosion), then the Get Result window (funds 1680, BS 5) on the map. Sheet
  local/runs/atkA1sheet.png, frames local/runs/atkA1_pick.png; snapshots probe/A1_143852 (cursor on
  the target, before the battle), A1_144300, A1_145000 (mid battle). The run then waits at the
  result window (no more presses).

### Our first attack, earlier attempts (2026-10-08 night)
- From t4_141006 (turn-4 player phase): CIRCLE at 143000 opens Gunleon's COMMAND menu with 移動/攻撃/
  精神/能力; DOWN selects 攻撃; CIRCLE opens the weapon list (renders correctly: ブンマー・スパナ
  2800, range 2-4, the others greyed); CIRCLE picks it and shows its blue range. Snapshots
  local/runs/probe/k_143110 (menu), k_143308 (weapon list), k_143500 (target mode), m5_* (cursor on
  the grey enemy). Sheets local/runs/atk1..5sheet.png.
- CIRCLE on the adjacent grey enemy does nothing: it stands 1 tile away and the only usable weapon
  reaches 2-4. Next: target the second enemy 3-4 tiles to the left (cursor LEFT x3-4 from target
  mode, then CIRCLE), or move first and attack from range 2.

### Scenario 1 turns 2-4 run clean (2026-10-08 night)
- End Phase loop (local/runs/probe/s1end3.ps1, s1end4.ps1: each 1800-tick cycle CROSS, CROSS, LEFT,
  LEFT, CIRCLE x3, then CIRCLE every 240) plays enemy phases, Gunleon counterattacks (battle
  animations), two enemies destroyed with experience screens, next player phases; no wall over
  40,000 ticks (local/runs/s1end4small.png). Snapshots local/runs/probe/t4_*.bin every 3000 ticks.
- Our own attacks still need a path: no enemy is in range of our units on turn 2, so timed CIRCLE only
  toggles move mode. Next: either step the cursor with frames in the loop, or a movement cheat.
- Battle-time HP display copy of Gunleon at 0x6cf440/0x6cf448 (6900 -> 6551 after the 349 hit;
  only during the battle); the persistent unit record is not found yet (candidates 0x6d2338,
  0x6d2490). RDRAM is at file offset 0x48bf in format-8 snapshots of this build.
- PS2X_CHEATS loader merged (srwz-cheats); no SRWZ cheat file yet.
### Wall 20 FIXED (2026-10-08 night): black screen after the second battle prep (IOP heap)
- Symptom: after the turn-2 battle prep the screen stays black and the runtime logs "Marked code at
  1000xx as modified" endlessly (3.7 GB of stdout).
- Chain: the game's memcpy-through-scratchpad (0x1c6f20) copies to 0x100000 (fromSPR D8_MADR); its
  caller, the battle sound-bank loader FUN_00189600, passes dst 0: sceSifAllocSysMemory(0, 0x90000)
  returned 0, the loader leaked its EE buffers on every per-frame retry, EE malloc ran dry, and the
  1 MB bank went to address 0 over the game's code. Found with a fromSPR-into-code log, a D8_MADR
  writer log (pc 0x1c70e8), PS2X_TRACE_CALLS on 0x1c6f20/0x189600 and an IOP alloc-failure log
  (debug branch srwz-sprwall).
- Cause: our IOP split RAM into a module arena [0x10000, 0x120000) (bump, mostly unused) and an
  832 KB heap [0x120000, 0x1F0000); with 0x5e400 resident only 0x71c00 was free.
- Fix (srwz-iopmem, merged): one pool from 0x10000, as sysmem on the IOP; modules allocate their
  images from it (first fit, freed on a failed load; fixed-address images fail if their range is
  taken); old snapshots claim each saved module's image on load. 551/551 tests (new pool test),
  regress 27/27 + scenes PASS; the second battle animation plays (local/runs/iop_before_after.png).
- Follow-ups from review: free module images on StopModule/non-resident start (with the per-module
  snapshot claim already in), honour AllocSysMemory type 1 (from the top), run the ps2xIOP test
  suites (PS2X_IOP_BUILD_TESTS) in build-ps2recomp.

### First fight matches PCSX2 (2026-10-08 evening): s01_battle_prep, s01_first_battle_anim
- Key path (both sides, from the COMMAND menu): CROSS (close), LEFT, LEFT (cursor off the unit),
  CIRCLE (map menu, フェイズ終了 selected), CIRCLE, CIRCLE (yes) -> ENEMY PHASE; Breaker attacks
  Gunleon -> battle prep screen -> CIRCLE (戦闘開始) -> battle animation. PCSX2: local/runs/bt4.ps1
  (sheet local/runs/bt4_sheet.png); ours: local/runs/probe/fight1.ps1, fight2.ps1, prep1.ps1, anim1.ps1.
- s01_enemy_phase (tick 78005), s01_battle_prep (78560; scene vs PCSX2 1491 s: PASS 3.3),
  s01_first_battle_anim (80080; HP/EN panel, portrait, dialogue all drawn; reference = our own frame
  because PCSX2's frame differs by a random dialogue line and camera pan: local/runs/fight_side.png).
  The 'grey status panel' of Wall 19 was the dialogue window; the real battle panel draws correctly.
- Ours spends ~25 s on the ENEMY PHASE banner/darkened map where PCSX2 takes ~4 s (77000-78000
  enemy movement/missile frames vs PCSX2's direct cut): not investigated yet.

### Battle map matches PCSX2 (2026-10-08 evening): checkpoint s01_map_menu
- Paired checkpoint `s01_map_menu` (ours tick 70250, COMMAND menu open on the first turn; PCSX2
  scenario1_map_1470.p2s, menu at game 1476 s): scene PASS 1.2. Menu, cursor diamond, unit, grid and
  buildings all match (diff local/runs/ckpt-s01_map_menu-scene-1008-192832/scene-diff.png). The
  earlier "flat floor / no HUD" was the same DMA chain bug; the red/black pulse is the game's move
  range (PCSX2 shows it too). `s01_map_pre_menu` (tick 70000) is the state before the CIRCLE.
- regress now runs both scene checks (config/game.json regressScenes).

### Wall 19 FIXED (2026-10-08 evening): DMA chain walk stopped at 4096 tags
- Cause: PS2Memory's chain-mode walk had `kMaxChainTags = 4096` and silently ended the transfer
  there. SRWZ's map 3D pass is a ~2 MB VIF1 chain; its tail (the last VU1 batches and the A+D
  PRMODECONT=1 restore) was dropped, so every later 2D draw took PRMODE's 3D attributes (STQ, not
  UV): flat grey message windows, no text/portraits, glyph cells as blocks. Found by comparing a
  PCSX2 GS dump of the map frame (tools/gsregs.py, from the scenario1_map_1470.p2s state) with our
  register stream: PCSX2 ends the 3D pass with two small batches and PRMODECONT=1, ours did not.
- Fix (PS2Recomp srwz a0ea809, branch srwz-dmachain): no tag limit beyond 1<<20 (looping guard) plus
  a 64 MiB data bound (review), capped `[dma:chain]` warning. 550/550 tests, regress dmachain1 PASS
  27/27, scene s01_dialogue_window PASS 6.5 (was FAIL 26.3). Dialogue window, portrait, text and
  the textured map floor + cursor now draw (local/runs/probe23sheet.png, probe22sheet.png).
- Not modelled (review notes, for other games): MFIFO (D_CTRL.MFD), stall control (STS/STD), and
  chains a game extends while running (the walk reads the chain at kick time).
- PCSX2 map state with command menu: local/reference/states/scenario1_map_1470.p2s; GS dumps of
  that frame: local/reference/w19dump3/gs/.

### Wall 19 diagnosis (2026-10-08 afternoon, walls thread; resume here)
- The "grey status panel" at ticks 49400-69000 is the map-dialogue window (PCSX2 shows it at game
  1240-1620 s with portrait and text). The red/black pulse on the map is genuine (PCSX2 shows it).
- Cause found: every 2D window draw reaches our GS with PRMODECONT=0, so its attributes come from
  PRMODE (0x58 from the 3D pass: STQ, not UV) instead of its PRIM (0x156, FST=1); each sprite
  samples one stale texel -> flat grey box, no glyphs/portrait, "bars" = glyph cells in one colour.
  Our stream per frame: 2D GIF chain (sceDmaSend) -> VIF1 3D run writing PRMODECONT 1,0 -> tris,
  no closing PRMODECONT=1 (logs local/runs/probe14..18.out.txt).
- PCSX2 ground truth (guidance map dump of 10-07, tools/gsregs.py): each VU1 batch starts
  with PRMODECONT 1,0 (inside PATH1 packets) and the frame's 3D ends with PRMODECONT=1 before the
  2D. Ours has one 1,0 pair and no closing 1 -> suspect missing/garbled VU1 XGKICK output (MSCNT
  continuation programs: 4 MSCAL vs 2516 MSCNT per few frames). Next: dump of the SAME scene
  (the 18:27 capture hit the guidance briefing instead: keys went astray while Wyn was active;
  local/reference/w19dump/gs/), then compare PATH1 packets write for write.
- Ruled out: MSKPATH3 (never used), GIF_MODE (never written), VU1 budget (never hit), GIF drain
  ordering (PS2X_GIF_FIFO=1 no change), CLUT load semantics (match the GS rule).
- Tools/branches: PS2Recomp `srwz-probe` e3c157d (debug switches, default off; some use getenv per
  call: make them cached before any merge). srwz-durable merged into srwz (2d5fcc2); 8753c20 still
  to merge after tooling's 3000-tick verify. Snapshot local/runs/probe/s3_56004.bin (format 7)
  loads into current builds; checkpoint local/reference/states/s01_dialogue_window.

### Wall 19: scenario 1 past the bazaar to the first map (route scenario1_next)
- **Main build (46dd575, bin-soak copy), FF1, headless:** scenario1 then CIRCLE every 400 ticks. The
  dialogue goes on past Rand's "Aan?" through the Zora bazaar (Garrod, Gelba, Mel) to ~48800, then a
  battle-animation scene (~49400-69000), then a battle map (~70000, Gunleon, compass top right).
  No hang, no `[guest-branch:missing-target]`, `[ee:thread-end]` or `[ee:unwind]` line in 4 runs
  (~28800+26400+26600+26400 ticks). Sheets `local/runs/snapnext{1..4}sheet.png`, frames
  `local/runs/snapnext/f57207.png`, `f62002.png`, `f72408.png`; chained snapshots
  `local/runs/snapnext/` (s1_19000, s2_36000, s3_56005, s4_72001 on the map).
- **First problem: wrong graphics, no hang.** (a) Battle animation: the bottom-right status panel is a
  flat grey box with no names, numbers or portrait, and the HP/EN bars are black/blue blocks
  (~56200-69000, f62002.png). (b) The map: floor is flat brown, then a full-screen red/black
  pulsing overlay (range highlight?), no HUD or text windows (f72408.png). Last tick line:
  `tick=27000 pc=0x143408 ra=0x143408 sp=0x1ffff80 vsync=82958 dma=249115` (alive, idle loop; the
  GS dumps keep changing). No PCSX2 reference for this scene yet (needs ~15 min route: capture
  only when someone fixes it).
- Route `scenario1_next` in config/game.json (verified by chained snapshots, not one continuous run).

### Wall 16: past SELECT SCENARIO into scenario 1
- **Reached at 1x:** protagonist select в†’ profile в†’ "г‚Ігѓјгѓ г‚’й–‹е§‹гЃ—гЃѕгЃ™" в†’ prologue crawl в†’
  scenario 1 dialogue (Breaker and Mel, then Rand). Contact sheet `local/runs/w16m5-sub.png`.
- **Guidance route:** briefing dialogue, then the stg_501 battle map with its dialogue and pad
  tutorial (`w16g5-sub.png`).
- **Cause of every hang:** functions the Ghidra map missed. A JAL or JALR hit no recompiled code
  (0x3eb780, 0x2bcc60, 0x2be0d0, 0x2bdbd0, 0x1bdb20, overlay 0x757280).
  - Fix: tools/codeptrs.py writes config/entries.txt (99) and config/overlay_entries.txt (178 in
    27 overlays).
  - Snapshot load on long strips fixed (srwz-w16 ba1904f, test).
  - VIF1/PATH1 debug switches added (bd25625).
  - Merged into srwz 64b4202; shared output regenerated with all entries.
- Route, snapshots and pad ticks: `local/runs/w16-notes.md`. Pad ticks are VBlanks: run routes
  at FF1.

### Soak on the main build (46dd575, guest libsdr on)
- 14 of 14 attract-demo runs at 1x passed (headless, muted, title snapshot to vsync 50000, ~383 s
  each; 12 planned plus 2 extra, 3-5 in parallel): 0 sound-poll hangs, 0 flat-DMA stalls. Before
  the srwz-sndhang fix about 2 in 7 froze. Logs `local/runs/soak/`, `local/runs/sndhang/soak*`.
- A copied runner needs its DLLs beside it (a bare exe copy exits silently in 2 s).
- Guidance battle map on this build draws the forest and cursor (`local/runs/maprun1sheet.png`).


### Walls 17/18 resolved (later the same night)
- **Wall 18 (sprite interpreter) fixed**, merged in srwz 8718957.
  - Cause: dispatchGuestBranch took a checkpoint unwind with ctx->pc at the callee's entry
    (nested self-call or loop to entry) for a plain return, so the caller resumed with the
    callee's registers.
  - Fix: EeScheduler::unwindingToDispatcher(); PS2X_UNWIND_LOG.
  - Result: FF4 A/B went from 11/12 hung to 0/12, and the 1x main route runs past scenario 1 into
    the Zora bazaar.
- **Wall 17 (3D maps) fixed** on srwz-vu3d, under review.
  - Cause (a): COP2 macro code could write VF0, which HiG's clip loops do on purpose for flags.
    Fixed with ps2_vu0_macro.h (flags, clamp, DIV/RSQRT, FTOI, VI0), plus 6 libvu0 stubs aligned
    with the SDK.
  - Cause (b): VIF1 DIRECT image data owed past a DIRECT was read from raw VIF bytes.
  - Result: guidance battle map matches PCSX2 (`local/runs/vu3d-guidemap-compare.png`); world
    map draws.
- **Still open:**
  - A rare EE thread-1 в†’ pc 0 stall in the attract demo; the chain is
    FUN_002f9bd0 в†’ FUN_002ed8e0 в†’ FUN_002ed9b0, see `local/runs/sndhang-notes.md`.
  - The GIF 0x59вЂ“0x5f в†’ display-register mapping (upstream, not real hardware).
  - COP1 Inf/NaN audit.

### Wall 17 (open): 3D maps black (branch srwz-vu3d, worktree local/wt-vu3d)
- Battle map and world map draw nothing.
  - The VIF1 в†’ VU1 path runs and kicks PATH1.
  - The per-object matrix HiG uploads (VU1 qw 0вЂ“7) is garbage: ~1e29 or NaN, so Q в‰€ 0.
- Suspect: the EE COP2 macro translations (vu_translation_helpers.cpp): VDIV by 0, no MAX clamp
  or denormal flush.
- Repro: `snap16/final_guide_map_7300.bin` (bin-w16g). Opus worker at Extra effort.

### Wall 18 (open): sprite-command interpreter on garbage (branch srwz-sprite, worktree local/wt-sprite)
- FUN_0036b330 (tables 0x42dc20/0x42dc80) loops at 0x36b600/0x36b630 or returns to a garbage pc.
  Frequent at FF4, occasional at 1x; likely tied to Wall 13a (decompression thread starved
  under fast-forward).
- Repro: `snap16/final_guide_7000.bin`. Opus worker at High effort.

### Wall 15: the 60/30 fps flip
- **Cause:** the game paces with thread priorities.
  - The main loop drops to priority 10 and the VBlank-end handler raises it, so a pass that ends
    after VBlank end waits a whole extra VBlank.
  - The inline GS raster (12вЂ“17 ms) plus 1вЂ“2 ms late wakeups came to ~17.1 ms per pass, against a
    16.67 ms budget.
- **Fix** (srwz-flip, under review, not merged):
  - pacing waits sleep then poll (PS2X_PACE_SPIN_US);
  - the presenter latches VRAM in slices;
  - PS2X_PACE_LOG;
  - asynchronous GS thread, PS2X_GS_ASYNC=1, default off.
- **Effect:** menu 31вЂ“33 в†’ 58вЂ“60 fps in alternating A/B runs (BlueStacks running, noisy).
- **Next lever:** raster cost, ~59 ms single-threaded per menu frame.

## 2026-10-07 (evening, walls stream)
- resume_into_owner on for SRWZ (main b5c45a6, regenerated): menu 45.3 game fps at 60 VBlank/s
  (menufps2), boot faster; regress baseline remade from hb9 (headless).
- srwz-timer merged (67ce40f, reviewed; batching default OFF; review fixed two default-mode
  regressions: eager snapshots failing to load, GIF_STAT slot dangling after a load). Build,
  ps2x_tests 481/481. hb10 regress "FAIL" and menufps3 21.5 fps were taken in quiet mode (job
  pinned to 8 cores at BelowNormal, other runners active): every screen correct, timing shifted;
  re-measure on a quiet machine with all cores before reading anything into it.
- Movie freeze under load (srwz-mpeg): GetPicture now pulls through the game's no-data callback
  like libmpeg (heavy-load stall reproduced without it; moderate load with it reaches the title,
  356 picture hashes identical). Review: stream callbacks must run synchronously inside that
  callback (ADPCM payloads otherwise read a reloaded buffer); being reworked, not merged.
- srwz-lean: semantics equal, no measurable speed difference; merge default-off next, then
  enable for SRWZ for the build-time win (crash/watchdog pc becomes block-level; rebuild with it
  off for an exact-instruction dig).
- Wall 14 (SPU2, own model, Fable): model + 47 tests; found the IOP sysmem allocator never reused
  freed blocks (sound driver looped on AllocSysMemory failing); fixed on srwz-spu2; demo rerun
  pending.

## 2026-10-07 (afternoon, walls stream)
- Rasteriser rewrite merged (srwz-gsfast, reviewed): title frame 314 в†’ ~11 ms on 8 threads in the
  replay; in game the title/menu run at 31-40 game fps instead of 3.4. Then a stall under host
  load (0.3 fps whenever a build ran): the raster pool's yield spins and per-worker check-in;
  fixed by counting chunks and sleeping on a condition variable (6c9d3b7/c1f436d, reviewed):
  34 fps under a build, unchanged unloaded.
- Fast-forward max: busy-scale governor and per-tick stdout flush (5736176, reviewed).
- Wall 13 (attract demo): not a hang. (a) Under fast-forward, decompression thread 2 is starved
  (it only runs between frame end and the next VBlank end; catch-up debt fires VBlank end at
  once); smaller with the fast rasteriser, still open. (b) At every speed the voice line never
  ends: multi_streamer.irx (RPC sid 0x12345) waits on SPU2 voice progress/IRQ; the IOP's SPU2 is
  a bare register map. Notes `local/runs/wall13-notes.md`.
- Wall 14 = SPU2. **Decision (Wyn, 2026-10-07): our own SPU2 voice/timing model; PCSX2's SPU2
  code stays out, licence stays open.** Fable worker at Extra effort on srwz-spu2.
- Open: the movie can park forever under heavy load (sceMpegGetPicture waitExternal with no
  frame), worker on srwz-mpeg. Resume-into-owner regenerate running.

## 2026-10-07 (tooling)
- Private GitHub backups (Wyn, 2026-10-07): main pushed to SloppyWyn/SRW-Z-Native at 7906bdf,
  PS2Recomp srwz to SloppyWyn/S2Recomp-srwz at daf09d7, both private. Full-history audit first:
  only tools, docs, config, CLAUDE.md and our runtime code (no binaries, images, generated game
  code). .gitignore and the pre-commit hook tightened (memory cards, captures, images, generated
  C++). Push main and srwz after each merge.
- Generated-code queue (walls thread merges in this order, regress between): resume_into_owner
  (srwz-owner a135bf9, merged; enable = regenerate with make_config's new line), batched
  timing `PS2X_BATCH_TIMING` (srwz-timer f71570b on srwz 5736176, default off: +21-43% guest work
  per EE-CPU second at FF max, -32% EE CPU at 1x, identical MPEG hashes; snapshots flush first),
  lean_codegen (srwz-lean 6222f55 on a135bf9: exe 87 -> 53 MB, build 478 -> 320 s, regress PASS
  and 11,685 identical picture hashes; run-time speed still to measure contention-proof:
  PS2X_REALTIME_CLOCK=0, EE-thread CPU between fixed vsync marks, scripts in local/lean/).
  Overlays follow both switches via tools/overlays.py (7c40383, 5a68177).
- Done today on tooling/main: tools/launch.ps1 (no console windows, no focus steal), no audio
  device for muted/headless runs, content-order regress, game window opens without focus
  (srwz-focus), snapshots merged.
- Guest names from retro-trans/SRW-Z (MIT fan translation, commit d766bc7, read only): 21 text,
  font, LZ and scenario functions verified by decompiling our ELF (body matched, not just the
  address) and listed in `config/names.txt`; `tools/make_config.py` adds them as named entry
  points (name table only: refuses bound, non-identifier or non-function-start entries).
  Rejected claims and the LZ/SRVC/cp932 notes: `docs/GAME-FORMATS.md`.
- Stub poison sweep (PS2Recomp srwz-poison): all 666 runtime handlers called with poisoned
  caller stack slots; only `sceeFontPrintfAt` (not bound by SRWZ) reads the caller's locals.
  Also suspected: the va_list cursor steps 4 bytes where the EE uses 8 (vfprintf is bound).
  Fixes in progress.
- The USB connect/disconnect chime Wyn heard (2026-10-07 ~12:06 local) was a Razer device's
  HID collection (VID 1532 PID 007A, plus Razer's RZVIRTUAL copy) restarting every 5-40 s,
  not our runs (1 of 50 runner starts within 5 s of an event; none overnight).

## 2026-10-07 (morning, walls stream)
- PS2Recomp `srwz` = b2fe4c0: memory-card ReadFast/WriteFast bindings regenerated (srwz.toml
  diff is exactly those two moving to stubs; FUN_00133630/00133740 are stub wrappers now),
  fast-forward `PS2X_FASTFORWARD=<f>|max` (reviewed; 0 = off; x4 reaches the title in 168 s vs
  479 s, movie pictures identical by hash), headless runner (`PS2X_HEADLESS=1`, automatic when
  no monitor; the windowed monitor probe is untested with a monitor present).
- New regress baseline `local/runs/baseline` from headless 1x run hb1; hb2 passes against it
  (`tools/regress.ps1 -Headless`, window widened to В±600 ticks). Headless runs reach screens
  earlier than windowed ones: the standard tick set now covers copyright, NAMCO BANDAI PRESENTS
  and the movie start, not the logos.
- Disk question answered (D: clone): CPU-bound, 3.3-3.5 game fps on both SSDs with the log on
  or off. Wall 12's cause is the CPU GS rasteriser (94% of game time on the title).
- In flight: rasteriser correctness + speed as one branch `srwz-gsfast` (Fable, High), measured
  with the replay harness (`ps2xTest/gs_replay`, `tools/gsref.py`, dump
  `local/reference/raster-gsdump/title.gs`). The menu fps check and Wall 13 (attract demo stall,
  likely the same slowness) wait for it.

## 2026-10-07 (~01:40, handoff before the usage pause; walls stream)
- PS2Recomp `srwz` = 87e353a: everything below is reviewed and merged. The function log
  tracker is off by default (ce47d08). Memory-card ReadFast/WriteFast handlers (srwz-mc,
  reviewed) need the bindings regenerated.
- Wall 10 done (full-height title; presenter follows SMODE2/DISPLAY like the GS CRTC; DH = 2h-1
  in frame mode; DH > 511 crops). Reviewed and merged as db6eb6f.
- Wall 11 done: the port reaches the first menu with a scripted START (run45). The pad was fine;
  the title runs at ~3.5 game fps, which made presses look ignored.
- In flight (each commits a checkpoint on its branch):
  - Wall 12, title/menu at ~3.5 fps (High, `local/wt-w12`, `srwz-w12`).
  - Fast-forward (`local/wt-ff`, `srwz-ff`): x4 в‰€ 2.8x real time in run fd4.
  - Dotted logo glitches, GS rasterizer (High, `local/wt-raster`, `srwz-raster`).
- Queued job: `regen.ps1` in the coordinator's scratchpad (log `local/runs/regen-mc.log`). It
  takes the build lock, rebuilds ps2_analyzer/ps2_recomp, reruns the analyzer and
  make_config.py (backups `local/recomp/*.before-mc`), regenerates output and overlays, then
  build-game.bat. Afterwards: check srwz.toml differs only by the two mc stubs, run regress,
  remake the baseline (`regress.ps1 -MakeBaseline`, run33's lacks the BANDAI NAMCO logo), and
  measure the menu's pad-read rate with the log off (Wyn's SSD-or-log question; the D:
  disk-test session measures it too).
- Next walls: Wall 13, the attract demo stalls at vsync ~39,130, pc 0x143898 (same place as
  Wall 12's slow title).

## 2026-10-07 (disk test)

### C: vs D: disk: no effect on game speed
Clone of main on D:\dev\SRWZ_D (Crucial P3 Plus; C: is a Crucial P2). Same two runners from
PS2Recomp 8138212 (log tracker on / PS2X_ENABLE_AGRESSIVE_LOGS=OFF), each run from C: and D:
with that disk's game files, working dir and logs, START at tick 3000 to skip the movie,
fps = scePadRead calls/s over host 90-150 s (`tools/fpsrun.py`), build lock held, 2 passes:
D log 3.30/3.35, C log 3.33/3.35, D nolog 3.38/3.57, C nolog 3.52/3.58 game fps.
ps2_log.txt ~540 MB per 150 s (~3.6 MB/s), far below either SSD. The title's ~3.5 fps is CPU,
not disk; the log costs ~5 %. D: build: tools 351 s (first configure), regen 46 s, runner 791 s.
Results: local/disktest/results.txt.

## 2026-10-06 (night, tooling)

### Tooling: binding checker (RECOMPILER-IDEAS item 3)
`tools/bindcheck.py` (report-only, `--selftest` passes, ~40 s on SRWZ; report in
`local/bindcheck/`): for each runtime binding it compares the guest copy's argument reads with the
handler's, checks the SDK database match, and flags dropped, duplicate, aliased, shadowed and
TODO bindings and libraries half owned by the runtime, with severity by reachability. SRWZ:
323 bindings, 1 ERROR, 83 WARN. Findings, most important first:
- `sceMcReadFast` (0x133630) and `sceMcWriteFast` (0x133740) are listed as stubs but the runtime
  has no handler, so they stay guest code doing SIF RPC to the IOP memory-card server against
  a runtime-owned libmc (fd from the runtime's `sceMcOpen`, result via its `sceMcSync`). The menu
  save/load code (`FUN_00193ce0` via 0x195490; writes from 0x192328, 0x1954b4) calls them: loading
  or saving a game will very likely fail. Needs two handlers like `sceMcRead`/`sceMcWrite`.
  (`docs/TITLE-PATH.md` says the browser uses `sceMcRead`; nothing calls it.)
- 22 bindings sit in gaps of the Ghidra map and are dropped by the recompiler without a word;
  all unreachable in SRWZ (no calls or references, overlays checked too), but the recompiler
  should warn.
- `_sceFsSemInit` handler is a TODO that throws (reachable only via unbound `sceDread`); four
  underscore aliases (`_sceSifSendCmd`, `_sceSifLoadModule*`, `_sceSifLoadElfPart`) resolve to
  handlers with fewer arguments, all dead today.

### Tooling stream: state at the second usage pause (2026-10-07 ~01:45)
- Merged: build lock (fair queue), wall loop, bindcheck, function log off, sceMcReadFast/WriteFast.
- Snapshot (srwz-snap 2770988): code complete, unit tests passed; runner build queued; runs and
  scoring continue on a Fable worker (handover in local/wt-snap/docs/SNAPSHOT-DESIGN.md).
- Perf (srwz-perf 9dae6f9, on srwz 87e353a): item-7 dispatch fixes + per-config Release flags
  built; no measurable gain at real time yet; next regress + clean throughput test (NOTES-perf.md).
- Build speed (srwz-build) and guest names/call trace/crash handler (srwz-debug): WIP committed,
  next steps in their NOTES files.

### Tooling stream: work in flight (state at 19:10, before a usage pause)
Each task lives in its own PS2Recomp worktree under `local/` (branch in brackets); every one
commits WIP with a NOTES file or design doc giving its next step. Every runner build takes the
`build` lock (`local\wt-tooling\tools\lock.ps1`) until the lock is on main.
- Runner save/restore of the whole state, `PS2X_SNAPSHOT_SAVE/AT/LOAD` (`local/wt-snap`,
  `srwz-snap`): feasibility in `docs/SNAPSHOT-DESIGN.md` there, then implementation; done = a
  restored run gives the same dumps as a straight run, from the title and from mid-movie.
- Full rebuild time and RAM, incremental regeneration, generated headers out of the PCH
  (RECOMPILER-IDEAS item 4) (`local/wt-build`, `srwz-build`; output in `local/wt-build-out`).
- Per-function log tracker off by default in Release, Release flags applied per config, cheap
  call-path fixes such as the overlay lookup's string (items 1, 13, 7-cheap) (`local/wt-perf`,
  `srwz-perf`).
- Guest names in the runtime, `PS2X_CALL_TRACE`, crash handler (item 2) (`local/wt-debug`,
  `srwz-debug`).
- Binding checker at generation time, report-only (item 3) (`local/wt-bindcheck`,
  `srwz-bindcheck`, or a Python tool on `tooling`).
- Next, Wyn's go-ahead (2026-10-06): once these merge with regress green, the
  generated-code changes one at a time behind a switch: resume into owner functions (item 5)
  first, then batched timer/IOP accounting, leaner per-instruction code, the typed stub layer.
- `tooling` (this branch) waits for merge into main behind the fast-forward worker's
  long-run/regress edits.

### Tooling: build lock and the one-command wall loop
- `tools/lock.ps1`: named cross-session locks in the main checkout's `local/` (worktrees resolve
  to the same file through git's common dir). `tools/build-game.bat` holds the `build` lock for
  the whole runner build (holder = the batch file's cmd.exe, so a crashed build leaves a stale
  lock that the next build takes over). Self-test passes.
- `tools/wall.ps1` + `config/game.json` (per-game paths, build steps, runner features,
  checkpoints) + `tools/gameconfig.ps1` + `docs/TOOLS.md`: build, run to a named checkpoint with
  dumps, contact sheet, PCSX2 comparison, regression score and log summary in one
  `local/runs/<name>/report.md`. Uses `PS2X_FASTFORWARD` / `PS2X_SNAPSHOT_LOAD` once the
  runner has them; refuses real-time runs over 120 s without `-Force`. Self-test passes; real
  run wallrun5 (checkpoint logo) verified by its contact sheet.
- Found on the way: the current runner shows a BANDAI NAMCO logo (ticks ~1100-1640) before
  BANPRESTO, which the run33 baseline lacks, and intro timing varies by up to ~600 ticks between
  runs; `regress.ps1` therefore FAILs the whole intro on the current runner. The baseline needs
  re-making once the BANDAI NAMCO screen is confirmed against PCSX2.

## 2026-10-06 (night)

### Where everything stands (handoff, 2026-10-06 ~19:10; pick up from here)
PS2Recomp `srwz` = ea87cd1 (all reviewed and merged: Walls 8-9, pad script, graphics-mode /
gparam, argument fixes, GS image block, PS2X_MUTE); parent `main` has every tool committed.
Regress baseline `local/runs/baseline` (from run33) is out of date: the current runner shows
the BANDAI NAMCO logo before BANPRESTO (correct, PCSX2 shows it at ~10 s) and intro timing
drifts up to ~600 ticks; remake it with `regress.ps1 -MakeBaseline` and widen the match.
Open work, each in its own place:
- Wall 10 (half-height title, High): worktree `local/wt-w10`, branch `srwz-w10`; see its
  commit messages for state. Next: presenter derives output size from SMODE2/DISPLAY.
- Wall 11 (pad never read, Medium): main checkout, uncommitted; notes in
  `local/runs/wall11-notes.md`. PCSX2 ground truth: `local/reference/pad-trace.txt` (pad
  state 0x59c210 reaches 0x63 at 6.8 s and stays; START at the title: 0x523930 0x02в†’0x48).
- Fast-forward (`PS2X_FASTFORWARD`, Medium): worktree `local/wt-ff`, branch `srwz-ff`;
  script edits to long-run/regress on main.
- Tooling thread (separate session): branch `tooling` (head 02df204: lock.ps1 + build lock in
  build-game.bat, wall.ps1, config/game.json, docs/TOOLS.md) waits to be merged into main
  once the fast-forward script edits are committed. That thread also does the safe
  RECOMPILER-IDEAS.md items (function log off, release flags, trace/names, binding checker,
  incremental regen); generated-code changes are approved by Wyn after those, one at a
  time behind switches.
Reference kit: `local/reference/` (NOTES.md has PCSX2 intro timing: movie 19.96-409.99 s,
title ~415 s, menu 1 s after START); save state `local/reference/states/pretitle_400s.p2s`;
first menu `local/reference/menu/pcsx2_420s.png`. PCSX2 automation runs from the private
config `local/pcsx2-auto` behind `local/pcsx2.lock`; builds behind `local/build.lock`.

### Wall 10 (open): the title screen is drawn at half height
run40: "PRESS ANY BUTTON" shows from vsync ~27,250, but the picture fills only the top 224 of
448 lines, squashed, with the purple nebula very faint (`local/runs/run40-title.png` vs.
`local/reference/title-press-any-button.jpg`). Dump 27608: SMODE2 0x3 (interlaced), DISPLAY
DH 223, 640x224 buffer, presented 640x224. On hardware a 224-line field-mode buffer fills the
screen; our presenter shows the buffer at its own height (the "DH stays h-1" deviation in
Wall 8). Also suspect: `sceGsExecLoadImage/StoreImage` (Stubs/GS.cpp:617/682) take the block
address times 8, so the game's save/restore of GS blocks 0x1a40/0x2080/0x26c0 around the movie
lands in the wrong place (docs/TITLE-PATH.md wall 1).

### Wall 9: the opening movie never ended (and run34 looked stuck)
The opening movie (`OPPRG.PSS`) is 6 min 29 s (11,685 pictures at 30 fps), not ~3 min. Two
problems:
- The player `FUN_0018b260` leaves its loop when guest `sceMpegIsEnd` (0x115d18,
  `return **(mpeg+0x40)`) is set; the game's own libmpeg `_getpic` sets that flag at sequence
  end, but the runtime's `sceMpegGetPicture` stub never wrote it, so the screen stayed black
  after the last picture forever (run37: pc 0x18b63c from vsync 25,351 on).
- run34's "stop" at 171 s was the window being minimized: raylib waits in `glfwWaitEvents`
  while minimized (no presents, no dumps, close ignored). The new watchdog showed it.
Fix (PS2Recomp 9f74600, reviewed): `sceMpegGetPicture` writes `sys->isEnd` (1 once the program
end is demuxed, all pictures served and the last one shown for its time; writes skipped when
sys is 0, bounds-checked); `FLAG_WINDOW_ALWAYS_RUN`; `PS2X_WATCHDOG=<s>` reports a stall (host
stacks, last 64 SDK calls, guest threads/semas/event flags; Windows x64 only; a report can
freeze the process, debugging only); `[sceSifSetDma]` log capped (`PS2X_SIF_LOG_LIMIT`).
Result (run40, 490 s): `sceMpegReset`, `sceCdStStop`, the title logo ~5 s after the last text
(as in PCSX2), "PRESS ANY BUTTON" from vsync ~27,250. Regress run41b: PASS.
Review notes left open: `sceMpegIsEnd`'s own stub uses a different end rule (needs CD-producer
EOF; decoder failure never sets isEnd for GetPicture readers): share one predicate; a movie
looped via `sceMpegReset` without a new CD stream ends at once.

Done alongside (all reviewed or self-tested, see CLAUDE.md for usage): `tools/refcompare.py`,
`tools/long-run.ps1`, `tools/regress.ps1` (baseline `local/runs/baseline` from run33; window
widened to В±75 ticks after boot-timing drift of ~45 ticks failed run41), `tools/stubaudit.py`
+ `docs/STUB-AUDIT.md` (7 confirmed argument bugs; note: a binding runs the Syscalls handler
before the Stubs one of the same name, so `sceSifSendCmd` lives in Syscalls/RPC.cpp:971),
`docs/TITLE-PATH.md` (code path from the movie to the first menu and its likely walls).
Pending merge: scripted pad input `PS2X_PAD_SCRIPT` (srwz-pad 7ad3080, reviewed), graphics-mode
stubs and the game's own gparam at 0x3f3ca0 (srwz-gparam, review changes being applied).

## 2026-10-06 (evening)

### Wall 8: glyph-shaped garbage over the logo and copyright screens
Three lines of white glyph fragments ("LMKIOWNвЂ¦") sat over the top of the Banpresto logo
and the copyright screen, plus a blob at the bottom right, identical on both screens and
surviving every fade. The first guess (indexed-texture swizzle or CLUT) was wrong: the
4-bit swizzle is right (unit sprite banks at 0x1400/0x1A00/0x2000/0x2600 and the copyright
picture decode cleanly), and no draw ever samples the font.

Cause: the game keeps its small font (digits, A-Z, `/-()`, 512x240) in the **upper byte of
its depth buffer**: it uploads it as PSMT4HL + PSMT4HH at block 0x1180 = ZBP page 140, right
after the two 640x224 frame buffers. That only works with PSMZ24, whose depth test ignores
the top byte. The game calls `sceGsSetDefDBuff(env, 0, 640, 224, ztest, zpsm, clear=1)` with
zpsm chosen from a config word (0x30/0x31/0x32/0x3a; Z24 here); arguments 5-7 arrive in
t0-t2, but the runtime stub read them from the stack, got 0 and set PSMZ32 with ZMSK on.
Every draw uses TEST 0x5000f (depth test GEQUAL) at z 0xFFFFFF, so with Z32 each font pixel
(0x0F000000 and up) failed the depth test and kept whatever white an earlier frame had
left there (the garbage pixels are 0x80FFFFFF: drawn white, never overwritten).

Fixing the zpsm uncovered a second hole: with Z24 the depth writes are on (ZMSK 0), and the
screen after the logo went black. The game never calls `sceGsSwapDBuff`; it sends its own
draw-environment packets, whose clear block comes from `sceGsSetDefClear`, which was an
empty runtime stub. So depth was never reset, the fade sprite's z 0xFFFFFF stayed, and the
copyright picture (sprite at z 0xFFFFF5) failed GEQUAL.

Fix (PS2Recomp `srwz`, Stubs/GS.cpp): `sceGsSetDefDBuff` reads ztest/zpsm/clear from t0-t2
and, with clear set, seeds both clear packets (giftag 14 registers, else 8) like libgraph;
`sceGsSwapDBuff` sends the seeded clear (as `sceGsSwapDBuffDc` already did; for other games);
`sceGsSetDefClear` fills the 12-doubleword `sceGsClear` exactly like the game's own libgraph
copy at 0x10ce20 (TEST ZTST ALWAYS, sprite from (x,y) to (x+w,y+h) at depth z in r,g,b,a,
TEST back to ztest; args 9-11 on the stack in 8-byte slots, checked at both call sites) and
returns 6. Result (run30, memory dumps): logo, copyright screen (now including the line the
garbage hid) and opening movie all clean.

**State (2026-10-06, late): reviewed, committed.** The change (Stubs/GS.cpp above, plus the
`PS2X_GS_DUMP` / `PS2X_GS_PRIM_UNIQUE` debug switches in gs/gs_cpu_backend.cpp) is committed
on PS2Recomp `srwz` and the submodule bumped. The runtime-reviewer asked for libgraph's
encoding in the seeded clear packets (Q = 1.0 in RGBAQ; depth test off only when ztest == 0)
before committing; done. Verified with run31 (dumps every 30 ticks from 1200 to 1920 plus
2820, `local/runs/run31dump/sheet.png`): logo, fade, copyright screen and opening movie
clean, no stray pixel where the glyphs used to be.

Review follow-ups (not blocking this commit; they matter for other games), most important first:
1. `decodeGsTrailingArgs2/3` and the "legacy" fallback in `decodeGsSetDefImageArgs`
   (Helpers/Support.h) read arguments 5-7 from the stack whenever t0-t2 are all zero. The EE
   calling convention never does that, and it breaks common 2D setups in other games.
2. `sceGsSwapDBuff` applies 8 register pairs plus the clear by heuristic; it should send the
   guest's giftag + NLOOP qwords through the GIF path and drop the extra
   `clearFramebufferContext` call.
3. `sceGsSetDefDBuff` still differs from the game's copy at 0x10cf28: FBP of the second
   buffer, DispEnv built through `sceGsSetDefDispEnv`, DTHE when psm & 2. Harmless for SRWZ
   (it rewrites those itself), wrong for other games.

Follow-up 1 **done** (PS2Recomp d85423e, reviewed): the decoders read arguments 5-8 only from
t0-t3; the stack fallback read sp+16..28, which are the caller's locals, not argument slots,
and the "legacy" LoadImage layout came from an upstream placeholder commit (8f74733) and
contradicted libgraph. Checked against the game's copies (DrawEnv 0x10cc38, LoadImage
0x10d570, StoreImage 0x10d758). run32 shows the same pictures as run31; unit test
`sceGsSetDefDBuffDc seeds a clear packetвЂ¦` now passes its arguments in registers.

Follow-ups 2 and 3 **done** (PS2Recomp 43c909b, reviewed): `sceGsSetDefDBuff` matches 0x10cf28
field by field (DispEnv through the `sceGsSetDefDispEnv` builder, DTHE = psm & 2, ZBP from the
stored gparam, second-buffer FBP, giftag hi 0xe, clear packets via the `sceGsSetDefClear`
builder and untouched when clear = 0, read-modify-write of the guest struct);
`sceGsSwapDBuff` applies disp[id] and sends the guest giftag + NLOOP qwords by GIF DMA (normal
mode, as libgraph's `sceGsPutDrawEnv`), no more `clearFramebufferContext`; `sceGsPutDrawEnv`
sends from the giftag (it was one qword off). New test `ps2xTest/gs_dbuff` (expected values
from `gen_expected.py`, a model of the decompiled code): 518/518, and 125 failures against the
old stubs. Visible in SRWZ: PMODE 0x8007 в†’ 0x66, DISPLAY MAGH 3 / DW 2559 / DY 50, as libgraph;
run33 pictures unchanged. Known deviation: DISPLAY DH stays h-1 in **frame** mode (libgraph
gives 2h-1 = 447 for SRWZ's `ResetGraph(0,1,2,1)`) because the presenter reads DH as the
buffer height. A game's DMAC channel-2 handler now fires on every swap, as on hardware.

Found on the way (open), most important first:
- `sceGszbufaddr` and `sceGsSetDefDrawEnv` (bound at 0x10cb70 / 0x10cc38) still use the old
  code: psm/w/h from a0/a2/a3 instead of a0-a2, and a gparam test that can never hit. In field
  mode they disagree with `sceGsSetDefDBuff`. Route both through the new `libgraphZbufAddr` /
  `libgraphSetDefDrawEnv`. SRWZ runs in frame mode, so its values agree today.
- The runtime never writes the game's own gparam copy at 0x3f3ca0 (1,2,1,3 in the ELF), which
  FUN_0016cd48 and FUN_0016d3e8 read; `sceGsGetGParam` returns a 4-byte struct where the SDK's
  is four shorts, in scratchpad.
- `sceGsSwapDBuffDc` still uses the old register-pair heuristic; `readGsDBuff`/`writeGsDBuff`
  copy 0x230 bytes without a range check at the end of RAM or scratchpad.
- `sceSifSendCmd` (Stubs/SIF.cpp:28-29, bound at 0x11e530) reads dest/size from sp+16/sp+20;
  the signature `(cid, packet, psize, src, dest, size)` puts them in t0/t1. Same family as
  Walls 7-8.
- `sceGsSetDefLoadImage/StoreImage` write a private 12-byte struct and return 0; libgraph
  writes the GIF packet (giftag, BITBLTBUF, TRXPOS, TRXREG, TRXDIR) and returns 6 / 7. Works
  only while `sceGsExecLoadImage/StoreImage` are stubs too.
- `ps2x_tests`: 435/437 pass. The two failures predate this work: "sceGsSyncVCallback runs
  вЂ¦ on its callback stack" still expects the top-of-RAM stack pool that Wall 5 moved to
  0x90000, and "VU0 macro mappings" wants `instructions.h` in its working directory.
- `PS2X_GS_DUMP_DIR` must be absolute (the runner starts in `local\game`) and must exist:
  the dumper neither creates it nor reports a failed write.

How it was found: new tooling, worth using on the next graphics wall:
- `PS2X_GS_DUMP=1500,1560` (+ `PS2X_GS_DUMP_DIR`) saves GS memory (4 MB, native layout),
  the display registers and the presented picture at the first frame at or after each
  VBlank tick (`gsdump_<tick>.bin/.txt/.rgba`). Timing drifts a little between runs: dump
  every 30 ticks across a stretch rather than at one tick.
- `tools/gsview.py DUMP --psm T4 --bp 0x2fc4 --bw 10 --w 640 --h 448 [--cbp вЂ¦] -o x.png`
  renders any region in any format (self-test: `--selftest`; `--map` shows used pages).
- `PS2X_GS_PRIM_UNIQUE=1` logs the first primitive of every distinct draw setup (type,
  texture, CLUT, frame, TEST, ZBUF) without the 64-line cap: the full list of what a screen
  draws, in a few lines.

### Wall 7: the movie decoded and uploaded at 30 fps but the window stayed magenta
Magenta is the runner's "no frame to present" colour. A diagnostic in the presenter
(`[gs:present:none]`) showed that during the movie PMODE held 0x895340: both CRT circuits
off, and a value that looks like a pointer, not a mode word. The game's writes to the
display registers never went through MMIO: the movie player builds a `sceGsDispEnv` on
its stack with `sceGsSetDefDispEnv(env, 0, 640, 448, 0, 0)`, patches the DISPFB field by
hand each frame (FBP 0 / 0xb4, FBW 12, DBX 40, DBY 16: the 4:3 window into a 720x480
picture), and calls `sceGsPutDispEnv(env)`; both are runtime stubs.

Cause: the runtime's `sceGsSetDefDispEnv` wrote only `dispfb` and `display` into the
struct; `pmode`, `smode2` and `bgcolor` stayed whatever the stack held, and
`sceGsPutDispEnv` applied them. The logo screen worked only because it goes through the
double-buffer stubs, which seed PMODE themselves.

Fix (PS2Recomp `srwz`): the stub fills all five fields like libgraph does: PMODE
EN1=0 EN2=1 MMOD=1 AMOD=1 SLBG=0 ALP=0xff, SMODE2 from the interlace/field mode given to
`sceGsResetGraph`, DISPFB(0, w/64, psm, 0, 0), DISPLAY in VCK units (MAGH = 2560/w - 1,
DW = w*MAGH - 1) at the NTSC/PAL origin, BGCOLOR 0. The runtime-reviewer pass (Opus)
decompiled the game's own libgraph copy and corrected the first draft: PMODE is the
literal 0x66 (ALP 0), MAGH rounds up, PAL's DX origin is 656, and arguments 5-6 (dx, dy)
arrive in t0/t1, which the old stub read from stack leftovers instead (DX was 0xdd0 where
the game passed 0). Known gaps left for other games: 480p, field-mode DH doubling, and
`sceGsPutDispEnv` writing both circuits where the SDK writes circuit 2 only. Result (run16,
run17): one present failure
at tick 0 instead of one per frame, and the opening movie plays in the window at 30 fps
(run16-47s.png). **First picture, 2026-10-06.**

Tooling added on the way: `tools\run-report.ps1`, `tools\screenshot.ps1`,
`tools\decomp.ps1`, the `[gs:present:none]` and `[gs:priv]` diagnostics.

### Wall 6: the opening movie ran at about one frame every 3 seconds
The game reaches the opening movie (`MPG/OPPRG.PSS`) about 34 s in, but the player
(`FUN_0018b260`) paces itself by spinning on timer 0 (`RCNT0_MODE = 0x83`, H-blank clock;
`while (RCNT0_COUNT < 500) feed_demux()`, about 1/30 s) and the frames barely came.

Cause (measured, run10 with the new `hostMs`/`eeMs`/`vsync` fields on the `[run:tick]`
line): PS2Recomp's guest clock advanced only by the fixed cycle charges at calls and loop
edges, never by wall-clock time. Before the movie guest time already ran at half speed
(1 s of guest per 2 s of host, 60 VBlanks per 2 s); inside the poll loop, which is a few
guest instructions around a runtime call, it nearly stopped: 20 ms of guest time and one
VBlank per 2 s of host. Everything timed (timers, VBlank, the IOP, CD streaming) crawled
with it.

Fix (PS2Recomp `srwz`, "EE scheduler: guest clock catches up with wall-clock time"):
every 32nd `accountCycles` tops `m_eeCycle` up to the host clock (capped at 250 ms per
top-up; beyond that the host stalled and the excess is dropped). The scheduler's wait
when the guest is *ahead* is unchanged, so this only ever moves the clock to "now".
`PS2X_REALTIME_CLOCK=0` restores the old behaviour. Result (run11): guest time tracks
host time within 20 ms, VBlank at 60 Hz, and the movie uploads 30 pictures per second
(the `gif` counter on the tick line: 60 per 2 s against 1 per 2 s before).

Note for traces: with `PS2X_TRACE_STUBS=1` the stub trace itself used to stop the movie
(run7: 45,792 `sceCdStRead` calls returning 0); with the real-time clock that effect is
much smaller but a full trace still costs host time. Judge progress from an untraced run
or a narrow trace filter. The runtime's own MPEG/CD stream logs are capped at 32 lines
each, so their counts say nothing about rate.

### Wall 5: main thread resumes at 0xb7fcc0 after a sound RPC
After the async loader reads COMPDATA.BN and two KURODATA files, the game binds RPC service
0x12345 (its sound driver on the emulated IOP) and calls `sceSifCallRpc(client, 0, 0, send,
0x800, recv, 0x800, end_func = 0x187a00, end_param = 0)` from `FUN_00187a10` (the 9th
argument is passed: `sd $zero, 0($sp)` in the `jal` delay slot, which Ghidra's decompiler
hides). Afterwards the main thread jumped to `0xb7fcc0` with ra = s1 = 0xb7fcc0.

Cause: PS2Recomp runs the end-callback (and interrupt handlers and alarms) on guest stacks
it carves downward from the top of RAM (first one: sp = 0x1fffff0). This game's crt0 calls
`SetupThread(gp, stack = 0x1f00000, size = 0x100000, ...)`, so its main thread's stack is
that same top megabyte. The callback body `FUN_00188470` opens a 0xA0 frame at 0x1ffff50
and saves s0 at +0 and s1 at +0x10, exactly over `FUN_00188b00`'s saved s0 (0x1ffff50) and
ra (0x1ffff60) in the waiting main thread. s1 held 0xb7fcc0 (an outer function's local,
the end of the COMPDATA buffer), so `FUN_00188b00` loaded ra = 0xb7fcc0 and returned there.
The game's interrupt handlers (VBlank-on at 0x140430, timer 2 at 0x126150) had been running
on that same stack before, over the main thread's outer frames.

Fix (PS2Recomp `srwz`, 6b5bef2 + 1ab8ceb): runtime callback stacks now come from 0x90000 up
to the lowest loaded segment (0x100000 here): kernel-side space on a real PS2, unused by the
game, above the SDK's kernel patches (libkernl copies code to 0x80075000/0x80076000 and
InitAlarm data to 0x82000; InitAlarm is runtime-bound here anyway). The top of RAM is only a
fallback, with a warning. `[ee:invocation-stack]` lines log each stack (run9: thread 1 at
0xffff0, thread 2 at 0xfbff0). Runtime-only change: the runner rebuilt in 3 minutes. The game
goes on to create its second thread, draw, flip display buffers and start the opening movie
(Wall 6).

Latent, same family: the runtime also keeps RPC packets, RPC server data, TLS and boot-mode
blocks at 0x1f00000-0x1f31000 (`Syscalls/Helpers/State.h`), the deep end of this game's main
stack. Harmless unless the stack grows past ~830 KB; move them if anything odd shows up there.

### Wall 4: all 12 IOP modules fail to load
PS2Recomp's IOP loader read `sceSifLoadModuleBuffer`'s address from EE memory, but in the
SCE SDK it is an IOP address (the game copies the IRX to IOP heap with sceSifSetDma
first). Now it reads IOP memory first. All 12 modules load and start ("SDR driver
version 4.0.1 (C) SCEI"); memory card, pad init and RPC binding follow.

## 2026-10-06

### Overlays: all 206 recompiled and linked
`flow.bin` (the game's flow controller) and the 205 other overlays all load at the same
addresses, so PS2Recomp, which knows one program, had no way to run them. Now:
`tools/overlays.py` recompiles each overlay (from its Ghidra map) and wraps it in its own
C++ namespace with a static initialiser that registers its 17,636 entry points (in all)
under its MWo3 name; PS2Recomp's `hasFunction`/`lookupFunction` fall back to
`ps2xOverlayLookup`, which reads the name in the header at the load address in guest
memory and picks that overlay's entry. (PS2Recomp's parallel single-file writer races:
"combined output completion queue is missing index N"; overlays are written serially.)

### Wall 3: module loading, garbage sizes, then a crash at 0x11111111
The game decompressed `MODULE.BN` into a buffer that overlapped its own input: the
analyzer bound newlib's `_malloc_r`/`_free_r`/`_calloc_r` to the runtime's allocator
but left `memalign` (0x19d058, misnamed `setlocale`) and `_realloc_r` (0x19fb28) as
game code; those edit newlib's block headers and free "spare" fragments, which with the
runtime's allocator frees the whole block. Bound both (`config/bindings.txt`). The
garbage module table also meant junk sizes for the IOP heap and, after a runaway
decompression, a corrupted timer list (the jump to `0x11111111`). Also bound by hand:
`sceSifLoadModuleBuffer` (0x124180), `sceSifFreeIopHeap` (0x123688),
`sceSifFreeSysMemory` (0x123610), public wrappers the analyzer didn't match.
Diagnosed with the new stub trace (`PS2X_TRACE_STUBS`).

### Wall 2: hang in sceSifInitRpc (0x11e7a8)
The EE waited for the IOP to set a SIF flag. PS2Recomp doesn't emulate the SIF at
that level; it replaces the SDK's SIF/RPC/CD/pad/mc/sound functions with its own, which
talk to its IOP emulator. The program is stripped, so they weren't recognised. Fixed
with the analyzer's SCE SDK database (988 functions named, 263 bound) merged with the
Ghidra map (`tools/make_config.py`). Never apply the analyzer's `[patches]` (they NOP
ordinary stores in this game).

### Wall 1: first boot
Recompiled (59,089 files) and built (14 min). Boots, sets up its heap, reaches the SIF.
