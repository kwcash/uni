#!/usr/bin/env python3
"""
iterate.py — open and close one iteration, and prove it moved something.

An iteration that ends because you got tired is not finished. An iteration that
ends because a number in FACTS.md changed is. This enforces the difference.

    iterate.py open  --goal "Ingest the 78 undergraduate OCW courses"
    ... do the work ...
    iterate.py close

`open` snapshots the current facts. `close` regenerates them, diffs against the
snapshot, and appends a dated entry to ITERATION_LOG.md recording what actually
moved. An iteration that moved nothing is logged as such, which is information
rather than failure.

    iterate.py status     what is open, and what has moved so far
    iterate.py backlog    the ordered work list with each exit condition

Usage
-----
    python3 scripts/iterate.py open --goal "Parse the 40 IGCSE syllabi"
    python3 scripts/iterate.py status
    python3 scripts/iterate.py close --note "0620 and 0625 had no paper table"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import log  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(ROOT, ".iteration_state.json")
LOG = os.path.join(ROOT, "ITERATION_LOG.md")

# Ordered by what unblocks the most. Each carries an exit condition a script can
# check, because "done" is otherwise a feeling.
BACKLOG = [
    ("I1", "Ingest the 78 undergraduate OCW courses",
     "by_contributing_source.ocw_undergraduate >= 70",
     "ocw_ingest.py --dir <undergrad dir> --source-type ocw_undergraduate"),
    ("I2", "Parse the 40 IGCSE syllabi",
     "by_contributing_source.cie_igcse >= 35",
     "cie_parse.py --in <igcse dir> --qualification IGCSE"),
    ("I3", "Parse the 40 A-Level syllabi",
     "by_contributing_source.cie_alevel >= 35",
     "cie_parse.py --in <alevel dir> --qualification A_Level"),
    ("I4", "Harvest the Western Civilization courses",
     "harvest_required <= 5",
     "deep_harvest.py against the 61 codes, then ocw_ingest.py --dir"),
    ("I5", "Tune the lexicon against real output",
     "unscored <= 10",
     "read coverage_matrix.md, edit schema/lexicon.json, re-score"),
    ("I6", "Reconcile the Power Currency pillar spine",
     "manual: 137 reproduces, or the doctoral document is restated",
     "adjust power_currency_pillars terms, compare against 137"),
    ("I7", "Settle the disputed counts",
     "manual: exclusions.txt written, one number published per set",
     "merge_dedupe.py --expect, then write overrides/exclusions.txt"),
    ("I8", "Build the research rung",
     "by_rung.research > 0",
     "ingest the 40 dissertation projects as research-rung records"),
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def facts_path() -> str:
    for cand in ("out/facts.json", "results/facts.json"):
        p = os.path.join(ROOT, cand)
        if os.path.exists(p):
            return p
    return os.path.join(ROOT, "out", "facts.json")


def load_facts() -> dict:
    p = facts_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def regenerate() -> dict:
    """Run the facts generator against whichever scored repository exists."""
    for cand in ("out/fstem_repository_scored.jsonl", "results/fstem_repository_scored.jsonl"):
        repo = os.path.join(ROOT, cand)
        if os.path.exists(repo):
            outdir = os.path.dirname(repo)
            try:
                subprocess.run([sys.executable, os.path.join(HERE, "emit_facts.py"),
                                "--in", repo, "--out", outdir],
                               capture_output=True, timeout=300, check=False)
            except (subprocess.TimeoutExpired, OSError) as exc:
                log(f"  ! could not regenerate facts: {exc}")
            break
    else:
        log("  ! no scored repository found. Run the pipeline before closing.")
    return load_facts()


def flatten(facts: dict) -> dict:
    """One flat namespace so a diff reads cleanly."""
    out = {}
    for k, v in facts.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, (int, float)):
                    out[f"{k}.{k2}"] = v2
        elif isinstance(v, (int, float)):
            out[k] = v
    return out


def diff(before: dict, after: dict) -> list[tuple[str, object, object, float]]:
    a, b = flatten(before), flatten(after)
    keys = sorted(set(a) | set(b))
    rows = []
    for k in keys:
        va, vb = a.get(k, 0), b.get(k, 0)
        if va != vb:
            rows.append((k, va, vb, vb - va))
    return rows


def check_exit(facts: dict, condition: str) -> bool | None:
    """Evaluate a mechanical exit condition. None where it is a manual call."""
    if condition.startswith("manual:"):
        return None
    flat = flatten(facts)
    for op in (">=", "<=", ">", "<", "=="):
        if op in condition:
            lhs, rhs = condition.split(op, 1)
            key, target = lhs.strip(), rhs.strip()
            try:
                target_n = float(target)
            except ValueError:
                return None
            val = float(flat.get(key, 0))
            return {">=": val >= target_n, "<=": val <= target_n,
                    ">": val > target_n, "<": val < target_n,
                    "==": val == target_n}[op]
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_open(args) -> int:
    if os.path.exists(STATE):
        with open(STATE, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        log(f"iteration {st['id']} is already open: {st['goal']}")
        log("close it first, or delete .iteration_state.json to abandon it.")
        return 1

    goal = args.goal
    item = None
    if args.item:
        item = next((b for b in BACKLOG if b[0].upper() == args.item.upper()), None)
        if not item:
            log(f"unknown backlog item: {args.item}")
            return 1
        goal = goal or item[1]
    if not goal:
        log("pass --goal or --item")
        return 1

    n = 1
    if os.path.exists(LOG):
        with open(LOG, "r", encoding="utf-8") as fh:
            n = fh.read().count("\n## Iteration ") + 1

    st = {
        "id": n,
        "item": item[0] if item else None,
        "goal": goal,
        "exit_condition": item[2] if item else (args.exit or "manual: stated in the goal"),
        "opened_at": now(),
        "facts_before": load_facts(),
    }
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=2)

    log(f"iteration {n} open")
    log(f"  goal: {goal}")
    log(f"  exit: {st['exit_condition']}")
    if item:
        log(f"  how:  {item[3]}")
    log("")
    log("Work, then: python3 scripts/iterate.py close")
    return 0


def cmd_status(args) -> int:
    if not os.path.exists(STATE):
        log("no iteration open. Start one:")
        log("  python3 scripts/iterate.py open --item I1")
        return 0
    with open(STATE, "r", encoding="utf-8") as fh:
        st = json.load(fh)
    after = load_facts()
    rows = diff(st["facts_before"], after)
    log(f"iteration {st['id']} open since {st['opened_at']}")
    log(f"  goal: {st['goal']}")
    log(f"  exit: {st['exit_condition']}")
    met = check_exit(after, st["exit_condition"])
    log(f"  exit condition met: {'yes' if met else 'no' if met is False else 'manual call'}")
    log("")
    if rows:
        log("  moved so far:")
        for k, a, b, d in rows:
            log(f"    {k:<40} {a} -> {b}  ({d:+g})")
    else:
        log("  nothing has moved yet.")
    return 0


def cmd_close(args) -> int:
    if not os.path.exists(STATE):
        log("no iteration open.")
        return 1
    with open(STATE, "r", encoding="utf-8") as fh:
        st = json.load(fh)

    log("regenerating facts...")
    after = regenerate()
    rows = diff(st["facts_before"], after)
    met = check_exit(after, st["exit_condition"])

    lines = []
    if not os.path.exists(LOG):
        lines.append("# FSTEM Iteration Log")
        lines.append("")
        lines.append("One entry per iteration. Appended by `scripts/iterate.py close`. "
                     "Read the last entry to know where the project stands.")
        lines.append("")

    lines.append(f"## Iteration {st['id']} · {st['opened_at'][:10]}")
    lines.append("")
    lines.append(f"**Goal.** {st['goal']}")
    lines.append("")
    lines.append(f"**Exit condition.** `{st['exit_condition']}` — "
                 f"{'met' if met else 'not met' if met is False else 'manual call'}")
    lines.append("")
    if rows:
        lines.append("**What moved.**")
        lines.append("")
        lines.append("| Measure | Before | After | Change |")
        lines.append("|---|---|---|---|")
        for k, a, b, d in rows:
            lines.append(f"| `{k}` | {a} | {b} | {d:+g} |")
    else:
        lines.append("**What moved.** Nothing. No number in FACTS.md changed.")
    lines.append("")
    if args.note:
        lines.append(f"**Note.** {args.note}")
        lines.append("")
    nxt = next((b for b in BACKLOG if check_exit(after, b[2]) is False), None)
    if nxt:
        lines.append(f"**Next.** {nxt[0]} — {nxt[1]}")
        lines.append("")
    lines.append(f"*Closed {now()}.*")
    lines.append("")

    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.remove(STATE)

    log("")
    log(f"iteration {st['id']} closed")
    if rows:
        for k, a, b, d in rows:
            log(f"  {k:<40} {a} -> {b}  ({d:+g})")
    else:
        log("  nothing moved. Logged as such.")
    log("")
    log(f"  log: {LOG}")
    if nxt:
        log(f"  next: {nxt[0]} — {nxt[1]}")
    return 0


def cmd_backlog(args) -> int:
    facts = load_facts()
    log("")
    log(f"{'ID':<5}{'Status':<10}{'Item':<46}Exit condition")
    log("-" * 110)
    for bid, goal, cond, _how in BACKLOG:
        met = check_exit(facts, cond)
        status = "done" if met else "open" if met is False else "manual"
        log(f"{bid:<5}{status:<10}{goal:<46}{cond}")
    log("")
    nxt = next((b for b in BACKLOG if check_exit(facts, b[2]) is False), None)
    if nxt:
        log(f"start next:  python3 scripts/iterate.py open --item {nxt[0]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Open and close iterations against FACTS.md.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("open", help="Open an iteration.")
    o.add_argument("--goal")
    o.add_argument("--item", help="Backlog id such as I1.")
    o.add_argument("--exit", help="Exit condition when not using a backlog item.")
    o.set_defaults(fn=cmd_open)

    s = sub.add_parser("status", help="What is open and what has moved.")
    s.set_defaults(fn=cmd_status)

    c = sub.add_parser("close", help="Close, regenerate facts, log what moved.")
    c.add_argument("--note", help="One line for the log.")
    c.set_defaults(fn=cmd_close)

    b = sub.add_parser("backlog", help="The ordered work list.")
    b.set_defaults(fn=cmd_backlog)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
