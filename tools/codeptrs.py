"""Finds functions the function map missed that are reached only through code pointers in data
(C++ vtables, callback tables): a guest JALR to one of them hits no recompiled function, and
the runtime reports `[guest-branch:missing-target]` and parks the thread (SRWZ Wall 16: the
protagonist-select screen's vtable slot 0x3eb780).

A candidate is a word in the ELF's loaded image that
  - lies outside every mapped function (data, not an instruction),
  - points into the code range, 4-aligned, at an address that is neither a function start nor
    inside a mapped function (a hole in the map),
  - lands where a function can begin: the target word is not zero, and the code before it
    ends (only zero padding back to the previous mapped function's end, or back to a `jr ra`
    or `j` and its delay slot, as in tables of small thunks).
Pointers into zero padding or into the middle of straight-line code are left out (sizes,
range ends, other data that happens to look like an address). The result is `fn_<addr>@0x<addr>` lines
for config/entries.txt, which tools/make_config.py adds to entry_points; ps2_recomp then
synthesizes a standalone function there, bounded by the next known function.

    python tools/codeptrs.py <elf> <functions.csv> [--code-range 0x100000-0x3ef080] [--out config/entries.txt] [--verbose]
    python tools/codeptrs.py --self-test
    Also code addresses the program builds in registers (lui + addiu/ori: callbacks installed
    at run time, SRWZ 0x264xxx), with the same acceptance rule as data pointers.
    --callers <elf>...: also direct jal/j from other programs' code (the overlays) into holes of
    this map (SRWZ: overlays call the thunk 0x1bdb20 that the boot ELF never calls itself).
    SRWZ: python tools/codeptrs.py local/code/SLPS_258.87 local/recomp/srwz_functions.csv --callers local/code/elf/*.elf --out config/entries.txt

--code-range defaults to the ELF's executable sections when it also has a separate
non-executable allocated section (overlays: .text vs .data), else to the map's first dense
run of functions (from the lowest start until the first hole larger than --max-hole bytes,
default 0x1000; the boot ELF is one section holding code and data).
Overlays: python tools/codeptrs.py local/code/elf/<name>.elf local/recomp/overlays/<name>/<name>_functions.csv
"""
import argparse
import bisect
import csv
import struct
import sys


def load_segments(path):
    e = open(path, 'rb').read()
    if e[:4] != b'\x7fELF':
        raise SystemExit(f'{path}: not an ELF')
    phoff, = struct.unpack_from('<I', e, 0x1c)
    phentsize, phnum = struct.unpack_from('<HH', e, 0x2a)
    segs = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from('<5I', e, phoff + i * phentsize)
        if p_type == 1 and p_filesz:
            segs.append((p_vaddr, e[p_offset:p_offset + p_filesz]))
    return segs


def exec_range(path):
    """(lo, hi) of the executable sections, or None when code and data share one section."""
    e = open(path, 'rb').read()
    shoff, = struct.unpack_from('<I', e, 0x20)
    shentsize, shnum = struct.unpack_from('<HH', e, 0x2e)
    execs, others = [], 0
    for i in range(shnum if shoff else 0):
        _, sh_type, flags, addr, _, size = struct.unpack_from('<6I', e, shoff + i * shentsize)
        if not (flags & 2) or not size:          # SHF_ALLOC
            continue
        if flags & 4:                            # SHF_EXECINSTR
            execs.append((addr, addr + size))
        else:
            others += 1
    if not execs or not others:
        return None
    return min(a for a, _ in execs), max(b for _, b in execs)


def load_functions(path):
    fns = []
    for row in csv.DictReader(open(path, encoding='utf-8')):
        fns.append((int(row['Start'], 16), int(row['End'], 16)))
    fns.sort()
    return fns


def dense_range(fns, max_hole):
    lo = fns[0][0]
    hi = fns[0][1]
    for s, e in fns[1:]:
        if s - hi > max_hole:
            break
        hi = max(hi, e)
    return lo, hi


