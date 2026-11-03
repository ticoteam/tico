"""An old-name call (the `hub` alias, X-Tico-Alias) leaves one `alias.hub_used` event; a normal call leaves none."""
import json

from backend import alias_uses
from backend.auth import Identity
from backend.tests.test_api import api, headers  # noqa: F401


def alias_events(api):
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, target, detail FROM events WHERE action = ?", (alias_uses.ACTION,)).fetchall()
    return [(actor, target, json.loads(detail)) for actor, target, detail in rows]


def test_an_old_name_call_is_recorded_once_with_its_form_and_command(api):
    sent = {**headers("ana-test"), "X-Tico-Alias": "cli", "X-Tico-Alias-Command": "task show"}
    assert api.get("/api/v2/me", headers=sent).status_code == 200
    assert alias_events(api) == [("human:ana", "task show", {"form": "cli", "bot": "", "command": "task show",
                                                              "computer": ""})]


def test_a_normal_call_and_an_unknown_form_record_nothing(api):
    assert api.get("/api/v2/me", headers=headers("ana-test")).status_code == 200
    unknown = {**headers("ana-test"), "X-Tico-Alias": "carrier-pigeon"}
    assert api.get("/api/v2/me", headers=unknown).status_code == 200
    assert alias_events(api) == []


def test_a_bots_old_name_call_names_the_bot(api):
    bot = Identity("bot:acme-ops", "bot", runner_id="r1")
    api.app.state.store.write(Identity("human:ana", "owner", "ana@acme.example"),
                              lambda c: alias_uses.record(c, bot, "mcp", "task_show"))
    assert alias_events(api) == [("bot:acme-ops", "task_show", {"form": "mcp", "bot": "acme-ops", "command": "task_show",
                                                                "computer": "r1"})]
