#!/usr/bin/env python3
"""Show what a runner snapshot holds and which runner can load it (format 8 and later).

    python tools/snapinfo.py <snapshot.bin> [--json] [--sections] [--types]
    python tools/snapinfo.py --selftest

Reads only the HEAD block (game, writing build, VBlank tick, EE cycle, each section's
version, each raw type's size/alignment and the sections holding it) and checks the
trailing FNV-1a 64 checksum with --checksum (pure Python: ~30 s for a 50 MB file). Format 7 and older files are named as
such (they load only into the runner build that wrote them).
"""
import argparse
import json
import os
import struct
import sys
import tempfile

MAGIC = b"PS2XSNAP"


class SnapError(Exception):
    pass


def fnv1a64(data):
    h = 1469598103934665603
    for b in data:
        h ^= b
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


class Reader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def take(self, n):
        if self.pos + n > len(self.data):
            raise SnapError("truncated snapshot header")
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.take(8))[0]

    def str(self):
        n = self.u64()
        if n > 1 << 20:
            raise SnapError("impossible string length in header")
        return self.take(n).decode("utf-8", "replace")


def read_head(path, checksum=False):
    with open(path, "rb") as f:
        prefix = f.read(1 << 20)  # the HEAD block is a few KB
        size = os.fstat(f.fileno()).st_size
    if prefix[:8] != MAGIC:
        raise SnapError("not a runner snapshot (bad magic)")
    r = Reader(prefix, 8)
    version = r.u32()
    info = {"path": path, "bytes": size, "format": version}
    if version < 8:
        info["note"] = "format %d: before durable snapshots (format 8 runners load format 7 by struct sizes)" % version
        if version == 7:
            sig = Reader(r.take(r.u64()))
            sig.take(12 + 7 * 4)  # function table base/end/slots, 7 raw struct sizes
            info["game"] = sig.str()
            info["build"] = sig.str()
            if r.take(4) == b"INFO":  # first body section: VBlank tick, EE cycle
                r.u64()
                info["tick"] = r.u64()
                info["eeCycle"] = r.u64()
        return info
    tag = r.take(4)
    if tag != b"HEAD":
        raise SnapError("format %d snapshot without a HEAD block" % version)
    r.u64()  # section length
    info["game"] = r.str()
    info["build"] = r.str()
    info["tick"] = r.u64()
    info["eeCycle"] = r.u64()
    info["sections"] = {}
    for _ in range(r.u64()):
        p = r.str()
        info["sections"][p] = r.u32()
    info["types"] = []
    for _ in range(r.u64()):
        name = r.str()
        size_, align = r.u32(), r.u32()
        secs = [r.str() for _ in range(r.u64())]
        info["types"].append({"name": name, "size": size_, "align": align, "sections": secs})
    if checksum:
        with open(path, "rb") as f:
            data = f.read()
        stored = struct.unpack("<Q", data[-8:])[0]
        info["checksumOk"] = fnv1a64(data[:-8]) == stored
    return info


def selftest():
    def head_bytes(game, build, tick, cycle, sections, types):
        def s(x):
            b = x.encode()
            return struct.pack("<Q", len(b)) + b
        body = s(game) + s(build) + struct.pack("<QQ", tick, cycle)
        body += struct.pack("<Q", len(sections))
        for p, v in sections:
            body += s(p) + struct.pack("<I", v)
        body += struct.pack("<Q", len(types))
        for n, sz, al, secs in types:
            body += s(n) + struct.pack("<II", sz, al) + struct.pack("<Q", len(secs)) + b"".join(s(x) for x in secs)
        return b"HEAD" + struct.pack("<Q", len(body)) + body

    payload = MAGIC + struct.pack("<I", 8) + head_bytes(
        "SLPS_258.87", "pe:1:2:3", 27400, 123456, [("EESC", 1), ("GS  ", 2)],
        [("struct R5900Context", 656, 16, ["EESC"])]) + b"BODY"
    payload += struct.pack("<Q", fnv1a64(payload))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.bin")
        with open(p, "wb") as f:
            f.write(payload)
        info = read_head(p, checksum=True)
        assert info["format"] == 8 and info["tick"] == 27400 and info["game"] == "SLPS_258.87", info
        assert info["sections"]["GS  "] == 2 and info["types"][0]["size"] == 656, info
        assert info["checksumOk"] is True, info
        with open(p, "r+b") as f:
            f.seek(len(payload) - 12)
            f.write(b"X")
        assert read_head(p, checksum=True)["checksumOk"] is False
        sig = struct.pack("<III", 1, 2, 3) + struct.pack("<7I", *range(7))
        sig += struct.pack("<Q", 11) + b"SLPS_258.87" + struct.pack("<Q", 3) + b"pe1"
        old = MAGIC + struct.pack("<I", 7) + struct.pack("<Q", len(sig)) + sig + b"INFO" + struct.pack("<QQQ", 16, 27400, 99) + b"rest"
        with open(p, "wb") as f:
            f.write(old)
        assert read_head(p)["format"] == 7 and read_head(p)["build"] == "pe1" and read_head(p)["tick"] == 27400, read_head(p)
        with open(p, "wb") as f:
            f.write(b"NOTASNAP")
        try:
            read_head(p)
            raise AssertionError("bad magic accepted")
        except SnapError:
            pass
    print("snapinfo selftest: PASS")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sections", action="store_true")
    ap.add_argument("--types", action="store_true")
    ap.add_argument("--checksum", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if not a.snapshot:
        ap.error("snapshot path required")
    try:
        info = read_head(a.snapshot, checksum=a.checksum)
    except (SnapError, OSError) as e:
        print("snapinfo: %s: %s" % (a.snapshot, e), file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(info, indent=1))
        return 0
    if info["format"] < 8:
        print("%s: %d bytes, %s, tick %s, written by %s; %s" % (a.snapshot, info["bytes"], info.get("game", "?"),
                                                              info.get("tick", "?"), info.get("build", "?"), info["note"]))
        return 0
    print("%s: format %d, %s, tick %d, EE cycle %d, written by %s, %d sections, %d raw types%s" % (
        a.snapshot, info["format"], info["game"], info["tick"], info["eeCycle"], info["build"],
        len(info["sections"]), len(info["types"]),
        "" if "checksumOk" not in info else (", checksum ok" if info["checksumOk"] else ", CHECKSUM BAD")))
    if a.sections:
        for p, v in sorted(info["sections"].items()):
            print("  section %-12r v%d" % (p, v))
    if a.types:
        for t in info["types"]:
            print("  %-48s %5d bytes align %2d  in %s" % (t["name"], t["size"], t["align"], ", ".join(t["sections"])))
    return 0 if info.get("checksumOk", True) else 1


if __name__ == "__main__":
    sys.exit(main())
