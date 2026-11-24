"""Tico sorts Needs you (backend/needs_order.py): the list shows at once in the person's saved order, new items go in by
the quick rule, and only the page's ask (`POST /api/v2/needs-you/sort`) claims one background model call per person and
hands it to a thread, when the order is over 6 hours old or holds no place for an item and the last sort is over 15
minutes old. The ask never builds the list or waits; the result is saved, the claim cleared on every path, and a `needs`
change pushed."""

import http.client
import json
import socket
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend import auth as auth_module
from backend import events as E
from backend import needs_order as N
from backend import updates
from backend.store import H
from backend.tests.helpers.api import get, post
from clients import judge as J
from clients.judge import JudgeError, Written

ANA, BEN = "human:ana", "human:ben"


class Model:
    """A writer that answers `order` (item numbers as the prompt lists them), or raises it, and keeps each prompt."""

    def __init__(self, order):
        self.order, self.prompts, self.model = order, [], "acme-order-model"

    def __call__(self, system, user):
        self.prompts.append(user)
        if isinstance(self.order, Exception):
            raise self.order
        return Written({"order": self.order}, {"prompt_tokens": 900, "completion_tokens": 12})


@pytest.fixture
def started(monkeypatch):
    """The sorts handed to a thread, run by the test when it says."""
    found = []
    monkeypatch.setattr(N, "spawn", found.append)
    return found


def model(monkeypatch, order):
    found = Model(order)
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: found)
    return found


def ask(api, title, due=None):
    """Ana asks Ben for an OK on a task of hers; its task id."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, ANA, title, "Body.", ANA, due=due, lint=False)
        H.task_ask(c, ANA, task["id"], BEN, f"OK {title}?", kind="ok", seen_version=H.task(c, task["id"])["version"])
    return task["id"]


def read(api, token="ben-test"):
    """The page opens: the list (its task ids), then its ask to sort with the ids it shows; whether Tico sorts it."""
    found = get(api, "needs-you", token=token)
    asked = post(api, "needs-you/sort", {"ids": [i["id"] for i in found["items"]]}, token=token)
    return [i["task_id"] for i in found["items"]], asked["sorting"]


def row(api):
    with api.app.state.store.read() as c:
        found = c.execute("SELECT * FROM needs_you_orders WHERE person_id=?", (BEN,)).fetchone()
        return dict(found) if found else None


def age(api, **when):
    """Move the saved row's times back: `sorted_at` and `started_at` by minutes ago (None clears), and `updated_at` with
    them, to the latest of those: the row was last written then."""
    moved = [m for m in when.values() if m is not None]
    if moved and "updated_at" not in when:
        when["updated_at"] = min(moved)
    with api.app.state.store.transaction() as c:
        for column, minutes in when.items():
            at = None if minutes is None else H.shift(H.now(), minutes=-minutes)
            c.execute(f"UPDATE needs_you_orders SET {column}=? WHERE person_id=?", (at, BEN))


def pushed(api):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT kind FROM changes WHERE topic='needs' AND subject_id=?", (BEN,))]


def depends(api, blocker, private=False):
    """An open task of Ana's that waits on `blocker`; private, Ben may not read it."""
    with api.app.state.store.transaction() as c:
        waiting = H.task_create(c, ANA, f"Ship after {blocker}", "Body.", ANA, private=private or None, lint=False)
        c.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'depends_on',?)",
            (waiting["id"], blocker, H.now()),
        )


