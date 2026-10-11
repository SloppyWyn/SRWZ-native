"""Frame-pacing report from a PS2X_PACE_LOG file (runtime/ps2_pace_log.h).

    python tools/pacelog.py <pace log> [--from SEC] [--to SEC] [--frames] [--thread ID]
    python tools/pacelog.py --selftest

A "frame" is the guest's main-loop pass between two lowerings of the frame thread's priority
(a PRIO line raising the thread to a higher priority from an interrupt handler starts it, the
thread lowering itself ends it): the pacing scheme of games that park the main thread at a low
priority behind a busy idle thread and boost it from the VBlank interrupt. Without such PRIO
lines, frames are counted between VBlank starts only.

Per second: VBlanks, frames, how many frames missed their VBlank (took more than one period),
and where the frame thread's host time went: DMA kicks (GS work done inline), scheduler pacing
waits while it was running (the guest clock ahead of the host), the rest (guest code). Per
VBlank: guest cycles charged by code vs. added by the real-time catch-up. With the asynchronous
GS (PS2X_GS_ASYNC): gsMs = GS-thread busy time per VBlank, thrMs = executor time spent per
second waiting for the GS at VBlank start (the one-VBlank lag limit).
"""
import argparse
import re
import statistics
import sys
from collections import defaultdict

LINE = re.compile(r'^(\d+) (\w+)(.*)$')
KV = re.compile(r'(\w+)=(-?(?:0x)?[0-9a-fA-F]+)')


def parse(lines):
    events = []
    for raw in lines:
        m = LINE.match(raw.strip())
        if not m:
            continue
        t, kind, rest = int(m.group(1)), m.group(2), m.group(3)
        kv = {}
        for k, v in KV.findall(rest):
            kv[k] = int(v, 16) if v.lower().startswith('0x') else int(v)
        if kind == 'PRIO':
            p = re.search(r'id=(\d+) (\d+)->(\d+) isr=(\d)', rest)
            if p:
                kv.update(id=int(p.group(1)), old=int(p.group(2)), new=int(p.group(3)), isr=int(p.group(4)))
        events.append((t, kind, kv))
    return events


def frame_thread(events):
    """The thread an interrupt handler boosts most often (iChangeThreadPriority from VBlank)."""
    count = defaultdict(int)
    for _, kind, kv in events:
        if kind == 'PRIO' and kv.get('isr') == 1 and kv['new'] < kv['old']:
            count[kv['id']] += 1
    return max(count, key=count.get) if count else None


def frames(events, tid):
    """[(start_us, end_us, dma_us, paced_us)] for the frame thread: start = boost, end = lowering."""
    out = []
    start = None
    dma = paced = 0
    running = False
    for t, kind, kv in events:
        if kind == 'PRIO' and kv.get('id') == tid:
            if kv['isr'] == 1 and kv['new'] < kv['old']:
                if start is None:
                    start, dma, paced = t, 0, 0
            elif kv['isr'] == 0 and kv['new'] > kv['old'] and start is not None:
                out.append((start, t, dma, paced))
                start = None
        elif start is not None and kind == 'DMA':
            dma += kv.get('us', 0)
        elif start is not None and kind == 'PACE' and kv.get('thr') == tid:
            paced += kv.get('us', 0)
    return out


