#!/usr/bin/env python3
"""gsref - a slow, straightforward reference GS (per-pixel rules from the GS User's Manual) that
replays a PCSX2 GS dump frame into GS memory, for checking a GS implementation against it.

  python tools/gsref.py DUMP.gs --frames N -o out.bin [--png out.png --fbp 70 --fbw 10 --w 640 --h 224]
        [--stats]
  python tools/gsref.py --selftest

out.bin is the 4 MiB GS memory after N frames (native layout; tools/gsview.py renders it).

Rules implemented (GS User's Manual, ch. 3 "Drawing"; PCSX2's software rasteriser agrees):
  * Coordinates: X/Y are 12.4 fixed point; window position = (XYZ - XYOFFSET) / 16.
  * Sampling point of pixel (x, y) is (x.0, y.0) (not x+0.5). A triangle draws the pixels whose
    sampling point is inside it; a point exactly on an edge is drawn only for a left or top
    edge (top-left rule), so triangles sharing an edge never draw a pixel twice and leave no gap.
    Sprites draw x in [ceil(x0), ceil(x1)), y in [ceil(y0), ceil(y1)).
  * Scissor SCAX0..SCAX1 / SCAY0..SCAY1 inclusive.
  * Attributes (colour with IIP, Z, fog, S/T/Q or U/V) are interpolated linearly to the sampling
    point; flat shading takes the colour of the vertex that kicked the primitive.
  * Texture: U/V in 1/16 texel (FST=1) or S/Q*2^TW, T/Q*2^TH (FST=0). Nearest = texel
    floor(u); bilinear = texels around (u - 0.5, v - 0.5) weighted by the fraction. Wrap per
    CLAMP (repeat / clamp / region clamp / region repeat). Indexed formats through the CLUT
    buffer, loaded at TEX0 writes per CLD (CSM1).
  * TFX modulate/decal/highlight/highlight2, TCC; fog; alpha test (AFAIL modes); destination
    alpha test; depth test; blend ((A - B) * C >> 7) + D with COLCLAMP; FBA; FBMSK; ZMSK.
Only what 2D/HiG titles use is covered: no AA1, no mipmapping (MXL > 0 uses level 0), no dither,
PSMCT32/24/16 frame buffers, Z32/24/16.
"""
import argparse
import math
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsview  # noqa: E402
import pcsx2gs  # noqa: E402

MEM = gsview.MEM
PSM_NAME = {0x00: "CT32", 0x01: "CT24", 0x02: "CT16", 0x0A: "CT16S", 0x13: "T8", 0x14: "T4", 0x1B: "T8H",
            0x24: "T4HL", 0x2C: "T4HH", 0x30: "Z32", 0x31: "Z24", 0x32: "Z16", 0x3A: "Z16S"}


def bits(v, lo, n):
    return (v >> lo) & ((1 << n) - 1)


def f32(u):
    return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]


class Mem:
    def __init__(self, data):
        self.m = np.frombuffer(bytearray(data), np.uint8).copy()

    def read(self, psm, bp, bw, X, Y):
        return gsview.read_at(self.m.tobytes() if False else self.m, PSM_NAME[psm], bp, bw, X, Y)

    def write(self, psm, bp, bw, X, Y, val):
        name = PSM_NAME[psm]
        f = gsview.FMT[name]
        X = np.asarray(X, np.int64)
        Y = np.asarray(Y, np.int64)
        val = np.asarray(val, np.uint32)
        byte, nib = gsview.addr(name, bp, bw, X, Y)
        m = self.m
        v = f.val
        if v == "u32":
            for k in range(4):
                m[byte + k] = (val >> (8 * k)) & 255
        elif v == "u24":
            for k in range(3):
                m[byte + k] = (val >> (8 * k)) & 255
        elif v == "u16":
            m[byte] = val & 255
            m[byte + 1] = (val >> 8) & 255
        elif v == "u8":
            m[byte] = val & 255
        elif v == "n4":
            # sequential, so that two nibbles of one byte in the same call both land
            for b, n, x in zip(byte.ravel(), nib.ravel(), val.ravel()):
                m[b] = (m[b] & (0xF0 >> (4 * n))) | ((int(x) & 15) << (4 * n))
        elif v == "b3":
            m[(byte + 3) % MEM] = val & 255
        elif v == "b3l":
            for b, x in zip(byte.ravel(), val.ravel()):
                m[(b + 3) % MEM] = (m[(b + 3) % MEM] & 0xF0) | (int(x) & 15)
        else:
            for b, x in zip(byte.ravel(), val.ravel()):
                m[(b + 3) % MEM] = (m[(b + 3) % MEM] & 0x0F) | ((int(x) & 15) << 4)


class Vertex:
    __slots__ = ("x", "y", "z", "f", "r", "g", "b", "a", "s", "t", "q", "u", "v")


