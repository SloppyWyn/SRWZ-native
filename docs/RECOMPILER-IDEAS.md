# Recompiler and runtime improvement ideas (2026-10-06)

An assessment, not a change. It ranks improvements to PS2Recomp's recompiler (`ps2xRecomp`),
its generated output and the runtime parts that generated code calls into. Evidence comes from
the source (`third_party/PS2Recomp`, branch `srwz` at `ea87cd1`), the generated output
(`local/recomp/output`), the build tree (`out/build`, object timestamps of the full build at
14:03-14:13) and the newest run's log (`local/game/ps2_log.txt`, from run w10a). Nothing was
built or run for this document. Payoff figures are estimates, and each one says how it was made.

## The numbers behind it

| What | Measured |
|---|---|
| Generated files | 59,139 in `local/recomp/output` (446 MB on disk, 305 MB of `.cpp`) |
| By kind | 46,966 `entry_*` fragments (132 MB), 10,096 `FUN_*` (154 MB), 1,815 `caseD_*`, ~300 named SDK/stub wrappers, `register_functions.cpp` (13.7 MB, 164,969 table entries) |
| Fragments | 41,577 of the 46,965 `entry_*` fragments lie inside a real function. In a sample of 2,087 of them, the owner's `switch (ctx->pc)` already has a `case` for 816 (39%) |
| Generated headers | `ps2_recompiled_functions.h` 5.1 MB (59k declarations), `ps2_recompiled_stubs.h` 26 KB. Both go into the runner's PCH (`cmake_pch.pch` = **175 MB**) |
| Who needs the declarations | Only 837 of 59k files call another generated function by name (tail jumps `FUN_x(...); return;`). Every `jal` goes through `runtime->dispatchGuestBranch(target address)`, with no name involved |
| Unity batches | 1,849 (CMake `UNITY_BUILD_BATCH_SIZE 32`, by file count). Source per batch: median 88 KB, p90 382 KB, max 1.7 MB (excluding `register_functions`). 20x imbalance |
| Compile timeline (full build) | Fragment batches (1,468) finish at +0.4…3.5 min, `FUN_*` batches (315) at +4.2…10.0 min after the PCH. So about 10 min of the ~15 min build is the compile of generated code: about 35% on fragments, 65% on real functions. `register_functions.cpp` (13.8 MB) compiles in ~30 s: size alone is not the cost, control flow is |
| Exe | `ps2EntryRunner.exe` 132 MB, `.map` 107 MB, objects 1.3 GB |
| Release flags actually used | `/O2 /Ob2 /MD /MP /bigobj`, no PDB. `EnableFastReleaseMode` (`/GL /LTCG /arch:AVX2 /GS-`) is **never applied**: it is guarded by `CMAKE_BUILD_TYPE STREQUAL "Release"`, which is empty under the Visual Studio (multi-config) generator ("Build type:" is blank in `local/build-game.log`) |
| Defines in Release | `PS2_FUNCTION_LOG_TRACKER=1`, `AGRESSIVE_LOGS=1`, `PS2_RUNTIME_LOGS=1`, `PS2X_ENABLE_IOP_RPC_TRACE=1` (all CMake options default ON, made PUBLIC on `ps2_runtime`) |
| Function log | `local/game/ps2_log.txt` = **2.38 GB** after the 539 s run w10a: one flushed line per guest function entry and exit |
| Per instruction (all output) | 1,227,127 emitted MIPS instructions (~772k unique in the ELF: fragments and resumable delay slots emit code twice), 1,775,132 `ctx->pc =` stores (1.45 per instruction), 206,687 `READ*` and 144,761 `WRITE*` lambdas/blocks, 93,581 `dispatchGuestBranch` sites, 7,908 `eeCheckpointDue` loop edges |
| Unreferenced functions | 412 of 11,870 real functions (2.7% of their source bytes) are neither a direct call/jump target nor a 32-bit word anywhere in the ELF or the overlay binaries |

### What a generated function looks like
`FUN_00100230_0x100230.cpp` (a 22-instruction loop), in short:

