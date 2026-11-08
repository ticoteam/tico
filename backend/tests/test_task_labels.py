"""Who changes a task's labels: a mover any label; a bot a classification label on a task it manages (its own, one
it asked for, or one of a type it manages); a label on a public roadmap or one Tico acts on keeps its gate."""

import json

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
        return [(r[0], r[1]) for r in c.execute("SELECT rule, detail FROM refusals WHERE actor=?", ("bot:" + slug,))]


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
    label(api, item["id"], ["roadmap"], token, expected=403, approval_id=approval["id"])   # not decided yet
    post(api, "approvals/" + approval["id"], {"decision": "approved"})
    assert label(api, item["id"], ["roadmap"], token, approval_id=approval["id"])["labels"] == ["roadmap"]
    # Taking it off the roadmap is checked the same way, and an approval is used once.
    label(api, item["id"], [], token, expected=403)
    label(api, item["id"], [], token, expected=403, approval_id=approval["id"])
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
    assert refused["code"] == "label" and "whose party it manages or whose type it manages" in refused["detail"]
    ops = bot_token(api, "ops")
    assert label(api, ticket["id"], ["qa"], ops)["labels"] == ["qa"]      # the type's manager
    assert label(api, hidden["id"], ["qa"], ops)["labels"] == ["qa"]      # its own private task
    # The manager's right is to labels only, and holds on a type whose bots do not work it.
    post(api, "task-types/Engineering", {"bots": "parties"})
    assert label(api, ticket["id"], ["qa", "area-exports"], ops)["labels"] == ["qa", "area-exports"]
    r = api.post("/api/v2/tasks/" + ticket["id"], json={"version": get(api, "tasks/" + ticket["id"])["task"]["version"],
                                                      "labels": ["qa"], "due": "2026-12-01T00:00:00Z"}, headers=headers(ops))
    assert r.status_code == 403 and r.json()["error"]["code"] == "identity", r.text


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
    # Retrying a public label with an approval that is not decided yet is the same known refusal.
    public_setup(api)
    own = post(api, "tasks", {"owner": "cpo", "title": "Research search", "body": "x", "type": "Product roadmap"})
    preview = post(api, "tasks/" + own["id"] + "/publication-preview", {"labels": ["roadmap"]}, token=token)
    pending = post(api, "approvals", {"kind": "publish", "task_id": own["id"], "payload": preview["payload"]}, token=token)
    for _ in range(H.QUARANTINE_AT + 2):
        refused = label(api, own["id"], ["roadmap"], token, expected=403, approval_id=pending["id"])["error"]
    assert refused["code"] == "label" and "does not cover this change" in refused["detail"]
    assert get(api, "tasks/" + own["id"])["task"]["labels"] == [] and state(api, "cpo") == "active"
    assert sum(rule == "label" for rule, _ in refusals(api, "cpo")) == 2 * (H.QUARANTINE_AT + 2)
    item = post(api, "tasks", {"owner": "cpo", "title": "Research exports", "body": "x"})
    for _ in range(H.QUARANTINE_AT + 1):
        r = api.post("/api/v2/tasks/" + item["id"], json={"version": item["version"], "number": 992}, headers=headers(token))
        assert r.status_code == 403, r.text
    assert state(api, "cpo") == "quarantined"


def test_an_approval_for_a_public_label_never_carries_a_goal_label_and_is_used_once_on_a_public_task(api):
    public_setup(api)
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research search", "body": "x", "type": "Product roadmap"})
    with api.app.state.store.transaction() as c:      # published: the label now puts it on the roadmap
        c.execute("UPDATE tasks SET visibility='public', public_title='Better search' WHERE id=?", (item["id"],))

    def approved(labels):
        preview = post(api, "tasks/" + item["id"] + "/publication-preview", {"labels": labels}, token=token)
        approval = post(api, "approvals", {"kind": "publish", "task_id": item["id"], "payload": preview["payload"]}, token=token)
        post(api, "approvals/" + approval["id"], {"decision": "approved"})
        return approval["id"]

    refused = label(api, item["id"], ["roadmap", "goal:on"], token, expected=403, approval_id=approved(["roadmap", "goal:on"]))
    assert "goal:on changes how Tico runs the task" in refused["error"]["detail"]
    assert label(api, item["id"], ["roadmap"], token, approval_id=approved(["roadmap"]))["labels"] == ["roadmap"]
    assert api.get("/public/roadmaps/roadmap/tasks/" + item["id"]).status_code == 200
    assert label(api, item["id"], [], token, approval_id=approved([]))["labels"] == []
    assert api.get("/public/roadmaps/roadmap/tasks/" + item["id"]).status_code == 404


def test_label_refusals_on_different_tasks_each_count(api):
    token = bot_token(api, "cpo")
    tickets = [post(api, "tasks", {"owner": "cmo", "title": f"Build part {i}", "body": "x", "type": "Engineering"})
               for i in range(2)]
    for ticket in tickets:
        for _ in range(2):
            label(api, ticket["id"], ["qa"], token, expected=403)
    with api.app.state.store.read() as c:
        about = [json.loads(r[0]).get("task") for r in c.execute(
            "SELECT detail FROM refusals WHERE actor='bot:cpo' AND rule='label' ORDER BY created_at")]
    assert about == [tickets[0]["id"]] * 2 + [tickets[1]["id"]] * 2, "the replayed refusal keeps its task"



