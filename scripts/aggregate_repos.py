#!/usr/bin/env python3
"""
aggregate_repos.py — one profile per repository, plus a comparison.

The merged repository answers what the whole holding contains. It cannot answer
what any one directory contributes, because after the merge a course belongs to
whichever harvest carried the richest copy. These profiles read the per-harvest
JSONL files instead, so each repository is described on its own terms.

Each profile answers the same eight questions:

    1. What is in it              counts, rungs, levels, years
    2. What it teaches            verticals, multi-label
    3. Where to enter from the top    hub courses, most domains bridged
    4. Where to enter from the bottom foundation courses, most depended upon
    5. What threads run through it
    6. Who its authors are        bibliography anchors
    7. How current it is          year distribution
    8. What is wrong with it      flags, and what they mean

Written for the question you are actually asking. A hub course is the top-down
entry: teach it and a student meets several domains at once. A foundation course
is the bottom-up entry: nothing downstream works until it is held. Both matter
and they are rarely the same course.

Usage
-----
    python3 scripts/aggregate_repos.py --in out/ocw_*.jsonl --outdir out/profiles
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import RUNGS, read_jsonl, log  # noqa: E402

# The per-repository JSONL files come straight from the ingest, which runs
# before scoring. Reading them raw gives every profile zero verticals, zero
# threads and zero hub courses, which reads as a finding and is a plumbing
# fault. Score in memory instead of depending on the pipeline order.
try:
    from score_verticals import (searchable_text, score_verticals,
                                 score_power_currency, score_threads,
                                 score_record)
    _SCORER = True
except ImportError:  # pragma: no cover
    _SCORER = False

DEFAULT_LEXICON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "lexicon.json")


def ensure_scored(recs: list[dict], lexicon_path: str) -> int:
    """Score any record the pipeline left unscored. Returns how many were scored."""
    if not _SCORER:
        return 0
    todo = [r for r in recs if not r.get("verticals")]
    if not todo:
        return 0
    try:
        import json
        with open(lexicon_path, "r", encoding="utf-8") as fh:
            lex = json.load(fh)
    except (OSError, ValueError):
        return 0
    for r in todo:
        score_record(r, lex)
    return len(todo)


def repo_name(path: str, recs: list[dict]) -> str:
    """
    Name the repository the way a person would.

    preflight writes one JSONL per directory as <source_type>__<dirname>.jsonl,
    so the stem after the double underscore is already the directory name. Fall
    back to the provenance path, then to the bare filename with any upload hash
    and extension stripped.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    if "__" in stem:
        return stem.split("__", 1)[1]

    for r in recs[:20]:
        src = (r.get("provenance") or {}).get("source_file") or ""
        if src and os.path.isdir(src):
            return os.path.basename(src.rstrip("/"))

    clean = re.sub(r"^[0-9a-f]{6,}-", "", stem)
    clean = re.sub(r"\.(txt|json|jsonl)$", "", clean, flags=re.I)
    return clean or stem


