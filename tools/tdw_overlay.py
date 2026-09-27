#!/usr/bin/env python3
"""
C2. Overlay map, ledger rows and site content for program m7.

Dry run is the default. Nothing is written without --apply.

    python3 tools/tdw_overlay.py rename       [--src PATH/fstem.db] [--apply]
    python3 tools/tdw_overlay.py render-scope [--src PATH/fstem.db] [--apply]
    python3 tools/tdw_overlay.py attach       [--apply]     # tdw_overlay_attachments + proposals
    python3 tools/tdw_overlay.py ledger       [--apply]     # SEED_07_OVERLAY_M7.csv
    python3 tools/tdw_overlay.py site         [--apply]     # site/m7_site_content.json
    python3 tools/tdw_overlay.py all          [--src ...] [--apply]

rename and render-scope work on build/fstem.db, a copy of --src made on first use.
The original fstem.db is never opened for writing. attach, ledger and site read
build/fstem_m7.db from tools/tdw_ingest.py and read build/fstem.db when it exists.
"""
import argparse, csv, json, math, os, re, shutil, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
M7DB = os.path.join(BUILD, "fstem_m7.db")
BASEDB = os.path.join(BUILD, "fstem.db")
AFFORD = os.path.join(ROOT, "tools", "overlay_affordances.csv")
RENAME_MAP = os.path.join(ROOT, "scripts", "fstem_rename_map.csv")
SEED_OUT = os.path.join(BUILD, "SEED_07_OVERLAY_M7.csv")
SITE_OUT = os.path.join(ROOT, "site", "m7_site_content.json")
PROGRAM = "m7"
SPINE = "FSTEM-AI-SPINE-001"

OLD_KEY = re.compile(r'^(FSTEM-AI-SPINE-001-M8)-(m[1-7])(?=$|-W\d+$)')
# (table, column) pairs that carry overlay keys. Only rows whose value matches OLD_KEY change.
KEY_COLUMNS = [("module_overlays", "overlay_id"), ("lessons", "lesson_id"), ("lessons", "module_id"),
               ("artifacts", "key"), ("artifacts", "artifact_id")]
# Base (Pass 1) tables. The overlay never writes to these.
BASE_TABLES = ["assets", "modules", "tracks", "track_courses", "verticals", "bibliography"]


def ensure_copy(src):
    os.makedirs(BUILD, exist_ok=True)
    if os.path.exists(BASEDB):
        return BASEDB
    if not src or not os.path.exists(src):
        return None
    if os.path.abspath(src) == os.path.abspath(BASEDB):
        return BASEDB
    shutil.copy2(src, BASEDB)
    print(f"copied {src} -> {BASEDB}")
    return BASEDB


def has_table(cx, t):
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def cols(cx, t):
    return [r[1] for r in cx.execute(f"PRAGMA table_info({t})")]


def new_key(v):
    return OLD_KEY.sub(r'\1-PROG-\2', v)


def rename(cx, apply):
    """Step 1. FSTEM-AI-SPINE-001-M8-m<n> -> FSTEM-AI-SPINE-001-M8-PROG-m<n>."""
    total = 0
    for t, c in KEY_COLUMNS:
        if not has_table(cx, t) or c not in cols(cx, t):
            print(f"  {t}.{c:<12} absent, skipped")
            continue
        rows = [(v,) for (v,) in cx.execute(f"SELECT {c} FROM {t}") if v and "-M8-m" in v]
        hits = []
        for (v,) in rows:
            if t == "artifacts" and c == "artifact_id":
                parts = v.split(":")
                if len(parts) >= 3 and OLD_KEY.match(parts[1]):
                    hits.append((v, ":".join([parts[0], new_key(parts[1])] + parts[2:])))
            elif OLD_KEY.match(v):
                hits.append((v, new_key(v)))
        old_n = cx.execute(f"SELECT COUNT(*) FROM {t} WHERE {c} LIKE '%-M8-m%'").fetchone()[0]
        if apply:
            for o, n in hits:
                cx.execute(f"UPDATE {t} SET {c}=? WHERE {c}=?", (n, o))
        new_n = cx.execute(f"SELECT COUNT(*) FROM {t} WHERE {c} LIKE '%-M8-PROG-m%'").fetchone()[0]
        print(f"  {t}.{c:<12} old-form {old_n:>4}  to rename {len(hits):>4}  new-form after {new_n:>4}")
        for o, n in hits[:3]:
            print(f"      {o} -> {n}")
        total += len(hits)
    return total


