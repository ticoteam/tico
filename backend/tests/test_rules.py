"""Wake rules (backend/rules.py): every message to a bot names the rule that decided it, table rows override the
built-ins most-specific-first, the always-wake floor holds, and a run records the rule that woke it."""

import json

from backend import hubdb as H
from backend import rules as R
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_wake_rules import as_cause, claim, finish, jobs, news, parked_task, setup, skips


def rule_row(api, token="ben-test", expected=200, **body):
    body = {"scope_kind": "team", "event": "status_or_relation", "then": "note", **body}
    return post(api, "rules", body, token=token, expected=expected)


def plain_task(c, title="Plain work", clear=True):
    task = H.task_create(c, "bot:ops", title, "Do it", "bot:cpo")
    if clear:
        c.execute("DELETE FROM jobs")
    return H.task(c, task["id"])


def reopened(c, task):
    """A wake an event names (the task was handed back to its owner): it starts a run under the rule `assignment`."""
    return H._wake(c, task, task["owner"], "Open: " + task["title"])


def test_every_message_to_a_bot_names_its_rule_and_the_filters_are_unchanged(api):
    setup(api)
    post(api, "chat/cpo", {"text": "Run your check"})
    with api.app.state.store.transaction() as c:
        task, _ = parked_task(c)
        with as_cause("bot:ops"):
            held = news(c, task)
        plain = news(c, plain_task(c))
        person = H.message(c, c.execute("SELECT id FROM messages WHERE from_actor='human:ana' LIMIT 1").fetchone()[0])
    assert person["refs"]["rule"] == "person_message"
    assert held["refs"]["rule"] == "parked" and held["refs"]["quiet"] is True
    # News no rule names waits as a note; an event that hands the bot work wakes under its own rule.
    assert plain["refs"]["rule"] == "status_or_relation" and plain["refs"]["quiet"] is True
    with api.app.state.store.transaction() as c:
        assert not reopened(c, plain_task(c, "Reopened work"))["refs"].get("quiet")
    with api.app.state.store.read() as c:
        assert [s["detail"]["rule_id"] for s in skips(c)] == ["parked", "status_or_relation"]


def test_rows_override_by_scope_task_over_bot_over_team_and_the_floor_stays(api):
    setup(api)
    team = rule_row(api)["rule"]
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        msg = news(c, task)
        assert msg["refs"]["rule"] == team["id"] and msg["refs"]["quiet"] is True and jobs(c, "cpo") == 0
        assert msg["refs"]["held_by"] == "rule"
    bot = rule_row(api, scope_kind="bot", scope_id="cpo", then="wake")["rule"]
    with api.app.state.store.transaction() as c:
        assert news(c, task)["refs"]["rule"] == bot["id"] and jobs(c, "cpo") == 1
    one = rule_row(api, scope_kind="task", scope_id=task["id"], then="ignore")["rule"]
    with api.app.state.store.transaction() as c:
        msg = news(c, task)
        assert msg["refs"]["rule"] == one["id"] and msg["refs"]["held_by"] == "ignored"
        assert c.execute("SELECT count(*) FROM notes WHERE message_id=?", (msg["id"],)).fetchone()[0] == 0
    # A person's message is never made to wait, and a row cannot ask it to.
    rule_row(api, event="person_message", then="ignore", expected=422)
    rule_row(api, event="*", then="ignore")
    post(api, "chat/cpo", {"text": "Still there?"})
    with api.app.state.store.read() as c:
        last = c.execute("SELECT refs_json FROM messages WHERE from_actor='human:ana' ORDER BY rowid DESC").fetchone()
        assert json.loads(last[0])["rule"] == "person_message" and "quiet" not in json.loads(last[0])
        # What the bot waits on still wakes it under a `*` row.
        found = R.judge(c, H.KEEPER, "bot:cpo", "message", {"task": task["id"], "blocker_done": True}, "Done", None)
        assert (found.outcome, found.rule) == ("wake", "awaited_blocker_done")


