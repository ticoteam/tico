"""Grant matrix 1–12: real vault and HTTP routes, with token-authenticated fake computers, never Chrome."""

import threading
import time
import asyncio
from types import SimpleNamespace

import pytest

from backend import browser_commands as B
from backend import website_tools as W
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.helpers.api import assign, claim, get, headers, post, put, ready, runner
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.botops_parity import act
from backend.tests.helpers.botops_confirm_cards import confirm
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.member_bots import botops, turn  # noqa: F401 (fixture)

SITE = "accounts.acme.example"


@pytest.fixture
def website(api):
    setup(api)
    tid = post(api, "website-tools", {"name": "Acme", "sites": [SITE]})["id"]
    return SimpleNamespace(api=api, tid=tid, store=api.app.state.store)


def add(s, holder=None, token="ana-test"):
    # The value is synthetic, never included in parametrization IDs or assertion diagnostics.
    result = s.api.post(
        f"/api/v2/website-tools/{s.tid}/accounts",
        json={"username": "", "password": "synthetic", "holder": holder, "all_bots": False},
        headers=headers(token),
    )
    assert result.status_code == 200
    return result.json()["id"]


def share(s, aid, subject, on=True, token="ana-test", expected=200):
    return put(s.api, f"website-tools/{s.tid}/accounts/{aid}/access/{subject}", {"on": on}, token, expected=expected)


def identity(actor):
    return Identity(actor, "bot" if actor.startswith("bot:") else "owner" if actor == "human:ana" else "human")


def pick(s, actor, site=None):
    with s.store.read() as c:
        return W.pick(c, identity(actor), site or s.tid)


@pytest.mark.parametrize("holder", ["human:cara", "bot:ops"])
def test_own_accounts_are_only_usable_by_the_holder_even_with_a_stray_grant(website, holder):
    """1: neither the owner, another person nor the owner's bot inherits an own login."""
    s = website
    aid = add(s, holder)
    assert pick(s, holder)["account_id"] == aid
    for other in ("human:ana", "human:ben", "bot:finance"):
        with pytest.raises(Problem) as refused:
            pick(s, other)
        assert (refused.value.status, refused.value.code) == (403, "no_account")
        with s.store.read() as c:
            assert not W.can_use(c, other, W._account(c, s.tid, aid))
    assert share(s, aid, "bot:finance", expected=409)["error"]["code"] == "own_account"
    # Even a stale legacy grant cannot make the account shared.
    with s.store.transaction() as c:
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,?,?,?)",
            (H.new_id(), aid, "bot:finance", "human:ana", H.now()),
        )
    with pytest.raises(Problem) as refused:
        pick(s, "bot:finance")
    assert refused.value.code == "no_account"


@pytest.mark.parametrize("subject", ["human:cara", "bot:ops"])
def test_shared_grant_and_revoke_are_effective_immediately(website, subject):
    """2: individual grants work, and revoking one never grants another teammate access."""
    s = website
    aid = add(s)
    share(s, aid, subject)
    assert pick(s, subject)["account_id"] == aid
    share(s, aid, subject, on=False)
    with pytest.raises(Problem) as refused:
        pick(s, subject)
    assert (refused.value.status, refused.value.code) == (403, "no_account")


@pytest.mark.parametrize("subject", ["team:engineering", "computers", "human:missing", "bot:missing"])
def test_accounts_cannot_be_granted_to_a_team_computer_or_unknown_teammate(website, subject):
    """2: a team audience controls visibility; account access names a registered person or bot."""
    aid = add(website)
    assert share(website, aid, subject, expected=422)["error"]["code"] == "subject"


def test_pick_prefers_own_then_oldest_shared_with_a_stable_tie_break(website):
    """3: choosing by site, URL, name and id follows the same rule."""
    s = website
    first, second = add(s), add(s)
    with s.store.transaction() as c:
        c.execute("UPDATE credentials SET created_at=? WHERE tool_id=?", ("2026-01-01T00:00:00Z", s.tid))
    for aid in (second, first):
        share(s, aid, "bot:ops")
    for site in (s.tid, "ACME", SITE, "https://" + SITE + "/login"):
        assert pick(s, "bot:ops", site)["account_id"] == min(first, second)
    own = add(s, "bot:ops")
    assert pick(s, "bot:ops")["account_id"] == own
    with pytest.raises(Problem) as refused:
        pick(s, "bot:finance")
    assert (refused.value.status, refused.value.code) == (403, "no_account")
    assert "ask an admin" in refused.value.detail


def test_narrowing_an_audience_preserves_the_account_grant(website):
    """6: visibility narrowing never silently revokes a grant; revoke access explicitly."""
    s = website
    aid = add(s)
    share(s, aid, "bot:ops")
    response = s.api.patch(f"/api/v2/website-tools/{s.tid}", json={"audience": {"people": ["ana"]}}, headers=headers())
    assert response.status_code == 200
    assert pick(s, "bot:ops")["account_id"] == aid
    share(s, aid, "bot:ops", on=False)
    with pytest.raises(Problem) as refused:
        pick(s, "bot:ops")
    assert (refused.value.status, refused.value.code) == (404, "no_such_site")


