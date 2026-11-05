"""Every page in the app's navigation has demo data. A page added to the nav without a demo section,
or a demo section that stops filling its page, fails here."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
import time_machine

from backend import demo
from backend.tests.test_demo import NOW, signed_in

UI = Path(__file__).resolve().parents[2] / "ui"
WEEK_AGO = NOW - timedelta(days=7)

NAV_LINK = re.compile(r'<a\b[^>]*\bdata-nav="([a-z-]+)"')          # static rail and account menu
NAV_ROW = re.compile(r"\blink\('([a-z-]+)',")                       # redesign.js rows
NAV_DATASET = re.compile(r"\.dataset\.nav\s*=\s*['\"]([a-z-]+)['\"]")  # rows added in code (finance)


def nav_pages():
    pages = set(NAV_LINK.findall((UI / "index.html").read_text()))
    for source in (UI / "app").glob("*.js"):
        text = source.read_text()
        pages.update(NAV_ROW.findall(text))
        pages.update(NAV_DATASET.findall(text))
    return pages


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
    "tools": page("tools", "integrations"),
    "connections": page("connections", "connections"),
    "meetings": page("meetings", "meetings"),
    "threads": page("threads", "threads"),
    # The sidebar's Inbox header opens the Threads page on its Inbox view: the same threads.
    "inbox": page("threads", "threads"),
    "strategy": page("strategy", "bets"),
    "growth": page("growth/numbers", "months", months=24),
    "finance": page("finance/transactions", "rows"),
    "rules": page("rules", "rules"),
    # The Rules section's tabs, one row each under Rules (ui/app/org-rules.js reads).
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
}


def test_nav_parse_finds_known_pages():
    assert {"overview", "tasks", "strategy", "finance", "rules"} <= nav_pages()


def test_every_nav_page_has_a_demo_mapping():
    for name in sorted(nav_pages()):
        assert name in PAGES or name in NO_DEMO_DATA, (
            f"{name}: add a demo_sections module for {name}, then map it here "
            "(PAGES in backend/tests/test_demo_coverage.py)")


@pytest.fixture(scope="module", autouse=True)
def demo_clock():
    """Builds and API reads see the same day, including clocks outside hubdb.now."""
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    with signed_in(demo.build(tmp_path_factory.mktemp("demo"), now=NOW)) as client:
        yield client


@pytest.mark.parametrize("name", sorted(PAGES.keys() - NO_DEMO_DATA.keys()))
def test_page_has_demo_data(api, name):
    path, key, query = PAGES[name]
    response = api.get("/api/v2/" + path, params=query)
    assert response.status_code == 200, f"{name}: GET /api/v2/{path} returned {response.status_code}"
    body = response.json()
    items = body if key is None else body.get(key)
    assert items, f"{name}: GET /api/v2/{path} has no {key or 'items'} in the demo"


def test_full_page_audience_editor_has_a_restricted_example(api):
    pages = api.get('/api/v2/pages?bot=marketing-lead').json()['pages']
    assert pages
    shown = api.get('/api/v2/pages/' + pages[0]['file_id']).json()
    assert shown['can_manage'] and shown['page']['audience']['people'] == ['ana']


def test_a_demo_task_and_meeting_link_to_each_other(api):
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


def test_demo_task_has_an_editor_for_the_live_task_page(api):
    meetings = api.get('/api/v2/meetings').json()['meetings']
    task = next(row['task_id'] for meeting in meetings for row in
                api.get(f"/api/v2/meetings/{meeting['id']}/tasks").json()['tasks'])
    detail = api.get(f'/api/v2/tasks/{task}').json()
    assert any(event['field'] == 'due' and event['actor'] == 'human:ana' for event in detail['events'])


def test_task_pages_have_demo_data(api):
    pages = api.get("/api/v2/pages?bot=marketing-lead").json()["pages"]
    shown = api.get("/api/v2/pages/" + pages[0]["file_id"]).json()
    task_place = next(p["place"] for p in shown["pinned"] if p["place"].startswith("task:"))
    pins = api.get("/api/v2/places/" + task_place).json()["pins"]
    assert any(p.get("page", {}).get("file_id") == pages[0]["file_id"] for p in pins)


def test_a_demo_task_has_a_page_made_by_its_person(api):
    pages = api.get("/api/v2/pages?bot=coo").json()["pages"]
    made = next(p for p in pages if p["updated_by"] == "human:ana")
    pins = api.get("/api/v2/places/task:" + made["task_id"]).json()["pins"]
    assert any(p.get("page", {}).get("file_id") == made["file_id"] for p in pins)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT v.attempt_id,m.refs FROM bot_file_versions v JOIN attempts a ON a.id=v.attempt_id "
                        "JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id WHERE v.file_id=?",
                        (made["file_id"],)).fetchone()
        assert row and made["task_id"] in row["refs"]


def test_mail_settings_have_a_read_grant_and_keep_owner_routing(api):
    grant = api.get("/api/v2/postal/addresses/all/grants").json()["grants"][0]
    assert (grant["grantee_id"], grant["level"]) == ("legal-review", "read")
    assert api.get("/api/v2/postal/settings").json()["settings"]["legal_bot_id"] is None
