"""C3 tests for tools/tdw_gate.py and tools/tdw_lint.py."""
import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import tdw_gate, tdw_lint  # noqa: E402

# KDP line 435 is a Documented claim that cites note [7]; line 437 is Argued.
LONG_QUOTE = ("The National People's Congress decision of May 28, 2020, set the aim as a legal system and enforcement "
              "mechanism to safeguard national security in Hong Kong.")


class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "tdw_ingest.py")], check=True, capture_output=True)
        cls.corpus = tdw_gate.Corpus()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_gate(self, text, name="draft.md", register=False):
        p = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return tdw_gate.check_file(p, self.corpus, register)

    def codes(self, r):
        return {c for c, _, _ in r["fails"]}

    def test_finding_without_documented_fails(self):
        r = self.run_gate("### Finding 1\n\n**Argued.** The law closed the city. [KDP:L437]\n")
        self.assertIn("G1", self.codes(r))

    def test_finding_with_untagged_documented_fails(self):
        r = self.run_gate("### Finding 1\n\n**Documented.** The decision set the aim.\n")
        self.assertIn("G1", self.codes(r))

    def test_finding_tagged_to_argued_line_fails(self):
        r = self.run_gate("### Finding 1\n\n**Documented.** The operation reached further. [KDP:L437]\n")
        self.assertIn("G1", self.codes(r))

    def test_finding_with_traced_documented_passes(self):
        r = self.run_gate("### Finding 1\n\n**Documented.** The decision of May 28, 2020, set the aim. [KDP:L435]\n")
        self.assertNotIn("G1", self.codes(r))

    def test_long_untagged_quote_fails_and_tagged_passes(self):
        self.assertIn("G2", self.codes(self.run_gate(f'The book says "{LONG_QUOTE}"\n')))
        self.assertNotIn("G2", self.codes(self.run_gate(f'The book says "{LONG_QUOTE}" [KDP:L435]\n')))
        self.assertIn("G2", self.codes(self.run_gate(f"> {LONG_QUOTE}\n")))

    def test_derivative_reference_fails(self):
        self.assertIn("G3", self.codes(self.run_gate("See Total_Domain_War.pdf, slide 3.\n")))
        self.assertIn("G3", self.codes(self.run_gate("Per Source_Quality_Ranking notes.\n")))

    def test_origin_mention_fails_only_under_site(self):
        self.assertIn("G4", self.codes(self.run_gate("Built on MIT OCW.\n", "site/page.md")))
        self.assertIn("G4", self.codes(self.run_gate('{"code": "6.858"}\n', "site/data.json")))
        self.assertNotIn("G4", self.codes(self.run_gate("Built on MIT OCW.\n", "notes/page.md")))

    def test_register(self):
        r = self.run_gate("The plan failed — we regrouped. It is very important.\n", register=True)
        self.assertTrue({"R1", "R4"} <= self.codes(r))
        r = self.run_gate("The plan failed. We regrouped.\n", register=True)
        self.assertTrue(r["passed"], r["fails"])


class Lint(unittest.TestCase):
    def test_skips_non_prose(self):
        text = "---\ntitle: \"A — B\"\n---\n# Head — x\n| a — b |\n> q — r\n```\nx — y\n```\nOne line.\n"
        r = tdw_lint.lint_text(text)
        self.assertEqual(r["em_dashes"], 0)
        self.assertEqual(r["sentences"], 1)

    def test_counts(self):
        r = tdw_lint.lint_text("It is not a tactic but a mindset. " + "word " * 30 + "end.\n")
        self.assertEqual(r["not_x_but_y"], 1)
        self.assertEqual(r["sentences_over_25"], 1)
        self.assertEqual(r["sentences_over_35"], 0)


if __name__ == "__main__":
    unittest.main()
