#!/usr/bin/env python3
"""
FSTEM database migration, 22 August 2026.

Applies every correction settled this session to the official fstem.db:
  schema  : vertical provenance columns, substrate_status, modules, lessons, tracks
  data    : vertical_corrections, dedup splits, repointed folders, title fixes, AI spine

Idempotent. Safe to re-run. Always writes a timestamped backup first.

    python3 01_migrate_db.py --db out/fstem.db --dry-run
    python3 01_migrate_db.py --db out/fstem.db
"""
import argparse, csv, os, re, shutil, sqlite3, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = []


def say(msg):
    LOG.append(msg)
    print(msg)


# ---------------------------------------------------------------- schema
SCHEMA = [
    ("assets", "verticals_scored", "TEXT"),
    ("assets", "verticals_override", "TEXT"),
    ("assets", "vertical_rule_id", "TEXT"),
    ("assets", "vertical_conflict", "INTEGER DEFAULT 0"),
    ("assets", "substrate_status", "TEXT"),
    ("assets", "fstem_id", "TEXT"),
    ("assets", "authored_by", "TEXT DEFAULT 'MIT_OCW'"),
    ("verticals", "method", "TEXT"),
    ("verticals", "rule_id", "TEXT"),
]

TABLES = {
    "modules": """CREATE TABLE IF NOT EXISTS modules (
        module_id       TEXT PRIMARY KEY,
        parent_fstem_id TEXT NOT NULL,
        asset_id        TEXT,
        module_index    INTEGER NOT NULL,
        module_count    INTEGER NOT NULL,
        title           TEXT,
        weeks           INTEGER DEFAULT 3,
        rung            TEXT,
        verb            TEXT,
        vertical        TEXT,
        track_overlay   TEXT,
        variant         TEXT,
        substrate_course TEXT,
        substrate_term  TEXT,
        substrate_block TEXT,
        substrate_status TEXT,
        refresh_date    TEXT,
        refresh_due     TEXT,
        micro_credential TEXT,
        status          TEXT DEFAULT 'skeleton',
        title_source    TEXT
    )""",
    "lessons": """CREATE TABLE IF NOT EXISTS lessons (
        lesson_id   TEXT PRIMARY KEY,
        module_id   TEXT NOT NULL,
        week        INTEGER NOT NULL,
        topic       TEXT,
        substrate_ref TEXT,
        work_due    TEXT,
        status      TEXT DEFAULT 'skeleton'
    )""",
    "tracks": """CREATE TABLE IF NOT EXISTS tracks (
        track_id TEXT PRIMARY KEY,
        name     TEXT NOT NULL,
        degree   TEXT,
        rung     TEXT
    )""",
    "track_courses": """CREATE TABLE IF NOT EXISTS track_courses (
        track_id   TEXT NOT NULL,
        fstem_id   TEXT NOT NULL,
        seq        INTEGER,
        role       TEXT
    )""",
    "module_overlays": """CREATE TABLE IF NOT EXISTS module_overlays (
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
    )""",
    "migrations": """CREATE TABLE IF NOT EXISTS migrations (
        name TEXT PRIMARY KEY, applied_at TEXT
    )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mod_parent ON modules(parent_fstem_id)",
    "CREATE INDEX IF NOT EXISTS idx_mod_status ON modules(status)",
    "CREATE INDEX IF NOT EXISTS idx_les_module ON lessons(module_id)",
    "CREATE INDEX IF NOT EXISTS idx_assets_fstem ON assets(fstem_id)",
    "CREATE INDEX IF NOT EXISTS idx_tc_track ON track_courses(track_id)",
    "CREATE INDEX IF NOT EXISTS idx_ovl_track ON module_overlays(track_id)",
    "CREATE INDEX IF NOT EXISTS idx_ovl_parent ON module_overlays(parent_fstem_id)",
    "CREATE INDEX IF NOT EXISTS idx_ovl_module ON module_overlays(module_id)",
]

TRACKS = [
    ("m1", "AI & Data Science", "MS-AIDS", "masters"),
    ("m2", "Supply Chain", "MS-SCM", "masters"),
    ("m3", "Manufacturing", "MS-MFG", "masters"),
    ("m4", "Quantitative Finance", "MS-QF", "masters"),
    ("m5", "Economics & Policy", "MS-ECP", "masters"),
    ("m6", "Power Currency & Distributed Energy", "MS-PCDE", "masters"),
    ("m7", "Total Domain Conflict", "MS-TDC", "masters"),
]

# Phase 1 vertical substrate test. Only verticals whose SOURCE expired are 'dead'.
SUBSTRATE_BY_VERTICAL = {
    "computer_science": "partial",   # EECS theory holds; AI/ML half expired
    "mathematics": "live",
    "engineering": "live",
    "economics": "live",
    "business": "live",
    "finance": "live",
    "history": "live",
    "literature": "live",
    "philosophy": "live",
    "arts": "live",
    "politics": "live",
    "society_technology": "live",
    "general_core": "live",
}
# Course-level overrides. These beat the vertical default.
SUBSTRATE_BY_CODE = {
    '15.778':  'partial',   # carried, not in the pin block
    '15.760A': 'partial',   # carried, not in the pin block
    'ESD.273J': 'partial',   # carried, not in the pin block
    'ESD.260J': 'partial',   # carried, not in the pin block
    '15.762J': 'partial',   # carried, not in the pin block
    '15.763J': 'partial',   # carried, not in the pin block
    'ESD.70J': 'partial',   # carried, not in the pin block
    'ESD.71':  'partial',   # carried, not in the pin block
    'ESD.72':  'partial',   # carried, not in the pin block
    '11.304J': 'partial',   # carried, not in the pin block
    '11.943J': 'partial',   # carried, not in the pin block
    'IDS.720J': 'partial',   # carried, not in the pin block
    '1.212J':  'partial',   # carried, not in the pin block
    'IDS.505J': 'partial',   # carried, not in the pin block
    '22.812J': 'partial',   # carried, not in the pin block
    '15.023J': 'partial',   # carried, not in the pin block
    'ESD.123J': 'partial',   # carried, not in the pin block
    '1.253J':  'partial',   # carried, not in the pin block
    '6.034':   'dead',   # carried, not in the pin block
    '6.864':   'dead',   # carried, not in the pin block
    '6.867':   'dead',   # carried, not in the pin block
    '15.097':  'dead',   # carried, not in the pin block
    '6.036':   'dead',   # carried, not in the pin block
    'FSTEM.AI.SPINE': 'dead',   # carried, not in the pin block
    '6.7960':  'live',   # carried, not in the pin block
    '15.773':  'live',   # carried, not in the pin block
    '6.830':   'partial',   # carried, not in the pin block
    '6.858':   'partial',   # carried, not in the pin block
    '6.438':   'partial',   # carried, not in the pin block
    '6.0002':  'partial',   # carried, not in the pin block
    'HST.947': 'dead',   # carried, not in the pin block
    'MAS.963': 'dead',   # carried, not in the pin block
    'HST.953': 'live',   # carried, not in the pin block
    '1.264J':  'partial', # web services block dead, database block live; batch 014
    '16.852J': 'partial', # LAI assessment apparatus unmaintained; batch 014
    'ESD.290': 'partial', # architecture rebuilt, legal layer superseded; batch 014
    '2.854':   'live',    # queueing and Markov method; batch 015
    '2.852':   'live',    # decomposition proofs; batch 015
    '2.830J':  'live',    # SPC and DOE method; M4 diverges partial; batch 015
    '2.75':    'live',    # kinematics and elasticity; Fall 2001 vintage; batch 015
    '2.875':   'live',    # constraint and variation propagation; batch 016
    '15.066J': 'live',    # LP, IP, simulation, NLP theory; batch 016
    '15.783J': 'live',    # development process; M3 diverges partial; batch 016
    '15.980J': 'partial', # co-location and geographic layers superseded; batch 016
    '15.792J': 'dead',    # seminar content never reached OCW; M1 diverges partial; batch 017
    'ESD.33':  'partial', # INCOSE process layer and three of five cases superseded; batch 017
    'ESD.34':  'live',    # form to function mapping and decomposition; batch 017
    'ESD.36':  'partial', # EVM practice layer and dispersed-team layer superseded; batch 017
    '15.401':  'live',    # discounting and mean-variance method; M1 diverges partial; batch 018
    '15.402':  'live',    # MM theorems and valuation arithmetic; M1 diverges partial; batch 018
    '15.433':  'partial', # anomaly record, credit and active-management layers superseded; batch 018
    '15.450':  'live',    # Ito calculus, dynamic programming, estimation theory; batch 018
    '15.414':  'live',    # valuation and cost of capital method; M3 diverges partial; batch 019
    '15.511':  'partial', # recognition, lease and combination layers superseded; M1 diverges live; batch 019
    '15.997':  'partial', # trading ops, hedge accounting and governance superseded; batch 019
    '15.617':  'partial', # review standards, exemption catalogue and regulatory order superseded; batch 019
    '18.655':  'live',    # decision theory, sufficiency, estimation, asymptotics; batch 020
    '14.381':  'live',    # probability, estimation, likelihood, testing theory; batch 020
    '14.382':  'live',    # regression, GMM, orthogonalised estimation; M3 diverges partial; batch 020
    '14.454':  'partial', # sudden stop framing, run calibration and policy regime superseded; batch 020
    '15.S12':  'partial', # limits, policy posture and consortium layer superseded; M1 live, M4 dead; batch 021
    '15.483':  'partial', # conduct standard, pricing rules and platform layers superseded; M1 diverges live; batch 021
    '15.431':  'live',    # venture valuation, waterfall and fund economics; M4 diverges partial; batch 021
    '16.863J': 'live',    # STAMP causality, CAST and STPA; M4 diverges partial; batch 022
    '15.760B': 'live',    # process analysis, newsvendor and production control; M4 diverges partial; batch 022
    '14.121':  'live',    # preference, demand, production and choice theory; batch 023
    '14.122':  'live',    # game theoretic method, refinements taught as arguments; batch 023
    '14.126':  'live',    # structured game classes and type spaces; Spring 2024; batch 023
    '14.451':  'live',    # dynamic programming and growth derivations; M1 diverges partial; batch 023

    # backfilled from drafted courses, batch 027 audit.
    "1.203J":          "live",    # backfill 027
    "1.206J":          "live",    # backfill 027
    "1.224J":          "live",    # backfill 027
    "14.271":          "live",    # backfill 027
    "14.310X":         "partial", # backfill 027
    "14.452":          "live",    # backfill 027
    "14.770":          "partial", # backfill 027
    "14.771":          "partial", # backfill 027
    "15.057":          "live",    # backfill 027
    "15.060":          "live",    # backfill 027
    "15.071":          "live",    # backfill 027
    "15.082J":         "live",    # backfill 027
    "15.317":          "live",    # backfill 027
    "15.764":          "live",    # backfill 027
    "17.100J":         "live",    # backfill 027
    "18.409":          "live",    # backfill 027
    "18.657":          "live",    # backfill 027
    "21H.952J":        "live",    # backfill 027
    "22.312":          "live",    # backfill 027
    "6.436J":          "live",    # backfill 027
    "6.871":           "partial", # backfill 027
    "9.520":           "live",    # backfill 027
    "ESD.10":          "partial", # backfill 027
    "ESD.141":         "partial", # backfill 027
    "ESD.60":          "live",    # backfill 027
    "ESD.933":         "partial", # backfill 027
    "IDS.333":         "live",    # backfill 027
    "IDS.410J":        "partial", # backfill 027
    "MAS.S62":         "partial", # backfill 027
    "RES.10-002":      "live",    # backfill 027
    "RES.ENV-007":     "live",    # backfill 027
    "STS.340J":        "live",    # backfill 027
    "STS.460":         "partial", # backfill 027
    "STS.462":         "live",    # backfill 027

    # backfilled at corpus close, batch 029.
    "16.891J":     "partial", # backfill 029
    "16.892J":     "partial", # backfill 029
    "16.89J":      "partial", # backfill 029
    "17.408":      "partial", # backfill 029
    "ESD.68J":     "partial", # backfill 029
    "STS.436":     "live",    # backfill 029
    "STS.471J":    "partial", # backfill 029
}
# Age handling.
#
# A first attempt automatically demoted pre-2006 material in computer science,
# mathematics and engineering to 'dead'. The dry run rejected it: of 31 changes, most
# were wrong. It wanted to kill 6.042J Mathematics for Computer Science, 6.046J Design
# and Analysis of Algorithms, 2.75 Precision Machine Design, 18.305 Advanced Analytic
# Methods and 1.225J Transportation Flow Systems, none of which have expired.
#
# The lesson: age plus vertical does not predict expiry. What predicts it is whether a
# course teaches mathematics or practice, and no column in this database records that.
# Engineering fundamentals and pure mathematics are the most durable material here.
#
# So the age test is ADVISORY. It lists candidates for a human to judge and changes
# nothing. Decisions land in SUBSTRATE_BY_CODE, where they are visible and reversible.
AGE_REVIEW_BEFORE = 2006

# The freshness rule stays automatic. A substrate from 2022 or later that is 'partial'
# only because of a vertical default is current, and the blast radius is small.
AGE_LIVE_FROM = 2022

# Verticals where a pre-2006 substrate is worth a second look. Humanities and pure
# mathematics are absent on purpose: a 2002 Shakespeare course has not expired, and
# listing it buries the courses that might have.
AGE_REVIEW_VERTICALS = {"computer_science", "engineering", "business", "finance"}


DATA = HERE  # overridden by --data


def load_csv(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        p = os.path.join(HERE, name)
    if not os.path.exists(p):
        say(f"  MISSING {name}, skipping the steps that need it")
        return None
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def already(cx, name):
    return cx.execute("SELECT 1 FROM migrations WHERE name=?", (name,)).fetchone() is not None


def mark(cx, name):
    cx.execute("INSERT OR REPLACE INTO migrations VALUES (?,?)",
               (name, datetime.datetime.now().isoformat(timespec="seconds")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--age-review", action="store_true",
                    help="list pre-2006 assets in fast-moving verticals for manual judgement")
    ap.add_argument("--data", default=HERE,
                    help="directory holding the two CSVs (default: this folder; "
                         "point at ../overrides once they live there)")
    a = ap.parse_args()
    global DATA
    DATA = a.data

    if not os.path.exists(a.db):
        sys.exit(f"database not found: {a.db}")

    if not a.dry_run:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = f"{a.db}.bak-{stamp}"
        shutil.copy2(a.db, bak)
        say(f"backup written: {bak}")

    cx = sqlite3.connect(a.db)
    cx.execute(TABLES["migrations"])

    # ---------------- schema
    say("\n== schema ==")
    for tbl, col, typ in SCHEMA:
        cols = {r[1] for r in cx.execute(f"PRAGMA table_info({tbl})")}
        if col in cols:
            say(f"  ok    {tbl}.{col} exists")
        else:
            cx.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
            say(f"  ADD   {tbl}.{col} {typ}")
    for name, ddl in TABLES.items():
        cx.execute(ddl)
    say(f"  ok    tables: {', '.join(TABLES)}")
    for ix in INDEXES:
        cx.execute(ix)
    say(f"  ok    {len(INDEXES)} indexes")

    # ---------------- preserve raw scorer output before any override
    say("\n== preserve verticals_scored ==")
    if already(cx, "verticals_scored_snapshot"):
        say("  ok    already snapshotted, not overwriting")
    else:
        n = 0
        for aid, in cx.execute("SELECT asset_id FROM assets").fetchall():
            vs = [r[0] for r in cx.execute(
                "SELECT vertical FROM verticals WHERE asset_id=? ORDER BY score DESC", (aid,))]
            cx.execute("UPDATE assets SET verticals_scored=? WHERE asset_id=?", (",".join(vs), aid))
            n += 1
        cx.execute("UPDATE verticals SET method='lexicon' WHERE method IS NULL AND pillars=''")
        cx.execute("UPDATE verticals SET method='pillars' WHERE method IS NULL AND pillars!=''")
        mark(cx, "verticals_scored_snapshot")
        say(f"  SNAP  {n} assets, verticals_scored frozen; verticals.method set")

    # ---------------- rename map: fstem_id, title fixes, repoints, new rows
    say("\n== rename map ==")
    rmap = load_csv("fstem_rename_map_CORRECTED.csv")
    if rmap:
        added = linked = titled = 0
        for r in rmap:
            code = (r.get("variant_code") or r.get("mit_code") or "").strip()
            fid = r["fstem_id"].strip()
            # A split row carries a variant_code that differs from its mit_code. It is a
            # DISTINCT subject recovered from a rotating number, so it becomes its own asset
            # rather than overwriting the sibling that shares the MIT number.
            is_split = code != r["mit_code"].strip()
            row = None if is_split else cx.execute(
                "SELECT asset_id,title FROM assets WHERE code=?", (r["mit_code"].strip(),)).fetchone()
            if is_split and cx.execute("SELECT 1 FROM assets WHERE code=?", (code,)).fetchone():
                cx.execute("UPDATE assets SET fstem_id=? WHERE code=?", (fid, code))
                linked += 1
                continue
            if row is None:
                aid = f"fstem:{fid}"
                cx.execute(
                    "INSERT OR IGNORE INTO assets (asset_id,code,code_norm,title,provider,source_type,"
                    "rung,verb,held,fstem_id,authored_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, code, code, r["mit_title"], "FSTEM", "fstem_authored", r["rung"],
                     "apply" if r["rung"] == "masters" else "reproduce", 1, fid,
                     "FSTEM" if r.get("text_repo") == "FSTEM" else "MIT_OCW"))
                added += 1
                continue
            aid, cur_title = row
            cx.execute("UPDATE assets SET fstem_id=? WHERE asset_id=?", (fid, aid))
            linked += 1
            newt = (r.get("mit_title") or "").strip()
            if newt and newt != (cur_title or "").strip() and "corrected from source" in (r.get("repair_notes") or ""):
                cx.execute("UPDATE assets SET title=? WHERE asset_id=?", (newt, aid))
                say(f"  TITLE {r['mit_code']}: {cur_title!r} -> {newt!r}")
                titled += 1
        say(f"  ok    {linked} assets linked to fstem_id, {added} new rows, {titled} titles corrected")

    # ---------------- vertical corrections
    say("\n== vertical corrections ==")
    vc = load_csv("vertical_corrections.csv")
    if vc:
        applied = confirmed = held = 0
        for r in vc:
            act = r["action"].strip().lower()
            row = cx.execute("SELECT asset_id FROM assets WHERE code=?", (r["mit_code"].strip(),)).fetchone()
            if not row:
                continue
            aid = row[0]
            if act == "correct":
                cx.execute("UPDATE assets SET verticals_override=?, vertical_rule_id=?, vertical_conflict=1 "
                           "WHERE asset_id=?", (r["vertical_resolved"], r["rule_id"], aid))
                applied += 1
            elif act == "confirm":
                cx.execute("UPDATE assets SET vertical_rule_id=? WHERE asset_id=?", (r["rule_id"], aid))
                confirmed += 1
            else:
                cx.execute("UPDATE assets SET vertical_rule_id=?, vertical_conflict=1 WHERE asset_id=?",
                           (r["rule_id"], aid))
                held += 1
        say(f"  ok    {applied} correct applied, {confirmed} confirmed, {held} review held unapplied")
        say("  NOTE  review rows set vertical_conflict=1 and are NOT auto-applied. A person signs them.")

    # ---------------- substrate status
    say("\n== substrate status ==")
    n = 0
    aged, review = [], []
    for aid, code, vs, yr in cx.execute(
            "SELECT asset_id, code, COALESCE(verticals_override, verticals_scored), year "
            "FROM assets").fetchall():
        prim = (vs or "").split(",")[0].strip()
        explicit = SUBSTRATE_BY_CODE.get(code)
        st = explicit or SUBSTRATE_BY_VERTICAL.get(prim, "live")
        try:
            year = int(str(yr)[:4])
        except (TypeError, ValueError):
            year = None
        # Freshness rule, automatic. An explicit per-code decision wins over it.
        if not explicit and year and year >= AGE_LIVE_FROM and st == "partial":
            aged.append((code, year, prim, "partial", "live"))
            st = "live"
        # Age test, ADVISORY ONLY. Changes nothing.
        if not explicit and year and year < AGE_REVIEW_BEFORE and st != "dead":
            review.append((code, year, prim, st))
        cx.execute("UPDATE assets SET substrate_status=? WHERE asset_id=?", (st, aid))
        n += 1
    dist = dict(cx.execute("SELECT substrate_status, count(*) FROM assets GROUP BY 1"))
    say(f"  ok    {n} assets classified: {dist}")
    if aged:
        say(f"  FRESH {len(aged)} changed by the freshness rule:")
        for code, year, prim, was, now in sorted(aged):
            say(f"          {code:<16}{year}  {prim:<20}{was} -> {now}")
    if review:
        # Scoped to verticals whose content is technology or practice. Literature,
        # history, philosophy, arts and pure mathematics are excluded because their
        # material does not expire from age alone, and listing them buries the rest.
        cand = [r for r in review if r[2] in AGE_REVIEW_VERTICALS]
        say(f"\n  REVIEW {len(review)} assets predate {AGE_REVIEW_BEFORE}, "
            f"{len(cand)} of them in a vertical where age may matter.")
        say("         Nothing was changed. The drafting pass judges each course on its")
        say("         content, which is the real mechanism. This list only matters for")
        say(f"         courses that never get drafted. Run with --age-review to see it.")
        if a.age_review:
            for code, year, prim, st in sorted(cand, key=lambda r: (r[2], r[0])):
                say(f"          {code:<16}{year}  {prim:<20}currently {st}")
            say("  NOTE  to change one, add it to SUBSTRATE_BY_CODE and re-run.")

    # ---------------- tracks
    say("\n== tracks ==")
    for t in TRACKS:
        cx.execute("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?)", t)
    say(f"  ok    {len(TRACKS)} masters tracks seeded (course mapping filled by 02_build_modules.py)")

    mark(cx, "migration_2026_08_22")

    if a.dry_run:
        cx.rollback()
        say("\nDRY RUN, nothing written.")
    else:
        cx.commit()
        say("\ncommitted.")
        with open(os.path.join(HERE, "migration_log.txt"), "a") as fh:
            fh.write(f"\n--- {datetime.datetime.now().isoformat(timespec='seconds')} ---\n")
            fh.write("\n".join(LOG) + "\n")
    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
