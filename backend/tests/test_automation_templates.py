"""The inbox templates (backend/automation_templates): each one is a flow the hub checks once its roles are filled; the fill
resolves roles on the org chart to the first bot that can take the work, takes out what the owner may not use, sets the
llm step's `as` by owner kind and gives a group Sort; a group with no lead to act as is refused (422) when nothing it
would do can run; a person cannot fill one for an inbox they cannot read; and a fill saves nothing."""

import json

import pytest
from urllib.parse import urlencode

from backend import automation_actions as AA
from backend import automation_engine as E
from backend import automation_flow as F
from backend import automation_templates as T
from backend import bot_settings as BS
from backend.store import H
from backend.tests.automation_fakes import Services
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.automation_cli import cli
from backend.tests.helpers.automation_templates import fill

# The catalog role each bot is made from (the fixture's bots; `ben` operates the product bots, `ana` the rest).
STAFF = {
    "assistant": "general-counsel",
    "cpo": "engineering-manager",
    "finance": "finance-owner",
    "product-design": "growth-manager",
    "ops": "success-manager",
    # Marketing and sales are one template now (the Growth Manager): product-design holds both roles, and
    # doc-updater takes a template only where a test gives it one.
    "doc-updater": None,
}


def set_templates(api, roles):
    """Give bots their catalog template (bot_settings), a None taking it away."""
    with api.app.state.store.transaction() as c:
        for bot, template in roles.items():
            BS.update(c, bot, {"template": template})


def listed(api, name):
    """A channel on the team's Slack list (Settings > Tools > Slack)."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO slack_channels(key,name,created_at,updated_at) VALUES(?,?,?,?)",
            ("#" + name, name, "2026-10-28T00:00:00Z", "2026-10-28T00:00:00Z"),
        )


def set_config(api, bot, key, value):
    """Set one key of a bot's declaration (bot_settings), e.g. its profile or harness."""
    with api.app.state.store.transaction() as c:
        BS.update(c, bot, {key: value})


