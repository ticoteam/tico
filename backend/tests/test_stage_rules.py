"""Stage rules (backend/stage_rules.py): Snooze only for a real wait (a snooze is only "look again on a date"; a wait
on another task is depends_on, on a person an ask), judged by Jev once per bot snooze and failing open; a
fixed-condition Block inside the write; a bot's rule is only a suggestion. Owner on the team's tests are with the
Engineering preset's (test_product_engineering_presets.py).

Synthetic rows only (backend/tests/helpers/engine.py); Jev is a fake that counts its calls.
"""

import pytest

from backend import stage_rules as SR
from backend import task_engine as E
from backend.hubdb import Refused
from backend.store import H, Problem
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.tasks_board import bot_token

ANA = X.ANA
# What Jev reads as a block in a snooze's why: a task, a PR, a merge, a release, main or checks, or a person.
BLOCKED_WORDS = ("#", "rc.", "merge", " pr ", " pr.", "main", "checks", "read-only", "no access", "customer", "decide")
LOOK_AGAIN = "couldn't reproduce, try again Nov 16"


class FakeJev:
    """Jev as stage_rules asks it: a p for each question. A why that names what it waits on (another task, a release,
    a person) is blocked; one that only means to look again later is not."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, state, questions, label):
        self.calls.append((state, dict(questions), label))
        if self.fail:
            raise RuntimeError("Jev is down")
        why = state["event"]["why"].lower()
        p = 0.95 if (any(word in why for word in BLOCKED_WORDS) or " pr" in why.replace("production", "")) else 0.05
        return {"answers": {q: {"noul": p} for q in questions}}


@pytest.fixture
def jev(monkeypatch):
    fake = FakeJev()
    monkeypatch.setattr(SR, "JEV", fake)
    return fake


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        X.bot(conn, "ops")
        SR.ensure_defaults(conn)
        yield conn


def _task(c):
    return X.new_task(c, ANA, "Ship the release notes", assignee="bot:ops")


def test_a_bots_snooze_on_another_task_or_a_release_is_refused_and_look_again_on_a_date_goes(c, jev):
    """The spec's own cases: a snooze that waits on a task or a release is told to depend on it; one that only looks
    again on a date goes. Each snooze is one Jev call, asked the rule's question."""
    row = _task(c)
    for why in ("waiting for #304 to push its PR", "waiting for rc.23"):
        blocked = SR.judge_snooze(c, row, "bot:ops", why, None)
        assert blocked is not None, why
        rule, line = blocked
        assert rule["id"] == SR.REAL_WAIT
        assert line == "Rule Snooze only for a real wait: " + SR.BLOCKED
        assert "Blocked? tico task relate <id> depends_on #N." in E.refusal_line(rule, line)
    assert SR.judge_snooze(c, row, "bot:ops", LOOK_AGAIN, "2099-11-16T08:00:00Z") is None
    why = "look again on 2099-11-16: read the version production runs"
    assert SR.judge_snooze(c, row, "bot:ops", why, "2099-11-16T08:00:00Z") is None
    assert len(jev.calls) == 4
    assert [call[2] for call in jev.calls] == ["stage-rule@1"] * 4
    assert all([q["instructions"]["question"] for q in call[1].values()] == [SR.REAL_WAIT_JEV] for call in jev.calls)


def test_a_bots_snooze_on_a_person_is_told_to_ask_them(c, jev):
    rule, line = SR.judge_snooze(c, _task(c), "bot:ops", "waiting for the customer to decide", None)
    assert "Ask them on the task." in E.refusal_line(rule, line)
    assert len(jev.calls) == 1


def test_a_stored_default_in_its_earlier_words_is_brought_up_to_date_and_an_edited_one_is_not(company):
    """Companies that have the default stored from before: their rule asks today's question at the next start, unless
    someone there changed its words."""
    old_jev, old_reason = SR.EARLIER[SR.REAL_WAIT]["jev"][0], SR.EARLIER[SR.REAL_WAIT]["reason"][0]
    with company.transaction() as c:
        SR.ensure_defaults(c)
        c.execute("UPDATE stage_rules SET jev=?, reason=? WHERE id=?", (old_jev, old_reason, SR.REAL_WAIT))
        assert SR.ensure_defaults(c) == []
        rule = SR.get(c, SR.REAL_WAIT)
        assert (rule["jev"], rule["reason"]) == (SR.REAL_WAIT_JEV, SR.BLOCKED)
        c.execute("UPDATE stage_rules SET jev=?, reason=? WHERE id=?", (old_jev, "Ours: ask Ana.", SR.REAL_WAIT))
        SR.ensure_defaults(c)
        rule = SR.get(c, SR.REAL_WAIT)
        assert (rule["jev"], rule["reason"]) == (SR.REAL_WAIT_JEV, "Ours: ask Ana.")
        revision = rule["revision"]
        SR.ensure_defaults(c)
        assert SR.get(c, SR.REAL_WAIT)["revision"] == revision  # nothing to bring: nothing written


