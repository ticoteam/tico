"""The Beta list (backend/beta.py) is the one source: the manual marks exactly its headings, the app gets it in config,
and support diagnostics list the Beta features turned on."""

import json
import re
from pathlib import Path

from backend import beta
from backend.tests.helpers.onboarding import environment, signed_in  # noqa: F401
from backend.tests.helpers.support import hq  # noqa: F401  (the fake HQ, autouse)

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_the_app_gets_the_list_and_diagnostics_name_what_is_on(environment):
    api = environment()
    owner = signed_in()
    assert [f["id"] for f in api.get("/api/v2/config", headers=owner).json()["beta"]] == list(beta.IDS)
    # A fresh team has none on; the Mac app asking for support is the Mac app in use.
    got = api.get("/api/v2/support/diagnostics?performance_hours=0", headers=owner)
    assert json.loads(got.json()["text"])["beta"] == []
    got = api.post(
        "/api/v2/support/diagnostics/capture",
        headers=owner,
        json={"app": {"version": "0.3.51", "lines": [], "mac": True}, "performance_hours": 0},
    )
    assert got.status_code == 200, got.text
    assert json.loads(got.json()["text"])["beta"] == ["mac_app"]


UI = Path(__file__).resolve().parents[2] / "ui"
# A pill the app draws by id: `betaPill('id')` in a script (.js, or .ts once converted), or `data-beta="id"` in a static
# spot. ui/app/state.ts defines both and is left out.
SPOT = re.compile(r"""betaPill\(['"]([a-z_]+)['"]\)|data-beta="([a-z_]+)\"""")


def _spots():
    used = {}
    for path in sorted(UI.rglob("*")):
        rel = path.relative_to(UI)
        if path.suffix not in (".js", ".ts", ".html") or "tests" in rel.parts or rel.as_posix() == "app/state.ts":
            continue
        for match in SPOT.finditer(path.read_text()):
            used.setdefault(match.group(1) or match.group(2), []).append(rel.as_posix())
    return used


def test_diagnostics_keep_beta_only_as_a_list():
    from backend import diagnostics

    assert diagnostics.allowed({"beta": ["listening"]}) == {"beta": ["listening"]}
    assert diagnostics.allowed({"beta": {"listening": "x"}}) == {"beta": []}
