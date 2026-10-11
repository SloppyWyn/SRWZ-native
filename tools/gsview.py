#!/usr/bin/env python3
"""gsview - render regions of a raw PS2 GS local-memory dump (4 MiB, native swizzle) to PNG.

  python tools/gsview.py DUMP --psm PSM --bp BP --bw BW --w W --h H [--x X --y Y]
         [--cbp CBP --cpsm CPSM --csa CSA --csm CSM] [--alpha] [--scale N] -o out.png
  python tools/gsview.py DUMP --fbp N ...      # framebuffer FBP (2048-word pages), bp = N*32
  python tools/gsview.py DUMP --map            # 64x8 grid of 512 pages: '.' all zero, '#' used
  python tools/gsview.py --selftest

PSM: CT32 CT24 CT16 CT16S T8 T4 T8H T4HL T4HH Z32 Z24 Z16 Z16S (or 0x.. code).
BP/CBP in 256-byte blocks (TBP0/DBP/CBP), BW in 64-px units. Z formats are shown with the
colour interpretation of the matching CT format (only the addressing differs).
Indexed formats go through a CSM1 CLUT read from GS memory at --cbp; without --cbp the raw
index is drawn as grey (4-bit: index*17). 4-bit textures use the 8x2 CLUT at --cbp whatever
--csa (CSA picks the CLUT-buffer slot, not the address); --shared-clut instead reads slot CSA
of a buffer an 8-bit CT32 load from --cbp filled (a 4-bit draw with CLD=0). CT32 alpha 0x80 = opaque; --alpha writes RGBA
(alpha*2 clamped), CT16 alpha is the A bit (0/255).
"""
import argparse
import functools
import sys
from collections import namedtuple

import numpy as np
from PIL import Image

MEM = 4 * 1024 * 1024

# ---- GS tables (see PCSX2 GSTables) ---------------------------------------------------------
BT32 = [[0, 1, 4, 5, 16, 17, 20, 21], [2, 3, 6, 7, 18, 19, 22, 23],
        [8, 9, 12, 13, 24, 25, 28, 29], [10, 11, 14, 15, 26, 27, 30, 31]]
BT16 = [[0, 2, 8, 10], [1, 3, 9, 11], [4, 6, 12, 14], [5, 7, 13, 15],
        [16, 18, 24, 26], [17, 19, 25, 27], [20, 22, 28, 30], [21, 23, 29, 31]]
BT16S = [[0, 2, 16, 18], [1, 3, 17, 19], [8, 10, 24, 26], [9, 11, 25, 27],
         [4, 6, 20, 22], [5, 7, 21, 23], [12, 14, 28, 30], [13, 15, 29, 31]]
BT32Z = [[24, 25, 28, 29, 8, 9, 12, 13], [26, 27, 30, 31, 10, 11, 14, 15],
         [16, 17, 20, 21, 0, 1, 4, 5], [18, 19, 22, 23, 2, 3, 6, 7]]
BT16Z = [[24, 26, 16, 18], [25, 27, 17, 19], [28, 30, 20, 22], [29, 31, 21, 23],
         [8, 10, 0, 2], [9, 11, 1, 3], [12, 14, 4, 6], [13, 15, 5, 7]]
BT16SZ = [[24, 26, 8, 10], [25, 27, 9, 11], [16, 18, 0, 2], [17, 19, 1, 3],
          [28, 30, 12, 14], [29, 31, 13, 15], [20, 22, 4, 6], [21, 23, 5, 7]]


def _stack(rows, step, n):
    """rows + (rows + step) + (rows + 2*step) ... n copies"""
    return [[v + step * k for v in r] for k in range(n) for r in rows]


CT32 = _stack([[0, 1, 4, 5, 8, 9, 12, 13], [2, 3, 6, 7, 10, 11, 14, 15],
               [16, 17, 20, 21, 24, 25, 28, 29], [18, 19, 22, 23, 26, 27, 30, 31]], 32, 2)