def test_sorted_in_the_background_saved_reused_and_new_items_placed_until_the_next_sort(api, monkeypatch, started):
    writer = model(monkeypatch, [2, 1])
    a = ask(api, "the launch note")
    b = ask(api, "the price list")

    # Nothing saved: the quick rule (oldest first) shows at once, and one sort is claimed and handed to a thread.
    assert read(api) == ([a, b], True)
    assert row(api)["started_at"] is not None and not writer.prompts, "the ask claimed it; the thread asks"
    assert get(api, "needs-you", token="ben-test")["sorting"] is True, "the list says so while the claim holds"
    assert read(api) == ([a, b], True) and len(started) == 1, "one sort per person at a time"
    started.pop()()
    assert len(writer.prompts) == 1 and "the launch note" in writer.prompts[0]
    assert row(api)["started_at"] is None and pushed(api)[-1] == "sorted"
    with api.app.state.store.read() as c:
        spent = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='judge.spend'")]
    assert [s["label"] for s in spent] == ["needs-you-order"] and "usage" not in spent[0], "metered at its usage"

    # Saved: shown in the model's order, and reused with no call.
    assert read(api) == ([b, a], False) and not started

    # New items under 15 minutes after the sort: placed by the quick rule, no call. Due soon first, then one that holds
    # up a task Ben may read, above the saved order; one that holds up only a task he may not read goes below it.
    later = ask(api, "the brochure")
    hidden = ask(api, "the contract draft")
    blocker = ask(api, "the contract")
    due = ask(api, "the invoice", due=H.shift(H.now(), hours=20))
    depends(api, blocker)
    depends(api, hidden, private=True)
    assert read(api) == ([due, blocker, b, a, later, hidden], False) and not started

    # Past 15 minutes: one call for the new items; it hears which item holds work up.
    age(api, sorted_at=16)
    with api.app.state.store.transaction() as c:  # an id ranked before, gone for good (answered, done)
        kept = json.loads(row(api)["item_ids"]) + ["v:gone"]
        c.execute("UPDATE needs_you_orders SET item_ids=? WHERE person_id=?", (json.dumps(kept), BEN))
    assert read(api)[1] is True and len(started) == 1
    writer.order = [6, 5, 4, 3, 2, 1]
    started.pop()()
    assert "2. the contract | ask: OK the contract? | waiting since" in writer.prompts[-1]
    assert "by Ana | holds up 1 other task" in writer.prompts[-1]
    assert writer.prompts[-1].count("holds up") == 1, "a task Ben may not read is never named to the model"
    assert read(api) == ([hidden, later, a, b, blocker, due], False)
    assert "v:gone" not in json.loads(row(api)["item_ids"]), "only a snoozed id keeps its place"

    # Over 6 hours old: the next read sorts again, once.
    age(api, sorted_at=7 * 60)
    assert read(api)[1] is True and len(started) == 1


def test_a_worker_whose_claim_was_taken_makes_no_call(api, monkeypatch, started):
    """A worker that waited past its claim (on the lane, say) while another took the list asks no model: one paid
    call per claim."""
    writer = model(monkeypatch, [2, 1])
    ask(api, "the launch note"), ask(api, "the price list")
    assert read(api)[1] is True
    age(api, started_at=0.5)  # another claim since
    started.pop()()
    assert not writer.prompts and row(api)["started_at"] is not None, "the other claim stands"


def test_the_claim_holds_while_the_call_runs_and_the_call_is_one_attempt(api, monkeypatch, started):
    """The claim is cleared only once the call has returned, and the call runs inside one attempt (`J.one_try`), whose
    connection is shut at DEADLINE, so it returns before the claim could run out."""
    gate, inside, attempt = threading.Event(), threading.Event(), []

    def slow(system, user):
        attempt.append(J._WATCHES.get() is not None)
        inside.set()
        gate.wait(10)
        return Written({"order": [2, 1]}, {})

    slow.model = "acme-order-model"
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: slow)
    a, b = ask(api, "the launch note"), ask(api, "the price list")
    assert read(api)[1] is True
    worker = threading.Thread(target=started.pop())
    worker.start()
    assert inside.wait(10)
    assert row(api)["started_at"] is not None and read(api)[1] is True and not started, "held while the call runs"
    gate.set()
    worker.join(10)
    assert attempt == [True] and row(api)["started_at"] is None
    assert read(api) == ([b, a], False)


def test_the_llm_writer_is_held_to_the_attempts_deadline(monkeypatch):
    """Inside an attempt the company provider's writer opens through the deadline's opener, waiting no longer than
    what is left of it."""
    opened = []

    def deadline_open(request, timeout):
        opened.append(timeout)
        raise J.JudgeError("unavailable", "stop here")

    monkeypatch.setattr(J, "urlopen", deadline_open)
    monkeypatch.setattr(J.urllib.request, "urlopen", lambda *a, **k: pytest.fail("not held to the deadline"))
    write = J.llm_writer("openai", "sk-test", "acme-model")
    with pytest.raises(J.JudgeError), J.one_try(5):
        write("system", "user")
    assert len(opened) == 1 and opened[0] <= 5