class Map:
    def __init__(self, fns):
        self.fns = fns
        self.starts = [s for s, _ in fns]
        # running max of ends, so an address after a long function nested over short ones is "inside"
        self.maxend = []
        m = 0
        for _, e in fns:
            m = max(m, e)
            self.maxend.append(m)

    def where(self, addr):
        i = bisect.bisect_right(self.starts, addr) - 1
        if i >= 0 and self.starts[i] == addr:
            return 'start', i
        if i >= 0 and addr < self.maxend[i]:
            return 'inside', i
        return 'hole', i


JR_RA = 0x03e00008


def ends_flow(insn, allow_j=True):
    """jr ra, or an unconditional j (a tail jump): control never falls through past its delay slot."""
    return insn == JR_RA or (allow_j and (insn >> 26) == 2)


def follows_function_end(word, target, prev_end, allow_j=True):
    """True when the code before target ends: only zero padding back to the previous mapped
    function's end, or back to a jr ra / j whose delay slot is the last non-padding word.
    With allow_j=False (inside a mapped function, where a j is often just a loop or an
    if/else) a j still counts when zero padding follows its delay slot (alignment between
    functions: SRWZ 0x2be0d0)."""
    a = target - 4
    while a >= prev_end and word(a) == 0:
        a -= 4
    if a < prev_end:
        return True
    w = word(a)
    if w is None:
        return False
    zeros = (target - a) // 4 - 1          # zero words between the last non-zero word and target
    if ends_flow(w, allow_j or zeros >= 2):  # delay slot was a nop; >= 1 more padding word
        return True
    before = word(a - 4)
    return before is not None and ends_flow(before, allow_j or zeros >= 1)


def scan(segs, fns, code_range, verbose=False):
    m = Map(fns)
    lo, hi = code_range

    def word(addr):
        for base, data in segs:
            if base <= addr and addr + 4 <= base + len(data):
                return struct.unpack_from('<I', data, addr - base)[0]
        return None

    found, rejected = {}, {}
    for base, data in segs:
        for off in range(0, len(data) - 3, 4):
            w, = struct.unpack_from('<I', data, off)
            if not (lo <= w < hi) or w & 3:
                continue
            src = base + off
            if m.where(src)[0] != 'hole':
                continue
            kind, i = m.where(w)
            if kind == 'start':
                continue
            if kind == 'hole':
                prev_end = m.maxend[i] if i >= 0 else lo
                ok = word(w) not in (0, None) and follows_function_end(word, w, prev_end)
            else:
                # Inside a mapped function: a map entry that runs on past its real end over an
                # unmapped function (SRWZ: entry_002bcbe8 covers 0x2bcc60). Only after a jr ra
                # (not a j, common inside functions); ps2_recomp makes it an entry of the owner.
                ok = word(w) not in (0, None) and follows_function_end(word, w, fns[i][0], allow_j=False)
            (found if ok else rejected).setdefault(w, []).append(src)
    if verbose:
        for t in sorted(rejected):
            print(f'# rejected {t:#010x} (zero word, or not after a function end), from {rejected[t][0]:#010x}', file=sys.stderr)
    return found


def plausible_entry(word, m, fns, t, lo):
    """The data-pointer acceptance rule for a target t (see scan)."""
    kind, i = m.where(t)
    if kind == 'start' or not word(t):
        return False
    if kind == 'hole':
        return follows_function_end(word, t, m.maxend[i] if i >= 0 else lo)
    return follows_function_end(word, t, fns[i][0], allow_j=False)


def scan_materialized(segs, fns, code_range, window=10):
    """Code addresses built in registers by the program's own code (lui rt, hi; then addiu/ori
    rs=rt within `window` instructions), e.g. a callback installed in an object at run time.
    Only instructions of mapped functions count; targets follow the data-pointer rule."""
    m = Map(fns)
    lo, hi = code_range

    def word(addr):
        for base, data in segs:
            if base <= addr and addr + 4 <= base + len(data):
                return struct.unpack_from('<I', data, addr - base)[0]
        return None

    found = {}
    for base, data in segs:
        for off in range(0, len(data) - 3, 4):
            pc = base + off
            if not (lo <= pc < hi):
                continue
            w, = struct.unpack_from('<I', data, off)
            if (w >> 26) != 0xf or m.where(pc)[0] == 'hole':
                continue
            rt, upper = (w >> 16) & 31, (w & 0xffff) << 16
            for k in range(1, window + 1):
                w2 = word(pc + 4 * k)
                if w2 is None:
                    break
                op = w2 >> 26
                if op in (9, 0xd) and ((w2 >> 21) & 31) == rt:
                    imm = w2 & 0xffff
                    t = (upper + (((imm ^ 0x8000) - 0x8000) if op == 9 else imm)) & 0xffffffff
                    if lo <= t < hi and not t & 3 and plausible_entry(word, m, fns, t, lo):
                        found.setdefault(t, []).append(pc)
                    break
    return found


