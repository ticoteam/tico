"""Pairing a Hermes profile with its bot, restoring an archived bot, and what an archived Hermes bot's agent leaves behind."""

from backend.store import H
from backend.tests.test_agents import beat, credential, hermes_bot
from backend.tests.test_api import api, get, headers, post  # noqa: F401


def pair(api, profile="scout", ip=None, harness="hermes"):
    extra = {"cf-connecting-ip": ip} if ip else {}
    r = api.post("/api/v2/agents/pairings", json={"profile": profile, "harness": harness, "host": "mac-mini", "version": "1.0"},
                 headers=extra)
    return r


def poll(api, pairing, secret=None):
    return api.get("/api/v2/agents/pairings/" + pairing["pairing_id"],
                   headers={"X-Pairing-Secret": secret if secret is not None else pairing["secret"]})


def approve(api, code, bot="scout", token="ana-test"):
    return api.post("/api/v2/agents/pairings/approve", json={"code": code, "bot": bot}, headers=headers(token))


def test_the_pairing_lifecycle_hands_the_credential_over_once(api):
    hermes_bot(api, status="planned")
    made = pair(api)
    assert made.status_code == 201, made.text
    pairing = made.json()
    code = pairing["code"]
    assert len(code) == 9 and code[4] == "-" and not set(code.replace("-", "")) & set("0O1IL")
    assert pairing["expires_in"] == 600 and pairing["poll_every"] == 3 and len(pairing["secret"]) >= 32
    assert poll(api, pairing).json() == {"state": "pending"}
    # Only someone holding the secret may ask; a wrong one is the same 404 as an unknown pairing.
    assert poll(api, pairing, "wrong").status_code == 404
    assert api.get("/api/v2/agents/pairings/nope", headers={"X-Pairing-Secret": pairing["secret"]}).status_code == 404
    # Approval needs a person's sign-in, and a code nobody asked for is a plain 404.
    assert api.post("/api/v2/agents/pairings/approve", json={"code": code, "bot": "scout"}).status_code == 401
    assert approve(api, "ZZZZ-ZZZZ").status_code == 404
    done = approve(api, code.lower().replace("-", " "))
    assert done.status_code == 200, done.text
    assert done.json() == {"bot": "scout", "profile": "scout", "host": "mac-mini"}
    # A code is single use.
    assert approve(api, code).status_code == 404
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM agent_pairings WHERE id=?", (pairing["pairing_id"],)).fetchone()
        assert pairing["secret"] not in tuple(row) and code.replace("-", "") not in tuple(row)
    first = poll(api, pairing).json()
    assert first["state"] == "approved" and first["bot"] == "scout" and first["token"].startswith("tico-agent-")
    assert first["url"] == api.app.state.store.settings.runner_url
    assert get(api, "me", token=first["token"])["actor"] == "bot:scout"
    assert poll(api, pairing).json()["token"] == first["token"]  # installation can retry before a heartbeat
    assert beat(api, first["token"])["bot"] == "scout"
    assert next(bot for bot in get(api, "bots") if bot["slug"] == "scout")["state"] == "active"
    # The token is returned exactly once, and is gone from the row.
    assert poll(api, pairing).json() == {"state": "claimed"}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT token FROM agent_pairings").fetchone()[0] is None
    # Pairing again replaces the standing credential, as Create credential does.
    again = pair(api).json()
    assert approve(api, again["code"]).status_code == 200
    second = poll(api, again).json()["token"]
    assert api.get("/api/v2/me", headers=headers(first["token"])).status_code == 401
    assert get(api, "me", token=second)["actor"] == "bot:scout"


def test_a_pairing_expires_and_can_be_declined(api):
    hermes_bot(api)
    late = pair(api).json()
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE agent_pairings SET expires_at=?", (H.shift(H.now(), seconds=-1),))
    assert approve(api, late["code"]).status_code == 404
    assert poll(api, late).json() == {"state": "expired"}
    # Approved but never collected is wiped when its time runs out.
    held = pair(api).json()
    assert approve(api, held["code"]).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE agent_pairings SET expires_at=? WHERE id=?", (H.shift(H.now(), seconds=-1), held["pairing_id"]))
    assert poll(api, held).json() == {"state": "expired"}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT token FROM agent_pairings WHERE id=?", (held["pairing_id"],)).fetchone()[0] is None
    refused = pair(api).json()
    declined = api.post("/api/v2/agents/pairings/decline", json={"code": refused["code"]}, headers=headers("cara-test"))
    assert declined.status_code == 200 and declined.json()["declined"] is True
    assert poll(api, refused).json() == {"state": "declined"}
    assert approve(api, refused["code"]).status_code == 404


def test_pairing_requests_are_rate_limited_per_address_and_capped_overall(api):
    for _ in range(10):
        assert pair(api, ip="10.0.0.1").status_code == 201
    limited = pair(api, ip="10.0.0.1")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
    for _ in range(10):
        assert pair(api, ip="10.0.0.2").status_code == 201
    assert pair(api, ip="10.0.0.3").status_code == 429          # 20 are waiting: no more until some are answered


