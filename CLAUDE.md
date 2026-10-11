# SRWZ-native — project notes for Claude

A native PC (Windows) port of **Super Robot Taisen Z** (PS2, `SLPS-25887`, VER 1.04) by
static recompilation: the game's MIPS code translated ahead of time into C++, with a
runtime standing in for the PS2 hardware and system. Owner: Wyn. Started 2026-10-06.
The PS2 CPU is not x86, so every instruction is translated instead of run as is.
**Desk rules for this PC (other apps and projects that must never be touched) are in the private
`local\DESK-RULES.md`: read it first.** Never name other projects or apps in committed files.

## Rules
- **Open project (Wyn, 2026-10-11), the way KillzoneRecomp does it.** Two public repos:
  https://github.com/SloppyWyn/SRWZ-native (remote `public` here, branch `main`) and the fork
  https://github.com/SloppyWyn/PS2Recomp (remote `public` in the submodule, branch `srwz`). They
  started from one clean commit each (7db1914, c32b589); the full working history stays in the
  **private** backups: this repo `main` to `origin` = https://github.com/SloppyWyn/SRW-Z-Native and
  PS2Recomp branches to the submodule's `backup` = https://github.com/SloppyWyn/S2Recomp-srwz,
  pushed after each merge. Claude may commit as often as useful; commit straight to `main`.
- **Publishing an update** (when Wyn says "publish"): new public commits whose tree is the
  private branch's tree and whose parent is the previous public commit
  (`git commit-tree <private>^{tree} -p public/<branch>`), PS2Recomp first, then this repo with
  `.gitmodules` pointing at the public fork and the gitlink at the new public PS2Recomp commit.
  Before every publish: grep both trees for Wyn's other apps and projects (`local\DESK-RULES.md`),
  his real name, game files, `local/` and secrets; check a fresh `git clone --recursive`.
  Author name in both repos is `Wyn` (git config). Never publish unreviewed PS2Recomp branches.
  Never push anything taken from the game, public or private.
- **Never commit game data, the BIOS or the emulator.** `PCSX2/`, `bios/`, `disc/` and
  `local/` are Wyn's private files and are git-ignored; `.githooks/pre-commit` also
  refuses disc images, ELFs, IRX/BIN files, `SLPS_*`, `SCPH*` and anything over 5 MB,
  even when forced. After a fresh clone: `git config core.hooksPath .githooks`.
  Anything unpacked from the disc goes under `local/`.
