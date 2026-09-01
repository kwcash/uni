#!/usr/bin/env python3
"""
fstem_repair.py  ·  repairs and invariant checks for fstem.db

Dry-run by default. Nothing is written without --apply.
Every write is recorded in a repair_log table so it can be reversed.

Usage
  python3 fstem_repair.py --db out/fstem.db                       # report only
  python3 fstem_repair.py --db out/fstem.db --apply --rule min     # apply, course = worst module
  python3 fstem_repair.py --db out/fstem.db --apply --rule prior   # apply, keep course as pinned prior
  python3 fstem_repair.py --db out/fstem.db --check                # invariants, exit 1 on any violation

Decisions the flags encode
  --rule cap      DEFAULT. A course reads live only when every module reads live. Any failing module
                  caps the course at partial. dead stays a hand judgment and is never assigned by rule.
                  This is R6 section 3 as written ("denies full authority") and matches the build's practice.
  --rule min      course substrate_status becomes the worst of its modules. Turns three partial courses
                  holding one dead module (15.S12, 1.264J, ESD.290) into dead courses. Stricter than R6.
  --rule prior    course substrate_status stays as pinned; register keeps both directions
  --m4-code X     overlay m4 substrate_course '15.45' becomes X (confirm 15.450 first)
  --fin009 V      FSTEM-FIN-R4-009 modules take variant V (humanities or practicum)
  --dedupe        delete exact duplicate (asset_id, raw) rows in bibliography, keep lowest rowid
  --all-courses   apply --rule to all 364 courses, not only the 106 drafted
"""
import argparse, re, sqlite3, sys, datetime

RANK = {"live": 3, "partial": 2, "dead": 1}
UNRANK = {v: k for k, v in RANK.items()}
NOW = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
THIS_YEAR = datetime.date.today().year


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def probe(con):
    """Assert the schema this script relies on. Name every missing column."""
    need = {
        "assets": {"asset_id", "code", "fstem_id", "substrate_status", "n_bibliography"},
        "modules": {"module_id", "parent_fstem_id", "module_index", "module_count",
                    "status", "substrate_status", "refresh_due", "micro_credential", "variant"},
        "module_overlays": {"overlay_id", "module_id", "parent_fstem_id", "track_id",
                            "substrate_course", "substrate_status"},
        "bibliography": {"asset_id", "author", "title", "year", "raw", "is_citation"},
        "flags": {"asset_id", "flag"},
        "track_courses": {"track_id", "fstem_id"},
    }
    bad = []
    for t, cols in need.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        if not have:
            bad.append(f"table {t} missing")
            continue
        miss = cols - have
        if miss:
            bad.append(f"{t} lacks {sorted(miss)}; has {sorted(have)}")
    if bad:
        sys.exit("schema probe failed\n  " + "\n  ".join(bad))


def ensure_log(con):
    con.execute("""CREATE TABLE IF NOT EXISTS repair_log (
        applied_at TEXT, step TEXT, tbl TEXT, key TEXT, col TEXT, old TEXT, new TEXT)""")


def log(con, step, tbl, key, col, old, new):
    con.execute("INSERT INTO repair_log VALUES (?,?,?,?,?,?,?)",
                (NOW, step, tbl, key, col, None if old is None else str(old),
                 None if new is None else str(new)))


def worst_by_course(con, drafted_only=True):
    """course fstem_id -> worst module substrate_status"""
    sql = "SELECT parent_fstem_id, substrate_status FROM modules"
    if drafted_only:
        sql += " WHERE parent_fstem_id IN (SELECT parent_fstem_id FROM modules WHERE status='draft')"
    out = {}
    for fid, st in con.execute(sql):
        r = RANK.get(st)
        if r is None:
            continue
        out[fid] = min(out.get(fid, 9), r)
    return {k: UNRANK[v] for k, v in out.items()}


def section(title):
    print(f"\n== {title}")


# ----------------------------------------------------------------------------
# repairs
# ----------------------------------------------------------------------------
def fix_micro_credential(con, apply):
    section("micro_credential format")
    rows = con.execute("""SELECT module_id, parent_fstem_id, module_index, micro_credential
                          FROM modules WHERE micro_credential NOT LIKE parent_fstem_id || '-MC%'
                            AND NOT (parent_fstem_id='FSTEM-AI-SPINE-001' AND micro_credential LIKE 'FSTEM-AISPINE-M%')""").fetchall()
    for mid, fid, idx, old in rows:
        new = f"FSTEM-AISPINE-M{idx}" if fid == "FSTEM-AI-SPINE-001" else f"{fid}-MC{idx:02d}"
        print(f"  {mid}: {old} -> {new}")
        if apply:
            con.execute("UPDATE modules SET micro_credential=? WHERE module_id=?", (new, mid))
            log(con, "micro_credential", "modules", mid, "micro_credential", old, new)
    print(f"  {len(rows)} rows")


