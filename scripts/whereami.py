#!/usr/bin/env python3
"""
whereami.py — answer the path question with the machine instead of memory.

`ls` is a Linux and Mac command. On Windows it is a PowerShell alias that takes
different arguments, and `/Projects` is a path from the filesystem root, which is
almost never where a Projects folder actually lives.

This runs the same everywhere Python runs, which is everywhere you can run the
rest of the toolkit. It prints where you are, where your home is, every candidate
Projects folder it can find, and what each one holds.

    python3 scripts/whereami.py

On Windows, if `python3` is not recognised, use `python` or `py`.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys

MARKERS = ("ocw", "corpus", "cie", "igcse", "alevel", "westciv", "fstem", "scripts", "venv")


def looks_like_projects(path: str) -> int:
    """Score a directory by how much it looks like the FSTEM Projects folder."""
    try:
        entries = os.listdir(path)
    except OSError:
        return 0
    low = [e.lower() for e in entries]
    return sum(1 for m in MARKERS if any(m in e for e in low))


def candidates() -> list[str]:
    home = os.path.expanduser("~")
    cwd = os.getcwd()
    out = []

    # Walk up from here. The VS Code terminal often opens a level or two deep.
    p = os.path.abspath(cwd)
    for _ in range(4):
        out.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent

    for base in (home, os.path.join(home, "Documents"), os.path.join(home, "Desktop"),
                 os.path.join(home, "OneDrive"), os.path.join(home, "OneDrive", "Documents")):
        for name in ("Projects", "projects", "FSTEM", "fstem"):
            out.append(os.path.join(base, name))

    # Absolute forms people try, and Windows drive roots.
    out += ["/Projects", "/projects", "/jimrogers/Projects", "/home/jimrogers/Projects"]
    if platform.system() == "Windows":
        for drive in "CDE":
            out.append(f"{drive}:\\Projects")
            out.append(f"{drive}:\\Users")

    seen, uniq = set(), []
    for c in out:
        a = os.path.abspath(c)
        if a not in seen and os.path.isdir(a):
            seen.add(a)
            uniq.append(a)
    return uniq


def main() -> int:
    print("=" * 68)
    print("WHERE AM I")
    print("=" * 68)
    print()
    print(f"  operating system   {platform.system()} {platform.release()}")
    print(f"  python             {sys.version.split()[0]}  ({sys.executable})")
    print(f"  working directory  {os.getcwd()}")
    print(f"  home directory     {os.path.expanduser('~')}")
    print(f"  path separator     {os.sep!r}")

    venv = os.environ.get("VIRTUAL_ENV")
    print(f"  virtualenv active  {venv or 'no'}")

    bash = shutil.which("bash")
    print(f"  bash available     {bash or 'NO — use run_pipeline.py, not run_pipeline.sh'}")
    print()

    print("=" * 68)
    print("CANDIDATE PROJECTS FOLDERS")
    print("=" * 68)
    print()

    cands = candidates()
    scored = sorted(((looks_like_projects(c), c) for c in cands), key=lambda t: -t[0])

    best = None
    for score, path in scored:
        if score == 0:
            continue
        if best is None:
            best = path
        print(f"  [{score} markers]  {path}")
        try:
            entries = sorted(e for e in os.listdir(path) if not e.startswith("."))
        except OSError:
            continue
        for e in entries[:30]:
            full = os.path.join(path, e)
            kind = "dir " if os.path.isdir(full) else "file"
            n = ""
            if os.path.isdir(full):
                try:
                    n = f"  ({len(os.listdir(full))} entries)"
                except OSError:
                    n = ""
            print(f"                  {kind} {e}{n}")
        if len(entries) > 30:
            print(f"                  ...and {len(entries) - 30} more")
        print()

    if best is None:
        print("  Nothing looked like a Projects folder.")
        print()
        print("  Listing the working directory instead:")
        try:
            for e in sorted(os.listdir(os.getcwd()))[:40]:
                print(f"    {e}")
        except OSError as exc:
            print(f"    could not list: {exc}")
        print()
        print("  Find it in VS Code: right-click any folder in the Explorer panel,")
        print("  choose 'Copy Path', and pass it directly:")
        print("      python3 scripts/whereami.py")
        print("      python3 scripts/preflight.py --root \"<paste the path>\"")
        return 0

    print("=" * 68)
    print("WHAT TO RUN")
    print("=" * 68)
    print()
    print(f'  python3 scripts/preflight.py --root "{best}"')
    print()
    if not bash:
        print("  bash was not found, so after preflight run the Python version:")
        print("      python3 out/run_pipeline.py")
    else:
        print("  then:")
        print("      bash out/run_pipeline.sh")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
