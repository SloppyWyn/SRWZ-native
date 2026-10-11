# VU1 speed plan (2026-10-11)

Decided with Wyn: VU1 speed is the main thing to fix (it blocks playing and slows every test).
Approved: new PS2Recomp branch `srwz-vu1x` (park `srwz-vuint`), and
Extra effort for the timing analysis (tell Wyn when it is used).

## Findings (read-only study, no runs)
- Saved map profile `local/runs/speed/baseprof.prof.txt`: `VU1Interpreter::run` 92% of the game
  thread. Almost no time in the math: ~1/3 per-cycle pipeline bookkeeping (commitReadyPipelines,
  calculatePairReadyCycle, queueVfWrite, markPairWrites), ~1/3 exact flags computed for every FMAC
  (calculateFmacExactResult, updateFmacFlags, calculateFmacProductSticky), ~10% normalizeOperand
  (~30 calls per multiply-add).
- Workload per map frame (`local/runs/speed/prof.out.txt`, VIF1 stats): ~690 MSCNT batches, ~687
  XGKICKs, ~1.6 MB PATH1 (~146 qwords per kick, ~47 vertices), ~32k vertices, roughly 1M VU pairs,
  ~230 ms. So ~1,000 host clocks per VU pair (lean interpreter 50-100, translator 5-20).
  Estimate only: Stage 0 counters confirm it.
- The microcode block (0x3ef780 onward) is 7 DMA-ret packages, 12 MPGs, not 8 programs:
  A 108 pairs, B 383 (256 at 0 + 127 at 0x800), C 399, D 333, E 250, F 319, G 269.
  Runtime images seen (`local/vu1code/`): A + stale B tail, E + stale B tail, B. C was loaded earlier.
  D, F, G: usage unknown (probably battles).
- One family: same setup at 0 (matrices) and batch skeleton (XTOP buffer, XGKICK, [E], B back).
  ~45 opcodes; no EFU, no JR/JALR, no FS* status reads, no LQI/SQI/ILWR/ISWR/XITOP.
- B = transform, directional lights (MAX/MINI clamps), clip (CLIPw + FCAND guard band -> ADC bit),
  back-face cull (OPMULA/OPMSUB into vf0, FMAND exactly 4 pairs later), DIV -> MULq 7 pairs later,
  FTOI4 position, FTOI0 colour. Software-pipelined loop: 28 pairs per lit vertex, 22 unlit.
- VU1 runs synchronously to the end (`ps2_runtime.cpp` MSCAL/MSCNT callbacks, budget 65536 never
  hit); its cycle count is not fed back into EE timing. Timing matters only where it changes values
  (MAC/clip flag reads, Q reads, VI branch quirk, XGKICK vs stores).

## Options weighed
| | HLE per program | Translator (chosen) | Finish interpreter |
|---|---|---|---|
| VU1 speed-up | ~30-50x | stage 1 ~5-15x, stage 2 ~20-50x | ~1.5-3x |
| Effort | 4-7 weeks | ~1.5-2 weeks to stage 1, 3-4 weeks to stage 2 | open-ended |
| Other games | no | yes | yes |
- HLE gains nothing here: bit-exact vs the interpreter forces the same float ops, clamps, flag
  rules and memory writes, so it is a hand translation per program.
- Interpreter steps 1-2 gave x1.05: the cost is the per-cycle design, not the details.
- Map at 30 fps needs VU1 <= ~13 ms/frame (~18x). The other ~20 ms/frame is next after that.

## Plan
0. Setup: branch `srwz-vu1x` off srwz c5ee546, worktree `local\wt-vu1x`; park `srwz-vuint`;
   frozen baseline runner `local\runs\bin-srwz-c5ee546` (094f58a predates Wall 21).
1. Stage 0 (1-2 days): `PS2X_VU1_STATS=1` (pairs, stall cycles, runs per frame, per program hash
   and entry pc) on map, dialogue, enemy phase, battle prep, battle anim; `PS2X_VU1_LOCKSTEP=1`
   (second interpreter on a copy; compare VF/VI/ACC/Q/P/I/MAC/CLIP/STATUS, VU data memory, kicked
   GIF packets after each MSCAL/MSCNT; report program + pc of first difference). Prove it:
   interpreter vs interpreter passes, a planted error is caught. Gate: re-plan if counts are far off.
