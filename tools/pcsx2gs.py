#!/usr/bin/env python3
"""pcsx2gs - read a PCSX2 GS dump (.gs, uncompressed; set GSDumpCompression = 0) and decode its
GIF stream into GS register writes.

  python tools/pcsx2gs.py DUMP.gs --info              # header, packets, frames
  python tools/pcsx2gs.py DUMP.gs --vram out.bin       # the 4 MiB GS memory at dump start
                                                       # (native layout, for tools/gsview.py)
  python tools/pcsx2gs.py DUMP.gs --prims [--frame N]  # every primitive of frame N (default 0)
  python tools/pcsx2gs.py --selftest

Format (PCSX2 2.x, GSDumpFile/GSState::Freeze, state version 9):
  u32 0xFFFFFFFF, u32 header_size, header (9 x u32: state_version, state_size, serial_offset,
  serial_size, crc, shot_w, shot_h, shot_offset, shot_size; offsets relative to the header),
  then the state (state_size bytes: u32 version, 15 x u64 env registers, 2 x 12 u64 context
  registers, 6 x u64 vertex registers, u32 tr.x, tr.y, 4 MiB VRAM, GIF path state, f32 q),
  8192 bytes of privileged registers, then packets: u8 type; 0 = transfer (u8 path, u32 size,
  data), 1 = vsync (u8 field), 2 = read FIFO2 (u32 size), 3 = privileged registers (8192).
A frame is the packets between two vsyncs.
"""
import argparse
import struct
import sys
from collections import namedtuple

MEM = 4 * 1024 * 1024
STATE_ENV = ["PRIM", "PRMODECONT", "TEXCLUT", "SCANMSK", "TEXA", "FOGCOL", "DIMX", "DTHE", "COLCLAMP",
             "PABE", "BITBLTBUF", "TRXDIR", "TRXPOS", "TRXREG", "TRXREG_OBSOLETE"]
STATE_CTX = ["XYOFFSET", "TEX0", "TEX1", "CLAMP", "MIPTBP1", "MIPTBP2", "SCISSOR", "ALPHA", "TEST",
             "FBA", "FRAME", "ZBUF"]
# GS register addresses (A+D) for the state registers.
REG_ADDR = {"PRIM": 0x00, "PRMODECONT": 0x1A, "TEXCLUT": 0x1C, "SCANMSK": 0x22, "TEXA": 0x3B, "FOGCOL": 0x3D,
            "DIMX": 0x44, "DTHE": 0x45, "COLCLAMP": 0x46, "PABE": 0x49,
            "XYOFFSET": (0x18, 0x19), "TEX0": (0x06, 0x07), "TEX1": (0x14, 0x15), "CLAMP": (0x08, 0x09),
            "MIPTBP1": (0x34, 0x35), "MIPTBP2": (0x36, 0x37), "SCISSOR": (0x40, 0x41), "ALPHA": (0x42, 0x43),
            "TEST": (0x47, 0x48), "FBA": (0x4A, 0x4B), "FRAME": (0x4C, 0x4D), "ZBUF": (0x4E, 0x4F)}

Dump = namedtuple("Dump", "serial shot_w shot_h shot state_regs ctx_regs vram priv packets")


def load(path):
    d = open(path, "rb").read()
    crc, = struct.unpack_from("<I", d, 0)
    if crc != 0xFFFFFFFF:
        raise SystemExit("old-style GS dump (no header) not supported")
    hsize, = struct.unpack_from("<I", d, 4)
    h = struct.unpack_from("<9I", d, 8)
    ver, ssize, soff, slen, _crc, sw, sh, shoff, shlen = h
    if ver != 9:
        print(f"warning: state version {ver}, parser written for 9", file=sys.stderr)
    hb = d[8:8 + hsize]
    serial = hb[soff:soff + slen].decode("ascii", "replace")
    shot = hb[shoff:shoff + shlen]
    p = 8 + hsize
    st = d[p:p + ssize]
    p += ssize
    o = 4
    env = {}
    for n in STATE_ENV:
        env[n], = struct.unpack_from("<Q", st, o)
        o += 8
    ctx = [{}, {}]
    for i in range(2):
        for n in STATE_CTX:
            ctx[i][n], = struct.unpack_from("<Q", st, o)
            o += 8
    o += 6 * 8 + 8  # RGBAQ ST UV FOG XYZ obsolete, tr.x tr.y
    # The VRAM sits right before the GIF path state (4 x (16-byte tag + u32 reg)) and q (f32).
    vstart = ssize - 84 - MEM
    if vstart != o + 1 and vstart < o:
        print(f"warning: unexpected state layout (vram at {vstart}, registers end at {o})", file=sys.stderr)
    vram = st[vstart:vstart + MEM]
    priv = d[p:p + 8192]
    p += 8192
    packets = []
    while p < len(d):
        t = d[p]
        p += 1
        if t == 0:
            path = d[p]
            size, = struct.unpack_from("<I", d, p + 1)
            packets.append(("xfer", path, d[p + 5:p + 5 + size]))
            p += 5 + size
        elif t == 1:
            packets.append(("vsync", d[p], None))
            p += 1
        elif t == 2:
            size, = struct.unpack_from("<I", d, p)
            packets.append(("fifo", size, None))
            p += 4
        elif t == 3:
            packets.append(("regs", 0, d[p:p + 8192]))
            p += 8192
        else:
            raise SystemExit(f"bad packet type {t} at {p - 1}")
    return Dump(serial, sw, sh, shot, env, ctx, vram, priv, packets)


