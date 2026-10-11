# SRWZ-native

A native Windows port of **Super Robot Taisen Z** (PS2, Japan, `SLPS-25887`, ver. 1.04) by static
recompilation: the game's MIPS code is translated ahead of time into C++ with
[PS2Recomp](https://github.com/ran-j/PS2Recomp), and a runtime stands in for the PS2 hardware.
PCSX2 is the reference everything is compared against.

**No game code or data is in this repository.** You need your own disc image of the game; the
build translates your copy on your machine. BIOS, disc images and anything taken from the game stay
out of git (a commit hook refuses them).

## What this is, in plain words

Most people play old console games through an **emulator**: a program that pretends to be the
console and translates the game's machine code while you play, like an interpreter translating a
book aloud one sentence at a time. This project does something different: it **translates the whole
book once, in advance**. The game's PlayStation 2 code is turned into ordinary C++ and compiled into
a normal Windows program. No emulator runs the game's main program; it simply *is* a PC program now.

Some things that make it fun:

- **A whole game, translated.** Super Robot Taisen Z (Banpresto, 2008, Japan only) has about
  772,000 instructions in its main program, plus 205 extra code pieces that are swapped in for each
  stage of the story. All of it becomes about 380 C++ files.
- **The PS2 was really several computers in one box.** Next to the main processor sat two vector
  units for 3D math, a graphics chip, and a whole PlayStation 1 processor that handled sound, the
  disc and the controllers. The port keeps the original sound drivers running on a stand-in for that
  PS1 chip, so the music comes from the game's own code.
- **2 million tiny math steps per frame.** On the battle map, the PS2's 3D math chip runs about
  two million instructions every frame. Imitating that chip carefully gave 3 frames per second;
  plugging in PCSX2's fast engine for it (packaged by another fan port, KillzoneRecomp) gave about
  40. Open source helping open source.
- **Save anywhere, replay exactly.** The whole machine state can be saved to one file and loaded
  in a quarter of a second (the title screen without sitting through the 6.5-minute intro movie),
  and two runs from the same save produce *identical* frames. That is how every change is checked:
  run 300 frames before and after, compare fingerprints, and know nothing broke.
- **Checked against the real thing.** Key milestones are compared picture by picture with PCSX2,
  the reference emulator, from paired save points in both.
- **Built by AI agents, directed by one person, on other people's foundations.** Wyn set the goals
  and tested; Claude agents wrote the code and tools; the recompiler, the reference emulator and the
  fast VU1 engine come from the open-source projects below. Four days took it from nothing to
  playing all of scenario 1, through 21 "walls" (show-stopping bugs), each written up in
  `docs/PROGRESS.md`.

## Under the hood

For the technically curious, the pipeline and the moving parts:

1. **Function map.** The boot ELF (stripped Metrowerks C++, SDK 3.1.0, loads at `0x100000`) is
   analysed in Ghidra with the Emotion Engine extension; PS2Recomp's analyzer names 988 Sony library
   functions from its SDK database. Code reached only through vtables, callback tables or overlay
   calls is found by `tools/codeptrs.py`.
2. **Recompilation.** `ps2_recomp` turns every R5900 function into C++ (about 380 files of
   `0x2000`-byte groups). 318 SDK functions (SIF/RPC, CD, pad, memory card, sound, libgraph,
   libmpeg...) are bound to runtime implementations instead of being translated.
3. **Overlays.** The 205 stage overlays all load at `0x756700` (plus `compdata` at `0x6d6800`,
   Metrowerks `MWo3` headers); each is recompiled into its own namespace, and calls into the overlay
   range are dispatched by the overlay name found in guest memory.
4. **Runtime.** The EE side runs the generated code with a guest thread scheduler; the IOP side runs
   the game's own IRX modules (sound drivers, streaming) on PS2Recomp's R3000 interpreter with an
   SPU2 model; DMA, VIF1 and GIF are modelled; VU1 runs on a cycle-accurate interpreter or, with
   `PS2X_VU1_KZVU=1`, on PCSX2's microVU; the GS is a multi-threaded software renderer; movies go
   through an IPU/MPEG path on FFmpeg.
5. **Testing.** Runner snapshots save the whole machine state between frames (sectioned format that
   later builds can still load); `PS2X_DETERMINISTIC=1` takes guest time from cycle charges instead
   of the wall clock, so two runs give identical per-frame RAM/VRAM/GS hashes; checkpoints pair our
   snapshots with PCSX2 save states for picture comparisons; `tools/regress.ps1` scores dumps against
   a baseline by content. Every bug fixed so far, with cause and evidence, is in `docs/PROGRESS.md`.

## Status (2026-10-11)

- Boots, intro movie, title, menus, new game, scenario 1 dialogue, battle map, battle animations,
  the intermission, saving to and loading from the memory card: **scenario 1 plays to the end.**
- Speed: the battle map runs at about **40 frames/s** with PCSX2's microVU as the VU1 backend
  (optional switch `PS2X_VU1_KZVU=1`), up from about 3 with the original VU1 interpreter.
- Work in progress: scenarios 2 onward, battle-scene speed, sound and movie polish.
- Progress log with every bug ("wall") found and how it was fixed: [`docs/PROGRESS.md`](docs/PROGRESS.md).

## What is here

- `tools/`: everything used to build, run, test and compare the port: run scripts, regression and
  scene checks against PCSX2 captures, snapshot checkpoints, a VU disassembler, GS memory viewer,
  RAM search, profiler charts and more. Each tool has a `--selftest` / `-SelfTest`.
- `config/`: per-game configuration (bindings, entry points, checkpoints, pad routes, RAM map).
- `docs/`: how it works and how it is tested (`TESTING.md`, `TOOLS.md`, `VU1-PLAN.md`, `STEP0.md`,
  `DISTRIBUTION.md`, ...).
- `third_party/PS2Recomp`: our PS2Recomp fork (branch `srwz`) as a submodule. Its runtime changes
  are game-agnostic: runner snapshots (save states), deterministic runs with per-frame hashes,
  headless runs, crash reports and a stall watchdog, many hardware and SDK fixes, VU1 counters and
  the optional microVU backend.

## Building

Windows, Visual Studio 2022 Build Tools (MSVC), CMake, Python 3, Ghidra (for the function map).
The full cycle is described in [`CLAUDE.md`](CLAUDE.md) ("Layout") and [`docs/TOOLS.md`](docs/TOOLS.md).
A one-step build for players is planned ([`docs/DISTRIBUTION.md`](docs/DISTRIBUTION.md)).

## Who did what

Plainly, with rough sizes (lines of code, 2026-10-11) so nobody gets more credit than they earned:

- **[PS2Recomp](https://github.com/ran-j/PS2Recomp)** by ran-j and about 21 contributors (GPL-3.0):
  the recompiler that translates the game, and the runtime it runs on. It was about 102,000 lines
  when this project started from it; our fork adds about 37,000 (fixes, save states, deterministic
  runs, tests). The runtime that runs the game is still mostly their work.
- **[PCSX2](https://github.com/PCSX2/pcsx2)** (GPL-3.0-or-later): the reference emulator every
  result is compared with, and the source of much of what is known about the hardware. Its microVU
  engine and VU interpreter (about 16,000 lines, years of work) run the 3D math chip in the fast
  mode.
- **[KillzoneRecomp](https://github.com/drakolordx7/KillzoneRecomp)** by drakolordx7: `kzvu`, about
  2,000 lines that package PCSX2's microVU as a library with tests. Small, but it is what turned
  3 frames per second into 40 here.
- **Claude** (Anthropic), as a team of coding agents: wrote this repository (about 18,000 lines of
  tools, docs and configuration), our PS2Recomp fork's changes, and the bug investigations.
  `CLAUDE.md`, `START-HERE.md` and `docs/HANDOFF.md` are the working notes the agents follow.
- **Wyn**: started and directs the project, set its rules (exact checks, compare with PCSX2, no
  rushing), made the decisions, tested against PCSX2, and found the KillzoneRecomp lead.
- **Also used:** [Ghidra](https://github.com/NationalSecurityAgency/ghidra) with the
  [ghidra-emotionengine-reloaded](https://github.com/chaoticgd/ghidra-emotionengine-reloaded)
  extension (the game's function map and decompiles);
  [retro-trans/SRW-Z](https://github.com/retro-trans/SRW-Z) (documentation of the game's LZ format,
  text renderer and message window);
  [god-of-war-recomp](https://github.com/KIexster/god-of-war-recomp) and other PS2Recomp ports whose
  published notes and measurements guided our choices (`docs/VU1-PLAN.md`; no code taken from them).

## License

GPL-3.0 (see [`LICENSE`](LICENSE)), as PS2Recomp; builds with the microVU backend also link PCSX2
code (GPL-3.0-or-later). Super Robot Taisen Z belongs to its owners; this project contains none of
it.

## Contact

Wyn: Discord username `_wyn_`
