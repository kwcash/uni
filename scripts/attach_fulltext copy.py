#!/usr/bin/env python3
"""Attach a fulltext table to out/fstem.db, joined to assets by course code.
Filename convention: 14.01-fall-2023.txt or 14-01-fall-2007.txt (old format).
Run AFTER each go.sh, since go.sh rebuilds the db and drops this table."""

import os
import re
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "..", "out", "fstem.db")
TEXT_ROOT = os.path.expanduser("~/Projects/corpus/text")

TERM_RE = re.compile(
    r"^(?P<stem>.+?)-(?P<term>fall|spring|summer|january-iap|iap)-?(?P<year>\d{4})?$",
    re.IGNORECASE)

def parse_name(name):
    """'14.01-fall-2023' -> ('14.01', 'Fall', 2023). Old '14-01-...' -> '14.01'."""
    m = TERM_RE.match(name)
    stem, term, year = (m.group("stem"), m.group("term"), m.group("year")) if m \
                       else (name, None, None)
    # old format: first hyphen in the stem is really a dot (14-01 -> 14.01)
    if "." not in stem and "-" in stem:
        stem = stem.replace("-", ".", 1)
    code = stem.upper()
    term = term.title() if term else None
    year = int(year) if year else None
    return code, term, year

con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute("DROP TABLE IF EXISTS fulltext")
cur.execute("""CREATE TABLE fulltext (
    code TEXT, term TEXT, year INTEGER,
    folder TEXT, repo TEXT, txt_path TEXT, size_kb INTEGER)""")

n = 0
for repo in sorted(os.listdir(TEXT_ROOT)):
    rdir = os.path.join(TEXT_ROOT, repo)
    if not os.path.isdir(rdir):
        continue
    for f in sorted(os.listdir(rdir)):
        if not f.endswith(".txt"):
            continue
        folder = f[:-4]
        code, term, year = parse_name(folder)
        path = os.path.join(rdir, f)
        kb = os.path.getsize(path) // 1024
        cur.execute("INSERT INTO fulltext VALUES (?,?,?,?,?,?,?)",
                    (code, term, year, folder, repo, path, kb))
        n += 1

con.commit()
total = cur.execute("SELECT COUNT(DISTINCT code) FROM assets").fetchone()[0]
matched = cur.execute("""SELECT COUNT(DISTINCT a.code) FROM assets a
    JOIN fulltext t ON UPPER(a.code) = t.code""").fetchone()[0]
print(f"fulltext rows: {n}")
print(f"asset codes matched to text: {matched} of {total}")

unmatched = cur.execute("""SELECT a.code FROM assets a
    LEFT JOIN fulltext t ON UPPER(a.code) = t.code
    WHERE t.code IS NULL LIMIT 15""").fetchall()
if unmatched:
    print("sample unmatched:", ", ".join(u[0] for u in unmatched))
con.close()