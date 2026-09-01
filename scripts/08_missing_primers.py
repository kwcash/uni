#!/usr/bin/env python3
"""
Name the courses whose primers never reached the Samsung.

A course with no primer on disk produced TITLE_PENDING modules. This lists them
with their MIT code and title so they can be hunted by name, and writes a CSV.

    python3 08_missing_primers.py --db ../out/fstem.db
"""
import argparse, csv, os, sqlite3

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--primers", default="../primers", help="primer directory, to confirm absence")
ap.add_argument("--out", default="../MISSING_PRIMERS.csv")
a = ap.parse_args()

cx = sqlite3.connect(a.db)
cx.row_factory = sqlite3.Row

rows = cx.execute(
    "SELECT m.parent_fstem_id AS fstem_id, a.code, a.title, a.rung, a.term, a.year, "
    "       COUNT(*) AS pending, "
    "       (SELECT COUNT(*) FROM modules x WHERE x.parent_fstem_id = m.parent_fstem_id) AS total "
    "FROM modules m LEFT JOIN assets a ON a.asset_id = m.asset_id "
    "WHERE m.title = 'TITLE_PENDING' "
    "GROUP BY m.parent_fstem_id ORDER BY a.code").fetchall()

if not rows:
    print("no TITLE_PENDING modules. Every course has a primer.")
    raise SystemExit(0)

# what filenames the parser would accept, so the hunt knows what to look for
def candidates(fid):
    return [f"FSTEM-{fid}-PRIMER.md", f"{fid}-PRIMER.md", f"{fid}.md",
            f"any .md whose filename contains {fid}"]

have = set()
if os.path.isdir(a.primers):
    for root, _, files in os.walk(a.primers):
        have.update(files)

print(f"{len(rows)} courses have no primer, accounting for "
      f"{sum(r['pending'] for r in rows)} TITLE_PENDING modules\n")
print(f"{'MIT code':<16}{'FSTEM id':<24}{'rung':<15}{'term':<14}title")
print("-" * 100)
for r in rows:
    term = f"{r['term'] or ''} {r['year'] or ''}".strip() or "unknown"
    print(f"{r['code'] or '?':<16}{r['fstem_id']:<24}{r['rung'] or '?':<15}{term:<14}{r['title'] or '?'}")

print("\nfilenames the parser accepts, for the hunt:")
for r in rows:
    print(f"  {r['code'] or '?':<16} {candidates(r['fstem_id'])[0]}")

with open(a.out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["mit_code", "fstem_id", "title", "rung", "term",
                                       "pending_modules", "total_modules", "expected_filename"])
    w.writeheader()
    for r in rows:
        w.writerow(dict(mit_code=r["code"], fstem_id=r["fstem_id"], title=r["title"],
                        rung=r["rung"], term=f"{r['term'] or ''} {r['year'] or ''}".strip(),
                        pending_modules=r["pending"], total_modules=r["total"],
                        expected_filename=f"FSTEM-{r['fstem_id']}-PRIMER.md"))
print(f"\nwrote {a.out}")
cx.close()
