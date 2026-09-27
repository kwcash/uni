#!/usr/bin/env python3
"""
QC gate for generated TDW prose and site copy. Exit 1 on any failure.

    python3 tools/tdw_gate.py PATH [PATH ...] [--json OUT.json] [--no-register]

Checks (added to the SEED_04 checks, which live outside this repo):
  G1  An overlay finding with no Documented line fails. A finding is a heading that
      contains "Finding" or a paragraph opening **Finding**. Its Documented line must
      carry a line tag such as [KDP:L435] or [KDP_MASTER.md:L435-L437], and the tagged
      span must hold a Documented claim with a note ref in build/fstem_m7.db.
  G2  A quotation of 25 or more words from a source of record without a line tag fails.
  G3  Any reference to the three derivative zip files fails.
  G4  Any MIT or OCW mention, or an origin course number, in a file under site/ fails.
  R*  Register (Markdown drafts only, off with --no-register): no em-dashes, no sentence
      over 35 words, no "not X but Y", no weak adverb from the CLAUDE.md list.
      Sentences over 25 words and a mean above 20 are warnings.
"""
import argparse, csv, json, os, re, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tdw_lint  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M7DB = os.path.join(ROOT, "build", "fstem_m7.db")
SOURCES = {"KDP": "KDP_MASTER.md", "PRIMER": "PRIMER_MASTER.md"}
TAG = re.compile(r"\[(KDP|PRIMER)(?:_MASTER\.md)?:L(\d+)(?:\s*[-–]\s*L?(\d+))?\]")
DERIVATIVE = re.compile(r"Total[_ ]Domain[_ ]War\.pdf|Modern[_ ]Statecraft|Source[_ ]Quality[_ ]Ranking", re.I)
ORIGIN = re.compile(r"\bMIT\b|\bOCW\b|OpenCourseWare|ocw\.mit\.edu|Massachusetts Institute of Technology")
QUOTE = re.compile(r"[\"“]([^\"”]{40,}?)[\"”]")
FINDING_HEAD = re.compile(r"^#{1,6}\s.*\bFinding\b", re.I)
FINDING_PARA = re.compile(r"^\*\*Finding\b", re.I)


def norm(s):
    s = re.sub(r"\\\[\d+\\\]|\[\d+\]", "", s)
    s = re.sub(r"[*_`\\]", "", s)
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


