"""The review page of a Dev ticket (backend/review_pages.py): the pull request line it writes, and what the page shows
with pictures, without a review-two check, with pictures from an older head, with a page that changed no screens, and
with text from the ticket that must stay text. The pass over merged tickets runs against the app's own database."""

import json

import pytest

from backend import hubdb as H, review_pages as RP
from backend.tests.test_github import api  # noqa: F401

HEAD = "a" * 40


def facts(**over: object) -> RP.Facts:
    values: dict[str, object] = {
        "title": "Dashboard",
        "owner": "builder",
        "pr_title": "Dashboard",
        "pr_url": "https://github.example/pull/21",
        "pr_number": 21,
        "head": HEAD,
        "spec": ["Shows the week."],
        "checks": "1 passed",
        "reviews": [
            RP.Review(name="Review 1", verdict="approve", head=HEAD, by="codex", filed=True),
            RP.Review(name="Review 2", verdict="approve", head=HEAD, by="claude", filed=True),
        ],
        "notes": ["Took the header fix"],
        "r2_head": HEAD,
        "shots": None,
    }
    values.update(over)
    return RP.Facts(**values)


def shots(pages: tuple[str, ...] = ("home",), pictures: bool = True, dropped: int = 0) -> RP.Shots:
    shot = RP.Shot(
        page="home",
        width="1280",
        theme="light",
        pictures={"before": b"\x89PNG-before", "after": b"\x89PNG-after"} if pictures else {},
    )
    return RP.Shots(
        manifest=True,
        pages=list(pages),
        shots=[shot] if pages else [],
        dropped=dropped,
        head=HEAD,
    )


def page(values: RP.Facts) -> str:
    return RP.render(values).decode("utf-8")


def test_the_review_line_replaces_the_one_there_or_is_added_at_the_end():
    body = "## Spec\nShows the week.\n\nReview page: none (could not build it)\n"
    assert RP.with_review_line(body, "https://t.example/#/page/p1") == (
        "## Spec\nShows the week.\n\nReview page: https://t.example/#/page/p1\n"
    )
    assert RP.with_review_line("## Spec\nx", "https://t.example/#/page/p1") == (
        "## Spec\nx\n\nReview page: https://t.example/#/page/p1\n"
    )
    assert RP.with_review_line("", "none (could not build it)") == "Review page: none (could not build it)\n"


def test_the_spec_is_the_lines_under_the_spec_heading():
    body = "## Risk\nlow\n\n## Spec\nFirst line\nSecond line\n\n## Demo steps\n1. Open\n"
    assert RP._spec(body) == ["First line", "Second line"]
    assert RP._spec("no spec here") == []


def test_a_ticket_with_no_review_two_check_has_no_pictures():
    assert "No pictures." in page(facts(shots=None))


def test_a_last_review_that_found_no_screens_changed_says_so():
    assert "No screens changed." in page(facts(shots=shots(pages=())))


def test_pictures_are_inline_data_and_nothing_else_is_an_image():
    html = page(facts(shots=shots()))
    assert "src=\"data:image/png;base64," in html
    assert "<img" in html and "src=\"http" not in html


def test_pictures_from_an_older_head_say_so_and_matching_pictures_do_not():
    older = page(facts(shots=shots(), r2_head="b" * 40))
    assert "Pictures from bbbbbbb, before the last fixes." in older
    current = page(facts(shots=shots()))
    assert "before the last fixes" not in current


def test_pictures_left_out_for_size_are_counted_in_a_line():
    assert "2 pictures left out to keep the page small." in page(facts(shots=shots(dropped=2)))


def test_a_review_that_was_not_filed_or_has_no_verdict_yet_reads_plainly():
    html = page(facts(reviews=[RP.Review(name="Review 1", verdict="", head="", by="", filed=False)]))
    assert "Review 1: not filed" in html
    pending = page(facts(reviews=[RP.Review(name="Review 1", verdict="", head="", by="", filed=True)]))
    assert "Review 1: no verdict yet" in pending


def test_text_from_the_ticket_and_its_pull_request_is_escaped():
    html = page(facts(title="<script>x</script>", notes=["<img src=x onerror=1>"]))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html


