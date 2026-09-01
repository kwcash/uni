#!/usr/bin/env python3
"""
fstem_guide_edits.py  ·  bring the guides into line with the repaired database

Reads repair_log in the database to learn which courses and overlays changed, then edits the guides.

  python3 fstem_guide_edits.py --db ../out/fstem.db --modules modules/            # propose only
  python3 fstem_guide_edits.py --db ../out/fstem.db --modules modules/ --apply    # token-safe edits only
  python3 fstem_guide_edits.py ... --apply --apply-prose                          # also the divergence prose

Writes audit_out/guide_edits_proposed.md and audit_out/guide_edits_by_hand.md.
Backs up modules/ to audit_out/modules_bak_<stamp>.tgz before writing anything.
A guide is edited only when every expected sentence is found exactly once. Otherwise it goes to the by-hand list.
"""
import argparse, os, re, sqlite3, subprocess, sys, tarfile, datetime

STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def section_bounds(lines, n):
    """(start, end) line indexes of '## n.' section, end exclusive."""
    start = end = None
    for i, l in enumerate(lines):
        if re.match(rf"^##\s+{n}\.", l):
            start = i
        elif start is not None and re.match(r"^##\s+\d+\.", l):
            end = i
            break
    if start is None:
        return None
    return start, end if end is not None else len(lines)


def find_once(lines, pattern, bounds):
    """Return the single line index in bounds whose text contains pattern, else None."""
    if bounds is None:
        return None
    hits = [i for i in range(*bounds) if pattern in lines[i]]
    return hits[0] if len(hits) == 1 else None


