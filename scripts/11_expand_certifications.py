#!/usr/bin/env python3
"""
FSTEM certification-tier expansion: 5 priority certs, content reuse matrix,
problem sets, quizzes, and capstone projects.

Adds to what 10_build_certifications.py built, on the same principles:
  - idempotent, tracked in `migrations`, backed up before write, --dry-run
  - never forks a parallel schema: new tables follow the existing
    one-row-per-fine-grained-item pattern already used by bibliography/
    flags/verticals, keyed back to module_id / cert track_id
  - structure is emitted deterministically (top-down); problem/quiz TEXT
    fields here are drafting placeholders, not finished content — the
    same TITLE_PENDING-style discipline 02_build_modules.py uses for
    titles it can't source yet

New tables (created only if missing):
  cert_catalog          cost/audience/duration metadata a masters track has no use for
  readings              module_id -> source reading (MIT OCW, FSTEM original, paper)
  problem_sets          module_id -> a problem set shell
  problems              problem_set_id -> one problem
  quizzes               module_id -> a quiz shell
  quiz_questions        quiz_id -> one question
  projects              track_id (cert) -> capstone/final project
  project_deliverables  project_id -> one graded deliverable

    python3 11_expand_certifications.py --db out/fstem_certs.db --dry-run
    python3 11_expand_certifications.py --db out/fstem_certs.db
"""
import argparse, os, shutil, sqlite3, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = []


def say(msg):
    LOG.append(msg)
    print(msg)