def scan_own_calls(segs, fns, code_range):
    """jal from the program's own mapped code to an address that is not a function start (a
    function only one call site reaches, which the map missed: SRWZ 0x2bdbd0)."""
    m = Map(fns)
    lo, hi = code_range
    found = {}
    for base, data in segs:
        for off in range(0, len(data) - 3, 4):
            pc = base + off
            if not (lo <= pc < hi) or m.where(pc)[0] == 'hole':
                continue
            w, = struct.unpack_from('<I', data, off)
            if (w >> 26) != 3:
                continue
            t = ((pc + 4) & 0xf0000000) | ((w & 0x03ffffff) << 2)
            if lo <= t < hi and m.where(t)[0] != 'start':
                found.setdefault(t, []).append(pc)
    return found


def scan_callers(caller_paths, fns, code_range):
    """Direct calls/jumps (jal, j) from other programs' code (overlays) into holes of this map:
    a function only those programs call, e.g. a thunk the boot ELF never calls itself."""
    m = Map(fns)
    lo, hi = code_range
    found = {}
    for path in caller_paths:
        rng = exec_range(path)
        for base, data in load_segments(path):
            for off in range(0, len(data) - 3, 4):
                pc = base + off
                if rng and not (rng[0] <= pc < rng[1]):
                    continue
                w, = struct.unpack_from('<I', data, off)
                if (w >> 26) not in (2, 3):
                    continue
                t = ((pc + 4) & 0xf0000000) | ((w & 0x03ffffff) << 2)
                if lo <= t < hi and m.where(t)[0] == 'hole':
                    found.setdefault(t, []).append(pc)
    return found


