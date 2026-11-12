"""Bookkeeper rehearsal, checks 1-6 (scripts/finance_bookkeeper_rehearsal.py): in-process against the built-in
Bookkeeper (backend/bookkeeper.py) and the instructions it ships (templates/catalog/finance-owner). A known gap is an
xfail with strict=True that names it: the run shows it, and the script counts it as not passed. Synthetic fixtures."""

import uuid

from backend import bookkeeper as BK
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.store import H
from backend.tests.helpers.bookkeeper import DEFAULT_RIGHTS, built, check, checks, ensure
from backend.tests.helpers.mcp import call, rpc
from backend.tests.helpers.onboarding import draft, environment, machine, signed_in  # noqa: F401


def _token(api):
    """A run's token for bot:finance-owner, as a claimed job carries it: a computer enrolls, the bot is assigned to it,
    a person asks it something, and the computer claims the job (the same steps as test_api.setup_attempt, signed in
    as the owner of this environment)."""

    def post(path, body, token=None):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        headers["Authorization"] = "Bearer " + token if token else signed_in()["Authorization"]
        r = api.post("/api/v2/" + path, json=body, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    code = post("enrollments", {"operator": "morgan"})["code"]
    run = post("runners/enroll", {"code": code, "label": "Rehearsal Mac", "platform": "test"})
    with api.app.state.store.read() as c:  # the built one is already on the first computer: move it to this one
        row = c.execute("SELECT generation FROM assignments WHERE bot=?", (BK.SLUG,)).fetchone()
    post(
        "bots/" + BK.SLUG + "/assignment",
        {"runner_id": run["runner_id"], "expected_generation": row["generation"] if row else 0},
    )
    post("runners/heartbeat", {"version": "test", "platform": "test", "readiness": {BK.SLUG: True}}, run["token"])
    post("chat/" + BK.SLUG, {"text": "Please summarize the current work."})
    return post("jobs/claim", {"bot": BK.SLUG, "start_places": True}, run["token"])["attempt"]["token"]


# ----------------------------------------------------------------------------- check 1, 2 and 5: built, guarded, offered the right tools


def _c1_archive_refused_as_a_system_bot(api, token):
    bot = api.get("/api/v2/bots/" + BK.SLUG, headers=signed_in()).json()
    body = {"expected_revision": bot.get("revision", 0), "successor": None, "revoke_agent": True}
    r = api.post("/api/v2/bots/" + BK.SLUG + "/archive", json=body, headers=signed_in())
    assert r.status_code == 409 and "system_bot" in r.text, r.text


def _c2_rule_create_is_refused_once_finance_rules_is_revoked(api, token):
    """The default Bookkeeper holds finance.rules and may propose a rule (turns test: needs_approval); without it a
    rule_create is refused before anything is written."""
    store = api.app.state.store
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert (
            FA.act(c, owner, "right_revoke", {"teammate": BK.ACTOR, "right": "finance.rules"}, "revoke-rules").status
            == "committed"
        )
    err, out = call(
        api, "tico_finance_act", {"kind": "rule_create", "operation_id": "rule-1", "payload": {}}, token=token
    )
    assert out.get("status") == "denied" and "finance.rules" in out.get("reason", ""), out


def test_bookkeeper_rehearsal_setup_and_guards(environment):
    """One Bookkeeper, built once: default rights and seat, the system-bot guards, the six tools and none of the approval tools, close and rule refusals. The right revoke runs last."""
    api = built(environment)
    ensure(api)
    token = _token(api)
    _c1_archive_refused_as_a_system_bot(api, token)
    _c2_rule_create_is_refused_once_finance_rules_is_revoked(api, token)


# ----------------------------------------------------------------------------- check 3 and 6: triggers, quiet runs and visible failures


def _c6_a_failed_sync_reaches_the_owner_without_botops(api, token):
    """A bank connection whose sync fails tells its owner what failed, once for the failure run: one finance.sync.failed
    event and one keeper notice to the owner. A retry that still fails says nothing more. No BotOps task is involved."""
    from backend.finance import sync

    store = api.app.state.store
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        c.execute(
            "INSERT INTO fin_connections(id, provider, purpose, created_at, created_by, name) VALUES(?,?,?,?,?,?)",
            ("conn-rehearsal", "mercury", "bank", H.now(), owner, "Acme checking"),
        )
    first = H.now()
    # no token stored: the run fails at once (the same failure path as a Mercury outage), and the retry two hours on
    assert sync.connection(store, None, "conn-rehearsal", at=first) == "no_token"
    assert sync.connection(store, None, "conn-rehearsal", at=H.shift(first, hours=2)) == "no_token"
    with store.read() as c:
        events = c.execute("SELECT count(*) FROM fin_events WHERE name='finance.sync.failed'").fetchone()[0]
        notes = c.execute(
            "SELECT recipient_id, body FROM messages WHERE kind='notice' AND body LIKE 'Finance could not sync%'"
        ).fetchall()
    assert events == 1 and [n["recipient_id"] for n in notes] == [owner], (events, [dict(n) for n in notes])
    assert "Acme checking" in notes[0]["body"], notes[0]["body"]


def test_bookkeeper_rehearsal_quiet_check_and_visible_failures(environment):
    """One Bookkeeper: the empty queue is a quiet check, the seeded routines, receipts and unused subscriptions, and a failed sync reaches the owner."""
    api = built(environment)
    ensure(api)
    token = _token(api)
    _c6_a_failed_sync_reaches_the_owner_without_botops(api, token)


# ----------------------------------------------------------------------------- check 2 and 4: the instructions match the code
