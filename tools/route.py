"""Named pad routes -> a PS2X_PAD_SCRIPT string, so nobody hand-writes tick lists.

    python tools/route.py <route|file.txt|@file> [--game config/game.json] [--start T] [--explain]
    python tools/route.py --list [--game ...]
    python tools/route.py --selftest

Macros and routes are per game: "pad" in config/game.json ({hold, gap, macros, routes}) plus
route files config/routes/<name>.txt (beside the game config). A route is a list of steps; a
file has one step per line (commas also split), '#' starts a comment. Steps:
    START  CIRCLE+12  UP|CROSS   press buttons (DualShock names, '|' = together, +hold ticks;
                                 default pad.hold) at the cursor
    wait N                       move the cursor N ticks on (relative to the previous press)
    at N                         put the cursor at absolute VBlank tick N (never backwards)
    3000:START+8                 a literal pad-script entry: press at tick 3000, cursor = 3000
    <name>                       a macro or route, expanded in place
    <name>+H                     the same with every press inside held H ticks
    <step>*N                     repeat (DOWN*3, confirm*2)
Two presses with no wait/at between them are pad.gap ticks apart. --start T shifts a route
that has no absolute tick of its own to begin at T (default: 0). Ticks are the runner's guest
VBlank ticks (~60/s, the same at any fast-forward factor). Routes marked unverified in the config
print a warning on stderr. --explain prints the timeline on stderr.
The argument may also be inline steps ("select_scenario, wait 300, confirm"), a raw pad script
("3000:START+8,..." is normalised and checked)
or @<file> / a .txt path (a route file). The run scripts (long-run.ps1, wall.ps1, fpsrun.py) call
this for -Route and for PS2X_PAD_SCRIPT=@<file>.
"""
import argparse, json, os, re, sys

BUTTONS = ["SELECT", "L3", "R3", "START", "UP", "RIGHT", "DOWN", "LEFT", "L2", "R2", "L1", "R1",
           "TRIANGLE", "CIRCLE", "CROSS", "SQUARE"]  # PadScript.cpp's names
DEFAULT_HOLD, DEFAULT_GAP = 8, 60
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GAME = os.path.join(os.path.dirname(HERE), "config", "game.json")


class RouteError(Exception):
    pass


