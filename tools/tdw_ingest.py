#!/usr/bin/env python3
"""
Parse the two sources of record into a structured corpus.

    python3 tools/tdw_ingest.py            # writes build/fstem_m7.db, build/tdw_corpus.json,
                                           # build/ingest_report.md

Reads KDP_MASTER.md and PRIMER_MASTER.md from the repo root. Never writes to them.
Every row carries source_file, start_line, end_line (1-based, inclusive) and the
source file's sha256. A stored span is the source lines joined with "\\n", so
text == "\\n".join(lines[start-1:end]) holds byte for byte.
"""
import hashlib, json, os, re, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
SOURCES = {"KDP": "KDP_MASTER.md", "PRIMER": "PRIMER_MASTER.md"}

HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*$')
ATTRS = re.compile(r'\s*\{[^}]*\}\s*$')
NOTE = re.compile(r'^\*\*\\\[(\d+)\\\]\*\*')
REF = re.compile(r'\\\[(\d+)\\\]')
CLAIM = re.compile(r'^\*\*(Documented|Argued)([^*]*)\*\*')
CARD_FIELDS = [("what_happened", "What happened."), ("the_gap", "The gap."),
               ("the_fix", "The fix."), ("where_to_work", "Where you could work on it.")]
ROMAN = ["I", "II", "III", "IV", "V", "VI"]

PROV = "source_file TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, sha256 TEXT NOT NULL"
TABLES = {
    "tdw_documents": "doc_id TEXT PRIMARY KEY, title TEXT, subtitle TEXT, edition TEXT, author TEXT, front_matter TEXT",
    "tdw_sections": "section_id TEXT PRIMARY KEY, doc_id TEXT, level INTEGER, heading TEXT, title TEXT, "
                    "parent_id TEXT, chapter_id TEXT, part TEXT, is_case_chapter INTEGER, text TEXT",
    "tdw_claims": "claim_id TEXT PRIMARY KEY, doc_id TEXT, label TEXT, label_text TEXT, chapter_id TEXT, "
                  "chapter TEXT, section_id TEXT, note_scope TEXT, note_refs TEXT, text TEXT",
    "tdw_notes": "note_id TEXT PRIMARY KEY, doc_id TEXT, note_num INTEGER, note_scope TEXT, chapter TEXT, "
                 "section_id TEXT, text TEXT",
    "tdw_field_cards": "card_id TEXT PRIMARY KEY, doc_id TEXT, chapter_id TEXT, chapter TEXT, is_compilation INTEGER, "
                       "what_happened TEXT, the_gap TEXT, the_fix TEXT, where_to_work TEXT, text TEXT",
    "tdw_exercises": "exercise_id TEXT PRIMARY KEY, doc_id TEXT, chapter_id TEXT, chapter TEXT, text TEXT",
    "tdw_graphic_specs": "spec_id TEXT PRIMARY KEY, doc_id TEXT, chapter_id TEXT, chapter TEXT, text TEXT",
    "tdw_stratagems": "stratagem_id TEXT PRIMARY KEY, number INTEGER, name TEXT, group_num INTEGER, "
                      "group_roman TEXT, group_name TEXT, is_anchor INTEGER, start_col INTEGER, end_col INTEGER, "
                      "entry_text TEXT, text TEXT",
    "tdw_dimensions": "dimension_id TEXT PRIMARY KEY, number INTEGER, name TEXT, appendix_a_start INTEGER, "
                      "appendix_a_end INTEGER, text TEXT",
    "tdw_domains": "domain_id TEXT PRIMARY KEY, number INTEGER, name TEXT, text TEXT",
    "tdw_crosswalk": "row_id TEXT PRIMARY KEY, dimension TEXT, principle_of_war TEXT, function TEXT, fit TEXT, text TEXT",
    "tdw_roadmap": "row_id TEXT PRIMARY KEY, horizon TEXT, seq INTEGER, element TEXT, owner TEXT, cost TEXT, "
                   "from_chapter TEXT, text TEXT",
}