def test_member_can_delegate_only_to_their_bot_and_the_parent_revoke_reaches_it(website):
    """2,5: a member's bot uses its own grant, even on someone else's computer or in another team."""
    s = website
    aid = add(s)
    with s.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara',created_by='human:cara' WHERE bot='ops'")
    share(s, aid, "human:cara")
    share(s, aid, "bot:ops", token="cara-test")
    assert pick(s, "bot:ops")["account_id"] == aid
    share(s, aid, "bot:finance", token="cara-test", expected=403)
    share(s, aid, "human:cara", on=False)
    with pytest.raises(Problem) as refused:
        pick(s, "bot:ops")
    assert refused.value.code == "no_account"


def test_credential_admin_changes_apply_without_restarting(website):
    """7: removing admin rights stops shared add, replace and reveal on the very next request."""
    s = website
    aid = add(s)
    s.store.settings.credential_admins = ("ana@acme.example",)
    for method, path, body in (
        ("POST", f"website-tools/{s.tid}/accounts", {"password": "synthetic"}),
        ("PATCH", f"website-tools/{s.tid}/accounts/{aid}", {"password": "synthetic"}),
        ("POST", f"credentials/{aid}/reveal", {}),
    ):
        response = s.api.request(method, "/api/v2/" + path, json=body, headers=headers("ben-test"))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
    # A person's own account remains theirs even without credential-admin rights.
    own = add(s, "human:ben", token="ben-test")
    assert pick(s, "human:ben")["account_id"] == own


def test_grants_are_persistent_and_holds_are_not(website):
    """8: a new connection and fresh mailbox retain grants, while a restart forgets only transient holds."""
    s = website
    aid = add(s)
    share(s, aid, "bot:ops")
    with s.store.read() as c:
        who = B.who_of(c, "bot:ops")
    B.MAILBOX.take(aid, "bot:ops", who)
    B.MAILBOX.reset()
    assert B.MAILBOX.holder(aid) is None
    from fastapi.testclient import TestClient
    from backend.app import create_app

    with TestClient(create_app(s.store.settings)) as restarted:
        with restarted.app.state.store.read() as c:
            assert W.pick(c, identity("bot:ops"), s.tid)["account_id"] == aid


@pytest.fixture
def computers(website):
    s = website
    s.aid = add(s)
    share(s, s.aid, "bot:ops")
    s.host = runner(s.api, label="Host")
    ready(s.api, s.host, [], capabilities=["browser_v1"])
    post(s.api, f"runners/{s.host['runner_id']}/browser-host", {"on": True})
    s.own = runner(s.api, operator="ben", label="Own")
    with s.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara',created_by='human:cara',team='product' WHERE bot='ops'")
    assign(s.api, s.own, "ops")
    ready(s.api, s.own, ["ops"], capabilities=["browser_v1"])
    s.task = post(s.api, "tasks", {"owner": "ops", "title": "Read the report", "body": "Read Acme."})
    s.turn = claim(s.api, s.own, "ops")
    for machine in (s.host, s.own):
        get(s.api, "runner-browser-commands?wait=0", machine["token"])
    facts = B.MAILBOX.facts(s.aid)
    facts.where, facts.runner_id, facts.computer = "central", s.host["runner_id"], "Host"
    facts.tool_id, facts.sites = s.tid, [SITE]
    return s


def command(s, verb="read", token=None):
    return s.api.post(
        "/api/v2/browser/commands", json={"site": s.tid, "verb": verb}, headers=headers(token or s.turn["token"])
    )


def begin(s, verb="open", token=None):
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("response", command(s, verb, token)))
    thread.start()
    return thread, box


def prepared(s, machine):
    """Wait for preparation, without delivering a command or relying on a scheduler delay."""
    import time

    deadline = time.monotonic() + 3
    while not B.MAILBOX.queued(machine["runner_id"]) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert B.MAILBOX.queued(machine["runner_id"])


def end(thread, box, status=200, code=None):
    thread.join(10)
    assert not thread.is_alive()
    response = box["response"]
    assert response.status_code == status, response.status_code
    if code:
        assert response.json()["error"]["code"] == code
    return response


def poll(s, machine, wait=0):
    return get(s.api, f"runner-browser-commands?wait={wait}", machine["token"])["commands"]


def report(s, machine, cmd, expected=200):
    return post(
        s.api,
        f"runner-browser-commands/{cmd['id']}/report",
        {"ok": True, "signed_in": True},
        machine["token"],
        expected=expected,
    )


@pytest.mark.parametrize("state", ["online", "offline", "no_chrome", "revoked", "unset"])
def test_host_placement_and_fallback_use_only_the_authorized_computer(computers, state):
    """5,10: runner operator and bot team do not replace the bot's identity or grants."""
    s = computers
    with s.store.transaction() as c:
        if state == "offline":
            B.MAILBOX.polled_at[s.host["runner_id"]] = time.monotonic() - B.POLL_FRESH - 1
        elif state == "no_chrome":
            c.execute("UPDATE runner_states SET capabilities='[]' WHERE runner_id=?", (s.host["runner_id"],))
        elif state == "revoked":
            c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), s.host["runner_id"]))
        elif state == "unset":
            c.execute("DELETE FROM registry_metadata WHERE key=?", (B.KEY,))
    thread, box = begin(s)
    target = s.host if state == "online" else s.own
    [cmd] = poll(s, target, wait=3)
    assert cmd["account"] == s.aid
    report(s, target, cmd)
    assert end(thread, box).json()["where"] == ("central" if state == "online" else "own")


