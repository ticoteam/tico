"""Help intake and replies through the watcher, task, approval and GitHub routes."""

import importlib.machinery
import importlib.util
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from backend import outbound_actions as OA
from backend import (
    mailbox_access,
    outside_intake,
    pipeline_settings,
    relationship_runs,
    routing,
    support,
    support_triage,
)
from backend.entities import store as entities
from backend.store import H
from backend.tests.helpers.api import get, post, put
from backend.tests.helpers.company import bot
from backend.tests.helpers.github import PR, SECRET, hook, pr_event
from backend.tests.helpers.watchers import hosted, report

URL = "https://hq.acme.example"
STAFF = "test-hq-key"
TICKET = "TK-ABCDEFGH"
ANSWER = "In Help, open the guide to connecting a second computer."
ISSUE = "https://github.com/ticoteam/tico/issues/13"


class HQ:
    url = URL

    def __init__(self):
        self.ticket = None
        self.replies = []
        self.revise = None

    def __call__(self, request):
        path = request.url.path
        if path == "/v1/support" and request.method == "POST":
            fields = json.loads(request.content)
            self.ticket = {
                "ticket_id": TICKET,
                "body": fields["message"],
                "install_id": fields.get("install_id"),
                "version": fields.get("version"),
                "app_version": None,
                "created": self.now(),
                "updated": self.now(),
                "status": "open",
                "messages": [],
            }
            return httpx.Response(201, json={"ticket_id": TICKET, "secret": "test-ticket-secret", "status": "open"})
        assert request.headers.get("authorization") == f"Bearer {STAFF}"
        if path == "/v1/staff/tickets":
            return httpx.Response(200, json={"tickets": [self.ticket]})
        assert path == f"/v1/staff/tickets/{TICKET}" or path == f"/v1/staff/tickets/{TICKET}/reply"
        if path.endswith("/reply"):
            body = json.loads(request.content)["body"]
            self.replies.append(body)
            self.message("staff", body)
            self.ticket["status"] = "answered"
            return httpx.Response(200, json={"ticket_id": TICKET, "reply_id": len(self.ticket["messages"])})
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

    script = Path(__file__).resolve().parents[2] / "templates/catalog/support-owner/software/hq-tickets"
    loader = importlib.machinery.SourceFileLoader("hq_watcher_wiring", str(script))
    watcher = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(watcher)

    def poll():
        events = []
        watcher.watch(hq, {"TICO_WATCHER_STATE": str(tmp_path / "watch")}, events.append, log=lambda _: None)
        return events

    def intake(kind="question"):
        post(api, "support/tickets", {"message": "How do I add another computer?"})
        events = poll()
        assert len(events) == 1
        monkeypatch.setattr(support_triage, "engine_for", lambda *_: True)
        assert report(api, runner, *events)["tasks"] == {}
        verdict = {"spam": 0, "kind": kind, "kind_confidence": 1, "urgency": 1, "held": False, "hold_at": 0.85}
        monkeypatch.setattr(support_triage, "ask_many", lambda *_: [(verdict, "")])
        support_triage.drain(api.app.state.store, api.app.state.auth, None)
        assert report(api, runner, *events)["tasks"] == {}
        with api.app.state.store.read() as c:
            return c.execute("SELECT task_id FROM watcher_items WHERE key=?", ("hq:" + TICKET,)).fetchone()[0]

    return hq, runner, token, poll, intake


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


def queue_answer(api, task, token):
    return post(
        api,
        "outbound-actions",
        {
            "channel": "help-ticket",
            "operation": "answer",
            "destination": TICKET,
            "occurrence_key": "docs-answer",
            "task_id": task,
            "payload": {"body": ANSWER, "last_message_id": 0},
        },
        token=token,
    )["action"]


def test_help_intake_is_deduplicated_and_does_not_treat_install_id_as_company_proof(api, flow):
    hq, _, _, _, intake = flow
    task_id = intake()
    task = get(api, f"tasks/{task_id}")["task"]
    assert task["pipeline_id"] == "support"
    assert hq.ticket["install_id"]
    assert not task.get("entity_id")
    assert not hq.replies
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM conversations WHERE audience='entity'").fetchone()
        assert not c.execute("SELECT 1 FROM jobs WHERE bot_id=?", (H.RELATIONSHIP_WORKER,)).fetchone()


def test_docs_answer_settles_only_after_hq_receipt_and_followup_reopens(api, flow):
    hq, runner, token, poll, intake = flow
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


@pytest.mark.parametrize("merged", [True, False])
def test_linked_engineering_bug_queues_only_one_reply_after_merge_or_done(api, flow, merged):
    hq, _, token, _, intake = flow
    parent = intake("bug")
    child = engineering_task(api, parent, token)
    child_id = child["id"]
    detail = get(api, f"tasks/{child_id}")
    links = detail["task"]["links"]
    assert detail["parent"]["id"] == parent
    assert any(link["kind"] == "issue" and link["url"] == ISSUE for link in links)
    assert not get(api, "outbound-actions?channel=help-ticket")["items"]
    if merged:
        post(api, f"tasks/{child_id}/links", {"url": PR})
        event = pr_event("closed", merged=True, state="closed", number=412)
        hook(api, "pull_request", event)
        hook(api, "pull_request", event)
        (at_merge,) = get(api, "outbound-actions?channel=help-ticket")["items"]
        assert at_merge["state"] == "waiting_approval"
    child = get(api, f"tasks/{child_id}")["task"]
    post(api, f"tasks/{child_id}/done", {"version": child["version"]})
    (reply,) = get(api, "outbound-actions?channel=help-ticket")["items"]
    assert reply["state"] == "waiting_approval" and reply["task_id"] == parent and hq.replies == []
    approve(api, reply)
    assert OA.step(api.app.state.store)
    assert len(hq.replies) == 1 and re.search(r"Engineering has finished", hq.replies[0])


def test_help_reply_stops_when_policy_changes_during_provider_lookup(api, flow):
    hq, _, token, _, intake = flow
    task = intake()
    action = queue_answer(api, task, token)
    approve(api, action)
    hq.revise = lambda: put(api, "bots/ops/email-sending-policy", {"mode": "never"})
    assert OA.step(api.app.state.store)
    assert hq.replies == []
    assert get(api, f"outbound-actions/{action['id']}")["action"]["state"] == "blocked"


def test_verified_intake_wakes_the_enrolled_relationship_worker_once(api):
    store, authority = api.app.state.store, api.app.state.auth
    sender = "customer@acme.example"
    with store.transaction() as c:
        owner = authority.owner_identity(c)
        box = mailbox_access.ensure_mailbox(c, owner.email, person_id="ana")["id"]
        entity = entities.upsert(c, owner.actor, "person", match={"email": sender})["id"]
        relationship_runs.ensure_worker(c, "ana")
    hosted(api, relationship_runs.BOT)

    def arrive():
        with store.transaction() as c:
            return outside_intake.save_message(
                c,
                channel="gmail",
                connection_id=box,
                owner_id="ana",
                source_thread_id="thread",
                source_message_id="received",
                sender=sender,
                verified_id=sender,
                title="A request",
                body="Please help.",
            )

    first = arrive()
    assert store.automations.drain() == 1
    assert arrive() == first
    assert store.automations.drain() == 0
    source = outside_intake.conversation_id("gmail", box, "thread")
    with store.read() as c:
        (wake,) = c.execute(
            "SELECT m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=?",
            (relationship_runs.BOT,),
        ).fetchall()
        scope = json.loads(wake["refs"])[relationship_runs.REF]
        assert scope == {"entity": entity, "source": source, "sponsor": "human:ana"}
