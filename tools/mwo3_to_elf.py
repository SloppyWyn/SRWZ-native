"""Wraps Metrowerks MWo3 overlays (unpacked by tools/unpack.py) as minimal ELF files, so
Ghidra and PS2Recomp can read them like the boot program: one PT_LOAD at the overlay's
load address holding the whole blob (the 0x40-byte header is loaded too: it is in
memory at the base, which is how the runtime tells overlays apart), plus .text, .data
and .bss sections.

Layout: 0x00-0x3f header; the header's text size counts from 0x40, but the first bytes
of that are zero padding (code starts at 0x80 in every overlay seen so far). .text and the
entry point start at the first non-zero word, so the padding isn't analysed as code.

    python tools/mwo3_to_elf.py local/code/stage/*.bin local/code/compdata.bin --out local/code/elf
"""
import argparse
import os
import struct

HEADER = 0x40
EF_MIPS_R5900 = 0x20924000  # the boot ELF's e_flags (o32, mips3, R5900)


def mwo3(blob):
    if blob[:4] != b'MWo3':
        raise ValueError('not an MWo3 overlay')
    version, addr, text, data, bss, _, _ = struct.unpack_from('<7I', blob, 4)
    name = blob[0x20:0x40].split(b'\0')[0].decode('ascii')
    return dict(name=name, version=version, addr=addr, text=text, data=data, bss=bss)


def build_elf(blob):
    h = mwo3(blob)
    addr, text, data, bss = h['addr'], h['text'], h['data'], h['bss']
    if HEADER + text + data > len(blob):
        raise ValueError(f"{h['name']}: header sizes exceed the file")
    code = HEADER
    while code < HEADER + text and blob[code:code + 4] == b'\0\0\0\0':
        code += 4
    shstr = b'\0.text\0.data\0.bss\0.shstrtab\0'
    names = {n: shstr.index(n.encode()) for n in ('.text', '.data', '.bss', '.shstrtab')}

    ehsize, phsize, shsize = 52, 32, 40
    blob_off = 0x100  # after the ELF header and the program header, aligned
    shstr_off = blob_off + len(blob)
    sh_off = (shstr_off + len(shstr) + 3) & ~3

    out = bytearray(sh_off + 5 * shsize)
    struct.pack_into('<4sBBBB8xHHIIIIIHHHHHH', out, 0,
                     b'\x7fELF', 1, 1, 1, 0,          # 32-bit, little-endian, version 1
                     2, 8, 1,                         # ET_EXEC, EM_MIPS, version
                     addr + code, ehsize, sh_off,     # entry (start of the code), phoff, shoff
                     EF_MIPS_R5900, ehsize, phsize, 1, shsize, 5, 4)
    struct.pack_into('<8I', out, ehsize,
                     1, blob_off, addr, addr, len(blob), len(blob) + bss, 7, 0x10)  # PT_LOAD RWX
    out[blob_off:blob_off + len(blob)] = blob
    out[shstr_off:shstr_off + len(shstr)] = shstr

    def section(i, name, kind, flags, vaddr, offset, size, align):
        struct.pack_into('<10I', out, sh_off + i * shsize,
                         name, kind, flags, vaddr, offset, size, 0, 0, align, 0)

    section(1, names['.text'], 1, 0x6, addr + code, blob_off + code, HEADER + text - code, 0x10)  # ALLOC|EXEC
    section(2, names['.data'], 1, 0x3, addr + HEADER + text, blob_off + HEADER + text, data, 0x10)  # ALLOC|WRITE
    section(3, names['.bss'], 8, 0x3, addr + HEADER + text + data, blob_off + HEADER + text + data, bss, 0x10)
    section(4, names['.shstrtab'], 3, 0, 0, shstr_off, len(shstr), 1)
    return h, bytes(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('overlays', nargs='+')
    p.add_argument('--out', default='local/code/elf')
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for path in a.overlays:
        h, elf = build_elf(open(path, 'rb').read())
        dest = os.path.join(a.out, os.path.splitext(h['name'])[0] + '.elf')
        open(dest, 'wb').write(elf)
    print(f'wrote {len(a.overlays)} ELF files to {a.out}')


if __name__ == '__main__':
    main()