def test_a_persons_snooze_is_not_judged(c, jev):
    row = _task(c)
    assert SR.judge_snooze(c, row, ANA, "waiting for #304 to push its PR", None) is None
    assert jev.calls == []


def test_every_jev_rule_on_one_bot_snooze_goes_in_one_call(c, jev):
    """Three Jev-judged rules on one snooze: Jev is called once, with an answer per rule."""
    for name in ("Waiting on a person", "Waiting on money"):
        SR.add(c, ANA, {"name": name, "event": "bot_snoozes", "jev": f"The bot says: {name}.", "then": "block"})
    row = _task(c)
    SR.judge_snooze(c, row, "bot:ops", "waiting for the customer to reply", None)
    assert len(jev.calls) == 1
    assert len(jev.calls[0][1]) == 3


def test_no_bot_snooze_makes_no_call(c, jev):
    row = _task(c)
    E.reassign(c, row["id"], "bot:ops", ANA, None, row["version"])
    E.snooze(c, row["id"], ANA, "Back next week", "2099-01-05T08:00:00.000000Z", E.task(c, row["id"])["version"])
    assert jev.calls == []


def test_jev_failing_lets_the_snooze_go(c, monkeypatch):
    down = FakeJev(fail=True)
    monkeypatch.setattr(SR, "JEV", down)
    row = _task(c)
    assert SR.judge_snooze(c, row, "bot:ops", "ticoteam/tico is read-only for me", None) is None
    assert len(down.calls) == 1


def test_a_fixed_condition_rule_blocks_a_bots_snooze_inside_the_write_naming_the_rule(c, jev):
    rule = SR.add(
        c,
        ANA,
        {
            "name": "No long snoozes",
            "event": "bot_snoozes",
            "cond": {"snooze_hours_over": 48},
            "then": "block",
            "reason": "Snooze two days at most.",
        },
    )
    row = _task(c)
    with pytest.raises(Refused) as refused:
        E.snooze(c, row["id"], "bot:ops", "Back later", "2099-01-05T08:00:00.000000Z", row["version"])
    assert f"(rule {rule['id']})" in str(refused.value)
    short = H.shift(H.now(), hours=12)
    assert E.snooze(c, row["id"], "bot:ops", "Back later", short, row["version"]).get("snooze")
    assert jev.calls == []  # a Jev rule is never asked inside the write


def test_a_retired_rule_does_nothing_and_the_defaults_are_not_made_again(c, jev):
    SR.retire(c, ANA, SR.REAL_WAIT, "Not for us")
    assert SR.ensure_defaults(c) == []
    row = _task(c)
    assert SR.judge_snooze(c, row, "bot:ops", "ticoteam/tico is read-only for me", None) is None
    assert jev.calls == []


def test_a_bots_new_rule_or_change_is_only_suggested_until_a_person_accepts_it(c):
    made = SR.add(c, "bot:ops", {"name": "Quiet", "event": "bot_snoozes", "jev": "Is it quiet?", "then": "block"})
    assert made["status"] == "suggested" and made["id"].startswith("sr_")
    assert all(r["id"] != made["id"] for r in SR.applying(c, "general", "general-todo", "bot_snoozes"))
    assert SR.accept(c, ANA, made["id"])["status"] == "active"
    change = SR.edit(c, "bot:ops", SR.REAL_WAIT, {"reason": "Ask, don't snooze."})
    assert (change["status"], change["suggests"]) == ("suggested", SR.REAL_WAIT)
    assert SR.get(c, SR.REAL_WAIT)["reason"] == SR.BLOCKED
    SR.accept(c, ANA, change["id"])
    assert SR.get(c, SR.REAL_WAIT)["reason"] == "Ask, don't snooze."
    assert SR.get(c, change["id"])["retired_at"]
    asked_out = SR.retire(c, "bot:ops", made["id"], "No longer needed")
    assert SR.skip(c, ANA, asked_out["id"])["retired_at"]
    assert not SR.get(c, made["id"])["retired_at"]
    with pytest.raises(Problem):
        SR.accept(c, "bot:ops", asked_out["id"])


def test_an_edit_on_a_stale_revision_and_a_bad_rule_are_refused(c):
    rule = SR.get(c, SR.REAL_WAIT)
    SR.edit(c, ANA, SR.REAL_WAIT, {"reason": "Ask first."}, rule["revision"])
    with pytest.raises(Problem) as stale:
        SR.edit(c, ANA, SR.REAL_WAIT, {"reason": "Ask again."}, rule["revision"])
    assert stale.value.status == 409
    with pytest.raises(Problem):
        SR.add(c, ANA, {"name": "Quiet", "event": "timer", "timer": {"kind": "quiet", "hours": 6}, "then": "block"})
    with pytest.raises(Problem):
        SR.add(c, ANA, {"name": "Odd", "event": "bot_snoozes", "cond": {"moon": "full"}, "then": "block"})
    assert SR.cost(SR.get(c, SR.REAL_WAIT)) == "one Jev call per event"


