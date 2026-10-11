#!/usr/bin/env python3
"""Helpers for tools\\checkpoint.ps1: frame-hash comparison and the scene diff.

    python tools/checkpoint.py hashdiff <a.hash> <b.hash> [--from T] [--until T]
        Compares two PS2X_FRAME_HASH logs tick by tick (lines "tick=N cycle=C ram=.. vram=.. gs=..").
        Exit 0: identical over the common ticks; 1: they differ (the first differing tick and
        what differs are printed); 2: nothing to compare.
    python tools/checkpoint.py scene <gsdump_N.rgba> <reference.png> [--tolerance 15] [--out diff.png]
        Our presented frame (size from the dump's .txt) against a reference: our own frame (same
        size: compared pixel for pixel) or a PCSX2 capture (window or picture: cropped to its 4:3
        picture as in tools\\refcompare.py). Score = mean absolute difference of the two pictures as
        64x48 grey thumbnails (refcompare's scale: <15 close, >35 no match). PASS when score <=
        tolerance. --out writes ours | reference | difference side by side.
    python tools/checkpoint.py rgba2png <gsdump_N.rgba> <out.png>
    python tools/checkpoint.py --selftest
"""
import argparse
import os
import re
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refcompare  # noqa: E402  (crop_box, load_dump)

LINE = re.compile(r"tick=(\d+) cycle=(\d+) ram=([0-9a-f]+) vram=([0-9a-f]+) gs=([0-9a-f]+)")
FIELDS = ("cycle", "ram", "vram", "gs")


def read_hashes(path):
    out = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE.search(line)
            if m:
                out[int(m.group(1))] = dict(zip(FIELDS, (m.group(2), m.group(3), m.group(4), m.group(5))))
    return out


def hashdiff(a, b, first=None, last=None):
    """(status, message): status 0 same, 1 differ, 2 nothing to compare."""
    ha, hb = read_hashes(a), read_hashes(b)
    common = sorted(t for t in set(ha) & set(hb) if (first is None or t >= first) and (last is None or t <= last))
    if not common:
        return 2, "no common ticks (a: %d lines, b: %d lines)" % (len(ha), len(hb))
    for t in common:
        diff = [k for k in FIELDS if ha[t][k] != hb[t][k]]
        if diff:
            same = len([x for x in common if x < t])
            what = ", ".join(diff)
            hint = " (EE cycle count differs: the runs took different paths through guest time)" if "cycle" in diff else ""
            return 1, "FAIL: first difference at tick %d: %s%s; %d tick(s) identical before it (from %d)" % (
                t, what, hint, same, common[0])
    gaps = (common[-1] - common[0] + 1) - len(common)
    return 0, "PASS: %d ticks identical (%d-%d)%s" % (
        len(common), common[0], common[-1], ", %d tick(s) missing from one run" % gaps if gaps else "")


def load_ours(rgba_path):
    return refcompare.load_dump(rgba_path).convert("RGB")


def load_reference(path, size):
    img = Image.open(path).convert("RGB")
    if img.size == size:
        return img, "same size: pixel for pixel"
    g = np.asarray(img.convert("L"), dtype=np.float32)
    # Window chrome first: black border rows, then the grey title and menu bars (bright, nearly
    # uniform rows); refcompare's own chrome test stops at a black first row.
    top = 0
    while top < g.shape[0] // 5 and g[top].mean() < refcompare.DARK:
        top += 1
    while top < g.shape[0] // 5 and g[top].mean() > 80 and g[top].std() < 30:
        top += 1
    t, b, l, r = refcompare.crop_box(g[top:])
    t, b = t + top, b + top
    return img.crop((l, t, r, b)).resize(size, Image.BILINEAR), "PCSX2 capture cropped to %dx%d at (%d,%d)" % (r - l, b - t, l, t)


def thumb_score(a, b):
    ta = np.asarray(a.convert("L").resize(refcompare.THUMB, Image.BILINEAR), dtype=np.float32)
    tb = np.asarray(b.convert("L").resize(refcompare.THUMB, Image.BILINEAR), dtype=np.float32)
    return float(np.abs(ta - tb).mean())


