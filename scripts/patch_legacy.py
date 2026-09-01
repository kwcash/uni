#!/usr/bin/env python3
"""
patch_legacy.py — make the three extraction scripts take a path.

extract_course_semantic.py, extract_course_clean.py and extract_course_summaries.py
all hardcode this line:

    ocw1_path = os.path.expanduser('~/Projects/OCW1')

That is why they cannot be pointed at the undergraduate harvest or the Western
Civilization set. This rewrites that one line into an argument with the old
value as the default, so every existing invocation keeps working and a new one
becomes possible.

    python3 extract_course_semantic.py                       # unchanged behaviour
    python3 extract_course_semantic.py ~/Projects/OCW_UNDERGRAD
    python3 extract_course_semantic.py --root ~/Projects/OCW_WESTCIV

Nothing is overwritten without a backup. Each patched file gets a .bak sibling
on first run, and a second run is a no-op.

Usage
-----
    python3 scripts/patch_legacy.py --scripts-dir ~/Projects/scripts
    python3 scripts/patch_legacy.py --scripts-dir ~/Projects/scripts --dry-run
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import log  # noqa: E402

TARGETS = [
    "extract_course_semantic.py",
    "extract_course_clean.py",
    "extract_course_summaries.py",
    "parse_corpus.py",
    "analyze_corpus.py",
    "deep_harvest.py",
    "resource_catalog.py",
]

HARDCODE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<var>\w*path\w*|\w*dir\w*|\w*root\w*)\s*=\s*"
    r"os\.path\.expanduser\(\s*['\"](?P<value>~[^'\"]*)['\"]\s*\)\s*$",
    re.M,
)

SHIM = '''{indent}# --- FSTEM patch: accept a path instead of hardcoding one ---------------
{indent}import argparse as _fstem_argparse
{indent}_fstem_ap = _fstem_argparse.ArgumentParser(add_help=True)
{indent}_fstem_ap.add_argument("root", nargs="?", default=None,
{indent}                       help="Directory of course folders. Default {value}")
{indent}_fstem_ap.add_argument("--root", dest="root_flag", default=None,
{indent}                       help="Same as the positional argument.")
{indent}_fstem_args, _ = _fstem_ap.parse_known_args()
{indent}{var} = os.path.expanduser(
{indent}    _fstem_args.root_flag or _fstem_args.root or "{value}")
{indent}print(f"[fstem] root: {{{var}}}")
{indent}# --- end FSTEM patch -----------------------------------------------------
'''

MARKER = "# --- FSTEM patch:"


def patch_source(src: str) -> tuple[str, list[str]]:
    """Return (new_source, notes). Idempotent."""
    if MARKER in src:
        return src, ["already patched"]

    notes = []
    matches = list(HARDCODE_RE.finditer(src))
    if not matches:
        return src, ["no hardcoded expanduser path found"]

    # Patch only the first hardcoded root. Scripts have exactly one.
    m = matches[0]
    shim = SHIM.format(indent=m.group("indent"), var=m.group("var"), value=m.group("value"))
    new = src[: m.start()] + shim.rstrip("\n") + src[m.end():]
    notes.append(f"{m.group('var')} now takes a path, default {m.group('value')}")

    if len(matches) > 1:
        notes.append(f"{len(matches) - 1} further hardcoded path(s) left alone; review by hand")

    if not re.search(r"^import os\b", new, re.M):
        new = "import os\n" + new
        notes.append("added missing 'import os'")

    return new, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Make the legacy extraction scripts take a path.")
    ap.add_argument("--scripts-dir", required=True, help="Directory holding the legacy scripts.")
    ap.add_argument("--dry-run", action="store_true", help="Report what would change and write nothing.")
    args = ap.parse_args()

    sdir = os.path.abspath(os.path.expanduser(args.scripts_dir))
    if not os.path.isdir(sdir):
        log(f"not a directory: {sdir}")
        return 1

    found = []
    for name in TARGETS:
        for path in glob.glob(os.path.join(sdir, "**", name), recursive=True):
            found.append(path)
    if not found:
        log(f"none of the known scripts found under {sdir}")
        log("expected any of: " + ", ".join(TARGETS))
        return 1

    changed = 0
    for path in sorted(set(found)):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        new, notes = patch_source(src)
        rel = os.path.relpath(path, sdir)

        if new == src:
            log(f"  -  {rel:<34} {'; '.join(notes)}")
            continue

        if args.dry_run:
            log(f"  ~  {rel:<34} would patch: {'; '.join(notes)}")
            changed += 1
            continue

        bak = path + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        log(f"  ✓  {rel:<34} {'; '.join(notes)}")
        changed += 1

    log("")
    verb = "would patch" if args.dry_run else "patched"
    log(f"{verb} {changed} of {len(set(found))} scripts. Backups carry a .bak suffix.")
    if changed and not args.dry_run:
        log("")
        log("Now each script accepts a directory:")
        log("  python3 extract_course_semantic.py ~/Projects/OCW_UNDERGRAD")
        log("  python3 extract_course_semantic.py ~/Projects/OCW_WESTCIV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