# ------------------------------------------------------------------------------------------------ the pass, in the app's database
class Reply:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class FakeGitHub:
    """The GitHub App's side of a pass: reads a pull request, edits its body (answering `patch_status`), mints tokens.
    `edits` keeps the body of every edit attempt, failed ones included."""

    def __init__(self, store, patch_status: int = 200):
        self.store = store
        self.patch_status = patch_status
        self.edits: list[str] = []

    def mint(self, repos, perms):
        return ["token"]

    def _call(self, method, path, headers=None, **kwargs):
        if method == "GET":
            return Reply(
                200,
                {
                    "title": "Dashboard",
                    "body": "## Spec\nShows the week.",
                    "head": {"sha": HEAD},
                    "html_url": "https://github.example/pull/1",
                    "number": 1,
                },
            )
        self.edits.append(kwargs["json"]["body"])
        return Reply(self.patch_status)


class FakePages:
    """Stands in for backend/pages.py: publish gives a new file id and keeps who published which ticket."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, request, identity, fields, body):
        self.published.append((identity.actor, fields["task"]))
        return {"page": {"file_id": f"page-{len(self.published)}"}}


def dev_type(c, name: str) -> str:
    return H.type_create(
        c,
        H.KEEPER,
        name,
        [
            {"name": "Build", "status": "open"},
            {"name": "Release", "status": "review", "flow": {"on_merge": True}},
        ],
    )["id"]


def queued(api, owners: list[str], start: int = 100) -> list[str]:
    """A merged Dev pull request per owner, its page queued the way the merge queues it. Returns the link ids."""
    store = api.app.state.store
    ids: list[str] = []
    with store.transaction() as c:
        type_id = dev_type(c, f"Dev tickets from {start}")
        for number, owner in enumerate(owners, start=start):
            task = H.task_create(
                c,
                "human:ana",
                f"Change {number}",
                "",
                owner,
                type=type_id,
                step="Build",
                private=False,
                mover=True,
            )
            link = H.task_link(c, "human:ana", task["id"], f"https://github.com/ticoteam/tico/pull/{number}")
            c.execute(
                "UPDATE task_links SET state='merged', detail=? WHERE id=?",
                (json.dumps({"head_sha": HEAD}), link["id"]),
            )
            assert RP.queue(c, H.task(c, task["id"]), link["id"]) is True
            ids.append(link["id"])
    return ids


def saved(api, link_id: str) -> dict:
    with api.app.state.store.read() as c:
        row = c.execute("SELECT detail FROM task_links WHERE id=?", (link_id,)).fetchone()
    return json.loads(row[0])["review_page"]


def task_of(api, link_id: str) -> str:
    with api.app.state.store.read() as c:
        return c.execute("SELECT task_id FROM task_links WHERE id=?", (link_id,)).fetchone()[0]


def due(api, link_id: str) -> None:
    """The page's retry time passes, as the time between two passes would."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.review_page.retry_at','') WHERE id=?", (link_id,))


def test_queue_is_idempotent_and_a_second_merge_event_changes_nothing(api):
    store = api.app.state.store
    (link_id,) = queued(api, ["bot:cpo"])
    first = saved(api, link_id)
    assert first["state"] == "pending"
    with store.transaction() as c:
        task = H.task(c, task_of(api, link_id))
        assert RP.queue(c, task, link_id) is False
    assert saved(api, link_id) == first
    with store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.review_page.state','done') WHERE id=?", (link_id,))
        assert RP.queue(c, H.task(c, task_of(api, link_id)), link_id) is False
    assert saved(api, link_id)["state"] == "done"


def test_a_pass_takes_only_pending_due_pages_and_no_more_than_twenty(api):
    store = api.app.state.store
    ids = queued(api, ["bot:cpo"] * 25)
    gh, pages = FakeGitHub(store), FakePages()
    with store.transaction() as c:  # rows a pass must leave alone: done, failed, not yet due, not merged
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.review_page.state','done') WHERE id=?", (ids[0],))
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.review_page.state','failed') WHERE id=?", (ids[1],))
        c.execute(
            "UPDATE task_links SET detail=json_set(detail,'$.review_page.retry_at','2999-01-01T00:00:00.000000Z') WHERE id=?",
            (ids[2],),
        )
        c.execute("UPDATE task_links SET state='open' WHERE id=?", (ids[3],))
    eligible = ids[4:]
    skipped = {task_of(api, i) for i in ids[:4]}

    first = RP.tick(gh, pages=pages, blobs=None)
    assert len(first) == 20  # PER_PASS
    assert set(first) <= set(eligible)
    second = RP.tick(gh, pages=pages, blobs=None)
    assert set(second) == set(eligible) - set(first)

    published = {task for _, task in pages.published}
    assert published == {task_of(api, i) for i in eligible}
    assert not published & skipped


