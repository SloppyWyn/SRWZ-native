#!/usr/bin/env python3
"""Checks real stub-call arguments from a game run against the typed handlers' declared prototypes.

    python tools/stubtrace_check.py --signatures <ps2x_stubsigs.json> --trace <run.err.txt> [...]
                                    [--json out.json] [--all] [--strict]
    python tools/stubtrace_check.py --selftest

Input: a runner's stderr with PS2X_TRACE_STUBS=<filter or 1> and PS2X_TRACE_STUBS_FULL=1 (every
argument location: a0-a3, t0-t3, f12-f19 and the first four 8-byte stack slots; without FULL only
a0-a3 are checked), and the ps2x_stubsigs JSON of the same runtime (the typed stub ABI layer's
declared signatures, ps2_stub_abi.h). Game-agnostic: it knows only the EE ABI and the declarations,
so it runs unchanged on another title's trace.

For each traced call of a typed handler, every declared argument is decoded the way the typed
wrapper decodes it (integer-class args 1-8 in a0-t3, floats in f12-f19, overflow of either class
into 8-byte stack slots in argument order, ps2_abi::argLayout) and checked:
  BADPTR     a `gptr` argument that is not 0 and not a guest address (RAM 0-32 MB, its KSEG0/1 and
             uncached mirrors, the scratchpad 0x70000000-0x70003fff, the EE I/O registers, VU memory,
             the GS privileged registers)
  RANGE      an s8/u8/s16/u16 argument whose register value is not that type sign/zero-extended
  FLOAT_NAN  a float argument that is NaN or infinite
  FLOAT_ODD  a float argument with a huge (>1e30) or tiny non-zero (<1e-30) magnitude (info)
  LOOKS_PTR  an integer argument that always holds a pointer-like value (>= 0x100000 and valid) (info)
A finding on every call of a handler is a MISMATCH (the declaration is likely wrong); on some calls
a WARN (look at the values). Handlers named in the trace that are not typed are listed as
untyped (coverage). A binding name resolves like the runtime's: syscall table first, then stubs,
leading underscores ignored. Exit code 1 with --strict when there is a MISMATCH.
"""
import argparse
import json
import math
import re
import struct
import sys

ENTRY_RE = re.compile(r"^\[stub\] (\S+?)@0x([0-9a-f]+)\(a0=0x([0-9a-f]+)(?: \"[^\"]*\")? a1=0x([0-9a-f]+) "
                      r"a2=0x([0-9a-f]+) a3=0x([0-9a-f]+)\) ra=0x([0-9a-f]+)(.*)$")
FULL_RE = re.compile(r"\| t0=0x([0-9a-f]+) t1=0x([0-9a-f]+) t2=0x([0-9a-f]+) t3=0x([0-9a-f]+) "
                     r"((?:f\d+=0x[0-9a-f]+ ?){8})sp=0x([0-9a-f]+) stk=((?:0x[0-9a-f]+,?){8})")

