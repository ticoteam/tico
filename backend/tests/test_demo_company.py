"""The company demo as its owner sees it: what the seed leaves behind, read through the same API the app uses. These
check behaviour (who owns what, what waits for whom, what every bot has done), not the wording of the seed."""

import re
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend import demo_content as D
from backend.app import create_app

NOW = datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc)      # a Friday evening: the week is complete
ROSTER = {slug for slug, _template, _parent in D.BOTS}
DAY = {"from": "2026-10-31", "to": "2026-11-06"}
PER_INSTALL = {"credential_keys", "granola_connections", "slack_credentials"}   # this machine's keys and the secrets they seal
# Bytes this machine's keys produce: a sealed secret (AES-GCM, a fresh nonce), and an idempotency fingerprint keyed
# with the cipher key. Each install differs there by design, so they are compared as a mark, not as bytes.
SEALED = re.compile(r"sealed:v1:[A-Za-z0-9+/=]+")
KEYED = {("idempotency", "request_hash")}


def _mark(table, column, value):
    if column in ("ciphertext", "nonce") or (table, column) in KEYED:
        return "<per-install>"
    return SEALED.sub("sealed:v1:<per-install>", value) if isinstance(value, str) else value


def stored(path):
    """Every table's rows, as the build wrote them, less the per-install keys and the bytes they make."""
    with sqlite3.connect(path) as db:
        tables = [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                 "AND name NOT LIKE 'sqlite_%'")]
        out = {}
        for name in tables:
            if name in PER_INSTALL:
                continue
            cursor = db.execute(f'SELECT * FROM "{name}"')
            columns = [column[0] for column in cursor.description]
            out[name] = [tuple(_mark(name, column, value) for column, value in zip(columns, row))
                         for row in cursor.fetchall()]
        return out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Two builds of the same date, back to back, read before any app has opened either database."""
    first = demo.build(tmp_path_factory.mktemp("company"), now=NOW)
    rows = stored(first.db_path)
    second = demo.build(tmp_path_factory.mktemp("company-again"), now=NOW)
    return first, rows, stored(second.db_path)


@pytest.fixture(scope="module")
def ana(built):
    settings, _rows, _again = built
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as api:
        api.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
        yield api


def test_the_company_goal_has_department_goals_and_one_is_off_track(ana):
    goals = ana.get("/api/v2/goals?all=true").json()["goals"]
    [company] = [goal for goal in goals if goal["owner_id"] == "company"]
    assert [goal["id"] for goal in goals if goal["parent_id"] == company["id"]], "departments hang off the company goal"
    red = [goal for goal in goals if goal["status"] == "red"]
    assert len(red) == 1 and red[0]["parent_id"] == company["id"], "exactly one goal is off track, under the company goal"


def test_every_bot_has_a_daily_update_and_the_friday_review_covers_the_team(ana):
    for slug in sorted(ROSTER):
        flags = ana.get(f"/api/v2/bots/{slug}/updates").json()
        assert flags["daily"] and flags["weekly"], slug
    friday = ana.get("/api/v2/updates?kind=weekly&limit=100").json()["updates"]
    reviewed = {update["bot"] for update in friday if update["day"] == "2026-11-06"}
    assert ROSTER <= reviewed, sorted(ROSTER - reviewed)


def test_the_pricing_decision_waits_for_ana(ana):
    waiting = [item for item in ana.get("/api/v2/needs-you").json()["items"]
               if item["requester_id"] == "bot:pricing" and item["status"] == "open"]
    assert len(waiting) == 1 and waiting[0]["owner_id"] == "human:ana"


def test_tasks_cover_every_status_and_some_have_chains(ana):
    tasks = ana.get("/api/v2/tasks?limit=200").json()["tasks"]
    assert {task["status"] for task in tasks} >= {"open", "doing", "waiting", "review", "done", "closed", "declined"}
    parents = [task for task in tasks if (task.get("children_summary") or {}).get("total")]
    assert parents, "some tasks split into child tasks"
    tree = ana.get(f"/api/v2/tasks/{parents[0]['id']}/tree")
    assert tree.status_code == 200 and tree.json()


def test_no_task_is_left_stuck_for_days(ana):
    assert ana.get("/api/v2/tasks/stuck").json()["tasks"] == []


def test_a_meeting_waiting_for_review_is_not_in_the_live_list(ana):
    live = ana.get("/api/meetings").json()
    pending = ana.get("/api/meetings?review=pending").json()
    assert live and pending
    assert not {meeting["id"] for meeting in live} & {meeting["id"] for meeting in pending}
    assert all(meeting["review_state"] == "pending" for meeting in pending)


def test_docs_market_and_links_are_there(ana):
    paths = [doc["path"] for doc in ana.get("/api/v2/docs").json()["docs"]]
    assert any(path.startswith("market/") for path in paths) and any(path.startswith("help/") for path in paths)
    entities = {entity["id"]: entity for entity in ana.get("/api/v2/market/entities").json()["entities"]}
    assert entities["company/self"]["name"] == D.COMPANY
    rivals = [edge for edge in ana.get("/api/v2/market/edges").json()["edges"]
              if edge["rel"] == "competes_with" and edge["dst"] == "company/self"]
    assert len(rivals) >= 3, "competitors point at the company"


def test_usage_covers_every_bot_and_every_routine_has_a_run(ana):
    usage = {row["bot"] for row in ana.get("/api/v2/usage", params=DAY).json()["rows"]}
    assert ROSTER <= usage, sorted(ROSTER - usage)
    routine_owners = {automation["owner_id"] for automation in ana.get("/api/v2/automations").json()["automations"]}
    assert {f"bot:{slug}" for slug in {row[0] for row in D.ROUTINES}} <= routine_owners
    assert ana.get("/api/v2/automations/runs").json()["runs"], "the routines have run"


def test_status_focus_turns_and_history_are_there_for_every_bot(ana):
    status = {row["bot"]: row for row in ana.get("/api/v2/status").json()["bots"]}
    assert all(status[slug]["focus"] for slug in ROSTER)
    for slug in sorted(ROSTER):
        assert ana.get(f"/api/v2/bots/{slug}/turns").json(), slug
        assert ana.get(f"/api/v2/bots/{slug}/history").status_code == 200, slug


def test_chats_and_slack_are_connected(ana):
    assert ana.get("/api/v2/slack/app").json()["state"] == "connected"
    channels = ana.get("/api/v2/slack/channels").json()["channels"]
    assert any(channel["name"] == D.SLACK["channel"] for channel in channels)
    conversations = ana.get("/api/v2/conversations").json()["conversations"]
    assert any(any(p.startswith("bot:") for p in conv["participants"]) for conv in conversations)


def test_the_same_date_builds_the_same_company(built):
    _first, rows, again = built
    assert again == rows