def self_test():
    # code 0x1000-0x1100: fn A 0x1000-0x1010; hole 0x1010-0x1040: padding to 0x1020, missing
    # function X at 0x1020 (3 insns + jr ra; nop), missing thunk Y at 0x1038 right after X's
    # delay slot (j; nop); fn B 0x1040-0x1100; data 0x1100-0x1120 holds pointers.
    code = bytearray(0x120)
    def put(a, v): struct.pack_into('<I', code, a - 0x1000, v)
    for a in range(0x1000, 0x1010, 4): put(a, 0x24020001)
    for a in (0x1020, 0x1024, 0x1028): put(a, 0x24020003)
    put(0x102c, JR_RA)             # X returns; delay slot 0x1030 = nop
    put(0x1034, 0x24020004)        # (a stray insn: a pointer to 0x1034 is mid-flow? no: after jr ra's nop)
    put(0x1038, 0x08000400)        # Y: j
    for a in range(0x1040, 0x1100, 4): put(a, 0x24020002)
    put(0x1100, 0x1020)   # vtable slot -> X: found (only padding back to fn A's end)
    put(0x1104, 0x1040)   # -> known start: ignored
    put(0x1108, 0x1028)   # -> middle of X: rejected
    put(0x110c, 0x1010)   # -> padding: rejected (zero word)
    put(0x1110, 0x1004)   # -> inside fn A, not after a jr ra: rejected
    put(0x1114, 0x5000)   # outside the code range: ignored
    put(0x1118, 0x1038)   # -> Y, after 0x1034 which follows no flow end: rejected
    put(0x111c, 0x1034)   # -> 0x1034, right after jr ra + nop delay slot: found
    segs = [(0x1000, bytes(code))]
    fns = [(0x1000, 0x1010), (0x1040, 0x1100)]
    assert dense_range(fns, 0x1000) == (0x1000, 0x1100)
    found = scan(segs, fns, (0x1000, 0x1100))
    assert sorted(found) == [0x1020, 0x1034], found
    assert found[0x1020] == [0x1100]
    # a pointer stored inside a function body (an instruction word) is not a source
    put(0x1044, 0x1020)
    found = scan([(0x1000, bytes(code))], fns, (0x1000, 0x1100))
    assert found[0x1020] == [0x1100], found
    # a target right after a j + delay slot is accepted
    put(0x1034, 0x08000400)
    put(0x1038, 0x24020005)
    put(0x1118, 0x103c)
    put(0x103c, 0x24020006)
    found = scan([(0x1000, bytes(code))], fns, (0x1000, 0x1100))
    assert 0x103c in found and 0x1038 not in found, found
    # a jal from another program's code into the hole (callers) is an entry, wherever it lands
    import os, tempfile
    ov = bytearray(0x10)
    struct.pack_into('<I', ov, 0, (3 << 26) | (0x1028 >> 2))      # jal 0x1028 (mid-hole: still an entry)
    struct.pack_into('<I', ov, 4, (3 << 26) | (0x1040 >> 2))      # jal a known start: ignored
    struct.pack_into('<I', ov, 8, (2 << 26) | (0x1004 >> 2))      # j inside fn A: ignored
    elf = bytearray(0x34 + 0x20)
    elf[:4] = b'ELF'
    struct.pack_into('<I', elf, 0x1c, 0x34)                       # e_phoff
    struct.pack_into('<HH', elf, 0x2a, 0x20, 1)                   # e_phentsize, e_phnum
    struct.pack_into('<5I', elf, 0x34, 1, len(elf), 0x8000, 0x8000, len(ov))
    fd, path = tempfile.mkstemp(suffix='.elf')
    os.write(fd, bytes(elf) + bytes(ov)); os.close(fd)
    try:
        calls = scan_callers([path], fns, (0x1000, 0x1100))
    finally:
        os.remove(path)
    assert calls == {0x1028: [0x8000]}, calls
    # inside a mapped function, right after jr ra + delay slot: found (the map entry runs on)
    put(0x1050, JR_RA); put(0x1054, 0); put(0x1058, 0x24020007)
    put(0x1114, 0x1058)
    found = scan([(0x1000, bytes(code))], fns, (0x1000, 0x1100))
    assert 0x1058 in found and 0x1004 not in found, found
    # ... but not after a j inside a function
    put(0x1050, 0x08000400)
    found = scan([(0x1000, bytes(code))], fns, (0x1000, 0x1100))
    assert 0x1058 not in found, found
    # ... unless zero padding follows the j's delay slot (j; nop; nop | target)
    put(0x1050, 0x08000400); put(0x1054, 0); put(0x1058, 0); put(0x105c, 0x24020008)
    put(0x1114, 0x105c)
    found = scan([(0x1000, bytes(code))], fns, (0x1000, 0x1100))
    assert 0x105c in found, found
    # lui/addiu in a mapped function building the address of a missed function: found
    code2 = bytearray(code)
    def put2(a, v): struct.pack_into('<I', code2, a - 0x1000, v)
    put2(0x1040, (0xf << 26) | (4 << 16) | 0)          # lui a0, 0
    put2(0x1044, (9 << 26) | (4 << 21) | (4 << 16) | 0x1020)   # addiu a0, a0, 0x1020
    put2(0x1048, (0xf << 26) | (5 << 16) | 0)          # lui a1, 0
    put2(0x104c, (0xd << 26) | (5 << 21) | (5 << 16) | 0x1028)  # ori a1, a1, 0x1028: mid-X, rejected
    mat = scan_materialized([(0x1000, bytes(code2))], fns, (0x1000, 0x1100))
    assert sorted(mat) == [0x1020], mat
    put2(0x1050, (3 << 26) | (0x1024 >> 2))           # jal 0x1024 (mid-X) from fn B: an entry
    put2(0x1054, (3 << 26) | (0x1000 >> 2))           # jal fn A's start: ignored
    own = scan_own_calls([(0x1000, bytes(code2))], fns, (0x1000, 0x1100))
    assert own == {0x1024: [0x1050]}, own
    print('codeptrs.py self-test passed')


