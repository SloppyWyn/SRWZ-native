# Playing the game through (plan only)

How we test the whole game, scenario by scenario, without anyone replaying it by hand. Nothing
here is built yet beyond what is marked. It is generic for any PS2 port on this runtime: every
game-specific address, macro and checkpoint lives in per-game config, not in the tools.

SRW is about the friendliest kind of game for this: turn-based, menu-driven, no reflexes needed.

## Stages
1. **Scripted pad macros.** The runner already plays a pad script (`PS2X_PAD_SCRIPT`, how runs
   reach the first menu). Next: named, reusable macros (open menu, confirm dialogue, move a unit
   to a tile, end turn) defined per game in config and composed into routes, so a route reads
   like "new game, skip intro, scenario 1 deploy, end turn".
2. **RAM-state reader.** A tool that reads game state straight from guest memory instead of
   screenshots: scenario id, turn, phase, unit list with position, HP and EN, menu/cursor state,
   money. Addresses per game in config (for SRWZ a `config/` state map; seeded from the
   retro-trans notes in `docs/GAME-FORMATS.md` and verified against our ELF like the names were).
   Works on our runner (a debug read interface or the snapshot/dump path) and on PCSX2 through
   PINE (`tools/pine.py`), so both sides report the same fields.
3. **Differential runs against PCSX2.** The same input script runs in PCSX2 and in our port;
   the RAM-state fields and the screens (content-order matching, as in `tools/regress.ps1`) are
   compared step by step, and the first divergence is reported with both states. This is the
   main payoff: bugs found automatically, with the exact point where they start.
4. **Saves as per-scenario checkpoints.** Each cleared scenario leaves a memory-card save (and a
   runner snapshot for the same point), so testing scenario 30 never means replaying 1 to 29. A
   clear save from Wyn's own copy lets us start at late chapters directly. Snapshots are tied
   to one runner build; memory-card saves survive rebuilds, so they are the long-lived
   checkpoints.
5. **Input recorder.** When Wyn plays a stretch in our port (or PCSX2), the runner records
   the pad input with vsync ticks into the pad-script format, so his session becomes a replay
   test rerun after every change (with the RAM-state reader checking it ends in the same state).

## Developer cheats (to reach test targets fast)
Route map, branch conditions, known codes and addresses for SRWZ: `docs/ROUTES.md` (web research, unverified until checked against our ELF and RAM).
- Memory pokes behind an env var and per-game config (e.g. `PS2X_CHEATS=<file>` with
  `address=value` lines applied every frame or once): stats, money, invincibility, scenario
  win/clear flags. Seeded from known PCSX2 codes for this disc where they exist (converted from
  their encrypted/raw form to plain addresses, verified against our RAM-state map).
- A runner snapshot at every scenario start, so a run can jump to any point; fast-forward between.
- **Rule:** cheats get a run *to* the thing under test, never *through* it. A battle or scene a
  cheat skipped counts as untested; reports name every cheat that was active.

## Coverage checklist (Wyn, 2026-10-07: no rushing)
Unit join/leave points and conditional units for ordering the checklist: `docs/ROUTES.md` section 8.
Every unit's skills get used at least once. The runs fill in a per-game checklist (generated from
the game's unit/attack/spirit data once its format is documented, ordered by when each unit
joins):
- every unit, every attack (combination attacks included), every spirit command;
- an item is ticked only when its animation played in full and the result (screens, and the
  RAM-state fields: damage, HP/EN, flags) matched PCSX2 on the same inputs;
- reports show coverage percent and list what is still unticked.
Cheats may bring a unit into a battle quickly; the attack itself always runs for real.

## Watching
Wyn can watch the playthrough three ways, none of which opens anything uninvited:
- **Live.** A batch file he starts himself (like the sound-check one) opens the game window and
  plays a route of macros in it, sound on, at normal speed.
- **Video of every run.** Headless runs already save frames; each run (or each scenario) is
  encoded to an MP4 with sound from its presented frames and the audio output (ffmpeg, which the
  runtime already ships), and kept with the run's report, to watch whenever he likes.
- **Replays.** Recorded inputs played back in the game window from a save or snapshot. This needs
  the port to be deterministic from a checkpoint (same inputs, same result), which the
  checkpoint and differential stages need anyway.

## Who does what
- Routine progress (menus, dialogue, simple map moves) runs on macros and the state reader, cheap
  to run unattended.
- Claude steps in only where a choice matters (a hard battle, a route split), using the state
  reader's numbers rather than screenshots.
- Wyn's own play sessions and clear saves are welcome inputs, never a requirement.

## Order
After the game is playable from the first menu into scenario 1 on our runner. Stage 2 (state
reader) and stage 5 (recorder) are useful early for debugging walls and can come first.
