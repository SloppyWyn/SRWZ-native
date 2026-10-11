#!/usr/bin/env python3
"""Binding verifier for PS2Recomp configs (report only; changes nothing).

PS2Recomp replaces a guest function with a runtime handler when the config binds it
(`stubs = ["name@0xADDR", ...]`), or when the function map gives a function a name the
runtime implements. A wrong binding fails silently at run time (Walls 7 and 8: handlers
reading arguments 5-7 from the stack where the EE passes them in t0-t2). This step looks at
every binding before a build and reports, per binding:

  - the guest copy: size, the argument registers it reads before writing them (a0-a3,
    t0-t3, f12-f19; calls and tail jumps are followed, so pass-through counts), the caller
    stack slots it reads (EE ABI: arguments 9+ at sp+0, sp+8, ...), varargs spills, the
    calls and syscalls it makes;
  - the runtime handler the name resolves to (syscall list first, then stubs, a leading
    underscore dropped or added, like ps2_runtime_calls.h) and what it reads, from
    tools/stubaudit.py's scan of the runtime source; a mismatch is the Walls 7/8 class;
  - the SCE symbol database (the analyzer's embedded one): whether the bytes at the address
    are that SDK function (hash with relocations masked, like the analyzer), another one,
    or nothing known; how many fixed bits the match rests on (low confidence below 512);
    whether the calls the database records for it land on functions bound to those names;
  - binding-set problems: a binding at an address where the function map has no function
    (the recompiler drops it silently), two names on one address, one name on two
    addresses, names the runtime does not implement or implements as a TODO that throws,
    names shadowed by a syscall of the same name, implicit bindings by function-map name;
  - unbound guest functions that only forward to a bound one (wrappers the analyzer
    missed), and `[patches]` that turn a store into a NOP.

Severity: ERROR (likely wrong at run time), WARN (check by hand), INFO (context).

Usage:
    python tools/bindcheck.py CONFIG.toml [--ps2recomp DIR] [--analyzer analyzer.toml]
        [--ghidra ghidra.toml] [--manual config/bindings.txt] [--output GENERATED_DIR]
        [--report FILE] [--json FILE] [--log FILE] [--strict]
    python tools/bindcheck.py --selftest

  --output   the recompiler's output folder: confirms which bindings it actually emitted
  --log      append the one-line summary to this file (e.g. the recompiler's log)
  --strict   exit 1 when there is an ERROR (default: always 0, report only)
"""
import argparse
import bisect
import csv
import hashlib
import json
import os
import re
import struct
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stubaudit  # noqa: E402  (handler scan of the runtime source)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REG = ["zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
       "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"]
ARG_GPRS = list(range(4, 12))          # a0-a3, t0-t3
ARG_FPRS = list(range(12, 20))         # f12-f19
CALLER_SAVED = [1, 2, 3] + list(range(4, 16)) + [24, 25, 31]
SEV_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}
LOW_CONFIDENCE_BITS = 512


def argname(r):
    return REG[r]


# --------------------------------------------------------------------------------------------
# R5900 decoding: register uses/defs, branches, memory accesses
# --------------------------------------------------------------------------------------------

class Ins:
    __slots__ = ("pc", "word", "ug", "dg", "uf", "df", "kind", "target", "likely", "mem")

    def __init__(self, pc, word):
        self.pc, self.word = pc, word
        self.ug, self.dg, self.uf, self.df = set(), set(), set(), set()
        self.kind = None        # None | br | j | jal | jr | jalr | syscall | eret | unknown
        self.target = None
        self.likely = False
        self.mem = None         # (base reg, offset, 'load'|'store', rt, width)