def report(events, lo, hi, show_frames, tid=None):
    vs = [(t, kv) for t, k, kv in events if k == 'VS' and lo <= t / 1e6 <= hi]
    tid = tid if tid is not None else frame_thread(events)
    fr = [f for f in frames(events, tid) if lo <= f[0] / 1e6 <= hi] if tid is not None else []
    print('frame thread: %s; VBlanks %d, frames %d in %.1f-%.1f s' % (tid, len(vs), len(fr), lo, hi if hi < 1e9 else (events[-1][0] / 1e6 if events else 0)))
    period = 16667
    per = defaultdict(lambda: dict(vbl=0, fr=0, miss=0, dur=[], dma=[], paced=[], charged=0, caught=0, late=[], gsbusy=[], throttle=0))
    for t, k, kv in events:
        if k == 'GSQ' and lo <= t / 1e6 <= hi:
            s = per[int(t // 1e6)]
            s['gsbusy'].append(kv.get('busy', 0))
            s['throttle'] += kv.get('throttle', 0)
    for t, kv in vs:
        s = per[int(t // 1e6)]
        s['vbl'] += 1
        s['charged'] += kv.get('charged', 0)
        s['caught'] += kv.get('caught', 0)
        s['late'].append(kv.get('late', 0))
    for a, b, dma, paced in fr:
        s = per[int(a // 1e6)]
        s['fr'] += 1
        d = b - a
        s['dur'].append(d)
        s['dma'].append(dma)
        s['paced'].append(paced)
        if d > period:
            s['miss'] += 1
    print('%5s %4s %4s %4s %8s %8s %8s %8s %7s %7s %7s %7s' % ('sec', 'vbl', 'fr', 'miss', 'frameMs', 'dmaMs', 'pacedMs', 'otherMs', 'caught%', 'lateMs', 'gsMs', 'thrMs'))
    for sec in sorted(per):
        s = per[sec]
        md = statistics.mean(s['dur']) / 1000 if s['dur'] else 0
        mdma = statistics.mean(s['dma']) / 1000 if s['dma'] else 0
        mp = statistics.mean(s['paced']) / 1000 if s['paced'] else 0
        tot = s['charged'] + s['caught']
        print('%5d %4d %4d %4d %8.2f %8.2f %8.2f %8.2f %7.1f %7.2f %7.2f %7.1f' % (
            sec, s['vbl'], s['fr'], s['miss'], md, mdma, mp, md - mdma - mp,
            100.0 * s['caught'] / tot if tot else 0, statistics.mean(s['late']) / 1000 if s['late'] else 0,
            statistics.mean(s['gsbusy']) / 1000 if s['gsbusy'] else 0, s['throttle'] / 1000))
    if show_frames:
        for a, b, dma, paced in fr:
            print('frame %10.3f s  %6.2f ms  dma %6.2f  paced %6.2f%s' % (a / 1e6, (b - a) / 1e3, dma / 1e3, paced / 1e3, '  MISS' if b - a > period else ''))
    return per


def selftest():
    log = []
    t = 0
    # Thread 5 boosted at each VBlank end; frame 1 fits (10 ms), frame 2 misses (20 ms, 8 ms of it paced).
    for i, (dur, paced) in enumerate([(10000, 0), (20000, 8000), (10000, 0)]):
        log.append('%d VS v=%d late=0 ahead=0 thr=2 charged=100 caught=300 paced=0 idle=0' % (t, i + 1))
        log.append('%d PRIO id=5 10->1 isr=1 cur=2' % (t + 500))
        log.append('%d DMA ch=0x1000a000 tag=0x0 us=3000' % (t + 2000))
        if paced:
            log.append('%d PACE thr=5 pc=0x100 us=%d' % (t + 3000, paced))
        log.append('%d PRIO id=5 1->10 isr=0 cur=5' % (t + 500 + dur))
        t += 33334 if dur > 16667 else 16667
    ev = parse(log)
    assert frame_thread(ev) == 5
    fr = frames(ev, 5)
    assert len(fr) == 3, fr
    assert fr[1][1] - fr[1][0] == 20000 and fr[1][3] == 8000 and fr[0][2] == 3000, fr
    per = report(ev, 0, 1e9, False)
    assert per[0]['miss'] == 1 and per[0]['fr'] == 3
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log', nargs='?')
    ap.add_argument('--from', dest='lo', type=float, default=0)
    ap.add_argument('--to', dest='hi', type=float, default=1e9)
    ap.add_argument('--frames', action='store_true')
    ap.add_argument('--thread', type=int)
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.log:
        ap.error('pace log path needed')
    with open(a.log, encoding='utf-8', errors='replace') as f:
        report(parse(f), a.lo, a.hi, a.frames, a.thread)


if __name__ == '__main__':
    main()
