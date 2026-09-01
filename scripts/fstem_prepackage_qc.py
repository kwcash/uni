#!/usr/bin/env python3
"""FSTEM pre-package editorial and structural check."""
VERSION = "fstem_prepackage_qc v024 (AUDIT-024)"

import argparse
import os
import re
import sys
from collections import Counter

SECTIONS = [
    "1. What this module teaches",
    "2. Substrate",
    "3. Currency",
    "4. Sessions",
    "5. The deposit",
    "6. Readings",
    "7. Plus layer",
    "8. Provenance",
]

PLUS_FIELDS = [
    "Currency slot",
    "Patent hook",
    "Research thread",
    "Doctrine-to-code artifact",
    "Red Cell target",
    "Sponsor slot",
    "Micro-credential",
    "Reproducibility deposit",
]

DEPOSIT_FIELDS = ["Claim.", "Evidence.", "Completion criterion.", "Re-run."]

HEADER_KEYS = [
    "Module status",
    "Parent:",
    "Module:",
    "Rung:",
    "Track overlay:",
    "Substrate:",
    "Currency refreshed:",
    "Micro-credential:",
    "Variant:",
]

DASHES = {"—": "em-dash", "–": "en-dash"}

COMMA_EXEMPT_SECTIONS = {"6. Readings", "7. Plus layer"}
COMMA_EXEMPT_FIELDS = ("**Evidence.**", "**Completion criterion.**", "**Re-run.**")


def split_sections(lines):
    out, cur = {}, None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur:
            out[cur].append((i + 1, line))
    return out


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])(?:\*\*)?\s+", text) if s.strip()]


def check(path):
    fails, warns = [], []
    raw = open(path, encoding="utf-8").read()
    lines = raw.split("\n")

    def fail(n, msg):
        fails.append(f"{os.path.basename(path)}:{n}: {msg}")

    # dashes outside title lines
    for n, line in enumerate(lines, 1):
        if line.startswith("# "):
            continue
        for ch, name in DASHES.items():
            if ch in line:
                fail(n, f"{name} outside a title line")

    # header
    if not lines or not lines[0].startswith("# "):
        fail(1, "no title line")
    head = "\n".join(lines[:12])
    for key in HEADER_KEYS:
        if key not in head:
            fail(1, f"header missing {key}")
    if not re.search(r"Module status: DRAFT \d+ · \d{4}-\d{2}-\d{2} · batch [A-Z]+(?:-[A-Z]+)*-\d{3}", head):
        fail(1, "header Module status malformed or missing batch tag")
    if not re.search(r"\*\*Parent:\*\* FSTEM-(?:[A-Z0-9]+-R\d-\d+|AI-SPINE-\d{3}),", head):
        fail(1, "header Parent not a parseable id")
    if not re.search(r"\*\*Module:\*\* M\d+ of \d+", head):
        fail(1, "header Module not in M<n> of <N> form")
    if not re.search(r"status \*\*(live|partial|dead)\*\*", head):
        fail(1, "header Substrate carries no live, partial or dead status")
    if not re.search(r"Currency refreshed:\*\* \d{4}-\d{2}-\d{2} · \*\*next refresh due:\*\* \d{4}-\d{2}-\d{2}", head):
        fail(1, "header Currency refreshed missing one of the two dates")

    secs = split_sections(lines)
    order = [s for s in secs if s in SECTIONS]
    if order != SECTIONS:
        fail(1, f"section order wrong or incomplete: {order}")

    # deposit block
    dep = secs.get("5. The deposit", [])
    deptext = "\n".join(l for _, l in dep)
    for f in DEPOSIT_FIELDS:
        if f"**{f}**" not in deptext:
            fail(1, f"deposit missing field {f}")
    if re.search(r"\bpapers?\b", deptext, re.I):
        fail(1, "the word 'paper' appears inside section 5")
    if "Carry the gate amendment" not in deptext:
        fail(1, "deposit missing the gate sentence")
    if not re.search(r"drawn from .+ built in FSTEM-[A-Z0-9-]+-M\d", deptext):
        fail(1, "gate amendment names no prior artifact by module id")
    crit = ""
    for _, l in dep:
        if l.startswith("**Completion criterion.**"):
            crit = l
    if not re.search(r"at least \d", crit, re.I):
        fail(1, "completion criterion missing the 'at least N' form")
    if not re.search(r"to within \d+ percent", crit):
        fail(1, "completion criterion missing the 'to within N percent' form")

    # plus layer order and separator
    plus = [l for _, l in secs.get("7. Plus layer", []) if l.startswith("**")]
    found = []
    for l in plus:
        m = re.match(r"\*\*([^*]+?)([.:])\*\*", l)
        if not m:
            continue
        found.append(m.group(1))
        if m.group(2) != ":":
            fail(1, f"plus-layer field '{m.group(1)}' uses a period rather than a colon")
    if found != PLUS_FIELDS:
        fail(1, f"plus-layer field order wrong: {found}")

    # divergence declarations
    diverges = "diverging from course status" in head
    s2 = "\n".join(l for _, l in secs.get("2. Substrate", []))
    s8 = "\n".join(l for _, l in secs.get("8. Provenance", []))
    if diverges:
        if "diverg" not in s2.lower():
            fail(1, "header declares a divergence that section 2 never states")
        if "diverg" not in s8.lower():
            fail(1, "header declares a divergence that section 8 never states")

    # openers
    for name, body in secs.items():
        for n, line in body:
            stripped = re.sub(r"^[*\->\d.\s]+", "", line)
            if re.match(r"(It is|There are|There is)\b", stripped):
                fail(n, "line opens with 'It is' or 'There are'")

    # comma rule
    for name, body in secs.items():
        if name in COMMA_EXEMPT_SECTIONS:
            continue
        for n, line in body:
            if line.startswith(COMMA_EXEMPT_FIELDS):
                continue
            if not line.strip():
                continue
            for s in sentences(line):
                if s.count(",") > 2:
                    fail(n, f"sentence carries {s.count(',')} commas: {s.strip()[:90]}")
    return fails, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--batch")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    print(VERSION)
    files = sorted(
        os.path.join(a.directory, f)
        for f in os.listdir(a.directory)
        if f.endswith(".md")
    )
    if a.batch:
        files = [f for f in files if f"batch {a.batch}" in open(f, encoding="utf-8").read()]
        print(f"scoped to batch {a.batch}: {len(files)} guides")
    allf, causes = [], Counter()
    for f in files:
        fails, _ = check(f)
        allf += fails
        for x in fails:
            causes[x.split(": ", 1)[1].split(":")[0][:60]] += 1
    for x in allf:
        print("FAIL " + x)
    if a.summary:
        for c, n in causes.most_common():
            print(f"{n:5d}  {c}")
    print(f"{len(files)} guides · {len(allf)} FAIL · 0 WARN")
    sys.exit(1 if allf else 0)


if __name__ == "__main__":
    main()
