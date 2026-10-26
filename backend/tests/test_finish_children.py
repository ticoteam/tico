"""No open subtask stays under a finished task: closing or finishing a parent closes its open subtasks all the way
down, or with children=detach makes them standalone tasks. Each is the actor's own close or detach, so a bot ends
only what it may end, and reopening the parent leaves them as they are, on the record."""
from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401  (fixtures)

ANA, CMO, SEO = "human:ana", "bot:cmo", "bot:seo"
FINISHED = ("done", "closed", "declined")


def hub(path):
    c = H.connect(path)
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ("cmo", "seo")},
                    {"people": [{"id": "ana", "email": "ana@acme.example"}, {"id": "ben", "email": "ben@acme.example"}]})
    return c


def make(c, actor, title, owner, parent=None):
    return H.task_create(c, actor, title, "", owner, parent_id=parent and parent["id"], lint=False)


def status(c, row):
    return H.task(c, row["id"])["status"]


def open_under_finished(c):
    """Every open task whose parent is finished: what this feature never leaves behind."""
    return [r[0] for r in c.execute(
        "SELECT kid.id FROM task_relations r JOIN tasks kid ON kid.id=r.from_task JOIN tasks up ON up.id=r.to_task "
        f"WHERE r.kind='parent' AND up.status IN {FINISHED} AND kid.status NOT IN {FINISHED}")]


def children_said(c, row):
    return [r[0] for r in c.execute("SELECT new FROM task_events WHERE task_id=? AND field='children' ORDER BY ts,rowid",
                                    (row["id"],))]


def test_closing_a_parent_closes_its_open_subtasks_all_the_way_down_and_wakes_no_parent(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Launch the pricing page", CMO)
    copy = make(c, CMO, "Write the pricing copy", SEO, parent)
    proof = make(c, CMO, "Proofread the pricing copy", SEO, copy)
    shipped = make(c, CMO, "Pick the plan names", SEO, parent)
    H.task_update(c, SEO, shipped["id"], status="done", note="Picked")
    late = make(c, CMO, "Check the plan names in German", SEO, shipped)    # open under a done subtask
    H.task_close(c, ANA, parent["id"], note="Launch cancelled")
    assert [status(c, t) for t in (parent, copy, proof, shipped, late)] == ["closed", "closed", "closed", "done", "closed"]
    assert open_under_finished(c) == []
    # Each says why, and points at the task whose close it was.
    assert H.task(c, proof["id"])["note"] == "Closed with its parent task"
    assert c.execute("SELECT new FROM task_events WHERE task_id=? AND field='closed_with'",
                     (proof["id"],)).fetchone()[0] == parent["id"]
    assert children_said(c, parent) == ["Closed with it: Write the pricing copy; Proofread the pricing copy; "
                                        "Check the plan names in German"]
    # The subtasks closing one by one tell nobody that all subtasks are done: the parent itself was closed.
    assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs_json,'$.wake')='subtask_finished'").fetchone()


