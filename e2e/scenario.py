"""The fixed scenario every e2e kind drives, and the reads it captures.

`populate` writes through the v2 API as a person and as a bot (a runner claims the bot's turn), so the rows are the
ones a real install makes. `capture` reads the main v2 resources back as the owner. Both run against the `api`
fixture from backend/tests/test_api.py: a fresh temporary database and a fictional company (Acme).
"""
import sqlite3
import time

from backend.tests.test_api import assign, claim, headers, ready, runner

OWNER, MEMBER = "ana-test", "cara-test"
NOTE_STEP = {"id": "tell", "type": "action", "enabled": True, "do": "wake_bot",
             "args": {"bot": "ops", "text": "Look"}}
HTML = b"<html><head><title>Launch</title></head><body><h1>Launch weekly</h1><p>On track.</p></body></html>"


class Api:
    """Calls the v2 API as a signed-in person (by test token) or as a bot (by attempt token)."""

    def __init__(self, client):
        self.client = client
        self.tokens = {}

    def call(self, method, path, token=OWNER, expected=200, **kwargs):
        response = self.client.request(method, "/api/v2/" + path, headers=headers(token), **kwargs)
        assert response.status_code == expected, (method, path, response.status_code, response.text[:400])
        return response.json() if response.content and response.headers.get("content-type", "").startswith("application/json") else None


