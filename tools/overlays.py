"""Recompiles the game's code overlays and links each into one self-registering C++ file
for the runner (PS2X_OVERLAY_DIR = local/recomp/overlay_build; runtime side in
PS2Recomp's ps2_overlays.h).

Needs: the overlay ELFs (tools/mwo3_to_elf.py) and their Ghidra maps and configs in
local/recomp/overlays/<name>/ (tools/ghidra/ExportOverlayMaps.java).

    python tools/overlays.py            # every overlay
    python tools/overlays.py flow stg_001
    python tools/overlays.py --out <dir> --link-only stg_001   # reuse the last ps2_recomp output
    python tools/overlays.py --self-test
    python tools/overlays.py --resume-into-owner on|off|auto   # PS2Recomp's resume_into_owner
                                        # switch for the overlay runs; auto (default) follows
                                        # [general] resume_into_owner of local/recomp/srwz.toml
    python tools/overlays.py --lean-codegen on|off|auto        # PS2Recomp's lean_codegen, the same
                                        # way (its output includes ps2_runtime_lean_macros.h,
                                        # which is carried into ov_<name>.cpp)

Per overlay: the config is switched to single-file output, ps2_recomp runs, and its
output (functions, header, table) becomes local/recomp/overlay_build/ov_<name>.cpp: the
functions inside `namespace ov_<name>` (overlays reuse the same addresses, hence the same
generated names) and a static initialiser that registers the overlay's entry points
under its MWo3 name (e.g. "flow.bin") with its load address and code end, plus the
overlay's function names (ps2xRegisterOverlayFunctionNames, ps2_guest_names.h) so logs,
the watchdog, the call trace and crash reports print "<file>:<name>+off": the name table
ps2_recomp wrote into the overlay's register_functions.cpp, completed by the Ghidra map
<name>_functions.csv.
"""
import argparse
import concurrent.futures
import csv
import os
import re
import struct
import subprocess
import sys

DECL = re.compile(r'^void ([A-Za-z_][A-Za-z0-9_]*)\(uint8_t\* rdram, R5900Context\* ctx, PS2Runtime \*runtime\);$')
TABLE = re.compile(r'g_ps2RecompiledFunctionTable\[\d+\] = ([A-Za-z_][A-Za-z0-9_]*); // (0x[0-9a-fA-F]+)')
TRACE_DECL = 'void ps2xStubTrace('
# One entry of ps2_recomp's guest name table: {0x756780u, 0x75678cu, "entry", 0u},
# (the name literal is already escaped by ps2_recomp and is kept as is).
NAME_ENTRY = re.compile(r'^\s*\{0x([0-9a-fA-F]+)u, 0x([0-9a-fA-F]+)u, "((?:[^"\\]|\\.)*)", (\d+)u\},\s*$')


def set_root(root):
    global ROOT, RECOMP, OVERLAYS, CODE, BUILD, MAIN_TOML
    ROOT = os.path.abspath(root)
    RECOMP = os.path.join(ROOT, 'third_party', 'PS2Recomp', 'out', 'build', 'ps2xRecomp', 'Release', 'ps2_recomp.exe')
    OVERLAYS = os.path.join(ROOT, 'local', 'recomp', 'overlays')
    CODE = os.path.join(ROOT, 'local', 'code')
    BUILD = os.path.join(ROOT, 'local', 'recomp', 'overlay_build')
    MAIN_TOML = os.path.join(ROOT, 'local', 'recomp', 'srwz.toml')


set_root(os.path.join(os.path.dirname(__file__), '..'))

# PS2Recomp's [general] resume_into_owner for the per-overlay runs: True/False, or None =
# follow the main program's config (MAIN_TOML), so the overlays get the same code shape.
RESUME_INTO_OWNER = None
RESUME_LINE = re.compile(r'(?m)^resume_into_owner = \w+\n?')


