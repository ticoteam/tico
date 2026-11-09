"""Who may ask for a CI permit on a ticket (POST /api/v2/ci/permits, backend/ci_runs.py): a bot working the ticket (its
owner, or a run that carries it) or a person who can read it. Another bot is refused, so it can neither spend the ticket's
minutes nor become the builder whose manager grants the ticket's one extension. A run on a private ticket asks for its
own ticket like any other run. Fictional people only: ben (requester), ana (company)."""

import itertools

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, ready, runner  # noqa: F401

_ids = itertools.count(0xC1000000)


def ticket(api, owner="bot:ops", private=False, title="Build the export"):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, "human:ben", title, "Build it.", owner, private=private, lint=False)


def run_on(api, row, bot="ops"):
    """The bot's run, carrying `row` (its work): the attempt with the bot's token."""
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    attempt = claim(api, machine, bot)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], row["id"]))
    return attempt


def ask(api, token, task_id):
    body = {"task_id": task_id, "remote_id": f"{next(_ids):012x}", "cpus": 4, "minutes": 10}
    return api.post("/api/v2/ci/permits", json=body, headers=headers(token))


def builder_runs(api, task_id):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT bot FROM ci_runs WHERE task_id=?", (task_id,))]


@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers

    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def test_a_run_on_its_own_private_ticket_gets_a_permit(api):
    row = ticket(api, private=True)
    attempt = run_on(api, row)
    r = ask(api, attempt["token"], row["id"])
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] is True
    assert builder_runs(api, row["id"]) == ["bot:ops"]


def test_the_bot_that_owns_the_ticket_gets_a_permit(api):
    row = ticket(api)
    attempt = run_on(api, ticket(api, title="Other work"))  # its run carries other work; it still owns this ticket
    r = ask(api, attempt["token"], row["id"])
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] is True


def test_a_run_carrying_the_ticket_gets_a_permit_though_another_bot_owns_it(api):
    row = ticket(api, owner="bot:ops")
    attempt = run_on(api, ticket(api, owner="bot:cpo", title="Its own work"), bot="cpo")
    with api.app.state.store.transaction() as c:  # the run takes the ops ticket on as well
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], row["id"]))
    r = ask(api, attempt["token"], row["id"])
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] is True


def test_a_bot_not_working_the_ticket_is_refused_and_records_nothing(api):
    row = ticket(api, owner="bot:ops")
    other = run_on(api, ticket(api, owner="bot:cpo", title="Its own work"), bot="cpo")
    r = ask(api, other["token"], row["id"])
    assert r.status_code == 403, r.text
    assert builder_runs(api, row["id"]) == []


def test_a_person_who_reads_the_ticket_gets_a_permit(api):
    row = ticket(api)
    r = ask(api, "ana-test", row["id"])
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] is True


def test_a_person_who_cannot_read_a_private_ticket_is_refused(api):
    row = ticket(api, private=True)
    r = ask(api, "ana-test", row["id"])
    assert r.status_code == 404, r.text
    assert builder_runs(api, row["id"]) == []


def test_a_retried_key_is_asked_again_and_refused_once_the_bot_no_longer_works_the_ticket(api):
    row = ticket(api, owner="bot:ops")
    attempt = run_on(api, ticket(api, owner="bot:cpo", title="Its own work"), bot="cpo")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], row["id"]))
    body = {"task_id": row["id"], "remote_id": f"{next(_ids):012x}", "cpus": 4, "minutes": 10}
    key = headers(attempt["token"])
    assert api.post("/api/v2/ci/permits", json=body, headers=key).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=NULL WHERE id=?", (row["id"],))
    r = api.post("/api/v2/ci/permits", json=body, headers=key)  # the same Idempotency-Key, replayed
    assert r.status_code == 403, r.text


def test_a_run_id_already_used_on_another_ticket_is_refused_not_replayed(api):
    mine, theirs = ticket(api, title="Mine"), ticket(api, title="Theirs")
    attempt = run_on(api, mine)
    remote_id = f"{next(_ids):012x}"
    first = {"task_id": theirs["id"], "remote_id": remote_id, "cpus": 4, "minutes": 10}
    assert api.post("/api/v2/ci/permits", json=first, headers=headers(attempt["token"])).status_code == 200
    again = {**first, "task_id": mine["id"]}
    r = api.post("/api/v2/ci/permits", json=again, headers=headers(attempt["token"]))
    assert r.status_code == 409, r.text
    assert builder_runs(api, mine["id"]) == []


def test_the_managers_nudge_on_a_private_ticket_names_no_title(api, monkeypatch):
    from backend import ci_runs

    row = ticket(api, private=True, title="Secret acquisition export")
    monkeypatch.setattr(ci_runs, "_above", lambda c, who: ["bot:cpo"])
    with api.app.state.store.transaction() as c:
        ci_runs._nudge(c, ci_runs._task(c, row["id"]), "bot:ops", 120, H.now())
        made = c.execute(
            "SELECT title, body FROM tasks WHERE owner_id IN ('bot:cpo','cpo') AND requester_id=? ORDER BY created_at DESC",
            (H.KEEPER,),
        ).fetchone()
    assert made is not None
    assert "Secret acquisition" not in made["title"] and "Secret acquisition" not in made["body"]