def test_the_snooze_route_refuses_a_blocked_bot_and_writes_the_rule_on_the_history(api, monkeypatch):
    fake = FakeJev()
    monkeypatch.setattr(SR, "JEV", fake)
    token = bot_token(api, "ops")
    task = post(api, "tasks", {"owner": "ops", "title": "Tidy the roadmap", "body": "Close stale items."})
    version = api.get(f"/api/v2/tasks/{task['id']}", headers={"Authorization": f"Bearer {token}"}).json()["task"][
        "version"
    ]
    refused = post(
        api,
        f"tasks/{task['id']}/snooze",
        {"version": version, "why": "I have no access to the roadmap board"},
        token,
        expected=409,
    )
    assert "Blocked? tico task relate <id> depends_on #N." in str(refused)
    assert "(rule sr_real_wait)" in str(refused)
    assert refused["next"][0] == f"tico task relate {task['id']} depends_on <task>"
    assert any("tico task ask" in step for step in refused["next"])
    with api.app.state.store.read() as c:
        assert E.task(c, task["id"]).get("snooze") is None
        notes = [
            r[0]
            for r in c.execute(
                "SELECT note FROM history_events WHERE subject_id=? AND kind='stage_rule'", (task["id"],)
            )
        ]
    assert notes == ["Rule Snooze only for a real wait: " + SR.BLOCKED]
    post(
        api,
        f"tasks/{task['id']}/snooze",
        {"version": version, "why": LOOK_AGAIN, "until": "2099-01-05"},
        token,
    )
    assert len(fake.calls) == 2


def test_once_at_release_blocked_bot_snoozes_end_and_each_manager_gets_one_wake(company, jev):
    """The release sweep over open bot snoozes: the blocked ones end, each manager is woken once; a real wait and a
    person's snooze stay; a second run does nothing."""
    with company.transaction() as c:
        X.bot(c, "ops")
        X.bot(c, "lead")
        c.execute("UPDATE pipelines SET manager_id='bot:lead' WHERE id='general'")
        SR.ensure_defaults(c)
        tasks = [X.new_task(c, ANA, title, assignee="bot:ops") for title in ("One", "Two", "Three", "Four")]
        whys = ("ticoteam/tico is read-only for me", "waiting for #304 to merge", LOOK_AGAIN, "")
        for row, why in zip(tasks[:3], whys):
            E.snooze(c, row["id"], "bot:ops", why, "2099-01-05T08:00:00.000000Z", row["version"])
        E.snooze(c, tasks[3]["id"], ANA, "After the offsite", "2099-01-05T08:00:00.000000Z", tasks[3]["version"])
    assert sorted(SR.sweep_snoozes(company)) == sorted(t["id"] for t in tasks[:2])
    assert len(jev.calls) == 3  # one per bot snooze; the person's is not judged
    with company.read() as c:
        assert [bool(E.task(c, t["id"]).get("snooze")) for t in tasks] == [False, False, True, True]
        wakes = c.execute(
            "SELECT j.bot_id, m.body FROM messages m JOIN jobs j ON j.message_id = m.id WHERE m.wake_key LIKE ?",
            ("%:stage-rules-sweep:%",),
        ).fetchall()
    assert [bot for bot, _ in wakes] == ["lead"]
    assert wakes[0][1].startswith("Stage rules: 2 snoozed tasks were blocked, not waiting")
    assert SR.sweep_snoozes(company) == []


def test_the_cli_sends_set_anyway_only_when_asked():
    """`tico task reassign --set-anyway` is a person's way past a refused owner; without the flag nothing is sent."""
    from clients import hubcli, remotecli

    class Recorder:
        def __init__(self):
            self.sent = []

        def post(self, path, body, key=None):
            self.sent.append((path, body))
            return {}

    for argv, anyway in (([], None), (["--set-anyway"], True)):
        args = hubcli.parser().parse_args(["task", "reassign", "t1", "human:ana", "--version", "3", *argv])
        client = Recorder()
        remotecli.via_tool(client, args)
        assert client.sent[0][0] == "tasks/t1/owner"
        assert client.sent[0][1].get("set_anyway") is anyway


