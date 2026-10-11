# Runtime handler ABI audit (2026-10-06)

The EE passes integer arguments 1-8 in a0-a3, t0-t3 (regs 4-11) and arguments 9 and up on the
stack in 8-byte slots from sp+0. Walls 7 and 8 were handlers reading arguments 5-7 from
sp+16..28 (the O32 home slots), which hold the caller's locals. This is the sweep for the
remaining cases, made with `python tools/stubaudit.py --bound local/recomp/srwz.toml`, then
checked by hand against the game's own copies (`tools\decomp.ps1 -Name audit ...`, output in
`local/runs/audit*.txt`). No PS2Recomp file was changed. The fixes come later, from this list.

Paths are relative to `third_party/PS2Recomp/ps2xRuntime/src/lib/Kernel/`.

## How a binding picks its handler (it matters for the SIF fixes)

`ps2_recompiler.cpp` (around line 1295) and `game_overrides.cpp` (`resolveHandlerByName`)
resolve a bound name to **`ps2_syscalls::X` first** (when X is in `PS2_SYSCALL_LIST`) and only
then to `ps2_stubs::X`. A leading underscore is dropped when looking the name up.
`sceSifSendCmd` and `sceSifCallRpc` are in both lists, so SRWZ's bindings run
`Syscalls/RPC.cpp`, **not** `Stubs/SIF.cpp`. Fixing only `Stubs/SIF.cpp:27` (`sceSifSendCmd`)
changes nothing for SRWZ. The live copy is `Syscalls/RPC.cpp:971`, which has the same bug.

## Numbers

- Handlers scanned: 665 (551 stubs in `Stubs/*.cpp`, 114 syscall handlers named in
  `PS2_SYSCALL_LIST`), with helper calls and `ps2_syscalls::` delegation followed.
- Stack-read helpers found: `readStackU32` (`Stubs/Helpers/Support.h:1683`, and a separate one
  in `Syscalls/Helpers/Runtime.h:77`), `readStackArg` (`Stubs/MPEG.cpp:780`), direct
  `FAST_READ32(sp + n)` reads, and `Ps2VarArgCursor` (`Stubs/Helpers/Support.h:777`).
- With a signature: 239. Sources: `builtin` (the tool's table, typed in from the SDK
  reference) for almost all of them, `comment` for 2. No SDK headers are vendored. The
  analyzer's symbol database (`sce_symbol_database_data.h`) has no argument counts: it only
  names the library.
- Flagged: 18. Bound by SRWZ: 9 (plus `sceSifSendCmd` in `Stubs/SIF.cpp`, which is bound but
  shadowed by the syscall handler).
- Checked against the game's copy and confirmed as ABI mismatches: 9 handlers (below). Five
  of them are actually reached by SRWZ. One of those five, `sceMpegAddStrCallback`, does no
  harm today.
- Signatures not checked: 139 bound handlers have no signature in the table. The tool lists
  the ones that read a3 but no t-register, to check by hand.

## Status (2026-10-07)

- The stack reads below were fixed in PS2Recomp e28ea1f; the poison sweep (srwz be11d1a,
  `ps2xTest/src/ps2_stub_poison_tests.cpp`: all 666 handlers called with poisoned caller stack
  slots and a page trap) confirms no handler reads the caller's locals any more (`flagged 0`).
- The va_list cursor (`Ps2VaListCursor`, `Stubs/Helpers/Support.h`) now walks one 8-byte slot
  per argument, as the game's own newlib `vfprintf` `0x1a19b0` and SCE formatter `0x11ce48` do
  (`local/runs/vaprobe*.txt`); before, `vfprintf`/`vsprintf` (`vfprintf` is bound) read the second
  and later arguments wrongly. `%l`/`%q` read 64 bits, `%h`/`%hh` narrow.
- `sceeFontPrintfAt` no longer copies the caller's O32 home slots or writes the caller's frame
  (it read sp+0x10.. as varargs and overwrote sp+0..0x37); fixed in the same commit (269570e).
- Known risks of the cursor change: `%ld` of a 32-bit value stored zero-extended (e.g. a stub
  return set with `SET_GPR_U32`) with bit 31 set now prints as a large unsigned 64-bit number,
  as `%ld` of a 64-bit register would; `%hh` uses C char semantics, where the game's older newlib
  treats `hh` like `h` (differs only for values outside the char range).

## Confirmed

Ranked by how likely each one is to affect SRWZ on the way to the title screen and the first
menu.

