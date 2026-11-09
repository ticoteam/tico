"""Helpers that other tests share, moved out of test_automation_api.py so they import no test file."""

from backend import bot_settings as BS
from backend.tests.helpers.api import headers, post


NOTE_STEP = {"id": "tell", "type": "action", "enabled": True, "do": "wake_bot", "args": {"bot": "ops", "text": "Look"}}


def flow(**extra):
    return {
        "name": "Morning check",
        "owner": "person:ana",
        "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"},
        "steps": [NOTE_STEP],
        **extra,
    }


def make(api, token="ana-test", expected=200, **extra):
    return post(api, "automations", flow(**extra), token, expected=expected)


def patch(api, aid, body, token="ana-test", expected=200):
    r = api.patch(f"/api/v2/automations/{aid}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def audit_rows(api, aid):
    """The automation's audit rows, oldest first, with before and after read back."""
    from backend import audit_log

    with api.app.state.store.read() as c:
        return audit_log.rows(c, obj="automation:" + aid, limit=200)["rows"][::-1]


def as_bot(api, slug):
    """A test token that signs in as the bot named by slug (its id, or its slug if it is new), as bot:<id>."""
    from backend.auth import Identity
    from backend.store import H

    with api.app.state.store.transaction() as c:
        found = H.resolve_bot(c, slug)
        bot_id = found["id"] if found else slug
        if not found:
            c.execute(
                "INSERT INTO bots(id,slug,display_name,created_at) VALUES(?,?,?,?)", (bot_id, slug, slug, H.now())
            )
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", (bot_id,))
            BS.write(c, bot_id, {"runtime": "fake"})
        c.execute(
            "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (bot_id, "test", "x-" + bot_id, H.now(), "human:ana"),
        )
    api.app.state.auth.settings.test_identities[slug + "-test"] = Identity(H.bot_actor(bot_id), "bot", agent="test")
    return slug + "-test"
