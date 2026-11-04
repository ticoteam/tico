"""Work piling up at a step is measured by the step's backlog setting (backend/step_backlog.py): counted for the step or for
each owner, and named to the type's manager, the owner, one level up from the owner, or a named bot or person. `warnings`
says which steps are over and sends nothing; the task roundup lists each warning on the bots it names (test_task_roundup.py)."""
import pytest

from backend import step_backlog as SB
from backend.store import H
from backend.tests.test_api import api, restrict  # noqa: F401


def board(c, backlog):
    typ = H.type_create(c, H.KEEPER, "Studio work", [{"name": "Inbox", "status": "open"},
                                                     {"name": "Build", "status": "doing"}], bots="work")
    c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
    inbox = typ["steps"][0]
    H._type_steps(c, H.KEEPER, typ["id"], [{"id": inbox["id"], "name": "Inbox", "status": "open",
                                            "flow": {"backlog": backlog}},
                                           {"id": typ["steps"][1]["id"], "name": "Build", "status": "doing"}])
    return typ


def tickets(c, typ, owner, n):
    return [H.task_create(c, "human:ana", f"Ticket {owner} {i}", "x", owner, type=typ["id"], step="Inbox", lint=False)
            for i in range(n)]


def test_a_step_is_warned_only_over_its_backlog_limit(api):
    with api.app.state.store.transaction() as c:
        typ = board(c, {"over": 3, "notify": ["manager"], "every_hours": 4})
        tickets(c, typ, "bot:ops", 3)
        assert SB.warnings(c) == [], "at the limit is not over it"
        tickets(c, typ, "bot:cpo", 1)
        [warning] = SB.warnings(c)
        assert (warning["step"], warning["owner"], warning["count"], warning["targets"]) == ("Inbox", None, 4, ["bot:coo"])
        assert warning["every_hours"] == 4
        assert warning["text"].startswith("Backlog: 4 tickets wait at Inbox (over 3; ops 3, cpo 1)")


def test_per_owner_counts_each_owner_and_names_the_owner_one_level_up_and_a_named_person(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='coo' WHERE bot='ops'")
        typ = board(c, {"over": 2, "per": "owner", "notify": ["owner", "owner_manager", "human:ana"]})
        tickets(c, typ, "bot:ops", 3)
        tickets(c, typ, "bot:cpo", 2)
        [warning] = SB.warnings(c)
        assert (warning["step"], warning["owner"], warning["count"], warning["targets"]) == (
            "Inbox", "bot:ops", 3, ["bot:ops", "bot:coo", "human:ana"])


def test_a_private_ticket_changes_nothing_a_warning_says(api):
    with api.app.state.store.transaction() as c:
        typ = board(c, {"over": 3, "per": "step", "notify": ["manager"]})
        hidden = H.task_create(c, "human:ana", "Private plan", "x", "bot:cpo", type=typ["id"], step="Inbox",
                               private=True, lint=False)
        tickets(c, typ, "bot:ops", 3)
        assert SB.warnings(c) == [], "the private ticket does not push the step over"
        H.task_create(c, "human:ana", "Ticket bot:ops 3", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        [warning] = SB.warnings(c)
        assert (warning["count"], warning["targets"]) == (4, ["bot:coo"])
        assert warning["text"].startswith("Backlog: 4 tickets wait at Inbox (over 3; ops 4)")
        assert "cpo" not in warning["text"]
        assert warning["first"]["id"] != hidden["id"] and not H.task_private(c, warning["first"]), \
            "a private ticket never hosts the warning"


def test_a_ticket_of_a_bot_not_every_member_reads_is_not_counted(api):
    with api.app.state.store.transaction() as c:
        restrict(c, "cpo", people=["human:ana"])
        typ = board(c, {"over": 3, "per": "step", "notify": ["manager"]})
        [hidden] = tickets(c, typ, "bot:cpo", 1)
        tickets(c, typ, "bot:ops", 3)
        assert SB.warnings(c) == [], "the restricted bot's ticket does not push the step over"
        H.task_create(c, "human:ana", "Ticket bot:ops 3", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        [warning] = SB.warnings(c)
        assert warning["count"] == 4
        assert warning["first"]["id"] != hidden["id"], "the oldest ticket is the restricted bot's, and it never hosts the warning"
        assert "cpo" not in warning["text"]


def test_a_ticket_a_restricted_bot_asked_for_is_not_counted(api):
    with api.app.state.store.transaction() as c:
        restrict(c, "cpo", people=["human:ana"])
        typ = board(c, {"over": 3, "per": "step", "notify": ["manager"]})
        [asked] = tickets(c, typ, "bot:ops", 1)
        c.execute("UPDATE tasks SET requester_id='bot:cpo' WHERE id=?", (asked["id"],))
        H.task_create(c, "human:ana", "Ticket bot:ops 1", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        H.task_create(c, "human:ana", "Ticket bot:ops 2", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        H.task_create(c, "human:ana", "Ticket bot:ops 3", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        assert SB.warnings(c) == [], "a ticket the restricted bot asked for does not push the step over"


def test_per_owner_a_private_ticket_never_makes_an_owner_over(api):
    with api.app.state.store.transaction() as c:
        typ = board(c, {"over": 2, "per": "owner", "notify": ["owner"]})
        tickets(c, typ, "bot:ops", 2)
        H.task_create(c, "human:ana", "Private plan", "x", "bot:ops", type=typ["id"], step="Inbox", private=True,
                      lint=False)
        assert SB.warnings(c) == []


@pytest.mark.parametrize("backlog", [{"over": -1}, {"over": 3, "per": "team"}, {"over": 3, "notify": ["owner"]},
                                     {"over": 3, "notify": ["bot:nobody"]}, {"over": 3, "every_hours": 0}])
def test_a_backlog_setting_that_cannot_work_is_refused(api, backlog):
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused):
            board(c, backlog)