def fix_no_bibliography_flag(con, apply):
    section("no_bibliography flag versus table")
    counts = dict(con.execute("SELECT asset_id, COUNT(*) FROM bibliography GROUP BY 1"))
    flagged = {r[0] for r in con.execute("SELECT asset_id FROM flags WHERE flag='no_bibliography'")}
    add, drop = [], []
    for (aid,) in con.execute("SELECT asset_id FROM assets"):
        n = counts.get(aid, 0)
        if n == 0 and aid not in flagged:
            add.append(aid)
        if n > 0 and aid in flagged:
            drop.append((aid, n))
    for aid in add:
        print(f"  add flag: {aid} (0 rows)")
        if apply:
            con.execute("INSERT INTO flags(asset_id, flag) VALUES (?, 'no_bibliography')", (aid,))
            log(con, "no_bibliography", "flags", aid, "flag", None, "no_bibliography")
    for aid, n in drop:
        print(f"  drop flag: {aid} ({n} rows)")
        if apply:
            con.execute("DELETE FROM flags WHERE asset_id=? AND flag='no_bibliography'", (aid,))
            log(con, "no_bibliography", "flags", aid, "flag", "no_bibliography", None)
    print(f"  {len(add)} added, {len(drop)} dropped")


def fix_author_residue(con, apply):
    section("interface text in author column")
    residue = ("textbook", "required", "optional", "weeks", "buy at", "amazon", "pdf")
    rows = con.execute("SELECT rowid, asset_id, author FROM bibliography WHERE is_citation=1 AND author IS NOT NULL").fetchall()
    hit = [(rid, aid, a) for rid, aid, a in rows if a.strip().lower() in residue]
    for rid, aid, a in hit:
        print(f"  {aid} rowid {rid}: author '{a}' -> NULL, is_citation -> 0")
        if apply:
            con.execute("UPDATE bibliography SET author=NULL, is_citation=0 WHERE rowid=?", (rid,))
            log(con, "author_residue", "bibliography", str(rid), "author", a, None)
    print(f"  {len(hit)} rows")


RANGE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\s*[-\u2013\u2014/]\s*(1[5-9]\d\d|20\d\d)\b")
ARXIV = re.compile(r"arXiv[:\s]*(\d{4})\.\d{4,5}(?:v\d+)?", re.I)
ISBN = re.compile(r"ISBN[:\s]*[\d\-Xx ]{10,}", re.I)
PAGES = re.compile(r"\bpp?\.\s*\d+\s*[-\u2013]\s*\d+")
YEAR = re.compile(r"(?<![\d.])((?:1[4-9]|20)\d{2})(?![\d])")
PAREN_YEAR = re.compile(r"\(\s*((?:1[4-9]|20)\d{2})\s*[a-z]?\s*\)")


def reparse_year(raw):
    """Return the most plausible publication year in raw, or None."""
    s = ARXIV.sub(" ", raw)
    s = ISBN.sub(" ", s)
    s = PAGES.sub(" ", s)
    s = RANGE.sub(" ", s)
    m = PAREN_YEAR.search(s)
    if m:
        return int(m.group(1))
    ys = [int(y) for y in YEAR.findall(s) if 1450 <= int(y) <= THIS_YEAR]
    return ys[-1] if ys else None


