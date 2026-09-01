#!/usr/bin/env python3
"""
FSTEM full write-out. Renders the module and lesson tables as a readable master list.

    python3 04_writeout.py --db out/fstem.db --out build/
"""
import argparse, collections, os, sqlite3, sys, datetime

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default="build")
    ap.add_argument("--rung", default=None, choices=["undergraduate", "masters"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cx = sqlite3.connect(a.db); cx.row_factory = sqlite3.Row
    q = ("SELECT m.*, s.title AS course_title, s.year FROM modules m "
         "LEFT JOIN assets s ON s.asset_id = m.asset_id")
    if a.rung:
        q += f" WHERE m.rung = '{a.rung}'"
    q += " ORDER BY m.vertical, m.parent_fstem_id, m.module_index"
    rows = [dict(r) for r in cx.execute(q)]
    if not rows:
        sys.exit("no modules. Run 02_build_modules.py first.")
    les = collections.defaultdict(list)
    for r in cx.execute("SELECT * FROM lessons ORDER BY module_id, week"):
        les[r["module_id"]].append(dict(r))

    by_v = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by_v[r["vertical"]][r["parent_fstem_id"]].append(r)

    L = []
    W = L.append
    today = datetime.date.today().isoformat()
    W(f"# FSTEM Module Master List\n")
    W(f"**Generated {today} from the module and lesson tables.** Regenerate with `04_writeout.py`.\n")
    W(f"**{len(rows)} modules · {sum(len(v) for v in les.values())} lessons · "
      f"{len({r['parent_fstem_id'] for r in rows})} courses · {len(by_v)} verticals**\n")

    st = collections.Counter(r["substrate_status"] for r in rows)
    ts = collections.Counter(r["title_source"] for r in rows)
    W("| Measure | Count |\n|---|---:|")
    for k, v in sorted(st.items()):
        W(f"| substrate `{k}` | {v} |")
    for k, v in sorted(ts.items()):
        W(f"| title from {k} | {v} |")
    W("")

    W("## Contents\n")
    for v in sorted(by_v):
        n = sum(len(x) for x in by_v[v].values())
        W(f"- [{v}](#{v.replace('_','-')}) · {len(by_v[v])} courses · {n} modules")
    W("")

    for v in sorted(by_v):
        W(f"\n---\n\n# {v}\n")
        for fid in sorted(by_v[v]):
            ms = by_v[v][fid]
            m0 = ms[0]
            W(f"\n## {fid} · {m0['course_title'] or 'TITLE_PENDING'}\n")
            W(f"`{m0['substrate_course']}` · {m0['substrate_term'] or 'term unknown'} · "
              f"{m0['rung']} · verb {m0['verb']} · variant {m0['variant']} · "
              f"substrate **{m0['substrate_status']}** · refresh due {m0['refresh_due']}\n")
            W("| Module | Title | Source block | Weeks | Status |")
            W("|---|---|---|---|---|")
            for m in ms:
                W(f"| M{m['module_index']}/{m['module_count']} | {m['title']} | "
                  f"{m['substrate_block'] or '_pending_'} | {m['weeks']} | {m['status']} |")
            for m in ms:
                ll = les.get(m["module_id"], [])
                if not ll:
                    continue
                W(f"\n**{m['module_id']}** lessons: " +
                  " · ".join(f"W{l['week']} {l['topic']}" for l in ll))
            W("")

    try:
        ovl = [dict(r) for r in cx.execute(
            "SELECT o.*, t.name AS tname FROM module_overlays o "
            "LEFT JOIN tracks t ON t.track_id = o.track_id "
            "ORDER BY o.track_id, o.parent_fstem_id, o.module_index")]
    except sqlite3.OperationalError:
        ovl = []
    if ovl:
        W("\n---\n\n# Track overlays\n")
        W(f"**{len(ovl)} overlay modules across "
          f"{len({o['track_id'] for o in ovl})} tracks.** An overlay belongs to one track "
          "rather than to every student taking the parent course, so it lives in "
          "`module_overlays` rather than in `modules`.\n")
        W("| Track | Overlay | Title | Parent | Substrate | Variant | Refresh due |")
        W("|---|---|---|---|---|---|---|")
        for o in ovl:
            W(f"| {o['track_id']} {o['tname'] or ''} | {o['overlay_id']} | {o['title']} | "
              f"{o['parent_fstem_id']} | `{o['substrate_course']}` {o['substrate_status']} | "
              f"{o['variant']} | {o['refresh_due']} |")
        W("")

    p = os.path.join(a.out, "MODULE_MASTER_LIST.md")
    open(p, "w", encoding="utf-8").write("\n".join(L))
    print(f"wrote {p}")
    print(f"  {len(rows)} modules, {sum(len(v) for v in les.values())} lessons, "
          f"{len(by_v)} verticals, {len(ovl)} track overlays")
    return 0


if __name__ == "__main__":
    sys.exit(main())