@pytest.mark.parametrize("change", ["revoke", "delete_account", "delete_tool", "legacy_revoke", "legacy_delete"])
def test_removing_access_closes_the_session_and_refuses_the_next_command(computers, change):
    """6: removal destroys cached frames/holds and sends a value-free close to the old computer."""
    s = computers
    thread, box = begin(s)
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    if change == "revoke":
        share(s, s.aid, "bot:ops", on=False)
    elif change == "legacy_revoke":
        with s.store.read() as c:
            gid = c.execute("SELECT id FROM credential_grants WHERE credential_id=?", (s.aid,)).fetchone()[0]
        post(s.api, f"credentials/{s.aid}/grants/{gid}/revoke", {})
    else:
        path = (
            f"credentials/{s.aid}"
            if change == "legacy_delete"
            else f"website-tools/{s.tid}" + (f"/accounts/{s.aid}" if change == "delete_account" else "")
        )
        assert s.api.delete("/api/v2/" + path, headers=headers()).status_code == 200
    assert B.session_view(s.aid)["state"] == "unknown"
    assert B.MAILBOX.holder(s.aid) is None
    [close] = poll(s, s.host, wait=3)
    assert close["verb"] == "close" and "value" not in close
    report(s, s.host, close)
    response = command(s, "open")
    assert response.status_code in (403, 404)
    assert response.json()["error"]["code"] == ("no_such_site" if change == "delete_tool" else "no_account")


@pytest.mark.parametrize("central", [False, True])
@pytest.mark.parametrize("delivered", [False, True])
def test_an_attempt_ending_mid_command_is_refused_before_delivery_or_report(computers, central, delivered):
    """11: the attempt must still run on its own computer, for central commands as well as fallback."""
    s = computers
    if not central:
        post(s.api, f"runners/{s.host['runner_id']}/browser-host", {"on": False})
    machine = s.host if central else s.own
    thread, box = begin(s)
    if delivered:
        [cmd] = poll(s, machine, wait=3)
    else:
        prepared(s, machine)
    with s.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id=?", (s.turn["id"],))
    if delivered:
        assert report(s, machine, cmd, expected=409)["error"]["code"] == "stale_lease"
    else:
        assert poll(s, machine) == []
    end(thread, box, 409, "stale_lease")


@pytest.mark.parametrize("state", ["paused", "archived"])
def test_a_paused_or_deleted_bot_cannot_use_an_existing_grant(computers, state):
    """5: the bot's current standing beats a previously issued attempt token."""
    s = computers
    with s.store.transaction() as c:
        c.execute("UPDATE bots SET state=? WHERE id='ops'", (state,))
    response = command(s)
    assert response.status_code == 409 and response.json()["error"]["code"] == "stale_lease"
    assert poll(s, s.host) == []


def test_the_assistant_uses_its_persons_grants_and_never_the_assistant_bots(website):
    """4: an Assistant turn uses Cara's own account, and cannot borrow an account granted only to its bot."""
    s = website
    shared, own = add(s), add(s, "human:cara")
    share(s, shared, "bot:assistant")
    machine, attempt = assistant_turn(s.api, "cara-test")
    ready(s.api, machine, ["assistant"], capabilities=["browser_v1"])
    get(s.api, "runner-browser-commands?wait=0", machine["token"])
    s.turn = attempt
    thread, box = begin(s)
    [cmd] = poll(s, machine, wait=3)
    assert cmd["account"] == own
    report(s, machine, cmd)
    end(thread, box)
    assert (
        s.api.delete(f"/api/v2/website-tools/{s.tid}/accounts/{own}", headers=headers("cara-test")).status_code == 200
    )
    response = command(s)
    assert response.status_code == 403 and response.json()["error"]["code"] == "no_account"


def test_botops_uses_the_requesters_rights_and_the_host_change_waits_for_confirmation(website, botops):
    """4: grants follow the requester's rights; choosing a browser host still requires their tap."""
    s = website
    aid = add(s)
    attempt = turn(s.api, botops, person="ana-test", text="Share the Acme account with ops")
    response = act(s.api, attempt, "PUT", f"website-tools/{s.tid}/accounts/{aid}/access/bot:ops", {"on": True})
    assert response.status_code == 200
    assert pick(s, "bot:ops")["account_id"] == aid
    host = runner(s.api, label="Host")
    response = act(s.api, attempt, "POST", f"runners/{host['runner_id']}/browser-host", {"on": True})
    assert response.status_code == 200 and response.json()["needs_confirm"]
    with s.store.read() as c:
        assert B.host_id(c) is None
    assert confirm(s.api, response.json(), "ana-test")["action"]["status"] == "done"
    with s.store.read() as c:
        assert B.host_id(c) == host["runner_id"]