def bar(n: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return ""
    return "#" * max(1, round(width * n / total)) if n else ""


NOT_AUTHORS = {
    "science", "technology", "society", "history", "journal", "review", "press",
    "university", "readings", "introduction", "chapter", "part", "volume", "edition",
    "the", "and", "in", "on", "of", "see", "note", "notes", "ibid", "various",
}


def author_of(entry: dict) -> str | None:
    """Pull a surname from a bibliography row. Crude, and consistent."""
    if entry.get("author"):
        return str(entry["author"]).strip()
    raw = (entry.get("raw") or "").strip()
    # An honorific means a person in a sentence, not a citation. "Mr. Smith"
    # came from a hypothetical in an assignment prompt and was counted ten times.
    raw = re.sub(r"^(?:Mr|Mrs|Ms|Dr|Prof|Professor)\.?\s+", "", raw)
    for pat in (r"^([A-Z][A-Za-z'\-]{1,20}),\s*[A-Z]", r"^([A-Z][A-Za-z'\-]{2,20}),"):
        m = re.match(pat, raw)
        if m and m.group(1).lower() not in NOT_AUTHORS:
            return m.group(1)
    return None


def profile(path: str, recs: list[dict], all_codes_by_repo: dict) -> tuple[str, str, dict]:
    name = repo_name(path, recs)
    n = len(recs)

    rungs = Counter(r["rung"] for r in recs)
    levels = Counter(r.get("level_stated") or "not stated" for r in recs)
    verts = Counter(v["vertical"] for r in recs for v in r.get("verticals") or [])
    threads = Counter(t for r in recs for t in r.get("threads") or [])
    flags = Counter(f for r in recs for f in r.get("extraction_flags") or [])
    years = Counter(r.get("year") for r in recs if r.get("year"))

    # Hub courses. Top-down entry: one course, several domains.
    hubs = sorted(
        ((len(r.get("verticals") or []), r) for r in recs if len(r.get("verticals") or []) >= 3),
        key=lambda t: (-t[0], t[1]["code"]))[:12]

    # Foundation courses. Bottom-up entry: most depended upon inside this repo.
    depends = Counter()
    for r in recs:
        for pre in r.get("prerequisites") or []:
            depends[pre] += 1
    by_code = {r["code"]: r for r in recs}
    foundations = [(cnt, by_code[c]) for c, cnt in depends.most_common(40) if c in by_code][:12]

    authors = Counter()
    for r in recs:
        # An instructor is not an author of the reading list they set.
        own = {i.split()[-1].lower() for i in (r.get("instructors") or []) if i.split()}
        for b in (r.get("ocw") or {}).get("bibliography") or []:
            a = author_of(b)
            if a and a.lower() in own:
                a = None
            if a:
                authors[a] += 1

    bib_total = sum(len((r.get("ocw") or {}).get("bibliography") or []) for r in recs)
    held = sum(1 for r in recs if (r.get("ocw") or {}).get("held"))
    unscored = sum(1 for r in recs if not r.get("verticals"))

    L = [f"# Repository Profile: {name}", ""]
    L.append(f"Source: `{path}`. Courses: **{n}**. Held on disk: {held}.")
    L.append("")

    L.append("## 1. What is in it")
    L.append("")
    L.append("| Rung | Grades | Courses | |")
    L.append("|---|---|---|---|")
    for rung, meta in RUNGS.items():
        c = rungs.get(rung, 0)
        if c:
            L.append(f"| {rung} | {meta['grades'][0]}–{meta['grades'][1]} | {c} | `{bar(c, n)}` |")
    L.append("")
    L.append("| Level as MIT states it | Courses |")
    L.append("|---|---|")
    for k, v in levels.most_common():
        L.append(f"| {k} | {v} |")
    L.append("")

    L.append("## 2. What it teaches")
    L.append("")
    L.append("Multi-label. A course serving three domains appears three times, which is the point.")
    L.append("")
    L.append("| Vertical | Courses | Share | |")
    L.append("|---|---|---|---|")
    for k, v in verts.most_common():
        L.append(f"| {k} | {v} | {round(100 * v / n)}% | `{bar(v, n)}` |")
    L.append("")

    L.append("## 3. Top-down entry: the hub courses")
    L.append("")
    if hubs:
        L.append("Teach one of these and a student meets several domains at once. These are where a")
        L.append("broad top-down programme starts, because each carries more than its own subject.")
        L.append("")
        L.append("| Domains | Code | Title |")
        L.append("|---|---|---|")
        for cnt, r in hubs:
            doms = ", ".join(v["vertical"] for v in sorted(
                r["verticals"], key=lambda x: -x["score"])[:4])
            L.append(f"| **{cnt}** | `{r['code']}` | {r['title'][:58]}<br><sub>{doms}</sub> |")
    else:
        L.append("None. No course in this repository claims three or more verticals.")
    L.append("")

    L.append("## 4. Bottom-up entry: the foundation courses")
    L.append("")
    if foundations:
        L.append("Most depended upon by other courses in this repository. Nothing downstream works")
        L.append("until these are held, which makes them the first thing to build if you build upward.")
        L.append("")
        L.append("| Depended on by | Code | Title |")
        L.append("|---|---|---|")
        for cnt, r in foundations:
            L.append(f"| **{cnt}** | `{r['code']}` | {r['title'][:64]} |")
    else:
        L.append("No prerequisite edges found inside this repository. Either the courses state no")
        L.append("prerequisites, or the extraction did not reach the syllabus section that lists them.")
    L.append("")

    L.append("## 5. Threads")
    L.append("")
    if threads:
        L.append("| Thread | Courses |")
        L.append("|---|---|")
        for k, v in threads.most_common():
            L.append(f"| {k} | {v} |")
    else:
        L.append("No research threads detected.")
    L.append("")

    L.append("## 6. Bibliography anchors")
    L.append("")
    L.append(f"{bib_total:,} citations across {n} courses.")
    L.append("")
    if authors:
        L.append("| Author | Cited in |")
        L.append("|---|---|")
        for k, v in authors.most_common(15):
            L.append(f"| {k} | {v} |")
    else:
        L.append("No parseable authors. The bibliography section did not extract.")
    L.append("")

    L.append("## 7. How current it is")
    L.append("")
    if years:
        buckets = Counter()
        for y, c in years.items():
            buckets[(y // 5) * 5] += c
        L.append("| Period | Courses | |")
        L.append("|---|---|---|")
        for b in sorted(buckets):
            L.append(f"| {b}–{b + 4} | {buckets[b]} | `{bar(buckets[b], n)}` |")
        ys = sorted(years.elements())
        L.append("")
        L.append(f"Median course year: **{ys[len(ys) // 2]}**.")
    else:
        L.append("No years recorded.")
    L.append("")

    L.append("## 8. What is wrong with it")
    L.append("")
    L.append(f"Unscored, too little text to attribute: **{unscored}** of {n}.")
    L.append("")
    if flags:
        L.append("| Flag | Courses |")
        L.append("|---|---|")
        for k, v in flags.most_common():
            L.append(f"| {k} | {v} |")
    L.append("")

    stats = {
        "name": name, "n": n, "rungs": rungs, "verts": verts, "threads": threads,
        "codes": {r["code"] for r in recs}, "held": held, "unscored": unscored,
        "median_year": (sorted(years.elements())[len(list(years.elements())) // 2] if years else None),
        "bib": bib_total, "hubs": hubs, "foundations": foundations,
        # Density, not head count. Both lists above cap at twelve, so counting
        # them made every repository look identical and the verdict column said
        # "either" five times running. A cap is not a measurement.
        "hub_share": (sum(1 for r in recs if len(r.get("verticals") or []) >= 3) / n) if n else 0.0,
        # Count only codes this repository actually holds. Counting every code
        # the parser saw put "deepest 164" in the comparison while the table two
        # sections above topped out at 24. A document that contradicts itself
        # teaches the reader to trust none of it.
        "spine_share": (len([c for c in depends if c in by_code]) / n) if n else 0.0,
        "spine_max": (max([v for c, v in depends.items() if c in by_code] or [0])),
    }
    all_codes_by_repo[name] = stats["codes"]
    return name, "\n".join(L) + "\n", stats


def comparison(stats: list[dict]) -> str:
    L = ["# Repository Comparison", ""]
    L.append("Four harvests, side by side. Read this before deciding what to build first.")
    L.append("")
    L.append("| Repository | Courses | Held | Unscored | Citations | Median year |")
    L.append("|---|---|---|---|---|---|")
    for s in stats:
        L.append(f"| **{s['name']}** | {s['n']} | {s['held']} | {s['unscored']} | "
                 f"{s['bib']:,} | {s['median_year'] or '—'} |")
    L.append("")

    L.append("## Rung distribution")
    L.append("")
    rungnames = [r for r in RUNGS]
    L.append("| Repository | " + " | ".join(r.replace("_", " ") for r in rungnames) + " |")
    L.append("|---" * (len(rungnames) + 1) + "|")
    for s in stats:
        L.append(f"| **{s['name']}** | " + " | ".join(str(s["rungs"].get(r, 0)) for r in rungnames) + " |")
    L.append("")

    L.append("## Vertical coverage")
    L.append("")
    allv = sorted({v for s in stats for v in s["verts"]})
    L.append("| Repository | " + " | ".join(allv) + " |")
    L.append("|---" * (len(allv) + 1) + "|")
    for s in stats:
        L.append(f"| **{s['name']}** | " + " | ".join(str(s["verts"].get(v, 0)) for v in allv) + " |")
    L.append("")

    L.append("## Overlap")
    L.append("")
    L.append("Courses held by more than one repository. A high overlap means a harvest duplicates")
    L.append("rather than extends, which changes what it is worth.")
    L.append("")
    L.append("| | " + " | ".join(s["name"] for s in stats) + " |")
    L.append("|---" * (len(stats) + 1) + "|")
    for a in stats:
        row = []
        for b in stats:
            row.append("—" if a["name"] == b["name"] else str(len(a["codes"] & b["codes"])))
        L.append(f"| **{a['name']}** | " + " | ".join(row) + " |")
    L.append("")

    L.append("## Where to start")
    L.append("")
    L.append("Two routes, and the data supports different ones per repository.")
    L.append("")
    L.append("Hub share counts courses claiming three or more domains. Spine share counts "
             "courses another course names as a prerequisite. Deepest is the largest number "
             "of courses depending on any single one.")
    L.append("")
    L.append("| Repository | Hub share | Spine share | Deepest | Suggests |")
    L.append("|---|---:|---:|---:|---|")
    for s in stats:
        hs, ss, sm = s["hub_share"], s["spine_share"], s["spine_max"]
        strong_hub = hs >= 0.30
        strong_spine = ss >= 0.08 and sm >= 8
        if strong_spine and not strong_hub:
            verdict = f"bottom-up. One course carries {sm} dependents."
        elif strong_hub and not strong_spine:
            verdict = "top-down. Hubs are dense, the prerequisite spine is thin."
        elif strong_hub and strong_spine:
            verdict = (f"either, and bottom-up is stronger at {sm} dependents."
                       if sm >= 15 else "either. Both entries carry weight.")
        else:
            verdict = "neither yet. Harvest more prerequisites before deciding."
        L.append(f"| **{s['name']}** | {hs:.0%} | {ss:.0%} | {sm} | {verdict} |")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="One aggregate profile per repository.")
    ap.add_argument("--in", dest="inputs", nargs="+", required=True)
    ap.add_argument("--outdir", default="out/profiles")
    ap.add_argument("--lexicon", default=DEFAULT_LEXICON)
    args = ap.parse_args()

    paths = []
    for pat in args.inputs:
        hits = sorted(glob.glob(pat))
        paths.extend(hits if hits else [pat])
    paths = [p for p in dict.fromkeys(paths) if os.path.exists(p)]
    if not paths:
        log("no input files resolved")
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    all_codes: dict = {}
    seen_names: dict = {}
    stats: list[dict] = []

    for p in paths:
        recs = read_jsonl(p)
        if not recs:
            log(f"  {os.path.basename(p)}: empty, skipped")
            continue
        n_scored = ensure_scored(recs, args.lexicon)
        name, text, st = profile(p, recs, all_codes)
        if name in seen_names:
            log(f"  ! {name}: a second file resolves to this name. "
                f"{os.path.basename(seen_names[name])} and {os.path.basename(p)}.")
            log(f"    One is stale. Delete it and re-run, or the profile is a coin toss.")
            name = f"{name}__{os.path.splitext(os.path.basename(p))[0]}"
        seen_names[name] = p
        fp = os.path.join(args.outdir, f"PROFILE_{name}.md")
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(text)
        stats.append(st)
        note = f"  (scored {n_scored} in memory)" if n_scored else ""
        log(f"  {name:<20} {st['n']:>5} courses  ->  {fp}{note}")

    if len(stats) > 1:
        cp = os.path.join(args.outdir, "COMPARISON.md")
        with open(cp, "w", encoding="utf-8") as fh:
            fh.write(comparison(stats))
        log(f"  {'comparison':<20} {'':>5}          ->  {cp}")

    log("")
    log(f"{len(stats)} profile(s) in {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
