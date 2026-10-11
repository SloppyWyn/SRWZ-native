"""Builds the recompiler config for the boot program from two sources:

  - Ghidra's export (ps2xRecomp/tools/ghidra/ExportPS2Functions.java): the function map
    and the kernel-call wrappers it recognised;
  - PS2Recomp's analyzer (ps2_analyzer.exe), whose embedded SCE SDK database names the
    Sony library functions in a stripped program: its `stubs` (functions the runtime
    replaces), `entry_points` (named SDK functions kept as recompiled code) and `[mmio]`.

The analyzer's `[patches]` are left out on purpose: for this game they would turn 15
ordinary `sw` instructions (in malloc's unlock, HiG setup, ...) into NOPs, because they
store into the loaded image's range.

config/names.txt adds display names (entry_points) for guest functions found by hand or taken
from other people's notes. ps2_recomp uses an entry_points name only for the guest name table
when the address is already a function start in the Ghidra map, so each one is checked
against that map (and against the bindings) and refused otherwise: a name never changes code.

config/guest.txt lists SDK functions the game keeps as its own (guest) code even though a
source binds them to a runtime handler: typically a library whose HLE stub only covers part
of what the real one does, when the game's own copy can talk to the real IOP module instead
(SRWZ: libsdr's sceSdRemote* RPCs to sdrdrv). Each "name@address" is dropped from the stubs
and kept as a named entry point; an entry that matches no binding is reported.

    python tools/make_config.py local/recomp/ghidra.toml local/recomp/analyzer.toml local/recomp/srwz.toml
        [--output <dir>]   generated code goes there instead of ghidra.toml's output (a private copy)
        [--guest <file>]   the keep-as-guest list (default config/guest.txt)
"""
import argparse
import os
import sys
import tomllib

# Hand-made bindings (config/bindings.txt) win over both sources.
BINDINGS = os.path.join(os.path.dirname(__file__), '..', 'config', 'bindings.txt')
# Display names only (config/names.txt): they win over the analyzer's names, never bind.
NAMES = os.path.join(os.path.dirname(__file__), '..', 'config', 'names.txt')
# Functions kept as guest code although a source binds them (config/guest.txt).
GUEST = os.path.join(os.path.dirname(__file__), '..', 'config', 'guest.txt')
# Code entry points the function map missed (config/entries.txt, from tools/codeptrs.py):
# unlike names.txt these are NOT function starts in the Ghidra map; ps2_recomp synthesizes a
# standalone function at each one.
ENTRIES = os.path.join(os.path.dirname(__file__), '..', 'config', 'entries.txt')


def read_list(path):
    lines = []
    try:
        for line in open(path, encoding='utf-8'):
            line = line.split('#', 1)[0].strip()
            if line:
                lines.append(line)
    except FileNotFoundError:
        pass
    return lines


def ghidra_starts(csv_path):
    """Function starts of the Ghidra map (ExportPS2Functions CSV: Name,Start,End,Size)."""
    starts = set()
    for row in open(csv_path, encoding='utf-8'):
        fields = row.split(',')
        if len(fields) >= 2 and fields[1].strip().lower().startswith('0x'):
            starts.add(int(fields[1], 16))
    return starts


def binding(entry):
    name, _, addr = entry.partition('@')
    return name, int(addr, 16) if addr else None


def q(text):
    return '"' + text.replace('\\', '/').replace('"', '\\"') + '"'


def drop_guest(stubs, guest):
    """Removes the keep-as-guest entries from the stubs; returns (kept entry points, unmatched)."""
    kept, unmatched = [], []
    for entry in guest:
        name, addr = binding(entry)
        if addr is None or not name.isidentifier():
            unmatched.append(f'{entry}: needs a C identifier and an address')
        elif stubs.get(addr) == name:
            del stubs[addr]
            kept.append((name, addr))
        elif addr in stubs:
            unmatched.append(f'{entry}: the address is bound to {stubs[addr]}, not {name}')
        else:
            unmatched.append(f'{entry}: not bound by any source')
    return kept, unmatched


def add_code_entries(lines, starts, stub_addrs, have):
    """Missed code entry points (config/entries.txt): returns (added (name, addr), skipped)."""
    added, skipped = [], []
    for entry in lines:
        name, addr = binding(entry)
        if addr is None or not name.isidentifier():
            skipped.append(f'{entry}: needs a C identifier and an address')
        elif addr in stub_addrs:
            skipped.append(f'{entry}: bound to a runtime handler')
        elif addr in starts:
            skipped.append(f'{entry}: already a function start in the Ghidra map')
        elif addr in have:
            skipped.append(f'{entry}: already an entry point')
        else:
            added.append((name, addr))
            have.add(addr)
    return added, skipped


