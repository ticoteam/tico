"""Help intake and replies through the watcher, task, approval and GitHub routes."""

import importlib.machinery
import importlib.util
import json
import re
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import httpx
import pytest

from backend import outbound_actions as OA
from backend import (
    bot_settings,
    mailbox_access,
    outside_intake,
    pipeline_settings,
    relationship_runs,
    routing,
    support,
    support_triage,
    watchers,
)
from backend.entities import rules as directory_rules
from backend.entities import store as entities
from backend.store import H
from backend.tests.helpers.api import claim, get, post, put, unrelated_task_managers
from backend.tests.helpers.company import bot
from backend.tests.helpers.github import PR, SECRET, hook, pr_event
from backend.tests.helpers.watchers import hosted, report, tasks

URL = "https://hq.acme.example"
STAFF = "test-hq-key"
GATEWAY = "tgw_" + "A" * 43
COMPANY = "co_acme"
TICKET = "TK-ABCDEFGH"
ANSWER = "In Help, open the guide to connecting a second computer."
ISSUE = "https://github.com/ticoteam/tico/issues/13"


def load_watcher(name):
    script = Path(__file__).resolve().parents[2] / "templates/catalog/support-owner/software" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_") + "_wiring", str(script))
    watcher = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(watcher)
    return watcher


class HQ:
    url = URL

    def __init__(self):
        self.ticket = None
        self.next_ticket = TICKET
        self.submissions = []
        self.replies = []
        self.reply_versions = []
        self.revise = None
        self.before_reply = None
        self.lose_receipt = False

    def __call__(self, request):
        path = request.url.path
        if path == "/v1/support" and request.method == "POST":
            fields = json.loads(request.content)
            bearer = request.headers.get("authorization")
            self.submissions.append((str(request.url), bearer))
            self.ticket = {
                "ticket_id": self.next_ticket,
                "body": fields["message"],
                "install_id": fields.get("install_id"),
                "version": fields.get("version"),
                "app_version": None,
                "created": self.now(),
                "updated": self.now(),
                "status": "open",
                "messages": [],
            }
            if bearer == f"Bearer {GATEWAY}" and fields.get("install_id"):
                self.ticket.update(
                    verified_company_id=COMPANY,
                    verified_install_id=fields["install_id"],
                    company={"id": COMPANY, "name": "Acme"},
                )
            return httpx.Response(
                201, json={"ticket_id": self.next_ticket, "secret": "test-ticket-secret", "status": "open"}
            )
        assert request.headers.get("authorization") == f"Bearer {STAFF}"
        if path == "/v1/staff/tickets":
            return httpx.Response(200, json={"tickets": [self.ticket]})
        ticket = self.ticket["ticket_id"]
        assert path in (f"/v1/staff/tickets/{ticket}", f"/v1/staff/tickets/{ticket}/reply")
        if path.endswith("/reply"):
            if self.before_reply:
                self.before_reply()
            fields = json.loads(request.content)
            self.reply_versions.append(fields["expected_last_message_id"])
            newest = max((m["id"] for m in self.ticket["messages"] if m["from"] == "person"), default=0)
            if fields["expected_last_message_id"] != newest:
                return httpx.Response(409, json={"error": "stale_reply"})
            body = fields["body"]
            self.replies.append(body)
            self.message("staff", body)
            self.ticket["status"] = "answered"
            if self.lose_receipt:
                return httpx.Response(502)
            return httpx.Response(200, json={"ticket_id": ticket, "reply_id": len(self.ticket["messages"])})
        if self.revise:
            self.revise()
        return httpx.Response(200, json=self.ticket)

    def call(self, method, path):
        return self(httpx.Request(method, self.url + path, headers={"Authorization": f"Bearer {STAFF}"})).json()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def message(self, author, body):
        message = {"id": len(self.ticket["messages"]) + 1, "from": author, "body": body, "created": self.now()}
        self.ticket["messages"].append(message)
        self.ticket["updated"] = self.now()