class Doc:
    def __init__(self, doc_id, path):
        self.id, self.path = doc_id, path
        raw = open(path, "rb").read()
        self.sha = hashlib.sha256(raw).hexdigest()
        self.lines = raw.decode("utf-8").split("\n")
        self.file = os.path.basename(path)

    def span(self, s, e):
        return "\n".join(self.lines[s - 1:e])

    def prov(self, s, e):
        return {"source_file": self.file, "start_line": s, "end_line": e, "sha256": self.sha}

    def para_end(self, s):
        """Last line of the paragraph that starts at line s."""
        e = s
        while e < len(self.lines) and self.lines[e].strip() and not HEADING.match(self.lines[e]):
            e += 1
        return e

    def trim(self, s, e):
        while e > s and not self.lines[e - 1].strip():
            e -= 1
        return e


def front_matter(doc):
    if doc.lines[0].strip() != "---":
        return {}, 0
    end = doc.lines.index("---", 1) + 1
    fm = {}
    for ln in doc.lines[1:end - 1]:
        m = re.match(r'^([\w-]+):\s*"?(.*?)"?\s*$', ln)
        if m:
            fm[m.group(1)] = m.group(2)
    return fm, end


def sections(doc, start):
    heads, fence = [], False
    for i in range(start, len(doc.lines)):
        ln = doc.lines[i]
        if ln.startswith("```"):
            fence = not fence
        m = None if fence else HEADING.match(ln)
        if m:
            heads.append((i + 1, len(m.group(1)), m.group(2)))
    out, stack, part, chapter = [], [], None, None
    for k, (ln, lvl, head) in enumerate(heads):
        nxt = next((h[0] for h in heads[k + 1:] if h[1] <= lvl), len(doc.lines) + 1)
        end = doc.trim(ln, nxt - 1)
        while stack and stack[-1][1] >= lvl:
            stack.pop()
        sid = f"{doc.id}-L{ln}"
        title = ATTRS.sub("", head).strip()
        if lvl == 1:
            part, chapter = title, None
        if lvl == 2:
            chapter = sid
        out.append(dict(section_id=sid, doc_id=doc.id, level=lvl, heading=head, title=title,
                        parent_id=stack[-1][0] if stack else None,
                        chapter_id=chapter if lvl >= 2 else None, part=part, is_case_chapter=0,
                        text=doc.span(ln, end), **doc.prov(ln, end)))
        stack.append((sid, lvl))
    kids = {}
    for s in out:
        kids.setdefault(s["parent_id"], []).append(s)
    for s in out:
        if s["level"] == 2 and any(c["title"] == "The Terms of This Case" for c in kids.get(s["section_id"], [])):
            s["is_case_chapter"] = 1
        if doc.id == "PRIMER" and s["level"] == 2 and re.match(r'Chapter \d+\.', s["title"]):
            s["is_case_chapter"] = 1
    return out


def h_scope_map(secs):
    """Map each Appendix H subsection to the case chapter whose notes it holds."""
    chapters = [s for s in secs if s["level"] == 2 and s["is_case_chapter"]]
    apph = next((s for s in secs if s["level"] == 2 and s["title"].startswith("Appendix H")), None)
    out, fails = {}, []
    if not apph:
        return out, fails
    for s in secs:
        if s["parent_id"] != apph["section_id"] or s["level"] != 3:
            continue
        m = re.match(r'^(.*?)(?:\s*\((.*)\))?$', s["title"])
        keys = [k for k in (m.group(1), m.group(2)) if k]
        hit = [c for c in chapters if any(c["title"] == k or c["title"].startswith(k + ":") for k in keys)]
        if len(hit) == 1:
            out[s["section_id"]] = hit[0]
        else:
            fails.append(f"Appendix H section '{s['title']}' (line {s['start_line']}) matched {len(hit)} chapters")
    return out, fails