- `PS_LOG_ENTRY("FUN_00100230_0x100230")` under `#ifdef PS2_FUNCTION_LOG_TRACKER`: a call to
  `ps2_log::log_entry` (tabs for depth, `">> name enter\n"`, `flush()`), and a guard object
  whose destructor does the same for exit. Defined in Release, so it runs.
- `switch (ctx->pc) { case 0x100240u: goto label_100240; … }` for resume points.
- Per instruction: `ctx->pc = 0x…u;` (often twice), then the operation on `ctx->r[n]` as
  `__m128i` (`SET_GPR_S32` keeps the upper 64 bits with `_mm_move_sd`), `GPR_U32(ctx, 0)`
  zero checks folded at compile time.
- Memory: `READ32(addr)` is a lambda `[&]{ return isSpecialAddress(a) ? runtime->Load32(...) :
  Ps2FastRead32(rdram, a); }()`; known MMIO addresses are emitted as a direct
  `runtime->Load32(rdram, ctx, 0x10000000u)` (good). `WRITE*` also calls `ps2TraceGuestWrite`,
  an inline no-op ("will be deleted on next release").
- Branches: `const bool taken = …; ctx->pc = slot; ctx->in_delay_slot = true; ctx->branch_pc
  = …; <delay slot>; ctx->in_delay_slot = false; if (taken) goto label;`.
- Backward branches: `if (runtime->eeCheckpointDue()) return;` (32 cycles charged).
- `jal`: set `ra`, run the delay slot, then `if (!runtime->dispatchGuestBranch(rdram, ctx,
  target, src, fallthrough, DirectCall, "JAL")) return;`. The resume label after it re-checks
  `ctx->pc` and re-runs the delay slot path when entered from the switch.
- Jump tables (`jr $v0`) become a local `switch (jumpTarget)` over the case labels: good, no
  dispatcher round trip.
- Bound SDK functions are wrappers: `ctx->pc = ra; ps2xStubTrace(...); ps2_syscalls::X or
  ps2_stubs::X(rdram, ctx, runtime); ps2xStubTrace(...)`.

### The call path (`ps2_runtime.cpp:1397`)
Every `jal` costs, out of line: `EeScheduler::checkpointDue(8)` → `accountCycles(8)` →
`PS2Memory::advanceEeTimers(8)` (an `unordered_map::find` on GIF_STAT to clear FQC, plus a 64-bit
div/mod per running timer) → `advanceIopEeCycles(8)` → `IopEmulator::runEeCycles` → **one IOP
cycle**, i.e. a full IOP scheduler iteration (`servicePendingDmaInterrupts`,
`servicePendingGuestCallbacks`, `timrman.serviceDue`, `beginNextReady`, …) per guest call; every
32nd charge also reads the host clock. Then `hasFunction(target)` and `lookupFunction(target)`
(two table lookups, plus `pushDispatchPc`), then the indirect call. For targets in an overlay
range (`0x6d6800`, `0x756700`, i.e. all stage/battle code), `hasFunction` and
`lookupFunction` each call `ps2xOverlayLookup`, which **builds a `std::string` of the MWo3 name
and does two hash-map finds**, so every call into overlay code allocates twice.

When a checkpoint is due (time slice, timer interrupt, VBlank, any event), the generated function
returns with `ctx->pc` set and the whole native call stack unwinds back to the scheduler, which
resumes at `ctx->pc` through the function table. Resume addresses mostly map to `entry_*`
fragments, which run only to the next fragment boundary and return to the scheduler again.

**The hottest loop of the movie runs in that slow mode.** The player's pacing loop
`while (RCNT0 < 500) feed_demux()` at `0x18b630`-`0x18b654` is split into
`entry_0018b630` (calls `FUN_0018bd70`, the feed) and `entry_0018b640` (reads RCNT0, branches
back to `0x18b630` with a bare `return`). Once a checkpoint has sent the loop back to the
scheduler, every iteration is two scheduler round trips (each: two mutex lock pairs, a
`std::deque` swap, debug-context publish, two table lookups, a `try` frame, `processDueDeadlines`)
instead of a `goto`. Evidence: in the last 50 MB of `ps2_log.txt`, `entry_0018b630` and
`entry_0018b640` are entered 101,103 times each, against 273,694 calls of `FUN_0018bd70`, so
about 37% of loop iterations ran through the fragments; the owner `FUN_0018b260` was re-entered
507 times in the same window. It never gets back to the owner until the function returns.