def main():
    parser = argparse.ArgumentParser(description='Merge the Ghidra export and the analyzer into the recompiler config.')
    parser.add_argument('ghidra')
    parser.add_argument('analyzer')
    parser.add_argument('target')
    parser.add_argument('--output', help="generated code folder (default: ghidra.toml's output)")
    parser.add_argument('--guest', default=GUEST, help='keep-as-guest list (default: config/guest.txt)')
    parser.add_argument('--entries', default=ENTRIES, help='missed code entry points (default: config/entries.txt)')
    parser.add_argument('--self-test', action='store_true')
    if '--self-test' in sys.argv:
        return self_test()
    args = parser.parse_args()
    ghidra = tomllib.load(open(args.ghidra, 'rb'))
    analyzer = tomllib.load(open(args.analyzer, 'rb'))
    g, a = ghidra['general'], analyzer['general']

    manual = read_list(BINDINGS)

    stubs, conflicts = {}, []
    for source in (manual, g.get('stubs', []), a.get('stubs', [])):
        for entry in source:
            name, addr = binding(entry)
            key = addr if addr is not None else name
            if key in stubs and stubs[key] != name:
                conflicts.append(f'{key:#x}: {stubs[key]} vs {name}' if addr is not None else name)
                continue
            stubs[key] = name
    guest = read_list(args.guest)
    kept, unmatched = drop_guest(stubs, guest)
    stub_addrs = {k for k in stubs if isinstance(k, int)}
    names, refused = {}, []
    starts = ghidra_starts(g['ghidra_output']) if read_list(NAMES) else set()
    for entry in read_list(NAMES):
        name, addr = binding(entry)
        if addr is None or not name.isidentifier():
            refused.append(f'{entry}: needs a C identifier and an address')
        elif addr in stub_addrs:
            refused.append(f'{entry}: bound to the runtime handler {stubs[addr]}')
        elif addr not in starts:
            refused.append(f'{entry}: not a function start in the Ghidra map')
        elif addr in names and names[addr] != name:
            refused.append(f'{entry}: already named {names[addr]} in names.txt')
        else:
            names[addr] = name
    entries = []
    renamed = 0
    for entry in a.get('entry_points', []):
        name, addr = binding(entry)
        if addr in names:
            renamed += names[addr] != name
            continue
        if addr not in stub_addrs:
            entries.append((name, addr))
    entries += [(name, addr) for addr, name in names.items()]
    have = {addr for _, addr in entries}
    entries += [(name, addr) for name, addr in kept if addr not in have]
    have = {addr for _, addr in entries}
    if not starts and read_list(args.entries):
        starts = ghidra_starts(g['ghidra_output'])
    added, skipped = add_code_entries(read_list(args.entries), starts, stub_addrs, have)
    entries += added

    out = ['# Generated by tools/make_config.py from the Ghidra export and the analyzer.', '',
           '[general]',
           f"input = {q(g['input'])}",
           f"output = {q(args.output or g['output'])}",
           f"ghidra_output = {q(g['ghidra_output'])}",
           'single_file_output = false',
           'output_group_bytes = 0x2000',
           # Ghidra code labels resume inside their owner function instead of becoming
           # entry_* fragments (PS2Recomp srwz-owner, reviewed; ~100x fewer scheduler trips).
           'resume_into_owner = true',
           # Leaner per-instruction code (PS2Recomp srwz-lean, reviewed): exe -39%, build -33%,
           # same speed. Crash/watchdog pc is block-level; rebuild with it off for an exact dig.
           'lean_codegen = true',
           'patch_syscalls = false',
           f"patch_cop0 = {str(g.get('patch_cop0', True)).lower()}",
           f"patch_cache = {str(g.get('patch_cache', True)).lower()}",
           'stubs = [']
    for key, name in sorted(stubs.items(), key=lambda kv: (isinstance(kv[0], str), kv[0])):
        out.append(f'  {q(name + "@" + format(key, "#010x")) if isinstance(key, int) else q(name)},')
    out += [']', 'untracked_stubs = []', 'entry_points = [']
    out += [f'  {q(f"{name}@{addr:#010x}")},' for name, addr in sorted(entries, key=lambda e: e[1])]
    out += [']', 'skip = []', '', '[mmio]']
    out += [f'{q(k)} = {q(v)}' for k, v in analyzer.get('mmio', {}).items()]
    open(sys.argv[3], 'w', encoding='utf-8', newline='\n').write('\n'.join(out) + '\n')

    print(f'stubs: {len(stubs)} (manual {len(manual)}, Ghidra {len(g.get("stubs", []))}, analyzer {len(a.get("stubs", []))}), '
          f'entry points: {len(entries)} (names.txt {len(names)}, {renamed} replacing an analyzer name), '
          f'mmio: {len(analyzer.get("mmio", {}))}, kept as guest code: {len(kept)}')
    print(f'  entries.txt: {len(added)} missed code entry point(s) added, {len(skipped)} skipped')
    for s in skipped:
        print('  entries.txt, skipped:', s)
    for name, addr in kept:
        print(f'  guest.txt, kept as guest code: {name}@{addr:#010x}')
    for u in unmatched:
        print('  guest.txt, unmatched:', u)
    for r in refused:
        print('  names.txt, refused:', r)
    for c in conflicts:
        print('  conflict, kept the first:', c)


def self_test():
    stubs = {0x127688: 'sceSdRemote', 0x127530: 'sceSdRemoteInit', 0x100000: 'memcpy', 'printf': 'printf'}
    kept, unmatched = drop_guest(stubs, ['sceSdRemote@0x00127688', 'sceSdRemoteInit@0x127530',
                                         'wrong@0x00100000', 'absent@0x00200000', 'noaddr'])
    assert kept == [('sceSdRemote', 0x127688), ('sceSdRemoteInit', 0x127530)], kept
    assert stubs == {0x100000: 'memcpy', 'printf': 'printf'}, stubs
    assert len(unmatched) == 3 and 'bound to memcpy' in unmatched[0] and 'not bound' in unmatched[1], unmatched
    added, skipped = add_code_entries(['fn_003eb780@0x003eb780', 'f@0x00100000', 'g@0x00200000',
                                       'h@0x00300000', 'bad name@0x1', 'fn_003eb780@0x003eb780'],
                                      starts={0x200000}, stub_addrs={0x100000}, have={0x300000})
    assert added == [('fn_003eb780', 0x3eb780)], added
    assert len(skipped) == 5 and 'runtime handler' in skipped[0] and 'Ghidra map' in skipped[1] \
        and 'already an entry' in skipped[2] and 'identifier' in skipped[3] and 'already an entry' in skipped[4], skipped
    print('make_config.py self-test passed')


if __name__ == '__main__':
    main()
