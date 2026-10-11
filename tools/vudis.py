"""vudis - disassembler for PS2 VU micro code (upper + lower instruction pairs).

    python tools/vudis.py <code.bin> [--start 0x0] [--count N] [--base 0]
    python tools/vudis.py --selftest

<code.bin> is raw VU micro memory (VU1: 16 KiB, e.g. cut from a runner snapshot's MEMR block or
a game's MPG payload). Addresses are byte offsets into it (VU PC = byte address / 8 per pair);
each line: byte address, lower word, upper word, then "upper | lower". Branch targets are
printed as byte addresses. Encodings: Sony "VU User's Manual" (instruction reference).
"""
import argparse
import struct
import sys

XYZW = 'xyzw'
BC = 'xyzw'


def dest(w):
    d = (w >> 21) & 15
    return ''.join(c for i, c in enumerate(XYZW) if d & (8 >> i))


def ftfs(w):
    return (w >> 16) & 31, (w >> 11) & 31, (w >> 6) & 31


UPPER = {}
for i, n in enumerate(['ADD', 'SUB', 'MADD', 'MSUB', 'MAX', 'MINI', 'MUL']):
    for b in range(4):
        UPPER[i * 4 + b] = (n + BC[b], 'bc')
UPPER.update({0x1C: ('MULq', 'q'), 0x1D: ('MAXi', 'i'), 0x1E: ('MULi', 'i'), 0x1F: ('MINIi', 'i'),
              0x20: ('ADDq', 'q'), 0x21: ('MADDq', 'q'), 0x22: ('ADDi', 'i'), 0x23: ('MADDi', 'i'),
              0x24: ('SUBq', 'q'), 0x25: ('MSUBq', 'q'), 0x26: ('SUBi', 'i'), 0x27: ('MSUBi', 'i'),
              0x28: ('ADD', '3'), 0x29: ('MADD', '3'), 0x2A: ('MUL', '3'), 0x2B: ('MAX', '3'),
              0x2C: ('SUB', '3'), 0x2D: ('MSUB', '3'), 0x2E: ('OPMSUB', '3'), 0x2F: ('MINI', '3')})
USPECIAL = {}
for i, n in enumerate(['ADDA', 'SUBA', 'MADDA', 'MSUBA']):
    for b in range(4):
        USPECIAL[i * 4 + b] = (n + BC[b], 'abc')
for b in range(4):
    USPECIAL[0x18 + b] = ('MULA' + BC[b], 'abc')
for i, n in enumerate(['ITOF0', 'ITOF4', 'ITOF12', 'ITOF15', 'FTOI0', 'FTOI4', 'FTOI12', 'FTOI15']):
    USPECIAL[0x10 + i] = (n, 'cvt')
USPECIAL.update({0x1C: ('MULAq', 'aq'), 0x1D: ('ABS', 'cvt'), 0x1E: ('MULAi', 'aq'), 0x1F: ('CLIPw', 'clip'),
                 0x20: ('ADDAq', 'aq'), 0x21: ('MADDAq', 'aq'), 0x22: ('ADDAi', 'aq'), 0x23: ('MADDAi', 'aq'),
                 0x24: ('SUBAq', 'aq'), 0x25: ('MSUBAq', 'aq'), 0x26: ('SUBAi', 'aq'), 0x27: ('MSUBAi', 'aq'),
                 0x28: ('ADDA', 'a3'), 0x29: ('MADDA', 'a3'), 0x2A: ('MULA', 'a3'), 0x2C: ('SUBA', 'a3'),
                 0x2D: ('MSUBA', 'a3'), 0x2E: ('OPMULA', 'a3'), 0x2F: ('NOP', 'none')})


def upper(w):
    flags = ''.join(f for f, bit in (('[I]', 31), ('[E]', 30), ('[M]', 29), ('[D]', 28), ('[T]', 27)) if w >> bit & 1)
    fn = w & 63
    ft, fs, fd = ftfs(w)
    d = dest(w)
    if fn >= 0x3C:
        op = ((w >> 6) & 31) << 2 | (fn & 3)
        name, kind = USPECIAL.get(op, ('?u%02x' % op, 'none'))
        if kind == 'abc':
            s = '%s.%s ACC, vf%d, vf%d%s' % (name, d, fs, ft, BC[w & 3])
        elif kind == 'cvt':
            s = '%s.%s vf%d, vf%d' % (name, d, ft, fs)
        elif kind == 'aq':
            s = '%s.%s ACC, vf%d, %s' % (name, d, fs, name[-1].upper())
        elif kind == 'a3':
            s = '%s.%s ACC, vf%d, vf%d' % (name, d, fs, ft)
        elif kind == 'clip':
            s = 'CLIPw.xyz vf%d, vf%dw' % (fs, ft)
        else:
            s = name
    else:
        name, kind = UPPER.get(fn, ('?u%02x' % fn, '3'))
        if kind == 'bc':
            s = '%s.%s vf%d, vf%d, vf%d%s' % (name, d, fd, fs, ft, BC[w & 3])
        elif kind in ('q', 'i'):
            s = '%s.%s vf%d, vf%d, %s' % (name, d, fd, fs, kind.upper())
        else:
            s = '%s.%s vf%d, vf%d, vf%d' % (name, d, fd, fs, ft)
    return s + (' ' + flags if flags else '')