def test_a_jev_outage_does_not_use_up_the_release_sweep(company, monkeypatch):
    """Jev down at the first start judges nothing and keeps every snooze; the next run, with Jev up, ends the blocked one."""
    with company.transaction() as c:
        X.bot(c, "ops")
        SR.ensure_defaults(c)
        row = X.new_task(c, ANA, "One", assignee="bot:ops")
        E.snooze(c, row["id"], "bot:ops", "no access to the board", "2099-01-05T08:00:00.000000Z", row["version"])
    monkeypatch.setattr(SR, "JEV", FakeJev(fail=True))
    assert SR.sweep_snoozes(company) == []
    up = FakeJev()
    monkeypatch.setattr(SR, "JEV", up)
    assert SR.sweep_snoozes(company) == [row["id"]]
    assert len(up.calls) == 1
    assert SR.sweep_snoozes(company) == []


def test_tell_or_move_back_on_an_action_when_is_refused_until_action_events_carry_them_out(c):
    # Only timers carry out Tell and Move back in step 1 (owner_check and snooze_rules read Block alone), so a rule
    # "enters -> Tell the manager" would save and never act.
    for event in ("enters", "owner_changes", "bot_snoozes", "enters,owner_changes"):
        for then in ("tell_manager", "tell_owner", "move_back"):
            with pytest.raises(Problem) as refused:
                SR.add(c, ANA, {"name": "Never acts", "event": event, "then": then})
            assert refused.value.status == 422 and str(refused.value.detail) == SR.NEEDS_TIMER
    # An edit to that shape is refused too, and a bot cannot suggest it.
    with pytest.raises(Problem):
        SR.edit(c, ANA, SR.REAL_WAIT, {"then": "tell_manager"})
    with pytest.raises(Problem):
        SR.add(c, "bot:ops", {"name": "Never acts", "event": "enters", "then": "tell_owner"})
    # Timers keep every Then but Block (a timer has no write to refuse).
    for then in ("tell_manager", "tell_owner", "move_back"):
        made = SR.add(c, ANA, {"name": then, "event": "timer", "timer": {"kind": "quiet", "hours": 6}, "then": then})
        assert made["not_acting"] == ""


def test_a_saved_rule_that_never_acts_is_listed_as_not_acting_and_can_still_be_retired(c):
    ts = H.now()
    c.execute(
        "INSERT INTO stage_rules (id, name, pipeline_id, stages, event, timer, cond, jev, then_do, then_params, reason, "
        "status, suggests, created_by, created_at, updated_at) VALUES ('sr_old', 'Old', '', '[]', 'enters', '{}', '{}', "
        "'', 'tell_owner', '{}', '', 'active', '', ?, ?, ?)",
        (ANA, ts, ts),
    )
    listed = {r["id"]: r for r in SR.listed(c)}
    assert listed["sr_old"]["not_acting"] == SR.NEEDS_TIMER
    assert listed[SR.REAL_WAIT]["not_acting"] == ""
    # A bot may suggest retiring it, and a person retires it.
    asked = SR.retire(c, "bot:ops", "sr_old", "Never acts")
    assert asked["status"] == "suggested"
    SR.accept(c, ANA, asked["id"])
    assert SR.get(c, "sr_old")["retired_at"]


def test_a_suggestion_of_the_refused_shape_saved_before_is_not_accepted(c):
    ts = H.now()
    c.execute(
        "INSERT INTO stage_rules (id, name, pipeline_id, stages, event, timer, cond, jev, then_do, then_params, reason, "
        "status, suggests, created_by, created_at, updated_at) VALUES ('sr_sg_old', 'Old', '', '[]', 'enters', '{}', "
        "'{}', '', 'move_back', '{}', '', 'suggested', '', 'bot:ops', ?, ?)",
        (ts, ts),
    )
    with pytest.raises(Problem) as refused:
        SR.accept(c, ANA, "sr_sg_old")
    assert str(refused.value.detail) == SR.NEEDS_TIMER
    assert SR.get(c, "sr_sg_old")["status"] == "suggested"
    # It can still be skipped.
    assert SR.skip(c, ANA, "sr_sg_old")["retired_at"]


def test_a_start_never_sweeps_open_snoozes_with_the_depends_on_question(company, jev):
    """No sweep at release (the spec): a company whose one-off sweep never ran (no bot snooze at its first start) keeps
    a bot's 'waiting for rc.23' snooze across later starts, and Jev is not asked."""
    with company.transaction() as c:
        X.bot(c, "ops")
        SR.ensure_defaults(c)
    SR.install(company)
    with company.transaction() as c:
        row = X.new_task(c, ANA, "One", assignee="bot:ops")
        E.snooze(c, row["id"], "bot:ops", "waiting for rc.23", "2099-01-05T08:00:00.000000Z", row["version"])
    SR.install(company)
    import threading

    for thread in threading.enumerate():
        if thread.name == "stage-rules-sweep":
            thread.join(10)
    with company.read() as c:
        assert E.task(c, row["id"]).get("snooze")
    assert jev.calls == []