def fix_years(con, apply):
    section("year parser artifacts")
    rows = con.execute("SELECT rowid, asset_id, author, year, raw FROM bibliography WHERE year IS NOT NULL AND raw IS NOT NULL").fetchall()
    changed, nulled, review = 0, 0, []
    for rid, aid, author, year, raw in rows:
        diagnosable = False
        m = ARXIV.search(raw)
        if m and int(m.group(1)) == year:
            diagnosable = True
        m = RANGE.search(raw)
        if m and int(m.group(1)) == year:
            diagnosable = True
        if year > THIS_YEAR or year < 1450:
            diagnosable = True
        if not diagnosable:
            if year < 1900:
                review.append((aid, author, year, raw[:80]))
            continue
        new = reparse_year(raw)
        if new == year:
            continue
        if new is None:
            nulled += 1
        else:
            changed += 1
        if changed + nulled <= 12:
            print(f"  {aid} {author or ''} {year} -> {new}   | {raw[:70]}")
        if apply:
            con.execute("UPDATE bibliography SET year=? WHERE rowid=?", (new, rid))
            log(con, "year_reparse", "bibliography", str(rid), "year", year, new)
    print(f"  {changed} re-dated, {nulled} set NULL (no publication year found in raw)")
    print(f"  {len(review)} rows before 1900 with no diagnosable artifact, left alone; review by hand")
    for r in review[:5]:
        print(f"     review: {r}")


QUOTED = re.compile(r"[\u201c\"]\s*([^\u201d\"]{3,}?)\s*[\u201d\"]")


def fix_titles(con, apply):
    section("title from quoted segment in raw")
    rows = con.execute("SELECT rowid, raw FROM bibliography WHERE is_citation=1 AND (title IS NULL OR title='') AND raw IS NOT NULL").fetchall()
    n = 0
    for rid, raw in rows:
        m = QUOTED.search(raw)
        if not m:
            continue
        t = m.group(1).strip().rstrip(".,;")
        if len(t) < 4:
            continue
        n += 1
        if apply:
            con.execute("UPDATE bibliography SET title=? WHERE rowid=?", (t, rid))
            log(con, "title_from_raw", "bibliography", str(rid), "title", None, t)
    print(f"  {n} of {len(rows)} citation rows gain a title; the rest are unquoted (books) and need a second parser")


def fix_dedupe(con, apply):
    section("duplicate bibliography rows")
    dup = con.execute("""SELECT COUNT(*) FROM bibliography b WHERE rowid > (
                            SELECT MIN(rowid) FROM bibliography x WHERE x.asset_id=b.asset_id AND x.raw IS b.raw)""").fetchone()[0]
    print(f"  {dup} rows are exact repeats within their asset")
    if apply:
        victims = con.execute("""SELECT b.rowid, b.asset_id, b.raw FROM bibliography b WHERE rowid > (
                            SELECT MIN(rowid) FROM bibliography x WHERE x.asset_id=b.asset_id AND x.raw IS b.raw)""").fetchall()
        for rid, aid, raw in victims:
            log(con, "dedupe", "bibliography", f"{aid}|rowid={rid}", "raw", raw, None)
        con.execute("DELETE FROM bibliography WHERE rowid IN (%s)" % ",".join(str(v[0]) for v in victims))
        con.execute("""UPDATE assets SET n_bibliography=(SELECT COUNT(*) FROM bibliography b WHERE b.asset_id=assets.asset_id)""")
        print("  deleted; assets.n_bibliography recounted")


def fix_overlays(con, apply, rule, m4_code):
    section("overlays")
    if m4_code:
        old = con.execute("SELECT substrate_course FROM module_overlays WHERE track_id='m4'").fetchone()[0]
        ok = con.execute("SELECT COUNT(*) FROM assets WHERE code=?", (m4_code,)).fetchone()[0]
        if not ok:
            print(f"  m4: {m4_code} is not an asset code either; not applied")
        else:
            print(f"  m4 substrate_course: {old} -> {m4_code}")
            if apply:
                con.execute("UPDATE module_overlays SET substrate_course=? WHERE track_id='m4'", (m4_code,))
                log(con, "overlay_m4_code", "module_overlays", "FSTEM-AI-SPINE-001-M8-m4", "substrate_course", old, m4_code)
    worst = worst_by_course(con, drafted_only=False)
    for oid, code, ost in con.execute("SELECT overlay_id, substrate_course, substrate_status FROM module_overlays ORDER BY 1"):
        a = con.execute("SELECT fstem_id, substrate_status FROM assets WHERE code=?", (code,)).fetchone()
        if a is None:
            print(f"  {oid}: substrate {code} resolves to no asset; status left at {ost}")
            continue
        fid, cst = a
        w = worst.get(fid, cst)
        if rule == "min":
            target = w
        elif rule == "cap":
            target = "partial" if (cst == "live" and w != "live") else cst
        else:  # prior: an overlay still must not outrank the course it anchors to
            target = cst
        if target != ost:
            print(f"  {oid} ({code}): {ost} -> {target}   [course={cst}, worst module={worst.get(fid)}]")
            if apply:
                con.execute("UPDATE module_overlays SET substrate_status=? WHERE overlay_id=?", (target, oid))
                log(con, "overlay_status", "module_overlays", oid, "substrate_status", ost, target)
    missing = con.execute("""SELECT o.module_id FROM module_overlays o
                             LEFT JOIN modules m ON m.module_id=o.module_id WHERE m.module_id IS NULL""").fetchall()
    if missing:
        print(f"  parent module {missing[0][0]} does not exist; {len(missing)} overlays point at it."
              " Not created here. Decide: add an M8 slot row, or document that overlay.module_id names a slot.")