| # | Handler (file:line) | What it reads | What the ABI says (game's copy) | Bound | Reached by SRWZ? / evidence |
|---|---|---|---|---|---|
| 1 | `sceSdRemote` (`Stubs/Audio.cpp:77`, reads at 84-93) | Args 5-7 from t0-t2, **but when a register is 0 it falls back to `FAST_READ32(sp+0x10/0x14/0x18)`** (4-byte O32 slots) | Variadic. The game's `0x127688` takes a0-t3 (8 params) and copies a2..t3 as 64-bit slots into the RPC buffer at `0x455b04`. Nothing is read from the caller's stack (`audit.txt`). | yes `0x127688` | **Yes, on every sound path.** Sound driver `0x189050`, `0x18c270`, `0x18cad0`, `0x18ca50` (`audit3/4.txt`). `0x18ca50` calls `sceSdRemote(1, 0x80e0 BlockTrans, 0, 2 /*stop*/, 0, 0)`: t0=t1=0, so the stub reads the caller's locals. Today the stop branch ignores them. Any BlockTrans/VoiceTrans with a legitimate 0 in t0-t2 (start, size, SPU address) gets garbage. Drop the fallback and read t0-t2 only. |
| 2 | `sceSifCallRpc` → `SifCallRpc` (`Syscalls/RPC.cpp:966` → `342`, reads at 352-368) | t0-t3 and sp+0 (correct), **plus** sp+0x10/0x14/0x18/0x1c/0x20 as a second "stack pack", picked when the register pack looks implausible (`plausiblePack`, `selectRpcAbi`) | 9 args: the game's `0x11f130` takes `param_9` (end_para) from sp+0 and nothing from sp+0x10.. (`audit.txt`) | yes `0x11f130` | **Yes, every IOP RPC (CD, pad, mc, sound).** The register path is right, and the default picks it. The stack path can only ever produce caller locals, so it should go. The risk is a register pack that fails `plausiblePack` (send size > 32 MB, or a receive buffer outside RAM, e.g. an uncached or scratchpad address): then garbage replaces correct values. |
| 3 | `printf`, `sprintf`, `printf_r`, (`snprintf`, `fprintf`, `scePrintf`, `vfprintf` callers) (`Stubs/LibC.cpp:524`, `565`; `Stubs/Compatibility.cpp:120`; cursor in `Stubs/Helpers/Support.h:777-830`) | Vararg slots 0-7 from a0-t3 (right). **Slots 8+ at sp+0x10 in 4-byte steps.** `nextU64` pairs two 32-bit slots (O32), so a 64-bit vararg takes two registers. | The game's `printf` `0x19f0c8` and `sprintf` `0x1a07a8` spill a1..t3 as 8-byte slots right below the incoming stack args, so va_arg walks 8-byte slots continuing at the caller's sp+0. A 64-bit value is one slot (`audit.txt`). | yes `0x19f0c8`, `0x1a07a8`, `0x19f088` | **Not today.** A scan of the generated code (432 call sites in the main program, 19 in the overlays) found no call passing a vararg on the stack. The only stores to 0..0x18($sp) before a call are the caller's own prologue saves. So no SRWZ call has more than 8 arguments. It breaks a format with 9+ arguments, or any `%ll`. |
| 4 | `sceMpegAddStrCallback` (`Stubs/MPEG.cpp:1932`, via `readAbiArg4` at `789`/`1939`) | Arg 5 (`data`) from t0, **or sp+0x10 when t0 is 0** | 5 args: the game's `0x11a1f0` stores `param_5` (t0) beside the callback (`audit.txt`) | yes `0x11a1f0` | **Yes (movie player), harmless today.** `0x18b260` registers both stream callbacks with `data = 0` (`decomp_w9a.txt` lines 28-29), so the stub stores the caller's local at sp+0x10 as `data`. The callbacks `0x18b910` and `0x18ba60` take only (mp, cbdata) and never read `data` (`audit3.txt`). Fix it with the rest. |
| 5 | `sceMpegDemuxPssRing` (`Stubs/MPEG.cpp:2148`, `readAbiArg4` at `2166`) | Arg 5 (ring size) from t0, or sp+0x10 when t0 is 0 | 5 args: the game's `0x119df0` uses `param_5` (t0) as the ring size (`audit.txt`) | yes `0x119df0` | Yes (movie demux), but only wrong when the ring size is 0, which no real call does. |
| 6 | `sceSifSendCmd` (syscall, `Syscalls/RPC.cpp:971`, reads at 977-981) and its shadowed twin (`Stubs/SIF.cpp:27`, reads at 30-31) | dest and size from **sp+0x10 / sp+0x14**; t0/t1 never read | 6 args: the game's `0x11e530` passes a0..t1 to `_sceSifSendCmd` `0x11e3f8` (7 params: cid, mode, pkt, size, src, **dest = t1, size = t2** of the inner call, i.e. t0/t1 of the outer one) (`audit.txt`) | yes `0x11e530`, and `_sceSifSendCmd@0x11e3f8` resolves to the same handler | **No.** Every caller of `0x11e530` in the main program (`sceSifInitRpc`, `sceSifBindRpc`, `sceSifCallRpc`, `sceSifInitCmd`, `sceSifExecRequest`, `sceSifGetOtherData`) is itself bound. **Also wrong:** `_sceSifSendCmd@0x11e3f8` takes 7 args (extra `mode` in a1), so binding it to the 6-arg handler shifts every argument. It is reached only from `0x11e530`/`0x11e570`, both bound, so this is dead today. Remove that binding when fixing. |
| 7 | `sceSifRegisterRpc` → `SifRegisterRpc` (`Stubs/SIF.cpp:568` → `Syscalls/RPC.cpp:705`, reads at 715-717) | cfunc, cbuff, qd from **sp+0x10/0x14/0x18**; t0-t2 never read | 7 args: the game's `0x11f408` stores `param_5`/`param_6` (t0/t1) and dereferences `param_7` = qd (t2) (`audit.txt`) | yes `0x11f408` | **No.** The only caller is `regMain` `0x133138` (`audit2.txt`: `sceSifRegisterRpc(sd, 0x80000400, func, buf, 0, 0, qd=0x457740)`), a thread started by `mceRegistSifFunc` `0x1331a0`, which is reached only from inside `sceMcInit` (bound). It goes wrong as soon as anything registers an RPC server from guest code. |