def s11(w):
    v = w & 0x7FF
    return v - 0x800 if v & 0x400 else v


LSPECIAL = {0x30: 'MOVE', 0x31: 'MR32', 0x34: 'LQI', 0x35: 'SQI', 0x36: 'LQD', 0x37: 'SQD', 0x38: 'DIV',
            0x39: 'SQRT', 0x3A: 'RSQRT', 0x3B: 'WAITQ', 0x3C: 'MTIR', 0x3D: 'MFIR', 0x3E: 'ILWR', 0x3F: 'ISWR',
            0x40: 'RNEXT', 0x41: 'RGET', 0x42: 'RINIT', 0x43: 'RXOR', 0x64: 'MFP', 0x68: 'XTOP', 0x69: 'XITOP',
            0x6C: 'XGKICK', 0x70: 'ESADD', 0x71: 'ERSADD', 0x72: 'ELENG', 0x73: 'ERLENG', 0x74: 'EATANxy',
            0x75: 'EATANxz', 0x76: 'ESUM', 0x78: 'ESQRT', 0x79: 'ERSQRT', 0x7A: 'ERCPR', 0x7B: 'WAITP',
            0x7C: 'ESIN', 0x7D: 'EATAN', 0x7E: 'EEXP'}
LOWER = {0x00: 'LQ', 0x01: 'SQ', 0x04: 'ILW', 0x05: 'ISW', 0x08: 'IADDIU', 0x09: 'ISUBIU', 0x10: 'FCEQ',
         0x11: 'FCSET', 0x12: 'FCAND', 0x13: 'FCOR', 0x14: 'FSEQ', 0x15: 'FSSET', 0x16: 'FSAND', 0x17: 'FSOR',
         0x18: 'FMEQ', 0x1A: 'FMAND', 0x1B: 'FMOR', 0x1C: 'FCGET', 0x20: 'B', 0x21: 'BAL', 0x24: 'JR',
         0x25: 'JALR', 0x28: 'IBEQ', 0x29: 'IBNE', 0x2C: 'IBLTZ', 0x2D: 'IBGTZ', 0x2E: 'IBLEZ', 0x2F: 'IBGEZ'}


def lower(w, addr):
    ft, fs, fd = ftfs(w)
    d = dest(w)
    if w >> 25 == 0x40:
        fn = w & 63
        if fn == 0x30:
            return 'IADD vi%d, vi%d, vi%d' % (fd, fs, ft)
        if fn == 0x31:
            return 'ISUB vi%d, vi%d, vi%d' % (fd, fs, ft)
        if fn == 0x32:
            imm = (w >> 6) & 31
            imm = imm - 32 if imm & 16 else imm
            return 'IADDI vi%d, vi%d, %d' % (ft, fs, imm)
        if fn == 0x34:
            return 'IAND vi%d, vi%d, vi%d' % (fd, fs, ft)
        if fn == 0x35:
            return 'IOR vi%d, vi%d, vi%d' % (fd, fs, ft)
        if fn >= 0x3C:
            op = ((w >> 6) & 31) << 2 | (fn & 3)
            n = LSPECIAL.get(op, '?l%02x' % op)
            fsf, ftf = (w >> 21) & 3, (w >> 23) & 3
            if n in ('MOVE', 'MR32'):
                return '%s.%s vf%d, vf%d' % (n, d, ft, fs)
            if n in ('LQI', 'LQD'):
                return '%s.%s vf%d, (vi%d%s)' % (n, d, ft, fs, '++' if n == 'LQI' else '--')
            if n in ('SQI', 'SQD'):
                return '%s.%s vf%d, (vi%d%s)' % (n, d, fs, ft, '++' if n == 'SQI' else '--')
            if n == 'DIV':
                return 'DIV Q, vf%d%s, vf%d%s' % (fs, BC[fsf], ft, BC[ftf])
            if n == 'SQRT':
                return 'SQRT Q, vf%d%s' % (ft, BC[ftf])
            if n == 'RSQRT':
                return 'RSQRT Q, vf%d%s, vf%d%s' % (fs, BC[fsf], ft, BC[ftf])
            if n == 'MTIR':
                return 'MTIR vi%d, vf%d%s' % (ft, fs, BC[fsf])
            if n == 'MFIR':
                return 'MFIR.%s vf%d, vi%d' % (d, ft, fs)
            if n in ('ILWR', 'ISWR'):
                return '%s.%s vi%d, (vi%d)' % (n, d, ft, fs)
            if n in ('XTOP', 'XITOP'):
                return '%s vi%d' % (n, ft)
            if n == 'XGKICK':
                return 'XGKICK vi%d' % fs
            if n == 'MFP':
                return 'MFP.%s vf%d, P' % (d, ft)
            if n in ('ESIN', 'EATAN', 'EEXP', 'ESQRT', 'ERSQRT', 'ERCPR'):
                return '%s P, vf%d%s' % (n, fs, BC[fsf])
            if n in ('ESADD', 'ERSADD', 'ELENG', 'ERLENG', 'EATANxy', 'EATANxz', 'ESUM'):
                return '%s P, vf%d' % (n, fs)
            if n in ('RNEXT', 'RGET'):
                return '%s.%s vf%d, R' % (n, d, ft)
            if n in ('RINIT', 'RXOR'):
                return '%s R, vf%d%s' % (n, fs, BC[fsf])
            return n
        return '?lower %08x' % w
    op = w >> 25
    n = LOWER.get(op, '?l%02x' % op)
    imm = s11(w)
    if n in ('LQ', 'SQ'):
        if n == 'LQ':
            return 'LQ.%s vf%d, %d(vi%d)' % (d, ft, imm, fs)
        return 'SQ.%s vf%d, %d(vi%d)' % (d, fs, imm, ft)
    if n in ('ILW', 'ISW'):
        return '%s.%s vi%d, %d(vi%d)' % (n, d, ft, imm, fs)
    if n in ('IADDIU', 'ISUBIU'):
        imm15 = ((w >> 10) & 0x7800) | (w & 0x7FF)
        return '%s vi%d, vi%d, %d' % (n, ft, fs, imm15)
    tgt = addr + 8 + imm * 8
    if n in ('B',):
        return 'B 0x%x' % tgt
    if n == 'BAL':
        return 'BAL vi%d, 0x%x' % (ft, tgt)
    if n in ('IBEQ', 'IBNE'):
        return '%s vi%d, vi%d, 0x%x' % (n, ft, fs, tgt)
    if n.startswith('IB'):
        return '%s vi%d, 0x%x' % (n, fs, tgt)
    if n == 'JR':
        return 'JR vi%d' % fs
    if n == 'JALR':
        return 'JALR vi%d, vi%d' % (ft, fs)
    if n in ('FCSET',):
        return 'FCSET 0x%x' % (w & 0xFFFFFF)
    if n in ('FCEQ', 'FCAND', 'FCOR'):
        return '%s vi1, 0x%x' % (n, w & 0xFFFFFF)
    if n in ('FSEQ', 'FSAND', 'FSOR', 'FMEQ', 'FMAND', 'FMOR', 'FCGET', 'FSSET'):
        return '%s vi%d, %s' % (n, ft, ('0x%x' % (((w >> 10) & 0x800) | (w & 0x7FF))) if n.startswith('FS') else 'vi%d' % fs)
    return n