@pytest.fixture
def flow(api, tmp_path, monkeypatch):
    hq = HQ()
    monkeypatch.setenv("TICO_HQ_URL", URL)
    monkeypatch.setenv("TICO_HQ_STAFF_KEY", STAFF)
    monkeypatch.setenv("TICO_HQ_SUPPORT_BOT", "ops")
    monkeypatch.setenv("TICO_SUPPORT", "on")
    monkeypatch.setattr(support, "TRANSPORT", httpx.MockTransport(hq))
    monkeypatch.setattr(support_triage, "kick", lambda *_: None)
    monkeypatch.setattr(OA, "ring", lambda: None)
    api.app.state.store.settings.github_webhook_secret = SECRET
    runner = hosted(api)
    token = bot(api, "ops")

    watcher = load_watcher("hq-tickets")

    def poll():
        events = []
        watcher.watch(hq, {"TICO_WATCHER_STATE": str(tmp_path / "watch")}, events.append, log=lambda _: None)
        return events

    def intake(kind="question", *, held=False, decide=True):
        post(api, "support/tickets", {"message": "How do I add another computer?"})
        events = poll()
        assert len(events) == 1
        monkeypatch.setattr(support_triage, "engine_for", lambda *_: True if decide else None)
        assert report(api, runner, *events)["tasks"] == {}
        verdict = {
            "spam": 0.95 if held else 0,
            "kind": kind,
            "kind_confidence": 1,
            "urgency": 1,
            "held": held,
            "hold_at": 0.85,
        }
        if decide:
            monkeypatch.setattr(support_triage, "ask_many", lambda *_: [(verdict, "")])
        support_triage.drain(api.app.state.store, api.app.state.auth, None)
        assert report(api, runner, *events)["tasks"] == {}
        with api.app.state.store.read() as c:
            return c.execute(
                "SELECT task_id FROM watcher_items WHERE key=?", ("hq:" + hq.ticket["ticket_id"],)
            ).fetchone()[0]

    return hq, runner, token, poll, intake, watcher


@pytest.mark.parametrize("status", [401, 403])
def test_refused_github_judge_files_unchecked_and_reports_one_health_warning(api, monkeypatch, tmp_path, status):
    watcher = load_watcher("gh-support")
    runner = hosted(api)
    issues = [
        {
            "number": number,
            "title": "Missing board",
            "body": "The board is empty.",
            "state": "open",
            "updated_at": "2026-10-01T12:05:00Z",
            "html_url": f"https://github.com/acme/app/issues/{number}",
        }
        for number in (1, 2)
    ]

    def github(_method, url, _headers, _body):
        return 200, {}, json.dumps(issues if "/issues?" in url else []).encode()

    class RefusingJudge:
        def open(self, request, *, timeout):
            raise HTTPError(request.full_url, status, "refused", {}, None)

    monkeypatch.setattr(watcher.urllib.request, "build_opener", lambda *_: RefusingJudge())
    events, lines = [], []
    env = {"GH_SUPPORT_REPOS": "acme/app", "HQ_STAFF_KEY": STAFF, "HQ_URL": URL}
    code = watcher.main(
        ["watch"],
        env,
        tmp_path,
        http=github,
        emit=events.append,
        out=lines.append,
        now=datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
    )
    warnings = [line for line in lines if "warning" in line]
    assert code == 1 and len(warnings) == 1 and lines[-1] == warnings[0]
    assert f"HTTP {status}" in warnings[0] and "unchecked" in warnings[0] and STAFF not in warnings[0]
    report(api, runner, *events, name="gh-support", code=code, output="\n".join(lines))
    filed = tasks(api)
    assert len(filed) == 2 and all(row["title"].startswith("GitHub issue:") for row in filed)
    with api.app.state.store.read() as c:
        (problem,) = watchers.problems(c, {runner["runner_id"]})
    assert problem[:2] == ("ops", "gh-support") and problem[2].endswith(warnings[0])


def approve(api, action):
    detail = get(api, f"outbound-actions/{action['id']}")
    assert detail["shown_hash"]
    post(api, f"outbound-actions/{action['id']}/approve", {"shown_hash": detail["shown_hash"]})


def engineering_task(api, parent, token):
    with api.app.state.store.transaction() as c:
        pipeline = deepcopy(routing.SUPPORT_RECORD)
        pipeline.update(id="engineering-test", name="Engineering test", preset="engineering", manager_id="human:ana")
        for stage in [*pipeline["stages"], pipeline["done"], pipeline["closed"]]:
            stage["id"] = stage["id"].replace("support-", "engineering-test-")
        pipeline_settings.save(c, H.TICO_ACTOR, pipeline)
    return post(
        api,
        "tasks",
        {
            "parent": parent,
            "owner": "ben",
            "pipeline": "engineering-test",
            "title": "Fix the missing board",
            "message": "Reproduction and expected result.",
            "links": [ISSUE],
        },
        token=token,
    )