class RefGS:
    def __init__(self, vram):
        self.mem = Mem(vram)
        self.reg = {}
        self.ctx = [dict(), dict()]
        for c in self.ctx:
            for n in ("XYOFFSET", "TEX0", "TEX1", "CLAMP", "SCISSOR", "ALPHA", "TEST", "FBA", "FRAME", "ZBUF"):
                c[n] = 0
        self.prim = 0
        self.prmodecont = 1
        self.prmode = 0
        self.texa = 0
        self.texclut = 0
        self.fogcol = 0
        self.pabe = 0
        self.colclamp = 1
        self.dthe = 0
        self.bitbltbuf = 0
        self.trxpos = 0
        self.trxreg = 0
        self.rgbaq = (0x80, 0x80, 0x80, 0x80)
        self.q = 1.0
        self.st = (0.0, 0.0)
        self.uv = (0, 0)
        self.fog = 0
        self.verts = []
        self.clut = np.zeros(512, np.uint32)  # 16-bit halves, CT32 entry n = clut[n] | clut[n+256] << 16
        self.cbp = [0, 0]
        self.xfer = None
        self.stats = {"prims": 0, "pixels": 0}

    # -- register writes ------------------------------------------------------------------
    def write(self, addr, v):
        R = {0x06: ("TEX0", 0), 0x07: ("TEX0", 1), 0x08: ("CLAMP", 0), 0x09: ("CLAMP", 1),
             0x14: ("TEX1", 0), 0x15: ("TEX1", 1), 0x18: ("XYOFFSET", 0), 0x19: ("XYOFFSET", 1),
             0x40: ("SCISSOR", 0), 0x41: ("SCISSOR", 1), 0x42: ("ALPHA", 0), 0x43: ("ALPHA", 1),
             0x47: ("TEST", 0), 0x48: ("TEST", 1), 0x4A: ("FBA", 0), 0x4B: ("FBA", 1),
             0x4C: ("FRAME", 0), 0x4D: ("FRAME", 1), 0x4E: ("ZBUF", 0), 0x4F: ("ZBUF", 1)}
        if addr in R:
            n, i = R[addr]
            self.ctx[i][n] = v
            if n == "TEX0":
                self.load_clut(v)
            return
        if addr == 0x00:
            self.prim = v & 0x7FF
            self.verts = []
        elif addr == 0x1B:
            self.prmode = v & 0x7FF
        elif addr == 0x1A:
            self.prmodecont = v & 1
        elif addr == 0x01:
            self.rgbaq = (bits(v, 0, 8), bits(v, 8, 8), bits(v, 16, 8), bits(v, 24, 8))
            self.q = f32(v >> 32)
        elif addr == 0x02:
            self.st = (f32(v), f32(v >> 32))
        elif addr == 0x03:
            self.uv = (bits(v, 0, 14), bits(v, 16, 14))
        elif addr == 0x0A:
            self.fog = bits(v, 56, 8)
        elif addr in (0x04, 0x0C):  # XYZF2 / XYZF3
            self.vertex(bits(v, 0, 16), bits(v, 16, 16), bits(v, 32, 24), bits(v, 56, 8), addr == 0x04)
        elif addr in (0x05, 0x0D):  # XYZ2 / XYZ3
            self.vertex(bits(v, 0, 16), bits(v, 16, 16), bits(v, 32, 32), None, addr == 0x05)
        elif addr == 0x16 or addr == 0x17:  # TEX2: CLUT fields of TEX0
            # TEX2 replaces PSM (bits 20-25) and CBP/CPSM/CSM/CSA/CLD (bits 37-63) of TEX0.
            i = addr - 0x16
            t = self.ctx[i]["TEX0"]
            m = (0x3F << 20) | (((1 << 27) - 1) << 37)
            new = (t & ~m) | (v & m)
            self.ctx[i]["TEX0"] = new
            self.load_clut(new)
        elif addr == 0x3B:
            self.texa = v
        elif addr == 0x3D:
            self.fogcol = v
        elif addr == 0x1C:
            self.texclut = v
        elif addr == 0x49:
            self.pabe = v & 1
        elif addr == 0x46:
            self.colclamp = v & 1
        elif addr == 0x45:
            self.dthe = v & 1
        elif addr == 0x50:
            self.bitbltbuf = v
        elif addr == 0x51:
            self.trxpos = v
        elif addr == 0x52:
            self.trxreg = v
        elif addr == 0x53:
            self.start_transfer(v & 3)
        elif addr == 0x54:  # HWREG
            self.image(struct.pack("<Q", v))
        else:
            self.reg[addr] = v

    def packed(self, desc, lo, hi):
        if desc == 0x1:
            self.rgbaq = (bits(lo, 0, 8), bits(lo, 32, 8), bits(hi, 0, 8), bits(hi, 32, 8))
            self.rgbaq_q = self.q  # Q from the last ST
        elif desc == 0x2:
            self.st = (f32(lo), f32(lo >> 32))
            self.q = f32(hi)
        elif desc == 0x3:
            self.uv = (bits(lo, 0, 14), bits(lo, 32, 14))
        elif desc == 0x4:
            self.vertex(bits(lo, 0, 16), bits(lo, 32, 16), bits(hi, 4, 24), bits(hi, 36, 8), not bits(hi, 47, 1))
        elif desc == 0x5:
            self.vertex(bits(lo, 0, 16), bits(lo, 32, 16), bits(hi, 0, 32), None, not bits(hi, 47, 1))
        elif desc == 0xA:
            self.fog = bits(hi, 36, 8)

    def tag(self):
        self.q = 1.0

    # -- CLUT -----------------------------------------------------------------------------
    def load_clut(self, tex0):
        cld = bits(tex0, 61, 3)
        cbp = bits(tex0, 37, 14)
        psm = bits(tex0, 20, 6)
        if psm not in (0x13, 0x14, 0x1B, 0x24, 0x2C):
            return
        if cld == 0 or cld > 5:
            return
        if cld == 2:
            self.cbp[0] = cbp
        elif cld == 3:
            self.cbp[1] = cbp
        elif cld == 4:
            if self.cbp[0] == cbp:
                return
            self.cbp[0] = cbp
        elif cld == 5:
            if self.cbp[1] == cbp:
                return
            self.cbp[1] = cbp
        cpsm = bits(tex0, 51, 4)
        csm = bits(tex0, 55, 1)
        csa = bits(tex0, 56, 5)
        four = psm in (0x14, 0x24, 0x2C)
        n = 16 if four else 256
        i = np.arange(n)
        if csm == 0:
            if four:
                X, Y = i & 7, i >> 3
            else:
                e = (i & ~0x18) | ((i & 8) << 1) | ((i & 0x10) >> 1)
                X, Y = e & 15, e >> 4
            bw = 1
        else:
            cbw = bits(self.texclut, 0, 6)
            X = bits(self.texclut, 6, 6) * 16 + i
            Y = np.full(n, bits(self.texclut, 12, 10))
            bw = cbw
        cp = cpsm if cpsm in (0x0, 0x2, 0xA) else 0x0
        raw = self.mem.read(cp, cbp, bw, X, Y).astype(np.uint32)
        if cp == 0x0:
            base = (csa & 15) * 16 if four else 0
            idx = (base + i) & 255
            self.clut[idx] = raw & 0xFFFF
            self.clut[idx + 256] = raw >> 16
        else:
            base = csa * 16 if four else 0
            idx = (base + i) & 511
            self.clut[idx] = raw & 0xFFFF

    # -- transfers ------------------------------------------------------------------------
    def start_transfer(self, xdir):
        b = self.bitbltbuf
        dbp, dbw, dpsm = bits(b, 32, 14), bits(b, 48, 6), bits(b, 56, 6)
        sbp, sbw, spsm = bits(b, 0, 14), bits(b, 16, 6), bits(b, 24, 6)
        p = self.trxpos
        ssax, ssay, dsax, dsay = bits(p, 0, 11), bits(p, 16, 11), bits(p, 32, 11), bits(p, 48, 11)
        w, h = bits(self.trxreg, 0, 12), bits(self.trxreg, 32, 12)
        if xdir == 0:
            self.xfer = dict(bp=dbp, bw=dbw, psm=dpsm, x0=dsax, y0=dsay, w=w, h=h, n=0, buf=b"")
        elif xdir == 2:
            Y, X = np.mgrid[0:h, 0:w]
            v = self.mem.read(spsm, sbp, sbw, X + ssax, Y + ssay)
            self.mem.write(dpsm, dbp, dbw, (X + dsax) % 2048, (Y + dsay) % 2048, v)
            self.xfer = None
        else:
            self.xfer = None

    def image(self, data):
        x = self.xfer
        if not x or x["w"] == 0:
            return
        bpp = {0x00: 32, 0x01: 24, 0x02: 16, 0x0A: 16, 0x13: 8, 0x14: 4, 0x1B: 8, 0x24: 4, 0x2C: 4,
               0x30: 32, 0x31: 24, 0x32: 16, 0x3A: 16}[x["psm"]]
        buf = x["buf"] + data
        total = x["w"] * x["h"]
        if bpp == 4:
            arr = np.frombuffer(buf, np.uint8)
            vals = np.stack([arr & 15, arr >> 4], -1).ravel().astype(np.uint32)
            used = len(buf)
        elif bpp == 24:
            k = len(buf) // 3
            arr = np.frombuffer(buf[:k * 3], np.uint8).reshape(-1, 3).astype(np.uint32)
            vals = arr[:, 0] | (arr[:, 1] << 8) | (arr[:, 2] << 16)
            used = k * 3
        else:
            nb = bpp // 8
            k = len(buf) // nb
            vals = np.frombuffer(buf[:k * nb], {1: np.uint8, 2: "<u2", 4: "<u4"}[nb]).astype(np.uint32)
            used = k * nb
        x["buf"] = buf[used:]
        vals = vals[:max(0, total - x["n"])]
        if len(vals):
            idx = np.arange(x["n"], x["n"] + len(vals))
            X = x["x0"] + idx % x["w"]
            Y = x["y0"] + idx // x["w"]
            self.mem.write(x["psm"], x["bp"], x["bw"], X % 2048, Y % 2048, vals)
            x["n"] += len(vals)
        if x["n"] >= total:
            self.xfer = None

    # -- vertices and kicks ---------------------------------------------------------------
    def vertex(self, x, y, z, f, kick):
        v = Vertex()
        v.x, v.y, v.z = x, y, z
        v.f = self.fog if f is None else f
        v.r, v.g, v.b, v.a = self.rgbaq
        v.s, v.t = self.st
        v.q = self.q
        v.u, v.v = self.uv
        prim = self.prim if self.prmodecont else (self.prim & 7) | (self.prmode & ~7)
        ptype = prim & 7
        self.verts.append(v)
        need = {0: 1, 1: 2, 2: 2, 3: 3, 4: 3, 5: 3, 6: 2}.get(ptype, 99)
        if len(self.verts) < need:
            return
        vs = self.verts[-need:]
        if kick:
            self.draw(prim, vs)
        if ptype in (0, 1, 3, 6):
            self.verts = []
        elif ptype == 2:
            self.verts = self.verts[-1:]
        elif ptype == 4:
            self.verts = self.verts[-2:]
        elif ptype == 5:
            self.verts = [self.verts[0], self.verts[-1]]

    # -- drawing --------------------------------------------------------------------------
    def draw(self, prim, vs):
        ptype = prim & 7
        P = dict(iip=bits(prim, 3, 1), tme=bits(prim, 4, 1), fge=bits(prim, 5, 1), abe=bits(prim, 6, 1),
                 fst=bits(prim, 8, 1), ctxt=bits(prim, 9, 1))
        c = self.ctx[P["ctxt"]]
        ofx, ofy = bits(c["XYOFFSET"], 0, 16), bits(c["XYOFFSET"], 32, 16)
        sc = c["SCISSOR"]
        sx0, sx1, sy0, sy1 = bits(sc, 0, 11), bits(sc, 16, 11), bits(sc, 32, 11), bits(sc, 48, 11)
        self.stats["prims"] += 1
        X = [(v.x - ofx) / 16.0 for v in vs]
        Y = [(v.y - ofy) / 16.0 for v in vs]
        if ptype == 6:
            x0, x1 = sorted((X[0], X[1]))
            y0, y1 = sorted((Y[0], Y[1]))
            ix0, ix1 = max(math.ceil(x0), sx0), min(math.ceil(x1) - 1, sx1)
            iy0, iy1 = max(math.ceil(y0), sy0), min(math.ceil(y1) - 1, sy1)
            if ix0 > ix1 or iy0 > iy1:
                return
            py, px = np.mgrid[iy0:iy1 + 1, ix0:ix1 + 1]
            px = px.ravel().astype(np.float64)
            py = py.ravel().astype(np.float64)
            a, b = vs[0], vs[1]
            tx = (px - X[0]) / (X[1] - X[0]) if X[1] != X[0] else np.zeros_like(px)  # noqa: E501
            ty = (py - Y[0]) / (Y[1] - Y[0]) if Y[1] != Y[0] else np.zeros_like(py)

            def lerp_x(fa, fb):
                return fa + (fb - fa) * tx

            def lerp_y(fa, fb):
                return fa + (fb - fa) * ty
            A = dict(
                z=np.full(px.shape, float(b.z)), f=np.full(px.shape, float(b.f)),
                r=np.full(px.shape, float(b.r)), g=np.full(px.shape, float(b.g)),
                b=np.full(px.shape, float(b.b)), a=np.full(px.shape, float(b.a)),
                u=lerp_x(a.u, b.u), v=lerp_y(a.v, b.v),
                s=lerp_x(a.s, b.s), t=lerp_y(a.t, b.t), q=np.full(px.shape, float(b.q)))
            self.shade(P, c, px.astype(np.int64), py.astype(np.int64), A)
        elif ptype in (3, 4, 5):
            x0, y0 = X[0], Y[0]
            x1, y1 = X[1], Y[1]
            x2, y2 = X[2], Y[2]
            area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if area == 0:
                return
            ix0 = max(math.ceil(min(X)), sx0)
            ix1 = min(math.floor(max(X)), sx1)
            iy0 = max(math.ceil(min(Y)), sy0)
            iy1 = min(math.floor(max(Y)), sy1)
            if ix0 > ix1 or iy0 > iy1:
                return
            py, px = np.mgrid[iy0:iy1 + 1, ix0:ix1 + 1]
            px = px.ravel().astype(np.float64)
            py = py.ravel().astype(np.float64)
            # orient counter-clockwise in a y-down frame so every edge function is >= 0 inside
            P3 = [(x0, y0), (x1, y1), (x2, y2)]
            if area < 0:
                P3 = [P3[0], P3[2], P3[1]]
            inside = np.ones(px.shape, bool)
            ws = []
            for k in range(3):
                (ax, ay), (bx, by) = P3[k], P3[(k + 1) % 3]
                # edge function: > 0 inside (area > 0 orientation)
                e = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
                dx, dy = bx - ax, by - ay
                # top-left rule: with the interior on the e > 0 side, an edge is "top" when it is
                # horizontal with the interior below it, "left" when the interior is to its right.
                # In this orientation (y down, e > 0 inside) a left edge goes downward (dy > 0)
                # and a top edge goes leftward... derive from the inward normal instead:
                nx, ny = -dy, dx  # gradient of e = inward normal
                topleft = (nx > 0) or (nx == 0 and ny > 0)
                inside &= (e > 0) | ((e == 0) & topleft)
                ws.append(e)
            if not inside.any():
                return
            # barycentric weights for the original vertex order
            def bary(px, py):
                d = area
                w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / d
                w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / d
                return w0, w1, 1.0 - w0 - w1
            px, py = px[inside], py[inside]
            w0, w1, w2 = bary(px, py)
            last = vs[2]

            def it(name):
                # a0 + w1 (a1 - a0) + w2 (a2 - a0): exact for an attribute that is the same at all
                # three vertices (the GS DDA steps by a zero gradient then), e.g. a 24-bit Z
                a0, a1, a2 = (float(getattr(v, name)) for v in vs)
                return a0 + w1 * (a1 - a0) + w2 * (a2 - a0)
            if P["iip"]:
                col = {k: it(k) for k in "rgba"}
            else:
                col = {k: np.full(px.shape, float(getattr(last, k))) for k in "rgba"}
            A = dict(z=it("z"), f=it("f"), u=it("u"), v=it("v"), s=it("s"), t=it("t"), q=it("q"), **col)
            self.shade(P, c, px.astype(np.int64), py.astype(np.int64), A)
        elif ptype == 0:
            v = vs[0]
            px = np.array([math.floor(X[0] + 0.5)])  # nearest pixel (points round)
            py = np.array([math.floor(Y[0] + 0.5)])
            ok = (px >= sx0) & (px <= sx1) & (py >= sy0) & (py <= sy1)
            if not ok.all():
                return
            A = {k: np.array([float(getattr(v, k))]) for k in ("z", "f", "r", "g", "b", "a", "u", "v", "s", "t", "q")}
            self.shade(P, c, px, py, A)
        else:
            self.stats.setdefault("skipped_lines", 0)
            self.stats["skipped_lines"] += 1

    def sample(self, c, P, A):
        tex0, tex1, clamp = c["TEX0"], c["TEX1"], c["CLAMP"]
        tbp, tbw, psm = bits(tex0, 0, 14), bits(tex0, 14, 6), bits(tex0, 20, 6)
        tw, th = 1 << min(bits(tex0, 26, 4), 10), 1 << min(bits(tex0, 30, 4), 10)
        if P["fst"]:
            U = A["u"] / 16.0
            V = A["v"] / 16.0
        else:
            q = A["q"]
            U = A["s"] / q * tw
            V = A["t"] / q * th
        # FST U/V are 10.4 fixed point: the interpolated value keeps 4 fraction bits
        U = np.floor(U * 16.0) / 16.0
        V = np.floor(V * 16.0) / 16.0
        mmag = bits(tex1, 5, 1)
        mmin = bits(tex1, 6, 3)
        # no LOD: magnification filter decides (MMIN only differs when minifying; for MXL = 0 and
        # LCM = 0, K = 0 with Q = 1 the GS magnifies, LOD = 0 -> MMAG)
        linear = mmag == 1
        wms, wmt = bits(clamp, 0, 2), bits(clamp, 2, 2)
        minu, maxu, minv, maxv = bits(clamp, 4, 10), bits(clamp, 14, 10), bits(clamp, 24, 10), bits(clamp, 34, 10)

        def wrap(t, size, wm, mn, mx):
            if wm == 0:
                return t & (size - 1)
            if wm == 1:
                return np.clip(t, 0, size - 1)
            if wm == 2:
                return np.clip(t, mn, mx)
            return (t & mn) | mx

        def fetch(iu, iv):
            iu = wrap(iu, tw, wms, minu, maxu)
            iv = wrap(iv, th, wmt, minv, maxv)
            raw = self.mem.read(psm, tbp, tbw, iu, iv).astype(np.uint32)
            return self.texel_rgba(c, psm, raw)
        if not linear:
            return fetch(np.floor(U).astype(np.int64), np.floor(V).astype(np.int64))
        U2 = U - 0.5
        V2 = V - 0.5
        u0 = np.floor(U2).astype(np.int64)
        v0 = np.floor(V2).astype(np.int64)
        fu = (U2 - u0)
        fv = (V2 - v0)
        c00, c10, c01, c11 = fetch(u0, v0), fetch(u0 + 1, v0), fetch(u0, v0 + 1), fetch(u0 + 1, v0 + 1)
        fu = fu[:, None]
        fv = fv[:, None]
        top = c00 * (1 - fu) + c10 * fu
        bot = c01 * (1 - fu) + c11 * fu
        return np.floor(top * (1 - fv) + bot * fv + 1e-9)

    def texel_rgba(self, c, psm, raw):
        tex0 = c["TEX0"]
        texa = self.texa
        ta0, aem, ta1 = bits(texa, 0, 8), bits(texa, 15, 1), bits(texa, 32, 8)
        if psm in (0x13, 0x14, 0x1B, 0x24, 0x2C):
            cpsm = bits(tex0, 51, 4)
            csa = bits(tex0, 56, 5)
            four = psm in (0x14, 0x24, 0x2C)
            if cpsm == 0x0 or cpsm == 0x1:
                idx = (((csa & 15) * 16 if four else 0) + raw) & 255
                raw = self.clut[idx] | (self.clut[idx + 256] << 16)
                psm = 0x0
            else:
                idx = ((csa * 16 if four else 0) + raw) & 511
                raw = self.clut[idx]
                psm = 0x2
        if psm in (0x00, 0x30):
            r, g, b, a = raw & 255, (raw >> 8) & 255, (raw >> 16) & 255, raw >> 24
        elif psm in (0x01, 0x31):
            r, g, b = raw & 255, (raw >> 8) & 255, (raw >> 16) & 255
            a = np.where(aem & ((r | g | b) == 0), 0, ta0)
        else:
            r = (raw & 31) << 3
            g = ((raw >> 5) & 31) << 3
            b = ((raw >> 10) & 31) << 3
            a = np.where(raw & 0x8000, ta1, np.where(aem & ((raw & 0x7FFF) == 0), 0, ta0))
        return np.stack([r, g, b, np.broadcast_to(a, r.shape)], -1).astype(np.float64)

    def shade(self, P, c, px, py, A):
        n = len(px)
        if n == 0:
            return
        sc = c["SCISSOR"]
        ok = (px >= bits(sc, 0, 11)) & (px <= bits(sc, 16, 11)) & (py >= bits(sc, 32, 11)) & (py <= bits(sc, 48, 11))
        if not ok.all():
            px, py = px[ok], py[ok]
            A = {k: v[ok] for k, v in A.items()}
            n = len(px)
            if n == 0:
                return
        R = np.floor(A["r"]).astype(np.int64)
        G = np.floor(A["g"]).astype(np.int64)
        B = np.floor(A["b"]).astype(np.int64)
        Al = np.floor(A["a"]).astype(np.int64)
        tex0 = c["TEX0"]
        if P["tme"]:
            t = self.sample(c, P, A).astype(np.int64)
            tr, tg, tb, ta = t[:, 0], t[:, 1], t[:, 2], t[:, 3]
            tfx, tcc = bits(tex0, 35, 2), bits(tex0, 34, 1)
            if tfx == 0:
                R, G, B = np.minimum((tr * R) >> 7, 255), np.minimum((tg * G) >> 7, 255), np.minimum((tb * B) >> 7, 255)
                Al = np.minimum((ta * Al) >> 7, 255) if tcc else Al
            elif tfx == 1:
                R, G, B = tr, tg, tb
                Al = ta if tcc else Al
            else:
                R2 = np.minimum(((tr * R) >> 7) + Al, 255)
                G2 = np.minimum(((tg * G) >> 7) + Al, 255)
                B2 = np.minimum(((tb * B) >> 7) + Al, 255)
                if tfx == 2:
                    Al = np.minimum(ta + Al, 255) if tcc else Al
                else:
                    Al = ta if tcc else Al
                R, G, B = R2, G2, B2
        if P["fge"]:
            F = np.floor(A["f"]).astype(np.int64)
            fr, fg, fb = bits(self.fogcol, 0, 8), bits(self.fogcol, 8, 8), bits(self.fogcol, 16, 8)
            R = (F * R + (255 - F) * fr) >> 8
            G = (F * G + (255 - F) * fg) >> 8
            B = (F * B + (255 - F) * fb) >> 8
        test = c["TEST"]
        frame, zbuf = c["FRAME"], c["ZBUF"]
        fbp, fbw, fpsm, fbmsk = bits(frame, 0, 9) * 32, bits(frame, 16, 6), bits(frame, 24, 6), bits(frame, 32, 32)
        zbp, zpsm, zmsk = bits(zbuf, 0, 9) * 32, bits(zbuf, 24, 4) | 0x30, bits(zbuf, 32, 1)
        write_fb = np.ones(n, bool)
        write_z = np.ones(n, bool)
        write_a = np.ones(n, bool)
        if bits(test, 0, 1):
            atst, aref, afail = bits(test, 1, 3), bits(test, 4, 8), bits(test, 12, 2)
            passed = [np.zeros(n, bool), np.ones(n, bool), Al < aref, Al <= aref, Al == aref, Al >= aref,
                      Al > aref, Al != aref][atst]
            fail = ~passed
            if afail == 0:
                write_fb &= ~fail
                write_z &= ~fail
            elif afail == 1:
                write_z &= ~fail
            elif afail == 2:
                write_fb &= ~fail
            else:
                write_z &= ~fail
                write_a &= ~fail
        dst = self.mem.read(fpsm, fbp, fbw, px, py).astype(np.int64)
        if fpsm in (0x02, 0x0A):
            dr, dg, db = (dst & 31) << 3, ((dst >> 5) & 31) << 3, ((dst >> 10) & 31) << 3
            da = np.where(dst & 0x8000, 0x80, 0)
        else:
            dr, dg, db = dst & 255, (dst >> 8) & 255, (dst >> 16) & 255
            da = (dst >> 24) & 255 if fpsm == 0x00 else np.full(n, 0x80)
        alive = np.ones(n, bool)
        if bits(test, 14, 1) and fpsm != 0x01:
            datm = bits(test, 15, 1)
            abit = (da >> 7) & 1
            alive &= abit == datm
        zte, ztst = bits(test, 16, 1), bits(test, 17, 2)
        Z = np.floor(A["z"] + 0.0).astype(np.int64)
        zmax = {0x30: 0xFFFFFFFF, 0x31: 0xFFFFFF, 0x32: 0xFFFF, 0x3A: 0xFFFF}[zpsm]
        Z = np.minimum(Z, zmax)
        if zte:
            if ztst == 0:
                alive &= False
            elif ztst >= 2:
                dz = self.mem.read(zpsm, zbp, fbw, px, py).astype(np.int64)
                alive &= (Z >= dz) if ztst == 2 else (Z > dz)
        write_fb &= alive
        write_z &= alive
        if P["abe"]:
            al = c["ALPHA"]
            sa, sb, scc, sd, fix = bits(al, 0, 2), bits(al, 2, 2), bits(al, 4, 2), bits(al, 6, 2), bits(al, 32, 8)
            Cs, Cd = (R, G, B), (dr, dg, db)

            def pick(sel, k):
                return Cs[k] if sel == 0 else (Cd[k] if sel == 1 else 0)
            Cv = Al if scc == 0 else (da if scc == 1 else np.full(n, fix))
            out = []
            for k in range(3):
                v = (((pick(sa, k) - pick(sb, k)) * Cv) >> 7) + pick(sd, k)
                out.append(v)
            blend = np.ones(n, bool)
            if self.pabe:
                blend = (Al & 0x80) != 0
            R = np.where(blend, out[0], R)
            G = np.where(blend, out[1], G)
            B = np.where(blend, out[2], B)
        if self.colclamp:
            R, G, B = np.clip(R, 0, 255), np.clip(G, 0, 255), np.clip(B, 0, 255)
        else:
            R, G, B = R & 255, G & 255, B & 255
        Aout = Al | (0x80 if bits(c["FBA"], 0, 1) else 0)
        if fpsm in (0x02, 0x0A):
            val = (R >> 3) | ((G >> 3) << 5) | ((B >> 3) << 10) | (((Aout >> 7) & 1) << 15)
            if fbmsk:
                m = ((fbmsk >> 3) & 31) | (((fbmsk >> 11) & 31) << 5) | (((fbmsk >> 19) & 31) << 10) | (((fbmsk >> 31) & 1) << 15)
                val = (val & ~m) | (dst & m)
            amask = 0x8000
        else:
            val = R | (G << 8) | (B << 16) | ((Aout & 255) << 24)
            if fbmsk:
                val = (val & ~fbmsk) | (dst & fbmsk)
            amask = 0xFF000000
        val = np.where(write_a, val, (val & ~amask) | (dst & amask))
        sel = write_fb
        if sel.any():
            self.mem.write(fpsm, fbp, fbw, px[sel], py[sel], val[sel].astype(np.uint32))
            self.stats["pixels"] += int(sel.sum())
        selz = write_z & (zmsk == 0)
        if zte and selz.any():
            self.mem.write(zpsm, zbp, fbw, px[selz], py[selz], Z[selz].astype(np.uint32))

    # -- driving --------------------------------------------------------------------------
    def run_gif(self, data):
        for ev in pcsx2gs.gif_writes(data):
            k = ev[0]
            if k == "reg":
                self.write(ev[1], ev[2])
            elif k == "packed":
                self.packed(ev[1], ev[2], ev[3])
            elif k == "tag":
                self.tag()
            elif k == "image":
                self.image(ev[1])