def render_scope(cx, apply):
    """Step 2. render_scope on overlay blocks. NULL means all programs."""
    if not has_table(cx, "module_overlays"):
        print("  module_overlays absent, skipped")
        return 0
    if "render_scope" in cols(cx, "module_overlays"):
        print("  module_overlays.render_scope already present")
        return 0
    print("  ALTER TABLE module_overlays ADD COLUMN render_scope TEXT DEFAULT NULL")
    if apply:
        cx.execute("ALTER TABLE module_overlays ADD COLUMN render_scope TEXT DEFAULT NULL")
    return 1


def rename_map():
    return {r["mit_code"]: r for r in csv.DictReader(open(RENAME_MAP, encoding="utf-8"))}


def affordances():
    rm = rename_map()
    out = []
    for r in csv.DictReader(open(AFFORD, encoding="utf-8")):
        code = r["origin_code"]
        if code == "FSTEM.AI.SPINE":
            fid, title = SPINE, "[AI SPINE COURSE TITLE]"
        else:
            m = rm.get(code)
            fid, title = (m["fstem_id"], m["fstem_title_2026"]) if m else (None, None)
        out.append(dict(r, fstem_id=fid, title=title, o2=int(r["o2"]),
                        keywords=[k for k in r["keywords"].split(";") if k]))
    return out


def base_modules(fid):
    if not os.path.exists(BASEDB):
        return None
    cx = sqlite3.connect(BASEDB)
    rows = [r[0] for r in cx.execute("SELECT module_id FROM modules WHERE parent_fstem_id=? ORDER BY module_index",
                                      (fid,))]
    cx.close()
    return rows


ATTACH_DDL = """
CREATE TABLE IF NOT EXISTS tdw_overlay_attachments (
    attachment_id TEXT PRIMARY KEY, program TEXT, fstem_id TEXT, course_title TEXT, origin_code TEXT,
    o2 INTEGER, rule TEXT, module_scope TEXT, module_id TEXT, overlay_key TEXT, render_scope TEXT,
    status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS tdw_overlay_proposals (
    attachment_id TEXT, rank INTEGER, section_id TEXT, section_title TEXT, chapter TEXT, score REAL,
    matched_terms TEXT, status TEXT, source_file TEXT, start_line INTEGER, end_line INTEGER, sha256 TEXT,
    PRIMARY KEY (attachment_id, rank));
"""
SKIP_SECTIONS = re.compile(r'^(Field Card|Sourcing Status|Notes|Copyright|Dedication|Epigraph)')


def attachments():
    rows = []
    for a in affordances():
        if a["o2"] < 2:
            continue
        base = dict(program=PROGRAM, fstem_id=a["fstem_id"], course_title=a["title"], origin_code=a["origin_code"],
                    o2=a["o2"], rule=a["rule"], render_scope=PROGRAM, keywords=a["keywords"])
        if a["module_scope"] == "M8":
            mid = f"{SPINE}-M8"
            rows.append(dict(base, attachment_id=f"ATT-{mid}", module_scope="M8", module_id=mid,
                             overlay_key=f"{mid}-PROG-{PROGRAM}", status="steward_to_confirm",
                             note="Spine practicum slot. Pilot target for C4."))
        elif a["module_scope"] == "each":
            mods = base_modules(a["fstem_id"])
            if mods:
                for mid in mods:
                    rows.append(dict(base, attachment_id=f"ATT-{mid}", module_scope="each", module_id=mid,
                                     overlay_key=f"{mid}-PROG-{PROGRAM}", status="steward_to_confirm",
                                     note="Attach only if this module has three or fewer base findings."))
            else:
                rows.append(dict(base, attachment_id=f"ATT-{a['fstem_id']}", module_scope="each", module_id=None,
                                 overlay_key=None, status="pending_base_db",
                                 note="Expands to one row per module once build/fstem.db is present."))
        else:
            rows.append(dict(base, attachment_id=f"ATT-{a['fstem_id']}", module_scope="steward", module_id=None,
                             overlay_key=None, status="steward_to_confirm",
                             note="Steward names the module. One finding for the course."))
    return rows


