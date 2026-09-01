#!/usr/bin/env python3
"""
Load track_courses.csv into fstem.db. Idempotent.

Rows marked keep=N are dropped. Sequence renumbers 1..N per track after the drop.
Rows whose fstem_id does not resolve to a real asset are reported and skipped.

    python3 05_load_tracks.py --db ../out/fstem.db --csv ../track_courses.csv --dry-run
    python3 05_load_tracks.py --db ../out/fstem.db --csv ../track_courses.csv
"""
import argparse, csv, collections, sqlite3, sys

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--csv", required=True)
ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()

cx = sqlite3.connect(a.db)
known = {r[0] for r in cx.execute(
    "SELECT fstem_id FROM assets WHERE fstem_id IS NOT NULL AND fstem_id != ''")}

by_track = collections.defaultdict(list)
dropped = bad = 0
for r in csv.DictReader(open(a.csv, encoding="utf-8-sig")):
    if (r.get("keep") or "Y").strip().upper() != "Y":
        dropped += 1
        continue
    fid = r["fstem_id"].strip()
    if fid not in known:
        print(f"  UNKNOWN {r['track_id']} {fid}, skipped")
        bad += 1
        continue
    by_track[r["track_id"].strip()].append((fid, (r.get("role") or "track").strip()))

rows = []
for tid in sorted(by_track):
    seen = set()
    seq = 0
    for fid, role in by_track[tid]:
        if fid in seen:
            print(f"  DUPE    {tid} {fid}, kept once")
            continue
        seen.add(fid)
        seq += 1
        rows.append((tid, fid, seq, role))

print(f"\ntracks   : {len(by_track)}")
for tid in sorted(by_track):
    n = sum(1 for r in rows if r[0] == tid)
    c = sum(1 for r in rows if r[0] == tid and r[3] == "core")
    print(f"  {tid}  {n:>2} courses  ({c} core, {n-c} track)")
print(f"\nrows     : {len(rows)}   dropped keep=N: {dropped}   unknown ids: {bad}")

if a.dry_run:
    print("\nDRY RUN, nothing written.")
    sys.exit(0)

cx.execute("DELETE FROM track_courses")
cx.executemany("INSERT INTO track_courses (track_id, fstem_id, seq, role) VALUES (?,?,?,?)", rows)

# stamp the overlay onto every module in a track course
cx.execute("UPDATE modules SET track_overlay = ''")
for tid, fid, seq, role in rows:
    cx.execute("UPDATE modules SET track_overlay = "
               "CASE WHEN track_overlay = '' THEN ? ELSE track_overlay || ',' || ? END "
               "WHERE parent_fstem_id = ?", (tid, tid, fid))
cx.commit()

n = cx.execute("SELECT COUNT(*) FROM modules WHERE track_overlay != ''").fetchone()[0]
print(f"\ncommitted. {len(rows)} track_courses rows, {n} modules carry a track overlay.")
cx.close()