INT_TYPES = {"s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "gptr", "u128"}


def layout(params):
    """[(param index, type, kind, index)] with kind 'int' (gpr 4+index), 'float' (f12+index), 'stack'."""
    out, ints, floats, stack = [], 0, 0, 0
    for i, t in enumerate(params):
        if t == "varargs":
            break
        if t == "f32":
            if floats < 8:
                out.append((i, t, "float", floats))
            else:
                out.append((i, t, "stack", stack))
                stack += 1
            floats += 1
        else:
            if ints < 8:
                out.append((i, t, "int", ints))
            elif t == "u128":
                stack = (stack + 1) & ~1  # a 16-byte aligned slot pair (ps2_abi::argLayout)
                out.append((i, t, "stack", stack))
                stack += 2
            else:
                out.append((i, t, "stack", stack))
                stack += 1
            ints += 1
    return out


# Physical ranges a guest pointer may name besides RAM: the EE I/O registers (a sceDmaChan * is the
# channel's register block, e.g. 0x1000a000), the VU memories and the GS privileged registers.
HW_RANGES = ((0x10000000, 0x10010000), (0x11000000, 0x11010000), (0x12000000, 0x12002000))


def valid_ptr(v):
    if 0x70000000 <= v < 0x70004000:
        return True
    phys = v & 0x1FFFFFFF if (0x20000000 <= v < 0x40000000 or 0x80000000 <= v < 0xC0000000) else v
    if phys < 0x02000000:
        return True
    return any(lo <= phys < hi for lo, hi in HW_RANGES)


def parse_call(line):
    m = ENTRY_RE.match(line.rstrip("\r\n"))
    if not m:
        return None
    name = m.group(1)
    regs = [int(m.group(k), 16) for k in range(3, 7)]
    call = {"name": name, "addr": int(m.group(2), 16), "ra": int(m.group(7), 16), "gpr": regs + [None] * 4,
            "f": [None] * 8, "stk": None}
    fm = FULL_RE.search(m.group(8))
    if fm:
        call["gpr"] = regs + [int(fm.group(k), 16) for k in range(1, 5)]
        call["f"] = [int(x.split("=")[1], 16) for x in fm.group(5).split()]
        call["stk"] = [int(x, 16) for x in fm.group(7).split(",")]
    return call


def as_float(bits):
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def value_of(call, kind, idx):
    """(value, available)"""
    if kind == "int":
        v = call["gpr"][idx]
        return v, v is not None
    if kind == "float":
        v = call["f"][idx]
        return v, v is not None
    stk = call["stk"]
    if stk is None or 2 * idx >= len(stk):
        return None, False
    return stk[2 * idx], True


def check_value(t, v):
    """Finding codes for one argument value (low 32 bits of its location)."""
    out = []
    if t == "gptr":
        if v != 0 and not valid_ptr(v):
            out.append("BADPTR")
    elif t == "s8":
        if not (v <= 0x7F or v >= 0xFFFFFF80):
            out.append("RANGE")
    elif t == "u8":
        if v > 0xFF:
            out.append("RANGE")
    elif t == "s16":
        if not (v <= 0x7FFF or v >= 0xFFFF8000):
            out.append("RANGE")
    elif t == "u16":
        if v > 0xFFFF:
            out.append("RANGE")
    elif t == "f32":
        f = as_float(v)
        if math.isnan(f) or math.isinf(f):
            out.append("FLOAT_NAN")
        elif f != 0.0 and (abs(f) > 1e30 or abs(f) < 1e-30):
            out.append("FLOAT_ODD")
    return out


def resolve(name, table):
    """The registry entry a binding name runs: syscall first, then stub; leading underscores ignored."""
    bare = name.lstrip("_")
    for kind in ("syscall", "stub"):
        for cand in (name, bare, "_" + bare):
            h = table.get((kind, cand))
            if h is not None:
                return h
    return None


def check(signatures, lines):
    table = {(h["kind"], h["name"]): h for h in signatures["handlers"]}
    per, untyped, nfull, ncalls = {}, {}, 0, 0
    for line in lines:
        call = parse_call(line)
        if not call:
            continue
        ncalls += 1
        nfull += call["stk"] is not None
        h = resolve(call["name"], table)
        if h is None:
            untyped[call["name"]] = untyped.get(call["name"], 0) + 1
            continue
        key = "%s %s" % (h["kind"], h["name"])
        r = per.setdefault(key, {"handler": h["name"], "kind": h["kind"], "bound_as": set(), "calls": 0,
                                 "params": h.get("params", []), "deviation": h.get("deviation"),
                                 "checked": {}, "hits": {}, "examples": {}, "ptrlike": {}, "unavailable": 0})
        r["calls"] += 1
        r["bound_as"].add(call["name"])
        for i, t, kind, idx in layout(h.get("params", [])):
            v, ok = value_of(call, kind, idx)
            if not ok:
                r["unavailable"] += 1
                continue
            r["checked"][i] = r["checked"].get(i, 0) + 1
            for code in check_value(t, v):
                r["hits"].setdefault((i, code), 0)
                r["hits"][(i, code)] += 1
                r["examples"].setdefault((i, code), "0x%x (ra=0x%x)" % (v, call["ra"]))
            if t in INT_TYPES - {"gptr"} and v >= 0x100000 and valid_ptr(v):
                r["ptrlike"][i] = r["ptrlike"].get(i, 0) + 1
    rows = []
    for key in sorted(per):
        r = per[key]
        findings = []
        for (i, code), n in sorted(r["hits"].items()):
            total = r["checked"].get(i, 0)
            sev = "INFO" if code == "FLOAT_ODD" else ("MISMATCH" if n == total else "WARN")
            findings.append({"severity": sev, "code": code, "arg": i + 1, "type": r["params"][i], "count": n,
                             "of": total, "example": r["examples"][(i, code)]})
        for i, n in sorted(r["ptrlike"].items()):
            if n == r["checked"].get(i, 0) and n > 0:
                findings.append({"severity": "INFO", "code": "LOOKS_PTR", "arg": i + 1, "type": r["params"][i],
                                 "count": n, "of": n, "example": ""})
        rows.append({"handler": r["handler"], "kind": r["kind"], "bound_as": sorted(r["bound_as"]),
                     "calls": r["calls"], "params": r["params"], "deviation": r["deviation"],
                     "args_checked": sum(r["checked"].values()), "args_unavailable": r["unavailable"],
                     "findings": findings})
    return {"calls": ncalls, "full_calls": nfull, "typed": rows,
            "untyped": [{"name": n, "calls": c} for n, c in sorted(untyped.items())]}


def report(res, show_all=False):
    out = []
    typed = res["typed"]
    sev = {"MISMATCH": 0, "WARN": 0, "INFO": 0}
    for r in typed:
        for f in r["findings"]:
            sev[f["severity"]] += 1
    out.append("stub calls traced: %d (%d with every argument location, PS2X_TRACE_STUBS_FULL)" %
               (res["calls"], res["full_calls"]))
    out.append("typed handlers seen: %d (%d calls, %d argument values checked); untyped bindings seen: %d"
               % (len(typed), sum(r["calls"] for r in typed), sum(r["args_checked"] for r in typed),
                  len(res["untyped"])))
    out.append("findings: MISMATCH %d, WARN %d, INFO %d" % (sev["MISMATCH"], sev["WARN"], sev["INFO"]))
    for r in typed:
        if not r["findings"] and not show_all:
            continue
        out.append("%-8s %-28s calls %-6d (%s)%s" % (r["kind"], r["handler"], r["calls"], ", ".join(r["params"]),
                                                     "  [deviation]" if r["deviation"] else ""))
        for f in r["findings"]:
            out.append("    %-8s %-9s arg %d (%s): %d of %d calls%s" % (
                f["severity"], f["code"], f["arg"], f["type"], f["count"], f["of"],
                ("; e.g. " + f["example"]) if f["example"] else ""))
    if show_all and res["untyped"]:
        out.append("untyped: " + ", ".join("%s (%d)" % (u["name"], u["calls"]) for u in res["untyped"]))
    return "\n".join(out)


SELFTEST_SIGS = {"handlers": [
    {"name": "sceGood", "kind": "stub", "params": ["gptr", "s16", "f32"]},
    {"name": "sceBadPtr", "kind": "stub", "params": ["u32", "gptr"]},
    {"name": "SysNine", "kind": "syscall", "params": ["gptr"] * 8 + ["u32"]},
    {"name": "sceFloats", "kind": "stub", "params": ["gptr"] + ["f32"] * 9},
    {"name": "sceSome", "kind": "stub", "params": ["gptr"]},
]}


def _line(name, regs, t=(0, 0, 0, 0), f=(0,) * 8, stk=(0,) * 8, full=True):
    s = "[stub] %s@0x100000(a0=0x%x a1=0x%x a2=0x%x a3=0x%x) ra=0x123456 (caller+0x10)" % ((name,) + tuple(regs))
    if full:
        s += " | t0=0x%x t1=0x%x t2=0x%x t3=0x%x " % tuple(t)
        s += " ".join("f%d=0x%x" % (12 + i, f[i]) for i in range(8))
        s += " sp=0x1f0000 stk=" + ",".join("0x%x" % w for w in stk)
    return s


def selftest():
    one = struct.unpack("<I", struct.pack("<f", 1.0))[0]
    nan = 0x7FC00000
    lines = [
        _line("sceGood", (0x200000, 0xFFFF8000, 0, 0), f=(one,) + (0,) * 7),
        _line("_sceGood", (0, 0x7FFF, 0, 0), f=(one,) + (0,) * 7),
        _line("sceBadPtr", (5, 0xDEAD0000, 0, 0)),
        _line("sceBadPtr", (5, 0x05000000, 0, 0)),
        _line("SysNine", (0x100000,) * 4, t=(0x70000010, 0x20100000, 0x80100000, 0), stk=(7, 0xFFFFFFFF) + (0,) * 6),
        _line("sceFloats", (0x200000, 0, 0, 0), f=(one,) * 7 + (nan,), stk=(one, 0) + (0,) * 6),
        _line("sceSome", (0x12345678, 0, 0, 0)),
        _line("sceSome", (0x200000, 0, 0, 0)),
        _line("sceSome", (0x200000, 0, 0, 0), full=False),
        _line("sceUntyped", (0, 0, 0, 0)),
        "[stub] sceGood@0x100000 -> v0=0x0",
        "unrelated line",
    ]
    res = check(SELFTEST_SIGS, lines)
    by = {r["handler"]: r for r in res["typed"]}
    codes = {n: [(f["severity"], f["code"], f["arg"]) for f in r["findings"]] for n, r in by.items()}
    checks = [
        ("parses 10 calls, 9 of them full", res["calls"] == 10 and res["full_calls"] == 9),
        ("sceGood (also bound as _sceGood): clean", codes.get("sceGood") == [] and by["sceGood"]["calls"] == 2),
        ("sceBadPtr: BADPTR on every call = MISMATCH", ("MISMATCH", "BADPTR", 2) in codes.get("sceBadPtr", [])),
        ("SysNine: 9th arg from stk slot 0, mirrors/scratchpad valid", codes.get("SysNine") == [] and
         by["SysNine"]["args_checked"] == 9),
        ("sceFloats: NaN in f19 = MISMATCH on arg 9, ninth float from the stack", ("MISMATCH", "FLOAT_NAN", 9) in
         codes.get("sceFloats", []) and by["sceFloats"]["args_checked"] == 10),
        ("sceSome: BADPTR on 1 of 3 = WARN", ("WARN", "BADPTR", 1) in codes.get("sceSome", [])),
        ("untyped listed", res["untyped"] == [{"name": "sceUntyped", "calls": 1}]),
        ("hardware addresses are valid pointers", valid_ptr(0x1000A000) and valid_ptr(0xB000A000) and
         valid_ptr(0x12001000) and not valid_ptr(0x05000000) and not valid_ptr(0x13000000)),
        ("range check s16", check_value("s16", 0x8000) == ["RANGE"] and check_value("s16", 0xFFFF8000) == []),
        ("layout: mixed overflow in argument order", [x[2:] for x in layout(["f32"] * 9 + ["u32"] * 9)][8:10] ==
         [("stack", 0), ("int", 0)] and layout(["f32"] * 9 + ["u32"] * 9)[17][2:] == ("stack", 1)),
    ]
    ok = True
    for label, cond in checks:
        ok &= bool(cond)
        print("%-62s %s" % (label, "ok" if cond else "FAIL"))
    if not ok:
        print(json.dumps(codes, indent=1))
    print("selftest: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--signatures", help="ps2x_stubsigs JSON of the runtime that made the trace")
    ap.add_argument("--trace", nargs="+", help="runner stderr file(s) with [stub] lines")
    ap.add_argument("--json", help="write the result as JSON here")
    ap.add_argument("--all", action="store_true", help="list every typed handler seen and the untyped ones")
    ap.add_argument("--strict", action="store_true", help="exit 1 on a MISMATCH")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.signatures or not a.trace:
        ap.error("--signatures and --trace are required")
    sigs = json.load(open(a.signatures, encoding="utf-8"))
    lines = []
    for path in a.trace:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines.extend(fh)
    res = check(sigs, lines)
    print(report(res, a.all))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)
    mism = sum(1 for r in res["typed"] for f in r["findings"] if f["severity"] == "MISMATCH")
    return 1 if (a.strict and mism) else 0


if __name__ == "__main__":
    sys.exit(main())