def fix_course_status(con, apply, rule, all_courses):
    section(f"course substrate_status, rule={rule}")
    if rule == "prior":
        print("  prior rule chosen: assets.substrate_status untouched. Amend R6 section 3 and the brief to say so.")
        return
    worst = worst_by_course(con, drafted_only=not all_courses)
    n = 0
    for fid, w in sorted(worst.items()):
        code, cur = con.execute("SELECT code, substrate_status FROM assets WHERE fstem_id=?", (fid,)).fetchone()
        if rule == "min":
            expected = w
        else:  # cap
            if cur == "live" and w != "live":
                expected = "partial"
            elif cur == "partial" and w == "live":
                expected = "live"
            else:
                expected = cur
        if cur != expected:
            n += 1
            print(f"  {fid} {code}: {cur} -> {expected}")
            if apply:
                con.execute("UPDATE assets SET substrate_status=? WHERE fstem_id=?", (expected, fid))
                log(con, "course_status_min", "assets", fid, "substrate_status", cur, expected)
    print(f"  {n} courses")


def fix_fin009(con, apply, variant):
    section("FSTEM-FIN-R4-009 variant")
    if not variant:
        print("  no --fin009 given; left as is")
        return
    for mid, old in con.execute("SELECT module_id, variant FROM modules WHERE parent_fstem_id='FSTEM-FIN-R4-009'"):
        if old != variant:
            print(f"  {mid}: {old} -> {variant}")
            if apply:
                con.execute("UPDATE modules SET variant=? WHERE module_id=?", (variant, mid))
                log(con, "fin009_variant", "modules", mid, "variant", old, variant)


def create_views(con, apply):
    section("views that replace the hand tallies")
    views = {
        "v_course_worst_module": """
            SELECT m.parent_fstem_id AS fstem_id,
                   CASE MIN(CASE m.substrate_status WHEN 'live' THEN 3 WHEN 'partial' THEN 2 WHEN 'dead' THEN 1 END)
                        WHEN 3 THEN 'live' WHEN 2 THEN 'partial' WHEN 1 THEN 'dead' END AS worst_module,
                   SUM(m.status='draft') AS drafted, COUNT(*) AS modules
            FROM modules m GROUP BY 1""",
        "v_divergence": """
            SELECT m.module_id, a.fstem_id, a.code, a.substrate_status AS course, m.substrate_status AS module,
                   CASE WHEN (CASE m.substrate_status WHEN 'live' THEN 3 WHEN 'partial' THEN 2 ELSE 1 END)
                         >  (CASE a.substrate_status WHEN 'live' THEN 3 WHEN 'partial' THEN 2 ELSE 1 END)
                        THEN 'upward' ELSE 'downward' END AS direction
            FROM modules m JOIN assets a ON a.fstem_id=m.parent_fstem_id
            WHERE m.status='draft' AND m.substrate_status != a.substrate_status""",
        "v_live_every_module": """
            SELECT w.fstem_id, a.code FROM v_course_worst_module w JOIN assets a ON a.fstem_id=w.fstem_id
            WHERE w.drafted>0 AND w.worst_module='live' AND w.modules=w.drafted""",
        "v_refresh_calendar": """
            SELECT tc.track_id, m.module_id, a.code, m.substrate_status, m.refresh_due,
                   CASE WHEN tc.role='core' THEN 'core' ELSE 'track' END AS role
            FROM track_courses tc JOIN modules m ON m.parent_fstem_id=tc.fstem_id JOIN assets a ON a.fstem_id=tc.fstem_id
            WHERE m.status='draft'
            UNION ALL
            SELECT o.track_id, o.overlay_id, o.substrate_course, o.substrate_status, o.refresh_due, 'overlay'
            FROM module_overlays o""",
    }
    for name, body in views.items():
        print(f"  {name}")
        if apply:
            con.execute(f"DROP VIEW IF EXISTS {name}")
            con.execute(f"CREATE VIEW {name} AS {body}")


