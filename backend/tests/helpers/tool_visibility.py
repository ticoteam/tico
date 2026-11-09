"""Helpers that other tests share, moved out of test_tool_visibility.py so they import no test file."""

import json
from backend.tests.helpers.api import headers
from backend.tests.helpers.api_tools import key, tool
from backend.tests.helpers.credentials import setup


SECRET = "acme-bank-synthetic-rw-0011223344"


SECRET_R = "acme-bank-synthetic-ro-5566778899"


ONLY = {"everyone": False, "people": [], "teams": [], "bots": []}


def patch(api, tid, body, token="ana-test", expected=200):
    r = api.patch(f"/api/v2/api-tools/{tid}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def eve(api):
    """Eve, a member on the roster with no part in anything here."""
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        if not any(p["id"] == "eve" for p in roster["people"]):
            roster["people"].append({"id": "eve", "email": "eve@acme.example"})
        c.execute("INSERT OR IGNORE INTO humans(id,name,email) VALUES('eve','Eve','eve@acme.example')")
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))


def bank(api, audience=ONLY):
    """Ana's bank tool with a Read and write key and a Read key, Only listed (by default nobody but her)."""
    setup(api)
    eve(api)
    tid = tool(api, "Acme Bank", base_url="https://api.bank.acme.example", check_path="/v1/me")["id"]
    rw = key(api, tid, SECRET)["id"]
    ro = key(api, tid, SECRET_R, "read")["id"]
    if audience is not None:
        patch(api, tid, {"audience": audience})
    return tid, rw, ro
