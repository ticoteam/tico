"""A stage's limit counts bot work, not tasks waiting on a person (docs/pipelines-v3.md "Limits"): a task whose only
waits are a person's OK or answer gives up its slot, so requests keep leaving Ideas while people review. Spec:
"A stage's limit counts bot work, not tasks waiting on a person" (Tico Team, Nov 12: Spec full at 20 of 20, 15 of them
on one person's OK, and nothing could leave Ideas).

Synthetic rows only (backend/tests/helpers/engine.py). Jev is a fake verdict (page_verdicts.record); never a model.
"""

import uuid

import pytest

from backend import never_stuck as NS
from backend import page_verdicts as V
from backend import task_engine as E
from backend import task_pages as P
from backend import task_reads
from backend import waiting_on as W
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
WRITER = "bot:writer"
PRODUCT = (
    {"name": "Ideas", "owner": WRITER, "by_hand": True, "requests_move_on": True},
    {
        "name": "Spec",
        "owner": WRITER,
        "task_limit": 2,
        "requirements": [{"kind": "page", "line": "Spec page", "person": ANA}],
    },
    {"name": "Build", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}]},
)


@pytest.fixture
def store(company):
    company.blobs = Blobs(company.settings)
    return company


@pytest.fixture
def c(store):
    with store.transaction() as conn:
        for slug in ("writer", "builder", "manager"):
            X.bot(conn, slug)
        X.pipeline(conn, "product", "Product", PRODUCT, manager="bot:manager")
        yield conn


def _page(c, store):
    """A page (a Tico file) with one version. Returns its file id."""
    data = b"<h1>Spec</h1><p>Build the importer.</p>"
    digest = store.blobs.put(data, "text/html")
    now, blob, fid = H.now(), H.new_id(), "file-" + uuid.uuid4().hex[:24]
    c.execute(
        "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) VALUES (?,?,?,?,?,?,?)",
        (blob, WRITER, digest, len(data), "spec.html", "text/html", now),
    )
    c.execute(
        "INSERT INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, first_activity_at, "
        "last_activity_at, current_version) VALUES (?,?,?,?,?,'file','text/html','tico_blob',?,?,1)",
        (fid, "writer", "bot", fid, "Spec", now, now),
    )
    c.execute(
        "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, 1, blob, digest, len(data), "spec.html", "text/html", WRITER, now),
    )
    return fid


def _request(c, title):
    """A person's request in Ideas, Jev's duplicate check done: it moves into Spec by itself when Spec has room."""
    row = X.new_task(c, ANA, title, "product", assignee=WRITER)
    X.ideas_checked(c)
    return row


def _idea(c, title):
    """An idea a bot filed for itself: it is filed snoozed, and waits in Ideas until a person unsnoozes it."""
    return X.new_task(c, WRITER, title, "product", assignee=WRITER)


def _in_spec(c, title):
    """A request that went into Spec: the writer's turn (no page yet), so it holds a slot."""
    row = _request(c, title)
    moved = E.move(c, row["id"], "Spec", ANA)
    assert moved["stage"]["name"] == "Spec"
    return moved


def _attach(c, store, row):
    seen = E.task(c, row["id"])["version"]
    return P.attach(c, row["id"], "Spec page", "page:" + _page(c, store), WRITER, seen_version=seen)


def _jev_passes(c, made):
    owed = V.owe(c, made["file_id"], made["version"], made["rubric_hash"])
    V.record(c, owed["id"], {"verdict": "met", "p": 0.9, "reason": ""})


def _to_person(c, store, row):
    """The writer attaches its page and Jev passes it: the stage asks Ana to OK it, and nothing else is missing."""
    _jev_passes(c, _attach(c, store, row))
    waits = W.waiting_on(c, row["id"])
    assert [(w.state, w.who, w.ask) for w in waits] == [(W.PERSON, ANA, True)]
    return E.task(c, row["id"])


