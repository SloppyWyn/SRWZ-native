"""Unpacks every piece of the game's code from your own disc image into local/ (git
ignores it): the boot program, the EE overlays and the IOP modules.

    python tools/unpack.py disc/srwz.iso

local/extract/   files copied off the disc as they are
local/code/      SLPS_258.87              boot ELF (EE), loads at 0x100000
                 compdata.bin             MWo3 overlay, loads at 0x6d6800 (DATA/COMPDATA.BN)
                 stage/<name>.bin         205 MWo3 overlays, all load at 0x756700 (DATA/STAGE.BIN)
                 iop/<module>.irx         12 IOP modules (MODULES/MODULE.BN)
                 IOPRP310.IMG             IOP replacement image (SDK 3.1.0 modules)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
from banlz import decompress  # noqa: E402
from iso9660 import Iso  # noqa: E402

FILES = ['SYSTEM.CNF', 'SLPS_258.87', 'IOPRP310.IMG', 'MODULES/MODULE.BN', 'DATA/COMPDATA.BN', 'DATA/STAGE.BIN']


def mwo3(blob):
    """MWo3 overlay header: magic, version, load address, text, data, bss, end, end, name[32]."""
    if blob[:4] != b'MWo3':
        raise ValueError('not an MWo3 overlay')
    version, addr, text, data, bss, _, _ = struct.unpack_from('<7I', blob, 4)
    name = blob[0x20:0x40].split(b'\0')[0].decode('ascii')
    return dict(name=name, version=version, addr=addr, text=text, data=data, bss=bss)


def stage_overlays(blob):
    """DATA/STAGE.BIN: LZ records back to back (zero padding between some). Each record
    holds a count, then (offset, 0, size) per overlay, then the overlays."""
    at = 0
    while at < len(blob):
        if blob[at] == 0:
            at += 1
            continue
        out, at = decompress(blob, at)
        count = struct.unpack_from('<I', out, 0)[0]
        for k in range(count):
            offset, _, size = struct.unpack_from('<III', out, 4 + k * 12)
            yield out[offset:offset + size]


def irx_modules(blob):
    """Splits the unpacked MODULE.BN at each ELF header; names come from the IOPMOD header."""
    starts = []
    at = blob.find(b'\x7fELF')
    while at >= 0:
        starts.append(at)
        at = blob.find(b'\x7fELF', at + 4)
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(blob)
        phoff, = struct.unpack_from('<I', blob, start + 28)
        phnum, = struct.unpack_from('<H', blob, start + 44)
        name = f'module{i}'
        for p in range(phnum):
            kind, offset = struct.unpack_from('<II', blob, start + phoff + p * 32)
            if kind == 0x70000080:  # IOPMOD: name at +0x1a
                name = blob[start + offset + 0x1a:start + offset + 0x40].split(b'\0')[0].decode('ascii')
        yield name, blob[start:end].rstrip(b'\0')


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    iso = Iso(sys.argv[1])
    print(f'disc {iso.volume_id}')
    files = {path.upper(): r for path, r in iso.walk()}
    extract = 'local/extract'
    for path in FILES:
        iso.extract(files[path], os.path.join(extract, path))
    code = 'local/code'
    os.makedirs(f'{code}/stage', exist_ok=True)
    os.makedirs(f'{code}/iop', exist_ok=True)
    for name in ('SLPS_258.87', 'IOPRP310.IMG'):
        with open(f'{extract}/{name}', 'rb') as src, open(f'{code}/{name}', 'wb') as dst:
            dst.write(src.read())

    comp, _ = decompress(open(f'{extract}/DATA/COMPDATA.BN', 'rb').read())
    h = mwo3(comp)
    open(f'{code}/compdata.bin', 'wb').write(comp)
    print(f"compdata.bin: loads at {h['addr']:#x}, text {h['text']:#x}, data {h['data']:#x}")

    count = text = 0
    for blob in stage_overlays(open(f'{extract}/DATA/STAGE.BIN', 'rb').read()):
        h = mwo3(blob)
        open(f"{code}/stage/{h['name']}", 'wb').write(blob)
        count += 1
        text += h['text']
    print(f'stage overlays: {count}, code {text} bytes in all')

    modules, _ = decompress(open(f'{extract}/MODULES/MODULE.BN', 'rb').read())
    names = []
    for name, blob in irx_modules(modules):
        open(f'{code}/iop/{name}.irx', 'wb').write(blob)
        names.append(name)
    print(f"IOP modules: {', '.join(names)}")


if __name__ == '__main__':
    main()
