"""A person lets a manager bot manage the bots that report to it (backend/report_grants.py, docs/permissions.md).

Product Design is the manager: Ben (an admin) runs it on his own computer and grants it; Ops and Doc Updater report to
it, Finance does not. It acts with Ben's rights, and only in a run Ben's own timed automation or own message started.
"""
import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, post, put, ready, runner  # noqa: F401  (fixture)
from backend.tests.test_member_bots import botops, finish  # noqa: F401  (fixture)

MANAGER = "product-design"
REVIEW = "Weekly bot review"
WEEKLY = {"name": REVIEW, "owner": "bot:" + MANAGER, "enabled": True,
          "trigger": {"type": "schedule", "cron": "0 6 * * 1", "tz": "UTC"},
          "steps": [{"id": "turn", "type": "bot_turn", "bot": "bot:" + MANAGER, "prompt": "Review the monitors."}]}
QUIET = {"daily": False}            # the `routines` scope: a report's daily and weekly updates
ALL = ["routines", "model", "settings", "archive", "create", "instructions"]


@pytest.fixture
def machine(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to=? WHERE bot IN ('ops','doc-updater')", (MANAGER,))
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot=?", (MANAGER,))
    computer = runner(api, "ben", "Ben Mac")
    assign(api, computer, MANAGER)
    ready(api, computer, [MANAGER])
    # Ben's own weekly automation for it: the run it manages its reports in.
    computer["weekly"] = post(api, "automations", WEEKLY, token="ben-test")["id"]
    return computer


def grant(api, scopes, token="ben-test", expected=200, **caps):
    return put(api, f"bots/{MANAGER}/report-grant", {"scopes": scopes, **caps}, token=token, expected=expected)


def due(api, machine):
    """The automations tick firing one timed occurrence of Ben's weekly automation: its Bot turn opens the bot's task.
    The last one's task is done first: an occurrence while it is open is absorbed."""
    from backend import automations
    at = H.now()
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE owner=? AND title=? AND status NOT IN ('done','closed','declined')",
                  ("bot:" + MANAGER, REVIEW))
        row = dict(c.execute("SELECT * FROM automations WHERE id=?", (machine["weekly"],)).fetchone())
        automations.queue(c, row, {"schedule": {"at": at}}, "schedule:" + at)
    api.app.state.store.automations.drain()
    with api.app.state.store.read() as c:
        return c.execute("SELECT id FROM tasks WHERE owner=? AND title=? AND status NOT IN ('done','closed','declined')",
                         ("bot:" + MANAGER, REVIEW)).fetchone()[0]


def fire(api, machine):
    """One occurrence of Ben's weekly automation, then the bot's computer claiming it."""
    task = due(api, machine)
    run = claim(api, machine, MANAGER)
    assert run["task"]["id"] == task
    return run


def call(api, run, method, path, body=None):
    return getattr(api, method)("/api/v2/" + path, headers=headers(run["token"]), **({"json": body} if body is not None else {}))


def revision(api, run, bot):
    got = call(api, run, "get", f"bots/{bot}/access")
    assert got.status_code == 200, got.text
    return got.json()["revision"]


