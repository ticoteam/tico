"""Who changes a task's labels: a mover any label; a bot a classification label on a task it manages (its own, one
it asked for, or one in a pipeline it manages); a label on a public roadmap or one Tico acts on keeps its gate. The
publish approval that once let a bot add a public label went with the old engine: the refusal says a person adds it and
names no removed command, and the publication preview answers 404 until it returns (backend/roadmaps.py NOT_YET)."""

import json

from backend import hubdb as H, pipeline_settings
from backend.task_calls import SYSTEM
from backend.tests.helpers.roadmaps import public_setup as roadmap_public_setup  # noqa: F401
from backend.tests.helpers.tasks_board import api, get, headers, post  # noqa: F401
from backend.tests.helpers.tasks_board import bot_token as board_bot_token  # noqa: F401


def public_setup(api):
    """The public roadmap, set up once on the shared hub (`test_roadmaps.public_setup` creates its tags)."""
    if getattr(api, "public_item", None) is None:
        api.public_item = roadmap_public_setup(api)
    return api.public_item


def bot_token(api, slug="ops"):
    """The bot's leased turn (`test_tasks_board.bot_token`), claimed once per bot on the shared hub."""
    tokens = api.__dict__.setdefault("bot_tokens", {})
    if slug not in tokens:
        tokens[slug] = board_bot_token(api, slug)
    return tokens[slug]


PIPELINES = {"engineering": "Engineering", "product": "Product roadmap"}


def pipelines(api):
    """The two pipelines the checks file tasks in, made once on the shared hub, each with General's one stage."""
    if getattr(api, "pipelines_made", False):
        return
    with api.app.state.store.transaction() as c:
        [todo] = pipeline_settings.read(c, "general")["stages"]
        stage = {k: v for k, v in todo.items() if k not in ("id", "requirements")}
        for pid, name in PIPELINES.items():
            record = {"id": pid, "name": name, "goal": name + " work.", "prefix": pid[:2].upper()}
            pipeline_settings.save(c, SYSTEM, {**record, "stages": [stage]})
    api.pipelines_made = True


