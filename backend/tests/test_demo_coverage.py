"""Every page in the app's navigation has demo data. A page added to the nav without a demo section,
or a demo section that stops filling its page, fails here."""

from datetime import timedelta

import pytest
import time_machine

from backend import demo
from backend.tests.helpers.demo import NOW, signed_in

WEEK_AGO = NOW - timedelta(days=7)


def page(path, key=None, **query):
    return path, key, query


# Nav page -> (list endpoint, key of the list in the body or None when the body is the list, query).
PAGES = {
    "overview": page("places/person%3Ame", "pins"),
    "updates": page("updates", "updates"),
    "tasks": page("tasks", "tasks"),
    "docs": page("docs", "docs"),
    "library": page("docs", "docs"),
    "files": page("files", "files"),
    # Library › Skills (ui/app/skills.ts): the directory, its usage and proposals (backend/demo_sections/skills.py).
    "skills": page("skills", "skills"),
    # Library › Contacts (ui/app/directory.ts): the people the demo's company works with (backend/demo_sections/directory.py).
    "contacts": page("entities/find/person", "rows"),
    "tools": page("tools", "integrations"),
    # The Tools page's one list (backend/api_tools.py); the old Connections row maps here until the nav drops it.
    "connections": page("tool-index", "rows"),
    "meetings": page("meetings", "meetings"),
    "meetings-live": page("live-meetings", "meetings"),
    "threads": page("threads", "threads"),
    # The sidebar's Inbox header opens the Threads page on its Inbox view: the same threads.
    "inbox": page("threads", "threads"),
    "strategy": page("strategy", "bets"),
    "growth": page("growth/numbers", "months", months=24),
    # Not a nav row: the Growth page's Retention card, computed from the demo's customers (backend/demo_sections/growth.py).
    "growth-retention": page("growth/computed", "cohorts", by="plan"),
    "finance": page("finance/transactions", "rows"),
    "rules": page("rules", "rules"),
    # The Rules section's tabs, one row each under Rules (ui/app/org-rules.ts reads).
    "rules-internal": page("rules", "rules", kind="internal"),
    "rules-external": page("rules", "rules", kind="external"),
    "rules-due": page("rules/due", "due", next="3660d"),
    "rules-suggested": page("rules", "rules", status="suggested"),
    "automations": page("automations", "automations", limit=500),
    "learnings": page("learnings", "nights"),
    "usage": page("usage", "rows", **{"from": str(WEEK_AGO.date()), "to": str(NOW.date())}),
    "replay": page("replay", "items", **{"from": WEEK_AGO.isoformat(), "to": NOW.isoformat()}),
    "changelog": page("changelog", "entries"),
    "settings": page("bots"),
    "settings-inboxes-letters-out": page("letters", "items"),
    # History reuses the runs, task changes and decisions the existing demo sections record.
    "settings-history": page("history", "items"),
    "settings-email-sending": page("email/sends", "sends", mailbox="ana@acme.example", pending=True),
    "settings-alerts": page("alerts/me"),
    # Not a nav row: its seats show in the Team chart and open from there.
    "recruit": page("seats", "seats"),
}

# Nav pages that the demo does not fill, with the reason.
NO_DEMO_DATA = {
    "welcome": "Finish setup shows only until setup is done, and the demo starts set up.",
    "listening": "The demo makes no listening: its sources would have nothing to read.",
}


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """Builds and API reads see the same day, including clocks outside hubdb.now."""
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    with signed_in(demo.build(tmp_path_factory.mktemp("demo"), now=NOW)) as client:
        yield client


def _page_has_demo_data(api, name):
    path, key, query = PAGES[name]
    response = api.get("/api/v2/" + path, params=query)
    assert response.status_code == 200, f"{name}: GET /api/v2/{path} returned {response.status_code}"
    body = response.json()
    items = body if key is None else body.get(key)
    assert items, f"{name}: GET /api/v2/{path} has no {key or 'items'} in the demo"


def _full_page_audience_editor_has_a_restricted_example(api):
    pages = api.get('/api/v2/pages?bot=growth-manager').json()['pages']
    assert pages
    shown = api.get('/api/v2/pages/' + pages[0]['file_id']).json()
    assert shown['can_manage'] and shown['page']['audience']['people'] == ['ana']