@pytest.mark.parametrize("delivered", [False, True])
def test_revoked_commands_never_return_success_or_deliver_a_login(computers, delivered):
    """6: revoking an account cancels queued fills and rejects a late report after delivery."""
    s = computers
    thread, box = begin(s, "open")
    if delivered:
        [cmd] = poll(s, s.host, wait=3)
    else:
        prepared(s, s.host)
    share(s, s.aid, "bot:ops", on=False)
    end(thread, box, 403, "no_account")
    if delivered:
        assert report(s, s.host, cmd, expected=404)["error"]["code"] == "not_found"
    commands = poll(s, s.host)
    assert all(cmd["verb"] == "close" and "value" not in cmd for cmd in commands)


def test_an_unavailable_computer_does_not_requeue_cleanup_forever(computers):
    s = computers
    thread, box = begin(s, "open")
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    share(s, s.aid, "bot:ops", on=False)
    post(s.api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": []}, s.host["token"])
    assert poll(s, s.host) == []
    assert B.MAILBOX.queued(s.host["runner_id"]) == []


def test_cleanup_keeps_a_bounded_slot_when_the_computer_queue_is_full(monkeypatch):
    monkeypatch.setattr(B, "QUEUE_MAX", 1)

    async def run():
        box = B.Mailbox()
        loop = box.bind()
        picked = {"account_id": "account", "tool_id": "tool", "sites": [SITE]}
        ordinary = B._new(loop, B.Place("computer", "Host", True), picked, "read", {})
        cleanup = B._new(loop, B.Place("computer", "Host", True), picked, "close", {})
        cleanup.cleanup = True
        box.enqueue(ordinary)
        box.enqueue(cleanup)
        assert box.queued("computer") == [cleanup]
        outcome = ordinary.reply.result()
        assert isinstance(outcome, Problem) and outcome.code == "slow_down"

    asyncio.run(run())


def test_revoking_a_grant_that_does_not_exist_leaves_the_session_alone(computers):
    s = computers
    thread, box = begin(s, "open")
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    share(s, s.aid, "bot:finance", on=False, token="cara-test")
    assert B.session_view(s.aid)["state"] == "in_use"
    assert poll(s, s.host) == []


def test_a_cached_success_never_overrides_a_revoked_grant(computers):
    """6,9: idempotency protects against duplicate effects, never against current authorization."""
    s = computers
    box = {}

    def send():
        return s.api.post(
            "/api/v2/browser/commands",
            json={"site": s.tid, "verb": "back"},
            headers=headers(s.turn["token"], "same-command"),
        )

    thread = threading.Thread(target=lambda: box.setdefault("response", send()))
    thread.start()
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    share(s, s.aid, "bot:ops", on=False)
    response = send()
    assert response.status_code == 403 and response.json()["error"]["code"] == "no_account"


@pytest.mark.parametrize(
    "central_only,host,own,status,code",
    [
        (True, "offline", True, 409, "browser_host_offline"),
        (True, "no_chrome", True, 409, "browser_host_offline"),
        (True, "unset", True, 409, "browser_host_offline"),
        (False, "unset", False, 409, "no_browser"),
        (False, "offline", False, 409, "browser_host_offline"),
    ],
)
def test_placement_refusals_are_stable_and_actionable(computers, central_only, host, own, status, code):
    """9,10: clear refusal, stable status/code, no delivery to a different computer."""
    s = computers
    with s.store.transaction() as c:
        c.execute("UPDATE tools SET central_only=? WHERE id=?", (central_only, s.tid))
        if host == "unset":
            c.execute("DELETE FROM registry_metadata WHERE key=?", (B.KEY,))
        elif host == "offline":
            B.MAILBOX.polled_at[s.host["runner_id"]] = time.monotonic() - B.POLL_FRESH - 1
        else:
            c.execute("UPDATE runner_states SET capabilities='[]' WHERE runner_id=?", (s.host["runner_id"],))
        if not own:
            c.execute("UPDATE runner_states SET capabilities='[]' WHERE runner_id=?", (s.own["runner_id"],))
    response = command(s, "open")
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert poll(s, s.own) == [] and poll(s, s.host) == []


def test_host_change_rejects_an_old_report_and_the_next_command_uses_the_new_host(computers):
    """12: a report from a computer that stopped being the browser host cannot revive its session."""
    s = computers
    other = runner(s.api, label="New host")
    ready(s.api, other, [], capabilities=["browser_v1"])
    get(s.api, "runner-browser-commands?wait=0", other["token"])
    thread, box = begin(s)
    [old] = poll(s, s.host, wait=3)
    post(s.api, f"runners/{other['runner_id']}/browser-host", {"on": True})
    assert report(s, s.host, old, expected=404)["error"]["code"] == "not_found"
    end(thread, box, 409, "browser_host_offline")
    thread, box = begin(s)
    [cmd] = poll(s, other, wait=3)
    report(s, other, cmd)
    assert end(thread, box).json()["where"] == "central"
    assert B.session_view(s.aid)["computer"] == "New host"


def test_changing_a_host_closes_an_already_open_session_on_the_old_computer(computers):
    """12: an idle signed-in session is closed when the admin chooses a different browser host."""
    s = computers
    thread, box = begin(s, "open")
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    other = runner(s.api, label="New host")
    ready(s.api, other, [], capabilities=["browser_v1"])
    get(s.api, "runner-browser-commands?wait=0", other["token"])
    post(s.api, f"runners/{other['runner_id']}/browser-host", {"on": True})
    assert B.session_view(s.aid)["state"] == "unknown"
    [close] = poll(s, s.host, wait=3)
    assert close["verb"] == "close" and "value" not in close
    report(s, s.host, close)


def test_server_restart_drops_an_inflight_command_and_the_late_report(computers):
    """12: restart clears the mailbox, and the old computer's report cannot complete a new command."""
    s = computers
    thread, box = begin(s)
    [old] = poll(s, s.host, wait=3)
    B.MAILBOX.reset()
    for machine in (s.host, s.own):
        get(s.api, "runner-browser-commands?wait=0", machine["token"])
    end(thread, box, 409, "browser_host_offline")
    report(s, s.host, old, expected=404)
    thread, box = begin(s)
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)


