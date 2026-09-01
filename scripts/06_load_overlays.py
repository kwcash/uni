#!/usr/bin/env python3
"""
FSTEM overlay loader, 22 August 2026.

A track overlay is a module that belongs to ONE track rather than to every student
taking the parent course. Overlays cannot live in `modules`, because that table is
keyed on module_id and seven overlays sharing a module index would collide. They
live in `module_overlays`, keyed on the pair (module_id, track_id).

The script also flips drafted modules out of `skeleton` status, so a later
02_build_modules.py rebuild leaves finished prose alone.

    python3 06_load_overlays.py --db ../out/fstem.db --dir modules/ --dry-run
    python3 06_load_overlays.py --db ../out/fstem.db --dir modules/
"""
import argparse, glob, os, re, sqlite3, sys

TABLE = """CREATE TABLE IF NOT EXISTS module_overlays (
    overlay_id       TEXT PRIMARY KEY,
    module_id        TEXT NOT NULL,
    parent_fstem_id  TEXT NOT NULL,
    track_id         TEXT NOT NULL,
    track_name       TEXT,
    module_index     INTEGER,
    module_count     INTEGER,
    title            TEXT,
    variant          TEXT,
    substrate_course TEXT,
    substrate_status TEXT,
    refresh_date     TEXT,
    refresh_due      TEXT,
    micro_credential TEXT,
    status           TEXT DEFAULT 'draft',
    source_path      TEXT,
    UNIQUE (module_id, track_id)
)"""

OVERLAY_ID = re.compile(r'-M\d+-(m[1-7])$')

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ovl_track  ON module_overlays(track_id)",
    "CREATE INDEX IF NOT EXISTS idx_ovl_parent ON module_overlays(parent_fstem_id)",
    "CREATE INDEX IF NOT EXISTS idx_ovl_module ON module_overlays(module_id)",
]