def queue_answer(api, task, token, last_message_id=0):
    return post(
        api,
        "outbound-actions",
        {
            "channel": "help-ticket",
            "operation": "answer",
            "destination": TICKET,
            "occurrence_key": "docs-answer",
            "task_id": task,
            "payload": {"body": ANSWER, "last_message_id": last_message_id},
        },
        token=token,
    )["action"]


def test_help_intake_is_deduplicated_and_does_not_treat_install_id_as_company_proof(api, flow):
    hq, _, _, _, intake, _ = flow
    task_id = intake()
    task = get(api, f"tasks/{task_id}")["task"]
    assert task["pipeline_id"] == "support"
    assert hq.ticket["install_id"]
    assert not task.get("entity_id")
    assert not hq.replies
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM conversations WHERE audience='entity'").fetchone()
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id=?", (H.RELATIONSHIP_WORKER,)).fetchone()


def test_verified_help_tickets_share_the_company_and_appear_in_directory_history(api, flow, monkeypatch):
    hq, _, _, _, intake, _ = flow
    monkeypatch.delenv("TICO_HQ_STAFF_KEY")
    monkeypatch.setenv("TICO_HQ_STAFF_READ_KEY", STAFF)
    monkeypatch.setenv("TICO_GATEWAY_KEY", GATEWAY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL + "/v1/gateway")
    first = intake(decide=False)
    hq.next_ticket = "TK-BCDEFGHJ"
    second = intake(decide=False)
    assert hq.submissions == [(URL + "/v1/support", f"Bearer {GATEWAY}")] * 2
    with api.app.state.store.read() as c:
        first, second = H.task(c, first), H.task(c, second)
        entity = first["entity_id"]
        assert entity and entity == second["entity_id"]
        assert entities.verified_external_ref(c, H.TICO_ACTOR, "tico-hq", "", "company", COMPANY) == entity
        assert {r[0] for r in c.execute("SELECT id FROM conversations WHERE entity_id=?", (entity,))} == {
            first["conversation_id"],
            second["conversation_id"],
        }
    history = get(api, f"entities/activity?ref={entity}")
    assert {r["id"] for r in history["rows"]} == {first["conversation_id"], second["conversation_id"]}
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c, "ops")
        H.task_update(c, H.TICO_ACTOR, first["id"], private=True)
    history = get(api, f"entities/activity?ref={entity}", token="cara-test")
    assert [r["id"] for r in history["rows"]] == [second["conversation_id"]]


def test_support_sends_no_gateway_key_without_install_id_or_to_a_different_origin(api, flow, monkeypatch):
    hq, *_ = flow
    monkeypatch.setenv("TICO_GATEWAY_KEY", GATEWAY)
    monkeypatch.setenv("TICO_GATEWAY_URL", URL + "/v1/gateway")
    post(api, "support/tickets", {"message": "I need help", "include_ids": False})
    hq.next_ticket = "TK-BCDEFGHJ"
    monkeypatch.setenv("TICO_GATEWAY_URL", "https://other.acme.example/v1/gateway")
    post(api, "support/tickets", {"message": "I still need help"})
    assert hq.submissions == [(URL + "/v1/support", None)] * 2


def test_docs_answer_settles_only_after_hq_receipt_and_followup_reopens(api, flow):
    hq, runner, token, poll, intake, _ = flow
    task = intake()
    action = queue_answer(api, task, token)
    assert action["state"] == "waiting_approval" and hq.replies == []
    assert get(api, f"tasks/{task}")["task"]["status"] == "open"
    approve(api, action)
    assert OA.step(api.app.state.store)
    assert hq.replies == [ANSWER]
    detail = get(api, f"tasks/{task}")
    assert detail["task"]["status"] == "done"
    assert any(ANSWER in msg["body"] for msg in detail["comments"])
    hq.message("person", "I found the guide but still need help.")
    (followup,) = poll()
    assert report(api, runner, followup)["tasks"] == {"hq:" + TICKET: task}
    assert get(api, f"tasks/{task}")["task"]["status"] == "open"