def test_a_run_records_the_rule_that_woke_it_and_fail_open_wakes(api, monkeypatch):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        reopened(c, plain_task(c))
    attempt = claim(api, r, "cpo")
    finish(api, r, attempt)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT rule_id FROM turns WHERE id=?", (attempt["id"],)).fetchone()[0] == "assignment"
    monkeypatch.setattr(R, "_judge", lambda *a, **k: 1 / 0)
    with api.app.state.store.transaction() as c:
        other = plain_task(c, "Other work", clear=False)
        before = jobs(c, "cpo")
        woke = reopened(c, other)
        assert woke["refs"]["rule"] == R.FAIL_OPEN and jobs(c, "cpo") == before + 1


def test_rule_writes_follow_the_scope_and_the_list_shows_builtins_and_counts(api):
    setup(api)
    rule_row(api, token="cara-test", expected=403)
    made = rule_row(api)["rule"]
    assert made["revision"] == 1 and made["enabled"] is True
    seen = get(api, "rules", token="cara-test")
    ids = {r["id"] for r in seen["rules"]}
    assert {"parked", "own_write", "person_message", made["id"]} <= ids
    row = next(r for r in seen["rules"] if r["id"] == made["id"])
    assert row["runs_7d"] == 0 and row["can_change"] is False
    post(api, f"rules/{made['id']}", {"expected_revision": 1, "then": "wake"}, token="cara-test", expected=403)
    stale = post(api, f"rules/{made['id']}", {"expected_revision": 9, "then": "wake"}, expected=409)
    assert stale["error"]["code"] == "version_conflict"
    changed = post(api, f"rules/{made['id']}", {"expected_revision": 1, "then": "wake", "on": False})["rule"]
    assert changed["revision"] == 2 and changed["then"] == "wake" and changed["enabled"] is False
    post(api, "rules/parked", {"expected_revision": 1, "then": "wake"}, expected=403)
    rule_row(api, then="action", params={"action": "nope"}, expected=422)
    rule_row(api, when={"tag": [f"tag-{n}" for n in range(200)]}, expected=422)   # read per message: kept small
    rule_row(api, then="action", params={"action": "close"})
    post(api, f"rules/{made['id']}/delete", {}, token="cara-test", expected=403)
    post(api, f"rules/{made['id']}/delete", {})
    assert made["id"] not in {r["id"] for r in get(api, "rules")["rules"]}