def populate(client):
    """Run the scenario on a server (the `api` fixture's client). Returns the ids it made, by name."""
    api = Api(client)
    ids = {}
    # A chat with the ops bot, a reply in it, and a direct message to the bot.
    chat = api.call("post", "chat/ops", json={"text": "Plan the launch."})
    ids["conversation"] = chat["conversation"]["id"]
    ids["chat_message"] = chat["message"]["id"]
    reply = api.call("post", "conversations/%s/messages" % ids["conversation"], json={"text": "Add the budget."})
    ids["reply_message"] = reply["message"]["id"]
    sent = api.call("post", "messages", json={"to": "bot:ops", "text": "Third note."})
    ids["direct_message"] = sent["id"]
    # Tasks: created for a person, handed to a bot, moved through done, then closed by the owner.
    made = api.call("post", "tasks", json={"title": "Draft the launch plan", "body": "Please draft it.",
                                           "owner": "human:ana"})["task"]
    ids["task"] = made["id"]
    assigned = api.call("post", "tasks/%s" % made["id"], json={"version": made["version"], "owner": "bot:ops"})
    doing = api.call("post", "tasks/%s" % made["id"], json={"version": assigned["task"]["version"], "status": "doing"})
    done = api.call("post", "tasks/%s" % made["id"], json={"version": doing["task"]["version"], "status": "done",
                                                            "note": "Drafted."})
    api.call("post", "tasks/%s" % made["id"], json={"version": done["task"]["version"], "status": "closed",
                                                     "note": "Accepted."})
    open_task = api.call("post", "tasks", json={"title": "Check the vendor list", "body": "Compare the three.",
                                                "owner": "bot:ops"})["task"]
    ids["open_task"] = open_task["id"]
    # A bot's turn: a file, a page and a spend approval it asks a person to decide.
    machine, attempt = bot_turn(client)
    bot = Api(client)
    bot_token = attempt["token"]
    file = bot.call("post", "files/uploads", token=bot_token, json={"name": "plan.md", "text": "# Plan\n\nLaunch Monday."})
    ids["file"] = file["file"]["id"] if "file" in file else file["id"]
    page = client.post("/api/v2/pages", params={"name": "weekly.html", "title": "Launch weekly", "cadence": "weekly"},
                       content=HTML, headers={**headers(bot_token), "Content-Type": "text/html"})
    assert page.status_code == 200, page.text
    ids["page"] = page.json()["page"]["file_id"]
    settle_media(client)
    asked = bot.call("post", "approvals", token=bot_token, json={
        "kind": "spend", "payload": {"amount": 5, "account": "ads", "what": "Launch test"},
        "task_id": open_task["id"]})
    ids["approval"] = asked["id"]
    decided = api.call("post", "approvals/%s" % asked["id"], json={"decision": "declined", "note": "Not now."})
    assert decided["decision"] == "declined", decided
    waiting = bot.call("post", "approvals", token=bot_token, json={
        "kind": "spend", "payload": {"amount": 9, "account": "ads", "what": "Second test"},
        "task_id": open_task["id"]})
    ids["pending_approval"] = waiting["id"]          # still open: it shows in Needs you
    finish_turn(client, machine, attempt)
    # A goal with a measure, one reading, and a check-in.
    goal = api.call("post", "goals", json={"title": "Raise activation", "owner": "me"})["goal"]
    ids["goal"] = goal["id"]
    kpi = api.call("post", "kpis", json={"name": "Activation", "unit": "%", "cadence": "daily", "goal_id": goal["id"],
                                         "kind": "improve", "baseline": 40, "target": 70, "deadline": "2099-12-31"})["kpi"]
    ids["kpi"] = kpi["id"]
    api.call("post", "kpis/%s/readings" % kpi["id"], json={"value": 41, "evidence": "https://bi.example/q/1"})
    api.call("post", "goals/%s/checkins" % goal["id"], json={"body": "On plan.", "signal": "on_track"})
    # An automation a person owns: scheduled, one step that wakes the ops bot.
    auto = api.call("post", "automations", json={
        "name": "Morning check", "owner": "person:ana",
        "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}, "steps": [NOTE_STEP]})
    ids["automation"] = auto["id"] if "id" in auto else auto["automation"]["id"]
    return ids


def settle_media(client, timeout=30):
    """Previews and metadata for new files are made by a background loop. Wait until its queue is empty, so the state
    the scenario leaves (every version's media_state) is the same on every run and on a copy that boots."""
    client.app.state.file_metadata.wake.set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        with client.app.state.store.read() as c:
            if not c.execute("SELECT 1 FROM bot_file_versions WHERE media_state='pending' LIMIT 1").fetchone():
                return
        time.sleep(0.05)
    raise AssertionError("file previews still pending after %s seconds" % timeout)


def bot_turn(client, bot="ops"):
    """A person chats with the bot and a new computer claims the turn: (machine, the attempt with the bot's token).
    The bot is moved to the new computer at its current assignment generation."""
    api = Api(client)
    machine = runner(client)
    current = api.call("get", "bots/" + bot) .get("assignment") or {}
    assign(client, machine, bot, generation=current.get("generation", 0))
    ready(client, machine, [bot])
    api.call("post", "chat/" + bot, json={"text": "Write the plan"})
    return machine, claim(client, machine, bot)


def finish_turn(client, machine, attempt):
    """The runner reports the bot's turn complete, so no attempt is in flight when a database is copied (a restart
    would cut an in-flight attempt and write an event for it)."""
    api = Api(client)
    api.call("post", "attempts/%s/started" % attempt["id"], token=machine["token"], json={"thread_id": attempt["lane"]})
    api.call("post", "attempts/%s/complete" % attempt["id"], token=machine["token"],
             json={"outcome": "completed", "last_seq": 0, "text": "Drafted the launch plan."})


def populate_direct(path, columns=None):
    """Rows the v2 API does not reach: runner load samples, host performance points, rate-limit readings, service
    health, watcher runs and the company's usage pool. Written straight to the database, the way the runner and the
    hub's own timers write them. `columns` ({"table.column": "old_column"}) names a column the database calls by an
    older name (a rollback copy made before a rename); a name the database has is used as it is."""
    columns = columns or {}
    rows = [
        ("rate_limits", {"runtime": "codex", "used_percent": 12.5, "window_minutes": 300,
                         "resets_at": "2026-11-01T05:00:00.000000Z", "updated_at": "2026-11-01T00:00:00.000000Z"}),
        ("service_health", {"service": "mail", "last_succeeded_at": "2026-11-01T00:00:00.000000Z", "last_error": None,
                            "detail": '{"checked": 3}'}),
        ("perf_points", {"runner_id": "runner-1", "span": 60, "ts": 1793500000, "n": 4, "scope": "host", "cpu": 0.25,
                         "cpu_max": 0.5, "load1": 1.0, "mem_used": 100, "mem_max": 200, "mem_total": 400, "swap_used": 0,
                         "swap_total": 0, "disk_free": 1000, "disk_total": 2000, "active": 1}),
        ("runner_load_samples", {"runner_id": "runner-1", "ts": 1793500000, "sampled_at": 1793500000, "cpu_pct": 12.0,
                                 "mem_used": 100, "mem_total": 400, "swap_used": 0, "swap_total": 0, "running_turns": 1}),
        ("watcher_runs", {"bot": "ops", "name": "inbox-check", "runner_id": "runner-1",
                          "started_at": "2026-11-01T00:00:00.000000Z", "finished_at": "2026-11-01T00:00:01.000000Z",
                          "exit_code": 0, "timed_out": 0, "every": 300, "output": "ok", "events": 0}),
    ]
    with sqlite3.connect(path) as c:
        for table, row in rows:
            _insert(c, table, row, columns)
        c.commit()


def _insert(c, table, row, columns):
    have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    names = []
    for name in row:
        if name not in have:
            name = columns.get(f"{table}.{name}", name)
            if name not in have:
                raise AssertionError(f"{table} has no column {name} for the direct rows")
        names.append(name)
    marks = ",".join("?" * len(names))
    c.execute(f"INSERT INTO {table}({','.join(names)}) VALUES({marks})", tuple(row.values()))


# Each capture: (golden name, request path, query). Reads as the owner unless stated.
CAPTURES = [
    ("tasks", "tasks", {"snoozed": "all"}),
    ("task_detail", "tasks/{task}", {}),
    ("conversations", "conversations", {}),
    ("messages", "conversations/{conversation}/messages", {}),
    ("needs_you", "needs-you", {}),
    ("bots", "bots", {}),
    ("bot_ops", "bots/ops", {}),
    ("status", "status", {}),
    ("goals", "goals", {}),
    ("goal_detail", "goals/{goal}", {}),
    ("kpis", "kpis", {}),
    ("kpi_readings", "kpis/{kpi}/readings", {}),
    ("bot_files", "bots/ops/files", {}),
    ("pages", "pages", {}),
    ("automations", "automations", {}),
]


def capture(client, ids, token=OWNER):
    """The main v2 responses for `token`, keyed by golden name: the request and the status and body it got."""
    api = Api(client)
    out = {}
    for name, path, query in CAPTURES:
        resolved = path.format(**{k: v for k, v in ids.items() if k in path})
        body = api.call("get", resolved, token=token, params=query)
        out[name] = {"request": "GET /api/v2/" + resolved + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""),
                     "status": 200, "body": body}
    return out


def events(stream):
    """The catch-up stream read from change 0: every change up to `ready`, then anything that follows in half a second."""
    stream.until(lambda event: event["event"] == "ready")
    stream.drain(0.5)
    return [{"event": e["event"], "id": e.get("id"), "data": e.get("data")}
            for e in stream.seen if e["event"] not in ("keepalive", "closed")]


def _view(api, token):
    """The task ids and message ids one viewer reads, following every page."""
    tasks, offset = set(), 0
    while True:
        page = api.call("get", "tasks", token=token, params={"snoozed": "all", "offset": offset, "limit": 500})
        tasks |= {t["id"] for t in page["tasks"]}
        if page.get("next_offset") is None:
            break
        offset = page["next_offset"]
    messages = set()
    for convo in api.call("get", "conversations", token=token)["conversations"]:
        before = None
        while True:
            params = {"limit": 200, **({"before": before} if before else {})}
            page = api.call("get", "conversations/%s/messages" % convo["id"], token=token, params=params)
            messages |= {m["id"] for m in page["messages"]}
            if not page.get("has_more"):
                break
            before = page["next_before"]
    return {"tasks": sorted(tasks), "messages": sorted(messages)}


def viewer_sets(client):
    """The task ids and message ids each viewer reads through the v2 API: the owner, a plain member and a bot.

    A bot reads with an attempt token, which only a live turn holds, so this runs a turn of its own (a chat from the
    owner, a claim, a completion). The turn's writes are part of the database the viewer sees, so callers compare
    what was there before the turn (see e2e/tests/test_upgrade.py)."""
    api = Api(client)
    machine, attempt = bot_turn(client)
    result = {"owner": _view(api, OWNER), "member": _view(api, MEMBER), "bot": _view(api, attempt["token"])}
    finish_turn(client, machine, attempt)
    return result
