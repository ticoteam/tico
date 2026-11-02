"""Demo threads and org rules: the Home threads with their sub-thread, the company thread, Ana's DMs (the Assistant and
pricing), the rules (three for everyone, four between teammates with a With, each teammate named @handle) and Ben's
suggestion, and the one draft a rule held for Ben (written nowhere; its review task
waits for Ben, and Ana, the owner, can review it too)."""

import re
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


def test_the_rules_say_who_follows_them_and_with_whom(built):
    _, settings = built
    with signed_in(settings) as api:
        rules = api.get("/api/v2/rules", params={"status": "active"}).json()["rules"]
    texts = {rule["text"]: rule for rule in rules}
    for item in CONTENT["rules"]:
        assert item["text"] in texts, item["text"]
        rule = texts[item["text"]]
        assert rule["if_broken"] == item.get("if_broken", "block")
        assert (rule.get("reviewer") or {}).get("id") == (item["reviewer"].split(":", 1)[1] if "reviewer" in item else None)
        if "who" not in item:
            assert rule["who"]["everyone"] is True
            continue
        who, with_ = item["who"], item["with"]
        assert rule["who"]["all_bots"] is bool(who.get("all_bots"))
        for key in ("groups", "bots", "people"):
            assert [x["id"] for x in rule["who"][key]] == who.get(key, []), (item["key"], key)
            assert [x["id"] for x in rule["with"][key]] == with_.get(key, []), (item["key"], key)


def test_peer_rules_fill_the_with_column(built):
    _, settings = built
    with signed_in(settings) as api:
        rules = api.get("/api/v2/rules", params={"kind": "internal", "status": "active"}).json()["rules"]
    peer = [r for r in rules if any(r["with"][k] for k in ("groups", "bots", "people"))]
    assert len(peer) >= 3
    by_text = {r["text"]: r for r in peer}
    ben = by_text["Don't assign @ben a task without asking first."]
    assert ben["who"]["all_bots"] and [p["id"] for p in ben["with"]["people"]] == ["ben"]


def test_every_mention_in_a_rule_names_a_real_teammate(built):
    """A rule names a teammate as @slug (a bot) or @handle (a person), the way the Rules page shows a mention chip."""
    _, settings = built
    with signed_in(settings) as api:
        bots = {b["id"] for b in api.get("/api/v2/org").json()["bots"]}
        rules = api.get("/api/v2/rules", params={"status": "active"}).json()["rules"]
    people = {"ana", "ben", "cara", "eli"}
    ours = {item["text"] for item in CONTENT["rules"]}
    named = [m.rstrip(".-") for r in rules if r["text"] in ours for m in re.findall(r"(?<![\w@])@(\w[\w.-]*)", r["text"])]
    assert {"ana", "ben", "bookkeeper", "accounts-payable", "firmware", "pricing"} <= set(named)
    assert [m for m in named if m not in bots | people] == []
    assert not any(re.search(r"\b(Ana|Ben)'s\b|go to Ben\b", text) for text in ours), "a teammate is named by @handle"


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
        hold = db.execute("SELECT id, state, reviewer, task_id, json_extract(verdict, '$.outcome') "
                          "FROM org_rule_holds").fetchall()
    assert len(hold) == 1
    hold_id, state, reviewer, task_id, outcome = hold[0]
    assert (state, reviewer, outcome) == ("held", "human:ben", "escalate")
    # Ben reviews it, and so does Ana, the owner (the demo's only sign-in): she lists the hold and reads its text.
    with signed_in(settings) as api:
        listed = api.get("/api/v2/rule-holds").json()["holds"]
        read = api.get(f"/api/v2/rule-holds/{hold_id}").json()
        task = api.get(f"/api/v2/tasks/{task_id}").json()["task"]
    assert [(h["id"], h["reviewer"], h["can_send"]) for h in listed] == [(hold_id, "human:ben", True)]
    assert read["text"] == held["say"] and read["nonce"]
    assert task["owner_id"] == "human:ben"
    assert task["title"] == "Rule review"


