#!/usr/bin/env python3
"""Local stand-in for fstem_prepackage_qc.py, implementing handoff R5 sections 7 to 10.
Not the installed v023 tool. Used only to ship a clean batch from this thread."""
import re, sys, glob, os

SECTIONS = ["## 1. What this module teaches", "## 2. Substrate", "## 3. Currency",
            "## 4. Sessions", "## 5. The deposit", "## 6. Readings",
            "## 7. Plus layer", "## 8. Provenance"]
PLUS = ["Currency slot:", "Patent hook:", "Research thread:", "Doctrine-to-code artifact:",
        "Red Cell target:", "Sponsor slot:", "Micro-credential:", "Reproducibility deposit:"]

def commas_ok(line):
    s = line.strip()
    if not s or s.startswith("|") or s.startswith("#") or s.startswith("```"):
        return True
    if s.startswith("**Claim.**") or s.startswith("**Evidence.**"):
        return True
    if s.startswith("**Completion criterion.**") or s.startswith("**Re-run.**"):
        return True
    if re.match(r"^\d+\.\s+\*\*", s):          # reading-list entry
        return True
    if s.startswith("- ") or s.startswith("**Vintage:**"):
        return True
    for p in PLUS:                              # plus-layer target lists
        if s.startswith("**" + p[:-1]):
            return True
    if s.startswith("**Substrate:**") or s.startswith("**Parent:**"):
        return True
    if s.startswith("**DEAD-LAYER:**"):
        return True
    return s.count(",") <= 2

def check(path):
    errs, warns = [], []
    raw = open(path, encoding="utf-8").read()
    lines = raw.split("\n")
    name = os.path.basename(path)

    # --- header, section 8 ---
    if not lines[0].startswith("# FSTEM-"):
        errs.append("title line missing")
    if len(lines) > 1 and lines[1].strip() == "":
        errs.append("blank line after title")
    hdr = lines[1:10]
    for i, key in enumerate(["**Module status:", "**Parent:**", "**Module:**", "**Rung:**",
                             "**Track overlay:**", "**Substrate:**", "**Currency refreshed:**",
                             "**Micro-credential:**", "**Variant:**"]):
        if i < len(hdr) and not hdr[i].startswith(key):
            # vintage line legitimately sits under Substrate, shifting the block
            if not any(l.startswith(key) for l in lines[1:12]):
                errs.append("header line missing: %s" % key)
    for l in lines[1:12]:
        if l.startswith("---"):
            errs.append("rule beneath header block")

    # --- dashes, section 10 ---
    for n, l in enumerate(lines, 1):
        if l.startswith("# "):
            continue
        if "\u2014" in l or "\u2013" in l:
            errs.append("line %d: em or en dash" % n)

    # --- openers, section 10 ---
    for n, l in enumerate(lines, 1):
        s = re.sub(r"^\*+", "", l.strip())
        if s.startswith("It is ") or s.startswith("There are ") or s.startswith("There is "):
            errs.append("line %d: passive opener" % n)

    # --- commas, section 10 ---
    for n, l in enumerate(lines, 1):
        if not commas_ok(l):
            errs.append("line %d: %d commas" % (n, l.count(",")))

    # --- section order, section 7 ---
    found = [l.strip() for l in lines if l.startswith("## ")]
    if found != SECTIONS:
        errs.append("section order or set wrong: %s" % (found,))

    # --- deposit block, section 9 ---
    try:
        dep = raw.split("## 5. The deposit")[1].split("## 6. Readings")[0]
    except IndexError:
        dep = ""
        errs.append("no deposit block")
    if dep:
        for f in ["**Claim.**", "**Evidence.**", "**Completion criterion.**", "**Re-run.**"]:
            if f not in dep:
                errs.append("deposit missing %s" % f)
        if re.search(r"\bpapers?\b", dep, re.I):
            errs.append("deposit contains the word 'paper'")
        if not re.search(r"at least \d", dep):
            errs.append("deposit missing 'at least N' form")
        if not re.search(r"to within \d+ percent", dep):
            errs.append("deposit missing 'to within N percent' form")
        # Word-boundary match, confirmed against the installed v023 in batch 027.
        # A bare substring test also fires on the legal "that at least".
        if re.search(r"\bat at least\b", dep):
            errs.append("deposit contains 'at at least'")
        g = re.search(r"built in ([^\s.]+)", dep)
        if not g:
            errs.append("gate sentence missing 'built in <MODULE-ID>'")
        elif "*" in g.group(1):
            errs.append("gate module id is bolded")
        if "eight or more weeks prior" not in dep:
            errs.append("gate amendment wording missing")

    # --- plus layer, section 6 ---
    try:
        pl = raw.split("## 7. Plus layer")[1].split("## 8. Provenance")[0]
    except IndexError:
        pl = ""
        errs.append("no plus layer")
    if pl:
        order = [p for p in PLUS if p in pl]
        if order != PLUS:
            errs.append("plus layer fields missing or out of order")
        for p in PLUS:
            if re.search(re.escape(p[:-1]) + r"\.", pl):
                errs.append("plus layer uses period separator: %s" % p)

    # --- divergence, section 8 ---
    sub = [l for l in lines if l.startswith("**Substrate:**")]
    if sub and "diverging from course status" in sub[0]:
        s2 = raw.split("## 2. Substrate")[1].split("## 3. Currency")[0]
        if "diverg" not in s2:
            errs.append("header declares divergence, section 2 does not")
        s8 = raw.split("## 8. Provenance")[1]
        if "diverg" not in s8:
            errs.append("header declares divergence, section 8 does not")

    # --- vintage, section 8 ---
    m = re.search(r"(Fall|Spring|Summer|IAP)\s+(\d{4})", sub[0] if sub else "")
    if m:
        yr = int(m.group(2))
        has = any(l.startswith("**Vintage:**") for l in lines)
        if (yr < 2005 or yr > 2020) and not has:
            errs.append("vintage line required for %d" % yr)
        if 2005 <= yr <= 2020 and has:
            errs.append("vintage line present inside window")
    return errs, warns

def main():
    files = sorted(glob.glob(os.path.join(sys.argv[1], "*.md")))
    tf = tw = 0
    print("local_qc v027-thread  (stand-in, not the installed v023)")
    for f in files:
        e, w = check(f)
        tf += len(e); tw += len(w)
        if e:
            print("FAIL %s" % os.path.basename(f))
            for x in e:
                print("      %s" % x)
    print("\n%d files · %d FAIL · %d WARN" % (len(files), tf, tw))
    return 1 if tf else 0

if __name__ == "__main__":
    sys.exit(main())