def locate(secs, line):
    """Innermost section, chapter (level 2) and level-3 section containing a line."""
    inner = ch = l3 = None
    for s in secs:
        if s["start_line"] <= line <= max(s["end_line"], s["start_line"]):
            if s["level"] == 2:
                ch = s
            if s["level"] == 3:
                l3 = s
            if inner is None or s["level"] >= inner["level"]:
                inner = s
    return inner, ch, l3


def ingest(doc):
    fm, body = front_matter(doc)
    secs = sections(doc, body)
    hmap, fails = h_scope_map(secs)
    by_id = {s["section_id"]: s for s in secs}

    def scope(line):
        inner, ch, l3 = locate(secs, line)
        if l3 and l3["section_id"] in hmap:
            return inner, hmap[l3["section_id"]]
        return inner, ch

    R = {k: [] for k in TABLES}
    R["tdw_documents"].append(dict(doc_id=doc.id, title=fm.get("title"), subtitle=fm.get("subtitle"),
                                   edition=fm.get("edition"), author=fm.get("author"),
                                   front_matter=json.dumps(fm), **doc.prov(1, len(doc.lines))))
    R["tdw_sections"] = secs

    seen_notes = {}
    for i, ln in enumerate(doc.lines, 1):
        m = NOTE.match(ln)
        if m:
            e = doc.para_end(i)
            inner, sc = scope(i)
            sc_id = sc["section_id"] if sc else "NONE"
            n = int(m.group(1))
            nid = f"{sc_id}-N{n}"
            if nid in seen_notes:
                fails.append(f"duplicate note [{n}] in {sc['title'] if sc else '?'} at lines "
                             f"{seen_notes[nid]} and {i}")
                nid = f"{nid}-dup{i}"
            seen_notes[nid] = i
            R["tdw_notes"].append(dict(note_id=nid, doc_id=doc.id, note_num=n, note_scope=sc_id,
                                       chapter=sc["title"] if sc else None,
                                       section_id=inner["section_id"] if inner else None,
                                       text=doc.span(i, e), **doc.prov(i, e)))
        m = CLAIM.match(ln)
        if m:
            e = doc.para_end(i)
            inner, sc = scope(i)
            _, ch, _ = locate(secs, i)
            text = doc.span(i, e)
            R["tdw_claims"].append(dict(
                claim_id=f"{doc.id}-C{i}", doc_id=doc.id, label=m.group(1),
                label_text=(m.group(1) + m.group(2)).strip(), chapter_id=ch["section_id"] if ch else None,
                chapter=ch["title"] if ch else None, section_id=inner["section_id"] if inner else None,
                note_scope=sc["section_id"] if sc else None,
                note_refs=json.dumps([int(x) for x in REF.findall(text)]), text=text, **doc.prov(i, e)))

    def chapter_of(s):
        c = by_id.get(s["chapter_id"])
        return (c["section_id"], c["title"]) if c else (None, None)

    for s in secs:
        cid, ctitle = chapter_of(s)
        if s["level"] == 3 and s["title"] == "Field Card":
            R["tdw_field_cards"].append(card(doc, s, f"{doc.id}-FC-L{s['start_line']}", cid, ctitle, 0, fails))
        if s["level"] == 3 and s["title"] == "Exercise":
            R["tdw_exercises"].append(dict(exercise_id=f"{doc.id}-EX-L{s['start_line']}", doc_id=doc.id,
                                           chapter_id=cid, chapter=ctitle, text=s["text"],
                                           **doc.prov(s["start_line"], s["end_line"])))
        if s["level"] == 3 and s["title"] == "Graphic Specification":
            R["tdw_graphic_specs"].append(dict(spec_id=f"{doc.id}-GS-L{s['start_line']}", doc_id=doc.id,
                                               chapter_id=cid, chapter=ctitle, text=s["text"],
                                               **doc.prov(s["start_line"], s["end_line"])))
        if s["level"] == 2 and s["title"].startswith("All Twelve Field Cards"):
            compilation_cards(doc, s, R, fails)

    if doc.id == "KDP":
        stratagems(doc, secs, R, fails)
        dims_domains(doc, secs, R, fails)
        crosswalk(doc, secs, R, fails)
        roadmap(doc, secs, R, fails)
    return R, fails


