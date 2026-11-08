"""Work piling up at a step reaches whoever the step's backlog setting names (backend/step_backlog.py): counted for the
step or for each owner, told to the type's manager, the owner, one level up from the owner, or a named bot or person,
once per stretch over the limit until `every_hours` passes."""
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


def told(c):
    return sorted((m["recipient_id"], m["body"].split(" (")[0]) for m in c.execute(
        "SELECT recipient_id, body FROM messages WHERE body LIKE 'Backlog:%'"))


def later(hours):
    return H.shift(H.now(), hours=hours)


def test_a_step_over_its_backlog_tells_the_types_manager_once_per_stretch(api):
    with api.app.state.store.transaction() as c:
        typ = board(c, {"over": 3, "notify": ["manager"], "every_hours": 4})
        tickets(c, typ, "bot:ops", 3)
        assert SB.sweep(c, later(0)) == [], "at the limit is not over it"
        tickets(c, typ, "bot:cpo", 1)
        assert [(s, n, t) for s, _, n, t in SB.sweep(c, later(0))] == [("Inbox", 4, ["bot:coo"])]
        assert SB.sweep(c, later(1)) == [], "once while it stays over"
        assert len(SB.sweep(c, later(5))) == 1, "again after every_hours"
        assert told(c) == [("bot:coo", "Backlog: 4 tickets wait at Inbox")] * 2


def test_per_owner_counts_each_owner_and_tells_the_owner_one_level_up_and_a_named_person(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='coo' WHERE bot='ops'")
        typ = board(c, {"over": 2, "per": "owner", "notify": ["owner", "owner_manager", "human:ana"]})
        tickets(c, typ, "bot:ops", 3)
        tickets(c, typ, "bot:cpo", 2)
        [(step, owner, n, who)] = SB.sweep(c, later(0))
        assert (step, owner, n, who) == ("Inbox", "bot:ops", 3, ["bot:ops", "bot:coo", "human:ana"])


def test_a_private_ticket_changes_nothing_a_warning_says(api):
    with api.app.state.store.transaction() as c:
        typ = board(c, {"over": 3, "per": "step", "notify": ["manager"]})
        hidden = H.task_create(c, "human:ana", "Private plan", "x", "bot:cpo", type=typ["id"], step="Inbox",
                               private=True, lint=False)
        tickets(c, typ, "bot:ops", 3)
        assert SB.sweep(c, later(0)) == [], "the private ticket does not push the step over"
        H.task_create(c, "human:ana", "Ticket bot:ops 3", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        assert [(s, n, t) for s, _, n, t in SB.sweep(c, later(0))] == [("Inbox", 4, ["bot:coo"])]
        [(room, body)] = c.execute("SELECT json_extract(refs,'$.task'), body FROM messages "
                                   "WHERE body LIKE 'Backlog:%'").fetchall()
        assert body.startswith("Backlog: 4 tickets wait at Inbox (over 3; ops 4)")
        assert room != hidden["id"] and not H.task_private(c, H.task(c, room)), "never written into a private room"
        assert "cpo" not in body
        [event] = c.execute("SELECT target, json_extract(detail,'$.count') FROM events WHERE action=?",
                            (SB.EVENT,)).fetchall()
        assert tuple(event) == (room, 4)


def test_a_ticket_of_a_bot_not_every_member_reads_is_not_counted(api):
    with api.app.state.store.transaction() as c:
        restrict(c, "cpo", people=["human:ana"])
        typ = board(c, {"over": 3, "per": "step", "notify": ["manager"]})
        [hidden] = tickets(c, typ, "bot:cpo", 1)
        tickets(c, typ, "bot:ops", 3)
        assert SB.sweep(c, later(0)) == [], "the restricted bot's ticket does not push the step over"
        assert told(c) == []
        H.task_create(c, "human:ana", "Ticket bot:ops 3", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        assert [n for _, _, n, _ in SB.sweep(c, later(0))] == [4]
        [(room, body)] = c.execute("SELECT json_extract(refs,'$.task'), body FROM messages "
                                   "WHERE body LIKE 'Backlog:%'").fetchall()
        assert room != hidden["id"], "the oldest ticket is the restricted bot's, and it never hosts the warning"
        assert "cpo" not in body


def test_a_ticket_a_restricted_bot_asked_for_is_not_counted(api):
    with api.app.state.store.transaction() as c:
        restrict(c, "cpo", people=["human:ana"])
        typ = board(c, {"over": 3, "per": "step", "notify": ["manager"]})
        [asked] = tickets(c, typ, "bot:ops", 1)
        c.execute("UPDATE tasks SET requester_id='bot:cpo' WHERE id=?", (asked["id"],))
        H.task_create(c, "human:ana", "Ticket bot:ops 1", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        H.task_create(c, "human:ana", "Ticket bot:ops 2", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        H.task_create(c, "human:ana", "Ticket bot:ops 3", "x", "bot:ops", type=typ["id"], step="Inbox", lint=False)
        assert SB.sweep(c, later(0)) == [], "a ticket the restricted bot asked for does not push the step over"
        assert told(c) == []


def test_per_owner_a_private_ticket_never_makes_an_owner_over(api):
    with api.app.state.store.transaction() as c:
        typ = board(c, {"over": 2, "per": "owner", "notify": ["owner"]})
        tickets(c, typ, "bot:ops", 2)
        H.task_create(c, "human:ana", "Private plan", "x", "bot:ops", type=typ["id"], step="Inbox", private=True,
                      lint=False)
        assert SB.sweep(c, later(0)) == []
        assert told(c) == []


@pytest.mark.parametrize("backlog", [{"over": -1}, {"over": 3, "per": "team"}, {"over": 3, "notify": ["owner"]},
                                     {"over": 3, "notify": ["bot:nobody"]}, {"over": 3, "every_hours": 0}])
def test_a_backlog_setting_that_cannot_work_is_refused(api, backlog):
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused):
            board(c, backlog)