def load_changes(con):
    course = {}   # fstem_id -> (old, new)
    for k, o, n in con.execute("SELECT key, old, new FROM repair_log WHERE step='course_status_min'"):
        course[k] = (o, n)
    overlay = {}  # overlay_id -> (old, new)
    for k, o, n in con.execute("SELECT key, old, new FROM repair_log WHERE step='overlay_status'"):
        overlay[k] = (o, n)
    m4 = con.execute("SELECT old, new FROM repair_log WHERE step='overlay_m4_code'").fetchone()
    return course, overlay, m4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--modules", default="modules")
    ap.add_argument("--out", default="audit_out")
    ap.add_argument("--apply", action="store_true", help="apply token-safe edits")
    ap.add_argument("--apply-prose", action="store_true", help="apply the divergence prose too")
    ap.add_argument("--qc", default="fstem_prepackage_qc.py")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    if not con.execute("SELECT name FROM sqlite_master WHERE name='repair_log'").fetchone():
        sys.exit("no repair_log in the database; run fstem_repair.py --apply first")
    course_chg, overlay_chg, m4 = load_changes(con)
    os.makedirs(a.out, exist_ok=True)

    proposed, byhand = [], []
    edits = {}   # path -> list of (line_idx, new_text, kind)   kind in {'token','prose'}

    def guide(mid):
        return os.path.join(a.modules, f"{mid}.md")

    def read(path):
        with open(path, encoding="utf-8") as f:
            return f.read().split("\n")

    # ------------------------------------------------------------------ courses
    def sec_end(lines, bounds):
        e = bounds[1]
        while e - 1 > bounds[0] and lines[e - 1].strip() == "":
            e -= 1
        return e

    def header_idx(lines):
        return next((i for i, l in enumerate(lines[:15]) if l.startswith("**Substrate:**")), None)

    def rep(lines, i, old, new_):
        return (i, lines[i].replace(old, new_, 1), "prose", "replace")

    for fid, (old, new) in sorted(course_chg.items()):
        code = con.execute("SELECT code FROM assets WHERE fstem_id=?", (fid,)).fetchone()[0]
        mods = con.execute("SELECT module_id, substrate_status FROM modules WHERE parent_fstem_id=? ORDER BY module_index", (fid,)).fetchall()
        failing = [m for m, s_ in mods if s_ != "live"]
        fail_txt = " and ".join(failing)
        verb = "fails" if len(failing) == 1 else "fail"
        for mid, st in mods:
            path = guide(mid)
            if not os.path.exists(path):
                byhand.append((mid, "guide file not found")); continue
            lines = read(path)
            s2, s8 = section_bounds(lines, 2), section_bounds(lines, 8)
            if s2 is None or s8 is None:
                byhand.append((mid, "section 2 or 8 header not found")); continue
            e, form = [], None
            hi = header_idx(lines)

            # ---- idempotent fixups on any guide of a changed course
            for i in range(*s8):
                if "diverging upward. No divergence." in lines[i]:
                    e.append(rep(lines, i, "diverging upward. No divergence.", "diverging upward."))
                elif "At this module it carries `live`" in lines[i] and "diverg" not in lines[i].lower():
                    e.append(rep(lines, i, "At this module it carries `live`", "This module diverges upward. At this module the substrate carries `live`"))
            if st == "live" and hi is not None and "status **live**" in lines[hi] and "diverging from" not in lines[hi]:
                e.append((hi, lines[hi].replace("status **live**", f"status **live**, diverging from course status **{new}**", 1), "token", "replace"))

            done_marks = ("The failing block is", "diverging upward", "diverges upward", "Divergence upward", "carries `partial` with",
                          "holds `partial` with", "`partial`, with the course", "is the failing block")
            already = any(any(mk in lines[i] for mk in done_marks) for b in (s2, s8) for i in range(*b))
            if already:
                if e:
                    edits.setdefault(path, []).extend(e)
                    proposed.append((f"{mid}  [fixup]", [(i + 1, lines[i], t) for i, t, _, _ in e]))
                continue

            if st == "live":
                iA  = find_once(lines, "carries status `live`, and **this module carries `live` with it**", s2)
                iA0 = find_once(lines, "carries status `live`", s2)
                if iA is not None:
                    e.append(rep(lines, iA, "carries status `live`, and **this module carries `live` with it**",
                                 f"carries status `{new}` at course grain. The failing block is {fail_txt}. **This module carries `live`, diverging upward**")); form = "A-hst"
                elif iA0 is not None:
                    l2 = lines[iA0]; head, _, tail = l2.partition("carries status `live`")
                    m = re.match(r"(.*?\.)(\s.*)?$", tail) if tail.strip() else None
                    first_end, rest = (m.group(1), m.group(2) or "") if m else (".", "")
                    e.append((iA0, f"{head}carries status `{new}` at course grain{first_end} The failing block is {fail_txt}. "
                                   f"This module carries `live` and records the divergence here and in section 8.{rest}", "prose", "replace")); form = "A"
                elif not any("`live`" in lines[i] and "course" in lines[i].lower() and "status" in lines[i].lower() for i in range(*s2)):
                    e.append((sec_end(lines, s2), f"The course carries status `{new}` because {fail_txt} {verb}. This module carries `live` and diverges upward, which section 8 records.", "prose", "insert")); form = "B"
                else:
                    byhand.append((mid, f"course {code} {old}->{new}; section 2 names a course status in a form I do not recognise")); continue
                i8a  = find_once(lines, "The substrate carries status `live`", s8)
                i8h  = find_once(lines, "The course and this module both carry `live`.", s8)
                i8h2 = find_once(lines, "The course and this module both carry `live`,", s8)
                i8b  = find_once(lines, "Course status `live`. Module status `live`.", s8)
                i8b2 = find_once(lines, "**No divergence.** Course and module both hold `live`.", s8)
                i8b3 = find_once(lines, "Course substrate status **live**. Module substrate status **live**. No divergence.", s8)
                if i8a is not None:
                    l8 = lines[i8a]
                    if "The substrate carries status `live` and " in l8:
                        e.append(rep(lines, i8a, "The substrate carries status `live` and ",
                                     f"The substrate carries status `{new}` at course grain. The failing block is {fail_txt}. This module diverges upward. At this module the substrate carries `live` and "))
                    else:
                        e.append(rep(lines, i8a, "The substrate carries status `live`",
                                     f"The substrate carries status `{new}` at course grain. The failing block is {fail_txt}. This module diverges upward. At this module the substrate carries `live`"))
                elif i8h is not None:
                    e.append(rep(lines, i8h, "The course and this module both carry `live`.",
                                 f"The course carries `{new}` because {fail_txt} {verb}. This module carries `live`, diverging upward."))
                    if "The database first recorded" in lines[i8h]:
                        byhand.append((mid, f"L{i8h+1}: the sentence about what the database first recorded reads oddly beside `{new}`; reword or cut"))
                elif i8h2 is not None:
                    e.append(rep(lines, i8h2, "The course and this module both carry `live`,",
                                 f"The course carries `{new}` because {fail_txt} {verb}. This module carries `live`, diverging upward,"))
                elif i8b is not None:
                    line = lines[i8b].replace("Course status `live`. Module status `live`.",
                                              f"Course status `{new}`, because {fail_txt} {verb}. Module status `live`, diverging upward.", 1)
                    e.append((i8b, line.replace("diverging upward. No divergence.", "diverging upward."), "prose", "replace"))
                elif i8b2 is not None:
                    e.append(rep(lines, i8b2, "**No divergence.** Course and module both hold `live`.",
                                 f"**Divergence upward.** The course holds `{new}` because {fail_txt} {verb}. This module holds `live`."))
                elif i8b3 is not None:
                    e.append(rep(lines, i8b3, "Course substrate status **live**. Module substrate status **live**. No divergence.",
                                 f"Course substrate status **{new}**, because {fail_txt} {verb}. Module substrate status **live**. Divergence upward."))
                else:
                    byhand.append((mid, f"course {code} {old}->{new}; section 8 status line not in a form I recognise (section 2 was form {form})")); continue

            else:
                # ---- the failing module: it now sits with its course and no longer diverges
                others = [m for m, s_ in mods if s_ == "live"]
                _o = [o.split("-")[-1] for o in others]
                oth = (", ".join(_o[:-1]) + " and " + _o[-1]) if len(_o) > 1 else (_o[0] if _o else "none")
                if hi is not None and "diverging from course status" in lines[hi]:
                    e.append((hi, re.sub(r", diverging from course status \*\*\w+\*\*", "", lines[hi]), "token", "replace"))
                s2_forms = [
                    ("carries status `live` at course level. **This module carries `partial`, and the divergence is deliberate.**",
                     f"carries status `partial` at course grain. **This module carries `partial` with the course, and the failing block is deliberate.**", "A-f1"),
                    ("carries status `live` at course level. **This module carries `partial`**, and the divergence sits",
                     f"carries status `partial` at course grain. **This module carries `partial` with the course**, and the failure sits", "A-f2"),
                    ("carries status `live` at course level, and **this module carries `partial`** because",
                     f"carries status `partial` at course grain, and **this module carries `partial` with the course** because", "A-f3"),
                    ("carries status `live`, and **this module carries `partial`, diverging downward while modules M1 through M3 sit with the course.**",
                     f"carries status `partial` because this module fails. **This module carries `partial` with the course while modules {oth} hold `live` and diverge upward.**", "A-hst-f"),
                    ("**This module diverges downward from course status `live` to `partial`.**",
                     "**This module holds `partial` and the course holds `partial` with it.**", "B-f1"),
                    ("**Two layers separate and the module holds `partial`. This module diverges downward under a course that holds `live`.**",
                     "**Two layers separate and the module holds `partial`. The course holds `partial` with it, and this module is the failing block.**", "B-f2"),
                    ("**This module diverges to `partial` under a course that holds `live`.**",
                     "**This module holds `partial` and the course holds `partial` with it.**", "B-f3"),
                ]
                hit2 = None
                for o_, n_, f_ in s2_forms:
                    i = find_once(lines, o_, s2)
                    if i is not None:
                        e.append(rep(lines, i, o_, n_)); hit2 = f_; break
                if hit2 is None and st == "partial" and any("**Divergence recorded.** Course substrate status **live**" in lines[i] for i in range(*s8)):
                    e.append((sec_end(lines, s2), "**No divergence.** The course holds `partial` and this module holds `partial` with it, because this module is the failing block.", "prose", "insert")); hit2 = "B3-f-insert"
                if hit2 is None and st == "dead":
                    e.append((sec_end(lines, s2), "**Divergence recorded.** The course holds `partial` and this module holds `dead`, diverging downward. Section 3 carries the teaching through the generate-from-practice inversion.", "prose", "insert")); hit2 = "dead-insert"
                if hit2 is None:
                    byhand.append((mid, f"FAILING MODULE of {code}: section 2 status sentence not in a form I recognise")); continue
                i = find_once(lines, "**The block is the reason the course keeps `live` at course grain.**", s2)
                if i is not None:
                    e.append(rep(lines, i, "**The block is the reason the course keeps `live` at course grain.**", "**The block is the reason the course holds `partial` at course grain.**"))
                s8_forms = [
                    ("The substrate carries status `live` at course level and **this module carries `partial`**, which",
                     "The substrate carries status `partial` at course grain and **this module carries `partial` with it**, which"),
                    ("The course carries status `live` and this module carries `partial`, diverging downward while modules M1 through M3 sit with the course.",
                     f"The course carries status `partial` because this module fails. This module carries `partial` with the course while modules {oth} hold `live` and diverge upward."),
                    ("The substrate carries status `partial` for this module while the parent course carries `live`, and the module states the difference rather than inheriting silently.",
                     "The substrate carries status `partial` for this module and the parent course carries `partial` with it, because this module is the failing block."),
                    ("**This module diverges downward.** The course holds `live` and this module holds `partial`,",
                     "**No divergence.** The course holds `partial` and this module holds `partial` with it,"),
                    ("Course status `live`. **Module status `partial`, diverging downward.**",
                     "Course status `partial`. **Module status `partial`, with the course. No divergence.**"),
                    ("**Divergence recorded.** Course substrate status **live**. Module substrate status **partial**. This module carries a status below its course, and",
                     "**No divergence.** Course substrate status **partial**. Module substrate status **partial**. This module is the failing block and the course holds `partial` with it, and"),
                ]
                hit8 = False
                for o_, n_ in s8_forms:
                    i = find_once(lines, o_, s8)
                    if i is not None:
                        e.append(rep(lines, i, o_, n_)); hit8 = True; break
                if not hit8:
                    i85 = find_once(lines, "Course status `live`.", s8)
                    i87 = find_once(lines, "**This module diverges to `partial`.**", s8)
                    if i85 is not None and i87 is not None:
                        e.append(rep(lines, i85, "Course status `live`.", "Course status `partial`. Module status `partial`, with the course."))
                        l = lines[i87].replace("**This module diverges to `partial`.**", "**This module is the failing block.**", 1)
                        l = l.replace("The divergence therefore runs downward from a healthy course, which", "The failure therefore denies the course full authority, which", 1)
                        e.append((i87, l, "prose", "replace")); hit8 = True
                if not hit8 and st == "dead":
                    hit8 = any("diverg" in lines[i].lower() for i in range(*s8))
                if not hit8:
                    byhand.append((mid, f"FAILING MODULE of {code}: section 8 status sentence not in a form I recognise (section 2 was {hit2})")); continue
                form = hit2
                # anything left in sections 2 and 8 that still argues the old rule
                for b in (s2, s8):
                    for i in range(*b):
                        if re.search(r"diverg(es|ing) downward|course that holds `live`|keeps `live`|holds `live` and this module holds `partial`", lines[i]) and not any(i == x[0] for x in e):
                            byhand.append((mid, f"L{i+1} still argues the old rule, reword: {lines[i][:130]}"))
            edits.setdefault(path, []).extend(e)
            proposed.append((f"{mid}  [form {form}]", [((i + 1), lines[i] if op == "replace" else f"(insert before L{i+1})", t) for i, t, _, op in e]))

    # ---- dead modules under a partial course: header declares a divergence section 2 must state
    for mid, in con.execute("""SELECT m.module_id FROM modules m JOIN assets a ON a.fstem_id=m.parent_fstem_id
                               WHERE m.status='draft' AND m.substrate_status='dead' AND a.substrate_status='partial'"""):
        path = guide(mid)
        if not os.path.exists(path) or path in edits:
            continue
        lines = read(path)
        s2, s8 = section_bounds(lines, 2), section_bounds(lines, 8)
        hi = header_idx(lines)
        if hi is None or "diverging from" not in lines[hi] or s2 is None:
            continue
        if any("diverg" in lines[i].lower() for i in range(*s2)):
            continue
        ins = sec_end(lines, s2)
        t = "**Divergence recorded.** The course holds `partial` and this module holds `dead`, diverging downward. Section 3 carries the teaching through the generate-from-practice inversion."
        edits.setdefault(path, []).append((ins, t, "prose", "insert"))
        proposed.append((f"{mid}  [dead-under-partial]", [(ins + 1, "(insert before L%d)" % (ins + 1), t)]))

    # ------------------------------------------------------------------ overlays
    for oid, (old, new) in sorted(overlay_chg.items()):
        path = guide(oid)
        if not os.path.exists(path):
            byhand.append((oid, "guide file not found"))
            continue
        lines = read(path)
        s2, s8 = section_bounds(lines, 2), section_bounds(lines, 8)
        code = con.execute("SELECT substrate_course FROM module_overlays WHERE overlay_id=?", (oid,)).fetchone()[0]
        if any("this overlay inherits it" in lines[i] for i in range(*s2)):
            for i in range(*s2):
                if re.search(r"nothing as failed|without amendment|full authority", lines[i]):
                    byhand.append((oid, f"L{i+1} still reads as if the substrate held whole. Name the failing block of {code} (see divergence_register.csv): {lines[i][:110]}"))
            continue
        i_hdr = next((i for i, l in enumerate(lines[:12]) if l.startswith("**Substrate:**") and "status **live**" in l), None)
        i2 = find_once(lines, "carries status `live`", s2)
        i8 = find_once(lines, "The substrate carries status `live`", s8)
        i_rd = next((i for i, l in enumerate(lines) if "**Live substrate, full authority.**" in l), None)
        if i_hdr is None or i2 is None or i8 is None:
            byhand.append((oid, f"overlay {old}->{new} on {code}; header/section2/section8 = "
                                f"{i_hdr is not None}/{i2 is not None}/{i8 is not None}"))
            continue
        e = []
        e.append((i_hdr, lines[i_hdr].replace("status **live**", f"status **{new}**", 1), "token", "replace"))
        e.append((i2, lines[i2].replace("carries status `live`",
                                        f"carries status `{new}` at course grain and this overlay inherits it", 1), "prose", "replace"))
        l8 = lines[i8]
        if "The substrate carries status `live` and supplies" in l8:
            new8 = l8.replace("The substrate carries status `live` and supplies",
                              f"The substrate carries status `{new}` at course grain and this overlay inherits it. It supplies", 1)
        else:
            new8 = l8.replace("The substrate carries status `live`",
                              f"The substrate carries status `{new}` at course grain and this overlay inherits it", 1)
        e.append((i8, new8, "prose", "replace"))
        if i_rd is not None:
            e.append((i_rd, lines[i_rd].replace("**Live substrate, full authority.**",
                                                "**Partial substrate at course grain. See section 2.**", 1), "token", "replace"))
        edits.setdefault(path, []).extend(e)
        proposed.append((oid, [(i + 1, lines[i], t) for i, t, _, _ in e]))
        # sentences that will now contradict the status; flag, do not edit
        for i in range(*s2):
            if re.search(r"nothing as failed|without amendment|full authority", lines[i]):
                byhand.append((oid, f"L{i+1} still reads as if live, reword: {lines[i][:120]}"))

    # ------------------------------------------------------------------ overlay m4 code
    if m4:
        oldc, newc = m4
        oid = "FSTEM-AI-SPINE-001-M8-m4"
        path = guide(oid)
        if os.path.exists(path):
            lines = read(path)
            pat = re.compile(rf"\b{re.escape(oldc)}\b(?!\d)")
            e = [(i, pat.sub(newc, l), "token", "replace") for i, l in enumerate(lines) if pat.search(l)]
            if e:
                edits.setdefault(path, []).extend(e)
                proposed.append((oid + " (code)", [(i + 1, lines[i], t) for i, t, _, _ in e]))

    # ------------------------------------------------------------------ write review files
    with open(os.path.join(a.out, "guide_edits_proposed.md"), "w", encoding="utf-8") as f:
        f.write(f"# Proposed guide edits, {STAMP}\n\n{len(proposed)} guides. Read before --apply-prose.\n\n")
        for mid, items in proposed:
            f.write(f"## {mid}\n\n")
            for n, old, new in items:
                f.write(f"L{n}\n- was: {old}\n- now: {new}\n\n")
    with open(os.path.join(a.out, "guide_edits_by_hand.md"), "w", encoding="utf-8") as f:
        f.write(f"# Guides needing a hand edit, {STAMP}\n\n{len(byhand)} items.\n\n")
        for mid, note in byhand:
            f.write(f"- **{mid}**: {note}\n")
    print(f"proposed edits: {len(proposed)} guides  -> {a.out}/guide_edits_proposed.md")
    print(f"hand edits:     {len(byhand)} items   -> {a.out}/guide_edits_by_hand.md")

    if not a.apply:
        print("nothing written. Read the proposed file, then re-run with --apply (tokens) and --apply-prose (divergence sentences).")
        return

    # ------------------------------------------------------------------ apply
    bak = os.path.join(a.out, f"modules_bak_{STAMP}.tgz")
    with tarfile.open(bak, "w:gz") as t:
        t.add(a.modules)
    print(f"backup: {bak}")
    n_files = n_lines = skipped = 0
    for path, e in edits.items():
        lines = read(path)
        wrote = False
        for i, new, kind, op in sorted(e, key=lambda x: -x[0]):
            if kind == "prose" and not a.apply_prose:
                skipped += 1
                continue
            if op == "insert":
                lines.insert(i, new)
            else:
                lines[i] = new
            wrote = True
            n_lines += 1
        if wrote:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            n_files += 1
    print(f"wrote {n_lines} lines in {n_files} guides; {skipped} prose edits held back (use --apply-prose)")

    # ------------------------------------------------------------------ QC
    if os.path.exists(a.qc):
        print(f"\nrunning {a.qc} on {a.modules}")
        r = subprocess.run([sys.executable, a.qc, a.modules, "--summary"], capture_output=True, text=True)
        print((r.stdout or "")[-2500:], (r.stderr or "")[-800:])
    else:
        print(f"\nQC tool {a.qc} not found; run it by hand before packaging")


if __name__ == "__main__":
    main()