def test_hq_rejects_a_reply_when_the_customer_writes_between_lookup_and_post(api, flow):
    hq, _, token, _, intake, _ = flow
    task = intake()
    action = queue_answer(api, task, token)
    approve(api, action)
    hq.before_reply = lambda: hq.message("person", "I have a different question now.")
    assert OA.step(api.app.state.store)
    blocked = get(api, f"outbound-actions/{action['id']}")["action"]
    assert blocked["state"] == "blocked" and "fresh draft" in blocked["last_error"]
    assert not OA.step(api.app.state.store)
    assert hq.reply_versions == [0] and hq.replies == []
    assert get(api, f"tasks/{task}")["task"]["status"] == "open"
    assert queue_answer(api, task, token)["id"] == action["id"]

    hq.before_reply = None
    fresh = queue_answer(api, task, token, last_message_id=1)
    assert fresh["id"] != action["id"] and fresh["state"] == "waiting_approval"
    approve(api, fresh)
    assert OA.step(api.app.state.store)
    assert hq.reply_versions == [0, 1] and hq.replies == [ANSWER]


def test_reply_command_uses_the_drafts_customer_message_id(api, flow, monkeypatch, tmp_path):
    hq, runner, token, poll, intake, watcher = flow
    task = intake()
    hq.message("person", "The problem changed in the meantime.")
    report(api, runner, *poll())
    reply = tmp_path / "reply.md"
    reply.write_text(ANSWER)
    monkeypatch.setattr(watcher, "tico_post", lambda env, path, body: post(api, path, body, token=token))
    args = ["reply", TICKET, str(reply)]
    env = {"HUB_TASK_ID": task}
    assert watcher.main(args, env=env) == 0
    (old,) = get(api, "outbound-actions?channel=help-ticket")["items"]
    approve(api, old)
    assert OA.step(api.app.state.store)
    assert get(api, f"outbound-actions/{old['id']}")["action"]["state"] == "blocked"
    assert hq.reply_versions == []

    assert watcher.main(args + ["--last-message-id", "1"], env=env) == 0
    fresh, old = get(api, "outbound-actions?channel=help-ticket")["items"]
    assert fresh["state"] == "waiting_approval" and old["state"] == "blocked"
    approve(api, fresh)
    assert OA.step(api.app.state.store)
    assert hq.reply_versions == [1] and hq.replies == [ANSWER]


@pytest.mark.parametrize("paused_at_merge", [True, False])
def test_linked_engineering_subtask_can_queue_after_failed_merge_enqueue_and_sends_once(api, flow, paused_at_merge):
    hq, runner, token, poll, intake, _ = flow
    parent = intake("setup")
    hq.message("person", "Here's one more detail.")
    report(api, runner, *poll())
    child = engineering_task(api, parent, token)
    child_id = child["id"]
    detail = get(api, f"tasks/{child_id}")
    links = detail["task"]["links"]
    assert detail["parent"]["id"] == parent
    assert any(link["kind"] == "issue" and link["url"] == ISSUE for link in links)
    assert not get(api, "outbound-actions?channel=help-ticket")["items"]
    assert get(api, f"tasks/{parent}")["task"]["status"] == "open"
    post(api, f"tasks/{child_id}/links", {"url": PR})
    if paused_at_merge:
        control(api, "ops", "pause")
    event = pr_event("closed", merged=True, state="closed", number=412)
    hook(api, "pull_request", event)
    hook(api, "pull_request", event)
    at_merge = get(api, "outbound-actions?channel=help-ticket")["items"]
    if paused_at_merge:
        assert at_merge == []
        assert not any("Engineering reply ready" in msg["body"] for msg in get(api, f"tasks/{parent}")["comments"])
        control(api, "ops", "resume")
    else:
        assert len(at_merge) == 1 and at_merge[0]["state"] == "waiting_approval"
    child = get(api, f"tasks/{child_id}")["task"]
    post(api, f"tasks/{child_id}/done", {"version": child["version"]})
    (reply,) = get(api, "outbound-actions?channel=help-ticket")["items"]
    assert reply["state"] == "waiting_approval" and reply["task_id"] == parent and hq.replies == []
    approve(api, reply)
    assert OA.step(api.app.state.store)
    assert hq.reply_versions == [1]
    assert len(hq.replies) == 1 and re.search(r"Engineering has finished", hq.replies[0])
    assert get(api, f"tasks/{parent}")["task"]["status"] == "open"


def test_help_reply_stops_when_policy_changes_during_provider_lookup(api, flow):
    hq, _, token, _, intake, _ = flow
    task = intake()
    action = queue_answer(api, task, token)
    approve(api, action)
    hq.revise = lambda: put(api, "bots/ops/email-sending-policy", {"mode": "never"})
    assert OA.step(api.app.state.store)
    assert hq.replies == []
    assert get(api, f"outbound-actions/{action['id']}")["action"]["state"] == "blocked"