def test_the_engineering_home_has_the_pause_and_its_fix_thread(built):
    _, settings = built
    # The Home is shared with the other sections' posts (recruit, automations), so this checks this section's own messages.
    said = [item["say"] for item in CONTENT["messages"] if item["in"] == "eng"]
    with signed_in(settings) as api:
        messages = [m for m in timeline(api, home_id(api, "bot:product-manager")) if m["body"] in said]
    assert len(messages) == len(said)
    bodies = [m["body"] for m in messages]
    assert any(text.startswith("Rollout paused") for text in bodies)
    assert sum(1 for m in messages if m["depth"] == 1) == 4      # the fix's sub-thread
    stamps = [datetime.strptime(m["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) for m in messages]
    assert all(NOW - timedelta(days=7) <= stamp <= NOW for stamp in stamps)


def test_ana_sidebar_lists_her_department_homes_and_her_dms(built):
    _, settings = built
    with signed_in(settings) as api:
        sidebar = api.get("/api/v2/threads").json()          # rooms and DMs are paged apart: threads, dms
        listed = {t["id"] for key in ("threads", "dms") for t in sidebar.get(key, [])}
        homes = [home_id(api, spec["home"]) for spec in CONTENT["threads"].values() if "home" in spec]
        dms = [api.get("/api/v2/threads/dm", params={"with": with_, "create": "false"}).json()["id"]
               for with_ in ("bot:coo", "bot:pricing")]
    assert [h for h in homes if h not in listed] == [], "department homes missing from the sidebar"
    assert [d for d in dms if d not in listed] == [], "DMs missing from the sidebar"


def test_the_company_thread_and_ana_assistant_dm_show(built):
    _, settings = built
    first = max((m for m in CONTENT["messages"] if m["in"] == "coo"), key=lambda m: m["ago"])
    with signed_in(settings) as api:
        dm = api.get("/api/v2/threads/dm", params={"with": "bot:coo", "create": "false"})
        assert dm.status_code == 200
        assert first["say"] in [m["body"] for m in timeline(api, dm.json()["id"])]
        titles = [t.get("title") for t in api.get("/api/v2/threads").json().get("threads", [])]
    assert CONTENT["threads"]["all"]["title"] in titles


def test_ana_and_pricing_talk_through_the_december_price(built):
    _, settings = built
    said = [m["say"] for m in sorted((m for m in CONTENT["messages"] if m["in"] == "pricing"), key=lambda m: -m["ago"])]
    with signed_in(settings) as api:
        dm = api.get("/api/v2/threads/dm", params={"with": "bot:pricing", "create": "false"})
        assert dm.status_code == 200
        bodies = [m["body"] for m in timeline(api, dm.json()["id"])]
    assert bodies[-len(said):] == said


def test_the_seed_is_deterministic(built, tmp_path_factory):
    _, settings = built
    second = demo.build(tmp_path_factory.mktemp("demo-threads-rules-again"), now=NOW)
    assert _content(settings.db_path) == _content(second.db_path)


# The rest of the core seed and the other sections also write rows whose presence depends on what ran before them in the
# process (the bots' profile notices at the real clock, a Finance tax deadline rule). So this compares only what this
# section writes: its messages, its conversations on the demo clock (NOW and the days before), its rules and its holds.
DEMO_CLOCK_CUTOFF = "2026-10-01"


def _content(path):
    """What the seed writes for threads and rules. Thread ids are not compared: the threads module makes its own
    (uuid4, by design, backend/threads.py `_new_id`), so only the rows' text, times and rules are the same."""
    says = [m["say"] for m in CONTENT["messages"]]
    texts = [item["text"] for item in CONTENT["rules"]] + [CONTENT["suggestion"]["text"]]
    with sqlite3.connect(path) as db:
        return {
            "messages": db.execute(f"SELECT body, created_at, kind, sender_id, recipient_id FROM messages "
                                   f"WHERE body IN ({','.join('?' * len(says))}) ORDER BY created_at, body",
                                   says).fetchall(),
            "conversations": db.execute("SELECT subject, audience, kind, owner_id, created_at, last_message_at "
                                        "FROM conversations WHERE created_at < ? ORDER BY created_at, subject",
                                        (DEMO_CLOCK_CUTOFF,)).fetchall(),
            "rules": db.execute(f"SELECT id, text, status, who, with_scope, if_broken, reviewer, suggested_by FROM org_rules "
                                f"WHERE text IN ({','.join('?' * len(texts))}) ORDER BY id", texts).fetchall(),
            "holds": db.execute("SELECT id, author, reviewer, state, created_at FROM org_rule_holds").fetchall(),
        }
