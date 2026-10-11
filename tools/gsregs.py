#!/usr/bin/env python3
"""List the GS register writes in a PCSX2 GS dump (.gs, uncompressed), in stream order, with the
GIF path each came from. Ground truth for "which register did the game write when" (Wall 19).

    python tools/gsregs.py <dump.gs> [--regs PRIM,PRMODE,PRMODECONT,TEX0_1] [--frames N]
                                [--summary] [--limit N]
    python tools/gsregs.py --selftest

Output lines: <packet#> P<path> <REG> 0x<value> [decoded]; "VSYNC" lines between frames.
--summary collapses runs of the same line ("x N"). Only uncompressed dumps
([EmuCore/GS] GSDumpCompression = 0); a .gs.zst/.gs.xz must be decompressed first.
"""
import argparse
import struct
import sys

REG_NAMES = {
    0x00: "PRIM", 0x01: "RGBAQ", 0x02: "ST", 0x03: "UV", 0x04: "XYZF2", 0x05: "XYZ2",
    0x06: "TEX0_1", 0x07: "TEX0_2", 0x08: "CLAMP_1", 0x09: "CLAMP_2", 0x0A: "FOG",
    0x0C: "XYZF3", 0x0D: "XYZ3", 0x14: "TEX1_1", 0x15: "TEX1_2", 0x16: "TEX2_1",
    0x17: "TEX2_2", 0x18: "XYOFFSET_1", 0x19: "XYOFFSET_2", 0x1A: "PRMODECONT",
    0x1B: "PRMODE", 0x1C: "TEXCLUT", 0x22: "SCANMSK", 0x34: "MIPTBP1_1", 0x35: "MIPTBP1_2",
    0x36: "MIPTBP2_1", 0x37: "MIPTBP2_2", 0x3B: "TEXA", 0x3D: "FOGCOL", 0x3F: "TEXFLUSH",
    0x40: "SCISSOR_1", 0x41: "SCISSOR_2", 0x42: "ALPHA_1", 0x43: "ALPHA_2", 0x44: "DIMX",
    0x45: "DTHE", 0x46: "COLCLAMP", 0x47: "TEST_1", 0x48: "TEST_2", 0x49: "PABE",
    0x4A: "FBA_1", 0x4B: "FBA_2", 0x4C: "FRAME_1", 0x4D: "FRAME_2", 0x4E: "ZBUF_1",
    0x4F: "ZBUF_2", 0x50: "BITBLTBUF", 0x51: "TRXPOS", 0x52: "TRXREG", 0x53: "TRXDIR",
    0x54: "HWREG", 0x60: "SIGNAL", 0x61: "FINISH", 0x62: "LABEL",
}
PATHS = {0: "1", 1: "2", 2: "3", 3: "1", 4: "D"}  # GSTransferPath: Path1Old, Path2, Path3, Path1New, Dummy


def prim_text(v):
    return ("type=%d iip=%d tme=%d fge=%d abe=%d aa1=%d fst=%d ctxt=%d fix=%d" %
            (v & 7, v >> 3 & 1, v >> 4 & 1, v >> 5 & 1, v >> 6 & 1, v >> 7 & 1, v >> 8 & 1, v >> 9 & 1, v >> 10 & 1))


def decode_gif(data, emit):
    """Walk GIF packets in `data`, calling emit(regname, value) for each register write."""
    off = 0
    n = len(data)
    while off + 16 <= n:
        lo, hi = struct.unpack_from("<QQ", data, off)
        off += 16
        nloop = lo & 0x7FFF
        pre = (lo >> 46) & 1
        prim = (lo >> 47) & 0x7FF
        flg = (lo >> 58) & 3
        nreg = (lo >> 60) & 0xF or 16
        regs = [(hi >> (4 * i)) & 0xF for i in range(nreg)]
        if pre and flg != 2:
            emit("PRIM", prim, "giftag")
        if flg == 0:  # PACKED
            for _ in range(nloop):
                for r in regs:
                    if off + 16 > n:
                        return
                    plo, phi = struct.unpack_from("<QQ", data, off)
                    off += 16
                    if r == 0x0E:
                        emit(REG_NAMES.get(phi & 0xFF, "R%02X" % (phi & 0xFF)), plo, "a+d")
                    elif r == 0x00:
                        emit("PRIM", plo & 0x7FF, "packed")
                    elif r in (0x06, 0x07, 0x08, 0x09):
                        emit(REG_NAMES[r], plo, "packed")
                    elif r == 0x0F:
                        pass
                    else:
                        emit(REG_NAMES.get(r, "D%X" % r), plo, "packed")
        elif flg == 1:  # REGLIST
            count = nloop * nreg
            for i in range(count):
                if off + 8 > n:
                    return
                v = struct.unpack_from("<Q", data, off)[0]
                off += 8
                r = regs[i % nreg]
                if r not in (0x0E, 0x0F):
                    emit(REG_NAMES.get(r, "D%X" % r), v, "reglist")
            if count & 1:
                off += 8
        else:  # IMAGE (2) or disabled (3): nloop qwords of data
            off += nloop * 16


