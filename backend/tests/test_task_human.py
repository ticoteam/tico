"""A task's Human is retired (docs/tasks.md, "Who a task falls to"): a person assignee takes its place on the ladder,
after the holder (waiting_on.person_for). The migration moves each Human once: to the assignee of a company task that
has none, else into the task's `human` role. Until the upgrade floor passes, `human` on create and update only sets
that role. Acme is the fictional company: Ana owns it, Ben and Cara are people."""

import importlib

import pytest

from backend import task_engine as E
from backend import task_roles as TRo
from backend import task_wakes as TW
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, post  # noqa: F401  (fixtures)

ANA, BEN, CARA = X.ANA, "human:ben", "human:cara"
MIGRATION = "backend.migrations.20261123231209_retire_task_human"
OK = {"kind": "ok", "who_oks": "people_only", "line": "OK the spec"}
# Spec is a bot's; Review a person's; Feature's first stage is its assignee's, then a bot builds it.
SPEC = ({"name": "Spec", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}, OK]},)
REVIEW = ({"name": "Review", "owner": CARA, "requirements": [{"kind": "owner_done"}, OK]},)
FEATURE = (
    {"name": "Shape", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Build", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}, OK]},
)


@pytest.fixture
def dev(api):
    """The pipelines, and after the test, every person's own Needs you is the one every person's pass gives them: a
    person assignee whose task `waiting_on.candidates` misses fails here."""
    store = api.app.state.store
    with store.transaction() as c:
        for slug in ("builder", "manager"):
            X.bot(c, slug)
        X.pipeline(c, manager="bot:manager")
        X.pipeline(c, "dev", "Development", SPEC, manager="bot:manager")
        X.pipeline(c, "review", "Review", REVIEW, manager="bot:manager")
        X.pipeline(c, "feature", "Feature", FEATURE, manager="bot:manager")
    yield api
    with store.read() as c:
        every = W.needs_by_person(c)
        people = {f"human:{found[0]}" for found in c.execute("SELECT id FROM humans").fetchall()}
        for person in sorted(people | set(every)):
            assert W.needs(c, person) == every.get(person, []), person


def _file(api, title, pipeline, owner="builder", token="ana-test", **extra):
    body = {"title": title, "body": "x", "owner": owner, "pipeline": pipeline, **extra}
    found = post(api, "tasks", body, token=token)
    return found.get("task", found)


def _asks(api, token):
    """The OKs in this person's Needs you, as (task, line)."""
    view = get(api, "needs-you", token=token)
    items = view["items"] if isinstance(view, dict) else view
    return {(i["task_id"], i.get("line")) for i in items if i["kind"] == "ask"}


def _roles(api, task_id):
    with api.app.state.store.read() as c:
        return TRo.roles_of(c, task_id)


def test_the_migration_moves_each_human_once(dev):
    """A company task with a Human and no assignee: that person is its assignee and gets its unnamed OK. With an agent
    assignee, or on a private task, the `human` role holds the person and the OK goes down the ladder."""
    api = dev
    public = _file(api, "Add CSV export", "dev", private=False)["id"]
    agent = _file(api, "Add PDF export", "dev", private=False)["id"]
    private = _file(api, "Payroll fix", "dev", private=True, readers=[BEN])["id"]
    migration = importlib.import_module(MIGRATION)
    with api.app.state.store.transaction() as c:
        # As an older release left them: Ana named Ben each task's Human; two of them have no assignee.
        for task_id in (public, agent, private):
            c.execute("UPDATE tasks SET human_id=? WHERE id=?", (BEN, task_id))
            H._task_event(c, task_id, ANA, "human_id", None, BEN, "")
        c.execute("UPDATE tasks SET assignee_id=NULL WHERE id IN (?,?)", (public, private))
        migration.up(c)
        migration.up(c)  # again: nothing more moves
        assert c.execute("SELECT count(*) FROM tasks WHERE human_id=?", (BEN,)).fetchone()[0] == 3  # left frozen
        moved = c.execute(
            "SELECT actor_id, new FROM history_events WHERE subject_id=? AND kind='assignee_id'", (public,)
        ).fetchall()
        assert [tuple(row) for row in moved] == [(ANA, BEN)]  # as Ana named him: reach.assignee_of reads it
        assert TRo.roles_of(c, public) == {}
        assert TRo.roles_of(c, agent) == {"human": [BEN]}
        assert TRo.roles_of(c, private) == {"human": [BEN]}
    task = get(api, f"tasks/{public}")["task"]
    assert task["assignee_id"] == BEN and task["owner_id"] == "bot:builder" and "human_id" not in task
    assert get(api, f"tasks/{agent}")["task"]["assignee_id"] == "bot:builder"
    assert get(api, f"tasks/{private}", token="ben-test")["task"]["assignee_id"] is None
    mine = _asks(api, "ben-test")
    assert (public, "OK the spec") in mine
    assert not {task_id for task_id, _ in mine} & {agent, private}
    assert {(agent, "OK the spec"), (private, "OK the spec")} <= _asks(api, "ana-test")  # down the ladder


def test_a_stage_a_person_holds_asks_them_not_the_assignee(dev):
    """The holder comes before the assignee: Cara holds Review, so its unnamed OK is hers though it is for Ben."""
    api = dev
    task = _file(api, "Review the export", "review", owner="ben")
    assert (task["owner_id"], task["assignee_id"]) == (CARA, BEN)
    assert (task["id"], "OK the spec") in _asks(api, "cara-test")
    assert task["id"] not in {task_id for task_id, _ in _asks(api, "ben-test")}


def test_a_person_assignee_gets_the_ok_the_agents_wake_names_them_and_a_new_assignee_moves_needs_you(dev):
    """Ben shapes his feature and hands it on: the Build agent holds it, Ben stays its assignee, so Build's unnamed OK
    is in his Needs you and the agent's wake tells it to ask him. A new assignee moves both people's Needs you."""
    api = dev
    task = _file(api, "Bulk export", "feature", owner="ben", token="ben-test")
    assert (task["owner_id"], task["assignee_id"]) == (BEN, BEN)
    moved = post(api, f"tasks/{task['id']}/done", {"version": task["version"]}, token="ben-test")
    assert (moved["owner_id"], moved["assignee_id"]) == ("bot:builder", BEN)
    assert (task["id"], "OK the spec") in _asks(api, "ben-test")
    with api.app.state.store.read() as c:
        wake = c.execute(
            "SELECT body FROM messages WHERE conversation_id=? AND recipient_id='bot:builder' AND kind='notice' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (moved["conversation_id"],),
        ).fetchone()[0]
    assert "is this task's assignee, so ask them the questions and OKs it needs from a person (--to human:ben)." in wake
    agent = _file(api, "Add PDF export", "dev")  # an agent assignee is never named so
    with api.app.state.store.read() as c:
        assert "this task's assignee" not in TW.body(c, E.task(c, agent["id"]), to="bot:builder")
    with api.app.state.store.transaction() as c:
        mark = c.execute("SELECT coalesce(max(seq), 0) FROM changes").fetchone()[0]
        E._write(c, task["id"], assignee_id=CARA)
        told = {r[0] for r in c.execute("SELECT subject_id FROM changes WHERE topic='needs' AND seq>?", (mark,))}
    assert {BEN, CARA} <= told
    assert task["id"] not in {task_id for task_id, _ in _asks(api, "ben-test")}
    assert (task["id"], "OK the spec") in _asks(api, "cara-test")


def test_human_on_create_and_update_sets_only_the_human_role(api):
    """`human` names a person in the `human` role and nothing else: the assignee and the ladder stay; "" or null takes
    the role off, an edit without it keeps it; a bot, nobody known or someone who may not edit the task is refused."""
    t = post(api, "tasks", {"owner": "ops", "title": "Add CSV export", "body": "x", "human": "ben"})
    assert (t["assignee_id"], t["owner_id"]) == ("bot:ops", "bot:ops") and "human_id" not in t
    assert _roles(api, t["id"]) == {"human": [BEN]}
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "human": "cara"})
    assert _roles(api, t["id"]) == {"human": [CARA]}
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "title": "Add CSV export now"})
    assert _roles(api, t["id"]) == {"human": [CARA]}  # not sent: kept
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "human": ""})
    assert _roles(api, t["id"]) == {}
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "human": "ben"})
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "human": None})
    assert _roles(api, t["id"]) == {} and t["assignee_id"] == "bot:ops"
    for wrong in ("ops", "nobody-here"):
        post(api, "tasks/" + t["id"], {"version": t["version"], "human": wrong}, expected=422)
        post(api, "tasks", {"owner": "ops", "title": "Add PDF export", "body": "x", "human": wrong}, expected=422)
    post(api, "tasks/" + t["id"], {"version": t["version"], "human": "cara"}, token="cara-test", expected=403)
    assert _roles(api, t["id"]) == {}


def test_human_naming_someone_who_cannot_read_a_private_task_is_refused(api):
    """The role never adds a reader: on create, on edit, and in the edit that makes the task private."""
    body = {"owner": "ops", "title": "Payroll fix", "body": "x", "private": True}
    refused = post(api, "tasks", {**body, "human": "ben"}, expected=422)
    assert "cannot read this private task" in str(refused)
    t = post(api, "tasks", {**body, "human": "ben", "readers": [BEN]})
    assert _roles(api, t["id"]) == {"human": [BEN]}
    t = post(api, "tasks", {**body, "title": "Payroll report"})
    post(api, "tasks/" + t["id"], {"version": t["version"], "human": "ben"}, expected=422)
    assert _roles(api, t["id"]) == {}
    t = post(api, "tasks", {**body, "title": "Payroll export", "private": False})
    edit = {"version": t["version"], "human": "ben", "private": True}
    assert "cannot read this private task" in str(post(api, "tasks/" + t["id"], edit, expected=422))
    assert _roles(api, t["id"]) == {}
