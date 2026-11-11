"""The chart a new company starts with: one Manager per team under the person, the team's Owners under their Manager,
the built-ins under the General Manager; and Nearby reach on it (docs/reach.md). Initech is the fictional company."""

import pytest

from backend import automations, reach, report_managers, task_caps, usage
from backend import hubdb as H
from backend.auth import Identity
from backend.store import Problem
from backend.tests.helpers.sample_company import booted, environment, machine, rows, settings_for, signed_in  # noqa: F401


def started(environment):
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    machine(api)
    with api.app.state.store.transaction() as c:
        api.app.state.onboarding.complete_company(c)
        manager = c.execute("SELECT id FROM bots WHERE slug='growth-manager'").fetchone()["id"]
        # A Growth seat filled later: the SEO Owner reports to the Growth Manager.
        c.execute(
            "INSERT INTO bots(id, slug, display_name, state) VALUES('seo-owner', 'seo-owner', 'SEO Owner', 'active')"
        )
        c.execute(
            "INSERT INTO bot_config(bot, team, owner_id, reports_to) VALUES('seo-owner', 'growth', 'owner', ?)",
            (manager,),
        )
    with api.app.state.store.read() as c:
        ctx = reach.context(c)
    return ctx, {row["slug"]: "bot:" + bid for bid, row in ctx["bots"].items()}


def test_a_seat_bot_reaches_its_manager_and_its_peers_and_not_the_person_or_another_team(environment, monkeypatch):
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)
    ctx, bot = started(environment)
    person = ctx["owner"]

    def why(sender, receiver):
        found = reach.decide(ctx, bot[sender], receiver if receiver == person else bot[receiver])
        return found.why if found.allowed else None

    assert why("builder", "engineering-manager") == "manager"
    # Two levels down, the person is not reached: the work goes to the Manager, who passes it on.
    assert why("builder", person) is None and why("seo-owner", person) is None
    assert why("engineering-manager", person) == "manager"
    assert why("builder", "reviewer-1") == "peer"
    assert why("product-manager", "engineering-manager") == "peer"  # Managers are peers of each other
    assert why("engineering-manager", "builder") == "report"
    # Sideways, with no task in common: another team's Owner, and another team's Manager.
    assert why("builder", "seo-owner") is None
    assert why("builder", "product-owner") is None
    assert why("builder", "growth-manager") is None
    assert why("product-manager", "builder") is None
    # The General Manager's team is reached by everyone.
    assert why("builder", "general-manager") == "helper" and why("seo-owner", "librarian") == "helper"


def lines(api):
    """{slug: the slug or person it reports to} for every bot that is not archived."""
    found = rows(
        api,
        "SELECT b.id, b.slug, bc.reports_to FROM bots b JOIN bot_config bc ON bc.bot=b.id WHERE b.state<>'archived'",
    )
    slugs = {r["id"]: r["slug"] for r in found}
    return {r["slug"]: slugs.get(r["reports_to"], r["reports_to"]) for r in found}


def test_every_bot_has_one_manager_and_the_built_ins_are_the_general_managers_team(environment):
    api = environment()
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    machine(api)
    with api.app.state.store.transaction() as c:
        api.app.state.onboarding.complete_company(c)
    first = lines(api)
    owner = first["general-manager"]
    assert owner.startswith("human:") and first["assistant"] == owner
    for slug in ("botops", "librarian", "strategy-owner", "rules-owner", "design-owner", "automations-owner"):
        assert first[slug] == "general-manager", slug
    assert [slug for slug, line in first.items() if not line] == []
    groups = {g["id"]: g for g in api.get("/api/v2/groups", headers=signed_in()).json()}
    general = groups["built-in"]
    assert general["name"] == "General"
    # No bot leads a group: a lead sets its group's reach and limits, which stay with people.
    assert [g["id"] for g in groups.values() if str(g.get("lead") or "").startswith("bot:")] == []
    assert "assistant" not in general["bots"] and {"general-manager", "botops", "librarian"} <= set(general["bots"])
    # A later start makes no second built-in and changes no line; a line a person set stays.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='product-manager' WHERE bot='librarian'")
        api.app.state.onboarding.complete_company(c)
    assert lines(api) == {**first, "librarian": "product-manager"}