def created(api, slug, title):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM tasks WHERE requester_id=? AND title=?", ("bot:" + slug, title)).fetchone()[0]


def written(api):
    """What a create writes besides the task: its room, a new tag, the task's tags."""
    with api.app.state.store.read() as c:
        return [c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("tasks", "conversations", "tags", "task_tags")]


def test_a_bot_creates_a_task_with_a_classification_label_but_not_one_tico_acts_on(api):
    token = bot_token(api, "cpo")
    before = written(api)
    r = api.post("/api/v2/tasks", json={"owner": "ops", "title": "Run the exports in goal mode", "body": "x",
                                        "labels": ["goal:on", "area-exports"]}, headers=headers(token))
    assert r.status_code == 403 and r.json()["error"]["code"] == "label", r.text
    detail = r.json()["error"]["detail"]
    assert "goal:on changes how Tico runs the task, so a person on the leadership, product, engineering teams" in detail
    assert "area-exports you may set on its own" in detail
    assert created(api, "cpo", "Run the exports in goal mode") == 0, "nothing is written"
    r = api.post("/api/v2/tasks", json={"owner": "ops", "title": "Stop goal mode", "body": "x", "labels": ["goal:off"]},
                 headers=headers(token))
    assert r.status_code == 403 and r.json()["error"]["code"] == "label", r.text
    assert written(api) == before, "no task, room, tag or task tag is written"
    ticket = post(api, "tasks", {"owner": "ops", "title": "Build exports", "body": "x", "type": "Engineering",
                                 "labels": ["qa"]}, token=token)
    assert ticket["labels"] == ["qa"]
    assert [rule for rule, _ in refusals(api, "cpo")] == ["label", "label"] and state(api, "cpo") == "active"


def test_a_bot_creates_no_task_with_a_public_roadmap_label_and_is_told_the_way_through(api):
    public_setup(api)
    token = bot_token(api, "cpo")
    body = {"owner": "cpo", "title": "Research search", "body": "x", "type": "Product roadmap",
            "labels": ["roadmap", "area-search"]}
    before = written(api)
    refused = post(api, "tasks", body, token=token, expected=403)["error"]
    assert refused["code"] == "label" and "roadmap is a public roadmap label" in refused["detail"]
    assert "create the task without it, then add it on the task" in refused["detail"]
    assert "--approval-id" in refused["detail"] and "area-search you may set on its own" in refused["detail"]
    assert created(api, "cpo", "Research search") == 0
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    refused = post(api, "tasks", body, token=token, expected=403)["error"]
    assert "a person on the leadership, product, engineering teams adds it" in refused["detail"]
    assert created(api, "cpo", "Research search") == 0 and written(api) == before
    # The way through: created without it, then labelled on the task with an approved publish approval.
    post(api, "roadmaps/settings", {"bot_public_approval": True})
    item = post(api, "tasks", {**body, "labels": ["area-search"]}, token=token)
    preview = post(api, "tasks/" + item["id"] + "/publication-preview", {"labels": ["roadmap", "area-search"]}, token=token)
    approval = post(api, "approvals", {"kind": "publish", "task_id": item["id"], "payload": preview["payload"]}, token=token)
    post(api, "approvals/" + approval["id"], {"decision": "approved"})
    assert label(api, item["id"], ["roadmap", "area-search"], token, approval_id=approval["id"])["labels"] == [
        "roadmap", "area-search"]


def test_the_cli_carries_the_approval_id_when_a_bot_adds_a_public_roadmap_label(api, monkeypatch):
    """`hub task label --add roadmap --approval-id`: the way through a refused create names this command."""
    from clients import remotecli
    from clients.hubcli import parser
    public_setup(api)
    token = bot_token(api, "cpo")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **params):
            return get(api, path, token=token)

        def post(self, path, body, key=None):
            r = api.post("/api/v2/" + path, json=body, headers=headers(token))
            if r.status_code != 200:
                raise SystemExit(r.text)
            return r.json()
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://example.com")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research search", "body": "x", "type": "Product roadmap",
                               "labels": ["area-search"]}, token=token)
    preview = post(api, "tasks/" + item["id"] + "/publication-preview", {"labels": ["area-search", "roadmap"]}, token=token)
    approval = post(api, "approvals", {"kind": "publish", "task_id": item["id"], "payload": preview["payload"]}, token=token)
    post(api, "approvals/" + approval["id"], {"decision": "approved"})
    remotecli.run(parser().parse_args(["task", "label", item["id"], "--add", "roadmap", "--approval-id", approval["id"]]))
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == ["area-search", "roadmap"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT consumed_at FROM approvals WHERE id=?", (approval["id"],)).fetchone()[0]


def test_a_mover_and_the_keeper_create_a_task_with_any_label_as_before(api):
    public_setup(api)
    made = post(api, "tasks", {"owner": "cmo", "title": "Draft the newsletter", "body": "x",
                               "labels": ["goal:on", "roadmap"]}, token="ben-test")
    assert made["labels"] == ["goal:on", "roadmap"]
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, H.KEEPER, "Refresh the map", "x", "bot:ops", labels=["goal:off"])
    assert get(api, "tasks/" + kept["id"])["task"]["labels"] == ["goal:off"]