def test_the_model_reads_the_list_as_it_is_when_the_sort_runs(api, monkeypatch, started):
    """An ask the person snoozed (or lost) after their read never reaches the model."""
    writer = model(monkeypatch, [2, 1])
    ask(api, "the launch note"), ask(api, "the price list"), ask(api, "the brochure")
    assert read(api)[1] is True
    item = next(i["id"] for i in get(api, "needs-you", token="ben-test")["items"] if "brochure" in i["line"])
    post(api, "needs-you/snooze", {"id": item, "until": H.shift(H.now(), days=1)}, token="ben-test")
    started.pop()()
    assert "brochure" not in writer.prompts[0] and "the launch note" in writer.prompts[0]


def test_no_call_once_the_list_is_no_longer_due_or_the_person_may_no_longer_sign_in(api, monkeypatch, started):
    """The worker checks again on its own read: a new item gone by then (snoozed, answered) leaves nothing to sort,
    and a person who left or whose sign-in was turned off since their read is not read for at all."""
    writer = model(monkeypatch, [2, 1])
    ask(api, "the launch note"), ask(api, "the price list")
    assert read(api)[1] is True
    started.pop()()
    assert len(writer.prompts) == 1
    age(api, sorted_at=20)
    ask(api, "the brochure")
    assert read(api)[1] is True, "a new item, the last sort 20 minutes ago"
    item = next(i["id"] for i in get(api, "needs-you", token="ben-test")["items"] if "brochure" in i["line"])
    post(api, "needs-you/snooze", {"id": item, "until": H.shift(H.now(), days=1)}, token="ben-test")
    sorted_at = row(api)["sorted_at"]
    started.pop()()
    assert len(writer.prompts) == 1 and row(api)["started_at"] is None and pushed(api)[-1] == "sorted"
    assert row(api)["sorted_at"] == sorted_at, "nothing was sorted: the order is as old as it was"

    age(api, sorted_at=7 * 60)
    assert read(api)[1] is True
    monkeypatch.setattr(auth_module, "standing", lambda c, pid: "off")
    started.pop()()
    assert len(writer.prompts) == 1 and row(api)["started_at"] is None


def test_a_deadline_outside_an_attempt_leaves_no_watch_running(monkeypatch):
    """Only an attempt (`one_try`) watches its connection, and it ends the watch with it: a bare `within` starts none,
    so no timer is left to shut a pooled connection later."""
    made = []

    class Kept:
        def __init__(self, sock, seconds):
            made.append(seconds)

        def cancel(self):
            pass

    monkeypatch.setattr(J, "_Watch", Kept)
    with socket.create_server(("127.0.0.1", 0)) as server:
        address = server.getsockname()
        with J.within(5):
            J._HTTPByDeadline("127.0.0.1")._connect(address).close()
        assert made == []
        with J.one_try(5):
            J._HTTPByDeadline("127.0.0.1")._connect(address).close()
    assert len(made) == 1


def test_an_answer_cut_short_is_unavailable():
    """A connection shut mid-answer (the attempt's watch) raises IncompleteRead: the writer says `unavailable`."""

    class Cut:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *size):
            raise http.client.IncompleteRead(b"{")

    write = J.llm_writer("openai", "sk-test", "acme-model", opener=lambda request, timeout: Cut())
    with pytest.raises(JudgeError) as caught:
        write("system", "user")
    assert caught.value.code == "unavailable"


def test_a_snoozed_id_keeps_its_place_and_one_gone_for_good_is_dropped(api):
    """An ask snoozed while a sort ran comes back where it was: first stays first. An id that left the list for good
    (answered, done) is dropped, so none pile up; an unchanged list is never "new" again."""
    store, now = api.app.state.store, H.now()
    with store.transaction() as c:
        c.execute(
            "INSERT INTO needs_you_orders (person_id, item_ids, started_at, created_at, updated_at) VALUES (?,?,?,?,?)",
            (BEN, json.dumps(["v:snoozed", "v:a", "v:b", "v:done", "v:later", "v:c"]), now, now, now),
        )
        for requirement in ("snoozed", "later"):
            c.execute(
                "INSERT INTO ask_snoozes (person_id, visit_id, requirement_id, task_id, asked_at, ask_version, "
                "snoozed_until, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (BEN, "v", requirement, "", now, "", H.shift(now, days=1), now, now),
            )
    shown = ["v:c", "v:b", "v:a", "v:snoozed"]  # snoozed while the model ordered it: its saved place still wins
    N._save(store, BEN, shown, now, True)
    assert json.loads(row(api)["item_ids"]) == ["v:snoozed", "v:c", "v:b", "v:a", "v:later"]
    with store.read() as c:
        assert N.due(N._saved(c, BEN), shown, H.shift(H.now(), minutes=16)) is False