def parse(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    h = {}
    for m in re.finditer(r'^\*\*(.+?):\*\*\s*(.*)$', txt[:4000], re.M):
        h[m.group(1).strip()] = m.group(2).strip()
    m = re.search(r'^\*\*Module status:\s*(.*?)\*\*', txt[:4000], re.M)
    if m:
        h["Module status"] = m.group(1).strip()
    t = re.search(r'^#\s*(\S+)\s*[—-]\s*(.+)$', txt, re.M)
    h["_id"] = t.group(1).strip() if t else os.path.basename(path)[:-3]
    h["_title"] = t.group(2).strip() if t else ""
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dir", default="modules", help="directory holding drafted module .md files")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.dir, "*.md")))
    if not files:
        sys.exit(f"no .md files in {a.dir}")

    cx = sqlite3.connect(a.db)
    cx.execute(TABLE)
    for ix in INDEXES:
        cx.execute(ix)

    known_tracks = {r[0] for r in cx.execute("SELECT track_id FROM tracks")}
    rows, drafted, skipped = [], [], 0

    for p in files:
        h = parse(p)
        if re.match(r'(?i)^\s*SKELETON', h.get("Module status", "")):
            skipped += 1
            continue

        oid = h["_id"]
        par = re.match(r'\s*(FSTEM-[A-Z0-9-]+?)\s*,', h.get("Parent", ""))
        parent = par.group(1) if par else None
        mm = re.search(r'M(\d+)\s+of\s+(\d+)', h.get("Module", ""))
        idx, cnt = (int(mm.group(1)), int(mm.group(2))) if mm else (None, None)

        # The drafted document is the source of truth about itself. A module may
        # carry a substrate status, a variant and a refresh window that differ from
        # its course, and the corpus relies on that divergence being recorded rather
        # than silently inheriting the course-level value.
        sub = h.get("Substrate", "")
        code = re.search(r'MIT OCW\s+([^\s,]+)', sub)
        st = re.search(r'status\s*\*\*(live|partial|dead)\*\*', sub)
        dates = re.findall(r'(\d{4}-\d{2}-\d{2})', h.get("Currency refreshed", ""))
        variant = h.get("Variant", "")

        # Overlay identity comes from the module id, never from the header text.
        # A track course's modules legitimately name the track they serve in the
        # "Track overlay" header, and that does NOT make them overlays. Only an id
        # ending -M<n>-m<t> is a track-specific overlay module.
        ov = h.get("Track overlay", "")
        tm = re.match(r'\s*(m[1-7])\b\s*(.*)', ov)
        if not (tm and OVERLAY_ID.search(oid)):
            # a shared or track-course module. Record its draft status and its own
            # status, variant and refresh window, which may differ from the course.
            if idx and parent:
                drafted.append(dict(
                    module_id=f"{parent}-M{idx}",
                    substrate_status=st.group(1) if st else None,
                    variant=variant or None,
                    refresh_date=dates[0] if dates else None,
                    refresh_due=dates[1] if len(dates) > 1 else None))
            continue

        tid, tname = tm.group(1), tm.group(2).strip()
        if known_tracks and tid not in known_tracks:
            print(f"  UNKNOWN track {tid} in {os.path.basename(p)}, skipped")
            continue

        mid = re.sub(r'-(m[1-7])$', '', oid)

        rows.append((oid, mid, parent, tid, tname, idx, cnt, h["_title"],
                     variant, code.group(1) if code else "",
                     st.group(1) if st else "", dates[0] if dates else "",
                     dates[1] if len(dates) > 1 else "",
                     h.get("Micro-credential", ""), "draft", os.path.abspath(p)))

    print(f"files scanned   : {len(files)}")
    print(f"skeletons skipped: {skipped}")
    print(f"shared modules   : {len(drafted)} marked drafted")
    print(f"overlays         : {len(rows)}")
    for r in rows:
        print(f"  {r[3]}  {r[0]:<32} {r[7][:44]}")

    missing = sorted({r[2] for r in rows} - {x[0] for x in cx.execute(
        "SELECT fstem_id FROM assets WHERE fstem_id IS NOT NULL")})
    if missing:
        print("\n  WARN unresolved parents:", ", ".join(missing))

    if a.dry_run:
        print("\nDRY RUN, nothing written.")
        return 0

    # Repair: earlier runs recorded track-course modules as overlays because the
    # header names the track they serve. Remove any row whose id is not a real
    # overlay id. Reports what it removed rather than doing it silently.
    bad = [r[0] for r in cx.execute("SELECT overlay_id FROM module_overlays")
           if not OVERLAY_ID.search(r[0])]
    if bad:
        cx.executemany("DELETE FROM module_overlays WHERE overlay_id=?", [(b,) for b in bad])
        print(f"\nREPAIR  removed {len(bad)} rows wrongly recorded as overlays")
        for b in bad[:5]:
            print(f"          {b}")
        if len(bad) > 5:
            print(f"          and {len(bad) - 5} more")

    cx.executemany("INSERT OR REPLACE INTO module_overlays VALUES (" + ",".join("?" * 16) + ")", rows)
    n = diverged = 0
    for d in drafted:
        mid = d["module_id"]
        n += cx.execute("UPDATE modules SET status='draft' WHERE module_id=? AND status='skeleton'",
                        (mid,)).rowcount
        # Write the module's own status, variant and window back over the generator's
        # course-level guess. Report where the module diverges from its course.
        cur = cx.execute("SELECT m.substrate_status, a.substrate_status FROM modules m "
                         "LEFT JOIN assets a ON a.asset_id = m.asset_id "
                         "WHERE m.module_id=?", (mid,)).fetchone()
        if d["substrate_status"] and cur and d["substrate_status"] != (cur[1] or cur[0]):
            print(f"  DIVERGE {mid:<32} course {cur[1] or cur[0]:<8} module {d['substrate_status']}")
            diverged += 1
        sets, vals = [], []
        for col in ("substrate_status", "variant", "refresh_date", "refresh_due"):
            if d[col]:
                sets.append(f"{col}=?"); vals.append(d[col])
        if sets:
            vals.append(mid)
            cx.execute(f"UPDATE modules SET {','.join(sets)} WHERE module_id=?", vals)
    cx.commit()
    print(f"\ncommitted. {len(rows)} overlays, {n} shared modules moved from skeleton to draft.")
    print(f"           {len(drafted)} modules had status, variant and refresh window written "
          f"from their own header, {diverged} diverging from the course.")
    print("A later 02_build_modules.py rebuild now leaves drafted prose alone.")
    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
