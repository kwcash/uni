#!/usr/bin/env python3
"""
preflight.py — find out what is actually on this machine, then write the run script.

Nobody should hand-edit five paths into a shell script when the machine can be
asked. This walks a root directory, classifies everything it finds, and emits a
run script wired to the real layout.

It changes nothing. It reads, it reports, and it writes two files into out/.

    out/preflight_report.md   what was found, and what was not
    out/run_pipeline.sh       the generated pipeline, ready to run

Usage
-----
    python3 scripts/preflight.py --root ~/Projects
    bash out/run_pipeline.sh

Classification rules
--------------------
OCW corpus directory   a directory whose children are mostly course-code slugs
                       such as 21h-383-technology-fall-2011 or esd.260j-fall-2006
CIE syllabus directory a directory of PDFs whose names or contents carry a
                       four-digit syllabus code, or the words igcse or a level
Consolidated corpus    a large .txt holding many concatenated course pages
Existing database      a .json whose top level holds course records
Legacy script          a .py in a scripts/ directory matching the known names
"""

from __future__ import annotations

import argparse
import json
import glob
import os
import re
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import log  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_PKG = os.path.dirname(HERE)

LEGACY_SCRIPTS = [
    "extract_course_semantic.py", "extract_course_clean.py", "extract_course_summaries.py",
    "parse_corpus.py", "analyze_corpus.py", "build_courseware.py",
    "deep_harvest.py", "resource_catalog.py", "extract_corpus.py",
    "extract.py", "harvest.py", "consolidate.py",
]

EXTRACTORS = {"extract_course_semantic.py", "extract_course_clean.py", "extract_course_summaries.py"}
PATCH_MARKER = "# --- FSTEM patch:"

SLUG_RE = re.compile(r"^(\d{1,2}[a-z]?[-.][0-9a-z]{1,6}(?:-\d+)?|[a-z]{2,4}[-.][0-9a-z]{1,6})", re.I)
CIE_HINT_RE = re.compile(r"(igcse|o[-_ ]?level|a[-_ ]?level|as[-_ ]?level|cambridge|\b\d{4}[-_])", re.I)
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".cache", "site-packages"}


def walk_dirs(root: str, max_depth: int = 4, exclude: tuple[str, ...] = ()):
    """
    Walk the tree, skipping the toolkit's own directory.

    The toolkit normally sits inside the scan root, so without this the
    fixtures ship straight into the generated pipeline as though they were the
    real IGCSE syllabi and the real Western Civilization code list. Two fake
    courses would then be counted as forty.
    """
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    exclude = tuple(os.path.abspath(e) for e in exclude)
    for dirpath, dirnames, filenames in os.walk(root):
        here = os.path.abspath(dirpath)
        if any(here == e or here.startswith(e + os.sep) for e in exclude):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        yield dirpath, dirnames, filenames