def test_runner_restart_without_a_report_times_out_and_cannot_report_late(website):
    """12: model a runner losing an in-flight command; an expired deadline needs no wall-clock sleep."""
    s = website

    class Caller:
        state = SimpleNamespace()

        async def is_disconnected(self):
            return False

    async def run():
        loop = B.MAILBOX.bind()
        picked = {"account_id": "account", "tool_id": s.tid, "sites": [SITE]}
        cmd = B._new(loop, B.Place("computer", "Host", True), picked, "read", {})
        B.MAILBOX.enqueue(cmd)
        assert B.MAILBOX.hand_out("computer", {cmd.id}) == [cmd]
        with pytest.raises(Problem) as refused:
            await B.wait_reply(Caller(), cmd, 0)
        assert (refused.value.status, refused.value.code) == (504, "browser_timeout")
        assert cmd.id not in B.MAILBOX.inflight and not B.MAILBOX.queued("computer")
        assert cmd.reply.done()

    asyncio.run(run())


@pytest.mark.parametrize(
    "code",
    [
        "no_account",
        "no_such_site",
        "central_only",
        "not_on_site",
        "no_2fa",
        "no_password",
        "busy",
        "person_in_control",
        "invalid_request",
        "browser_host_offline",
        "no_browser",
        "slow_down",
        "stale_ref",
        "browser_error",
    ],
)
def test_http_cli_and_mcp_keep_the_same_refusal_sentence_and_code(computers, code, monkeypatch):
    """9: the shared CLI/MCP functions preserve actual HTTP refusals, including each status and next action."""
    from clients.commands import browser, logins
    from clients.tico import APIError

    s = computers
    verb, args = "read", {}
    if code == "no_account":
        share(s, s.aid, "bot:ops", on=False)
    elif code == "no_such_site":
        assert s.api.delete(f"/api/v2/website-tools/{s.tid}", headers=headers()).status_code == 200
    elif code == "central_only":
        with s.store.transaction() as c:
            c.execute("UPDATE tools SET central_only=1 WHERE id=?", (s.tid,))
        verb, args = "copy", {"field": "password"}
    elif code == "not_on_site":
        verb, args = "open", {"url": "https://other.example/"}
    elif code == "no_2fa":
        verb = "code"
    elif code == "no_password":
        with s.store.transaction() as c:
            c.execute("UPDATE credentials SET has_password=0 WHERE id=?", (s.aid,))
        verb, args = "fill", {"field": "password"}
    elif code in ("busy", "person_in_control"):
        with s.store.read() as c:
            who = B.who_of(c, "human:ana" if code == "person_in_control" else "bot:finance")
        B.MAILBOX.take(s.aid, who["subject"], who, person=code == "person_in_control")
    elif code == "invalid_request":
        verb = "fill"
    elif code == "browser_host_offline":
        with s.store.transaction() as c:
            c.execute("UPDATE tools SET central_only=1 WHERE id=?", (s.tid,))
            c.execute("UPDATE runner_states SET capabilities='[]' WHERE runner_id=?", (s.host["runner_id"],))
    elif code == "no_browser":
        verb = "open"
        with s.store.transaction() as c:
            c.execute("DELETE FROM registry_metadata WHERE key=?", (B.KEY,))
            c.execute("UPDATE runner_states SET capabilities='[]' WHERE runner_id=?", (s.own["runner_id"],))
    elif code == "slow_down":
        monkeypatch.setattr(W, "SECRET_RATE", 0)
        verb, args = "fill", {"field": "password"}
    if code in ("stale_ref", "browser_error"):
        thread, box = begin(s)
        [cmd] = poll(s, s.host, wait=3)
        post(
            s.api,
            f"runner-browser-commands/{cmd['id']}/report",
            {"ok": False, "error": "unexpected_error" if code == "browser_error" else code, "text": "untrusted detail"},
            s.host["token"],
        )
        response = end(thread, box, 409, code)
    else:
        response = s.api.post(
            "/api/v2/browser/commands",
            json={"site": s.tid, "verb": verb, "args": args},
            headers=headers(s.turn["token"]),
        )
    error = response.json()["error"]
    assert error["code"] == code
    assert response.status_code == {
        "no_account": 403,
        "no_such_site": 404,
        "central_only": 403,
        "not_on_site": 422,
        "invalid_request": 422,
        "slow_down": 429,
    }.get(code, 409)
    assert error["detail"].endswith(".")
    assert "untrusted detail" not in error["detail"]

    class HttpRefusal:
        def post(self, path, body, key=None):
            raise APIError(
                error["code"], error["detail"], response.status_code, error["retryable"], extra=error.get("extra")
            )

    # Both frontends call these command functions; a login delegates to the same browser transport.
    for run in (
        lambda: browser.browser_read(HttpRefusal(), {"site": s.tid}),
        lambda: logins.login_fill(HttpRefusal(), {"site": s.tid, "field": "password"}),
    ):
        with pytest.raises(APIError) as refused:
            run()
        assert (refused.value.code, refused.value.status, refused.value.detail) == (
            code,
            response.status_code,
            error["detail"],
        )


