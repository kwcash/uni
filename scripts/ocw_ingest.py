#!/usr/bin/env python3
"""
ocw_ingest.py — normalise the existing OCW artifacts into FSTEM asset records.

Three OCW harvests feed the repository and all three are the same document type,
so one ingest handles all three. Only the source_type differs.

    ocw_graduate       the parsed 231-course corpus
    ocw_undergraduate  the 78-course undergraduate harvest
    ocw_westciv        the 59-course Western Civilization set

Inputs accepted, in order of preference:

    --db ocw_database.json      the parsed corpus. Full records.
    --csv ocw_courses.csv       the flat table. Partial records.
    --codes westciv_codes.txt   a bare code list. Stub records, harvest_required.

A stub record is not a defect. It marks a course the curriculum names and the
corpus does not yet hold, which is exactly what the harvest queue needs.

Usage
-----
    python3 ocw_ingest.py --db ~/Projects/Consolidation/analysis/ocw_database.json \\
        --source-type ocw_graduate --out out/ocw_graduate.jsonl

    python3 ocw_ingest.py --codes fixtures/westciv_codes.txt \\
        --source-type ocw_westciv --out out/ocw_westciv.jsonl
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fstem_common import new_record, normalise_code, write_jsonl, validate, log  # noqa: E402

PARSER = "ocw_ingest"

# The trailing (?:-\d+)? matters. OCW distinguishes separate offerings of one
# number by a suffix, so 21W.747 and 21W.747-2 are two courses and not one.
CODE_RE = re.compile(r"\b(\d{1,2}[A-Za-z]?\.[0-9A-Za-z]{1,6}(?:-\d+)?|[A-Za-z]{2,4}\.[0-9A-Za-z]{1,6}(?:-\d+)?)\b")


# ---------------------------------------------------------------------------
# Level classification
# ---------------------------------------------------------------------------

GRAD_MARKERS = re.compile(r"\b(graduate|advanced graduate|G\b)", re.I)
UGRAD_MARKERS = re.compile(r"\b(undergraduate|U\b)", re.I)


def classify_level(stated: str | None, code: str, description: str = "") -> tuple[str | None, str | None, bool]:
    """
    Return (level_stated, level_inferred, conflict).

    MIT states the level on every course page. Trust the stated field first and
    infer only where it is absent. The inference is deliberately weak, because a
    strong inference is what produced the misfiled forty in the first place.
    """
    st = None
    if stated:
        s = str(stated)
        if UGRAD_MARKERS.search(s) and GRAD_MARKERS.search(s):
            st = "both"
        elif GRAD_MARKERS.search(s):
            st = "graduate"
        elif UGRAD_MARKERS.search(s):
            st = "undergraduate"

    # Weak inference from the MIT numbering convention. The convention holds in
    # the engineering and science departments and breaks in the humanities,
    # where 21L.422 Tragedy is an undergraduate literature subject. Numbering
    # inference is therefore advisory only. It never sets the rung. It only
    # raises a flag for a human to settle, which is precisely the discipline the
    # misfiled forty went missing for want of.
    inf = None
    dept = (code or "").split(".")[0].upper()
    numeric_convention_holds = bool(re.fullmatch(r"\d{1,2}[A-Z]?", dept)) and dept.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ") not in {"21", "24"}
    m = re.search(r"\.(\d{1,3})", code or "")
    if m and numeric_convention_holds:
        n = int(m.group(1))
        inf = "undergraduate" if n < 400 else "graduate"

    conflict = bool(st and inf and st != "both" and st != inf)
    return st, inf, conflict


LEVEL_TO_RUNG = {
    "graduate": "masters",
    "undergraduate": "undergraduate",
    "both": "undergraduate",  # dual-level material serves the lower rung first
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _ocw_block(rec: dict) -> dict:
    return {
        "bibliography": rec.get("bibliography") or [],
        "schedule": rec.get("schedule") or [],
        "grading": rec.get("grading") or [],
        "assignments": rec.get("assignments") or [],
        "resource_count": rec.get("resource_count"),
        "corpus_folder": rec.get("corpus_folder"),
        "held": bool(rec.get("held", rec.get("corpus_folder"))),
    }


def _norm_bib(items) -> list[dict]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            yr = re.search(r"\b(1[89]\d{2}|20\d{2})\b", it)
            out.append({"author": None, "title": None, "year": int(yr.group(1)) if yr else None, "raw": it})
        elif isinstance(it, dict):
            out.append({
                "author": it.get("author"),
                "title": it.get("title"),
                "year": it.get("year") if isinstance(it.get("year"), int) else None,
                "raw": it.get("raw") or json.dumps(it, ensure_ascii=False),
            })
    return out


def _norm_schedule(items) -> list[dict]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"session": None, "topic": it})
        elif isinstance(it, dict):
            out.append({"session": str(it.get("session") or it.get("ses") or "") or None,
                        "topic": it.get("topic") or it.get("title")})
    return out


def _norm_grading(items) -> list[dict]:
    out = []
    for it in items or []:
        if isinstance(it, dict):
            w = it.get("weight")
            if isinstance(w, str):
                m = re.search(r"(\d{1,3})\s*%", w)
                w = int(m.group(1)) / 100.0 if m else None
            elif isinstance(w, (int, float)):
                w = float(w) / 100.0 if w > 1 else float(w)
            else:
                w = None
            out.append({"component": str(it.get("component") or it.get("name") or "unnamed"), "weight": w})
    return out


def build(raw: dict, source_type: str, source_file: str, held: bool = True) -> dict | None:
    code = normalise_code(raw.get("code") or raw.get("course_code") or raw.get("inferred_code") or "")
    if not code:
        return None

    stated, inferred, conflict = classify_level(
        raw.get("level") or raw.get("level_stated"), code, raw.get("description") or ""
    )
    # The stated level sets the rung. Where MIT states nothing, the harvest the
    # course arrived in sets it. A numeric guess never sets it.
    rung = LEVEL_TO_RUNG.get(stated or "", None)
    if rung is None:
        rung = "masters" if source_type == "ocw_graduate" else "undergraduate"

    flags = []
    if not stated:
        flags.append("level_not_stated")
    if conflict:
        flags.append("level_conflict")
    if stated and inferred and stated != inferred and stated != "both":
        flags.append("level_review")
    if not held:
        flags.append("harvest_required")
    if not (raw.get("bibliography")):
        flags.append("no_bibliography")
    if raw.get("text_source") == "none":
        flags.append("no_course_text")
    elif raw.get("text_source") == "pages/*.html":
        flags.append("text_from_raw_pages")

    year = raw.get("year")
    if not year:
        m = re.search(r"\b(19|20)\d{2}\b", str(raw.get("term") or ""))
        year = int(m.group(0)) if m else None

    rec = new_record(
        source_type=source_type,
        code=code,
        title=raw.get("title") or code,
        provider="MIT_OCW",
        parser=PARSER,
        source_file=source_file,
        rung=rung,
        level_stated=stated,
        level_inferred=inferred,
        description=raw.get("description"),
        objectives=raw.get("objectives") or raw.get("learning_objectives") or [],
        prerequisites=raw.get("prerequisites") or [],
        term=raw.get("term"),
        year=year,
        instructors=raw.get("instructors") or [],
        url=raw.get("url") or (f"https://ocw.mit.edu/search/?q={code}" if code else None),
        ocw={
            "bibliography": _norm_bib(raw.get("bibliography")),
            "schedule": _norm_schedule(raw.get("schedule")),
            "grading": _norm_grading(raw.get("grading")),
            "assignments": [a if isinstance(a, str) else json.dumps(a) for a in (raw.get("assignments") or [])],
            "resource_count": raw.get("resource_count"),
            "corpus_folder": raw.get("corpus_folder"),
            "held": held,
        },
        extraction_flags=flags,
    )
    rec["level_conflict"] = conflict
    return rec


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_db(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        # Either {code: record} or {"courses": [...]}.
        if "courses" in data and isinstance(data["courses"], list):
            return data["courses"]
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                v.setdefault("code", k)
                out.append(v)
        return out
    return list(data)


def load_csv(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


COURSE_JSON_NAMES = ("data.json", "course.json", "metadata.json", "course_data.json")

# Section labels emitted by extract_course_semantic.py, extract_course_clean.py
# and extract_course_summaries.py, plus the plain OCW page names.
SECTION_HEADS = {
    "syllabus": "syllabus",
    "course overview": "description",
    "course metadata": "metadata",
    "description": "description",
    "readings": "readings",
    "bibliography": "readings",
    "calendar": "schedule",
    "schedule": "schedule",
    "assignments": "assignments",
    "grading": "grading",
    "objectives": "objectives",
    "learning objectives": "objectives",
    "course meets for": "meta",
    "prerequisites": "prerequisites",
}

# The pages the legacy extractors read, in the same priority order.
PAGE_SPECS = [
    ("pages/syllabus/index.html", "SYLLABUS"),
    ("pages/index.html", "COURSE OVERVIEW"),
    ("pages/calendar/index.html", "SCHEDULE"),
    ("pages/assignments/index.html", "ASSIGNMENTS"),
    ("pages/readings/index.html", "READINGS"),
]

# Output written by the legacy extractors, newest convention first.
EXTRACT_SUFFIXES = ("SEMANTIC.txt", "CLEAN.txt", "summary.txt")

GRADE_ROW_RE = re.compile(r"^(.{3,60}?)[\s.:|]{2,}(\d{1,3})\s*%\s*$", re.M)
GRADE_INLINE_RE = re.compile(r"^(.{3,60}?)\s+(\d{1,3})\s*%\s*$", re.M)
SEPARATOR_RE = re.compile(r"^[-=_*~\s]{4,}$")
SLUG_CODE_RE = re.compile(r"^([0-9]{1,2}[a-z]?-[0-9a-z]{1,6}(?:-[0-9]+)?|[a-z]{2,4}-[0-9a-z]{1,6})(?:-|$)", re.I)

# MIT prints the level in the course-info sidebar, and the legacy extractors keep
# it. This is the authoritative level field, and reading it is what retires the
# numbering guess that misfiled forty courses.
LEVEL_LINE_RE = re.compile(
    r"^\s*Level\s*:?\s*$\n+\s*(Undergraduate|Graduate)(?:\s*(?:,|and|/)\s*(Undergraduate|Graduate))?",
    re.M | re.I,
)
LEVEL_INLINE_RE = re.compile(r"\bLevel\s*:?\s*(Undergraduate|Graduate)(?:\s*(?:,|and|/)\s*(Undergraduate|Graduate))?", re.I)


class _OCWPageText(HTMLParser):
    """Minimal HTML to text, dropping script, style, nav, footer and aside."""

    SKIP = {"script", "style", "nav", "footer", "aside", "noscript", "head"}
    BLOCK = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip_depth = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK and self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK and self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            t = data.strip()
            if t:
                self.parts.append(t + " ")

    def text(self) -> str:
        out = "".join(self.parts)
        out = re.sub(r"[ \t]+", " ", out)
        return re.sub(r"\n{3,}", "\n\n", out)


BOILERPLATE_RE = re.compile(
    r"(Browse Course Material|Give Now|About OCW|Help & FAQs|Contact Us|"
    r"Over 2,500 courses|Freely sharing knowledge|Massachusetts Institute of Technology|"
    r"Creative Commons License|Terms and Conditions|You are leaving MIT|"
    r"Proud member of|^MIT OpenCourseWare$|^Menu$|^More Info$|^Pages$|Accessibility)",
    re.I | re.M,
)


def _html_to_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return ""
    p = _OCWPageText()
    try:
        p.feed(raw)
    except Exception:
        return ""
    lines = [l for l in p.text().split("\n") if not BOILERPLATE_RE.search(l.strip())]
    return "\n".join(lines)


def _slug_to_code(slug: str) -> str | None:
    """OCW folder slugs look like 21h-383-technology-and-the-global-economy-fall-2011."""
    m = SLUG_CODE_RE.match(slug)
    if not m:
        return None
    return normalise_code(m.group(1).replace("-", ".", 1))


def _title_from_slug(slug: str, code: str | None) -> str:
    rest = slug
    if code:
        rest = slug[len(code.replace(".", "-", 1)):]
    rest = re.sub(r"-(?:fall|spring|summer|winter|january|iap)-\d{4}$", "", rest, flags=re.I)
    return re.sub(r"[-_]+", " ", rest).strip().title() or slug


def _course_text(folder: str) -> tuple[str, str]:
    """
    Return (text, provenance).

    Prefers the legacy extractors' output where it exists, because that text is
    already deboilerplated and section-labelled. Falls back to reading the OCW
    pages directly, so this works on a directory the extractors never touched.
    """
    for suffix in EXTRACT_SUFFIXES:
        hits = sorted(glob.glob(os.path.join(folder, "*" + suffix)))
        if hits:
            try:
                with open(hits[0], "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read(600_000), os.path.basename(hits[0])
            except OSError:
                pass

    chunks = []
    for rel, label in PAGE_SPECS:
        fp = os.path.join(folder, rel)
        if os.path.exists(fp):
            t = _html_to_text(fp)
            if t.strip():
                chunks.append(f"{label}:\n{t}")
    if chunks:
        return "\n\n".join(chunks)[:600_000], "pages/*.html"

    for pat in ("*.txt", "*.md"):
        hits = sorted(glob.glob(os.path.join(folder, pat)))
        if hits:
            try:
                with open(hits[0], "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read(600_000), os.path.basename(hits[0])
            except OSError:
                pass
    return "", "none"


def _sectionise(text: str) -> dict:
    """Split a labelled course dump into its sections."""
    out: dict[str, list[str]] = {}
    current = None
    for line in text.split("\n"):
        stripped = line.strip()
        if SEPARATOR_RE.match(stripped):
            continue
        key = stripped.strip("#=-*: ").lower()
        if key in SECTION_HEADS and len(stripped) < 60:
            current = SECTION_HEADS[key]
            out.setdefault(current, [])
            continue
        if current and stripped:
            out[current].append(stripped)
    return {k: v for k, v in out.items() if v}


def _level_from_pages(folder: str) -> str | None:
    """
    Read the level straight off the OCW course-info sidebar.

    This exists because the legacy extractors delete it. Their sidebar filter
    lists 'Level', 'Undergraduate' and 'Graduate' as repeated furniture and
    strips them, so every SEMANTIC.txt, CLEAN.txt and summary.txt in the corpus
    is missing the one field that settles which rung a course belongs to. That
    deletion is the origin of the misfiled forty. Read the raw page instead.
    """
    for rel in ("pages/syllabus/index.html", "pages/index.html"):
        fp = os.path.join(folder, rel)
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read(400_000)
        except OSError:
            continue
        # Match inside the markup, before any boilerplate filtering can remove it.
        m = re.search(r"Level[^A-Za-z]{0,40}(Undergraduate|Graduate)"
                      r"(?:[^A-Za-z]{0,20}(Undergraduate|Graduate))?", raw, re.I | re.S)
        if m:
            vals = [g for g in m.groups() if g]
            return " and ".join(dict.fromkeys(v.title() for v in vals))
        plain = _html_to_text(fp)
        got = _find_level(plain)
        if got:
            return got
    return None


def _find_level(text: str) -> str | None:
    for rx in (LEVEL_LINE_RE, LEVEL_INLINE_RE):
        m = rx.search(text)
        if m:
            vals = [g for g in m.groups() if g]
            return " and ".join(dict.fromkeys(v.title() for v in vals))
    return None


def load_dir(root: str) -> list[dict]:
    """
    Walk a tree of OCW course folders and build a raw record per course.

    Reads, in order: a per-course JSON, the legacy extractor output, then the
    raw OCW pages. A record built from the folder slug alone is still a record,
    and its thin extraction is flagged downstream.
    """
    rows = []
    entries = [d for d in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")]
    for slug in entries:
        folder = os.path.join(root, slug)
        raw: dict = {"corpus_folder": slug, "held": True}

        meta = None
        for name in COURSE_JSON_NAMES:
            for fp in (os.path.join(folder, name), os.path.join(folder, "pages", "syllabus", name)):
                if os.path.exists(fp):
                    try:
                        with open(fp, "r", encoding="utf-8") as fh:
                            candidate = json.load(fh)
                        if isinstance(candidate, dict) and candidate:
                            meta = candidate
                            break
                    except (OSError, json.JSONDecodeError):
                        continue
            if meta:
                break

        if isinstance(meta, dict):
            raw["code"] = meta.get("primary_course_number") or meta.get("course_number") or meta.get("code")
            raw["title"] = meta.get("course_title") or meta.get("title") or meta.get("name")
            lvl = meta.get("level") or meta.get("course_level")
            if isinstance(lvl, list):
                lvl = " and ".join(str(x) for x in lvl)
            raw["level"] = lvl
            raw["description"] = meta.get("description") or meta.get("course_description")
            raw["term"] = meta.get("term") or meta.get("semester")
            raw["year"] = meta.get("year") if isinstance(meta.get("year"), int) else None
            instructors = meta.get("instructors") or meta.get("faculty") or []
            raw["instructors"] = [i if isinstance(i, str) else (i.get("name") or "") for i in instructors]
            raw["url"] = meta.get("url") or meta.get("course_url")

        if not raw.get("code"):
            raw["code"] = _slug_to_code(slug)
        if not raw.get("code"):
            continue
        if not raw.get("title"):
            raw["title"] = _title_from_slug(slug, raw["code"])
        if not raw.get("year"):
            ym = re.search(r"(19|20)\d{2}", slug)
            raw["year"] = int(ym.group(0)) if ym else None
        if not raw.get("term"):
            tm = re.search(r"(fall|spring|summer|winter|iap)[-_](\d{4})", slug, re.I)
            raw["term"] = f"{tm.group(1).title()} {tm.group(2)}" if tm else None

        text, provenance = _course_text(folder)
        raw["text_source"] = provenance

        # Always attempt the raw page for the level, even when extractor output
        # exists, because the extractors strip it.
        if not raw.get("level"):
            raw["level"] = _level_from_pages(folder)

        if text:
            if not raw.get("level"):
                raw["level"] = _find_level(text)
            sec = _sectionise(text)
            if not raw.get("description"):
                body = sec.get("description") or sec.get("syllabus") or []
                if body:
                    raw["description"] = " ".join(body)[:4000]
            raw["objectives"] = (sec.get("objectives") or [])[:40]
            raw["prerequisites"] = re.findall(
                r"\b(\d{1,2}[A-Za-z]?\.[0-9A-Za-z]{1,6})\b",
                " ".join((sec.get("prerequisites") or []) + (sec.get("syllabus") or [])[:40]),
            )[:20]
            raw["bibliography"] = (sec.get("readings") or [])[:400]
            raw["schedule"] = [{"session": None, "topic": t} for t in (sec.get("schedule") or [])[:200]]
            raw["assignments"] = (sec.get("assignments") or [])[:100]

            grading_text = "\n".join((sec.get("grading") or []) + (sec.get("syllabus") or []))
            rows_found = {}
            for rx in (GRADE_ROW_RE, GRADE_INLINE_RE):
                for m in rx.finditer(grading_text):
                    comp, pct = m.group(1).strip(), int(m.group(2))
                    if 0 < pct <= 100 and comp not in rows_found:
                        rows_found[comp] = pct
            raw["grading"] = [{"component": c, "weight": w} for c, w in rows_found.items()]

        raw["resource_count"] = len(glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True))
        rows.append(raw)
    return rows


# The breadcrumb line the semantic extractor preserves:
#   10.01 | Spring 2020 | Undergraduate Ethics for Engineers: Artificial Intelligence
# It carries code, term, level and title on one line, and it survived the
# boilerplate filter that deleted the sidebar Level field.
BREADCRUMB_RE = re.compile(
    r"(?m)^[ \t]*([0-9A-Za-z.\-]{2,12})[ \t]*\|[ \t]*"
    r"((?:January[ \t]+)?(?:IAP|Fall|Spring|Summer|Winter)[ \t]*(?:IAP[ \t]*)?\d{4})[ \t]*\|[ \t]*"
    r"(Undergraduate|Graduate)(?:[ \t]*(?:,|and)?[ \t]*(Undergraduate|Graduate))?[ \t]+(.{3,140})$")

CORPUS_RECORD_RE = re.compile(r"(?m)^COURSE:[ \t]*(.+?)[ \t]*$")
FRONTPAGE_RECORD_RE = re.compile(r"(?m)^=+[ \t]*\nFRONT PAGE - (.+?)[ \t]*\n=+[ \t]*$")


def load_corpus(path: str) -> list[dict]:
    """
    Read a consolidated corpus such as ALL_SEMANTIC_AGGRESSIVE.txt.

    Handles both delimiters in use. The semantic and clean extractors write
    `COURSE: <slug>`; the summary extractor writes a `FRONT PAGE - <slug>`
    banner. A parser that knows only the first finds zero records in the third
    file, which is the sort of silent zero that costs a week.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    for rx in (CORPUS_RECORD_RE, FRONTPAGE_RECORD_RE):
        parts = rx.split(text)
        if len(parts) > 2:
            records = [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
            break
    else:
        return []

    rows = []
    for slug, body in records:
        raw: dict = {"corpus_folder": slug, "held": True, "text_source": os.path.basename(path)}

        bc = BREADCRUMB_RE.search(body)
        if bc:
            raw["code"] = bc.group(1)
            raw["term"] = bc.group(2)
            lv = [g for g in (bc.group(3), bc.group(4)) if g]
            raw["level"] = " and ".join(dict.fromkeys(lv))
            raw["title"] = bc.group(5).strip()
        if not raw.get("code"):
            raw["code"] = _slug_to_code(slug.replace(".", "-", 1)) or slug
        if not raw.get("level"):
            raw["level"] = _find_level(body)
        if not raw.get("title"):
            raw["title"] = _title_from_slug(slug, None)
        if not raw.get("term"):
            tm = re.search(r"(fall|spring|summer|winter|iap)[-_ ](\d{4})", slug, re.I)
            raw["term"] = f"{tm.group(1).title()} {tm.group(2)}" if tm else None
        ym = re.search(r"(19|20)\d{2}", raw.get("term") or slug)
        raw["year"] = int(ym.group(0)) if ym else None

        sec = _sectionise(body)
        body_lines = sec.get("description") or sec.get("syllabus") or []
        if body_lines:
            raw["description"] = " ".join(body_lines)[:4000]
        raw["objectives"] = (sec.get("objectives") or [])[:40]
        raw["prerequisites"] = re.findall(
            r"\b(\d{1,2}[A-Za-z]?\.[0-9A-Za-z]{1,6})\b",
            " ".join((sec.get("prerequisites") or []) + (sec.get("syllabus") or [])[:60]))[:20]
        raw["bibliography"] = (sec.get("readings") or [])[:400]
        raw["schedule"] = [{"session": None, "topic": t} for t in (sec.get("schedule") or [])[:200]]
        raw["assignments"] = (sec.get("assignments") or [])[:100]

        grading_text = "\n".join((sec.get("grading") or []) + (sec.get("syllabus") or []))
        found = {}
        for rx2 in (GRADE_ROW_RE, GRADE_INLINE_RE):
            for m in rx2.finditer(grading_text):
                comp, pct = m.group(1).strip(), int(m.group(2))
                if 0 < pct <= 100 and comp not in found:
                    found[comp] = pct
        raw["grading"] = [{"component": c, "weight": w} for c, w in found.items()]
        rows.append(raw)
    return rows


def load_codes(path: str) -> list[dict]:
    """A bare list. One code per line, optional 'CODE | Title | Instructor'."""
    rows, seen = [], {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            m = CODE_RE.search(parts[0])
            if not m:
                continue
            code = normalise_code(m.group(1))
            row = {
                "code": code,
                "title": parts[1] if len(parts) > 1 and parts[1] else code,
                "instructors": [parts[2]] if len(parts) > 2 and parts[2] else [],
            }
            if code in seen:
                seen[code].setdefault("_dupe_titles", []).append(row["title"])
                continue
            seen[code] = row
            rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalise OCW artifacts into FSTEM asset records.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", help="ocw_database.json")
    src.add_argument("--csv", help="ocw_courses.csv")
    src.add_argument("--codes", help="Plain code list. One per line, optional '| Title | Instructor'.")
    src.add_argument("--corpus", help="Consolidated corpus text, e.g. ALL_SEMANTIC_AGGRESSIVE.txt.")
    src.add_argument("--dir", dest="dirroot", help="Directory of OCW course folders. Reads per-course JSON where present, otherwise the folder slug plus any plain text inside.")
    ap.add_argument("--source-type", required=True,
                    choices=["ocw_graduate", "ocw_undergraduate", "ocw_westciv"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.db:
        rows, path, held = load_db(args.db), args.db, True
    elif args.csv:
        rows, path, held = load_csv(args.csv), args.csv, True
    elif args.corpus:
        rows, path, held = load_corpus(args.corpus), args.corpus, True
    elif args.dirroot:
        rows, path, held = load_dir(args.dirroot), args.dirroot, True
    else:
        rows, path, held = load_codes(args.codes), args.codes, False

    log(f"loaded {len(rows)} rows from {os.path.basename(path)}")

    records, skipped = [], 0
    for raw in rows:
        rec = build(raw, args.source_type, os.path.abspath(path), held=held)
        if rec is None:
            skipped += 1
            continue
        dupes = raw.get("_dupe_titles") or []
        if dupes:
            rec["duplicates"] = dupes
            rec["extraction_flags"].append("duplicate_titles_in_source")
        records.append(rec)

    write_jsonl(records, args.out)
    conflicts = sum(1 for r in records if r["level_conflict"])
    unheld = sum(1 for r in records if not r["ocw"]["held"])
    log(f"wrote {len(records)} records to {args.out} ({skipped} skipped)")
    log(f"  level conflicts: {conflicts}")
    log(f"  harvest required: {unheld}")

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
