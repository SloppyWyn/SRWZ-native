#!/usr/bin/env python3
"""PINE client for PCSX2: read and write guest (EE) memory of a running game from a script.

PINE is PCSX2's IPC protocol (Settings > Advanced > PINE, ini [EmuCore] EnablePINE = true,
PINESlot = 28011). On Windows it is a TCP socket on 127.0.0.1:<slot>.
Wire format (little endian): request = u32 total size (incl. these 4 bytes) + one or more
commands (u8 opcode + args); reply = u32 total size + u8 result (0 = OK, 0xFF = FAIL) +
the replies' data concatenated. Addresses are EE virtual addresses (0x00100000 = main RAM).

Usage:
    python tools/pine.py info                         # version, status, game id/title
    python tools/pine.py read 0x59c210 [count]        # 32-bit words
    python tools/pine.py dump 0x59c200 0x60           # hex dump (bytes)
    python tools/pine.py write 0x59c210 0x2a          # write a 32-bit word
    python tools/pine.py state                        # the game's state words (pad, game)
    python tools/pine.py watch [--interval 0.05] [--seconds 60] [--out file] [--words a,b,...]
                                                      # log every change of the words, timestamped
    python tools/pine.py savestate 1 | loadstate 1    # slot save/load (sstates folder)
    python tools/pine.py --selftest [--write-test]    # protocol test against a fake in-process
                                                      # server; plus a live test if PCSX2 is up
    --port N (default 28011)
"""
import argparse
import socket
import struct
import sys
import threading
import time

MSG_READ8, MSG_READ16, MSG_READ32, MSG_READ64 = 0, 1, 2, 3
MSG_WRITE8, MSG_WRITE16, MSG_WRITE32, MSG_WRITE64 = 4, 5, 6, 7
MSG_VERSION, MSG_SAVESTATE, MSG_LOADSTATE = 8, 9, 0xA
MSG_TITLE, MSG_ID, MSG_UUID, MSG_GAMEVERSION, MSG_STATUS = 0xB, 0xC, 0xD, 0xE, 0xF
STATUS_NAMES = {0: "running", 1: "paused", 2: "shutdown"}
_RSIZE = {MSG_READ8: 1, MSG_READ16: 2, MSG_READ32: 4, MSG_READ64: 8}
_WFMT = {MSG_WRITE8: "<B", MSG_WRITE16: "<H", MSG_WRITE32: "<I", MSG_WRITE64: "<Q"}

# SRWZ (SLPS-25887) words worth watching. Pad state machine FUN_0018d7d0 (Wall 11):
#   0x59c204 pad port/slot handle passed to libpad, 0x59c208/0x59c20c last read result,
#   0x59c210 machine state (0 = start, 0x28/0x29 = mode set, 0x2a = idle check,
#   0x46/0x47 = actuator align, 99 = ready/reading), 0x59c214 scePadGetState result
#   (6 = stable, 2 = findpad), 0x59c218/0x59c21c scePadInfoMode results, 0x59c24c/0x59c24e.
#   0x523930 the game's top-level state word (PCSX2: 0x18 movie, 0x02 title, 0x48/0x49 after
#   Start at the title, 0x37-0x39 attract demo). 0x59c1d8 the game's VBlank counter (60/s;
#   tools\pcsx2-capture.ps1 uses it as the game clock). 0x59c100 the pad data buffer.
GAME_WORDS = [0x59c1d8, 0x523930, 0x523934, 0x523938, 0x52393c,
              0x59c100, 0x59c104, 0x59c204, 0x59c208, 0x59c20c, 0x59c210, 0x59c214, 0x59c218, 0x59c21c,
              0x59c244, 0x59c248, 0x59c24c, 0x59c3cc, 0x59c3d0]


class PineError(Exception):
    pass