def frames(dump):
    """List of frames, each a list of transfer payloads (path, bytes) up to the next vsync."""
    out, cur = [], []
    for kind, a, b in dump.packets:
        if kind == "xfer":
            cur.append((a, b))
        elif kind == "vsync":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def initial_writes(dump):
    """(addr, value) GS register writes that restore the dump's starting drawing state."""
    w = []
    for n in ("PRMODECONT", "TEXCLUT", "SCANMSK", "TEXA", "FOGCOL", "DIMX", "DTHE", "COLCLAMP", "PABE"):
        w.append((REG_ADDR[n], dump.state_regs[n]))
    for i in range(2):
        for n in STATE_CTX:
            v = dump.ctx_regs[i][n]
            if n == "TEX0":
                v &= ~(7 << 61)  # CLD = 0: do not reload the CLUT
            w.append((REG_ADDR[n][i], v))
    w.append((REG_ADDR["PRIM"], dump.state_regs["PRIM"]))
    return w


# ---- GIF decoding ---------------------------------------------------------------------------
def gif_writes(data):
    """Decode GIF packets into a list of events:
      ("reg", addr, value64)          A+D / REGLIST / PACKED register writes (packed forms expanded
                                       to their register, see below)
      ("packed", desc, lo, hi)        PACKED writes of RGBAQ/ST/UV/XYZF2/XYZ2/FOG (need packed
                                       semantics: Q latch, ADC)
      ("tag",)                        start of a GIF tag (Q := 1.0)
      ("image", bytes)                IMAGE data (HWREG)
    """
    ev = []
    p = 0
    n = len(data)
    while p + 16 <= n:
        lo, hi = struct.unpack_from("<QQ", data, p)
        p += 16
        nloop = lo & 0x7FFF
        pre = (lo >> 46) & 1
        prim = (lo >> 47) & 0x7FF
        flg = (lo >> 58) & 3
        nreg = (lo >> 60) & 15 or 16
        regs = [(hi >> (4 * i)) & 15 for i in range(nreg)]
        ev.append(("tag",))
        if flg == 0:
            if pre:
                ev.append(("reg", 0x00, prim))
            for _ in range(nloop):
                for r in regs:
                    if p + 16 > n:
                        return ev
                    a, b = struct.unpack_from("<QQ", data, p)
                    p += 16
                    if r == 0xE:
                        ev.append(("reg", b & 0xFF, a))
                    elif r in (0x1, 0x2, 0x3, 0x4, 0x5, 0xA):
                        ev.append(("packed", r, a, b))
                    elif r == 0xF:
                        pass
                    else:
                        ev.append(("reg", r, a))
        elif flg == 1:
            total = nloop * nreg
            for k in range(total):
                if p + 8 > n:
                    return ev
                a, = struct.unpack_from("<Q", data, p)
                p += 8
                r = regs[k % nreg]
                if r not in (0xE, 0xF):
                    ev.append(("reg", r, a))
            if total & 1:
                p += 8
        else:
            size = nloop * 16
            ev.append(("image", data[p:p + size]))
            p += size
    return ev


PRIM_NAMES = ["point", "line", "linestrip", "tri", "tristrip", "trifan", "sprite", "?"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--vram")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.dump:
        ap.error("dump required")
    d = load(a.dump)
    if a.info:
        fr = frames(d)
        print(f"serial {d.serial}, screenshot {d.shot_w}x{d.shot_h}, {len(d.packets)} packets, {len(fr)} frames")
        for i, f in enumerate(fr):
            print(f"  frame {i}: {len(f)} transfers, {sum(len(b) for _, b in f)} bytes, paths {sorted(set(p for p, _ in f))}")
        for n, v in d.state_regs.items():
            print(f"  {n:16s} 0x{v:016x}")
        for i in range(2):
            for n, v in d.ctx_regs[i].items():
                print(f"  {n}_{i + 1:<12d} 0x{v:016x}")
    if a.vram:
        open(a.vram, "wb").write(d.vram)
        print(f"wrote {a.vram}")


def selftest():
    # A PACKED tag with A+D and XYZ2, then a REGLIST tag, then IMAGE.
    tag = lambda nloop, flg, nreg, regs, eop=1: struct.pack("<QQ", nloop | (eop << 15) | (flg << 58) | (nreg << 60), regs)
    data = tag(1, 0, 2, 0x5E, 0) + struct.pack("<QQ", 0x1234, 0x4C) + struct.pack("<QQ", (16 << 32) | 32, 7)
    data += tag(1, 1, 3, 0x0E01, 0) + struct.pack("<QQ", 0xAA, 0xBB) + struct.pack("<Q", 0xCC) + bytes(8)
    data += tag(2, 2, 1, 0) + bytes(range(32))
    ev = gif_writes(data)
    exp = [("tag",), ("reg", 0x4C, 0x1234), ("packed", 5, (16 << 32) | 32, 7),
           ("tag",), ("reg", 1, 0xAA), ("reg", 0, 0xBB),
           ("tag",), ("image", bytes(range(32)))]
    ok = ev == exp
    print("selftest", "PASS" if ok else f"FAIL {ev}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