def onboarding(api, bot):
    """A bot that is active on the chart but still onboarding (needs_setup): it learns and takes no task yet."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot=?", (bot,))


def by_id(items):
    return {item["id"]: item for item in items}


# The judge answers no to every question, so a message falls through the route to the draft turn.
NO = {qid: {"type": "noul", "noul": 0.0} for qid in ("only_founder", "meet", "bug", "money", "partner", "nothing")}
MAIL = {
    "message": {
        "source": "mail",
        "mailbox": "ana@acme.example",
        "id": "m9",
        "thread": "t9",
        "from": "sender@acme.example",
        "from_name": "Sender Example",
        "subject": "Board pack",
        "snippet": "Deck attached",
        "body": "Can you send the deck by Friday?",
    }
}
SLACK = {
    "message": {
        "source": "slack",
        "inbox": "slack:#general",
        "channel": "C1",
        "ts": "1.1",
        "thread_ts": "1.1",
        "from": "person:ben",
        "text": "Can you send the deck by Friday?",
    }
}


def run_to_turn(flow, payload):
    """The filled flow run on one message until it waits for its draft turn: the turn the services were asked for."""
    services = Services(answers=NO)
    run = E.execute(flow, payload, services)
    assert run["status"] == "waiting", run
    return services.turns[-1]


def bot_flow(api, bot, inbox="ana@acme.example"):
    with api.app.state.store.read() as c:
        flow = T.fill(c, "founder", f"bot:{bot}", inbox, api.app.state.store.settings)
    F.check(flow)
    return flow


def assert_sorted_draft(flow):
    draft = by_id(flow["steps"])["draft"]
    assert draft["type"] == "decide" and "as" not in draft
    assert list(F.choosing(flow["steps"])) == []


def test_every_template_is_a_flow_the_hub_checks_once_filled(api):
    set_templates(api, STAFF)
    for tid in T.ORDER:
        flow = fill(api, tid, "person:ana", "ana@acme.example")
        assert flow["enabled"] is False and flow["owner"] == "person:ana"
        assert flow["trigger"] == {"type": "message", "inbox": "ana@acme.example"}
        F.check(flow)  # raises F.Invalid when the shape or a reference is wrong


def test_the_roles_resolve_on_the_org_chart(api):
    set_templates(api, STAFF)
    flow = fill(api, "founder", "person:ana", "ana@acme.example")
    dests = by_id(flow["destinations"])
    assert [a["args"]["owner"] for a in dests["legal"]["then"]] == ["bot:assistant"]
    assert [a["args"]["owner"] for a in dests["engineering"]["then"]] == ["bot:cpo"]
    assert [a["args"]["owner"] for a in dests["finance"]["then"]] == ["bot:finance"]
    assert by_id(flow["steps"])["draft"]["as"] == "assistant:ana"


def test_a_wake_the_owner_may_not_make_becomes_a_task_for_that_bot(api):
    set_templates(api, STAFF)
    # Ana may not wake product-design (Ben's bot): the marketing destination opens a task for it instead.
    marketing = by_id(fill(api, "founder", "person:ana", "ana@acme.example")["destinations"])["marketing"]
    assert [a["do"] for a in marketing["then"]] == ["create_task"]
    assert marketing["then"][0]["args"]["owner"] == "bot:product-design"


def test_a_wake_the_owner_may_make_stays_a_wake_and_the_bot_turn_runs_as_that_bot(api):
    set_templates(api, STAFF)
    # product-design is its own owner here: it may tell itself, and its llm step runs as itself. The fill is called
    # directly: who may make an automation for a bot is the route's rule, tested with the rest of the access checks.
    with api.app.state.store.read() as c:
        flow = T.fill(c, "founder", "bot:product-design", "ben@acme.example", api.app.state.store.settings)
    F.check(flow)
    marketing = by_id(flow["destinations"])["marketing"]
    assert [a["do"] for a in marketing["then"]] == ["wake_bot"]
    assert marketing["then"][0]["args"]["bot"] == "bot:product-design"
    assert by_id(flow["steps"])["draft"]["as"] == "bot:product-design"


def test_a_destination_with_no_bot_to_reach_is_dropped_with_what_points_at_it(api):
    set_templates(api, {**STAFF, "ops": None})  # no support lead on the chart
    flow = fill(api, "support", "person:ana", "ana@acme.example")
    assert [d["id"] for d in flow["destinations"]] == ["me", "engineering", "finance", "sales", "archive"]
    steps = by_id(flow["steps"])
    assert "customer" not in steps["ask"]["questions"]  # only the rule to Support asked it
    assert all(rule.get("to") != "support" for rule in steps["route"]["rules"])
    assert "support" not in steps["draft"]["choose"]["to"] and steps["draft"]["choose"]["default"] == "me"


def test_a_group_owner_gets_sort_and_its_lead_runs_the_task_and_meeting_routes(api):
    set_templates(api, STAFF)
    gid = post(api, "groups", {"name": "Acme Desk", "add": {"people": ["ana"]}, "lead": "person:ana"})["id"]
    listed(api, "general")  # a group reads a Slack channel, not a mailbox
    flow = fill(api, "founder", f"group:{gid}", "slack:#general")
    F.check(flow)
    assert list(F.choosing(flow["steps"])) == []
    assert all("as" not in step for step in flow["steps"])
    sort = by_id(flow["steps"])["draft"]
    assert sort["type"] == "decide" and "level" not in sort
    assert set(sort["questions"]["route"]["criteria"]) == {
        "me",
        "legal",
        "engineering",
        "finance",
        "marketing",
        "archive",
    }
    route = by_id(flow["steps"])["draft_route"]["rules"]
    criteria = list(sort["questions"]["route"]["criteria"])
    assert [rule["to"] for rule in route[:-1]] == criteria  # one route per criterion, in its order
    assert all(
        rule["when"] == [{"field": "draft.route", "op": "is", "value": dest, "min": 0.5}]
        for rule, dest in zip(route[:-1], criteria)
    )
    assert route[-1]["when"] == [] and route[-1]["to"] == "me"  # the default, when nothing matches
    assert by_id(flow["destinations"])["engineering"]["then"][0]["args"]["owner"] == "bot:cpo"  # the task route
    route = by_id(flow["steps"])["route"]["rules"]
    assert any(a["do"] == "draft_reply" for rule in route for a in rule.get("then", []))  # the meeting route
    with api.app.state.store.read() as c:
        assert AA.acts_as(c, {"flow": {"owner": f"group:{gid}"}}) == "human:ana"  # the lead is who they run as


def test_blank_is_a_sort_to_me_or_archive(api):
    flow = fill(api, "blank", "person:ana", "ana@acme.example")
    assert [d["id"] for d in flow["destinations"]] == ["me", "archive"]
    assert [step["type"] for step in flow["steps"]] == ["filter", "decide", "filter"]
    assert flow["steps"][0]["rules"] == []
    assert set(flow["steps"][1]["questions"]["route"]["criteria"]) == {"me", "archive"}


def test_a_person_cannot_fill_a_template_for_an_inbox_they_cannot_read(api):
    fill(api, "founder", "person:ana", "ben@acme.example", expected=403)  # Ana's automation on Ben's mail
    fill(api, "founder", "person:ben", "ana@acme.example", token="ben-test", expected=403)
    fill(api, "founder", "person:ana", "ana@acme.example", token="ben-test", expected=403)  # not Ana's to change


def test_the_answers_match_the_published_contract(api):
    from backend.tests.helpers.openapi_v2 import conforms

    set_templates(api, STAFF)
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    shape = {
        op["operationId"]: op["responses"]["200"]["content"]["application/json"]["schema"]
        for path in document["paths"].values()
        for op in path.values()
        if op.get("operationId") in ("listAutomationTemplates", "getAutomationTemplate")
    }
    assert conforms(get(api, "automations/templates"), shape["listAutomationTemplates"], document) is None
    before = get(api, "automations")["automations"]
    filled = fill(api, "founder", "person:ana", "ana@acme.example")
    assert conforms(filled, shape["getAutomationTemplate"], document) is None
    assert get(api, "automations")["automations"] == before, "a fill saves nothing"
    # The listing and an unknown template.
    listing = get(api, "automations/templates")["templates"]
    assert [t["id"] for t in listing] == ["founder", "support", "sales", "blank"]
    assert all(t["destinations"] for t in listing)
    fill(api, "nope", "person:ana", "ana@acme.example", expected=404)
    # The CLI fills the same template as the API.
    assert [t["id"] for t in cli(api, "ana-test", "templates")["templates"]] == ["founder", "support", "sales", "blank"]
    via_cli = cli(api, "ana-test", "template", "founder", "--owner", "person:ana", "--inbox", "ana@acme.example")
    assert via_cli == filled


def test_an_active_bot_still_onboarding_is_passed_over_for_the_next_one(api):
    set_templates(api, {**STAFF, "doc-updater": "engineering-manager"})
    onboarding(api, "cpo")  # active on the chart, but it learns and takes no task yet
    flow = fill(api, "founder", "person:ana", "ana@acme.example")
    assert by_id(flow["destinations"])["engineering"]["then"][0]["args"]["owner"] == "bot:doc-updater"
    F.check(flow)


def test_a_bot_whose_profile_takes_no_tasks_is_passed_over_for_the_next_one(api):
    set_templates(api, {**STAFF, "doc-updater": "engineering-manager"})
    set_config(api, "cpo", "profile", "responder")  # answers in chat and takes no tasks
    flow = fill(api, "founder", "person:ana", "ana@acme.example")
    assert by_id(flow["destinations"])["engineering"]["then"][0]["args"]["owner"] == "bot:doc-updater"


def test_a_bot_on_a_synced_harness_is_passed_over_for_the_next_one(api):
    set_templates(api, {**STAFF, "doc-updater": "engineering-manager"})
    set_config(api, "cpo", "harness", "dots")  # works on another platform and only syncs its chat here
    flow = fill(api, "founder", "person:ana", "ana@acme.example")
    assert by_id(flow["destinations"])["engineering"]["then"][0]["args"]["owner"] == "bot:doc-updater"


def test_a_wake_that_becomes_a_task_goes_to_the_next_bot_that_takes_one(api):
    # Marketing's first candidate is onboarding, and Ana may tell neither of Ben's bots: the task goes to the second.
    set_templates(api, {**STAFF, "cpo": "growth-manager", "doc-updater": "engineering-manager"})
    onboarding(api, "cpo")
    marketing = by_id(fill(api, "founder", "person:ana", "ana@acme.example")["destinations"])["marketing"]
    assert [a["do"] for a in marketing["then"]] == ["create_task"]
    assert marketing["then"][0]["args"]["owner"] == "bot:product-design"


def test_a_wake_that_becomes_a_task_with_no_bot_left_drops_the_destination(api):
    set_config(api, "product-design", "profile", "responder")  # the only marketing bot takes no tasks
    flow = fill(api, "founder", "person:ana", "ana@acme.example")
    assert "marketing" not in by_id(flow["destinations"])
    steps = by_id(flow["steps"])
    assert "partner" not in steps["ask"]["questions"]  # only the rule to Marketing asked it
    assert all(rule.get("to") != "marketing" for rule in steps["route"]["rules"])
    assert "marketing" not in steps["draft"]["choose"]["to"]
    F.check(flow)


def test_a_responder_owner_still_gets_a_wake_for_itself(api):
    set_templates(api, STAFF)
    set_config(api, "product-design", "profile", "responder")
    # A wake is a chat message, not a task: the bot may wake itself whatever its profile.
    with api.app.state.store.read() as c:
        flow = T.fill(c, "founder", "bot:product-design", "ben@acme.example", api.app.state.store.settings)
    marketing = by_id(flow["destinations"])["marketing"]
    assert [a["do"] for a in marketing["then"]] == ["wake_bot"]
    assert marketing["then"][0]["args"]["bot"] == "bot:product-design"


def test_an_onboarding_bot_owner_keeps_only_what_it_may_do(api):
    set_templates(api, STAFF)
    onboarding(api, "cpo")  # the owner files no task while it learns; its wakes stand
    with api.app.state.store.read() as c:
        flow = T.fill(c, "founder", "bot:cpo", "ben@acme.example", api.app.state.store.settings)
    F.check(flow)
    assert [d["id"] for d in flow["destinations"]] == ["me", "marketing", "archive"]


def test_a_group_with_no_lead_is_refused_with_a_422(api):
    set_templates(api, STAFF)
    gid = post(api, "groups", {"name": "Acme Support"})["id"]
    listed(api, "general")
    for tid in T.ORDER:  # nothing one of them does runs for a group with no lead
        fill(api, tid, f"group:{gid}", "slack:#general", expected=422)


def test_the_draft_turn_is_asked_about_the_mail_it_handles(api):
    set_templates(api, STAFF)
    turn = run_to_turn(fill(api, "founder", "person:ana", "ana@acme.example"), MAIL)
    assert "Board pack" in turn["prompt"] and "Can you send the deck by Friday?" in turn["prompt"]


def test_the_draft_turn_is_asked_about_the_slack_text_it_handles(api):
    set_templates(api, STAFF)
    listed(api, "general")
    turn = run_to_turn(fill(api, "founder", "person:ana", "slack:#general"), SLACK)
    assert "Can you send the deck by Friday?" in turn["prompt"]


def test_the_turn_task_carries_the_mail_subject_and_body(api):
    set_templates(api, STAFF)
    flow = bot_flow(api, "assistant")
    prompt = run_to_turn(flow, MAIL)["prompt"]
    with api.app.state.store.transaction() as c:
        started = AA.start_turn(
            c, {"id": "auto_founder", "flow": flow}, "run_1", "draft", "bot:assistant", prompt, None
        )
        words = H.task_words(c, started["ref"])  # the turn's instructions: Tico's, to the bot in its run
    assert "Board pack" in words and "Can you send the deck by Friday?" in words


def test_a_mail_turn_is_titled_by_the_mail_only_on_a_private_task(api):
    """An inbox's turns all shared its name ("Ana's inbox"). A mail's turn is titled by its sender and subject where
    only the task's readers see the title (the assistant's tasks are private, as a general counsel's); a company-visible
    task keeps the automation's name, since its title is in every teammate's lists and the mail is not."""
    set_templates(api, STAFF)
    reply = {
        "message": {
            **MAIL["message"],
            "from_name": '"Winberg Law" <legal@winberg.example>',
            "subject": "RE: Fwd: Demand letter",
        }
    }
    for bot, private in (("assistant", True), ("cpo", False)):
        flow = bot_flow(api, bot)
        turn = run_to_turn(flow, reply)
        assert turn["about"] == "Winberg Law: Demand letter"
        with api.app.state.store.transaction() as c:
            started = AA.start_turn(
                c,
                {"id": "auto_" + bot, "flow": flow},
                "run_" + bot,
                "draft",
                f"bot:{bot}",
                turn["prompt"],
                None,
                about=turn["about"],
            )
            task = H.task(c, started["ref"])
        assert bool(task["private"]) is private
        assert task["title"] == ("Winberg Law: Demand letter" if private else flow["name"])


def test_a_persons_message_routine_turn_stays_the_assistants_and_runs_as_a_personal_turn(api):
    """The one private task the shared Assistant may hold: a person's own message routine's turn (as before). Its run is
    claimed personal for that person, so it works in a throwaway copy with no git credentials."""
    from backend.tests.helpers.api import assign, claim, ready, runner

    set_templates(api, STAFF)
    flow = bot_flow(api, "assistant")
    prompt = run_to_turn(flow, MAIL)["prompt"]
    with api.app.state.store.transaction() as c:
        started = AA.start_turn(c, {"id": "auto_mine", "flow": flow}, "run_1", "draft", "bot:assistant", prompt, None)
        task = H.task(c, started["ref"])
    assert task["owner_id"] == "bot:assistant" and task["private"]
    machine = runner(api)
    with api.app.state.store.read() as c:
        held = c.execute(
            "SELECT generation FROM assignments WHERE bot=?", (H.resolve_bot(c, "assistant")["id"],)
        ).fetchone()
    assign(api, machine, "assistant", held["generation"] if held else 0)
    ready(api, machine, ["assistant"])
    attempt = claim(api, machine)
    assert attempt["task"]["id"] == task["id"] and attempt["personal"] is True


def test_an_assistant_run_reads_a_routine_turn_only_for_the_person_it_works_for(api):
    """One Assistant serves everyone: Ana's routine turn task is hers (its requester), and her routine's run reads it
    and no other private task, not even one shared with her; the Assistant's run for Ben, and its manager, read none of
    hers."""
    from backend.tests.helpers.api import assign, claim, ready, runner

    set_templates(api, STAFF)
    turns = {}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='assistant'")
    for person, inbox in (("ana", "ana@acme.example"), ("ben", "ben@acme.example")):
        flow = bot_flow(api, "assistant", inbox)
        prompt = run_to_turn(flow, MAIL)["prompt"]
        with api.app.state.store.transaction() as c:
            started = AA.start_turn(
                c, {"id": "auto_" + person, "flow": flow}, "run_" + person, "draft", "bot:assistant", prompt, None
            )
            turns[person] = H.task(c, started["ref"])
    assert turns["ana"]["requester_id"] == "human:ana" and turns["ben"]["requester_id"] == "human:ben"
    machine = runner(api)
    with api.app.state.store.read() as c:
        held = c.execute(
            "SELECT generation FROM assignments WHERE bot=?", (H.resolve_bot(c, "assistant")["id"],)
        ).fetchone()
        assert not H.task_private_readable(c, "bot:cpo", turns["ana"])  # the Assistant's manager
    with api.app.state.store.transaction() as c:  # shared with Ana, asked for by Cara
        shared = H.task_create(c, "human:cara", "Offer", "Ours.", "bot:ops", private=True, readers=["human:ana"])
    assign(api, machine, "assistant", held["generation"] if held else 0)
    ready(api, machine, ["assistant"])
    runs = {}
    for _ in turns:
        attempt = claim(api, machine)
        assert attempt and attempt["personal"] is True, attempt
        runs[next(p for p, t in turns.items() if t["id"] == attempt["task"]["id"])] = attempt["token"]
    for person, token in runs.items():
        other = "ben" if person == "ana" else "ana"
        assert api.get("/api/v2/tasks/" + turns[person]["id"], headers=headers(token)).status_code == 200
        assert api.get("/api/v2/tasks/" + turns[other]["id"], headers=headers(token)).status_code == 404
        listed = api.get("/api/v2/tasks", headers=headers(token)).text
        assert turns[other]["id"] not in listed
        # A routine turn reads its own task and no other private one: not even one shared with its person.
        assert api.get("/api/v2/tasks/" + shared["id"], headers=headers(token)).status_code == 404
        assert shared["id"] not in listed
    assert api.get("/api/v2/tasks/" + turns["ana"]["id"], headers=headers("ana-test")).status_code == 200


def test_a_routine_turn_reads_the_turn_it_was_woken_for_and_no_other_of_its_persons(api):
    """Two open turns of Ana's routine: an earlier one that named the Assistant as requester, and a new one hers. Each
    run reads the turn it was woken for and not the other, the new turn's claim does not name the earlier one, and once
    Ana may not sign in her turn's run reads nothing."""
    from backend.tests.helpers.api import assign, claim, ready, runner

    set_templates(api, STAFF)
    flow = bot_flow(api, "assistant")
    prompt = run_to_turn(flow, MAIL)["prompt"]
    with api.app.state.store.transaction() as c:
        earlier = H.task(
            c,
            AA.start_turn(c, {"id": "auto_old", "flow": flow}, "run_old", "draft", "bot:assistant", prompt, None)[
                "ref"
            ],
        )
        c.execute("UPDATE tasks SET requester_id='bot:assistant' WHERE id=?", (earlier["id"],))  # filed before #4851
        newer = H.task(
            c,
            AA.start_turn(c, {"id": "auto_new", "flow": flow}, "run_new", "draft", "bot:assistant", prompt, None)[
                "ref"
            ],
        )
        # The earlier turn's routine, as the run that filed it ran it (its wake names run_old): whose inbox that version
        # works on, Ana's (rooms._routine_person), though the routine works on Ben's now.
        moved = {**flow, "trigger": {**flow["trigger"], "inbox": "ben@acme.example"}}
        c.execute(
            "INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,"
            "updated_at) VALUES('auto_old','Inbox','bot:assistant','message',1,?,2,?,'human:ana',?)",
            (json.dumps(moved), H.now(), H.now()),
        )
        c.execute(
            "INSERT INTO automation_versions(automation_id,n,actor,created_at,flow,status) "
            "VALUES('auto_old',1,'human:ana',?,?,'active'),('auto_old',2,'human:ana',?,?,'active')",
            (H.now(), json.dumps(flow), H.now(), json.dumps(moved)),
        )
        c.execute(
            "INSERT INTO automation_runs(id,automation_id,version,started_at,status) "
            "VALUES('run_old','auto_old',1,?,'running')",
            (H.now(),),
        )
        # A wake stored before this release listed the earlier turn to reconcile with, and a company task of a bot the
        # Assistant may not read the activity of.
        from backend.tests.helpers.api import restrict

        restrict(c, "finance", people=["ana"])
        hidden = H.task_create(c, "human:ana", "Close the books", "Company.", "bot:finance", private=False, lint=False)
        wake = c.execute(
            "SELECT m.id,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE json_extract(m.refs,'$.task')=?",
            (newer["id"],),
        ).fetchone()
        refs = {
            **json.loads(wake["refs"]),
            "reconcile": {"with_actor": "bot:assistant", "active": [{"id": earlier["id"]}, {"id": hidden["id"]}]},
        }
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), wake["id"]))
    machine = runner(api)
    with api.app.state.store.read() as c:
        held = c.execute(
            "SELECT generation FROM assignments WHERE bot=?", (H.resolve_bot(c, "assistant")["id"],)
        ).fetchone()
    assign(api, machine, "assistant", held["generation"] if held else 0)
    ready(api, machine, ["assistant"])
    runs, keys = {}, {}
    for n in range(2):
        attempt = claim(api, machine, key=f"claim-{n}")
        runs[attempt["task"]["id"]], keys[attempt["task"]["id"]] = attempt, f"claim-{n}"
    old, new = runs[earlier["id"]], runs[newer["id"]]
    # A receipt saved before the filter: every copy of the wake in it (message and history, refs and refs_json) names
    # both again; its replay names neither.
    with api.app.state.store.transaction() as c:
        saved = c.execute(
            "SELECT rowid,response FROM idempotency WHERE operation='/api/v2/jobs/claim' AND key=?",
            (keys[newer["id"]],),
        ).fetchone()
        receipt = json.loads(saved["response"])
        stale = {"with_actor": "bot:assistant", "active": [{"id": earlier["id"]}, {"id": hidden["id"]}]}
        for message in [receipt["attempt"]["message"], *receipt["attempt"].get("history", [])]:
            if message.get("id") == wake["id"]:
                message["refs"] = {**(message.get("refs") or {}), "reconcile": stale}
                message["refs_json"] = json.dumps(message["refs"])
        c.execute("UPDATE idempotency SET response=? WHERE rowid=?", (json.dumps(receipt), saved["rowid"]))
    replayed = claim(api, machine, key=keys[newer["id"]])
    for shown in (new, replayed):
        for named in (earlier["id"], hidden["id"]):
            assert named not in json.dumps(shown), "the new turn's claim names a task it may not read"
    for run, own, other in ((old, earlier, newer), (new, newer, earlier)):
        assert api.get("/api/v2/tasks/" + own["id"], headers=headers(run["token"])).status_code == 200
        assert api.get("/api/v2/tasks/" + other["id"], headers=headers(run["token"])).status_code == 404
    # A duplicate it may not read is never named to it: not by a dry run, not by a create's refusal.
    with api.app.state.store.transaction() as c:
        twin = H.task_create(c, "human:ben", "Pay run", "Ben's.", "bot:ops", private=False, lint=False)["id"]
        c.execute(  # an earlier private one the Assistant asked for, filed before #4841
            "UPDATE tasks SET private=1, requester_id='bot:assistant' WHERE id=?", (twin,)
        )
    ask = {"owner": "ops", "title": "Pay run", "body": "Mine."}
    for path in ("tasks/dry-run", "tasks"):
        answer = api.post("/api/v2/" + path, json=ask, headers=headers(new["token"]))
        assert twin not in answer.text, (path, answer.text)
    with api.app.state.store.transaction() as c:
        doc = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in doc["people"]:
            if person.get("id") == "ana":
                person["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(doc),))
    for run, own in ((new, newer), (old, earlier)):  # the earlier turn too: its routine works on Ana's inbox
        assert api.get("/api/v2/tasks/" + own["id"], headers=headers(run["token"])).status_code == 404


def test_an_earlier_routine_turn_whose_routine_run_cannot_be_found_reads_nothing(api):
    """An earlier turn named the Assistant as requester; with no run of its routine to say whose inbox it worked on, its
    person is unknown, so its run reads nothing and is never handed out."""
    from backend import rooms
    from backend.tests.helpers.api import assign, claim, ready, runner

    set_templates(api, STAFF)
    flow = bot_flow(api, "assistant")
    prompt = run_to_turn(flow, MAIL)["prompt"]
    with api.app.state.store.transaction() as c:
        turn = AA.start_turn(c, {"id": "auto_lost", "flow": flow}, "run_lost", "draft", "bot:assistant", prompt, None)
        c.execute("UPDATE tasks SET requester_id='bot:assistant' WHERE id=?", (turn["ref"],))
        assert rooms._routine_person(c, H.task(c, turn["ref"])) == ""
    machine = runner(api)
    with api.app.state.store.read() as c:
        held = c.execute(
            "SELECT generation FROM assignments WHERE bot=?", (H.resolve_bot(c, "assistant")["id"],)
        ).fetchone()
    assign(api, machine, "assistant", held["generation"] if held else 0)
    ready(api, machine, ["assistant"])
    assert claim(api, machine) is None


def test_a_mail_turn_title_names_the_sender_when_the_subject_is_empty():
    about = E._turn_about({"message": {"source": "mail", "from": "jo@acme.example", "from_name": "", "subject": "Re:"}})
    assert about == "jo@acme.example"
    assert E._turn_about({"message": {"source": "slack", "text": "hi"}}) is None
    long = E._turn_about({"message": {"source": "mail", "from_name": "Jo", "subject": "x" * 300}})
    assert len(long) == E.ABOUT_CHARS and long.endswith("…")


def test_a_bot_that_can_run_its_own_turn_keeps_the_draft_choose(api):
    set_templates(api, STAFF)
    draft = by_id(bot_flow(api, "assistant")["steps"])["draft"]
    assert draft["as"] == "bot:assistant" and draft["choose"]["mode"] == "draft"
    assert "{{message.subject}}" in draft["prompt"] and "{{message.body}}" in draft["prompt"]


def test_an_onboarding_bot_owner_gets_sort_for_its_draft(api):
    set_templates(api, STAFF)
    onboarding(api, "cpo")  # start_turn's task would be refused: onboarding
    assert_sorted_draft(bot_flow(api, "cpo"))


def test_a_responder_bot_owner_gets_sort_for_its_draft(api):
    set_templates(api, STAFF)
    set_config(api, "product-design", "profile", "responder")  # its profile takes no task, a routine's included
    assert_sorted_draft(bot_flow(api, "product-design", "ben@acme.example"))


def test_a_bot_owner_on_a_synced_harness_gets_sort_for_its_draft(api):
    set_templates(api, STAFF)
    set_config(api, "finance", "harness", "dots")  # works on another platform: no task reaches it
    assert_sorted_draft(bot_flow(api, "finance"))


def slack_bot_flow(api, bot):
    listed(api, "general")
    with api.app.state.store.read() as c:
        flow = T.fill(c, "founder", f"bot:{bot}", "slack:#general", api.app.state.store.settings)
    F.check(flow)
    return flow


def draft_actions(flow):
    """The draft and send actions the flow's filter rules hold."""
    return [
        a["do"]
        for step in flow["steps"]
        if step["type"] == "filter"
        for rule in step.get("rules") or []
        for a in rule.get("then") or []
        if a["do"] in T.DRAFTS
    ]


def test_a_task_hand_off_carries_the_mail_message_as_placeholders(api):
    set_templates(api, STAFF)
    flow = fill(api, "founder", "person:ana", "ana@acme.example")
    legal = by_id(flow["destinations"])["legal"]["then"][0]["args"]
    assert "{{message.body}}" in legal["body"]  # the task's body names the message it hands on
    assert "{{message.subject}}" in legal["body"] and "{{message.from}}" in legal["body"]
    for dest in ("engineering", "finance", "marketing"):  # marketing's wake, or its task when ana may not tell it
        assert "{{message.body}}" in json.dumps(by_id(flow["destinations"])[dest]["then"])


def test_a_task_hand_off_carries_the_slack_text_as_placeholders(api):
    set_templates(api, STAFF)
    listed(api, "general")
    flow = fill(api, "founder", "person:ana", "slack:#general")
    body = by_id(flow["destinations"])["legal"]["then"][0]["args"]["body"]
    assert "{{message.text}}" in body and "{{message.from}}" in body and "{{message.body}}" not in body


def test_a_slack_bot_owner_that_cannot_file_a_draft_is_sorted_without_drafts(api):
    set_templates(api, STAFF)
    set_config(api, "assistant", "profile", "reviewer")  # runs its routine's turn, but takes no draft task
    flow = slack_bot_flow(api, "assistant")
    assert draft_actions(flow) == []
    assert_sorted_draft(flow)
    assert "meet" not in by_id(flow["steps"])["ask"]["questions"]  # the only question the draft route read


def test_a_slack_bot_owner_that_can_file_a_draft_keeps_it(api):
    set_templates(api, STAFF)
    flow = slack_bot_flow(api, "assistant")  # no profile: it takes every task, its drafts included
    assert "draft_reply" in draft_actions(flow)
    assert by_id(flow["steps"])["draft"]["as"] == "bot:assistant"


def test_a_reviewer_owner_keeps_its_mail_draft_since_no_task_is_filed(api):
    set_templates(api, STAFF)
    set_config(api, "assistant", "profile", "reviewer")
    flow = bot_flow(api, "assistant")
    assert "draft_reply" in draft_actions(flow)
    assert by_id(flow["steps"])["draft"]["as"] == "bot:assistant"


def test_a_group_whose_bot_lead_cannot_file_a_draft_is_sorted_without_drafts(api):
    set_templates(api, STAFF)
    # Built-in bots and helpers (the Assistant, the Finance Owner) stay outside groups: the Success Manager leads.
    set_config(api, "ops", "profile", "reviewer")
    gid = post(api, "groups", {"name": "Support Desk", "add": {"bots": ["ops"]}, "lead": "bot:ops"})["id"]
    listed(api, "general")
    flow = fill(api, "founder", f"group:{gid}", "slack:#general")
    F.check(flow)
    assert draft_actions(flow) == []
    assert_sorted_draft(flow)


def test_a_person_owner_in_slack_keeps_its_drafts(api):
    set_templates(api, STAFF)
    listed(api, "general")
    flow = fill(api, "founder", "person:ana", "slack:#general")
    assert "draft_reply" in draft_actions(flow)
    assert by_id(flow["steps"])["draft"]["as"] == "assistant:ana"


@pytest.mark.parametrize(
    "role, old, new",
    [
        ("legal_lead", "general-counsel", "legal-owner"),
        ("support_lead", "success-manager", "support-owner"),
    ],
)
def test_a_role_two_templates_hold_is_filled_from_the_first_template_first(role, old, new):
    """A role's bots come from its templates in ROLES' order: the second template's bots only after the first's."""
    assert T._candidates({new: ["new-bot"]}, role) == ["new-bot"]
    assert T._candidates({new: ["new-bot"], old: ["old-bot"]}, role) == ["old-bot", "new-bot"]
