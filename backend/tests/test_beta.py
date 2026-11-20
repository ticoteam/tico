"""The Beta list (backend/beta.py) is the one source: the manual marks exactly its headings, the app gets it in config,
and support diagnostics list the Beta features turned on."""

import json
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


def test_diagnostics_keep_beta_only_as_a_list():
    from backend import diagnostics

    assert diagnostics.allowed({"beta": ["listening"]}) == {"beta": ["listening"]}
    assert diagnostics.allowed({"beta": {"listening": "x"}}) == {"beta": []}
