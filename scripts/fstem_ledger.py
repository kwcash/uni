#!/usr/bin/env python3
"""
fstem_ledger.py  ·  the artifacts ledger

Every generated artifact has a row here before it exists and after. Nothing is generated without a row.

  python3 fstem_ledger.py --db ../out/fstem.db --seed           # create the table, plan one row per artifact
  python3 fstem_ledger.py --db ../out/fstem.db --plan           # counts by track and kind, and by status
  python3 fstem_ledger.py --db ../out/fstem.db --plan --track m7

The plan (edit ARTIFACT_PLAN to change it; --seed is idempotent and adds only missing rows)
  lesson grain   lecture_outline · problem_set · reading_guide · seminar_prompt      one per lesson row
  module grain   exam_bank · study_guide                                             one per drafted module and overlay
  course grain   course_guide · course_exam                                          one per drafted course
  track grain    program_guide · capstone                                            one per track

Lifecycle for `status`
  planned -> generated -> critiqued -> revised -> reviewed -> accepted | rejected
A rejected artifact gets a new row with attempt+1; the old row stays.
"""
import argparse, sqlite3, datetime
from collections import defaultdict

NOW = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

ARTIFACT_PLAN = {
    "lesson": ["lecture_outline", "problem_set", "reading_guide", "seminar_prompt"],
    "module": ["exam_bank", "study_guide"],
    "course": ["course_guide", "course_exam"],
    "track":  ["program_guide", "capstone"],
}

DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     TEXT PRIMARY KEY,      -- {kind}:{key}:{attempt}
    kind            TEXT NOT NULL,
    grain           TEXT NOT NULL,         -- lesson | module | course | track
    key             TEXT NOT NULL,         -- lesson_id | module_id | fstem_id | track_id
    fstem_id        TEXT,                  -- owning course, for planning and review by course
    track_ids       TEXT,                  -- comma list of tracks the owning course sits in
    attempt         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'planned',
    model           TEXT,
    prompt_hash     TEXT,                  -- sha256 of cached prefix + suffix actually sent
    output_path     TEXT,
    output_hash     TEXT,
    critic_model    TEXT,
    critic_verdict  TEXT,                  -- pass | revise | regenerate
    critic_defects  TEXT,                  -- JSON list
    review_status   TEXT,                  -- accept | accept_with_edits | reject
    reviewer        TEXT,
    review_minutes  INTEGER,               -- the number the pipeline exists to protect
    reviewed_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_artifacts_status ON artifacts(status);
CREATE INDEX IF NOT EXISTS ix_artifacts_course ON artifacts(fstem_id);
CREATE INDEX IF NOT EXISTS ix_artifacts_kind ON artifacts(kind);
"""


def course_of(key, grain):
    if grain in ("lesson", "module"):
        # FSTEM-CS-R4-006-M1-W2 -> FSTEM-CS-R4-006 ; overlay FSTEM-AI-SPINE-001-M8-m4-W1 -> FSTEM-AI-SPINE-001
        return key.split("-M")[0]
    if grain == "course":
        return key
    return None


def seed(con):
    con.executescript(DDL)
    tracks_of = defaultdict(list)
    for fid, tid in con.execute("SELECT fstem_id, track_id FROM track_courses ORDER BY 1, 2"):
        tracks_of[fid].append(tid)
    for oid, tid in con.execute("SELECT overlay_id, track_id FROM module_overlays"):
        tracks_of[oid] = [tid]

    keys = {"lesson": [], "module": [], "course": [], "track": []}
    keys["lesson"] = [r[0] for r in con.execute("""
        SELECT l.lesson_id FROM lessons l
        WHERE l.status='draft' AND (l.module_id IN (SELECT module_id FROM modules WHERE status='draft')
                                    OR l.module_id IN (SELECT overlay_id FROM module_overlays)) ORDER BY 1""")]
    keys["module"] = [r[0] for r in con.execute("SELECT module_id FROM modules WHERE status='draft' ORDER BY 1")] \
                   + [r[0] for r in con.execute("SELECT overlay_id FROM module_overlays ORDER BY 1")]
    keys["course"] = [r[0] for r in con.execute("SELECT DISTINCT parent_fstem_id FROM modules WHERE status='draft' ORDER BY 1")]
    keys["track"] = [r[0] for r in con.execute("SELECT track_id FROM tracks ORDER BY 1")]

    added = 0
    for grain, kinds in ARTIFACT_PLAN.items():
        for key in keys[grain]:
            fid = course_of(key, grain)
            if grain in ("module",) and key in tracks_of:      # overlay
                tids = tracks_of[key]
            elif grain == "lesson" and key.split("-W")[0] in tracks_of:
                tids = tracks_of[key.split("-W")[0]]
            elif grain == "track":
                tids = [key]
            else:
                tids = tracks_of.get(fid, [])
            for kind in kinds:
                aid = f"{kind}:{key}:1"
                cur = con.execute("INSERT OR IGNORE INTO artifacts(artifact_id, kind, grain, key, fstem_id, track_ids, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                  (aid, kind, grain, key, fid, ",".join(tids), NOW(), NOW()))
                added += cur.rowcount
    con.commit()
    print(f"seeded {added} planned rows; ledger holds {con.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0]}")


def plan(con, track=None):
    where = f"WHERE track_ids LIKE '%{track}%'" if track else ""
    print(f"\nartifacts by grain and kind{' in ' + track if track else ''}")
    for g, k, n in con.execute(f"SELECT grain, kind, COUNT(*) FROM artifacts {where} GROUP BY 1,2 ORDER BY 1,2"):
        print(f"  {g:<7} {k:<16} {n:>6}")
    print("\nby status")
    for s, n in con.execute(f"SELECT status, COUNT(*) FROM artifacts {where} GROUP BY 1"):
        print(f"  {s:<12} {n:>6}")
    print("\nby track (rows counted once per track they serve)")
    for (tid,) in con.execute("SELECT track_id FROM tracks ORDER BY 1"):
        n = con.execute("SELECT COUNT(*) FROM artifacts WHERE track_ids LIKE ?", (f"%{tid}%",)).fetchone()[0]
        c = con.execute("SELECT COUNT(DISTINCT fstem_id) FROM artifacts WHERE track_ids LIKE ? AND fstem_id IS NOT NULL", (f"%{tid}%",)).fetchone()[0]
        print(f"  {tid}: {n:>6} artifacts across {c} courses")
    rv = con.execute("SELECT COUNT(*), COALESCE(SUM(review_minutes),0) FROM artifacts WHERE reviewed_at IS NOT NULL").fetchone()
    if rv[0]:
        print(f"\nreviewed: {rv[0]} artifacts, {rv[1]} minutes, {rv[1]/rv[0]:.1f} min each")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--track", default=None)
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    if a.seed:
        seed(con)
    if a.plan or not a.seed:
        if not con.execute("SELECT name FROM sqlite_master WHERE name='artifacts'").fetchone():
            print("no artifacts table yet; run --seed"); return
        plan(con, a.track)


if __name__ == "__main__":
    main()
