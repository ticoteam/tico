"""The Beta list (backend/beta.py) is the one source: the manual marks exactly its headings, the app gets it in config,
and support diagnostics list the Beta features turned on."""

import json
import re
from pathlib import Path

from backend import beta
from backend.tests.test_onboarding import environment, signed_in  # noqa: F401
from backend.tests.test_support import hq  # noqa: F401  (the fake HQ, autouse)

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_the_manual_marks_exactly_the_beta_headings():
    marked = []
    for page in sorted(DOCS.glob("*.md")):
        lines = page.read_text().splitlines()
        for n, line in enumerate(lines):
            if line.startswith(beta.MARKER):
                assert n >= 2 and lines[n - 1] == "" and lines[n - 2].startswith("#"), f"{page.name}:{n + 1} is not under a heading"
                heading = lines[n - 2]
                marked.append((page.name, None if heading.startswith("# ") else heading.lstrip("#").strip()))
    assert sorted(marked, key=str) == sorted((d for f in beta.FEATURES for d in f.docs), key=str)


def test_the_app_gets_the_list_and_diagnostics_name_what_is_on(environment):
    api = environment()
    owner = signed_in()
    assert [f["id"] for f in api.get("/api/v2/config", headers=owner).json()["beta"]] == list(beta.IDS)
    # A fresh team has none on; the Mac app asking for support is the Mac app in use.
    got = api.get("/api/v2/support/diagnostics?performance_hours=0", headers=owner)
    assert json.loads(got.json()["text"])["beta"] == []
    got = api.post("/api/v2/support/diagnostics/capture", headers=owner,
                   json={"app": {"version": "0.3.51", "lines": [], "mac": True}, "performance_hours": 0})
    assert got.status_code == 200, got.text
    assert json.loads(got.json()["text"])["beta"] == ["mac_app"]


UI = Path(__file__).resolve().parents[2] / "ui"
# A pill the app draws by id: `betaPill('id')` in a script, or `data-beta="id"` in a static spot. ui/app/state.js
# defines both and is left out.
SPOT = re.compile(r"""betaPill\(['"]([a-z_]+)['"]\)|data-beta="([a-z_]+)\"""")


def _spots():
    used = {}
    for path in sorted(UI.rglob("*")):
        rel = path.relative_to(UI)
        if path.suffix not in (".js", ".html") or "tests" in rel.parts or rel.as_posix() == "app/state.js":
            continue
        for match in SPOT.finditer(path.read_text()):
            used.setdefault(match.group(1) or match.group(2), []).append(rel.as_posix())
    return used


def test_every_pill_in_the_app_is_a_listed_feature_and_every_feature_has_one():
    used = _spots()
    # A pill for an id off the list draws nothing, so a typo would hide it silently and a new id would label an
    # unapproved feature once listed.
    assert set(used) <= set(beta.IDS), {k: v for k, v in used.items() if k not in beta.IDS}
    # A feature that is a bot gets its pill from the bot (botBetaPill); every other one needs a spot of its own.
    assert {f.id for f in beta.FEATURES if not f.bots} <= set(used)


def test_market_is_not_beta_while_message_bots_still_is():
    # Chris, Oct 18: Market is not Beta. No pill in the nav, on its page title or its pages, no Help row, no manual note.
    used, names = _spots(), [f["name"] for f in beta.listing()]
    assert "market" not in beta.IDS and "market" not in used and "Market" not in names
    assert beta.MARKER not in (DOCS / "librarian.md").read_text()
    # Message bots, also in the nav, keeps its pill there.
    assert "message_bots" in beta.IDS and "index.html" in used["message_bots"]


def test_diagnostics_keep_beta_only_as_a_list():
    from backend import diagnostics
    assert diagnostics.allowed({"beta": ["listening"]}) == {"beta": ["listening"]}
    assert diagnostics.allowed({"beta": {"listening": "x"}}) == {"beta": []}
