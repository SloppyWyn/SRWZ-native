# Step 0: what's on the disc (2026-10-06)

Go/no-go survey of Super Robot Taisen Z (PS2) for a native PC port by static
recompilation. Everything here can be reproduced from your own disc with
`python tools/unpack.py disc/srwz.iso`; the numbers came from the scripts in `tools/`
and short one-off checks.

**Verdict: go.** All of the game's code can be found and unpacked ahead of time,
the code that loads at run time is simple, and the graphics and sound go through
Sony's standard libraries. The hard parts are the ones every PS2 game has (the GS,
VU1, sound), not anything special to this game.

## The disc

- `SLPS_25887`, `VER = 1.04`, NTSC, 66 files, 3,758,358,528 bytes (plain 2048-byte sectors).
- Boot program `SLPS_258.87`. PCSX2-style CRC (XOR of the ELF's words): `321C5C3B`.
- The addresses in retro-trans/SRW-Z's notes (LZ decompressor `0x1C6D70`, script
  interpreter `0x1E5F20`, `MessageWindow::setText` `0x20C9B0`, glyph blit `0x13A290`)
  are function starts in this program: their findings apply to this build.

| Folder | Size | What |
|---|---|---|
| `SOUND/CCC.MSH` + `CCC.MSB` | 1.2 GB | voices and effects, streamed (multi_streamer) |
| `MPG/*.PSS` | 760 MB | opening, mid and ending movies (MPEG-2, played through libmpeg/IPU) |
| `BTL/` | 505 MB | battle animations, backgrounds, cut-ins, battle dialogue (`TWP.BIN` alone 425 MB) |
| `BGM/BGM.BIN` | 166 MB | music, streamed (ezbgm) |
| `DATA/` | 166 MB | `STAGE.BIN` (the stage overlays), `COMPDATA.BN`, `VT1.BIN`, `MTV_*` … |
| `DMY/DMY.BIN` | 117 MB | padding |
| `MAP/` | 53 MB | 3D map models, map names |
| `EFF/VEFF2DX.BIN` | 35 MB | 2D effects |
| root | | `SLPS_258.87`, `IOPRP310.IMG`, `SYSTEM.CNF`, `VMAP.DAT` |

## Main program (EE)

- ELF, stripped (no symbols), built with **Metrowerks CodeWarrior MIPS C 2.4.1.01** (C++,
  vtables), Sony SDK **3.1.0** (`PsIIlibvu0 3100`, `IOPRP310.IMG`). `gp = 0x4537f0`.
- One loaded segment at `0x100000`, 3.3 MB on file, 6.1 MB in memory (up to `0x6d6800`).
- Code `0x100000`–`~0x3f2000`: ~772,000 instructions, ~10,800 `jr ra` (roughly the
  number of functions). Then data; one VU1 microcode block at `0x3ef110`–`0x3f3878`.
- Instruction mix: FPU 2.1 %, 128-bit `LQ`/`SQ` 2.4 % each (mostly 16-byte copies),
  MMI 776, VU0 macro mode (`COP2`) 601 + 295 `LQC2`/`SQC2`, 168 `SYSCALL`.
  Little exotic SIMD; VU0 is used lightly (libvu0 maths).
- The ELF also declares 208 empty segments (one at `0x6d6800`, 206 at `0x756700`, one
  at `0x789d00`): the linker's slots for the overlays below.

## Code loaded while the game runs (overlays)

Both containers use the game's LZ format (`tools/banlz.py`). Each overlay starts with a
Metrowerks `MWo3` header: version, load address, text size, data size, bss size, end
address (twice), file name.

- **`compdata.bin`** (`DATA/COMPDATA.BN`): loads at `0x6d6800`; `0x8c0` bytes of code,
  `0x7f600` of data. The main program calls it at `0x6d69e0`, `0x6d6bf0`, `0x6d6ef0`.
- **205 stage overlays** (`DATA/STAGE.BIN`: `flow.bin`, `stg_001.bin` … `stg_611.bin`,
  `stg_901.bin`, `stg_clear.bin`), **all at `0x756700`**. In all: 466 KB of code
  (~116,000 instructions, ~2,900 functions) and 11.2 MB of data. The code is plain
  integer MIPS: no VU0, no MMI, 21 FPU instructions, 10,163 calls into the main
  program. Each stage's events are compiled code, with its text in the overlay's data.
  (What retro-trans calls the 205 "scenario records" are these overlays.)
- **Nothing else**: every other data file was scanned for MIPS code and `MWo3` headers.

For the recompiler: the main program, `compdata.bin` and 205 overlays sharing one
address. Calls into `0x756700+` need a dispatch by whichever overlay is loaded (the
same problem N64Recomp solves). C++ vtables mean indirect calls everywhere, so every
function entry has to be found (Ghidra's analysis export is PS2Recomp's preferred input).

## Graphics

- Sony **HiG/HiP** (the SDK's high-level graphics framework). Plugins named in its
  messages: Micro, Frame, Shape, Tex2d, Anime, Camera, Clip, Clut-bump, FishEye, Hrchy.
  Plus libgraph (`sceGs*`).
- **VU1 microcode**: one ~18 KB block with 8 `MPG` uploads, most likely HiG's standard
  micro programs. None in the overlays or data files.
- Movies: libmpeg + IPU (MPEG-2 in `.PSS`).

## IOP (sound, disc, controller, memory card)

All standard Sony modules; the only non-library ones are Sony's sample sound drivers.

- `IOPRP310.IMG`: the SDK 3.1.0 replacements (SYSMEM, LOADCORE, SIFCMD, SIFMAN,
  THREADMAN, IOMAN, MODLOAD, FILEIO, CDVDMAN, CDVDFSV, LOADFILE, TIMEMANI, ROMDRV,
  EESYNC, SYSCLIB, STDIO).
- `MODULES/MODULE.BN` (LZ): sio2man 3.17, padman 4.34, mcman 2.48, mcserv 2.16,
  Sound_Device_Library 3.3 (libsd), sdr_driver 4.1, cdvd_st_driver 2.2,
  Midi_Sequencer_Module 2.2, SPU2_Synthesizer_Module 1.5, multi_streamer 1.2,
  ezmidi_driver 1.3, ezbgm_driver 1.2.

## Where the work is

1. **CPU**: large but mechanical. ~890,000 instructions in all, found ahead of time.
2. **GS (graphics chip)**: the same for every PS2 game. Plan: use PCSX2's GS code (GPL-3).
3. **VU1**: 8 standard micro programs. Run them on a VU interpreter first; native versions later.
4. **Sound**: emulate the IOP and SPU2 first (PS2Recomp has an IOP emulator now);
   replacing the sound drivers' RPC calls with native code is possible later, since
   they're all known Sony modules.
5. **Movies**: three MPEG-2 files, decoded with ffmpeg.