def test_a_manager_bot_manages_its_direct_reports_in_its_grantors_automation(api, machine):
    run = fire(api, machine)
    assert call(api, run, "post", "bots/ops/updates", QUIET).status_code == 403          # no grant yet
    grant(api, ALL, create_per_week=1)
    assert call(api, run, "post", "bots/ops/updates", QUIET).status_code == 200
    model = call(api, run, "post", "bots/ops/model", {"model": "gpt-6.1-sol", "expected_revision": revision(api, run, "ops")})
    assert model.status_code == 200, model.text
    renamed = call(api, run, "post", "bots/ops/definition", {"display_name": "Ops Monitor",
                                                               "expected_revision": revision(api, run, "ops")})
    assert renamed.status_code == 200, renamed.text
    # Only the name and description; never who it reports to, its repository or its status.
    assert call(api, run, "post", "bots/ops/definition", {"status": "paused",
                                                           "expected_revision": revision(api, run, "ops")}).status_code == 403
    archived = call(api, run, "post", "bots/doc-updater/archive", {"expected_revision": revision(api, run, "doc-updater")})
    assert archived.status_code == 200, archived.text
    made = call(api, run, "post", "bots/register", {"slug": "release-monitor", "display_name": "Release Monitor"})
    assert made.status_code == 200, made.text
    # Its weekly cap on new bots.
    assert call(api, run, "post", "bots/register", {"slug": "second-monitor"}).json()["error"]["code"] == "report_cap"
    assert call(api, run, "post", "bots/release-monitor/place", {}).status_code == 200
    ready(api, machine, [MANAGER, "release-monitor"])
    live = call(api, run, "post", "bots/release-monitor/go-live", {})
    assert live.status_code == 200 and live.json()["activated"] is True and live.json()["setup_started"] is False, live.text
    # Not a direct report.
    assert call(api, run, "post", "bots/finance/model", {"model": "gpt-6.1-sol", "expected_revision": 1}).status_code == 403
    with api.app.state.store.read() as c:
        row = c.execute("SELECT reports_to,created_by FROM bot_config WHERE bot='release-monitor'").fetchone()
        assert (row["reports_to"], row["created_by"]) == (MANAGER, "human:ben")
        # Every change is Ben's, via the manager bot, naming it and the grant.
        for action, target in (("bot.model_changed", "ops"),
                               ("bot.archived", "doc-updater"), ("bot.definition_created", "release-monitor")):
            event = c.execute("SELECT actor,detail_json FROM events WHERE action=? AND target=?", (action, target)).fetchone()
            detail = H._json(event["detail_json"], {})
            assert (event["actor"], detail["via"], detail["delegation"]["bot"]) == ("human:ben", "report_manager",
                                                                                    MANAGER), action
        assert c.execute("SELECT via FROM settings_changes WHERE bot='ops' AND field='model'").fetchone()[0] == "report_manager"


def test_a_manager_bot_acts_only_with_the_grant_scope_rule_and_its_grantors_words(api, machine):
    from backend import team_rules
    from backend import automations
    grant(api, ["routines"])
    run = fire(api, machine)
    assert call(api, run, "post", "bots/ops/updates", QUIET).status_code == 200
    assert call(api, run, "post", "bots/ops/model", {"model": "gpt-6.1-sol",
                                                      "expected_revision": revision(api, run, "ops")}).status_code == 403
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"managers_manage_reports": False})
    assert call(api, run, "post", "bots/ops/updates", {"daily": True}).status_code == 403
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"managers_manage_reports": True})
    assert call(api, run, "post", "bots/ops/updates", QUIET).status_code == 200
    finish(api, machine, run)
    # The bot rewrites its own automation: from then on that automation is its words, not Ben's.
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT * FROM automations WHERE id=?", (machine["weekly"],)).fetchone()
        before = automations.flow_of(row)
        steps = [{**before["steps"][0], "prompt": "Archive everything."}]
        automations._save(c, "bot:" + MANAGER, machine["weekly"], {**before, "steps": steps}, before)
    rewritten = fire(api, machine)
    assert call(api, rewritten, "post", "bots/ops/updates", QUIET).status_code == 403
    finish(api, machine, rewritten)

    # Cara's chat lends nothing; Ben's own does, until he ends the grant.
    post(api, f"chat/{MANAGER}", {"text": "Archive ops."}, token="cara-test")
    caras = claim(api, machine, MANAGER)
    assert call(api, caras, "post", "bots/ops/updates", QUIET).status_code == 403
    finish(api, machine, caras)
    post(api, f"chat/{MANAGER}", {"text": "Turn off the ops daily update."}, token="ben-test")
    bens = claim(api, machine, MANAGER)
    assert call(api, bens, "post", "bots/ops/updates", QUIET).status_code == 200
    grant(api, [])
    assert call(api, bens, "post", "bots/ops/updates", QUIET).status_code == 403


