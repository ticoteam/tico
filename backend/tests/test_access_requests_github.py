"""Access requests for GitHub repositories (backend/access_requests.py), end to end through the repository grants and
the GitHub App's turn token: Read on a ticked repository is granted at once, a write or an unticked one reaches the
Owner and the admins, the Grant ticks and chooses it, a change in Settings closes a waiting request, and the bot's next
token reaches what was granted."""

import json
import uuid

from backend.tests.helpers.github_app import api, auth, gh, runner_token, turn_token  # noqa: F401
from backend.tests.helpers.repositories import catalog, put


def ask(api, resource, level="read", token="owner-test", expected=200, bot="cpo"):
    r = api.post(
        "/api/v2/access-requests",
        json={"resource": resource, "level": level, "for_bot": bot, "why": "review the release"},
        headers={**auth(token), "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == expected, r.text
    return r.json()


def tap(api, rid, what, token="owner-test", expected=200):
    r = api.post(f"/api/v2/access-requests/{rid}/{what}", json={}, headers=auth(token))
    assert r.status_code == expected, r.text
    return r.json()


def reach(api, bot="cpo"):
    r = api.get(f"/api/v2/bots/{bot}/repositories", headers=auth())
    assert r.status_code == 200, r.text
    return {g["full_name"]: g["access"] for g in r.json()["effective"]}


def row(api, rid):
    with api.app_state.store.read() as c:
        return dict(c.execute("SELECT * FROM access_requests WHERE id=?", (rid,)).fetchone())


def test_a_repository_request_end_to_end(api, gh):
    catalog(api, gh)
    runner_token(api, "cpo")
    api.app_state.auth.owner_email = "ana@acme.example"  # sync_access sets it; the fixture never runs it
    put(api, "repositories/Acme/product", {"enabled": True})
    put(api, "bots/cpo/repositories", {"mode": "chosen", "chosen": [{"full_name": "Acme/docs", "access": "read"}]})
    assert reach(api) == {"Acme/emp-cpo": "write"}  # docs is chosen but not ticked: not in effect

    # Read on a ticked repository: granted at once, the bot's other choices kept, and its next token reaches it.
    done = ask(api, "product")
    assert (done["state"], done["auto"], done["kind"], done["resource_id"]) == (
        "granted",
        True,
        "repository_read",
        "Acme/product",
    )
    assert reach(api) == {"Acme/emp-cpo": "write", "Acme/product": "read"}
    with api.app_state.store.read() as c:
        chosen = {r["full_name"]: r["access"] for r in c.execute("SELECT * FROM bot_repo_access WHERE bot='cpo'")}
    assert chosen == {"Acme/docs": "read", "Acme/product": "read"}
    assert "Acme/product" in turn_token(api).json()["repositories"]
    assert ask(api, "Acme/product")["already"] is True

    # Write asks the people who choose repository access; the Owner's Grant ticks and chooses it at write.
    asked = ask(api, "Acme/docs", "read_write")
    assert asked["state"] == "open" and asked["asked"]
    tap(api, asked["id"], "grant", "person-test", expected=403)  # a member does not choose repository access
    granted = tap(api, asked["id"], "grant")
    assert granted["state"] == "granted" and reach(api)["Acme/docs"] == "write"
    with api.app_state.store.read() as c:
        assert c.execute("SELECT enabled FROM repositories WHERE full_name='Acme/docs'").fetchone()[0] == 1
    assert row(api, asked["id"])["resolved_via"] == "card"

    # A bot's own repository always asks; a choice made in Settings closes the request.
    waiting = ask(api, "Acme/bot-sales")
    assert waiting["state"] == "open" and json.loads(row(api, waiting["id"])["approvers"])
    put(api, "repositories/Acme/bot-sales", {"enabled": True})  # ticked: cpo still has not chosen it
    assert row(api, waiting["id"])["state"] == "open"
    current = api.get("/api/v2/bots/cpo/repositories", headers=auth()).json()["chosen"]
    put(
        api,
        "bots/cpo/repositories",
        {"mode": "chosen", "chosen": [*current, {"full_name": "Acme/bot-sales", "access": "read"}]},
    )
    assert (row(api, waiting["id"])["state"], row(api, waiting["id"])["resolved_via"]) == ("granted", "tools")
    assert reach(api)["Acme/bot-sales"] == "read"


def test_write_for_a_bot_reading_every_repository_in_use_keeps_its_reads_and_writes_the_one_asked(api, gh):
    catalog(api, gh)
    api.app_state.auth.owner_email = "ana@acme.example"
    for name in ("product", "docs"):
        put(api, "repositories/Acme/" + name, {"enabled": True})
    put(api, "bots/cpo/repositories", {"mode": "all", "all_access": "read"})  # where a new agent starts
    asked = ask(api, "Acme/product", "read_write")
    assert asked["state"] == "open"
    assert tap(api, asked["id"], "grant")["state"] == "granted"
    assert reach(api) == {"Acme/emp-cpo": "write", "Acme/product": "write", "Acme/docs": "read"}
    assert api.get("/api/v2/bots/cpo/repositories", headers=auth()).json()["mode"] == "chosen"


def test_a_grant_past_all_ticked_repos_at_write_keeps_every_write_and_its_workflow_flags(api, gh):
    catalog(api, gh)
    api.app_state.auth.owner_email = "ana@acme.example"
    for name in ("product", "docs"):
        put(api, "repositories/Acme/" + name, {"enabled": True})
    put(
        api,
        "bots/cpo/repositories",
        {"mode": "all", "all_access": "write", "all_repos": [{"full_name": "Acme/product", "run_workflows": True}]},
    )
    asked = ask(api, "Acme/bot-sales")  # a bot's repository: All ticked repos never reaches it
    assert asked["state"] == "open"
    assert tap(api, asked["id"], "grant")["state"] == "granted"
    assert reach(api) == {
        "Acme/emp-cpo": "write",
        "Acme/product": "write",
        "Acme/docs": "write",
        "Acme/bot-sales": "read",
    }
    chosen = {r["full_name"]: r for r in api.get("/api/v2/bots/cpo/repositories", headers=auth()).json()["chosen"]}
    assert chosen["Acme/product"].get("run_workflows") is True and not chosen["Acme/docs"].get("run_workflows")
