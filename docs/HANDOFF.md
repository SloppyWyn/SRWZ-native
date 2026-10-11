# Handoff

One page for a fresh session (no project memory). Read
`START-HERE.md` first, then this, `docs/TESTING.md`, the top of `docs/PROGRESS.md` (the
"STOPPED HERE" block is the exact end state) and `CLAUDE.md`.

## Where the game stands
- Boots, intro movie, title, menus, new game, scenario 1 dialogue, battle map, battles (prep, full
  animations, counters, experience, level-up), and now **scenario 1 to the end**: map won (turn 5-6),
  post-battle town scene, intermission menu, save to memory card slot 1, load back.
- Fixed today: Wall 19 (DMA chain walk cap), Wall 20 (IOP sound memory pool), Wall 21 (MMI `PMFHL.LW`
  dropped the high half of 64-bit products: soft-float multiply returned 0 and looped, black screen
  after the town scene). PS2Recomp `srwz` is at c5ee546 (Wall 21 merged).
- Speed: ~5 game frames/s on the battle map (VU1 3D pass interpreted); menus open ~1000 ticks after a
  press there. Tooling's VU1 interpreter work is on `srwz-vuint` (PROGRESS, tooling entry).

## Check first
- The main build was being rebuilt at the switch: `tools\verify-merge.ps1 -Regen -KeepGoing`
  (console `local\runs\verify-w21b.console.txt`, summary in the newest `local\runs\verify-*\`). If a
  step failed, fix that before anything else. Old snapshots only load if the snapshot layout is unchanged;
  re-save what you need on the new build.
- After the reinstall the recompiler tomls under `local\recomp\` pointed at the old Desktop folder;
  they are repointed to `C:\dev\SRWZ-native` (backups `*.toml.before-pathfix`). If any other tool
  still names `C:\Users\Wyn\Desktop`, repoint it the same way.

## Next steps
0. **VU1 speed first** (decided 2026-10-11, `docs/VU1-PLAN.md`): branch `srwz-vu1x` (worktree
   `local\wt-vu1x`, baseline runner `local\runs\bin-srwz-c5ee546`). Stage 0 counters
   (`PS2X_VU1_STATS=1`) reviewed and merged: srwz 5a15667, verify-merge PASS 2026-10-11; results in
   the plan. **microVU via kzvu works on the map: 3.1 -> ~40 frames/s** (PROGRESS top entry,
   srwz-vu1x 5b0e164, `PS2X_VU1_KZVU=1`). First, collect what moved since 2026-10-11: new commits in
   KillzoneRecomp (we have 5f07000; `ext/kzvu`), god-of-war-recomp (34cbe58), PCSX2 microVU (we have
   646df006a4; kzvu says which commit it verified), Sinan-Karakaya's upstream PR #254, upstream
   PS2Recomp (`tools\upstream-check.ps1`). Next: review + merge, snapshot save with the switch on,
   battle scenes, a controlled A/B, how kzvu/PCSX2 sources reach a fresh checkout. Fallback was: read the prior art
   listed in the plan (MrCoolTheCucumber's VU JIT and verify mode,
   Sinan-Karakaya/soyjxck whole-program VU1 compile, `local\ext\god-of-war-recomp` patches) plus
   Wyn's ChatGPT/Qwen fork research in `local\research\` if present; then the lockstep checker,
   then Stage 1. Extra is approved for the timing analysis.
   Before it: the main build was fixed on 2026-10-11 (PROGRESS top entry); check
   `local\runs\verify-vu1prep.console.txt` passed.
1. ~~Fresh-boot reload of the slot 1 save~~ done 2026-10-11, checkpoint `s01_end_save` registered
   (PROGRESS top entry; use DOWN+8 in title menus). Left: its PCSX2 pair, if the save can be put
   on PCSX2's card (otherwise note it).
2. Bisect the `s01_first_battle_anim` scene score drift (0.0 -> 14.0 on plain srwz 094f58a, limit 15):
   cheat loader, Wall 20 IOP pool or srwz-prof; real picture change or capture timing.
3. Owed PCSX2 comparisons: Riot Jarenchi, the Harmonica cannon, the enemy-phase counters.
4. Scenario 2 onward, same method; the battle-sandbox cheat (any unit next to any enemy) once Wyn says go.

## How to drive the game
- `local\runs\probe\step.ps1 -From <snap>.bin -Keys "CIRCLE@60,DOWN@400" -Wait 1500 -Name sN`
  (`stepx.ps1` adds `-Exe <runner>`): presses (hold 30), saves a snapshot at the end and exits, writes
  `local\runs\step_sN.png` and prints the cursor and live enemies from RAM. One step at a time, look
  at the picture, then the next. Long blind timed loops waste runs (presses arrive before menus open).
- RAM map: `config/srwz-ram.md` (map cursor 0x46bf40, unit table 0x5900f8 / 0xb0 per slot: name,
  square, on-map flag). Not yet checked in PCSX2.
- Snapshot chain of the end of scenario 1 (old build): `local\runs\probe\G_238950` (turn 5 start),
  `s5_244988` (Gunleon on target), `s13_260624` (Garrod on Gelava), `s35_348528` (town scene); fixed
  build: `w21_361500`, `i3_368400` (intermission), `i8_377600` (after the save), `i10_380600` (after load).

## Saved points
- Checkpoints: `local\reference\states\` (`s01_dialogue_window`, `s01_map_pre_menu`, `s01_map_menu`,
  `s01_enemy_phase`, `s01_battle_prep`, `s01_first_battle_anim`, `title_screen`; `s01_end_save` holds
  the memory card files and a snapshot, not yet registered). `tools\checkpoint.ps1 list|load|scene|verify`.
- Memory card of the runs: `local\game\mc0\` (now holds the slot 1 save).

## Rules (Wyn)
- `START-HERE.md` sections 3-6 (how he works, desk rules, testing, playthrough rules).
- GitHub: no pushes until he says "push". Commit locally; PS2Recomp changes on `srwz-*`, reviewed by
  the runtime-reviewer agent, then merged into `srwz`, then `verify-merge.ps1`.

## How Wyn likes reports
Short, plain words, no register names. Lead with the result. A screenshot or two at each visible step
with one line each. Before/after pictures for fixes.
