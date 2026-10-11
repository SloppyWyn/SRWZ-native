# PCSX2 toolbox

What our reference emulator (`PCSX2\pcsx2-qt.exe`, **PCSX2 v2.8.2**, Wyn's copy) can do for
SRWZ-native, and how to drive each feature from our scripts. Sources: `pcsx2-qt.exe -help`,
the strings and settings keys in the binary, `Documents\PCSX2\inis\PCSX2.ini`, and runs made
on 2026-10-06 (marked **verified**).

Paths used below:
- `$P = PCSX2\pcsx2-qt.exe`, `$ISO = disc\srwz.iso`
- `$D` = PCSX2's data folder. For scripts: `local\pcsx2-auto\PCSX2` (launch with
  `-datapath <abs path of local\pcsx2-auto>`, made by `tools\pcsx2-auto.ps1`). Wyn's own
  is `C:\Users\Wyn\Documents\PCSX2`: hands off. The ini is `$D\inis\PCSX2.ini`.
- SRWZ is `SLPS-25887`, disc CRC `321C5C3B`: per-game files are named after these.

Rules:
- **One PCSX2 at a time.** Two instances share the memory cards ("Memory Card Read Failed ...
  another instance may be using this memory card") and the PINE port. Check `tasklist |
  findstr pcsx2` *and* take the lock (`tools\pcsx2-lock.ps1`, `local\pcsx2.lock`) right before
  launching, in the same script step.
- **Scripts use the private automation config** `local\pcsx2-auto\` (via `-datapath`), never
  Wyn's own `$D` config or memory cards. Save states for scripts are made there too.
- PCSX2 rewrites its ini on exit: back up before changing it.
- Close PCSX2 gracefully (WM_CLOSE; the private config has `ConfirmShutdown = false`): a
  killed PCSX2 loses the buffered end of its log file.

## Top three, in short
1. **PINE** (`tools\pine.py`): read and write guest memory of a running game over a local
   TCP socket, batched reads in one emulated instant. Ground truth for any variable we
   compare with our runner, and the game clock (VBlank counter `0x59c1d8`) that
   `pcsx2-capture.ps1` times its shots by. **Verified.**
2. **Save states** with `-statefile`: `local\reference\states\pretitle_400s.p2s` (made in the
   private config) boots to game ~400 s, 10 s before the movie ends, in about a second;
   `pcsx2-capture.ps1 -State` uses it. **Verified.**
3. **Turbo / unlimited** (`-turbo`, `-unlimited`, `[Framerate] TurboScalar`): reach late
   points of the intro in half the time or less.

Wall 11 ground truth from these: `local\reference\pad-trace.txt` (pad state machine on PCSX2
reaches its read state 0x63 at game ~6.8 s and stays there).

## Command line (complete list from `-help`)
| flag | what it does |
|---|---|
| `-batch` | exit when the game shuts down (always use from scripts) |
| `-nogui` | hide the main window while running (implies `-batch`) |
| `-fastboot` / `-slowboot` | skip / show the BIOS boot animation |
| `-state <index>` | load save-state slot N of this game on boot |
| `-statefile <file>` | load this .p2s on boot (any path) |
| `-turbo` / `-unlimited` | start in turbo (TurboScalar) / unlimited speed |
| `-debugger` | open the debugger and break on the ELF entry point |
| `-logfile <path>` | write the application log there instead of `$D\logs\emulog.txt` |
| `-earlyconsolelog` | log early console messages too |
| `-elf <file>` | boot this ELF instead of the disc's (with the disc still inserted) |
| `-gameargs "<args>"` | argv for the booted ELF |
| `-bios` | boot to the BIOS menu |
| `-nofullscreen` / `-fullscreen` / `-bigpicture` | window mode |
| `-portable`, `-datapath <dir>` | use another data folder (a clean, scripted config!) |
| `-testconfig`, `-setupwizard`, `-version`, `-help` | misc |
| `-- <file>` | the boot file: an ISO, an ELF, or a GS dump (`.gs`, `.gs.zst`, `.gs.xz`) |

`-datapath <dir>` is worth knowing: a script can run PCSX2 against its own copy of the
`inis\` folder (e.g. with PINE, trace logs and hotkeys set) and never touch Wyn's ini.

## Speed: turbo, unlimited, slow motion
- ini `[Framerate]`: `NominalScalar = 1` (normal speed), `TurboScalar = 2` (here),
  `SlomoScalar = 0.5`. A value of 0 means unlimited.
- Flags: `-turbo` (TurboScalar), `-unlimited` (no limit).
- Hotkeys (`[Hotkeys]`): `ToggleTurbo = Keyboard/Tab`, `HoldTurbo = Keyboard/Period`,
  `ToggleFrameLimit = Keyboard/F4` (unlimited), `ToggleSlowMotion = Shift+Tab`.
- `[EmuCore] EnableFastBootFastForward = true` fast-forwards the boot only.
- Gives us: the intro (410 s) in ~205 s at 2x (**verified**: "FMV ended" at wall 205.7 s);
  unlimited is faster but its rate varies with the scene, so game time can't be computed
  from wall time: `pcsx2-capture.ps1` reads the game's VBlank counter (`0x59c1d8`) over PINE.
- Example: `& $P -batch -fastboot -nofullscreen -turbo -- $ISO`

## Save states
- Slot files: `$D\sstates\SLPS-25887 (321C5C3B).NN.p2s` (`resume.p2s` = save on shutdown,
  `[EmuCore] SaveStateOnShutdown`). Scripts save into the private config (slot 9) and copy
  the file into `local\reference\states\`.
- Save: hotkey `SaveStateToSlot = F1` (current slot; `NextSaveStateSlot = F2`), menu
  System > Save State, or **PINE `MsgSaveState(slot)`** (`python tools\pine.py savestate 9`).
- Load at boot: `-state 9` (slot) or `-statefile <path>`. While running: `LoadStateFromSlot =
  F3`, or PINE `MsgLoadState(slot)`.
- A state stores the whole machine (EE/IOP/GS/SPU, memory card contents) but not settings;
  the BIOS must match (`Savestate BIOS ... Version Mismatch` warning otherwise). States break
  across PCSX2 versions: regenerate after an upgrade.
- Ours: `local\reference\states\pretitle_400s.p2s` (6.4 MB; game ~400 s, the last seconds
  of the opening movie, ~10 s before it ends and ~15 s before the title). Made in the private
  config by booting at 2x and sending PINE `SaveState(9)` at game 400 s; PCSX2 saved it in
  0.14 s. **Verified**: loading it with `-statefile`, the game is in its movie state (0x18)
  ~1 s after launch, the movie ends at game ~411 s, the title follows.
- `-statefile` needs the disc too (`-- $ISO`); `-fastboot` is irrelevant then.
- Example: `& $P -datapath <abs>\local\pcsx2-auto -batch -nofullscreen -statefile
  <abs>\local\reference\states\pretitle_400s.p2s -- $ISO`
- Script: `powershell -File tools\pcsx2-capture.ps1 -Name title -State
  local\reference\states\pretitle_400s.p2s -Times 414,418` (game times stay absolute: the
  VBlank-counter clock is restored with the state).

## Pause and frame advance
- Hotkeys: `TogglePause = Keyboard/Space` (set), `FrameAdvance` (unbound by default; add
  e.g. `FrameAdvance = Keyboard/F11` to `[Hotkeys]`), `OpenPauseMenu = Escape`.
- `[UI] StartPaused = true` boots paused (handy with `-statefile`: load, then step).
- Frame advance runs exactly one frame while paused: with PINE reads in between, a script
  can single-step the game frame by frame and log memory per frame.
- PINE `MsgStatus` reports running / paused / shutdown, but PINE cannot pause or step: send
  the hotkeys to the window (as `pcsx2-capture.ps1 -Keys "400:F11"` does with any key name).

## Debugger
- Open: Debug > Open Debugger, or `-debugger` (opens it and breaks at the ELF entry).
- Has: disassembly (EE and IOP tabs), registers (GPR, COP0, FPU, VU), memory view and
  search, call stack, threads (EE kernel threads), symbols (ELF symbols, IRX exports,
  function scanner; `[Debugger/Analysis]`), and breakpoints:
  execute breakpoints and memory breakpoints (read / write / on change), with a condition
  expression, and an enable flag. Memory breakpoints log `Hit load/store breakpoint @0x...`.
- Breakpoints and the layout are saved per game in `$D\inis\debuggersettings\SLPS-25887_321C5C3B.json`
  (written when the debugger closes), so a script can pre-write breakpoints into that JSON.
- **Not scriptable from outside**: no GDB stub, no command console, no socket in v2.8.2
  (the binary has no gdb/remote-debug strings). Registers cannot be read over PINE.
  For automation use PINE (memory) plus logging breakpoints; for registers at a point, an
  interactive session.
- Example: `& $P -batch -nofullscreen -debugger -- $ISO`, then set an execute breakpoint at
  `0x18d7d0` (the pad state machine) and read `0x59c210` in the memory view.

## PINE (IPC: read/write guest memory from a script)
- Enable: ini `[EmuCore] EnablePINE = true`, `PINESlot = 28011` (Settings > Advanced >
  PINE). On in the private config (`tools\pcsx2-auto.ps1`), off in Wyn's. The server
  starts with PCSX2; Windows uses TCP `127.0.0.1:28011`. **It serves one client at a time**:
  a second connection is accepted but gets no answers until the first closes (so
  `pcsx2-capture.ps1`, which reads its clock over PINE, and `pine.py watch` cannot run on the
  same PCSX2 together; `pine.py --selftest` reports this as "another client is connected").
- Protocol: request `u32 size` + commands (`u8 opcode` + args), reply `u32 size` + `u8 result`
  (0 OK, 0xFF fail) + data. Opcodes: Read8/16/32/64 = 0-3 (`u32 addr`), Write8/16/32/64 =
  4-7 (`u32 addr`, value), Version 8, SaveState 9 (`u8 slot`), LoadState 0xA, Title 0xB,
  ID 0xC, UUID 0xD, GameVersion 0xE, Status 0xF. Several commands in one request run
  together (one emulated instant): `pine.read32_many()` uses that.
- Addresses are EE virtual addresses (main RAM `0x00100000+`, scratchpad `0x70000000`). GS
  privileged registers (`0x12000000`) and IOP memory are not reachable: PINE goes through
  the EE memory map, and our GS mirrors are the game's own copies in RAM.
- `tools\pine.py`: `info`, `read <addr> [n]`, `dump <addr> <bytes>`, `write <addr> <val>`,
  `state` (SRWZ's pad and game words), `watch [--words a,b] [--interval s] [--seconds n]
  [--out file]` (logs each change, timestamped), `savestate N`, `loadstate N`,
  `--selftest [--write-test]` (fake in-process server; plus a live read test when PCSX2 is
  up, and with `--write-test` a write + read-back + restore of one unused guest word).
  Also a module: `from pine import Pine; Pine().read32(0x59c210)`.
- Speed: one batched read of 22 words takes well under a millisecond; `watch` at 8 ms
  intervals sampled about once per frame at 2x (26,000 samples over a 200 s run).
- SRWZ words (`pine.GAME_WORDS`): `0x59c1d8` VBlank counter (60/s, the game clock),
  `0x523930` top-level game state (0x18 movie, 0x02 title, 0x48/0x49 after Start at the
  title, 0x37-0x39 attract demo), `0x59c100` pad data buffer (bytes `00 73 ff ff 7f 7f 7f 7f` = DualShock
  analog, nothing pressed; Start clears bit 3 of byte 2), `0x59c210` pad state machine,
  `0x59c24c` buttons as the game maps them (Start = 0x0800).
- Comparing with our runner: read the same addresses at the same game moment (same VBlank
  count) in both (our runner's memory dumps, or a PINE-like reader on its side).
- Example: `python tools\pine.py watch --words 0x59c210,0x59c214,0x523930 --seconds 60`

## GS dumps, GS dump player
- Make: hotkeys `GSDumpSingleFrame = Shift+F8`, `GSDumpMultiFrame = Ctrl+Shift+F8` (hold),
  or Tools > Save GS dump. Written to `$D\snaps\` as `<game>_<date>.gs.zst`
  (`[EmuCore/GS] GSDumpCompression = 2` zstd). A dump holds the GS registers, memory and
  every GIF transfer of the frame.
- Play: boot the dump file: `& $P -batch -nofullscreen -- $D\snaps\x.gs.zst` (it loops;
  pause and frame advance work).
- Per-draw output while playing (`[EmuCore/GS]`): `DumpGSData = true` with `SaveRT`,
  `SaveFrame`, `SaveTexture`, `SaveDepth`, `SaveAlpha`, `SaveInfo`, `SaveTransferImages`,
  `SaveDrawStats`, limited by `SaveDrawStart`/`SaveDrawCount`/`SaveDrawBy` and
  `SaveFrameStart`/`SaveFrameCount`; images go to `HWDumpDirectory` / `SWDumpDirectory`.
  This is the "GS debugger": one picture per draw of PCSX2's render targets and textures,
  to set beside our `PS2X_GS_DUMP` + `tools\gsview.py` output.
- Gives us: the title screen and battle frames, draw by draw, as Wyn's comparison kit asks.

## Logs (EE, IOP, SIF, pad, CDVD)
- Application log: `[Logging] EnableFileLogging = true` (on), `-logfile <path>`,
  `EnableTimestamps = true`. Our capture script writes it as `emulog.txt`.
- Guest console output: `[Logging] EnableEEConsole = true` (EE `printf`/`scePrintf`, the
  kernel's messages), `EnableIOPConsole = true` (IOP `printf`: module loads, padman/mcman
  messages). Also in the menu Debug > Logging.
- **Not working for us yet (v2.8.2, -logfile):** in three runs with `EnableEEConsole`,
  `EnableIOPConsole` and/or `[EmuCore/TraceLog] Enabled = true`, the `-logfile` file stopped
  right after host init ("Enumerated 0 devices": no BIOS, game or PINE lines), although the
  run itself went fine and PCSX2 closed gracefully. The same run without them logs normally.
  Unresolved: try without `-logfile` (`$D\logs\emulog.txt`), with `[Logging]
  EnableSystemConsole = true`, or the Qt log window. Until then the EE/IOP/trace output is
  only for interactive use.
- Trace log (`[EmuCore/TraceLog]`, `Enabled = true` plus channels): `EE.bios` (EE syscalls
  by name), `EE.memory`, `EE.knownhw`/`EE.unknownhw` (hardware register accesses), `EE.dmac`,
  `EE.dmahw`, `EE.gif`, `EE.giftag`, `EE.vif`, `EE.vifcode`, `EE.counters`, `EE.ipu`,
  `EE.r5900`, `EE.cop0/1/2`, `EE.cache`, `EE.spr`, `EE.mskpath3`; `IOP.bios` (IOP module
  imports called), `IOP.pad` (the SIO2 pad traffic), `IOP.memcards`, `IOP.cdvd`, `IOP.knownhw`,
  `IOP.unknownhw`, `IOP.dmac`, `IOP.dmahw`, `IOP.counters`, `IOP.memory`, `IOP.r3000a`,
  `IOP.cop2`, `IOP.mdec`; `MISC.sif` (SIF DMA and register traffic). Meant to go to the log
  (see above). Volumes are large: enable one or two channels, from a save state, briefly.
- `[EmuCore] CdvdVerboseReads = true` logs every disc read (sector, count).
- Example (pad + SIF, from the pre-title state, private ini backed up first, lock held):
  set `Enabled = true`, `IOP.pad = true`, `MISC.sif = true`, then
  `& $P -datapath <abs>\local\pcsx2-auto -batch -nofullscreen -statefile
  <abs>\local\reference\states\pretitle_400s.p2s -- $ISO` and read `$D\logs\emulog.txt`.

## Per-game settings
- `$D\gamesettings\SLPS-25887_321C5C3B.ini` (right-click the game > Properties). Same
  sections and keys as `PCSX2.ini`; any key there overrides the global one for this game
  only (e.g. `[EmuCore/TraceLog]` for SRWZ only, or `[Pad] InputProfileName = <name>` to use
  `$D\inputprofiles\<name>.ini` as this game's controller config).
- Example: a per-game `[EmuCore/GS] DumpGSData = true` makes only SRWZ dump draws.

## Pad and hotkey config
- `[Pad1]` .. `[Pad8]`, `Type = DualShock2`; one line per binding: `<Button> = <Source>/<Key>`.
  Sources: `Keyboard/<Qt key name>` (`Return`, `X`, `Up`, `F8`), `SDL-0/<button>`
  (`FaceSouth`, `Start`, `DPadUp`, `+LeftTrigger`), `Mouse/...`, `XInput-0/...`.
- **Several bindings per button**: repeat the key, each line is an alternative
  (`Start = SDL-0/Start` and `Start = Keyboard/Return` both press Start). `A & B` on one line
  is a chord (both needed). Our scripted keys (private config, `tools\pcsx2-auto.ps1`):
  Start=Return, Cross=X, Circle=C, Triangle=V, Square=Z, arrows=d-pad, next to Wyn's SDL
  pad bindings. **Verified**: Return pressed at the title shows in the pad buffer (byte 2
  0xff -> 0xf7) and in `0x59c24c` = 0x0800, and the game leaves the
  title (0x523930: 0x02 -> 0x48).
- `[Hotkeys]` uses the same format (`TogglePause = Keyboard/Space`). Unbound actions worth
  binding for scripts: `FrameAdvance`, `SaveStateToSlot1`..`10`, `ShutdownVM`,
  `ToggleSoftwareRendering` (F9, set).
- Input profiles: `$D\inputprofiles\<name>.ini` with the same `[Pad1]`/`[Hotkeys]` sections.
- Input recording (`[EmuCore] EnableRecordingTools = true`, on; `InputRecToggleMode =
  Shift+R`): records pad input per frame from boot or a save state into a `.p2m2` file and
  replays it exactly. A replayable input script for menu walks.

## How we use it now
- Title and menu captures from the pre-title state, in seconds instead of 7 minutes:
  `pcsx2-capture.ps1 -Name title-state -State local\reference\states\pretitle_400s.p2s
  -Times 405,414,418` took 22 s in all (PINE clock locked 1 s after launch; the 418 s shot is
  the "PRESS ANY BUTTON" title). **Verified** 2026-10-06.
- `tools\pine.py watch` on PCSX2 = the ground truth for any guest variable (Wall 11's pad
  state machine: `local\reference\pad-trace.txt`).
- Debugger, GS dump replay and trace logs: interactive or one-off; the recipes above.
