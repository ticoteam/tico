"""Website tools' browser commands (backend/browser_commands.py): a bot's verb reaches the computer that runs the
browser and its report comes back; where it runs, one caller at a time, a person's live view and control.

The accounts come from a stand-in for backend/website_tools.py with the same signatures (`pick`, `picked_account`,
`secret`, `expires_in`, `site_matches`, `log`), so these tests need no vault; the computers are runners on their own
routes, with their own tokens."""

import sys
import threading
import time
import types

import pytest

from backend import browser_commands as bc
from backend import website_tools as real_tools
from backend.store import Problem
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner

PASSWORD = "canary-pw-7Q2x"
CODE = "424242"
ACCOUNT = "website-tools/tool1/accounts/acct1/browser"
JPEG = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x10\x00\x20\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9"


class Tools(types.ModuleType):
    """backend/website_tools.py's interface over one account, acct1 on tool1 (acme.example)."""

    def __init__(self):
        super().__init__("backend.website_tools")
        self.central_only = False
        self.has_password = True
        self.logs = []

    def _picked(self):
        return {
            "tool_id": "tool1",
            "account_id": "acct1",
            "name": "Acme",
            "sites": ["acme.example"],
            "central_only": self.central_only,
            "username": "robot",
            "has_password": self.has_password,
            "has_totp": True,
            "holder": None,
        }

    require_value = staticmethod(real_tools.require_value)

    def pick(self, conn, who, site):
        if site not in ("acme.example", "Acme"):
            raise Problem("no_such_site", "No website tool for that site", 404)
        return self._picked()

    def picked_account(self, conn, who, tool_id, account_id, admins=None, auth=None):
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "Only a person", 403)
        if (tool_id, account_id) != ("tool1", "acct1"):
            raise Problem("not_found", "No such account on this tool", 404)
        return self._picked()

    def authorize(self, conn, who, tool_id, account_id):
        assert (tool_id, account_id) == ("tool1", "acct1")
        return self._picked()

    def secret(self, conn, vault, account_id, field, *, charge=True):
        return {"username": "robot", "password": PASSWORD, "code": CODE}[field]

    def spend(self, account_id):
        return None

    def expires_in(self, conn, vault, account_id):
        return 17

    def site_matches(self, sites, url):
        from urllib.parse import urlsplit

        host = urlsplit(url if "://" in url else "//" + url).hostname or ""
        return any(host == s or host.endswith("." + s) for s in sites)

    def log(self, conn, picked, who, verb, page, where, result="use"):
        self.logs.append((who.actor, verb, page, where, result))


@pytest.fixture
def tools(monkeypatch):
    import backend

    fake = Tools()
    monkeypatch.setitem(sys.modules, "backend.website_tools", fake)
    monkeypatch.setattr(backend, "website_tools", fake, raising=False)
    return fake


@pytest.fixture
def setup(api, tools):
    """A browser host with Chrome, and the bots ops and finance on their own computer (Chrome too), each on a task."""
    host = runner(api, label="Host Mac")
    post(
        api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "capabilities": ["browser_v1"]},
        token=host["token"],
    )
    post(api, f"runners/{host['runner_id']}/browser-host", {"on": True})
    own = runner(api, label="Own Mac")
    assign(api, own, "ops")
    assign(api, own, "finance")
    ready(api, own, ["ops", "finance"], capabilities=["browser_v1"])
    tasks = {}
    for bot in ("ops", "finance"):
        tasks[bot] = post(api, "tasks", {"owner": bot, "title": f"Pull the {bot} report", "body": "Get it from Acme."})
    turns = {bot: claim(api, own, bot) for bot in ("ops", "finance")}
    for computer in (host, own):
        get(api, "runner-browser-commands?wait=0", token=computer["token"])
    facts = bc.MAILBOX.facts("acct1")
    facts.where, facts.runner_id, facts.computer = "central", host["runner_id"], "Host Mac"
    facts.tool_id, facts.sites = "tool1", ["acme.example"]
    return types.SimpleNamespace(api=api, host=host, own=own, tasks=tasks, ops=turns["ops"], finance=turns["finance"])


def call(s, turn, verb, args=None, site="acme.example"):
    """A bot's command, sent from a thread: the request waits for a computer's report."""
    box = {}

    def go():
        box["r"] = s.api.post(
            "/api/v2/browser/commands",
            json={"site": site, "verb": verb, "args": args or {}},
            headers=headers(turn["token"]),
        )

    thread = threading.Thread(target=go)
    thread.start()
    return thread, box


