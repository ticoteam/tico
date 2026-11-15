"""An OK that names nothing to show shows the task's page from an earlier stage (#3784): Product's OK release, which
follows QA, read as the bare task title on Tico Team (task #72) and Tico's opinion said "Ask shows no release content".
The page goes only to a reader who may open it (files.readable_page_refs), the opinion is read from it only when every
reader of the ask may read it, and a new version of it is a new ask for the opinion. Synthetic rows only."""

import json
import threading

import pytest

from backend import flow_opinions as F
from backend import pipeline_settings as PS
from backend import task_calls as TC
from backend import task_engine as E
from backend import updates
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, post, unrelated_task_managers
from backend.tests.helpers.page_requirement import _page
from backend.tests.helpers.product_engineering_presets import _answers, _team

ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"  # owner, bot administrator, everyone else
GOOD = {"pick": "approve", "line": "Approve. QA shows the cart kept after a restart.", "note": "", "score": 60}
QA_TEXT = "<h1>QA</h1><p>The cart is kept after the tab closes: before and after shots.</p>"
STARTED: list[threading.Thread] = []


def _stages(person):
    return (
        {"name": "QA", "owner": "bot:writer", "requirements": [{"kind": "page", "line": "QA page", "who_oks": "none"}]},
        {
            "name": "OK release",
            "owner": "bot:writer",
            "requirements": [{"kind": "ok", "line": "OK the release", "person": person, "who_oks": "people_only"}],
        },
    )


def _attach(c, task_id, fid, version):
    """The page that counts for the open stage's page requirement, as task_pages.attach leaves it."""
    row = E.task(c, task_id)
    rid = c.execute("SELECT id FROM requirements WHERE stage_id=? AND kind='page'", (row["stage_id"],)).fetchone()[0]
    c.execute(
        "UPDATE requirement_pages SET replaced_at=? WHERE visit_id=? AND requirement_id=? AND replaced_at IS NULL",
        (H.now(), row["visit"]["id"], rid),
    )
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,?,'{}',?,?)",
        (H.new_id(), row["visit"]["id"], rid, fid, version, "bot:writer", H.now()),
    )


def _released(api, people=None, qa=True, person=BEN):
    """A task that had its QA page attached in QA (`people`: the page's own audience) and is now in OK release, where
    `person`'s OK names nothing to show. (task id, page file id)."""
    store = api.app.state.store
    with store.transaction() as c:
        X.bot(c, "writer")
        made = X.pipeline(c, "rel", "Releases", _stages(person), manager=ANA)
        task = X.new_task(c, ANA, "Saved carts", "rel", assignee="bot:writer")
        fid, version = _page(c, store, QA_TEXT)
        c.execute("UPDATE bot_files SET scope=?, task_id=? WHERE id=?", ("task:" + task["id"], task["id"], fid))
        if people:
            c.execute(
                "INSERT INTO pages(file_id, audience, created_at, updated_at) VALUES (?,?,?,?)",
                (fid, json.dumps({"people": people}), H.now(), H.now()),
            )
        if qa:
            _attach(c, task["id"], fid, version)
        E.move(c, task["id"], made["stages"]["OK release"], ANA, "QA is done")
    return task["id"], fid


def _ask(api, task_id, token="ben-test"):
    items = get(api, "needs-you", token=token)["items"]
    return next(i for i in items if i["kind"] == "ask" and i["task_id"] == task_id)


def _entry(api, task_id, token):
    task = get(api, f"tasks/{task_id}", token=token)["task"]
    return next(e for e in task["to_leave"] if e["kind"] == "ok")


def test_an_ok_with_nothing_to_show_shows_the_page_from_the_stage_before(api):
    task_id, fid = _released(api)
    ask = _ask(api, task_id)["ask"]
    assert ask["shows"] == [] and "page" not in ask
    assert ask["earlier"] == {
        "file_id": fid,
        "version": 1,
        "ref": f"page:{fid}@1",
        "stage_id": "rel-qa",
        "stage": "QA",
        "source": [f"page:{fid}@1"],
    }
    assert _entry(api, task_id, "ben-test")["earlier"]["stage"] == "QA", "the task's own read shows it too"