CT16 = _stack([[0, 2, 8, 10, 16, 18, 24, 26, 1, 3, 9, 11, 17, 19, 25, 27],
               [4, 6, 12, 14, 20, 22, 28, 30, 5, 7, 13, 15, 21, 23, 29, 31]], 32, 4)
CT8 = _stack([[0, 4, 16, 20, 32, 36, 48, 52, 2, 6, 18, 22, 34, 38, 50, 54],
              [8, 12, 24, 28, 40, 44, 56, 60, 10, 14, 26, 30, 42, 46, 58, 62],
              [33, 37, 49, 53, 1, 5, 17, 21, 35, 39, 51, 55, 3, 7, 19, 23],
              [41, 45, 57, 61, 9, 13, 25, 29, 43, 47, 59, 63, 11, 15, 27, 31],
              [96, 100, 112, 116, 64, 68, 80, 84, 98, 102, 114, 118, 66, 70, 82, 86],
              [104, 108, 120, 124, 72, 76, 88, 92, 106, 110, 122, 126, 74, 78, 90, 94],
              [65, 69, 81, 85, 97, 101, 113, 117, 67, 71, 83, 87, 99, 103, 115, 119],
              [73, 77, 89, 93, 105, 109, 121, 125, 75, 79, 91, 95, 107, 111, 123, 127]], 128, 2)
CT4 = _stack([
    [0, 8, 32, 40, 64, 72, 96, 104, 2, 10, 34, 42, 66, 74, 98, 106, 4, 12, 36, 44, 68, 76, 100, 108, 6, 14, 38, 46, 70, 78, 102, 110],
    [16, 24, 48, 56, 80, 88, 112, 120, 18, 26, 50, 58, 82, 90, 114, 122, 20, 28, 52, 60, 84, 92, 116, 124, 22, 30, 54, 62, 86, 94, 118, 126],
    [65, 73, 97, 105, 1, 9, 33, 41, 67, 75, 99, 107, 3, 11, 35, 43, 69, 77, 101, 109, 5, 13, 37, 45, 71, 79, 103, 111, 7, 15, 39, 47],
    [81, 89, 113, 121, 17, 25, 49, 57, 83, 91, 115, 123, 19, 27, 51, 59, 85, 93, 117, 125, 21, 29, 53, 61, 87, 95, 119, 127, 23, 31, 55, 63],
    [192, 200, 224, 232, 128, 136, 160, 168, 194, 202, 226, 234, 130, 138, 162, 170, 196, 204, 228, 236, 132, 140, 164, 172, 198, 206, 230, 238, 134, 142, 166, 174],
    [208, 216, 240, 248, 144, 152, 176, 184, 210, 218, 242, 250, 146, 154, 178, 186, 212, 220, 244, 252, 148, 156, 180, 188, 214, 222, 246, 254, 150, 158, 182, 190],
    [129, 137, 161, 169, 193, 201, 225, 233, 131, 139, 163, 171, 195, 203, 227, 235, 133, 141, 165, 173, 197, 205, 229, 237, 135, 143, 167, 175, 199, 207, 231, 239],
    [145, 153, 177, 185, 209, 217, 241, 249, 147, 155, 179, 187, 211, 219, 243, 251, 149, 157, 181, 189, 213, 221, 245, 253, 151, 159, 183, 191, 215, 223, 247, 255]], 256, 2)

