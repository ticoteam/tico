"""Visual specs (backend/visual_specs.py): a spec is a page on the task it describes, published with its styles inlined.
A task has no description, so nothing copies a spec into the tasks filed under it."""

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
    return post(api, "tasks", {"owner": "cpo", "title": title, "message": "Spec."})


def file_under(api, parent, title="Build it", message="Do the work."):
    child = post(api, "tasks", {"owner": "cpo", "title": title, "message": message, "parent": parent["id"]})
    return child, get(api, "tasks/" + child["id"])["task"]


def test_styles_are_inlined_before_the_head_closes_and_the_page_keeps_its_markup():
    out = VS.inline_styles(HTML).decode()
    assert (
        out.index("<head>") < out.index("<style>") < out.index("<title>")
    )  # first in the head: the page's own styles win
    assert out.rindex(VS.PAGE_BODY) < out.index("</style>")  # the app shell's body grid is undone
    assert ":root" in out  # tokens.css defines the variables the rest of the styles read
    assert "<h1>Exports</h1>" in out


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


def test_a_task_filed_under_a_spec_is_its_title_and_its_first_message(api):
    item = described(api, "Exports child")
    assert publish_spec(api, item["id"]).status_code == 200
    _, task = file_under(api, item)
    assert task["request"]["text"] == "Do the work."  # the spec stays a document on its own task
    assert "body" not in task