def overlay_entries(elf_dir, maps_dir):
    """{overlay name: {target: refs}} for every overlay with a map (maps_dir/<name>/<name>_functions.csv)."""
    import os
    out = {}
    for name in sorted(os.listdir(maps_dir)):
        csv_path = os.path.join(maps_dir, name, name + '_functions.csv')
        elf = os.path.join(elf_dir, name + '.elf')
        if not (os.path.isfile(csv_path) and os.path.isfile(elf)):
            continue
        fns = load_functions(csv_path)
        if not fns:
            continue
        segs = load_segments(elf)
        rng = exec_range(elf) or dense_range(fns, 0x1000)
        found = scan(segs, fns, rng)
        for t, srcs in scan_materialized(segs, fns, rng).items():
            found.setdefault(t, []).extend(srcs)
        for t, srcs in scan_own_calls(segs, fns, rng).items():
            found.setdefault(t, []).extend(srcs)
        if found:
            out[name] = found
    return out


def main():
    if '--self-test' in sys.argv:
        return self_test()
    if '--overlays' in sys.argv:
        # python tools/codeptrs.py --overlays local/code/elf local/recomp/overlays --out config/overlay_entries.txt
        ap = argparse.ArgumentParser()
        ap.add_argument('--overlays', nargs=2, metavar=('ELF_DIR', 'MAPS_DIR'))
        ap.add_argument('--out')
        a = ap.parse_args()
        res = overlay_entries(*a.overlays)
        lines = ['# Generated by tools/codeptrs.py --overlays: per overlay, functions its map missed that are',
                 '# reached only through code pointers in its data or built in registers. tools/overlays.py',
                 '# adds them to that overlay\'s entry_points. Format: [overlay] then name@address  # refs', '']
        total = 0
        for name, found in res.items():
            lines.append(f'[{name}]')
            for t in sorted(found):
                lines.append(f'fn_{t:08x}@{t:#010x}  # {len(found[t])} ref(s), first at {found[t][0]:#010x}')
                total += 1
            lines.append('')
        text = '\n'.join(lines)
        if a.out:
            open(a.out, 'w', encoding='utf-8', newline='\n').write(text)
            print(f'{total} entry point(s) in {len(res)} overlay(s) -> {a.out}')
        else:
            sys.stdout.write(text)
        return
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('elf')
    ap.add_argument('functions')
    ap.add_argument('--code-range')
    ap.add_argument('--max-hole', type=lambda s: int(s, 0), default=0x1000)
    ap.add_argument('--out')
    ap.add_argument('--callers', nargs='*', default=[], help='other ELFs (overlays) whose jal/j into this code count as entries')
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()
    segs = load_segments(a.elf)
    fns = load_functions(a.functions)
    if a.code_range:
        lo, hi = (int(x, 0) for x in a.code_range.split('-'))
    else:
        lo, hi = exec_range(a.elf) or dense_range(fns, a.max_hole)
    found = scan(segs, fns, (lo, hi), a.verbose)
    callers = scan_callers(a.callers, fns, (lo, hi))
    for t, srcs in callers.items():
        found.setdefault(t, []).extend(srcs)
    for t, srcs in scan_materialized(segs, fns, (lo, hi)).items():
        found.setdefault(t, []).extend(srcs)
    for t, srcs in scan_own_calls(segs, fns, (lo, hi)).items():
        found.setdefault(t, []).extend(srcs)
    lines = [f'# Generated by tools/codeptrs.py: functions reached only through code pointers in data',
             f'# or calls from other programs ({len(a.callers)} caller ELF(s)), missing from the function map',
             f'# (code range {lo:#x}-{hi:#x}). Format: name@address  # refs',
             '']
    for t in sorted(found):
        refs = found[t]
        lines.append(f'fn_{t:08x}@{t:#010x}  # {len(refs)} ref(s), first at {refs[0]:#010x}')
    text = '\n'.join(lines) + '\n'
    if a.out:
        open(a.out, 'w', encoding='utf-8', newline='\n').write(text)
        print(f'{len(found)} entry point(s) -> {a.out} (code range {lo:#x}-{hi:#x})')
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()