def main_toml_switch(key, override):
    """A boolean [general] switch for the overlay runs: the override, else the main program's value."""
    if override is not None:
        return override
    try:
        text = open(MAIN_TOML, encoding='utf-8').read()
    except OSError:
        return False
    m = re.search(r'(?m)^%s = (true|false)' % key, text)
    return bool(m and m.group(1) == 'true')


def resume_into_owner():
    return main_toml_switch('resume_into_owner', RESUME_INTO_OWNER)


# PS2Recomp's [general] lean_codegen (PS2Recomp srwz-lean): same rule, and the per-trim keys
# (read after lean_codegen by ps2_recomp) are copied from the main toml too. Lean output
# includes ps2_runtime_lean_macros.h after ps2_runtime_macros.h, preceded by
# `#define PS2X_LEAN_GPR_SCALAR 1` when the scalar GPR trim is on; link() carries both over,
# the define ahead of every include (it must be seen before the lean header).
LEAN_CODEGEN = None
LEAN_TRIM_KEYS = ('lean_pc_stores', 'lean_memory_helpers', 'lean_delay_slot_flags', 'lean_gpr_scalar', 'lean_zero_fold')
LEAN_LINE = re.compile(r'(?m)^lean_(codegen|%s) = \w+\n?' % '|'.join(k[len('lean_'):] for k in LEAN_TRIM_KEYS))
LEAN_INCLUDE = '#include "ps2_runtime_lean_macros.h"'
LEAN_DEFINE = re.compile(r'^#define PS2X_LEAN_\w+ \S+$')


def lean_codegen():
    return main_toml_switch('lean_codegen', LEAN_CODEGEN)


def lean_trim_lines():
    """The per-trim lean_* lines of the main toml, verbatim (only those present there)."""
    try:
        text = open(MAIN_TOML, encoding='utf-8').read()
    except OSError:
        return ''
    return ''.join(m.group(0) + '\n' for m in re.finditer(r'(?m)^(%s) = (true|false)$' % '|'.join(LEAN_TRIM_KEYS), text))


def c_string(text):
    """Escapes a name for a C string literal the way ps2_recomp does (printable ASCII only)."""
    out = []
    for ch in text:
        if not ' ' <= ch <= '~':
            continue
        if ch in '"\\?':  # '?': no trigraphs
            out.append('\\')
        out.append(ch)
    return ''.join(out)


def recomp_names(register_cpp):
    """{start: (end, escaped name, flags)} from ps2_recomp's register_functions.cpp (empty
    for output generated before the recompiler emitted a name table)."""
    names = {}
    for line in register_cpp.splitlines():
        m = NAME_ENTRY.match(line)
        if m:
            names[int(m.group(1), 16)] = (int(m.group(2), 16), m.group(3), int(m.group(4)))
    return names


def ghidra_names(csv_path):
    """{start: (end, escaped name, 0)} from a Ghidra map (Name,Start,End,Size; End inclusive)."""
    names = {}
    if not os.path.exists(csv_path):
        return names
    with open(csv_path, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            try:
                start, end = int(row['Start'], 16), int(row['End'], 16)
            except (KeyError, TypeError, ValueError):
                continue
            name = c_string((row.get('Name') or '').strip())
            if name:
                names[start] = (end + 1 if end >= start else 0, name, 0)
    return names


def name_table(register_cpp, csv_path):
    """The overlay's names sorted by start: ps2_recomp's table (it knows fragments and stubs)
    plus any Ghidra map entry at a start it lacks."""
    merged = ghidra_names(csv_path)
    merged.update(recomp_names(register_cpp))
    return [(start,) + merged[start] for start in sorted(merged)]


def mwo3_info(name):
    path = os.path.join(CODE, 'compdata.bin' if name == 'compdata' else os.path.join('stage', name + '.bin'))
    blob = open(path, 'rb').read(0x40)
    if blob[:4] != b'MWo3':
        raise ValueError(f'{path}: not an MWo3 overlay')
    _, addr, text = struct.unpack_from('<3I', blob, 4)
    file_name = blob[0x20:0x40].split(b'\0')[0].decode('ascii')
    return file_name, addr, addr + 0x40 + text


# Missed code entry points per overlay (tools/codeptrs.py --overlays, committed): added to the
# overlay's entry_points so ps2_recomp synthesizes them (vtable-only functions, Wall 16).
OVERLAY_ENTRIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'overlay_entries.txt')


