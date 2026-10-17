"""Who changes a task's labels: a mover any label; a bot a classification label on a task it manages (its own, one
it asked for, or one of a type it manages); a label on a public roadmap or one Tico acts on keeps its gate."""

from backend import hubdb as H
from backend.tests.test_roadmaps import public_setup
from backend.tests.test_tasks_board import api, bot_token, get, headers, post  # noqa: F401 (fixture)


def manage(api, type_name, slug):
    """`slug` manages the type, as the Product roadmap type's manager is set for Head of Product."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=? WHERE name=?", (slug, type_name))


def label(api, task_id, labels, token, expected=200, **extra):
    task = get(api, "tasks/" + task_id)["task"]
    return post(api, "tasks/" + task_id, {"version": task["version"], "labels": labels, **extra},
                token=token, expected=expected)


def refusals(api, slug):
    with api.app.state.store.read() as c:
        return [(r[0], r[1]) for r in c.execute("SELECT rule, detail_json FROM refusals WHERE actor=?", ("bot:" + slug,))]


def state(api, slug):
    with api.app.state.store.read() as c:
        return H.bot(c, slug)["state"]


def test_a_bot_adds_and_removes_a_classification_label_on_its_own_roadmap_item(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research exports", "body": "x", "type": "Product roadmap"})
    assert label(api, item["id"], ["area-exports"], token)["labels"] == ["area-exports"]
    assert label(api, item["id"], [], token)["labels"] == []
    assert refusals(api, "cpo") == [] and state(api, "cpo") == "active"
    # One it asked for, and one of a type it manages, the same way.
    asked = post(api, "tasks", {"owner": "ops", "title": "Size the exports work", "body": "x"}, token=token)
    assert label(api, asked["id"], ["priority-high"], token)["labels"] == ["priority-high"]
    other = post(api, "tasks", {"owner": "cmo", "title": "Research imports", "body": "x", "type": "Product roadmap"})
    label(api, other["id"], ["area-imports"], token, expected=403)
    manage(api, "Product roadmap", "cpo")
    assert label(api, other["id"], ["area-imports"], token)["labels"] == ["area-imports"]


def test_a_public_roadmap_label_takes_an_approval_both_ways_and_is_never_applied_silently(api):
    public_setup(api)
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research search", "body": "x", "type": "Product roadmap"})
    refused = label(api, item["id"], ["roadmap", "area-search"], token, expected=403)["error"]
    assert refused["code"] == "label"
    assert "roadmap is a public roadmap label" in refused["detail"] and "publication-preview" in refused["detail"]
    assert "area-search you may change on its own" in refused["detail"]
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == []
    preview = post(api, "tasks/" + item["id"] + "/publication-preview", {"labels": ["roadmap"]}, token=token)
    approval = post(api, "approvals", {"kind": "publish", "task_id": item["id"], "payload": preview["payload"]}, token=token)
    label(api, item["id"], ["roadmap"], token, expected=422, approval_id=approval["id"])   # not decided yet
    post(api, "approvals/" + approval["id"], {"decision": "approved"})
    assert label(api, item["id"], ["roadmap"], token, approval_id=approval["id"])["labels"] == ["roadmap"]
    # Taking it off the roadmap is checked the same way, and an approval is used once.
    label(api, item["id"], [], token, expected=403)
    label(api, item["id"], [], token, expected=422, approval_id=approval["id"])
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == ["roadmap"]
    # A team that does not ask bots for approvals leaves the public labels to a person.
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    refused = label(api, item["id"], [], token, expected=403)["error"]
    assert "a person on the leadership, product, engineering teams changes it" in refused["detail"]
    assert label(api, item["id"], [], "ben-test")["labels"] == []          # a person who is a mover, as before


def test_a_label_tico_acts_on_stays_a_movers_even_on_the_bots_own_task(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research filters", "body": "x"})
    refused = label(api, item["id"], ["goal:on"], token, expected=403)["error"]
    assert refused["code"] == "label" and "how Tico runs the task" in refused["detail"]
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == []


def test_a_bot_labels_no_private_task_it_cannot_see_nor_a_ticket_it_does_not_manage(api):
    manage(api, "Engineering", "ops")
    cpo = bot_token(api, "cpo")
    hidden = post(api, "tasks", {"owner": "ops", "title": "Review the contract", "body": "x", "private": True})
    label(api, hidden["id"], ["qa"], cpo, expected=404)
    ticket = post(api, "tasks", {"owner": "cmo", "title": "Build exports", "body": "x", "type": "Engineering"})
    refused = label(api, ticket["id"], ["qa"], cpo, expected=403)["error"]
    assert refused["code"] == "label" and "owns, asked for or whose type it manages" in refused["detail"]
    ops = bot_token(api, "ops")
    assert label(api, ticket["id"], ["qa"], ops)["labels"] == ["qa"]      # the type's manager
    assert label(api, hidden["id"], ["qa"], ops)["labels"] == ["qa"]      # its own private task


def test_a_bot_still_changes_no_lane_or_number_and_a_mover_is_unchanged(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research sorting", "body": "x"})
    for change in ({"lane": "company"}, {"number": 991}, {"lane": "company", "labels": ["area-sort"]}):
        r = api.post("/api/v2/tasks/" + item["id"], json={"version": item["version"], **change}, headers=headers(token))
        assert r.status_code == 403 and r.json()["error"]["code"] == "identity", r.text
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == []
    other = post(api, "tasks", {"owner": "cmo", "title": "Draft the newsletter", "body": "x"})
    moved = post(api, "tasks/" + other["id"], {"version": other["version"], "labels": ["goal:on", "news"],
                                              "number": 991}, token="ben-test")
    assert moved["labels"] == ["goal:on", "news"] and moved["number"] == 991
    label(api, other["id"], [], "priya-test", expected=403)               # a person who is not a mover, as before


def test_a_repeated_label_refusal_counts_once_per_task_an_hour_but_other_repeats_still_quarantine(api):
    token = bot_token(api, "cpo")
    ticket = post(api, "tasks", {"owner": "cmo", "title": "Build imports", "body": "x", "type": "Engineering"})
    for _ in range(H.QUARANTINE_AT + 2):
        label(api, ticket["id"], ["qa"], token, expected=403)
    assert state(api, "cpo") == "active"
    assert sum(rule == "label" for rule, _ in refusals(api, "cpo")) == H.QUARANTINE_AT + 2, "each is still recorded"
    item = post(api, "tasks", {"owner": "cpo", "title": "Research exports", "body": "x"})
    for _ in range(H.QUARANTINE_AT + 1):
        r = api.post("/api/v2/tasks/" + item["id"], json={"version": item["version"], "number": 992}, headers=headers(token))
        assert r.status_code == 403, r.text
    assert state(api, "cpo") == "quarantined"