def test_rule_weekly_counts_runs_per_rule(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        reopened(c, plain_task(c))
    finish(api, r, claim(api, r, "cpo"))
    out = api.post("/api/v2/sql", json={"sql": "SELECT rule, runs FROM rule_weekly WHERE bot='cpo'"},
                   headers={"Authorization": "Bearer ana-test"})
    assert out.status_code == 200, out.text
    assert ["assignment", 1] in [list(row) for row in out.json()["rows"]]


def test_an_event_no_rule_names_is_a_note_and_a_named_one_wakes(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        plain = news(c, task, "Related task changed")
        assert plain["refs"]["rule"] == "status_or_relation" and plain["refs"]["quiet"] is True
        assert c.execute("SELECT count(*) FROM notes WHERE message_id=?", (plain["id"],)).fetchone()[0] == 1
        assert R._rule_for_event("brand_new_event") == (R.NOTE, R.DEFAULT_NOTE)
        for event in ("subtask_finished", "task_done", "blocker", "release_published", "backlog"):
            woke = news(c, task, "x", refs={"wake": event})
            assert woke["refs"]["rule"] == event and not woke["refs"].get("quiet"), event
        assert jobs(c, "cpo") == 5


def test_an_ask_rule_writes_quietly_and_raises_one_needs_you_item_per_task_and_rule(api):
    setup(api)
    row = rule_row(api, then="ask")["rule"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Plan the launch", "Plan it", "bot:cpo")
        c.execute("DELETE FROM jobs")
        first, again = news(c, H.task(c, task["id"])), news(c, H.task(c, task["id"]))
        for msg in (first, again):
            assert msg["refs"]["rule"] == row["id"] and msg["refs"]["quiet"] is True
        assert jobs(c, "cpo") == 0
        cards = c.execute("SELECT t.owner, t.title, t.body FROM events e JOIN tasks t ON t.id=e.target "
                          "WHERE e.action='rule.ask'").fetchall()
        assert len(cards) == 1 and cards[0]["owner"] == "human:ana" and row["id"] in cards[0]["body"]


def test_an_ask_card_about_a_private_task_is_private(api):
    setup(api)
    rule_row(api, then="ask")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Acme layoff plan", "Plan it", "bot:cpo", private=True)
        c.execute("DELETE FROM jobs")
        news(c, H.task(c, task["id"]))
        card = c.execute("SELECT t.id, t.private FROM events e JOIN tasks t ON t.id=e.target "
                         "WHERE e.action='rule.ask'").fetchone()
        assert card and card["private"] == 1
        assert not H.task_private_readable(c, "human:cara", H.task(c, card["id"]))


def test_an_action_rule_is_written_only_by_someone_who_could_make_the_change(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:cara", "Cara's errand", "x", "bot:cpo")
    rule_row(api, token="cara-test", scope_kind="task", scope_id=task["id"])            # a party sets a note row
    rule_row(api, token="cara-test", scope_kind="task", scope_id=task["id"], then="action",
             params={"action": "close"}, expected=403)                                    # an action acts as Tico
    made = rule_row(api, scope_kind="task", scope_id=task["id"], then="action", params={"action": "close"})["rule"]
    post(api, f"rules/{made['id']}", {"expected_revision": 1, "on": False}, token="cara-test")   # turning it off is hers


def test_an_action_rule_runs_as_the_keeper_and_a_failed_action_wakes_the_bot(api):
    setup(api)
    row = rule_row(api, then="action", params={"action": "close", "note": "Not needed"})["rule"]
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        msg = news(c, task)
        assert msg["refs"]["rule"] == row["id"] and msg["refs"]["quiet"] is True
        assert H.task(c, task["id"])["status"] == "closed"
        ran = c.execute("SELECT detail_json FROM events WHERE action='rule.action' AND target=?", (task["id"],)).fetchone()
        assert json.loads(ran[0]) == {"rule": row["id"], "action": "close"}
    post(api, f"rules/{row['id']}", {"expected_revision": 1, "params": {"action": "move_step", "step": "No such step"}})
    with api.app.state.store.transaction() as c:
        other = plain_task(c, "Other work", clear=False)
        before = jobs(c, "cpo")
        woke = news(c, other)
        assert woke["refs"]["rule"] == R.FAIL_OPEN and jobs(c, "cpo") == before + 1
        assert H.task(c, other["id"])["status"] != "closed"


def test_a_long_rule_list_is_byte_capped_and_continues_where_it_stopped(api):
    made = rule_row(api, when={"tag": [f"acme-tag-{n:03d}" for n in range(60)]})["rule"]
    with api.app.state.store.transaction() as c:
        row = dict(c.execute("SELECT * FROM rules WHERE id=?", (made["id"],)).fetchone())
        c.executemany(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})",
                      [[f"{made['id']}-{i}" if k == "id" else v for k, v in row.items()] for i in range(399)])
    seen, offset = set(), 0
    while True:
        page = get(api, f"rules?scope_kind=team&limit=500&offset={offset}")
        assert len(json.dumps(page)) < 400 * 1024
        seen |= {r["id"] for r in page["rules"]}
        if not page["more"]:
            break
        offset = page["next_offset"]
    assert offset and len(seen) == 400, "the cap split the 500-row page, and every row is still reached"
