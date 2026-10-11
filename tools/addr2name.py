"""Names the function containing each address in the boot program, from the recompiler
config (SDK names from the analyzer) and Ghidra's function map.

    python tools/addr2name.py 0x140e3c 0x12627c
"""
import bisect
import csv
import re
import sys

CONFIG = 'local/recomp/srwz.toml'
GHIDRA_CSV = 'local/recomp/srwz_functions.csv'


def load():
    names = {}
    with open(GHIDRA_CSV, newline='') as f:
        for row in csv.DictReader(f):
            try:
                names[int(row['Start'], 16)] = (row['Name'], int(row['End'], 16))
            except (KeyError, ValueError):
                continue
    for name, addr in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)@0x([0-9A-Fa-f]+)"', open(CONFIG).read()):
        a = int(addr, 16)
        names[a] = (name, names.get(a, (None, a))[1])
    return names


def main():
    names = load()
    starts = sorted(names)
    for arg in sys.argv[1:]:
        a = int(arg, 16)
        i = bisect.bisect_right(starts, a) - 1
        if i < 0:
            print(f'{a:#x}: before the first function')
            continue
        name, end = names[starts[i]]
        inside = '' if a <= end else f'  (past its end {end:#x}: may belong to an unnamed function)'
        print(f'{a:#x}: {name}+{a - starts[i]:#x}{inside}')


if __name__ == '__main__':
    main()
