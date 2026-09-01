#!/usr/bin/env python3
"""
merge_dedupe.py — one repository from five harvests.

Does four jobs and reports on all four.

    1. Merge every input JSONL into one store.
    2. Dedupe on asset_id. The richer record wins field by field. Nothing is
       discarded silently; the loser's identity lands in duplicates.
    3. Detect cross-corpus overlap. 21H.383 sits in the graduate corpus and in
       the Western Civilization set. That is one course and two claims on it.
    4. Apply level corrections from a CSV so the misfiled forty move to the
       undergraduate rung by an auditable edit rather than by a guess.

Counting discipline: the script never drops a record to satisfy an expected
total. Where the count disagrees with the expectation it says so and names the
candidates. Silent truncation reads as coverage that was never there.

Usage
-----
    python3 merge_dedupe.py --in out/*.jsonl --out out/fstem_repository.jsonl \\
        --level-overrides overrides/level_corrections.csv \\
        --expect ocw_westciv=59 \\
        --report out/merge_report.md
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import RUNGS, read_jsonl, write_jsonl, validate, log  # noqa: E402

RICHNESS_FIELDS = ["description", "objectives", "prerequisites", "instructors", "term", "year", "url"]


def richness(rec: dict) -> int:
    """How much a record actually carries. Drives which copy wins a merge."""
    score = sum(1 for f in RICHNESS_FIELDS if rec.get(f))
    ocw = rec.get("ocw") or {}
    score += len(ocw.get("bibliography") or []) * 2
    score += len(ocw.get("schedule") or [])
    score += len(ocw.get("grading") or []) * 2
    score += 20 if ocw.get("held") else 0
    cie = rec.get("cie") or {}
    score += len(cie.get("subject_content") or []) * 2
    score += len(cie.get("assessment_objectives") or []) * 3
    score += len(cie.get("papers") or [])
    return score


def merge_two(winner: dict, loser: dict) -> dict:
    """Fill the winner's empty fields from the loser. Never overwrite."""
    out = dict(winner)
    for key, val in loser.items():
        if key in ("asset_id", "duplicates", "provenance"):
            continue
        cur = out.get(key)
        if cur in (None, "", [], {}) and val not in (None, "", [], {}):
            out[key] = val
        elif isinstance(cur, list) and isinstance(val, list) and key in ("objectives", "prerequisites", "instructors", "extraction_flags", "threads"):
            merged = list(cur)
            for item in val:
                if item not in merged:
                    merged.append(item)
            out[key] = merged

    for block in ("ocw", "cie"):
        w, l = out.get(block), loser.get(block)
        if isinstance(w, dict) and isinstance(l, dict):
            for k, v in l.items():
                if w.get(k) in (None, "", [], {}) and v not in (None, "", [], {}):
                    w[k] = v
        elif w is None and isinstance(l, dict):
            out[block] = l

    srcs = list(out.get("contributing_sources") or [out.get("source_type")])
    for st2 in (loser.get("contributing_sources") or [loser.get("source_type")]):
        if st2 and st2 not in srcs:
            srcs.append(st2)
    out["contributing_sources"] = srcs

    dupes = list(out.get("duplicates") or [])
    src = loser.get("source_type")
    tag = f"{src}:{loser.get('code')}"
    if tag not in dupes:
        dupes.append(tag)
    for d in loser.get("duplicates") or []:
        if d not in dupes:
            dupes.append(d)
    out["duplicates"] = dupes

    flags = list(out.get("extraction_flags") or [])
    if src and src != winner.get("source_type") and "cross_corpus_overlap" not in flags:
        flags.append("cross_corpus_overlap")
    out["extraction_flags"] = flags
    return out


