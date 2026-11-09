"""Helpers that other tests share, moved out of test_step_backlog.py so they import no test file."""

from backend.store import H


def board(c, backlog):
    typ = H.type_create(
        c,
        H.KEEPER,
        "Studio work",
        [{"name": "Inbox", "status": "open"}, {"name": "Build", "status": "doing"}],
        bots="work",
    )
    c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
    inbox = typ["steps"][0]
    H._type_steps(
        c,
        H.KEEPER,
        typ["id"],
        [
            {"id": inbox["id"], "name": "Inbox", "status": "open", "flow": {"backlog": backlog}},
            {"id": typ["steps"][1]["id"], "name": "Build", "status": "doing"},
        ],
    )
    return typ


def tickets(c, typ, owner, n):
    return [
        H.task_create(c, "human:ana", f"Ticket {owner} {i}", "x", owner, type=typ["id"], step="Inbox", lint=False)
        for i in range(n)
    ]
