"""C1 tests. Run: python3 -m unittest discover -s tests -v"""
import hashlib, json, os, re, sqlite3, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "build", "fstem_m7.db")
SOURCES = ["KDP_MASTER.md", "PRIMER_MASTER.md"]
SPAN_TABLES = ["tdw_documents", "tdw_sections", "tdw_claims", "tdw_notes", "tdw_field_cards",
               "tdw_exercises", "tdw_graphic_specs", "tdw_stratagems", "tdw_dimensions",
               "tdw_domains", "tdw_crosswalk", "tdw_roadmap"]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class IngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = {f: sha(os.path.join(ROOT, f)) for f in SOURCES}
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "tdw_ingest.py")],
                       check=True, capture_output=True)
        cls.cx = sqlite3.connect(DB)
        cls.cx.row_factory = sqlite3.Row
        cls.lines = {f: open(os.path.join(ROOT, f), "rb").read().decode("utf-8").split("\n")
                     for f in SOURCES}

    def test_sources_untouched(self):
        for f in SOURCES:
            self.assertEqual(self.before[f], sha(os.path.join(ROOT, f)), f)

    def test_claim_counts_match_grep(self):
        for f in SOURCES:
            for label in ("Documented", "Argued"):
                grep = sum(1 for ln in self.lines[f] if re.match(rf'^\*\*{label}', ln))
                got = self.cx.execute("SELECT COUNT(*) FROM tdw_claims WHERE source_file=? AND label=?",
                                      (f, label)).fetchone()[0]
                self.assertEqual(grep, got, f"{f} {label}")
                self.assertGreater(got, 0)

    def test_every_claim_ref_resolves(self):
        notes = {(r["source_file"], r["note_scope"], r["note_num"])
                 for r in self.cx.execute("SELECT * FROM tdw_notes")}
        bad = [(c["source_file"], c["start_line"], n)
               for c in self.cx.execute("SELECT * FROM tdw_claims")
               for n in json.loads(c["note_refs"])
               if (c["source_file"], c["note_scope"], n) not in notes]
        self.assertEqual(bad, [])

    def test_spans_byte_for_byte(self):
        n = 0
        for t in SPAN_TABLES:
            for r in self.cx.execute(f"SELECT * FROM {t}"):
                f = r["source_file"]
                self.assertEqual(r["sha256"], self.before[f])
                if t == "tdw_documents":
                    continue
                want = "\n".join(self.lines[f][r["start_line"] - 1:r["end_line"]]).encode("utf-8")
                self.assertEqual(r["text"].encode("utf-8"), want, f"{t} {f}:{r['start_line']}")
                n += 1
        for r in self.cx.execute("SELECT * FROM tdw_stratagems WHERE is_anchor=0"):
            line = self.lines[r["source_file"]][r["start_line"] - 1]
            self.assertEqual(r["entry_text"], line[r["start_col"]:r["end_col"]])
        self.assertGreater(n, 1000)

    def test_framework_counts(self):
        q = lambda s: self.cx.execute(s).fetchone()[0]
        self.assertEqual(q("SELECT COUNT(*) FROM tdw_stratagems"), 36)
        self.assertEqual(q("SELECT COUNT(DISTINCT number) FROM tdw_stratagems WHERE number BETWEEN 1 AND 36"), 36)
        self.assertEqual(q("SELECT COUNT(*) FROM tdw_stratagems WHERE is_anchor=1"), 6)
        self.assertEqual(q("SELECT COUNT(*) FROM tdw_dimensions"), 10)
        self.assertEqual(q("SELECT COUNT(*) FROM tdw_domains"), 12)

    def test_json_matches_db(self):
        with open(os.path.join(ROOT, "build", "tdw_corpus.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        for t in SPAN_TABLES:
            self.assertEqual(len(data[t]), self.cx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], t)


if __name__ == "__main__":
    unittest.main()