def proposals(cx, att, top=3):
    secs = [dict(zip([d[0] for d in cur.description], r)) for cur in
            [cx.execute("SELECT section_id, title, chapter_id, level, text, source_file, start_line, end_line, "
                        "sha256 FROM tdw_sections WHERE source_file='KDP_MASTER.md' AND level=3")] for r in cur]
    chap = {r[0]: r[1] for r in cx.execute("SELECT section_id, title FROM tdw_sections WHERE level=2")}
    secs = [s for s in secs if not SKIP_SECTIONS.match(s["title"])
            and not (chap.get(s["chapter_id"]) or "").startswith(("Appendix G", "Appendix H"))]
    N = len(secs)
    out = []
    for a in att:
        terms = a["keywords"]
        pats = {t: re.compile(r'\b' + re.escape(t) + r'\b', re.I) for t in terms}
        df = {t: sum(1 for s in secs if pats[t].search(s["text"])) for t in terms}
        scored = []
        for s in secs:
            hits = {t: len(pats[t].findall(s["text"])) for t in terms}
            hit = {t: n for t, n in hits.items() if n}
            if not hit:
                continue
            words = max(len(s["text"].split()), 50)
            score = sum((1 + math.log(n)) * math.log(1 + N / df[t]) for t, n in hit.items())
            score = score * len(hit) / math.sqrt(words / 50)
            scored.append((round(score, 3), s, sorted(hit)))
        scored.sort(key=lambda x: (-x[0], x[1]["start_line"]))
        for rank, (sc, s, hit) in enumerate(scored[:top], 1):
            out.append(dict(attachment_id=a["attachment_id"], rank=rank, section_id=s["section_id"],
                            section_title=s["title"], chapter=chap.get(s["chapter_id"]), score=sc,
                            matched_terms=";".join(hit), status="steward_to_confirm",
                            source_file=s["source_file"], start_line=s["start_line"], end_line=s["end_line"],
                            sha256=s["sha256"]))
    return out


def attach(apply):
    cx = sqlite3.connect(M7DB)
    att = attachments()
    prop = proposals(cx, att)
    before = [cx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if has_table(cx, t) else 0
              for t in ("tdw_overlay_attachments", "tdw_overlay_proposals")]
    print(f"  tdw_overlay_attachments before {before[0]:>3}  to write {len(att)}")
    print(f"  tdw_overlay_proposals   before {before[1]:>3}  to write {len(prop)}")
    for a in att:
        ps = [p for p in prop if p["attachment_id"] == a["attachment_id"]]
        print(f"    {a['attachment_id']:<34} o2={a['o2']} {a['status']:<20} "
              + " | ".join(f"{p['chapter']} / {p['section_title']} L{p['start_line']}" for p in ps)[:150])
    if apply:
        cx.executescript("DROP TABLE IF EXISTS tdw_overlay_attachments; DROP TABLE IF EXISTS tdw_overlay_proposals;"
                         + ATTACH_DDL)
        keys = ["attachment_id", "program", "fstem_id", "course_title", "origin_code", "o2", "rule", "module_scope",
                "module_id", "overlay_key", "render_scope", "status", "note"]
        cx.executemany(f"INSERT INTO tdw_overlay_attachments VALUES ({','.join('?' * len(keys))})",
                       [[a[k] for k in keys] for a in att])
        pk = list(prop[0]) if prop else []
        cx.executemany(f"INSERT INTO tdw_overlay_proposals ({','.join(pk)}) VALUES ({','.join('?' * len(pk))})",
                       [[p[k] for k in pk] for p in prop])
        cx.commit()
        after = [cx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("tdw_overlay_attachments", "tdw_overlay_proposals")]
        print(f"  after: attachments {after[0]}, proposals {after[1]}")
    cx.close()
    return len(att)


