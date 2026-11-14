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

    assert why("reviewer-1", "engineering-manager") == "manager"
    # Two levels down, the person is not reached: the work goes to the Manager, who passes it on.
    assert why("reviewer-1", person) is None and why("seo-owner", person) is None
    assert why("engineering-manager", person) == "manager"
    assert why("reviewer-1", "qa-owner") == "peer"
    assert why("product-manager", "engineering-manager") == "peer"  # Managers are peers of each other
    assert why("engineering-manager", "reviewer-1") == "report"
    # Sideways, with no task in common: another team's Owner, and another team's Manager.
    assert why("reviewer-1", "seo-owner") is None
    assert why("reviewer-1", "feature-owner") is None
    assert why("reviewer-1", "growth-manager") is None
    assert why("product-manager", "reviewer-1") is None
    # The General Manager's team is reached by everyone.
    assert why("reviewer-1", "general-manager") == "helper" and why("seo-owner", "librarian") == "helper"


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


def seeded_under_the_assistant(api):
    """BotOps as the seed roster makes it (templates/environment-registry/employees.yaml): under the Assistant, set by
    no person, before the General Manager exists."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='assistant' WHERE bot='botops'")


def test_a_new_company_puts_botops_under_the_general_manager_whatever_line_the_seed_gave(environment):
    """Tico Team on rc.7: BotOps kept the seed's line to the Assistant, because only an unset line was filled. Setup lays
    out the built-ins' lines for a new company; a line set afterwards stays through later starts."""
    api = environment()
    seeded_under_the_assistant(api)
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    first = lines(api)
    owner = first["general-manager"]
    assert owner.startswith("human:") and first["assistant"] == owner and first["finance-owner"] == owner
    team = (
        "botops",
        "librarian",
        "strategy-owner",
        "rules-owner",
        "design-owner",
        "automations-owner",
        "updates-owner",
    )
    assert {slug: first[slug] for slug in team} == dict.fromkeys(team, "general-manager")
    assert [slug for slug, line in first.items() if not line] == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='assistant' WHERE bot='botops'")  # a person moves it back
        api.app.state.onboarding.complete_company(c)
    assert lines(api) == {**first, "botops": "assistant"}


@pytest.mark.parametrize("botops_line", ["assistant", "general-manager"])
def test_a_company_started_on_rc7_starts_again_with_the_same_bots_and_lines(environment, monkeypatch, botops_line):
    """A company made on rc.7 (BotOps on the seed's line, or moved under the General Manager by hand since) starts on
    this release: no second built-in, no bot without a manager, no bot hired, no setup task filed, no line changed."""
    from backend.onboarding import Onboarding

    api = environment()
    seeded_under_the_assistant(api)
    with monkeypatch.context() as rc7:
        # rc.7 filled only a line nobody had set, so the seed's line stayed.
        fill = Onboarding.ensure_general_team
        rc7.setattr(Onboarding, "ensure_general_team", lambda self, c, laid=(): fill(self, c))
        assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
        machine(api)
    assert lines(api)["botops"] == "assistant"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to=? WHERE bot='botops'", (botops_line,))

    def company():
        return (
            lines(api),
            rows(api, "SELECT id, slug, state FROM bots ORDER BY id"),
            rows(api, "SELECT id, title FROM tasks WHERE owner_id='bot:botops' ORDER BY id"),  # the bots it sets up
        )

    before = company()
    restarted = environment(seed_roster=False)
    with restarted.app.state.store.transaction() as c:
        restarted.app.state.onboarding.complete_company(c)
    assert company() == before
    assert before[0]["botops"] == botops_line and [slug for slug, line in before[0].items() if not line] == []
    slugs = [row["slug"] for row in before[1]]
    assert len(slugs) == len(set(slugs)) and not {"growth-manager-2", "botops-2", "general-manager-2"} & set(slugs)


MANAGERS = (
    ("product-manager", "feature-owner"),
    ("engineering-manager", "reviewer-1"),
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
