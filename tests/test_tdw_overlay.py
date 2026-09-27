"""C2 step 6. Fails if any overlay operation writes to a base table or a base column.

Builds a fixture fstem.db from the pipeline's own schemas, runs every overlay step
with apply on, and compares base content before and after.
"""
import hashlib, importlib.util, json, os, sqlite3, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ovl = load("tdw_overlay", os.path.join(ROOT, "tools", "tdw_overlay.py"))
ledger_mod = load("fstem_ledger", os.path.join(ROOT, "scripts", "fstem_ledger.py"))
loader = load("overlays", os.path.join(ROOT, "scripts", "06_load_overlays.py"))

BASE_DDL = """
CREATE TABLE assets (asset_id TEXT PRIMARY KEY, code TEXT, fstem_id TEXT, title TEXT, substrate_status TEXT);
CREATE TABLE modules (module_id TEXT PRIMARY KEY, parent_fstem_id TEXT NOT NULL, asset_id TEXT,
    module_index INTEGER NOT NULL, module_count INTEGER NOT NULL, title TEXT, status TEXT DEFAULT 'skeleton',
    micro_credential TEXT, track_overlay TEXT);
CREATE TABLE lessons (lesson_id TEXT PRIMARY KEY, module_id TEXT NOT NULL, week INTEGER NOT NULL, topic TEXT,
    status TEXT DEFAULT 'skeleton');
CREATE TABLE tracks (track_id TEXT PRIMARY KEY, name TEXT NOT NULL, degree TEXT, rung TEXT);
CREATE TABLE track_courses (track_id TEXT NOT NULL, fstem_id TEXT NOT NULL, seq INTEGER, role TEXT);
"""