def control(api, bot_id, action):
    path = f"bots/{bot_id}"
    revision = get(api, f"{path}/pause-state")["revision"]
    post(api, f"{path}/control", {"action": action, "expected_revision": revision})


def test_help_commands_share_one_action_even_after_hq_loses_the_receipt(api, flow, monkeypatch, tmp_path):
    hq, _, token, _, intake, watcher = flow
    task = intake()
    signature = "hm-a1b2c3d4e5f6"
    hq.ticket["signature"] = signature
    reply = tmp_path / "reply.md"
    reply.write_bytes(f" {ANSWER}\r\n".encode())
    monkeypatch.setattr(watcher, "tico_post", lambda env, path, body: post(api, path, body, token=token))
    monkeypatch.setattr(watcher, "settings", lambda env: (hq, ""))
    env = {"HUB_TASK_ID": task}
    first = queue_answer(api, task, token)
    bulk = ["close-signature", signature, str(reply), "--task", f"{TICKET}={task[:12]}"]
    assert watcher.main(bulk, env=env) == 0
    (action,) = get(api, "outbound-actions?channel=help-ticket")["items"]
    assert action["id"] == first["id"] and action["state"] == "waiting_approval"
    assert hq.replies == []
    approve(api, action)
    hq.lose_receipt = True
    assert OA.step(api.app.state.store)
    assert watcher.main(["reply", TICKET, str(reply)], env=env) == 1
    (retry,) = get(api, "outbound-actions?channel=help-ticket")["items"]
    assert retry["id"] == first["id"] and retry["state"] == "unknown"
    assert not OA.step(api.app.state.store)
    assert hq.replies == [ANSWER]


@pytest.mark.parametrize("hq_held", [False, True])
def test_handed_back_ticket_can_queue_but_hq_retains_its_hold(api, flow, hq_held):
    hq, _, token, _, intake, _ = flow
    task = intake(held=True)
    with api.app.state.store.transaction() as c:
        assert H.task(c, task)["owner_id"] == "human:ana"
        H.task_update(c, "human:ana", task, owner="bot:ops")
    action = queue_answer(api, task, token)
    assert action["state"] == "waiting_approval"
    hq.ticket["held"] = hq_held
    approve(api, action)
    assert OA.step(api.app.state.store)
    assert hq.replies == ([] if hq_held else [ANSWER])


@pytest.mark.parametrize("newer", [False, True])
def test_human_confirmation_records_the_answer_and_checks_newer_messages(api, flow, newer):
    hq, runner, token, poll, intake, _ = flow
    task = intake()
    action = queue_answer(api, task, token)
    approve(api, action)
    hq.lose_receipt = True
    assert OA.step(api.app.state.store)
    assert hq.replies == [ANSWER]
    assert get(api, f"outbound-actions/{action['id']}")["action"]["state"] == "unknown"
    if newer:
        hq.message("person", "This still does not work.")
        (followup,) = poll()
        report(api, runner, followup)
    post(api, f"outbound-actions/{action['id']}/confirm-sent", {})
    detail = get(api, f"tasks/{task}")
    assert detail["task"]["status"] == ("open" if newer else "done")
    assert any(ANSWER in msg["body"] for msg in detail["comments"])


def test_sender_approval_floor_still_checks_org_rules(api, monkeypatch):
    monkeypatch.setitem(directory_rules.CHANNEL_OF, "help-ticket", "sms")
    adapter = replace(OA.ADAPTERS["help-ticket"], policy_floor=lambda c, actor: "ask_first")
    monkeypatch.setitem(OA.ADAPTERS, "help-ticket", adapter)
    put(api, "outbound-actions/policy", {"settings": {"help-ticket:external": "on_their_own"}})
    with api.app.state.store.transaction() as c:
        args = {"actor": "bot:ops", "channel": "help-ticket", "audience": "external", "destination": TICKET}
        assert OA.decide(c, **args)["choice"] == "ask_first"
        now = H.now()
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,with_scope,if_broken,status,suggested_by,created_at,"
            "updated_at,toward_scope) VALUES('sms-block','external','No SMS to customers',"
            "'{\"everyone\":true}','{}','block','active','human:ana',?,?,?)",
            (now, now, json.dumps(["channel:sms"])),
        )
        assert OA.decide(c, **args)["choice"] == "never"