def test_an_item_of_a_task_the_person_snoozed_keeps_its_place(api):
    """A task a person snoozed hides its asks; a sort meanwhile keeps their places, so they come back where they were,
    not as new."""
    a, _b = ask(api, "the launch note"), ask(api, "the price list")
    ids = [i["id"] for i in get(api, "needs-you", token="ben-test")["items"]]
    store, now = api.app.state.store, H.now()
    with store.transaction() as c:
        c.execute(
            "INSERT INTO needs_you_orders (person_id, item_ids, started_at, created_at, updated_at) VALUES (?,?,?,?,?)",
            (BEN, json.dumps(ids), now, now, now),
        )
        c.execute("INSERT INTO snoozes (id, task_id, by_id, created_at) VALUES ('sn1', ?, ?, ?)", (a, BEN, now))
    N._save(store, BEN, ids[1:], now, True)
    assert json.loads(row(api)["item_ids"]) == ids


def test_a_demo_or_a_copy_starts_no_sort(api, started):
    """A demo or a rehearsal copy keeps nothing and asks no model: no thread, no row, never `sorting`."""
    ask(api, "the launch note"), ask(api, "the price list")
    store = api.app.state.store
    ben, kept = store.settings.test_identities["ben-test"], store.settings
    ids = [i["id"] for i in get(api, "needs-you", token="ben-test")["items"]]
    for flag in ("demo", "rehearsal"):
        store.settings = replace(kept, **{flag: True})
        try:
            assert N.start(store, api.app.state.auth, ben, ids) == {"sorting": False, "sorting_until": None}
        finally:
            store.settings = kept
    assert not started and row(api) is None


def test_the_model_orders_the_top_of_a_long_list_and_an_answer_cut_short_is_tried_again(api, monkeypatch, started):
    """The model orders only the first RANK_MAX items, whose numbers fit its answer; the rest keep their order after
    them. An answer cut short (not JSON) keeps the shown order without stamping it fresh: tried again 15 minutes on,
    never on every open."""
    monkeypatch.setattr(N, "RANK_MAX", 2)
    writer = model(monkeypatch, [2, 1])
    a, b, c_ = ask(api, "the launch note"), ask(api, "the price list"), ask(api, "the brochure")
    assert read(api)[1] is True
    started.pop()()
    assert "the brochure" not in writer.prompts[0] and "2. the price list" in writer.prompts[0]
    assert read(api) == ([b, a, c_], False)

    writer.order = JudgeError("shape", "the model did not answer with JSON")
    age(api, sorted_at=7 * 60)
    stale = row(api)["sorted_at"]
    assert read(api)[1] is True
    started.pop()()
    assert len(writer.prompts) == 2 and row(api)["sorted_at"] == stale and row(api)["started_at"] is None
    assert read(api) == ([b, a, c_], False) and not started, "no paid call again on the next open"
    age(api, updated_at=16)
    assert read(api)[1] is True, "15 minutes on, still due: tried again"


def test_a_sort_builds_its_list_on_the_needs_you_lane(api, monkeypatch, started):
    """The worker's list build takes its turn on the Needs you lane (backend/needs_lane.py), with the routes' builds."""
    from concurrent.futures import ThreadPoolExecutor

    from backend import views

    model(monkeypatch, [2, 1])
    ask(api, "the launch note"), ask(api, "the price list")
    built, shown = [], views.needs_shown
    monkeypatch.setattr(
        views, "needs_shown", lambda *a, **k: built.append(threading.current_thread().name) or shown(*a, **k)
    )
    lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="needs-lane-test")
    monkeypatch.setattr(api.app.state, "needs_lane", lane, raising=False)
    try:
        assert read(api)[1] is True
        built.clear()
        started.pop()()
    finally:
        lane.shutdown()
    assert built and all(name.startswith("needs-lane-test") for name in built)


