#!/usr/bin/env python3
"""Apply the FSTEM SUBSTRATE_BY_CODE pin block to 01_migrate_db.py.

The pin block has been printed by register.sh on every run since batch 014 and
has never been applied. This applies it, verifies the result, and restores the
backup if verification fails.

    python3 apply_pins.py                     # report only, writes nothing
    python3 apply_pins.py --apply             # back up, write, verify
    python3 apply_pins.py --check-db --db ../out/fstem.db   # compare to assets

The script never touches the database. It edits one Python source file.
"""
VERSION = "apply_pins v023 (AXIOM-023)"

import argparse
import ast
import os
import py_compile
import re
import shutil
import sqlite3
import sys
import time

# Thirty-two codes. Three from batch 014 through two from batch 022.
PINS = {
    '1.264J':  ('partial', 'web services block dead, database block live; batch 014'),
    '16.852J': ('partial', 'LAI assessment apparatus unmaintained; batch 014'),
    'ESD.290': ('partial', 'architecture rebuilt, legal layer superseded; batch 014'),
    '2.854':   ('live',    'queueing and Markov method; batch 015'),
    '2.852':   ('live',    'decomposition proofs; batch 015'),
    '2.830J':  ('live',    'SPC and DOE method; M4 diverges partial; batch 015'),
    '2.75':    ('live',    'kinematics and elasticity; Fall 2001 vintage; batch 015'),
    '2.875':   ('live',    'constraint and variation propagation; batch 016'),
    '15.066J': ('live',    'LP, IP, simulation, NLP theory; batch 016'),
    '15.783J': ('live',    'development process; M3 diverges partial; batch 016'),
    '15.980J': ('partial', 'co-location and geographic layers superseded; batch 016'),
    '15.792J': ('dead',    'seminar content never reached OCW; M1 diverges partial; batch 017'),
    'ESD.33':  ('partial', 'INCOSE process layer and three of five cases superseded; batch 017'),
    'ESD.34':  ('live',    'form to function mapping and decomposition; batch 017'),
    'ESD.36':  ('partial', 'EVM practice layer and dispersed-team layer superseded; batch 017'),
    '15.401':  ('live',    'discounting and mean-variance method; M1 diverges partial; batch 018'),
    '15.402':  ('live',    'MM theorems and valuation arithmetic; M1 diverges partial; batch 018'),
    '15.433':  ('partial', 'anomaly record, credit and active-management layers superseded; batch 018'),
    '15.450':  ('live',    'Ito calculus, dynamic programming, estimation theory; batch 018'),
    '15.414':  ('live',    'valuation and cost of capital method; M3 diverges partial; batch 019'),
    '15.511':  ('partial', 'recognition, lease and combination layers superseded; M1 diverges live; batch 019'),
    '15.997':  ('partial', 'trading ops, hedge accounting and governance superseded; batch 019'),
    '15.617':  ('partial', 'review standards, exemption catalogue and regulatory order superseded; batch 019'),
    '18.655':  ('live',    'decision theory, sufficiency, estimation, asymptotics; batch 020'),
    '14.381':  ('live',    'probability, estimation, likelihood, testing theory; batch 020'),
    '14.382':  ('live',    'regression, GMM, orthogonalised estimation; M3 diverges partial; batch 020'),
    '14.454':  ('partial', 'sudden stop framing, run calibration and policy regime superseded; batch 020'),
    '15.S12':  ('partial', 'limits, policy posture and consortium layer superseded; M1 live, M4 dead; batch 021'),
    '15.483':  ('partial', 'conduct standard, pricing rules and platform layers superseded; M1 diverges live; batch 021'),
    '15.431':  ('live',    'venture valuation, waterfall and fund economics; M4 diverges partial; batch 021'),
    '16.863J': ('live',    'STAMP causality, CAST and STPA; M4 diverges partial; batch 022'),
    '15.760B': ('live',    'process analysis, newsvendor and production control; M4 diverges partial; batch 022'),
    '14.121':  ('live',    'preference, demand, production and choice theory; batch 023'),
    '14.122':  ('live',    'game theoretic method, refinements taught as arguments; batch 023'),
    '14.126':  ('live',    'structured game classes and type spaces; Spring 2024; batch 023'),
    '14.451':  ('live',    'dynamic programming and growth derivations; M1 diverges partial; batch 023'),
}