@pytest.mark.parametrize("where", ["central", "own"])
def test_browser_placement_stays_with_the_session_until_open(computers, where):
    """A: recovering a host never moves an own sign-in, and a stale session never silently falls back."""
    s = computers
    if where == "own":
        B.MAILBOX.polled_at[s.host["runner_id"]] = time.monotonic() - B.POLL_FRESH - 1
    machine = s.own if where == "own" else s.host
    thread, box = begin(s)
    [cmd] = poll(s, machine, wait=3)
    report(s, machine, cmd)
    end(thread, box)
    ready(s.api, s.host, [], capabilities=["browser_v1"])
    get(s.api, "runner-browser-commands?wait=0", s.host["token"])
    thread, box = begin(s, "read")
    [cmd] = poll(s, machine, wait=3)
    assert cmd["verb"] == "read"
    report(s, machine, cmd)
    assert end(thread, box).json()["where"] == where
    B.MAILBOX.polled_at[machine["runner_id"]] = time.monotonic() - B.POLL_FRESH - 1
    response = command(s)
    assert response.status_code == 409 and "open the website again" in response.json()["error"]["detail"]
    assert poll(s, s.own) == [] and poll(s, s.host) == []


def person_request(s, path, token="ana-test", body=None):
    box = {}

    def send():
        box["response"] = s.api.request(
            "GET" if body is None else "POST", "/api/v2/" + path, json=body, headers=headers(token)
        )

    thread = threading.Thread(target=send)
    thread.start()
    return thread, box


def test_needs_you_view_and_input_follow_an_own_computer_session(computers):
    """B: the asked person helps the same own-computer browser, then Done lets the bot continue there."""
    import base64

    s = computers
    B.MAILBOX.polled_at[s.host["runner_id"]] = time.monotonic() - B.POLL_FRESH - 1
    thread, box = begin(s)
    [cmd] = poll(s, s.own, wait=3)
    post(
        s.api,
        f"runner-browser-commands/{cmd['id']}/report",
        {"ok": True, "url": "https://" + SITE + "/code"},
        s.own["token"],
    )
    end(thread, box)
    rid = post(s.api, "browser/needs-you", {"site": s.tid, "line": "Please finish sign-in."}, s.turn["token"])[
        "requirement_id"
    ]
    with s.store.read() as c:
        person = c.execute("SELECT person_id FROM requirements WHERE id=?", (rid,)).fetchone()[0]
    token = person.removeprefix("human:") + "-test"
    path = f"website-tools/{s.tid}/accounts/{s.aid}/browser"
    thread, box = person_request(s, path, token, {})
    [cmd] = poll(s, s.own, wait=3)
    assert (
        cmd["verb"] == "open"
        and cmd["args"] == {"url": "https://" + SITE + "/code", "keep": True}
        and cmd["where"] == "own"
    )
    report(s, s.own, cmd)
    assert end(thread, box).json()["where"] == "own"
    post(s.api, path + "/control", {"take": True}, token)
    thread, box = person_request(s, path + "/input", token, {"kind": "key", "key": "Enter"})
    [cmd] = poll(s, s.own, wait=3)
    assert cmd["verb"] == "input_key"
    report(s, s.own, cmd)
    end(thread, box)
    thread, box = person_request(s, path + "/frame", token)
    [cmd] = poll(s, s.own, wait=3)
    assert cmd["verb"] == "screenshot"
    post(
        s.api,
        f"runner-browser-commands/{cmd['id']}/report",
        {"ok": True, "image": base64.b64encode(b"\xff\xd8\xff\xd9").decode(), "width": 32, "height": 16},
        s.own["token"],
    )
    assert end(thread, box).headers["content-type"] == "image/jpeg"
    post(s.api, f"tasks/{s.task['id']}/asks/{rid}/answer", {"value": "Done"}, token)
    post(s.api, path + "/control", {"take": False}, token)
    thread, box = begin(s, "read")
    [cmd] = poll(s, s.own, wait=3)
    report(s, s.own, cmd)
    end(thread, box)
    assert B.session_view(s.aid)["computer"] == "Own"