def test_a_failed_call_or_a_lost_claim_never_leaves_the_list_sorting(api, monkeypatch, started):
    model(monkeypatch, JudgeError("unavailable", "down"))
    a, b = ask(api, "the launch note"), ask(api, "the price list")
    assert read(api)[1] is True
    started.pop()()
    saved = row(api)
    assert saved["started_at"] is None and len(json.loads(saved["item_ids"])) == 2 and pushed(api)[-1] == "sorted"
    assert read(api) == ([a, b], False), "a failure keeps the shown order and ends the sort"

    # A claim whose process died: sorting while it holds, with when it runs out; then not, and no call for 15 minutes.
    age(api, sorted_at=None, started_at=1)
    found = get(api, "needs-you", token="ben-test")
    assert found["sorting"] is True and found["sorting_until"] == H.shift(row(api)["started_at"], seconds=120)
    age(api, started_at=3)
    found = get(api, "needs-you", token="ben-test")
    assert (found["sorting"], found["sorting_until"]) == (False, None)
    assert read(api)[1] is False and not started
    age(api, started_at=7 * 60)
    assert read(api)[1] is True and len(started) == 1


def test_only_the_persons_own_ask_starts_a_sort_and_it_builds_no_list(api, monkeypatch, started):
    """A list read never starts a sort, nor does a view's or an agent's ask; the person's own ask checks their saved
    row alone, never building the list (the worker reads it)."""
    from backend import views

    writer = model(monkeypatch, [2, 1])
    a = ask(api, "the launch note")
    assert read(api) == ([a], False), "one item has one order: no call"
    b = ask(api, "the price list")
    assert get(api, "needs-you", token="ben-test")["sorting"] is False and get(
        api, "needs-you?limit=50", token="ben-test"
    )
    store = api.app.state.store
    ben = store.settings.test_identities["ben-test"]
    ids = [i["id"] for i in get(api, "needs-you", token="ben-test")["items"]]
    for who in (
        replace(ben, view="view-1"),
        replace(ben, via="botops", task_actor="bot:botops"),
        replace(ben, via="assistant"),  # their Assistant, even with their rights: only their own page asks
    ):
        assert N.start(store, api.app.state.auth, who, ids) == {"sorting": False, "sorting_until": None}
    assert not started and not writer.prompts and row(api) is None
    real = views.needs_items
    monkeypatch.setattr(views, "needs_items", lambda *a, **k: pytest.fail("the ask built the list"))
    many = ids + [f"v:x{n}" for n in range(600)]  # a list past 500 items is sorted too
    assert post(api, "needs-you/sort", {"ids": many}, token="ben-test")["sorting"] is True and len(started) == 1
    monkeypatch.setattr(views, "needs_items", real)
    started.pop()()
    assert read(api) == ([b, a], False)


def test_a_new_order_has_open_pages_read_the_list_whole(api, started):
    a, b, c_ = ask(api, "one"), ask(api, "two"), ask(api, "three")
    store, auth = api.app.state.store, api.app.state.auth
    ben = store.settings.test_identities["ben-test"]
    with store.read() as c:
        held = E.needs_baseline(c, auth, ben, None)
    ids = list(held["marks"])
    with store.transaction() as c:
        c.execute(
            "INSERT INTO needs_you_orders (person_id, item_ids, sorted_at, created_at, updated_at) VALUES (?,?,?,?,?)",
            (BEN, json.dumps(ids[::-1]), H.now(), H.now(), H.now()),
        )
    assert read(api) == ([c_, b, a], False)
    fresh = ask(api, "four")  # a new ask in the same batch as the new order
    with store.read() as c:
        view = SimpleNamespace(c=c, auth=auth, who=ben, mine={"threads": {}, "needs": {}})
        [(_, entry)] = E._needs_list(view, [{"seq": 1}], None, held)
    assert entry["moved"] is True, "no ids say a place changed: read it whole"
    new = [x["id"] for x in get(api, "needs-you", token="ben-test")["items"] if x["task_id"] == fresh]
    assert entry["changed"] == new and entry["removed"] == [], "the new ask is still named, for its notice"
