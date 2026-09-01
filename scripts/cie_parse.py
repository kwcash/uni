#!/usr/bin/env python3
"""
cie_parse.py — Cambridge IGCSE and A-Level syllabus PDFs into FSTEM asset records.

The only genuinely new document type in the repository. OCW gives a course with
readings and a schedule. CIE gives a syllabus with assessment objectives, subject
content and papers. The superset schema holds both.

Every extraction is best effort. What the parser cannot find it records in
extraction_flags rather than failing the file. A syllabus that parses at eighty
percent and names its missing twenty beats a parser that halts.

Usage
-----
    python3 cie_parse.py --in ~/Projects/CIE/igcse --out out/cie_igcse.jsonl
    python3 cie_parse.py --in ~/Projects/CIE/alevel --out out/cie_alevel.jsonl --qualification A_Level
    python3 cie_parse.py --in fixtures/ --out out/test.jsonl --ext .txt

Text extraction order: pdfminer.six, then pypdf, then a plain .txt sibling.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import new_record, write_jsonl, validate, log  # noqa: E402

PARSER = "cie_parse"

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(path: str) -> str:
    if path.lower().endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    # A pre-extracted sibling wins. Offline machines often have these already.
    sibling = os.path.splitext(path)[0] + ".txt"
    if os.path.exists(sibling):
        with open(sibling, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    try:
        from pdfminer.high_level import extract_text as _pdfminer
        return _pdfminer(path) or ""
    except Exception:
        pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:
        log(f"  ! text extraction failed for {os.path.basename(path)}: {exc}")
        return ""


def clean(text: str) -> str:
    """Strip running headers, footers and page furniture."""
    lines = []
    for line in text.split("\n"):
        s = line.rstrip()
        if re.match(r"^\s*(Back to contents page|www\.cambridgeinternational\.org)", s, re.I):
            continue
        if re.match(r"^\s*Cambridge (IGCSE|International|O Level).{0,80}(Syllabus for examination|\d{4})\s*$", s, re.I):
            continue
        if re.match(r"^\s*\d{1,3}\s*$", s):  # bare page numbers
            continue
        lines.append(s)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


# ---------------------------------------------------------------------------
# Field extractors. Each returns (value, flag_or_None).
# ---------------------------------------------------------------------------

QUAL_PATTERNS = [
    (r"Cambridge\s+International\s+AS\s*&\s*A\s*Level", "AS_and_A_Level"),
    (r"Cambridge\s+International\s+A\s*Level", "A_Level"),
    (r"Cambridge\s+International\s+AS\s*Level", "AS_Level"),
    (r"Cambridge\s+IGCSE", "IGCSE"),
    (r"Cambridge\s+O\s*Level", "O_Level"),
]


def find_qualification(text: str):
    head = text[:6000]
    for pat, name in QUAL_PATTERNS:
        if re.search(pat, head, re.I):
            return name, None
    for pat, name in QUAL_PATTERNS:
        if re.search(pat, text, re.I):
            return name, None
    return None, "qualification_not_found"


def find_code(text: str):
    """CIE syllabus codes are four digits, usually adjacent to the subject name."""
    head = text[:6000]
    # Strongest signal: the code sits on the cover next to the qualification
    # name. The subject often wraps to the next line, so newlines must pass.
    m = re.search(r"Cambridge\s+(?:IGCSE|International|O\s*Level)[^\d]{0,90}?\b(\d{4})\b", head, re.I | re.S)
    if m and not (1990 <= int(m.group(1)) <= 2100):
        return m.group(1), None
    # Next: "Syllabus code 0580" or "(0580)".
    m = re.search(r"[Ss]yllabus\s+code[:\s]+(\d{4})", head)
    if m:
        return m.group(1), None
    m = re.search(r"\((\d{4})\)", head)
    if m:
        return m.group(1), None
    # Last resort: any bare four-digit token that is not a year.
    for cand in re.findall(r"\b(\d{4})\b", head):
        if not (1990 <= int(cand) <= 2100):
            return cand, "code_low_confidence"
    return None, "code_not_found"


def find_title(text: str, code: str | None):
    head = text[:4000]
    if code:
        # "Cambridge IGCSE(TM) Mathematics 0580"
        m = re.search(
            r"Cambridge[^\n]{0,60}?(?:IGCSE|Level)\s*[™®]?\s*(.{2,60}?)\s*" + re.escape(code),
            head, re.I | re.S,
        )
        if m:
            t = re.sub(r"\s+", " ", m.group(1)).strip(" -–:")
            if 2 <= len(t) <= 60:
                return t, None
    m = re.search(r"^\s*(?:Cambridge[^\n]*\n)?\s*([A-Z][A-Za-z&,'()\- ]{3,60})\s*$", head, re.M)
    if m:
        return m.group(1).strip(), "title_low_confidence"
    return None, "title_not_found"


def find_exam_years(text: str):
    head = text[:8000]
    patterns = [
        r"[Ff]or examination (?:from|in)\s+((?:\d{4}[,\s and]*)+)",
        r"[Ss]yllabus for examination in\s+((?:\d{4}[,\s and]*)+)",
        r"[Uu]se this syllabus for exams? in\s+((?:\d{4}[,\s and]*)+)",
        r"[Ff]irst examination(?:s)? (?:from|in)\s+((?:\d{4}[,\s and]*)+)",
    ]
    m = None
    for pat in patterns:
        m = re.search(pat, head)
        if m:
            break
    if m:
        years = sorted(int(y) for y in re.findall(r"\d{4}", m.group(1)))
        if years:
            return years[0], years[-1], None
    years = sorted({int(y) for y in re.findall(r"\b(20[2-4]\d)\b", head)})
    if years:
        return years[0], years[-1], "exam_years_low_confidence"
    return None, None, "exam_years_not_found"


AO_RE = re.compile(r"\bAO(\d)\b[\s:.\-–]*([A-Z][^\n]{0,120})?")
AO_WEIGHT_RE = re.compile(r"\bAO(\d)\b[^\n%]{0,120}?(\d{1,3})\s*%")


def find_assessment_objectives(text: str):
    aos: dict[str, dict] = {}
    for m in AO_RE.finditer(text):
        aid = f"AO{m.group(1)}"
        desc = (m.group(2) or "").strip(" .:-–")
        # Reject fragments that are clearly table noise.
        if desc and not re.match(r"^(and|or|weighting|\d)", desc, re.I):
            prev = aos.get(aid, {}).get("description")
            if not prev or len(desc) > len(prev):
                aos.setdefault(aid, {"ao_id": aid, "description": None, "weight": None})
                aos[aid]["description"] = desc
        else:
            aos.setdefault(aid, {"ao_id": aid, "description": None, "weight": None})

    for m in AO_WEIGHT_RE.finditer(text):
        aid = f"AO{m.group(1)}"
        pct = int(m.group(2))
        if 0 < pct <= 100:
            aos.setdefault(aid, {"ao_id": aid, "description": None, "weight": None})
            if aos[aid]["weight"] is None:
                aos[aid]["weight"] = round(pct / 100.0, 4)

    out = [aos[k] for k in sorted(aos)]
    if not out:
        return [], "assessment_objectives_not_found"
    if all(a["weight"] is None for a in out):
        return out, "ao_weights_not_found"
    return out, None


PAPER_LINE_RE = re.compile(r"^\s*Paper\s+(\d[A-Za-z]?)\b\s*(.*)$", re.I)
DUR_HM_RE = re.compile(r"(\d+)\s*hours?(?:\s*(\d{1,2})\s*(?:minutes|mins))?", re.I)
DUR_M_RE = re.compile(r"(\d{1,3})\s*(?:minutes|mins)\b", re.I)
MARKS_RE = re.compile(r"(\d{1,3})\s*marks\b", re.I)
PCT_RE = re.compile(r"(\d{1,3})\s*%")


def find_papers(text: str):
    """Line-based. A paper row carries its duration, marks and weight on one line."""
    papers: dict[str, dict] = {}
    for line in text.split("\n"):
        m = PAPER_LINE_RE.match(line)
        if not m:
            continue
        pid, rest = m.group(1), m.group(2)

        duration = None
        dm = DUR_HM_RE.search(rest)
        if dm:
            duration = int(dm.group(1)) * 60 + (int(dm.group(2)) if dm.group(2) else 0)
        else:
            dm = DUR_M_RE.search(rest)
            if dm:
                duration = int(dm.group(1))

        mk = MARKS_RE.search(rest)
        marks = int(mk.group(1)) if mk else None

        pc = PCT_RE.search(rest)
        weight = round(int(pc.group(1)) / 100.0, 4) if pc else None

        # The paper name is whatever precedes the first numeric field.
        title = rest
        for pat in (DUR_HM_RE, DUR_M_RE, MARKS_RE, PCT_RE):
            hit = pat.search(title)
            if hit:
                title = title[: hit.start()]
        title = re.sub(r"\s+", " ", title).strip(" -–:,") or None

        rec = papers.setdefault(pid, {"paper": pid, "title": None, "duration_min": None, "marks": None, "weight": None})
        for key, val in (("title", title), ("duration_min", duration), ("marks", marks), ("weight", weight)):
            if rec[key] is None and val is not None:
                rec[key] = val

    out = [papers[k] for k in sorted(papers, key=lambda s: (len(s), s))]
    if not out:
        return [], "papers_not_found"
    if all(p["duration_min"] is None and p["marks"] is None and p["weight"] is None for p in out):
        return out, "paper_details_not_found"
    return out, None


# Subject content headings: "1 Number", "1.1 Types of number", "C1.1", "E1.1".
UNIT_RE = re.compile(r"^\s*([CE]?\d{1,2}(?:\.\d{1,2}){0,2})\s+([A-Z][^\n]{2,90})\s*$", re.M)


def find_subject_content(text: str):
    """Segment the subject content section into ordered units with learning points."""
    start = 0
    m = re.search(r"^\s*\d?\s*Subject content\s*$", text, re.M | re.I)
    if m:
        start = m.end()
    body = text[start:]

    end = re.search(r"^\s*\d?\s*(Details of the assessment|What else you need to know)\s*$", body, re.M | re.I)
    if end:
        body = body[: end.start()]

    matches = list(UNIT_RE.finditer(body))
    if not matches:
        return [], "subject_content_not_found"

    units = []
    for i, mm in enumerate(matches):
        unit_id = mm.group(1)
        title = re.sub(r"\s+", " ", mm.group(2)).strip()
        chunk = body[mm.end(): matches[i + 1].start() if i + 1 < len(matches) else len(body)]

        points = []
        for line in chunk.split("\n"):
            s = line.strip(" \t•·–-‣")
            if len(s) < 4:
                continue
            if re.match(r"^(Notes and examples|Notes|Examples)\b", s, re.I):
                continue
            if re.match(r"^[CE]?\d{1,2}(\.\d{1,2}){0,2}\s", s):
                continue
            points.append(re.sub(r"\s+", " ", s))

        units.append({
            "unit_id": unit_id,
            "title": title,
            "learning_points": points[:40],
            "extended_only": unit_id.startswith("E"),
        })

    return units, None


GRADE_RE = re.compile(r"^\s*Grade\s+([A-U]\*?|\d)\b[\s:.\-–]*(.*)$", re.M)


def find_grade_descriptors(text: str):
    """A descriptor runs from its Grade heading to the next heading or blank block."""
    out, seen = [], set()
    matches = list(GRADE_RE.finditer(text))
    for i, m in enumerate(matches):
        g = m.group(1)
        if g in seen:
            continue
        seen.add(g)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = m.group(2) + "\n" + text[m.end(): end]
        # Stop at the first blank line: the descriptor is one paragraph.
        chunk = re.split(r"\n\s*\n", chunk, maxsplit=1)[0]
        desc = re.sub(r"\s+", " ", chunk).strip(" .:-–") or None
        out.append({"grade": g, "descriptor": desc[:600] if desc else None})
    if not out:
        return [], "grade_descriptors_not_found"
    return out, None


def find_aims(text: str):
    m = re.search(r"^\s*(?:\d[\.\d]*\s+)?Aims\s*$(.{0,3000}?)(?=^\s*(?:\d[\.\d]*\s+)?[A-Z][A-Za-z ]{3,60}\s*$)",
                  text, re.M | re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).split("\n"):
        s = line.strip(" \t•·–-‣")
        if len(s) > 12 and not re.match(r"^(The aims|These aims)", s, re.I):
            out.append(re.sub(r"\s+", " ", s))
    return out[:20]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

QUAL_TO_SOURCE_TYPE = {
    "IGCSE": "cie_igcse",
    "O_Level": "cie_igcse",
    "AS_Level": "cie_alevel",
    "A_Level": "cie_alevel",
    "AS_and_A_Level": "cie_alevel",
}


def parse_file(path: str, force_qualification: str | None = None) -> dict | None:
    raw = extract_text(path)
    if not raw.strip():
        log(f"  ! empty text: {os.path.basename(path)}")
        return None
    text = clean(raw)

    # Guard. A CIE syllabus names the qualification, or numbers its subject
    # content, or lists assessment objectives. A file that does none of the
    # three is not a syllabus, and parsing it produces a record made entirely
    # of flags.
    signals = (
        bool(re.search(r"Cambridge\s+(IGCSE|International|O\s*Level)", text, re.I)),
        bool(re.search(r"^\s*\d?\s*Subject content\s*$", text, re.M | re.I)),
        bool(re.search(r"\bAO[123]\b", text)),
    )
    if sum(signals) == 0:
        log(f"  - skipped, not a CIE syllabus: {os.path.basename(path)}")
        return None

    flags: list[str] = []

    qual, f = (force_qualification, None) if force_qualification else find_qualification(text)
    if f:
        flags.append(f)
    if not qual:
        qual = "IGCSE" if re.search(r"igcse", path, re.I) else "A_Level"
        flags.append("qualification_guessed_from_filename")

    code, f = find_code(text)
    if f:
        flags.append(f)
    if not code:
        code = os.path.splitext(os.path.basename(path))[0][:16]
        flags.append("code_from_filename")

    title, f = find_title(text, code)
    if f:
        flags.append(f)
    if not title:
        title = os.path.splitext(os.path.basename(path))[0].replace("_", " ")

    first_year, last_year, f = find_exam_years(text)
    if f:
        flags.append(f)

    aos, f = find_assessment_objectives(text)
    if f:
        flags.append(f)
    papers, f = find_papers(text)
    if f:
        flags.append(f)
    content, f = find_subject_content(text)
    if f:
        flags.append(f)
    grades, f = find_grade_descriptors(text)
    if f:
        flags.append(f)

    tiers = []
    if re.search(r"\bCore\b", text) and re.search(r"\bExtended\b", text):
        tiers = ["Core", "Extended"]

    source_type = QUAL_TO_SOURCE_TYPE.get(qual, "cie_igcse")

    return new_record(
        source_type=source_type,
        code=code,
        title=title,
        provider="Cambridge_CIE",
        parser=PARSER,
        source_file=os.path.abspath(path),
        level_stated=qual,
        level_inferred=qual,
        objectives=find_aims(text),
        year=first_year,
        cie={
            "syllabus_code": code if re.fullmatch(r"\d{4}", code or "") else None,
            "qualification": qual,
            "first_exam_year": first_year,
            "last_exam_year": last_year,
            "assessment_objectives": aos,
            "subject_content": content,
            "papers": papers,
            "grade_descriptors": grades,
            "tiers": tiers,
        },
        extraction_flags=flags,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse CIE syllabi into FSTEM asset records.")
    ap.add_argument("--in", dest="indir", required=True, help="Directory of syllabus files.")
    ap.add_argument("--out", dest="outfile", required=True, help="Output JSONL path.")
    ap.add_argument("--ext", default=".pdf", help="File extension to scan. Default .pdf")
    ap.add_argument("--qualification", default=None,
                    choices=["IGCSE", "O_Level", "AS_Level", "A_Level", "AS_and_A_Level"],
                    help="Force the qualification instead of detecting it.")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.indir, "**", "*" + args.ext), recursive=True))
    if not paths:
        log(f"no *{args.ext} files under {args.indir}")
        return 1

    log(f"parsing {len(paths)} files from {args.indir}")
    records, failed = [], 0
    for p in paths:
        try:
            rec = parse_file(p, args.qualification)
        except Exception as exc:
            log(f"  ! {os.path.basename(p)}: {exc}")
            rec = None
        if rec is None:
            failed += 1
            continue
        records.append(rec)
        nflag = len(rec["extraction_flags"])
        mark = "ok " if nflag == 0 else f"{nflag} flag(s)"
        log(f"  {rec['code']:<8} {rec['title'][:44]:<44} {mark}")

    write_jsonl(records, args.outfile)
    log(f"\nwrote {len(records)} records to {args.outfile} ({failed} failed)")

    errors = validate(records)
    if errors:
        log(f"SCHEMA ERRORS ({len(errors)}):")
        for e in errors[:20]:
            log("  " + e)
        return 2
    log("schema: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