def manage(api, pipeline, slug):
    """`slug` manages the pipeline, as the Product roadmap pipeline's manager is set for Head of Product."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE pipelines SET manager_id=? WHERE id=?", ("bot:" + slug, pipeline))


def label(api, task_id, labels, token, expected=200, **extra):
    task = get(api, "tasks/" + task_id)["task"]
    return post(
        api, "tasks/" + task_id, {"version": task["version"], "labels": labels, **extra}, token=token, expected=expected
    )


def refusals(api, slug):
    with api.app.state.store.read() as c:
        return [(r[0], r[1]) for r in c.execute("SELECT rule, detail FROM refusals WHERE actor=?", ("bot:" + slug,))]


def state(api, slug):
    with api.app.state.store.read() as c:
        return H.bot(c, slug)["state"]


def _a_bot_adds_and_removes_a_classification_label_on_its_own_roadmap_item(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research exports", "body": "x", "pipeline": "product"})
    assert label(api, item["id"], ["area-exports"], token)["labels"] == ["area-exports"]
    assert label(api, item["id"], [], token)["labels"] == []
    assert refusals(api, "cpo") == [] and state(api, "cpo") == "active"
    # One it asked for, and one in a pipeline it manages, the same way.
    asked = post(api, "tasks", {"owner": "ops", "title": "Size the exports work", "body": "x"}, token=token)
    assert label(api, asked["id"], ["priority-high"], token)["labels"] == ["priority-high"]
    other = post(api, "tasks", {"owner": "cmo", "title": "Research imports", "body": "x", "pipeline": "product"})
    label(api, other["id"], ["area-imports"], token, expected=403)
    manage(api, "product", "cpo")
    assert label(api, other["id"], ["area-imports"], token)["labels"] == ["area-imports"]


def _a_public_roadmap_label_is_refused_both_ways_and_never_applied_silently(api):
    public_setup(api)
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research search", "body": "x", "pipeline": "product"})
    refused = label(api, item["id"], ["roadmap", "area-search"], token, expected=403)["error"]
    assert refused["code"] == "label"
    assert "roadmap is a public roadmap label" in refused["detail"] and "publication-preview" not in refused["detail"]
    assert "a person on the leadership, product, engineering teams changes it" in refused["detail"]
    assert "area-search you may change on its own" in refused["detail"]
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == []
    # Taking it off the roadmap is checked the same way.
    assert label(api, item["id"], ["roadmap"], "ben-test")["labels"] == ["roadmap"]
    label(api, item["id"], [], token, expected=403)
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == ["roadmap"]
    # A team that does not ask bots for approvals leaves the public labels to a person.
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    refused = label(api, item["id"], [], token, expected=403)["error"]
    assert "a person on the leadership, product, engineering teams changes it" in refused["detail"]
    assert label(api, item["id"], [], "ben-test")["labels"] == []  # a person who is a mover, as before


def _a_label_tico_acts_on_stays_a_movers_even_on_the_bots_own_task(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research filters", "body": "x"})
    refused = label(api, item["id"], ["goal:on"], token, expected=403)["error"]
    assert refused["code"] == "label" and "how Tico runs the task" in refused["detail"]
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == []


def _a_bot_labels_no_private_task_it_cannot_see_nor_a_ticket_it_does_not_manage(api):
    manage(api, "engineering", "ops")
    cpo = bot_token(api, "cpo")
    hidden = post(api, "tasks", {"owner": "ops", "title": "Review the contract", "body": "x", "private": True})
    label(api, hidden["id"], ["qa"], cpo, expected=404)
    ticket = post(api, "tasks", {"owner": "cmo", "title": "Build exports", "body": "x", "pipeline": "engineering"})
    # A task it has no part in and whose pipeline it does not manage is not its to change at all.
    refused = label(api, ticket["id"], ["qa"], cpo, expected=403)["error"]
    assert refused["code"] == "identity" and "Comment on it, or file a new task" in refused["detail"]
    ops = bot_token(api, "ops")
    assert label(api, ticket["id"], ["qa"], ops)["labels"] == ["qa"]  # the pipeline's manager
    assert label(api, hidden["id"], ["qa"], ops)["labels"] == ["qa"]  # its own private task
    # The manager's right is to labels only.
    assert label(api, ticket["id"], ["qa", "area-exports"], ops)["labels"] == ["qa", "area-exports"]
    r = api.post(
        "/api/v2/tasks/" + ticket["id"],
        json={
            "version": get(api, "tasks/" + ticket["id"])["task"]["version"],
            "labels": ["qa"],
            "due": "2026-12-01T00:00:00Z",
        },
        headers=headers(ops),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "identity", r.text


def _a_bot_still_changes_no_lane_or_number_and_a_mover_is_unchanged(api):
    token = bot_token(api, "cpo")
    item = post(api, "tasks", {"owner": "cpo", "title": "Research sorting", "body": "x"})
    for change in ({"lane": "company"}, {"number": 991}, {"lane": "company", "labels": ["area-sort"]}):
        r = api.post("/api/v2/tasks/" + item["id"], json={"version": item["version"], **change}, headers=headers(token))
        assert r.status_code == 403 and r.json()["error"]["code"] == "identity", r.text
    assert get(api, "tasks/" + item["id"])["task"]["labels"] == []
    other = post(api, "tasks", {"owner": "cmo", "title": "Draft the newsletter", "body": "x"})
    moved = post(
        api,
        "tasks/" + other["id"],
        {"version": other["version"], "labels": ["goal:on", "news"], "number": 991},
        token="ben-test",
    )
    assert moved["labels"] == ["goal:on", "news"] and moved["number"] == 991
    label(api, other["id"], [], "priya-test", expected=403)  # a person who is not a mover, as before


def _a_repeated_label_refusal_counts_once_per_task_an_hour_but_other_repeats_still_hold_the_chat(api):
    token = bot_token(api, "cpo")
    ticket = post(api, "tasks", {"owner": "cpo", "title": "Build imports", "body": "x", "pipeline": "engineering"})
    for _ in range(H.QUARANTINE_AT + 2):
        label(api, ticket["id"], ["goal:on"], token, expected=403)
    assert state(api, "cpo") == "active"
    assert sum(rule == "label" for rule, _ in refusals(api, "cpo")) == H.QUARANTINE_AT + 2, "each is still recorded"
    item = post(api, "tasks", {"owner": "cpo", "title": "Research exports", "body": "x"})
    for _ in range(H.QUARANTINE_AT + 1):
        r = api.post(
            "/api/v2/tasks/" + item["id"], json={"version": item["version"], "number": 992}, headers=headers(token)
        )
        assert r.status_code == 403, r.text
    assert state(api, "cpo") == "active"
    with api.app.state.store.read() as c:
        hold = c.execute(
            "SELECT task_id,conversation_id,reason FROM quarantine_holds WHERE released_at IS NULL"
        ).fetchone()
        assert hold["task_id"] is None and hold["conversation_id"]
        assert hold["reason"] == f"{H.QUARANTINE_AT} repeated refusals today"


def _label_refusals_on_different_tasks_each_count(api):
    token = bot_token(api, "cpo")
    tickets = [
        post(api, "tasks", {"owner": "cpo", "title": f"Build part {i}", "body": "x", "pipeline": "engineering"})
        for i in range(2)
    ]
    for ticket in tickets:
        for _ in range(2):
            label(api, ticket["id"], ["goal:on"], token, expected=403)
    with api.app.state.store.read() as c:
        about = [
            json.loads(r[0]).get("task")
            for r in c.execute("SELECT detail FROM refusals WHERE actor='bot:cpo' AND rule='label' ORDER BY created_at")
        ]
    assert about == [tickets[0]["id"]] * 2 + [tickets[1]["id"]] * 2, "the replayed refusal keeps its task"


def _a_mover_grants_a_bot_one_tag_and_it_sets_that_tag_and_nothing_else(api):
    token = bot_token(api, "cpo")
    tag = post(api, "tags", {"key": "check-passed"}, token="ben-test")["tag"]
    assert tag["bot_writers"] == []
    # A bot that owns a tag still cannot grant it, to itself or another bot.
    own = post(api, "tags", {"key": "area-checks"}, token=token)["tag"]
    grant = {"version": own["version"], "bot_writers": ["cpo"]}
    refused = post(api, "tags/" + own["id"], grant, token=token, expected=403)
    assert refused["error"]["code"] == "identity"
    # A label Tico acts on is never granted.
    flow = next((t for t in get(api, "tags")["tags"] if t["key"] == "goal:on"), None)
    flow = flow or post(api, "tags", {"key": "goal:on"}, token="ben-test")["tag"]
    r = api.post(
        "/api/v2/tags/" + flow["id"],
        json={"version": flow["version"], "bot_writers": ["cpo"]},
        headers=headers("ben-test"),
    )
    assert r.status_code >= 400 and r.json()["error"]["code"] == "kind", r.text
    granted = post(api, "tags/" + tag["id"], {"version": tag["version"], "bot_writers": ["cpo"]}, token="ben-test")
    assert granted["tag"]["bot_writers"] == ["bot:cpo"]
    ticket = post(api, "tasks", {"owner": "cmo", "title": "Build checks", "body": "x", "pipeline": "engineering"})
    assert label(api, ticket["id"], ["qa"], "ben-test")["labels"] == ["qa"]
    assert label(api, ticket["id"], ["qa", "check-passed"], token)["labels"] == ["qa", "check-passed"]
    assert label(api, ticket["id"], ["qa"], token)["labels"] == ["qa"]
    # Any other label in the same change, added or removed, is refused whole.
    for wanted in (["qa", "check-passed", "area-checks"], ["check-passed"]):
        refused = label(api, ticket["id"], wanted, token, expected=403)["error"]
        assert refused["code"] == "identity", refused
    assert get(api, "tasks/" + ticket["id"])["task"]["labels"] == ["qa"]
    # Nothing else on the ticket becomes the bot's, and a task it cannot read stays hidden.
    r = api.post(
        "/api/v2/tasks/" + ticket["id"],
        json={
            "version": get(api, "tasks/" + ticket["id"])["task"]["version"],
            "labels": ["qa", "check-passed"],
            "due": "2026-12-01T00:00:00Z",
        },
        headers=headers(token),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "identity", r.text
    hidden = post(api, "tasks", {"owner": "ops", "title": "Review the checks", "body": "x", "private": True})
    label(api, hidden["id"], ["check-passed"], token, expected=404)
    # An empty list revokes the grant.
    tag = get(api, "tags/" + tag["id"])["tag"]
    revoked = post(api, "tags/" + tag["id"], {"version": tag["version"], "bot_writers": []}, token="ben-test")
    assert revoked["tag"]["bot_writers"] == []
    label(api, ticket["id"], ["qa", "check-passed"], token, expected=403)


def created(api, slug, title):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT count(*) FROM tasks WHERE requester_id=? AND title=?", ("bot:" + slug, title)
        ).fetchone()[0]


def written(api):
    """What a create writes besides the task: its room, a new tag, the task's tags."""
    with api.app.state.store.read() as c:
        return [
            c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("tasks", "conversations", "tags", "task_tags")
        ]