def card_fields(doc, s, e):
    out = {}
    for k, label in CARD_FIELDS:
        for i in range(s, e + 1):
            if doc.lines[i - 1].startswith(f"**{label}**"):
                pe = doc.para_end(i)
                out[k] = doc.span(i, pe)[len(label) + 4:].strip()
                break
    return out


def card(doc, s, cid_, cid, ctitle, comp, fails):
    f = card_fields(doc, s["start_line"], s["end_line"])
    miss = [k for k, _ in CARD_FIELDS if k not in f]
    if miss:
        fails.append(f"field card {cid_} ({ctitle}) missing {', '.join(miss)}")
    return dict(card_id=cid_, doc_id=doc.id, chapter_id=cid, chapter=ctitle, is_compilation=comp,
                text=s["text"], **{k: f.get(k) for k, _ in CARD_FIELDS},
                **doc.prov(s["start_line"], s["end_line"]))


def compilation_cards(doc, sec, R, fails):
    starts = [i for i in range(sec["start_line"], sec["end_line"] + 1)
              if re.match(r'^\*\*Chapter \d+\. .*\*\*\s*$', doc.lines[i - 1])]
    for k, st in enumerate(starts):
        en = doc.trim(st, (starts[k + 1] - 1) if k + 1 < len(starts) else sec["end_line"])
        title = doc.lines[st - 1].strip("* ")
        pseudo = dict(start_line=st, end_line=en, text=doc.span(st, en))
        R["tdw_field_cards"].append(card(doc, pseudo, f"{doc.id}-FC-L{st}", sec["section_id"], title, 1, fails))


def child(secs, parent_prefix, title_prefix):
    par = next((s for s in secs if s["level"] == 2 and s["title"].startswith(parent_prefix)), None)
    if not par:
        return None, []
    kids = [s for s in secs if s["parent_id"] == par["section_id"] and s["title"].startswith(title_prefix)]
    return par, kids


def stratagems(doc, secs, R, fails):
    par, groups = child(secs, "Appendix B", "Group ")
    seen = {}
    for g in groups:
        gm = re.match(r'Group (\w+)\. (.*)', g["title"])
        for i in range(g["start_line"], g["end_line"] + 1):
            ln = doc.lines[i - 1]
            m = re.match(r'^\*\*Stratagem (\d+)\. (.+?)\.?\*\*', ln)
            if m:
                e = doc.para_end(i)
                seen[int(m.group(1))] = (m.group(2), gm, 1, i, e, 0, len(doc.lines[e - 1]) if e == i else None)
            if ln.startswith("The rest of the group."):
                ms = list(re.finditer(r'\*\*(\d+)\. (.+?)\.\*\*', ln))
                for k, m2 in enumerate(ms):
                    end = ms[k + 1].start() if k + 1 < len(ms) else len(ln)
                    while end > m2.start() and ln[end - 1] == " ":
                        end -= 1
                    seen[int(m2.group(1))] = (m2.group(2), gm, 0, i, i, m2.start(), end)
    for n in sorted(seen):
        name, gm, anchor, s, e, c0, c1 = seen[n]
        gnum = (n - 1) // 6 + 1
        if ROMAN[gnum - 1] != gm.group(1):
            fails.append(f"stratagem {n} sits under Group {gm.group(1)}, numbering says Group {ROMAN[gnum - 1]}")
        span = doc.span(s, e)
        entry = span if anchor else doc.lines[s - 1][c0:c1]
        R["tdw_stratagems"].append(dict(
            stratagem_id=f"TDW-S{n:02d}", number=n, name=name, group_num=gnum, group_roman=gm.group(1),
            group_name=gm.group(2), is_anchor=anchor, start_col=None if anchor else c0,
            end_col=None if anchor else c1, entry_text=entry, text=span, **doc.prov(s, e)))
    missing = sorted(set(range(1, 37)) - set(seen))
    if missing:
        fails.append(f"stratagems not found: {missing}")


