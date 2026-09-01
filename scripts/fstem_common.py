"""
fstem_common.py — shared record construction, ladder constants and validation.

Every parser in this directory emits records through new_record() so that one
schema governs the whole repository. Nothing else in the pipeline builds a dict
by hand.

Offline safe. Standard library plus an optional jsonschema check.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

PARSER_VERSION = "1.0.0"

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "fstem_asset.schema.json")

# ---------------------------------------------------------------------------
# The ladder. Grade 9 to Grade 100.
# ---------------------------------------------------------------------------

RUNGS = {
    "secondary_lower": {"grades": [9, 11], "verb": "encounter", "gate_out": "G1"},
    "secondary_upper": {"grades": [12, 13], "verb": "derive", "gate_out": "G2"},
    "undergraduate": {"grades": [14, 17], "verb": "reproduce", "gate_out": "G3"},
    "masters": {"grades": [18, 19], "verb": "apply", "gate_out": "G4"},
    "research": {"grades": [20, 23], "verb": "extend", "gate_out": "G5"},
}

SOURCE_TYPE_DEFAULT_RUNG = {
    "ocw_graduate": "masters",
    "ocw_undergraduate": "undergraduate",
    "ocw_westciv": "undergraduate",
    "cie_igcse": "secondary_lower",
    "cie_alevel": "secondary_upper",
}

VERTICALS = [
    "mathematics",
    "engineering",
    "computer_science",
    "economics",
    "finance",
    "power_currency",
    "total_domain_war",
    "general_core",
]

THREADS = ["T-JOULE", "T-FLOW", "T-MACHINE", "T-DOMAIN", "T-INTEGRATE", "T-METHOD"]


# ---------------------------------------------------------------------------
# Record construction
# ---------------------------------------------------------------------------

def _provider_short(provider: str) -> str:
    return "cie" if provider == "Cambridge_CIE" else "ocw"


def normalise_code(code: str) -> str:
    """Trim, collapse whitespace and normalise case for MIT course codes."""
    if code is None:
        return ""
    c = re.sub(r"\s+", "", str(code)).strip()
    # MIT codes carry an uppercase letter suffix: 21H.383, ESD.260J, 6.041SC.
    # Lowercase input is common in scraped slugs, so lift it.
    if re.match(r"^[0-9]{1,2}[A-Za-z]?\.", c) or re.match(r"^[A-Za-z]{2,4}\.", c):
        c = c.upper()
    return c


def new_record(
    *,
    source_type: str,
    code: str,
    title: str,
    provider: str,
    parser: str,
    source_file: str | None = None,
    **kwargs,
) -> dict:
    """Build a schema-shaped record. Unknown keyword arguments raise."""
    if source_type not in SOURCE_TYPE_DEFAULT_RUNG:
        raise ValueError(f"unknown source_type: {source_type}")

    code = normalise_code(code)
    rung = kwargs.pop("rung", None) or SOURCE_TYPE_DEFAULT_RUNG[source_type]
    band = RUNGS.get(rung, {}).get("grades")
    verb = RUNGS.get(rung, {}).get("verb")

    rec = {
        "asset_id": f"{_provider_short(provider)}:{code}",
        "source_type": source_type,
        "contributing_sources": [source_type],
        "code": code,
        "title": (title or "").strip(),
        "provider": provider,
        "rung": rung,
        "grade_band": list(band) if band else None,
        "verb": verb,
        "level_stated": kwargs.pop("level_stated", None),
        "level_inferred": kwargs.pop("level_inferred", None),
        "level_conflict": False,
        "verticals": [],
        "threads": [],
        "description": kwargs.pop("description", None),
        "objectives": kwargs.pop("objectives", []) or [],
        "prerequisites": [normalise_code(p) for p in (kwargs.pop("prerequisites", []) or [])],
        "term": kwargs.pop("term", None),
        "year": kwargs.pop("year", None),
        "instructors": kwargs.pop("instructors", []) or [],
        "url": kwargs.pop("url", None),
        "ocw": kwargs.pop("ocw", None),
        "cie": kwargs.pop("cie", None),
        "articulation": {"feeds": [], "fed_by": [], "gate": RUNGS.get(rung, {}).get("gate_out")},
        "duplicates": [],
        "extraction_flags": kwargs.pop("extraction_flags", []) or [],
        "provenance": {
            "harvested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_file": source_file,
            "parser": parser,
            "parser_version": PARSER_VERSION,
            "notes": kwargs.pop("notes", None),
        },
    }

    if kwargs:
        raise TypeError(f"new_record got unexpected arguments: {sorted(kwargs)}")
    return rec


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(records: list[dict], schema_path: str = SCHEMA_PATH) -> list[str]:
    """Return a list of human-readable errors. Empty list means the set is clean."""
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema not installed; validation skipped"]

    with open(schema_path, "r", encoding="utf-8") as fh:
        schema = json.load(fh)
    validator = jsonschema.Draft202012Validator(schema)

    errors = []
    for rec in records:
        for err in validator.iter_errors(rec):
            path = ".".join(str(p) for p in err.path) or "<root>"
            errors.append(f"{rec.get('asset_id', '?')} :: {path} :: {err.message}")
    return errors


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def write_jsonl(records: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def log(msg: str) -> None:
    print(msg, file=sys.stderr)