def test_a_bot_finishing_its_task_closes_the_subtasks_it_filed_and_reopening_keeps_them_closed(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Launch the pricing page", CMO)
    ask = make(c, CMO, "Approve the final copy", ANA, parent)
    done = H.task_update(c, CMO, parent["id"], status="done", note="Page is live")
    assert (done["status"], status(c, ask)) == ("done", "closed")
    assert open_under_finished(c) == []
    # Ana hears it on the parent's notice, which names what closed with it, not once per subtask.
    told = [r[0] for r in c.execute("SELECT body FROM messages WHERE to_actor=? AND body NOT LIKE 'New task%'", (ANA,))]
    assert told == ["Finished: Launch the pricing page\nPage is live\nClosed with it: Approve the final copy"]
    # Reopening the parent does not reopen the subtask; the record says it stays closed.
    H.task_update(c, ANA, parent["id"], status="open", note="One more change")
    assert status(c, ask) == "closed"
    assert children_said(c, parent)[-1] == "Still closed from when it closed: Approve the final copy"


def test_detach_leaves_open_subtasks_open_and_standalone_with_their_own_subtrees(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Plan the offsite", CMO)
    venue = make(c, ANA, "Book the venue", SEO, parent)
    deposit = make(c, ANA, "Pay the venue deposit", SEO, venue)
    menu = make(c, ANA, "Pick the menu", SEO, parent)
    H.task_update(c, SEO, menu["id"], status="done", note="Picked")
    wine = make(c, ANA, "Order the wine", SEO, menu)                       # open under a done subtask
    H.task_update(c, ANA, parent["id"], status="done", note="Planned", children="detach")
    assert [status(c, t) for t in (parent, venue, deposit, menu, wine)] == ["done", "open", "open", "done", "open"]
    # The open ones leave the finished tree; a detached task keeps its own subtasks.
    assert (TR.parent_of(c, venue["id"]), TR.parent_of(c, wine["id"])) == (None, None)
    assert TR.parent_of(c, deposit["id"]) == venue["id"] and TR.parent_of(c, menu["id"]) == parent["id"]
    assert open_under_finished(c) == []
    assert children_said(c, parent) == ["Made standalone: Book the venue; Order the wine"]


def test_a_private_subtask_the_closer_cannot_read_goes_standalone_unnamed(tmp_path):
    c = hub(tmp_path / "hub.db")
    parent = make(c, ANA, "Plan the offsite", CMO)
    secret = H.task_create(c, CMO, "Compare the venue salaries", "", CMO, parent_id=parent["id"], private=True, lint=False)
    assert not H.task_private_readable(c, ANA, H.task(c, secret["id"]))
    # Ana's close is not held up by a task she cannot see, and does not end it either.
    H.task_close(c, ANA, parent["id"])
    assert (status(c, parent), status(c, secret), TR.parent_of(c, secret["id"])) == ("closed", "open", None)
    assert children_said(c, parent) == ["Made standalone: a private subtask"]
    assert open_under_finished(c) == []


def test_a_bot_may_not_end_a_subtask_it_may_not_close_and_the_finish_rolls_back(api):
    _, _, attempt = setup_attempt(api, "ops")
    parent = post(api, "tasks", {"owner": "ops", "title": "Ship the refund page", "body": "x"})
    # A person files a subtask under the bot's task: the bot may neither close nor detach it.
    child = post(api, "tasks", {"owner": "cpo", "title": "Approve the refund copy", "body": "x",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    for children in (None, "detach"):
        version = get(api, "tasks/" + parent["id"])["task"]["version"]
        body = {"version": version, "status": "done", "note": "Shipped", **({"children": children} if children else {})}
        r = api.post("/api/v2/tasks/" + parent["id"], json=body, headers=headers(attempt["token"]))
        assert r.status_code >= 400, r.text
        assert "Approve the refund copy" in r.text
        # Nothing of the finish stays: the parent is not done over an open subtask.
        assert get(api, "tasks/" + parent["id"])["task"]["status"] == "open"
        assert get(api, "tasks/" + child["id"])["task"]["status"] == "open"
    # The person who asked for both closes the parent, and the subtask with it.
    version = get(api, "tasks/" + parent["id"])["task"]["version"]
    post(api, "tasks/" + parent["id"], {"version": version, "close": True})
    assert get(api, "tasks/" + child["id"])["task"]["status"] == "closed"


def test_a_person_detaches_over_the_api_and_a_bad_choice_is_refused(api):
    parent = post(api, "tasks", {"owner": "ops", "title": "Ship the refund page", "body": "x"})
    child = post(api, "tasks", {"owner": "cpo", "title": "Approve the refund copy", "body": "x",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    version = get(api, "tasks/" + parent["id"])["task"]["version"]
    post(api, "tasks/" + parent["id"], {"version": version, "close": True, "children": "keep"}, expected=422)
    post(api, "tasks/" + parent["id"], {"version": version, "close": True, "children": "detach"})
    after = get(api, "tasks/" + child["id"])["task"]
    assert after["status"] == "open"
    with api.app.state.store.read() as c:
        assert TR.parent_of(c, child["id"]) is None