def numbered(doc, sec, R, table, idkey, prefix):
    for i in range(sec["start_line"], sec["end_line"] + 1):
        m = re.match(r'^\*\*(\d+)\. (.+?)\.\*\*', doc.lines[i - 1])
        if m:
            e = doc.para_end(i)
            n = int(m.group(1))
            R[table].append({idkey: f"{prefix}{n:02d}", "number": n, "name": m.group(2),
                             "text": doc.span(i, e), **doc.prov(i, e)})


def dims_domains(doc, secs, R, fails):
    _, d = child(secs, "Appendix C", "The Ten Dimensions")
    _, m = child(secs, "Appendix C", "The Twelve Domains")
    if d:
        numbered(doc, d[0], R, "tdw_dimensions", "dimension_id", "TDW-DIM")
    if m:
        numbered(doc, m[0], R, "tdw_domains", "domain_id", "TDW-DOM")
    _, pt = child(secs, "Appendix A", "The Principles Table")
    subs = [s for s in secs if pt and s["parent_id"] == pt[0]["section_id"]]
    for row in R["tdw_dimensions"]:
        hit = next((s for s in subs if s["title"] == row["name"]), None)
        row["appendix_a_start"] = hit["start_line"] if hit else None
        row["appendix_a_end"] = hit["end_line"] if hit else None
        if not hit:
            fails.append(f"dimension {row['name']} has no Appendix A principles entry")


def table_rows(doc, sec):
    rows, header = [], None
    for i in range(sec["start_line"], sec["end_line"] + 1):
        ln = doc.lines[i - 1]
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(re.fullmatch(r':?-+:?', c) for c in cells):
            continue
        if header is None:
            header = cells
            continue
        rows.append((i, cells))
    return header, rows


def crosswalk(doc, secs, R, fails):
    _, cw = child(secs, "Appendix D", "The Crosswalk")
    if not cw:
        fails.append("Appendix D crosswalk table not found")
        return
    hdr, rows = table_rows(doc, cw[0])
    for i, c in rows:
        if len(c) != 4:
            fails.append(f"crosswalk row at line {i} has {len(c)} cells")
            continue
        R["tdw_crosswalk"].append(dict(row_id=f"TDW-XW{len(R['tdw_crosswalk']) + 1:02d}", dimension=c[0],
                                       principle_of_war=c[1], function=c[2], fit=c[3],
                                       text=doc.span(i, i), **doc.prov(i, i)))


def roadmap(doc, secs, R, fails):
    for h in ("Now", "Next", "Later"):
        _, s = child(secs, "Appendix F", h + ":")
        if not s:
            fails.append(f"Appendix F horizon {h} not found")
            continue
        hdr, rows = table_rows(doc, s[0])
        if hdr != ["Element", "Owner", "Cost", "From"]:
            fails.append(f"Appendix F {h} header reads {hdr}")
        for k, (i, c) in enumerate(rows, 1):
            if len(c) != 4:
                fails.append(f"roadmap {h} row at line {i} has {len(c)} cells")
                continue
            R["tdw_roadmap"].append(dict(row_id=f"TDW-RM-{h.upper()}-{k:02d}", horizon=h, seq=k, element=c[0],
                                         owner=c[1], cost=c[2], from_chapter=c[3],
                                         text=doc.span(i, i), **doc.prov(i, i)))


def unresolved(rows):
    notes = {(n["note_scope"], n["note_num"]) for n in rows["tdw_notes"]}
    out = []
    for c in rows["tdw_claims"]:
        for r in json.loads(c["note_refs"]):
            if (c["note_scope"], r) not in notes:
                out.append((c, r))
    return out


