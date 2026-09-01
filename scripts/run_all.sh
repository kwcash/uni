#!/usr/bin/env bash
#
# run_all.sh — build the FSTEM repository from all five harvests.
#
# Runs offline. No network. Every stage writes to out/ and nothing deletes.
#
# Point the four SRC_ variables at your real paths, then run:
#
#     bash scripts/run_all.sh
#
# To rehearse against the shipped fixtures instead, run:
#
#     FSTEM_FIXTURES=1 bash scripts/run_all.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
OUT="$ROOT/out"
mkdir -p "$OUT"

# ---------------------------------------------------------------------------
# Sources. Edit these five lines and nothing else.
# ---------------------------------------------------------------------------
SRC_OCW_DB="${SRC_OCW_DB:-$HOME/Projects/Consolidation/analysis/ocw_database.json}"
SRC_OCW_UNDERGRAD="${SRC_OCW_UNDERGRAD:-$HOME/Projects/Consolidation/analysis/ocw_undergrad_database.json}"
SRC_WESTCIV_CODES="${SRC_WESTCIV_CODES:-$ROOT/fixtures/ocw/westciv_codes.txt}"
SRC_CIE_IGCSE="${SRC_CIE_IGCSE:-$HOME/Projects/CIE/igcse}"
SRC_CIE_ALEVEL="${SRC_CIE_ALEVEL:-$HOME/Projects/CIE/alevel}"

EXT="${EXT:-.pdf}"
if [[ "${FSTEM_FIXTURES:-0}" == "1" ]]; then
  SRC_CIE_IGCSE="$ROOT/fixtures/cie"
  SRC_CIE_ALEVEL="$ROOT/fixtures/cie"
  SRC_OCW_DB=""
  SRC_OCW_UNDERGRAD=""
  EXT=".txt"
  echo "== fixture rehearsal =="
fi

say() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
# Stage 1. Ingest.
# ---------------------------------------------------------------------------
say "1. Ingest"

if [[ -n "$SRC_OCW_DB" && -f "$SRC_OCW_DB" ]]; then
  python3 "$HERE/ocw_ingest.py" --db "$SRC_OCW_DB" \
    --source-type ocw_graduate --out "$OUT/ocw_graduate.jsonl"
else
  echo "skip ocw_graduate: $SRC_OCW_DB not found"
fi

if [[ -n "$SRC_OCW_UNDERGRAD" && -f "$SRC_OCW_UNDERGRAD" ]]; then
  python3 "$HERE/ocw_ingest.py" --db "$SRC_OCW_UNDERGRAD" \
    --source-type ocw_undergraduate --out "$OUT/ocw_undergraduate.jsonl"
else
  echo "skip ocw_undergraduate: $SRC_OCW_UNDERGRAD not found"
fi

if [[ -f "$SRC_WESTCIV_CODES" ]]; then
  python3 "$HERE/ocw_ingest.py" --codes "$SRC_WESTCIV_CODES" \
    --source-type ocw_westciv --out "$OUT/ocw_westciv.jsonl"
fi

if [[ -d "$SRC_CIE_IGCSE" ]]; then
  python3 "$HERE/cie_parse.py" --in "$SRC_CIE_IGCSE" --ext "$EXT" \
    --out "$OUT/cie_igcse.jsonl"
fi

if [[ -d "$SRC_CIE_ALEVEL" && "$SRC_CIE_ALEVEL" != "$SRC_CIE_IGCSE" ]]; then
  python3 "$HERE/cie_parse.py" --in "$SRC_CIE_ALEVEL" --ext "$EXT" \
    --out "$OUT/cie_alevel.jsonl"
fi

# ---------------------------------------------------------------------------
# Stage 2. Merge, dedupe, level-correct.
# ---------------------------------------------------------------------------
say "2. Merge and dedupe"
python3 "$HERE/merge_dedupe.py" \
  --in "$OUT"/ocw_*.jsonl "$OUT"/cie_*.jsonl \
  --out "$OUT/fstem_repository.jsonl" \
  --level-overrides "$ROOT/overrides/level_corrections.csv" \
  --exclude "$ROOT/overrides/exclusions.txt" \
  --expect ocw_westciv=59 ocw_graduate=231 ocw_undergraduate=78 \
  --report "$OUT/merge_report.md" > "$OUT/merge_stdout.md"

tail -n 40 "$OUT/merge_stdout.md"

# ---------------------------------------------------------------------------
# Stage 3. Multi-label vertical and thread scoring.
# ---------------------------------------------------------------------------
say "3. Score verticals and threads"
python3 "$HERE/score_verticals.py" \
  --in "$OUT/fstem_repository.jsonl" \
  --out "$OUT/fstem_repository_scored.jsonl" \
  --lexicon "$ROOT/schema/lexicon.json" \
  --report "$OUT/coverage_matrix.md" > "$OUT/coverage_stdout.md"

tail -n 40 "$OUT/coverage_stdout.md"

say "4. Regenerate the facts"
python3 "$HERE/emit_facts.py" --in "$OUT/fstem_repository_scored.jsonl" --out "$OUT" > /dev/null
echo "facts written to $OUT/FACTS.md"

say "Done"
echo "Repository:      $OUT/fstem_repository_scored.jsonl"
echo "Merge report:    $OUT/merge_report.md"
echo "Coverage matrix: $OUT/coverage_matrix.md"
