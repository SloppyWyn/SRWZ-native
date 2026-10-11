# From the end of the opening movie to the first menu (2026-10-06)

Look-ahead study: what the game runs between `MPG/OPPRG.PSS` and the title menu, which SDK
calls it makes on the way, and where the next walls probably are. Static analysis only
(Ghidra decompiles, `local/recomp/srwz.toml`, the runtime stubs as of PS2Recomp 43c909b plus
uncommitted Wall 9 work, and the GS dumps already in `local/runs/`). Nothing was run.
Argument-reading bugs in stubs are listed in `docs/STUB-AUDIT.md`; this document cross-references
them and does not repeat them.

## 1. Control flow

Everything up to and including the first menu is **main-program code**. `flow.bin` is not on
this path: it is loaded later, in state 0x4b (see the end of this section).

### The main loop: `FUN_00140ef0` (called from `entry`)
Boot init, then `do { … switch (state @0x523930) … } while (1)`, one pass per frame. Each pass
also polls the pad (`FUN_0018d7d0`), pumps the async CD loader (`FUN_00140d00` → `FUN_00139460`),
builds the frame's DMA chain, sends it with `sceDmaSend`, waits for the DMA (BC0F loop), and calls
`sceHiGsDisplaySwap`. Boot sets state = **0x2c**. The states on this path, in order:

| State | Code | What it does |
|---|---|---|
| 0x2c | `FUN_0018ed10` | Boot memory-card check (`sceMcGetInfo`/`sceMcSync`/`sceMcGetDir` on `/BISLPS-25887*`), then 0x51. Already passes today. |
| 0x51 | inline | → 0x1d. |
| 0x1d | `FUN_00196260(0x1d)` | Loads the logo pictures from `data/vt1.BIN` (LZ), starts sound 100 (`FUN_00181350(100,1,0,0,3,0)`), → 0x20. |
| 0x20 | `FUN_00196a60` / `FUN_00196490` | Banpresto logo, copyright screen. When done: `[0x523948]=0`, `[0x523950]=0`, → 0x17. (Entered as 0x1e it would skip the movie and go to state 0.) |
| 0x17 | inline | Suspends the second thread (`SuspendThread(0x46e648)`), removes the VBlank/timer handlers (`FUN_00140390`), → 0x18. |
| 0x18 | `FUN_0018c130`, `FUN_0018c270` | **The movie**, see below. Then state = `[0x523950]` = 0; if the movie was skipped with START (`gp-0x792c` = 1) state = 1. Re-adds the interrupt handlers (`FUN_001402f0(2,0x140430)`, `(3,0x140440)`) and `ResumeThread`. |
| 0 / 1 | inline | Remember 0/1 in `[0x523920]`, → 0x52. |
| 0x52 | `FUN_00155be0(0 or 1)` | Sets state 2 and calls `FUN_00154be0` (title set-up, below). |
| 2 | `FUN_00155c50` | Title per-frame: sub-state `[0x58e978]` 100 → wait for the sound/BGM loader to be idle → 10000 → 0x2711 → 0x2712, then every frame `FUN_00154fb0` (title + first menu). |