def body_refs(rows, docs):
    """Refs in non-claim, non-note lines that do not resolve in their chapter's note scope."""
    notes = {(n["source_file"], n["note_scope"], n["note_num"]) for n in rows["tdw_notes"]}
    taken = {(r["source_file"], i) for t in ("tdw_claims", "tdw_notes")
             for r in rows[t] for i in range(r["start_line"], r["end_line"] + 1)}
    out = []
    for d in docs.values():
        secs = [s for s in rows["tdw_sections"] if s["source_file"] == d.file]
        hmap, _ = h_scope_map(secs)
        for i, ln in enumerate(d.lines, 1):
            if (d.file, i) in taken or ln.startswith("|"):
                continue
            refs = REF.findall(ln)
            if not refs:
                continue
            _, ch, l3 = locate(secs, i)
            sc = hmap.get(l3["section_id"]) if l3 else None
            sc = sc or ch
            for r in refs:
                if (d.file, sc["section_id"] if sc else None, int(r)) not in notes:
                    out.append((d.file, i, sc["title"] if sc else None, r))
    return out


def write_db(path, rows):
    if os.path.exists(path):
        os.remove(path)
    cx = sqlite3.connect(path)
    for t, cols in TABLES.items():
        cx.execute(f"CREATE TABLE {t} ({cols}, {PROV})")
        for r in rows[t]:
            keys = list(r)
            cx.execute(f"INSERT INTO {t} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                       [r[k] for k in keys])
    cx.commit()
    cx.close()


def report(path, rows, fails, docs):
    un = unresolved(rows)
    L = ["# TDW ingest report", ""]
    for d in docs.values():
        L.append(f"- `{d.file}` sha256 `{d.sha}`, {len(d.lines)} lines")
    L += ["", "## Row counts", "", "| Table | KDP | PRIMER | Total |", "|---|---|---|---|"]
    for t in TABLES:
        k = sum(1 for r in rows[t] if r.get("source_file") == "KDP_MASTER.md")
        p = sum(1 for r in rows[t] if r.get("source_file") == "PRIMER_MASTER.md")
        L.append(f"| {t} | {k} | {p} | {k + p} |")
    L += ["", f"## Unresolved note refs ({len(un)})", "",
          "A claim cites [n] and no note [n] exists in the same chapter's note scope. Listed, not fixed.", ""]
    if un:
        L += ["| Source | Line | Chapter | Label | Ref |", "|---|---|---|---|---|"]
        for c, r in un:
            L.append(f"| {c['source_file']} | {c['start_line']} | {c['chapter']} | {c['label']} | [{r}] |")
    else:
        L.append("None.")
    body = body_refs(rows, docs)
    L += ["", f"## Unresolved refs outside claim paragraphs ({len(body)})", "",
          "Informational. A plain paragraph cites [n] and its chapter holds no note [n].", ""]
    if body:
        L += ["| Source | Line | Chapter | Ref |", "|---|---|---|---|"]
        L += [f"| {f} | {i} | {ch} | [{r}] |" for f, i, ch, r in body]
    else:
        L.append("None.")
    L += ["", f"## Parse failures and anomalies ({len(fails)})", ""]
    L += [f"- {f}" for f in fails] or ["None."]
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return un


def main():
    os.makedirs(BUILD, exist_ok=True)
    docs = {k: Doc(k, os.path.join(ROOT, v)) for k, v in SOURCES.items()}
    before = {k: d.sha for k, d in docs.items()}
    rows, fails = {t: [] for t in TABLES}, []
    for d in docs.values():
        r, f = ingest(d)
        for t in TABLES:
            rows[t] += r[t]
        fails += [f"{d.file}: {x}" for x in f]
    write_db(os.path.join(BUILD, "fstem_m7.db"), rows)
    json.dump(rows, open(os.path.join(BUILD, "tdw_corpus.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    un = report(os.path.join(BUILD, "ingest_report.md"), rows, fails, docs)
    after = {k: Doc(k, os.path.join(ROOT, v)).sha for k, v in SOURCES.items()}
    assert before == after, "a source of record changed during ingest"
    for t in TABLES:
        print(f"{t:<20}{len(rows[t]):>6}")
    print(f"unresolved note refs: {len(un)}   parse failures: {len(fails)}")
    print("wrote build/fstem_m7.db, build/tdw_corpus.json, build/ingest_report.md")


if __name__ == "__main__":
    sys.exit(main())
