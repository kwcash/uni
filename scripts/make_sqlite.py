#!/usr/bin/env python3
"""
make_sqlite.py — the repository as a queryable database.

The JSONL is the record of truth and stays that way. This is the same data in a
shape you can ask questions of, which the JSONL cannot answer without a script
per question.

Six tables:

    assets          one row per teaching asset, the flat fields
    verticals       asset to vertical, with score. Many rows per asset.
    threads         asset to research thread
    bibliography    asset to cited work
    flags           asset to extraction flag
    prerequisites   asset to the course code it depends on

Rebuilt from scratch on every run, so it never drifts from the JSONL. Delete it
and re-run and you lose nothing.

Usage
-----
    python3 scripts/make_sqlite.py --in out/fstem_repository_scored.jsonl \\
        --out out/fstem.db

    sqlite3 out/fstem.db "SELECT rung, COUNT(*) FROM assets GROUP BY rung;"
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import read_jsonl, log  # noqa: E402

SCHEMA = """
DROP TABLE IF EXISTS assets;
DROP TABLE IF EXISTS verticals;
DROP TABLE IF EXISTS threads;
DROP TABLE IF EXISTS bibliography;
DROP TABLE IF EXISTS flags;
DROP TABLE IF EXISTS prerequisites;

CREATE TABLE assets (
    asset_id        TEXT PRIMARY KEY,
    code            TEXT NOT NULL,
    title           TEXT,
    provider        TEXT,
    source_type     TEXT,
    rung            TEXT,
    grade_min       INTEGER,
    grade_max       INTEGER,
    verb            TEXT,
    level_stated    TEXT,
    level_conflict  INTEGER,
    term            TEXT,
    year            INTEGER,
    url             TEXT,
    description     TEXT,
    held            INTEGER,
    resource_count  INTEGER,
    n_bibliography  INTEGER,
    n_schedule      INTEGER,
    n_verticals     INTEGER,
    n_threads       INTEGER,
    cie_qualification TEXT,
    cie_first_exam  INTEGER,
    cie_last_exam   INTEGER
);

CREATE TABLE verticals (
    asset_id  TEXT NOT NULL,
    vertical  TEXT NOT NULL,
    score     REAL,
    pillars   TEXT
);

CREATE TABLE threads      (asset_id TEXT NOT NULL, thread TEXT NOT NULL);
CREATE TABLE bibliography (asset_id TEXT NOT NULL, author TEXT, title TEXT, year INTEGER, raw TEXT);
CREATE TABLE flags        (asset_id TEXT NOT NULL, flag TEXT NOT NULL);
CREATE TABLE prerequisites(asset_id TEXT NOT NULL, requires TEXT NOT NULL);

