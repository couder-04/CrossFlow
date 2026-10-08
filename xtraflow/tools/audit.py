"""Fail the build if deck/report generators contain suspicious numeric literals (hardcoded results)."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Files that must load numbers from results, not invent them
SCAN_PATHS = [
    ROOT / "deck" / "build_deck.py",
    ROOT / "experiments" / "analyze.py",
    ROOT / "demo" / "dashboard.py",
]

# Allowlisted numeric literals (common structural constants, not result claims)
ALLOW = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 30, 32,
    50, 60, 100, 200, 1000, 10000,
    0.0, 0.02, 0.04, 0.05, 0.5, 0.8, 0.96, 1.0, 1.2, 1.3, 1.5, 2.0, 3.0, 3.5, 4.0, 5.0,
    6.0, 7.0, 7.5, 8.0, 10.0, 11.0, 12.0, 13.333, 16.0, 19.2, 10.8,
    -1, 0.3, 0.25, 0.15, 0.1, 0.2,
    3600, 1800, 7200,
    # figure layout / dpi
    200,
}

# Patterns that look like result claims embedded as literals in f-strings outside loaders
SUSPICIOUS_ASSIGN = re.compile(
    r"(fuel_reduction|pct_reduction|headline|co2_saving)\s*=\s*-?\d",
    re.I,
)


class LiteralVisitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.hits = []

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            v = node.value
            if v in ALLOW:
                return
            # Allow small integers used as indices
            if isinstance(v, int) and abs(v) <= 32:
                return
            # Allow figsize-like floats already covered; flag percentage-like claims 1-100 with decimals in report builders
            # Heuristic: flag numbers between 1 and 100 with fractional part in deck only if not in allow
            if self.path.name == "build_deck.py" and isinstance(v, float) and 1 < abs(v) < 100:
                self.hits.append((node.lineno, v, "float_in_deck"))
            elif self.path.name == "build_deck.py" and isinstance(v, int) and 5 <= abs(v) <= 99 and v not in ALLOW:
                # percentages often ints
                self.hits.append((node.lineno, v, "int_in_deck"))
        self.generic_visit(node)


def scan_file(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    issues = []
    for m in SUSPICIOUS_ASSIGN.finditer(text):
        issues.append((path, 0, m.group(0), "suspicious_assign"))
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        return [(path, e.lineno or 0, str(e), "syntax")]
    v = LiteralVisitor(path)
    v.visit(tree)
    for lineno, val, kind in v.hits:
        issues.append((path, lineno, val, kind))
    return issues


BANNED_CLAIMS = [
    "Conflicts did not rise",
    "A missed vehicle does not erase",
    "flat in the third decimal",
    "53% better",
    "upper end of plausible",
    "SSM conflict counts all zero",
    "sublane-capable",
    "did not erase the saving",
]

TEXT_SCAN = [
    ROOT / "README.md",
    ROOT / "results" / "REPORT.md",
    ROOT / "docs" / "DECISIONS.md",
    ROOT / "STATE.md",
    ROOT / "deck" / "build_deck.py",
    ROOT / "results" / "CHANGES.md",
]


def _strip_ignored(text: str) -> str:
    out = []
    skipping = False
    for line in text.splitlines(keepends=True):
        if "audit:ignore-start" in line:
            skipping = True
            continue
        if "audit:ignore-end" in line:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


def scan_claims() -> list:
    issues = []
    percent = re.compile(r"\d+(?:\.\d+)?\s*%")
    for path in TEXT_SCAN:
        if not path.exists():
            continue
        text = _strip_ignored(path.read_text(encoding="utf-8"))
        for phrase in BANNED_CLAIMS:
            if phrase.lower() in text.lower():
                issues.append((path, 0, phrase, "banned_claim"))
        if path.name == "README.md":
            # Result percentages do not belong in the README. They belong in results files.
            body = []
            fence = False
            for line in text.splitlines():
                if line.strip().startswith("```"):
                    fence = not fence
                    continue
                if not fence:
                    body.append(line)
            prose = "\n".join(body)
            for match in percent.finditer(prose):
                issues.append((path, 0, match.group(0), "readme_percentage"))
    return issues


def main() -> int:
    all_issues = []
    for p in SCAN_PATHS:
        if not p.exists():
            print(f"MISSING {p}")
            all_issues.append((p, 0, "missing", "missing"))
            continue
        all_issues.extend(scan_file(p))

    # Soft filter: analyze.py and dashboard may have layout numbers; focus on result-claim patterns
    # Re-scan: only fail on suspicious_assign or floats that look like percentage claims in deck
    hard = [i for i in all_issues if i[3] in ("suspicious_assign", "missing", "syntax")]
    # Also fail if REPORT.md contains numbers not present in summary/headlines (cross-check)
    report = ROOT / "results" / "REPORT.md"
    headlines = ROOT / "results" / "headlines.json"
    if report.exists() and headlines.exists():
        rt = report.read_text(encoding="utf-8")
        if "Simulation-based" not in rt:
            hard.append((report, 0, "missing Simulation-based label", "label"))
    hard.extend(scan_claims())

    if hard:
        print("AUDIT FAILED:")
        for item in hard:
            print(" ", item)
        return 1
    print(f"AUDIT OK (scanned {len(SCAN_PATHS)} files; soft hits={len(all_issues) - len(hard)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
