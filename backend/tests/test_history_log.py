"""History's Log (GET /api/v2/history, backend/history_log.py): every kind already recorded, filtered by teammate, team
and kind, never more than the reader may query through SQL, and the CSV export for the owner and the Admins."""

import csv
import io
from contextlib import closing

import pytest

from backend import history_log
from backend import sql
from backend.auth import Identity
from backend.store import H
from backend.tests.old_asks import old_answer, old_ask
from backend.tests.helpers.api import get, headers, post, setup_attempt  # noqa: F401


def seed(api):
    """One row of every kind: a run of ops, Cara's public task for ops (made, then done), a private one of Ana's, a
    credential the inbox revealed, an automation send kept as a draft, a permission change, and Cara's answer to ops (an old ask and answer, as an older file holds them)."""
    store = api.app.state.store
    setup_attempt(api)  # ana's chat with ops, its run under way
    with store.transaction() as c:
        public = H.task_create(
            c, "human:cara", "Refresh the Acme pricing page", "", "bot:ops", lint=False, private=False
        )
        secret = H.task_create(c, "human:ana", "Acme layoffs plan", "", "human:ben", lint=False, private=True)
        H.task_done(c, "bot:ops", public["id"], "Refreshed it", H.task(c, public["id"])["version"])
        c.execute(
            "INSERT INTO credentials(id,name,kind,created_at,updated_at,updated_by) VALUES('k1','=Stripe key','token',?,?,'ana')",
            (H.now(), H.now()),
        )
        H.event(c, "bot:inbox", "credential.revealed", "k1", {"attempt": None})
        H.event(c, "human:ana", "automation.send_off", "auto-1", {"run": "r1"})
        H.event(c, "human:ana", "bot.owners_changed", "ops", {"owners": ["ana"]})
        H.event(c, "human:ana", "message.say", "m1", {})  # not a kind the Log shows
    # Each answers ops in their own chat with it, which is theirs alone: not even the owner reads Cara's.
    for who, words in (("ana", "Send it Monday"), ("cara", "Yes, ship it")):
        question = post(api, "chat/ops", {"text": "Ready?"}, token=who + "-test")
        with store.transaction() as c:
            ask = old_ask(
                c,
                "bot:ops",
                "human:" + who,
                "Ship the pricing page today?",
                conversation_id=question["conversation_id"],
                refs={"task": public["id"]},
            )
            old_answer(c, "human:" + who, ask, words)
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
    moves = {(i["subject"], i["result"]) for i in owner["items"] if i["kind"] == "task"}
    assert {("Refresh the Acme pricing page", "created"), ("Refresh the Acme pricing page", "done")} <= moves
    assert by_kind["credential.revealed"]["subject"] == "=Stripe key"
    assert (
        by_kind["decision"]["subject"] == "Ship the pricing page today?"
        and by_kind["decision"]["result"] == "Send it Monday"
    )
    assert by_kind["decision"]["link"] == "#/task/" + public["id"]
    assert "Yes, ship it" not in str(owner)
    assert owner["names"]["bot:ops"] == "ops"

    # Cara reads her own task, its run-up and her answer; nothing of the private task, the reveal or Ana's own events.
    cara = get(api, "history", token="cara-test")
    seen = str(cara)
    assert {"task", "decision"} <= set(kinds(cara))
    for hidden in ("Acme layoffs plan", secret["id"], "Stripe", "k1", "auto-1"):
        assert hidden not in seen
    assert not {"credential.revealed", "outbound.blocked"} & set(kinds(cara))
    # Each of Cara's rows is one her own SQL reads.
    sql = post(
        api,
        "sql",
        {
            "sql": "SELECT 'task:'||id FROM history_events WHERE subject_type='task' UNION ALL SELECT 'decision:'||id FROM messages "
            "UNION ALL SELECT 'permission.changed:'||id FROM events"
        },
        token="cara-test",
    )
    assert {i["id"] for i in cara["items"]} <= {row[0] for row in sql["rows"]}


