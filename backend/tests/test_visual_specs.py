"""Visual specs (backend/visual_specs.py): a spec is a page on a Product roadmap card, published with its styles inlined.
Tickets filed under the card (the builder ticket the pipeline's spawn link files) and their children (review, QA) get the
spec's link and text. Approval is the pipeline's own "Spec approved" requirement, tested in test_pipeline_e2e."""

from backend import hubdb as H
from backend import pipeline_links
from backend import visual_specs as VS
from backend.tests.test_files import turn
from backend.tests.test_roadmaps import _item
from backend.tests.test_tasks_board import api, get, headers, post  # noqa: F401  (fixture)

HTML = (
    b"<html><head><title>Export</title><script>alert('x')</script></head>"
    b"<body><h1>Exports</h1><p>Pick a range, then <b>download</b>.</p></body></html>"
)


def publish_spec(api, item, bot="cpo", who="ana-test", data=HTML, spec="true"):
    """Publishes the spec page on the card, as the card's builder on a new turn."""
    attempt = turn(api, bot, who)[1]
    return api.post(
        "/api/v2/pages",
        params={"name": "exports.html", "task": item["id"], "spec": spec},
        content=data,
        headers={**headers(attempt["token"]), "Content-Type": "text/html"},
    )


def file_under(api, parent, title="Build it", body="Do the work."):
    child = post(
        api,
        "tasks",
        {"owner": "cpo", "title": title, "body": body, "relations": [{"task": parent["id"], "kind": "parent"}]},
    )
    return child, get(api, "tasks/" + child["id"])["task"]["body"]


def test_styles_are_inlined_before_the_head_closes_and_the_page_keeps_its_markup():
    out = VS.inline_styles(HTML).decode()
    assert out.index("<style>") < out.index("</head>")
    assert ":root" in out  # tokens.css defines the variables the rest of the styles read
    assert "<h1>Exports</h1>" in out
    assert VS.page_text(HTML) == "Exports Pick a range, then download."


def test_a_spec_goes_on_a_product_roadmap_card_only(api):
    general = post(api, "tasks", {"owner": "cpo", "title": "Not a card", "body": "x"})
    r = publish_spec(api, general)
    assert r.status_code == 422, r.text


def test_the_published_spec_is_stored_with_its_styles_as_the_cards_spec(api):
    item = _item(api, "Exports stored", step="Spec")
    assert publish_spec(api, item).status_code == 200
    with api.app.state.store.read() as c:
        spec = VS.spec_page(c, item["id"])
    assert spec is not None
    assert b":root" in VS._files.blobs.get(spec["digest"])


def test_a_ticket_filed_under_the_card_and_its_review_child_get_the_spec_link_and_text(api):
    item = _item(api, "Exports child", step="Spec")
    assert publish_spec(api, item).status_code == 200
    ticket, body = file_under(api, item)
    assert body.startswith("Do the work.")
    assert "Spec page: " in body and "/#/page/" in body and "Pick a range, then download." in body
    _, review = file_under(api, ticket, title="Review it", body="Check the diff.")
    assert "Spec page: " in review and "Pick a range, then download." in review


def test_a_card_without_a_spec_leaves_the_body_alone(api):
    item = _item(api, "No spec", step="Spec")
    _, body = file_under(api, item)
    assert body == "Do the work."


def test_a_narrowed_spec_gives_the_link_and_not_the_text(api):
    item = _item(api, "Exports narrowed", step="Spec")
    assert publish_spec(api, item).status_code == 200
    with api.app.state.store.transaction() as c:
        live = VS.spec_page(c, item["id"])
        c.execute("UPDATE pages SET audience=? WHERE file_id=?", ('{"people":["ana"]}', live["id"]))
    _, body = file_under(api, item)
    assert "Spec page: " in body and "Pick a range" not in body


def test_a_private_card_gives_the_link_and_not_the_text(api):
    item = _item(api, "Exports private", step="Spec")
    assert publish_spec(api, item).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1, readers=? WHERE id=?", ('["human:ana"]', item["id"]))
        card = H.task(c, item["id"])
        spec = VS.spec_page(c, item["id"])
        assert VS._restricted(c, card, spec["id"])


def test_a_neutral_spawn_and_its_children_carry_nothing_of_the_spec(api):
    item = _item(api, "Exports neutral", step="Spec")
    assert publish_spec(api, item).status_code == 200
    neutral, body = file_under(api, item, body=pipeline_links.NEUTRAL_BODY)
    assert body == pipeline_links.NEUTRAL_BODY
    _, child = file_under(api, neutral, title="Review it", body="Check the diff.")
    assert child == "Check the diff."