- **Ask before downloading or installing anything** (Wyn's request).
- Standing permissions and rules (Wyn, 2026-10-06):
  - Change any setting needed to keep going (PCSX2's included) without asking; back the file
    up first and tell him what changed. Moving or resizing windows is always fine.
  - Game and PCSX2 runs are silent unless sound is needed, and open no audio device at all
    (no device switching on/off per run): the run scripts set `PS2X_NO_AUDIO=1` (plus
    `PS2X_MUTE=1` for older runners; `-Sound` opts out), PCSX2's private config has
    `[SPU2/Output] Backend = Null` (`-Sound` on pcsx2-capture restores his backend).
    Never route sound to his default device.
  - If he takes the mouse/keyboard or a window while a script drives PCSX2 or the game, let
    him: pause, wait until he is idle, then resume; treat affected shots as suspect.
  - Go wide with parallel agents and tooling whenever it speeds the work up.
  - **Start processes only through `tools\launch.ps1`** (no console window, no focus steal; Wyn
    works at this PC): `. tools\launch.ps1` then `Start-Quiet exe -ArgumentList ... -StdOut f -StdErr f`
    / `Invoke-Quiet exe @(args)`, or `powershell -File tools\launch.ps1 -- exe args` from a shell or .bat.
    Never `Start-Process`, `cmd /c start`, or a console exe started from a shell without a console.
  - **Stop only your own processes** (2026-10-08, after a session killed every MSBuild on the PC):
    the pid you launched and its children (`taskkill /T /PID <yours>`), or processes filtered by
    your worktree's path in their command line. Never kill by image name across the PC: other
    sessions build here, and Wyn's own apps run here too (`local\DESK-RULES.md`). A build's lock holder
    is in `local\build.lock` (`tools\lock.ps1 -Status`).
  - `powershell -File tools\reap.ps1 [-List] [-Watch]` (`-SelfTest`; `tools\reap-watch.cmd` = double-click,
    hidden): ends orphaned or stuck processes of our builds and runs (only ones under the project
    folder, never the lock holder's tree), log `local\reap.log`; `lock.ps1` runs it before a build queues.
  - Build for the long run and for future ports of other PS2 games: runtime fixes follow the
    real SDK/hardware behaviour, not what SRWZ happens to need; anything game-specific goes in
    per-game config, never hard-coded; tools work for any game.
- If PCSX2 code is used (GS, VU, SPU2), the project becomes GPL-3.
- Working mode (Wyn, 2026-10-06, replaces the earlier "High everywhere, Extra only after
  NEEDS EXTRA EFFORT" rule): the coordinator session plans, splits work into
  tasks, briefs agents, checks their results, keeps this file and PROGRESS.md current,
  commits and reports to Wyn in plain words. It does hands-on work itself only when that
  is quicker than a brief (one build, one run, a short check).
- Effort is chosen per task by the coordinator; it lives in each agent's frontmatter, so pick
  the agent by level (`.claude/agents/`, all Opus):
  - `worker-low` (low): mechanical work with a clear spec: a script, a known fix, a rename, notes.
  - `worker-medium` (medium): routine engineering with some judgement: a runtime stub from a
    decompile, a tool, a focused investigation with the logs in hand.
  - `worker-high` / `deep-debugger` (high): an open wall, or a subtle runtime/recompiler
    change other games will also run on.
  - `worker-extra` / `deep-debugger-extra` (xhigh): a problem a High pass couldn't crack, or
    one clearly deep from the start (hardware timing, VU microcode, threading). No prior High
    pass is required.
  - `runtime-reviewer` (Opus, high): reviews every PS2Recomp change before it is committed.
  - `log-triage` (Sonnet, medium): reads run logs and answers one question with quoted lines.
- Delegation: every brief stands alone (goal, files, what is ruled out, which run logs or
  dumps, what "done" means, what to report). Independent tasks run in parallel; worktrees
  only when two agents would touch the same files. Never accept an agent's claim without
  checking its evidence (a dump, a screenshot, a run report).
- Spend effort on reasoning, not reading (Wyn, 2026-10-06). Summarise runs with
  `tools\run-report.ps1` instead of paging through logs; decompile with `tools\decomp.ps1`
  (clean output); look at GS memory with `tools\gsview.py`; hand log reading to `log-triage`.
- Tools: when a task would go faster or more reliably with a tool we lack (viewer, dump,
  tracer, test harness), check PS2Recomp, PCSX2's debugger/GS dumps, Ghidra scripts and
  `tools/` first, else build it. Small single-purpose tools with a self-test, in `tools/`,
  listed here with a one-line usage. Runtime debug switches go behind an environment
  variable (like `PS2X_GS_DUMP`) so they cost nothing when off.
- Prior art first (Wyn, 2026-10-11): before building any large runtime piece (VU, GS, IOP, sound,
  tooling), search the forks of ran-j/PS2Recomp and the game ports built on it ("*-recomp" repos) for
  an existing solution, read-only, and port or wrap it with credit. Re-scan them **every working day**
  (Wyn: new ones appear daily), kept cheap: new commits in the projects already listed, plus new
  forks and ports since the last scan; note the date and anything useful in `docs/VU1-PLAN.md`. Why: on 2026-10-11 KillzoneRecomp's kzvu (PCSX2 microVU as a
  library) replaced a 1-2 week VU1 plan in one afternoon (map 3.1 -> ~40 fps). Known projects and what
  each has: `docs/VU1-PLAN.md`; Wyn's raw AI research: `local\research\`.
- Propose tooling unasked (Wyn, 2026-10-07): when a slow or manual step comes up for the third
  time, or costs minutes on every run, say so in one line with the saving per run and the
  payback in runs, and build it. Stop polishing when the next improvement saves less than it
  costs to build (diminishing returns).
- Wyn's part: the comparison kit under `local/reference/` (PCSX2 screenshots of the fixed
  points: warning screen, logo, first movie frame, title, first menu, with notes on what
  the buttons do) and PCSX2 GS dumps of the title screen and the first battle. Ask for
  these when a picture is in doubt rather than guessing.
- On "continue": read `docs/PROGRESS.md`, take the newest open wall, carry on.

## Machine (Wyn's PC)
Windows 11, AMD Ryzen 7 7800X3D (8 cores / 16 threads), 31 GB RAM, NVIDIA RTX 4070 Ti,
~660 GB free on C:. Installed 2026-10-06: Visual Studio 2022 Build Tools
(MSVC 14.44, C++ workload; `vcvars64.bat` under `C:\Program Files (x86)\Microsoft Visual
Studio\2022\BuildTools\VC\Auxiliary\Build`), CMake 4.4.4 (on PATH), Temurin JDK 21
(`JAVA_HOME`). Ghidra 12.1.4 lives in `ghidra/` (ignored) with the
ghidra-emotionengine-reloaded extension (language `r5900:LE:32:default`). The shell
Claude runs in sets NoDefaultCurrentDirectoryInExePath: run programs by full path.

## Layout
- `disc/srwz.iso` (your own dump), `bios/` (your own BIOS, for PCSX2), `PCSX2/` (the
  reference emulator: screenshots, debugger, save states), `installers/`. All private.
- `third_party/PS2Recomp`: submodule (ran-j/PS2Recomp, GPL-3); our changes go on its
  local `srwz` branch. Build: `tools\build-ps2recomp.bat [Release|Debug]` (MSVC, no
  GUI studio) into `third_party/PS2Recomp/out/build`.
- `local/ghidra/`: the Ghidra project (`srwz`, program `SLPS_258.87`, auto-analysed:
  10,264 functions). Headless: `ghidra\support\analyzeHeadless.bat local\ghidra srwz
  -process SLPS_258.87 -noanalysis -readOnly -scriptPath <dir> -postScript <script> <args>`.
- `local/recomp/`: the recompiler config and output. `ghidra.toml` + `srwz_functions.csv`
  come from PS2Recomp's `ExportPS2Functions.java` (script args = TOML path, CSV path);
  `analyzer.toml` from `ps2_analyzer.exe local\code\SLPS_258.87 local\recomp\analyzer.toml`,
  whose embedded SCE SDK database (built from Japanese games with debug info) names 988
  Sony library functions in our stripped program; `python tools/make_config.py
  local/recomp/ghidra.toml local/recomp/analyzer.toml local/recomp/srwz.toml` merges them
  (318 functions bound to the runtime's own implementations, 723 named entry points, the
  `[mmio]` table). **Never take the analyzer's `[patches]`**: for this game they NOP 15
  ordinary `sw` instructions (malloc unlock, HiG setup) that store into the image's range.
  Generated C++ goes to `local/recomp/output` (it is the game's code: it never leaves
  `local/`). With `output_group_bytes = 0x2000` (PS2Recomp srwz-build and later) it is ~380
  `ps2x_group_<addr>.cpp` files; ps2_recomp rewrites only files whose content changed and removes
  ones it no longer produces (`ps2x_generated_files.txt`), so the folder is not deleted (only
  once, when switching layouts).
- `tools/ghidra/`: our Ghidra scripts. `FunctionStats.java`; `Decompile.java <addr>...`
  prints decompiled C and callers for the functions containing those addresses (output on
  `INFO  Decompile.java>` lines).
- Overlays in Ghidra: `local/code/elf/*.elf` (from `python tools/mwo3_to_elf.py
  local/code/stage/*.bin local/code/compdata.bin --out local/code/elf`) are imported and
  analysed in the project folder `srwz/overlays` (2,646 functions). In every overlay the
  code starts at offset `0x80` (the header's text size counts from `0x40`, which is zero
  padding); the first functions are tiny accessors returning pointers into the overlay's
  data, the interface the main program calls (e.g. `0x756780`).
- `local/game/`: the whole disc unpacked (`python tools/iso9660.py disc/srwz.iso --extract
  --out local/game`), with the boot ELF in it: the runtime takes the ELF's folder as
  `cdrom0:`/`host:` and `mc0/` beside it as the memory card.
- The full cycle after a change: `tools\build-ps2recomp.bat` (only if PS2Recomp's recompiler
  changed), `ps2_recomp.exe local\recomp\srwz.toml` (~30 s), `python tools/overlays.py` (~40 s),
  `tools\build-game.bat` (a binding change rebuilds one or two groups plus the table: ~30 s; a
  runtime header change or a fresh build ~7-8 min, ~8 GB), then
  `powershell -File tools\run-game.ps1 -Name runN -Seconds 45 -Trace 1`.
  The run script closes the window gracefully (a forced kill loses buffered stdout) and keeps
  `local\runs\runN.out.txt` / `.err.txt`. The runner itself is
  `third_party\PS2Recomp\out\build\ps2xRuntime\Release\ps2EntryRunner.exe
  local\game\SLPS_258.87 disc\srwz.iso` (argv[2] = disc image for sector reads, our addition).
- `PS2X_TRACE_STUBS=1` (or a comma list of name parts, e.g. `Sif,Cd,malloc`) logs every call
  into a runtime-bound SDK function with a0..a3, ra and v0, on stderr (our addition: the
  recompiler emits `ps2xStubTrace` in each stub wrapper; `ps2_stub_trace.cpp`).
- Timing: the guest clock follows wall-clock time (our PS2Recomp change, Wall 6);
  `PS2X_REALTIME_CLOCK=0` goes back to cycle charges only. The `[run:tick]` line (every
  2 s) shows `hostMs`, `eeMs`, `vsync` and the DMA/GIF counters: the quickest way to see
  whether the game is alive and at what rate. The runtime's MPEG/CD stream logs are capped
  at 32 lines each, so count rates from the tick line, not from them. A full stub trace
  costs host time; prefer a narrow filter when timing matters.
- Speed claims need a controlled A/B (alternate builds, 3+ rounds, nothing else running, build lock held; fps, VBlank/s, EE cycles/vsync): `docs/TOOLS.md` "Performance measurements".
- Run tooling: `tools\run-game.ps1` (bare run, logs); `tools\run-report.ps1 -Name runN
  -Seconds 52 -Shots 25,48` (run + window captures with the overlay hidden + a summary:
  clock drift, VBlank and DMA/GIF rates per window, present failures, privileged GS
  register changes, errors, stderr categories); `tools\screenshot.ps1` (one capture);
  `tools\decomp.ps1 -Name decompN 0xADDR...` (Ghidra decompile, clean). The runtime's
  `[gs:present:none]` line says why nothing was presented (the window shows magenta then);
  `[gs:priv]` logs changes to PMODE/SMODE2/DISPFB/DISPLAY/BGCOLOR (MMIO writes and the
  DispEnv stubs). GS debug logs (GIF tags, primitives, kicks, register writes) stop after
  64-128 lines; `PS2X_GS_LOG_LIMIT=N` raises every cap to N for a graphics investigation.
  Stderr lines from different threads can interleave: a spliced line is noise, not data.
- Stall watchdog (Wall 9): `PS2X_WATCHDOG=5` prints, when the window loop or the guest VBlank
  count stops moving for 5 s, every host thread's native stack (exe frames named from
  `ps2EntryRunner.map`, which the runner now links with `/MAP`), the last 64 runtime-bound SDK
  calls (a `>` with no matching `<` is a stub that has not returned), and the guest state (debug
  pc/ra, EE threads, semaphores, event flags), on stderr as `[watchdog]` lines. A short
  "guest VBlank STUCK" report ~6 s after boot is the boot-time load, not a hang.
  `PS2X_SIF_LOG_LIMIT=N` raises the 32-call cap on the `[sceSifSetDma:CALL/DESC]` log.
- The opening movie (`OPPRG.PSS`) is 6 min 29 s (11,685 pictures): the title screen comes at
  about vsync 27,000 (~450 s host). Runs that need the title must last ~470 s.
- Graphics inspection (Wall 8): `PS2X_GS_DUMP=<tick,tick,…>` with `PS2X_GS_DUMP_DIR=<dir>`
  saves GS memory, display registers and the presented frame (`gsdump_<tick>.bin/.txt/.rgba`,
  the .rgba is 640x512 RGBA, the picture in its top rows); `python tools/gsview.py <dump>
  --psm <fmt> --bp <block> --bw <width/64> --w --h [--cbp --cpsm --csa] -o out.png` renders any
  region (`--map` = used pages, `--selftest`); `PS2X_GS_PRIM_UNIQUE=1` logs every distinct draw
  setup once (texture, CLUT, frame, TEST, ZBUF). Look at memory before theorising.
  `PS2X_GS_DUMP_DIR` must be an absolute, existing folder. `python tools/dumpsheet.py <dump
  dir> <out.png>` puts every dump's presented frame on one contact sheet, labelled by tick.
  `python tools/refcompare.py <reference.jpg> <dump dir> [-o side.png]` crops a PCSX2 window
  capture (`local/reference/`) to its 4:3 picture and ranks the dumps by likeness (mean abs
  diff on a 64x48 grey thumbnail: <15 close, >35 no match; `--selftest`).
  `powershell -File tools\long-run.ps1 -Name runN -Seconds 600 [-DumpEvery 600 -DumpFrom 0]`
  is a long run dumping every N ticks into `local\runs\runNdump`; it stops the game when the
  dumps and stdout go quiet for 30 s, records the stall (time, last dump, last `[run:tick]`)
  and prints a summary with the contact sheet (`-SelfTest` uses a dummy process).
  `powershell -File tools\regress.ps1 -Name runN [-Seconds 60]` is the regression check for
  every runtime merge: runs through long-run.ps1 with the standard dumps (every 30 ticks
  1200-1920, 2820, every 600 after) and scores them against `local\runs\baseline` by content
  in order, not tick: each dump is classified against all baseline frames (screen / blank /
  movie / anim), the matched baseline ticks must not go backwards; FAIL = no match anywhere
  or out of order, EDGE = content the baseline does not cover (docs/TOOLS.md); per dump
  class, mad, matched baseline tick, PASS/FAIL, then the verdict. Exact movie matching when
  both runs set `PS2X_MPEG_HASH`. `-Strict` = old tick-window scoring. `-NoRun` scores an
  existing dump dir, `-MakeBaseline -From <dump dir>` replaces the baseline (now hb1dump),
  `-SelfTest` (hb3 vs hb1 passes despite ~470 ticks of drift; a corrupted frame, a wrong
  screen, two swapped screens and a hang to black fail).
- Stub tests: `ps2x_tests` (PS2Recomp's suite; 2 known failures, see PROGRESS) and
  `ps2_gs_dbuff_tests` (libgraph double-buffer stubs vs. a model of the game's own code), both
  under `third_party/PS2Recomp/out/build/ps2xTest/`. A stub the game never calls is verified
  this way, against the decompile, not by a run.
- SDK stubs: arguments 5-8 are in t0-t3 (regs 8-11), 9+ on the stack in 8-byte slots from
  sp+0. Walls 7 and 8 were both stubs reading 5-7 from the stack. Before trusting a stub,
  compare it with the game's own copy of the SDK function (`tools\decomp.ps1` on its address).
  `python tools/stubaudit.py [--bound local/recomp/srwz.toml] [--json] [--all] [--selftest]`
  lists every handler's stack and t0-t3 reads and flags ABI mismatches (results: `docs/STUB-AUDIT.md`).
- `python tools/bindcheck.py local/recomp/srwz.toml --analyzer local/recomp/analyzer.toml --manual config/bindings.txt --output local/recomp/output --report local/bindcheck/srwz-bindcheck.txt --log local/recomp/recomp.log` (`--selftest`; ~40 s; run after `ps2_recomp`): report-only binding verifier: guest copy vs handler argument reads, SDK-database identity, bindings the recompiler drops (no function at the address), duplicates, aliases, TODO handlers, missed wrappers, guest SDK functions in a runtime-owned library; one summary line appended to the log.
- `powershell -File tools\appaudio.ps1 -ProcessName pcsx2-qt -Mute -WaitSeconds 30` (or `-Unmute`; `-List`, `-SelfTest`; `-Device "<name part>"` / `-ResetDevice` set the per-app output device, persisted per exe): per-process audio mute through Core Audio.
- `powershell -File tools\pcsx2-capture.ps1 -Name <set> -Times 20,415 [-Turbo] [-Seconds N] [-Keys "420:Return"] [-Sound]`: boots the disc in PCSX2 (muted, settings untouched) and saves `local\reference\<set>\pcsx2_<s>s.png` at those game seconds (-Turbo = 2x, ~1 s accurate; keep the window un-minimised); intro timing in `local\reference\NOTES.md`. `-PostKeys` posts the keys as window messages (no focus change, works while Wyn uses the PC; verified title -> menu -> SELECT SCENARIO -> protagonist select, set `w16post`); with it a key can be a chord (`429:R1&START`); the private config also binds L1=Q R1=W L2=A R2=S SELECT=Backspace (`pcsx2-auto.ps1`).
- VU1/VIF1 debugging (srwz-w16): `PS2X_VIF1_STATS=1` adds a `[vif1:stats]` line per tick line (VIF1 bytes, MSCAL/MSCNT/MPG/DIRECT/UNPACK counts, GIF packets/bytes per PATH1/2/3); `PS2X_VIF1_TRACE=[skip:]n` logs VIF1 codes with CYCLE/MASK/MODE/BASE/OFST/TOPS; `PS2X_PATH1_DUMP=[skip:]n` dumps XGKICK packets as qwords. `PS2X_VU1_STATS=1` (srwz 5a15667+) adds a `[vu1:stats]` line per tick line: per micro program entry (hash of micro memory : entry pc) finished runs, pairs, VU cycles, stall cycles (`docs/VU1-PLAN.md` "Stage 0 results"). `python tools/vudis.py <vu code.bin> [--start a --count n]` (`--selftest`) disassembles VU micro code (cut VU1 code/data from a snapshot's MEMR block: after RDRAM come u64-size-prefixed SPR, IOP RAM, GS VRAM, VU0 code, VU0 data, VU1 code, VU1 data).
- `python tools/codeptrs.py local/code/SLPS_258.87 local/recomp/srwz_functions.csv --callers local/code/elf/*.elf --out config/entries.txt [--verbose]` (`--self-test`): functions the map missed that are reached only through code pointers in data (vtables, callback tables) or through jal/j from overlay code (`--callers`; e.g. the thunk 0x1bdb20, 182 overlay calls), or built in registers (lui+addiu/ori); a target may also sit inside an over-long map entry right after its jr ra (0x2bcc60 in entry_002bcbe8) ( a JALR to one logs `[guest-branch:missing-target]` and parks the thread, Wall 16). `config/entries.txt` (generated, committed) feeds `make_config.py`, which adds them to `entry_points` (ps2_recomp synthesizes each one). Overlays: `python tools/codeptrs.py --overlays local/code/elf local/recomp/overlays --out config/overlay_entries.txt` (178 in 27 overlays; `tools/overlays.py` adds each overlay's list to its entry_points; a private overlay build: copy `local/recomp/overlays` under a root of your own, point the tomls' output there, then `overlays.py --root <root> --out <dir>` and `wt-build.ps1 -OverlayDir <dir>`, as in `local/wt-w16-ovroot`).
- `docs/PCSX2-TOOLBOX.md`: what PCSX2 2.8.2 can do for us and how to script it.
  - PINE memory reads and writes: `python tools/pine.py read|watch|state`.
  - Save states: `tools\pcsx2-capture.ps1 -State local\reference\states\pretitle_400s.p2s` reaches the title in about 20 s.
  - Also covered: turbo, GS dumps, the debugger (not scriptable) and logs (the trace channels are broken in 2.8.2).
- PCSX2 pad ground truth for Wall 11: `local\reference\pad-trace.txt`.
- `python tools/route.py <route> [--explain]` (`--list`, `--selftest`): named pad routes -> `PS2X_PAD_SCRIPT` (macros/routes in `config/game.json` `pad`, files `config/routes/<name>.txt`; e.g. `select_scenario` = `3000:START+8,3400:START+8`; `scenario1` (new game to the scenario 1 dialogue) and `guidance_map` (guidance to the stg_501 map dialogue) run at fast-forward 1 only; checkpoint `scenario1` has `fastForward` 1 and its own `baseline`); `-Route <name>` on long-run/wall (`--route` on fpsrun), checkpoints may name a route, `PS2X_PAD_SCRIPT=@<file>` is expanded by the scripts. `docs/TOOLS.md` "Pad routes".
- `powershell -File tools\wall.ps1 -Name runN -Checkpoint logo [-Recomp] [-Build] [-Compare] [-Seconds N] [-Env K=V,...] [-DryRun]` (`-NoRun -From <dump dir>`, `-List`, `-SelfTest`): a wall investigation in one go: build, muted run to a named checkpoint of `config/game.json` (per-game paths, build steps, checkpoints with dump window and PCSX2 reference; fast-forward/snapshot when the runner has them), contact sheet, reference side-by-side, regression score, log summary -> `local\runs\runN\report.md` + 10-line summary. The loop for a new port: `docs/TOOLS.md`.
- `powershell -File tools\review-pack.ps1 -Branch srwz-foo [-Logs a.txt,b.txt] [-Base rev]` (`-Repo main` for this repo, `-SelfTest`): review pack for a branch in `local\runs\<branch>\`: diff and `--stat` against the merge base with srwz/main (a merged branch: the base before its merge), commits, test counts from its NOTES files and the logs, and `brief.md`, a review-brief skeleton with the runtime-reviewer checklist (ABI, game-agnostic, default off, off = byte-identical, threads, snapshots, tests).
- `powershell -File tools\verify-merge.ps1 [-Regen] [-Quiet] [-Skip 1,fps] [-KeepGoing]` (`-DryRun`, `-SelfTest`): the check after every runtime merge, one PASS/FAIL line per step, stops at the first hard failure: wait for the main runner to be free + build lock, (`-Regen` make_config/ps2_recomp/overlays), build-game, ps2x_tests, headless regress, title snapshot re-save to `local\runs\snap\title.bin` (+ one load), fpsrun title/menu A/B-style (fps, VBlank/s, Mcycles/vsync) -> `local\runs\verify-<time>\summary.txt`. `docs/TOOLS.md` "After a merge".
- `powershell -File tools\upstream-check.ps1 [-NoFetch] [-SelfTest]`: what landed upstream in ran-j/PS2Recomp since our srwz merge base, one line per commit with the files both sides changed (likely conflicts); run at the start of each working day and before big runtime merges.
- No audio device (PS2Recomp srwz-noaudio and later): `PS2X_NO_AUDIO=1`, `PS2X_MUTE=1` or headless skip raylib's `InitAudioDevice` entirely (stdout `[audio] no audio device (PS2X_NO_AUDIO|PS2X_MUTE|headless)`; no WASAPI stream, no audio session); sounds are dropped, game timing unchanged (nothing in the guest waits on host playback). run-game, long-run, regress, run-report, wall and `fpsrun.py` set it by default; `-Sound` (`--sound`) opens the device with sound on. Older runners only mute at volume 0 (device still opens). Test: `ps2_audio_device_tests`.
- Headless / unattended runs (PS2Recomp srwz-headless and later): `PS2X_HEADLESS=1` (or `-Headless` on run-game, long-run, regress, run-report, wall) runs with no window, GL context or audio device; a build that finds no monitor (monitor off overnight) goes headless by itself (`[run] no monitor: headless`; older builds crash in raylib InitWindow). Dumps, pad script, watchdog and `[run:tick]` work unchanged; `-Shots` is skipped, use dumps. Loop paced at 60 Hz x the fast-forward factor. Headless 1x runs boot ~600 ticks earlier than windowed ones; the regression check matches on content order, so either kind of baseline works (`-Strict` needs a headless one).
- `PS2X_STOP_FILE=<path>`: the runner stops cleanly (stdout flushed, exit 0) once the file exists; the scripts stop runs through `tools\runstop.ps1` (stop file, then window close, kill after 10 s; `-SelfTest`). Ctrl-C/Ctrl-Break also stop it cleanly.
- Guest call trace (PS2Recomp srwz-debug and later): `PS2X_TRACE_CALLS=<item,...>` logs guest calls whose target matches (`*` all, `0x18b000-0x18c000` a range, `0x18bd70` the function containing it, `sceCd` any name containing it), kinds call/jump/sched, to `PS2X_TRACE_CALLS_FILE` (default `ps2x_calls.txt` in the working folder, `-` = stderr), up to `PS2X_TRACE_CALLS_MAX` lines (default 1,000,000); `PS2X_TRACE_CALLS_RET=1` adds `<` lines with v0. Free when off; tail calls emitted as direct C++ calls are not seen.
- Guest names: runtime logs, the watchdog and crash reports print guest pcs as `name+0xoff` (name table emitted by ps2_recomp into `register_functions.cpp`; regenerate after updating the recompiler); overlay code shows as `<overlay>.bin:<name>+off` (`tools/overlays.py` registers each overlay's names, ps2_recomp's table plus its Ghidra map, via `ps2xRegisterOverlayFunctionNames`; output from before that falls back to `sub_<entry>+off`). `overlays.py --out <dir> --link-only [names]` relinks into a scratch folder without rerunning ps2_recomp; `--root` points a worktree copy at the main checkout; `--self-test` checks the name parsing.
- Crash handler (on by default, `PS2X_CRASH_HANDLER=0` off): a native crash prints `[crash]` lines on stderr (named host stack, guest pc/ra/registers, last 64 SDK calls and guest dispatches), flushes logs and exits with the exception code; `PS2X_CRASH_DUMP=<abs path>` also writes a minidump; `PS2X_CRASH_TEST=<stub name part>[:n]` (test only) crashes in the n-th matching stub call, and `PS2X_CRASH_TEST_HOLD=symbols|stderr` makes a thread hold that lock then (the report falls back to bare addresses, or a 10 s timeout ends the process: the handler never hangs). The map is parsed at start-up on a background thread (`[hoststack] N symbols` line). Tests: `ps2_debug_names_tests`.
- Runner snapshots (PS2Recomp srwz-snap and later; design and limits in its `docs/SNAPSHOT-DESIGN.md`): `PS2X_SNAPSHOT_SAVE=<path with {tick}>` + `PS2X_SNAPSHOT_AT=<tick,...>` [+ `PS2X_SNAPSHOT_EXIT=1`] save the whole runner state at the first safe point at or after each tick (F5 in a window saves now; stderr `[snapshot] saved ... bytes= ms=`, or `still waiting for a safe point ... <reason>`); `PS2X_SNAPSHOT_LOAD=<file>` resumes from one (`[snapshot] loaded ... ms=`). A title snapshot (tick ~27400, 45 MB) gives the title in ~2 s instead of ~120 s at fast-forward 4. A snapshot loads only into the runner build that wrote it: re-save after every rebuild (keep title snapshots in `local\runs\snap\`).
  - SRWZ's movie has one MPEG sequence header, so the decoder is rebuilt by replaying the stream so far: mid-movie saves past ~tick 10,100 need `PS2X_MPEG_REPLAY_MB=320` in the saving run (files 100-260 MB; `[snapshot:mpeg] ... last picture matches`).
  - Give a loaded run only `PS2X_GS_DUMP` ticks after its load tick, and score it against the straight run that wrote the snapshot (a loaded run keeps its parent's timing; two straight runs differ much more).
- Checkpoints (PS2Recomp srwz-durable and later): `powershell -File tools\checkpoint.ps1 list|info|save|load|verify|scene|pcsx2 <name>` over `local\reference\states\<name>\` (snapshot.bin, pcsx2.p2s, reference.png, ours.png, mc0\, manifest.json). `save <name> -At <tick> [-From <checkpoint>] [-Route r]` runs to a tick, from boot or chained from another checkpoint; `-Snapshot <bin>` adopts a file. `verify <name> [-Frames 300]` = two deterministic runs, per-VBlank hashes compared (PASS, or the first differing tick and what differs); `scene <name> [-Frames 60] [-Tolerance 15]` = frame vs reference picture with a side-by-side diff PNG (PCSX2 window captures are cropped); `pcsx2 <name> -Times .. [-Keys ..]` = PCSX2 from the paired state. `-Exe` picks a runner build. `python tools/snapinfo.py <bin> [--sections --types]` shows a snapshot's header. Snapshots are durable from format 8: they load into any later build whose sections and raw layouts match, else are refused naming the section (PS2Recomp `docs/SNAPSHOT-DESIGN.md`); format 7 files load by struct sizes.
- `python tools/snapsearch.py a.bin=6900 b.bin=6551 [c.bin=same|changed|any|<v>] [--width 1|2|4] [--range lo-hi]` (`--dump <bin> <addr>`, `--selftest`): Cheat-Engine-style search of EE RAM across runner snapshots (RDRAM located by the ELF's code bytes); finds unit stats, flags and save-block fields for cheats.
- Test cheats (PS2Recomp srwz-cheats and later): `PS2X_CHEATS=<file.pnach>` applies PCSX2-style raw codes (`patch=1,EE,<type|addr>,extended,<value>`, type 0/1/2 = 8/16/32-bit; patch=1 every VBlank, patch=0 once) at each guest VBlank; the same file goes in PCSX2 (`local\pcsx2-auto\PCSX2\cheats\<CRC>.pnach`, CRC 321C5C3B, cheats enabled) so paired runs match. Cheats bring a run to a test point, never through it (docs/PLAYTHROUGH.md); verify every address in PCSX2 first (docs/ROUTES.md section 7 lists the unverified ones).- Deterministic runs: `PS2X_DETERMINISTIC=1` (guest time from EE cycle charges only, synchronous GS, no audio device, fixed-epoch guest clock), `PS2X_FRAME_HASH=<file>` [+ `PS2X_FRAME_HASH_UNTIL=+N`] (per-VBlank RAM/VRAM/GS hashes), `PS2X_MC_ROOT=<dir>` (memory card folder). Scenes that busy-wait run slowly in this mode (fixed charges per branch: s01 ~5 VBlanks/s).
- `tools\run-report.ps1 -Shots` hangs when the job runs in the background (no window
  capture); use the dump .rgba files for pictures in that case.
- `config/bindings.txt` (committed): hand-made `name@address` bindings with the reason for
  each, merged first by `make_config.py`. Add one whenever the trace shows the game calling
  an SDK function as guest code that the runtime should own (wrappers the analyzer missed),
  or guest code that depends on a library's internals the runtime replaced (allocator).
- Overlays (our addition to PS2Recomp, `ps2_overlays.h`): `tools/overlays.py` recompiles each
  overlay from `local/recomp/overlays/<name>/` (Ghidra maps exported by
  `tools/ghidra/ExportOverlayMaps.java` over the `srwz/overlays` project folder) and writes
  `local/recomp/overlay_build/ov_<name>.cpp` (its code in `namespace ov_<name>`, plus a static
  initialiser calling `ps2xRegisterOverlay`); the runner builds that folder
  (`PS2X_OVERLAY_DIR`). Lookups in an overlay range read the MWo3 name at `base + 0x20` in
  guest memory and use that overlay's entries.
- `docs/PROGRESS.md`: every wall so far, newest first, with cause and fix. Add to it.
- `docs/HANDOFF.md`: one-page state, next step, saved points and rules for a fresh session. Read it first.
- `docs/TESTING.md`: how we test and manage save states (checkpoints paired with PCSX2, save on arrival, determinism, the test ladder, regressScenes). Read it before testing work.
- Shell pitfalls here: backslashes in Bash heredocs get eaten or turned into escapes on the
  way to Python (`\b`, `\r` in Windows paths): write paths and backslash literals with the
  Edit/Write tools or `chr(92)`, and re-read what was written; `.bat` arguments split
  at `;` (Ghidra `-scriptPath` with two folders fails: copy scripts into one folder,
  `local\ghidra-scripts`); a PowerShell command containing both `Remove-Item` and
  `cmd.exe /c` is refused by a safety check (delete in a separate step).
- `local/` (private, regenerated): `python tools/unpack.py disc/srwz.iso` writes
  `local/extract/` (files as on the disc) and `local/code/` (boot ELF, `compdata.bin`,
  `stage/*.bin` overlays, `iop/*.irx`, `IOPRP310.IMG`).
- `tools/iso9660.py` (list/extract the disc), `tools/banlz.py` (the game's LZ format),
  `tools/unpack.py` (everything code-related, in one go).
- `docs/STEP0.md`: the survey of the disc and the code. Read it before starting work.
- `docs/PLAYTHROUGH.md`: plan for testing the whole game unattended (pad macros, RAM-state reader, PCSX2 differential runs, save checkpoints, input recorder).
- `docs/DISTRIBUTION.md`: how the port will be distributed later (player's own hash-checked disc image, no BIOS, prebuilt or locally generated executable); keep game paths in config so that stays possible.

## What we know (details in docs/STEP0.md)
- Boot ELF loads at `0x100000`; ~772k instructions of stripped Metrowerks C++ (SDK 3.1.0).
- Overlays: `compdata.bin` at `0x6d6800` (`DATA/COMPDATA.BN`) and 205 stage overlays all at
  `0x756700` (`DATA/STAGE.BIN`), Metrowerks `MWo3` header (version, load address, text,
  data, bss, end ×2, name). Overlay code is plain integer MIPS calling into the main
  program. No other code anywhere on the disc.
- Graphics: Sony HiG/HiP, one VU1 microcode block (8 programs) at `0x3ef110`.
- IOP: only standard Sony modules (padman, mcman, libsd, sdrdrv, modmidi, modhsyn,
  multi_streamer, ezmidi, ezbgm, cdvd streaming) plus the SDK 3.1.0 `IOPRP310.IMG`.
- retro-trans/SRW-Z (the English translation, https://github.com/retro-trans/SRW-Z)
  documents the LZ format, the text renderer and the message window; its addresses match
  this build. Their "scenario records" in `STAGE.BIN` are the stage overlays.

## Plan
0. Survey the disc and the code. **Done** (2026-10-06): go.
1. CPU: build PS2Recomp on Windows (CMake 3.20+, a C++20 compiler, mainly tested with
   MSVC), get a function map (Ghidra), translate the boot ELF and the overlays (overlay
   dispatch at `0x756700`), boot to the first system calls. **In progress.** First run
   (2026-10-06, Ghidra map only): boots, sets up the heap, then hangs in `sceSifInitRpc`
   (`0x11e7a8`) waiting for `sceSifGetSreg(0)`: PS2Recomp doesn't emulate the SIF at that
   level, it expects the SDK's SIF/RPC/CD/pad/mc/sound calls bound to its own versions
   (which talk to its IOP emulator; that runs the game's real IRX modules on an R3000
   interpreter). Hence the analyzer bindings above.
2. First picture: feed the GS packets to PCSX2's GS code; aim for the title screen.
3. Playable: controller, sound (IOP/SPU2 emulation first), movies (ffmpeg), saves.
4. Native extras: widescreen, higher resolution, the English translation without its
   byte limits.

Wyn plays and compares against PCSX2; Claude writes the code.