class Pine:
    def __init__(self, port=28011, host="127.0.0.1", timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _recvn(self, n):
        buf = b""
        while len(buf) < n:
            c = self.sock.recv(n - len(buf))
            if not c:
                raise PineError("connection closed")
            buf += c
        return buf

    def batch(self, cmds):
        """cmds: list of (opcode, args_bytes). Returns the raw reply payload (after result)."""
        body = b"".join(bytes([op]) + args for op, args in cmds)
        self.sock.sendall(struct.pack("<I", len(body) + 4) + body)
        size = struct.unpack("<I", self._recvn(4))[0]
        rest = self._recvn(size - 4)
        if rest[0] != 0:
            raise PineError("PINE command failed (result 0x%02x; no game running?)" % rest[0])
        return rest[1:]

    def _read(self, op, addr):
        return int.from_bytes(self.batch([(op, struct.pack("<I", addr))]), "little")

    def read8(self, a): return self._read(MSG_READ8, a)
    def read16(self, a): return self._read(MSG_READ16, a)
    def read32(self, a): return self._read(MSG_READ32, a)
    def read64(self, a): return self._read(MSG_READ64, a)

    def read32_many(self, addrs):
        """Reads several words in one round trip (one batch request: same emulated instant)."""
        data = self.batch([(MSG_READ32, struct.pack("<I", a)) for a in addrs])
        return list(struct.unpack("<%dI" % len(addrs), data))

    def read_bytes(self, addr, n):
        """Bytes via batched 64-bit reads (addr and n need not be aligned)."""
        start, end = addr & ~7, (addr + n + 7) & ~7
        cmds = [(MSG_READ64, struct.pack("<I", a)) for a in range(start, end, 8)]
        out = b""
        for i in range(0, len(cmds), 256):   # keep each request small
            out += self.batch(cmds[i:i + 256])
        return out[addr - start:addr - start + n]

    def _write(self, op, addr, val):
        self.batch([(op, struct.pack("<I", addr) + struct.pack(_WFMT[op], val))])

    def write8(self, a, v): self._write(MSG_WRITE8, a, v)
    def write16(self, a, v): self._write(MSG_WRITE16, a, v)
    def write32(self, a, v): self._write(MSG_WRITE32, a, v)
    def write64(self, a, v): self._write(MSG_WRITE64, a, v)

    def _string(self, op):
        d = self.batch([(op, b"")])
        n = struct.unpack("<I", d[:4])[0]
        return d[4:4 + n].rstrip(b"\0").decode("utf-8", "replace")

    def version(self): return self._string(MSG_VERSION)
    def title(self): return self._string(MSG_TITLE)
    def game_id(self): return self._string(MSG_ID)
    def game_uuid(self): return self._string(MSG_UUID)
    def game_version(self): return self._string(MSG_GAMEVERSION)

    def status(self):
        return struct.unpack("<I", self.batch([(MSG_STATUS, b"")]))[0]

    def save_state(self, slot): self.batch([(MSG_SAVESTATE, bytes([slot]))])
    def load_state(self, slot): self.batch([(MSG_LOADSTATE, bytes([slot]))])


# ---------------------------------------------------------------- fake server (self-test)
class FakePcsx2:
    """A minimal PINE server over a dict-backed memory, for testing the client offline."""

    def __init__(self):
        self.mem = bytearray(0x02000000)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        c, _ = self.srv.accept()

        def recvn(n):
            b = b""
            while len(b) < n:
                x = c.recv(n - len(b))
                if not x:
                    return None
                b += x
            return b
        while True:
            h = recvn(4)
            if h is None:
                return
            body = recvn(struct.unpack("<I", h)[0] - 4)
            out, i, ok = b"", 0, True
            while i < len(body):
                op = body[i]; i += 1
                if op in _RSIZE:
                    a = struct.unpack_from("<I", body, i)[0] & 0x1FFFFFF; i += 4
                    out += bytes(self.mem[a:a + _RSIZE[op]])
                elif op in _WFMT:
                    a = struct.unpack_from("<I", body, i)[0] & 0x1FFFFFF; i += 4
                    n = struct.calcsize(_WFMT[op])
                    self.mem[a:a + n] = body[i:i + n]; i += n
                elif op in (MSG_VERSION, MSG_TITLE, MSG_ID, MSG_UUID, MSG_GAMEVERSION):
                    s = {MSG_VERSION: b"PCSX2 fake", MSG_ID: b"SLPS-25887"}.get(op, b"x") + b"\0"
                    out += struct.pack("<I", len(s)) + s
                elif op == MSG_STATUS:
                    out += struct.pack("<I", 0)
                elif op in (MSG_SAVESTATE, MSG_LOADSTATE):
                    i += 1
                else:
                    ok = False
                    break
            if not ok:
                c.sendall(struct.pack("<IB", 5, 0xFF))
            else:
                c.sendall(struct.pack("<IB", len(out) + 5, 0) + out)


def selftest(port, write_test=False):
    fake = FakePcsx2()
    struct.pack_into("<I", fake.mem, 0x59c210, 0x63)
    with Pine(fake.port) as p:
        assert p.version() == "PCSX2 fake", p.version()
        assert p.game_id() == "SLPS-25887"
        assert p.status() == 0
        assert p.read32(0x59c210) == 0x63
        p.write32(0x59c214, 0x11223344)
        assert p.read16(0x59c214) == 0x3344 and p.read8(0x59c217) == 0x11
        p.write64(0x100000, 0x0102030405060708)
        assert p.read64(0x100000) == 0x0102030405060708
        assert p.read32_many([0x59c210, 0x59c214]) == [0x63, 0x11223344]
        assert p.read_bytes(0x100003, 3) == bytes([5, 4, 3])
        p.save_state(1)
    print("selftest: fake server OK (reads, writes, batch, strings, status)")
    try:
        p = Pine(port, timeout=2)
    except OSError:
        print("selftest: no PCSX2 PINE server on port %d (enable PINE and start a game for the live test)" % port)
        return 0
    with p:
        try:
            print("live: %s, status %s" % (p.version(), STATUS_NAMES.get(p.status(), "?")))
        except (socket.timeout, TimeoutError):
            # PCSX2 serves one PINE client at a time: another script holds the connection.
            print("live: PINE accepted but did not answer: another client is connected (skipped)")
            return 0
        try:
            print("live: game %s %r" % (p.game_id(), p.title()))
            if write_test:
                # a word below the ELF (0x000fff00, unused after boot), restored at once
                a = 0x000FFF00
                old = p.read32(a)
                p.write32(a, 0xA5A55A5A)
                got = p.read32(a)
                p.write32(a, old)
                assert got == 0xA5A55A5A, hex(got)
                print("live: write/read-back OK at 0x%08x" % a)
            print_state(p)
        except PineError as e:
            print("live: %s" % e)
    return 0


def print_state(p):
    vals = p.read32_many(GAME_WORDS)
    print("  ".join("%06x=%08x" % (a, v) for a, v in zip(GAME_WORDS, vals)))


def watch(p, words, interval, seconds, out):
    f = open(out, "a") if out else None
    t0 = time.perf_counter()
    last = None
    n = 0

    def emit(line):
        print(line)
        if f:
            f.write(line + "\n"); f.flush()
    emit("# watch " + " ".join("%06x" % a for a in words) + " (t = seconds since start)")
    while seconds <= 0 or time.perf_counter() - t0 < seconds:
        try:
            vals = p.read32_many(words)
        except PineError as e:
            emit("%9.3f  %s" % (time.perf_counter() - t0, e)); time.sleep(1); continue
        n += 1
        if vals != last:
            ch = "" if last is None else " ch:" + ",".join("%06x" % a for a, v, w in zip(words, vals, last) if v != w)
            emit("%9.3f  " % (time.perf_counter() - t0) + " ".join("%08x" % v for v in vals) + ch)
            last = vals
        time.sleep(interval)
    emit("# %d samples" % n)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="info")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--port", type=int, default=28011)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--write-test", action="store_true", help="selftest: also write and restore one guest word")
    ap.add_argument("--interval", type=float, default=0.05)
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--out")
    ap.add_argument("--words")
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.port, a.write_test)
    num = lambda s: int(s, 0)
    with Pine(a.port) as p:
        if a.cmd == "info":
            st = p.status()
            print("version %s, status %s" % (p.version(), STATUS_NAMES.get(st, st)))
            if st != 2:
                print("game %s %r" % (p.game_id(), p.title()))
        elif a.cmd == "read":
            addr, cnt = num(a.args[0]), num(a.args[1]) if len(a.args) > 1 else 1
            for i, v in enumerate(p.read32_many([addr + 4 * i for i in range(cnt)])):
                print("%08x: %08x" % (addr + 4 * i, v))
        elif a.cmd == "dump":
            addr, n = num(a.args[0]), num(a.args[1])
            d = p.read_bytes(addr, n)
            for i in range(0, n, 16):
                print("%08x: %s" % (addr + i, d[i:i + 16].hex(" ")))
        elif a.cmd == "write":
            p.write32(num(a.args[0]), num(a.args[1]))
        elif a.cmd == "state":
            print_state(p)
        elif a.cmd == "watch":
            words = [num(x) for x in a.words.split(",")] if a.words else GAME_WORDS
            watch(p, words, a.interval, a.seconds, a.out)
        elif a.cmd == "savestate":
            p.save_state(num(a.args[0]))
        elif a.cmd == "loadstate":
            p.load_state(num(a.args[0]))
        else:
            ap.error("unknown command " + a.cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
