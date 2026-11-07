"""UI source is .js until a script converts to TypeScript (ui/app/x.ts, compiled by npm run build:ui). A check that picks UI
source by a .js glob or suffix alone skips every converted file, and it stops covering that file with no failure.

This guard fails when a test, script or backend module selects .js UI source and never names .ts: such a file lists both
suffixes, or it is in ALLOWED with the reason it reads .js on purpose. It reads text only (a git grep prefilter, then
regexes), so it is cheap enough for the static job, which runs on every change."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
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

# Files that read .js on purpose. The demo coverage map and the support bundle's browser file list are PR #2988's to widen:
# drop their entries when it merges.
ALLOWED = {
    "backend/tests/test_demo_coverage.py": "maps pages to ui/app/*.js; PR #2988 widens it to .ts",
    "backend/support.py": "the browser file names a diagnostics bundle keeps; PR #2988 widens them to .ts",
    "backend/tests/test_ui_bundle.py": "reads the compiled build/ui files the bundle is made of (a .ts source's output is .js)",
    "scripts/tests/test_pr_checks.py": "fixtures: it writes ui/app/*.js into temporary trees, and does not scan this repo's UI",
}

# Scanners this change converted. If the patterns stop seeing one, the guard has gone blind to that file's kind: fail.
KNOWN = (
    "backend/tests/test_beta.py",
    "backend/tests/test_harness_capabilities.py",
    "backend/tests/test_settings_tab_links.py",
    "ui/tests/settings-no-dead-ends.cjs",
    "scripts/cleanup/rename.py",
    "scripts/build-icon-font.py",
    "scripts/check_ui_routes.py",
    "scripts/screenshots/select.js",
)


def selects_js_source(text):
    return any(p.search(text) for p in SELECTS) or bool(ENDS_JS.search(text) and LISTING.search(text))


def candidates():
    out = subprocess.run(
        ["git", "grep", "-l", "-E", PREFILTER, "--", *FOLDERS], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout
    return sorted(rel for rel in out.splitlines() if not any(part in f"/{rel}" for part in EXCLUDED))


def test_every_ui_source_selection_names_ts_too():
    selecting, found = set(), []
    for rel in candidates():
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if not selects_js_source(text):
            continue
        selecting.add(rel)
        if rel not in ALLOWED and not any(p.search(text) for p in COVERS):
            found.append(rel)
    # A scan that stops seeing the scanners it was written for (a moved file, no git checkout) would pass without looking.
    missing = [rel for rel in KNOWN if rel not in selecting]
    assert not missing, (
        f"the guard no longer sees these known UI-source scanners (moved? not a git checkout?): {missing}"
    )
    assert not found, (
        "These select .js UI source but never name .ts, so a converted UI file goes unseen. List both suffixes (a glob "
        "or a suffix tuple for each), or add the file to ALLOWED here with the reason:\n" + "\n".join(found)
    )


def test_every_allowed_file_still_selects_js_ui_source():
    stale = [rel for rel in ALLOWED if not (ROOT / rel).is_file() or not selects_js_source((ROOT / rel).read_text())]
    assert not stale, f"ALLOWED names files that no longer read .js UI source; drop them from ALLOWED: {stale}"


@pytest.mark.parametrize(
    "snippet",
    [
        'glob("*.js")',
        "UI.glob('app/*.js')",
        '("ui/app", "*.js")',
        'path.suffix not in (".js", ".html")',
        "fs.readdirSync(dir).filter(f => f.endsWith('.js'))",
        "const source = f => f.endsWith('.js') || f.endsWith('.ts');\nconst files = fs.readdirSync(ui).filter(source);",
        'names = [f for f in os.listdir(app) if f.endswith(".js")]',
    ],
)
def test_the_patterns_see_each_way_of_selecting_js(snippet):
    assert selects_js_source(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    ['p.endsWith(".js")', 'ui[1].endsWith(".css")', 'path.suffix == ".css"', '("ui/app", "*.ts")'],
)
def test_url_checks_and_other_suffixes_are_not_ui_source_selections(snippet):
    assert not selects_js_source(snippet), snippet


def test_a_snippet_that_names_ts_is_covered():
    assert any(p.search('("ui/app", "*.ts")') for p in COVERS)
    assert any(p.search('path.suffix in (".js", ".ts")') for p in COVERS)
    assert any(p.search("'\\\\.(?:js|ts)'") for p in COVERS)
