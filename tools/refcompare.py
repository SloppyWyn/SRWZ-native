"""Compare a PCSX2 reference screenshot with our GS dump frames.
    python tools/refcompare.py <reference.jpg> <dump dir> [-o out.png] [--top N]
    python tools/refcompare.py --selftest
The reference is a full PCSX2 window capture: the title bar and menu are skipped, the
window frame and black borders trimmed, leaving the 4:3 picture (a dark picture, whose
edges can't be told from the borders, falls back to the centred 4:3 fit of the client
area). Every gsdump_<tick>.rgba in the dump dir (picture in its top rows, size from
presented=WxH in the .txt) is scaled with the reference to a 64x48 grayscale thumbnail
and scored by mean absolute difference (0 = identical, 255 = opposite). Lower is better.
"""
import sys, os, re, glob, argparse
import numpy as np
from PIL import Image

THUMB = (64, 48)
DARK = 12  # mean luma below this counts as border


def client_area(g):
    """(top, bottom, left, right) of the window's client area in a grayscale array."""
    H, W = g.shape
    # Window frame: thin uniform bright lines at the outer edges, at most 8 px.
    def frame(v): return v.mean() > DARK and v.std() < 6
    l, r, b = 0, W, H
    while l < 8 and frame(g[H // 4:, l]): l += 1
    while W - r < 8 and frame(g[H // 4:, r - 1]): r -= 1
    while H - b < 8 and frame(g[b - 1, l:r]): b -= 1
    # Chrome (title bar, menu): grey across the whole width. The client area starts at the
    # first row whose side strips are dark (letterbox band or pillarbox border).
    side = max(4, (r - l) // 20)
    t = 0
    for y in range(int(H * 0.2)):
        if g[y, l:l + side].mean() < DARK and g[y, r - side:r].mean() < DARK:
            t = y
            break
    return t, b, l, r


def fit43(t, b, l, r):
    h, w = b - t, r - l
    if w * 3 > h * 4:
        pw = h * 4 // 3
        x = l + (w - pw) // 2
        return t, b, x, x + pw
    ph = w * 3 // 4
    y = t + (h - ph) // 2
    return y, y + ph, l, r


def crop_box(g):
    """(top, bottom, left, right) of the picture inside a PCSX2 window capture."""
    t, b, l, r = client_area(g)
    sub = g[t:b, l:r]
    rows = np.where(sub.mean(1) > DARK)[0]
    cols = np.where(sub.mean(0) > DARK)[0]
    fit = fit43(t, b, l, r)
    if len(rows) and len(cols):
        box = (t + rows[0], t + rows[-1] + 1, l + cols[0], l + cols[-1] + 1)
        h, w = box[1] - box[0], box[3] - box[2]
        # Trust the trimmed box only if it is 4:3 and fills the client area one way.
        if h > 0 and abs(w / h - 4 / 3) < 0.05 and (w >= 0.97 * (r - l) or h >= 0.97 * (b - t)):
            return box
    return fit


def load_reference(path):
    img = Image.open(path).convert('RGB')
    g = np.asarray(img.convert('L')).astype(np.float32)
    t, b, l, r = crop_box(g)
    return img.crop((l, t, r, b)), (t, b, l, r)


def load_dump(rgba_path):
    txt = rgba_path[:-5] + '.txt'
    w, h = 640, 448
    if os.path.exists(txt):
        m = re.search(r'presented=(\d+)x(\d+)', open(txt).read())
        if m: w, h = int(m.group(1)), int(m.group(2))
    raw = np.fromfile(rgba_path, dtype=np.uint8)
    # A frame with no presented picture (presented=0x0) or a short/odd .rgba is a black frame
    # (blank), not a crash.
    w, h = min(w, 640), min(h, 512)
    if w <= 0 or h <= 0 or raw.size != 512 * 640 * 4:
        return Image.new('RGB', (640, 448))
    return Image.fromarray(raw.reshape(512, 640, 4)[:h, :w, :3].copy())


def thumb(img):
    return np.asarray(img.convert('L').resize(THUMB, Image.BILINEAR)).astype(np.float32)


def score_dir(ref_img, d):
    rt = thumb(ref_img)
    res = []
    for p in glob.glob(os.path.join(d, 'gsdump_*.rgba')):
        tick = int(re.findall(r'(\d+)', os.path.basename(p))[0])
        res.append((float(np.abs(thumb(load_dump(p)) - rt).mean()), tick, p))
    return sorted(res)


def dump_ticks(d):
    out = {}
    for p in glob.glob(os.path.join(d, 'gsdump_*.rgba')):
        out[int(re.findall(r'(\d+)', os.path.basename(p))[0])] = p
    return out


def compare_dirs(run, base, window=45, threshold=10.0):
    """Score each run dump against the baseline frames within +-window ticks, taking the best.
    Fades move a lot between two dumps 30 ticks apart, so blends of each pair of consecutive
    baseline frames (10% steps) count as candidates too, the blend's tick interpolated.
    Returns [(tick, score or None, base tick or None, status)], status PASS, FAIL or EDGE
    (fails, but its window runs past the baseline's first/last frame, so the frame it should
    match may simply not be in the baseline: reported, not counted). No frame near = FAIL,
    unless the tick lies beyond the baseline's range altogether (EDGE)."""
    bt = {t: thumb(load_dump(p)) for t, p in dump_ticks(base).items()}
    ts = sorted(bt)
    cands = [(t, bt[t]) for t in ts]
    for a, b in zip(ts, ts[1:]):
        if b - a <= 2 * window:
            for k in range(1, 10):
                f = k / 10
                cands.append((a + (b - a) * f, bt[a] * (1 - f) + bt[b] * f))
    res = []
    for tick, p in sorted(dump_ticks(run).items()):
        near = [(t, c) for t, c in cands if abs(t - tick) <= window]
        if not near:
            # Beyond the baseline's range: nothing to compare, so not counted (EDGE).
            outside = bool(ts) and (tick + window < ts[0] or tick - window > ts[-1])
            res.append((tick, None, None, 'EDGE' if outside else 'FAIL')); continue
        th = thumb(load_dump(p))
        s, t = min(((float(np.abs(th - c).mean()), t) for t, c in near), key=lambda x: x[0])
        status = 'PASS' if s <= threshold else 'EDGE' if (tick - window < ts[0] or tick + window > ts[-1]) else 'FAIL'
        res.append((tick, s, round(t), status))
    return res


# ---- content-order regression (default for --dirs; --strict = compare_dirs above) ----
BLANK_STD = 2.0     # thumbnail luma std below this = blank (uniform) screen...
BLANK_GREY = 16.0   # ...when its mean colour is grey (channels within this)
MOVIE_ENT = 3.0     # luma-histogram entropy (bits, 64 bins) of a movie/natural picture; screens
                    # of text and logos sit at 0.5..2.5
BLEND_GAP = 90      # blend consecutive baseline frames (fades) only when this close in ticks
NCC_MIN = 0.8       # a screen match also needs this normalised correlation on a 128x96 thumbnail:
                    # mostly-black screens (a line of text) are all within a few mad of each
                    # other; the correlation tells their texts apart (same 0.92+, other 0.4..0.65)
FADE_NCC = 0.98     # ...and at or above this correlation it is the same screen at another
                    # brightness (a fade), whatever the mad
SAMPLE_GAP = 180    # baseline frames further apart than this leave the stretch between unsampled
STRUCT = (128, 96)


def sthumb(img):
    return np.asarray(img.convert('L').resize(STRUCT, Image.BILINEAR)).astype(np.float32)


def ncc(a, b):
    a = a - a.mean(); b = b - b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum()) / d if d > 0 else 0.0


def entropy(th):
    h = np.histogram(th, bins=64, range=(0, 256))[0].astype(np.float64)
    h = h[h > 0] / h.sum()
    return float(-(h * np.log2(h)).sum())


def is_blank(img):
    # Uniform and grey: black gaps, white flashes, the grey steps of a fade (a uniform colour,
    # e.g. a magenta screen, is not blank).
    th = thumb(img)
    if th.std() >= BLANK_STD: return False
    m = np.asarray(img.resize((16, 12), Image.BILINEAR)).reshape(-1, 3).astype(np.float32).mean(0)
    return float(m.max() - m.min()) < BLANK_GREY


def mpeg_log(d):
    """[(picture, tick, fnv)] from PS2X_MPEG_HASH lines ([MPEG:hash] picture=N tick=T ... fnv=H)
    in <dir>/mpeghash.txt, else the run's stdout log <runs>/<name>.out.txt next to <name>dump,
    else the log named by a baseline's SOURCE.txt. [] when the run did not log hashes."""
    d = os.path.abspath(d)
    cands = [os.path.join(d, 'mpeghash.txt')]
    if d.endswith('dump'):
        cands.append(d[:-4] + '.out.txt')
    src = os.path.join(d, 'SOURCE.txt')
    if os.path.exists(src):
        m = re.search(r'from (.+?dump) on ', open(src, encoding='utf-8', errors='replace').read())
        if m: cands.append(m.group(1)[:-4] + '.out.txt')
    for c in cands:
        if os.path.exists(c):
            rows = [(int(a), int(b), h) for a, b, h in re.findall(
                r'\[MPEG:hash\] picture=(\d+) tick=(\d+) .*?fnv=([0-9a-f]+)',
                open(c, encoding='utf-8', errors='replace').read())]
            if rows: return rows
    return []


def movie_spans(log):
    """Playback spans [(t0, t1, [(picture, tick)])] of a hash log: consecutive lines with rising
    pictures no further apart than 3x the typical spacing (a new movie restarts at picture 0).
    t1 runs one typical spacing past the last logged picture."""
    if not log: return []
    dt = sorted(b[1] - a[1] for a, b in zip(log, log[1:]) if b[0] > a[0]) or [600]
    step = dt[len(dt) // 2]
    spans, cur = [], [log[0]]
    for a, b in zip(log, log[1:]):
        if b[0] > a[0] and b[1] - a[1] <= 3 * step: cur.append(b)
        else: spans.append(cur); cur = [b]
    spans.append(cur)
    return [(s[0][1], s[-1][1] + step, [(p, t) for p, t, _ in s]) for s in spans]


def picture_at(span, tick):
    """Picture index served at a tick inside a span (linear between the logged pictures)."""
    pts = span[2]
    if len(pts) == 1 or tick <= pts[0][1]: return float(pts[0][0])
    for (p0, t0), (p1, t1) in zip(pts, pts[1:]):
        if tick <= t1: return p0 + (p1 - p0) * (tick - t0) / max(1, t1 - t0)
    (p0, t0), (p1, t1) = pts[-2], pts[-1]
    return p1 + (p1 - p0) * (tick - t1) / max(1, t1 - t0)


def tick_of_picture(spans, pic, movie_no):
    """Baseline tick at which picture `pic` of its movie_no-th playback was served (or None)."""
    if movie_no >= len(spans): return None
    pts = spans[movie_no][2]
    step = pts[-1][0] - pts[-2][0] if len(pts) > 1 else 0
    if pic < pts[0][0] or pic > pts[-1][0] + step: return None
    for (p0, t0), (p1, t1) in zip(pts, pts[1:]):
        if pic <= p1: return t0 + (t1 - t0) * (pic - p0) / max(1, p1 - p0)
    return float(spans[movie_no][1])


def compare_seq(run, base, threshold=10.0, max_blank=900):
    """Content-order regression: the run shows the baseline's screens in the baseline's order,
    whatever its timing. Each run dump is classified against ALL baseline frames:
      screen - mad <= threshold to some baseline frame (or a fade blend of two close ones);
      blank  - (near) black: a wildcard with no order constraint, but a blank stretch longer than
               max_blank ticks fails (a hang to black);
      movie  - no screen matches, but the picture is a movie frame. With PS2X_MPEG_HASH logs on
               both sides: the dump lies in a playback span and its position is the baseline tick
               of the same picture (and the logged hashes must agree). Without: luma entropy >=
               MOVIE_ENT and it may stand for any baseline movie frame (entropy >= MOVIE_ENT,
               matching neither neighbouring baseline frame);
      anim   - (after the chain) an unplaced dump between placed ones bracketing a dynamic
               baseline frame (an animation at another phase), or inside the movie;
      none   - nothing matches.
    The positions (baseline ticks) must be non-decreasing in run order: the longest chain (most
    dumps, then least mad) is kept; a matched dump left out is 'out of order' (FAIL). An unmatched
    dump before the chain / after it is EDGE when the chain starts at the baseline's first screen /
    ends at its last, or when it lies in a stretch the baseline never sampled (frames more than
    SAMPLE_GAP apart): content the baseline does not cover. Else FAIL.
    Returns (rows, notes), rows [(tick, mad or None, base tick or None, class, status, why)]."""
    bimg = {t: load_dump(p) for t, p in dump_ticks(base).items()}
    bfr = {t: thumb(i) for t, i in bimg.items()}
    bst = {t: sthumb(i) for t, i in bimg.items()}
    bblank = {t: is_blank(i) for t, i in bimg.items()}
    ts = sorted(bfr)
    notes = []

    def mad(x, y): return float(np.abs(x - y).mean())
    def same(a, sa, b, sb):
        r = ncc(sa, sb)
        return (mad(a, b) <= threshold and r >= NCC_MIN) or r >= FADE_NCC
    cands_b = [(float(t), bfr[t], bst[t]) for t in ts if not bblank[t]]
    for a, b in zip(ts, ts[1:]):
        if b - a <= BLEND_GAP and not (bblank[a] and bblank[b]):
            for k in range(1, 10):
                f = k / 10
                cands_b.append((a + (b - a) * f, bfr[a] * (1 - f) + bfr[b] * f, bst[a] * (1 - f) + bst[b] * f))
    # Dynamic baseline frames: not blank, matching neither neighbour (animation, movie); the
    # rich ones (entropy >= MOVIE_ENT) are the baseline's movie frames.
    bdyn = []
    for i, t in enumerate(ts):
        th = bfr[t]
        if bblank[t]: continue
        nb = [ts[j] for j in (i - 1, i + 1) if 0 <= j < len(ts)]
        if all(mad(th, bfr[n]) > threshold or ncc(bst[t], bst[n]) < NCC_MIN for n in nb): bdyn.append(t)
    bmovie = [t for t in bdyn if entropy(bfr[t]) >= MOVIE_ENT]
    cpos = np.array([c[0] for c in cands_b])
    # (no candidates when every baseline frame is blank: keep the empty arrays' trailing shapes so
    # the per-dump vector maths below yields empty score lists, not a shape mismatch)
    cth = np.stack([c[1] for c in cands_b]) if cands_b else np.zeros((0,) + THUMB[::-1], np.float32)
    cst = (np.stack([c[2] for c in cands_b]).reshape(len(cands_b), -1) if cands_b
           else np.zeros((0, STRUCT[0] * STRUCT[1]), np.float32))
    cst = cst - cst.mean(1, keepdims=True)
    cnorm = np.sqrt((cst * cst).sum(1))
    rlog, blog = mpeg_log(run), mpeg_log(base)
    hashed = bool(rlog and blog)
    rspans, bspans = movie_spans(rlog), movie_spans(blog)
    if hashed:
        def keyed(log, spans):
            out, k = {}, 0
            for p, t, h in log:
                while k + 1 < len(spans) and t >= spans[k + 1][0]: k += 1
                out[(k, p)] = h
            return out
        rk, bk = keyed(rlog, rspans), keyed(blog, bspans)
        common = sorted(set(rk) & set(bk))
        bad = [c for c in common if rk[c] != bk[c]]
        notes.append(f'mpeg hashes on both sides: {len(common)} pictures in common, {len(bad)} differ'
                     + (f' (first: movie {bad[0][0]} picture {bad[0][1]})' if bad else ''))
        if bad: notes.append('FAIL: movie pictures differ')
    elif rlog or blog:
        notes.append('mpeg hashes on one side only: movie frames judged by picture statistics')

    rows = []
    for tick, p in sorted(dump_ticks(run).items()):
        img = load_dump(p)
        th = thumb(img)
        if is_blank(img):
            rows.append((tick, None, None, 'blank', [])); continue
        st = sthumb(img).ravel()
        st = st - st.mean()
        mads = np.abs(cth - th).mean((1, 2))
        den = cnorm * float(np.sqrt((st * st).sum()))
        nccs = np.where(den > 0, cst @ st / np.where(den > 0, den, 1), 0.0)
        sc = list(zip(cpos.tolist(), mads.tolist(), nccs.tolist()))
        good = [(t, s) for t, s, r in sc if (s <= threshold and r >= NCC_MIN) or r >= FADE_NCC]
        best = min(good or [x[:2] for x in sc], key=lambda x: x[1]) if sc else (None, None)
        if hashed:
            # Inside a logged playback: the movie position is exact, screens don't count.
            movie = []
            for k, sp in enumerate(rspans):
                if sp[0] <= tick <= sp[1]:
                    bt = tick_of_picture(bspans, picture_at(sp, tick), k)
                    if bt is not None: movie = [(bt, 0.0)]
            if movie:
                rows.append((tick, best[1], movie[0][0], 'movie', movie)); continue
            if good:
                rows.append((tick, best[1], best[0], 'screen', good)); continue
        else:
            # A rich picture may stand for any baseline movie frame (dark movie frames also
            # match each other's thumbnails, so this is added to any screen match).
            movie = [(float(t), threshold) for t in bmovie] if entropy(th) >= MOVIE_ENT else []
            if good:
                rows.append((tick, best[1], best[0], 'screen', good + movie)); continue
            if movie:
                rows.append((tick, best[1], movie[0][0], 'movie', movie)); continue
        rows.append((tick, best[1], best[0], 'none', []))

    # Longest non-decreasing chain over the candidate positions (blank = wildcard).
    # Per dump keep the cheapest cost per position; a Fenwick tree over the positions gives the
    # best chain ending at or below a position among earlier dumps.
    nodes, score = [], []  # node (dump, pos, cost); score (count, -cost, prev node)
    allpos = sorted({round(pos, 3) for r in rows for pos, _ in r[4]})
    rank = {p: k + 1 for k, p in enumerate(allpos)}
    tree = [None] * (len(allpos) + 1)
    def query(k):
        b = None
        while k > 0:
            if tree[k] is not None and (b is None or score[tree[k]][:2] > score[b][:2]): b = tree[k]
            k -= k & -k
        return b
    def update(k, n):
        while k < len(tree):
            if tree[k] is None or score[n][:2] > score[tree[k]][:2]: tree[k] = n
            k += k & -k
    for i, r in enumerate(rows):
        per = {}
        for pos, cost in r[4]:
            q = round(pos, 3)
            if q not in per or cost < per[q]: per[q] = cost
        mine = []
        for q, cost in per.items():
            m = query(rank[q])
            nodes.append((i, q, cost))
            score.append((score[m][0] + 1, score[m][1] - cost, m) if m is not None else (1, -cost, -1))
            mine.append(len(nodes) - 1)
        for n in mine: update(rank[nodes[n][1]], n)
    chosen = {}
    if nodes:
        n = max(range(len(nodes)), key=lambda k: score[k][:2])
        while n >= 0:
            chosen[nodes[n][0]] = nodes[n]; n = score[n][2]
    ci = sorted(chosen)
    nonblank = [t for t in ts if not bblank[t]]
    # The chain covers the baseline's start (end) when no baseline screen precedes (follows) its
    # first (last) position other than ones looking like the position's own frame.
    def covers(pos, side):
        if pos is None or not nonblank: return False
        ref = min(bfr, key=lambda t: abs(t - pos))
        rest = [t for t in nonblank if (t < pos - 1e-6 if side == 'start' else t > pos + 1e-6)]
        return all(same(bfr[t], bst[t], bfr[ref], bst[ref]) or t in bmovie and ref in bmovie for t in rest)
    at_start = bool(ci) and covers(chosen[ci[0]][1], 'start')
    at_end = bool(ci) and covers(chosen[ci[-1]][1], 'end')
    # An unplaced dump between two placed ones at positions p <= q:
    #   inside a movie (p and q movie frames): a movie frame the statistics missed (text card);
    #   a dynamic baseline frame between p and q: the same animation caught at another phase;
    #   a hole of more than SAMPLE_GAP ticks between baseline frames after p's screen ends and
    #   before q: the baseline never sampled that stretch (EDGE, not counted).
    def bracketed(i):
        p = [chosen[j][1] for j in ci if j < i]
        q = [chosen[j][1] for j in ci if j > i]
        if not p or not q: return None
        p, q = p[-1], q[0]
        mv = [t for t in bmovie if abs(t - p) < 1 or abs(t - q) < 1]
        if len(mv) == 2 or (mv and abs(p - q) < 1): return ('movie', round(p))
        d = [t for t in bdyn if p < t < q]
        if d: return ('anim', d[0])
        ref = min(ts, key=lambda t: abs(t - p))
        pe = ref
        for t in ts:
            if t > pe and not bblank[t] and same(bfr[t], bst[t], bfr[ref], bst[ref]): pe = t
            elif t > pe: break
        pts = [pe] + [t for t in ts if pe < t < q] + [q]
        if any(b - a > SAMPLE_GAP for a, b in zip(pts, pts[1:])): return ('gap', round(pe))
        return None
    out = []
    for i, (tick, s, bt, cls, cands) in enumerate(rows):
        rb = round(bt) if bt is not None else None
        # (also a matched dump left out of the chain, when everything it matched is animation)
        dyn_only = cands and all(any(abs(pos - d) <= BLEND_GAP for d in bdyn) for pos, _ in cands)
        an = bracketed(i) if i not in chosen and (cls in ('movie', 'none') or dyn_only) else None
        if an and an[0] == 'gap' and cls not in ('movie', 'none'): an = None
        if cls == 'blank':
            out.append((tick, None, None, cls, 'PASS', ''))
        elif i in chosen:
            out.append((tick, s, round(chosen[i][1]), cls, 'PASS', ''))
        elif an and an[0] == 'movie':
            out.append((tick, s, an[1], 'movie', 'PASS', 'inside the movie'))
        elif an and an[0] == 'anim':
            out.append((tick, s, an[1], 'anim', 'PASS', 'transition at another phase'))
        elif an:
            out.append((tick, s, an[1], cls, 'EDGE', 'in a stretch the baseline did not sample'))
        elif cands:
            out.append((tick, s, rb, cls, 'FAIL', 'out of order'))
        elif ci and i < ci[0] and at_start:
            out.append((tick, s, rb, cls, 'EDGE', 'before the baseline starts'))
        elif ci and i > ci[-1] and at_end:
            out.append((tick, s, rb, cls, 'EDGE', 'after the baseline ends'))
        else:
            out.append((tick, s, rb, cls, 'FAIL', 'no match'))
    # Blank stretches: a hang to black would pass every frame otherwise.
    k = 0
    while k < len(out):
        if out[k][3] != 'blank': k += 1; continue
        e = k
        while e + 1 < len(out) and out[e + 1][3] == 'blank': e += 1
        span = out[e][0] - out[k][0]
        if span > max_blank:
            for j in range(k, e + 1):
                o = out[j]; out[j] = o[:4] + ('FAIL', f'blank for {span} ticks')
        k = e + 1
    notes.append(f'baseline: {len(ts)} frames, {len(bdyn)} dynamic, {len(bmovie)} of them movie'
                 + ('; movie positions from mpeg hashes' if hashed else ''))
    return out, notes


def side_by_side(a, b, out, labels):
    from PIL import ImageDraw
    a = a.resize((640, 480)); b = b.resize((640, 480))
    s = Image.new('RGB', (1288, 480), (40, 40, 40))
    s.paste(a, (0, 0)); s.paste(b, (648, 0))
    d = ImageDraw.Draw(s)
    d.text((6, 6), labels[0], fill=(255, 0, 255)); d.text((654, 6), labels[1], fill=(255, 0, 255))
    s.save(out)


def selftest():
    import tempfile
    rng = np.random.default_rng(1)
    ok = True
    # Synthetic PCSX2 window: 1000x700, frame 2 px, chrome 60 rows, client letterboxed and
    # pillarboxed variants, picture = random noise (bright) or a dark picture.
    def window(cw, ch, pic):
        W, H = cw + 4, ch + 62
        a = np.zeros((H, W, 3), np.uint8)
        a[:, :2] = a[:, -2:] = 120; a[-2:] = 120
        a[:60, 2:-2] = 50; a[0:20, 2:-2] = 70
        t, b, l, r = fit43(60, 60 + ch, 2, 2 + cw)
        a[t:b, l:r] = np.asarray(Image.fromarray(pic).resize((r - l, b - t)))
        return a, (t, b, l, r)
    pic = rng.integers(30, 255, (448, 640, 3), dtype=np.uint8)
    dark = np.zeros((448, 640, 3), np.uint8); dark[200:240, 300:340] = 200
    edge = pic.copy(); edge[:, :40] = 0  # picture with a black left edge
    for name, cw, ch, p in [('pillarbox', 1000, 600, pic), ('letterbox', 1000, 900, pic),
                            ('dark', 1000, 600, dark), ('black-edge', 1000, 600, edge)]:
        a, want = window(cw, ch, p)
        got = crop_box(np.asarray(Image.fromarray(a).convert('L')).astype(np.float32))
        err = max(abs(int(x) - int(y)) for x, y in zip(got, want))
        print(f'crop {name}: got {tuple(int(x) for x in got)} want {want} -> {"ok" if err <= 2 else "FAIL"}')
        ok &= err <= 2
    # Exact copy of one dump frame must score best.
    with tempfile.TemporaryDirectory() as d:
        frames = {}
        for tick in (100, 200, 300, 400):
            raw = np.zeros((512, 640, 4), np.uint8)
            raw[:448, :, :3] = rng.integers(0, 255, (448, 640, 3), dtype=np.uint8) // (tick // 100)
            raw.tofile(os.path.join(d, f'gsdump_{tick}.rgba'))
            open(os.path.join(d, f'gsdump_{tick}.txt'), 'w').write(f'tick={tick}\npresented=640x448 rowstride=640\n')
            frames[tick] = raw
        a, _ = window(1000, 600, frames[300][:448, :, :3].copy())
        path = os.path.join(d, 'ref.png'); Image.fromarray(a).save(path)
        ref, _ = load_reference(path)
        res = score_dir(ref, d)
        print('exact copy: best tick', res[0][1], 'score %.2f' % res[0][0], '(next %.2f)' % res[1][0],
              '-> ok' if res[0][1] == 300 else '-> FAIL')
        ok &= res[0][1] == 300
    # Odd dump dirs must not crash --dirs: empty, a single dump, an all-blank baseline (no screen
    # candidates at all), frames with no presented picture (presented=0x0) or a short .rgba.
    with tempfile.TemporaryDirectory() as root:
        def mkdir(name, frames):
            d = os.path.join(root, name); os.makedirs(d)
            for tick, (px, pres) in frames.items():
                raw = np.zeros((512, 640, 4), np.uint8)
                if px is not None: raw[:448, :, :3] = px
                (raw.ravel()[:1000] if pres == 'short' else raw).tofile(os.path.join(d, f'gsdump_{tick}.rgba'))
                open(os.path.join(d, f'gsdump_{tick}.txt'), 'w').write(
                    f'tick={tick}\npresented={"640x448" if pres == "short" else pres} rowstride=640\n')
            return d
        noise = rng.integers(0, 255, (448, 640, 3), dtype=np.uint8)
        empty = mkdir('empty', {})
        one = mkdir('one', {100: (noise, '640x448')})
        blank = mkdir('blank', {100: (None, '640x448'), 130: (None, '640x448')})
        odd = mkdir('odd', {100: (noise, '0x0'), 130: (noise, 'short'), 160: (noise, '640x448')})
        cases = [('empty run', empty, one, lambda r: r == []),
                 ('empty baseline', one, empty, lambda r: [x[4] for x in r] == ['FAIL']),
                 ('single dump each', one, one, lambda r: [x[3:5] for x in r] == [('screen', 'PASS')]),
                 ('all-blank baseline', one, blank, lambda r: [x[3:5] for x in r] == [('none', 'FAIL')]),
                 ('no presented picture / short rgba', odd, one,
                  lambda r: [x[3] for x in r] == ['blank', 'blank', 'screen'] and r[2][4] == 'PASS')]
        for name, run, base, want in cases:
            try:
                rows, _ = compare_seq(run, base)
                compare_dirs(run, base)
                good = want(rows)
                got = [x[3:5] for x in rows]
            except Exception as e:
                good, got = False, f'{type(e).__name__}: {e}'
            print(f'dirs {name}: {got} -> {"ok" if good else "FAIL"}')
            ok &= good
    print('selftest', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


def main():
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    if '--dirs' in sys.argv:
        ap = argparse.ArgumentParser()
        ap.add_argument('--dirs', nargs=2, metavar=('RUN', 'BASELINE'))
        ap.add_argument('--window', type=int, default=75)
        ap.add_argument('--threshold', type=float, default=10.0)
        ap.add_argument('--strict', action='store_true', help='old tick-window scoring')
        ap.add_argument('--max-blank', type=int, default=900)
        a = ap.parse_args()
        if a.strict:
            res = compare_dirs(a.dirs[0], a.dirs[1], a.window, a.threshold)
            for tick, s, t, st in res:
                print(f'{st}  tick {tick:>7}  ' +
                      (f'mad {s:5.1f} vs baseline ~{t}' if s is not None else 'no baseline frame within window'))
            bad = sum(r[3] == 'FAIL' for r in res)
            edge = sum(r[3] == 'EDGE' for r in res)
            print(f'verdict: {"PASS" if res and not bad else "FAIL"} ({len(res) - bad - edge}/{len(res)} ticks pass, '
                  f'{bad} fail, {edge} at baseline edge; threshold {a.threshold}, strict)')
            sys.exit(0 if res and not bad else 1)
        res, notes = compare_seq(a.dirs[0], a.dirs[1], a.threshold, a.max_blank)
        for tick, s, t, cls, st, why in res:
            print(f'{st}  tick {tick:>7}  {cls:<6} ' + (f'mad {s:5.1f}' if s is not None else 'mad     -')
                  + (f' vs baseline {t}' if t is not None else '') + (f'  ({why})' if why else ''))
        for n in notes: print('note:', n)
        bad = sum(r[4] == 'FAIL' for r in res) + sum(n.startswith('FAIL') for n in notes)
        edge = sum(r[4] == 'EDGE' for r in res)
        seen = sum(r[4] == 'PASS' and r[3] != 'blank' for r in res)
        ok = bool(res) and not bad and seen > 0
        print(f'verdict: {"PASS" if ok else "FAIL"} ({len(res) - bad - edge}/{len(res)} dumps pass, '
              f'{bad} fail, {edge} outside the baseline; content order, threshold {a.threshold})')
        sys.exit(0 if ok else 1)
    ap = argparse.ArgumentParser()
    ap.add_argument('reference'); ap.add_argument('dumpdir')
    ap.add_argument('-o', '--out'); ap.add_argument('--top', type=int, default=5)
    a = ap.parse_args()
    ref, box = load_reference(a.reference)
    t, b, l, r = box
    print(f'reference crop: x {l}..{r} y {t}..{b} ({r - l}x{b - t})')
    res = score_dir(ref, a.dumpdir)
    if not res:
        print('no gsdump_*.rgba in', a.dumpdir); sys.exit(1)
    print(f'{len(res)} dumps scored (mean abs diff, 0..255, lower = closer); best:')
    for s, tick, _ in res[:a.top]:
        print(f'  tick {tick:>7}  {s:6.1f}')
    print('verdict:', 'close match' if res[0][0] < 15 else 'weak match' if res[0][0] < 35 else 'no match')
    if a.out:
        side_by_side(ref, load_dump(res[0][2]), a.out,
                     (os.path.basename(a.reference), f'tick {res[0][1]}  mad {res[0][0]:.1f}'))
        print('side by side ->', a.out)


if __name__ == '__main__':
    main()