def test_filters_narrow_by_teammate_team_and_kind_and_a_bot_needs_read(api):
    public, _ = seed(api)
    only = get(api, "history?kind=run,task")
    assert kinds(only) == ["run", "task"]
    get(api, "history?kind=mail", expected=422)
    get(api, "history?bot=ops&person=ana", expected=422)

    # A teammate is who acted, or whom the row is about: Cara's task for ops is in ops' history.
    ops = get(api, "history?bot=ops")
    assert all("bot:ops" in (i["actor"], i["about"]) for i in ops["items"])
    assert {"run", "task", "permission.changed", "decision"} <= set(kinds(ops))
    assert [i["result"] for i in get(api, "history?person=ana&kind=decision")["items"]] == ["Send it Monday"]
    assert [i["result"] for i in get(api, "history?person=cara&kind=decision", token="cara-test")["items"]] == [
        "Yes, ship it"
    ]
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
    assert ["inbox", "Credential revealed", "'=Stripe key", "revealed"] == rows[1][1:5] or any(
        row[2:4] == ["Credential revealed", "'=Stripe key"] for row in rows[1:]
    )
    assert any(row[2] == "Permission change" and row[5].endswith("/#/bot/ops") for row in rows[1:])
    assert api.get("/api/v2/history.csv", headers=headers("ben-test")).status_code == 200  # an Admin
    assert api.get("/api/v2/history.csv", headers=headers("cara-test")).status_code == 403  # a member

    # Cut at its cap, the CSV says where the next one starts.
    with pytest.MonkeyPatch.context() as m:
        m.setattr(history_log, "CSV_MAX", 2)
        short = api.get("/api/v2/history.csv", headers=headers())
    assert len(list(csv.reader(io.StringIO(short.text)))) == 3 and "|" in short.headers["x-next"]


def test_consolidated_history_only_shows_task_moves_and_recorded_runs(api):
    _, _, active = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Refresh pricing", "", "bot:ops", lint=False, private=False)
        unrelated = H.history_event(c, "company", task["id"], "human:ana", "status", "draft", "published")
        recorded = H.turn_start(c, "bot:ops", "ops", task_id=task["id"])["id"]
        H.turn_finish(c, "bot:ops", recorded, exit_code="completed")
    runs = get(api, "history?kind=run")["items"]
    by_id = {row["id"]: row for row in runs}
    assert by_id["run:" + active["id"]]["result"] == "running"
    assert by_id["run:" + recorded]["result"] == "done"
    assert by_id["run:" + recorded]["link"] == "/api/runs/" + recorded + "/log"
    assert by_id["run:" + recorded]["task"]["id"] == task["id"]
    with api.app.state.store.transaction() as c:
        # A lease that never acquired a run record must not look like a running turn.
        c.execute("UPDATE attempts SET trigger=NULL WHERE id=?", (active["id"],))
    assert [row["id"] for row in get(api, "history?kind=run")["items"]] == ["run:" + recorded]
    changes = get(api, "history?kind=task")["items"]
    assert any(row["task"]["id"] == task["id"] for row in changes)
    assert "task:" + unrelated not in {row["id"] for row in changes}


def test_blocked_send_uses_current_automation_access_in_json_and_csv(api):
    made = post(
        api,
        "automations",
        {
            "name": "Confidential hiring plan",
            "owner": "person:ben",
            "trigger": {"type": "event", "name": "x.y"},
            "steps": [{"id": "note", "type": "action", "do": "note", "args": {"text": "Seen"}}],
        },
    )
    aid = made["id"]
    with api.app.state.store.transaction() as c:
        H.event(c, "human:ben", "automation.send_off", aid, {"run": "r1"})

    def exported():
        r = api.get("/api/v2/history.csv?kind=outbound.blocked", headers=headers("ben-test"))
        assert r.status_code == 200, r.text
        return list(csv.reader(io.StringIO(r.text)))[1:]

    assert get(api, "history?kind=outbound.blocked", token="ben-test")["items"][0]["subject"] == made["name"]
    assert exported()[0][3] == made["name"]
    changed = api.patch(f"/api/v2/automations/{aid}", json={"owner": "person:cara"}, headers=headers())
    assert changed.status_code == 200, changed.text
    get(api, f"automations/{aid}", token="ben-test", expected=403)
    page = get(api, "history?kind=outbound.blocked", token="ben-test")
    assert page["items"][0]["subject"] == "An automation" and made["name"] not in str(page)
    assert exported()[0][3] == "An automation"
    assert get(api, "history?kind=outbound.blocked")["items"][0]["subject"] == made["name"]