CREATE INDEX idx_assets_rung     ON assets(rung);
CREATE INDEX idx_assets_code     ON assets(code);
CREATE INDEX idx_assets_source   ON assets(source_type);
CREATE INDEX idx_vert_asset      ON verticals(asset_id);
CREATE INDEX idx_vert_name       ON verticals(vertical);
CREATE INDEX idx_thread_asset    ON threads(asset_id);
CREATE INDEX idx_thread_name     ON threads(thread);
CREATE INDEX idx_flag_name       ON flags(flag);
CREATE INDEX idx_bib_asset       ON bibliography(asset_id);
"""

# Questions the JSONL cannot answer without writing a script each time.
SAMPLES = [
    ("Assets per rung",
     "SELECT rung, COUNT(*) AS n FROM assets GROUP BY rung ORDER BY n DESC;"),
    ("Courses claimed by three or more verticals",
     "SELECT a.code, a.title, COUNT(*) AS n FROM assets a JOIN verticals v USING(asset_id) "
     "GROUP BY a.asset_id HAVING n >= 3 ORDER BY n DESC LIMIT 15;"),
    ("Every asset carrying the energy thread, in teaching order",
     "SELECT a.rung, a.code, a.title FROM assets a JOIN threads t USING(asset_id) "
     "WHERE t.thread = 'T-JOULE' ORDER BY a.grade_min, a.code;"),
    ("Verticals by rung, the coverage matrix",
     "SELECT v.vertical, a.rung, COUNT(*) AS n FROM verticals v JOIN assets a USING(asset_id) "
     "GROUP BY v.vertical, a.rung ORDER BY v.vertical, a.rung;"),
    ("Most cited authors across the corpus",
     "SELECT author, COUNT(*) AS n FROM bibliography WHERE author IS NOT NULL "
     "GROUP BY author ORDER BY n DESC LIMIT 20;"),
    ("Open work, by flag",
     "SELECT flag, COUNT(*) AS n FROM flags GROUP BY flag ORDER BY n DESC;"),
    ("Courses with no downstream course requiring them",
     "SELECT a.code, a.title FROM assets a WHERE a.rung='undergraduate' "
     "AND a.code NOT IN (SELECT requires FROM prerequisites) LIMIT 15;"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a queryable sqlite database from the repository.")
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--samples", action="store_true", help="Run the sample queries and print results.")
    args = ap.parse_args()

    recs = read_jsonl(args.infile)
    if not recs:
        log(f"no records in {args.infile}")
        return 1

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if os.path.exists(out):
        os.remove(out)

    con = sqlite3.connect(out)
    con.executescript(SCHEMA)

    a_rows, v_rows, t_rows, b_rows, f_rows, p_rows = [], [], [], [], [], []
    for r in recs:
        aid = r["asset_id"]
        ocw = r.get("ocw") or {}
        cie = r.get("cie") or {}
        band = r.get("grade_band") or [None, None]

        a_rows.append((
            aid, r["code"], r.get("title"), r.get("provider"), r.get("source_type"),
            r.get("rung"), band[0], band[1], r.get("verb"), r.get("level_stated"),
            1 if r.get("level_conflict") else 0, r.get("term"), r.get("year"), r.get("url"),
            (r.get("description") or "")[:4000],
            1 if ocw.get("held") else 0, ocw.get("resource_count"),
            len(ocw.get("bibliography") or []), len(ocw.get("schedule") or []),
            len(r.get("verticals") or []), len(r.get("threads") or []),
            cie.get("qualification"), cie.get("first_exam_year"), cie.get("last_exam_year"),
        ))
        for v in r.get("verticals") or []:
            v_rows.append((aid, v["vertical"], v.get("score"), ",".join(v.get("pillars_hit") or [])))
        for t in r.get("threads") or []:
            t_rows.append((aid, t))
        for b in ocw.get("bibliography") or []:
            b_rows.append((aid, b.get("author"), b.get("title"), b.get("year"), (b.get("raw") or "")[:600]))
        for f in r.get("extraction_flags") or []:
            f_rows.append((aid, f))
        for pre in r.get("prerequisites") or []:
            p_rows.append((aid, pre))

    ncols = len(a_rows[0])
    con.executemany("INSERT OR REPLACE INTO assets VALUES (" + ",".join("?" * ncols) + ")", a_rows)
    con.executemany("INSERT INTO verticals VALUES (?,?,?,?)", v_rows)
    con.executemany("INSERT INTO threads VALUES (?,?)", t_rows)
    con.executemany("INSERT INTO bibliography VALUES (?,?,?,?,?)", b_rows)
    con.executemany("INSERT INTO flags VALUES (?,?)", f_rows)
    con.executemany("INSERT INTO prerequisites VALUES (?,?)", p_rows)
    con.commit()

    log(f"wrote {out}")
    log(f"  assets        {len(a_rows):>7}")
    log(f"  verticals     {len(v_rows):>7}")
    log(f"  threads       {len(t_rows):>7}")
    log(f"  bibliography  {len(b_rows):>7}")
    log(f"  flags         {len(f_rows):>7}")
    log(f"  prerequisites {len(p_rows):>7}")

    if args.samples:
        for name, sql in SAMPLES:
            log("")
            log(f"-- {name}")
            log(f"   {sql}")
            try:
                for row in con.execute(sql).fetchall()[:12]:
                    log("     " + " | ".join("" if c is None else str(c)[:52] for c in row))
            except sqlite3.Error as exc:
                log(f"     query failed: {exc}")

    con.close()
    log("")
    log("Query it:")
    log(f'  sqlite3 {out} "SELECT rung, COUNT(*) FROM assets GROUP BY rung;"')
    log("  python3 scripts/make_sqlite.py --in <jsonl> --out <db> --samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
