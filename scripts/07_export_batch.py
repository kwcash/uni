#!/usr/bin/env python3
"""
FSTEM batch exporter. Pulls one track's drafting material into a single upload.

The drafting agent needs what only the database knows: real module titles parsed
from the primers, substrate blocks, variants, refresh windows and bibliography
seeds. This writes that slice and nothing else.

    python3 07_export_batch.py --db ../out/fstem.db --track m1
    python3 07_export_batch.py --db ../out/fstem.db --track m1 --skip-drafted
"""
import argparse, csv, json, os, sqlite3, sys

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--track", required=True, help="track_id, for example m1")
ap.add_argument("--out", default="batch_export")
ap.add_argument("--skip-drafted", action="store_true",
                help="omit modules whose status has moved off skeleton")
a = ap.parse_args()

os.makedirs(a.out, exist_ok=True)
cx = sqlite3.connect(a.db)
cx.row_factory = sqlite3.Row

courses = cx.execute(
    "SELECT tc.seq, tc.role, tc.fstem_id, s.asset_id, s.code, s.title, s.rung, s.term, s.year, "
    "       s.substrate_status, COALESCE(s.verticals_override, s.verticals_scored) AS verts "
    "FROM track_courses tc JOIN assets s ON s.fstem_id = tc.fstem_id "
    "WHERE tc.track_id = ? ORDER BY tc.seq", (a.track,)).fetchall()
if not courses:
    sys.exit(f"no courses for track {a.track}. Run 05_load_tracks.py first.")

bundle, rows, skipped = [], [], 0
for c in courses:
    mods = cx.execute(
        "SELECT * FROM modules WHERE parent_fstem_id = ? ORDER BY module_index",
        (c["fstem_id"],)).fetchall()
    bib = [dict(author=b[0], year=b[1], title=b[2]) for b in cx.execute(
        "SELECT author, year, COALESCE(title,'') FROM bibliography "
        "WHERE asset_id = ? AND is_citation = 1 LIMIT 15", (c["asset_id"],)).fetchall()]

    entry = dict(seq=c["seq"], role=c["role"], fstem_id=c["fstem_id"], mit_code=c["code"],
                 course_title=c["title"], rung=c["rung"],
                 term=f"{c['term'] or ''} {c['year'] or ''}".strip(),
                 vertical=(c["verts"] or "").split(",")[0],
                 substrate_status=c["substrate_status"], bibliography_seed=bib, modules=[])

    for m in mods:
        if a.skip_drafted and (m["status"] or "skeleton") != "skeleton":
            skipped += 1
            continue
        d = dict(module_id=m["module_id"], module_index=m["module_index"],
                 module_count=m["module_count"], title=m["title"],
                 title_source=m["title_source"], substrate_block=m["substrate_block"],
                 substrate_status=m["substrate_status"], variant=m["variant"],
                 verb=m["verb"], weeks=m["weeks"], refresh_date=m["refresh_date"],
                 refresh_due=m["refresh_due"], micro_credential=m["micro_credential"],
                 status=m["status"])
        entry["modules"].append(d)
        les = cx.execute("SELECT week, topic, substrate_ref, work_due FROM lessons "
                         "WHERE module_id = ? ORDER BY week", (m["module_id"],)).fetchall()
        d["lessons"] = [dict(l) for l in les]
        rows.append(dict(fstem_id=c["fstem_id"], mit_code=c["code"], course_title=c["title"],
                         **{k: v for k, v in d.items() if k != "lessons"}))
    bundle.append(entry)

jp = os.path.join(a.out, f"BATCH_{a.track}.json")
cp = os.path.join(a.out, f"BATCH_{a.track}.csv")
json.dump(dict(track=a.track, courses=bundle), open(jp, "w", encoding="utf-8"), indent=1)
with open(cp, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

pend = sum(1 for r in rows if r["title"] == "TITLE_PENDING")
print(f"track        : {a.track}")
print(f"courses      : {len(bundle)}")
print(f"modules      : {len(rows)}   drafted skipped: {skipped}")
print(f"TITLE_PENDING: {pend}")
print(f"\nwrote {jp}\n      {cp}")
cx.close()
