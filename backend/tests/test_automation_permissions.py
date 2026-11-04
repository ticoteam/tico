"""Who may see and change an automation (backend/automations.py `may_change`), one rule for the UI, the CLI and MCP:
the top of the org; the owner; anyone above the owner; the lead of its group and anyone above that lead. Peers are
refused, and seeing follows the same rule. BotOps has whoever asked it's rights (test_botops_automations.py)."""
from backend import automations as AU
from backend.auth import Identity
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_automation_api import as_bot


class Chart:
    """An org chart: Chris at the top, the lead under him, two builders under the lead; the lead leads `eng`, which
    sits in `product` (led by Pat, under Chris)."""
    PARENTS = {"b:builder-a": "b:lead", "b:builder-b": "b:lead", "b:lead": "p:chris", "p:pat": "p:chris"}
    LEADS = {"eng": ["b:lead", "p:pat"], "product": ["p:pat"]}

    def above(self, key):
        out = []
        while key in self.PARENTS:
            key = self.PARENTS[key]
            out.append(key)
        return out

    OFF = ()                                   # the bots whose switch to manage their reports is off

    def manages(self, slug):
        return slug not in self.OFF

    def leads(self, gid):
        return self.LEADS.get(gid, [])


def bot(slug):
    return Identity("bot:" + slug, "bot")


def person(pid):
    return Identity("human:" + pid, "human")


def may(who, owner, group=None):
    flow = {"owner": owner, **({"group": group} if group else {})}
    return AU.may_change(None, who, flow, Chart())


def test_the_owner_and_everyone_above_it_may_change_it():
    assert may(bot("builder-a"), "bot:builder-a")
    assert may(bot("lead"), "bot:builder-a")
    assert may(person("chris"), "bot:builder-a")


def test_a_peer_or_a_report_is_refused():
    assert not may(bot("builder-b"), "bot:builder-a")
    assert not may(bot("builder-a"), "bot:lead")
    assert not may(person("pat"), "bot:builder-a")


def test_the_group_lead_and_the_leads_of_groups_it_sits_in_may_change_it():
    assert may(bot("lead"), "bot:builder-b", "group:eng")
    assert may(person("pat"), "bot:builder-a", "group:eng")
    assert may(bot("lead"), "group:eng") and not may(bot("builder-a"), "group:eng")


def test_the_top_of_the_org_may_change_anything_and_a_runner_nothing():
    assert may(Identity("human:ana", "owner"), "bot:builder-a")
    assert not may(Identity("runner:1", "runner"), "bot:builder-a")


def test_seeing_follows_the_same_rule_on_every_surface(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    aid = post(api, "automations", {"name": "Ops", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"},
                                    "steps": []})["id"]
    assert [a["id"] for a in get(api, "automations", "ben-test")["automations"]] == [aid]
    assert get(api, "automations", "cara-test")["automations"] == []
    get(api, f"automations/{aid}", "cara-test", expected=403)
    get(api, f"automations/{aid}/runs", "cara-test", expected=403)
    post(api, f"automations/{aid}/run", {}, "cara-test", expected=403)
    assert api.delete(f"/api/v2/automations/{aid}", headers=headers("cara-test")).status_code == 403
    assert get(api, f"automations/{aid}", as_bot(api, "ops"))["can_edit"] is True
    assert get(api, "automations", as_bot(api, "finance"))["automations"] == []


def test_automations_follow_their_owner_into_a_new_group(api):
    """A bot moved to another group takes its automations with it, so the new group's lead may change them."""
    eng = post(api, "groups", {"name": "Engineering", "add": {"bots": ["ops"]}})["id"]
    rel = post(api, "groups", {"name": "Release"})["id"]
    aid = post(api, "automations", {"name": "Ops", "owner": "bot:ops", "group": f"group:{eng}",
                                    "trigger": {"type": "event", "name": "x.y"}, "steps": []})["id"]
    other = post(api, "automations", {"name": "Coo", "owner": "bot:coo", "group": f"group:{eng}",
                                      "trigger": {"type": "event", "name": "x.y"}, "steps": []})["id"]
    assert api.patch(f"/api/v2/groups/{rel}", json={"add": {"bots": ["ops"]}}, headers=headers()).status_code == 200
    moved = get(api, f"automations/{aid}")
    assert moved["group"] == f"group:{rel}" and moved["version"] == 1
    assert get(api, f"automations/{other}")["group"] == f"group:{eng}"


def test_a_chat_may_name_only_an_automation_its_writer_can_see(api):
    """A chat's refs.automation (BotOps edits the open one): the automation must exist and the writer must be able to
    see it, the rule GET /api/v2/automations/{aid} applies."""
    chat = {"text": "Change this one", "refs": {"automation": "auto_missing"}}
    aid = post(api, "automations", {"name": "Ops", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"},
                                    "steps": []})["id"]
    named = {"text": "Change this one", "refs": {"automation": aid}}
    assert api.post("/api/v2/chat/ops", json=named, headers=headers()).status_code == 200
    unknown = api.post("/api/v2/chat/ops", json=chat, headers=headers())
    assert unknown.status_code == 422 and "Unknown automation" in unknown.text, unknown.text
    peer = api.post("/api/v2/chat/ops", json=named, headers=headers("cara-test"))
    assert peer.status_code == 422 and "Unknown automation" in peer.text, peer.text


class Off(Chart):
    OFF = ("lead",)


def test_a_bot_whose_switch_is_off_reaches_nothing_below_it_but_its_own_routines():
    assert not AU.may_change(None, bot("lead"), {"owner": "bot:builder-a"}, Off())
    assert AU.may_change(None, bot("lead"), {"owner": "bot:lead"}, Off())
    assert AU.may_change(None, person("chris"), {"owner": "bot:builder-a"}, Off())