---

## Ranked list

Ranked by payoff and risk together. "Other games" means: does it change what PS2Recomp does for a
game other than SRWZ (generated code, runtime behaviour or defaults)? Those need Wyn's go-ahead.

### 1. Switch off the per-function log tracker in our builds (S, very low risk)
**What.** `PS2_FUNCTION_LOG_TRACKER=1` (from `PS2X_ENABLE_AGRESSIVE_LOGS`, default ON, PUBLIC on
`ps2_runtime`) compiles `PS_LOG_ENTRY` into all 59k generated functions: two flushed writes to
`ps2_log.txt` per guest call, plus a guard object with a destructor in every function.
**Evidence.** `ps2_log.txt` is 2.38 GB after run w10a (539 s host). The defines are in the
runner's Release `PreprocessorDefinitions` (`out/build/ps2xRuntime/ps2EntryRunner.vcxproj`).
**Payoff.** Run time: at ~35 bytes per line, 2.38 GB is ~68 M lines, ~34 M guest calls, ~63k
calls/s, ~126k `flush()` = `WriteFile` calls per second on the EE thread. At 1-3 µs each that is
**15-40% of the EE thread**, plus 2.4 GB written to disk per run (more on longer runs). It does not
show as a faster movie today (the guest clock follows the host and the movie spins on a timer), it
shows as headroom in heavy scenes and lower disk churn. Build: no `ps2_log.h`/`<fstream>` inline
code and no unwind-only destructor in 59k functions; a guess of 5-15% off the generated-code
compile, measure it. Also removes a misleading `ps2_log.txt` that nobody reads.
**How.** Either pass `-DPS2X_ENABLE_AGRESSIVE_LOGS=OFF` in `tools/configure-ps2recomp.bat` (no
PS2Recomp change; it also drops ~40 `PS2_IF_AGRESSIVE_LOGS` blocks in the stubs: check none of
our run reports depend on them first), or add a separate option
`PS2X_ENABLE_FUNCTION_LOG_TRACKER` whose default follows `PS2X_ENABLE_AGRESSIVE_LOGS` (default
behaviour unchanged for other games) and pass it OFF. The function trace it gives is replaced,
better, by item 2.
**Other games:** no (configure-time choice of ours; a new option keeps the old default).

### 2. A cheap, filterable call trace, guest names in the runtime, and crash reports (S-M, low risk)
**What.**
- Emit a name table into `register_functions.cpp` (address → Ghidra/SDK name, ~12k real
  functions plus overlay names from `ov_*.cpp`) so the runtime can name any guest pc: in the
  missing-function error (today: `trace=` with bare addresses), `formatDispatchHistory`, the
  watchdog and crash reports. `tools/addr2name.py` does this offline today.
- `PS2X_TRACE_CALLS=<ranges or names>` (e.g. `0x18b000-0x18c000,FUN_00140ef0`): trace in
  `dispatchGuestBranch` and the scheduler's resume, not in the generated code. A bitmap over the
  dense function table (858,743 slots → 107 KB) makes the off/filtered check one load and a test.
  A line: `vsync, thread id, pc, name, a0-a3, ra, sp`, written to a ring buffer flushed by a
  background thread (never `flush()` per line), plus an optional `<` line with v0. Tail jumps
  (837 files call `FUN_x(); return;` directly) need the same hook in the emitted call, or go
  through the table.
