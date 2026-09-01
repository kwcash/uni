#!/usr/bin/env python3
"""
score_verticals.py — multi-label vertical and thread attribution.

Replaces the single-label allocation that already produced one documented
failure. Under single-label scoring Power Currency drew 3 courses of 231. Under
multi-label scoring against its fourteen pillars it drew 218. The difference was
never in the corpus. The difference was in the method.

A record may claim any number of verticals. A course that teaches optimisation
feeds Mathematics, Engineering, Computer Science and Supply Chain at once, and
forcing it to pick one hides three of those four.

Power Currency scores differently from the other verticals. It is not a subject
with a term list. It is a subject that spans fourteen pillars, and a record
belongs to it when it touches three or more of them.

Usage
-----
    python3 score_verticals.py --in out/fstem_repository.jsonl \\
        --out out/fstem_repository_scored.jsonl \\
        --lexicon schema/lexicon.json \\
        --report out/coverage_matrix.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import RUNGS, read_jsonl, write_jsonl, validate, log  # noqa: E402

DEFAULT_LEXICON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "lexicon.json")


def searchable_text(rec: dict) -> str:
    """Everything the record says about itself, flattened."""
    parts = [rec.get("title") or "", rec.get("description") or ""]
    parts.extend(rec.get("objectives") or [])

    ocw = rec.get("ocw") or {}
    parts.extend((b.get("title") or b.get("raw") or "") for b in (ocw.get("bibliography") or []))
    parts.extend((s.get("topic") or "") for s in (ocw.get("schedule") or []))
    parts.extend(ocw.get("assignments") or [])

    cie = rec.get("cie") or {}
    for unit in cie.get("subject_content") or []:
        parts.append(unit.get("title") or "")
        parts.extend(unit.get("learning_points") or [])
    parts.extend((a.get("description") or "") for a in (cie.get("assessment_objectives") or []))
    parts.extend((p.get("title") or "") for p in (cie.get("papers") or []))

    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).lower()


def matched_terms(text: str, terms) -> list[str]:
    """Distinct terms present. Word-boundary matching, so 'grid' misses 'gridlock'."""
    hits = []
    for term in terms:
        pat = r"\b" + re.escape(term.lower()).replace(r"\ ", r"\s+")
        if re.search(pat, text):
            hits.append(term)
    return hits


def score_verticals(text: str, lex: dict) -> list[dict]:
    out = []
    for name, cfg in lex["verticals"].items():
        terms = cfg["terms"]
        hits = matched_terms(text, terms.keys())
        if not hits:
            continue
        total = sum(terms[h] for h in hits)
        score = min(1.0, total / float(cfg.get("saturation", 6.0)))

        # One generic word is not a claim on a vertical. "Set language" in an
        # IGCSE mathematics syllabus matched general_core on the single token
        # "language" until this guard existed. A vertical needs either two
        # distinct terms or one term specific enough to stand alone.
        min_terms = lex["thresholds"].get("vertical_min_terms", 2)
        strong = lex["thresholds"].get("vertical_strong_term_weight", 3)
        if len(hits) < min_terms and max(terms[h] for h in hits) < strong:
            continue

        if score >= lex["thresholds"]["vertical_emit"]:
            out.append({
                "vertical": name,
                "score": round(score, 4),
                "matched_terms": sorted(hits)[:25],
                "pillars_hit": [],
            })
    return out


def score_power_currency(text: str, lex: dict) -> dict | None:
    """Fourteen pillars. Three or more makes the record part of the field."""
    pillars = lex["power_currency_pillars"]
    hit_pillars, all_terms = [], []
    for pid, cfg in pillars.items():
        hits = matched_terms(text, cfg["terms"])
        if len(hits) >= lex["thresholds"]["pillar_min_terms"]:
            hit_pillars.append(pid)
            all_terms.extend(hits)
    if len(hit_pillars) < lex["thresholds"]["power_currency_min_pillars"]:
        return None
    score = min(1.0, len(hit_pillars) / 8.0)
    return {
        "vertical": "power_currency",
        "score": round(score, 4),
        "matched_terms": sorted(set(all_terms))[:25],
        "pillars_hit": sorted(hit_pillars, key=lambda p: int(p[1:])),
    }


def score_threads(text: str, lex: dict) -> list[str]:
    out = []
    for tid, terms in lex["threads"].items():
        if len(matched_terms(text, terms)) >= lex["thresholds"]["thread_min_terms"]:
            out.append(tid)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Multi-label vertical and thread scoring.")
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="outfile", required=True)
    ap.add_argument("--lexicon", default=DEFAULT_LEXICON)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    with open(args.lexicon, "r", encoding="utf-8") as fh:
        lex = json.load(fh)

    records = read_jsonl(args.infile)
    log(f"scoring {len(records)} assets")

    unscored = []
    for rec in records:
        text = searchable_text(rec)
        vs = score_verticals(text, lex)
        pc = score_power_currency(text, lex)
        if pc:
            vs.append(pc)
        vs.sort(key=lambda v: -v["score"])
        rec["verticals"] = vs
        rec["threads"] = score_threads(text, lex)
        if not vs:
            unscored.append(rec["asset_id"])
            flags = rec.setdefault("extraction_flags", [])
            if "unscored" not in flags:
                flags.append("unscored")

    write_jsonl(records, args.outfile)

    # ---- Coverage matrix --------------------------------------------------
    rung_order = list(RUNGS.keys())
    vert_names = list(lex["verticals"].keys()) + ["power_currency"]
    grid = defaultdict(Counter)
    for rec in records:
        for v in rec["verticals"]:
            grid[v["vertical"]][rec["rung"]] += 1

    label_count = Counter(len(r["verticals"]) for r in records)
    thread_count = Counter(t for r in records for t in r["threads"])

    lines = ["# FSTEM Coverage Matrix", ""]
    lines.append(f"Assets scored: {len(records)}. Unscored: {len(unscored)}.")
    lines.append("")
    lines.append("Multi-label. Row totals exceed the asset count because one asset serves several verticals. That overlap is the finding, not an error.")
    lines.append("")
    lines.append("## Verticals by rung")
    lines.append("")
    header = "| Vertical | " + " | ".join(r.replace("_", " ") for r in rung_order) + " | Total |"
    lines.append(header)
    lines.append("|---" * (len(rung_order) + 2) + "|")
    for v in vert_names:
        row = grid.get(v, Counter())
        cells = [str(row.get(r, 0)) for r in rung_order]
        lines.append(f"| {v} | " + " | ".join(cells) + f" | {sum(row.values())} |")
    lines.append("")

    lines.append("## Gaps")
    lines.append("")
    gaps = [(v, r) for v in vert_names for r in rung_order if grid.get(v, Counter()).get(r, 0) == 0]
    if gaps:
        lines.append("| Vertical | Empty rung |")
        lines.append("|---|---|")
        for v, r in gaps:
            lines.append(f"| {v} | {r.replace('_', ' ')} |")
    else:
        lines.append("No empty cells.")
    lines.append("")

    lines.append("## Labels per asset")
    lines.append("")
    lines.append("| Verticals claimed | Assets |")
    lines.append("|---|---|")
    for n in sorted(label_count):
        lines.append(f"| {n} | {label_count[n]} |")
    lines.append("")
    lines.append("## Research threads")
    lines.append("")
    lines.append("| Thread | Assets |")
    lines.append("|---|---|")
    for t, n in thread_count.most_common():
        lines.append(f"| {t} | {n} |")
    lines.append("")

    if unscored:
        lines.append("## Unscored assets")
        lines.append("")
        lines.append("These carry too little text to score. Harvest the full course page, then re-run.")
        lines.append("")
        for aid in unscored[:60]:
            lines.append(f"- {aid}")
        if len(unscored) > 60:
            lines.append(f"- ...and {len(unscored) - 60} more")
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
    log("schema: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
