#!/usr/bin/env python3
"""
Prose linter for the CLAUDE.md register. Read-only.

    python3 tools/tdw_lint.py FILE [FILE ...] [--json OUT.json] [--md OUT.md]

Counts over prose lines only. Skips YAML front matter, headings, tables, code fences,
blockquotes and HTML comments. Markdown emphasis and note refs are stripped before
counting words.
"""
import argparse, json, re, sys

WEAK = ["roughly", "approximately", "essentially", "actually", "truly", "really", "simply", "merely", "quite",
        "very", "particularly", "significantly", "notably", "importantly", "clearly", "effectively"]
NOMINAL_SUFFIX = re.compile(r"\b[a-z]{3,}(?:ization|tion|ment|ance|ence|ness|ity)s?\b", re.I)
NOMINAL_STOP = {"hence", "whence", "since", "city", "cities", "pity", "fence", "fences", "once"}
NOMINAL_PHRASES = re.compile(r"\b(?:serves as|functions as|is designed to|makes use of|facilitates?|facilitated|"
                             r"leverages?|leveraged|utiliz(?:es?|ed))\b", re.I)
NOT_BUT = re.compile(r"\bnot\b(?:(?!\bnot\b)[^.;:!?]){1,80}?,?\s*\bbut\b", re.I)
WEAK_RE = re.compile(r"\b(?:" + "|".join(WEAK) + r")\b", re.I)
ABBREV = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|St|Gen|Col|Lt|Adm|Sgt|Capt|Gov|Sen|Rep|Jr|Sr|No|Nos|vs|v|Inc|Corp|Co|Ltd|"
                    r"U\.S|U\.K|U\.N|a\.m|p\.m|e\.g|i\.e|etc|Jan|Feb|Mar|Apr|Aug|Sept?|Oct|Nov|Dec|Art|Vol|pp?|ch)\.$")
INITIAL = re.compile(r"(?:^|\s)[A-Z]\.$")
LABEL = re.compile(r"^\*\*[^*]{1,80}?[.:]\*\*\s*")   # leading bold label: **Documented.**, **The gap.**
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.,-]*")


def prose_lines(text):
    """Yield (line_no, line) for prose lines."""
    lines = text.split("\n")
    i, fence, comment = 0, False, False
    if lines and lines[0].strip() == "---":
        try:
            i = lines.index("---", 1) + 1
        except ValueError:
            pass
    for n in range(i, len(lines)):
        ln = lines[n]
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            continue
        if fence:
            continue
        if comment or s.startswith("<!--"):
            comment = "-->" not in s
            continue
        if not s or s.startswith(("#", "|", ">")) or re.fullmatch(r"[-*_]{3,}", s):
            continue
        yield n + 1, ln


def clean(s):
    s = re.sub(r"\\\[\d+\\\]", "", s)
    s = re.sub(r"\[(\d+)\]", "", s)
    s = re.sub(r"[*_`]", "", s)
    s = s.replace("\\", "")
    return s


def paragraphs(text):
    """Group consecutive prose lines into paragraphs: [(first_line, text)]."""
    out, cur, start, prev = [], [], None, None
    for n, ln in prose_lines(text):
        if prev is not None and n != prev + 1:
            out.append((start, " ".join(cur)))
            cur = []
        if not cur:
            start = n
        cur.append(ln.strip())
        prev = n
    if cur:
        out.append((start, " ".join(cur)))
    return out


def sentences(par):
    par = clean(par)
    out, buf = [], ""
    for k, tok in enumerate(re.split(r"(\s+)", par)):
        buf += tok
        t = tok.strip()
        if not t:
            continue
        if re.search(r"[.!?][\"”’')\]]*$", t) and not ABBREV.search(t) and not INITIAL.search(" " + t) \
                and not (k == 0 and re.fullmatch(r"\d+\.", t)):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if WORD.search(s)]