MANAGERS = (
    ("product-manager", "product-owner"),
    ("engineering-manager", "builder"),
    ("growth-manager", "seo-owner"),
    ("success-manager", "support-owner"),
)


@pytest.mark.parametrize("manager,report", MANAGERS)
def test_a_manager_changes_its_reports_model_name_and_routines_and_nothing_that_widens_it(environment, manager, report):
    """What "Managers and their reports" (docs/permissions.md) gives each Manager of the default chart, and what stays
    with people. A change to either list is a change to this test."""
    api = environment()
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    machine(api)
    auth, admin = api.app.state.auth, api.app.state.onboarding.admin
    with api.app.state.store.transaction() as c:
        api.app.state.onboarding.complete_company(c)
        boss = H.resolve_bot(c, "success-manager")["id"]
        c.execute(
            "INSERT INTO bots(id, slug, display_name, state) VALUES('support-owner', 'support-owner', 'S', 'active')"
        )
        c.execute(
            "INSERT INTO bot_config(bot, team, owner_id, reports_to) VALUES('support-owner', 'success', 'owner', ?)",
            (boss,),
        )
        boss = H.resolve_bot(c, "growth-manager")["id"]
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('seo-owner', 'seo-owner', 'S', 'active')")
        c.execute(
            "INSERT INTO bot_config(bot, team, owner_id, reports_to) VALUES('seo-owner', 'growth', 'owner', ?)", (boss,)
        )
    with api.app.state.store.read() as c:
        me, it = H.resolve_bot(c, manager)["id"], H.resolve_bot(c, report)["id"]
        who = Identity("bot:" + me, "bot")
        # Given: its model, its task model, its name and description, and its routines.
        assert [auth.report_manager(c, who, it, scope) for scope in report_managers.SCOPES] == [True, True, True]
        assert automations.may_change(c, who, {"owner": "bot:" + it})
        # A hire or an archive only through the BotOps ask, three of each in seven days.
        assert not any(auth.report_lifecycle(c, who, it, op, "bot:" + me) for op in report_managers.LIFECYCLE_OPS)
        asked = Identity("bot:" + me, "bot", via="botops")
        assert auth.report_lifecycle(c, asked, it, "archive") and auth.report_lifecycle(c, asked, "new", "create", me)
        assert (report_managers.cap_limit(c, "create"), report_managers.cap_limit(c, "archive")) == (3, 3)
        # Refused: everything `Auth.bot_manager` gates (instructions, access, repository access, status, computer),
        # the rest of the definition, reach, spend limits and task caps.
        assert not auth.bot_manager(c, who, it) and not auth.botops_manages(c, who, it)
        for field in ("reports_to", "status", "repo", "owners"):
            with pytest.raises(Problem):
                admin.report_change(c, who, it, {field})
        assert not reach.may_edit(c, auth, who, "bot:" + it, reach.context(c))
        assert usage.led_bots(c, who) == set()
        with pytest.raises(Problem):
            task_caps.may_set(c, auth, who, "bot:" + it)
        # Another team's Owner, and a built-in, are nobody's report here.
        other = H.resolve_bot(c, "librarian")["id"]
        assert not any(auth.report_manager(c, who, other, scope) for scope in report_managers.SCOPES)


def test_the_general_manager_changes_nothing_on_the_built_ins_under_it(environment):
    api = environment()
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    machine(api)
    auth = api.app.state.auth
    with api.app.state.store.transaction() as c:
        api.app.state.onboarding.complete_company(c)
    with api.app.state.store.read() as c:
        me = H.resolve_bot(c, "general-manager")["id"]
        who, asked = Identity("bot:" + me, "bot"), Identity("bot:" + me, "bot", via="botops")
        for slug in ("botops", "librarian", "strategy-owner"):
            it = H.resolve_bot(c, slug)["id"]
            assert not any(auth.report_manager(c, who, it, scope) for scope in report_managers.SCOPES), slug
            assert not auth.report_lifecycle(c, asked, it, "archive") and not auth.bot_manager(c, who, it)
            assert not reach.may_edit(c, auth, who, "bot:" + it, reach.context(c))
        assert usage.led_bots(c, who) == set()