def test_opening_a_persons_view_keeps_the_page_and_supplies_a_recovery_url(computers):
    """C: viewing a two-step sign-in never navigates it back to its old address."""
    s = computers
    facts = B.MAILBOX.facts(s.aid)
    facts.url = "https://" + SITE + "/second-step"
    thread, box = person_request(s, f"website-tools/{s.tid}/accounts/{s.aid}/browser", body={})
    [cmd] = poll(s, s.host, wait=3)
    assert cmd["verb"] == "open" and cmd["args"] == {"url": "https://" + SITE + "/second-step", "keep": True}
    report(s, s.host, cmd)
    end(thread, box)
    assert B.MAILBOX.facts(s.aid).url.endswith("/second-step")


def test_a_refused_person_view_leaves_the_bots_session_intact(computers):
    """D: losing a viewer's permission cancels only that viewer's queued command."""
    s = computers
    thread, box = begin(s)
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    before = B.MAILBOX.facts(s.aid)
    thread, box = person_request(s, f"website-tools/{s.tid}/accounts/{s.aid}/browser", "ben-test", {})
    prepared(s, s.host)
    s.store.settings.credential_admins = ("ana@acme.example",)
    assert poll(s, s.host) == []
    end(thread, box, 404, "not_found")
    assert B.MAILBOX.facts(s.aid) is before
    assert B.MAILBOX.holder(s.aid).actor == "bot:ops"
    thread, box = begin(s, "read")
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)


@pytest.mark.parametrize("error", ["stale_ref", "not_on_site", "not_a_password_field"])
def test_refused_fills_do_not_burn_the_successful_secret_limit(computers, monkeypatch, error):
    """E: a refused fill refunds its reservation, while the first successful fill uses the one allowed slot."""
    s = computers
    monkeypatch.setattr(W, "SECRET_RATE", 1)

    def fill():
        box = {}
        thread = threading.Thread(
            target=lambda: box.setdefault(
                "response",
                s.api.post(
                    "/api/v2/browser/commands",
                    json={"site": s.tid, "verb": "fill", "args": {"field": "password"}},
                    headers=headers(s.turn["token"]),
                ),
            )
        )
        thread.start()
        return thread, box

    for _ in range(2):
        thread, box = fill()
        [cmd] = poll(s, s.host, wait=3)
        post(s.api, f"runner-browser-commands/{cmd['id']}/report", {"ok": False, "error": error}, s.host["token"])
        end(thread, box, 422 if error != "stale_ref" else 409, error)
        assert not W._spent[s.aid]
    thread, box = fill()
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    thread, box = fill()
    end(thread, box, 429, "slow_down")


def test_command_deadline_shrinks_in_the_payload_and_matches_the_wire_contract():
    """F: queue time consumes the command's relative execution budget before a runner sees it."""
    import json
    from pathlib import Path
    from scripts.check_wire_contract import errors

    async def run():
        loop = asyncio.get_running_loop()
        cmd = B._new(
            loop,
            B.Place("computer", "Host", True),
            {"account_id": "account", "tool_id": "tool", "sites": [SITE]},
            "read",
            {},
        )
        assert cmd.payload(now=cmd.deadline_at - 7)["expires_in"] == 7
        assert cmd.payload(now=cmd.deadline_at + 1)["expires_in"] == 0
        wire = json.loads((Path(__file__).parents[2] / "contracts/runner/wire.json").read_text())
        schema = {"$ref": "#/$defs/BrowserRelayCommand", "$defs": wire["$defs"]}
        assert errors(cmd.payload(now=cmd.deadline_at - 7), schema, wire) == []

    asyncio.run(run())


def test_control_handoff_cancels_queued_bot_commands_and_returned_person_input(computers):
    """1: old queued work never crosses a person's take-control/give-back boundary."""
    s = computers
    path = f"website-tools/{s.tid}/accounts/{s.aid}/browser"
    thread, box = begin(s)
    prepared(s, s.host)
    post(s.api, path + "/control", {"take": True})
    end(thread, box, 409, "person_in_control")
    assert poll(s, s.host) == []
    thread, box = person_request(s, path + "/input", body={"kind": "key", "key": "Enter"})
    prepared(s, s.host)
    post(s.api, path + "/control", {"take": False})
    end(thread, box, 409, "no_control")
    assert poll(s, s.host) == []


