# Distribution (for later: after the game plays)

Nothing here is built yet. This records the model we intend to use, so the code we write now
does not close any of these doors.

## What the player needs
- **Their own disc image only.** No BIOS: the runtime provides the PS2 system services itself
  (the kernel calls and the SDK libraries are bound to our own implementations, and the IOP
  modules come from the disc's own `IOPRP310.IMG`/IRX files). PCSX2 and its BIOS are only our
  reference tools, never part of what a player installs.
- **Verified by hash.** The installer or the first run hashes the image (and, for a partial
  match, the boot ELF `SLPS_258.87`, VER 1.04) and refuses anything else with a clear message:
  wrong region, wrong revision, or a bad rip (with which file differs). The runner already takes
  the image path as an argument (`argv[2]`) and reads it directly; nothing is copied out of it.

## What we ship
The runtime plus the recompiled game program, the same model as the N64 recompilation projects
(the player brings the ROM, the project ships no game data). Two choices for the recompiled part,
to decide later:
- **Plan A, prebuilt executable.** Simplest for players. Caveat: the executable is a translation
  of the game's code, so shipping it is closer to shipping the game than plan B is.
- **Plan B, generate on the player's machine.** The installer runs our pipeline on the code from
  the player's own disc: unpack, `ps2_recomp`, overlays, compile. Cleanest legally (we ship only
  our own code and tools). Cost: a compiler step in the installer (an MSVC or clang toolchain
  download or bundle) and a few minutes of build time; with grouped output and `lean_codegen` the
  full build is ~5 minutes on Wyn's machine. The Ghidra function map and our configs
  (`config/`, `srwz.toml` inputs) are ours and ship as data; the disc supplies the code.
Never ship: the disc image or anything unpacked from it, the BIOS, PCSX2, game assets.

## Optional add-ons
- **English translation:** the fan patch by retro-trans (https://github.com/retro-trans/SRW-Z,
  MIT for their tools and text) as an optional add-on applied to the player's own data. Their
  licence covers their work, not Bandai Namco's rights in the game.
- Native extras (widescreen, higher resolution, HD texture packs: `docs/RECOMPILER-IDEAS.md`
  item 16) are separate options, not part of the base install.

## Second title: Super Robot Wars Z Special Disc (after Z plays through)
- It is a separate PS2 executable on its own disc, so it is a second recompilation target with
  its own per-game config (its own `config/game.json`, `names.txt`, bindings and recompiler toml
  equivalents) and its own player-supplied, hash-checked image, sharing the same runtime and
  tooling. It is also the "second title" the typed-stub default flip and other runtime changes
  should be checked against.
- The "expansion" integration is a layer above the two ports: one launcher, one shared memory
  card so the Special Disc reads Z's clear data as it does on the console, shared settings and
  pack folders (translation, HD textures), and switching between the two without leaving the app.
- Running its scenarios inside the main game's engine is a later modding question, once both
  games' data formats are documented.

## Constraints that follow for today's code
- Keep every game-specific path, address and hash in per-game config (`config/game.json`), so an
  installer can point the runtime at the player's files.
- If any PCSX2 code is ever used (GS, VU, SPU2), the project becomes GPL-3 (see CLAUDE.md).
- Keep the runtime runnable without a BIOS and without any file from `local/` other than the
  player's image.