- A crash handler (`SetUnhandledExceptionFilter` / vectored handler; none exists in the
  runtime today): host stack named from the `.map` (the watchdog already has this code), guest
  pc/ra/sp/thread with names, the last 64 dispatches with names, the last 64 SDK calls (the
  watchdog's ring), optionally a minidump.
**Evidence.** Walls 3, 5, 9 were found with ad-hoc traces and decompiles of addresses; the
watchdog and `PS2X_TRACE_STUBS` show the pattern works. Generated function names already embed
the guest address, so a host stack from the `.map` is already a guest call stack.
**Payoff.** Faster wall diagnosis (fewer decompile/addr2name round trips), and the base for the
PCSX2 diff (item 6). Zero cost when off.
**Other games:** no (env-gated, additive).

### 3. Binding verification at generation time (M, low risk, report-only first)
**What.** The recompiler already decodes every bound function before replacing it. Add a pass
that, for each binding, computes from the game's own copy which argument registers are read
before written (a0-a3, t0-t3), whether it reads the caller's stack above its frame (args 9+ at
`sp+0`, `sp+8`, … in 8-byte slots), and float args (f12-f19), and compares that with the
handler's declared arity. Report mismatches; later, fail the generation on them unless the
binding carries an explicit override with a reason. Also:
- Resolve a binding to an **explicit** handler (`name@addr = ps2_stubs::X` or
  `ps2_syscalls::X`) and warn when a name resolves in both lists. Today
  `resolveSyscallName` wins silently (`ps2_recompiler.cpp` ~1295, `game_overrides.cpp`):
  `sceSifSendCmd`, `sceSifLoadModule`, `sceSifLoadModuleBuffer`, `sceSifLoadElf`,
  `sceSifLoadElfPart` are in both `PS2_SYSCALL_LIST` and `PS2_STUB_LIST`, which is why fixing
  `Stubs/SIF.cpp` changed nothing for SRWZ.
- Flag a binding whose name was matched after dropping a leading underscore
  (`_sceSifSendCmd@0x11e3f8`, 7 args, landed on the 6-arg handler).
- Refuse `[patches]` that NOP a store (`sw/sh/sb/sd/sq` → `nop`) unless the patch names a reason;
  print every applied patch with its disassembly. The analyzer's patches NOP 15 ordinary stores
  in this game (CLAUDE.md); `make_config.py` drops them by hand today.
**Evidence.** Walls 7 and 8 (handlers reading args 5-7 from `sp+16..28`), the 7 confirmed bugs in
`docs/STUB-AUDIT.md`, the shadowed `sceSifSendCmd`, the 7-vs-6-arg `_sceSifSendCmd` binding, the
store-NOP patches. All of these are visible from the ELF plus a declared arity.
**Payoff.** Catches the whole class before a run instead of after a wall (each wall so far cost
hours). Declared arities: start from `tools/stubaudit.py`'s table (239 signatures), extend as
handlers are touched. Ghidra's own parameter counts are a cross-check, not a source of truth
(its decompiler hides the 9th argument in Wall 5).
**Other games:** no while it only reports; failing the build is opt-in per config.

### 4. Incremental regeneration and rebuild (M, low-medium risk)
**What.** Today any binding change is a full ~15 min rebuild, for three reasons:
(a) the workflow deletes `local/recomp/output` and rewrites all 59k files, so every mtime is new;
(b) the 5 MB `ps2_recompiled_functions.h` and `ps2_recompiled_stubs.h`, which change with the
function set, are in the 175 MB PCH, so the PCH and all 1,849 TUs rebuild;
(c) CMake unity batches are by position in the file list, and a binding removes the fragments of
the bound function, so every later batch changes membership.
Fixes, each useful alone:
- The recompiler writes a file only if its content changed and deletes outputs it no longer
  produces (it knows the whole set), so the folder never needs deleting and mtimes survive.
- Generated `.cpp` files declare only what they use (837 files need one or a few extern
  declarations; `register_functions.cpp` declares its own), and the generated headers leave the
  PCH. The PCH then holds only runtime headers and changes only with the runtime.
- The recompiler emits its own grouped TUs with stable membership, by guest address range and
  sized by content (e.g. ~300-500 KB of source each, a huge function alone), and the runner turns
  CMake unity off for them. A binding change then rebuilds the one or two groups that hold the
  changed code plus `register_functions`.
