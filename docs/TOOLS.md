# Tools: the wall investigation loop

The tools in `tools/` are written for any PS2 game ported with PS2Recomp. What belongs to one
game (ELF, disc, runner and recompiler paths, build steps, reference shots, checkpoints) lives
in `config/game.json`; `tools/gameconfig.ps1` reads it and resolves every path against the
MAIN checkout (git's common dir), so the tools work the same from a worktree under `local/wt-*`.

## One wall, one report

    powershell -File tools\wall.ps1 -Name runN -Checkpoint title [-Recomp] [-Build] [-Compare] [-Seconds N] [-Env K=V,...]

1. `-Recomp` / `-Build` run the config's build steps (delete the recompiler output,
   `ps2_recomp`, `overlays.py`; then `tools\build-game.bat`, which holds the shared build lock).
   `-DryRun` prints the steps and the run plan without running anything.
2. Runs the game with no audio device (below) through `tools\long-run.ps1`, dumping GS state (`PS2X_GS_DUMP`) at the
   checkpoint's dump window; the run stops once every dump is in (or at the time limit, or on
   a stall). If the runner supports `PS2X_FASTFORWARD` / `PS2X_SNAPSHOT_LOAD` (the name is in
   the exe's strings, or the config says so), it fast-forwards and/or starts from the
   checkpoint's snapshot; without fast-forward it refuses runs over
   `runner.maxRealtimeSeconds` unless `-Force`. A checkpoint may pin its factor
   (`"fastForward": "1"` for screens that break under fast-forward; `-FastForward` overrides it,
   and a pinned factor is never refused as too long).
3. Contact sheet (`tools\dumpsheet.py`), comparison with the checkpoint's PCSX2 shot
   (`tools\refcompare.py`, side-by-side image), and with `-Compare` the regression score
   against `local\runs\baseline` (`tools\regress.ps1 -NoRun`), or against the checkpoint's own
   `"baseline"` dump folder (relative to the main checkout) when it has one: route checkpoints
   such as `scenario1` keep theirs (made with `tools\regress.ps1 -MakeBaseline -From <route run
   dump> -Baseline local\runs\baseline-<checkpoint>`).
4. Log summary (`tools\run-report.ps1 -NoRun`): clock and drift, VBlank/DMA/GIF rates, error
   lines, `[gs:present:none]`, watchdog stall reports, stderr categories.
5. Writes `local\runs\runN\report.md` (+ `sheet.png`, `side.png`, `run.txt`) and prints a
   10-line summary. `-NoRun -From <dump dir>` re-reports an existing run; `-List` shows the
   checkpoints; `-SelfTest` checks the config and the report path on an existing dump dir.

Read the summary first: a VBlank rate well under 60/s means the host was loaded (other builds
or runners) and the guest got less far than planned; boot timing alone drifts by up to ~600
ticks between runs, which is why dump windows are wide and why the regression check matches
on content in order rather than on tick (below).

## Starting processes: tools\launch.ps1 only

On Windows 11 a console process started without an inherited console (`Start-Process`,
`cmd /c start`, ps2EntryRunner.exe, cmake/MSBuild/cl, python, powershell) is handed to Windows
Terminal and opens a window in front of whoever sits at the PC. Every script here starts its
children through `tools\launch.ps1` instead:

    . "$PSScriptRoot\launch.ps1"
    $p = Start-Quiet $exe -ArgumentList a, "b c" -WorkingDirectory d -StdOut x.out.txt -StdErr x.err.txt [-Env @{K='V'}]
    Wait-Quiet $p -TimeoutSeconds 60 [-Kill]          # exit code, or $null on timeout
    $lines = Invoke-Quiet python @("tools\x.py", "a")   # like & python ...: stdout lines, $LASTEXITCODE
    powershell -NoProfile -File tools\launch.ps1 [-TimeoutSeconds N] -- tools\build-game.bat Release

- CreateProcess with `CREATE_NO_WINDOW`: the child gets a console without a window, and its own
  children (MSBuild, cl, java...) inherit it. stdout/stderr go straight into the given files
  (readable while it runs), stdin is NUL, only those handles are inherited. `Start-Quiet`
  returns a `System.Diagnostics.Process` (exit code kept), so `tools\runstop.ps1` stops the
  runner as before (`PS2X_STOP_FILE`, window close, kill).
- A child's own window (the game in windowed mode, PCSX2) is shown with `SW_SHOWNOACTIVATE`
  (`-Window Minimized`: `SW_SHOWMINNOACTIVE`; `-Position x,y` moves it without activation).
  raylib/GLFW activates the game window on show anyway, so the run scripts pass
  `-FocusGuardSeconds`: a background thread hands the foreground back to the previous window
  (within ~15 ms) until 5 s after the child's window appeared.
- `Invoke-Quiet -Echo` prints a long build's lines as they come, `-Stream` passes them down
  the pipeline, `-MergeErr` = `2>&1` (stderr never becomes a PowerShell error record).
- Python: `subprocess` with `creationflags=subprocess.CREATE_NO_WINDOW` (`tools\fpsrun.py`
  `quiet()` also sets `SW_SHOWNOACTIVATE`).
- `Get-VisibleWindows` / `Get-ForegroundHwnd` list the visible top-level windows and the
  foreground one, for checks; `powershell -File tools\launch.ps1 -SelfTest`.

## Sound: no audio device for unattended runs

Scripted runs are silent and open no audio device, so a stream of short runs does not switch
the host's sound devices on and off. run-game, long-run, regress, run-report and wall set
`PS2X_NO_AUDIO=1` (and `PS2X_MUTE=1` for runners older than PS2Recomp srwz-noaudio, which
only mute at volume 0) through `Set-RunnerAudio` in `tools\runstop.ps1`; `tools/fpsrun.py`
does the same. The runner then never calls `InitAudioDevice` (stdout `[audio] no audio device
(PS2X_NO_AUDIO)`; `PS2X_MUTE=1` and headless do the same); sounds are dropped and the guest's
timing is unchanged, since nothing in it waits on host playback. `-Sound` (`--sound` for
fpsrun.py), or a caller's `PS2X_MUTE=0`, opens the device with sound on.

PCSX2: `tools\pcsx2-auto.ps1` sets the private config's `[SPU2/Output] Backend = Null`
(PCSX2 2.8 values: Null, Cubeb, SDL; Null is "Null (No Output)" in its settings) before every
launch, so PCSX2 opens no audio device; `tools\pcsx2-capture.ps1 -Sound` puts back the backend
from the user's own PCSX2.ini (Cubeb if unset). `tools\appaudio.ps1` still mutes PCSX2's
session as a fallback.

## Regression check: content in order

`tools\regress.ps1` (and `python tools\refcompare.py --dirs <run dump dir> <baseline dir>`)
does not compare a dump with the baseline frame at the same tick. Each run dump is classified
against ALL baseline frames:

- `screen`: mad <= `-Threshold` (10) on a 64x48 grey thumbnail AND normalised correlation
  >= 0.8 on a 128x96 one (mostly-black screens with a line of text are all within a few mad of
  each other; the correlation tells the texts apart), or correlation >= 0.98 at any mad (the
  same screen at another brightness: a fade). Blends of baseline frames <= 90 ticks apart count.
- `blank`: uniform grey/black/white. A wildcard (no order constraint), but a blank stretch
  longer than `-MaxBlank` (900) ticks fails: a hang to black.
- `movie`: no screen matches but the picture is rich (luma entropy >= 3 bits; text and logo
  screens sit at 0.5-2.5); it may stand for any baseline movie frame (a rich baseline frame
  that matches neither neighbour). If BOTH runs logged `PS2X_MPEG_HASH=N` (`[MPEG:hash]
  picture= tick= fnv=` on stdout; the baseline keeps them in `mpeghash.txt`), a dump inside a
  logged playback gets the exact baseline tick of the same picture instead, and the hashes of
  the pictures both runs logged must agree (else FAIL).
- `anim`: an unplaced dump between two placed ones that bracket a dynamic baseline frame
  (an animation caught at another phase), or inside the movie.

The matched baseline ticks must not go backwards in run order (repeats and skips are fine): the
longest such chain is kept (most dumps, then least mad). FAIL = no match anywhere, or a match
out of order. EDGE (not counted) = before the chain when the chain starts at the baseline's
first screen, after it when it ends at the baseline's last, or in a stretch the baseline never
sampled (frames more than 180 ticks apart). Output per dump: status, tick, class, mad, matched
baseline tick, reason; then the verdict. `-Strict` gives the old tick-window scoring.

Known blind spots: content the baseline never shows (before its first dump, after its last, or
in an unsampled stretch) is not judged; without hash logs any rich picture passes inside the
movie stretch, and a dark text card in the movie passes as `anim`; a whole screen drawn at the
wrong brightness passes as a fade. A dump `none` at the very start is EDGE only when a later
dump matches the baseline's first screen.

## Setting up a new port

1. Copy `config/game.json` and fill in `paths` (ELF, disc, workdir, runner, recompiler,
   recompiler TOML and output, runs, reference, baseline, snapshots) and `build.steps`.
2. First run: `tools\long-run.ps1 -Name cal1 -Seconds 90 -DumpEvery 60` and look at the sheet
   to find where the fixed screens fall (vsync ticks of OUR runner, not emulator frames).
3. Capture PCSX2 reference shots of those screens (`tools\pcsx2-capture.ps1`) into
   `local\reference\`, and add one checkpoint per screen: `tick`, `dump` window
   (`from`/`to`/`every` or `ticks`), `reference`, optional `snapshot` `{file, tick}`, `notes`.
4. Make a regression baseline from a good run (`tools\regress.ps1 -MakeBaseline -From ...`).
5. From then on, every wall: `tools\wall.ps1 -Name runN -Checkpoint <screen> -Compare`, read
   `report.md`, fix, repeat with `-Recomp -Build` as needed.

## Pad routes: no hand-written tick lists

`tools/route.py` turns named routes into a `PS2X_PAD_SCRIPT` string. Per game: `pad` in
`config/game.json` (`hold`, `gap`, `macros`, `routes`; a route may say `verified` (the run that
showed it) or `"verified": false`, and `reaches`) plus route files `config/routes/<name>.txt`
(one step per line, `#` comments, a `# unverified` line marks it). Steps: `START`, `UP|CROSS`,
`CIRCLE+12` (press, hold in ticks; default `pad.hold`), `wait N` (ticks after the previous press),
`at N` (absolute VBlank tick), `3000:START+8` (a literal entry), a macro/route name (`confirm+12`
holds every press in it 12 ticks), `<step>*N` (repeat). Presses with no wait between them are
`pad.gap` ticks apart. Ticks are guest VBlanks, so a route plays the same at any fast-forward.

    python tools/route.py select_scenario            -> 3000:START+8,3400:START+8
    python tools/route.py "select_scenario, wait 300, confirm" --explain   (timeline on stderr)
    python tools/route.py --list | --selftest
    powershell -File tools\long-run.ps1 -Name r1 -Seconds 90 -DumpEvery 60 -Headless -Route new_game
    powershell -File tools\wall.ps1 -Name r2 -Checkpoint select_scenario   (the checkpoint names its route)

`-Route` takes a name, a route file, `@file` or inline steps; on wall it overrides the
checkpoint's `route`. An inherited or `-Env` `PS2X_PAD_SCRIPT=@<file>` is expanded by long-run,
wall and fpsrun before launch (the runtime itself takes only the expanded string). Unverified
routes print a warning; once a run's sheet shows the screen, set `verified` to that run's name.

## Unattended runs (no monitor, overnight)

The runner needs no window: `PS2X_HEADLESS=1` (or `-Headless` on run-game, long-run, regress,
run-report and wall) runs it without a window, GL context or audio device, and a desktop build
that finds no monitor at startup switches to headless by itself (`[run] no monitor: headless`
on stdout). GS dumps, the pad script, the watchdog and the `[run:tick]` lines work the same;
window captures (`run-report.ps1 -Shots`) do not, so use dumps. The scripts stop the runner
through `PS2X_STOP_FILE` (`tools\runstop.ps1`: the runner exits cleanly, stdout flushed, once
the file exists) plus a window close for older windowed builds; a forced kill is the last resort.

## Performance measurements (A/B)

A speed claim ("X costs fps") needs a controlled A/B; single runs on this shared machine
disagree by 30-50 % (other sessions' runners and builds, SMT siblings, the game's own frame
pacing). Protocol:

- Two runners that differ only in the thing under test (same PS2Recomp revision and toml
  otherwise; the variant regenerated into a scratch folder and built into its own build dir,
  never `local\recomp\output` / `overlay_build`). Check the generated code really differs (e.g.
  `ps2_runtime_lean_macros.h` included or not).
- Nothing else running: check `tasklist` for `ps2EntryRunner.exe`, `cl.exe`, `link.exe` before
  every run, and hold `local\build.lock` for the whole measurement
  (`tools\lock.ps1 -Acquire -Name build -Owner abtest -HolderPid <driver pid>`, release at the end)
  so nobody builds meanwhile. A run during which another runner or compiler appeared is
  discarded and redone.
- Alternate the builds A, B, A, B, ... with at least 3 rounds each, same route and settings:
  headless, `PS2X_NO_AUDIO=1`, `PS2X_TRACE_STUBS=scePadRead` (pad reads/s = game fps), and either
  fpsrun's START-at-3000 route (`PS2X_PAD_SCRIPT=3000:START+8`: the movie skip lands straight on
  the title with its menu; title = the logo/menu fade-in, ~0.5-5 s after the press, menu = from
  10 s after it) or a title snapshot per build (snapshots load only into the build that wrote them).