DICT_RE = re.compile(r'^([ \t]*)SUBSTRATE_BY_CODE[ \t]*=[ \t]*\{', re.M)


SUBSTR_RE = re.compile(r'^([ \t]*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.{0,40})', re.M)


def diagnose(directory):
    """Report every assignment in the directory whose name mentions substrate,
    and every file that mentions the phrase at all. Writes nothing."""
    print("Scanning %s for the classifier table." % os.path.abspath(directory))
    hits = 0
    pyfiles = sorted(f for f in os.listdir(directory) if f.endswith(('.py', '.sh')))
    if not pyfiles:
        print("  no .py or .sh files here. Are you in fstem_pack?")
        return 1
    for fn in pyfiles:
        path = os.path.join(directory, fn)
        try:
            src = open(path, encoding='utf-8', errors='replace').read()
        except Exception as exc:
            print("  %-28s unreadable (%s)" % (fn, exc))
            continue
        named = []
        for m in SUBSTR_RE.finditer(src):
            if 'substrate' in m.group(2).lower() or 'pin' in m.group(2).lower():
                line = src[:m.start()].count('\n') + 1
                named.append((line, m.group(1), m.group(2), m.group(3).strip()))
        mentions = src.lower().count('substrate')
        if named or mentions:
            print("  %-28s %3d mentions of 'substrate'" % (fn, mentions))
            hits += 1
        for line, indent, name, rhs in named:
            shape = 'dict literal' if rhs.startswith('{') else 'NOT a dict literal'
            scope = 'module level' if indent == '' else 'indented %d' % len(indent)
            print("      line %-5d %-26s %s · %s" % (line, name, shape, scope))
            print("        %s= %s" % (' ' * 0, rhs[:60]))
    if not hits:
        print("  Nothing in this directory mentions 'substrate' at all.")
        print("  The classifier may live elsewhere, or under another name.")
    print()
    print("What to do with this:")
    print("  If a dict literal appears above under a different name, rerun with")
    print("      python3 apply_pins.py --apply --name <THAT_NAME> --file <THAT_FILE>")
    print("  If the table is built at runtime from a CSV or the database, the pin")
    print("  block is the wrong remedy and the handoff item needs rewriting.")
    return 0


def locate_dict(src, name='SUBSTRATE_BY_CODE'):
    """Return (start, end, indent) spanning the SUBSTRATE_BY_CODE literal."""
    rx = DICT_RE if name == 'SUBSTRATE_BY_CODE' else re.compile(
        r'^([ \t]*)' + re.escape(name) + r'[ \t]*=[ \t]*\{', re.M)
    m = rx.search(src)
    if not m:
        return None
    start = m.start()
    i = src.index('{', m.start())
    depth = 0
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return start, i + 1, m.group(1)
        i += 1
    return None


