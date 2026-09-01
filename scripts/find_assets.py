#!/usr/bin/env python3
"""
find_assets.py — hunt the whole machine for FSTEM scripts and data.

preflight.py answers "what is under Projects". This answers the harder question,
"what did I write that is not under Projects, and is Projects even the right
directory". It searches by what a file does rather than by what it is called,
because a script you renamed or copied to a backup drive still does the same job.

Reads only. Writes one report.

Usage
-----
    python3 scripts/find_assets.py
    python3 scripts/find_assets.py --roots ~ /media /mnt /Volumes
    python3 scripts/find_assets.py --roots ~ --max-files 40000

What it classifies
------------------
Scripts, by content signature:

    extractor        parses HTML or PDF course pages into text
    database_builder writes a JSON or CSV database of course records
    recursive_walker walks a directory tree with os.walk, rglob or scandir
    harvester        fetches over the network
    cataloguer       indexes resource files such as lecture PDFs
    analyser         computes statistics over a corpus
    courseware       generates module or course pages

Data artifacts, by name and shape:

    course database, bibliography, prerequisite map, coverage table,
    consolidated corpus, per-course extraction output, CIE syllabus PDFs
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import log  # noqa: E402

ROOT_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".cache", "site-packages",
    ".local", ".npm", ".cargo", ".rustup", "dist-packages", ".mozilla", ".config",
    "AppData", "Library", "System", "Windows", "Program Files", "snap", "proc", "sys",
    "dev", "run", ".Trash", "$RECYCLE.BIN", ".gradle", ".m2",
}

# A file is FSTEM-relevant when its text talks about this domain.
DOMAIN_RE = re.compile(
    r"(ocw|opencourseware|course_?number|syllabus|bibliograph|curriculum|"
    r"prerequisite|coursewar|igcse|cambridge|a[- ]level|module|lecture|"
    r"fstem|power ?currency|stratagem|total domain)", re.I)

SIGNATURES = [
    ("extractor", re.compile(r"(HTMLParser|BeautifulSoup|lxml|html\.parser|pdfminer|PyPDF|pypdf|fitz|extract_text)", re.I)),
    ("database_builder", re.compile(r"(json\.dump|csv\.DictWriter|csv\.writer|to_csv|sqlite3|\.db['\"]|database)", re.I)),
    ("recursive_walker", re.compile(r"(os\.walk|rglob|glob\([^)]*\*\*|scandir|Path\([^)]*\)\.rglob|recursive\s*=\s*True)", re.I)),
    ("harvester", re.compile(r"(requests\.|urllib\.request|urlopen|httpx|aiohttp|wget|curl|scrapy)", re.I)),
    ("cataloguer", re.compile(r"(resource|catalog|\.pdf|lecture_?notes|data\.json)", re.I)),
    ("analyser", re.compile(r"(Counter\(|statistics|median|mean\(|value_counts|most_common|histogram)", re.I)),
    ("courseware", re.compile(r"(README\.md|module_?page|build_?course|INDEX\.md|workbook|template)", re.I)),
    # Distinguishes a script that DISCOVERS pages from one that hardcodes five
    # paths. This is the signature of the deep harvest, and it is the script
    # most often remembered and least often found.
    ("page_discovery", re.compile(
        r"((os\.walk|rglob|glob)[^\n]{0,80}index\.html"
        r"|index\.html[^\n]{0,80}(os\.walk|rglob|glob)"
        r"|lecture[-_]?notes|instructor[-_]?insights|related[-_]?resources|study[-_]?materials|recitations)", re.I)),
    ("sqlite_writer", re.compile(r"(sqlite3|CREATE TABLE|\.executemany|conn\.commit|\.db['\"]|SQLAlchemy)", re.I)),
]

DATA_PATTERNS = [
    ("course_database", re.compile(r"^(ocw_database|course_database|courses)\.json$", re.I)),
    ("bibliography", re.compile(r"^bibliograph.*\.(csv|json)$", re.I)),
    ("prerequisite_map", re.compile(r"^prereq.*\.(json|csv)$", re.I)),
    ("coverage_table", re.compile(r"^.*coverage.*\.(csv|json)$", re.I)),
    ("track_allocation", re.compile(r"^track_alloc.*\.(json|csv)$", re.I)),
    ("course_table", re.compile(r"^ocw_courses\.(csv|json)$", re.I)),
    ("consolidated_corpus", re.compile(r"^ALL[_-].*\.txt$", re.I)),
    ("extraction_output", re.compile(r".*(SEMANTIC|CLEAN|summary)\.txt$")),
    ("pillar_map", re.compile(r"^pc_pillar_map\.json$", re.I)),
    ("fstem_repository", re.compile(r"^fstem_repository.*\.jsonl$", re.I)),
    ("sqlite_db", re.compile(r".*\.(db|sqlite|sqlite3|db3)$", re.I)),
    ("spreadsheet_db", re.compile(r".*(database|corpus|courses|curriculum).*\.xlsx$", re.I)),
    ("resource_index", re.compile(r"^(resource_catalog|resource_index|resources)\.(json|csv)$", re.I)),
]

# Scripts the project documents reference. Absence is a finding.
EXPECTED = {
    "parse_corpus.py": "consolidated corpus to ocw_database.json, bibliography_master.csv, prerequisite_map.json",
    "analyze_corpus.py": "database to corpus statistics and coverage",
    "build_courseware.py": "curriculum plus database to course and module pages",
    "deep_harvest.py": "opens lecture-note pages the first harvest missed",
    "resource_catalog.py": "indexes lecture PDFs with real titles and sessions",
    "extract_course_semantic.py": "OCW pages to per-course SEMANTIC.txt",
    "extract_course_clean.py": "OCW pages to per-course CLEAN.txt",
    "extract_course_summaries.py": "OCW pages to per-course summary.txt",
    "consolidate_corpus.py": "per-course text files to ALL_SEMANTIC_AGGRESSIVE.txt (the concatenation step)",
}

# The concatenation step may carry any of these names. It is the one script in
# the chain that nobody remembers writing, because it is fifteen lines.
CONSOLIDATOR_ALIASES = ("consolidate", "combine", "merge_txt", "aggregate", "concat", "all_semantic", "build_corpus")


def iter_files(roots: list[str], max_files: int, max_depth: int):
    seen_real = set()
    count = 0
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        base = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            if dirpath.rstrip(os.sep).count(os.sep) - base >= max_depth:
                dirnames[:] = []
            try:
                real = os.path.realpath(dirpath)
            except OSError:
                continue
            if real in seen_real:
                dirnames[:] = []
                continue
            seen_real.add(real)
            for fn in filenames:
                count += 1
                if count > max_files:
                    log(f"  ! file ceiling {max_files} reached; raise with --max-files")
                    return
                yield dirpath, fn


def classify_script(path: str, name: str = "") -> tuple[list[str], str | None, int]:
    try:
        size = os.path.getsize(path)
        if size > 800_000:
            return [], None, size
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read(400_000)
    except OSError:
        return [], None, 0

    if not DOMAIN_RE.search(src):
        return [], None, size

    roles = [rname for rname, rx in SIGNATURES if rx.search(src)]
    low = (name or os.path.basename(path)).lower()
    if any(a in low for a in CONSOLIDATOR_ALIASES) or re.search(
            r"(ALL[_-][A-Z_]+\.txt|join\(chunks|''\.join\(parts|write\(corpus)", src):
        if "consolidator" not in roles:
            roles.append("consolidator")
    # A file that talks about this domain but matches no known role still
    # belongs in the report. Dropping it is how a lost script stays lost.
    if not roles:
        roles = ["unclassified"]
    doc = None
    # The module docstring rarely sits at byte zero; a shebang and comments
    # usually precede it. Find the first triple-quoted block anywhere in the head.
    m = re.search(r'(?:"""|\'\'\')\s*(.{10,200}?)(?:\n\s*\n|"""|\'\'\')', src[:3000], re.S)
    if m:
        doc = re.sub(r"\s+", " ", m.group(1)).strip()
    return roles, doc, size


