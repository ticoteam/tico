"""History's Log (GET /api/v2/history, backend/history_log.py): every kind already recorded, filtered by teammate, team
and kind, never more than the reader may query through SQL, and the CSV export for the owner and the Admins."""
import csv
import io

from backend import history_log
from backend.store import H
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401


def seed(api):
    """One row of every kind: a run of ops, Cara's public task for ops, a private one of Ana's, ops' two send approvals
    (one granted, one denied), a credential the inbox revealed, an automation send kept as a draft, a permission change, and Cara's answer to ops."""
    store = api.app.state.store
    setup_attempt(api)                                   # ana's chat with ops, its run under way
    with store.transaction() as c:
        public = H.task_create(c, "human:cara", "Refresh the Acme pricing page", "", "bot:ops", lint=False, private=False)
        secret = H.task_create(c, "human:ana", "Acme layoffs plan", "", "human:ben", lint=False, private=True)
        H.task_update(c, "bot:ops", public["id"], status="doing", note="On it")
        asked = H.approval_request(c, "bot:ops", "send", {"to": "ben@acme.example", "cc": [], "subject": "Report",
                                                         "body_sha256": "a" * 64, "mailbox": "ops@acme.example"},
                                   task_id=secret["id"])
        H.approval_decide(c, "human:ana", asked["id"], "approved")
        other = H.approval_request(c, "bot:ops", "send", {"to": "cara@acme.example", "cc": [], "subject": "Invoice",
                                                         "body_sha256": "b" * 64, "mailbox": "ops@acme.example"},
                                   task_id=public["id"])
        H.approval_decide(c, "human:ana", other["id"], "declined")
        c.execute("INSERT INTO credentials(id,name,kind,created,updated,updated_by) VALUES('k1','=Stripe key','token',?,?,'ana')",
                  (H.now(), H.now()))
        H.event(c, "bot:inbox", "credential.revealed", "k1", {"attempt": None})
        H.event(c, "human:ana", "automation.send_off", "auto-1", {"run": "r1"})
        H.event(c, "human:ana", "bot.owners_changed", "ops", {"owners": ["ana"]})
        H.event(c, "human:ana", "message.say", "m1", {})        # not a kind the Log shows
    # Each answers ops in their own chat with it, which is theirs alone: not even the owner reads Cara's.
    for who, words in (("ana", "Send it Monday"), ("cara", "Yes, ship it")):
        question = post(api, "chat/ops", {"text": "Ready?"}, token=who + "-test")
        with store.transaction() as c:
            ask = H.say(c, "bot:ops", "human:" + who, "Ship the pricing page today?", kind="ask",
                        conversation_id=question["conversation_id"], refs={"task": public["id"]})
            H.answer(c, "human:" + who, ask["id"], words)
    return public, secret


def kinds(page):
    return sorted({item["kind"] for item in page["items"]})


def test_the_log_shows_every_recorded_kind_and_a_member_only_what_sql_shows_them(api):
    public, secret = seed(api)
    owner = get(api, "history")
    assert kinds(owner) == sorted(history_log.KINDS) and not owner["next"]
    stamps = [(i["at"], i["id"]) for i in owner["items"]]
    assert stamps == sorted(stamps, reverse=True)
    by_kind = {i["kind"]: i for i in owner["items"]}
    assert by_kind["approval.granted"]["actor"] == "human:ana" and by_kind["approval.granted"]["about"] == "bot:ops"
    assert by_kind["credential.revealed"]["subject"] == "=Stripe key"
    assert by_kind["decision"]["subject"] == "Ship the pricing page today?" and by_kind["decision"]["result"] == "Send it Monday"
    assert by_kind["decision"]["link"] == "#/task/" + public["id"]
    assert "Yes, ship it" not in str(owner)
    assert owner["names"]["bot:ops"] == "ops"

    # Cara reads her own task, its run-up and her answer; nothing of the private task, the reveal or Ana's own events.
    cara = get(api, "history", token="cara-test")
    seen = str(cara)
    assert {"task", "decision"} <= set(kinds(cara))
    for hidden in ("Acme layoffs plan", secret["id"], "Stripe", "k1", "auto-1"):
        assert hidden not in seen
    assert not {"approval.asked", "approval.granted", "credential.revealed", "outbound.blocked"} & set(kinds(cara))
    # Each of Cara's rows is one her own SQL reads.
    sql = post(api, "sql", {"sql": "SELECT 'task:'||id FROM task_events UNION ALL SELECT 'decision:'||id FROM messages "
                                   "UNION ALL SELECT 'permission.changed:'||id FROM events"}, token="cara-test")
    assert {i["id"] for i in cara["items"]} <= {row[0] for row in sql["rows"]}