def done(thread, box):
    thread.join(20)
    assert not thread.is_alive()
    return box["r"]


def now(s, turn, verb, args=None, site="acme.example"):
    """A command answered without any computer (refused, or answered by the server)."""
    return s.api.post(
        "/api/v2/browser/commands",
        json={"site": site, "verb": verb, "args": args or {}},
        headers=headers(turn["token"]),
    )


def collect(s, computer, wait=5):
    return get(s.api, f"runner-browser-commands?wait={wait}", token=computer["token"])["commands"]


def report(s, computer, cmd, **answer):
    body = {"ok": True, "url": "https://acme.example/home?session=x", "title": "Home"} | answer
    return post(s.api, f"runner-browser-commands/{cmd['id']}/report", body, token=computer["token"])


def serve(s, computer, **answer):
    cmds = collect(s, computer)
    for cmd in cmds:
        report(s, computer, cmd, **answer)
    return cmds


def stale(s, computer):
    bc.MAILBOX.polled_at[computer["runner_id"]] = time.monotonic() - bc.POLL_FRESH - 1


def queued():
    return [c for q in bc.MAILBOX.queues.values() for c in q]


def test_a_command_reaches_the_host_and_its_report_returns_to_the_bot(setup, tools):
    s = setup
    thread, box = call(s, s.ops, "open", {"url": "https://app.acme.example/reports"})
    [cmd] = serve(s, s.host, signed_in=True)
    assert cmd["verb"] == "open" and cmd["args"] == {"url": "https://app.acme.example/reports"}
    assert cmd["profile"] == "acct1" and cmd["sites"] == ["acme.example"] and "value" not in cmd
    r = done(thread, box)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "where": "central", "url": "https://acme.example/home?session=x", "title": "Home"}
    assert tools.logs == [("bot:ops", "open", "acme.example/home", "central", "use")]
    session = bc.session_view("acct1")
    assert {k: session[k] for k in ("state", "where", "computer")} == {
        "state": "in_use",
        "where": "central",
        "computer": "Host Mac",
    }
    assert session["by"]["subject"] == "bot:ops" and session["by"]["kind"] == "bot"
    with s.api.app.state.store.read() as c:
        assert bc.host_view(c) == {"runner_id": s.host["runner_id"], "label": "Host Mac", "online": True}
    # An address off the tool's sites never reaches a computer.
    r = now(s, s.ops, "open", {"url": "https://evil.example/"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_on_site"


def test_host_offline_falls_back_to_the_callers_own_computer(setup, tools):
    s = setup
    stale(s, s.host)
    thread, box = call(s, s.ops, "open")
    [cmd] = serve(s, s.own, text="Reports")
    r = done(thread, box)
    assert r.status_code == 200 and r.json()["where"] == "own"
    assert cmd["verb"] == "open"
    with s.api.app.state.store.read() as c:
        assert bc.host_view(c)["online"] is False
        assert bc.health_problem(c).startswith("Browser host offline: Host Mac")


def test_central_only_with_the_host_offline_is_refused(setup, tools):
    s = setup
    tools.central_only = True
    stale(s, s.host)
    r = now(s, s.ops, "open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "browser_host_offline"
    assert "Host Mac is offline" in r.json()["error"]["detail"]
    assert queued() == []


def test_busy_for_a_second_bot_until_close_or_the_hold_ends(setup, tools):
    s = setup
    thread, box = call(s, s.ops, "open")
    serve(s, s.host)
    assert done(thread, box).status_code == 200
    r = now(s, s.finance, "read")
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    thread, box = call(s, s.ops, "close")
    serve(s, s.host)
    assert done(thread, box).status_code == 200
    thread, box = call(s, s.finance, "open")
    serve(s, s.host, text="Page")
    assert done(thread, box).status_code == 200
    # finance holds it now; once its hold runs out ops gets it back without a close.
    assert now(s, s.ops, "read").json()["error"]["code"] == "busy"
    bc.MAILBOX.holds["acct1"].until = time.monotonic() - 1
    thread, box = call(s, s.ops, "read")
    serve(s, s.host, text="Page")
    assert done(thread, box).status_code == 200


def test_a_person_in_control_blocks_bots_and_drives_the_browser(setup, tools):
    s = setup
    r = s.api.post(f"/api/v2/{ACCOUNT}/input", json={"kind": "click", "x": 10, "y": 20}, headers=headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_control"
    assert post(s.api, ACCOUNT + "/control", {"take": True}) == {"ok": True, "control": True}
    r = now(s, s.ops, "open")
    assert r.status_code == 409 and r.json()["error"]["code"] == "person_in_control"
    assert bc.session_view("acct1")["state"] == "in_use"
    # Another person waits too.
    r = s.api.post(f"/api/v2/{ACCOUNT}/control", json={"take": True}, headers=headers("ben-test"))
    assert r.status_code in (404, 409)
    box = {}
    thread = threading.Thread(
        target=lambda: box.setdefault("r", post(s.api, ACCOUNT + "/input", {"kind": "click", "x": 10, "y": 20}))
    )
    thread.start()
    [cmd] = serve(s, s.host)
    thread.join(20)
    assert box["r"] == {"ok": True} and cmd["verb"] == "input_click" and cmd["args"] == {"x": 10, "y": 20}
    assert post(s.api, ACCOUNT + "/control", {"take": False}) == {"ok": True, "control": False}
    thread, box = call(s, s.ops, "read")
    serve(s, s.host, text="Page")
    assert done(thread, box).status_code == 200


def test_fill_sends_the_value_to_the_computer_only(setup, tools):
    s = setup
    thread, box = call(s, s.ops, "fill", {"field": "password", "ref": "e7"})
    [cmd] = collect(s, s.host)
    assert cmd["value"] == PASSWORD and cmd["args"] == {"field": "password", "ref": "e7"}
    report(s, s.host, cmd, text=f"typed {PASSWORD}", title=f"Signed in {PASSWORD}")
    r = done(thread, box)
    assert r.status_code == 200 and PASSWORD not in r.text
    assert tools.logs == [("bot:ops", "fill", "acme.example/home", "central", "use")]
    assert PASSWORD not in repr(tools.logs)
    with s.api.app.state.store.read() as c:
        for table in ("events", "idempotency", "tool_calls"):
            rows = c.execute(f"SELECT * FROM {table}").fetchall()
            assert PASSWORD not in repr([tuple(r) for r in rows]), table
    # A runner's refusal never echoes the value either.
    thread, box = call(s, s.ops, "fill", {"field": "password"})
    [cmd] = collect(s, s.host)
    report(s, s.host, cmd, ok=False, error="not_on_site", text=f"refused {PASSWORD}")
    r = done(thread, box)
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_on_site" and PASSWORD not in r.text
    assert tools.logs[-1][1:] == ("fill", "acme.example/home", "central", "refused")


def test_an_account_with_no_password_is_refused_before_the_browser_is_held(setup, tools):
    s = setup
    tools.has_password = False
    for verb in ("fill", "copy"):
        r = now(s, s.ops, verb, {"field": "password"})
        error = r.json()["error"]
        assert (r.status_code, error["code"]) == (409, "no_password"), r.text
        assert 'tico browser needs-you acme.example "Sign in to acme.example"' in error["detail"]
    assert queued() == [] and "acct1" not in bc.MAILBOX.holds
    # The username still fills, and another bot finds the account free.
    thread, box = call(s, s.finance, "fill", {"field": "username"})
    [cmd] = collect(s, s.host)
    assert cmd["value"] == "robot"
    report(s, s.host, cmd)
    assert done(thread, box).status_code == 200


def test_copy_runs_on_the_callers_computer_and_not_for_central_only(setup, tools):
    s = setup
    thread, box = call(s, s.ops, "copy", {"field": "username"})
    assert collect(s, s.host, wait=0) == []
    [cmd] = collect(s, s.own)
    assert cmd["value"] == "robot" and cmd["verb"] == "copy"
    report(s, s.own, cmd)
    r = done(thread, box)
    assert r.status_code == 200 and r.json()["where"] == "own"
    assert tools.logs[-1] == ("bot:ops", "copy", "acme.example/home", "own", "use")
    assert bc.session_view("acct1")["where"] == "central"
    tools.central_only = True
    r = now(s, s.ops, "copy", {"field": "password"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "central_only"


def test_code_is_answered_by_the_server(setup, tools):
    s = setup
    r = now(s, s.ops, "code")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "where": "own", "text": CODE, "expires_in": 17}
    assert queued() == [] and tools.logs[-1][1] == "code"


def test_needs_you_asks_a_person_and_their_answer_wakes_the_bot(setup, tools):
    s = setup
    task = s.tasks["ops"]
    asked = post(
        s.api,
        "browser/needs-you",
        {"site": "acme.example", "line": "Please type the code Acme texted you."},
        token=s.ops["token"],
    )
    rid = asked["requirement_id"]
    assert rid
    assert task["id"] in {str(i.get("task_id") or i["id"]) for i in get(s.api, "needs-you")["items"]}
    with s.api.app.state.store.read() as c:
        shows = c.execute("SELECT shows FROM requirements WHERE id=?", (rid,)).fetchone()[0]
        before = c.execute("SELECT count(*) FROM messages WHERE wake_key LIKE ?", (task["id"] + ":%",)).fetchone()[0]
    assert "browser:tool1/acct1" in shows
    assert bc.session_view("acct1")["state"] == "needs_you"
    assert tools.logs[-1][:2] == ("bot:ops", "needs_you")
    post(s.api, f"tasks/{task['id']}/asks/{rid}/answer", {"value": "Done"})
    with s.api.app.state.store.read() as c:
        after = c.execute("SELECT count(*) FROM messages WHERE wake_key LIKE ?", (task["id"] + ":%",)).fetchone()[0]
    assert after > before
    # A person's own call is refused: needs-you is a bot's, on its task.
    r = s.api.post("/api/v2/browser/needs-you", json={"site": "acme.example", "line": "Help."}, headers=headers())
    assert r.status_code == 403


def test_the_frame_takes_at_most_one_screenshot_a_second(setup, tools):
    s = setup
    results = []

    def watch():
        results.append(s.api.get(f"/api/v2/{ACCOUNT}/frame", headers=headers()))

    watchers = [threading.Thread(target=watch) for _ in range(3)]
    for w in watchers:
        w.start()
    time.sleep(0.3)
    cmds = collect(s, s.host)
    assert [c["verb"] for c in cmds] == ["screenshot"]
    import base64

    report(s, s.host, cmds[0], image=base64.b64encode(JPEG).decode(), url="https://acme.example/home")
    for w in watchers:
        w.join(20)
    assert [r.status_code for r in results] == [200, 200, 200]
    assert results[0].content == JPEG and results[0].headers["content-type"] == "image/jpeg"
    assert results[0].headers["x-page-width"] == "32" and results[0].headers["x-page-height"] == "16"
    assert results[0].headers["x-page-url"] == "https://acme.example/home"
    again = s.api.get(f"/api/v2/{ACCOUNT}/frame", headers=headers())
    assert again.status_code == 200 and again.content == JPEG
    assert collect(s, s.host, wait=0) == []  # within the second: the picture it has
    # A bot never reads the live view.
    r = s.api.get(f"/api/v2/{ACCOUNT}/frame", headers=headers(s.ops["token"]))
    assert r.status_code == 403


def test_a_computer_that_is_neither_the_host_nor_running_the_turn_gets_nothing(setup, tools):
    s = setup
    other = runner(s.api, label="Other Mac")
    post(
        s.api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "capabilities": ["browser_v1"]},
        token=other["token"],
    )
    thread, box = call(s, s.ops, "read")
    time.sleep(0.3)
    assert collect(s, other, wait=0) == []
    assert collect(s, s.own, wait=0) == []
    # The host stops being the host before it collects: it gets nothing and the bot is told.
    post(s.api, f"runners/{s.host['runner_id']}/browser-host", {"on": False})
    assert all(cmd["verb"] == "close" for cmd in collect(s, s.host, wait=0))
    r = done(thread, box)
    assert r.status_code == 409 and r.json()["error"]["code"] == "browser_host_offline"
    # And a report for a command it never collected is a 404.
    r = s.api.post("/api/v2/runner-browser-commands/nope/report", json={"ok": True}, headers=headers(other["token"]))
    assert r.status_code == 404


def test_no_reply_is_a_504(setup, tools, monkeypatch):
    s = setup
    monkeypatch.setattr(bc, "REPLY_WAIT", 0.3)
    r = now(s, s.ops, "read")
    assert r.status_code == 504 and r.json()["error"]["code"] == "browser_timeout"
    assert queued() == []


def test_the_browser_host_takes_no_other_bots(setup, tools):
    s = setup
    with s.api.app.state.store.read() as c:
        with pytest.raises(Problem) as refused:
            bc.check_placement(c, "ops", s.host["runner_id"])
        bc.check_placement(c, "ops", s.own["runner_id"])
    assert refused.value.code == "browser_host"
    # A computer with bots on it is not made the host.
    r = s.api.post(f"/api/v2/runners/{s.own['runner_id']}/browser-host", json={"on": True}, headers=headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "browser_host"
    r = s.api.post(
        f"/api/v2/runners/{s.own['runner_id']}/browser-host", json={"on": True}, headers=headers("cara-test")
    )
    assert r.status_code == 403


def test_health_says_when_chrome_is_missing_and_a_removed_host_is_forgotten(setup, tools):
    s = setup
    post(s.api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": []}, token=s.host["token"])
    with s.api.app.state.store.read() as c:
        assert (
            bc.health_problem(c)
            == "Chrome missing on Host Mac: install Google Chrome (Chromium on Linux) there so website tools can run."
        )
    from backend import computers

    with s.api.app.state.store.transaction() as c:
        computers.revoke(c, "human:ana", s.host["runner_id"])
    with s.api.app.state.store.read() as c:
        assert bc.host_id(c) is None and bc.health_problem(c) is None


def test_values_sent_earlier_are_scrubbed_from_every_later_answer_on_the_account(setup, tools, monkeypatch):
    s = setup
    secret = "p@ss word/42"
    tools.secret = lambda conn, vault, aid, field, **kwargs: {"username": "robot", "password": secret, "code": CODE}[
        field
    ]
    thread, box = call(s, s.ops, "fill", {"field": "password"})
    serve(s, s.host)
    assert done(thread, box).status_code == 200
    # A later read after a "show password" click, and a form sent by GET: the value and its URL forms are gone.
    from urllib.parse import quote, quote_plus

    thread, box = call(s, s.ops, "read")
    serve(
        s,
        s.host,
        text=f"Password {secret} for robot",
        title=f"Hi {secret}",
        url=f"https://acme.example/login?pw={quote_plus(secret)}&again={quote(secret, safe='')}",
    )
    r = done(thread, box)
    body = r.json()
    assert r.status_code == 200 and secret not in r.text and quote_plus(secret) not in r.text
    assert (
        quote(secret, safe="") not in r.text and body["text"] == f"Password {bc.MASK} for robot"
    )  # username not typed
    assert body["title"] == f"Hi {bc.MASK}"
    # A later refusal's detail, and the person's live view's page address.
    thread, box = call(s, s.ops, "click", {"ref": "e1"})
    [cmd] = collect(s, s.host)
    report(s, s.host, cmd, ok=False, error="stale_ref", text=f"gone {secret}")
    r = done(thread, box)
    assert r.status_code == 409 and secret not in r.text
    import base64

    watch = {}
    thread = threading.Thread(
        target=lambda: watch.setdefault("r", s.api.get(f"/api/v2/{ACCOUNT}/frame", headers=headers()))
    )
    thread.start()
    [cmd] = collect(s, s.host)
    report(s, s.host, cmd, image=base64.b64encode(JPEG).decode(), url=f"https://acme.example/?pw={quote_plus(secret)}")
    thread.join(20)
    assert quote_plus(secret) not in watch["r"].headers["x-page-url"]
    assert secret not in repr(bc.MAILBOX.sessions) and quote_plus(secret) not in repr(bc.MAILBOX.frames)
    # Ten minutes on, the value is no longer kept.
    monkeypatch.setattr(bc, "SENT_KEEP", 0.0)
    assert bc.MAILBOX.scrub("acct1", secret) == secret and "acct1" not in bc.MAILBOX.sent


def test_needs_you_clears_on_done_on_giving_back_and_on_the_bots_next_command(setup, tools):
    s = setup
    task = s.tasks["ops"]

    def ask():
        return post(
            s.api, "browser/needs-you", {"site": "acme.example", "line": "Please sign in."}, token=s.ops["token"]
        )

    def state():
        with s.api.app.state.store.read() as c:
            return bc.session_view("acct1", c)["state"]

    rid = ask()["requirement_id"]
    assert state() == "needs_you"
    post(s.api, f"tasks/{task['id']}/asks/{rid}/answer", {"value": "Done"})
    assert state() == "unknown"
    ask()
    assert state() == "needs_you"
    post(s.api, ACCOUNT + "/control", {"take": True})
    post(s.api, ACCOUNT + "/control", {"take": False})
    assert state() == "unknown"
    ask()
    thread, box = call(s, s.ops, "read")
    serve(s, s.host, text="Page", signed_in=True)
    assert done(thread, box).status_code == 200
    assert bc.session_view("acct1")["state"] == "in_use" and bc.MAILBOX.facts("acct1").state == "unknown"


def test_a_bots_command_never_takes_a_persons_control_even_under_the_persons_id(setup, tools):
    """The Assistant drives the browser under its person's id: its commands are a bot's, so a person's control holds."""
    ana = {"subject": "human:ana", "kind": "person", "id": "ana", "name": "Ana", "initials": "A"}
    box = bc.MAILBOX
    box.take("acct1", "human:ana", ana, person=True)
    with pytest.raises(Problem) as refused:
        box.take("acct1", "human:ana", ana)
    assert refused.value.code == "person_in_control"
    assert box.release("acct1", "human:ana") is False  # the Assistant's close leaves the person's control
    assert box.holder("acct1").person is True
    assert box.release("acct1", "human:ana", person=True) is True
    box.take("acct1", "human:ana", ana)  # the Assistant's hold, then the person takes it over
    box.take("acct1", "human:ana", ana, person=True)
    assert box.holder("acct1").person is True


def test_a_caller_that_goes_away_leaves_nothing_queued(setup, tools, monkeypatch):
    import asyncio

    monkeypatch.setattr(bc, "GONE_CHECK", 0.05)

    class Caller:
        def __init__(self, gone):
            self.state = types.SimpleNamespace()
            self.gone = gone

        async def is_disconnected(self):
            return self.gone

    async def go():
        loop = bc.MAILBOX.bind()
        spot = bc.Place(s.host["runner_id"], "Host Mac", True)
        cancelled = bc._new(loop, spot, tools._picked(), "read", {})
        bc.MAILBOX.enqueue(cancelled)
        waiting = asyncio.create_task(bc.wait_reply(Caller(False), cancelled, 30))
        await asyncio.sleep(0.1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert cancelled.reply.done() and queued() == []
        left = bc._new(loop, spot, tools._picked(), "read", {})
        bc.MAILBOX.enqueue(left)
        with pytest.raises(Problem) as gone:
            await bc.wait_reply(Caller(True), left, 30)
        assert gone.value.code == "gone" and left.reply.done() and queued() == []

    s = setup
    asyncio.run(go())


def test_a_retried_read_is_refused_not_replayed_and_the_retry_cache_is_capped(setup, tools, monkeypatch):
    s = setup

    def send(verb, key):
        return s.api.post(
            "/api/v2/browser/commands",
            json={"site": "acme.example", "verb": verb, "args": {}},
            headers=headers(s.ops["token"], key),
        )

    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", send("read", "k-read")))
    thread.start()
    serve(s, s.host, text="Big page")
    assert done(thread, box).json()["text"] == "Big page"
    again = send("read", "k-read")
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_ran" and queued() == []
    assert "Big page" not in repr(bc.MAILBOX.recent)
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", send("back", "k-back")))
    thread.start()
    serve(s, s.host)
    first = done(thread, box).json()
    assert send("back", "k-back").json() == first  # a small answer is replayed
    import asyncio

    monkeypatch.setattr(bc, "RECENT_MAX", 3)

    async def fill():
        for i in range(10):
            bc.MAILBOX.recent_outcome("bot:ops", f"key-{i}")
        return len(bc.MAILBOX.recent)

    assert asyncio.run(fill()) <= 3


def test_a_poll_left_open_by_a_stopped_runner_never_takes_the_restarted_runners_command(setup, tools):
    s = setup
    old = {}
    first = threading.Thread(target=lambda: old.update(cmds=collect(s, s.host, wait=10)))
    first.start()
    time.sleep(0.3)
    new = {}
    second = threading.Thread(target=lambda: new.update(cmds=collect(s, s.host, wait=10)))
    second.start()
    first.join(5)
    assert not first.is_alive() and old["cmds"] == []
    thread, box = call(s, s.ops, "read")
    second.join(10)
    assert not second.is_alive() and [c["verb"] for c in new["cmds"]] == ["read"]
    report(s, s.host, new["cmds"][0], text="Reports")
    assert done(thread, box).status_code == 200


def test_scrub_report_also_covers_a_short_current_fill_value():
    """H: the single report scrubber covers values too short to remember across later reads."""
    box = bc.Mailbox()
    value = "xyz"
    box.remember("account", "password", value)
    report = box.scrub_report(
        "account", bc.BrowserReport(ok=True, text=value, title=value, url=value, error=value), value
    )
    assert all(value not in str(getattr(report, field)) for field in ("text", "title", "url", "error"))
