#!/usr/bin/env python3
"""Distill each FSTEM2026 course's source.txt into a compact BRIEF.md.
Keeps: front page, syllabus, calendar, readings, assignments (trimmed).
Drops: OCW chrome, video transcripts, repeated navigation.
Target: 5-15KB per brief, ready for LLM primer work in small batches.

Usage:
  python3 distill_briefs.py            # all built course folders
  python3 distill_briefs.py FSTEM-ECON-R3-001   # one course
"""

import os
import re
import sys

ROOT = os.path.expanduser("~/Projects/FSTEM2026")

# section path keywords -> priority (lower = keep more)
WANT = [
    ("syllabus", 1, 6000),      # keep up to 6KB
    ("calendar", 2, 2500),
    ("readings", 2, 3000),
    ("assignments", 3, 2000),
    ("lecture-notes", 4, 1500),
]

CHROME = re.compile(
    r"^(Browse Course Material|Course Info|Search|Give Now|Menu|Toggle|"
    r"Download|Share|facebook|twitter|linkedin|MIT OpenCourseWare|"
    r"Massachusetts Institute of Technology|Accessibility|Creative Commons|"
    r"OCW is open|Learn more|→|←|»|«|\d+ ?/ ?\d+)", re.IGNORECASE)

def clean(text, cap):
    seen, out, size = set(), [], 0
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) < 3 or CHROME.match(s):
            continue
        if s in seen and len(s) < 60:      # collapse repeated short chrome
            continue
        seen.add(s)
        out.append(s)
        size += len(s) + 1
        if size > cap:
            out.append("[...trimmed]")
            break
    return "\n".join(out)

def split_sections(raw):
    """source.txt format: '--- rel/path ---' markers."""
    parts = re.split(r"\n--- (.+?) ---\n", raw)
    head = parts[0]
    sections = list(zip(parts[1::2], parts[2::2]))
    return head, sections

def distill(course_dir):
    src = os.path.join(course_dir, "source.txt")
    ident = os.path.join(course_dir, "IDENTITY.md")
    if not os.path.isfile(src):
        return None
    raw = open(src, encoding="utf-8", errors="ignore").read()
    head, sections = split_sections(raw)

    brief = []
    if os.path.isfile(ident):
        brief.append(open(ident).read().strip())
        brief.append("\n---\n")

    # front page: first root index section, else the head
    front = next((body for path, body in sections
                  if path.count("/") <= 1 and "index" in path), head)
    brief.append("## Front page\n")
    brief.append(clean(front, 2500))

    used = set()
    for key, _prio, cap in WANT:
        matches = [(p, b) for p, b in sections if key in p.lower() and p not in used]
        if not matches:
            continue
        # prefer the shortest path (the section landing page)
        matches.sort(key=lambda x: len(x[0]))
        path, body = matches[0]
        used.add(path)
        brief.append(f"\n## {key.title()} ({path})\n")
        brief.append(clean(body, cap))

    out = os.path.join(course_dir, "BRIEF.md")
    text = "\n".join(brief)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text) // 1024

targets = []
only = sys.argv[1] if len(sys.argv) > 1 else None
for vert in sorted(os.listdir(ROOT)):
    vdir = os.path.join(ROOT, vert)
    if not os.path.isdir(vdir):
        continue
    for rung in sorted(os.listdir(vdir)):
        rdir = os.path.join(vdir, rung)
        for course in sorted(os.listdir(rdir)):
            if only and course != only:
                continue
            targets.append(os.path.join(rdir, course))

done = 0
for t in targets:
    kb = distill(t)
    if kb is not None:
        print(f"  {os.path.basename(t):22s} BRIEF.md {kb} KB")
        done += 1
print(f"\n{done} briefs written")
