#!/usr/bin/env python3
"""Read-only text extraction: all OCW repos -> one txt per course.
NEVER writes inside any source directory."""

import os
from html.parser import HTMLParser

REPOS = ["OCW1", "ocwundergr", "ocwwesternCIV", "ocwwestcivGrad"]

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = False
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip = True
    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = False
    def handle_data(self, data):
        if not self.skip:
            t = data.strip()
            if t:
                self.parts.append(t)

def html_to_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            p = TextExtractor()
            p.feed(f.read())
            return "\n".join(p.parts)
    except Exception as e:
        return f"[error reading {path}: {e}]"

grand_total = 0

for repo in REPOS:
    source_dir = os.path.expanduser(f"~/Projects/{repo}")
    out_dir = os.path.expanduser(f"~/Projects/corpus/text/{repo}")

    if not os.path.isdir(source_dir):
        print(f"SKIP {repo}: directory not found")
        continue

    os.makedirs(out_dir, exist_ok=True)

    courses = sorted(d for d in os.listdir(source_dir)
                     if os.path.isdir(os.path.join(source_dir, d))
                     and not d.startswith("."))

    print(f"\n=== {repo}: {len(courses)} courses ===")

    for i, course in enumerate(courses, 1):
        croot = os.path.join(source_dir, course)
        out = [f"COURSE: {course}", "=" * 60]
        n = 0
        for root, dirs, files in os.walk(croot):
            dirs[:] = [d for d in dirs if d not in
                       ("static_shared", "static_resources", "external-resources")]
            depth = root[len(croot):].count(os.sep)
            if depth > 4:
                dirs[:] = []
                continue
            for iname in ("index.html", "index.htm"):
                if iname in files:
                    rel = os.path.relpath(os.path.join(root, iname), croot)
                    out.append(f"\n--- {rel} ---\n")
                    out.append(html_to_text(os.path.join(root, iname)))
                    n += 1
                    break
        outfile = os.path.join(out_dir, course + ".txt")
        with open(outfile, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        kb = os.path.getsize(outfile) / 1024
        print(f"[{i}/{len(courses)}] {course}: {n} pages -> {kb:.0f} KB")
        grand_total += 1

print(f"\nDone. {grand_total} course texts across {len(REPOS)} repos in ~/Projects/corpus/text/")