def test_delivery_rechecks_the_current_control_holder():
    """1: an authorization snapshot taken before a handoff cannot deliver an old sender's command."""

    async def run():
        box = B.Mailbox()
        loop = box.bind()
        bot = {"subject": "bot:ops", "kind": "bot", "id": "ops", "name": "Ops", "initials": "O"}
        person = {"subject": "human:ana", "kind": "person", "id": "ana", "name": "Ana", "initials": "A"}
        box.take("account", "bot:ops", bot)
        cmd = B._new(
            loop,
            B.Place("computer", "Host", True),
            {"account_id": "account", "tool_id": "tool", "sites": [SITE]},
            "read",
            {},
            attempt_id="attempt",
            by=bot,
            identity=Identity("bot:ops", "bot"),
        )
        box.enqueue(cmd)
        allowed_before_handoff = {cmd.id}
        # Model a holder change after the async permission check, before the atomic hand-out step.
        box.holds["account"] = B.Hold("human:ana", person, True, time.monotonic() + 30)
        assert box.hand_out("computer", allowed_before_handoff) == []
        outcome = cmd.reply.result()
        assert isinstance(outcome, Problem) and outcome.code == "person_in_control"

    asyncio.run(run())


def test_browser_polling_alone_decides_computer_liveness(computers):
    """2: browser polls survive a missed heartbeat, and advertised Chrome without a poll is offline."""
    s = computers
    with s.store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET last_seen_at=? WHERE runner_id=?",
            (H.shift(H.now(), seconds=-200), s.host["runner_id"]),
        )
    thread, box = begin(s)
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    assert end(thread, box).json()["where"] == "central"
    ready(s.api, s.host, [], capabilities=["browser_v1"])
    B.MAILBOX.polled_at.pop(s.host["runner_id"])
    with s.store.read() as c:
        assert B.host_view(c)["online"] is False
    thread, box = begin(s)
    [cmd] = poll(s, s.own, wait=3)
    report(s, s.own, cmd)
    assert end(thread, box).json()["where"] == "own"


def test_an_offline_report_preserves_the_session_and_its_hold(computers):
    """3: a temporary transport gap refuses one command, then the same browser continues after polling resumes."""
    s = computers
    thread, box = begin(s)
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)
    facts = B.MAILBOX.facts(s.aid)
    thread, box = begin(s, "read")
    [cmd] = poll(s, s.host, wait=3)
    held = B.MAILBOX.holder(s.aid)
    B.MAILBOX.polled_at[s.host["runner_id"]] = time.monotonic() - B.POLL_FRESH - 1
    assert report(s, s.host, cmd, expected=409)["error"]["code"] == "browser_host_offline"
    end(thread, box, 409, "browser_host_offline")
    assert B.MAILBOX.facts(s.aid) is facts and B.MAILBOX.holder(s.aid) is held
    assert poll(s, s.host) == []
    thread, box = begin(s, "read")
    [cmd] = poll(s, s.host, wait=3)
    report(s, s.host, cmd)
    end(thread, box)


def test_a_restarted_browser_has_an_open_again_refusal(computers):
    """4: a fresh Chrome refuses a non-open verb with a stable, actionable restart code."""
    s = computers
    thread, box = begin(s, "read")
    [cmd] = poll(s, s.host, wait=3)
    post(s.api, f"runner-browser-commands/{cmd['id']}/report", {"ok": False, "error": "session_lost"}, s.host["token"])
    response = end(thread, box, 409, "session_lost")
    assert response.json()["error"]["detail"] == "This browser restarted; open the website again."


@pytest.mark.parametrize("reported", [False, True])
def test_open_timeout_tells_the_caller_to_open_again(reported):
    """5: an unfinished open cannot be recovered by a read on a session that may not exist."""

    class Caller:
        state = SimpleNamespace()

        async def is_disconnected(self):
            return False

    async def run():
        loop = B.MAILBOX.bind()
        cmd = B._new(
            loop,
            B.Place("computer", "Host", True),
            {"account_id": "account", "tool_id": "tool", "sites": [SITE]},
            "open",
            {},
        )
        if reported:
            cmd.reply.set_result(B.BrowserReport(ok=False, error="browser_timeout"))
        with pytest.raises(Problem) as refused:
            await B.wait_reply(Caller(), cmd, 30 if reported else 0)
        assert refused.value.status == (409 if reported else 504) and "open the website again" in refused.value.detail
        assert "read the page" not in refused.value.detail

    asyncio.run(run())


@pytest.mark.parametrize("old_signed_in", [True, False, None])
def test_older_runner_sign_in_flags_never_become_session_state(computers, old_signed_in):
    """6: old report fields remain readable without claiming a sign-in state the browser cannot establish."""
    s = computers
    thread, box = begin(s)
    [cmd] = poll(s, s.host, wait=3)
    post(
        s.api, f"runner-browser-commands/{cmd['id']}/report", {"ok": True, "signed_in": old_signed_in}, s.host["token"]
    )
    end(thread, box)
    B.MAILBOX.release(s.aid, "bot:ops")
    assert B.session_view(s.aid)["state"] == "unknown"
    assert get(s.api, f"website-tools/{s.tid}")["status"]["label"] == "Ready"


@pytest.mark.parametrize("length,masked", [(3, False), (4, True), (7, True)])
def test_typed_short_usernames_use_the_login_redaction_threshold(length, masked):
    """7: only a value Tico typed enters the redactor, with the same threshold as passwords and codes."""
    box = B.Mailbox()
    value = "user123"[:length]
    assert box.scrub("account", value) == value
    box.remember("account", "username", value)
    assert (box.scrub("account", value) == B.MASK) is masked
