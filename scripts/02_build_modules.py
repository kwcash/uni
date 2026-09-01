#!/usr/bin/env python3
"""
FSTEM module and lesson builder, 22 August 2026.

TOP-DOWN for structure, BOTTOM-UP for content.

  Top-down  : Python emits every module and lesson row from the course record.
              Ids, counts, sequence, rung, verb, variant, refresh dates, credentials.
              Deterministic. No model involved. Runs in seconds. Regenerates any time.

  Bottom-up : each module's prose comes from its substrate text plus its currency
              layer, drafted against FSTEM_MODULE_TEMPLATE.md. This script does not
              write prose. It writes the brief the drafting agent reads.

Titles come from the parent primer's module map where a primer exists on disk.
Where none exists the row lands as TITLE_PENDING rather than an invented title.

    python3 02_build_modules.py --db out/fstem.db --primers primers/ --out build/
    python3 02_build_modules.py --db out/fstem.db --primers primers/ --out build/ --stubs
"""
import argparse, csv, json, os, re, sqlite3, sys, datetime, collections

DEFAULT_MODULES = 4
SPINE_MODULES = {"FSTEM-AI-SPINE-001": 7}
WEEKS = 3
WINDOW_12 = {"computer_science"}

VARIANT_BY_VERTICAL = {
    "mathematics": "quantitative", "engineering": "quantitative",
    "computer_science": "quantitative", "economics": "quantitative",
    "finance": "quantitative", "business": "quantitative",
    "history": "humanities", "literature": "humanities",
    "philosophy": "humanities", "politics": "humanities",
    "society_technology": "humanities", "general_core": "humanities",
    "arts": "studio",
}
VERB = {"undergraduate": "Reproduce", "masters": "Apply", "research": "Extend"}
RUNGTAG = {"undergraduate": "R3", "masters": "R4"}

def add_months(d, n):
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return datetime.date(y, m, day)


ROW = re.compile(r'^\s*\|\s*(?:\*\*)?M?(\d+)(?:\*\*)?\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$', re.M)
HDR = re.compile(r'(?i)^\s*\|\s*module\s*\|', re.M)


def parse_primer(path):
    """Pull the module map and the deposit variant out of a primer."""
    txt = open(path, encoding="utf-8", errors="replace").read()
    out = {"titles": {}, "blocks": {}, "variant": None, "sessions": {}}
    m = HDR.search(txt)
    if m:
        tail = txt[m.start():]
        stop = re.search(r'\n\s*\n(?!\s*\|)', tail)
        table = tail[:stop.start()] if stop else tail[:3000]
        for r in ROW.finditer(table):
            i = int(r.group(1))
            title = r.group(2).strip().strip("*")
            if title.lower() in ("title", "---"):
                continue
            out["titles"][i] = title
            out["blocks"][i] = r.group(3).strip()
    low = txt.lower()
    if re.search(r'paper.{0,20}revision|revision.{0,20}expansion', low):
        out["variant"] = "humanities"
    elif re.search(r'one-command re-run|numeric completion|re-run script', low):
        out["variant"] = "quantitative"
    elif re.search(r'\bcritique cycle\b|drawings.{0,30}models', low):
        out["variant"] = "studio"
    elif re.search(r'op-ed|explainer|fieldwork|practicum', low):
        out["variant"] = "practicum"
    ms = re.search(r'status\s*\*\*(live|partial|dead)\*\*', txt)
    if ms:
        out["substrate_status"] = ms.group(1)
    return out


