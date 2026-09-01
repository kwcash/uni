#!/usr/bin/env python3
"""
recover_levels.py — get the course level back out of the bloated corpus.

The aggressive extractor deleted `Level`, `Undergraduate` and `Graduate` as
repeated sidebar furniture. That deletion is the origin of the misfiled forty.
The bloated extractor kept the sidebar, so the field was never lost. It sits in
ALL_SUMMARY_BLOATED.txt, one block per course, and nobody had to re-harvest
anything to get it back.

Measured on the real 231-course corpus:

    ALL_SEMANTIC_AGGRESSIVE.txt   0 Level markers, rich content
    ALL_CLEAN_MODERATE.txt        0 Level markers, no content at all
    ALL_SUMMARY_BLOATED.txt     920 Level markers, full sidebar

Reads the bloated file, writes an overrides CSV the merge stage consumes.

Usage
-----
    python3 scripts/recover_levels.py \\
        --in ~/Projects/Consolidation/analysis/ALL_SUMMARY_BLOATED.txt \\
        --out overrides/level_corrections.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import normalise_code, log  # noqa: E402

# The bloated extractor delimits records with a FRONT PAGE banner rather than
# the COURSE: header the semantic extractor uses.
RECORD_RE = re.compile(r"(?m)^=+\s*\nFRONT PAGE - (.+?)\s*\n=+\s*$")
COURSE_RE = re.compile(r"(?m)^COURSE:\s*(.+?)\s*$")

# The sidebar prints Level then one or two values on the following lines.
LEVEL_BLOCK_RE = re.compile(
    r"(?m)^[ \t]*Level[ \t]*$\s*((?:[ \t]*(?:Undergraduate|Graduate)[ \t]*\n){1,3})")
LEVEL_INLINE_RE = re.compile(r"\bLevel\b[^A-Za-z]{0,20}(Undergraduate|Graduate)", re.I)

LEVEL_TO_RUNG = {
    "Graduate": "masters",
    "Undergraduate": "undergraduate",
    "Undergraduate and Graduate": "undergraduate",
    "Graduate and Undergraduate": "undergraduate",
}

SLUG_CODE_RE = re.compile(
    r"^([0-9]{1,2}[a-z]?[.\-][0-9a-z]{1,6}(?:-[0-9]+)?|[a-z]{2,5}[.\-][0-9a-z\-]{1,10}?)"
    r"(?=-(?:fall|spring|summer|winter|january|iap)\b|$)", re.I)


def slug_to_code(slug: str) -> str:
    """
    OCW folder slugs arrive in two shapes.

        esd.260j-fall-2006          dot already present
        14-126-spring-2024          dot replaced by a dash
        res.env-007-iap-2025        prefix plus a dashed tail
    """
    s = slug.strip()
    s = re.sub(r"-(?:fall|spring|summer|winter|january|iap)[-\d]*$", "", s, flags=re.I)
    s = re.sub(r"-\d{4}$", "", s)
    s = re.sub(r"-\d+$", "", s) if re.search(r"-\d+$", s) and "." in s else s

    if "." in s:
        return normalise_code(s)
    # Dash form: first dash becomes the dot.
    parts = s.split("-", 1)
    if len(parts) == 2:
        return normalise_code(parts[0] + "." + parts[1].replace("-", ""))
    return normalise_code(s)


def split_records(text: str) -> list[tuple[str, str]]:
    parts = RECORD_RE.split(text)
    if len(parts) > 2:
        return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    parts = COURSE_RE.split(text)
    if len(parts) > 2:
        return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return []


def find_level(body: str) -> str | None:
    m = LEVEL_BLOCK_RE.search(body)
    if m:
        vals = re.findall(r"(Undergraduate|Graduate)", m.group(1))
        uniq = list(dict.fromkeys(vals))
        return " and ".join(uniq) if uniq else None
    m = LEVEL_INLINE_RE.search(body)
    return m.group(1).title() if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Recover course levels from the bloated corpus.")
    ap.add_argument("--in", dest="infile", required=True, help="ALL_SUMMARY_BLOATED.txt")
    ap.add_argument("--out", required=True, help="CSV of level corrections.")
    args = ap.parse_args()

    with open(args.infile, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    records = split_records(text)
    if not records:
        log("no course records found. Is this the bloated corpus?")
        return 1
    log(f"{len(records)} course records in {os.path.basename(args.infile)}")

    rows, missing = [], []
    for slug, body in records:
        level = find_level(body)
        if not level:
            missing.append(slug)
            continue
        rows.append({
            "code": slug_to_code(slug),
            "rung": LEVEL_TO_RUNG.get(level, "undergraduate"),
            "level_stated": level,
            "note": f"recovered from {os.path.basename(args.infile)} sidebar; slug {slug}",
        })

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "rung", "level_stated", "note"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["code"]))

    counts = Counter(r["level_stated"] for r in rows)
    rungs = Counter(r["rung"] for r in rows)
    log("")
    log(f"wrote {len(rows)} corrections to {args.out}")
    log("")
    log("  Level as MIT states it:")
    for k, n in counts.most_common():
        log(f"    {k:<30} {n}")
    log("")
    log("  Resulting rung:")
    for k, n in rungs.most_common():
        log(f"    {k:<30} {n}")

    # Rule 4. Silence must be loud.
    if missing:
        log("")
        log(f"  {len(missing)} course(s) carry no Level in the sidebar:")
        for s in missing[:20]:
            log(f"    - {s}")
        log("  Read those course pages by hand and add rows manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