def decode(word, pc):
    i = Ins(pc, word)
    op, rs, rt, rd = word >> 26, (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    sa, fn = (word >> 6) & 31, word & 63
    simm = (word & 0xFFFF) - 0x10000 if word & 0x8000 else word & 0xFFFF
    btarget = (pc + 4 + (simm << 2)) & 0xFFFFFFFF
    U, D = i.ug, i.dg

    def br(uses, likely=False):
        U.update(uses)
        i.kind, i.target, i.likely = "br", btarget, likely

    if op == 0:  # SPECIAL
        if fn in (0, 2, 3, 0x38, 0x3A, 0x3B, 0x3C, 0x3E, 0x3F):
            U.add(rt); D.add(rd)
        elif fn in (4, 6, 7, 0x14, 0x16, 0x17):
            U.update((rt, rs)); D.add(rd)
        elif fn == 8:
            U.add(rs); i.kind = "jr"
        elif fn == 9:
            U.add(rs); D.add(rd); i.kind = "jalr"
        elif fn in (0xA, 0xB):
            U.update((rs, rt, rd)); D.add(rd)
        elif fn == 0xC:
            U.add(3); i.kind = "syscall"
        elif fn in (0xD, 0xF):
            pass
        elif fn in (0x10, 0x12, 0x28):
            D.add(rd)
        elif fn in (0x11, 0x13, 0x29):
            U.add(rs)
        elif fn in (0x18, 0x19):
            U.update((rs, rt)); D.add(rd)
        elif fn in (0x1A, 0x1B) or 0x30 <= fn <= 0x36:
            U.update((rs, rt))
        elif 0x20 <= fn <= 0x27 or fn in (0x2A, 0x2B) or 0x2C <= fn <= 0x2F:
            U.update((rs, rt)); D.add(rd)
        else:
            i.kind = "unknown"
    elif op == 1:  # REGIMM
        if rt in (0, 1, 2, 3):
            br((rs,), likely=rt in (2, 3))
        elif rt in (0x10, 0x11, 0x12, 0x13):
            br((rs,), likely=rt in (0x12, 0x13)); D.add(31)
        else:
            U.add(rs)
    elif op in (2, 3):
        i.kind = "j" if op == 2 else "jal"
        i.target = ((pc + 4) & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
        if op == 3:
            D.add(31)
    elif op in (4, 5, 0x14, 0x15):
        br((rs, rt), likely=op >= 0x14)
    elif op in (6, 7, 0x16, 0x17):
        br((rs,), likely=op >= 0x16)
    elif 8 <= op <= 0xE or op in (0x18, 0x19):
        U.add(rs); D.add(rt)
    elif op == 0xF:
        D.add(rt)
    elif op == 0x1C:  # MMI
        if fn in (0x10, 0x12, 0x30):
            D.add(rd)
        elif fn in (0x11, 0x13, 0x31):
            U.add(rs)
        elif fn in (0x1A, 0x1B):
            U.update((rs, rt))
        elif fn in (0x34, 0x36, 0x37, 0x3C, 0x3E, 0x3F):
            U.add(rt); D.add(rd)
        elif fn == 4:
            U.add(rs); D.add(rd)
        elif fn == 9 and sa in (8, 9):      # pmfhi / pmflo
            D.add(rd)
        elif fn == 0x29 and sa in (8, 9):   # pmthi / pmtlo
            U.add(rs)
        else:
            U.update((rs, rt)); D.add(rd)
    elif op in (0x1A, 0x1B, 0x22, 0x26):    # ldl ldr lwl lwr: merge into rt, but they come in
        U.add(rs); D.add(rt)                # pairs that overwrite all of it (unaligned load)
        i.mem = (rs, simm, "load", rt, 8 if op < 0x20 else 4)
    elif op in (0x1E, 0x20, 0x21, 0x23, 0x24, 0x25, 0x27, 0x37):
        U.add(rs); D.add(rt)
        i.mem = (rs, simm, "load", rt, {0x1E: 16, 0x37: 8, 0x20: 1, 0x24: 1, 0x21: 2, 0x25: 2}.get(op, 4))
    elif op in (0x1F, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x3F):
        U.update((rs, rt))
        i.mem = (rs, simm, "store", rt, {0x1F: 16, 0x3F: 8, 0x28: 1, 0x29: 2, 0x2C: 8, 0x2D: 8}.get(op, 4))
    elif op in (0x2F, 0x33, 0x36, 0x3E):    # cache pref lqc2 sqc2
        U.add(rs)
        if op in (0x36, 0x3E):
            i.mem = (rs, simm, "load" if op == 0x36 else "store", None, 16)
    elif op == 0x31:  # lwc1
        U.add(rs); i.df.add(rt); i.mem = (rs, simm, "load", None, 4)
    elif op == 0x39:  # swc1
        U.add(rs); i.uf.add(rt); i.mem = (rs, simm, "store", None, 4)
    elif op == 0x10:  # COP0
        if rs == 0:
            D.add(rt)
        elif rs == 4:
            U.add(rt)
        elif rs == 8:
            br(())
        elif rs == 0x10 and fn == 0x18:
            i.kind = "eret"
    elif op == 0x11:  # COP1
        fs, fd = rd, sa
        if rs == 0:
            D.add(rt); i.uf.add(fs)
        elif rs == 2:
            D.add(rt)
        elif rs == 4:
            U.add(rt); i.df.add(fs)
        elif rs == 6:
            U.add(rt)
        elif rs == 8:
            br((), likely=bool(rt & 2))
        elif rs == 0x10:
            if fn == 4:
                i.uf.add(rt); i.df.add(fd)
            elif fn in (5, 6, 7, 0x24):
                i.uf.add(fs); i.df.add(fd)
            elif fn in (0x18, 0x19, 0x1A, 0x1E, 0x1F) or fn >= 0x30:
                i.uf.update((fs, rt))
            else:
                i.uf.update((fs, rt)); i.df.add(fd)
        elif rs == 0x14:
            i.uf.add(fs); i.df.add(fd)
    elif op == 0x12:  # COP2 (VU0 macro mode)
        if rs in (1, 2):
            D.add(rt)
        elif rs in (5, 6):
            U.add(rt)
        elif rs == 8:
            br(())
    else:
        i.kind = "unknown"
    U.discard(0)
    D.discard(0)
    return i


# --------------------------------------------------------------------------------------------
# The program image
# --------------------------------------------------------------------------------------------

class Image:
    """Loaded segments of an ELF (or synthetic words for the self-test)."""

    def __init__(self):
        self.segs = []   # (vaddr, bytes, executable)
        self.entry = None
        self.exec_ranges = []   # code sections (SHF_EXECINSTR)

    def is_code(self, addr):
        if self.exec_ranges:
            return any(a <= addr < b for a, b in self.exec_ranges)
        return any(base <= addr < base + len(buf) and exe for base, buf, exe in self.segs)

    @classmethod
    def from_elf(cls, path):
        im = cls()
        data = open(path, "rb").read()
        if data[:4] != b"\x7fELF":
            raise ValueError("not an ELF: " + path)
        im.entry, = struct.unpack_from("<I", data, 0x18)
        shoff, = struct.unpack_from("<I", data, 0x20)
        shentsize, shnum = struct.unpack_from("<HH", data, 0x2E)
        for k in range(shnum if shoff else 0):
            _, _, flags, addr, _, size = struct.unpack_from("<6I", data, shoff + k * shentsize)
            if flags & 4 and size:
                im.exec_ranges.append((addr, addr + size))
        phoff, = struct.unpack_from("<I", data, 0x1C)
        phentsize, phnum = struct.unpack_from("<HH", data, 0x2A)
        for k in range(phnum):
            p_type, p_off, p_vaddr, _, p_filesz, p_memsz, p_flags, _ = struct.unpack_from("<8I", data, phoff + k * phentsize)
            if p_type == 1 and p_filesz:
                im.segs.append((p_vaddr, data[p_off:p_off + p_filesz], bool(p_flags & 1)))
        return im

    def word(self, addr):
        for base, buf, _ in self.segs:
            if base <= addr and addr + 4 <= base + len(buf):
                return struct.unpack_from("<I", buf, addr - base)[0]
        return None

    def bytes_at(self, addr, n):
        for base, buf, _ in self.segs:
            if base <= addr and addr + n <= base + len(buf):
                return buf[addr - base:addr - base + n]
        return None

    def code_words(self):
        for base, buf, exe in self.segs:
            if exe:
                for off in range(0, len(buf) - 3, 4):
                    yield base + off, struct.unpack_from("<I", buf, off)[0]

    def all_words(self):
        for base, buf, _ in self.segs:
            for off in range(0, len(buf) - 3, 4):
                yield base + off, struct.unpack_from("<I", buf, off)[0]


class FuncMap:
    def __init__(self, entries):
        # entries: (start, end, name)
        self.entries = sorted(entries)
        self.starts = [e[0] for e in self.entries]
        self.by_start = {}
        self.maxend = []    # running maximum of ends: lets covering() stop early
        m = 0
        for s, e, n in self.entries:
            self.by_start.setdefault(s, (s, e, n))
            m = max(m, e)
            self.maxend.append(m)

    @classmethod
    def from_csv(cls, path):
        out = []
        for r in csv.DictReader(open(path, encoding="utf-8")):
            try:
                out.append((int(r["Start"], 16), int(r["End"], 16), r["Name"]))
            except (KeyError, ValueError):
                pass
        return cls(out)

    def covering(self, addr, reach=0x40000):
        """Every map entry whose range holds addr (nested fragments included)."""
        out = []
        i = bisect.bisect_right(self.starts, addr) - 1
        while i >= 0 and addr - self.entries[i][0] <= reach and self.maxend[i] > addr:
            s, e, n = self.entries[i]
            if s <= addr < e:
                out.append(self.entries[i])
            i -= 1
        return out

    def containing(self, addr):
        i = bisect.bisect_right(self.starts, addr) - 1
        while i >= 0:
            s, e, n = self.entries[i]
            if s <= addr < e:
                return self.entries[i]
            if addr - s > 0x100000:
                break
            i -= 1
        return None


# --------------------------------------------------------------------------------------------
# Guest analysis: argument registers read before written, caller stack reads, calls
# --------------------------------------------------------------------------------------------

class Guest:
    def __init__(self, start):
        self.start = start
        self.end = start
        self.ninsn = 0
        self.reads = {}        # gpr -> first pc (read before any write on some path)
        self.freads = {}       # fpr -> first pc
        self.stack = {}        # caller-area offset (from entry sp) -> first pc
        self.calls = []        # (pc, target, kind)
        self.syscalls = []     # (pc, number or None)
        self.spills = {}       # arg gpr -> own-frame offset it is stored to before any write
        self.varargs = False
        self.frame = 0
        self.notes = []
        self.via = []          # callees whose argument reads were merged in
        self.indirect = False

    def arity(self):
        n = 0
        for r in self.reads:
            if 4 <= r <= 11:
                n = max(n, r - 3)
        for o in self.stack:
            if o >= 0 and o % 8 == 0:
                n = max(n, 9 + o // 8)
        return n


class Analyzer:
    """Decodes guest functions and computes their argument use, following direct calls."""

    MAX_INSNS = 6000

    def __init__(self, image, funcmap, size_hint=None):
        self.image, self.fmap = image, funcmap
        self.size_hint = size_hint or {}
        self.cache = {}
        self.active = set()

    def extent(self, start):
        if start in self.fmap.by_start:
            s, e, _ = self.fmap.by_start[start]
            if e > s:
                return e
        if start in self.size_hint:
            return start + self.size_hint[start]
        # unknown: up to the first jr ra (plus its delay slot), capped
        pc = start
        for _ in range(self.MAX_INSNS):
            w = self.image.word(pc)
            if w is None:
                return pc
            if w == 0x03E00008:
                return pc + 8
            pc += 4
        return pc

    def insns(self, start, end):
        out = []
        pc = start
        while pc < end and len(out) < self.MAX_INSNS:
            w = self.image.word(pc)
            if w is None:
                break
            out.append(decode(w, pc))
            pc += 4
        return out

    def analyze(self, start, depth=0):
        if start in self.cache:
            return self.cache[start]
        if start in self.active or depth > 6:
            return None
        self.active.add(start)
        try:
            g = self._analyze(start, depth)
        finally:
            self.active.discard(start)
        self.cache[start] = g
        return g

    def _analyze(self, start, depth):
        g = Guest(start)
        end = self.extent(start)
        ins = self.insns(start, end)
        g.end = start + 4 * len(ins)
        g.ninsn = len(ins)
        if not ins:
            g.notes.append("no code at this address")
            return g
        index = {x.pc: k for k, x in enumerate(ins)}
        for x in ins:
            if x.kind == "unknown":
                g.notes.append("undecoded instruction %08x at %#x" % (x.word, x.pc))
                break
        # frame size: the first addiu sp, sp, -N
        for x in ins[:16]:
            if (x.word >> 26) in (9, 0x19) and (x.word >> 21) & 31 == 29 and (x.word >> 16) & 31 == 29:
                imm = x.word & 0xFFFF
                if imm & 0x8000:
                    g.frame = 0x10000 - imm
                break
        caller_stores = set()
        # state: (gdef bitmask, fdef bitmask, sp delta or None, aliases frozenset((reg, off)))
        init = (1 | (1 << 29), 0, 0, frozenset())
        states = {0: init}
        work = [0]
        while work:
            k = work.pop()
            st = states[k]
            for nk, nst in self._step(g, ins, index, k, st, caller_stores, depth):
                if nk is None:
                    continue
                old = states.get(nk)
                if old is None:
                    states[nk] = nst
                    work.append(nk)
                else:
                    merged = (old[0] & nst[0], old[1] & nst[1], old[2] if old[2] == nst[2] else None,
                              old[3] & nst[3])
                    if merged != old:
                        states[nk] = merged
                        work.append(nk)
        for o in list(g.stack):
            if o in caller_stores:
                del g.stack[o]
        # varargs: t3, t2, t1 (at least) spilled to consecutive 8-byte slots
        sp_ = g.spills
        if 11 in sp_ and 10 in sp_ and 9 in sp_ and sp_[11] - sp_[10] == 8 and sp_[10] - sp_[9] == 8:
            g.varargs = True
        return g

    def _apply(self, g, x, st, caller_stores):
        gdef, fdef, delta, alias = st
        for r in x.ug:
            if r in (29,):
                continue
            if not (gdef >> r) & 1 and r not in g.reads:
                g.reads[r] = x.pc
        for f in x.uf:
            if not (fdef >> f) & 1 and f not in g.freads:
                g.freads[f] = x.pc
        if x.mem:
            base, off, how, rt, width = x.mem
            amap = dict(alias)
            if base == 29 and delta is not None:
                where = delta + off
            elif base in amap and amap[base] is not None:
                where = amap[base] + off
            else:
                where = None
            if where is not None:
                if how == "load" and where >= 0 and where not in g.stack:
                    g.stack[where] = x.pc
                elif how == "store":
                    if where >= 0:
                        caller_stores.add(where)
                    elif rt is not None and 4 <= rt <= 11 and not (gdef >> rt) & 1 and rt not in g.spills:
                        g.spills[rt] = where
        # sp and sp-derived aliases
        w = x.word
        op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
        simm = (w & 0xFFFF) - 0x10000 if w & 0x8000 else w & 0xFFFF
        amap = dict(alias)
        newalias = None
        if op in (9, 0x19) and rt == 29 and rs == 29 and delta is not None:
            delta = delta + simm
        elif op in (9, 0x19) and rs == 29 and delta is not None:
            newalias = (rt, delta + simm)
        elif op == 0 and (w & 63) in (0x21, 0x25, 0x2D) and delta is not None:
            rd = (w >> 11) & 31
            if 29 in (rs, rt) and 0 in (rs, rt):
                newalias = (rd, delta)
        elif 29 in x.dg:
            delta = None
        for r in x.dg:
            amap.pop(r, None)
            gdef |= 1 << r
        if newalias and newalias[0] != 29:
            amap[newalias[0]] = newalias[1]
        for f in x.df:
            fdef |= 1 << f
        return (gdef, fdef, delta, frozenset(amap.items()))

    def _call(self, g, x, st, depth, tail):
        """Merge the argument reads of a direct callee into the caller."""
        target = x.target
        gdef, fdef, delta, alias = st
        g.calls.append((x.pc, target, "j" if tail else "jal"))
        callee = self.analyze(target, depth + 1) if self.image.word(target) is not None else None
        if callee is None or callee.varargs:
            return
        merged = False
        for r, _ in callee.reads.items():
            if 4 <= r <= 11 and not (gdef >> r) & 1 and r not in g.reads:
                g.reads[r] = x.pc
                merged = True
        for f in callee.freads:
            if 12 <= f <= 19 and not (fdef >> f) & 1 and f not in g.freads:
                g.freads[f] = x.pc
                merged = True
        if delta is not None:
            for o in callee.stack:
                if delta + o >= 0 and (delta + o) not in g.stack:
                    g.stack[delta + o] = x.pc
                    merged = True
        if merged:
            g.via.append(target)

    def _step(self, g, ins, index, k, st, caller_stores, depth):
        x = ins[k]
        n = len(ins)
        if x.kind in ("br", "j", "jal", "jr", "jalr"):
            st1 = self._apply(g, x, st, caller_stores)
            slot = ins[k + 1] if k + 1 < n else None
            st2 = self._apply(g, slot, st1, caller_stores) if slot else st1
            if x.kind == "syscall":
                pass
            if x.kind == "br":
                taken = index.get(x.target)
                out = []
                if taken is not None:
                    out.append((taken, st2))
                else:
                    g.notes.append("branch out of the function at %#x" % x.pc)
                out.append((k + 2 if k + 2 < n else None, st1 if x.likely else st2))
                return out
            if x.kind == "j":
                t = index.get(x.target)
                if t is not None and x.target != g.start:
                    return [(t, st2)]
                self._call(g, x, st2, depth, tail=True)
                return []
            if x.kind == "jal":
                self._call(g, x, st2, depth, tail=False)
                gdef, fdef, delta, alias = st2
                for r in CALLER_SAVED:
                    gdef |= 1 << r
                fdef |= 0xFFFFF   # f0-f19
                return [(k + 2 if k + 2 < n else None, (gdef, fdef, delta, alias))]
            if x.kind == "jalr":
                g.indirect = True
                gdef, fdef, delta, alias = st2
                for r in CALLER_SAVED:
                    gdef |= 1 << r
                fdef |= 0xFFFFF
                return [(k + 2 if k + 2 < n else None, (gdef, fdef, delta, alias))]
            # jr: return (ra) or an indirect jump (jump table / tail call)
            if (x.word >> 21) & 31 != 31:
                g.indirect = True
            return []
        if x.kind == "syscall":
            num = None
            for back in range(k - 1, max(-1, k - 6), -1):
                y = ins[back]
                if 3 in y.dg:
                    w = y.word
                    if (w >> 26) in (9, 0x19, 0xD) and (w >> 21) & 31 == 0:
                        imm = w & 0xFFFF
                        num = imm - 0x10000 if ((w >> 26) != 0xD and imm & 0x8000) else imm
                    break
            g.syscalls.append((x.pc, num))
        if x.kind == "eret":
            return []
        st1 = self._apply(g, x, st, caller_stores)
        return [(k + 1 if k + 1 < n else None, st1)]


def forwarder(analyzer, start, end, bound_addrs):
    """If start..end only forwards to a bound function, return (target, kind, arg changes)."""
    ins = analyzer.insns(start, end)
    if not ins or len(ins) > 24:
        return None
    calls = [x for x in ins if x.kind in ("j", "jal", "jalr", "br", "jr", "syscall", "unknown", "eret")]
    real = [x for x in calls if not (x.kind == "jr" and (x.word >> 21) & 31 == 31)]
    if len(real) != 1 or real[0].kind not in ("j", "jal") or real[0].target not in bound_addrs:
        return None
    c = real[0]
    changes = []
    for x in ins:
        if x.mem:
            base, off, how, rt, _ = x.mem
            if base != 29 or rt not in (16, 17, 18, 19, 20, 21, 22, 23, 30, 31, None):
                return None
            continue
        if x is c or x.kind == "jr":
            continue
        for r in x.dg:
            if 4 <= r <= 11:
                changes.append("%s set at %#x" % (REG[r], x.pc))
            elif r not in (2, 3, 29, 16, 17, 18, 19, 20, 21, 22, 23, 30, 31, 1):
                if x.pc < c.pc:
                    changes.append("%s set at %#x" % (REG[r], x.pc))
    if c.kind == "jal":
        after = [x for x in ins if x.pc > c.pc + 4]
        for x in after:
            if x.mem or x.kind == "jr":
                continue
            if any(r in (2, 3) for r in x.dg):
                return None   # rewrites the return value: more than a forwarder
    return c.target, c.kind, changes


# --------------------------------------------------------------------------------------------
# SCE symbol database (the analyzer's embedded one)
# --------------------------------------------------------------------------------------------

RELOC_MASK = {"NONE": 0xFFFFFFFF, "MIPS_26": 0xFC000000, "MIPS_HI16": 0xFFFF0000, "MIPS_LO16": 0xFFFF0000,
              "MIPS_GPREL16": 0xFFFF0000, "MIPS_LITERAL": 0xFFFF0000, "MIPS_32": 0}
RELOC_BITS = {"NONE": 32, "MIPS_26": 6, "MIPS_HI16": 16, "MIPS_LO16": 16, "MIPS_GPREL16": 16,
              "MIPS_LITERAL": 16, "MIPS_32": 0}


def norm_reloc(t):
    t = (t or "NONE").upper()
    aliases = {"MIPS26": "MIPS_26", "LO16": "MIPS_LO16", "HI16": "MIPS_HI16", "MIPSLO16": "MIPS_LO16",
               "MIPSHI16": "MIPS_HI16", "MIPS32": "MIPS_32", "MIPSGPREL16": "MIPS_GPREL16",
               "MIPSLITERAL": "MIPS_LITERAL"}
    t = aliases.get(t, t)
    return t if t in RELOC_MASK else "NONE"


class Variant:
    __slots__ = ("lib", "name", "hash", "variant", "size", "relocs", "calls", "static_bits")

    def __init__(self, lib, name, h, v, info):
        self.lib, self.name, self.hash, self.variant = lib, name, h, v
        self.size = int(info.get("size", 0))
        self.relocs = []
        self.calls = {}
        for off, r in (info.get("relocations") or {}).items():
            t = norm_reloc(r.get("type"))
            self.relocs.append((int(off, 0), t))
            if t == "MIPS_26" and r.get("symbol"):
                self.calls[int(off, 0)] = r["symbol"]
        relocated = sum(32 - RELOC_BITS[t] for _, t in self.relocs)
        self.static_bits = max(0, self.size * 8 - relocated)

    def matches(self, raw):
        if raw is None or len(raw) < self.size:
            return False
        b = bytearray(raw[:self.size])
        for off, t in self.relocs:
            if off + 4 <= len(b):
                v = struct.unpack_from("<I", b, off)[0] & RELOC_MASK[t]
                struct.pack_into("<I", b, off, v)
        return hashlib.sha1(bytes(b)).hexdigest() == self.hash


class SymDb:
    def __init__(self, symbols, tree):
        self.byname = {}
        self.bykey = {}
        for lib, names in (symbols or {}).items():
            if not isinstance(names, dict):
                continue
            for name, hashes in names.items():
                for h, variants in hashes.items():
                    for v, info in variants.items():
                        if str(info.get("type", "")).upper() not in ("FUNCTION", "FUNC"):
                            continue
                        var = Variant(lib, name, h, v, info)
                        self.byname.setdefault(name, []).append(var)
                        self.bykey[(lib, name, h, int(v, 16))] = var
        self.tree = tree

    @classmethod
    def from_header(cls, path):
        src = open(path, encoding="utf-8").read()
        cut = src.find("kTreeJsonChunks")
        pat = re.compile(r'R"PS2SDB\((.*?)\)PS2SDB"', re.S)
        symbols = json.loads("".join(pat.findall(src[:cut])))
        tree = json.loads("".join(pat.findall(src[cut:]))) if cut >= 0 else None
        return cls(symbols, tree)

    def candidates(self, image, addr):
        """Variants whose tree path matches at addr (the analyzer's candidate search)."""
        out = []
        if not self.tree:
            return out
        stack = [self.tree]
        while stack:
            node = stack.pop()
            w = image.word(addr + int(node.get("offset", 0)))
            if w is None:
                continue
            for edge in node.get("next", []):
                m = edge.get("match", {})
                t = norm_reloc((m.get("relocation") or {}).get("type"))
                if (w & RELOC_MASK[t]) != (int(m.get("value", 0)) & RELOC_MASK[t]):
                    continue
                child = edge.get("child", {})
                for s in child.get("symbols", []):
                    var = self.bykey.get((s.get("library"), s.get("name"), s.get("hash"), int(s.get("variant", 0))))
                    if var:
                        out.append(var)
                if child.get("next"):
                    stack.append(child)
        return out

    def identify(self, image, addr, min_bits=256):
        """All variants that hash-match at addr with at least min_bits fixed bits (the analyzer's rule)."""
        seen, out = set(), []
        for var in self.candidates(image, addr):
            k = (var.lib, var.name, var.hash, var.variant)
            if k in seen:
                continue
            seen.add(k)
            if var.size and var.static_bits >= min_bits and var.matches(image.bytes_at(addr, var.size)):
                out.append(var)
        return out

    def match_name(self, image, addr, name):
        """Variants of `name` (or with/without a leading underscore) that hash-match at addr."""
        out = []
        for n in {name, name.lstrip("_"), "_" + name}:
            for var in self.byname.get(n, []):
                if var.size and var.matches(image.bytes_at(addr, var.size)):
                    out.append(var)
        return out

    def known(self, name):
        return any(n in self.byname for n in (name, name.lstrip("_"), "_" + name))

    def variants(self, name):
        out = []
        for n in {name, name.lstrip("_"), "_" + name}:
            out.extend(self.byname.get(n, []))
        return out


# --------------------------------------------------------------------------------------------
# Runtime side: call lists, name resolution, handler reads
# --------------------------------------------------------------------------------------------

def load_call_lists(path):
    src = open(path, encoding="utf-8").read()

    def grab(macro):
        m = re.search(r"#define\s+" + macro + r"\(X\)(.*?)(?=\n#define|\Z)", src, re.S)
        return re.findall(r"\bX\((\w+)\)", m.group(1)) if m else []
    return grab("PS2_SYSCALL_LIST"), grab("PS2_STUB_LIST")


def resolve_in(name, entries):
    """ps2_runtime_calls.h resolveNameWithOptionalLeadingUnderscoreAlias."""
    if not name:
        return None
    if name in entries:
        return name
    if name.startswith("_"):
        return name[1:] if name[1:] in entries else None
    if "_" + name in entries:
        return "_" + name
    return None


class Runtime:
    def __init__(self, syscalls, stubs, sources):
        self.syscalls, self.stubs = list(syscalls), list(stubs)
        self.sysset, self.stubset = set(syscalls), set(stubs)
        self.funcs, self.byname, self.helpers, self.vararg = stubaudit.gather(sources) if sources else ([], {}, {}, None)

    @classmethod
    def from_root(cls, root):
        rt = os.path.join(root, "ps2xRuntime")
        syscalls, stubs = load_call_lists(os.path.join(rt, "include", "ps2_call_list.h"))
        kernel = os.path.join(rt, "src", "lib", "Kernel")
        srcs = []
        stubdir = os.path.join(kernel, "Stubs")
        for fn in sorted(os.listdir(stubdir)):
            if fn.endswith(".cpp"):
                p = os.path.join(stubdir, fn)
                srcs.append((p, open(p, encoding="utf-8", errors="replace").read(), "stub"))
        for sub in ("Stubs/Helpers", "Syscalls", "Syscalls/Helpers"):
            d = os.path.join(kernel, sub)
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if fn.endswith((".cpp", ".h")) and os.path.isfile(p):
                    kind = "syscall" if (sub == "Syscalls" and fn.endswith(".cpp")) else "helper"
                    srcs.append((p, open(p, encoding="utf-8", errors="replace").read(), kind))
        return cls(syscalls, stubs, srcs)

    def resolve(self, name):
        """(namespace, handler name, alias used, shadowed stub name) or None."""
        s = resolve_in(name, self.sysset)
        t = resolve_in(name, self.stubset)
        if s:
            return ("ps2_syscalls", s, s != name, t)
        if t:
            return ("ps2_stubs", t, t != name, None)
        return None

    def handler(self, ns, name):
        kind = "syscall" if ns == "ps2_syscalls" else "stub"
        for f in self.byname.get(name, []):
            if f.kind == kind:
                return f
        return None

    def reads(self, f):
        # TODO_NAMED logs a0-a3 for its message: not an argument read of the handler
        byname = {k: v for k, v in self.byname.items() if not k.startswith("TODO")}
        regs, stack, via = stubaudit.effective(f, byname)
        bodies = [f.body]
        seen = set()

        def collect(fn):
            for ns, callee in fn.calls:
                for g in self.byname.get(callee, []):
                    if (g.path, g.line) in seen:
                        continue
                    seen.add((g.path, g.line))
                    bodies.append(g.body)
                    collect(g)
        collect(f)
        fregs = set()
        for b in bodies:
            fregs |= {int(m) for m in re.findall(r"ctx\s*->\s*f\s*\[\s*(\d+)\s*\]", b)}
        todo = bool(re.search(r"\bTODO(?:_NAMED)?\s*\(", f.body))
        todo_only = todo and len([s for s in f.body.strip("{} \n\t").split(";") if s.strip()]) <= 2
        # A typed handler (ps2_stub_abi.h) declares its signature: that is the decoding itself.
        sig = stubaudit.declared_signature(f)
        src = "declared" if sig else None
        declared = getattr(f, "declared", None)
        if declared:
            fregs |= set(declared["layout"]["fregs"])
        if not sig:
            sig = stubaudit.comment_signature(f)
            src = "comment" if sig else None
        if not sig and f.name in stubaudit.SDK_SIGS:
            sig, src = stubaudit.SDK_SIGS[f.name], "builtin"
        legacy = getattr(f, "legacy", None)
        legacy_regs = None
        if legacy is not None:
            lregs, lstack, _ = stubaudit.effective(legacy, byname)
            legacy_regs = sorted(r for r in lregs if 4 <= r <= 11) + ["sp+%#x" % o for o in
                                                                      sorted({o for o, _, _ in lstack if o is not None})]
        return {
            "regs": dict(regs), "stack": stack, "via": via, "fregs": fregs,
            "todo": todo, "todo_only": todo_only, "nargs": sig[0] if sig else None, "sig_source": src,
            "prototype": sig[1] if sig else None, "fp_stack": sig[2] if sig and len(sig) > 2 else 0,
            "vararg_note": any("Ps2VarArgCursor" in n for n in f.notes),
            "typed_varargs": bool(declared and declared["layout"]["varargs"]),
            "typed_form": declared["form"] if declared else None,
            "deviation": declared.get("deviation") if declared else None,
            "legacy_reads": legacy_regs,
        }


# --------------------------------------------------------------------------------------------
# Config side
# --------------------------------------------------------------------------------------------

def kernel_rel(path):
    """A runtime source path relative to ps2xRuntime/src/lib/Kernel/."""
    p = path.replace(os.sep, "/")
    k = p.find("ps2xRuntime/src/lib/Kernel/")
    return p[k + len("ps2xRuntime/src/lib/Kernel/"):] if k >= 0 else p


def parse_selector(s):
    name, _, addr = s.partition("@")
    return name.strip(), (int(addr, 16) if addr.strip() else None)


def read_list_file(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def toml_stubs(path):
    t = tomllib.load(open(path, "rb"))
    return t.get("general", {}).get("stubs", []) or t.get("stubs", [])


class Finding:
    def __init__(self, sev, code, addr, name, msg, detail=None):
        self.sev, self.code, self.addr, self.name, self.msg = sev, code, addr, name, msg
        self.detail = detail or []

    def key(self):
        return (SEV_ORDER[self.sev], self.code, self.addr if self.addr is not None else -1, self.name or "")

    def as_dict(self):
        return {"severity": self.sev, "code": self.code, "address": None if self.addr is None else "%#x" % self.addr,
                "name": self.name, "message": self.msg, "detail": self.detail}


# --------------------------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------------------------

class Checker:
    def __init__(self, image, fmap, runtime, symdb, stubs, sources=None, patches=None, output_bindings=None,
                 names=None):
        self.image, self.fmap, self.rt, self.db = image, fmap, runtime, symdb
        self.stub_entries = stubs                      # list of config selectors
        self.sources = sources or {}                   # selector -> set of source labels
        self.patches = patches or {}                   # addr -> value (config) ; ('analyzer', addr) -> value
        self.output_bindings = output_bindings         # addr -> (trace name, call) or None
        self.extra_names = names or {}                 # addr -> name (entry points etc.)
        self.findings = []
        self.rows = []
        self.entry = image.entry
        self.min_bits = 256      # the analyzer's minimum of fixed bits for a database name
        self.size_hint = {}
        self.analyzer = None

    def add(self, *a, **k):
        self.findings.append(Finding(*a, **k))

    def name_at(self, addr):
        if addr in self.bound:
            return self.bound[addr][0]
        if addr in self.extra_names:
            return self.extra_names[addr]
        e = self.fmap.by_start.get(addr)
        return e[2] if e else None

    def refs(self):
        """Direct jal/j targets and 32-bit words naming each address."""
        if hasattr(self, "_refs"):
            return self._refs
        calls, words = {}, {}
        interesting = set(self.addrs_of_interest) | set(self.fmap.by_start)
        lui = {}
        for pc, w in self.image.code_words():
            op = w >> 26
            if op in (2, 3):
                t = ((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
                calls.setdefault(t, []).append(pc)
            # lui/addiu (or ori) pairs that build an address: function pointers in code
            rs, rt = (w >> 21) & 31, (w >> 16) & 31
            if op == 0xF:
                lui[rt] = (w & 0xFFFF) << 16
                continue
            if op in (9, 0x19, 0xD) and rs in lui:
                imm = w & 0xFFFF
                v = (lui[rs] + (imm - 0x10000 if (op != 0xD and imm & 0x8000) else imm)) & 0xFFFFFFFF if op != 0xD else lui[rs] | imm
                if v in interesting:
                    words.setdefault(v, []).append(pc)
            if lui:
                for r in decode(w, pc).dg:     # any other write ends the pairing
                    lui.pop(r, None)
                if op in (2, 3) or (op == 0 and (w & 63) in (8, 9)):
                    lui.clear()                 # jumps and calls: the pair does not span them
        for pc, w in self.image.all_words():
            # a word inside a function is an instruction, not a pointer (0x001320c0 is "sll a0, s3, 3")
            if w in interesting and not self.fmap.covering(pc, reach=0x4000):
                words.setdefault(w, []).append(pc)
        self._refs = (calls, words)
        self._reach = {}
        return self._refs

    def reachable(self, start, depth=0):
        """True when start has a pointer/address reference or a call from reachable, unbound code."""
        if start in self._reach:
            return self._reach[start]
        if depth > 12:
            return True
        calls, words = self._refs
        if start == self.entry:
            self._reach[start] = True
            return True
        self._reach[start] = False      # cycle guard
        live = any(self.site_live(c, depth + 1) for c in calls.get(start, []) + words.get(start, []))
        self._reach[start] = live
        return live

    def site_live(self, pc, depth=0):
        cov = self.fmap.covering(pc)
        if any(f[0] in self.bound for f in cov):
            return False
        if not cov:
            # outside every mapped function: data (a pointer table) counts, code does not run
            # (the recompiler only emits mapped functions)
            return not self.image.is_code(pc)
        return self.reachable(min(cov)[0], depth)

    def run(self):
        rt, db = self.rt, self.db
        # ---- the binding set as the recompiler builds it ----
        by_addr, names_only = {}, []
        for sel in self.stub_entries:
            name, addr = parse_selector(sel)
            if addr is None:
                names_only.append(name)
                continue
            by_addr.setdefault(addr, []).append(name)
        self.bound = {}
        for addr, names in by_addr.items():
            uniq = list(dict.fromkeys(names))
            self.bound[addr] = (uniq[-1], "config")   # the recompiler keeps the latest
            if len(uniq) > 1:
                self.add("ERROR", "DUP_ADDR", addr, uniq[-1],
                         "one address bound to %d names: %s (the recompiler keeps the last)" % (len(uniq), ", ".join(uniq)))
        name_set = {parse_selector(s)[0] for s in self.stub_entries}
        implicit = {}
        for s, e, n in self.fmap.entries:
            if s in self.bound:
                continue
            if n in name_set or resolve_in(n, rt.stubset):
                implicit[s] = n
        for addr, n in implicit.items():
            self.bound[addr] = (n, "implicit")
        self.addrs_of_interest = set(self.bound)
        # size hints for addresses the map does not know: the database size of the bound name
        for addr, (name, _) in self.bound.items():
            if addr not in self.fmap.by_start and db:
                vs = db.match_name(self.image, addr, name)
                if vs:
                    self.size_hint[addr] = max(v.size for v in vs)
        self.analyzer = Analyzer(self.image, self.fmap, self.size_hint)
        calls, words = self.refs()

        # ---- one name at several addresses ----
        byname = {}
        for addr, (name, how) in self.bound.items():
            byname.setdefault(name, []).append(addr)
        for name, addrs in byname.items():
            if len(addrs) > 1:
                det = []
                verified = True
                for a in sorted(addrs):
                    g = self.analyzer.analyze(a)
                    vs = db.match_name(self.image, a, name) if db else []
                    verified &= bool(vs)
                    det.append("%#x: %d insns, %s, syscalls %s, callers %d" % (
                        a, g.ninsn if g else 0, ("DB " + "/".join(sorted({v.name for v in vs}))) if vs else "no DB match",
                        [hex(n) if n is not None else "?" for _, n in g.syscalls] if g else [],
                        len(calls.get(a, []))))
                self.add("INFO" if verified else "WARN", "DUP_NAME", min(addrs), name,
                         "one name bound at %d addresses: %s" % (len(addrs), ", ".join("%#x" % a for a in sorted(addrs))), det)

        self.implied = {}
        for addr in sorted(self.bound):
            self.check_binding(addr, *self.bound[addr], implicit=addr in implicit, calls=calls, words=words)
        for (t, callee), (res, by) in sorted(self.implied.items()):
            live = self.live_callers(t, calls)
            here = self.fmap.by_start.get(t)
            self.add("WARN" if live else "INFO", "CALLEE_UNBOUND", t, callee,
                     "the database copies of %s call %s here; the runtime implements it (%s::%s) but nothing binds "
                     "this address (map name %s); %d call(s) from reachable guest code" % (
                         ", ".join(sorted(by)), callee, res[0], res[1], here[2] if here else "none", len(live)),
                     ["live callers at " + self.where(live)] if live else [])

        for name in names_only:
            hits = [s for s, e, n in self.fmap.entries if n == name]
            self.add("INFO", "NAME_ONLY", None, name,
                     "bound by name without an address: applies to %d function(s) of that name in the map" % len(hits))

        self.check_wrappers(calls)
        self.check_mixed_libraries(calls)
        self.check_patches()
        self.findings.sort(key=Finding.key)
        return self

    def live_callers(self, addr, calls):
        """Call sites of addr in functions that are not themselves bound (so they run as guest code)."""
        return sorted(c for c in calls.get(addr, []) if self.site_live(c))

    def where(self, pcs, limit=8):
        """'0x123 (in FUN_x)' for a few code addresses."""
        out = []
        for c in pcs[:limit]:
            cov = self.fmap.covering(c)
            outer = min(cov, key=lambda e: e[0]) if cov else None
            out.append("%#x%s" % (c, (" in " + (self.name_at(outer[0]) or outer[2])) if outer else ""))
        return ", ".join(out) + (" ..." if len(pcs) > limit else "")

    def check_binding(self, addr, name, how, implicit, calls, words):
        rt, db = self.rt, self.db
        row = {"address": "%#x" % addr, "name": name, "how": how, "sources": sorted(self.sources.get((name, addr), []))}
        self.rows.append(row)
        ncalls = len(calls.get(addr, []))
        wl = [w for w in words.get(addr, []) if self.site_live(w)]
        nwords = len(wl)
        row["callers"], row["pointer_words"] = ncalls, len(words.get(addr, []))
        if implicit:
            self.add("INFO", "IMPLICIT", addr, name,
                     "bound because the function map names it '%s' (not in the config's stubs)" % name)

        # ---- applied at all? ----
        in_map = addr in self.fmap.by_start
        row["in_map"] = in_map
        emitted = None if self.output_bindings is None else self.output_bindings.get(addr)
        if not in_map:
            live = self.live_callers(addr, calls)
            sev = "ERROR" if (live or nwords) else "WARN"
            cont = self.fmap.containing(addr)
            where = (" (inside %s %#x-%#x)" % (cont[2], cont[0], cont[1])) if cont else " (no function covers it)"
            msg = ("no function starts here in the function map%s, so the recompiler drops this binding; "
                   "%d direct call(s) (%d from reachable guest code), %d live address reference(s) (data word or lui/addiu pair)" % (where, ncalls, len(live), nwords))
            if self.output_bindings is not None:
                msg += "; not in the generated output" if emitted is None else "; yet the output has a wrapper"
            self.add(sev, "NOT_APPLIED", addr, name, msg, ["referenced at " + self.where(wl)] if wl else [])
        elif self.output_bindings is not None and emitted is None:
            self.add("WARN", "NOT_EMITTED", addr, name, "in the function map but the generated output has no stub wrapper for it")

        # ---- runtime handler ----
        res = rt.resolve(name)
        h = None
        if res is None:
            self.add("ERROR", "NO_HANDLER", addr, name,
                     "the runtime has no handler of this name: the recompiler warns and recompiles the guest code instead")
            row["handler"] = None
        else:
            ns, hname, alias, shadow = res
            row["handler"] = "%s::%s" % (ns, hname)
            if alias:
                # WARN when the SDK has both spellings as different functions (internal _X and public X)
                both = bool(self.db and name in self.db.byname and hname in self.db.byname)
                pub = [a for a, (n, _) in self.bound.items() if n == hname]
                self.add("WARN" if both else "INFO", "ALIAS", addr, name,
                         "resolved to %s::%s only by adding/dropping a leading underscore%s" % (
                             ns, hname,
                             ("; the SDK has both %s and %s as separate functions, so the handler's signature is the "
                              "other one's%s" % (name, hname, (" (%s is bound at %s)" % (hname, ", ".join("%#x" % a for a in pub))) if pub else ""))
                             if both else ""))
            if shadow:
                self.add("WARN", "SHADOWED", addr, name,
                         "name is in both PS2_SYSCALL_LIST and PS2_STUB_LIST: ps2_syscalls::%s runs, ps2_stubs::%s never "
                         "does for a binding" % (hname, shadow))
            f = rt.handler(ns, hname)
            if f is not None:
                h = rt.reads(f)
                row["handler_at"] = "%s:%d" % (kernel_rel(f.path), f.line)
                if h["todo_only"]:
                    live = self.live_callers(addr, calls)
                    self.add("ERROR" if (live or nwords) else "WARN", "TODO_HANDLER", addr, name,
                             "handler %s::%s (%s) is a TODO that throws when called; %d direct call(s), %d from reachable "
                             "guest code, %d live address reference(s)" % (ns, hname, row["handler_at"], ncalls, len(live), nwords),
                             (["live callers at " + self.where(live)] if live else []) +
                             (["referenced at " + self.where(wl)] if wl else []))
                elif h["todo"]:
                    self.add("INFO", "PARTIAL_HANDLER", addr, name,
                             "handler %s::%s (%s) has TODO paths" % (ns, hname, row["handler_at"]))
            else:
                row["handler_at"] = None

        # ---- the guest copy ----
        g = self.analyzer.analyze(addr)
        if g is None:
            return
        row.update({
            "size": g.end - g.start, "insns": g.ninsn, "arity": g.arity(), "varargs": g.varargs,
            "reads": [REG[r] for r in sorted(g.reads) if 4 <= r <= 11],
            "freads": ["f%d" % r for r in sorted(g.freads) if 12 <= r <= 19],
            "stack": ["sp+%#x" % o for o in sorted(g.stack)],
            "calls": ["%s%s" % ("j " if k == "j" else "", self.name_at(t) or "%#x" % t) for _, t, k in g.calls],
            "syscalls": [("%#x" % n) if n is not None and n >= 0 else (str(n) if n is not None else "?") for _, n in g.syscalls],
            "notes": g.notes,
        })

        # ---- SDK database ----
        if db:
            self.check_db(addr, name, g, row)

        # ---- ABI: handler reads vs the guest copy ----
        if h is not None:
            self.check_abi(addr, name, g, h, row)

    def check_db(self, addr, name, g, row):
        db = self.db
        mine = db.match_name(self.image, addr, name)
        others = [v for v in db.identify(self.image, addr, self.min_bits) if v.name.lstrip("_") != name.lstrip("_")]
        row["db"] = None
        if mine:
            best = max(mine, key=lambda v: (v.static_bits, v.size))
            row["db"] = {"lib": best.lib, "size": best.size, "static_bits": best.static_bits, "name": best.name}
            if best.static_bits < self.min_bits and g is not None and g.syscalls and not g.calls:
                return   # a kernel-call wrapper: the syscall number is in the matched bytes
            if best.static_bits < self.min_bits:
                self.add("INFO", "LOW_CONFIDENCE", addr, name,
                         "matches the database's %s on only %d fixed bits (%d bytes): too small to tell apart from "
                         "other functions of the same shape" % (best.name, best.static_bits, best.size))
                return
            other_names = sorted({v.name for v in others if v.size >= best.size})
            if other_names:
                self.add("WARN", "LOW_CONFIDENCE", addr, name,
                         "bytes also match %s in the database (ambiguous; the analyzer would not name it)" % ", ".join(other_names))
            elif best.static_bits < LOW_CONFIDENCE_BITS:
                self.add("INFO", "LOW_CONFIDENCE", addr, name,
                         "database match rests on %d fixed bits (%d bytes): small functions can coincide" % (best.static_bits, best.size))
            if g and best.size > (g.end - g.start) and addr in self.fmap.by_start:
                self.add("WARN", "SIZE", addr, name,
                         "the function map ends it at %#x, the SDK copy is %d bytes: the map may split it" % (g.end, best.size))
            self.check_db_calls(addr, name, best)
            return
        if others:
            best = max(others, key=lambda v: v.static_bits)
            self.add("ERROR", "DB_OTHER", addr, name,
                     "the bytes here are %s (%s, %d fixed bits) in the SDK database, not %s" % (
                         best.name, best.lib, best.static_bits, name))
            row["db"] = {"other": best.name}
            return
        if db.known(name):
            vs = db.variants(name)
            sizes = sorted({v.size for v in vs})
            size = g.end - g.start if g else 0
            callsets = []
            for v in vs:
                cs = sorted(set(v.calls.values()))
                if cs not in callsets:
                    callsets.append(cs)
            mine_calls = sorted({(self.name_at(t) or "%#x" % t) for _, t, _ in (g.calls if g else [])})
            det = ["SDK sizes %s, this copy %d bytes" % (sizes[:6], size),
                   "SDK callees %s" % ("; ".join(", ".join(c) or "-" for c in callsets[:3])),
                   "this copy calls %s" % (", ".join(mine_calls) or "-")]
            sev = "WARN"
            norm = lambda s: {x.lstrip("_") for x in s}
            if g and callsets and not any(norm(c) == norm(mine_calls) for c in callsets) and \
                    not any(abs(s - size) <= 8 for s in sizes):
                sev = "WARN"
                det.append("neither size nor callees agree with any SDK version: check the name")
                code = "DB_DIFFERS"
            else:
                code = "DB_NOMATCH"
            self.add(sev if code == "DB_DIFFERS" else "INFO", code, addr, name,
                     "the SDK database knows %s, but no version of it matches these bytes" % name, det)
            row["db"] = {"nomatch": True}
        else:
            self.add("INFO", "DB_UNKNOWN", addr, name, "not in the SDK database: the name cannot be verified from the bytes")

    def check_db_calls(self, addr, name, var):
        for off, callee in sorted(var.calls.items()):
            w = self.image.word(addr + off)
            if w is None or (w >> 26) not in (2, 3):
                continue
            t = ((addr + off + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
            here = self.bound.get(t)
            if here and here[0].lstrip("_") != callee.lstrip("_"):
                self.add("ERROR", "CALLEE_NAME", t, here[0],
                         "%s (%#x, a database match) calls %s here, but this address is bound as %s" % (
                             name, addr, callee, here[0]))
            elif not here:
                res = self.rt.resolve(callee)
                if res:
                    self.implied.setdefault((t, callee), (res, set()))[1].add(name)

    def check_abi(self, addr, name, g, h, row):
        hregs = {r for r in h["regs"] if 4 <= r <= 11}
        hstack = sorted({o for o, _, _ in h["stack"] if o is not None})
        hstack_unknown = [s for s in h["stack"] if s[0] is None]
        row["handler_reads"] = [REG[r] for r in sorted(hregs)] + ["sp+%#x" % o for o in hstack] + \
            (["sp+?"] if hstack_unknown else [])
        row["handler_nargs"] = h["nargs"]
        row["handler_sig_source"] = h["sig_source"]
        if h.get("typed_form"):
            row["handler_typed"] = h["typed_form"]
            row["handler_legacy_reads"] = [REG[x] if isinstance(x, int) else x for x in (h.get("legacy_reads") or [])]
        if h.get("deviation"):
            row["handler_deviation"] = h["deviation"]
        greads = {r for r in g.reads if 4 <= r <= 11}
        gstack = set(g.stack)
        syscall_wrapper = bool(g.syscalls) and not g.calls
        lines = []

        def where_h(r):
            ln = h["regs"].get(r)
            return (" (line %s)" % ln) if ln else ""

        if g.ninsn == 0:
            return
        if g.varargs:
            note = "guest copy is variadic (spills %s)" % ", ".join(REG[r] for r in sorted(g.spills))
            if h.get("typed_varargs"):
                note += "; typed handler (ps2_stub_abi.h): VarArgs continue a0-t3, then sp+0 in 8-byte slots"
            elif h["vararg_note"]:
                note += "; handler walks varargs with Ps2VarArgCursor (slots 8+ from sp+0, 8-byte steps)"
            self.add("INFO", "VARARGS", addr, name, note)
            return
        # stack reads by the handler
        for o in hstack:
            if o in gstack:
                continue
            ln = [s[1] for s in h["stack"] if s[0] == o]
            if 16 <= o <= 28:
                self.add("ERROR", "ABI_STACK_O32", addr, name,
                         "handler reads sp+%#x (an O32 home slot: arguments 5-8 travel in t0-t3 on the EE); the guest copy "
                         "reads %s" % (o, self._gdesc(g)), ["handler %s line %s" % (row.get("handler_at"), ln[:3])])
            else:
                self.add("ERROR", "ABI_STACK", addr, name,
                         "handler reads caller stack sp+%#x, the guest copy never does (it reads %s)" % (o, self._gdesc(g)),
                         ["handler %s line %s" % (row.get("handler_at"), ln[:3])])
        for r in sorted(hregs - greads):
            if r >= 8:
                if g.indirect and not g.reads:
                    continue
                self.add("ERROR" if not syscall_wrapper else "WARN", "ABI_TREG", addr, name,
                         "handler reads %s%s, which the guest copy never reads (it reads %s): the caller leaves "
                         "whatever was there" % (REG[r], where_h(r), self._gdesc(g)))
            elif not syscall_wrapper and not g.indirect:
                lines.append("handler reads %s%s, the guest copy does not" % (REG[r], where_h(r)))
        for r in sorted(greads - hregs):
            if r >= 8:
                self.add("WARN", "ARG_IGNORED", addr, name,
                         "the guest copy reads %s (argument %d) at %#x, the handler never does" % (REG[r], r - 3, g.reads[r]))
            else:
                lines.append("guest reads %s at %#x, the handler does not" % (REG[r], g.reads[r]))
        for o in sorted(gstack):
            if o not in hstack and not hstack_unknown:
                self.add("WARN", "ARG_IGNORED", addr, name,
                         "the guest copy reads caller stack sp+%#x (argument %d) at %#x, the handler never does" % (
                             o, 9 + o // 8 if o % 8 == 0 else -1, g.stack[o]))
        gf = {f for f in g.freads if 12 <= f <= 19}
        hf = {f for f in h["fregs"] if 12 <= f <= 19}
        if gf != hf and (gf or hf):
            lines.append("float args: guest reads %s, handler reads %s" % (
                ",".join("f%d" % f for f in sorted(gf)) or "none", ",".join("f%d" % f for f in sorted(hf)) or "none"))
        if h["nargs"] is not None:
            nreg = max([r - 3 for r in greads], default=0)
            nslots = len([o for o in gstack if o % 8 == 0])
            allowed_slots = max(0, h["nargs"] - 8) + h["fp_stack"]
            if nreg > h["nargs"] or nslots > allowed_slots:
                msg = "stubaudit's signature (%s) gives %d integer args%s, the guest copy reads %s" % (
                    h["sig_source"], h["nargs"], (" + %d float stack slot(s)" % h["fp_stack"]) if h["fp_stack"] else "",
                    self._gdesc(g))
                if h.get("deviation"):
                    # The declaration says why it deviates from the SDK prototype (PS2_STUB_DEVIATION).
                    self.add("INFO", "SIGTABLE_DEV", addr, name, msg + "; declared deviation: " + h["deviation"])
                else:
                    self.add("WARN", "SIGTABLE", addr, name, msg)
        if lines:
            self.add("INFO", "ABI_AREG", addr, name, "; ".join(lines))

    def _gdesc(self, g):
        parts = [REG[r] for r in sorted(g.reads) if 4 <= r <= 11] + ["sp+%#x" % o for o in sorted(g.stack)]
        return ", ".join(parts) or "no argument registers"

    def check_wrappers(self, calls):
        bound_addrs = set(self.bound)
        for s, e, n in self.fmap.entries:
            if s in bound_addrs or e - s > 96:
                continue
            if any(f[0] < s for f in self.fmap.covering(s)):
                continue   # a fragment inside another function, not a function of its own
            fw = forwarder(self.analyzer, s, e, bound_addrs)
            if not fw:
                continue
            target, kind, changes = fw
            tname = self.bound[target][0]
            ncallers = len(calls.get(s, []))
            ids = [v.name for v in self.db.identify(self.image, s, self.min_bits)] if self.db else []
            if len(set(ids)) > 3:
                ids = []
            live = self.live_callers(s, calls)
            msg = "%s only %s %s (%#x)%s; %d direct caller(s), %d from reachable guest code" % (
                n, "tail-jumps to" if kind == "j" else "calls", tname, target,
                (" after setting " + ", ".join(sorted({c.split()[0] for c in changes}))) if changes else "", ncallers, len(live))
            sev = "INFO"
            if ids:
                msg += "; the SDK database names it %s" % "/".join(sorted(set(ids)))
                if any(self.rt.resolve(i) for i in ids) and live:
                    sev = "WARN"
            if (self.rt.resolve(tname) or (0, 0, False))[2]:
                sev = "WARN" if live else sev
                msg += "; the target is bound through an underscore alias"
            if live:
                changes = ["live callers at " + self.where(live)] + changes
            self.add(sev, "WRAPPER", s, n, msg, changes[:8])

    def libs_of(self, name):
        return {v.lib for v in self.db.variants(name)} if self.db else set()

    def check_mixed_libraries(self, calls):
        """SDK functions kept as guest code in a library the runtime otherwise owns.

        The runtime's handlers keep their own state (open files, RPC clients, DMA, ...); a guest copy of
        a sibling function works on the library's guest state instead, which the handlers never set up."""
        if not self.db:
            return
        bound_libs = {}
        for addr, (name, _) in self.bound.items():
            for lib in self.libs_of(name):
                bound_libs.setdefault(lib, []).append(name)
        skip = {"libc", "libm", "libgcc", "gcc_wrapper", "mslgcc_ps2", "crt0", "libkernl"}
        for addr, name in sorted(self.extra_names.items()):
            if addr in self.bound or self.rt.resolve(name):
                continue
            libs = self.libs_of(name) - skip
            hit = [l for l in libs if l in bound_libs]
            if not hit:
                continue
            live = self.live_callers(addr, calls)
            if not live:
                continue
            g = self.analyzer.analyze(addr)
            callees = sorted({self.name_at(t) or "%#x" % t for _, t, _ in g.calls}) if g else []
            self.add("WARN", "MIXED_LIB", addr, name,
                     "%s function kept as guest code (the runtime has no %s) while %d other %s functions are runtime "
                     "handlers; it calls %s" % (hit[0], name, len(bound_libs[hit[0]]), hit[0], ", ".join(callees[:8]) or "nothing"),
                     ["live callers at " + self.where(live)])

    def check_patches(self):
        for key, value in sorted(self.patches.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
            src, addr = key
            old = self.image.word(addr)
            try:
                new = int(str(value), 0)
            except ValueError:
                new = None
            if old is None:
                continue
            x = decode(old, addr)
            is_store = bool(x.mem and x.mem[2] == "store")
            if new == 0 and is_store:
                sev = "ERROR" if src == "config" else "INFO"
                where = "" if src == "config" else " (in %s, not in the config)" % src
                self.add(sev, "PATCH_STORE_NOP", addr, (self.fmap.containing(addr) or (0, 0, None))[2],
                         "patch turns the store %08x into a NOP%s" % (old, where))
            elif src == "config":
                self.add("INFO", "PATCH", addr, None, "patch %08x -> %s" % (old, value))

    # ---- output ----
    def counts(self):
        c = {"ERROR": 0, "WARN": 0, "INFO": 0}
        for f in self.findings:
            c[f.sev] += 1
        return c

    def summary(self):
        c = self.counts()
        codes = {}
        for f in self.findings:
            if f.sev != "INFO":
                codes[f.code] = codes.get(f.code, 0) + 1
        top = ", ".join("%s %d" % kv for kv in sorted(codes.items(), key=lambda kv: -kv[1]))
        return "[bindcheck] %d bindings: %d ERROR, %d WARN, %d INFO%s" % (
            len(self.bound), c["ERROR"], c["WARN"], c["INFO"], (" (" + top + ")") if top else "")

    def report(self, title):
        out = ["Binding check: " + title, self.summary(), ""]
        out.append("Severity: ERROR = likely wrong at run time, WARN = check by hand, INFO = context.")
        out.append("Guest reads: argument registers read before written (calls and tail jumps followed); stack offsets")
        out.append("are from the caller's sp at entry (EE: args 9+ at sp+0, sp+8, ...). Handler reads: tools/stubaudit.py;")
        out.append("handler paths are relative to ps2xRuntime/src/lib/Kernel/. 'Reachable guest code': a call or address")
        out.append("reference from code that is not bound and is itself reachable (from the ELF entry or a data word).")
        out.append("")
        for sev in ("ERROR", "WARN", "INFO"):
            group = [f for f in self.findings if f.sev == sev]
            if not group:
                continue
            out.append("== %s (%d) ==" % (sev, len(group)))
            for f in group:
                a = ("%#x" % f.addr) if f.addr is not None else "-"
                out.append("%-15s %-9s %-28s %s" % (f.code, a, f.name or "", f.msg))
                for d in f.detail:
                    out.append(" " * 26 + "| " + d)
            out.append("")
        out.append("== Bindings (%d) ==" % len(self.rows))
        for r in sorted(self.rows, key=lambda r: int(r["address"], 16)):
            db = r.get("db")
            dbs = ("db %s %dB %db" % (db["lib"], db["size"], db["static_bits"])) if db and "lib" in db else \
                ("db=" + db["other"] if db and "other" in db else ("db nomatch" if db else "db -"))
            out.append("%s %-26s %-8s %s" % (r["address"], r["name"], r["how"], r.get("handler") or "NO HANDLER"))
            if "insns" in r:
                out.append("    guest %d insns, arity>=%d%s, reads %s; calls %s; syscalls %s; %s; callers %d, ptr words %d" % (
                    r["insns"], r["arity"], " varargs" if r["varargs"] else "",
                    ",".join(r["reads"] + r["freads"] + r["stack"]) or "-",
                    ",".join(r["calls"][:6]) + ("..." if len(r["calls"]) > 6 else "") or "-",
                    ",".join(r["syscalls"]) or "-", dbs, r["callers"], r["pointer_words"]))
            if "handler_reads" in r:
                out.append("    handler %s reads %s%s" % (r.get("handler_at"), ",".join(r["handler_reads"]) or "-",
                                                          (" (sig %d args, %s)" % (r["handler_nargs"], r.get("handler_sig_source")))
                                                          if r.get("handler_nargs") is not None else ""))
                if r.get("handler_typed"):
                    out.append("    typed (%s); legacy form reads %s" % (
                        r["handler_typed"], ",".join(r.get("handler_legacy_reads") or []) or "-"))
                if r.get("handler_deviation"):
                    out.append("    deviation (declared): %s" % r["handler_deviation"])
        return "\n".join(out) + "\n"


def load_output_bindings(outdir):
    """addr -> (trace name, handler call) for every stub wrapper the recompiler wrote."""
    out = {}
    pat = re.compile(r'ps2xStubTrace\("([^"]*)", 0x([0-9a-fA-F]+)u, rdram, ctx, false\);\s*(ps2_\w+::\w+|ps2_stubs::TODO_NAMED\("[^"]*")')
    for fn in os.listdir(outdir):
        if not fn.endswith(".cpp") or not re.search(r"_0x[0-9a-f]+\.cpp$", fn):
            continue
        p = os.path.join(outdir, fn)
        if os.path.getsize(p) > 4096:
            continue
        m = pat.search(open(p, encoding="utf-8", errors="replace").read())
        if m:
            out[int(m.group(2), 16)] = (m.group(1), m.group(3))
    return out


# --------------------------------------------------------------------------------------------
# Self-test: a synthetic program, runtime source and database
# --------------------------------------------------------------------------------------------

def asm(text, base):
    """A tiny assembler for the self-test (only what it needs)."""
    R = {n: k for k, n in enumerate(REG)}
    words = []
    labels = {}
    lines = [l.split("#")[0].strip() for l in text.strip().splitlines()]
    lines = [l for l in lines if l]
    pc = base
    for l in lines:
        if l.endswith(":"):
            labels[l[:-1]] = pc
        else:
            pc += 4
    pc = base
    for l in lines:
        if l.endswith(":"):
            continue
        op, _, rest = l.partition(" ")
        a = [x.strip() for x in rest.split(",")] if rest else []

        def r(x):
            return R[x.lstrip("$")]

        def imm(x):
            return labels[x] if x in labels else int(x, 0)

        def mem(x):
            off, base_ = x.split("(")
            return int(off, 0) & 0xFFFF, r(base_.rstrip(")"))
        if op == "nop":
            w = 0
        elif op == "jr":
            w = (r(a[0]) << 21) | 8
        elif op == "syscall":
            w = 0xC
        elif op in ("addiu", "daddiu"):
            w = ((9 if op == "addiu" else 0x19) << 26) | (r(a[1]) << 21) | (r(a[0]) << 16) | (imm(a[2]) & 0xFFFF)
        elif op == "li":
            w = (9 << 26) | (r(a[0]) << 16) | (imm(a[1]) & 0xFFFF)
        elif op == "move":
            w = (r(a[1]) << 21) | (r(a[0]) << 11) | 0x2D
        elif op == "addu":
            w = (r(a[1]) << 21) | (r(a[2]) << 16) | (r(a[0]) << 11) | 0x21
        elif op in ("lw", "sw", "ld", "sd"):
            off, b = mem(a[1])
            w = ({"lw": 0x23, "sw": 0x2B, "ld": 0x37, "sd": 0x3F}[op] << 26) | (b << 21) | (r(a[0]) << 16) | off
        elif op in ("jal", "j"):
            w = ((3 if op == "jal" else 2) << 26) | ((imm(a[0]) >> 2) & 0x3FFFFFF)
        elif op == "beq":
            w = (4 << 26) | (r(a[0]) << 21) | (r(a[1]) << 16) | (((imm(a[2]) - pc - 4) >> 2) & 0xFFFF)
        elif op == "word":
            w = imm(a[0])
        else:
            raise ValueError(op)
        words.append(w)
        pc += 4
    return words


SELFTEST_PROGRAM = """
# 0x1000 sceGood(a0..a3, t0, t1, t2): reads t0-t2 in registers
sceGood:
    addu v0, a0, a1
    addu v0, a2, a3
    addu v0, t0, t1
    addu v0, v0, t2
    jr ra
    nop
# 0x1018 sceOld(a0..a3, t0, t1): the handler reads O32 home slots
sceOld:
    addiu sp, sp, -32
    sd ra, 0(sp)
    addu v0, t0, t1
    ld ra, 0(sp)
    jr ra
    addiu sp, sp, 32
# 0x1030 sceNine: 9 args, the ninth from the caller's sp+0 (frame of 16)
sceNine:
    addiu sp, sp, -16
    lw v0, 16(sp)
    addu v0, v0, t3
    jr ra
    addiu sp, sp, 16
# 0x1044 sceFour(a0..a3); the handler reads t0 too
sceFour:
    addu v0, a0, a1
    addu v1, a2, a3
    jr ra
    nop
# 0x1054 wrapper: forwards to sceGood with t2 = 0 (not bound: a missed wrapper)
wrap:
    li t2, 0
    j sceGood
    nop
# 0x1060 a function nobody names
unnamed:
    jr ra
    nop
# 0x1068 passthru: tail-jumps to sceNine untouched, so it reads what sceNine reads
passthru:
    j sceNine
    nop
# 0x1070 printf-like: spills a1..t3
vprint:
    addiu sp, sp, -64
    sd a1, 8(sp)
    sd a2, 16(sp)
    sd a3, 24(sp)
    sd t0, 32(sp)
    sd t1, 40(sp)
    sd t2, 48(sp)
    sd t3, 56(sp)
    jr ra
    addiu sp, sp, 64
# 0x1098 syscall wrapper
sysw:
    li v1, 0x3c
    syscall
    jr ra
    nop
# 0x10a8 store to be NOPed by a patch
storer:
    sw a0, 0(a1)
    jr ra
    nop
"""

SELFTEST_RT_LIST = r'''
#define PS2_SYSCALL_LIST(X) \
    X(SetupThread)          \
    X(sceShadow)
#define PS2_STUB_LIST(X)    \
    X(sceGood)              \
    X(sceOld)               \
    X(sceNine)              \
    X(sceFour)              \
    X(vprint)               \
    X(sceShadow)            \
    X(sceTodo)              \
    X(sceAlias)
'''

SELFTEST_RT_SRC = r'''
namespace ps2_stubs {
    static uint32_t readStackU32(uint8_t *rdram, R5900Context *ctx, uint32_t offset)
    {
        uint32_t sp = getRegU32(ctx, 29);
        const uint8_t *ptr = getConstMemPtr(rdram, sp + offset);
        return ptr ? *ptr : 0;
    }
    void sceGood(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t a = getRegU32(ctx, 4), b = getRegU32(ctx, 5), c = getRegU32(ctx, 6), d = getRegU32(ctx, 7);
        uint32_t e = getRegU32(ctx, 8), f = getRegU32(ctx, 9), g = getRegU32(ctx, 10);
    }
    void sceOld(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t e = readStackU32(rdram, ctx, 16), f = readStackU32(rdram, ctx, 20);
    }
    void sceNine(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t h = getRegU32(ctx, 11);
        uint32_t i = readStackU32(rdram, ctx, 0);
    }
    void sceFour(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t a = getRegU32(ctx, 4), b = getRegU32(ctx, 5), c = getRegU32(ctx, 6), d = getRegU32(ctx, 7);
        uint32_t e = getRegU32(ctx, 8);
    }
    void vprint(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t f = getRegU32(ctx, 4);
    }
    void sceShadow(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t f = getRegU32(ctx, 4);
    }
    void sceTodo(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        TODO_NAMED("sceTodo", rdram, ctx, runtime);
    }
    void sceAlias(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t f = getRegU32(ctx, 4);
    }
}
'''

SELFTEST_RT_SYS = r'''
namespace ps2_syscalls {
    void SetupThread(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t a = getRegU32(ctx, 4);
    }
    void sceShadow(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t a = getRegU32(ctx, 4);
    }
}
'''


SELFTEST_TYPED_SRC = r'''
namespace ps2_stubs {
    static uint32_t readStackU32(uint8_t *rdram, R5900Context *ctx, uint32_t offset)
    {
        uint32_t sp = getRegU32(ctx, 29);
        const uint8_t *ptr = getConstMemPtr(rdram, sp + offset);
        return ptr ? *ptr : 0;
    }
    PS2_STUB_TYPED(int32_t, sceTypedFive, uint32_t a, uint32_t b, float scale, uint32_t c, uint32_t d, uint32_t e)
    {
        return static_cast<int32_t>(a + e * scale);
    }
    void sceTypedFive_legacy(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        setReturnS32(ctx, getRegU32(ctx, 4) + readStackU32(rdram, ctx, 16));
    }
    PS2_STUB_DISPATCH(sceTypedFive)
    PS2_STUB_DEVIATION(sceTypedFive, "a test deviation (SDK: short e)")
}
'''


def selftest():
    import tempfile
    base = 0x1000
    words = asm(SELFTEST_PROGRAM, base)
    im = Image()
    im.segs.append((base, b"".join(struct.pack("<I", w) for w in words), True))
    # data words: a pointer to 0x2000 (a binding at a hole in the map) and a call to it below
    labels = {"sceGood": 0x1000, "sceOld": 0x1018, "sceNine": 0x1030, "sceFour": 0x1044, "wrap": 0x1054,
              "unnamed": 0x1060, "passthru": 0x1068, "vprint": 0x1070, "sysw": 0x1098, "storer": 0x10a8}
    ends = {"sceGood": 0x1018, "sceOld": 0x1030, "sceNine": 0x1044, "sceFour": 0x1054, "wrap": 0x1060,
            "unnamed": 0x1068, "passthru": 0x1070, "vprint": 0x1098, "sysw": 0x10a8, "storer": 0x10b4}
    im.segs.append((0x3000, struct.pack("<II", 0x2000, (3 << 26) | (0x2000 >> 2)), True))
    fmap = FuncMap([(labels[n], ends[n], ("FUN_%08x" % labels[n]) if n in ("wrap", "unnamed", "passthru", "storer") else n)
                    for n in labels] + [(0x3004, 0x3008, "FUN_00003004")])
    im.entry = 0x3004
    # synthetic SDK database: sceGood's real bytes; sceFour named for unnamed's bytes ("jr ra; nop")
    raw = im.bytes_at(0x1000, 24)
    good_hash = hashlib.sha1(raw).hexdigest()
    four_raw = im.bytes_at(0x1044, 16)
    symbols = {"libtest": {
        "sceGood": {good_hash: {"00000001": {"type": "FUNCTION", "size": 24, "relocations": {}}}},
        "sceOther": {hashlib.sha1(four_raw).hexdigest(): {"00000002": {"type": "FUNCTION", "size": 16, "relocations": {}}}},
        "sceNine": {"0" * 40: {"00000003": {"type": "FUNCTION", "size": 20, "relocations": {}}}},
    }}
    w0 = struct.unpack_from("<I", raw, 0)[0]
    w4 = struct.unpack_from("<I", four_raw, 0)[0]
    tree = {"offset": 0, "next": [
        {"match": {"value": w0}, "child": {"offset": 4, "symbols": [{"library": "libtest", "name": "sceGood", "hash": good_hash, "variant": 1}]}},
        {"match": {"value": w4}, "child": {"offset": 4, "symbols": [{"library": "libtest", "name": "sceOther",
                                                                      "hash": hashlib.sha1(four_raw).hexdigest(), "variant": 2}]}},
    ]}
    db = SymDb(symbols, tree)
    tmp = tempfile.mkdtemp(prefix="bindcheck_")
    lst = os.path.join(tmp, "ps2_call_list.h")
    open(lst, "w").write(SELFTEST_RT_LIST)
    syscalls, stubs = load_call_lists(lst)
    rt = Runtime(syscalls, stubs, [("t/Stubs.cpp", SELFTEST_RT_SRC, "stub"), ("t/Syscalls.cpp", SELFTEST_RT_SYS, "syscall")])
    stubs_cfg = ["sceGood@0x1000", "sceOld@0x1018", "sceNine@0x1030", "sceFour@0x1044", "vprint@0x1070",
                 "SetupThread@0x1098", "sceShadow@0x10a8", "sceMissing@0x1098", "sceTodo@0x2000",
                 "_sceAlias@0x10a8"]
    ck = Checker(im, fmap, rt, db, stubs_cfg, patches={("config", 0x10a8): "0x0"})
    ck.min_bits = 64   # the synthetic functions are tiny
    ck.run()
    got = {}
    for f in ck.findings:
        got.setdefault((f.code, f.addr), f)
    expect = [
        ("ABI_STACK_O32", 0x1018, "handler reads the O32 slot sp+0x10"),
        ("ARG_IGNORED", 0x1018, "guest reads t0, the handler does not"),
        ("ABI_TREG", 0x1044, "handler reads t0 that the guest never reads"),
        ("NOT_APPLIED", 0x2000, "binding at a hole in the map, called once (ERROR)"),
        ("TODO_HANDLER", 0x2000, "handler is a TODO"),
        ("DUP_ADDR", 0x1098, "SetupThread and sceMissing on one address"),
        ("NO_HANDLER", 0x1098, "sceMissing (kept last) is not implemented"),
        ("DUP_ADDR", 0x10a8, "sceShadow and _sceAlias"),
        ("ALIAS", 0x10a8, "_sceAlias resolves through the underscore"),
        ("DB_OTHER", 0x1044, "sceFour's bytes are sceOther in the database"),
        ("DB_NOMATCH", 0x1030, "sceNine known, no version matches"),
        ("VARARGS", 0x1070, "vprint spills a1..t3"),
        ("WRAPPER", 0x1054, "wrap forwards to sceGood"),
        ("WRAPPER", 0x1068, "passthru tail-jumps to sceNine"),
        ("PATCH_STORE_NOP", 0x10a8, "patch NOPs a store"),
        ("LOW_CONFIDENCE", 0x1000, "sceGood matches on 192 fixed bits"),
    ]
    ok = True
    for code, addr, label in expect:
        hit = (code, addr) in got
        ok &= hit
        print("%-16s %#7x %-4s %s" % (code, addr, "ok" if hit else "FAIL", label))
    errs = {(f.code, f.addr) for f in ck.findings if f.sev == "ERROR"}
    not_expected = [("ABI_STACK", 0x1030), ("ABI_TREG", 0x1000), ("ABI_STACK_O32", 0x1030)]
    for code, addr in not_expected:
        hit = (code, addr) in got
        ok &= not hit
        print("%-16s %#7x %-4s %s" % (code, addr, "ok" if not hit else "FAIL", "absent (the binding is right)"))
    an = ck.analyzer
    checks = [
        ("sceNine reads t3 and sp+0", sorted(r for r in an.analyze(0x1030).reads if 4 <= r <= 11) == [11] and sorted(an.analyze(0x1030).stack) == [0]),
        ("passthru inherits sceNine's reads", sorted(r for r in an.analyze(0x1068).reads if 4 <= r <= 11) == [11] and sorted(an.analyze(0x1068).stack) == [0]),
        ("wrap reads t0,t1 but not t2", sorted(r for r in an.analyze(0x1054).reads if r >= 8) == [8, 9]),
        ("ldl/ldr pair is not a read of rt", 5 not in decode(0x69050007, 0).ug),
        ("sysw records syscall 0x3c", an.analyze(0x1098).syscalls == [(0x109c, 0x3c)]),
        ("vprint is variadic", an.analyze(0x1070).varargs),
        ("NOT_APPLIED is an ERROR", ("NOT_APPLIED", 0x2000) in errs),
        ("SetupThread resolves to syscall", rt.resolve("SetupThread")[0] == "ps2_syscalls"),
        ("sceShadow is shadowed", rt.resolve("sceShadow")[3] == "sceShadow"),
        ("underscore added: resolve_in('x', {'_x'})", resolve_in("x", {"_x"}) == "_x"),
        ("summary line", ck.summary().startswith("[bindcheck] ")),
    ]
    # typed handlers (ps2_stub_abi.h): the declared signature is the handler's reads
    trt = Runtime(["SysTyped"], ["sceTypedFive"], [("t/Typed.cpp", SELFTEST_TYPED_SRC, "stub")])
    tf = trt.handler("ps2_stubs", "sceTypedFive")
    th = trt.reads(tf) if tf else {}
    checks += [
        ("typed handler found by name", tf is not None),
        ("typed: declared sig, 5 args, reads t0", th.get("sig_source") == "declared" and th.get("nargs") == 5 and
         8 in th.get("regs", {})),
        ("typed: float args from the declaration", th.get("fregs") == {12}),
        ("typed: legacy form's reads kept", th.get("legacy_reads") == [4, "sp+0x10"]),
        ("typed: declared deviation read", th.get("deviation") == "a test deviation (SDK: short e)"),
    ]
    for label, cond in checks:
        ok &= bool(cond)
        print("%-40s %s" % (label, "ok" if cond else "FAIL"))
    text = ck.report("selftest")
    ok &= "== ERROR" in text and "== Bindings" in text
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("config", nargs="?")
    ap.add_argument("--ps2recomp", default=os.path.join(ROOT, "third_party", "PS2Recomp"))
    ap.add_argument("--analyzer", help="the analyzer's toml (source labels and its [patches])")
    ap.add_argument("--ghidra", help="the Ghidra export toml (source labels)")
    ap.add_argument("--manual", help="hand-made bindings, name@addr per line (source labels)")
    ap.add_argument("--output", help="the recompiler's output folder (which wrappers it emitted)")
    ap.add_argument("--report", help="write the report here (default: stdout)")
    ap.add_argument("--json", help="write findings and per-binding rows as JSON")
    ap.add_argument("--log", help="append the summary line to this file")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any ERROR")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.config:
        ap.error("config required")
    cfg = tomllib.load(open(a.config, "rb"))
    gen = cfg.get("general", {})
    image = Image.from_elf(gen["input"])
    fmap = FuncMap.from_csv(gen["ghidra_output"]) if gen.get("ghidra_output") else FuncMap([])
    rt = Runtime.from_root(a.ps2recomp)
    dbpath = os.path.join(a.ps2recomp, "ps2xAnalyzer", "include", "ps2recomp", "sce_symbol_database_data.h")
    db = SymDb.from_header(dbpath) if os.path.exists(dbpath) else None
    stubs = gen.get("stubs", [])
    sources = {}
    for label, path, loader in (("analyzer", a.analyzer, toml_stubs), ("ghidra", a.ghidra, toml_stubs),
                                ("manual", a.manual, read_list_file)):
        if path:
            for sel in loader(path):
                n, ad = parse_selector(sel)
                sources.setdefault((n, ad), set()).add(label)
    # normalise source keys to the bound name (Ghidra drops underscores, etc.): by address
    by_addr_src = {}
    for (n, ad), labs in sources.items():
        if ad is not None:
            by_addr_src.setdefault(ad, set()).update(labs)
    src_by_sel = {}
    for sel in stubs:
        n, ad = parse_selector(sel)
        src_by_sel[(n, ad)] = by_addr_src.get(ad, set())
    patches = {}
    for p in (cfg.get("patches", {}) or {}).get("instructions", []):
        patches[("config", int(str(p["address"]), 0))] = p["value"]
    if a.analyzer:
        an = tomllib.load(open(a.analyzer, "rb"))
        for p in (an.get("patches", {}) or {}).get("instructions", []):
            patches[("analyzer", int(str(p["address"]), 0))] = p["value"]
    names = {}
    for sel in gen.get("entry_points", []):
        n, ad = parse_selector(sel)
        if ad is not None:
            names[ad] = n
    outb = load_output_bindings(a.output) if a.output else None
    ck = Checker(image, fmap, rt, db, stubs, sources=src_by_sel, patches=patches, output_bindings=outb, names=names).run()
    for r in ck.rows:
        r["sources"] = sorted(src_by_sel.get((r["name"], int(r["address"], 16)), set()))
    text = ck.report(os.path.abspath(a.config))
    if a.report:
        open(a.report, "w", encoding="utf-8", newline="\n").write(text)
    else:
        sys.stdout.write(text)
    if a.json:
        json.dump({"summary": ck.summary(), "findings": [f.as_dict() for f in ck.findings], "bindings": ck.rows},
                  open(a.json, "w", encoding="utf-8"), indent=1)
    line = ck.summary()
    if a.report:
        line += " -> " + a.report
    print(line)
    if a.log:
        with open(a.log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 1 if (a.strict and ck.counts()["ERROR"]) else 0


if __name__ == "__main__":
    sys.exit(main())
