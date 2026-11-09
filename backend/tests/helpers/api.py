"""Helpers that other tests share, moved out of test_api.py so they import no test file."""

import uuid
import yaml
from backend.auth import Identity
from backend.config import Settings
from backend.execution import AWAKE_SETTLE
from backend.store import H, encode


def api_settings(tmp_path):
    registry = tmp_path / "hub-registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(
        yaml.safe_dump({"owner": "ana@acme.example", "bot_admins": ["ben@acme.example"]})
    )
    return Settings(
        db_path=tmp_path / "hub.db",
        registry_dir=registry,
        test_identities={
            "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
            "ben-test": Identity("human:ben", "human", "ben@acme.example"),
            "cara-test": Identity("human:cara", "human", "cara@acme.example"),
            "eve-test": Identity("human:eve", "human", "eve@acme.example"),
            "fay-test": Identity("human:fay", "human", "fay@acme.example"),
        },
    )


def unrelated_task_managers(c, *slugs):
    """Keep privacy negatives outside the parties' org chains, without changing bot access grants."""
    import json

    roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
    if not any(p['id'] == 'privacy-lead' for p in roster['people']):
        roster['people'].append({'id': 'privacy-lead', 'email': 'privacy-lead@acme.example'})
    c.execute(
        "INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)",
        ('privacy-lead', 'Privacy Lead', 'privacy-lead@acme.example'),
    )
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
    targets = slugs or tuple(row[0] for row in c.execute('SELECT bot FROM bot_config'))
    for slug in targets:
        c.execute("UPDATE bot_config SET reports_to='human:privacy-lead' WHERE bot=?", (slug,))


def restrict(c, bot, **audience):
    """Set a bot's access straight in the database: see, read and write all go to `audience`
    (people, teams, bots), or per level with see=, read=, write= each an audience dict."""
    from backend import bot_access as BA
    from backend.store import H

    levels = {level: audience.pop(level) for level in BA.LEVELS if level in audience}
    every = {level: levels.get(level, audience) for level in BA.LEVELS}
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)  # a bot by its id or its slug
    c.execute(
        "UPDATE bot_config SET access=? WHERE bot=?",
        (BA.stored(BA.document({level: BA.audience(value) for level, value in every.items()})), bot),
    )


def as_member(api, email):
    """Take an Admin back to a plain member (the fixture makes Ben an admin: an admin manages every bot, so
    a test about what a member may not see needs him to be one)."""
    from backend import access as Access

    with api.app.state.store.transaction() as c:
        stored = Access._load_json(c, Access.ACCESS) or {}
        Access._store(
            c,
            Access.ACCESS,
            {
                **stored,
                "admins": [e for e in Access.load_access(c, api.app.state.store.settings)["admins"] if e != email],
            },
        )
    with api.app.state.store.read() as c:
        api.app.state.auth.sync_access(c)


def headers(token="ana-test", key=None):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": key or str(uuid.uuid4())}


def legacy_send_approval(api, payload, *, actor="bot:ops", task_id=None):
    """An existing pre-cutover card for compatibility reads; new email asks use the frozen queue."""
    with api.app.state.store.transaction() as c:
        row = H.approval_request(c, actor, "send", payload, task_id=task_id)
        cutover = c.execute(
            "SELECT applied_at FROM schema_migrations WHERE version='20261105040656_email_sending_queue'"
        ).fetchone()[0]
        c.execute("UPDATE approvals SET created_at=? WHERE id=?", (H.shift(cutover, seconds=-1), row["id"]))
        return H.approval(c, row["id"])


def post(api, path, body, token="ana-test", key=None, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    data = r.json()
    if expected == 200 and isinstance(data, dict):
        if path.startswith("chat/"):
            return data["message"]
        if "task" in data and set(data) <= {
            "task",
            "readback",
            "sent_as",
        }:  # a relation write reads back what it stored
            return data["task"]
    return data


def hold(task, kind="blocked"):
    """A task view's hold of one kind (docs/tasks.md, Holds), or {} when it has none."""
    return next((h for h in task.get("holds") or () if h["kind"] == kind), {})


def get(api, path, token="ana-test", expected=200):
    r = api.get("/api/v2/" + path, headers=headers(token))
    assert r.status_code == expected, r.text
    data = r.json()
    if expected == 200 and isinstance(data, dict):
        if path == "conversations":
            return data["conversations"]
        if path.startswith("conversations/") and path.endswith("/messages"):
            return data["messages"]
    return data


def put(api, path, body, token="ana-test", expected=200):
    r = api.put("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def runner(api, operator="ana", label="Test Mac", device_id=None):
    code = post(api, "enrollments", {"operator": operator})["code"]
    body = {"code": code, "label": label, "platform": "test"}
    if device_id is not None:
        body["device_id"] = device_id
    return post(api, "runners/enroll", body)


def assign(api, r, bot, generation=0, operator="ana-test"):
    return post(
        api,
        "bots/" + bot + "/assignment",
        {"runner_id": r["runner_id"], "expected_generation": generation},
        token=operator,
    )


def ready(api, r, bots, capabilities=()):
    # A runner reports readiness per bot id (the assignment's bot), so a slug given here is named by its id.
    with api.app.state.store.read() as c:
        ids = {bot: (H.resolve_bot(c, bot) or {}).get("id", bot) for bot in bots}
    body = {"version": "test", "platform": "test", "readiness": {ids[bot]: True for bot in bots}}
    if capabilities:
        body["capabilities"] = list(capabilities)
    return post(api, "runners/heartbeat", body, token=r["token"])


def claim(api, r, bot=None, key=None):
    return post(api, "jobs/claim", {"bot": bot, "start_places": True}, token=r["token"], key=key)["attempt"]


def setup_attempt(api, bot="ops", operator="ana"):
    r = runner(api, operator)
    assign(api, r, bot)
    ready(api, r, [bot])
    if bot == "coo":
        # Nobody chats with the assistant; a person hands it a task.
        msg = post(api, "tasks", {"owner": bot, "title": "Summarize", "body": "Please summarize the current work."})
    else:
        msg = post(api, "chat/" + bot, {"text": "Please summarize the current work."})
    return r, msg, claim(api, r)


def expire(api, aid):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), aid))


def settled(api, r):
    """A machine that came back and has been reporting in ever since."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET awake_since=? WHERE runner_id=?",
            (H.shift(H.now(), seconds=-AWAKE_SETTLE - 1), r["runner_id"]),
        )
