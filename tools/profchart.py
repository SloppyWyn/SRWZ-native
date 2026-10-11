#!/usr/bin/env python3
"""Bar chart (PNG) of a PS2X_HOST_PROFILE report, for one host thread.

    python tools/profchart.py <report.txt> -o chart.png [--thread GameThread] [--top 12]
        [--group "VU1 interpreter=VU1Interpreter::run" --group "IOP=IopEmulator::Impl::runCycles" ...]
        [--title "s01 battle map"] [--note "PC light (video playing)"]
    python tools/profchart.py --selftest

Without --group: the thread's top self-time functions. With --group NAME=FUNCTION (repeatable):
one bar per group at FUNCTION's inclusive share, plus "other" for the rest of the samples.
Needs Pillow only.
"""
import argparse
import os
import re
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROW = re.compile(r"\[hostprof\]\s+(self|incl)\s+([\d.]+)%\s+(.*)$")
THREAD = re.compile(r'\[hostprof\] thread (\d+) "(.*)": (\d+) samples')


def parse(path):
    """{thread name or id: {'samples': n, 'self': [(name, pct)], 'incl': {name: pct}}}"""
    threads, cur = {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        m = THREAD.search(line)
        if m:
            key = m.group(2) if m.group(2) not in ("", "?") else m.group(1)
            cur = threads.setdefault(key, {"samples": int(m.group(3)), "self": [], "incl": {}})
            continue
        m = ROW.search(line)
        if m and cur is not None:
            kind, pct, name = m.group(1), float(m.group(2)), m.group(3).strip()
            if kind == "self":
                cur["self"].append((name, pct))
            else:
                cur["incl"][name] = pct
    return threads


def short(name, n=46):
    name = re.sub(r"`anonymous namespace'::", "", name)
    return name if len(name) <= n else name[: n - 1] + "..."


def draw(bars, out, title, note):
    font = ImageFont.load_default()
    w, rowh, left = 900, 26, 340
    h = 70 + rowh * len(bars) + 30
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.text((16, 12), title, fill="black", font=font)
    if note:
        d.text((16, 30), note, fill=(90, 90, 90), font=font)
    top = max([p for _, p in bars] + [1.0])
    scale = (w - left - 80) / max(top, 1.0)
    for i, (name, pct) in enumerate(bars):
        y = 60 + i * rowh
        d.text((16, y + 6), short(name), fill="black", font=font)
        d.rectangle([left, y + 4, left + max(1, int(pct * scale)), y + rowh - 6], fill=(66, 110, 180))
        d.text((left + int(pct * scale) + 8, y + 6), "%.1f%%" % pct, fill="black", font=font)
    img.save(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", nargs="?")
    ap.add_argument("-o", "--out")
    ap.add_argument("--thread", default="GameThread")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--group", action="append", default=[])
    ap.add_argument("--title", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.report or not a.out:
        ap.error("report and -o are required")
    threads = parse(a.report)
    t = threads.get(a.thread)
    if t is None:
        print("profchart: no thread %r (have: %s)" % (a.thread, ", ".join(threads)), file=sys.stderr)
        return 2
    if a.group:
        bars = []
        for g in a.group:
            label, fn = g.split("=", 1)
            bars.append((label, t["incl"].get(fn, 0.0)))
        rest = max(0.0, 100.0 - sum(p for _, p in bars))
        bars.append(("other", rest))
    else:
        bars = t["self"][: a.top]
    title = a.title or "%s: %d samples" % (a.thread, t["samples"])
    draw(bars, a.out, title, a.note)
    print("wrote %s (%d bars)" % (a.out, len(bars)))
    return 0


def selftest():
    report = (
        "[hostprof] report: 2 threads sampled\n"
        '[hostprof] thread 7 "GameThread": 1000 samples\n'
        "[hostprof]   self  40.0%  VU1Interpreter::commitReadyPipelines\n"
        "[hostprof]   self  10.0%  `anonymous namespace'::shadeSpriteRows<2>\n"
        "[hostprof]   incl 100.0%  EeScheduler::run\n"
        "[hostprof]   incl  85.0%  VU1Interpreter::run\n"
        "[hostprof]   incl  10.0%  ps2x::iop::detail::IopEmulator::Impl::runCycles\n"
        '[hostprof] thread 9 "?": 1000 samples\n'
        "[hostprof]   self  99.0%  ntdll\n")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.txt")
        open(p, "w").write(report)
        th = parse(p)
        assert th["GameThread"]["samples"] == 1000 and th["GameThread"]["incl"]["VU1Interpreter::run"] == 85.0, th
        assert "9" in th and th["9"]["self"][0] == ("ntdll", 99.0), th
        out = os.path.join(d, "c.png")
        assert main([p, "-o", out, "--group", "VU1=VU1Interpreter::run", "--group",
                     "IOP=ps2x::iop::detail::IopEmulator::Impl::runCycles"]) == 0
        with Image.open(out) as im:
            assert im.size[0] == 900 and im.size[1] > 100, im.size
        assert main([p, "-o", out]) == 0
        assert main([p, "-o", out, "--thread", "nope"]) == 2
    print("profchart selftest: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