## Unconfirmed (flagged, no SDK signature, or not bound)

| Handler (file:line) | What it reads | Why unconfirmed |
|---|---|---|
| `sceeFontGenerateString` (`Stubs/Font.cpp:228`, reads at 241-242) | a0-t3, sp+0x0 (`strAddr`), sp+0x18 (`param14`) | Game-specific (not an SDK function), not bound by SRWZ. With 8 integer register args, sp+0 and sp+0x18 are arguments 9 and 12 in 8-byte slots, which is consistent. Needs the original game's prototype. |
| `sceeFontPrintfAt`, `sceeFontPrintfAt2` (`Stubs/Font.cpp:504`, `556`) | Through `sceeFontGenerateString` after moving sp (`SET_GPR_U32(ctx, 29, frame)`), plus `oldSp + 0x10` at 513 | Not bound. **`sceeFontPrintfAt` was wrong** (found by the poison sweep): it copied 64 bytes from the caller's sp+0x10 as varargs and over the caller's frame; fixed in 269570e (t0-t3 into its own frame, then the caller's sp+0 slots). `sceeFontPrintfAt2` was fine. |
| `snprintf`, `fprintf`, `scePrintf` (`Stubs/LibC.cpp:625`, `811`; `Stubs/TTY.cpp:7`) | Same `Ps2VarArgCursor` as #3 | Same bug as #3, not bound by SRWZ. |
| `SifCallRpc`, `SifRegisterRpc` (`Syscalls/RPC.cpp:342`, `705`) as bare syscall names | As #2 and #7 | Same code as #2 and #7. SRWZ reaches them only through the `sce*` names. |

## Arguments ignored, not misread (info)

These handlers have 5+ arguments, never read t0-t3, and read nothing from the stack. That
leaves an argument unused, but nothing is misread:

- `sceMcSetFileInfo` (`Stubs/MemoryCard.cpp:1160`, bound `0x132c30`): ignores `info` (a3) and
  `valid` (t0). The game's copy copies the 64-byte `sceMcTblGetDir` from a3 and masks t0 with 7.
  Only matters when saves set file attributes or dates.
- `sceSifGetOtherData` (`Stubs/SIF.cpp:385`, bound `0x11ec88`): ignores `mode` (t0, the
  no-wait bit in the game's copy). The stub is always synchronous.
- `sceCdStream` (`Stubs/CD.cpp:908`, bound `0x12ffa0`): a no-op returning 1. Its game callers
  (`sceCdStInit` etc.) are bound themselves.

Also checked and found correct: `sceGsSetDefClear` (11 args: b, a, z at sp+0/8/0x10),
`sceGifPkRefLoadImage` (10 args: w, h at sp+0/8), `sceVu0ViewScreenMatrix` (the ninth float at
sp+0, confirmed by the game's `0x10f900` reading `in_stack_00000000`), `sceGsSetDefDBuff`,
`sceGsSetDefDispEnv`, `sceGsSetDefDrawEnv`, `sceGsSetDefLoadImage`/`StoreImage`, `sceMcGetInfo`,
`sceMcGetDir`, `sceVu0RotTransPersN`, `sceVu0LightColorMatrix` (all read t0-t3).

## Left to check

- 139 bound handlers have no signature in the tool's table. The tool lists the ones that read
  a3 but no t-register, whose argument count should be checked by hand: AddDmacHandler,
  AddIntcHandler, mbtowc_r (newlib `_mbtowc_r` has 5 args, the 5th the mbstate),
  sceFsSemInit, sceGszbufaddr, sceMpegGetPictureRAW8xy (a TODO stub), sceRpcGetFPacket(2),
  sceSifCmdIntrHdlr, sceTty*, sprintf.
- The table in `tools/stubaudit.py` is typed in from the SDK reference, not read from headers:
  check a signature against the game's copy before relying on it.