def words(s):
    return [w for w in WORD.findall(s) if re.search(r"[A-Za-z0-9]", w)]


def lint_text(text, name=""):
    em = sum(ln.count("—") for _, ln in prose_lines(text))
    em_lines = [n for n, ln in prose_lines(text) if "—" in ln]
    lengths, long25, long35, notbut, weak, nominal, total_words = [], [], [], [], [], 0, 0
    for start, par in paragraphs(text):
        par = LABEL.sub("", par)
        for s in sentences(par):
            n = len(words(s))
            lengths.append(n)
            if n > 25:
                long25.append((start, n, s[:160]))
            if n > 35:
                long35.append((start, n, s[:160]))
        c = clean(par)
        total_words += len(words(c))
        notbut += [(start, m.group(0)[:120]) for m in NOT_BUT.finditer(c)]
        weak += [(start, m.group(0).lower()) for m in WEAK_RE.finditer(c)]
        nominal += sum(1 for m in NOMINAL_SUFFIX.finditer(c) if m.group(0).lower() not in NOMINAL_STOP)
        nominal += len(NOMINAL_PHRASES.findall(c))
    ns = len(lengths) or 1
    per_k = lambda x: round(1000 * x / total_words, 2) if total_words else 0.0
    weak_counts = {}
    for _, w in weak:
        weak_counts[w] = weak_counts.get(w, 0) + 1
    return {
        "file": name,
        "prose_words": total_words,
        "sentences": len(lengths),
        "em_dashes": em,
        "em_dash_lines": em_lines[:200],
        "sentences_over_25": len(long25),
        "sentences_over_35": len(long35),
        "mean_sentence_length": round(sum(lengths) / ns, 2),
        "share_over_30": round(sum(1 for x in lengths if x > 30) / ns, 4),
        "not_x_but_y": len(notbut),
        "not_x_but_y_examples": notbut[:25],
        "weak_adverbs": len(weak),
        "weak_adverbs_per_1000": per_k(len(weak)),
        "weak_adverb_counts": dict(sorted(weak_counts.items(), key=lambda kv: -kv[1])),
        "nominalizations": nominal,
        "nominalizations_per_1000": per_k(nominal),
        "longest_sentences": sorted(long35, key=lambda x: -x[1])[:10],
    }


ROWS = [("prose words", "prose_words"), ("sentences", "sentences"), ("em-dashes", "em_dashes"),
        ("sentences > 25 words", "sentences_over_25"), ("sentences > 35 words", "sentences_over_35"),
        ("mean sentence length", "mean_sentence_length"), ("share > 30 words", "share_over_30"),
        ("not X but Y", "not_x_but_y"), ("weak adverbs", "weak_adverbs"),
        ("weak adverbs / 1000 words", "weak_adverbs_per_1000"), ("nominalizations", "nominalizations"),
        ("nominalizations / 1000 words", "nominalizations_per_1000")]


def table(results):
    head = "| Metric | " + " | ".join(r["file"] for r in results) + " |"
    L = [head, "|---" * (len(results) + 1) + "|"]
    for label, k in ROWS:
        L.append(f"| {label} | " + " | ".join(str(r[k]) for r in results) + " |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args()
    res = []
    for f in a.files:
        with open(f, encoding="utf-8") as fh:
            res.append(lint_text(fh.read(), f))
    t = table(res)
    print(t)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as fh:
            fh.write("# Prose lint\n\n" + t + "\n")
            for r in res:
                fh.write(f"\n## {r['file']}\n\nWeak adverbs by word: {r['weak_adverb_counts']}\n\n"
                         f"Longest sentences (paragraph start line, words):\n\n")
                for ln, n, s in r["longest_sentences"]:
                    fh.write(f"- L{ln}, {n} words: {s}\n")
                fh.write("\n\"Not X but Y\" examples:\n\n")
                for ln, s in r["not_x_but_y_examples"][:10]:
                    fh.write(f"- L{ln}: {s}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