def _a_demo_task_and_meeting_link_to_each_other(api):
    meetings = api.get('/api/v2/meetings').json()['meetings']
    linked = []
    for meeting in meetings:
        response = api.get(f"/api/v2/meetings/{meeting['id']}/tasks")
        assert response.status_code == 200, response.text
        linked.extend((meeting['id'], t['task_id']) for t in response.json()['tasks'])
    assert linked
    meeting, task = linked[0]
    response = api.get(f'/api/v2/tasks/{task}/meetings')
    assert response.status_code == 200, response.text
    assert meeting in {row['meeting_id'] for row in response.json()['meetings']}


def _meeting_follow_up_is_an_ask_in_needs_you(api):
    meetings = api.get('/api/v2/meetings').json()['meetings']
    linked = {
        row['task_id']
        for meeting in meetings
        for row in api.get(f"/api/v2/meetings/{meeting['id']}/tasks").json()['tasks']
    }
    needs = api.get('/api/v2/needs-you?limit=200').json()['items']
    # The follow-up is an OK the meeting's task asks of Ana, on that task.
    assert any(item['kind'] == 'ask' and item['task_id'] in linked for item in needs)


def _demo_task_has_an_editor_for_the_live_task_page(api):
    meetings = api.get('/api/v2/meetings').json()['meetings']
    task = next(
        row['task_id']
        for meeting in meetings
        for row in api.get(f"/api/v2/meetings/{meeting['id']}/tasks").json()['tasks']
    )
    detail = api.get(f'/api/v2/tasks/{task}').json()
    assert any(event['field'] == 'due' and event['actor'] == 'human:ana' for event in detail['events'])


def _task_pages_have_demo_data(api):
    pages = api.get("/api/v2/pages?bot=growth-manager").json()["pages"]
    shown = api.get("/api/v2/pages/" + pages[0]["file_id"]).json()
    task_place = next(p["place"] for p in shown["pinned"] if p["place"].startswith("task:"))
    pins = api.get("/api/v2/places/" + task_place).json()["pins"]
    assert any(p.get("page", {}).get("file_id") == pages[0]["file_id"] for p in pins)


def _a_demo_task_has_a_page_made_by_its_person(api):
    pages = api.get("/api/v2/pages?bot=assistant").json()["pages"]
    made = next(p for p in pages if p["updated_by"] == "human:ana")
    pins = api.get("/api/v2/places/task:" + made["task_id"]).json()["pins"]
    assert any(p.get("page", {}).get("file_id") == made["file_id"] for p in pins)
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT v.attempt_id,m.refs FROM bot_file_versions v JOIN attempts a ON a.id=v.attempt_id "
            "JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id WHERE v.file_id=?",
            (made["file_id"],),
        ).fetchone()
        assert row and made["task_id"] in row["refs"]


def _mail_settings_have_a_read_grant_and_keep_owner_routing(api):
    grant = api.get("/api/v2/postal/addresses/all/grants").json()["grants"][0]
    assert (grant["grantee_id"], grant["level"]) == ("contracts-manager", "read")
    assert api.get("/api/v2/postal/settings").json()["settings"]["legal_bot_id"] is None


def _skills_page_has_usage_proposals_and_an_unused_install(api):
    usage = api.get("/api/v2/skills/usage", params={"days": 30}).json()["skills"]
    assert all(row["uses"] for row in usage) and any(row["top"] for row in usage)
    pending = api.get("/api/v2/skills/proposals", params={"status": "pending"}).json()["proposals"]
    assert {p["screen_status"] for p in pending} == {"pending", "screened"}
    states = {i["coverage"] for i in api.get("/api/v2/skills/refund-check/usage").json()["installs"]}
    assert {"used", "unused"} <= states


def test_the_demo_fills_every_page_and_links_its_records(api, subtests):
    """One demo build for every page (a subtest per nav page, named for it), then the records' own checks."""
    for name in sorted(PAGES.keys() - NO_DEMO_DATA.keys()):
        with subtests.test(msg=name):
            _page_has_demo_data(api, name)
    for check in (
        _full_page_audience_editor_has_a_restricted_example,
        _a_demo_task_and_meeting_link_to_each_other,
        _meeting_follow_up_is_an_ask_in_needs_you,
        _demo_task_has_an_editor_for_the_live_task_page,
        _task_pages_have_demo_data,
        _a_demo_task_has_a_page_made_by_its_person,
        _mail_settings_have_a_read_grant_and_keep_owner_routing,
        _skills_page_has_usage_proposals_and_an_unused_install,
    ):
        with subtests.test(check.__name__):
            check(api)