def replay(dump, nframes):
    gs = RefGS(dump.vram)
    for a, v in pcsx2gs.initial_writes(dump):
        gs.write(a, v)
    for f in pcsx2gs.frames(dump)[:nframes]:
        for _path, data in f:
            gs.run_gif(data)
    return gs


def selftest():
    fails = 0

    def check(c, m):
        nonlocal fails
        if not c:
            fails += 1
            print("FAIL", m)
    gs = RefGS(bytes(MEM))
    gs.write(0x4C, 1 << 16)            # FRAME_1 fbp 0 fbw 1 CT32
    gs.write(0x40, (63 << 16) | (63 << 48))
    gs.write(0x47, 0)                  # TEST off
    gs.write(0x18, 0)
    gs.write(0x00, 4)                  # tristrip, flat
    gs.write(0x01, 0x80FFFFFF & 0xFFFFFFFF | 0x0)
    # two triangles of a 4x4 square from (0,0) to (4,4), split on the diagonal
    for x, y in ((0, 0), (4, 0), (0, 4), (4, 4)):
        gs.write(0x05, (x * 16) | ((y * 16) << 16))
    X, Y = np.meshgrid(np.arange(6), np.arange(6))
    img = gs.mem.read(0, 0, 1, X, Y)
    cover = (img != 0).astype(int)
    check(cover[:4, :4].all() and cover[4:, :].sum() == 0 and cover[:, 4:].sum() == 0, f"square coverage\n{cover}")
    # additive: every pixel drawn exactly once
    gs2 = RefGS(bytes(MEM))
    gs2.write(0x4C, 1 << 16)
    gs2.write(0x40, (63 << 16) | (63 << 48))
    gs2.write(0x42, 0x48 | (0x80 << 32))  # Cs*As + Cd
    gs2.write(0x00, 4 | (1 << 6))
    gs2.write(0x01, 0x10 | (0x80 << 24))
    for x, y in ((0, 0), (5, 1), (1, 6), (6, 7)):
        gs2.write(0x05, (x * 16) | ((y * 16) << 16))
    img = gs2.mem.read(0, 0, 1, *np.meshgrid(np.arange(8), np.arange(8))) & 255
    check(set(np.unique(img).tolist()) <= {0, 0x10}, f"double-drawn pixels on a shared edge\n{img}")
    # sprite (0,0)-(3,2) covers 3x2
    gs3 = RefGS(bytes(MEM))
    gs3.write(0x4C, 1 << 16)
    gs3.write(0x40, (63 << 16) | (63 << 48))
    gs3.write(0x00, 6)
    gs3.write(0x01, 0xFF)
    gs3.write(0x05, 0)
    gs3.write(0x05, (3 * 16) | ((2 * 16) << 16))
    img = gs3.mem.read(0, 0, 1, *np.meshgrid(np.arange(5), np.arange(4)))
    check(int((img != 0).sum()) == 6 and img[1, 2] != 0, "sprite coverage")
    print("selftest", "PASS" if not fails else f"FAIL ({fails})")
    return 1 if fails else 0