# ----------------------------------------------------------------------------
# state and checks
# ----------------------------------------------------------------------------
def report_state(con):
    section("state")
    q = lambda s: con.execute(s).fetchone()[0]
    print(f"  drafted shared modules      {q('SELECT COUNT(*) FROM modules WHERE status=\"draft\"')}")
    print(f"  overlays                    {q('SELECT COUNT(*) FROM module_overlays')}")
    worst = worst_by_course(con)
    up = down = 0
    for fid, cst in con.execute("SELECT fstem_id, substrate_status FROM assets"):
        for (mst,) in con.execute("SELECT substrate_status FROM modules WHERE parent_fstem_id=? AND status='draft'", (fid,)):
            if mst != cst:
                if RANK[mst] > RANK[cst]:
                    up += 1
                else:
                    down += 1
    print(f"  divergence register         {up + down}  ({up} upward, {down} downward)")
    live_all = sum(1 for fid, w in worst.items() if w == "live")
    print(f"  courses live at every module {live_all}")
    dead = q("""SELECT COUNT(*) FROM assets a WHERE substrate_status='dead'
                AND fstem_id IN (SELECT parent_fstem_id FROM modules WHERE status='draft')""")
    print(f"  dead drafted courses        {dead}")
    print("  refresh due, drafted modules + overlays, by track (inherited courses included):")
    for tid in [r[0] for r in con.execute("SELECT track_id FROM tracks ORDER BY 1")]:
        n12 = q(f"""SELECT COUNT(*) FROM modules m WHERE m.status='draft' AND m.refresh_due LIKE '{THIS_YEAR+1}%'
                    AND m.parent_fstem_id IN (SELECT fstem_id FROM track_courses WHERE track_id='{tid}')""")
        n18 = q(f"""SELECT COUNT(*) FROM modules m WHERE m.status='draft' AND m.refresh_due LIKE '{THIS_YEAR+2}%'
                    AND m.parent_fstem_id IN (SELECT fstem_id FROM track_courses WHERE track_id='{tid}')""")
        ov = q(f"SELECT COUNT(*) FROM module_overlays WHERE track_id='{tid}'")
        print(f"     {tid}: {n12} due {THIS_YEAR+1}, {n18} due {THIS_YEAR+2}, +{ov} overlay")


