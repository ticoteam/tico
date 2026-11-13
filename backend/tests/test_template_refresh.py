"""A bot follows its template: the computer running it reports each refresh, which is one event and moves the bot's
`template_version` (backend/template_refresh.py)."""

import json

from backend import bot_settings as BS
from backend.tests.helpers.api import assign, headers, post, runner  # noqa: F401  (fixtures)


def follows(api, template, version):
    with api.app.state.store.transaction() as c:
        BS.update(c, "ops", {"template": template, "template_version": version})


def events(api, action):
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT target,detail FROM events WHERE action=? ORDER BY created_at", (action,)).fetchall()
    return [(row["target"], json.loads(row["detail"])) for row in rows]


def version(api):
    r = api.get("/api/v2/bots/ops", headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    return r.json()["template_version"]


def test_a_refresh_is_one_event_and_moves_the_bots_template_version(api):
    machine, other = runner(api), runner(api, label="Other Mac")
    assign(api, machine, "ops")
    follows(api, "inbound-sales-owner", "0.6.0-rc.3")
    body = {
        "template": "inbound-sales-owner",
        "to_version": "0.6.0-rc.5",
        "updated": ["AGENTS.md", ".claude/settings.json"],
        "kept": ["playbooks/leads.md"],
    }
    post(api, "bots/ops/template/refresh", body, other["token"], expected=403)
    post(api, "bots/ops/template/refresh", body, "ana-test", expected=403)
    assert post(api, "bots/ops/template/refresh", body, machine["token"])["recorded"] is True
    assert version(api) == "0.6.0-rc.5"
    assert events(api, "bot.template_refreshed") == [
        (
            "ops",
            {
                "template": "inbound-sales-owner",
                "from_version": "0.6.0-rc.3",
                "to_version": "0.6.0-rc.5",
                "updated": ["AGENTS.md", ".claude/settings.json"],
                "kept": ["playbooks/leads.md"],
            },
        )
    ]
    # The same version again with nothing changed records nothing; a report about a template it no longer has neither.
    assert post(api, "bots/ops/template/refresh", {**body, "updated": [], "kept": []}, machine["token"]) == {
        "recorded": False
    }
    post(api, "bots/ops/template/refresh", {**body, "template": "pr-manager"}, machine["token"])
    assert len(events(api, "bot.template_refreshed")) == 1


def test_a_missing_template_is_reported_once_and_cleared_by_a_refresh(api):
    machine = runner(api)
    assign(api, machine, "ops")
    follows(api, "seo-specialist", "0.6.0-rc.3")
    gone = {"template": "seo-specialist", "missing": True}
    assert post(api, "bots/ops/template/refresh", gone, machine["token"])["recorded"] is True
    assert post(api, "bots/ops/template/refresh", gone, machine["token"])["recorded"] is False
    assert events(api, "bot.template_missing") == [("ops", {"template": "seo-specialist"})]
    assert version(api) == "0.6.0-rc.3"  # left as it is
    post(api, "bots/ops/template/refresh", {"template": "seo-specialist", "to_version": "0.6.0-rc.5"}, machine["token"])
    with api.app.state.store.read() as c:
        assert "template_missing" not in BS.read(c, "ops")
