#!/usr/bin/env python3
"""UI source is .js until a script converts to TypeScript (ui/app/x.ts). A check that picks UI source by a .js glob or
suffix alone skips each converted file, and it stops covering that file without a failure.

This check fails when a test, script or backend module selects .js UI source and never names .ts, unless the file is in
ALLOWED with the reason it reads .js on purpose. It also fails when its own patterns stop seeing a scanner this change
converted (KNOWN), or when an ALLOWED file no longer reads .js UI source.

It is a script, not a test file: the merge queue's dependency map selects a test that reads source text for changes
under backend/, scripts/ and ui/, so a test here would widen every such PR. It runs in the static job, text only:
    python3 scripts/check_ui_source_suffixes.py
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("backend", "scripts", "ui/tests")
EXCLUDED = ("/node_modules/", "/build/", "/vendor/", "/assets/")
PREFILTER = r"\*\.js|\.js['\"]"

# A .js UI source selection: a glob literal, a suffix list, or a readdir filter on .js.
SELECTS = [
    re.compile(r"""["'`][^"'`\n]*\*\.js["'`]"""),
    re.compile(r"""\bsuffix\b[^\n]*["']\.js["']"""),
    re.compile(r"""readdirSync[^\n]*["']\.js["']"""),
]
# A suffix test on .js (`endswith` in Python, `endsWith` in JS) selects files only where the same file also lists a folder.
# A URL check in a browser stub (`p.endsWith('.js')` on a request path) lists nothing, so it is not a selection.
ENDS_JS = re.compile(r"""\.[eE]nds[wW]ith\(\s*\(?\s*["']\.js["']""")
LISTING = re.compile(r"readdirSync|readdir\(|listdir|scandir|iterdir|glob\(|walk\(|ls-files")
# Naming .ts as well: a *.ts glob, a ".ts" suffix literal, or the js|ts alternation a regex builds from both.
COVERS = [re.compile(r"""\*\.ts\b"""), re.compile(r"""["'`]\.ts["'`]"""), re.compile(r"""js\|ts""")]

# Files that read .js on purpose. The UI scripts are TypeScript now (the kept .js files are read alongside them).
ALLOWED = {
    "scripts/tests/test_pr_checks.py": "fixtures: it writes ui/app/*.js into temporary trees, and does not scan this repo's UI",
}

# Scanners this change converted. If the patterns stop seeing one, this check has gone blind to that kind of file: fail.
KNOWN = (
    "backend/tests/test_beta.py",
    "backend/tests/test_settings_tab_links.py",
    "ui/tests/settings-no-dead-ends.cts",
    "scripts/cleanup/rename.py",
    "scripts/build-icon-font.py",
    "scripts/check_ui_routes.py",
    "scripts/screenshots/select.cts",
)

# Each snippet and whether it selects .js UI source. Checked on every run, so the patterns cannot drift unnoticed.
SELF_CHECKS = (
    ('glob("*.js")', True),
    ("UI.glob('app/*.js')", True),
    ('("ui/app", "*.js")', True),
    ('path.suffix not in (".js", ".html")', True),
    ("fs.readdirSync(dir).filter(f => f.endsWith('.js'))", True),
    (
        "const source = f => f.endsWith('.js') || f.endsWith('.ts');\nconst files = fs.readdirSync(ui).filter(source);",
        True,
    ),
    ('names = [f for f in os.listdir(app) if f.endswith(".js")]', True),
    ('p.endsWith(".js")', False),
    ('ui[1].endsWith(".css")', False),
    ('path.suffix == ".css"', False),
    ('("ui/app", "*.ts")', False),
)


def selects_js_source(text):
    return any(p.search(text) for p in SELECTS) or bool(ENDS_JS.search(text) and LISTING.search(text))


def covers_ts(text):
    return any(p.search(text) for p in COVERS)


def candidates():
    out = subprocess.run(
        ["git", "grep", "-l", "-E", PREFILTER, "--", *FOLDERS], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout
    return sorted(rel for rel in out.splitlines() if not any(part in f"/{rel}" for part in EXCLUDED))


def main():
    failures = []
    for text, want in SELF_CHECKS:
        if selects_js_source(text) != want:
            verb = "select" if want else "not select"
            failures.append(f"self-check: {text!r} should {verb} .js UI source")
    if not covers_ts('("ui/app", "*.ts")'):
        failures.append("self-check: a *.ts glob does not count as naming .ts")

    selecting, found = set(), []
    for rel in candidates():
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if not selects_js_source(text):
            continue
        selecting.add(rel)
        if rel not in ALLOWED and not covers_ts(text):
            found.append(rel)

    # A scan that stops seeing its known scanners (a moved file, or no git checkout) would pass without looking.
    failures += [
        f"known UI-source scanner not seen (moved, or a pattern lost it): {rel}"
        for rel in KNOWN
        if rel not in selecting
    ]
    failures += [
        f"ALLOWED no longer reads .js UI source; drop it from ALLOWED: {rel}" for rel in ALLOWED if rel not in selecting
    ]
    failures += [
        f"selects .js UI source but never names .ts (list both suffixes, or add to ALLOWED with a reason): {rel}"
        for rel in found
    ]

    if failures:
        print("check_ui_source_suffixes: converted UI files would go unseen", file=sys.stderr)
        print("\n".join(f"  {line}" for line in failures), file=sys.stderr)
        return 1
    print(f"check_ui_source_suffixes: ok, {len(selecting)} files select .js UI source; each names .ts or is ALLOWED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