def find_primer(pdir, fid):
    if not pdir:
        return None
    for pat in (f"FSTEM-{fid}-PRIMER.md", f"{fid}-PRIMER.md", f"{fid}.md"):
        p = os.path.join(pdir, pat)
        if os.path.exists(p):
            return p
    for root, _, files in os.walk(pdir):
        for f in files:
            if fid in f and f.endswith(".md"):
                return os.path.join(root, f)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--primers", default=None, help="directory holding the 358 primer .md files")
    ap.add_argument("--out", default="build")
    ap.add_argument("--stubs", action="store_true", help="also emit template-shaped .md skeletons")
    ap.add_argument("--tracks-only", action="store_true", help="restrict to the 7 masters tracks")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    cx = sqlite3.connect(a.db)
    cx.row_factory = sqlite3.Row
    cols = {r[1] for r in cx.execute("PRAGMA table_info(assets)")}
    if "fstem_id" not in cols:
        sys.exit("assets.fstem_id missing. Run 01_migrate_db.py first.")

    q = ("SELECT asset_id, code, title, rung, fstem_id, substrate_status, term, year, "
         "COALESCE(verticals_override, verticals_scored) AS verts FROM assets "
         "WHERE fstem_id IS NOT NULL AND fstem_id != ''")
    if a.tracks_only:
        q += " AND rung='masters'"
    rows = cx.execute(q).fetchall()
    if not rows:
        sys.exit("no assets carry an fstem_id. Run 01_migrate_db.py first.")

    today = datetime.date.today()
    mods, lessons, briefs = [], [], []
    stats = collections.Counter()

    for r in rows:
        fid = r["fstem_id"]
        vert = (r["verts"] or "").split(",")[0].strip() or "general_core"
        rung = r["rung"] or "masters"
        variant_default = VARIANT_BY_VERTICAL.get(vert, "quantitative")
        sub_status = r["substrate_status"] or "live"

        pp = find_primer(a.primers, fid)
        pinfo = parse_primer(pp) if pp else {"titles": {}, "blocks": {}, "variant": None}
        stats["primer_found" if pp else "primer_missing"] += 1

        count = SPINE_MODULES.get(fid, DEFAULT_MODULES)
        if pinfo["titles"]:
            count = max(count, max(pinfo["titles"]))
        variant = pinfo.get("variant") or variant_default
        sub_status = pinfo.get("substrate_status") or sub_status

        months = 12 if vert in WINDOW_12 or sub_status == "dead" else 18
        due = add_months(today, months)

        for i in range(1, count + 1):
            mid = f"{fid}-M{i}"
            title = pinfo["titles"].get(i)
            tsrc = "primer" if title else "pending"
            if not title:
                title = "TITLE_PENDING"
                stats["title_pending"] += 1
            else:
                stats["title_from_primer"] += 1
            mods.append(dict(
                module_id=mid, parent_fstem_id=fid, asset_id=r["asset_id"],
                module_index=i, module_count=count, title=title, weeks=WEEKS,
                rung=rung, verb=VERB.get(rung, "Apply").lower(), vertical=vert,
                track_overlay="", variant=variant, substrate_course=r["code"],
                substrate_term=f"{r['term'] or ''} {r['year'] or ''}".strip(),
                substrate_block=pinfo["blocks"].get(i, ""), substrate_status=sub_status,
                refresh_date=today.isoformat(), refresh_due=due.isoformat(),
                # Course-scoped, because "FSTEM-R4-01" repeated on every course's M1 and
                # a micro-credential that is not unique cannot stack.
                micro_credential=f"{fid}-MC{i:02d}",
                status="skeleton", title_source=tsrc))
            for w in range(1, WEEKS + 1):
                lessons.append(dict(
                    lesson_id=f"{mid}-W{w}", module_id=mid, week=w,
                    topic="TOPIC_PENDING", substrate_ref=pinfo["blocks"].get(i, ""),
                    work_due="Deposit" if w == WEEKS else "TBD", status="skeleton"))
            briefs.append(dict(
                module_id=mid, parent_id=fid, module_index=i, module_count=count,
                vertical=vert, rung=rung, verb=VERB.get(rung, "Apply"),
                track_overlay=None, substrate_course=r["code"],
                substrate_term=f"{r['term'] or ''} {r['year'] or ''}".strip(),
                substrate_sessions=pinfo["blocks"].get(i, ""), substrate_status=sub_status,
                variant=variant, refresh_date=today.isoformat(), refresh_due=due.isoformat(),
                title=title, primer_path=pp,
                bibliography_seed=[dict(author=b[0], year=b[1]) for b in cx.execute(
                    "SELECT author, year FROM bibliography WHERE asset_id=? AND is_citation=1 "
                    "LIMIT 12", (r["asset_id"],)).fetchall()]))

    # write DB rows.
    # Drafted work is protected twice. DELETE spares any row whose status has moved off
    # 'skeleton', and the INSERT skips those ids as well, because INSERT OR REPLACE would
    # otherwise overwrite the drafted row it just spared.
    drafted_m = {r[0] for r in cx.execute(
        "SELECT module_id FROM modules WHERE COALESCE(status,'skeleton') != 'skeleton'")}
    drafted_l = {r[0] for r in cx.execute(
        "SELECT lesson_id FROM lessons WHERE COALESCE(status,'skeleton') != 'skeleton'")}
    cx.execute("DELETE FROM modules WHERE status='skeleton'")
    cx.execute("DELETE FROM lessons WHERE status='skeleton'")
    for m in mods:
        if m["module_id"] in drafted_m:
            m["status"] = "draft"
    mk = list(mods[0].keys()); lk = list(lessons[0].keys())
    cx.executemany(f"INSERT OR REPLACE INTO modules ({','.join(mk)}) VALUES ({','.join('?'*len(mk))})",
                   [tuple(m[k] for k in mk) for m in mods if m["module_id"] not in drafted_m])
    cx.executemany(f"INSERT OR REPLACE INTO lessons ({','.join(lk)}) VALUES ({','.join('?'*len(lk))})",
                   [tuple(l[k] for k in lk) for l in lessons if l["lesson_id"] not in drafted_l])
    cx.commit()
    if drafted_m or drafted_l:
        print(f"preserved    : {len(drafted_m)} drafted modules, {len(drafted_l)} drafted lessons")

    # write the full list
    with open(os.path.join(a.out, "MODULE_LIST.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=mk); w.writeheader(); w.writerows(mods)
    with open(os.path.join(a.out, "LESSON_LIST.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=lk); w.writeheader(); w.writerows(lessons)
    os.makedirs(os.path.join(a.out, "briefs"), exist_ok=True)
    for b in briefs:
        json.dump(b, open(os.path.join(a.out, "briefs", b["module_id"] + ".json"), "w",
                          encoding="utf-8"), indent=1)

    if a.stubs:
        os.makedirs(os.path.join(a.out, "stubs"), exist_ok=True)
        for b in briefs:
            if b["module_id"] in drafted_m:
                continue   # drafted prose lives in modules/, never overwrite it with a skeleton
            emit_stub(os.path.join(a.out, "stubs", b["module_id"] + ".md"), b)

    print(f"courses      : {len(rows)}")
    print(f"modules      : {len(mods)}")
    print(f"lessons      : {len(lessons)}")
    print(f"briefs       : {len(briefs)}")
    print(f"\nprimers found: {stats['primer_found']}   missing: {stats['primer_missing']}")
    print(f"titles from primer: {stats['title_from_primer']}   TITLE_PENDING: {stats['title_pending']}")
    print(f"\nwrote {a.out}/MODULE_LIST.csv, LESSON_LIST.csv, briefs/" + (", stubs/" if a.stubs else ""))
    by = collections.Counter(m["vertical"] for m in mods)
    print("\nmodules by vertical:")
    for k, v in by.most_common():
        print(f"  {k:20} {v}")
    return 0


def emit_stub(path, b):
    """Template-shaped skeleton. Prose sections carry DRAFT markers, never invented text."""
    t = f"""# {b['module_id']} — {b['title']}

**Module status: SKELETON · {b['refresh_date']} · batch PENDING**
**Parent:** {b['parent_id']}, TITLE_PENDING
**Module:** M{b['module_index']} of {b['module_count']} · 3 weeks
**Rung:** {'R4 masters' if b['rung']=='masters' else 'R3 undergraduate'} · verb **{b['verb']}**
**Track overlay:** none
**Substrate:** MIT OCW {b['substrate_course']}, {b['substrate_term']} · sessions {b['substrate_sessions'] or 'TBD'} · status **{b['substrate_status']}**
**Currency refreshed:** {b['refresh_date']} · **next refresh due:** {b['refresh_due']}
**Micro-credential:** FSTEM-PENDING
**Variant:** {b['variant']}

## 1. What this module teaches

DRAFT_PENDING

## 2. Substrate

DRAFT_PENDING

## 3. Currency

DRAFT_PENDING

## 4. Sessions

| Session | Topic | Substrate reference | Work due |
|---|---|---|---|
| Week 1 | TOPIC_PENDING | {b['substrate_sessions'] or 'TBD'} | TBD |
| Week 2 | TOPIC_PENDING | {b['substrate_sessions'] or 'TBD'} | TBD |
| Week 3 | TOPIC_PENDING | {b['substrate_sessions'] or 'TBD'} | Deposit |

## 5. The deposit

**Claim.** DRAFT_PENDING

**Evidence.** DRAFT_PENDING

**Completion criterion.** DRAFT_PENDING

**Re-run.** DRAFT_PENDING

## 6. Readings

DRAFT_PENDING

## 7. Plus layer

**Currency slot:** DRAFT_PENDING
**Patent hook:** DRAFT_PENDING
**Research thread:** DRAFT_PENDING
**Doctrine-to-code artifact:** DRAFT_PENDING
**Red Cell target:** DRAFT_PENDING
**Sponsor slot:** DRAFT_PENDING
**Micro-credential:** DRAFT_PENDING
**Reproducibility deposit:** required

## 8. Provenance

DRAFT_PENDING
"""
    open(path, "w", encoding="utf-8").write(t)


if __name__ == "__main__":
    sys.exit(main())
