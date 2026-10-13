"""History replay shows a viewer only what the task list, chats, runs and statuses already show them, in time order."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from backend import replay
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


def test_replay_is_ordered_and_never_shows_a_viewer_more_than_they_can_see(api, monkeypatch):
    started = datetime.now(timezone.utc) - timedelta(minutes=1)
    store = api.app.state.store
    with store.transaction() as c:
        public = H.task_create(c, "human:cara", "Refresh the Acme pricing page", "", "bot:ops", lint=False, private=False)
        secret = H.task_create(c, "human:ana", "Acme layoffs plan", "", "human:ben", lint=False, private=True)
        mail = H.task_create(c, "human:ana", "Sort the inbox", "", "bot:inbox", lint=False, private=False)
        H.task_update(c, "bot:ops", public["id"], status="doing", note="On it")
        H.task_update(c, "human:ben", secret["id"], status="doing", note="Acme layoffs draft")
        H.status_set(c, H.KEEPER, "inbox", state="running", focus="Reading the owner's mail")
        H.status_set(c, H.KEEPER, "inbox", state="idle", focus="")
    mine = post(api, "chat/ops", {"text": "Hello from Cara"}, token="cara-test")
    gone = post(api, "chat/ops", {"text": "Typo, deleted"}, token="cara-test")
    post(api, "chat/ops", {"text": "Owner-only note"})
    post(api, "chat/inbox", {"text": "Forward the bank email"})
    with store.transaction() as c:
        H.say(c, "bot:ops", "human:cara", "Hi Cara", conversation_id=mine["conversation_id"], in_reply_to=mine["id"])
        c.execute("UPDATE messages SET deleted_at=?, body='' WHERE id=?", (H.now(), gone["id"]))
    ended = datetime.now(timezone.utc) + timedelta(minutes=1)
    span = urlencode({"from": started.isoformat(), "to": ended.isoformat()})

    def texts(data):
        return " ".join(str(i.get("text", "")) + " " + str((i.get("task") or {}).get("title", "")) for i in data["items"])

    owner = get(api, "replay?" + span)
    assert all(w in texts(owner) for w in ("Acme layoffs plan", "Owner-only note", "Forward the bank email"))

    cara = get(api, "replay?" + span, token="cara-test")
    stamps = [i["at"] for i in cara["items"]]
    assert stamps == sorted(stamps)
    seen = texts(cara)
    assert "Refresh the Acme pricing page" in seen and "Hello from Cara" in seen and "Hi Cara" in seen
    assert any(i["kind"] == "task.status" and i["task"]["id"] == public["id"] and i["new"] == "doing" for i in cara["items"])
    for hidden in ("Acme layoffs", "Owner-only note", "Forward the bank email", "Sort the inbox", "Typo", "owner's mail"):
        assert hidden not in seen
    assert not any(i.get("bot") == "inbox" or i.get("who") == "bot:inbox" for i in cara["items"])
    assert secret["id"] not in str(cara) and mail["id"] not in str(cara)
    get(api, "replay?" + span + "&bot=inbox", token="cara-test", expected=404)

    # Cut short, a replay ends before where any source stopped, and `next` continues from there.
    monkeypatch.setattr(replay, "LIMIT", 2)
    short = get(api, "replay?" + span, token="cara-test")
    assert short["truncated"] and short["items"] and all(i["at"] < short["next"] for i in short["items"])



def test_replay_refuses_a_time_too_far_out_to_convert(api):
    for start, end in (("0001-01-01T00:00:00+14:00", "0001-01-02T00:00:00+14:00"),
                       ("9999-12-30T00:00:00-14:00", "9999-12-31T23:59:59-14:00")):
        r = api.get("/api/v2/replay?" + urlencode({"from": start, "to": end}), headers={"Authorization": "Bearer ana-test"})
        assert r.status_code == 422, r.text
        assert "out of range" in r.text


def test_replay_scans_a_bounded_number_of_rows_per_source(api, monkeypatch):
    """Rows the viewer may not read, or outside the scope, still count: a call stops scanning and says it was cut short."""
    store = api.app.state.store
    monkeypatch.setattr(replay, "SCAN_MAX", 3)

    def span():
        return datetime.now(timezone.utc)

    def query(first, last, extra=""):
        return "replay?" + urlencode({"from": first.isoformat(), "to": last.isoformat()}) + extra

    # Task changes: a private task Cara cannot read changes status many times, then one she can read does.
    started = span() - timedelta(seconds=1)
    with store.transaction() as c:
        secret = H.task_create(c, "human:ana", "Private plan", "", "human:ben", lint=False, private=True)
        public = H.task_create(c, "human:cara", "Public plan", "", "bot:ops", lint=False, private=False)
    with store.transaction() as c:
        for status in ("doing", "waiting", "doing", "waiting", "doing"):
            H.task_update(c, "human:ben", secret["id"], status=status)
        H.task_update(c, "bot:ops", public["id"], status="doing")
    tasks = get(api, query(started, span() + timedelta(seconds=1)), token="cara-test")
    assert tasks["truncated"] and tasks["next"]
    assert all(i["at"] < tasks["next"] for i in tasks["items"])

    # Messages: a scope that leaves out every message in Cara's room still scans them, and stops.
    started = span() - timedelta(seconds=1)
    for n in range(6):
        post(api, "chat/ops", {"text": f"Note {n}"}, token="cara-test")
    rooms = get(api, query(started, span() + timedelta(seconds=1), "&person=ben"), token="cara-test")
    assert rooms["truncated"] and rooms["next"] and not rooms["items"]

    # Under the cap nothing is cut short.
    monkeypatch.setattr(replay, "SCAN_MAX", 1000)
    whole = get(api, query(started, span() + timedelta(seconds=1), "&person=ben"), token="cara-test")
    assert not whole["truncated"]