def sqlite_tables(path: str) -> list[str]:
    """Open a candidate database read-only and list its tables. Empty on failure."""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            return [r[0] for r in rows]
        finally:
            con.close()
    except Exception:
        return []


def file_digest(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(65536)
                if not block:
                    break
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt the machine for FSTEM scripts and data.")
    ap.add_argument("--roots", nargs="*", default=None,
                    help="Where to search. Default: home, /media, /mnt, /Volumes, /jimrogers.")
    ap.add_argument("--out", default=os.path.join(ROOT_PKG, "out"))
    ap.add_argument("--max-depth", type=int, default=8)
    ap.add_argument("--max-files", type=int, default=250_000)
    args = ap.parse_args()

    roots = args.roots or [os.path.expanduser("~"), "/media", "/mnt", "/Volumes",
                           "/jimrogers", "/home/jimrogers"]
    roots = [r for r in roots if os.path.isdir(os.path.expanduser(r))]
    if not roots:
        log("no searchable roots")
        return 1

    os.makedirs(args.out, exist_ok=True)
    log("searching: " + ", ".join(roots))

    scripts: list[dict] = []
    data: list[dict] = []

    for dirpath, fn in iter_files(roots, args.max_files, args.max_depth):
        path = os.path.join(dirpath, fn)
        low = fn.lower()

        if low.endswith(".py"):
            roles, doc, size = classify_script(path, fn)
            if roles:
                scripts.append({"path": path, "name": fn, "roles": roles, "doc": doc,
                                "size": size, "digest": file_digest(path)})
            continue

        for kind, rx in DATA_PATTERNS:
            if rx.match(fn):
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                entry = {"path": path, "name": fn, "kind": kind, "size": size, "tables": []}
                if kind == "sqlite_db":
                    entry["tables"] = sqlite_tables(path)
                    if not entry["tables"]:
                        entry["kind"] = kind = "db_file_unreadable"
                data.append(entry)
                break

    # ---- Analysis ---------------------------------------------------------
    by_name: dict[str, list[dict]] = defaultdict(list)
    for s in scripts:
        by_name[s["name"]].append(s)

    by_digest: dict[str, list[dict]] = defaultdict(list)
    for s in scripts:
        if s["digest"]:
            by_digest[s["digest"]].append(s)

    dir_counts: dict[str, int] = defaultdict(int)
    for s in scripts:
        dir_counts[os.path.dirname(s["path"])] += 1
    for d in data:
        dir_counts[os.path.dirname(d["path"])] += 1

    found_expected = {name: by_name.get(name, []) for name in EXPECTED}
    missing = [n for n, hits in found_expected.items() if not hits]

    role_index: dict[str, list[dict]] = defaultdict(list)
    for s in scripts:
        for r in s["roles"]:
            role_index[r].append(s)

    # ---- Report -----------------------------------------------------------
    L = ["# FSTEM Asset Hunt", "", "Roots searched: " + ", ".join(f"`{r}`" for r in roots), ""]
    L.append(f"Scripts matching the domain: **{len(scripts)}**. Data artifacts: **{len(data)}**.")
    L.append("")

    L.append("## Where the work actually lives")
    L.append("")
    L.append("Directories holding the most FSTEM files. If the top entry is not the directory you think of as the project, that is the answer to whether it is off.")
    L.append("")
    L.append("| Directory | Files |")
    L.append("|---|---|")
    for d, n in sorted(dir_counts.items(), key=lambda kv: -kv[1])[:15]:
        L.append(f"| `{d}` | {n} |")
    L.append("")

    L.append("## Expected scripts")
    L.append("")
    L.append("| Script | What it produces | Copies found |")
    L.append("|---|---|---|")
    for name, purpose in EXPECTED.items():
        hits = found_expected[name]
        mark = str(len(hits)) if hits else "**MISSING**"
        L.append(f"| `{name}` | {purpose} | {mark} |")
    L.append("")
    if missing:
        L.append("Missing scripts are the ones to hunt for by hand, or to accept as gone and rebuild. "
                 "Check the section below first: a renamed copy still shows up there by what it does.")
        L.append("")

    L.append("## Every script found, by role")
    L.append("")
    for role in [n for n, _ in SIGNATURES] + ["consolidator", "unclassified"]:
        hits = role_index.get(role, [])
        if not hits:
            continue
        L.append(f"### {role} ({len(hits)})")
        L.append("")
        L.append("| Path | Roles | Docstring |")
        L.append("|---|---|---|")
        for s in sorted(hits, key=lambda x: x["path"])[:40]:
            doc = (s["doc"] or "")[:80]
            L.append(f"| `{s['path']}` | {', '.join(s['roles'])} | {doc} |")
        if len(hits) > 40:
            L.append(f"| ...and {len(hits) - 40} more | | |")
        L.append("")

    dupes = {k: v for k, v in by_digest.items() if len(v) > 1}
    L.append("## Identical copies")
    L.append("")
    if dupes:
        L.append("The same file in more than one place. Decide which is authoritative before editing either, "
                 "because editing the wrong copy is a mistake that hides for weeks.")
        L.append("")
        L.append("| Digest | Copies |")
        L.append("|---|---|")
        for dg, group in list(dupes.items())[:20]:
            paths = "<br>".join(f"`{g['path']}`" for g in group)
            L.append(f"| {dg} | {paths} |")
    else:
        L.append("None. Every script exists in exactly one place.")
    L.append("")

    forks = {n: v for n, v in by_name.items() if len({x['digest'] for x in v}) > 1}
    L.append("## Same name, different contents")
    L.append("")
    if forks:
        L.append("These have diverged. One of them is the version you remember and the others are not.")
        L.append("")
        L.append("| Script | Versions |")
        L.append("|---|---|")
        for n, group in list(forks.items())[:20]:
            rows = "<br>".join(f"`{g['path']}` ({g['digest']}, {g['size']}B)" for g in group)
            L.append(f"| `{n}` | {rows} |")
    else:
        L.append("None. No script has diverging versions.")
    L.append("")

    L.append("## Data artifacts")
    L.append("")
    if data:
        L.append("| Kind | Path | MB |")
        L.append("|---|---|---|")
        for d in sorted(data, key=lambda x: (x["kind"], -x["size"]))[:60]:
            L.append(f"| {d['kind']} | `{d['path']}` | {round(d['size'] / 1e6, 2)} |")
        if len(data) > 60:
            L.append(f"| ...and {len(data) - 60} more | | |")
    else:
        L.append("None found. No database, no bibliography, no extraction output anywhere on this machine.")
    L.append("")

    L.append("## Is there a SQL database")
    L.append("")
    dbs = [d for d in data if d["kind"] in ("sqlite_db", "db_file_unreadable")]
    sheets = [d for d in data if d["kind"] == "spreadsheet_db"]
    writers = role_index.get("sqlite_writer", [])
    if dbs:
        L.append("| Path | Tables |")
        L.append("|---|---|")
        for d in dbs:
            tbl = ", ".join(d["tables"]) if d["tables"] else "**unreadable or not sqlite**"
            L.append(f"| `{d['path']}` | {tbl} |")
    else:
        L.append("**No sqlite or .db file anywhere in the searched roots.**")
    L.append("")
    if sheets:
        L.append("Spreadsheets that carry a schema instead:")
        L.append("")
        for d in sheets:
            L.append(f"- `{d['path']}`")
        L.append("")
    if writers:
        L.append("Scripts that could write one:")
        L.append("")
        for w in writers[:15]:
            L.append(f"- `{w['path']}`")
        L.append("")
    else:
        L.append("No script on this machine writes SQL. If a database exists, something else made it.")
        L.append("")

    L.append("## Is there a page-discovery harvester")
    L.append("")
    L.append("The three known extractors read five hardcoded paths per course. A script that "
             "walks for `index.html` and opens lecture-notes, exams, projects, recitations or "
             "instructor-insights is a different thing, and it is the one usually remembered.")
    L.append("")
    disc = role_index.get("page_discovery", [])
    if disc:
        L.append("| Path | Roles | Docstring |")
        L.append("|---|---|---|")
        for sc in sorted(disc, key=lambda x: x["path"]):
            L.append(f"| `{sc['path']}` | {', '.join(sc['roles'])} | {(sc['doc'] or '')[:70]} |")
    else:
        L.append("**None found.** Either it was never written, or it is outside the searched roots.")
    L.append("")

    kinds = {d["kind"] for d in data}
    L.append("## What the artifacts prove")
    L.append("")
    L.append("| Artifact | Present | What its absence would mean |")
    L.append("|---|---|---|")
    proofs = [
        ("course_database", "parse_corpus.py never completed, or its output was moved"),
        ("bibliography", "the 4,216-work bibliography does not exist on this machine"),
        ("prerequisite_map", "the 198-edge prerequisite map was never built"),
        ("consolidated_corpus", "the extraction was never consolidated into one text"),
        ("extraction_output", "the per-course extractors never ran, or ran elsewhere"),
        ("resource_index", "resource_catalog.py never completed its index of the 11,674 PDFs"),
        ("sqlite_db", "no SQL database was ever built; the schema exists only as a spreadsheet"),
    ]
    for kind, meaning in proofs:
        L.append(f"| {kind} | {'yes' if kind in kinds else '**no**'} | {meaning} |")
    L.append("")

    report = "\n".join(L)
    path = os.path.join(args.out, "asset_hunt.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    log("")
    log(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