SEED_COLS = ["artifact_id", "kind", "grain", "key", "fstem_id", "track_ids", "attempt", "status", "render_scope",
             "model", "prompt_hash", "output_path", "output_hash", "review_status", "reviewer", "review_minutes",
             "reviewed_at", "source_attachment"]


def ledger(apply):
    """Step 5. One overlay_block row per attachment, status todo. Base rows are never read or written."""
    att = attachments()
    rows = []
    for a in att:
        key = a["overlay_key"] or f"{a['fstem_id']}-M?-PROG-{PROGRAM}"
        rows.append(dict(artifact_id=f"overlay_block:{key}:1", kind="overlay_block", grain="module", key=key,
                         fstem_id=a["fstem_id"], track_ids=PROGRAM, attempt=1, status="todo",
                         render_scope=PROGRAM, source_attachment=a["attachment_id"]))
    before = 0
    if os.path.exists(SEED_OUT):
        with open(SEED_OUT, encoding="utf-8") as fh:
            before = sum(1 for _ in fh) - 1
    print(f"  {os.path.relpath(SEED_OUT, ROOT)} rows before {before}  to write {len(rows)}")
    if apply:
        with open(SEED_OUT, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=SEED_COLS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in SEED_COLS})
        print(f"  after {len(rows)}")
    return len(rows)


def base_counts():
    """Program facts from the base db when present. None when absent."""
    if not os.path.exists(BASEDB):
        return None
    cx = sqlite3.connect(BASEDB)
    fids = [r[0] for r in cx.execute("SELECT fstem_id FROM track_courses WHERE track_id=?", (PROGRAM,))] \
        if has_table(cx, "track_courses") else []
    if not fids:
        cx.close()
        return None
    q = ",".join("?" * len(fids))
    out = dict(
        course_count=len(fids),
        module_count=cx.execute(f"SELECT COUNT(*) FROM modules WHERE parent_fstem_id IN ({q})", fids).fetchone()[0],
        lesson_count=cx.execute(f"SELECT COUNT(*) FROM lessons l JOIN modules m ON m.module_id=l.module_id "
                                f"WHERE m.parent_fstem_id IN ({q})", fids).fetchone()[0],
        micro_credential_count=cx.execute(f"SELECT COUNT(DISTINCT micro_credential) FROM modules WHERE "
                                          f"parent_fstem_id IN ({q}) AND micro_credential IS NOT NULL AND "
                                          f"micro_credential != ''", fids).fetchone()[0])
    cx.close()
    return out


def first_sentence(text, label_end):
    body = text[label_end:].strip()
    m = re.match(r'(.+?[.!?])(\s|$)', body)
    return m.group(1) if m else body


def table_after(lines, start, end):
    rows = []
    for i in range(start, end + 1):
        ln = lines[i - 1]
        if not ln.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip().strip("*").strip() for c in ln.strip().strip("|").split("|")]
        if all(re.fullmatch(r':?-+:?', c) for c in cells if c):
            continue
        rows.append((i, cells))
    return rows