@pytest.fixture
def outside_arrival(api):
    store, authority = api.app.state.store, api.app.state.auth
    sender = "customer@acme.example"
    with store.transaction() as c:
        owner = authority.owner_identity(c)
        box = mailbox_access.ensure_mailbox(c, owner.email, person_id="ana")["id"]
        entity = entities.upsert(c, owner.actor, "person", match={"email": sender})["id"]
        relationship_runs.ensure_worker(c, "ana")
        config = bot_settings.read(c, relationship_runs.BOT)
        bot_settings.write(c, relationship_runs.BOT, {**config, "runtime": "claude"})

    def arrive(delivery="received", *, verified=True):
        with store.transaction() as c:
            return outside_intake.save_message(
                c,
                channel="gmail",
                connection_id=box,
                owner_id="ana",
                source_thread_id="thread",
                source_message_id=delivery,
                sender=sender,
                verified_id=sender if verified else "",
                title="A request",
                body="Please help.",
            )

    return store, entity, outside_intake.conversation_id("gmail", box, "thread"), arrive


def test_verified_intake_queues_one_wake_for_a_paused_worker_until_resume(api, outside_arrival):
    store, entity, source, arrive = outside_arrival
    worker = relationship_runs.BOT
    runner = hosted(api, worker)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "mac",
            "readiness": {
                "schema_version": 1,
                "relationship_lanes": True,
                "bots": {
                    worker: {
                        "ready": True,
                        "repository_present": False,
                        "configuration_valid": True,
                        "runtime": "claude",
                        "harness": "claude",
                        "sign_in": "ready",
                    },
                },
                "runtimes": {"claude": {"installed": True, "authenticated": "ready"}},
            },
            "capabilities": ["task_lanes_v2"],
        },
        token=runner["token"],
    )
    control(api, worker, "pause")
    first = arrive()
    assert store.automations.drain() == 1
    assert arrive() == first
    assert store.automations.drain() == 0
    assert claim(api, runner, worker) is None
    with store.read() as c:
        (wake,) = c.execute(
            "SELECT m.id,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND j.state='queued'",
            (worker,),
        ).fetchall()
        scope = json.loads(wake["refs"])[relationship_runs.REF]
        assert scope == {"entity": entity, "source": source, "sponsor": "human:ana"}
    control(api, worker, "resume")
    assert claim(api, runner, worker)["message"]["id"] == wake["id"]


def test_intake_verified_after_merge_goes_to_inbox_triage_if_the_thread_is_unmerged(api, outside_arrival):
    store, original, source, arrive = outside_arrival
    arrive()
    assert store.automations.drain() == 1
    with store.transaction() as c:
        kept = entities.upsert(c, "human:ana", "person", name="Merged contact", match={})["id"]
        entities.merge(c, "human:ana", kept, original)
        assert c.execute("SELECT entity_id FROM conversations WHERE id=?", (source,)).fetchone()[0] == kept
    hosted(api, relationship_runs.BOT)
    arrive("after-merge")
    with store.transaction() as c:
        assert c.execute("SELECT id FROM messages WHERE source_message_id='after-merge'").fetchone()
        entities.unmerge(c, "human:ana", original)
    assert store.automations.drain() == 1
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id=?", (relationship_runs.BOT,)).fetchone()
        (task,) = c.execute("SELECT id FROM tasks WHERE owner_id='human:ana' AND private=1").fetchall()
        assert "#all/after-merge" in H.task_words(c, task["id"])


def test_unverified_intake_hands_off_and_releases_claim_without_app(outside_arrival, monkeypatch):
    store, _, _, arrive = outside_arrival
    run_id = arrive(verified=False)
    monkeypatch.setattr(store.automations, "_app", lambda: None)
    assert store.automations.drain() == 1
    with store.read() as c:
        row = c.execute("SELECT status,claim_token FROM automation_runs WHERE id=?", (run_id,)).fetchone()
        assert tuple(row) == ("ok", None)


def test_a_canceled_help_draft_can_be_queued_again(api, flow):
    _, _, token, _, intake, _ = flow
    task = intake()
    first = queue_answer(api, task, token)
    post(api, f"outbound-actions/{first['id']}/cancel", {})
    again = queue_answer(api, task, token)
    assert again["id"] != first["id"] and again["state"] == "waiting_approval"
    assert queue_answer(api, task, token)["id"] == again["id"]  # the live one still dedupes
