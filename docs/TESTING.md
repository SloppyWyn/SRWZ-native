# Testing and save states

How we test the port, and how we manage the save states that make testing cheap. Wyn's
rules (2026-10-08), adjusted to what the tools actually do.

## 1. Checkpoints: named game moments, paired on both sides
A checkpoint is a game moment, not a time: `s01_dialogue_window`, `s01_map_menu`,
`s01_battle_prep`, `s01_first_battle_anim`, ... Each lives in `local\reference\states\<name>\`:
- `snapshot.bin`: our runner snapshot (format 8, durable across builds whose sections match).
- `pcsx2.p2s`: the PCSX2 save state of the same moment (when we have one).
- `reference.png`: what the moment should look like (a PCSX2 capture; or our own frame when the
  moment is random on both sides, e.g. a battle line, and the checkpoint is a regression guard).
- `manifest.json`: how it was reached (keys and ticks), tick, EE cycle, runner build, files.

`tools\checkpoint.ps1 list|info|save|load|verify|scene|pcsx2` manages them (see CLAUDE.md).
Comparisons are only valid at the same game moment on both sides.

## 2. Save on arrival
- When a run reaches a new meaningful point, save a snapshot there before anything else
  (`PS2X_SNAPSHOT_AT` in the run, or `checkpoint.ps1 save <name> -At <tick> -From <checkpoint>`),
  and a PCSX2 state of the same point (hotkey F1 = slot 1, posted by `pcsx2-capture.ps1 -Keys`;
  copy it out of `local\pcsx2-auto\PCSX2\sstates\` at once, the next F1 overwrites it).
- Before any replay longer than a minute, look for a nearer checkpoint or make one. Replay from
  the title only to test the boot path itself.
- Drive PCSX2 from its save states, not from timed keys after boot: timed keys go astray when
  the PC is busy or Wyn is using it (the w19dump capture ended in the tutorial instead).

## 3. Snapshot durability
Format 8 snapshots load into any later build whose sections and raw layouts match; format 7 ones
load by struct sizes. After a runtime merge, re-run the scene checks (regress does it); re-save a
checkpoint only when it is refused or its scene changed for a good reason.

## 4. Determinism
`checkpoint.ps1 verify <name> [-Frames N]` runs the snapshot twice with `PS2X_DETERMINISTIC=1`
and compares per-VBlank hashes; a difference is missing state in the snapshot (a bug). With the
idle-jump fix (srwz 19e1b28) s01_dialogue_window stays identical over 3000 VBlanks. Scene checks
run in deterministic mode, so a reference taken from our own frame must be re-taken when a merge
changes timing.

## 5. Test ladder, fast to slow
1. `ps2x_tests` (seconds): every runtime change.
2. `tools\regress.ps1 -Name x -Headless` (~10 min): the boot dumps against the baseline, then
   every scene in `config\game.json` `regressScenes`. Required before a runtime merge.
3. Scene checks on their own: `checkpoint.ps1 scene <name>` (a minute each).
4. Soak and playthrough runs (hours): before milestones only.

Every fixed wall adds a scene check at its checkpoint to `regressScenes`, so it cannot come back
unnoticed.

## 6. Diagnosis loop
Reproduce from a checkpoint, take the paired PCSX2 reference, probe (draw logs, `PS2X_GS_PROBE` on
srwz-probe, a PCSX2 GS dump read with `tools\gsregs.py`), form one hypothesis, fix, run the scene
check, then regress. Before trusting a tool's output for a conclusion, check it on a known-good
case: gsview's old 4-bit palette rule cost an afternoon on Wall 19.

## 7. Playthrough states
Checkpoints at every scenario start and before every battle, so a coverage pass (every attack and
spirit used once) can be retried without replays. Note random differences (dialogue lines,
hit/miss) between our port and PCSX2: compare rendering, not outcomes.
