# SRWZ game formats and text pipeline (what we take from retro-trans/SRW-Z)

Source: retro-trans/SRW-Z (MIT, fan English translation of this game, tools and RE notes, no
game data), https://github.com/retro-trans/SRW-Z, commit `d766bc7`: `docs/LZ_FORMAT.md`,
`docs/RENDERER.md`, `docs/TECHNICAL.md`, `docs/VWF.md`, `docs/CUSTOM_FONT.md`. Read-only clone in
`local/ext/retro-trans-srwz` (nothing of theirs was run). Every address below was checked
against our `SLPS_258.87` (VER 1.04, CRC `321C5C3B`) by decompiling the body
(`tools\decomp.ps1`), not only by finding a function start there. Their docs disagree on the
disc they used (FINDINGS/TECHNICAL: `SLPS_25887` "the Best"; README: SLPS-25887 plus a Best
SLPS-73270 build; RENDERER: CRC `6081EA7F`); the bodies match ours, so their main-program
addresses hold for this build.

## LZ ("banlz"): `Lz_decodeRecord` 0x1c6c40, `Lz_decompress` 0x1c6d70

Their description matches the guest code, and `tools/banlz.py` implements it:

- varint: 7 bits per byte, big-endian, bit 0 of each byte is the stop flag; value = v >> 1.
- header: `total`, `flags` (window = `1 << (((flags >> 1) & 15) + 8)`), a skipped varint only
  if `not (window >= total and flags & 0x21 == 1) and flags & 0x40`, then a reserved varint.
  `total == 0` decodes nothing. The header parser also clears a global (`0x5a3828`).
- groups: token T (literals `T & 15`, references `T >> 4`, 0 = a varint follows), the literals,
  then, if the output is not full, the references: R, distance `d = R & 15` continued by varint
  bytes while its low bit is 0, `dist = d >> 1`, length `(R >> 4 or a varint) + 1`, copied
  byte by byte from `out - (dist + 1)`, clamped to `total`.

Differences between the guest and `tools/banlz.py` (none matters for valid data):
- the guest checks "output full" only after the literals and after a whole group's references;
  `banlz.py` stops before every reference. A stream that fills the output in the middle of a
  group's references would make the guest copy past the end (clamped length 0 becomes a
  do-while of 2^32); the game's data never does this, and an encoder must not.
- a reference before the start of the output: the guest returns 1 (error), `banlz.py` raises.
- literal or reference counts of 0 from a varint are do-while loops in the guest (wrap); not
  in valid data.

Other users of the format: the font master (`Font_init` 0x139f60 calls `Lz_decodeRecord`;
VWF.md's "separate font bitstream codec, not banlz" is wrong), `DATA/COMPDATA.BN`,
`DATA/STAGE.BIN`, `DATA/HSFC.BIN`, `MODULES/MODULE.BN`; 24 call sites in all.

## Data locations

- `DATA/STAGE.BIN`: 205 LZ records, the stage overlays (`MWo3`, all loaded at `0x756700`,
  docs/STEP0.md); retro-trans calls them "scenario records", their text is in each overlay's
  data. Their note puts the decompressed script around `0x75exxxx` with a per-line pointer
  table at `0x759xxx` (0x20 stride), which is inside that overlay range.
- `DATA/HSFC.BIN`: 4 LZ records (stage synopses).
- `DATA/COMPDATA.BN`: one LZ record, 524,032 bytes decoded (checked with `banlz.py`), loaded at
  the fixed address `0x6d6800` (overlay `compdata.bin`); per their TECHNICAL.md the pointer
  tables are at record offsets `0x904..0x61658` and a string pool (weapon/unit names) at
  `0x61680..0x7ff00`; pool pointers are absolute RAM addresses (`0x6d6800 + offset`).
- `BTL/SRVC.BIN` + `BTL/SRVC.SEG`: battle dialogue, not LZ. `SRVC.SEG` is a `u32` array of block
  offsets into `SRVC.BIN` with an EOF sentinel (ours: 354 entries, last = 3,313,040 = file
  size, increasing). Blocks start with binary fields (not pure text) followed by cp932 strings.
- UI text (skills, series titles, system messages): plain SJIS in the boot ELF.

## Text encoding: cp932

Game text is cp932 (Shift-JIS plus the NEC rows), not strict Shift-JIS: in `SRVC.BIN` all
78,649 double-byte text runs decode as cp932, 35 of them fail strict `shift_jis` (e.g. `Ⅱ` =
`0x8755`, 33 times). Use `cp932` in tools. The renderer (`Font_putText`) draws only lead bytes
`0x81..0x88` (jump table `0x433e30`) and treats single bytes `0x2e..0x3d` as control codes
(jump table `0x433e50`); `0x0a` is a newline; raw ASCII draws as blank cells.

## Text pipeline (names in config/names.txt)

`Scenario_execCommand` 0x1e5f20 (branches on the command's first byte) -> ... ->
`MessageWindow_setText` 0x20c9b0 (copies or macro-expands the line to `obj+0xc`) and
`MessageWindow_drawText` 0x20c930 (renderer vcalls on `obj+0xc`) -> `TextBox_drawText` 0x209420
-> `Text_layoutLines` 0x2212b0 (sets the cell-width flag `gp-0x7e8c` to 0x40/0x80; splits with
`Text_getLine` 0x221030) -> `Text_drawSegment` 0x220db0 -> `Text_setPen` 0x2254d0 +
`Text_drawString` 0x225290 -> `Font_putText` 0x13a290 (glyph structs, pen += 0x18, cap 0x13f)
-> `Font_flushGlyphs` 0x13aca0 (GIF packets). Name macros `$n/$F/$N`: `NameMacro_instance`
0x200f80, `NameMacro_expand` 0x2011d0, `NameMacro_substitute` 0x201590; `Sjis_charState`
0x2010b0; `Text_measureLine` 0x2229d0. Font: `Font_init` 0x139f60, `Font_copyGlyphsToCache`
0x13c5c0 (24x24 4bpp glyphs, 0x120 bytes each, into a 0x1e000-byte cache),
`Gs_buildLoadImagePacket` 0x136330.

## Claims not taken (checked and found wrong, or not function entries)

- vtable `0x447848`, singleton `0x5a3f80`, object size `0x480`: these belong to the name-macro
  table returned by 0x200f80 (their "NEEDEXP"), not to the message window. Its entries are
  0x52 bytes (2-byte token, name at +0x29), not "41-byte stride".
- 0x220e70 "per-token char fetch": it tests whether the second line starts with one of two
  2-byte markers (`0x43c7d8`/`0x43c7e0`); role differs, not named.
- 0x10d570 / 0x10d898 / 0x11b2e0 "font decompress codecs" (VWF.md): they are
  `sceGsSetDefLoadImage`, `sceGsExecLoadImage` and `FlushCache` (analyzer names), image upload.
- 0x1cd740, 0x143180 "DMA cache to VRAM", 0x13c3b0 "frame function", 0x1a781c "GIF-DMA send
  pattern": addresses inside other functions (0x1cd250, 0x140ef0, 0x13c390, 0x1a7480), not
  entries.
- DMA wait 0x10d258, `strcpy` 0x1a0d88, `strlen` 0x1a0ea0, `memalign` 0x19d058: agree with the
  names we already have (analyzer / bindings); nothing to import.
- Overlay code: they name no overlay functions, so nothing is imported for `0x756700`/`0x6d6800`.