def test_with_no_earlier_page_the_ok_shows_the_task_as_before(api):
    task_id, _ = _released(api, qa=False)
    assert "earlier" not in _ask(api, task_id)["ask"]


def test_a_reader_who_may_not_open_the_page_gets_the_ok_without_it(api):
    task_id, _ = _released(api, people=["ana"], person=CARA)
    item = _ask(api, task_id, token="cara-test")
    assert item["ask"]["line"] == "OK the release" and "earlier" not in item["ask"], "the ask stays, the page goes"
    entry = _entry(api, task_id, "cara-test")
    assert entry["line"] == "OK the release"
    assert "earlier" not in entry, "no reference to it at all"
    assert _entry(api, task_id, "ana-test")["earlier"]["stage"] == "QA", "Ana, in its audience, still reads it"


def _private_home(api, fid):
    """The page now lives on another task of Ana's, a private one, and has no audience of its own: who may open it is
    who may read that task."""
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c, "writer")
        home = H.task_create(c, ANA, "Salary review", "Private.", ANA, private=True, lint=False)
        c.execute("UPDATE bot_files SET scope=?, task_id=? WHERE id=?", ("task:" + home["id"], home["id"], fid))


def test_a_page_another_private_task_holds_reaches_no_one_who_may_not_open_it(api):
    """Not only a page's own audience: a page read as another, private task is checked through the file's own read,
    in Needs you and in the task's read, and the OK shows the task's text."""
    task_id, fid = _released(api, person=CARA)
    _private_home(api, fid)
    assert api.get(f"/api/v2/files/{fid}?v=1", headers=headers("cara-test")).status_code in (403, 404)
    assert "earlier" not in _ask(api, task_id, token="cara-test")["ask"]
    assert "earlier" not in _entry(api, task_id, "cara-test")
    assert _entry(api, task_id, "ana-test")["earlier"]["file_id"] == fid, "Ana reads that task, and the page"


def test_attached_through_the_route_from_another_private_task_it_still_reaches_no_one_who_may_not_open_it(api):
    """The same page attached in QA through POST /api/v2/tasks/{id}/pages, as a person would."""
    task_id, fid = _released(api, person=CARA, qa=False)
    _private_home(api, fid)
    with api.app.state.store.transaction() as c:
        E.move(c, task_id, "rel-qa", ANA, "Attach the QA page")
        row = E.task(c, task_id)
        rid = c.execute("SELECT id FROM requirements WHERE stage_id='rel-qa' AND kind='page'").fetchone()[0]
    post(api, f"tasks/{task_id}/pages", {"page": f"page:{fid}@1", "requirement": rid, "version": row["version"]})
    with api.app.state.store.transaction() as c:
        E.move(c, task_id, "rel-ok-release", ANA, "QA done")
    assert api.get(f"/api/v2/files/{fid}?v=1", headers=headers("cara-test")).status_code in (403, 404)
    assert "earlier" not in _entry(api, task_id, "cara-test")
    assert "earlier" not in _ask(api, task_id, token="cara-test")["ask"]


def test_a_bots_ask_and_a_page_ok_never_borrow_an_earlier_page(api):
    task_id, _ = _released(api)
    with api.app.state.store.transaction() as c:
        row = E.task(c, task_id)
        asked = H.task_ask(c, "bot:writer", task_id, BEN, "OK the spend?", kind="ok", seen_version=row["version"])
        requirement = c.execute("SELECT * FROM requirements WHERE id=?", (asked["requirement_id"],)).fetchone()
        assert TC.earlier_page(c, task_id, row["visit"]["id"], dict(requirement)) is None
        page_requirement = {"id": "r", "kind": "page", "visit_id": None, "shows": None}
        assert TC.earlier_page(c, task_id, row["visit"]["id"], page_requirement) is None
        named = {"id": "r", "kind": "ok", "visit_id": None, "shows": ["page:other@1"]}
        assert TC.earlier_page(c, task_id, row["visit"]["id"], named) is None


