"""Demo threads and org rules: the Home threads with their sub-thread, the company thread, Ana's DM, the three rules and
Ben's suggestion, and the one draft a rule held for Ben (written nowhere; its review task waits for Ben)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.demo_sections.threads_rules import CONTENT

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    directory = tmp_path_factory.mktemp("demo-threads-rules")
    return directory, demo.build(directory, now=NOW)


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def timeline(api, thread_id):
    return api.get(f"/api/v2/threads/{thread_id}/timeline").json()["messages"]


def home_id(api, actor):
    return api.get(f"/api/v2/threads/home/{actor}").json()["id"]


def test_the_three_rules_apply_to_everyone(built):
    _, settings = built
    with signed_in(settings) as api:
        rules = api.get("/api/v2/rules", params={"status": "active"}).json()["rules"]
    texts = {rule["text"]: rule for rule in rules}
    for item in CONTENT["rules"]:
        assert item["text"] in texts, item["text"]
        assert texts[item["text"]]["who"]["everyone"] is True
        assert texts[item["text"]]["if_broken"] == item.get("if_broken", "block")


def test_bens_rule_suggestion_waits_as_a_suggestion(built):
    _, settings = built
    suggestion = CONTENT["suggestion"]
    with signed_in(settings) as api:
        suggested = api.get("/api/v2/rules", params={"status": "suggested"}).json()["rules"]
        active = api.get("/api/v2/rules", params={"status": "active"}).json()["rules"]
    assert [r["text"] for r in suggested] == [suggestion["text"]]
    assert suggested[0]["suggested_by"] == "human:ben"
    assert suggested[0]["who"]["bots"][0]["id"] == suggestion["bot"]
    assert suggestion["text"] not in [r["text"] for r in active]


def test_the_refund_draft_is_held_for_ben_and_written_nowhere(built):
    directory, settings = built
    with signed_in(settings) as api:
        care = timeline(api, home_id(api, "bot:support-lead"))
    held = next(m for m in CONTENT["messages"] if m.get("held"))
    assert held["say"] not in [m["body"] for m in care]
    with sqlite3.connect(settings.db_path) as db:
        hold = db.execute("SELECT state, reviewer, task_id, json_extract(verdict, '$.outcome') "
                          "FROM org_rule_holds").fetchall()
    assert len(hold) == 1
    state, reviewer, task_id, outcome = hold[0]
    assert (state, reviewer, outcome) == ("held", "human:ben", "escalate")
    # Ben reviews it, so Ana (the demo's only sign-in) sees the review task that names the hold.
    with signed_in(settings) as api:
        task = api.get(f"/api/v2/tasks/{task_id}").json()["task"]
    assert task["owner_id"] == "human:ben"
    assert task["title"] == "Rule review"


def test_the_engineering_home_has_the_pause_and_its_fix_thread(built):
    _, settings = built
    with signed_in(settings) as api:
        messages = timeline(api, home_id(api, "bot:product-manager"))
    bodies = [m["body"] for m in messages]
    assert any(text.startswith("Rollout paused") for text in bodies)
    assert sum(1 for m in messages if m["depth"] == 1) == 4      # the fix's sub-thread
    stamps = [datetime.strptime(m["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) for m in messages]
    assert all(NOW - timedelta(days=7) <= stamp <= NOW for stamp in stamps)


def test_the_company_thread_and_ana_assistant_dm_show(built):
    _, settings = built
    first = max((m for m in CONTENT["messages"] if m["in"] == "coo"), key=lambda m: m["ago"])
    with signed_in(settings) as api:
        dm = api.get("/api/v2/threads/dm", params={"with": "bot:coo", "create": "false"})
        assert dm.status_code == 200
        assert first["say"] in [m["body"] for m in timeline(api, dm.json()["id"])]
        titles = [t.get("title") for t in api.get("/api/v2/threads").json().get("threads", [])]
    assert CONTENT["threads"]["all"]["title"] in titles


def test_the_seed_is_deterministic(built, tmp_path_factory):
    _, settings = built
    second = demo.build(tmp_path_factory.mktemp("demo-threads-rules-again"), now=NOW)
    assert _content(settings.db_path) == _content(second.db_path)


def _content(path):
    """What the seed writes for threads and rules. Thread ids are not compared: the threads module makes its own
    (uuid4, by design, backend/threads.py `_new_id`), so only the rows' text, times and rules are the same each build."""
    with sqlite3.connect(path) as db:
        return {
            "messages": db.execute("SELECT body, created_at, kind, sender_id, recipient_id FROM messages "
                                   "ORDER BY created_at, body").fetchall(),
            "conversations": db.execute("SELECT subject, audience, kind, owner_id, created_at, last_message_at "
                                        "FROM conversations ORDER BY created_at, subject").fetchall(),
            "rules": db.execute("SELECT id, text, status, who, if_broken, reviewer, suggested_by FROM org_rules "
                                "ORDER BY id").fetchall(),
            "holds": db.execute("SELECT id, author, reviewer, state, created_at FROM org_rule_holds").fetchall(),
        }
