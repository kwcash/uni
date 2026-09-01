#!/usr/bin/env python3
"""Build the FSTEM 2026 tree from fstem_rename_map.csv.
Creates  ~/Projects/FSTEM2026/{VERTICAL}/{RUNG}/{FSTEM-ID}/
  - source.txt      copy of the extracted course text (renamed)
  - IDENTITY.md     the mapping record: FSTEM id <-> MIT origin
READ-ONLY on all sources. Never touches corpus/text or the OCW repos.
Re-runnable: refreshes files in place.

Usage:
  python3 build_fstem_tree.py            # build everything with text
  python3 build_fstem_tree.py --top 30   # only the 30 largest texts
"""

import csv
import os
import shutil
import sys

MAP = os.path.expanduser("~/Projects/scripts/fstem_rename_map.csv")
ROOT = os.path.expanduser("~/Projects/FSTEM2026")
TEXT = os.path.expanduser("~/Projects/corpus/text")

top_n = None
if "--top" in sys.argv:
    top_n = int(sys.argv[sys.argv.index("--top") + 1])

rows = list(csv.DictReader(open(MAP)))
rows = [r for r in rows if r["text_folder"]]          # only assets with text
rows.sort(key=lambda r: -int(r["text_kb"]))
if top_n:
    rows = rows[:top_n]

built, missing = 0, 0
for r in rows:
    src = os.path.join(TEXT, r["text_repo"], r["text_folder"] + ".txt")
    if not os.path.isfile(src):
        missing += 1
        continue
    vert = r["fstem_id"].split("-")[1]
    rung = r["fstem_id"].split("-")[2]
    dest_dir = os.path.join(ROOT, vert, rung, r["fstem_id"])
    os.makedirs(dest_dir, exist_ok=True)

    shutil.copy2(src, os.path.join(dest_dir, "source.txt"))

    with open(os.path.join(dest_dir, "IDENTITY.md"), "w") as f:
        f.write(f"""# {r['fstem_id']}

**FSTEM title (2026):** {r['fstem_title_2026']}
**Origin:** MIT OCW {r['mit_code']} — {r['mit_title']}
**Rung:** {r['rung']} ({rung})   **Vertical:** {r['vertical']} ({vert})
**Source text:** {r['text_repo']}/{r['text_folder']}.txt ({r['text_kb']} KB)
**License:** CC BY-NC-SA (MIT OpenCourseWare). FSTEM adaptation 2026.

Primer status: PENDING
""")
    built += 1

print(f"built {built} course folders under {ROOT}")
if missing:
    print(f"warning: {missing} mapped texts not found on disk")
print("next: primers land beside source.txt as PRIMER.md")
