#!/usr/bin/env python3
"""
FSTEM certification-tier builder.

Same discipline as 01_migrate_db.py / 02_build_modules.py, applied to a new tier:

  - never edits the masters corpus in place; starts from a COPY of the source db
  - idempotent: safe to re-run, tracked in the `migrations` table
  - always backs up before writing
  - reuses the existing schema (tracks / track_courses / assets / modules / lessons)
    tagged with rung='certification' instead of forking a parallel set of tables
  - top-down structure (this script emits every row deterministically), bottom-up
    content (module/lesson prose is drafted later against the brief, not invented here)

Certification courses are FSTEM-authored (provider='FSTEM', source_type='fstem_authored'),
not MIT OCW imports, so they get fresh fstem_id codes rather than `ocw:` asset ids.

    python3 10_build_certifications.py --src fstem1.db --db out/fstem_certs.db --dry-run
    python3 10_build_certifications.py --src fstem1.db --db out/fstem_certs.db
"""
import argparse, os, shutil, sqlite3, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = []


def say(msg):
    LOG.append(msg)
    print(msg)


# ---------------------------------------------------------------- catalog
#
# Three starter certifications. Each maps onto a vertical that already exists
# in fstem_common.VERTICALS so scoring/threading stays consistent with the
# masters corpus. Weeks and module counts are planning estimates, not content.
#
# module weeks sum to the cert's total weeks; lessons are 1/week/module.

CERTIFICATIONS = [
    {
        "cert_id": "CERT-PCS",
        "name": "Power Currency Systems Fundamentals",
        "vertical": "power_currency",
        "weeks": 6,
        "modules": [
            ("Physics of Energy and the Grid", 2),
            ("Grid Economics and Capacity Pricing", 2),
            ("Policy and Regulatory Frameworks", 2),
        ],
    },
    {
        "cert_id": "CERT-SAO",
        "name": "Systems Analysis for Operators",
        "vertical": "engineering",
        "weeks": 8,
        "modules": [
            ("Systems Thinking and Decomposition", 2),
            ("Reliability, Failure Modes and Root Cause", 2),
            ("Operational Data and Instrumentation", 2),
            ("Capstone: Operator Systems Audit", 2),
        ],
    },
    {
        "cert_id": "CERT-QFO",
        "name": "Quantitative Finance for Operators",
        "vertical": "finance",
        "weeks": 6,
        "modules": [
            ("Time Value, Discounting and Capital Cost", 2),
            ("Risk, Variance and Portfolio Method", 2),
            ("Capital Allocation Under Constraint", 2),
        ],
    },
]

RUNG = "certification"
VERB = "apply"
GATE = "GC"  # certification gate, distinct from the G1..G5 degree ladder


def already(cx, name):
    return cx.execute("SELECT 1 FROM migrations WHERE name=?", (name,)).fetchone() is not None


def mark(cx, name):
    cx.execute("INSERT OR REPLACE INTO migrations VALUES (?,?)",
               (name, datetime.datetime.now().isoformat(timespec="seconds")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="source db to copy from (e.g. the uploaded fstem1.db)")
    ap.add_argument("--db", required=True, help="destination certification db (created from --src if missing)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.src):
        sys.exit(f"source database not found: {a.src}")

    os.makedirs(os.path.dirname(os.path.abspath(a.db)) or ".", exist_ok=True)
    if not os.path.exists(a.db):
        shutil.copy2(a.src, a.db)
        say(f"seeded {a.db} from {a.src}")
    elif not a.dry_run:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = f"{a.db}.bak-{stamp}"
        shutil.copy2(a.db, bak)
        say(f"backup written: {bak}")

    cx = sqlite3.connect(a.db)
    cx.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied_at TEXT)")

    if already(cx, "certifications_2026_09_13"):
        say("certification tier already built, nothing to do (delete the row in `migrations` to force a rebuild)")
        cx.close()
        return 0

    say("\n== certification tracks ==")
    n_tracks = n_assets = n_modules = n_lessons = n_links = 0
    for cert in CERTIFICATIONS:
        track_id = cert["cert_id"]
        cx.execute(
            "INSERT OR REPLACE INTO tracks (track_id, name, degree, rung) VALUES (?,?,?,?)",
            (track_id, cert["name"], track_id, RUNG),
        )
        n_tracks += 1

        fstem_id = f"FSTEM-{cert['cert_id']}"
        asset_id = f"fstem:{fstem_id}"
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
        cx.execute(
            "INSERT OR IGNORE INTO track_courses (track_id, fstem_id, seq, role) VALUES (?,?,?,?)",
            (track_id, fstem_id, 1, "core"),
        )
        n_assets += 1
        n_links += 1

        module_count = len(cert["modules"])
        for idx, (title, weeks) in enumerate(cert["modules"], start=1):
            module_id = f"{fstem_id}-M{idx}"
            cx.execute(
                "INSERT OR REPLACE INTO modules "
                "(module_id, parent_fstem_id, asset_id, module_index, module_count, title, "
                " weeks, rung, verb, vertical, substrate_status, micro_credential, status, title_source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (module_id, fstem_id, asset_id, idx, module_count, title, weeks, RUNG, VERB,
                 cert["vertical"], "live", cert["cert_id"], "skeleton", "catalog"),
            )
            n_modules += 1
            for wk in range(1, weeks + 1):
                lesson_id = f"{module_id}-W{wk}"
                cx.execute(
                    "INSERT OR REPLACE INTO lessons "
                    "(lesson_id, module_id, week, topic, status) VALUES (?,?,?,?,?)",
                    (lesson_id, module_id, wk, f"{title} — week {wk}", "skeleton"),
                )
                n_lessons += 1
        say(f"  {track_id:<10} {cert['name']}  ({module_count} modules, {cert['weeks']} weeks)")

    mark(cx, "certifications_2026_09_13")

    if a.dry_run:
        cx.rollback()
        say(f"\nDRY RUN: would write {n_tracks} tracks, {n_assets} course assets, "
            f"{n_modules} modules, {n_lessons} lessons. Nothing written.")
    else:
        cx.commit()
        say(f"\ncommitted: {n_tracks} tracks, {n_assets} course assets, "
            f"{n_modules} modules, {n_lessons} lessons.")
    cx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