def _spec(c):
    return E.stage(c, "product-spec")


def _stage(c, row):
    return E.task(c, row["id"])["stage"]["name"]


def _pass(c):
    done = NS.check(c)
    assert NS.rules_hold(c) == []
    return done


def test_a_request_moves_into_a_spec_full_of_tasks_waiting_on_a_persons_ok(c, store):
    """Given Spec's limit is 2 and it holds 2 tasks that each wait only on a person's OK, a person's request in Ideas
    moves into Spec on the next pass."""
    for title in ("Importer spec", "Exporter spec"):
        _to_person(c, store, _in_spec(c, title))
    request = _request(c, "Export a pipeline")
    assert [(w.state, w.line) for w in W.waiting_on(c, request["id"])] == [(W.ROOM, "Moving to Spec")]
    assert _pass(c)["moved"] == 1
    assert _stage(c, request) == "Spec"
    assert W.slots(c, "product-spec").held == 1  # only the request, on the writer's turn


def test_bot_work_fills_spec_and_a_request_reads_no_room_whatever_waits_on_a_person(c, store):
    """Given Spec's limit is 2, with 2 tasks on a bot's turn and 3 waiting on a person, a request stays in Ideas and
    reads "No room in Spec"."""
    first, second = _in_spec(c, "Importer spec"), _in_spec(c, "Exporter spec")
    _to_person(c, store, first)
    _to_person(c, store, second)
    third, fourth = _in_spec(c, "Report spec"), _in_spec(c, "Search spec")
    _to_person(c, store, third)
    _in_spec(c, "Billing spec")
    slots = W.slots(c, "product-spec")
    assert (slots.held, len(slots.on_person)) == (2, 3)
    assert not E.has_room(c, _spec(c))
    request = _request(c, "Export a pipeline")
    assert [(w.state, w.line) for w in W.waiting_on(c, request["id"])] == [(W.ROOM, "No room in Spec")]
    assert _pass(c)["moved"] == 0
    assert _stage(c, request) == "Ideas" and _stage(c, fourth) == "Spec"


def test_a_spec_jev_is_checking_holds_its_slot(c, store):
    """Given a task in Spec waits on Jev's check of its page, it holds a slot."""
    row = _in_spec(c, "Importer spec")
    _attach(c, store, row)
    assert [w.state for w in W.waiting_on(c, row["id"])] == [W.JEV]
    assert W.slots(c, "product-spec").held == 1


def test_when_jev_passes_and_the_stage_asks_a_person_the_slot_frees_for_the_oldest_request(c, store):
    """Given Spec is full of bot work, when a bot attaches its page, Jev passes it and the stage asks a person to OK it,
    one slot frees: the oldest request in Ideas moves in."""
    first, _second = _in_spec(c, "Importer spec"), _in_spec(c, "Exporter spec")
    older, newer = _request(c, "Export a pipeline"), _request(c, "Import a pipeline")
    assert _pass(c)["moved"] == 0
    _to_person(c, store, first)
    assert _pass(c)["moved"] == 1
    assert (_stage(c, older), _stage(c, newer)) == ("Spec", "Ideas")


def test_when_a_spec_task_turns_to_a_person_no_bot_is_told_about_room(c, store):
    """Given Spec is full of bot work and Ideas holds only an idea a bot filed, when a Spec task's turn becomes a
    person's, then no bot is told about room, on that pass or the next, and the idea stays in Ideas."""
    first, second = _in_spec(c, "Importer spec"), _in_spec(c, "Exporter spec")
    idea = _idea(c, "Bug Triage handoffs")
    _pass(c)
    _to_person(c, store, first)
    X.settled(c)
    before = c.execute("SELECT MAX(rowid) FROM messages").fetchone()[0]
    _pass(c)
    _pass(c)
    new = c.execute(
        "SELECT wake_key, body FROM messages WHERE recipient_id LIKE 'bot:%' AND rowid > ?", (before,)
    ).fetchall()
    # Only the Spec tasks' own turns (a lost wake retried): nothing about room, nothing about the idea.
    assert all(str(key).split(":")[0] in (first["id"], second["id"]) for key, _ in new), new
    assert not any("has room" in str(body) for _, body in new), new
    assert _stage(c, idea) == "Ideas"