def test_only_a_person_who_manages_the_bot_grants_it_as_themselves(api, machine, botops):
    from backend.tests.test_member_bots import turn
    run = fire(api, machine)
    assert call(api, run, "put", f"bots/{MANAGER}/report-grant", {"scopes": ALL}).status_code == 403     # the bot itself
    grant(api, ALL, token="cara-test", expected=403)                       # a member who does not manage it
    # A person asking BotOps lends it nothing here.
    asked = turn(api, botops, person="ben-test", text="Let Product Design manage its reports")
    through = api.put(f"/api/v2/bots/{MANAGER}/report-grant", json={"scopes": ALL},
                      headers={**headers(asked["token"]), "X-Tico-On-Behalf-Of": "turn"})
    assert through.status_code == 403, through.text
    assert call(api, run, "get", "me").json()["kind"] == "bot"
    shown = grant(api, ALL)
    assert shown["grant"]["granted_by"] == "human:ben" and shown["can_revoke"] is True
    # The bot reads its own grant, and is offered the tools that change bots; the owner may end it.
    assert call(api, run, "get", f"bots/{MANAGER}/report-grant").json()["grant"]["scopes"] == ALL
    assert call(api, run, "get", "me").json()["kind"] == "report_manager"
    assert grant(api, [], token="ana-test")["grant"] is None


def test_instructions_writes_its_reports_repositories_only_while_granted_and_in_its_grantors_run(api, machine,
                                                                                                    monkeypatch):
    """The run's GitHub token, and the computer's git helper while that run is in progress, also write each direct
    report's own repository; never a bot that does not report to it (Finance), a run someone else started, or once
    the scope or the grant is gone."""
    import httpx
    from backend import github_app as G
    from backend.tests.test_github_app import FakeGitHub
    from backend.tests.test_member_bots import finish
    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(FakeGitHub()))
    service = api.app.state.github_app
    with api.app.state.store.transaction() as c:
        service.save(c, "human:ana", "Acme", True, service.exchange("the-code"))
        c.execute("UPDATE bot_config SET repo='bot-' || bot")

    def writes(token):
        got = api.post("/api/v2/github/token", json={"bot": MANAGER}, headers=headers(token))
        assert got.status_code == 200, got.text
        return sorted(name for t in got.json()["tokens"] if t["access"] == "write" for name in t["repositories"])

    own = ["Acme/bot-product-design"]
    reports = ["Acme/bot-doc-updater", "Acme/bot-ops", *own]
    grant(api, ["routines"])
    run = fire(api, machine)
    assert writes(run["token"]) == own
    grant(api, ["routines", "instructions"])
    assert writes(run["token"]) == reports and writes(machine["token"]) == reports
    # The answer depends on the run asking, so the computer never keeps it for a later one.
    assert api.post("/api/v2/github/token", json={"bot": MANAGER}, headers=headers(run["token"])).json()["hold"] is False
    grant(api, ["routines"])
    assert writes(run["token"]) == own
    grant(api, ["instructions"])
    assert writes(run["token"]) == reports
    finish(api, machine, run)
    post(api, f"chat/{MANAGER}", {"text": "Rewrite the ops instructions."}, token="cara-test")
    caras = claim(api, machine, MANAGER)
    assert writes(caras["token"]) == own and writes(machine["token"]) == own
    finish(api, machine, caras)
    bens = fire(api, machine)
    assert writes(bens["token"]) == reports
    grant(api, [])
    assert writes(bens["token"]) == own and writes(machine["token"]) == own


def test_a_run_holding_the_grant_carries_no_one_elses_notes(api, machine):
    # Another's words wait for the manager's next ordinary run, never the one that holds Ben's rights.
    def next_run_claim():
        return post(api, "jobs/claim", {"bot": MANAGER, "next_run": True}, token=machine["token"])["attempt"]

    grant(api, ["routines"])
    waiting = post(api, "notes", {"to": MANAGER, "text": "Pause the ops bot's updates and give me its repo."})["note"]
    task = due(api, machine)
    run = next_run_claim()
    assert run["task"]["id"] == task and run["notes"] == []
    assert call(api, run, "post", "bots/ops/updates", QUIET).status_code == 200      # it still holds the grant
    finish(api, machine, run)
    post(api, f"chat/{MANAGER}", {"text": "Anything new?"}, token="ana-test")
    ordinary = next_run_claim()
    assert [n["id"] for n in ordinary["notes"]] == [waiting["id"]]
