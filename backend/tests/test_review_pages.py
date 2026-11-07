"""The review page of a Dev ticket (backend/review_pages.py): the pull request line it writes, and what the page shows
with pictures, without a review-two check, with pictures from an older head, with a page that changed no screens, and
with text from the ticket that must stay text."""

from backend import review_pages as RP

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