def test_filters_narrow_by_teammate_team_and_kind_and_a_bot_needs_read(api):
    public, _ = seed(api)
    only = get(api, "history?kind=approval.asked,approval.granted")
    assert kinds(only) == ["approval.asked", "approval.granted"]
    get(api, "history?kind=mail", expected=422)
    get(api, "history?bot=ops&person=ana", expected=422)

    # A teammate is who acted, or whom the row is about: Ana's grant of ops' approval is in ops' history.
    ops = get(api, "history?bot=ops")
    assert all("bot:ops" in (i["actor"], i["about"]) for i in ops["items"])
    assert {"run", "task", "approval.asked", "approval.granted", "permission.changed", "decision"} <= set(kinds(ops))
    assert [i["result"] for i in get(api, "history?person=ana&kind=decision")["items"]] == ["Send it Monday"]
    assert [i["result"] for i in get(api, "history?person=cara&kind=decision", token="cara-test")["items"]] == ["Yes, ship it"]
    get(api, "history?bot=inbox", token="cara-test", expected=404)

    # A team holds the groups nested under it.
    gid = post(api, "groups", {"name": "Acme Ops", "add": {"bots": ["finance"]}})["id"]
    post(api, "groups", {"name": "Acme Ops Desk", "parent": gid, "add": {"bots": ["ops"]}})
    team = get(api, "history?team=" + gid)
    assert team["items"] and all("bot:ops" in (i["actor"], i["about"]) for i in team["items"])
    get(api, "history?team=nowhere", expected=404)

    since = get(api, "history?from=2999-01-01T00:00:00Z")
    assert since["items"] == []
    get(api, "history?from=yesterday", expected=422)


def test_pages_continue_from_next_with_nothing_skipped(api):
    seed(api)
    whole = [i["id"] for i in get(api, "history")["items"]]
    paged, before = [], ""
    while True:
        page = get(api, "history?limit=2" + (f"&before={before}" if before else ""))
        paged += [i["id"] for i in page["items"]]
        before = page["next"]
        if not before:
            break
    assert paged == whole


def test_csv_is_the_owners_and_admins_and_never_runs_a_cell_as_a_formula(api):
    seed(api)
    r = api.get("/api/v2/history.csv?kind=credential.revealed,permission.changed", headers=headers())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["time", "actor", "kind", "subject", "result", "link"]
    assert ["inbox", "Credential revealed", "'=Stripe key", "revealed"] == rows[1][1:5] or \
        any(row[2:4] == ["Credential revealed", "'=Stripe key"] for row in rows[1:])
    assert any(row[2] == "Permission change" and row[5].endswith("/#/bot/ops") for row in rows[1:])
    assert api.get("/api/v2/history.csv", headers=headers("ben-test")).status_code == 200     # an Admin
    assert api.get("/api/v2/history.csv", headers=headers("cara-test")).status_code == 403   # a member

    # Cut at its cap, the CSV says where the next one starts.
    import pytest
    with pytest.MonkeyPatch.context() as m:
        m.setattr(history_log, "CSV_MAX", 2)
        short = api.get("/api/v2/history.csv", headers=headers())
    assert len(list(csv.reader(io.StringIO(short.text)))) == 3 and "|" in short.headers["x-next"]
