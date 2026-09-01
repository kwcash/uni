#!/usr/bin/env python3
"""
fstem_qc_fix.py  ·  mechanical repairs for fstem_prepackage_qc failures, plus a worklist of the rest

  python3 fstem_qc_fix.py --modules modules/                 # report what would change, write worklist
  python3 fstem_qc_fix.py --modules modules/ --apply         # apply, then run QC and write the worklist

Fixes applied
  plus-layer field lines written as  **Field.** text   become  **Field:** text
  for the eight fields the checker names. Nothing else in the line changes.

Worklist
  audit_out/qc_worklist.csv   every remaining FAIL, one row each: class, file, line, text
  audit_out/qc_worklist_summary.txt   counts by class and by guide
"""
import argparse, csv, os, re, subprocess, sys, tarfile, datetime
from collections import Counter, defaultdict

STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
FIELDS = ["Currency slot", "Patent hook", "Research thread", "Doctrine-to-code artifact",
          "Red Cell target", "Sponsor slot", "Micro-credential", "Reproducibility deposit"]
FIELD_RE = re.compile(r"^\*\*(" + "|".join(re.escape(f) for f in FIELDS) + r")\.\*\*(\s|$)")


def fix_colons(modules, apply):
    changed = Counter()
    for name in sorted(os.listdir(modules)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(modules, name)
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        n = 0
        for i, l in enumerate(lines):
            m = FIELD_RE.match(l)
            if m:
                lines[i] = f"**{m.group(1)}:**" + l[len(m.group(0)) - len(m.group(2)):]
                n += 1
        if n:
            changed[name] = n
            if apply:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
    print(f"plus-layer colon fix: {sum(changed.values())} lines in {len(changed)} guides"
          f"{'' if apply else ' (dry run)'}")
    return changed


PARENT_RE = re.compile(r"^\*\*Parent:\*\* (FSTEM-[A-Z0-9-]+?-\d{3}),")
GATE_SHORT = re.compile(r"\bbuilt in M(\d)\b")


def fix_gates(modules, apply):
    """'drawn from X built in M2' -> 'drawn from X built in <parent>-M2'. Only that phrasing."""
    changed, byhand = Counter(), []
    for name in sorted(os.listdir(modules)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(modules, name)
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        parent = next((PARENT_RE.match(l).group(1) for l in lines[:12] if PARENT_RE.match(l)), None)
        n = 0
        for i, l in enumerate(lines):
            if "Carry the gate amendment" not in l:
                continue
            if re.search(r"drawn from .+ built in FSTEM-[A-Z0-9-]+-M\d", l):
                continue
            if parent and "drawn from" in l and GATE_SHORT.search(l):
                lines[i] = GATE_SHORT.sub(lambda m: f"built in {parent}-M{m.group(1)}", l)
                n += 1
            else:
                byhand.append((name, i + 1, l[:160]))
        if n:
            changed[name] = n
            if apply:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
    print(f"gate amendment id expansion: {sum(changed.values())} lines in {len(changed)} guides"
          f"{'' if apply else ' (dry run)'}; {len(byhand)} gate lines need a hand edit")
    return byhand


WORDNUM = {"one":"1","two":"2","three":"3","four":"4","five":"5","six":"6","seven":"7","eight":"8","nine":"9","ten":"10",
           "eleven":"11","twelve":"12","fifteen":"15","twenty":"20","twenty-five":"25","thirty":"30","fifty":"50","hundred":"100"}
WN = "|".join(sorted(WORDNUM, key=len, reverse=True))


def normalise_criterion(l):
    """Surface-form only. Same meaning, the checker's spelling."""
    o = l
    l = re.sub(r"\b(\d+)\s*%", r"\1 percent", l)
    l = re.sub(r"\bper cent\b", "percent", l, flags=re.I)
    l = re.sub(rf"\b({WN})\s+percent\b", lambda m: WORDNUM[m.group(1).lower()] + " percent", l, flags=re.I)
    def _cnt(m):
        lead = "At least" if m.group(1)[0].isupper() else "at least"
        num = m.group(2)
        return f"{lead} {WORDNUM.get(num.lower(), num)}"
    l = re.sub(rf"\b(at a minimum of|at least|no fewer than|a minimum of|not fewer than)\s+({WN}|\d+)\b", _cnt, l, flags=re.I)
    l = re.sub(r"(?<!to )\bwithin (\d+) percent\b", r"to within \1 percent", l)
    l = re.sub(r"\bto within (\d+)\s*percentage points\b", r"to within \1 percent", l)
    return l


def fix_criteria(modules, apply):
    changed = Counter()
    for name in sorted(os.listdir(modules)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(modules, name)
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        n = 0
        for i, l in enumerate(lines):
            if l.startswith("**Completion criterion.**"):
                nl = normalise_criterion(l)
                if nl != l:
                    lines[i] = nl; n += 1
        if n:
            changed[name] = n
            if apply:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
    print(f"criterion surface-form normalisation: {sum(changed.values())} lines in {len(changed)} guides"
          f"{'' if apply else ' (dry run)'}")


def guide_line(modules, fname, cls):
    """pull the sentence the checker judged, for classes whose message carries no excerpt"""
    try:
        with open(os.path.join(modules, fname), encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return ""
    if cls.startswith("completion criterion"):
        return next((l for l in lines if l.startswith("**Completion criterion.**")), "(no criterion line)")[:300]
    if cls.startswith("gate amendment"):
        return next((l for l in lines if "Carry the gate amendment" in l), "(no gate line)")[:300]
    return ""


def run_qc(qc, modules, out):
    if not os.path.exists(qc):
        print(f"QC tool {qc} not found; worklist not written")
        return
    r = subprocess.run([sys.executable, qc, modules, "--summary"], capture_output=True, text=True)
    text = (r.stdout or "") + (r.stderr or "")
    rows = []
    pat = re.compile(r"^FAIL (\S+?\.md):(\d+): (.*)$")
    for line in text.split("\n"):
        m = pat.match(line)
        if not m:
            continue
        fname, ln, msg = m.groups()
        # class = message up to the first colon, if the message carries a quoted excerpt after it
        cls, _, rest = msg.partition(": ")
        cls = re.sub(r"\d+", "N", cls)
        excerpt = rest if rest else guide_line(modules, fname, cls)
        rows.append((cls, fname, int(ln), excerpt or msg))
    rows.sort()
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "qc_worklist.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["class", "guide", "line", "text"])
        w.writerows(rows)
    by_cls = Counter(r[0] for r in rows)
    by_guide = Counter(r[1] for r in rows)
    with open(os.path.join(out, "qc_worklist_summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"{STAMP}\n{len(rows)} FAIL\n\nby class\n")
        for c, n in by_cls.most_common():
            f.write(f"  {n:>5}  {c}\n")
        f.write("\nby guide, worst first\n")
        for g, n in by_guide.most_common(40):
            f.write(f"  {n:>4}  {g}\n")
        f.write(f"\nguides with zero FAIL: {434 - len(by_guide)}\n")
    tail = [l for l in text.split("\n") if "guides ·" in l]
    print(tail[-1] if tail else "(no total line found)")
    print(f"worklist: {out}/qc_worklist.csv ({len(rows)} rows), summary: {out}/qc_worklist_summary.txt")
    for c, n in by_cls.most_common():
        print(f"  {n:>5}  {c}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", default="modules")
    ap.add_argument("--out", default="audit_out")
    ap.add_argument("--qc", default="fstem_prepackage_qc.py")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.apply:
        bak = os.path.join(a.out, f"modules_bak_{STAMP}.tgz")
        with tarfile.open(bak, "w:gz") as t:
            t.add(a.modules)
        print(f"backup: {bak}")
    fix_colons(a.modules, a.apply)
    fix_criteria(a.modules, a.apply)
    byhand = fix_gates(a.modules, a.apply)
    with open(os.path.join(a.out, "gate_lines_by_hand.txt"), "w", encoding="utf-8") as f:
        for name, ln, l in byhand:
            f.write(f"{name}:{ln}: {l}\n")
    run_qc(a.qc, a.modules, a.out)


if __name__ == "__main__":
    main()