def test_source_text_is_clipped_in_sql_before_python_or_csv(api):
    seed(api)
    large = "x" * 200_000
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        c.execute("UPDATE messages SET body=? WHERE kind IN ('ask','answer')", (large,))
        c.execute("UPDATE tasks SET title=?", (large,))
        c.execute("UPDATE attempts SET exit=?", (large,))
        H.event(c, "human:ana", "credential.granted", "k1", {"subject": "bot:ops", "unused": large})
        H.event(c, "human:ana", "person.access_edited", "ben", {"roles": large, "via": large})
    fields = {"answer", "asked", "title", "exit", "changed", "subject_actor"}
    with (
        store.read() as c,
        closing(sql.connect(store.settings.db_path, c, auth, Identity("human:ana", "owner"))) as conn,
    ):
        for source in history_log._sources(history_log.KINDS):
            statement, args = history_log._query(source, None, "", "", "", history_log.CSV_MAX, ["human:ana"])
            rows = list(conn.execute(statement, args))
            assert rows, source[0]
            for row in rows:
                assert all(
                    len(row[key]) <= history_log.TEXT_MAX + 1
                    for key in fields & set(row.keys())
                    if row[key] is not None
                ), dict(row)
    page = get(api, "history")
    answer = next(row for row in page["items"] if row["kind"] == "decision")
    assert answer["subject"] == answer["result"] == "x" * (history_log.TEXT_MAX - 1) + "…"
    assert any(row["result"] == "access: roles" for row in page["items"])
    assert any(row["result"] == "granted to ops" for row in page["items"])
    exported = api.get("/api/v2/history.csv", headers=headers())
    assert exported.status_code == 200 and large not in exported.text


def test_pages_keep_all_source_and_id_ties_in_order(api):
    seed(api)
    with api.app.state.store.transaction() as c:
        at = "2026-01-01T00:00:00.000000Z"
        for table in ("attempts", "history_events", "messages", "events"):
            c.execute(f"UPDATE {table} SET created_at=?", (at,))
    whole = get(api, "history")["items"]
    paged, before = [], ""
    for _ in range(len(whole) + 1):
        page = api.get("/api/v2/history", headers=headers(), params={"limit": 1, "before": before})
        assert page.status_code == 200, page.text
        data = page.json()
        paged.extend(data["items"])
        before = data["next"]
        if not before:
            break
    assert [row["id"] for row in paged] == [row["id"] for row in whole]


def test_bot_slug_filters_resolve_uuid_actors_and_csv_uses_their_display_names(api):
    from backend.tests.helpers.bot_access import bot, bot_id

    bot(api, "mira")
    ident = bot_id(api, "mira")
    assert ident != "mira"
    with api.app.state.store.transaction() as c:
        run = H.turn_start(c, "bot:" + ident, ident)["id"]
        H.turn_finish(c, "bot:" + ident, run, exit_code="completed")
    by_slug = get(api, "history?bot=mira&kind=run")
    assert by_slug["items"] == get(api, f"history?bot={ident}&kind=run")["items"]
    assert [row["id"] for row in by_slug["items"]] == ["run:" + run]
    assert by_slug["names"]["bot:" + ident] == "Mira"
    exported = api.get("/api/v2/history.csv?bot=mira&kind=run", headers=headers())
    assert exported.status_code == 200, exported.text
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert len(rows) == 2 and rows[1][1] == "Mira"


def test_person_and_group_audience_changes_are_permission_changes(api):
    gid = post(api, "groups", {"name": "Acme Support"})["id"]
    for target in ("humans/cara", "groups/" + gid):
        revision = get(api, target + "/access")["revision"]
        changed = api.put(
            "/api/v2/" + target + "/access",
            headers=headers(),
            json={
                "revision": revision,
                "see": {"everyone": False},
                "read": {"everyone": False},
                "write": {"everyone": False},
            },
        )
        assert changed.status_code == 200, changed.text
    rows = get(api, "history?kind=permission.changed")["items"]
    assert any(row["about"] == "human:cara" and row["result"] == "who sees them" for row in rows)
    assert any(row["about"] == "group:" + gid and row["result"] == "who sees the group" for row in rows)
    assert any(
        row["result"] == "who sees them" for row in get(api, "history?person=cara&kind=permission.changed")["items"]
    )
    exported = api.get("/api/v2/history.csv?kind=permission.changed", headers=headers())
    assert exported.status_code == 200
    assert {"who sees them", "who sees the group"} <= {row[4] for row in csv.reader(io.StringIO(exported.text))}