# name: (code, bits per slot, page w, page h, block w, block h, block table, column table,
#        value kind, index bits (0 = direct colour))
Fmt = namedtuple("Fmt", "name code bits pw ph bw bh btab ctab val ibits")
_F = [
    ("CT32", 0x00, 32, 64, 32, 8, 8, BT32, CT32, "u32", 0),
    ("CT24", 0x01, 32, 64, 32, 8, 8, BT32, CT32, "u24", 0),
    ("CT16", 0x02, 16, 64, 64, 16, 8, BT16, CT16, "u16", 0),
    ("CT16S", 0x0A, 16, 64, 64, 16, 8, BT16S, CT16, "u16", 0),
    ("T8", 0x13, 8, 128, 64, 16, 16, BT32, CT8, "u8", 8),
    ("T4", 0x14, 4, 128, 128, 32, 16, BT16, CT4, "n4", 4),
    ("T8H", 0x1B, 32, 64, 32, 8, 8, BT32, CT32, "b3", 8),
    ("T4HL", 0x24, 32, 64, 32, 8, 8, BT32, CT32, "b3l", 4),
    ("T4HH", 0x2C, 32, 64, 32, 8, 8, BT32, CT32, "b3h", 4),
    ("Z32", 0x30, 32, 64, 32, 8, 8, BT32Z, CT32, "u32", 0),
    ("Z24", 0x31, 32, 64, 32, 8, 8, BT32Z, CT32, "u24", 0),
    ("Z16", 0x32, 16, 64, 64, 16, 8, BT16Z, CT16, "u16", 0),
    ("Z16S", 0x3A, 16, 64, 64, 16, 8, BT16SZ, CT16, "u16", 0),
]
FMT = {f[0]: Fmt(*f) for f in _F}
BY_CODE = {f.code: f.name for f in FMT.values()}
VALBITS = {"u32": 32, "u24": 24, "u16": 16, "u8": 8, "n4": 4, "b3": 8, "b3l": 4, "b3h": 4}


def parse_psm(s):
    if s.upper() in FMT:
        return s.upper()
    try:
        code = int(s, 0)
    except ValueError:
        code = None
    if code in BY_CODE:
        return BY_CODE[code]
    raise SystemExit(f"unknown PSM {s!r}; known: " + " ".join(f"{f.name}(0x{f.code:02X})" for f in FMT.values()))