def disasm(code, start, count, base=0):
    out = []
    for k in range(count):
        a = start + k * 8
        if a + 8 > len(code):
            break
        lo, up = struct.unpack_from('<II', code, a)
        if up & (1 << 31):  # I bit: lower word is a float immediate
            l = 'LOI %g' % struct.unpack('<f', struct.pack('<I', lo))[0]
        else:
            l = lower(lo, a)
        out.append('%05x: %08x %08x  %-40s | %s' % (a + base, lo, up, upper(up), l))
    return out


def selftest():
    # NOP | NOP (upper 0x000002ff = NOP, lower 0x8000033c = NOP via MOVE.0 vf0,vf0)
    assert upper(0x000002FF) == 'NOP', upper(0x000002FF)
    # MULAx.xyzw ACC, vf1, vf2x : special op 0x18, dest xyzw, ft=2, fs=1
    w = (15 << 21) | (2 << 16) | (1 << 11) | (0x6 << 6) | 0x3C
    assert upper(w) == 'MULAx.xyzw ACC, vf1, vf2x', upper(w)
    # FTOI4.xyzw vf3, vf4
    w = (15 << 21) | (3 << 16) | (4 << 11) | (0x5 << 6) | 0x3D
    assert upper(w) == 'FTOI4.xyzw vf3, vf4', upper(w)
    # SQ.xyzw vf5, 2(vi3)
    w = (0x01 << 25) | (15 << 21) | (3 << 16) | (5 << 11) | 2
    assert lower(w, 0) == 'SQ.xyzw vf5, 2(vi3)', lower(w, 0)
    # XGKICK vi12 : special 0x6C -> bits6-10 = 0x1B, fn = 0x3C
    w = (0x40 << 25) | (12 << 11) | (0x1B << 6) | 0x3C
    assert lower(w, 0) == 'XGKICK vi12', lower(w, 0)
    # IBNE vi1, vi2, back 2 pairs from 0x40 -> 0x40 + 8 - 16 = 0x38
    w = (0x29 << 25) | (1 << 16) | (2 << 11) | (0x7FE)
    assert lower(w, 0x40) == 'IBNE vi1, vi2, 0x38', lower(w, 0x40)
    print('vudis.py self-test passed')


def main():
    if '--selftest' in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument('code')
    ap.add_argument('--start', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--count', type=lambda s: int(s, 0), default=2048)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0)
    a = ap.parse_args()
    code = open(a.code, 'rb').read()
    print('\n'.join(disasm(code, a.start, a.count, a.base)))


if __name__ == '__main__':
    main()