def test_a_spec_a_person_sends_back_keeps_its_place_and_the_stage_sits_over_its_limit(c, store):
    """Given Spec holds 2 of 2 bot-turn tasks, when a person sends back a third spec, it stays in Spec, the stage reads
    3/2, and no task enters until it is under 2. The Heartbeat's over-the-limit check is not broken by it."""
    from backend import task_calls

    third = _in_spec(c, "Importer spec")
    third = _to_person(c, store, third)
    _in_spec(c, "Exporter spec"), _in_spec(c, "Report spec")
    assert W.slots(c, "product-spec").held == 2
    page_req = c.execute("SELECT id FROM requirements WHERE stage_id='product-spec' AND kind='page'").fetchone()[0]
    task_calls.task_answer(c, ANA, third["id"], page_req, "no", "Say who it is for")
    assert [w.state for w in W.waiting_on(c, third["id"])] == [W.BOT]
    assert _stage(c, third) == "Spec"
    assert W.slots(c, "product-spec").held == 3
    request = _request(c, "Export a pipeline")
    assert _pass(c)["moved"] == 0  # _pass also asserts the seven rules hold: rule 6 allows the sent-back one
    assert _stage(c, request) == "Ideas"


def test_a_task_stuck_at_a_person_or_whose_bot_is_paused_keeps_its_slot(c, store):
    """Given a task whose ladder reached level "person", or whose bot is Paused, it holds a slot."""
    stuck, paused = _in_spec(c, "Importer spec"), _in_spec(c, "Exporter spec")
    _to_person(c, store, stuck)
    _to_person(c, store, paused)
    assert W.slots(c, "product-spec").held == 0
    c.execute("UPDATE visits SET level='person', person_id=? WHERE task_id=? AND left_at IS NULL", (ANA, stuck["id"]))
    assert W.slots(c, "product-spec").held == 1
    c.execute("UPDATE bots SET state='quarantined' WHERE id='writer'")
    assert W.slots(c, "product-spec").held == 2
    assert not E.has_room(c, _spec(c))


def test_a_task_waiting_on_a_person_whose_bot_pauses_keeps_its_place_over_the_limit(c, store):
    """Given a Spec task's turn became a person's and a request took its slot, when its bot is Paused it holds a slot
    again: the stage sits over its limit without anything moving, and the Heartbeat's over-the-limit check holds."""
    first = _in_spec(c, "Importer spec")
    _in_spec(c, "Exporter spec")
    _to_person(c, store, first)
    request = _request(c, "Export a pipeline")
    assert _pass(c)["moved"] == 1 and _stage(c, request) == "Spec"
    c.execute("UPDATE bots SET state='quarantined' WHERE id='writer'")
    assert W.slots(c, "product-spec").held == 3
    assert NS.rules_hold(c) == []


def test_moving_an_idea_into_a_spec_full_of_bot_work_by_hand_is_refused_with_why(c, store):
    """Given a person moves an idea into Spec by hand while Spec's limit of tasks are on a bot's turn, the move is
    refused and says the count is bot work."""
    _in_spec(c, "Importer spec"), _in_spec(c, "Exporter spec")
    idea = _idea(c, "Bug Triage handoffs")
    with pytest.raises(H.Refused) as refused:
        E.move(c, idea["id"], E.NEXT, ANA)
    assert str(refused.value) == (
        "task_stage_full: No room in Spec. It holds 2 a bot is working on: move it again when one leaves."
    )