def render(existing, name='SUBSTRATE_BY_CODE'):
    """Render the merged dict, existing entries first, then the pins."""
    merged = dict(existing)
    lines = ["%s = {" % name]
    for code, (status, note) in PINS.items():
        merged[code] = status
    keep = [k for k in existing if k not in PINS]
    for k in keep:
        lines.append("    %-10s %r,   # carried, not in the pin block" % ("'%s':" % k, existing[k]))
    for code, (status, note) in PINS.items():
        lines.append("    %-10s %-10s # %s" % ("'%s':" % code, "'%s'," % status, note))
    lines.append("}")
    return "\n".join(lines), merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', default='01_migrate_db.py')
    ap.add_argument('--apply', action='store_true', help='write the file (default reports only)')
    ap.add_argument('--check-db', action='store_true', help='also compare the pins against assets')
    ap.add_argument('--db', default='../out/fstem.db')
    ap.add_argument('--name', default='SUBSTRATE_BY_CODE', help='dict name to target')
    ap.add_argument('--diagnose', action='store_true', help='find the table, write nothing')
    a = ap.parse_args()
    print(VERSION)

    if a.diagnose:
        return diagnose(os.path.dirname(os.path.abspath(a.file)) or '.')

    if not os.path.exists(a.file):
        print("ABORT: %s not found. Run from fstem_pack." % a.file)
        return 1
    src = open(a.file, encoding='utf-8').read()
    loc = locate_dict(src, a.name)
    if not loc:
        print("ABORT: no %s dict literal found in %s." % (a.name, a.file))
        print("       Nothing was written.")
        print("       Run the diagnostic to find what is actually there:")
        print("           python3 apply_pins.py --diagnose")
        return 1
    start, end, indent = loc
    if indent.strip() != '':
        print("ABORT: SUBSTRATE_BY_CODE is indented, so it is not a module-level literal.")
        return 1

    try:
        existing = ast.literal_eval(src[src.index('{', start):end])
    except Exception as exc:
        print("ABORT: could not parse the existing dict as a literal (%s)." % exc)
        return 1
    if not isinstance(existing, dict):
        print("ABORT: SUBSTRATE_BY_CODE is not a dict literal.")
        return 1

    pinned = missing = wrong = 0
    for code, (status, _) in PINS.items():
        if code not in existing:
            print("  MISSING  %-10s should be %s" % (code, status))
            missing += 1
        elif existing[code] != status:
            print("  WRONG    %-10s reads %-8s should be %s" % (code, existing[code], status))
            wrong += 1
        else:
            pinned += 1
    print("  %d pinned · %d missing · %d wrong · %d codes total in file" %
          (pinned, missing, wrong, len(existing)))

    if a.check_db and os.path.exists(a.db):
        # assets has no mit_code column in this schema. Introspect rather than
        # assume, because a wrong guess here is the trap the handoff flags twice.
        con = sqlite3.connect(a.db)
        cols = [r[1] for r in con.execute("PRAGMA table_info(assets)")]
        key = 'mit_code' if 'mit_code' in cols else None
        if key is None:
            print("  assets carries no mit_code column, so the pin codes cannot be")
            print("  matched against it directly. Columns present: %s" % ", ".join(cols))
            print("  Skipping the drift check. Use script 06 as the authority.")
        else:
            drift = matched = 0
            for code, (status, _) in PINS.items():
                row = con.execute(
                    "SELECT substrate_status FROM assets WHERE %s=?" % key, (code,)).fetchone()
                if not row:
                    continue
                matched += 1
                if row[0] and row[0] != status:
                    print("  DB DRIFT %-10s database reads %s, pin says %s" % (code, row[0], status))
                    drift += 1
            print("  %d of %d codes matched · %d drift rows" % (matched, len(PINS), drift))
        con.close()

    if missing == 0 and wrong == 0:
        print("All %d codes are already pinned. Nothing to do." % len(PINS))
        return 0
    if not a.apply:
        print("\nReport only. Rerun with --apply to write:")
        print("    python3 apply_pins.py --apply")
        return 0

    block, merged = render(existing, a.name)
    backup = "%s.bak.%d" % (a.file, int(time.time()))
    shutil.copy2(a.file, backup)
    new = src[:start] + block + src[end:]
    open(a.file, 'w', encoding='utf-8').write(new)

    ok = True
    try:
        py_compile.compile(a.file, doraise=True)
    except Exception as exc:
        print("VERIFY FAILED: %s no longer compiles (%s)" % (a.file, exc))
        ok = False
    if ok:
        check = open(a.file, encoding='utf-8').read()
        loc2 = locate_dict(check, a.name)
        try:
            after = ast.literal_eval(check[check.index('{', loc2[0]):loc2[1]])
        except Exception as exc:
            print("VERIFY FAILED: rewritten dict does not parse (%s)" % exc)
            ok = False
        else:
            for code, (status, _) in PINS.items():
                if after.get(code) != status:
                    print("VERIFY FAILED: %s reads %r after write" % (code, after.get(code)))
                    ok = False
            for code in existing:
                if code not in after:
                    print("VERIFY FAILED: carried code %s was dropped" % code)
                    ok = False

    if not ok:
        shutil.copy2(backup, a.file)
        print("Restored %s from %s. Nothing changed." % (a.file, backup))
        return 1

    print("Wrote %d codes to %s. Backup at %s." % (len(after), a.file, backup))
    print("Rerun 01_migrate_db.py only when you intend to reclassify.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