**Payoff.** Binding-change rebuild from ~15 min to an estimated 2-4 min (1-3 TUs + the table +
the link; the link of a 132 MB exe is the remaining fixed cost, not measured here). Size-aware
groups also flatten the compile tail (today the 315 `FUN_*` batches take 6 of the 10 compile
minutes, with up to 1.7 MB of complex code in one batch) and lower the per-process memory peak,
which is what stops a build running beside the game. A smaller PCH (no 5 MB of declarations) also
cuts the per-process memory every `cl.exe` maps.
**Other games:** generated layout only, no behaviour change; put the grouping behind a config
switch (`output_grouping`) so other projects' builds are unchanged. No go-ahead needed if
option-gated, but tell Wyn.

### 5. Resume into the owner function, stop emitting nested fragments (M-L, medium-high risk)
**What.** Register every resume address inside a real function to that function and give the
owner a `case` for it (816 of a 2,087 sample already have one), instead of a separate `entry_*`
fragment. Keep fragments only for addresses not inside any real function (5,388).
**Evidence.** The movie pacing loop ping-pong above (two scheduler round trips per iteration
after any checkpoint, 37% of iterations in the sampled window). 41,577 nested fragments = 132 MB
of the 305 MB of source, and the fragment batches are ~3.5 of the 10 compile minutes.
**Payoff.** Hot loops go back to native `goto` speed after a checkpoint (every loop in the game
that runs across a VBlank or timer interrupt is affected, not just the movie); about a third off
the generated-code compile and ~40% off the source size and file count (47k fewer files).
**Risk.** It changes generated code for every game. A `goto` into the middle of the owner must not
skip an initialised variable (the compiler would reject it, so failures are loud) and must
reproduce the fragment's state: the delay-slot resume checks (`if (ctx->pc == …)` after `jal`),
`in_delay_slot`, and fall-through. Needs the full regression (`tools/regress.ps1`) and
`ps2x_tests`. **Needs Wyn's go-ahead.**

### 6. Line up our execution with PCSX2 (S-M, no risk)
**What.** PCSX2 v2.8.2 has no scriptable debugger or function trace (`docs/PCSX2-TOOLBOX.md`):
only PINE memory reads, logging memory breakpoints and trace-log channels (`EE.bios` names the
syscalls). So compare state, not calls:
- `PS2X_WATCH=0x523930,0x58e978,0x59c210,…` in the runtime: at each VBlank, log
  `vsync, address, old → new` for every watched word that changed (and optionally a0-a3 at named
  functions via item 2).
- `tools/pine.py watch --words …` already logs the same words from PCSX2 with timestamps.
- A small aligner tool that matches the two change sequences by order (not by time, which drifts)
  and reports the first divergence: "state 0x2c → 0x51 at our vsync 1,210 / PCSX2 ~1,190; then
  PCSX2 goes 0x51 → 0x1d, we go 0x51 → 0x00".
- Second channel: the `EE.bios` syscall sequence from PCSX2's trace log against our syscall trace.
**Payoff.** Turns "the picture is wrong" into "the game took a different branch at state X",
which is where most walls start. The state words are already known (`docs/TITLE-PATH.md`).
**Other games:** no (env-gated; the aligner is a tool in `tools/`).

### 7. Cheap fixes on the call path (S, low risk) and batching of timer/IOP time (M, medium risk)
Low risk, behaviour-neutral:
- `dispatchGuestBranch`: one table lookup instead of `hasFunction` + `lookupFunction`; keep
  `pushDispatchPc`.