def check(con, rule):
    section("invariants")
    fails = []
    q = lambda s: con.execute(s).fetchone()[0]

    def inv(name, n, detail=""):
        status = "ok " if n == 0 else "FAIL"
        print(f"  [{status}] {name}: {n} {detail}")
        if n:
            fails.append(name)

    def decide(name, n, detail=""):
        print(f"  [{'ok ' if n == 0 else 'DECIDE'}] {name}: {n} {detail}")

    drafted = {r[0] for r in con.execute("SELECT DISTINCT parent_fstem_id FROM modules WHERE status='draft'")}
    worst = worst_by_course(con, drafted_only=False)
    live_with_fail = [fid for fid, cst in con.execute("SELECT fstem_id, substrate_status FROM assets")
                      if fid in worst and cst == "live" and worst[fid] != "live"]
    above_worst = [fid for fid, cst in con.execute("SELECT fstem_id, substrate_status FROM assets")
                   if fid in worst and RANK[worst[fid]] < RANK[cst]]
    if rule == "prior":
        print(f"  [info] drafted courses reading live with a failing module: {len([f for f in live_with_fail if f in drafted])} (allowed under --rule prior)")
    elif rule == "min":
        inv("drafted course status above its worst module", len([f for f in above_worst if f in drafted]))
    else:
        inv("drafted course reads live with a failing module", len([f for f in live_with_fail if f in drafted]))
    nd = len([f for f in live_with_fail if f not in drafted])
    if nd:
        print(f"  [info] undrafted courses reading live with a failing module: {nd} (FSTEM-STS-R4-022 is the known one)")
    inv("overlay substrate code with no asset",
        q("SELECT COUNT(*) FROM module_overlays o WHERE NOT EXISTS (SELECT 1 FROM assets a WHERE a.code=o.substrate_course)"))
    n = 0
    for oid, code, ost in con.execute("SELECT overlay_id, substrate_course, substrate_status FROM module_overlays"):
        a = con.execute("SELECT fstem_id, substrate_status FROM assets WHERE code=?", (code,)).fetchone()
        if a and RANK[ost] > RANK[a[1]]:
            n += 1
    inv("overlay status above its substrate course", n)
    decide("overlay parent module missing",
        q("SELECT COUNT(*) FROM module_overlays o LEFT JOIN modules m ON m.module_id=o.module_id WHERE m.module_id IS NULL"),
        "(add an M8 slot row, or document that overlay.module_id names a slot)")
    inv("no_bibliography flag disagrees with table",
        q("""SELECT COUNT(*) FROM assets a WHERE (EXISTS(SELECT 1 FROM flags f WHERE f.asset_id=a.asset_id AND f.flag='no_bibliography'))
             != (NOT EXISTS(SELECT 1 FROM bibliography b WHERE b.asset_id=a.asset_id))"""))
    inv("duplicate (asset_id, raw) rows",
        q("SELECT COUNT(*) FROM (SELECT asset_id, raw FROM bibliography GROUP BY 1,2 HAVING COUNT(*)>1)"))
    inv("citation years outside 1450..current",
        q(f"SELECT COUNT(*) FROM bibliography WHERE is_citation=1 AND (year<1450 OR year>{THIS_YEAR})"))
    inv("citation year equals start of a date range or an arXiv prefix in raw",
        sum(1 for y, raw in con.execute("SELECT year, raw FROM bibliography WHERE is_citation=1 AND year IS NOT NULL AND raw IS NOT NULL")
            if (RANGE.search(raw) and int(RANGE.search(raw).group(1)) == y) or (ARXIV.search(raw) and int(ARXIV.search(raw).group(1)) == y)))
    inv("micro_credential off pattern",
        q("""SELECT COUNT(*) FROM modules WHERE micro_credential NOT LIKE parent_fstem_id || '-MC%'
             AND NOT (parent_fstem_id='FSTEM-AI-SPINE-001' AND micro_credential LIKE 'FSTEM-AISPINE-M%')"""))
    inv("module_count != sibling count",
        q("SELECT COUNT(*) FROM modules m WHERE module_count != (SELECT COUNT(*) FROM modules s WHERE s.parent_fstem_id=m.parent_fstem_id)"))
    inv("drafted course not in any track",
        q("SELECT COUNT(*) FROM (SELECT DISTINCT parent_fstem_id p FROM modules WHERE status='draft') d WHERE NOT EXISTS (SELECT 1 FROM track_courses t WHERE t.fstem_id=d.p)"))
    decide("drafted module with a substrate_block placeholder",
        q("SELECT COUNT(*) FROM modules WHERE status='draft' AND (substrate_block='' OR substrate_block LIKE '%placeholder%')"),
        "(RES.10-002 and the AI spine; needs a curricular decision, not a query)")
    decide("drafted modules whose lessons are all skeleton",
        q("SELECT COUNT(*) FROM modules m WHERE status='draft' AND NOT EXISTS (SELECT 1 FROM lessons l WHERE l.module_id=m.module_id AND l.status!='skeleton')"),
        "(populate from guide section 4, or stop counting lessons as state)")
    return fails


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--rule", choices=["cap", "min", "prior"], default="cap")
    ap.add_argument("--m4-code", default=None)
    ap.add_argument("--fin009", choices=["humanities", "practicum", "quantitative"], default=None)
    ap.add_argument("--dedupe", action="store_true")
    ap.add_argument("--all-courses", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    probe(con)
    if a.check:
        report_state(con)
        fails = check(con, a.rule)
        sys.exit(1 if fails else 0)

    print(f"{'APPLY' if a.apply else 'DRY RUN'} against {a.db}, rule={a.rule}")
    report_state(con)
    if a.apply:
        ensure_log(con)
    fix_micro_credential(con, a.apply)
    fix_no_bibliography_flag(con, a.apply)
    fix_author_residue(con, a.apply)
    fix_years(con, a.apply)
    fix_titles(con, a.apply)
    if a.dedupe:
        fix_dedupe(con, a.apply)
    fix_course_status(con, a.apply, a.rule, a.all_courses)
    fix_overlays(con, a.apply, a.rule, a.m4_code)
    fix_fin009(con, a.apply, a.fin009)
    create_views(con, a.apply)
    if a.apply:
        con.commit()
        print("\ncommitted. repair_log holds every change.")
        report_state(con)
    else:
        print("\nnothing written. Re-run with --apply.")


if __name__ == "__main__":
    main()