def read_dump(path):
    b = open(path, "rb").read()
    if b[:4] != b"\xff\xff\xff\xff":
        raise SystemExit("not a new-format PCSX2 GS dump (no 0xFFFFFFFF header); old or compressed")
    hdr_size = struct.unpack_from("<I", b, 4)[0]
    state_version, state_size = struct.unpack_from("<II", b, 8)
    off = 8 + hdr_size
    # state blob, then the 8 KB privileged register block
    off += state_size
    off += 8192
    return b, off


def iter_packets(b, off):
    n = len(b)
    idx = 0
    while off < n:
        t = b[off]
        off += 1
        if t == 0:  # Transfer
            path = b[off]
            size = struct.unpack_from("<I", b, off + 1)[0]
            off += 5
            yield idx, "T", PATHS.get(path, "?"), b[off:off + size]
            off += size
        elif t == 1:  # VSync
            off += 1
            yield idx, "V", None, None
        elif t == 2:  # ReadFIFO2
            off += 4
        elif t == 3:  # Registers
            off += 8192
        else:
            raise SystemExit("unknown packet type %d at offset %d" % (t, off - 1))
        idx += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?")
    ap.add_argument("--regs", default="PRIM,PRMODE,PRMODECONT,TEX0_1,TEX0_2,TEX2_1")
    ap.add_argument("--frames", type=int, default=1, help="stop after N VSyncs (0 = all)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.dump:
        ap.error("dump file required")
    want = set(x.strip().upper() for x in a.regs.split(",") if x.strip())
    b, off = read_dump(a.dump)
    lines = []
    frames = 0
    for idx, kind, path, data in iter_packets(b, off):
        if kind == "V":
            lines.append("VSYNC")
            frames += 1
            if a.frames and frames >= a.frames:
                break
            continue

        def emit(reg, value, how, _p=path):
            if reg in want or "ALL" in want:
                extra = ""
                if reg in ("PRIM", "PRMODE"):
                    extra = " " + prim_text(value)
                lines.append("P%s %s 0x%x%s" % (_p, reg, value, extra))
        decode_gif(data, emit)
        if a.limit and len(lines) >= a.limit:
            break
    if a.summary:
        out, prev, cnt = [], None, 0
        for l in lines:
            if l == prev:
                cnt += 1
            else:
                if prev is not None:
                    out.append("%s x%d" % (prev, cnt))
                prev, cnt = l, 1
        if prev is not None:
            out.append("%s x%d" % (prev, cnt))
        lines = out
    print("\n".join(lines))
    return 0


def selftest():
    got = []
    # PACKED tag: nloop=1, nreg=2, PRE=1 PRIM=0x156, regs A+D, A+D
    tag_lo = 1 | (1 << 15) | (1 << 46) | (0x156 << 47) | (0 << 58) | (2 << 60)
    tag_hi = 0xE | (0xE << 4)
    pkt = struct.pack("<QQ", tag_lo, tag_hi)
    pkt += struct.pack("<QQ", 1, 0x1A)          # PRMODECONT = 1
    pkt += struct.pack("<QQ", 0x58, 0x1B)       # PRMODE = 0x58
    # REGLIST tag: nloop=1, nreg=1, reg PRIM(0) -> value 0x3
    tag2_lo = 1 | (1 << 58) | (1 << 60)
    pkt += struct.pack("<QQ", tag2_lo, 0x0) + struct.pack("<QQ", 0x3, 0)
    decode_gif(pkt, lambda r, v, h: got.append((r, v, h)))
    expect = [("PRIM", 0x156, "giftag"), ("PRMODECONT", 1, "a+d"), ("PRMODE", 0x58, "a+d"), ("PRIM", 0x3, "reglist")]
    ok = got == expect
    print("SELFTEST %s %s" % ("PASSED" if ok else "FAILED", "" if ok else got))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