def test_a_paced_pass_runs_at_most_once_every_sixty_seconds(api, monkeypatch):
    store = api.app.state.store
    monkeypatch.setattr(RP, "_next", {"at": 0.0}, raising=False)
    gh, pages = FakeGitHub(store), FakePages()
    queued(api, ["bot:cpo"], start=200)
    assert RP.EVERY_S == 60
    assert len(RP.tick(gh, pages=pages, blobs=None, paced=True)) == 1  # the first paced pass runs
    queued(api, ["bot:cpo"], start=201)
    assert RP.tick(gh, pages=pages, blobs=None, paced=True) == []  # a paced pass inside the minute does nothing
    assert len(RP.tick(gh, pages=pages, blobs=None)) == 1  # an unpaced pass (the factory sim's) is not held back
    queued(api, ["bot:cpo"], start=202)
    monkeypatch.setattr(RP, "_next", {"at": 0.0}, raising=False)  # the minute has passed
    assert len(RP.tick(gh, pages=pages, blobs=None, paced=True)) == 1


def test_a_failed_edit_after_the_publish_keeps_the_page_and_publishes_no_second(api):
    store = api.app.state.store
    gh, pages = FakeGitHub(store, patch_status=403), FakePages()
    (link_id,) = queued(api, ["bot:cpo"])

    assert RP.tick(gh, pages=pages, blobs=None) == []
    state = saved(api, link_id)
    assert (state["state"], state["file_id"], state["tries"]) == ("pending", "page-1", 1)

    due(api, link_id)
    assert RP.tick(gh, pages=pages, blobs=None) == []
    assert len(pages.published) == 1
    state = saved(api, link_id)
    assert (state["state"], state["file_id"], state["tries"]) == ("pending", "page-1", 2)
    assert len(gh.edits) == 2  # the edit again, and nothing else


def test_the_last_try_marks_the_page_failed_and_no_pass_tries_it_again(api):
    store = api.app.state.store
    gh, pages = FakeGitHub(store, patch_status=403), FakePages()
    (link_id,) = queued(api, ["bot:cpo"])

    for _ in range(RP.TRIES):
        due(api, link_id)
        RP.tick(gh, pages=pages, blobs=None)
    state = saved(api, link_id)
    assert (state["state"], state["reason"], state["file_id"]) == (
        "failed",
        "could not update the pull request",
        "page-1",
    )
    assert gh.edits[-1].splitlines()[-1] == "Review page: none (could not update the pull request)"

    edits = len(gh.edits)
    due(api, link_id)
    assert RP.tick(gh, pages=pages, blobs=None) == []
    assert len(pages.published) == 1
    assert len(gh.edits) == edits
    assert saved(api, link_id)["state"] == "failed"


@pytest.mark.parametrize("owner", ["human:ana", "bot:ghost"])
def test_a_ticket_with_no_bot_owner_fails_at_once_with_no_page(api, owner):
    store = api.app.state.store
    gh, pages = FakeGitHub(store), FakePages()
    (link_id,) = queued(api, ["bot:cpo"])
    task_id = task_of(api, link_id)
    with store.transaction() as c:  # a person, or a bot since removed from the registry, now owns the ticket
        c.execute("UPDATE tasks SET owner_id=? WHERE id=?", (owner, task_id))

    assert RP.tick(gh, pages=pages, blobs=None) == []
    state = saved(api, link_id)
    assert (state["state"], state["reason"]) == ("failed", "ticket has no bot owner")
    assert pages.published == []
    assert gh.edits[-1].splitlines()[-1] == "Review page: none (ticket has no bot owner)"
    assert RP.tick(gh, pages=pages, blobs=None) == []