2. Stage 1 (~1.5-2 weeks): analysis once per loaded image (CFG from entries, pipeline timing worked
   out ahead with the interpreter's own usage/latency tables, block specialisation on entry pipeline
   state, liveness of MAC/clip/Q); one handler per opcode, no per-cycle queues; fall back to the
   interpreter for unknown code, a second XGKICK while one is active, budget cuts. Behind
   `PS2X_VU1_FAST=1` (off = byte-identical). Order A, B, E, then C/D/F/G. Gate: lockstep clean on
   all scenes and >= 3x, runtime-reviewer, merge, verify-merge, make it default.
3. Stage 2 (1-2 weeks): generator next to ps2_recomp emits compact C++ (SSE intrinsics, small
   function per block) per known image from the same analysis; microcode block address in per-game
   config; output under `local\recomp\vu1\`; runtime picks by hash of reachable code, else stage 1.
   Lesson of srwz-vuaot: never a copy of the generic step per pair. Target VU1 ~5-10 ms/frame.

## Stage 0 results (2026-10-11, srwz-vu1x 7ea9556, `PS2X_VU1_STATS=1`)
60 s real-time headless runs from the checkpoints (`local\runs\vu1stats\`):
| Scene | flips/s | VU1 pairs per flip | programs |
|---|---|---|---|
| s01_map_menu | 3.1 | ~1.98M (B 1.83M, A 0.15M) | A, B, E |
| s01_first_battle_anim | 36 | ~67k | B, A, E |
| s01_enemy_phase | 56 | ~7k | A, B, E |
| s01_battle_prep, title_screen | 60 | 0 | - |
- ~150-160 ns of game-thread time per VU pair (~700 host clocks). The earlier 1M/frame estimate was 2x low.
- B never stalls (0 stall cycles: hand-scheduled); A stalls 31% of its cycles; E is tiny.
- C, D, F, G did not run in these scenes.
- Map at 30 fps needs VU1 <= ~13 ms/frame for 2M pairs: ~6.5 ns/pair, ~25x. Stage 1 alone will not
  reach it; Stage 2 (and maybe a VU1 thread) is needed.
- Counters are exact-neutral: checkpoint verify s01_map_menu, baseline c5ee546 vs srwz-vu1x with
  stats on, 301 ticks identical; 560/560 tests.

## Prior art: god-of-war-recomp (KIexster, GPL-3, same PS2Recomp base)
https://github.com/KIexster/god-of-war-recomp, `docs/RENDIMIENTO.md` and 28 `patches/ps2recomp-vu1-*`:
same road (direct VF/VI/ACC writes with stalls kept, lazy flags, compiled blocks ending at branch
targets/delay slots/E bits, SSE FMACs with separate rounding, XGKICK bulk transfer, opt-in VU1 and
GS threads). Their VU1 frame benchmark 775 -> ~78 ms; game 5.7 -> ~20 fps. Their per-pair compiled
copies were slower (525 vs 336 ms), like our srwz-vuaot. Exactness: per-launch fingerprints vs the
interpreter, large FMAC/DIV exact test suites. Cloned 2026-10-11 (Wyn's OK) to
`local\ext\god-of-war-recomp` (34cbe58, 4.7 MB, code and docs only; never run its scripts). Port,
don't apply: their base is PS2Recomp c5a9d02 + a SotC fork, ours is ~200 commits away.
Their VU1 patches, ~5,900 lines; read in this order: `vu1-direct` (539, direct writes),
`vu1-lazy-flags` (194), `vu1-fast-flags` (147), `vu1-xgkick-bulk`/`-flush`, `vu1-blocks` (457),
`vu1-compiled` (1,871, the generator side), `vu1-simd` (195) + `-simd-zero`/`-simd-specials`,
`vu1-zero-factor`, `vu1-sticky-preserve` (an exactness fix worth copying), `vu1-thread` (350,
opt-in VU1 thread), `vif1-unpack-fast`. Credit them in any ported commit.

## Top lead to evaluate first: PCSX2 microVU inside PS2Recomp (KillzoneRecomp)
From Wyn's ChatGPT research (`local\research\vu1-ps2recomp-research.md`, 2026-10-11, links unchecked by us):
https://github.com/drakolordx7/KillzoneRecomp (GPL, MSVC builds documented, `ext/kzvu/README.md`,
`src/kz_vu.cpp`; PS2Recomp branch drakolordx7/PS2Recomp `killzone`) integrates PCSX2's microVU JIT
with interpreter fallback: ~220-pair transform 97-98 ns/run vs 55,000 ns/run on the PS2Recomp
interpreter; 1,776 synthetic checks with 0 JIT/interpreter differences; VIF1 worker thread
(`patches/ps2recomp/0016-vif1-worker-thread.patch`) took the game ~31 -> 46 fps.
- For: PCSX2 is our reference, so its VU1 code should match PCSX2 better than our interpreter;
  likely much less work than our own translator.
- Against: PCSX2 code (CLAUDE.md: project becomes GPL-3; PS2Recomp already is); bit-exact vs our
  interpreter no longer holds (the check moves to PCSX2 comparisons); large x86 JIT to debug.
- **Decision for Wyn.** Evaluate before Stage 1: read kzvu's integration, measure on our map.
- **First evaluation done (2026-10-11):** cloned to `local\ext\killzone-recomp` (5f07000); PCSX2
  sparse checkout (pcsx2/, common/, 3rdparty/fmt, fast_float, include) at 646df006a4 in
  `local\ext\pcsx2` (20 MB). kzvu + kzvu_test build with **our** MSVC 14.44 (VS 2022 generator; they
  used VS 2026/Ninja) against **our** PS2Recomp srwz 5a15667: `local\ext\kzvu-build2`, configure:
  `cmake -S <kzvu> -B <build> -G "Visual Studio 17 2022" -A x64 -DKZVU_PCSX2_DIR=... -DKZVU_PS2RECOMP_DIR=...
  -DKZVU_BUILD_TEST=ON` (start through `Invoke-Quiet` with an argument array: `launch.ps1 -- cmake -S`
  swallows `-S`). Result: **1,776 checks, 0 failures, 0 microVU-vs-our-interpreter differences**
  (synthetic programs; flags not compared); transform benchmark (16 vertices, DIV, FTOI4, XGKICK):
  microVU 214 ns/run, PCSX2 interpreter 11,327 ns, **ours 30,941 ns (~145x)**. Known PCSX2-vs-ours
  differences: branch VI-delay edge case (vi written twice in the 4 pairs before a branch), D bit
  ignored by microVU. kzvu README: memory must be kzvu's static buffers (PS2Memory allocates
  m_vu1Code/m_vu1Data with new[]), no save-state support, `kzvuMicroWritten(0, 0x4000)` when the VU1
  code generation changes, flagHack skips unread status flags.
- **Next:** integrate behind `PS2X_VU1_KZVU=1` on `srwz-vu1x` (kzvu README "Host integration
  sketch"), snapshot VU1 regs via kzvuGet/SetVF/VI/ACC at safe points, then run s01_map_menu:
  speed (A/B), frames vs PCSX2 (`checkpoint.ps1 scene`), and our B program's culling/clipping flags.
Also from that file: BT3-Recomp MAC-flag elision 15.15 -> 11.34 ms VU time per swap (verifier
`PS2X_VUJIT_VERIFY`, 7.7M runs, 0 mismatches; ClangCL, not MSVC); god-of-war-recomp latest VU1
round 24 -> 25.2 fps; Sinan-Karakaya/DQ8-Recompiled (profile-driven VU AOT, `vu_replay.cpp`
compares compiled vs interpreter; MSVC 14.51 took ~1 h for one 498-pair routine).

## More leads from Wyn's Qwen report (`local\research\*.pdf`, prose, claims unchecked)
- **AC5Recomp** (Ace Combat 5; github.com/sal063/Ace-Combat-5-Static-recompilation, QTG128/AC5Recomp):
  records VU1 microprograms and compiles them to C, native GPU code for its renderer's VU1
  programs, `PS2_VU_RECOMP=0` falls back to the interpreter; claims 16.8 ms/frame (~59.5 fps) over a
  full mission. GCC/MSYS2, not MSVC.
- **ARMSX2/libretro ps2** (github.com/libretro/ps2): microVU0/1 JITs for AArch64, `MVU_DIFF`
  verification, byte-identical framebuffer and per-frame RAM/GPR hashes vs a pure interpreter
  (ARM only; method worth copying).
- **Red-tv141/DC2-PS2RECOMP** (Dark Cloud 2): prototype moving VU1 to GPU compute; mandatory pixel
  gate per change; an earlier native VU1 compile was 100% bit-exact but too slow. MSVC (vcvars64).
- **silentsudin/RoadTripAdventure-recomp**: VU1 microcode as native code, "20-30 fps in 3D races",
  llvm-mingw, no accuracy checks. **BlackLineInteractive/ps2xHybrid**: HLE of PDS .csl VU1 microcode.
- The report calls BT3-Recomp "MSVC" but its build is ClangCL (the ChatGPT report is right there).

## Other PS2Recomp forks with VU1 work (research 2026-10-11, read-only, ~106 forks listed)
Side branches were checked only for LightVelox and brad-richardson (GitHub rate limit). Nobody
publishes clean speed numbers. All GPL-3 unless noted.
- **MrCoolTheCucumber/PS2Recomp** main (546 ahead, 2026-08-16): x64 VU JIT with IR, Xbyak blocks,
  program cache, analysis, and a **verify mode running interpreter and JIT side by side**
  (`ps2xRuntime/src/lib/vu/ps2_vu_ir*.cpp`, `ps2_vu_recompiler.cpp`, `ps2_vu_program_cache.cpp`,
  `ps2_vu_analysis.cpp`; docs `vu-x64-recompiler.md`, `vu-backends.md`). Also Vulkan GS, fibers,
  save states. Read first.
- **Sinan-Karakaya/PS2Recomp** (upstream PR #254, open): whole-program VU1 compile from microcode
  recorded at runtime, loop/region plans, program cache, differential tests vs the interpreter
  (`vu/ps2_vu1_program.cpp`, `ps2_vu1_region_*.h`, `ps2_vu1_loop_*`); EE fibers. PR says MSVC does
  not build it yet. **soyjxck/PS2Recomp** (2026-10-11) adds SSE FMACs (`ps2_vu1_fmac_sse.h`), a VU1
  worker thread, AVX2/FMA builds (FMA would break bit-exactness for us).
- **LightVelox/PS2Recomp** branch `sotc-port`: VU1 AOT recompiler, exact-zero FMAC fast path, VU1
  image cache, GPU GS, VU1/GIF threads, save states (`ps2_save_state.cpp`), IOP SPU2/SIO2/memcard.
  Source of god-of-war-recomp's GS and EE fixes. catsandsoup/PS2Recomp looks derived from it.
- **z3xox/BT3-Recomp**: `ps2_vu1_native.cpp`, SSE kernels for 3 hot microprograms keyed by hash
  ("11.92M kicks / 0 mismatches"), paraLLEl-GS Vulkan compute GS (LGPL-3.0+ part).
- **Voicedrew11/holyland** (MIT + GPL patches): cycle-exact VU1 lift to C++ with
  `PS2X_VU1_VERIFY` (1.94M runs, 0 mismatches) was **slower** with the async GS (5.1 vs 11.5 flips/s).
- brad-richardson/PS2Recomp `ssx3` etc.: PCSX2/ARMSX2 microVU adapters (Mac/iOS/Android only).
- rrisque/PS2Recomp: SotC correctness fixes (FPU/VU0 saturation, DIV/SQRT/RSQRT operands, MMI
  HI/LO, LQ/SQ alignment): a checklist against ours.

## Exactness traps
- Same rounding (FE_TOWARDZERO), same normalizeOperand/normalizeResult, no FMA contraction, flags
  via the interpreter's double-precision rule only where live.
- FMAND reads the MAC of the FMAC issued 4 cycles earlier; FCAND clip flags likewise; MULq before a
  DIV is done reads the old Q; VI write -> branch read quirk; upper/lower same-register order.
- Microcode is re-uploaded every frame (2 MPGs per frame on the map): hash per upload.

## Checks
1. Lockstep checker on every scene.
2. `checkpoint.ps1 verify -Exe local\runs\bin-srwz-c5ee546 -ExeB <new>` on s01_map_menu,
   s01_dialogue_window, s01_enemy_phase, s01_battle_prep, s01_first_battle_anim, title_screen, plus
   scenes until the counters show all 7 packages covered. Judge battle anim by hash only (it drifts).
3. `ps2x_tests` + one test per opcode against the interpreter.
4. Speed: 3-round alternating A/B vs plain srwz (`docs/TOOLS.md`).