class Corpus:
    def __init__(self):
        self.text = {}
        for k, f in SOURCES.items():
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                self.text[k] = norm(fh.read())
        self.claims = []
        if os.path.exists(M7DB):
            cx = sqlite3.connect(M7DB)
            doc = {v: k for k, v in SOURCES.items()}
            self.claims = [(doc[f], s, e, lab, refs) for f, s, e, lab, refs in cx.execute(
                "SELECT source_file, start_line, end_line, label, note_refs FROM tdw_claims")]
            cx.close()
        self.codes = set()
        with open(os.path.join(ROOT, "scripts", "fstem_rename_map.csv"), encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["mit_code"]:
                    self.codes.add(r["mit_code"])
        self.codes.add("FSTEM.AI.SPINE")

    def in_source(self, s):
        n = norm(s)
        return any(n in t for t in self.text.values())

    def documented_at(self, doc, a, b):
        return any(d == doc and s <= b and e >= a and lab == "Documented"
                   and json.loads(refs) for d, s, e, lab, refs in self.claims)


def blocks(text):
    """Paragraph blocks with their first line number."""
    out, cur, start = [], [], None
    for i, ln in enumerate(text.split("\n"), 1):
        if ln.strip():
            if not cur:
                start = i
            cur.append(ln)
        elif cur:
            out.append((start, "\n".join(cur)))
            cur = []
    if cur:
        out.append((start, "\n".join(cur)))
    return out


def findings(text):
    """(line, block text) for each finding: from its marker to the next heading of equal or higher level."""
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        if FINDING_HEAD.match(ln):
            lvl = len(ln) - len(ln.lstrip("#"))
            j = i + 1
            while j < len(lines) and not (re.match(r"^#{1,6}\s", lines[j]) and
                                          len(lines[j]) - len(lines[j].lstrip("#")) <= lvl):
                j += 1
            out.append((i + 1, "\n".join(lines[i:j])))
        elif FINDING_PARA.match(ln):
            j = i + 1
            while j < len(lines) and not re.match(r"^#{1,6}\s", lines[j]) and not FINDING_PARA.match(lines[j]):
                j += 1
            out.append((i + 1, "\n".join(lines[i:j])))
    return out


def check_file(path, corpus, register=True):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    fails, warns = [], []
    rel = os.path.relpath(os.path.abspath(path), ROOT)

    for ln, body in findings(text):
        doc_lines = [l for l in body.split("\n") if re.match(r"^\s*(?:[-*]\s*)?\*\*Documented\b|^\s*Documented[.:]", l)]
        ok = False
        for dl in doc_lines:
            for m in TAG.finditer(dl):
                a = int(m.group(2))
                b = int(m.group(3) or a)
                if corpus.documented_at(m.group(1), a, b):
                    ok = True
        if not ok:
            why = "no Documented line" if not doc_lines else \
                "Documented line lacks a line tag that resolves to a noted Documented claim"
            fails.append(("G1", ln, f"overlay finding: {why}"))

    for ln, blk in blocks(text):
        tagged = bool(TAG.search(blk))
        cands = [m.group(1) for m in QUOTE.finditer(blk)]
        if blk.lstrip().startswith(">"):
            cands.append(re.sub(r"(?m)^\s*>\s?", "", blk))
        for q in cands:
            if len(tdw_lint.words(q)) >= 25 and corpus.in_source(q) and not tagged:
                fails.append(("G2", ln, f"untagged quotation of {len(tdw_lint.words(q))} words: {q[:80]}"))

    for i, l in enumerate(text.split("\n"), 1):
        if DERIVATIVE.search(l):
            fails.append(("G3", i, f"derivative file referenced: {DERIVATIVE.search(l).group(0)}"))

    parts = rel.replace("\\", "/").split("/")
    if "site" in parts[:-1]:
        for i, l in enumerate(text.split("\n"), 1):
            m = ORIGIN.search(l)
            if m:
                fails.append(("G4", i, f"origin mention in site file: {m.group(0)}"))
            for c in corpus.codes:
                if re.search(r"(?<![\w.])" + re.escape(c) + r"(?![\w])", l):
                    fails.append(("G4", i, f"origin course number in site file: {c}"))

    if register and path.endswith(".md"):
        r = tdw_lint.lint_text(text, rel)
        if r["em_dashes"]:
            fails.append(("R1", r["em_dash_lines"][0], f"{r['em_dashes']} em-dashes"))
        if r["sentences_over_35"]:
            fails.append(("R2", r["longest_sentences"][0][0], f"{r['sentences_over_35']} sentences over 35 words"))
        if r["not_x_but_y"]:
            fails.append(("R3", r["not_x_but_y_examples"][0][0], f"{r['not_x_but_y']} 'not X but Y' constructions"))
        if r["weak_adverbs"]:
            fails.append(("R4", 0, f"weak adverbs: {r['weak_adverb_counts']}"))
        if r["sentences_over_25"]:
            warns.append(("W1", 0, f"{r['sentences_over_25']} sentences over 25 words"))
        if r["mean_sentence_length"] > 20:
            warns.append(("W2", 0, f"mean sentence length {r['mean_sentence_length']}"))
    return dict(file=rel, passed=not fails, fails=fails, warnings=warns)


def expand(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for f in sorted(files):
                    if f.endswith((".md", ".json", ".html", ".astro", ".txt", ".mdx", ".yml", ".yaml")):
                        yield os.path.join(root, f)
        else:
            yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--no-register", action="store_true")
    a = ap.parse_args()
    corpus = Corpus()
    res = [check_file(p, corpus, not a.no_register) for p in expand(a.paths)]
    for r in res:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['file']}")
        for code, ln, msg in r["fails"]:
            print(f"   {code} L{ln}: {msg}")
        for code, ln, msg in r["warnings"]:
            print(f"   {code} (warn): {msg}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
    bad = sum(1 for r in res if not r["passed"])
    print(f"\n{len(res) - bad} passed, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
