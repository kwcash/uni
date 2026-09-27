#!/usr/bin/env python3
"""
fstem_lessons_from_guides.py  ·  fill `lessons` from section 4 of each guide

  python3 fstem_lessons_from_guides.py --db ../out/fstem.db --modules modules/          # report only
  python3 fstem_lessons_from_guides.py --db ../out/fstem.db --modules modules/ --apply  # write, logged to repair_log

Reads two forms of section 4.
  table   | Week 1 | topic | substrate reference | work due |     (header may say Session or Week)
  prose   **Week 1** · topic · topic                             (no reference or work-due; existing values kept)
  fallback  **Week 1 does something.**                            (topic = the bold sentence)

Writes one row per week: lesson_id {module_id}-W{n}, topic, substrate_ref, work_due, status 'draft'.
Overlays get rows keyed on their overlay_id. Every write is logged. Nothing is written without --apply.
"""
import argparse, os, re, sqlite3, datetime
from collections import Counter

NOW = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ROW = re.compile(r"^\|\s*(?:Week|Session|W)?\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")
PROSE = re.compile(r"^\*\*Week (\d+)\*\*\s*[·:.\-–]\s*(.+?)\s*$")
TITLED = re.compile(r"^\*\*Week (\d+)\.\s*(.+?)\*\*\s*(.*)$")
DOTBOLD = re.compile(r"^\*\*Week (\d+)\.\*\*\s*(.+?)\s*$")
WORKDUE = re.compile(r"\s*Work due:\s*(.+?)\.?\s*$")
BOLD = re.compile(r"^\*\*Week (\d+)\s+(.+?)\*\*")


def section4(lines):
    s = e = None
    for i, l in enumerate(lines):
        if re.match(r"^##\s+4\.", l):
            s = i
        elif s is not None and re.match(r"^##\s+\d+\.", l):
            e = i
            break
    return (s, e if e is not None else len(lines)) if s is not None else None


