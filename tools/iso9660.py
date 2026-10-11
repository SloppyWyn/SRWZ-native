"""Reads a PS2 DVD image (plain ISO 9660, 2048-byte sectors): lists its files and
extracts them. Only ever reads the image.

    python tools/iso9660.py disc/srwz.iso                 # list: LBA, size, path
    python tools/iso9660.py disc/srwz.iso --extract SLPS_258.87 SYSTEM.CNF --out local/extract
"""
import argparse
import os
import struct
import sys

SECTOR = 2048


class Iso:
    def __init__(self, path):
        self.f = open(path, 'rb')
        pvd = self.read(16, SECTOR)
        if pvd[0] != 1 or pvd[1:6] != b'CD001':
            sys.exit(f'{path}: no ISO 9660 primary volume descriptor at sector 16')
        self.volume_id = pvd[40:72].decode('ascii', 'replace').strip()
        self.sectors = struct.unpack_from('<I', pvd, 80)[0]
        self.root = self.record(pvd, 156)

    def read(self, lba, size):
        self.f.seek(lba * SECTOR)
        return self.f.read(size)

    @staticmethod
    def record(buf, at):
        n = buf[at]
        lba = struct.unpack_from('<I', buf, at + 2)[0]
        size = struct.unpack_from('<I', buf, at + 10)[0]
        flags = buf[at + 25]
        name_len = buf[at + 32]
        name = buf[at + 33:at + 33 + name_len].decode('ascii', 'replace')
        return dict(len=n, lba=lba, size=size, dir=bool(flags & 2), name=name)

    def entries(self, directory):
        data = self.read(directory['lba'], directory['size'])
        at = 0
        while at < len(data):
            if data[at] == 0:  # records never cross a sector: skip to the next one
                at = (at // SECTOR + 1) * SECTOR
                continue
            r = self.record(data, at)
            at += r['len']
            if r['name'] in ('\x00', '\x01'):
                continue
            r['name'] = r['name'].split(';')[0]
            yield r

    def walk(self, directory=None, prefix=''):
        for r in self.entries(directory or self.root):
            path = prefix + r['name']
            if r['dir']:
                yield from self.walk(r, path + '/')
            else:
                yield path, r

    def extract(self, record, dest):
        os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
        left, lba = record['size'], record['lba']
        with open(dest, 'wb') as out:
            while left:
                chunk = min(left, 64 * SECTOR * 16)
                out.write(self.read(lba, chunk))
                lba += chunk // SECTOR
                left -= chunk


def main():
    p = argparse.ArgumentParser()
    p.add_argument('iso')
    p.add_argument('--extract', nargs='*', help='paths on the disc (case-insensitive); none = every file')
    p.add_argument('--out', default='local/extract')
    a = p.parse_args()
    iso = Iso(a.iso)
    files = list(iso.walk())
    if a.extract is None:
        print(f'# volume {iso.volume_id}, {iso.sectors} sectors, {len(files)} files')
        for path, r in sorted(files, key=lambda x: x[1]['lba']):
            print(f"{r['lba']:8d} {r['size']:12d} {path}")
        return
    wanted = {w.upper() for w in a.extract}
    for path, r in files:
        if not wanted or path.upper() in wanted:
            iso.extract(r, os.path.join(a.out, path))
            print(f"extracted {path} ({r['size']} bytes)")


if __name__ == '__main__':
    main()
