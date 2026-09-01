#!/usr/bin/env python3
"""
FSTEM module-guide QC. Enforces FSTEM_MODULE_TEMPLATE.md (locked 22 August 2026).

    python3 fstem_module_qc.py modules/*.md
    python3 fstem_module_qc.py --map fstem_rename_map_CORRECTED.csv modules/

Exit 0 clean, 1 on any FAIL. WARN never fails the batch.
"""
import sys, os, re, csv, glob, datetime, collections

WINDOW = {"computer_science": 12, "mathematics": 18, "engineering": 18, "economics": 18,
          "business": 18, "finance": 18, "history": 18, "literature": 18, "philosophy": 18,
          "arts": 18, "politics": 18, "society_technology": 18, "general_core": 18}
AI_SPINE_WINDOW = 12
BANNED_OPENER = re.compile(r'(?im)^\s*(?:[-*+]\s+|\d+\.\s+|>\s*)?(It is|It was|There is|There are|There was|There were)\b')
EMDASH = re.compile(r'[—–]')
VARIANT_ESSAY = re.compile(r'(?i)\b(essay|paper|reflection piece)\b')
QUANT = {"quantitative", "engineering"}
SECTIONS = ["What this module teaches", "Substrate", "Currency", "Sessions",
            "The deposit", "Readings", "Plus layer", "Provenance"]
PLUS = ["Currency slot", "Patent hook", "Research thread", "Doctrine-to-code artifact",
        "Red Cell target", "Sponsor slot", "Micro-credential", "Reproducibility deposit"]
DEPOSIT = ["Claim", "Evidence", "Completion criterion", "Re-run"]
HEADER = ["Module status", "Parent", "Module", "Rung", "Track overlay", "Substrate",
          "Currency refreshed", "Micro-credential"]
CHECKABLE = re.compile(r'(?i)(\d+\s*%|\d+(\.\d+)?\s*(percent|points?|items?|cases?|tests?|figures?|drawings?)'
                       r'|within\s+\d|at least \d|no more than \d|rubric|passing test|test suite|all tests pass)')