def parse(lines):
    """-> (form, {week: (topic, ref, work)}) ; ref/work None when the form lacks them"""
    b = section4(lines)
    if b is None:
        return "no-section", {}
    body = lines[b[0] + 1:b[1]]
    weeks = {}
    for l in body:
        m = ROW.match(l)
        if m and not set(m.group(2)) <= set("-: "):
            w = int(m.group(1))
            if w not in weeks:
                weeks[w] = (m.group(2).strip(), m.group(3).strip() or None, m.group(4).strip() or None)
    if weeks:
        return "table", weeks
    for l in body:
        m = TITLED.match(l)
        if m:
            w, title, tail = int(m.group(1)), m.group(2).rstrip("."), m.group(3)
            work = None
            mw = WORKDUE.search(tail)
            if mw:
                work = mw.group(1).strip()
                work = work[0].upper() + work[1:]
                tail = tail[:mw.start()]
            ref = None
            ms = re.match(r"\s*Substrate\s+(.+?)\.\s*(.*)$", tail)
            if ms:
                ref, tail = ms.group(1).strip(), ms.group(2)
            topics = tail.strip().rstrip(".")
            topic = f"{title} · {topics}" if topics else title
            if w not in weeks:
                weeks[w] = (topic, ref, work)
    if weeks:
        return "titled", weeks
    for l in body:
        m = DOTBOLD.match(l)
        if m:
            w = int(m.group(1))
            if w not in weeks:
                weeks[w] = (m.group(2).rstrip("."), None, None)
    if weeks:
        return "dotbold", weeks
    for l in body:
        m = PROSE.match(l)
        if m:
            w = int(m.group(1))
            if w not in weeks:
                weeks[w] = (m.group(2).rstrip("."), None, None)
    if weeks:
        return "prose", weeks
    for l in body:
        m = BOLD.match(l)
        if m:
            w = int(m.group(1))
            if w not in weeks:
                weeks[w] = (m.group(2).rstrip("."), None, None)
    return ("bold", weeks) if weeks else ("unparsed", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--modules", default="modules")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(lessons)")]
    need = {"lesson_id", "module_id", "week", "topic", "substrate_ref", "work_due", "status"}
    if not need <= set(cols):
        raise SystemExit(f"lessons lacks {sorted(need - set(cols))}; has {cols}")
    if a.apply:
        con.execute("""CREATE TABLE IF NOT EXISTS repair_log (
            applied_at TEXT, step TEXT, tbl TEXT, key TEXT, col TEXT, old TEXT, new TEXT)""")

    targets = [(r[0], r[1]) for r in con.execute("SELECT module_id, weeks FROM modules WHERE status='draft'")]
    targets += [(r[0], 3) for r in con.execute("SELECT overlay_id FROM module_overlays")]

    forms, problems, written, per_guide = Counter(), [], 0, []
    for mid, nweeks in sorted(targets):
        path = os.path.join(a.modules, f"{mid}.md")
        if not os.path.exists(path):
            problems.append((mid, "guide file not found")); continue
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        form, weeks = parse(lines)
        forms[form] += 1
        per_guide.append((mid, form, len(weeks), ' / '.join(weeks[w][0][:40] for w in sorted(weeks))))
        if not weeks:
            problems.append((mid, f"section 4 {form}")); continue
        if sorted(weeks) != list(range(1, (nweeks or 3) + 1)):
            problems.append((mid, f"weeks found {sorted(weeks)}, expected 1..{nweeks or 3} ({form})"))
        for w, (topic, ref, work) in sorted(weeks.items()):
            if not topic:
                problems.append((mid, f"week {w} has an empty topic")); continue
            lid = f"{mid}-W{w}"
            cur = con.execute("SELECT topic, substrate_ref, work_due, status FROM lessons WHERE lesson_id=?", (lid,)).fetchone()
            new_ref = ref if ref is not None else (cur[1] if cur else None)
            new_work = work if work is not None else (cur[2] if cur else None)
            if cur and (cur[0], cur[1], cur[2], cur[3]) == (topic, new_ref, new_work, "draft"):
                continue
            written += 1
            if a.apply:
                if cur:
                    con.execute("UPDATE lessons SET topic=?, substrate_ref=?, work_due=?, status='draft' WHERE lesson_id=?",
                                (topic, new_ref, new_work, lid))
                else:
                    con.execute("INSERT INTO lessons(lesson_id, module_id, week, topic, substrate_ref, work_due, status) VALUES (?,?,?,?,?,?,'draft')",
                                (lid, mid, w, topic, new_ref, new_work))
                con.execute("INSERT INTO repair_log VALUES (?,?,?,?,?,?,?)",
                            (NOW, "lessons_from_guide", "lessons", lid, "topic|substrate_ref|work_due|status",
                             "|".join(str(x) for x in cur) if cur else None,
                             f"{topic}|{new_ref}|{new_work}|draft"))
    if a.apply:
        con.commit()
    os.makedirs('audit_out', exist_ok=True)
    import csv
    with open('audit_out/lessons_forms.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['module_id', 'form', 'weeks_found', 'topics']); w.writerows(per_guide)

    print(f"{'APPLY' if a.apply else 'DRY RUN'}: {len(targets)} guides, {written} lesson rows {'written' if a.apply else 'to write'}")
    print("forms:", dict(forms))
    print(f"problems: {len(problems)}")
    for mid, p in problems[:40]:
        print(f"  {mid}: {p}")
    if len(problems) > 40:
        print(f"  ... {len(problems) - 40} more")
    q = lambda s: con.execute(s).fetchone()[0]
    print(f"lessons under drafted modules still skeleton: "
          f"{q('SELECT COUNT(*) FROM lessons l JOIN modules m ON m.module_id=l.module_id WHERE m.status=\"draft\" AND l.status=\"skeleton\"')}")
    print(f"overlay lesson rows: {q('SELECT COUNT(*) FROM lessons WHERE module_id LIKE \"FSTEM-AI-SPINE-001-M8-%m_\"')}")


if __name__ == "__main__":
    main()