def test_a_bot_creates_a_task_with_a_classification_label_but_not_one_tico_acts_on(api):
    pipelines(api)
    token = bot_token(api, "cpo")
    before = written(api)
    r = api.post(
        "/api/v2/tasks",
        json={
            "owner": "ops",
            "title": "Run the exports in goal mode",
            "body": "x",
            "labels": ["goal:on", "area-exports"],
        },
        headers=headers(token),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "label", r.text
    detail = r.json()["error"]["detail"]
    assert "goal:on changes how Tico runs the task, so a person on the leadership, product, engineering teams" in detail
    assert "area-exports you may set on its own" in detail
    assert created(api, "cpo", "Run the exports in goal mode") == 0, "nothing is written"
    r = api.post(
        "/api/v2/tasks",
        json={"owner": "ops", "title": "Stop goal mode", "body": "x", "labels": ["goal:off"]},
        headers=headers(token),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "label", r.text
    assert written(api) == before, "no task, room, tag or task tag is written"
    ticket = post(
        api,
        "tasks",
        {"owner": "ops", "title": "Build exports", "body": "x", "pipeline": "engineering", "labels": ["qa"]},
        token=token,
    )
    assert ticket["labels"] == ["qa"]
    assert [rule for rule, _ in refusals(api, "cpo")] == ["label", "label"] and state(api, "cpo") == "active"


def test_a_bot_creates_no_task_with_a_public_roadmap_label_and_is_told_the_way_through(api):
    public_setup(api)
    pipelines(api)
    token = bot_token(api, "cpo")
    body = {
        "owner": "cpo",
        "title": "Research search",
        "body": "x",
        "pipeline": "product",
        "labels": ["roadmap", "area-search"],
    }
    before = written(api)
    refused = post(api, "tasks", body, token=token, expected=403)["error"]
    assert refused["code"] == "label" and "roadmap is a public roadmap label" in refused["detail"]
    assert (
        "create the task without it, and a person on the leadership, product, engineering teams adds it"
        in refused["detail"]
    )
    assert "--approval-id" not in refused["detail"] and "area-search you may set on its own" in refused["detail"]
    assert created(api, "cpo", "Research search") == 0
    post(api, "roadmaps/settings", {"bot_public_approval": False})
    refused = post(api, "tasks", body, token=token, expected=403)["error"]
    assert "a person on the leadership, product, engineering teams adds it" in refused["detail"]
    assert created(api, "cpo", "Research search") == 0 and written(api) == before
    # No way through yet: the publication preview answers as the public roadmap does (backend/roadmaps.py NOT_YET).
    r = api.post("/api/v2/tasks/x/publication-preview", json={"labels": ["roadmap"]}, headers=headers(token))
    assert r.status_code == 404, r.text


def _a_mover_and_the_keeper_create_a_task_with_any_label_as_before(api):
    public_setup(api)
    made = post(
        api,
        "tasks",
        {"owner": "cmo", "title": "Draft the newsletter", "body": "x", "labels": ["goal:on", "roadmap"]},
        token="ben-test",
    )
    assert made["labels"] == ["goal:on", "roadmap"]
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, H.KEEPER, "Refresh the map", "x", "bot:ops", labels=["goal:off"])
    assert get(api, "tasks/" + kept["id"])["task"]["labels"] == ["goal:off"]


def _fresh(api):
    """A check's starting point on the shared hub, as on a new one: no refusal recorded and no hold open, every bot
    active, no task open (a title may be asked for again), the two pipelines with no manager, and bots asked for
    approvals on the public labels once the roadmap is set up."""
    pipelines(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM refusals")
        c.execute("UPDATE quarantine_holds SET released_at=? WHERE released_at IS NULL", (H.now(),))
        c.execute("UPDATE bots SET state='active' WHERE slug IN ('ops','cpo','cmo')")
        for (tid,) in c.execute("SELECT id FROM tasks WHERE status='open'").fetchall():
            H.task_close(c, SYSTEM, tid, "A fresh start for the next check.")
        c.execute("UPDATE pipelines SET manager_id=NULL WHERE id IN (?, ?)", tuple(PIPELINES))
    if getattr(api, "public_item", None) is not None:
        post(api, "roadmaps/settings", {"bot_public_approval": True})


def test_who_changes_a_tasks_labels(subtests, api):
    """Every label rule on one hub, each check from a fresh starting point (`_fresh`). The two refused-create checks
    stay their own tests: they count the tags table, and `_fresh` keeps the tags earlier checks made, so on a shared
    hub a tag leaked by a refused create (or a successful create of an unknown tag) would go unseen (3d7336456)."""
    checks = (
        _a_bot_adds_and_removes_a_classification_label_on_its_own_roadmap_item,
        _a_public_roadmap_label_is_refused_both_ways_and_never_applied_silently,
        _a_label_tico_acts_on_stays_a_movers_even_on_the_bots_own_task,
        _a_bot_labels_no_private_task_it_cannot_see_nor_a_ticket_it_does_not_manage,
        _a_bot_still_changes_no_lane_or_number_and_a_mover_is_unchanged,
        _a_repeated_label_refusal_counts_once_per_task_an_hour_but_other_repeats_still_hold_the_chat,
        _label_refusals_on_different_tasks_each_count,
        _a_mover_and_the_keeper_create_a_task_with_any_label_as_before,
        _a_mover_grants_a_bot_one_tag_and_it_sets_that_tag_and_nothing_else,
    )
    for check in checks:
        with subtests.test(check.__name__):
            _fresh(api)
            check(api)
