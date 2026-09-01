#!/usr/bin/env python3
"""
consolidate_corpus.py — pull every per-course text file into one corpus.

This is the fourth script in your extraction chain and the only one not among
the three you uploaded. The sequence you remember runs:

    extract_course_semantic.py   pages/*.html  ->  <course>SEMANTIC.txt   (per course)
    consolidate_corpus.py        <course>SEMANTIC.txt  ->  ALL_SEMANTIC_AGGRESSIVE.txt
    parse_corpus.py              ALL_SEMANTIC_AGGRESSIVE.txt  ->  ocw_database.json

Rebuilt rather than hunted for, because the job is deterministic and the output
format is fixed by what the extractors already emit. Each per-course file opens
with a `COURSE: <slug>` line, so concatenation alone yields a corpus whose
record boundaries a parser can find.

Reproduces the known corpus shape: 231 courses, 69,274 lines, 3.5 MB.

Usage
-----
    python3 scripts/consolidate_corpus.py --dir ~/Projects/OCW1 \\
        --out ~/Projects/Consolidation/analysis/ALL_SEMANTIC_AGGRESSIVE.txt

    # pick a different extractor's output
    python3 scripts/consolidate_corpus.py --dir ~/Projects/OCW1 --suffix CLEAN.txt \\
        --out ~/Projects/Consolidation/analysis/ALL_CLEAN.txt

    # no extractor output on disk? read the pages directly
    python3 scripts/consolidate_corpus.py --dir ~/Projects/OCW_UNDERGRAD --from-pages \\
        --out ~/Projects/Consolidation/analysis/ALL_UNDERGRAD.txt
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import log  # noqa: E402

# Import the page reader from the ingest so both paths produce identical text.
try:
    from ocw_ingest import PAGE_SPECS, _html_to_text
except ImportError:  # pragma: no cover
    PAGE_SPECS, _html_to_text = [], None

SUFFIXES = ["SEMANTIC.txt", "CLEAN.txt", "summary.txt"]
RULE = "=" * 75


def per_course_file(folder: str, suffix: str | None) -> str | None:
    order = [suffix] if suffix else SUFFIXES
    for suf in order:
        hits = sorted(glob.glob(os.path.join(folder, "*" + suf)))
        if hits:
            return hits[0]
    return None


def text_from_pages(folder: str, slug: str) -> str:
    """Same five pages the extractors read, same labels, same order."""
    if not _html_to_text:
        return ""
    parts = [f"COURSE: {slug}", RULE, ""]
    got = 0
    for rel, label in PAGE_SPECS:
        fp = os.path.join(folder, rel)
        if not os.path.exists(fp):
            continue
        body = _html_to_text(fp)
        if not body.strip():
            continue
        parts += [f"{label}:", "-" * 75, body, ""]
        got += 1
    return "\n".join(parts) if got else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Concatenate per-course extraction files into one corpus.")
    ap.add_argument("--dir", dest="root", required=True, help="Directory of course folders.")
    ap.add_argument("--out", required=True, help="Consolidated output path.")
    ap.add_argument("--suffix", default=None, choices=SUFFIXES,
                    help="Which extractor output to use. Default: the first of "
                         "SEMANTIC.txt, CLEAN.txt, summary.txt that exists.")
    ap.add_argument("--from-pages", action="store_true",
                    help="Ignore extractor output and read pages/*.html directly.")
    ap.add_argument("--manifest", default=None,
                    help="Write a CSV of course, source, bytes, lines. Defaults beside --out.")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        log(f"not a directory: {root}")
        return 1

    folders = [d for d in sorted(os.listdir(root))
               if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")]
    if not folders:
        log(f"no course folders under {root}")
        return 1

    log(f"consolidating {len(folders)} course folders from {root}")

    chunks, manifest, empty = [], [], []
    for slug in folders:
        folder = os.path.join(root, slug)
        body, source = "", ""

        if not args.from_pages:
            fp = per_course_file(folder, args.suffix)
            if fp:
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                        body = fh.read()
                    source = os.path.basename(fp)
                except OSError:
                    body = ""

        if not body.strip():
            body = text_from_pages(folder, slug)
            source = "pages/*.html" if body.strip() else ""

        if not body.strip():
            empty.append(slug)
            manifest.append((slug, "none", 0, 0))
            continue

        # Guarantee the record delimiter a parser looks for.
        if not body.lstrip().startswith("COURSE:"):
            body = f"COURSE: {slug}\n{RULE}\n\n{body}"

        chunks.append(body.rstrip() + "\n")
        manifest.append((slug, source, len(body), body.count("\n") + 1))

    if not chunks:
        # Loud, not fatal. An empty directory is a finding to report, and halting
        # a pipeline on it hides every stage that would have succeeded after.
        log("")
        log(f"  NOTHING EXTRACTABLE in {root}")
        log(f"  {len(folders)} course folder(s) yielded no text. Neither extractor output")
        log("  nor pages/*.html was found. Run the extractors first, or pass --from-pages,")
        log("  or check that this directory really holds OCW course folders.")
        return 0

    out = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    corpus = ("\n\n" + RULE + "\n\n").join(chunks)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(corpus)

    man_path = args.manifest or os.path.splitext(out)[0] + "_manifest.csv"
    with open(man_path, "w", encoding="utf-8") as fh:
        fh.write("course,source,bytes,lines\n")
        for slug, src, nb, nl in manifest:
            fh.write(f'"{slug}","{src}",{nb},{nl}\n')

    lines = corpus.count("\n") + 1
    log("")
    log(f"wrote {out}")
    log(f"  courses:  {len(chunks)} of {len(folders)}")
    log(f"  lines:    {lines:,}")
    log(f"  size:     {len(corpus) / 1e6:.2f} MB")
    log(f"  manifest: {man_path}")

    # Rule 4. Silence must be loud. Name what produced nothing.
    if empty:
        log("")
        log(f"  {len(empty)} course folder(s) produced no text:")
        for slug in empty[:25]:
            log(f"    - {slug}")
        if len(empty) > 25:
            log(f"    ...and {len(empty) - 25} more")
        log("  Those courses are absent from the corpus. Harvest or re-extract them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
