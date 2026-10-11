"""Decompresses the game's LZ records (header parser 0x1C6C40, decompressor 0x1C6D70
in SLPS_258.87). Written from the format notes in retro-trans/SRW-Z
(docs/LZ_FORMAT.md):

  varint: 7 bits per byte, big-endian; bit 0 of each byte is the stop flag.
  header: total, flags, [skipped: only if not (window >= total and flags & 0x21 == 1)
          and flags & 0x40], reserved.
  groups: token T (literal count T & 15, reference count T >> 4; 0 = read a varint),
          the literals, then (until the output is full) the references:
          R: d = R & 15 (low bit 0 = the distance continues as varint bytes),
          dist = d >> 1, len = (R >> 4 or a varint) + 1, copy from out[-(dist+1)].

    python tools/banlz.py IN OUT [--offset N]
"""
import argparse


class Reader:
    def __init__(self, data, at=0):
        self.data, self.at = data, at

    def byte(self):
        b = self.data[self.at]
        self.at += 1
        return b

    def varint(self):
        v = 0
        while True:
            v = (v << 7) | self.byte()
            if v & 1:
                return v >> 1


def header(r):
    total = r.varint()
    flags = r.varint()
    window = 1 << (((flags >> 1) & 0xF) + 8)
    if not (window >= total and (flags & 0x21) == 1) and (flags & 0x40):
        r.varint()
    r.varint()
    return total, flags


def decompress(data, at=0):
    """Returns (output, offset just past the record)."""
    r = Reader(data, at)
    total, _ = header(r)
    out = bytearray()
    while len(out) < total:
        t = r.byte()
        lit, nref = t & 0xF, t >> 4
        if lit == 0:
            lit = r.varint()
        if nref == 0:
            nref = r.varint()
        out += data[r.at:r.at + lit]
        r.at += lit
        for _ in range(nref):
            if len(out) >= total:
                break
            ref = r.byte()
            d = ref & 0xF
            if not d & 1:
                while True:
                    d = (d << 7) | r.byte()
                    if d & 1:
                        break
            dist = d >> 1
            n = ref >> 4
            if n == 0:
                n = r.varint()
            n = min(n + 1, total - len(out))
            src = len(out) - (dist + 1)
            if src < 0:
                raise ValueError(f'reference before the start (out {len(out)}, dist {dist})')
            for i in range(n):  # byte by byte: references may overlap their own output
                out.append(out[src + i])
    return bytes(out), r.at


def main():
    p = argparse.ArgumentParser()
    p.add_argument('src')
    p.add_argument('dst')
    p.add_argument('--offset', type=lambda s: int(s, 0), default=0)
    a = p.parse_args()
    data = open(a.src, 'rb').read()
    out, end = decompress(data, a.offset)
    open(a.dst, 'wb').write(out)
    print(f'{a.src}@{a.offset:#x}: {end - a.offset} -> {len(out)} bytes (record ends at {end:#x} of {len(data):#x})')


if __name__ == '__main__':
    main()
