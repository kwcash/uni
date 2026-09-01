#!/usr/bin/env python3
"""Post-build database patch. Run AFTER go.sh and attach_fulltext.py.
Fixes, in order:
  D1  phantom decimal prerequisites (1.5, 2.5, 6.1 ...)
  D3  J-suffix normalisation (code_norm columns on assets, prerequisites, fulltext)
  D2  bibliography: flag real citations, parse author/year where possible
  D4  missing threads T-QUANT, T-CAPITAL, T-LAW, T-PRIOR via title+description terms
Idempotent: safe to run repeatedly."""

import os
import re
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "..", "out", "fstem.db")

con = sqlite3.connect(DB)
cur = con.cursor()

def addcol(table, col, typ):
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    except sqlite3.OperationalError:
        pass  # already exists

def norm(code):
    return code.upper().rstrip("J") if code else code

# ---------------------------------------------------------------- D1
# A prerequisite is phantom when it names no held asset, no fulltext,
# and matches the bare-decimal shape that prose numbers take.
held = {norm(r[0]) for r in cur.execute("SELECT code FROM assets")}
held |= {norm(r[0]) for r in cur.execute("SELECT code FROM fulltext")}

DECIMAL = re.compile(r"^(\d{1,2})\.(\d{1,3})$")
UNITS = re.compile(r"(MB|GB|KB|X\d)", re.IGNORECASE)   # 3.1MB, 8.5X11
phantoms = []
for (req,) in cur.execute("SELECT DISTINCT requires FROM prerequisites"):
    if norm(req) in held:
        continue
    if UNITS.search(req):
        phantoms.append(req)
        continue
    m = DECIMAL.match(req)
    if m and (int(m.group(1)) > 24 or len(m.group(2)) == 1):
        phantoms.append(req)

if phantoms:
    q = ",".join("?" * len(phantoms))
    n = cur.execute(f"SELECT COUNT(*) FROM prerequisites WHERE requires IN ({q})",
                    phantoms).fetchone()[0]
    cur.execute(f"DELETE FROM prerequisites WHERE requires IN ({q})", phantoms)
    print(f"D1: removed {n} phantom prerequisite rows ({', '.join(sorted(phantoms))})")
else:
    print("D1: no phantoms found")

# ---------------------------------------------------------------- D3
for table in ("assets", "prerequisites", "fulltext"):
    src = "code" if table != "prerequisites" else "requires"
    addcol(table, "code_norm", "TEXT")
    cur.execute(f"UPDATE {table} SET code_norm = "
                f"UPPER(RTRIM(UPPER({src}),'J'))")
matched = cur.execute("""SELECT COUNT(DISTINCT a.code) FROM assets a
    JOIN fulltext t ON a.code_norm = t.code_norm""").fetchone()[0]
total = cur.execute("SELECT COUNT(DISTINCT code) FROM assets").fetchone()[0]
print(f"D3: code_norm added; fulltext join now {matched} of {total}")

# ---------------------------------------------------------------- D2
addcol("bibliography", "is_citation", "INTEGER")
CITE = re.compile(r"^([A-Z][a-zA-Z\-']+),\s+([A-Z]|[A-Z][a-z])")
YEAR = re.compile(r"\b(1[89]\d{2}|20[0-2]\d)\b")
rows = cur.execute("SELECT rowid, raw FROM bibliography").fetchall()
n_cite = 0
for rowid, raw in rows:
    if not raw:
        cur.execute("UPDATE bibliography SET is_citation=0 WHERE rowid=?", (rowid,))
        continue
    m = CITE.match(raw)
    looks = bool(m) and len(raw) > 40
    if looks:
        n_cite += 1
        y = YEAR.search(raw)
        cur.execute("""UPDATE bibliography SET is_citation=1, author=?, year=?
                       WHERE rowid=?""",
                    (m.group(1), int(y.group(1)) if y else None, rowid))
    else:
        cur.execute("UPDATE bibliography SET is_citation=0 WHERE rowid=?", (rowid,))
print(f"D2: {n_cite} of {len(rows)} rows flagged as citations; author/year parsed")

# ---------------------------------------------------------------- D4
NEW_THREADS = {
    "T-QUANT": ["probability", "statistics", "statistical", "calculus", "linear algebra",
                "regression", "econometric", "stochastic", "random variable",
                "inference", "quantitative", "optimization", "differential equation"],
    "T-CAPITAL": ["finance", "financial", "investment", "capital", "asset pricing",
                  "portfolio", "corporate finance", "banking", "monetary",
                  "macroeconomic", "fiscal", "accounting", "valuation"],
    "T-LAW": ["regulation", "regulatory", "policy", "law", "legal", "governance",
              "antitrust", "property rights", "compliance", "institution"],
    "T-PRIOR": ["patent", "innovation", "entrepreneur", "venture", "intellectual property",
                "invention", "technology transfer", "commercialization", "startup"],
}

existing = {(r[0], r[1]) for r in cur.execute("SELECT asset_id, thread FROM threads")}
assets = cur.execute("SELECT asset_id, COALESCE(title,''), COALESCE(description,'') "
                     "FROM assets").fetchall()
added = {t: 0 for t in NEW_THREADS}
for aid, title, desc in assets:
    text = (title + " " + desc).lower()
    for thread, terms in NEW_THREADS.items():
        if (aid, thread) in existing:
            continue
        hits = sum(1 for term in terms if term in text)
        if hits >= 2:                       # two independent terms required
            cur.execute("INSERT INTO threads VALUES (?,?)", (aid, thread))
            added[thread] += 1
for t, n in added.items():
    print(f"D4: {t} assigned to {n} assets (title+description match, provisional)")
n0 = cur.execute("""SELECT COUNT(*) FROM assets a WHERE NOT EXISTS
    (SELECT 1 FROM threads t WHERE t.asset_id=a.asset_id)""").fetchone()[0]
print(f"D4: assets with zero threads now {n0} of {len(assets)}")

con.commit()

# ---------------------------------------------------------------- report
print("\n=== POST-PATCH CHOKEPOINTS (true top 10) ===")
for r in cur.execute("""SELECT requires, COUNT(*) n FROM prerequisites
    GROUP BY requires ORDER BY n DESC LIMIT 10"""):
    print(f"  {r[0]:10s} gates {r[1]}")
con.close()
print("\nPatch complete.")