def overlay_entries(path=None):
    """{overlay name: ["name@0xaddr", ...]} from config/overlay_entries.txt ([name] sections)."""
    out, cur = {}, None
    try:
        lines = open(path or OVERLAY_ENTRIES, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            cur = line[1:-1].strip()
            out.setdefault(cur, [])
        elif cur and '@' in line:
            out[cur].append(line.split()[0])
    return out


ENTRY_POINTS_BLOCK = re.compile(r'(?ms)^entry_points = \[.*?^\]\n')


def set_entry_points(text, entries):
    """The toml text with [general] entry_points replaced by these entries (none: removed)."""
    text = ENTRY_POINTS_BLOCK.sub('', text)
    if not entries:
        return text
    block = 'entry_points = [\n' + ''.join(f'  "{e}",\n' for e in entries) + ']\n'
    return text.replace('skip = []', block + 'skip = []', 1)


def recompile(name):
    folder = os.path.join(OVERLAYS, name)
    config = os.path.join(folder, name + '.toml')
    text = open(config, encoding='utf-8').read()
    text = re.sub(r'(?m)^single_file_output = \w+', 'single_file_output = true', text)
    # Serial output per overlay: PS2Recomp's parallel single-file writer can fail with
    # "combined output completion queue is missing index N"; the overlays already run in
    # parallel with each other.
    text = re.sub(r'(?m)^output_worker_threads = \d+\n', '', text)
    text = text.replace('single_file_output = true', 'single_file_output = true\noutput_worker_threads = 1', 1)
    # The same resume_into_owner as the main program (see resume_into_owner()).
    text = RESUME_LINE.sub('', text)
    text = text.replace('single_file_output = true',
                        'single_file_output = true\nresume_into_owner = %s' % ('true' if resume_into_owner() else 'false'), 1)
    text = LEAN_LINE.sub('', text)
    text = text.replace('single_file_output = true',
                        'single_file_output = true\nlean_codegen = %s\n' % ('true' if lean_codegen() else 'false') + lean_trim_lines().rstrip('\n'), 1)
    text = set_entry_points(text, overlay_entries().get(name, []))
    open(config, 'w', encoding='utf-8', newline='\n').write(text)
    # CREATE_NO_WINDOW: no console window when the caller has none (tools/launch.ps1).
    result = subprocess.run([RECOMP, config], capture_output=True, text=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    open(os.path.join(folder, 'recomp.log'), 'w', encoding='utf-8').write(result.stdout + result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f'{name}: ps2_recomp failed ({result.returncode}), see {folder}/recomp.log')


def link(name):
    out = os.path.join(OVERLAYS, name, 'output')
    file_name, base, code_end = mwo3_info(name)
    ident = 'ov_' + re.sub(r'[^A-Za-z0-9_]', '_', name)

    decls = []
    for header in ('ps2_recompiled_functions.h', 'ps2_recompiled_stubs.h'):
        path = os.path.join(out, header)
        if os.path.exists(path):
            decls += [line.rstrip() for line in open(path, encoding='utf-8') if DECL.match(line.rstrip())]

    body, uses_trace, lean, defines = [], False, False, []
    for line in open(os.path.join(out, 'ps2_recompiled_functions.cpp'), encoding='utf-8'):
        if line.startswith('#include'):
            lean = lean or line.strip() == LEAN_INCLUDE
            continue
        if LEAN_DEFINE.match(line.strip()):
            defines.append(line.strip())  # PS2X_LEAN_GPR_SCALAR etc.: ahead of the includes below
            continue
        if line.startswith(TRACE_DECL):
            uses_trace = True  # declared once at file scope below, not inside the namespace
            continue
        body.append(line.rstrip('\n'))

    register_cpp = open(os.path.join(out, 'register_functions.cpp'), encoding='utf-8').read()
    entries = TABLE.findall(register_cpp)
    if not entries:
        raise ValueError(f'{name}: no entry points found in register_functions.cpp')
    names = name_table(register_cpp, os.path.join(OVERLAYS, name, name + '_functions.csv'))

    lines = [
        f'// Generated by tools/overlays.py from the PS2Recomp output for {file_name}.',
        f'// Loads at {base:#x}; code ends at {code_end:#x}; {len(entries)} entry points, {len(names)} names.',
    ] + defines + [
        '#include <cstdint>',
        '#include <stdexcept>',
        '#include "ps2_runtime_macros.h"',
    ] + ([LEAN_INCLUDE] if lean else []) + [
        '#include "ps2_runtime.h"',
        '#include "ps2_syscalls.h"',
        '#include "ps2_stubs.h"',
        '#include "ps2_overlays.h"',
        '#include "ps2_guest_names.h"',
        '#ifdef _DEBUG',
        '#include "ps2_log.h"',
        '#endif',
        '',
    ]
    if uses_trace:
        lines += ['void ps2xStubTrace(const char *name, uint32_t address, uint8_t *rdram, R5900Context *ctx, bool leaving);', '']
    lines += [f'namespace {ident}', '{'] + decls + [''] + body + [f'}} // namespace {ident}', '']
    lines += ['namespace', '{', 'const PS2OverlayEntry kEntries[] = {']
    lines += [f'    {{{addr}u, &{ident}::{fn}}},' for fn, addr in entries]
    lines += ['};',
              f'const bool kRegistered = ps2xRegisterOverlay("{file_name}", {base:#x}u, {code_end:#x}u, kEntries,',
              '                                              sizeof(kEntries) / sizeof(kEntries[0]));']
    if names:
        lines += ['const PS2GuestFunctionName kNames[] = {']
        lines += [f'    {{{start:#x}u, {end:#x}u, "{text}", {flags}u}},' for start, end, text, flags in names]
        lines += ['};',
                  f'const bool kNamesRegistered = ps2xRegisterOverlayFunctionNames("{file_name}", kNames,',
                  '                                                             sizeof(kNames) / sizeof(kNames[0]));']
    lines += ['}', '']
    os.makedirs(BUILD, exist_ok=True)
    target = os.path.join(BUILD, ident + '.cpp')
    new = '\n'.join(lines)
    if not os.path.exists(target) or open(target, encoding='utf-8').read() != new:
        open(target, 'w', encoding='utf-8', newline='\n').write(new)  # untouched files don't rebuild
    return len(entries)


def process(name, link_only=False):
    if not link_only:
        recompile(name)
    return name, link(name)


def self_test():
    import tempfile
    register = ('g_ps2RecompiledFunctionTable[0] = entry; // 0x756780\n'
                'const PS2GuestFunctionName g_ps2GuestFunctionNames[] = {\n'
                '    {0x756780u, 0x75678cu, "entry", 0u},\n'
                '    {0x7567e8u, 0x756808u, "entry_007567e8", 1u},\n'
                '    {0x756900u, 0x756910u, "a\\"b", 2u},\n'
                '};\n')
    expected = {0x756780: (0x75678c, 'entry', 0), 0x7567e8: (0x756808, 'entry_007567e8', 1),
                0x756900: (0x756910, 'a\\"b', 2)}
    assert recomp_names(register) == expected, recomp_names(register)
    assert c_string('x"y\\z?\x01') == 'x\\"y\\\\z\\?', c_string('x"y\\z?\x01')
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'm_functions.csv')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('Name,Start,End,Size\nGhidraEntry,0x00756780,0x0075678B,12\n'
                    'Extra,0x00756A00,0x00756A0F,16\nbad,zz,0,0\n')
        table = name_table(register, path)
        assert table == [(0x756780, 0x75678c, 'entry', 0), (0x7567e8, 0x756808, 'entry_007567e8', 1),
                         (0x756900, 0x756910, 'a\\"b', 2), (0x756a00, 0x756a10, 'Extra', 0)], table
        assert name_table('', os.path.join(tmp, 'missing.csv')) == []
        assert name_table('', path)[0] == (0x756780, 0x75678c, 'GhidraEntry', 0)
    assert LEAN_DEFINE.match('#define PS2X_LEAN_GPR_SCALAR 1') and not LEAN_DEFINE.match('#define FOO 1')
    assert LEAN_LINE.sub('', 'a\nlean_codegen = true\nlean_gpr_scalar = false\nlean_zero_fold = true\nb\n') == 'a\nb\n'
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'entries.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('# c\n\n[stg_501]\nfn_00757280@0x00757280  # 13 ref(s)\n\n[flow]\nfn_1@0x1\n')
        got = overlay_entries(path)
        assert got == {'stg_501': ['fn_00757280@0x00757280'], 'flow': ['fn_1@0x1']}, got
    toml = 'stubs = [\n]\nskip = []\n'
    once = set_entry_points(toml, ['a@0x1'])
    assert once == 'stubs = [\n]\nentry_points = [\n  "a@0x1",\n]\nskip = []\n', once
    assert set_entry_points(once, ['b@0x2']).count('entry_points') == 1 and '"b@0x2"' in set_entry_points(once, ['b@0x2'])
    assert set_entry_points(once, []) == toml
    print('overlays.py self-test passed')


def main():
    global BUILD
    parser = argparse.ArgumentParser(description='Recompile the code overlays and link each into ov_<name>.cpp.')
    parser.add_argument('names', nargs='*', help='overlays (folders under local/recomp/overlays); default: all')
    parser.add_argument('--root', help='project root holding local/ and third_party/ (default: this checkout)')
    parser.add_argument('--out', help='output folder (default: <root>/local/recomp/overlay_build)')
    parser.add_argument('--link-only', action='store_true', help='skip ps2_recomp and link its last output')
    parser.add_argument('--self-test', action='store_true', help='run the name table self-test and exit')
    parser.add_argument('--resume-into-owner', choices=['auto', 'on', 'off'], default='auto',
                        help="PS2Recomp's resume_into_owner for the overlay runs (auto: as in local/recomp/srwz.toml)")
    parser.add_argument('--lean-codegen', choices=['auto', 'on', 'off'], default='auto',
                        help="PS2Recomp's lean_codegen for the overlay runs (auto: as in local/recomp/srwz.toml)")
    args = parser.parse_args()
    global RESUME_INTO_OWNER, LEAN_CODEGEN
    RESUME_INTO_OWNER = None if args.resume_into_owner == 'auto' else args.resume_into_owner == 'on'
    LEAN_CODEGEN = None if args.lean_codegen == 'auto' else args.lean_codegen == 'on'
    if args.self_test:
        self_test()
        return
    if args.root:
        set_root(args.root)
    if args.out:
        BUILD = os.path.abspath(args.out)
    names = args.names or sorted(d for d in os.listdir(OVERLAYS) if os.path.isdir(os.path.join(OVERLAYS, d)))
    total, failed = 0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for future in concurrent.futures.as_completed([pool.submit(process, n, args.link_only) for n in names]):
            try:
                name, count = future.result()
                total += count
            except Exception as e:  # report every failure, keep going
                failed.append(str(e))
    print(f'{len(names) - len(failed)} overlays linked into {BUILD}, {total} entry points')
    for message in failed:
        print('FAILED:', message)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
