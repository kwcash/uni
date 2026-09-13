#!/usr/bin/env python3
"""
Restructure CERT-STAT-ML for the adult/professional audience: statistics
compressed into 2 dense modules (not a 4-module AP Stats subset), ML expanded
into 2 modules, capstone unchanged.

Rationale (per decision 2026-09-13): professional learners don't need the
AP-course pacing — they need the AP-Stats-equivalent ground covered fast so
more of the 6 weeks goes to the ML content that differentiates this cert.

This REPLACES the M1-M5 set built by 11_expand_certifications.py for
CERT-STAT-ML only. Old module/lesson/reading/problem_set/quiz rows for this
cert are deleted and rebuilt — safe because nothing past skeleton/TBD
placeholders exists yet. No other cert is touched.

Same discipline: idempotent (tracked in `migrations`), backed up before
write, --dry-run.

    python3 12_restructure_stat_ml.py --db out/fstem_certs.db --dry-run
    python3 12_restructure_stat_ml.py --db out/fstem_certs.db
"""
import argparse, os, shutil, sqlite3, sys, datetime

TRACK_ID = "CERT-STAT-ML"
FSTEM_ID = f"FSTEM-{TRACK_ID}"
RUNG = "certification"
VERB = "apply"
VERTICAL = "mathematics"

MODULES = [
    {"title": "Statistics Foundations: EDA, Distributions & Probability", "weeks": 2,
     "readings": [("MIT-18.05", "Probability and Statistics", "course"),
                  ("FSTEM-ORIG", "Statistics for Operators", "custom")],
     "problem_sets": 2, "quiz_questions": 15},
    {"title": "Inference & Regression: Tests, Intervals, Correlation", "weeks": 1,
     "readings": [("MIT-18.05", "Probability and Statistics", "course"),
                  ("FSTEM-ORIG", "Inference & Regression Primer", "custom")],
     "problem_sets": 2, "quiz_questions": 15},
    {"title": "ML Fundamentals I: Supervised Learning", "weeks": 1,
     "readings": [("MIT-6.036", "Introduction to Machine Learning", "course")],
     "problem_sets": 2, "quiz_questions": 15},
    {"title": "ML Fundamentals II: Model Evaluation & Unsupervised Methods", "weeks": 1,
     "readings": [("MIT-6.867", "Machine Learning", "course")],
     "problem_sets": 2, "quiz_questions": 15},
    {"title": "Applied Capstone", "weeks": 1,
     "readings": [("FSTEM-ORIG", "Energy Consumption Forecasting Dataset", "custom")],
     "problem_sets": 0, "quiz_questions": 0},
]