- Report per run and window: game fps, VBlank/s (from `[run:tick]`), and EE-thread cycles per
  vsync (`QueryThreadCycleTime` delta of the busiest thread in the window / vsync delta) with
  its saturation (thread CPU time / wall time). Cycle counts are the steadier measure of work;
  only trust them as such on runs with EE saturation >= 0.98 (a CPU-bound EE thread). CPU
  seconds on this shared machine carry ~10 % noise, and SMT siblings running compilers inflate
  cycle counts too.
- Game fps is bimodal in the title/menu: whole stretches at ~59 fps, others at ~30-35, in either
  build, at random. Compare means over several runs and look at the per-10 s series before
  reading a difference into a mean.
- Earlier single numbers (menufps2 45.3, menufps4 32.1, quiet-mode menufps3 21.5 game fps) were
  taken under load and are not comparable with each other or with a controlled A/B.

Reference example (2026-10-07, lean_codegen on vs off, PS2Recomp srwz 3d86345, resume_into_owner
on in both; driver and sampler in `local\abtest\` (`abdrive.ps1`, `abrun.ps1`, `analyze.py`),
3 clean rounds each, VBlank 60.0/s in every run):

| | title fps | menu fps | menu Mcycles/vsync | menu EE saturation |
|---|---|---|---|---|
| A lean on | 38.4 (sd 9.7) | 42.1 (sd 10.9) | 60.8 | 0.87 |
| B lean off | 40.0 (sd 13.4) | 37.3 (sd 3.8) | 60.6 | 0.87 |
| A/B | 0.96 | 1.13 | 1.004 | |

Verdict: no measurable cost; the fps spread is the 30/60 pacing flip, not the build (in 60 fps
stretches both builds run 63.4 Mcycles/vsync). The EE thread sat at 0.85-0.90, below the 0.98
bar, so the cycle figure here bounds the work rather than measuring it exactly.

## After a merge: tools\verify-merge.ps1

    powershell -File tools\verify-merge.ps1 [-Regen] [-Quiet] [-Skip 3,fps] [-KeepGoing] [-DryRun] [-SelfTest]

One command, one summary line per step (PASS/FAIL/SKIP, key numbers, log path) in
`local\runs\verify-<time>\summary.txt`; it stops at the first hard failure (`-KeepGoing` runs on):
0 waits until no runner from the main build path runs and takes the build lock; `-Regen` runs
make_config.py, ps2_recomp and overlays.py under it; 1 `tools\build-game.bat`; 2 builds
`ps2x_tests` and runs it in a scratch folder (pass: failures <= `-MaxTestFailures`, default 0);
3 `regress.ps1 -Headless -Seconds 60`; 4 re-saves the title snapshot (`PS2X_SNAPSHOT_SAVE/AT` at
`checkpoints.title.tick`, fast-forward from the config) and loads it once before replacing
`local\runs\snap\title.bin`; 5 `fpsrun.py --route title_menu` headless under the A/B rules (waits
until no other runner, cl or link runs, holds the build lock, redoes a run once if one appeared),
with its own thread-cycle sampler: title (0.5-5 s after START) and menu (from 10 s) game fps,
VBlank/s, busiest-thread Mcycles/vsync and saturation. One run is not an A/B: the line is a sanity
check (no stall, fps in the usual range), not a speed claim. `-Quiet` runs everything at
BelowNormal on half the logical processors (affinity, inherited by every child), so its fps are
not comparable with a normal run. The script holds the build lock while build-game.bat runs: a
holder may set `SRWZ_LOCK_HELD_BUILD=<its pid>` so its children's `lock.ps1 -Acquire` passes
through (`tools\lock.ps1` nesting).

## Review pack for a branch

`powershell -File tools\review-pack.ps1 -Branch srwz-foo [-Logs run.out.txt,tests.txt]` writes
`local\runs\srwz-foo\`: `diff.patch` and `stat.txt` against the merge base with `srwz` (`-Repo main`:
this repo against `main`; `-BaseBranch`/`-Base` override), `commits.txt`, `tests.txt` (lines such as
`Passed: N`, `differ 0`, `flagged 0` from the branch's `NOTES-*.md` / `docs/*NOTES*.md` and the `-Logs`)
and `brief.md`: branch, base, commits, files by area, test evidence and the runtime-reviewer
checklist, to fill in before handing the branch to review. A branch already merged is packed
against the base branch as it was before the merge (its reflog). `-SelfTest` packs an unmerged
runtime branch and `srwz-typed2`. Only git and text: game-agnostic.