def load_overrides(path: str | None) -> dict:
    """CSV: code,rung,level_stated,note. One row per correction."""
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            code = (row.get("code") or "").strip()
            if code:
                out[code.upper()] = row
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge, dedupe and level-correct the FSTEM repository.")
    ap.add_argument("--in", dest="inputs", nargs="+", required=True, help="Input JSONL files or globs.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--level-overrides", default=None, help="CSV of auditable level corrections.")
    ap.add_argument("--exclude", default=None, help="Text file of asset_ids or codes to drop, one per line.")
    ap.add_argument("--expect", nargs="*", default=[], help="source_type=N assertions. Reported, never enforced.")
    ap.add_argument("--report", default=None, help="Write a markdown report here.")
    args = ap.parse_args()

    paths = []
    for pattern in args.inputs:
        hits = glob.glob(pattern)
        paths.extend(hits if hits else [pattern])
    paths = [p for p in dict.fromkeys(paths) if os.path.exists(p)]
    if not paths:
        log("no input files resolved")
        return 1

    excluded = set()
    if args.exclude and os.path.exists(args.exclude):
        with open(args.exclude, "r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s and not s.startswith("#"):
                    excluded.add(s.upper())

    overrides = load_overrides(args.level_overrides)

    store: dict[str, dict] = {}
    source_counts: Counter = Counter()
    collisions: list[tuple[str, str, str]] = []
    dropped = 0

    for path in paths:
        recs = read_jsonl(path)
        log(f"{os.path.basename(path):<28} {len(recs):>5} records")
        for rec in recs:
            aid = rec["asset_id"]
            if aid.upper() in excluded or rec["code"].upper() in excluded:
                dropped += 1
                continue
            source_counts[rec["source_type"]] += 1
            if aid in store:
                a, b = store[aid], rec
                winner, loser = (a, b) if richness(a) >= richness(b) else (b, a)
                if a["source_type"] != b["source_type"]:
                    collisions.append((aid, a["source_type"], b["source_type"]))
                store[aid] = merge_two(winner, loser)
            else:
                store[aid] = rec

    # Level corrections. Auditable, one row per change.
    applied = 0
    for code, row in overrides.items():
        for aid, rec in store.items():
            if rec["code"].upper() != code:
                continue
            new_rung = (row.get("rung") or "").strip()
            if new_rung and new_rung in RUNGS and new_rung != rec["rung"]:
                rec["rung"] = new_rung
                rec["grade_band"] = list(RUNGS[new_rung]["grades"])
                rec["verb"] = RUNGS[new_rung]["verb"]
                rec.setdefault("articulation", {})["gate"] = RUNGS[new_rung]["gate_out"]
                applied += 1
            if row.get("level_stated"):
                rec["level_stated"] = row["level_stated"].strip()
            rec["level_conflict"] = False
            flags = rec.setdefault("extraction_flags", [])
            for f in ("level_conflict", "level_not_stated", "level_review"):
                if f in flags:
                    flags.remove(f)
            if "level_corrected" not in flags:
                flags.append("level_corrected")

    records = sorted(store.values(), key=lambda r: (r["rung"], r["code"]))
    write_jsonl(records, args.out)

    # ---- Report -----------------------------------------------------------
    by_rung = Counter(r["rung"] for r in records)
    by_source = Counter(r["source_type"] for r in records)
    unheld = sum(1 for r in records if (r.get("ocw") or {}).get("held") is False)
    flagged = Counter(f for r in records for f in r.get("extraction_flags") or [])

    lines = ["# FSTEM Repository Merge Report", ""]
    lines.append(f"Inputs: {len(paths)}. Raw rows: {sum(source_counts.values())}. Unique assets: {len(records)}. Dropped by exclusion list: {dropped}.")
    lines.append("")
    lines.append("## Assets by rung")
    lines.append("")
    lines.append("| Rung | Grades | Verb | Assets |")
    lines.append("|---|---|---|---|")
    for rung, meta in RUNGS.items():
        lines.append(f"| {rung} | {meta['grades'][0]}–{meta['grades'][1]} | {meta['verb']} | {by_rung.get(rung, 0)} |")
    lines.append(f"| unresolved | — | — | {by_rung.get('unresolved', 0)} |")
    lines.append("")
    lines.append("## Assets by source harvest")
    lines.append("")
    lines.append("| Source | Raw rows | Unique after dedupe |")
    lines.append("|---|---|---|")
    for st in sorted(source_counts):
        lines.append(f"| {st} | {source_counts[st]} | {by_source.get(st, 0)} |")
    lines.append("")

    if args.expect:
        lines.append("## Count assertions")
        lines.append("")
        lines.append("| Source | Expected | Actual | Delta |")
        lines.append("|---|---|---|---|")
        for spec in args.expect:
            if "=" not in spec:
                continue
            st, n = spec.split("=", 1)
            actual = by_source.get(st.strip(), 0)
            try:
                exp = int(n)
            except ValueError:
                continue
            lines.append(f"| {st.strip()} | {exp} | {actual} | {actual - exp:+d} |")
        lines.append("")
        lines.append("The script does not drop records to close a delta. Resolve it with an explicit exclusion list.")
        lines.append("")

    if collisions:
        lines.append("## Cross-corpus overlap")
        lines.append("")
        lines.append("One course, more than one harvest claiming it. Merged into a single asset.")
        lines.append("")
        lines.append("| Asset | Harvest A | Harvest B |")
        lines.append("|---|---|---|")
        for aid, a, b in sorted(set(collisions)):
            lines.append(f"| {aid} | {a} | {b} |")
        lines.append("")

    lines.append("## Extraction flags")
    lines.append("")
    lines.append("| Flag | Assets |")
    lines.append("|---|---|")
    for f, n in flagged.most_common():
        lines.append(f"| {f} | {n} |")
    lines.append("")
    lines.append(f"Level corrections applied: {applied}.")
    lines.append(f"Assets cited but not held, harvest required: {unheld}.")
    lines.append("")

    report = "\n".join(lines)
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        log(f"report written to {args.report}")
    print(report)

    errors = validate(records)
    if errors:
        log(f"SCHEMA ERRORS ({len(errors)}):")
        for e in errors[:20]:
            log("  " + e)
        return 2
    log(f"wrote {len(records)} assets to {args.out}. schema: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