# ---- Tico's opinion ------------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def threads(monkeypatch):
    """Each opinion is worked out on its own thread, as in production: a commit hook runs inside the write it follows."""
    STARTED.clear()

    def spawn(work):
        STARTED.append(threading.Thread(target=work))
        STARTED[-1].start()

    monkeypatch.setattr(F, "spawn", spawn)


class Model:
    def __init__(self):
        self.prompts: list[str] = []
        self.model = "acme-opinion-model"

    def __call__(self, system, user):
        self.prompts.append(user)
        return GOOD


def _opinion(api, item_id):
    post(api, "flow/opinions", {"items": [item_id]}, token="ben-test")
    for thread in STARTED:
        thread.join(10)
    return post(api, "flow/opinions", {"items": [item_id]}, token="ben-test")["opinions"][0]


def test_the_opinion_reads_the_page_the_person_is_shown_and_a_new_version_is_a_new_ask(api, monkeypatch):
    writer = Model()
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: writer)
    task_id, fid = _released(api)
    item = _ask(api, task_id)["id"]
    assert _opinion(api, item)["state"] == "ready"
    assert len(writer.prompts) == 1
    assert "The cart is kept after the tab closes" in writer.prompts[0]
    assert "from an earlier stage, QA" in writer.prompts[0]
    assert _opinion(api, item)["state"] == "ready" and len(writer.prompts) == 1, "held while the page is the same"
    store = api.app.state.store
    with store.transaction() as c:  # the task goes back to QA, gets a new QA page, and comes back
        row = E.task(c, task_id)
        E.move(c, task_id, "rel-qa", ANA, "One more check")
        _, version = _page(c, store, "<h1>QA</h1><p>Second pass: the cart is kept for 30 days.</p>", fid=fid)
        _attach(c, task_id, fid, version)
        E.move(c, task_id, row["stage_id"], ANA, "QA is done again")
    item = _ask(api, task_id)["id"]
    assert _ask(api, task_id)["ask"]["earlier"]["version"] == 2
    assert _opinion(api, item)["state"] == "ready"
    assert len(writer.prompts) == 2 and "Second pass" in writer.prompts[-1]


def test_a_page_with_its_own_audience_stays_out_of_the_shared_opinion(api, monkeypatch):
    writer = Model()
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: writer)
    task_id, _ = _released(api, people=["ana", "ben"])
    found = _ask(api, task_id)
    assert found["ask"]["earlier"]["stage"] == "QA", "Ben may open it, so his screen shows it"
    assert _opinion(api, found["id"])["state"] == "ready"
    assert "The cart is kept" not in writer.prompts[0] and "earlier stage" not in writer.prompts[0]


# ---- the default Product pipeline ----------------------------------------------------------------------------------
def test_product_ok_release_shows_the_qa_page_through_the_fallback(company):
    """A stage's requirement cannot name a page (`shows` is not one of its fields: pipeline_settings.REQUIREMENT_FIELDS,
    and the page it would name does not exist until QA makes it), so the default template is unchanged and every
    company's OK release shows its QA page through the fallback, with no data migration."""
    assert "shows" not in PS.REQUIREMENT_FIELDS
    with company.transaction() as c:
        _team(c)
        _answers(c, software="yes")
        PS.ensure_presets(c)
        ok = next(s for s in PS.read(c, "product")["stages"] if s["id"] == "product-ok-release")
        assert [r["kind"] for r in ok["requirements"]] == ["ok"] and not ok["requirements"][0].get("shows")
        row = H.task_create(c, ANA, "Saved carts", "Keep the cart.", "bot:pm", pipeline="product", lint=False)
        E.move(c, row["id"], "product-qa", ANA, "On for this test")
        _attach(c, row["id"], "f-qa", 2)
        E.move(c, row["id"], "product-ok-release", ANA, "QA is done")
        view = TC.task_view(c, row["id"], ANA)
        entry = next(e for e in view["to_leave"] if e["kind"] == "ok")
        assert entry["earlier"]["ref"] == "page:f-qa@2" and entry["earlier"]["stage"] == "QA"