def split_steps(text):
    """Steps of a route file / string: lines and commas, '#' comments dropped."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        out += [s.strip() for s in line.split(",") if s.strip()]
    return out


def buttons_of(spec):
    """'UP|cross' -> 'UP|CROSS', or None when it is not a button combination."""
    names = [n.strip().upper() for n in spec.split("|")]
    return "|".join(names) if all(n in BUTTONS for n in names) else None


class Pad:
    def __init__(self, cfg=None, game_path=None):
        pad = (cfg or {}).get("pad", {})
        self.hold = int(pad.get("hold", DEFAULT_HOLD))
        self.gap = int(pad.get("gap", DEFAULT_GAP))
        self.defs = {}   # name -> {"steps": [...], "kind": macro|route, ...}
        for kind in ("macros", "routes"):
            for name, d in pad.get(kind, {}).items():
                if not isinstance(d, dict):
                    d = {"steps": d}
                d = dict(d, kind=kind[:-1], source="config")
                if isinstance(d["steps"], str):
                    d["steps"] = split_steps(d["steps"])
                self.add(name, d)
        self.route_dir = os.path.join(os.path.dirname(os.path.abspath(game_path)), "routes") if game_path else None
        if self.route_dir and os.path.isdir(self.route_dir):
            for f in sorted(os.listdir(self.route_dir)):
                if f.endswith(".txt"):
                    self.add(f[:-4], self.load_file(os.path.join(self.route_dir, f)))

    def add(self, name, d):
        if buttons_of(name):
            raise RouteError(f"'{name}' is a button name; pick another macro/route name")
        if not re.fullmatch(r"[A-Za-z_][\w-]*", name):
            raise RouteError(f"bad macro/route name '{name}'")
        if name in self.defs:
            raise RouteError(f"'{name}' defined twice ({self.defs[name]['source']} and {d['source']})")
        self.defs[name] = d

    @staticmethod
    def load_file(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        first = next((l.lstrip("#").strip() for l in text.splitlines() if l.strip()), "")
        d = {"steps": split_steps(text), "kind": "route", "source": path,
             "notes": first if text.lstrip().startswith("#") else ""}
        if re.search(r"^#\s*unverified\b", text, re.M | re.I):
            d["verified"] = False
        return d

    # -------------------------------------------------------------- expansion
    def expand(self, steps, start=0, explain=None):
        st = {"cursor": start, "pressed": False, "events": [], "seen_abs": False}
        self._run(steps, st, [], explain)
        return st["events"]

    def _run(self, steps, st, stack, explain):
        for raw in steps:
            step = raw.strip()
            m = re.fullmatch(r"(.+?)\s*\*\s*(\d+)", step)
            reps = 1
            if m:
                step, reps = m.group(1), int(m.group(2))
                if reps < 1:
                    raise RouteError(f"'{raw}': repeat count must be >= 1")
            for _ in range(reps):
                self._one(step, raw, st, stack, explain)

    def _one(self, step, raw, st, stack, explain):
        low = step.lower()
        m = re.fullmatch(r"(wait|at)\s+(\d+)", low)
        if m:
            n = int(m.group(2))
            if m.group(1) == "wait":
                st["cursor"] += n
            else:
                if n < st["cursor"]:
                    raise RouteError(f"'{raw}': tick {n} is before the cursor ({st['cursor']})")
                st["cursor"] = n
            st["pressed"] = False
            return
        m = re.fullmatch(r"(?:(\d+)\s*:\s*)?([A-Za-z0-9_|\s]+?)(?:\s*\+\s*(\d+))?", step)
        btn = buttons_of(m.group(2)) if m else None
        if btn:
            if m.group(1) is not None:
                t = int(m.group(1))
                if t < st["cursor"]:
                    raise RouteError(f"'{raw}': tick {t} is before the cursor ({st['cursor']})")
                st["cursor"] = t
            elif st["pressed"]:
                st["cursor"] += self.gap
            hold = st["hold"] if st.get("hold") else int(m.group(3)) if m.group(3) else self.hold
            if hold < 1:
                raise RouteError(f"'{raw}': hold must be >= 1")
            if st["events"] and st["cursor"] < st["events"][-1][0] + st["events"][-1][2]:
                prev = st["events"][-1]
                if set(btn.split("|")) & set(prev[1].split("|")):
                    raise RouteError(f"'{raw}' at {st['cursor']} overlaps the previous {prev[1]} press "
                                     f"({prev[0]}+{prev[2]}): the game would see one press")
            st["events"].append((st["cursor"], btn, hold))
            st["pressed"] = True
            if explain is not None:
                explain.append(f"{st['cursor']:7d}  {btn}+{hold}   {' > '.join(stack) or '(top)'}")
            return
        m = re.fullmatch(r"([A-Za-z_][\w-]*)(?:\s*\+\s*(\d+))?", step.strip())
        name = m.group(1) if m else step.strip()
        if name not in self.defs:
            raise RouteError(f"unknown step '{raw}' (not a button, wait/at, or a macro/route: "
                             f"{', '.join(sorted(self.defs)) or 'none defined'})")
        if name in stack:
            raise RouteError(f"'{name}' refers to itself ({' > '.join(stack + [name])})")
        saved = st.get("hold")
        if m and m.group(2):
            st["hold"] = int(m.group(2))  # confirm+12: every press inside holds 12 ticks
        try:
            self._run(self.defs[name]["steps"], st, stack + [name], explain)
        finally:
            st["hold"] = saved

    def resolve(self, arg):
        """A route/macro name, a route file (path or @path), or a raw pad script -> (steps, def)."""
        if arg.startswith("@"):
            return None, self.load_file(arg[1:])
        if arg in self.defs:
            return arg, self.defs[arg]
        if arg.lower().endswith(".txt") or os.path.isfile(arg):
            if not os.path.isfile(arg):
                raise RouteError(f"route file not found: {arg}")
            return None, self.load_file(arg)
        steps = split_steps(arg)
        if len(steps) == 1 and re.fullmatch(r"[A-Za-z_][\w-]*", steps[0]) and not buttons_of(steps[0]):
            raise RouteError(f"unknown route '{arg}' (have: {', '.join(sorted(self.defs)) or 'none'}; "
                             f"or give a file / @file / inline steps / a raw pad script)")
        # Inline steps ("select_scenario, wait 300, confirm") or a raw pad script.
        return None, {"steps": steps, "kind": "inline", "source": "command line"}


def to_script(events):
    return ",".join(f"{t}:{b}+{h}" for t, b, h in events)


def load_game(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def route_script(arg, game=DEFAULT_GAME, start=0, explain=None, warn=None):
    pad = Pad(load_game(game), game)
    name, d = pad.resolve(arg)
    if d.get("verified") is False and warn is not None:
        warn.append(f"route {name or d['source']} is UNVERIFIED: {d.get('notes', '')}".rstrip(": "))
    return to_script(pad.expand(d["steps"], start, explain))


def listing(pad):
    lines = [f"pad: hold {pad.hold}, gap {pad.gap} ticks"]
    for kind in ("macro", "route"):
        lines.append(f"{kind}s:")
        for n, d in pad.defs.items():
            if d["kind"] != kind:
                continue
            ver = d.get("verified")
            tag = "" if kind == "macro" else ("  [unverified]" if ver is False else f"  [verified: {ver}]" if ver else "")
            try:
                s = to_script(pad.expand(d["steps"]))
            except RouteError as e:
                s = f"ERROR {e}"
            src = "" if d["source"] == "config" else f"  ({os.path.relpath(d['source'])})"
            lines.append(f"  {n:<16} {', '.join(d['steps'])}{tag}{src}")
            lines.append(f"  {'':<16} -> {s}")
            if d.get("notes") or d.get("reaches"):
                lines.append(f"  {'':<16}    {d.get('reaches', '')}{' - ' if d.get('reaches') and d.get('notes') else ''}{d.get('notes', '')}")
    return lines


def selftest():
    import tempfile
    ok = True

    def check(cond, what):
        nonlocal ok
        print(("ok:   " if cond else "FAIL: ") + what)
        ok = ok and bool(cond)

    cfg = {"pad": {"hold": 8, "gap": 60, "macros": {
        "skip_movie": ["at 3000", "START"], "title_start": ["START"], "confirm": ["CIRCLE"],
        "back": "CROSS"},
        "routes": {"select_scenario": {"steps": ["skip_movie", "wait 400", "title_start"]},
                   "guidance_map": {"steps": ["select_scenario", "wait 200", "DOWN", "confirm"], "verified": False},
                   "loop": ["loop"]}}}
    with tempfile.TemporaryDirectory() as tmp:
        gp = os.path.join(tmp, "game.json")
        os.makedirs(os.path.join(tmp, "routes"))
        with open(gp, "w") as f:
            json.dump(cfg, f)
        with open(os.path.join(tmp, "routes", "ff.txt"), "w") as f:
            f.write("# unverified: file route\nselect_scenario   # the verified part\nwait 120\nDOWN*2, confirm+4\n")
        p = Pad(load_game(gp), gp)
        ex = lambda a, s=0: to_script(p.expand(p.resolve(a)[1]["steps"], s))
        check(ex("select_scenario") == "3000:START+8,3400:START+8", "select_scenario = t2padon's verified script")
        check(ex("guidance_map") == "3000:START+8,3400:START+8,3600:DOWN+8,3660:CIRCLE+8", f"wait + default gap: {ex('guidance_map')}")
        check(ex("ff") == "3000:START+8,3400:START+8,3520:DOWN+8,3580:DOWN+8,3640:CIRCLE+4", f"route file, repeat, hold: {ex('ff')}")
        check(p.defs["ff"]["verified"] is False, "'# unverified' in a route file marks it unverified")
        check(ex("@" + os.path.join(tmp, "routes", "ff.txt")) == ex("ff"), "@file = the same route")
        check(ex("confirm", 500) == "500:CIRCLE+8", "--start shifts a route without an absolute tick")
        check(ex("confirm*2") == "0:CIRCLE+8,60:CIRCLE+8", "macro repeat")
        check(ex("100:start+5,200:CROSS") == "100:START+5,200:CROSS+8", "raw pad script passes through (normalised)")
        warn = []
        route_script("guidance_map", gp, warn=warn)
        check(warn and "UNVERIFIED" in warn[0], "unverified route warns")
        for bad, why in [("loop", "itself"), ("nosuch", "unknown route")]:
            try:
                ex(bad); raised = None
            except RouteError as e:
                raised = str(e)
            check((why is None and raised is None) or (why and raised and why in raised), f"error check '{bad}': {raised}")
        for steps, why in [(["at 100", "at 50"], "before the cursor"), (["START+20", "wait 10", "START"], "overlaps"),
                           (["FOO"], "unknown step"), (["START*0"], "repeat")]:
            try:
                p.expand(steps); raised = None
            except RouteError as e:
                raised = str(e)
            check(raised and why in raised, f"rejects {steps}: {raised}")
        try:
            Pad({"pad": {"macros": {"START": ["CIRCLE"]}}}); raised = None
        except RouteError as e:
            raised = str(e)
        check(raised and "button name" in raised, "a macro may not shadow a button name")
    # The real config expands cleanly.
    if os.path.exists(DEFAULT_GAME):
        try:
            p = Pad(load_game(DEFAULT_GAME), DEFAULT_GAME)
            bad = []
            for n, d in p.defs.items():
                try:
                    p.expand(d["steps"])
                except RouteError as e:
                    bad.append(f"{n}: {e}")
            check(not bad, f"{os.path.relpath(DEFAULT_GAME)}: {len(p.defs)} macros/routes expand" + (f" ({'; '.join(bad)})" if bad else ""))
        except RouteError as e:
            check(False, f"game config: {e}")
    print("selftest", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("route", nargs="?")
    ap.add_argument("--game", default=os.environ.get("PS2X_GAME_CONFIG") or DEFAULT_GAME)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    try:
        if a.list:
            print("\n".join(listing(Pad(load_game(a.game), a.game))))
            return 0
        if not a.route:
            ap.error("give a route (see --list)")
        explain, warn = ([] if a.explain else None), []
        s = route_script(a.route, a.game, a.start, explain, warn)
    except (RouteError, OSError, ValueError) as e:
        print(f"route: {e}", file=sys.stderr)
        return 2
    for w in warn:
        print(f"route: WARNING {w}", file=sys.stderr)
    if explain:
        print("   tick  press   from", file=sys.stderr)
        print("\n".join(explain), file=sys.stderr)
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
