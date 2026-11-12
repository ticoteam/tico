"""Connect GitHub: the first task that needs a repository asks the company's Owner once (docs/github-app.md "Set up",
docs/pipelines-v3.md "What lands in your list").

A stage whose requirements read the task's PRs (item_sources.github) cannot be met while the company has no GitHub App
installed. Its bot is Paused on "Connect GitHub", nobody is woken, and the Owner has one row for every task it holds up.
Installing the App ends it, and each task's bot is woken once.

Synthetic rows only (backend/tests/helpers/engine.py).
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as NS
from backend import task_engine as E
from backend import waiting_on as W
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BUILDER, MANAGER = "bot:builder", "bot:manager"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
BUILD = (
    {
        "name": "Build",
        "owner": BUILDER,
        "expected_minutes": 1440,
        "requirements": [X.github("pr_open"), X.github("checks_pass")],
    },
)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def eng(c):
    for slug in ("builder", "manager"):
        X.bot(c, slug)
    return X.pipeline(c, "eng", "Engineering", BUILD, manager=MANAGER)


@pytest.fixture
def later(c, time_machine):
    def step(**delta):
        time_machine.shift(timedelta(**delta))
        done = NS.check(c)
        assert NS.rules_hold(c) == []
        return {key: n for key, n in done.items() if n}

    return step


def _connect(c):
    return [n for n in W.needs(c, ANA) if n["kind"] == "paused"]


def test_the_first_task_asks_the_owner_once_and_a_second_waits_on_the_same_ask(c, eng):
    first = X.new_task(c, ANA, "Add a login page", "eng")["id"]
    assert [(w.state, w.who, w.line) for w in W.waiting_on(c, first)] == [("paused", BUILDER, F.CONNECT)]
    assert [(n["line"], n["href"], n["tasks"]) for n in _connect(c)] == [(F.CONNECT, F.CONNECT_AT, [first])]

    second = X.new_task(c, ANA, "Fix the footer", "eng")["id"]
    assert W.headline(c, second).line == F.CONNECT  # its waiting-on says what it waits for
    rows = _connect(c)  # the same row, not a second ask
    assert [(n["line"], n["bots"], sorted(n["tasks"])) for n in rows] == [
        (F.CONNECT, [BUILDER], sorted([first, second]))
    ]
    assert [n["kind"] for n in W.needs(c, ANA)] == ["paused"]  # her own tasks' work is the bot's, not hers


def test_nobody_is_woken_or_offered_a_snooze_while_it_waits(c, eng, later):
    first = X.new_task(c, ANA, "Add a login page", "eng")["id"]
    second = X.new_task(c, ANA, "Fix the footer", "eng")["id"]
    for task_id in (first, second):
        assert X.wakes(c, task_id) == []  # not on entry
        assert W.turns(c, E.task(c, task_id)) == set() and W.turn(c, E.task(c, task_id), paused=True) is None
    assert later(seconds=10) == {}
    assert later(days=5) == {"late": 2}  # Late is the label only: the manager bot has no move here
    for task_id in (first, second):
        assert X.wakes(c, task_id) == []
        assert dict(H._one(c, "SELECT level, tries FROM visits WHERE task_id=?", (task_id,))) == {
            "level": "owner",
            "tries": 0,
        }
    # A bot's snooze never takes the ask out of the Owner's list.
    E.snooze(c, first, MANAGER, "Waiting on GitHub", seen_version=E.task(c, first)["version"])
    assert sorted(_connect(c)[0]["tasks"]) == sorted([first, second])


def test_connecting_github_ends_the_ask_and_wakes_each_tasks_bot_once(c, eng, later):
    first = X.new_task(c, ANA, "Add a login page", "eng")["id"]
    second = X.new_task(c, ANA, "Fix the footer", "eng")["id"]
    assert later(minutes=5) == {}
    X.github_installed(c)
    assert _connect(c) == []  # read, not stored: it goes when the cause goes
    assert [(w.state, w.who, w.line) for w in W.waiting_on(c, first)] == [("bot", BUILDER, "No PR linked")]
    assert later(seconds=10) == {"woken": 2}
    assert [len(X.wakes(c, task_id, BUILDER)) for task_id in (first, second)] == [1, 1]
    assert later(seconds=10) == {}  # once each
    assert [len(X.wakes(c, task_id, BUILDER)) for task_id in (first, second)] == [1, 1]


def test_a_company_with_github_connected_never_sees_the_ask(c, eng, later):
    X.github_installed(c)
    task_id = X.new_task(c, ANA, "Add a login page", "eng")["id"]
    assert [(w.state, w.line) for w in W.waiting_on(c, task_id)] == [("bot", "No PR linked")]
    assert len(X.wakes(c, task_id, BUILDER)) == 1  # its bot is woken on entry, as ever
    X.ran(c, task_id, BUILDER)
    later(days=5)
    assert _connect(c) == [] and W.headline(c, task_id).line != F.CONNECT


def test_needs_you_gives_the_row_a_link_to_the_github_page(api):
    from backend.tests.helpers.api import get

    with api.app.state.store.transaction() as c:
        X.bot(c, "builder")
        X.pipeline(c, "eng", "Engineering", BUILD)
        X.new_task(c, ANA, "Add a login page", "eng")
        X.new_task(c, ANA, "Fix the footer", "eng")
    items = [i for i in get(api, "needs-you")["items"] if i["kind"] == "paused"]
    assert [(i["line"], i["href"], len(i["task_ids"])) for i in items] == [(F.CONNECT, F.CONNECT_AT, 2)]


def test_with_github_installed_but_no_repository_in_use_the_owner_is_asked_to_choose_one(c, eng, later):
    X.github_installed(c, repository=None)
    first = X.new_task(c, ANA, "Add a login page", "eng")["id"]
    second = X.new_task(c, ANA, "Fix the footer", "eng")["id"]
    assert [(n["line"], n["href"], sorted(n["tasks"])) for n in _connect(c)] == [
        (F.CHOOSE, F.CHOOSE_AT, sorted([first, second]))
    ]
    assert later(minutes=5) == {} and X.wakes(c, first) == []
    # A bot's own repository is not one a task can be built in.
    c.execute("INSERT INTO repositories(id,full_name,enabled,bot_repo) VALUES('b','acme/bot-builder',1,1)")
    assert [n["line"] for n in _connect(c)] == [F.CHOOSE]
    X.github_installed(c)  # a person puts acme/app in use
    assert _connect(c) == [] and later(seconds=10) == {"woken": 2}