def scene(rgba, reference, tolerance=15.0, out=None):
    ours = load_ours(rgba)
    ref, how = load_reference(reference, ours.size)
    score = thumb_score(ours, ref)
    if out:
        d = np.abs(np.asarray(ours, dtype=np.int16) - np.asarray(ref, dtype=np.int16)).max(axis=2)
        heat = np.zeros(d.shape + (3,), dtype=np.uint8)
        heat[..., 0] = np.clip(d * 3, 0, 255)
        heat[..., 1] = np.clip(d * 3 - 255, 0, 255)
        w, h = ours.size
        sheet = Image.new("RGB", (w * 3, h))
        sheet.paste(ours, (0, 0))
        sheet.paste(ref, (w, 0))
        sheet.paste(Image.fromarray(heat), (w * 2, 0))
        sheet.save(out)
    ok = score <= tolerance
    return ok, "%s: score %.1f (tolerance %.1f; reference %s)%s" % (
        "PASS" if ok else "FAIL", score, tolerance, how, "; diff image " + out if out else "")


def selftest():
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.hash"), os.path.join(d, "b.hash")
        rows = ["tick=%d cycle=%d ram=%016x vram=%016x gs=%016x" % (t, t * 100, t, t + 1, 7) for t in range(100, 110)]
        open(a, "w").write("\n".join(rows) + "\n")
        open(b, "w").write("\n".join(rows) + "\n")
        st, msg = hashdiff(a, b)
        assert st == 0 and "10 ticks identical" in msg, msg
        rows2 = list(rows)
        rows2[5] = rows2[5].replace("ram=%016x" % 105, "ram=%016x" % 999)
        open(b, "w").write("\n".join(rows2) + "\n")
        st, msg = hashdiff(a, b)
        assert st == 1 and "tick 105: ram;" in msg and "5 tick(s) identical" in msg, msg
        rows3 = list(rows)
        rows3[3] = rows3[3].replace("cycle=%d" % 10300, "cycle=1")
        open(b, "w").write("\n".join(rows3) + "\n")
        st, msg = hashdiff(a, b)
        assert st == 1 and "tick 103: cycle" in msg and "different paths" in msg, msg
        assert hashdiff(a, b, last=102)[0] == 0
        open(b, "w").write("")
        assert hashdiff(a, b)[0] == 2

        # A dump: 640x512 RGBA, picture 640x448 in the top rows, size in the .txt.
        pic = np.zeros((512, 640, 4), dtype=np.uint8)
        pic[:448, :, 3] = 255
        pic[100:300, 200:400, 0] = 255  # a red square
        rgba = os.path.join(d, "gsdump_100.rgba")
        pic.tofile(rgba)
        open(os.path.join(d, "gsdump_100.txt"), "w").write("tick=100\npresented=640x448 rowstride=640\n")
        same = os.path.join(d, "same.png")
        Image.fromarray(pic[:448, :, :3]).save(same)
        ok, msg = scene(rgba, same, out=os.path.join(d, "diff.png"))
        assert ok and "score 0.0" in msg and os.path.exists(os.path.join(d, "diff.png")), msg
        # A PCSX2-like window capture: grey title bar, black borders, the picture scaled up.
        win = np.full((800, 1100, 3), 60, dtype=np.uint8)
        win[40:, :] = 0
        big = np.asarray(Image.fromarray(pic[:448, :, :3]).resize((960, 720), Image.BILINEAR))
        win[60:780, 70:1030] = big
        cap = os.path.join(d, "cap.png")
        Image.fromarray(win).save(cap)
        ok, msg = scene(rgba, cap)
        assert ok and "PCSX2 capture" in msg, msg
        win2 = win.copy()
        win2[0, :] = 0           # a black first row above the title bar (PrintWindow capture)
        win2[1:40, :] = 117
        Image.fromarray(win2).save(cap)
        ok, msg = scene(rgba, cap)
        assert ok and "at (70,0)" not in msg and "PCSX2 capture" in msg, msg
        other = pic.copy()
        other[:448, :, :3] = 255 - other[:448, :, :3]
        wrong = os.path.join(d, "wrong.png")
        Image.fromarray(other[:448, :, :3]).save(wrong)
        ok, msg = scene(rgba, wrong)
        assert not ok and msg.startswith("FAIL"), msg
    print("checkpoint.py selftest: PASS")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hashdiff")
    h.add_argument("a")
    h.add_argument("b")
    h.add_argument("--from", dest="first", type=int)
    h.add_argument("--until", dest="last", type=int)
    s = sub.add_parser("scene")
    s.add_argument("rgba")
    s.add_argument("reference")
    s.add_argument("--tolerance", type=float, default=15.0)
    s.add_argument("--out")
    c = sub.add_parser("rgba2png")
    c.add_argument("rgba")
    c.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "hashdiff":
        st, msg = hashdiff(a.a, a.b, a.first, a.last)
        print(msg)
        return st
    if a.cmd == "scene":
        ok, msg = scene(a.rgba, a.reference, a.tolerance, a.out)
        print(msg)
        return 0 if ok else 1
    load_ours(a.rgba).save(a.out)
    print("wrote " + a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