def fixture(path):
    cx = sqlite3.connect(path)
    cx.executescript(BASE_DDL + loader.TABLE + ";" + ledger_mod.DDL)
    sp = "FSTEM-AI-SPINE-001"
    cx.execute("INSERT INTO assets VALUES ('a1','X','FSTEM-CS-R4-012','Computer Systems Security','live')")
    cx.execute("INSERT INTO tracks VALUES ('m7','Total Domain Conflict','MS-TDC','masters')")
    cx.execute("INSERT INTO track_courses VALUES ('m7','FSTEM-CS-R4-012',1,'track')")
    for i in range(1, 9):
        cx.execute("INSERT INTO modules VALUES (?,?,?,?,?,?,?,?,?)",
                   (f"{sp}-M{i}", sp, None, i, 8, f"Spine {i}", "draft", f"FSTEM-AISPINE-M{i}", ""))
        for w in (1, 2, 3):
            cx.execute("INSERT INTO lessons VALUES (?,?,?,?,?)", (f"{sp}-M{i}-W{w}", f"{sp}-M{i}", w, "t", "draft"))
    for t in ("m4", "m7"):
        oid = f"{sp}-M8-{t}"
        cx.execute("INSERT INTO module_overlays (overlay_id, module_id, parent_fstem_id, track_id) VALUES (?,?,?,?)",
                   (oid, f"{sp}-M8", sp, t))
        for w in (1, 2, 3):
            cx.execute("INSERT INTO lessons VALUES (?,?,?,?,?)", (f"{oid}-W{w}", oid, w, "o", "draft"))
        cx.execute("INSERT INTO artifacts (artifact_id, kind, grain, key, fstem_id, track_ids, created_at, updated_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (f"study_guide:{oid}:1", "study_guide", "module", oid, sp, t, "x", "x"))
    cx.execute("INSERT INTO artifacts (artifact_id, kind, grain, key, fstem_id, track_ids, created_at, updated_at) "
               "VALUES ('study_guide:FSTEM-AI-SPINE-001-M8:1','study_guide','module','FSTEM-AI-SPINE-001-M8',?,?,?,?)",
               (sp, "", "x", "x"))
    cx.commit()
    cx.close()


def sha_file(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def is_overlay(v):
    return v is not None and ("-M8-m" in v or "-M8-PROG-m" in v)


def snapshot(path):
    """Hash of every base table (schema and rows) and every non-overlay row of mixed tables."""
    cx = sqlite3.connect(path)
    out = {}
    for t in ovl.BASE_TABLES + ["lessons", "artifacts"]:
        if not ovl.has_table(cx, t):
            continue
        schema = cx.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0]
        rows = cx.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
        if t in ("lessons", "artifacts"):
            rows = [r for r in rows if not any(is_overlay(str(v)) for v in r)]
        out[t] = hashlib.sha256((schema + json.dumps(rows)).encode()).hexdigest()
    ocols = [c for c in ovl.cols(cx, "module_overlays") if c != "render_scope"]
    out["module_overlays.base_cols"] = hashlib.sha256(json.dumps(
        cx.execute(f"SELECT {','.join(c for c in ocols if c != 'overlay_id')} FROM module_overlays ORDER BY track_id"
                   ).fetchall()).encode()).hexdigest()
    cx.close()
    return out


class OverlayIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "tdw_ingest.py")], check=True, capture_output=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls.tmp.name, "fstem.db")
        fixture(cls.src)
        ovl.BASEDB = os.path.join(cls.tmp.name, "build_fstem.db")
        ovl.BUILD = cls.tmp.name
        ovl.SEED_OUT = os.path.join(cls.tmp.name, "SEED_07_OVERLAY_M7.csv")
        ovl.SITE_OUT = os.path.join(cls.tmp.name, "site", "m7_site_content.json")
        cls.src_hash = sha_file(cls.src)
        db = ovl.ensure_copy(cls.src)
        cls.before = snapshot(db)
        cx = sqlite3.connect(db)
        ovl.rename(cx, True)
        ovl.render_scope(cx, True)
        cx.commit()
        cx.close()
        ovl.attach(False)
        ovl.ledger(True)
        ovl.site(True)
        cls.after = snapshot(db)
        cls.db = db

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_base_untouched(self):
        for k in self.before:
            self.assertEqual(self.before[k], self.after[k], f"overlay step wrote to base: {k}")

    def test_original_never_written(self):
        self.assertEqual(self.src_hash, sha_file(self.src))

    def test_keys_renamed(self):
        cx = sqlite3.connect(self.db)
        q = lambda s: cx.execute(s).fetchone()[0]
        self.assertEqual(q("SELECT COUNT(*) FROM module_overlays WHERE overlay_id LIKE '%-M8-m_'"), 0)
        self.assertEqual(q("SELECT COUNT(*) FROM module_overlays WHERE overlay_id LIKE '%-M8-PROG-m_'"), 2)
        self.assertEqual(q("SELECT COUNT(*) FROM lessons WHERE lesson_id LIKE '%-M8-PROG-m_-W_'"), 6)
        self.assertEqual(q("SELECT COUNT(*) FROM lessons WHERE module_id LIKE '%-M8-PROG-m_'"), 6)
        self.assertEqual(q("SELECT COUNT(*) FROM artifacts WHERE key LIKE '%-M8-PROG-m_' AND "
                           "artifact_id LIKE '%-M8-PROG-m_:1'"), 2)
        self.assertEqual(q("SELECT COUNT(*) FROM lessons WHERE lesson_id LIKE '%-M8-W_'"), 3)
        self.assertIsNone(q("SELECT render_scope FROM module_overlays LIMIT 1"))
        cx.close()

    def test_loader_still_recognises_renamed_keys(self):
        self.assertTrue(loader.OVERLAY_ID.search("FSTEM-AI-SPINE-001-M8-PROG-m7"))
        self.assertTrue(loader.OVERLAY_ID.search("FSTEM-AI-SPINE-001-M8-m4"))
        self.assertFalse(loader.OVERLAY_ID.search("FSTEM-AI-SPINE-001-M8"))

    def test_site_content_has_no_origin_codes(self):
        with open(ovl.SITE_OUT, encoding="utf-8") as fh:
            blob = fh.read()
        for bad in ("MIT", "OCW", "OpenCourseWare", "6.858", "STS.", "ESD.", "RES.", "17.408"):
            self.assertNotIn(bad, blob)
        data = json.loads(blob)
        self.assertEqual(data["program"]["facts"]["course_count"], 1)


if __name__ == "__main__":
    unittest.main()