- `ps2xOverlayLookup`: compare the 16-byte name in place (`memcmp`/`strncmp` against each
  overlay's stored name, or a hash of the raw bytes), no `std::string`; cache the last
  (region, name bytes) → overlay. Today every call into stage/battle code allocates twice.
- `advanceEeTimers`: keep GIF_STAT in a member instead of the `unordered_map` lookup per charge;
  skip timers whose mode has no counting.
- Inline fast path for direct `jal` in generated code: `if (!eeCheckpointDue(8)) { fn =
  g_ps2RecompiledFunctionTable[slot]; if (fn) { fn(...); if (ctx->pc == fallthrough) goto
  resume; } }` with the out-of-line path for overlays and misses. Keeps the table indirection,
  so overrides and overlays still work. (This one changes generated code: tell Wyn.)
Timing-sensitive (**needs Wyn's go-ahead**):
- Accumulate EE cycles and advance timers and the IOP only at checkpoints or when a timer
  compare / IOP event is due, instead of a full IOP scheduler iteration for one IOP cycle on
  every guest call. Changes EE/IOP interleaving granularity for every game.
**Payoff.** Per call, from an estimated few hundred ns to a few tens of ns (the counts above:
93,581 call sites; the movie loop makes ~2 calls per iteration). Matters most in battle scenes
(HiG, many small calls) and overlay code.

### 8. Stub argument test with poisoned stack slots (M, low risk)
**What.** In `ps2x_tests`, call every runtime handler twice with the same a0-t3 and different
poison in the caller-stack area `sp+0x10..0x3f` (the O32 home slots, which hold the caller's
locals on the EE) and compare everything observable: v0/v1, guest memory, the runtime calls made.
Any difference means the handler reads the caller's locals: the Walls 7/8 class, found without
knowing the signature. Handlers with 9+ arguments get legitimate values at `sp+0`, `sp+8`, … from
a declared arity (item 3) and poison above.
**Payoff.** Mechanical coverage of all ~665 handlers, including the 139 bound ones with no
signature. Handlers that need IOP/files/GS state need a fixture; start with the pure ones (GS
packet builders, libvu0, MPEG setup, libc).
**Other games:** no (tests only).

### 9. Compile-time trims in the emitted code (S, low risk)
- `READ*` macros: replace the lambda (`[&]() -> uint32_t {…}()`) with a static inline function
  taking `(rdram, ctx, runtime, addr)`. There are 206,687 of them; each is a distinct closure type
  the front end must create and the optimiser must inline. Semantics identical.
- Drop `ps2TraceGuestWrite` from `WRITE*` (an inline no-op marked for deletion).
- Cap compile parallelism while a game runs (`/MP8` or `-p:CL_MPCount=8`), and make the batch
  size content-aware (part of item 4): today 32 files per batch whatever their size.
**Payoff.** Guess 10-25% off the generated-code compile and lower memory per TU; measure first on
one large batch with `/Bt+` (front-end vs back-end time) and `/d2cgsummary`.
**Other games:** generated code changes shape but not meaning; tell Wyn.

### 10. Leaner per-instruction code (M, medium risk)
- Store `ctx->pc` only where it can be observed: before memory ops that may reach a runtime
  handler or fault, syscalls, COP0, calls, branches out of the function, checkpoints. Today 1.45
  stores per instruction.
- Set `in_delay_slot`/`branch_pc` only around delay-slot instructions that can trap or call out
  (`handleSyscall` checks the flag); today every branch writes both flags.
- `__restrict` on `rdram` and `ctx` (guest stores through `uint8_t*` alias everything, so the
  compiler reloads `ctx->r[]` after each one).
**Payoff.** Estimate 10-30% on pure guest compute (from the counts: ~1.5 extra stores and a
reload chain per instruction against ~3-6 useful host operations), and smaller code for every
item above. **Risk:** precise-pc reporting in faults and MMIO logs, anything in the runtime that
reads `ctx->pc` mid-function. Every game. **Needs Wyn's go-ahead.**

### 11. A typed stub ABI layer (L, medium risk)
**What.** Handlers declare a signature, e.g. `PS2_STUB(sceSifSendCmd, u32 cid, gptr pkt, u32
psize, gptr src, gptr dest, u32 size)`, and a generated wrapper decodes args 1-8 from a0-a3/t0-t3,
9+ from `sp+0, 8, 16…`, floats from f12-f19, varargs through one EE-correct cursor (8-byte slots;
`Ps2VarArgCursor` pairs two 4-byte slots today, STUB-AUDIT #3). The signature then also feeds
items 3 and 8 and `stubaudit.py` (no more hand-typed table).
**Payoff.** Makes the Walls 7/8 class impossible for converted handlers. **Size:** 665 handlers;
convert by touch (new and fixed handlers first). **Risk:** each conversion changes that handler's
behaviour for every game where the old reads were wrong (that is the point, but it is a
behaviour change): **needs Wyn's go-ahead** for the layer and for bulk conversion.

### 12. `/FORCE:MULTIPLE` (S, no risk)
**Why it is there.** `ps2xRuntime/CMakeLists.txt` adds it on MSVC for "WinAPI vs raylib symbol
clash for CloseWindow"; the link log shows LNK4006 for `CloseWindow` and `ShowCursor` (raylib's
`rcore.obj` vs `user32.lib`) and LNK4088 "image may not run". **The problem:** it also turns any
real duplicate (two generated functions with the same name, an overlay helper outside its
namespace, a handler defined in two files) into a silent pick-one. **Fix:** keep the flag for now
but make `build-game.bat` fail when the link output has an LNK4006 for anything other than those
two names; later, isolate raylib's Win32 use so the flag can go.
**Other games:** no.

### 13. Release flags that never apply (S, low payoff)
`EnableFastReleaseMode` checks `CMAKE_BUILD_TYPE`, empty under the VS generator, so none of its
flags are used. Do **not** "fix" it by enabling `/GL` + `/LTCG` for the runner: whole-program code
generation over 300 MB of generated code would move all of the 10 minutes into a mostly serial,
memory-hungry link. `/arch:AVX2` and `/GS-` on `ps2_runtime` alone are harmless and small. If a
PDB is wanted for crash dumps, `/Z7` + `/DEBUG:FASTLINK` on the runtime only; the generated code
is already named by the `.map`.

### 14. VU0 macro (COP2) code (S, low risk, low payoff)
45 files use COP2 at all, 19 with arithmetic; the emission is tight SSE (`PS2_VMUL`,
`_mm_blendv_ps` per dest mask). Not a hot spot on any path we know (no VU work on the title path,
`docs/TITLE-PATH.md`). One correctness note: `vdiv` returns `0.0f` on a zero divisor; hardware
gives ±max float with the divide flag. Fix when battles show odd geometry.

### 15. Skipping unreachable functions (not recommended)
Only 412 of 11,870 real functions (2.7% of their bytes) have no direct reference and no 32-bit
word equal to their address in the ELF or the overlays. Metrowerks C++ calls through vtables and
pointers computed at run time, so pruning risks a missing-function stop for 3% of the build.

### 16. Sprite and texture replacement (HD packs) (L, later: after "the game plays")
**What.** In our GS rasteriser: hash every texture upload (pixels plus palette, so a palette swap
of the same pixels hashes separately); a dump switch that writes each unique texture once as PNG
named by its hash; a replacement folder looked up by hash at draw time (same size or an integer
multiple, sampled with scaled coordinates); a batch upscale tool over the dump folder; a higher
internal render resolution in the rasteriser; a GPU backend as the fallback if software rendering
cannot hold 60 fps at that size.
**Pitfalls.** Partial uploads into larger sheets (hash the region the draw samples, or the whole
sheet once complete, not each upload); render-to-texture effects (frame-buffer feedback must not
be replaced or hashed as a texture); animated textures updated per frame.
**When.** After the game plays through. The hash and dump hooks are cheap to add while the
rasteriser is fresh, and the dump is useful for debugging anyway, so add those two early, behind
a switch; replacement and higher resolution later.

---

## Do first

1. **Item 1, the log tracker off.** One configure flag (or a default-preserving option), no
   behaviour change, 2.4 GB less disk per run, an estimated 15-40% of the EE thread back, and a
   smaller compile. No go-ahead needed.
2. **Item 2, names + filtered call trace + crash handler**, with item 6 (PCSX2 state diff)
   right after it. Env-gated and additive, so no go-ahead needed; it replaces the function trace
   item 1 removes, and it shortens every future wall.

Item 3 (binding verifier, report-only) and item 4 (incremental build, option-gated) are the next
two; neither changes behaviour for other games.

**Need Wyn's go-ahead** (they change what PS2Recomp does for every game): item 5 (resume into
the owner), the cycle batching in item 7 (and tell him about its generated-code fast path),
item 10 (leaner per-instruction code), item 11 (typed ABI conversions). Items 4 and 9 change the
shape of the generated output only; keep them behind config switches and tell him.
