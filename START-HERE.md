# START HERE

The single entry point for a fresh Claude Code session: everything it needs is in this folder.
Private notes for this PC are in `local\DESK-RULES.md` (not in git): read them too.

## 1. First prompt (paste this)

> Read START-HERE.md, then docs/HANDOFF.md, docs/TESTING.md, docs/PROGRESS.md, CLAUDE.md, then continue.

`docs/PROGRESS.md` is newest first and is the truth when this page and it disagree.

## 2. What this is, and where it stands

A native Windows port of Super Robot Taisen Z (PS2) by static recompilation: the game's MIPS code
is translated to C++ ahead of time (PS2Recomp, submodule `third_party/PS2Recomp`, our branch
`srwz`), and a runtime stands in for the PS2 hardware. PCSX2 is the reference we compare against.

| Stage | What | State |
|---|---|---|
| 1-6 | Boot, intro movie, title, menus, new game, scenario 1 dialogue, map, battles | Done |
| 7 | Speed | Map 3.1 -> ~40 frames/s with PCSX2 microVU (`PS2X_VU1_KZVU=1`, branch `srwz-vu1x` 5b0e164, not merged yet). **Next job:** review + merge, snapshots with it on, battle scenes (`docs/HANDOFF.md`, `docs/VU1-PLAN.md`) |
| 8 | Scenario 1 to the end | Done: map won, Wall 21 fixed, intermission, saved to slot 1, reloaded from a fresh boot, checkpoint `s01_end_save`. Left: owed PCSX2 comparisons (PROGRESS) |
| 9 | Playthrough | Per `docs/ROUTES.md` and `docs/PLAYTHROUGH.md` |

## 3. How Wyn works

- **Leads with the goal, then lets you drive.** He says "take the control and lead": plan, act,
  and ask only for real forks or for things only his hands can do (GitHub clicks, PCSX2 at the
  keyboard). He interrupts with a short redirect when priorities change; follow it at once.
- **Decides fast once the trade-off is clear.** Give the options in plain words with numbers and
  your recommendation first; he picks, often within a minute.
- **Brings outside research.** He runs his own searches with other AI tools in parallel and drops
  the results in `local\research\` (the microVU lead came that way): read them, check the claims,
  credit them.
- **Wants it to check out.** Every claim in public text must match the logs; credit is shared
  exactly as earned, his own included. Mistakes get owned, not hidden.
- **Open by default.** The project is public "the way KillzoneRecomp does it": share findings,
  credit others, keep only his private details out.
- **Learns as he goes.** He says when something is new to him: explain it simply, one step at a
  time, with the exact buttons to click.
- Short plain messages. Lead with the result, a number that proves it, and a few screenshots
  (ours / PCSX2 / difference) with one plain line each.
- A setback gets one honest line. Strike through a claim you withdraw; don't silently rewrite it.
- Nothing is a "root cause" or "breakthrough" until a fix or a decisive test confirms it.
- Speed claims only from an alternating A/B against plain `srwz` (3+ rounds, `docs/TOOLS.md`
  "Performance measurements"), and only when the gain beats the round-to-round noise.

## 4. Desk rules (he works at this PC while we run)

- Nothing opens in his face: start every process through `tools\launch.ps1` (no console window,
  no focus steal), at BelowNormal priority. Runs are silent (`PS2X_NO_AUDIO=1`).
- Work continues while he uses the PC; pause anything that drives a window when he takes it.
- Never touch Wyn's other apps and projects: they are listed in the private `local\DESK-RULES.md`.
- Never kill by image name. Stop only your own process tree; `tools\reap.ps1` cleans leftovers.
- `.claude/settings.local.json` is his: never edit it. Don't work around a permission prompt.
- GitHub is OFF until he says "push" (last pushes: main 46dd575, srwz 3ebec88). Commit locally:
  main repo on `main`, PS2Recomp changes on a `srwz-*` branch, reviewed, then merged into `srwz`.
- Never commit game data, the BIOS or PCSX2 (`disc/`, `bios/`, `PCSX2/`, `local/` are private).

## 5. Testing, in brief (`docs/TESTING.md`)

- Checkpoints paired with PCSX2 (`tools\checkpoint.ps1`); save a checkpoint on arrival at any new point.
- Drive input from RAM state, not blind timed keys; one key at a time from a save.
- PCSX2 at `-Turbo` (2x). Cap logs (`PS2X_GS_LOG_LIMIT`, `run-report.ps1`, `log-triage` agent).
- Before any runtime merge: `ps2x_tests`, `tools\regress.ps1` with the `regressScenes`, and
  `checkpoint.ps1 verify -ExeB` for changes meant to be exact.

## 6. Playthrough rules

- "We do not rush." Every unit's attacks (combination attacks included) and every spirit command
  get used once and compared with PCSX2.
- Cheats bring a run to a test point and never skip one; the same `.pnach` drives both sides
  (`PS2X_CHEATS`, PCSX2 cheats folder; CLAUDE.md "Test cheats").

## 7. Open PS2Recomp branches

| Branch | State |
|---|---|
| `srwz-vu1x` (worktree `local\wt-vu1x`) | VU1 translator plan (`docs/VU1-PLAN.md`). Stage 0 counters merged into `srwz` (5a15667, 2026-10-11). Next stages go on this branch |
| `srwz-vuint` (worktree `local\wt-vuint`) | Parked 2026-10-11 for the translator plan (`docs/VU1-PLAN.md`). 89c9b21 step 1 (x1.05, inside the noise, exact on 3 scenes); 056576e step 2 pipeline bitmasks (WIP, A/B pending). Next: step 3, FMAC flag path and per-pair case pointers. Then report VU1's share of main-thread time |
| `srwz-w21` | Merged into `srwz` (c5ee546): Wall 21 fix (PMFHL.LW / MMI HI-LO moves). Keep for history |
| `srwz-vuaot` b08cea7, `srwz-vuop` | Parked: AOT VU1, slower (x0.81). Keep, don't delete |
| `srwz-cycles` | Parked (instruction-count cycle charges, off by default) |
| `srwz-typed3` 0fc62af | Parked for review (typed stub ABI) |
| `srwz-probe` | Debug only, never merge |

Test harness for the VU1 work: the frozen baseline runner is `local\runs\bin-srwz-094f58a`; each
step is checked by `checkpoint.ps1 verify -Exe <baseline> -ExeB <new>` on `s01_map_menu`,
`s01_dialogue_window`, `s01_enemy_phase`, then a 3-round alternating A/B of EE CPU ms over 300
deterministic VBlanks from the `s01_map_menu` snapshot.

## 8. Parked ideas (only when Wyn says go)

Battle sandbox cheat (after scenario 1), native modern fonts, mouse cursor, the SRW Z Special Disc,
distribution (`docs/DISTRIBUTION.md`: no BIOS, disc data or PCSX2 code shipped).