# ---- address math ---------------------------------------------------------------------------
@functools.cache
def page_tab(name):
    """Per-page table [y%ph, x%pw] -> byte offset in page (nibble offset for 4-bit formats)."""
    f = FMT[name]
    bt, ct = np.array(f.btab, np.int64), np.array(f.ctab, np.int64)
    ly, lx = np.mgrid[0:f.ph, 0:f.pw]
    blk, pix = bt[ly // f.bh, lx // f.bw], ct[ly % f.bh, lx % f.bw]
    return blk * 512 + pix if f.bits == 4 else blk * 256 + pix * (f.bits // 8)


def _ppr(f, bw):
    ppr = (bw * 64) // f.pw
    if ppr == 0:
        print(f"warning: bw={bw} too small for {f.name} (page is {f.pw} px wide); using 1 page per row",
              file=sys.stderr)
        ppr = 1
    return ppr


def addr(name, bp, bw, X, Y):
    """Vectorised: (byte address, nibble) arrays for pixel arrays X, Y."""
    f = FMT[name]
    page = (Y // f.ph) * _ppr(f, bw) + X // f.pw
    off = page_tab(name)[Y % f.ph, X % f.pw]
    base = (bp + page * 32) * 256
    if f.bits == 4:
        slot = base * 2 + off
        return (slot >> 1) & (MEM - 1), slot & 1
    return (base + off) & (MEM - 1), np.zeros_like(off)


def read_at(mem, name, bp, bw, X, Y):
    """Raw pixel values (uint32) at coordinate arrays X, Y."""
    f = FMT[name]
    m8 = np.frombuffer(mem, np.uint8)
    byte, nib = addr(name, bp, bw, np.asarray(X, np.int64), np.asarray(Y, np.int64))
    v = f.val
    if v in ("u32", "u24"):
        raw = m8.view("<u4")[byte >> 2].astype(np.uint32)
        return raw & 0xFFFFFF if v == "u24" else raw
    if v == "u16":
        return m8.view("<u2")[byte >> 1].astype(np.uint32)
    if v == "u8":
        return m8[byte].astype(np.uint32)
    if v == "n4":
        return ((m8[byte] >> (nib * 4)) & 15).astype(np.uint32)
    b3 = m8[(byte + 3) & (MEM - 1)].astype(np.uint32)
    return b3 if v == "b3" else (b3 & 15 if v == "b3l" else b3 >> 4)


def read_raw(mem, name, bp, bw, x, y, w, h):
    X, Y = np.meshgrid(np.arange(x, x + w), np.arange(y, y + h))
    return read_at(mem, name, bp, bw, X, Y)


# ---- colour ---------------------------------------------------------------------------------
def colorize(raw, kind):
    """raw values -> (...,4) uint8 RGBA. kind: CT32 / CT24 / CT16 / CT16S (CT16S same colour layout)."""
    raw = raw.astype(np.uint32)
    if kind in ("CT16", "CT16S"):
        ex = lambda c: (c << 3) | (c >> 2)
        ch = [ex(raw & 31), ex((raw >> 5) & 31), ex((raw >> 10) & 31), ((raw >> 15) & 1) * 255]
    else:
        a = np.minimum(((raw >> 24) & 255) * 2, 255) if kind == "CT32" else np.full(raw.shape, 255, np.uint32)
        ch = [raw & 255, (raw >> 8) & 255, (raw >> 16) & 255, a]
    return np.stack(ch, -1).astype(np.uint8)


def read_clut(mem, cbp, cpsm, csa, ibits, csm, shared=False):
    """CSM1 CLUT palette (N,4) RGBA, as the GS's CLUT buffer holds it when the texture draws.

    A CLUT load (TEX0 with CLD != 0) copies from GS memory at CBP into the buffer at entry
    CSA*16: for an 8-bit texture 256 entries from a 16x16 rectangle (entry e at pixel
    (e'&15, e'>>4), e' = e with bits 3 and 4 swapped), for a 4-bit texture 16 entries from an
    8x2 rectangle (entry i at (i&7, i>>3)). The 4-bit texture then reads the 16 entries at its
    CSA slot, i.e. exactly the 8x2 rectangle at its own CBP: CSA moves the slot, not the
    memory address (default here).
    shared=True: the buffer was filled by an earlier 8-bit load from CBP and this 4-bit texture
    draws without reloading (CLD = 0): its 16 entries are buffer entries CSA*16 .. CSA*16+15
    of that 16x16 rectangle (CT32 only; CSA 0..15)."""
    if csm != 0:
        raise SystemExit("CSM2 CLUTs are not supported (--csm must be 0)")
    if cpsm not in ("CT32", "CT16", "CT16S"):
        raise SystemExit("--cpsm must be CT32, CT16 or CT16S")
    if csa > (15 if cpsm == "CT32" else 31):
        raise SystemExit("--csa out of range (CT32: 0..15, CT16: 0..31)")
    if ibits == 4 and not shared:
        i = np.arange(16)
        return colorize(read_at(mem, cpsm, cbp, 1, i & 7, i >> 3), cpsm)
    if ibits == 4 and (cpsm != "CT32" or csa > 15):
        raise SystemExit("--shared-clut models a CT32 8-bit load (CSA 0..15)")
    e = np.arange(16 if ibits == 4 else 256) + (csa * 16 if ibits == 4 else 0)
    e = (e & ~0x18) | ((e & 8) << 1) | ((e & 0x10) >> 1)
    return colorize(read_at(mem, cpsm, cbp, 1, e & 15, e >> 4), cpsm)


def render(mem, psm, bp, bw, x, y, w, h, clut=None, alpha=False):
    """-> uint8 array (h,w,3) or (h,w,4). clut = dict(cbp, cpsm, csa, csm) or None."""
    f = FMT[psm]
    raw = read_raw(mem, psm, bp, bw, x, y, w, h)
    if f.ibits:
        if clut is None:
            g = (raw * 17 if f.ibits == 4 else raw).astype(np.uint8)
            rgba = np.stack([g, g, g, np.full_like(g, 255)], -1)
        else:
            pal = read_clut(mem, clut["cbp"], clut["cpsm"], clut["csa"], f.ibits, clut["csm"], clut.get("shared", False))
            rgba = pal[raw]
    else:
        rgba = colorize(raw, f.name if f.val == "u16" else {"u32": "CT32", "u24": "CT24"}[f.val])
    return rgba if alpha else rgba[..., :3]


# ---- self-test ------------------------------------------------------------------------------
def saddr(name, bp, bw, x, y):
    """Scalar reference address, straight from the spec: (byte, nibble)."""
    f = FMT[name]
    page = (y // f.ph) * ((bw * 64) // f.pw or 1) + x // f.pw
    blk = f.btab[(y % f.ph) // f.bh][(x % f.pw) // f.bw]
    pix = f.ctab[y % f.bh][x % f.bw]
    block = bp + page * 32 + blk
    if f.bits == 4:
        slot = block * 512 + pix
        return (slot >> 1) % MEM, slot & 1
    return (block * 256 + pix * (f.bits // 8)) % MEM, 0


def swrite(buf, name, bp, bw, x, y, val):
    f = FMT[name]
    b, n = saddr(name, bp, bw, x, y)
    nbytes = {"u32": 4, "u24": 3, "u16": 2, "u8": 1}.get(f.val)
    if nbytes:
        buf[b:b + nbytes] = int(val).to_bytes(nbytes, "little")
    elif f.val == "n4":
        buf[b] = (buf[b] & (0x0F << (4 * (1 - n)))) | (val << (4 * n))
    elif f.val == "b3":
        buf[b + 3] = val
    elif f.val == "b3l":
        buf[b + 3] = (buf[b + 3] & 0xF0) | val
    else:
        buf[b + 3] = (buf[b + 3] & 0x0F) | (val << 4)


def selftest():
    ok = 0

    def check(cond, msg):
        nonlocal ok
        if not cond:
            raise AssertionError("FAIL: " + msg)
        ok += 1

    # bijection over one page (scalar and vectorised tables)
    for f in FMT.values():
        unit = max(f.bits // 8, 1)
        want = list(range(16384)) if f.bits == 4 else list(range(0, 8192, unit))
        got = sorted(page_tab(f.name).ravel().tolist())
        check(got == want, f"{f.name} vectorised page table is not a bijection")
        sl = sorted(saddr(f.name, 0, 64, x, y)[0] * 2 + saddr(f.name, 0, 64, x, y)[1] if f.bits == 4
                    else saddr(f.name, 0, 64, x, y)[0]
                    for y in range(f.ph) for x in range(f.pw))
        check(sl == want, f"{f.name} scalar address is not a bijection")
        X, Y = np.meshgrid(np.arange(f.pw), np.arange(f.ph))
        b, n = addr(f.name, 0, 64, X, Y)
        check(sorted((b * 2 + n if f.bits == 4 else b).ravel().tolist()) == want, f"{f.name} addr() not a bijection")
        check(f.pw * f.ph * f.bits == 8192 * 8, f"{f.name} page size mismatch")
    print("bijection: ok for", " ".join(FMT))

    # hand-computed facts
    check(saddr("CT32", 0, 1, 8, 0) == (256, 0), "CT32 (8,0)")
    check(saddr("CT32", 0, 1, 0, 8) == (512, 0), "CT32 (0,8)")
    check(saddr("T8", 0, 2, 0, 2) == (33, 0), "T8 (0,2)")
    t4 = saddr("T4", 0, 2, 1, 0)
    check(t4 == (4, 0), "T4 (1,0)")
    print(f"CT32 (8,0)  -> byte {saddr('CT32', 0, 1, 8, 0)[0]}   (spec: 256)")
    print(f"CT32 (0,8)  -> byte {saddr('CT32', 0, 1, 0, 8)[0]}   (spec: 512)")
    print(f"T8   (0,2)  -> byte {saddr('T8', 0, 2, 0, 2)[0]}    (spec: 33)")
    print(f"T4   (1,0)  -> byte {t4[0]}, {'high' if t4[1] else 'low'} nibble (pix 8, even -> low)")

    # write (scalar) / read back (vectorised) round trips
    cfgs = [("CT32", 0x8C0, 10, 640, 448), ("T4", 0x1400, 2, 128, 768), ("T8", 0x100, 4, 256, 128)]
    cfgs += [(n, 0x40 + 0x25 * i, 2, 128, 128) for i, n in enumerate(FMT)]
    for name, bp, bw, w, h in cfgs:
        buf = bytearray(MEM)
        mask = (1 << VALBITS[FMT[name].val]) - 1
        exp = np.zeros((h, w), np.uint32)
        for y in range(h):
            for x in range(w):
                v = (((x * 7 + y * 13) * 0x9E3779B1) ^ (x * 31 + y)) & mask
                exp[y, x] = v
                swrite(buf, name, bp, bw, x, y, v)
        got = read_raw(buf, name, bp, bw, 0, 0, w, h)
        check(np.array_equal(got, exp), f"{name} bp={bp:#x} bw={bw} round trip")
        sub = read_raw(buf, name, bp, bw, 5, 3, w - 9, h - 6)
        check(np.array_equal(sub, exp[3:h - 3, 5:w - 4]), f"{name} sub-region")
        print(f"round trip ok: {name:5s} bp={bp:#x} bw={bw} {w}x{h}")

    # CLUT + render path
    buf = bytearray(MEM)
    sw = lambda i: (i & 0xE7) | ((i & 8) << 1) | ((i & 0x10) >> 1)
    for i in range(256):
        swrite(buf, "CT32", 0x200, 1, sw(i) & 15, sw(i) >> 4, i | ((255 - i) << 8) | (((i * 3) & 255) << 16) | (0x40 << 24 if i & 1 else 0x80 << 24))
        swrite(buf, "CT16", 0x210, 1, sw(i) & 15, sw(i) >> 4, (i & 31) | (((i >> 3) & 31) << 5) | (((i * 5) & 31) << 10) | ((i & 1) << 15))
    for y in range(16):
        for x in range(32):
            swrite(buf, "T8", 0x300, 2, x, y, (x * 8 + y) & 255)
            swrite(buf, "T4", 0x310, 2, x, y, (x + y) & 15)
    r = render(buf, "T8", 0x300, 2, 0, 0, 32, 16, dict(cbp=0x200, cpsm="CT32", csa=0, csm=0), alpha=True)
    idx = np.fromfunction(lambda y, x: (x * 8 + y) % 256, (16, 32), dtype=np.int64)
    e = np.stack([idx, 255 - idx, (idx * 3) & 255, np.where(idx & 1, 128, 255)], -1)
    check(np.array_equal(r, e), "T8 + CT32 CLUT render")
    # 4-bit, CLUT loaded by the texture itself: the 8x2 rectangle at CBP whatever the CSA slot
    # (the old csa*16 memory offset drew the wrong palette: a false palette finding, 2026-10-08).
    for csa in (0, 3, 15):
        r = render(buf, "T4", 0x310, 2, 0, 0, 32, 16, dict(cbp=0x200, cpsm="CT32", csa=csa, csm=0))
        idx = np.fromfunction(lambda y, x: (x + y) % 16, (16, 32), dtype=np.int64)
        check(np.array_equal(r, np.stack([idx, 255 - idx, (idx * 3) & 255], -1)), f"T4 + CT32 CLUT csa={csa}")
    # ... and with --shared-clut, the slot of a buffer an 8-bit load filled: entries csa*16+i.
    for csa in (0, 3, 15):
        r = render(buf, "T4", 0x310, 2, 0, 0, 32, 16, dict(cbp=0x200, cpsm="CT32", csa=csa, csm=0, shared=True))
        idx = np.fromfunction(lambda y, x: (x + y) % 16 + csa * 16, (16, 32), dtype=np.int64)
        check(np.array_equal(r, np.stack([idx, 255 - idx, (idx * 3) & 255], -1)), f"T4 + shared CT32 CLUT csa={csa}")
    for csa in (0, 5, 20):
        r = render(buf, "T4", 0x310, 2, 0, 0, 32, 16, dict(cbp=0x210, cpsm="CT16", csa=csa, csm=0), alpha=True)
        idx = np.fromfunction(lambda y, x: (x + y) % 16, (16, 32), dtype=np.int64)
        ex5 = lambda c: (c << 3) | (c >> 2)
        e = np.stack([ex5(idx & 31), ex5((idx >> 3) & 31), ex5((idx * 5) & 31), (idx & 1) * 255], -1)
        check(np.array_equal(r, e), f"T4 + CT16 CLUT csa={csa}")
    # Known-good case built from the GS manual's rule alone, not from read_clut's own code: a
    # 4-bit CT32 CLUT is an 8x2 pixel rectangle, entry i at (i % 8, i // 8); index 8 is the
    # first pixel of the second row, index 15 the last.
    kb = bytearray(MEM)
    for i in range(16):
        swrite(kb, "CT32", 0x400, 1, i % 8, i // 8, 0x80000000 | (0x11 * i))
    for x in range(16):
        swrite(kb, "T4", 0x500, 1, x, 0, x)
    r = render(kb, "T4", 0x500, 1, 0, 0, 16, 1, dict(cbp=0x400, cpsm="CT32", csa=7, csm=0))
    check(r[0, :, 0].tolist() == [0x11 * i for i in range(16)], "known-good: 4-bit CT32 CLUT is the 8x2 rect at CBP")
    g = render(buf, "T4", 0x310, 2, 0, 0, 4, 1)
    check(g[0, :, 0].tolist() == [0, 17, 34, 51], "T4 grey without CLUT")
    print("CLUT/render ok")
    print(f"SELFTEST PASSED ({ok} checks)")


# ---- cli ------------------------------------------------------------------------------------
def load(path):
    data = open(path, "rb").read()
    if len(data) != MEM:
        raise SystemExit(f"{path}: expected {MEM} bytes, got {len(data)}")
    return data


def main():
    ap = argparse.ArgumentParser(description="Render a raw PS2 GS memory dump region to PNG.")
    ap.add_argument("dump", nargs="?")
    ap.add_argument("--psm")
    ap.add_argument("--bp", type=lambda s: int(s, 0))
    ap.add_argument("--fbp", type=lambda s: int(s, 0), help="framebuffer FBP (2048-word pages); bp = fbp*32")
    ap.add_argument("--bw", type=lambda s: int(s, 0))
    ap.add_argument("--w", type=lambda s: int(s, 0))
    ap.add_argument("--h", type=lambda s: int(s, 0))
    ap.add_argument("--x", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--y", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--cbp", type=lambda s: int(s, 0))
    ap.add_argument("--cpsm", default="CT32")
    ap.add_argument("--csa", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--csm", type=int, default=0)
    ap.add_argument("--shared-clut", action="store_true",
                    help="4-bit: read the CSA slot of a CLUT buffer an earlier 8-bit load from CBP filled (CLD=0)")
    ap.add_argument("--alpha", action="store_true")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("-o", "--out")
    ap.add_argument("--map", "--dump-info", dest="map", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if not a.dump:
        ap.error("DUMP required")
    mem = load(a.dump)
    if a.map:
        zero = bytes(8192)
        used = ["." if mem[p * 8192:(p + 1) * 8192] == zero else "#" for p in range(512)]
        print("\n".join("".join(used[r * 64:(r + 1) * 64]) for r in range(8)))
        print(f"{used.count('#')}/512 pages non-zero", file=sys.stderr)
        return
    if a.psm is None or a.bw is None or a.w is None or a.h is None or a.out is None:
        ap.error("--psm, --bw, --w, --h and -o are required")
    if (a.bp is None) == (a.fbp is None):
        ap.error("give exactly one of --bp / --fbp")
    bp = a.bp if a.bp is not None else a.fbp * 32
    psm = parse_psm(a.psm)
    clut = None
    if a.cbp is not None:
        clut = dict(cbp=a.cbp, cpsm=parse_psm(a.cpsm), csa=a.csa, csm=a.csm, shared=a.shared_clut)
    elif FMT[psm].ibits and (a.csa or a.csm):
        print("note: no --cbp, drawing raw index as grey", file=sys.stderr)
    img = Image.fromarray(render(mem, psm, bp, a.bw, a.x, a.y, a.w, a.h, clut, a.alpha))
    if a.scale > 1:
        img = img.resize((img.width * a.scale, img.height * a.scale), Image.NEAREST)
    img.save(a.out)
    print(f"wrote {a.out} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()