def classify_ocw_dir(dirpath: str, dirnames: list[str]) -> tuple[bool, int, float, str]:
    """
    A course corpus is a directory whose children are course folders.

    Two signals, either sufficient. The children are named like course slugs, or
    the children contain the pages/ tree an OCW download produces. The second
    signal is the stronger one, because it survives any naming convention.
    """
    if len(dirnames) < 3:
        return False, 0, 0.0, ""
    slug_hits = sum(1 for d in dirnames if SLUG_RE.match(d))
    page_hits = sum(1 for d in dirnames[:60]
                    if os.path.isdir(os.path.join(dirpath, d, "pages")))
    extracted = sum(1 for d in dirnames[:60]
                    if glob.glob(os.path.join(dirpath, d, "*SEMANTIC.txt"))
                    or glob.glob(os.path.join(dirpath, d, "*CLEAN.txt"))
                    or glob.glob(os.path.join(dirpath, d, "*summary.txt")))
    n = float(len(dirnames))
    ratio = max(slug_hits, page_hits) / n
    if page_hits >= 3 or (slug_hits >= 3 and slug_hits / n >= 0.6):
        state = "extracted" if extracted >= max(1, len(dirnames) // 3) else "raw"
        return True, max(slug_hits, page_hits), round(ratio, 2), state
    return False, 0, 0.0, ""


def classify_cie_dir(dirpath: str, filenames: list[str]) -> tuple[bool, int]:
    pdfs = [f for f in filenames if f.lower().endswith((".pdf", ".txt"))]
    if len(pdfs) < 3:
        return False, 0
    hinted = sum(1 for f in pdfs if CIE_HINT_RE.search(f) or CIE_HINT_RE.search(dirpath))
    return hinted >= max(3, len(pdfs) // 3), len(pdfs)


def guess_cie_qualification(dirpath: str, filenames: list[str]) -> str | None:
    blob = (dirpath + " " + " ".join(filenames)).lower()
    if re.search(r"\bigcse\b", blob):
        return "IGCSE"
    if re.search(r"\bas\s*&\s*a\s*level|\ba[-_ ]?level\b", blob):
        return "A_Level"
    if re.search(r"\bo[-_ ]?level\b", blob):
        return "O_Level"
    return None


def sniff_database(path: str) -> tuple[bool, int]:
    try:
        if os.path.getsize(path) < 2000:
            return False, 0
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return False, 0
    if isinstance(data, dict):
        if isinstance(data.get("courses"), list):
            return True, len(data["courses"])
        vals = [v for v in data.values() if isinstance(v, dict)]
        if len(vals) >= 5 and any(("title" in v or "code" in v) for v in vals[:10]):
            return True, len(vals)
    if isinstance(data, list) and len(data) >= 5 and isinstance(data[0], dict):
        if any(k in data[0] for k in ("title", "code", "course_number")):
            return True, len(data)
    return False, 0


def script_usage(path: str) -> str | None:
    """Ask a legacy script how it wants to be called. Five second ceiling."""
    for flag in ("--help", "-h"):
        try:
            res = subprocess.run([sys.executable, path, flag], capture_output=True,
                                 text=True, timeout=5)
            out = (res.stdout or res.stderr or "").strip()
            if out and len(out) < 4000:
                return out
        except (subprocess.TimeoutExpired, OSError):
            continue
    # Fall back to the module docstring.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read(4000)
        m = re.search(r'^\s*(?:"""|\'\'\')(.{20,1200}?)(?:"""|\'\'\')', src, re.S)
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    return None


def readiness() -> tuple[list[str], list[str]]:
    """
    What the pipeline needs, and what this machine has.

    Run before anything else, because a missing library on an offline machine
    turns a two-minute run into an evening.
    """
    ok, missing = [], []

    v = sys.version_info
    if (v.major, v.minor) >= (3, 9):
        ok.append(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        missing.append(f"Python {v.major}.{v.minor} is too old. 3.9 or later required.")

    def probe(mod, why, fatal):
        try:
            __import__(mod)
            ok.append(f"{mod} ({why})")
            return True
        except ImportError:
            (missing if fatal else ok).append(
                f"{mod} MISSING ({why})" if fatal else f"{mod} absent, {why}")
            return False

    has_pdf = probe("pdfminer", "best CIE PDF text", False)
    if not has_pdf:
        has_pdf = probe("pypdf", "fallback CIE PDF text", False)
    if not has_pdf:
        missing.append("No PDF library. CIE syllabi cannot be read. "
                       "Install one:  pip install pypdf   (or pdfminer.six)")

    try:
        __import__("jsonschema")
        ok.append("jsonschema (record validation)")
    except ImportError:
        ok.append("jsonschema absent, validation will be skipped but the pipeline still runs")

    return ok, missing


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover the local layout and generate the pipeline.")
    ap.add_argument("--root", default=None,
                    help="Where to look. Default: the first of ~/Projects, /jimrogers/Projects, "
                         "/home/jimrogers/Projects, ./Projects that exists.")
    ap.add_argument("--out", default=os.path.join(ROOT_PKG, "out"))
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--include-self", action="store_true",
                    help="Scan the toolkit's own directory too. Off by default, because its "
                         "fixtures look exactly like real data.")
    ap.add_argument("--probe-scripts", action="store_true",
                    help="Run each legacy script with --help to learn its CLI. Off by default.")
    args = ap.parse_args()

    if args.root:
        candidates = [args.root]
    else:
        candidates = ["~/Projects", "/jimrogers/Projects", "/home/jimrogers/Projects",
                      "./Projects", "~/projects"]
    root = None
    for c in candidates:
        cand = os.path.abspath(os.path.expanduser(c))
        if os.path.isdir(cand):
            root = cand
            break
    if root is None:
        if args.root and ("<" in args.root or ">" in args.root):
            log("That looks like a placeholder rather than a path.")
            log("Run this first, then copy the path it prints:")
            log("  python3 scripts/whereami.py")
            return 1
        log("no root found. Tried: " + ", ".join(candidates))
        log("Pass one explicitly:  python3 scripts/preflight.py --root /path/to/Projects")
        return 1

    os.makedirs(args.out, exist_ok=True)

    ready_ok, ready_missing = readiness()
    log("readiness")
    for item in ready_ok:
        log(f"  ok      {item}")
    for item in ready_missing:
        log(f"  BLOCKED {item}")
    if ready_missing:
        log("")
        log("  Fix the blocked items before running the generated pipeline.")
        log("  Everything below still reports correctly.")
    log("")
    log(f"scanning {root} to depth {args.max_depth}")

    ocw_dirs, cie_dirs, corpora, databases, scripts, code_lists = [], [], [], [], [], []

    skip_self = (ROOT_PKG,) if not args.include_self else ()
    if skip_self and os.path.abspath(ROOT_PKG).startswith(os.path.abspath(root) + os.sep):
        log(f"  excluding the toolkit's own directory: {ROOT_PKG}")
        log("")

    for dirpath, dirnames, filenames in walk_dirs(root, args.max_depth, skip_self):
        is_ocw, hits, ratio, state = classify_ocw_dir(dirpath, dirnames)
        if is_ocw:
            ocw_dirs.append({"path": dirpath, "courses": hits, "ratio": ratio,
                             "total_children": len(dirnames), "state": state})
            dirnames[:] = []  # do not descend into a course corpus
            continue

        is_cie, npdf = classify_cie_dir(dirpath, filenames)
        if is_cie:
            cie_dirs.append({"path": dirpath, "files": npdf,
                             "qualification": guess_cie_qualification(dirpath, filenames)})

        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            low = fn.lower()
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue

            if low.endswith(".txt") and size > 500_000:
                corpora.append({"path": fp, "mb": round(size / 1e6, 1)})
            elif low.endswith(".json") and size > 2000:
                ok, n = sniff_database(fp)
                if ok:
                    databases.append({"path": fp, "records": n, "mb": round(size / 1e6, 2)})
            elif low in LEGACY_SCRIPTS:
                patched = False
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                        patched = PATCH_MARKER in fh.read(20000)
                except OSError:
                    pass
                scripts.append({"path": fp, "name": low, "patched": patched,
                                "extractor": low in EXTRACTORS})
            elif low.endswith(".txt") and re.search(r"(westciv|western.?civ|codes)", low):
                code_lists.append({"path": fp})

    ocw_dirs.sort(key=lambda d: -d["courses"])
    databases.sort(key=lambda d: -d["records"])
    corpora.sort(key=lambda d: -d["mb"])

    # ---- Report -----------------------------------------------------------
    lines = ["# FSTEM Preflight Report", "", f"Root: `{root}`", ""]
    lines.append("## Readiness")
    lines.append("")
    lines.append("| Check | State |")
    lines.append("|---|---|")
    for item in ready_ok:
        lines.append(f"| {item} | ok |")
    for item in ready_missing:
        lines.append(f"| {item} | **blocked** |")
    lines.append("")
    if ready_missing:
        lines.append("Blocked items stop the stage that needs them. The rest of the pipeline runs.")
        lines.append("")

    def table(title: str, rows: list[str], header: str, note: str = ""):
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append("None found.")
            if note:
                lines.append("")
                lines.append(note)
            lines.append("")
            return
        lines.append(header)
        lines.append("|" + "---|" * header.count("|") if False else "|" + "---|" * (header.count("|") - 1))
        lines.extend(rows)
        lines.append("")
        if note:
            lines.append(note)
            lines.append("")

    table("OCW course directories",
          [f"| `{d['path']}` | {d['courses']} | {d['ratio']} | {d['state']} |" for d in ocw_dirs],
          "| Path | Course folders | Match ratio | Extraction |",
          "Feed these to `ocw_ingest.py --dir`. Where extraction reads `raw`, the ingest parses `pages/*.html` "
          "directly, so the legacy extractors are optional. Running them first yields cleaner text.")

    table("CIE syllabus directories",
          [f"| `{d['path']}` | {d['files']} | {d['qualification'] or 'undetected'} |" for d in cie_dirs],
          "| Path | Files | Qualification |",
          "Feed these to `cie_parse.py`. Where the qualification reads undetected, pass `--qualification` explicitly.")

    table("Existing databases",
          [f"| `{d['path']}` | {d['records']} | {d['mb']} |" for d in databases[:12]],
          "| Path | Records | MB |",
          "Feed these to `ocw_ingest.py --db`. The largest is almost certainly the 231-course corpus.")

    table("Consolidated text corpora",
          [f"| `{d['path']}` | {d['mb']} |" for d in corpora[:12]],
          "| Path | MB |",
          "These are the inputs your legacy `parse_corpus.py` expects.")

    table("Legacy scripts",
          [f"| `{s['path']}` | {'extractor' if s['extractor'] else 'other'} | "
           f"{'takes a path' if s['patched'] else 'hardcoded to ~/Projects/OCW1' if s['extractor'] else 'not applicable'} |"
           for s in scripts],
          "| Path | Role | Argument handling |",
          "The three extractors hardcode `~/Projects/OCW1`. Run `patch_legacy.py` once and they take a directory. "
          "Note also that their sidebar filter deletes the `Level` field, so extractor output alone cannot settle "
          "which rung a course belongs to. `ocw_ingest.py` reads the level from the raw page for that reason.")

    table("Code lists",
          [f"| `{c['path']}` |" for c in code_lists],
          "| Path |",
          "Feed these to `ocw_ingest.py --codes`.")

    if args.probe_scripts and scripts:
        lines.append("## Legacy script usage")
        lines.append("")
        for s in scripts:
            usage = script_usage(s["path"])
            lines.append(f"### {s['name']}")
            lines.append("")
            lines.append("```")
            lines.append(usage or "no usage recovered")
            lines.append("```")
            lines.append("")

    lines.append("## What is missing")
    lines.append("")
    missing = []
    if not ocw_dirs and not databases:
        missing.append("No OCW course directory and no existing database. Nothing to ingest for the OCW rungs.")
    if not cie_dirs:
        missing.append("No CIE syllabus directory. Grades 9 to 13 will be empty.")
    if not code_lists and not any("westciv" in d["path"].lower() for d in ocw_dirs):
        missing.append("No Western Civilization code list. Export one as 'CODE | Title | Instructor' per line.")
    if missing:
        for m in missing:
            lines.append(f"- {m}")
    else:
        lines.append("Nothing. Every stage has an input.")
    lines.append("")

    report = "\n".join(lines)
    report_path = os.path.join(args.out, "preflight_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)

    # ---- Generated pipeline ----------------------------------------------
    sh = [
        "#!/usr/bin/env bash",
        "# Generated by preflight.py. Regenerate rather than hand-edit.",
        f"# Root scanned: {root}",
        "set -euo pipefail",
        "",
        f'PKG="{ROOT_PKG}"',
        f'OUT="{os.path.abspath(args.out)}"',
        'mkdir -p "$OUT"',
        'EXT="${EXT:-.pdf}"   # override for pre-extracted text:  EXT=.txt bash run_pipeline.sh',
        'say() { printf "\\n\\033[1m== %s ==\\033[0m\\n" "$1"; }',
        "",
        "say '0. Legacy extraction'",
    ]
    py_stages: list[str] = []

    def source_type_for(path: str) -> str:
        low = os.path.basename(path.rstrip("/")).lower() + " " + path.lower()
        # Western Civilization first, because "ocwwestcivGrad" carries both
        # "westciv" and "grad" and the harvest it belongs to is the former.
        # \w* rather than .? so "westerncivilization" matches as well as "westciv".
        if re.search(r"west\w*civ|humanit|liberal.?arts", low):
            return "ocw_westciv"
        if re.search(r"undergrad|ugrad|undergr", low):
            return "ocw_undergraduate"
        if re.search(r"\bgrad|masters|ocw1|graduate", low):
            return "ocw_graduate"
        return "ocw_undergraduate"

    def tag_for(path: str) -> str:
        base = os.path.basename(path.rstrip("/")) or "dir"
        return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower() or "dir"


    extractor = next((sc for sc in scripts if sc["name"] == "extract_course_semantic.py"), None)
    scripts_dir = os.path.dirname(extractor["path"]) if extractor else None
    raw_dirs = [d for d in ocw_dirs if d.get("state") == "raw"]

    if extractor and raw_dirs:
        if not extractor["patched"]:
            sh.append(f'python3 "$PKG/scripts/patch_legacy.py" --scripts-dir "{scripts_dir}"')
        for d in raw_dirs:
            sh.append(f'python3 "{extractor["path"]}" "{d["path"]}"')
            py_stages.append(f'run([r"{extractor["path"]}", r"{d["path"]}"], optional=True)')
    elif not extractor:
        sh.append("echo 'no extract_course_semantic.py found; ingest will read pages/*.html directly'")
    else:
        sh.append("echo 'every course directory already carries extractor output'")

    # Consolidation. Optional for the new ingest, required by the legacy
    # parse_corpus.py, and cheap either way.
    if ocw_dirs:
        sh.append("")
        sh.append("# Consolidated corpora. Not needed by the new ingest; parse_corpus.py wants them.")
        for d in ocw_dirs[:6]:
            tag = tag_for(d["path"]).upper()
            sh.append(f'python3 "$PKG/scripts/consolidate_corpus.py" --dir "{d["path"]}" \\')
            sh.append(f'  --out "$OUT/ALL_SEMANTIC_{tag}.txt" || echo "consolidation skipped for {tag}"')
            py_stages.append(
                f'run([S("consolidate_corpus.py"), "--dir", r"{d["path"]}", '
                f'"--out", O("ALL_SEMANTIC_{tag}.txt")], optional=True)')

    sh += ["", "say '1. Ingest'"]

    used_types = set()

    # Two directories may legitimately share a source type. Two Western
    # Civilization harvests are both ocw_westciv, and the merge stage exists to
    # reconcile them. Only the output filename has to be unique.
    for d in ocw_dirs[:6]:
        st = source_type_for(d["path"])
        used_types.add(st)
        sh.append(f'python3 "$PKG/scripts/ocw_ingest.py" --dir "{d["path"]}" \\')
        sh.append(f'  --source-type {st} --out "$OUT/{st}__{tag_for(d["path"])}.jsonl"')
        py_stages.append(
            f'run([S("ocw_ingest.py"), "--dir", r"{d["path"]}", '
            f'"--source-type", "{st}", "--out", O("{st}__{tag_for(d["path"])}.jsonl")])')

    for d in databases[:1]:
        if "ocw_graduate" not in used_types:
            used_types.add("ocw_graduate")
            sh.append(f'python3 "$PKG/scripts/ocw_ingest.py" --db "{d["path"]}" \\')
            sh.append('  --source-type ocw_graduate --out "$OUT/ocw_graduate.jsonl"')
            py_stages.append(
                f'run([S("ocw_ingest.py"), "--db", r"{d["path"]}", '
                f'"--source-type", "ocw_graduate", "--out", O("ocw_graduate.jsonl")])')

    for c in code_lists[:1]:
        if "ocw_westciv" not in used_types:
            sh.append(f'python3 "$PKG/scripts/ocw_ingest.py" --codes "{c["path"]}" \\')
            sh.append('  --source-type ocw_westciv --out "$OUT/ocw_westciv.jsonl"')
            py_stages.append(
                f'run([S("ocw_ingest.py"), "--codes", r"{c["path"]}", '
                f'"--source-type", "ocw_westciv", "--out", O("ocw_westciv.jsonl")])')

    for i, d in enumerate(cie_dirs[:4]):
        qual = d["qualification"]
        tag = (qual or f"cie{i}").lower()
        qflag = f' --qualification {qual}' if qual else ""
        sh.append(f'python3 "$PKG/scripts/cie_parse.py" --in "{d["path"]}" --ext "$EXT"{qflag} \\')
        sh.append(f'  --out "$OUT/cie_{tag}.jsonl"')
        qargs = f', "--qualification", "{qual}"' if qual else ""
        py_stages.append(
            f'run([S("cie_parse.py"), "--in", r"{d["path"]}", "--ext", os.environ.get("EXT", ".pdf")'
            f'{qargs}, "--out", O("cie_{tag}.jsonl")], optional=True)')

    py_stages.append(
        'run([S("merge_dedupe.py"), "--in"] + '
        '[os.path.join(OUT, f) for f in sorted(os.listdir(OUT)) '
        'if f.endswith(".jsonl") and (f.startswith("ocw_") or f.startswith("cie_"))] + '
        '["--out", O("fstem_repository.jsonl"), '
        '"--level-overrides", os.path.join(PKG, "overrides", "level_corrections.csv"), '
        '"--exclude", os.path.join(PKG, "overrides", "exclusions.txt"), '
        '"--expect", "ocw_westciv=59", "ocw_graduate=231", "ocw_undergraduate=78", '
        '"--report", O("merge_report.md")])')
    py_stages.append(
        'run([S("score_verticals.py"), "--in", O("fstem_repository.jsonl"), '
        '"--out", O("fstem_repository_scored.jsonl"), '
        '"--lexicon", os.path.join(PKG, "schema", "lexicon.json"), '
        '"--report", O("coverage_matrix.md")])')
    py_stages.append(
        'run([S("emit_facts.py"), "--in", O("fstem_repository_scored.jsonl"), "--out", OUT])')

    sh += [
        "",
        "say '2. Merge and dedupe'",
        'python3 "$PKG/scripts/merge_dedupe.py" \\',
        '  --in "$OUT"/ocw_*.jsonl "$OUT"/cie_*.jsonl \\',
        '  --out "$OUT/fstem_repository.jsonl" \\',
        '  --level-overrides "$PKG/overrides/level_corrections.csv" \\',
        '  --exclude "$PKG/overrides/exclusions.txt" \\',
        '  --expect ocw_westciv=59 ocw_graduate=231 ocw_undergraduate=78 \\',
        '  --report "$OUT/merge_report.md" > "$OUT/merge_stdout.md"',
        'tail -n 40 "$OUT/merge_stdout.md"',
        "",
        "say '3. Score verticals and threads'",
        'python3 "$PKG/scripts/score_verticals.py" \\',
        '  --in "$OUT/fstem_repository.jsonl" \\',
        '  --out "$OUT/fstem_repository_scored.jsonl" \\',
        f'  --lexicon "$PKG/schema/lexicon.json" \\',
        '  --report "$OUT/coverage_matrix.md" > "$OUT/coverage_stdout.md"',
        'tail -n 40 "$OUT/coverage_stdout.md"',
        "",
        "say '4. Regenerate the facts'",
        'python3 "$PKG/scripts/emit_facts.py" --in "$OUT/fstem_repository_scored.jsonl" --out "$OUT" > /dev/null',
        'echo "facts written to $OUT/FACTS.md"',
        "",
        "say 'Done'",
        'echo "Repository:      $OUT/fstem_repository_scored.jsonl"',
        'echo "Merge report:    $OUT/merge_report.md"',
        'echo "Coverage matrix: $OUT/coverage_matrix.md"',
        "",
    ]

    sh_path = os.path.join(args.out, "run_pipeline.sh")
    with open(sh_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sh))
    os.chmod(sh_path, 0o755)

    # A Python twin, because bash is not present on every machine and a shell
    # script that cannot run is a pipeline that does not exist.
    py = [
        "#!/usr/bin/env python3",
        '"""Generated by preflight.py. Same stages as run_pipeline.sh, no shell required."""',
        "import os, subprocess, sys",
        "",
        f'PKG = r"{ROOT_PKG}"',
        f'OUT = r"{os.path.abspath(args.out)}"',
        'os.makedirs(OUT, exist_ok=True)',
        'PY = sys.executable',
        "",
        "def run(args, optional=False):",
        "    script = os.path.basename(str(args[0]))",
        "    target = next((os.path.basename(str(a).rstrip(os.sep)) for a in args[1:] if os.sep in str(a)), '')",
        "    label = script + ('  ' + target if target else '')",
        "    print('', flush=True)",
        "    print('>>> ' + label, flush=True)",
        "    r = subprocess.run([PY] + args)",
        "    if r.returncode != 0:",
        "        if optional:",
        "            print('    (optional stage failed, continuing)', flush=True)",
        "            return False",
        "        print('', flush=True)",
        "        print('FAILED at: ' + label, flush=True)",
        "        sys.exit(r.returncode)",
        "    return True",
        "",
        "S = lambda n: os.path.join(PKG, 'scripts', n)",
        "O = lambda n: os.path.join(OUT, n)",
        "",
    ]

    for spec in py_stages:
        py.append(spec)

    py += [
        "",
        "print('')",
        "print('== Done ==')",
        'print("Repository:      " + O("fstem_repository_scored.jsonl"))',
        'print("Merge report:    " + O("merge_report.md"))',
        'print("Coverage matrix: " + O("coverage_matrix.md"))',
        'print("Facts:           " + O("FACTS.md"))',
        "",
    ]

    py_path = os.path.join(args.out, "run_pipeline.py")
    with open(py_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(py))
    os.chmod(py_path, 0o755)

    log("")
    log(f"report:   {report_path}")
    log(f"pipeline: {sh_path}")
    log("")
    log(f"Review the pipeline, then run:  bash {sh_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
