"""Visual specs (backend/visual_specs.py): a spec is a page on the task it describes, published with its styles inlined.
Tasks filed under that task, and their own sub-tasks, get the spec's link and text."""

from backend import hubdb as H
from backend import visual_specs as VS
from backend.tests.helpers.files import turn
from backend.tests.helpers.tasks_board import api, get, headers, post  # noqa: F401  (fixture)

HTML = (
    b"<html><head><title>Export</title><script>alert('x')</script></head>"
    b"<body><h1>Exports</h1><p>Pick a range, then <b>download</b>.</p></body></html>"
)


def publish_spec(api, task_id, bot="cpo", who="ana-test", data=HTML, spec="true", attempt=None):
    """Publishes the spec page on the task, as its owner bot on a new turn (or on `attempt`, a turn already
    claimed: a bot goes on a computer once per test)."""
    attempt = attempt or turn(api, bot, who)[1]
    return api.post(
        "/api/v2/pages",
        params={"name": "exports.html", "task": task_id, "spec": spec},
        content=data,
        headers={**headers(attempt["token"]), "Content-Type": "text/html"},
    )


def described(api, title):
    return post(api, "tasks", {"owner": "cpo", "title": title, "body": "Spec."})


def file_under(api, parent, title="Build it", body="Do the work."):
    child = post(api, "tasks", {"owner": "cpo", "title": title, "body": body, "parent": parent["id"]})
    return child, get(api, "tasks/" + child["id"])["task"]["body"]


def test_styles_are_inlined_before_the_head_closes_and_the_page_keeps_its_markup():
    out = VS.inline_styles(HTML).decode()
    assert (
        out.index("<head>") < out.index("<style>") < out.index("<title>")
    )  # first in the head: the page's own styles win
    assert out.rindex(VS.PAGE_BODY) < out.index("</style>")  # the app shell's body grid is undone
    assert ":root" in out  # tokens.css defines the variables the rest of the styles read
    assert "<h1>Exports</h1>" in out
    assert VS.page_text(HTML) == "Exports Pick a range, then download."


def test_a_spec_goes_on_a_task(api):
    attempt = turn(api, "cpo", "ana-test")[1]
    assert publish_spec(api, "", attempt=attempt).status_code == 422
    assert publish_spec(api, "not-a-task", attempt=attempt).status_code == 422


def test_the_published_spec_is_stored_with_its_styles_as_the_tasks_spec(api):
    item = described(api, "Exports stored")
    assert publish_spec(api, item["id"]).status_code == 200
    with api.app.state.store.read() as c:
        spec = VS.spec_page(c, item["id"])
    assert spec is not None
    assert b":root" in VS._files.blobs.get(spec["digest"])


def test_a_task_filed_under_it_and_that_tasks_own_sub_task_get_the_spec_link_and_text(api):
    item = described(api, "Exports child")
    assert publish_spec(api, item["id"]).status_code == 200
    ticket, body = file_under(api, item)
    assert body.startswith("Do the work.")
    assert "Spec page: " in body and "/#/page/" in body and "Pick a range, then download." in body
    _, review = file_under(api, ticket, title="Review it", body="Check the diff.")
    assert "Spec page: " in review and "Pick a range, then download." in review


def test_an_archived_spec_is_not_handed_down(api):
    item = described(api, "Exports archived")
    assert publish_spec(api, item["id"]).status_code == 200
    with api.app.state.store.transaction() as c:
        live = VS.spec_page(c, item["id"])
        c.execute("UPDATE bot_files SET archived=1 WHERE id=?", (live["id"],))
    _, body = file_under(api, item)
    assert body == "Do the work."


def test_a_task_without_a_spec_leaves_the_body_alone(api):
    item = described(api, "No spec")
    _, body = file_under(api, item)
    assert body == "Do the work."


def test_a_narrowed_spec_gives_the_link_and_not_the_text(api):
    item = described(api, "Exports narrowed")
    assert publish_spec(api, item["id"]).status_code == 200
    with api.app.state.store.transaction() as c:
        live = VS.spec_page(c, item["id"])
        c.execute("UPDATE pages SET audience=? WHERE file_id=?", ('{"people":["ana"]}', live["id"]))
    _, body = file_under(api, item)
    assert "Spec page: " in body and "Pick a range" not in body
    assert "Spec: " not in body  # nor the page's title


class _Blobs:
    def get(self, digest):
        return HTML


class _Files:
    blobs = _Blobs()


def test_a_company_task_hidden_by_its_bots_access_gives_the_link_and_not_the_text(company, monkeypatch):
    """A company task owned by a bot only some may read is hidden from everyone else (auth.task_row). A task filed
    under it by someone who reads it, for a bot everyone reads, gets the spec's link but neither its title nor its
    text."""
    from backend.tests.helpers import engine as X
    from backend.tests.helpers.api import restrict

    with company.transaction() as c:
        for slug in ("cpo", "ops"):
            X.bot(c, slug)
        everyone = {"everyone": True}
        restrict(c, "cpo", see=everyone, read={"people": ["ana"]}, write={"people": ["ana"]})
        top = H.task_create(c, "human:ana", "Acquire Initech", "Spec.", "bot:cpo", lint=False)
        monkeypatch.setattr(VS, "_files", _Files())
        monkeypatch.setattr(
            VS,
            "spec_page",
            lambda c, task_id: {"id": "f1", "title": "Initech terms", "digest": "d"} if task_id == top["id"] else None,
        )
        child = H.task_create(
            c, "human:ana", "Draft the letter", "Do the work.", "bot:ops", parent_id=top["id"], lint=False
        )
    assert "Spec page: " in child["body"]
    assert "Pick a range" not in child["body"] and "Initech terms" not in child["body"]