### Before the movie: `FUN_0018c130`
- `FUN_0018bee0`: **saves three GS regions to main memory** with `sceGsSetDefStoreImage` +
  `sceGsExecStoreImage` (blocks 0x1a40, 0x2080, 0x26c0; BW 10, PSMCT32, 640x160 each, i.e. the
  whole range 0x1a40-0x2d00 the movie's two 720x480 buffers will overwrite).
- `FUN_00147a60`: `sceHiGsDisplayMode(4)` → `sceGsResetGraph(0,1,2,0)` (interlaced NTSC, **field**
  mode), `sceHiGsDisplaySize(640,448)`, two `sceHiGsDisplaySwap`.
- Allocates the movie buffers (EE heap; 0x6000 + 0x800 bytes of IOP heap via `sceSifAllocIopHeap`).

### The movie: `FUN_0018c270` → `FUN_0018b260`
- `FUN_0018c270`: mutes/sets SPU volumes (`sceSdRemote` 0x8130/0x8150/0x8010), opens the file
  (`FUN_0018c500`: cdrom0 path → `sceCdStInit` + `sceCdSearchFile`(guest `0x12ec70`) + `sceCdStStart`;
  host path → `sceOpen`/`sceIoctl`/`sceLseek`), plays it, closes it (`FUN_0018c890`: `sceCdStStop`,
  `sceSifFreeIopHeap`), restores SPU volumes, frees the buffers, then:
  - `FUN_00147b50`: `sceHiGsDisplayMode(0x201)` → `sceGsResetGraph(0,1,2,1)` (back to **frame**
    mode), `sceHiGsDisplaySize(640,224)`, two `sceHiGsDisplaySwap`.
  - `FUN_0018c020`: **restores the three saved GS regions** with `sceGsSetDefLoadImage` +
    `sceGsExecLoadImage` (+ `sceGsSyncPath`).
- `FUN_0018b260` (player): `sceMpegInit`, `sceSdRemoteInit`, audio ring set-up (`FUN_0018c9b0`),
  black frame (`FUN_0018be30`, GIF DMA started by hand in `FUN_0018b230`), `sceMpegCreate`,
  callbacks (`sceMpegAddStrCallback` 0/2, `sceMpegAddCallback` 1 = `0x18bb50` no-data, 4 =
  `0x18bcd0` timer wait, 0), pre-fill (`FUN_0018bd70` → read callback `0x18c8f0` = `sceCdStRead`,
  then `sceMpegDemuxPssRing` at `0x11a1d0`), DispEnv via `sceGsSetDefDispEnv`/`sceGsPutDispEnv`.
  Frame loop: `while (!sceMpegIsEnd)`: timer 0 reset, `sceMpegGetPicture`, upload, `sceGsSyncPath`,
  start the audio block transfer once (`FUN_0018cad0` → `sceSdRemote(1,0x80e0 BlockTrans,…)`),
  feed the demuxer until `RCNT0 >= 500`, `sceGsSyncV`, flip DISPFB, `sceGsPutDispEnv`, pad poll
  (`FUN_0018d7d0`); **START** (`[0x59c24e] & 0x800`) sets `gp-0x792c` = 1 and leaves.
  On exit: two flips, `FUN_0018cb50` (audio stop), `sceMpegReset`, `sceMpegDelete` (guest
  `0x115bb0`, just `return 1`), four `sceGsSyncV`.
- End detection: `sceMpegIsEnd` is **guest code** at `0x115d18`, `return *mpeg->sys` (`*(mpeg+0x40)`).
  Only the runtime's `sceMpegGetPicture` writes that word (`MPEG.cpp` ~2410), once the stream end
  was demuxed and every picture was shown. That is Wall 9's territory.
- The no-data callback `0x18bb50` reads with `FUN_0018bd70(...,1)`, which **loops until the read
  callback returns non-zero**: if the runtime ever invokes it after the data runs out, and
  `sceCdStRead` keeps returning 0, the game spins there forever. On hardware the PSS end code stops
  the demuxer before that can happen.

### Title set-up: `FUN_00154be0`
`FUN_0018a890(5,0x25d)` → `FUN_00181350(0x25d,0,0,0,1)` queues **title BGM 0x25d** (sound-loader
state 200), then **spins** `while ([0x597990] != 0) FUN_00189b70(1)` until the BGM loader is
idle (see wall 3). Then allocates the title object table (0x3a0 bytes), reads the title data from
`data/vt1.BIN` (`FUN_00139620`: async CD loader `0x139460` → `sceCdDiskReady`/`sceCdRead`/
`sceCdSync`/`sceCdGetError`), decompresses it (LZ `0x1c6c40`), sets up 10 sprite objects and
`FUN_001a4f20`. Param 0 → `FUN_00154bc0` (full intro), 1 (movie skipped) → `FUN_00154ad0`.

### Title and first menu: `FUN_00154fb0` (sub-state `gp-0x7a2c`)
- 0: object 0x6c animates the logo in, with sound effects (`FUN_00180e80`, `FUN_00181830(0x8529)`); → 1.
- 1: **"PRESS ANY BUTTON"**. Waits for any *newly pressed* button (`[0x59c24e] != 0`). After 600
  frames (10 s) without one it fades out (`FUN_0017d6c0`) → 5 → 8 → state 0x37 = **attract demo**.
- 2: menu items appear (objects 6, 2, 1, 0).
- 3: **the first menu**: up/down in `[0x59c252]` (auto-repeat, 0x1000/0x4000), confirm with
  `[0x59c24e] & 0x820` (START or circle; the game's bits are the pad bytes swapped).
- 4 → 7: leaves by item: 0 → state 0x48 (new game, `FUN_001a3ec0`/`FUN_001a4130`), 1 → load
  (`sceMcGetInfo`/`sceMcSync` loop, then state 0x2a = `FUN_00193c40`/`FUN_00193ce0`, the mc
  browser: `sceMcGetDir`/`sceMcOpen`/`sceMcReadFast`/`sceMcClose`; nothing calls `sceMcRead`: every
  read goes through the `j sceMcReadFast` thunk `0x195490`, saves write with `sceMcWriteFast`
  (`0x192328`, and `FUN_001954a0` for sizes >= 0x10000) or `sceMcWrite` (`FUN_001954a0`, smaller
  sizes)), 2 → state 0x24, 3 → state 0x3d.
- Every frame (sub-states < 7): uploads the logo picture (`FUN_00136150`: 0x46000 bytes PSMT8 to
  block 0x2fc4, CLUT 0x40 qwords to 0x2fc0), draws it as one sprite (giftag NLOOP 7: TEX0
  TBP 0x2fc4 / TBW 10 / PSMT8 / CBP 0x2fc0, ALPHA, TEST 0x5000f, two sprite vertices) twice for a
  cross-fade, then `FUN_001a5060` (uploads for the 2D objects) and `FUN_001ad1a0` per object.

### Pad: `FUN_0018d7d0` (every frame, main loop and movie)
State `[0x59c210]`: 0 → `scePadGetState` must be 6 or 2 → `scePadInfoMode(CURID)`: 4 (digital) →
0x28 → `InfoMode(CUREXID)` non-zero → 0x29 → `scePadSetMainMode(0,p,1,3)` → 0x2a → wait for
GetState != 5 → 0 → CURID 7 → 0x46 → `scePadInfoAct(-1)`, `scePadSetActAlign` → 0x47 → 99.
In 99 it reads with `FUN_0018d3c0` (guest wrapper of `scePadRead`): buttons byte-swapped and
inverted into `[0x59c24c]` (held), `[0x59c24e]` (newly pressed), `[0x59c250..]` (repeat); in mode 7
the left stick also yields directions (< 0x41 / > 0xbf) and **ORs them into the top nibble**.

### flow.bin (after the first menu)
State 0x4a/0x4b loads `STAGE.BIN` entry 0 (= `flow.bin`) to 0x756700 with `FUN_001762c0(0)` →
`FUN_00175f70` (CD read + LZ) and calls its `0x756780` (allocates the 0xa0-byte flow object, starts a
fade, → state 0x4c); state 0x4c calls `0x7568d0` each frame (runs `FUN_007592e0`, returns to state
`[0x523940]` when done). It is the between-stages controller, not part of the title.

## 2. SDK and runtime-bound functions on the path

B = bound to a runtime handler in `srwz.toml`; G = runs as guest code.

| Function | Addr | | Notes on the stub |
|---|---|---|---|
| sceMpegInit / Create / AddStrCallback / AddCallback / GetPicture / Reset | 0x115b00 / 0x114f58 / 0x11a1f0 / 0x115d70 / 0x115bf8 / 0x115c98 | B | Being changed for Wall 9. `sceMpegReset` (`MPEG.cpp` ~2550) also writes 0 to the **fixed address 0x171904**, a leftover from another game; in SRWZ that is code, so harmless today. |
| sceMpegIsEnd, sceMpegDelete | 0x115d18, 0x115bb0 | G | IsEnd reads `*mpeg->sys`, which only GetPicture sets. Delete is a guest `return 1`: the runtime's per-mpeg state is never erased. |
| sceMpegDemuxPssRing | 0x11a1d0 | B | Runtime demux (Wall 9). |
| sceCdStInit / StStart / StRead / StStop / StSeekF | 0x12fc28 / 0x12fc58 / 0x12fd28 / 0x12fcf0 / 0x12fc90 | B | Movie stream. |
| sceCdDiskReady / Read / Sync / GetError, sceCdInit, sceCdMmode | 0x12f698 / 0x12f8b8 / 0x12ee80 / 0x12fa98 | B | Title data (`vt1.BIN`); same path as the logos, already works. sceCdSearchFile 0x12ec70 is G. |
| sceOpen / Ioctl / Lseek / Close | 0x120570 / 0x1210e8 / 0x120978 / 0x120800 | B | Only for `host0:` movies, not taken from the disc. |
| sceSdRemoteInit, sceSdRemote | 0x127530, 0x127688 | B | sceSdRemote has the zero-register stack fallback (STUB-AUDIT #1). The post-movie calls (0x8130/0x8150/0x8070/0x8010) use 4-5 args; BlockTrans start (`0x18cad0`) has non-zero t0-t2; BlockTrans stop (`0x18ca50`) has t0=t1=0 and reads caller locals, ignored by the stop branch today. |
| sceSifAllocIopHeap / FreeIopHeap | 0x123520 / 0x123688 | B | Fine. |
| sceSifCallRpc, sceSifCheckStatRpc | 0x11f130, 0x11f330 | B | BGM/sound RPCs (client 0x597a00, mode 3 = no-wait) polled with CheckStatRpc. Register pack plausible on these calls (send 0x10/0x40, recv 0x5979c0). STUB-AUDIT #2. |
| scePadGetState / InfoMode / SetMainMode / InfoAct / SetActAlign / Read / SetActDirect | 0x130a28 / 0x130e50 / 0x130f88 / 0x130c20 / 0x131108 / 0x1309b0 / 0x131040 | B | Checked: digital at open, CUREXID non-zero, SetMainMode switches to 0x73 with an exec-cmd transient, sticks centred at 0x80, `data[0]=0`. Keyboard: arrows, Z/X/C/V, Enter = START. Movie skip already uses this. |
| sceMcGetInfo / Sync / GetDir / Open / Close (Read) | 0x132470 / 0x132350 / 0x132660 / 0x131d10 / 0x131e70 | B | GetInfo reads format from t0, GetDir maxent/table from t0/t1: correct. |
| sceGsSetDefStoreImage / ExecStoreImage / SetDefLoadImage / ExecLoadImage | 0x10d758 / 0x10da18 / 0x10d570 / 0x10d898 | B | **Broken address scale, see wall 1.** Also private 12-byte struct, return 0 instead of 7/6 (known). |
| sceGsSetDefDispEnv / PutDispEnv / SyncV / SyncPath / ResetGraph | 0x10c738 / 0x10cab0 / 0x10d1c0 / 0x10d258 / 0x10c480 | B | Fixed in Walls 7-8. ResetGraph is called twice more (field, then frame) via HiG. |
| sceDmaSend | 0x10e5b8 | B | Main loop, already working. |
| sceHiGsDisplayMode / Size / Swap / Set | 0x16d148 / 0x16d258 / 0x16d3e8 / 0x16cd48 | G | Read the game's gparam copy at 0x3f3ca0, which the runtime never writes (open item in PROGRESS). |
| libvu0 maths, scePad wrapper, LZ, async loader, sound loader | 0x10ed20-0x10f250, 0x18d3c0, 0x1c6c40, 0x139460, 0x189b70 | G | |
| FlushCache, SuspendThread, ResumeThread, ChangeThreadPriority | | syscalls | Already exercised before the movie. |
| printf | 0x19f0c8 | B | Error paths only. |

Direct hardware access on the path (no SDK): GIF DMA channel 2 started by hand (`0x18b230`,
TADR/CHCR 0x105, chain mode), IPU-to DMA channel 4 in the no-data callback (`0x18bb50`), timer 0
(`RCNT0_MODE = 0x83`), `GS_CSR` read each frame (field bit), DMAC STAT/PCR + BC0F wait in the main
loop.

## 3. What the title and menu need from the hardware side

- **The title is drawn, not a movie.** PCSX2: black, then the logo fades in on grey (BGCOLOR or a
  clear), then a nebula background and "PRESS ANY BUTTON" (`local/reference/title-*.jpg`). The
  logo is a 640x448 PSMT8 picture uploaded every frame to block 0x2fc4 with its CLUT at 0x2fc0 and
  drawn as alpha-blended sprites; the rest are 2D sprite objects (`FUN_001ad1a0`, scaled/rotated
  with libvu0 macro code on the EE, emitted as plain GIF packets: giftags NLOOP 2/3/7).
- **No VU1 or VIF work found on this path** beyond the per-frame HiG call `FUN_003e26c0` the logos
  already run. Font: the in-Z-buffer small font (Wall 8) is re-sent by the main loop's own packets
  (`FUN_00136330(…,0x1180,8,0x24/0x2c,…)`), not via LoadImage.
- **New compared with the logo/copyright screens:**
  1. The GS **save/restore around the movie** (StoreImage = GS-to-host readback through
     `consumeLocalToHostBytes`, then LoadImage). The logos never read GS memory back.
  2. A display-mode round trip: field mode 640x448 for the movie, back to frame 640x224 after.
  3. A 0x46000-byte texture upload *every frame* (logos upload once), and alpha cross-fades.
  4. First use of the BGM path (sound-loader state 200: ezbgm RPCs, `cdrom0:\BGM\BGM.BIN` opened
     on the emulated IOP). The logos only used sound path 3.
  5. Pad input actually matters (menu, "any button"), and an attract-mode battle demo after 10 s
     idle.

## 4. Likely walls, most likely first

1. **`sceGsExecLoadImage` / `sceGsExecStoreImage` put data 8x too far into GS memory.**
   `Stubs/GS.cpp:617` and `:682` compute `dbp/sbp = vram_addr * 2048 / 256`, but libgraph's
   `dbp` is already a block address (the game's own `0x10d570` shifts it straight into BITBLTBUF
   bits 32-45). Masked to 14 bits, the save/restore of 0x1a40/0x2080/0x26c0 goes to
   0x1200/0x0400/0x1600 instead: the real 0x1a40-0x2d00 range keeps the movie's last pictures, and
   the restore writes 1.2 MB of stale data over the frame buffer and the Z/font area. Evidence
   beyond the code: the boot upload in `FUN_00139f60` (CLUT 0x2ca9, PSMT4 256x256 at 0x2caa) is at
   **0x2548/0x2550** in `local/runs/gsdump_300.bin`, and 0x2ca9/0x2caa are empty; rendered with
   `gsview.py --psm T4 --bp 0x2550 --bw 4 --cbp 0x2548 --cpsm CT16` it is the UI digits/labels
   sheet (numbers 1-24, Pls/10, 援護, 艦防). So that texture is already misplaced since boot (a map
   screen wall later), and anything the title takes from 0x1a40-0x2d00 will show movie pixels.
   Fix: use `vram_addr` as the block address (and ideally write libgraph's real GIF packet into
   the guest struct, as STUB-AUDIT already suggests). The unit test only round-trips, so it passes
   with the bug.
2. **Movie end not reached or not left (Wall 9).** The loop exits only on `*mpeg->sys != 0`,
   which only the runtime's GetPicture sets; and the game's no-data callback (`0x18bb50` →
   `FUN_0018bd70(…,1)`) spins while `sceCdStRead` returns 0. Evidence: run34 stops at ~171 s with
   no stall in the game code reported yet; PCSX2 reaches the title ~5 s after the last text.
3. **Hang in the title set-up waiting for the BGM loader.** `FUN_00154be0` loops
   `while ([0x597990]) FUN_00189b70(1)`; state 200 → 0xc9 → … needs ezbgm RPC replies
   (`sceSifCallRpc` no-wait + `sceSifCheckStatRpc` on client 0x597a00, commands 0x90e0/0x9060)
   and the IOP-side open of `cdrom0:\BGM\BGM.BIN`. First use of this branch (the logos used
   path 3). A missing IOP file/CD service or a lost RPC completion = black screen after the movie.
   Check with `PS2X_TRACE_STUBS=SifCallRpc,SifCheckStatRpc` right after the movie.
4. **Attract demo after 10 s at "PRESS ANY BUTTON".** With no key pressed, sub-state 1 → 5 → 8
   → main state 0x37/0x38 → `FUN_002e6f90` / `FUN_002e37f0`: the battle-animation player
   (HiG, VU1 microcode, `BTL/` data). Evidence: `FUN_00154fb0` case 1 countdown 600 and case 8.
   Unattended runs will walk into battle graphics; press Enter in test runs or treat this as the
   next wall after the title.
5. **Display mode round trip.** `sceHiGsDisplayMode` calls `sceGsResetGraph` twice (field for the
   movie, frame after); the HiG code reads the gparam copy at 0x3f3ca0 that the runtime never
   updates, and the stub's DH is `h-1` in frame mode where libgraph gives `2h-1` (PROGRESS, Wall 8
   notes). After the movie the copy matches the ELF's frame-mode values again, so this is a
   lower risk; the symptom would be a half-height or offset title picture.
6. **Pad.** Looked right (see section 2). Risk only if the transient exec-cmd state is never seen
   (the 0x2a/0x47 waits need GetState != 5 on a later frame, which the stub provides), or if a
   gamepad reports sticks off-centre: in mode 7 that ORs direction bits into the "newly pressed"
   word and would fire "any button" by itself.
7. **Load menu / memory card.** Stub argument reads are correct; the boot check already passes.
   Risk is in the mc0 directory emulation (`/BISLPS-25887*` listing) once item 1 is chosen.
8. **`sceSdRemote` zero-register fallback** (STUB-AUDIT #1): on the path (movie audio stop) but
   harmless in today's stop branch.