def say(msg):
    print(msg)


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
        sys.exit(f"database not found: {a.db}")

    if not a.dry_run:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = f"{a.db}.bak-{stamp}"
        shutil.copy2(a.db, bak)
        say(f"backup written: {bak}")

    cx = sqlite3.connect(a.db)
    cx.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied_at TEXT)")

    mig_name = "restructure_stat_ml_2026_09_13"
    if already(cx, mig_name):
        say(f"{mig_name} already applied, nothing to do "
            "(delete the row in `migrations` to force a rebuild)")
        cx.close()
        return 0

    old_ids = [r[0] for r in cx.execute(
        "SELECT module_id FROM modules WHERE parent_fstem_id=?", (FSTEM_ID,))]
    say(f"\n== removing {len(old_ids)} old modules for {TRACK_ID} ==")
    for mid in old_ids:
        cx.execute("DELETE FROM lessons WHERE module_id=?", (mid,))
        cx.execute("DELETE FROM readings WHERE module_id=?", (mid,))
        for ps_id, in cx.execute("SELECT ps_id FROM problem_sets WHERE module_id=?", (mid,)):
            cx.execute("DELETE FROM problems WHERE ps_id=?", (ps_id,))
        cx.execute("DELETE FROM problem_sets WHERE module_id=?", (mid,))
        for quiz_id, in cx.execute("SELECT quiz_id FROM quizzes WHERE module_id=?", (mid,)):
            cx.execute("DELETE FROM quiz_questions WHERE quiz_id=?", (quiz_id,))
        cx.execute("DELETE FROM quizzes WHERE module_id=?", (mid,))
        cx.execute("DELETE FROM modules WHERE module_id=?", (mid,))
    say(f"  ok    cleared modules/lessons/readings/problem_sets/quizzes for {TRACK_ID}")

    say(f"\n== rebuilding {TRACK_ID} ({len(MODULES)} modules) ==")
    module_count = len(MODULES)
    n_modules = n_lessons = n_readings = n_ps = n_prob = n_quiz = n_qq = 0
    for idx, mod in enumerate(MODULES, start=1):
        module_id = f"{FSTEM_ID}-M{idx}"
        cx.execute(
            "INSERT INTO modules "
            "(module_id, parent_fstem_id, asset_id, module_index, module_count, title, "
            " weeks, rung, verb, vertical, substrate_status, micro_credential, status, title_source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (module_id, FSTEM_ID, f"fstem:{FSTEM_ID}", idx, module_count, mod["title"], mod["weeks"],
             RUNG, VERB, VERTICAL, "live", TRACK_ID, "skeleton", "catalog"),
        )
        n_modules += 1
        for wk in range(1, mod["weeks"] + 1):
            cx.execute(
                "INSERT INTO lessons (lesson_id, module_id, week, topic, status) VALUES (?,?,?,?,?)",
                (f"{module_id}-W{wk}", module_id, wk, f"{mod['title']} — week {wk}", "skeleton"),
            )
            n_lessons += 1
        for ridx, (source, rtitle, rtype) in enumerate(mod["readings"], start=1):
            cx.execute(
                "INSERT INTO readings (reading_id, module_id, source, title, ref_type, url) "
                "VALUES (?,?,?,?,?,?)",
                (f"{module_id}-R{ridx}", module_id, source, rtitle, rtype, None),
            )
            n_readings += 1
        for psi in range(1, mod["problem_sets"] + 1):
            ps_id = f"{module_id}-PS{psi}"
            cx.execute(
                "INSERT INTO problem_sets (ps_id, module_id, title, difficulty, est_minutes) "
                "VALUES (?,?,?,?,?)",
                (ps_id, module_id, f"{mod['title']} — Problem Set {psi}", "TBD", 90),
            )
            n_ps += 1
            for pnum in range(1, 9):
                cx.execute(
                    "INSERT INTO problems (problem_id, ps_id, prompt, answer_type, solution, difficulty) "
                    "VALUES (?,?,?,?,?,?)",
                    (f"{ps_id}-P{pnum}", ps_id, "TBD", "TBD", "TBD", "TBD"),
                )
                n_prob += 1
        if mod["quiz_questions"]:
            quiz_id = f"{module_id}-QUIZ"
            cx.execute(
                "INSERT INTO quizzes (quiz_id, module_id, title, duration_minutes, passing_score) "
                "VALUES (?,?,?,?,?)",
                (quiz_id, module_id, f"{mod['title']} — Quiz", 45, 70),
            )
            n_quiz += 1
            for qnum in range(1, mod["quiz_questions"] + 1):
                cx.execute(
                    "INSERT INTO quiz_questions "
                    "(question_id, quiz_id, type, prompt, options, correct, explanation, rubric) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (f"{quiz_id}-Q{qnum}", quiz_id, "TBD", "TBD", None, None, None, None),
                )
                n_qq += 1
        say(f"  M{idx}  {mod['title']}  ({mod['weeks']}w)")

    mark(cx, mig_name)

    summary = (f"{n_modules} modules, {n_lessons} lessons, {n_readings} readings, "
               f"{n_ps} problem sets, {n_prob} problems, {n_quiz} quizzes, {n_qq} quiz questions")
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
