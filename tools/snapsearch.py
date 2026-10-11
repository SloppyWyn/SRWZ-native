#!/usr/bin/env python3
"""Cheat-Engine-style value search across runner snapshots: which EE RAM words had the given values
(or changed / stayed the same) from one snapshot to the next.

    python tools/snapsearch.py a.bin=6900 b.bin=6551 [c.bin=same|changed|any|<value>] [--width 2]
                               [--range 0x500000-0x800000] [--elf local/code/SLPS_258.87] [--limit 50]
    python tools/snapsearch.py --dump a.bin 0x6cf440 [--count 64]
    python tools/snapsearch.py --selftest

Each condition is <snapshot>=<rule>: a number (decimal or 0x hex) the word must hold, "same" (equal to
the previous snapshot's word), "changed" (different), "any". Output: one line per hit with the
address and the word in every snapshot. --width 1, 2 or 4 bytes (little endian, aligned).

RDRAM is found inside the snapshot by matching the ELF's first loadable segment (the ELF that wrote
the snapshot), so the tool does not depend on the snapshot's section layout.
"""
import argparse
import os
import struct
import sys

RAM = 0x2000000


def elf_probe(elf_path):
    elf = open(elf_path, "rb").read()
    phoff = struct.unpack_from("<I", elf, 0x1C)[0]
    phnum = struct.unpack_from("<H", elf, 0x2C)[0]
    for k in range(phnum):
        t, off, va, _pa, fs, _ms, _fl, _al = struct.unpack_from("<8I", elf, phoff + k * 32)
        if t == 1 and fs >= 0x400:
            return va + 0x200, elf[off + 0x200:off + 0x240]
    raise SystemExit("no loadable segment in " + elf_path)


def load_ram(path, probe):
    data = open(path, "rb").read()
    va, chunk = probe
    i = data.find(chunk)
    if i < 0:
        raise SystemExit(f"{path}: ELF code not found (another game or build?)")
    base = i - va
    if base < 0 or base + RAM > len(data):
        raise SystemExit(f"{path}: RDRAM does not fit at file offset {base:#x}")
    return memoryview(data)[base:base + RAM]


def parse_rule(text):
    t = text.strip().lower()
    if t in ("same", "changed", "any"):
        return t
    return int(t, 0)


def search(rams, rules, width, lo, hi, limit):
    fmt = {1: "<B", 2: "<H", 4: "<I"}[width]
    first = rules[0]
    if not isinstance(first, int):
        raise SystemExit("the first condition must be a value")
    needle = struct.pack(fmt, first)
    ram0 = bytes(rams[0][lo:hi])
    hits = []
    i = ram0.find(needle)
    while i != -1:
        addr = lo + i
        if addr % width == 0:
            values = [struct.unpack_from(fmt, r, addr)[0] for r in rams]
            ok = True
            for k in range(1, len(rules)):
                rule = rules[k]
                if rule == "any":
                    continue
                if rule == "same":
                    ok = values[k] == values[k - 1]
                elif rule == "changed":
                    ok = values[k] != values[k - 1]
                else:
                    ok = values[k] == rule
                if not ok:
                    break
            if ok:
                hits.append((addr, values))
                if len(hits) >= limit:
                    break
        i = ram0.find(needle, i + 1)
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("conditions", nargs="*")
    ap.add_argument("--width", type=int, default=2, choices=(1, 2, 4))
    ap.add_argument("--range", default="0x100000-0x2000000")
    ap.add_argument("--elf", default=os.path.join("local", "code", "SLPS_258.87"))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dump", nargs=2, metavar=("SNAPSHOT", "ADDR"))
    ap.add_argument("--count", type=int, default=64)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    probe = elf_probe(a.elf)
    if a.dump:
        ram = load_ram(a.dump[0], probe)
        addr = int(a.dump[1], 0) & ~0xF
        for row in range(0, a.count, 16):
            words = struct.unpack_from("<8H", ram, addr + row)
            print("%08x: %s" % (addr + row, " ".join("%04x" % w for w in words)))
        return 0
    if len(a.conditions) < 1:
        ap.error("give at least one <snapshot>=<value>")
    paths, rules = [], []
    for c in a.conditions:
        p, _, r = c.rpartition("=")
        paths.append(p)
        rules.append(parse_rule(r))
    lo, hi = (int(x, 0) for x in a.range.split("-"))
    rams = [load_ram(p, probe) for p in paths]
    hits = search(rams, rules, a.width, lo, hi, a.limit)
    for addr, values in hits:
        print("%08x  %s" % (addr, "  ".join(str(v) for v in values)))
    print(f"{len(hits)} hit(s){' (limit reached)' if len(hits) >= a.limit else ''}")
    return 0


def selftest():
    import tempfile
    tmp = tempfile.mkdtemp()
    import random
    random.seed(7)
    code = bytes(random.randrange(256) for _ in range(1024))
    elf = bytearray(0x1000 + len(code))
    struct.pack_into("<I", elf, 0x1C, 0x40)
    struct.pack_into("<H", elf, 0x2C, 1)
    struct.pack_into("<8I", elf, 0x40, 1, 0x1000, 0x100000, 0x100000, len(code), len(code), 5, 0x10)
    elf[0x1000:] = code
    ep = os.path.join(tmp, "x.elf")
    open(ep, "wb").write(elf)

    def snap(name, hp, mv):
        ram = bytearray(RAM)
        ram[0x100000:0x100000 + len(code)] = code
        struct.pack_into("<H", ram, 0x600010, hp)
        struct.pack_into("<H", ram, 0x700020, 6900)  # a decoy that does not change
        struct.pack_into("<H", ram, 0x600014, mv)
        p = os.path.join(tmp, name)
        open(p, "wb").write(b"HEADER" * 7 + bytes(ram) + b"TAIL")
        return p
    a = snap("a.bin", 6900, 5)
    b = snap("b.bin", 6551, 5)
    probe = elf_probe(ep)
    rams = [load_ram(p, probe) for p in (a, b)]
    hits = search(rams, [6900, 6551], 2, 0x100000, RAM, 10)
    ok = [h[0] for h in hits] == [0x600010]
    hits2 = search(rams, [5, "same"], 2, 0x600000, 0x600100, 10)
    ok = ok and [h[0] for h in hits2] == [0x600014]
    print("SELFTEST " + ("PASSED" if ok else "FAILED %r %r" % (hits, hits2)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