def site(apply):
    cx = sqlite3.connect(M7DB)
    cx.row_factory = sqlite3.Row
    kdp = open(os.path.join(ROOT, "KDP_MASTER.md"), encoding="utf-8").read().split("\n")
    ref = lambda r: {"source_file": r["source_file"], "start_line": r["start_line"], "end_line": r["end_line"]}

    docs = {r["doc_id"]: r for r in cx.execute("SELECT * FROM tdw_documents")}
    books = []
    for did in ("KDP", "PRIMER"):
        d = docs[did]
        parts = [dict(title=s["title"], **ref(s)) for s in
                 cx.execute("SELECT * FROM tdw_sections WHERE doc_id=? AND level=1 ORDER BY start_line", (did,))]
        chapters = [dict(title=s["title"], part=s["part"], is_case_chapter=bool(s["is_case_chapter"]), **ref(s))
                    for s in cx.execute("SELECT * FROM tdw_sections WHERE doc_id=? AND level=2 AND heading NOT LIKE "
                                        "'%.unlisted%' ORDER BY start_line", (did,))]
        books.append(dict(doc_id=did, front_matter=json.loads(d["front_matter"]), parts=parts, chapters=chapters,
                          counts=dict(claims_documented=cx.execute(
                              "SELECT COUNT(*) FROM tdw_claims WHERE doc_id=? AND label='Documented'", (did,)).fetchone()[0],
                              claims_argued=cx.execute(
                              "SELECT COUNT(*) FROM tdw_claims WHERE doc_id=? AND label='Argued'", (did,)).fetchone()[0],
                              notes=cx.execute("SELECT COUNT(*) FROM tdw_notes WHERE doc_id=?", (did,)).fetchone()[0]),
                          source_file=d["source_file"], sha256=d["sha256"]))

    stack_sec = cx.execute("SELECT * FROM tdw_sections WHERE doc_id='KDP' AND level=2 AND title='The Stack'").fetchone()
    stack = [dict(layer=c[0], in_any_enterprise=c[1], holds_here=c[2], source_file="KDP_MASTER.md", start_line=i,
                  end_line=i)
             for i, c in table_after(kdp, stack_sec["start_line"], stack_sec["end_line"]) if c[0] != "TOGAF element"]

    def short_table(title):
        s = cx.execute("SELECT * FROM tdw_sections WHERE doc_id='KDP' AND level=3 AND title=? AND start_line BETWEEN ? "
                       "AND ?", (title, stack_sec["start_line"], stack_sec["end_line"])).fetchone()
        return {c[1]: (i, c) for i, c in table_after(kdp, s["start_line"], s["end_line"]) if c[0].isdigit()}

    dshort, mshort = short_table("Architecture Principles. Ten Dimensions"), short_table("Business Architecture. Twelve Domains")
    dims = []
    for r in cx.execute("SELECT * FROM tdw_dimensions ORDER BY number"):
        lab = len(f"**{r['number']}. {r['name']}.**")
        sh = dshort.get(r["name"])
        dims.append(dict(id=r["dimension_id"], number=r["number"], name=r["name"],
                         definition=first_sentence(r["text"], lab), definition_ref=ref(r),
                         question=sh[1][2] if sh else None, principle=sh[1][3] if sh else None,
                         stack_ref=dict(source_file="KDP_MASTER.md", start_line=sh[0], end_line=sh[0]) if sh else None))
    doms = []
    for r in cx.execute("SELECT * FROM tdw_domains ORDER BY number"):
        lab = len(f"**{r['number']}. {r['name']}.**")
        sh = next((v for k, v in mshort.items() if int(v[1][0]) == r["number"]), None)
        doms.append(dict(id=r["domain_id"], number=r["number"], name=r["name"],
                         definition=first_sentence(r["text"], lab), definition_ref=ref(r),
                         summary=sh[1][2] if sh else None,
                         stack_ref=dict(source_file="KDP_MASTER.md", start_line=sh[0], end_line=sh[0]) if sh else None))
    strats = [dict(id=r["stratagem_id"], number=r["number"], name=r["name"], group_num=r["group_num"],
                   group=f"Group {r['group_roman']}. {r['group_name']}", is_anchor=bool(r["is_anchor"]), ref=ref(r))
              for r in cx.execute("SELECT * FROM tdw_stratagems ORDER BY number")]
    lifecycle = []
    for r in cx.execute("SELECT * FROM tdw_sections WHERE doc_id='PRIMER' AND level=2 AND part LIKE 'Part Three%' "
                        "ORDER BY start_line"):
        m = re.match(r'Chapter (\d+)\. (.*)', r["title"])
        if m:
            lifecycle.append(dict(stage=m.group(2), primer_chapter=int(m.group(1)), ref=ref(r)))
    cases = [r["title"] for r in cx.execute("SELECT title FROM tdw_sections WHERE doc_id='KDP' AND level=2 AND "
                                            "is_case_chapter=1")]
    htitles = [r["title"] for r in cx.execute(
        "SELECT title FROM tdw_sections WHERE doc_id='KDP' AND level=3 AND parent_id=(SELECT section_id FROM "
        "tdw_sections WHERE doc_id='KDP' AND level=2 AND title LIKE 'Appendix H%')")]
    for st in lifecycle:
        st["kdp_case_chapter"] = None
        for t in htitles:
            m = re.match(r'^(.*?)(?:\s*\((.*)\))?$', t)
            if m.group(1) != st["stage"]:
                continue
            keys = [k for k in (m.group(1), m.group(2)) if k]
            hit = [c for c in cases if any(c == k or c.startswith(k + ":") for k in keys)]
            st["kdp_case_chapter"] = hit[0] if len(hit) == 1 else None

    aff = affordances()
    base = base_counts()
    content = dict(
        program=dict(id=PROGRAM, name="Total Domain Conflict", degree="MS-TDC", rung="masters",
                     facts=dict(course_count=base["course_count"] if base else len(aff),
                                module_count=base["module_count"] if base else None,
                                lesson_count=base["lesson_count"] if base else None,
                                micro_credential_count=base["micro_credential_count"] if base else None,
                                facts_source="build/fstem.db" if base else
                                "tools/overlay_affordances.csv; module, lesson and micro-credential counts "
                                "await build/fstem.db")),
        courses=[dict(fstem_id=a["fstem_id"], title=a["title"], overlay_level=a["o2"]) for a in aff],
        framework=dict(stack=stack, dimensions=dims, domains=doms, stratagems=strats, lifecycle=lifecycle),
        books=books)
    blob = json.dumps(content, ensure_ascii=False, indent=1)
    leaks = re.findall(r'\bMIT\b|\bOCW\b|OpenCourseWare|\b\d{1,2}\.\d{2,3}[A-Z]?\b|\b(?:STS|ESD|RES|IDS)\.[\w-]+', blob)
    print(f"  site content: {len(content['courses'])} courses, {len(stack)} stack layers, {len(dims)} dimensions, "
          f"{len(doms)} domains, {len(strats)} stratagems, {len(lifecycle)} lifecycle stages")
    print(f"  program facts: {content['program']['facts']}")
    if leaks:
        print(f"  REFUSED: origin names or codes in site content: {sorted(set(leaks))[:10]}")
        return 0
    if apply:
        os.makedirs(os.path.dirname(SITE_OUT), exist_ok=True)
        with open(SITE_OUT, "w", encoding="utf-8") as fh:
            fh.write(blob + "\n")
        print(f"  wrote {os.path.relpath(SITE_OUT, ROOT)}")
    cx.close()
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["rename", "render-scope", "attach", "ledger", "site", "all"])
    ap.add_argument("--src", help="original fstem.db to copy into build/")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    mode = "APPLY" if a.apply else "DRY RUN"
    steps = ["rename", "render-scope", "attach", "ledger", "site"] if a.step == "all" else [a.step]
    for s in steps:
        print(f"\n== {s} ({mode}) ==")
        if s in ("rename", "render-scope"):
            db = ensure_copy(a.src)
            if not db:
                print("  build/fstem.db absent and no --src given. Skipped.")
                continue
            cx = sqlite3.connect(db)
            (rename if s == "rename" else render_scope)(cx, a.apply)
            if a.apply:
                cx.commit()
            cx.close()
        elif s == "attach":
            attach(a.apply)
        elif s == "ledger":
            ledger(a.apply)
        elif s == "site":
            site(a.apply)
    if not a.apply:
        print("\nDRY RUN, nothing written.")


if __name__ == "__main__":
    sys.exit(main())