TABLES = {
    "cert_catalog": """CREATE TABLE IF NOT EXISTS cert_catalog (
        track_id      TEXT PRIMARY KEY,
        cost_usd      INTEGER,
        audience      TEXT,
        duration_weeks INTEGER
    )""",
    "readings": """CREATE TABLE IF NOT EXISTS readings (
        reading_id  TEXT PRIMARY KEY,
        module_id   TEXT NOT NULL,
        source      TEXT NOT NULL,
        title       TEXT,
        ref_type    TEXT,
        url         TEXT
    )""",
    "problem_sets": """CREATE TABLE IF NOT EXISTS problem_sets (
        ps_id           TEXT PRIMARY KEY,
        module_id       TEXT NOT NULL,
        title           TEXT,
        difficulty      TEXT,
        est_minutes     INTEGER
    )""",
    "problems": """CREATE TABLE IF NOT EXISTS problems (
        problem_id  TEXT PRIMARY KEY,
        ps_id       TEXT NOT NULL,
        prompt      TEXT,
        answer_type TEXT,
        solution    TEXT,
        difficulty  TEXT
    )""",
    "quizzes": """CREATE TABLE IF NOT EXISTS quizzes (
        quiz_id         TEXT PRIMARY KEY,
        module_id       TEXT NOT NULL,
        title           TEXT,
        duration_minutes INTEGER,
        passing_score   INTEGER
    )""",
    "quiz_questions": """CREATE TABLE IF NOT EXISTS quiz_questions (
        question_id TEXT PRIMARY KEY,
        quiz_id     TEXT NOT NULL,
        type        TEXT,
        prompt      TEXT,
        options     TEXT,
        correct     TEXT,
        explanation TEXT,
        rubric      TEXT
    )""",
    "projects": """CREATE TABLE IF NOT EXISTS projects (
        project_id          TEXT PRIMARY KEY,
        track_id            TEXT NOT NULL,
        title               TEXT,
        description         TEXT,
        passing_threshold   INTEGER,
        estimated_hours     INTEGER
    )""",
    "project_deliverables": """CREATE TABLE IF NOT EXISTS project_deliverables (
        deliverable_id  TEXT PRIMARY KEY,
        project_id      TEXT NOT NULL,
        name            TEXT,
        format          TEXT,
        rubric          TEXT
    )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_read_module ON readings(module_id)",
    "CREATE INDEX IF NOT EXISTS idx_ps_module ON problem_sets(module_id)",
    "CREATE INDEX IF NOT EXISTS idx_prob_ps ON problems(ps_id)",
    "CREATE INDEX IF NOT EXISTS idx_quiz_module ON quizzes(module_id)",
    "CREATE INDEX IF NOT EXISTS idx_qq_quiz ON quiz_questions(quiz_id)",
    "CREATE INDEX IF NOT EXISTS idx_proj_track ON projects(track_id)",
    "CREATE INDEX IF NOT EXISTS idx_deliv_proj ON project_deliverables(project_id)",
]

RUNG = "certification"
VERB = "apply"

# ---------------------------------------------------------------------------
# Five priority certs. `readings` are the content-reuse matrix: what already
# exists (MIT OCW code, an FSTEM book chapter) versus what has to be written
# from scratch (source == "FSTEM-ORIG"). Problem sets and quizzes are shells:
# one per module, counts matching the plan, prompts left TBD for drafting.
# ---------------------------------------------------------------------------

CERTIFICATIONS = [
    {
        "cert_id": "CERT-STAT-ML",
        "name": "Statistics & ML Fundamentals",
        "vertical": "mathematics",
        "weeks": 6, "cost_usd": 800, "audience": "analysts, operators, government",
        "modules": [
            {"title": "Statistics Foundations", "weeks": 2,
             "readings": [("MIT-18.05", "Probability and Statistics", "course"),
                          ("FSTEM-ORIG", "Statistics for Operators", "custom")],
             "problem_sets": 2, "quiz_questions": 15},
            {"title": "Probability & Inference", "weeks": 1,
             "readings": [("MIT-18.05", "Probability and Statistics", "course"),
                          ("FSTEM-ORIG", "Bayesian Inference Primer", "custom")],
             "problem_sets": 2, "quiz_questions": 15},
            {"title": "Regression & Classification", "weeks": 1,
             "readings": [("MIT-6.036", "Introduction to Machine Learning", "course")],
             "problem_sets": 2, "quiz_questions": 15},
            {"title": "Supervised Learning", "weeks": 1,
             "readings": [("MIT-6.867", "Machine Learning", "course")],
             "problem_sets": 2, "quiz_questions": 15},
            {"title": "Applied Project", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Energy Consumption Forecasting Dataset", "custom")],
             "problem_sets": 0, "quiz_questions": 0},
        ],
        "project": {
            "title": "Analysis: Energy Consumption Forecasting",
            "description": "Given 2 years of hourly electricity consumption data for a US "
                            "utility, build a statistical model to forecast next month's "
                            "consumption.",
            "passing_threshold": 70, "estimated_hours": 20,
            "deliverables": [
                ("EDA Report", "Jupyter notebook or PDF",
                 "data_understanding:20, visualization_quality:20, pattern_identification:20"),
                ("Model & Justification", "Code + writeup",
                 "model_choice:25, cross_validation:20, error_analysis:20"),
                ("Forecast & CI", "Chart + numbers",
                 "forecast_accuracy:25, confidence_intervals:25, interpretation:20"),
            ],
        },
    },
    {
        "cert_id": "CERT-AI-OPS",
        "name": "AI & LLM for Operators",
        "vertical": "computer_science",
        "weeks": 6, "cost_usd": 900, "audience": "systems operators, infrastructure teams, government analysts",
        "modules": [
            {"title": "AI Fundamentals & History", "weeks": 1,
             "readings": [("FSTEM-ORIG", "AI Fundamentals & History", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Large Language Models", "weeks": 2,
             "readings": [("arXiv-2311.00476", "Large Language Models: A Survey", "paper")],
             "problem_sets": 2, "quiz_questions": 15},
            {"title": "Prompt Engineering & RAG", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Prompt Engineering & RAG Lab", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Deployment & Ops", "weeks": 2,
             "readings": [("FSTEM-ORIG", "Deployment & Ops", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Risk, Safety, Red-Teaming", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Risk, Safety, Red-Teaming", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
        ],
        "project": None,
    },
    {
        "cert_id": "CERT-TQM",
        "name": "Total Quality Management",
        "vertical": "engineering",
        "weeks": 8, "cost_usd": 750, "audience": "manufacturing operators, supply chain, quality teams",
        "modules": [
            {"title": "TQM History & Philosophy", "weeks": 2,
             "readings": [("FSTEM-ORIG", "Deming & Juran Readings", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Statistical Process Control", "weeks": 1,
             "readings": [("MIT-2.830J", "Control of Manufacturing Processes", "course")],
             "problem_sets": 2, "quiz_questions": 15},
            {"title": "Root Cause Analysis", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Root Cause Analysis", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Lean + Six Sigma Foundations", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Lean + Six Sigma Foundations", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Implementation Case Studies", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Industry Case Studies", "custom")],
             "problem_sets": 0, "quiz_questions": 0},
            {"title": "Team Leadership & Culture", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Team Leadership & Culture", "custom")],
             "problem_sets": 0, "quiz_questions": 10},
        ],
        "project": {
            "title": "Capstone: TQM Implementation Plan",
            "description": "Analyze a real production or service process (client site or "
                            "documented case) and produce a TQM implementation plan.",
            "passing_threshold": 70, "estimated_hours": 24,
            "deliverables": [
                ("Process Audit", "Written report", "current_state:30, defect_data:20"),
                ("Implementation Plan", "Written report + timeline", "feasibility:25, metrics:25"),
            ],
        },
    },
    {
        "cert_id": "CERT-LEAN",
        "name": "LEAN Manufacturing Fundamentals",
        "vertical": "engineering",
        "weeks": 6, "cost_usd": 700, "audience": "operations managers, manufacturing, supply chain",
        "modules": [
            {"title": "LEAN History & Principles", "weeks": 1,
             "readings": [("FSTEM-ORIG", "LEAN History & Principles", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Value Stream Mapping", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Value Stream Mapping", "custom")],
             "problem_sets": 2, "quiz_questions": 10},
            {"title": "5S, Kaizen, Pull Systems", "weeks": 1,
             "readings": [("FSTEM-ORIG", "5S, Kaizen, Pull Systems", "custom")],
             "problem_sets": 2, "quiz_questions": 10},
            {"title": "Metrics & Measurement", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Metrics & Measurement", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Real-World Implementation", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Real-World Implementation", "custom")],
             "problem_sets": 0, "quiz_questions": 0},
        ],
        "project": None,
    },
    {
        "cert_id": "CERT-SYSA",
        "name": "Applied Systems Analysis",
        "vertical": "engineering",
        "weeks": 8, "cost_usd": 850, "audience": "infrastructure operators, analysts, government",
        "modules": [
            {"title": "Systems Thinking Framework", "weeks": 2,
             "readings": [("FSTEM-ORIG", "Total Domain War: Systems Thinking (ch. 1-2)", "book")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Decomposition & Analysis", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Decomposition & Analysis", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Modeling & Simulation", "weeks": 1,
             "readings": [("MIT-16.881", "Robust System Design", "course")],
             "problem_sets": 2, "quiz_questions": 15},
            {"title": "Trade-Space Exploration", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Trade-Space Exploration", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Decision-Making Under Uncertainty", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Decision-Making Under Uncertainty", "custom")],
             "problem_sets": 1, "quiz_questions": 10},
            {"title": "Resilience & Robustness", "weeks": 1,
             "readings": [("FSTEM-ORIG", "Resilience & Robustness", "custom")],
             "problem_sets": 0, "quiz_questions": 0},
        ],
        "project": {
            "title": "Capstone: Analyze a Real System",
            "description": "Apply the decomposition, modeling and trade-space methods to a "
                            "real infrastructure or organizational system, industry or "
                            "government partner preferred.",
            "passing_threshold": 70, "estimated_hours": 30,
            "deliverables": [
                ("System Model", "Diagram + writeup", "decomposition:30, boundaries:20"),
                ("Trade-Space Analysis", "Report", "alternatives:25, sensitivity:25"),
            ],
        },
    },
]


def already(cx, name):
    return cx.execute("SELECT 1 FROM migrations WHERE name=?", (name,)).fetchone() is not None


def mark(cx, name):
    cx.execute("INSERT OR REPLACE INTO migrations VALUES (?,?)",
               (name, datetime.datetime.now().isoformat(timespec="seconds")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit(f"database not found: {a.db}, run 10_build_certifications.py first")

    if not a.dry_run:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = f"{a.db}.bak-{stamp}"
        shutil.copy2(a.db, bak)
        say(f"backup written: {bak}")

    cx = sqlite3.connect(a.db)
    cx.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied_at TEXT)")

    if already(cx, "certifications_expand_2026_09_13"):
        say("certification expansion already applied, nothing to do "
            "(delete the row in `migrations` to force a rebuild)")
        cx.close()
        return 0

    say("\n== schema ==")
    for name, ddl in TABLES.items():
        cx.execute(ddl)
    say(f"  ok    tables: {', '.join(TABLES)}")
    for ix in INDEXES:
        cx.execute(ix)
    say(f"  ok    {len(INDEXES)} indexes")

    say("\n== certifications ==")
    n_tracks = n_modules = n_lessons = n_readings = n_ps = n_prob = 0
    n_quiz = n_qq = n_proj = n_deliv = 0
    for cert in CERTIFICATIONS:
        track_id = cert["cert_id"]
        fstem_id = f"FSTEM-{cert['cert_id']}"
        asset_id = f"fstem:{fstem_id}"

        cx.execute("INSERT OR REPLACE INTO tracks (track_id, name, degree, rung) VALUES (?,?,?,?)",
                    (track_id, cert["name"], track_id, RUNG))
        cx.execute("INSERT OR REPLACE INTO cert_catalog (track_id, cost_usd, audience, duration_weeks) "
                    "VALUES (?,?,?,?)", (track_id, cert["cost_usd"], cert["audience"], cert["weeks"]))
        cx.execute(
            "INSERT OR REPLACE INTO assets "
            "(asset_id, code, code_norm, title, provider, source_type, rung, verb, "
            " held, fstem_id, authored_by, substrate_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (asset_id, fstem_id, fstem_id, cert["name"], "FSTEM", "fstem_authored",
             RUNG, VERB, 1, fstem_id, "FSTEM", "live"),
        )
        cx.execute(
            "INSERT OR IGNORE INTO verticals (asset_id, vertical, score, pillars, method, rule_id) "
            "VALUES (?,?,?,?,?,?)",
            (asset_id, cert["vertical"], 1.0, "", "manual", "cert_catalog_2026_09_13"),
        )
        cx.execute("INSERT OR IGNORE INTO track_courses (track_id, fstem_id, seq, role) VALUES (?,?,?,?)",
                    (track_id, fstem_id, 1, "core"))
        n_tracks += 1

        module_count = len(cert["modules"])
        for idx, mod in enumerate(cert["modules"], start=1):
            module_id = f"{fstem_id}-M{idx}"
            cx.execute(
                "INSERT OR REPLACE INTO modules "
                "(module_id, parent_fstem_id, asset_id, module_index, module_count, title, "
                " weeks, rung, verb, vertical, substrate_status, micro_credential, status, title_source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (module_id, fstem_id, asset_id, idx, module_count, mod["title"], mod["weeks"],
                 RUNG, VERB, cert["vertical"], "live", cert["cert_id"], "skeleton", "catalog"),
            )
            n_modules += 1
            for wk in range(1, mod["weeks"] + 1):
                lesson_id = f"{module_id}-W{wk}"
                cx.execute(
                    "INSERT OR REPLACE INTO lessons (lesson_id, module_id, week, topic, status) "
                    "VALUES (?,?,?,?,?)",
                    (lesson_id, module_id, wk, f"{mod['title']} — week {wk}", "skeleton"),
                )
                n_lessons += 1

            for ridx, (source, rtitle, rtype) in enumerate(mod["readings"], start=1):
                cx.execute(
                    "INSERT OR REPLACE INTO readings (reading_id, module_id, source, title, ref_type, url) "
                    "VALUES (?,?,?,?,?,?)",
                    (f"{module_id}-R{ridx}", module_id, source, rtitle, rtype, None),
                )
                n_readings += 1

            for psi in range(1, mod.get("problem_sets", 0) + 1):
                ps_id = f"{module_id}-PS{psi}"
                cx.execute(
                    "INSERT OR REPLACE INTO problem_sets (ps_id, module_id, title, difficulty, est_minutes) "
                    "VALUES (?,?,?,?,?)",
                    (ps_id, module_id, f"{mod['title']} — Problem Set {psi}", "TBD", 90),
                )
                n_ps += 1
                for pnum in range(1, 9):  # 8 problems/set, per plan
                    cx.execute(
                        "INSERT OR REPLACE INTO problems (problem_id, ps_id, prompt, answer_type, solution, difficulty) "
                        "VALUES (?,?,?,?,?,?)",
                        (f"{ps_id}-P{pnum}", ps_id, "TBD", "TBD", "TBD", "TBD"),
                    )
                    n_prob += 1

            if mod.get("quiz_questions", 0):
                quiz_id = f"{module_id}-QUIZ"
                cx.execute(
                    "INSERT OR REPLACE INTO quizzes (quiz_id, module_id, title, duration_minutes, passing_score) "
                    "VALUES (?,?,?,?,?)",
                    (quiz_id, module_id, f"{mod['title']} — Quiz", 45, 70),
                )
                n_quiz += 1
                for qnum in range(1, mod["quiz_questions"] + 1):
                    cx.execute(
                        "INSERT OR REPLACE INTO quiz_questions "
                        "(question_id, quiz_id, type, prompt, options, correct, explanation, rubric) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (f"{quiz_id}-Q{qnum}", quiz_id, "TBD", "TBD", None, None, None, None),
                    )
                    n_qq += 1

        if cert.get("project"):
            proj = cert["project"]
            project_id = f"{fstem_id}-CAPSTONE"
            cx.execute(
                "INSERT OR REPLACE INTO projects "
                "(project_id, track_id, title, description, passing_threshold, estimated_hours) "
                "VALUES (?,?,?,?,?,?)",
                (project_id, track_id, proj["title"], proj["description"],
                 proj["passing_threshold"], proj["estimated_hours"]),
            )
            n_proj += 1
            for didx, (name, fmt, rubric) in enumerate(proj["deliverables"], start=1):
                cx.execute(
                    "INSERT OR REPLACE INTO project_deliverables "
                    "(deliverable_id, project_id, name, format, rubric) VALUES (?,?,?,?,?)",
                    (f"{project_id}-D{didx}", project_id, name, fmt, rubric),
                )
                n_deliv += 1

        say(f"  {track_id:<14} {cert['name']}  ({module_count} modules, {cert['weeks']} weeks, "
            f"${cert['cost_usd']})")

    mark(cx, "certifications_expand_2026_09_13")

    summary = (f"{n_tracks} tracks, {n_modules} modules, {n_lessons} lessons, {n_readings} readings, "
               f"{n_ps} problem sets, {n_prob} problems, {n_quiz} quizzes, {n_qq} quiz questions, "
               f"{n_proj} projects, {n_deliv} deliverables")
    if a.dry_run:
        cx.rollback()
        say(f"\nDRY RUN: would write {summary}. Nothing written.")
    else:
        cx.commit()
        say(f"\ncommitted: {summary}.")
    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