def parse(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    hdr = {}
    for m in re.finditer(r'^\*\*(.+?):\*\*\s*(.*)$', txt[:4000], re.M):
        hdr[m.group(1).strip()] = m.group(2).strip()
    m = re.search(r'^\*\*Module status:\s*(.*?)\*\*', txt[:4000], re.M)
    if m:
        hdr["Module status"] = m.group(1).strip()
    return txt, hdr


def check(path, parents, today):
    txt, hdr = parse(path)
    F, W = [], []
    base = os.path.basename(path)
    # Skeletons are structure, not prose. Check the frame, skip the content rules.
    if re.search(r'(?i)^\*\*Module status:\s*SKELETON', txt, re.M):
        if "DRAFT_PENDING" not in txt:
            F.append("marked SKELETON but carries no DRAFT_PENDING markers")
        for k in HEADER:
            if k not in hdr:
                F.append(f"header missing {k!r}")
        return base, F, ["skeleton, content checks skipped"]
    body = txt.split("\n", 1)[1] if "\n" in txt else ""

    # 1-3 inherited
    for m in BANNED_OPENER.finditer(body):
        F.append(f"banned opener {m.group(1)!r} at char {m.start()}")
    for ln in body.split("\n"):
        if EMDASH.search(ln) and not ln.lstrip().startswith("#"):
            F.append(f"em-dash in non-title line: {ln.strip()[:70]!r}")
    if "batch" not in hdr.get("Module status", "").lower():
        F.append("no batch tag in Module status")

    # 4 header fields
    for k in HEADER:
        if k not in hdr:
            F.append(f"header missing {k!r}")
    sub = hdr.get("Substrate", "")
    ms = re.search(r'status\s*\*\*(live|partial|dead)\*\*', sub)
    if not ms:
        F.append("Substrate line missing status (live|partial|dead)")
    status = ms.group(1) if ms else None

    cr = hdr.get("Currency refreshed", "")
    d1 = re.search(r'(\d{4}-\d{2}-\d{2})', cr)
    d2 = re.findall(r'(\d{4}-\d{2}-\d{2})', cr)
    if not d1:
        F.append("no refresh date")
    if len(d2) < 2:
        F.append("no next-refresh-due date")

    # 5 refresh window
    if len(d2) >= 2:
        try:
            a = datetime.date.fromisoformat(d2[0]); b = datetime.date.fromisoformat(d2[1])
            months = (b.year - a.year) * 12 + (b.month - a.month)
            lim = AI_SPINE_WINDOW if re.search(r'(?i)AI[- ]spine|computer_science', txt[:1500]) else 18
            if months > lim:
                F.append(f"refresh window {months}mo exceeds {lim}mo limit")
            if b < today:
                W.append(f"module is stale (due {b}), must render a stale marker")
        except ValueError:
            F.append("unparseable refresh dates")

    # sections present and ordered
    found = [s for s in SECTIONS if re.search(r'^#+\s*\d*\.?\s*' + re.escape(s), txt, re.M | re.I)]
    for s in SECTIONS:
        if s not in found:
            F.append(f"missing section {s!r}")
    if found != [s for s in SECTIONS if s in found]:
        F.append("sections out of order")

    # 6-7 deposit
    dep = re.search(r'(?is)#+\s*\d*\.?\s*The deposit(.*?)(?=\n#+\s*\d*\.?\s*Readings)', txt)
    if not dep:
        F.append("deposit section not parseable")
    else:
        d = dep.group(1)
        for k in DEPOSIT:
            if not re.search(r'(?i)\*\*' + re.escape(k), d):
                F.append(f"deposit missing {k!r}")
        cc = re.search(r'(?is)\*\*Completion criterion\.?\*\*(.{0,400})', d)
        if cc and not CHECKABLE.search(cc.group(1)):
            F.append("completion criterion is not checkable (no number, test or named rubric)")
        # 11 variant
        var = (hdr.get("Variant") or "").lower()
        if not var:
            var = "quantitative" if re.search(r'(?i)one-command|re-run script|\.py\b', d) else ""
        if var in QUANT and VARIANT_ESSAY.search(d):
            F.append("essay deposit inside a quantitative/engineering module")

    # 8 plus layer
    pl = re.search(r'(?is)#+\s*\d*\.?\s*Plus layer(.*?)(?=\n#+\s*\d*\.?\s*Provenance)', txt)
    if not pl:
        F.append("Plus layer section not parseable")
    else:
        for k in PLUS:
            if not re.search(r'(?i)\*\*' + re.escape(k), pl.group(1)):
                F.append(f"Plus layer missing {k!r}")

    # 9-10 parent
    par = re.search(r'(FSTEM-[A-Z][A-Z-]*-(?:R\d-)?\d+)', hdr.get("Parent", ""))
    if not par:
        F.append("Parent id not parseable")
    elif parents and par.group(1) not in parents:
        F.append(f"parent {par.group(1)} not found in rename map")
    mm = re.search(r'M(\d+)\s+of\s+(\d+)', hdr.get("Module", ""))
    if not mm:
        F.append("Module line missing 'M<n> of <N>'")
    elif int(mm.group(1)) > int(mm.group(2)):
        F.append(f"module index {mm.group(1)} exceeds count {mm.group(2)}")

    # 12 dead-substrate inversion
    if status == "dead":
        prov = re.search(r'(?is)#+\s*\d*\.?\s*Provenance(.*)', txt)
        if not prov or not re.search(r'(?i)(2026 practice|substrate.{0,40}dead|inversion)', prov.group(1)):
            F.append("substrate status 'dead' but Provenance does not declare the inversion")
    return base, F, W


def main(argv):
    parents = set()
    args = list(argv)
    if "--map" in args:
        i = args.index("--map")
        for r in csv.DictReader(open(args[i + 1], encoding="utf-8")):
            parents.add(r["fstem_id"])
        del args[i:i + 2]
    files = []
    for a in args:
        files += glob.glob(os.path.join(a, "*.md")) if os.path.isdir(a) else glob.glob(a)
    if not files:
        print("no files matched"); return 1
    today = datetime.date.today()
    nf = nw = 0
    tally = collections.Counter()
    for p in sorted(files):
        base, F, W = check(p, parents, today)
        if F or W:
            print(f"\n{base}")
            for f in F: print(f"  FAIL  {f}"); tally[f.split("(")[0][:40]] += 1
            for w in W: print(f"  WARN  {w}")
        nf += len(F); nw += len(W)
    print(f"\n{len(files)} files · {nf} FAIL · {nw} WARN")
    if tally:
        print("\nmost common failures:")
        for k, v in tally.most_common(8): print(f"  {v:4}  {k}")
    return 1 if nf else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