def test_a_list_row_says_which_tasks_wait_only_on_a_person(c, store):
    """The board counts "Spec 5/20" and "15 wait on a person" from each list row's `on_person`."""
    waiting, working = _in_spec(c, "Importer spec"), _in_spec(c, "Exporter spec")
    _to_person(c, store, waiting)
    rows = task_reads.brief(c, [{"id": waiting["id"]}, {"id": working["id"]}])
    assert [row["on_person"] for row in rows] == [True, False]


def test_has_room_reads_no_waits_while_every_task_would_fit(c, store, monkeypatch):
    """The count stays one query until the stage is full; only then are its tasks' waits read."""
    _in_spec(c, "Importer spec")
    monkeypatch.setattr(W, "slots", lambda *_: pytest.fail("read waits with room to spare"))
    assert E.has_room(c, _spec(c))


def test_saying_which_list_rows_wait_on_a_person_reads_nothing_per_row(c, store):
    """`on_person` on list rows reads each task from the list's batch read (`waiting_on.prime`, the snapshot cache),
    not once per row: beyond the waits themselves, eight rows waiting on a person cost what two do."""

    def counted(read):
        read()  # warm the snapshot's caches, as the list's own reads do
        seen: list[str] = []
        c.set_trace_callback(seen.append)
        read()
        c.set_trace_callback(None)
        return len(seen)

    def beyond_waits(rows):
        ids = [row["id"] for row in rows]
        assert all(row["on_person"] for row in task_reads.brief(c, [{"id": i} for i in ids]))
        return counted(lambda: task_reads.brief(c, [{"id": i} for i in ids])) - counted(
            lambda: W.waiting_on_many(c, ids)
        )

    rows = [_to_person(c, store, _in_spec(c, f"Spec {n}")) for n in range(2)]
    few = beyond_waits(rows)
    rows += [_to_person(c, store, _in_spec(c, f"Spec {n}")) for n in range(2, 8)]
    assert beyond_waits(rows) <= few, few


@pytest.mark.parametrize("snapshot", [True, False], ids=["in a read transaction", "outside one"])
def test_a_list_of_fifty_rows_half_on_a_person_reads_no_task_per_row(c, store, monkeypatch, snapshot):
    """A list of 50 rows, 25 of them waiting only on a person, says which wait on a person from one batch read
    (`waiting_on.on_person_many`), never `task_engine.task` per row (P-I16, P-I39). Counted from a cold read, with the
    snapshot's cache and without it (a list read outside a transaction keeps nothing between reads): beyond its waits,
    the brief of 50 rows costs what the brief of 4 rows, 2 on a person, does. Each row's answer is `on_person`'s, a row whose snooze has ended
    included."""
    from backend import read_cache

    def cold(read):
        c.read_cache.clear()  # a new request: nothing kept from the writes that made the rows
        seen: list[str] = []
        with monkeypatch.context() as m:
            if not snapshot:
                m.setattr(read_cache, "cache", lambda conn: None)
            c.set_trace_callback(seen.append)
            try:
                read()
            finally:
                c.set_trace_callback(None)
        return len(seen)

    def beyond_waits(ids):
        return cold(lambda: task_reads.brief(c, [{"id": i} for i in ids])) - cold(lambda: W.waiting_on_many(c, ids))

    person = [_to_person(c, store, _in_spec(c, f"Spec {n}"))["id"] for n in range(25)]
    # One was snoozed and woken again: its ended snooze must not count as open, or it would hold a slot again.
    E.snooze(c, person[1], ANA)
    E.unsnooze(c, person[1], ANA)
    ideas = [_idea(c, f"Idea {n}")["id"] for n in range(25)]
    rows = task_reads.brief(c, [{"id": i} for i in person + ideas])
    each = {i for i, found in W.waiting_on_many(c, person + ideas).items() if W.on_person(c, E.task(c, i), found)}
    assert {row["id"] for row in rows if row["on_person"]} == each == set(person)
    few = beyond_waits(person[:2] + ideas[:2])
    assert beyond_waits(person + ideas) <= few, few
