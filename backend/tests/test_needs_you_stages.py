"""A stage's review (the ▶ on a stage, ui/app/decide.ts): a page of Needs you counts what waits on the person in each
stage over the whole list (`stages`), so a stage's ▶ shows its count before every page is read."""

from backend.tests.helpers.api import get, post
from backend.views import needs_stages


def product(api):
    stage = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    record = {
        "name": "Product",
        "goal": "Specs that ship",
        "stages": [{"id": "spec", "name": "Spec", **stage}, {"id": "build", "name": "Build", **stage}],
    }
    return post(api, "pipelines/product", record)["pipeline"]


def test_a_page_counts_each_stages_items_over_the_whole_list(api):
    product(api)
    made = [
        post(api, "tasks", {"title": f"Spec {n}", "body": "As discussed.", "owner": "ana", "pipeline": "product"})
        for n in range(3)
    ]
    post(api, f"tasks/{made[0]['id']}/move", {"to": "build", "why": "The spec is in the PR"})
    page = get(api, "needs-you?limit=1")
    assert len(page["items"]) == 1 and page["next"]
    assert page["stages"] == {"spec": 2, "build": 1}


def test_a_paused_cause_or_an_item_with_no_task_is_in_no_stage():
    task = {"stage_id": "spec"}
    items = [
        {"kind": "ask", "task": task},
        {"kind": "task", "task": task},
        {"kind": "mention", "task": task},
        {"kind": "mention", "task": None},
        {"kind": "paused", "task": task},
        {"kind": "task", "task": {"stage_id": None}},
    ]
    assert needs_stages(items) == {"spec": 3}