def test_who_may_approve_and_what_may_be_approved(api):
    hermes_bot(api)
    code = pair(api).json()["code"]
    # Cara neither owns Scout nor manages it: the server's own rule refuses, and the code stays open.
    assert approve(api, code, token="cara-test").status_code == 403
    # A bot on a computer cannot be paired.
    r = approve(api, code, bot="ops")
    assert r.status_code == 422 and "Hermes" in r.json()["error"]["detail"]
    # An admin may; so may an owner of that bot.
    post(api, "bots/scout/co-owners", {"add": ["cara"]})
    assert approve(api, code, token="cara-test").status_code == 200
    # A bot's own credential is not a person: it cannot approve a pairing for itself.
    token = post(api, "bots/scout/agent-credential", {})["token"]
    other = pair(api).json()["code"]
    assert approve(api, other, token=token).status_code == 403
    assert approve(api, other, token="ben-test").status_code == 200
    # An archived bot is not paired.
    hermes_bot(api, slug="old", status="planned")
    post(api, "bots/old/archive", {"expected_revision": get(api, "bots/old/access")["revision"]})
    assert approve(api, pair(api).json()["code"], bot="old").status_code == 409


def test_restore_brings_an_archived_bot_back_to_what_it_was(api):
    hermes_bot(api)
    assert post(api, "bots/scout/archive", {"expected_revision": get(api, "bots/scout/access")["revision"]})["status"] == "archived"
    # Nobody who does not manage it restores it, and a bot that is not archived has nothing to restore.
    assert api.post("/api/v2/bots/scout/restore", json={}, headers=headers("cara-test")).status_code == 403
    assert api.post("/api/v2/bots/ops/restore", json={}, headers=headers()).status_code == 409
    restored = post(api, "bots/scout/restore", {})
    assert restored["status"] == "active" and restored["restored"] is True and restored["agent"]["harness"] == "hermes"
    assert next(b for b in get(api, "bots") if b["slug"] == "scout")["state"] == "active"
    # A bot that was archived before the status was recorded comes back planned.
    post(api, "bots/scout/archive", {"expected_revision": get(api, "bots/scout/access")["revision"]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE events SET detail_json='{}' WHERE action='bot.archived' AND target='scout'")
    assert post(api, "bots/scout/restore", {})["status"] == "planned"
    # Register says where an archived slug went.
    post(api, "bots/scout/archive", {"expected_revision": get(api, "bots/scout/access")["revision"]})
    r = api.post("/api/v2/bots/register", json={"slug": "scout", "model": "hermes"}, headers=headers("cara-test"))
    assert r.status_code == 409 and "restore" in r.json()["error"]["detail"].lower()


def test_archiving_a_hermes_bot_says_its_agent_stops_and_revokes_the_credential(api):
    hermes_bot(api)
    token = post(api, "bots/scout/agent-credential", {})["token"]
    beat(api, token)
    archived = post(api, "bots/scout/archive", {"expected_revision": get(api, "bots/scout/access")["revision"]})
    assert archived["agent"]["harness"] == "hermes" and archived["agent"]["credential_revoked"] is True
    assert "Hermes agent will stop" in archived["agent"]["detail"]
    r = api.post("/api/v2/agents/heartbeat", json={}, headers=headers(token))
    assert r.status_code == 401
    # A bot on a computer says nothing about an agent.
    assert "agent" not in post(api, "bots/cpo/archive", {"expected_revision": get(api, "bots/cpo/access")["revision"]})


def openclaw_bot(api, slug="claw"):
    return hermes_bot(api, slug=slug, model="openclaw-own", harness="openclaw", display_name="Claw")


def test_an_openclaw_profile_pairs_only_with_an_openclaw_bot(api):
    openclaw_bot(api)
    hermes_bot(api)
    code = pair(api, profile="claw", harness="openclaw").json()["code"]
    # A Hermes bot cannot take an OpenClaw profile's code, and the code stays open.
    refused = approve(api, code, bot="scout")
    assert refused.status_code == 422 and "OpenClaw" in refused.json()["error"]["detail"]
    done = approve(api, code, bot="claw")
    assert done.status_code == 200 and done.json()["profile"] == "claw"
    # And the other way round.
    wrong = pair(api, harness="hermes").json()["code"]
    refused = approve(api, wrong, bot="claw")
    assert refused.status_code == 422 and "Hermes" in refused.json()["error"]["detail"]
    # An unknown harness is not accepted at all.
    assert pair(api, harness="other").status_code == 422
    # The credential the profile collects is the OpenClaw bot's.
    pairing = pair(api, profile="claw", harness="openclaw").json()
    assert approve(api, pairing["code"], bot="claw").status_code == 200
    token = poll(api, pairing).json()["token"]
    assert get(api, "me", token=token)["agent"] == "openclaw"


def test_pairing_preview_does_not_rotate_a_working_credential(api):
    hermes_bot(api)
    token = credential(api)["token"]
    made = pair(api).json()
    shown = get(api, "agents/pairing-preview?code=" + made["code"])
    assert shown["profile"] == "scout" and shown["host"] == "mac-mini" and shown["harness"] == "hermes"
    assert "token" not in shown
    assert get(api, "me", token=token)["actor"] == "bot:scout"
    assert poll(api, made).json()["state"] == "pending"