def compare(a):
    """Frame-buffer comparison of two GS memory files: counts, worst pixels, optional diff PNG."""
    A = open(a.compare[0], "rb").read()
    B = open(a.compare[1], "rb").read()
    ra = gsview.read_raw(A, "CT32", a.fbp * 32, a.fbw, 0, 0, a.w, a.h).astype(np.int64)
    rb = gsview.read_raw(B, "CT32", a.fbp * 32, a.fbw, 0, 0, a.w, a.h).astype(np.int64)
    ch = lambda r: np.stack([(r >> s) & 255 for s in (0, 8, 16, 24)], -1)
    ca, cb = ch(ra), ch(rb)
    d = np.abs(ca - cb)
    rgb = d[..., :3].max(-1)
    bad = rgb > a.tol
    print(f"fbp {a.fbp} {a.w}x{a.h}: identical {int((d.max(-1) == 0).sum())}, rgb within {a.tol}: "
          f"{int((~bad).sum())}, worse: {int(bad.sum())} pixels; max rgb diff {int(rgb.max())}, "
          f"mean {float(rgb.mean()):.3f}; alpha differs at {int((d[..., 3] > 0).sum())}")
    ys, xs = np.nonzero(bad)
    order = np.argsort(-rgb[ys, xs])[:8]
    for k in order:
        y, x = ys[k], xs[k]
        print(f"  ({x},{y}) {ra[y, x]:08x} vs {rb[y, x]:08x}")
    if a.png:
        from PIL import Image
        img = np.zeros((a.h, a.w * 3, 3), np.uint8)
        img[:, :a.w] = ca[..., :3]
        img[:, a.w:2 * a.w] = cb[..., :3]
        img[:, 2 * a.w:] = np.where(bad[..., None], [255, 0, 255], (np.minimum(rgb * 16, 255))[..., None]).astype(np.uint8)
        Image.fromarray(img).save(a.png)
        print("wrote", a.png, "(A | B | diff: magenta = beyond tolerance)")
    return 1 if bad.any() else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", nargs="?")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("-o", "--out")
    ap.add_argument("--png")
    ap.add_argument("--fbp", type=int, default=70)
    ap.add_argument("--fbw", type=int, default=10)
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--h", type=int, default=224)
    ap.add_argument("--compare", nargs=2, metavar=("A.bin", "B.bin"),
                    help="compare the frame buffer (--fbp/--fbw/--w/--h) of two GS memory files instead")
    ap.add_argument("--tol", type=int, default=2, help="--compare: channel difference allowed (default 2)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.compare:
        return compare(a)
    d = pcsx2gs.load(a.dump)
    gs = replay(d, a.frames)
    print("stats", gs.stats)
    mem = gs.mem.m.tobytes()
    if a.out:
        open(a.out, "wb").write(mem)
        print("wrote", a.out)
    if a.png:
        from PIL import Image
        Image.fromarray(gsview.render(mem, "CT32", a.fbp * 32, a.fbw, 0, 0, a.w, a.h)).save(a.png)
        print("wrote", a.png)


if __name__ == "__main__":
    sys.exit(main() or 0)
