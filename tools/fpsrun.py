"""Timed run of the game runner that measures the guest's frame rate over host time.

    python tools/fpsrun.py --exe <ps2EntryRunner.exe> --elf <SLPS_258.87> --iso <srwz.iso>
        --wd <working dir> --out <prefix> [--seconds 150] [--skip-tick 3000]
        [--window 90:150] [--route <name|file|@file|steps>] [--game config/game.json] [--sound] [--selftest]

Starts the runner with no audio device (PS2X_NO_AUDIO=1 and PS2X_MUTE=1; --sound plays sound) with PS2X_TRACE_STUBS=scePadRead (the game reads the pad once per
frame of its main loop, so reads per second = game fps) and, by default, a scripted START
at VBlank tick 3000, which skips the opening movie straight into the title/menu loop (the
fastest route on runners without fast-forward or snapshots). --route plays a pad route instead
(tools/route.py; replaces --skip-tick), and an inherited PS2X_PAD_SCRIPT=@<file> is expanded the
same way. Every stdout/stderr line is
stamped with host seconds since start and kept in <prefix>.out.txt / <prefix>.err.txt;
<prefix>.fps.txt has reads per 5 s bucket, the VBlank rate from [run:tick] lines and a
summary over --window (host seconds). The window is closed with WM_CLOSE after --seconds,
then killed if still alive after 10 s. The working directory is where the runtime writes
ps2_log.txt when built with PS2X_ENABLE_AGRESSIVE_LOGS; its final size is reported.
"""
import argparse, os, re, subprocess, sys, threading, time

TICK = re.compile(r"\[run:tick\].*?hostMs=(\d+).*?vsync=(\d+)")


def quiet():
    """Popen options like tools/launch.ps1: no console window (CREATE_NO_WINDOW), and a game
    window shown without activation (SW_SHOWNOACTIVATE), never in front of Wyn."""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 4  # SW_SHOWNOACTIVATE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def summarise(err_lines, out_lines, lo, hi, total):
    reads = [t for t, l in err_lines if l.startswith("[stub] scePadRead@") and "(" in l]
    ticks = [(t, int(m.group(1)), int(m.group(2))) for t, l in out_lines for m in [TICK.search(l)] if m]
    rows = []
    for b in range(0, int(total) + 5, 5):
        n = sum(1 for t in reads if b <= t < b + 5)
        rows.append(f"{b:4d}-{b + 5:<4d} s  reads {n:4d}  ({n / 5:5.1f} fps)")
    win = [t for t in reads if lo <= t < hi]
    span = min(hi, total) - lo
    fps = len(win) / span if span > 0 else 0.0
    tw = [x for x in ticks if lo <= x[0] < hi]
    vrate = (tw[-1][2] - tw[0][2]) / (tw[-1][0] - tw[0][0]) if len(tw) >= 2 and tw[-1][0] > tw[0][0] else 0.0
    return fps, len(win), span, vrate, rows


def selftest():
    err = [(t * 0.25, "[stub] scePadRead@0x1309b0(a0=0x0) ra=0x18d3ec") for t in range(400)]
    err += [(1.0, "[stub] scePadRead@0x1309b0 -> v0=0x1")]
    out = [(t, f"[run:tick] tick=1 hostMs={t * 1000} eeMs=0 vsync={t * 60} dma=0") for t in range(100)]
    fps, n, span, vrate, rows = summarise(err, out, 20, 80, 100)
    ok = abs(fps - 4.0) < 1e-9 and n == 240 and abs(vrate - 60) < 1e-9 and "20.0 fps" not in rows[0]
    print("selftest", "passed" if ok else f"FAILED fps={fps} n={n} vrate={vrate}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe"); ap.add_argument("--elf"); ap.add_argument("--iso"); ap.add_argument("--wd")
    ap.add_argument("--out"); ap.add_argument("--seconds", type=float, default=150)
    ap.add_argument("--skip-tick", type=int, default=3000)
    ap.add_argument("--window", default="90:150")
    ap.add_argument("--route", default="", help="pad route for PS2X_PAD_SCRIPT (tools/route.py)")
    ap.add_argument("--game", default=None, help="game config for --route")
    ap.add_argument("--sound", action="store_true", help="open the audio device with sound on")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    lo, hi = (float(x) for x in a.window.split(":"))
    audio = "0" if a.sound else "1"
    env = dict(os.environ, PS2X_NO_AUDIO=audio, PS2X_MUTE=audio, PS2X_TRACE_STUBS="scePadRead")
    import route as routes  # tools/route.py
    game = a.game or os.environ.get("PS2X_GAME_CONFIG") or routes.DEFAULT_GAME
    warn = []
    if a.route:
        env["PS2X_PAD_SCRIPT"] = routes.route_script(a.route, game, warn=warn)
    elif env.get("PS2X_PAD_SCRIPT", "").startswith("@"):
        env["PS2X_PAD_SCRIPT"] = routes.route_script(env["PS2X_PAD_SCRIPT"], game, warn=warn)
    elif a.skip_tick > 0:
        env["PS2X_PAD_SCRIPT"] = f"{a.skip_tick}:START+8"
    for w in warn:
        print("route: WARNING", w, file=sys.stderr)
    os.makedirs(a.wd, exist_ok=True)
    log = os.path.join(a.wd, "ps2_log.txt")
    if os.path.exists(log):
        os.remove(log)
    t0 = time.perf_counter()
    p = subprocess.Popen([a.exe, a.elf, a.iso], cwd=a.wd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, bufsize=1 << 16, **quiet())
    out_lines, err_lines = [], []

    def pump(stream, sink):
        for raw in iter(stream.readline, b""):
            sink.append((time.perf_counter() - t0, raw.decode("utf-8", "replace").rstrip("\r\n")))

    th = [threading.Thread(target=pump, args=(p.stdout, out_lines), daemon=True),
          threading.Thread(target=pump, args=(p.stderr, err_lines), daemon=True)]
    for t in th:
        t.start()
    try:
        p.wait(timeout=a.seconds)
        how = f"exited by itself, code {p.returncode}"
    except subprocess.TimeoutExpired:
        total = time.perf_counter() - t0
        subprocess.run(["taskkill", "/PID", str(p.pid)], capture_output=True, **quiet())
        try:
            p.wait(timeout=10); how = "closed"
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(); how = "killed"
    total = min(time.perf_counter() - t0, a.seconds)
    for t in th:
        t.join(timeout=5)
    with open(a.out + ".out.txt", "w", encoding="utf-8") as f:
        f.writelines(f"{t:9.3f} {l}\n" for t, l in out_lines)
    with open(a.out + ".err.txt", "w", encoding="utf-8") as f:
        f.writelines(f"{t:9.3f} {l}\n" for t, l in err_lines)
    fps, n, span, vrate, rows = summarise(err_lines, out_lines, lo, hi, total)
    logsize = os.path.getsize(log) if os.path.exists(log) else 0
    skip = next((f"{t:.1f} s" for t, l in out_lines + err_lines if "[pad:script] tick=" in l), "never")
    summary = [f"exe {a.exe}", f"wd  {a.wd}", f"run {total:.1f} s, {how}",
               f"START delivered at {skip}",
               f"window {lo:.0f}-{min(hi, total):.0f} s: {n} pad reads = {fps:.2f} game fps; VBlank {vrate:.1f}/s",
               f"ps2_log.txt {logsize / 1e6:.0f} MB"]
    with open(a.out + ".fps.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(summary + [""] + rows) + "\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
