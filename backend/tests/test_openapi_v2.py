"""The v2 contract a frontend builds on: served to signed-in callers, limited to the stable
resources, committed as docs/openapi/v2.json, and true to what the API answers."""

import re
import uuid

from backend import openapi_v2
from backend.finance import actions as FA
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)
from backend.tests.test_finance_fixtures import account


def conforms(value, schema, root, where="$"):
    """The subset of JSON Schema the contract uses: type, required, properties, items, oneOf, enum, $ref."""
    if "$ref" in schema:
        return conforms(value, root["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]], root, where)
    if "oneOf" in schema:
        errors = [conforms(value, option, root, where) for option in schema["oneOf"]]
        return None if any(e is None for e in errors) else "; ".join(errors)
    if "enum" in schema:
        return None if value in schema["enum"] else "%s: %r not in %r" % (where, value, schema["enum"])
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else kinds
    names = {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer", float: "number",
             type(None): "null"}
    actual = names[type(value)]
    if kinds and actual not in kinds and not (actual == "integer" and "number" in kinds):
        return "%s: %s is not %s" % (where, actual, kinds)
    if isinstance(value, dict):
        if schema.get("additionalProperties") is False and set(value) - set(schema.get("properties", {})):
            return where + ": unexpected fields " + str(set(value) - set(schema.get("properties", {})))
        for key in schema.get("required", []):
            if key not in value:
                return "%s: missing %s" % (where, key)
        for key, sub in schema.get("properties", {}).items():
            if key in value and (error := conforms(value[key], sub, root, where + "." + key)):
                return error
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value[:5]):
            if error := conforms(item, schema["items"], root, "%s[%d]" % (where, i)):
                return error
    return None


def test_the_committed_copy_is_current():
    assert openapi_v2.COPY.read_text() == openapi_v2.render(openapi_v2.generate()), \
        "docs/openapi/v2.json is stale: run python -m backend.openapi_v2"


def test_served_to_the_signed_in_only_and_limited_to_the_stable_resources(api):
    assert api.get("/api/v2/openapi.json").status_code == 401
    assert api.get("/openapi.json").status_code == 401 and api.get("/docs").status_code == 401
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert api.get(path, headers=headers()).status_code == 404       # FastAPI's own pages stay off
    document = api.get("/api/v2/openapi.json", headers=headers("ben-test")).json()
    assert document == api.get("/api/v2/openapi.json", headers=headers()).json()
    assert document["info"]["version"] == "2.0.0" and document["openapi"].startswith("3.")
    served = {(p, m) for p, ops in document["paths"].items() for m in ops}
    assert served == {(p, m) for p, m, *_ in openapi_v2.STABLE}
    for internal in ("/api/v2/runners/heartbeat", "/api/v2/jobs/claim", "/api/v2/credentials", "/scim/v2/Users",
                     "/api/v2/settings/history", "/api/v2/access"):
        assert not any(path.startswith(internal) for path in document["paths"])
    ids = [op["operationId"] for ops in document["paths"].values() for op in ops.values()]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[a-z][A-Za-z0-9]+", i) for i in ids)
    for ops in document['paths'].values():
        for op in ops.values():
            parameters = [(p['name'], p['in']) for p in op.get('parameters', [])]
            assert len(parameters) == len(set(parameters))
    assert '#/$defs/' not in str(document)
    tagged = {tag for ops in document["paths"].values() for op in ops.values() for tag in op["tags"]}
    assert tagged == set(openapi_v2.TAGS) == {t["name"] for t in document["tags"]}
    text = str(document)
    assert all(ref in document["components"]["schemas"] for ref in re.findall(r"#/components/schemas/(\w+)", text))
    for path, ops in document["paths"].items():                          # every write asks for an idempotency key
        if path.startswith("/api/v2/") and "post" in ops:
            assert "Idempotency-Key" in [p["name"] for p in ops["post"]["parameters"]], path


def test_the_declared_answers_match_the_live_ones(api):
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def check(operation, response):
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(response.json(), schema, document) is None, (operation, conforms(response.json(), schema, document), response.text[:400])

    def call(operation, method, path, **kw):
        response = getattr(api, method)(path, headers=headers(), **kw)
        assert response.status_code == 200, (path, response.text)
        check(operation, response)
        return response.json()

    call("getMe", "get", "/api/v2/me")
    call("getConfig", "get", "/api/v2/config")
    call("getOrg", "get", "/api/v2/org")
    call("listBots", "get", "/api/v2/bots")
    call("getBot", "get", "/api/v2/bots/ops")
    call("getBotInstructions", "get", "/api/v2/bots/ops/instructions")
    call("listRecentBots", "get", "/api/v2/me/recent")
    call("listBotTools", "get", "/api/v2/bots/ops/tools")
    chat = call("chatWithBot", "post", "/api/v2/chat/ops", json={"text": "hello"})
    cid = chat["conversation"]["id"]
    call("listConversations", "get", "/api/v2/conversations")
    call("listMessages", "get", "/api/v2/conversations/%s/messages" % cid)
    call("getConversationSnapshot", "get", "/api/v2/conversations/%s/snapshot" % cid)
    call("replyInConversation", "post", "/api/v2/conversations/%s/messages" % cid, json={"text": "again"})
    call("getMessage", "get", "/api/v2/messages/" + chat["message"]["id"])
    call("sendMessage", "post", "/api/v2/messages", json={"to": "bot:ops", "text": "third"})
    task = call("createTask", "post", "/api/v2/tasks",
                json={"title": "Draft the launch plan", "body": "Please draft it.", "owner": "human:ana"})["task"]
    call("listTasks", "get", "/api/v2/tasks")
    detail = call("getTask", "get", "/api/v2/tasks/" + task["id"])
    call("updateTask", "post", "/api/v2/tasks/" + task["id"], json={"version": detail["task"]["version"], "status": "doing"})
    said = call("commentOnTask", "post", "/api/v2/tasks/" + task["id"] + "/comments", json={"text": "looks good"})
    comment = "/api/v2/tasks/" + task["id"] + "/comments/" + said["comment"]["id"]
    call("editTaskComment", "post", comment, json={"text": "looks good to me"})
    call("deleteTaskComment", "post", comment + "/delete", json={})
    call("listUpdates", "get", "/api/v2/updates")
    call("countUnreadUpdates", "get", "/api/v2/updates/unread")
    call("getAssistant", "get", "/api/v2/assistant")
    call("sendAssistantMessage", "post", "/api/v2/assistant/messages", json={"text": "what's waiting on me"})
    call("getNeedsYou", "get", "/api/v2/needs-you")
    call("getNeedsYou", "get", "/api/v2/needs-you", params={"count": "true"})
    call("getPlace", "get", "/api/v2/places/dashboard")
    call("savePlace", "put", "/api/v2/places/dashboard", json={})
    call("listCards", "get", "/api/v2/cards")
    call("searchMeetings", "get", "/api/v2/meetings/search")
    call("searchDocs", "get", "/api/v2/context/search", params={"q": "plan"})
    doc = call("createInternalDoc", "post", "/api/v2/docs", json={"title": "Refund policy", "body": "Refund in 30 days."})["doc"]
    call("listInternalDocs", "get", "/api/v2/docs")
    call("getInternalDoc", "get", "/api/v2/docs/" + doc["id"])
    call("updateInternalDoc", "patch", "/api/v2/docs/" + doc["id"], json={"version": 1, "body": "Refund in 14 days."})
    call("listInternalDocVersions", "get", "/api/v2/docs/%s/versions" % doc["id"])
    call("getInternalDocVersion", "get", "/api/v2/docs/%s/versions/1" % doc["id"])
    call("restoreInternalDoc", "post", "/api/v2/docs/%s/restore" % doc["id"], json={"version": 1})
    link = call("addLinkedDoc", "post", "/api/v2/linked-docs", json={"url": "https://help.acme.example/refunds"})["linked"]
    call("listLinkedDocs", "get", "/api/v2/linked-docs")
    call("updateLinkedDoc", "patch", "/api/v2/linked-docs/" + link["id"], json={"description": "Public refunds page"})
    call("searchInternalAndLinkedDocs", "get", "/api/v2/docs/search", params={"q": "refund"})
    goal = call("createGoal", "post", "/api/v2/goals", json={"title": "Raise activation", "owner": "me"})["goal"]
    kpi = call("createKpi", "post", "/api/v2/kpis", json={
        "name": "Activation", "unit": "%", "cadence": "daily", "goal_id": goal["id"], "kind": "improve",
        "baseline": 40, "target": 70, "deadline": "2099-12-31"})["kpi"]
    reading = call("addKpiReading", "post", "/api/v2/kpis/%s/readings" % kpi["id"], json={"value": 41, "evidence": "https://bi.example/q/1"})["reading"]
    call("addKpiReading", "post", "/api/v2/kpis/%s/readings" % kpi["id"],
         json={"value": 42, "supersedes": reading["id"], "note": "Double counted"})
    call("listKpiReadings", "get", "/api/v2/kpis/%s/readings" % kpi["id"])
    call("getKpi", "get", "/api/v2/kpis/" + kpi["id"])
    call("listKpis", "get", "/api/v2/kpis")
    call("listKpis", "get", "/api/v2/kpis", params={"include_archived": "true"})
    call("updateKpi", "post", "/api/v2/kpis/" + kpi["id"], json={"definition": "Accounts that finish setup"})
    call("archiveKpi", "post", "/api/v2/kpis/%s/archive" % kpi["id"], json={})
    archived_goal = call("getGoal", "get", "/api/v2/goals/" + goal["id"])["goal"]
    archived_measure = next(k for k in archived_goal["kpis"] if k["id"] == kpi["id"])
    assert conforms(archived_measure, document["components"]["schemas"]["Kpi"], document) is None
    assert archived_measure["status"] is None and archived_measure["freshness"] == "archived"
    call("restoreKpi", "post", "/api/v2/kpis/%s/restore" % kpi["id"], json={})
    other = call("createKpi", "post", "/api/v2/kpis", json={"name": "NPS", "direction": "range"})["kpi"]
    call("linkGoalKpi", "post", "/api/v2/goals/%s/kpis" % goal["id"], json={"kpi_id": other["id"], "kind": "maintain", "min": 30})
    call("setGoalKpiTarget", "post", "/api/v2/goals/%s/kpis/%s" % (goal["id"], other["id"]), json={"kind": "maintain", "min": 30, "max": 60})
    call("unlinkGoalKpi", "post", "/api/v2/goals/%s/kpis/%s/unlink" % (goal["id"], other["id"]), json={})
    call("listBotKpis", "get", "/api/v2/bots/ops/kpis")
    call("addGoalCheckin", "post", "/api/v2/goals/%s/checkins" % goal["id"], json={"body": "On plan.", "signal": "on_track"})
    call("listGoalCheckins", "get", "/api/v2/goals/%s/checkins" % goal["id"])
    call("setGoalStatus", "post", "/api/v2/goals/%s/status" % goal["id"], json={"status": "yellow", "note": "The launch slipped."})
    call("handBackGoalStatus", "post", "/api/v2/goals/%s/status/auto" % goal["id"], json={})
    call("updateGoal", "post", "/api/v2/goals/" + goal["id"], json={"title": "Raise activation to 70%"})
    call("getGoal", "get", "/api/v2/goals/" + goal["id"])
    call("listGoals", "get", "/api/v2/goals")
    call("listGoals", "get", "/api/v2/goals", params={"all": "true"})
    call("getGoalTree", "get", "/api/v2/goals/tree")
    call("refreshGoalStatuses", "post", "/api/v2/goals/refresh", json={})
    proposal = call("createGoalProposal", "post", "/api/v2/goal-proposals", json={
        "kind": "kpi_definition", "kpi_id": kpi["id"], "payload": {"unit": "pct"}, "reason": "Say it plainly"})["proposal"]
    call("listGoalProposals", "get", "/api/v2/goal-proposals")
    call("getGoalsNeedsYou", "get", "/api/v2/goals/needs-you")
    call("decideGoalProposal", "post", "/api/v2/goal-proposals/%s/decide" % proposal["id"], json={"decision": "confirm"})
    turned = call("createGoalProposal", "post", "/api/v2/goal-proposals", json={
        "kind": "kpi_definition", "kpi_id": kpi["id"], "payload": {"unit": "percent"}, "reason": "Spell it out"})["proposal"]
    call("decideGoalProposal", "post", "/api/v2/goal-proposals/%s/decide" % turned["id"], json={"decision": "reject"})
    call("addGoalProposalReason", "post", "/api/v2/goal-proposals/%s/note" % turned["id"], json={"note": "pct is fine"})
    call("getUsage", "get", "/api/v2/usage")
    call("getUsageLimits", "get", "/api/v2/usage/limits")
    call("setUsageDefault", "put", "/api/v2/usage/limits", json={"monthly_usd": 500, "count_subscription": False})
    call("setBotUsageLimit", "put", "/api/v2/usage/limits/ops", json={"daily_usd": 20})
    call("getUsage", "get", "/api/v2/usage", params={"bot": "ops", "from": "2026-01-01", "to": "2026-01-03"})
    call("getHealth", "get", "/api/v2/health")



def test_the_finance_record_and_order_answers_match_the_live_ones(api):
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def call(operation, path, params=None):
        response = api.get(path, headers=headers(), params=params)
        assert response.status_code == 200, (path, response.text)
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        problem = conforms(response.json(), schema, document)
        assert problem is None, (operation, problem, response.text[:400])
        return response.json()

    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)

        def make(kind, payload):
            done = FA.act(c, "human:ana", kind, payload, str(uuid.uuid4()))
            assert done.status == "committed", done.reason
            return done.result

        location = make("location_create", {"name": "Denver"})["location_id"]
        item = make("item_create", {"name": "Consulting", "kind": "service", "sku": "CONSULT", "price":
        15_000})["item_id"]
        project = make("project_create", {"name": "Job 12"})["project_id"]
        vendor = make("contact_create", {"name": "Acme Supplies", "kind": "vendor"})["contact_id"]
        customer = make("contact_create", {"name": "Acme Retail", "kind": "customer"})["contact_id"]
        po = make("po_create", {"vendor_id": vendor, "po_date": "2026-10-10", "lines": [
            {"account_id": account(c, "6100"), "amount": 40_000, "description": "Seats"}]})["po_id"]
        estimate = make("estimate_create", {"customer_id": customer, "estimate_date": "2026-10-10", "lines": [
            {"account_id": account(c, "4000"), "amount": 90_000, "description": "Design"}]})["estimate_id"]
    call("listLocations", "/api/v2/locations")
    call("getLocation", "/api/v2/locations/" + location)
    call("listItems", "/api/v2/items", {"q": "consult"})
    call("getItem", "/api/v2/items/" + item)
    call("listProjects", "/api/v2/projects")
    call("getProject", "/api/v2/projects/" + project)
    call("listPurchaseOrders", "/api/v2/purchase-orders")
    call("getPurchaseOrder", "/api/v2/purchase-orders/" + po)
    call("listEstimates", "/api/v2/estimates")
    call("getEstimate", "/api/v2/estimates/" + estimate)


def test_closed_status_uses_close_permissions_and_rejects_combined_edits(api):
    from backend.tests.test_api import post
    from backend.store import H
    from backend.tests.test_task_lanes import task_bot
    from backend.tests.test_subtask_results import start
    machine = task_bot(api, lanes=True)
    task = post(api, "tasks", {"title": "Check closure alias", "body": "Check it.", "owner": "bot:ops"})
    checking = start(api, machine, task["id"], "closure-task")
    task = checking["task"]
    path = f"tasks/{task['id']}"
    refused = post(api, path, {"version": task["version"], "status": "closed"}, checking["token"], expected=403)
    assert refused["error"]["code"] == "close"
    post(api, path, {"version": task["version"], "status": "closed", "title": "Changed"}, expected=422)
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        assert (row["status"], row["title"], row["version"]) == ("doing", task["title"], task["version"])
    closed = post(api, path, {"version": task["version"], "status": "closed", "note": "Accepted."})
    assert closed["status"] == "closed" and closed["closed_by"] == "human:ana"
    assert closed["version"] == task["version"] + 1
    post(api, path, {"version": task["version"], "status": "closed"}, expected=409)


def test_the_thread_answers_match_the_live_ones(api):
    """The thread routes' declared answers, checked against what they return (the sidebar, a thread, its timeline,
    a message, search and the read mark)."""
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def call(operation, method, path, who="ana-test", expected=200, **kw):
        response = getattr(api, method)(path, headers=headers(who), **kw)
        assert response.status_code == expected, (path, response.text)
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(response.json(), schema, document) is None, (operation, conforms(response.json(), schema, document))
        return response.json()

    created = call("createThread", "post", "/api/v2/threads", json={
        "title": "Budget", "audience": "members", "members": ["human:ben"], "body": "Quarterly budget review"})
    thread_id = created["id"]
    call("listThreads", "get", "/api/v2/threads")
    call("getThread", "get", f"/api/v2/threads/{thread_id}", who="ben-test")
    call("getThreadHome", "get", "/api/v2/threads/home/human:ben")
    call("openThreadDm", "get", "/api/v2/threads/dm", params={"with": "human:ben"})
    call("createThreadDm", "post", "/api/v2/threads/dm", json={"members": ["human:ben"], "body": "Quick question"})
    call("listThreads", "get", "/api/v2/threads", params={"with": "human:ben"})
    ops_home = call("getThreadHome", "get", "/api/v2/threads/home/bot:ops")
    call("openEarlierMessages", "post", f"/api/v2/threads/{ops_home['id']}/open-earlier")
    uploaded = call("uploadThreadMessage", "post", f"/api/v2/uploads/threads/{thread_id}/messages",
                    data={"text": "Notes attached"}, files={"files": ("notes.txt", b"budget notes", "text/plain")})
    call("reactToMessage", "put", f"/api/v2/messages/{uploaded['message']['id']}/reactions/:+1:", who="ben-test")
    call("unreactToMessage", "delete", f"/api/v2/messages/{uploaded['message']['id']}/reactions/:+1:", who="ben-test")
    call("getThreadTimeline", "get", f"/api/v2/threads/{thread_id}/timeline", who="ben-test")
    call("listThreadMembers", "get", f"/api/v2/threads/{thread_id}/members")
    call("searchThreads", "get", "/api/v2/threads/search", who="ben-test", params={"q": "budget"})
    message = call("postThreadMessage", "post", f"/api/v2/threads/{thread_id}/messages", json={"text": "Draft attached"})
    call("editMessage", "patch", f"/api/v2/messages/{message['id']}", json={"text": "Draft attached, revised"})
    call("markThreadRead", "post", f"/api/v2/threads/{thread_id}/read", who="ben-test", json={"seq": 1})
    call("markThreadAndSubthreadsRead", "post", f"/api/v2/threads/{thread_id}/read-all", who="ben-test")
    call("deleteMessage", "delete", f"/api/v2/messages/{message['id']}")


# Every thread route contract section 10 lists (docs/dev/threads-contract.md), as the server spells its paths.
THREAD_CONTRACT = {
    ("get", "/api/v2/threads"), ("post", "/api/v2/threads"), ("get", "/api/v2/threads/search"),
    ("get", "/api/v2/threads/{thread_id}"), ("patch", "/api/v2/threads/{thread_id}"),
    ("get", "/api/v2/threads/{thread_id}/timeline"), ("post", "/api/v2/threads/{thread_id}/read"),
    ("post", "/api/v2/threads/{thread_id}/read-all"), ("get", "/api/v2/threads/{thread_id}/members"),
    ("post", "/api/v2/threads/{thread_id}/members"), ("delete", "/api/v2/threads/{thread_id}/members/{actor}"),
    ("put", "/api/v2/threads/{thread_id}/follow"), ("delete", "/api/v2/threads/{thread_id}/follow"),
    ("post", "/api/v2/threads/{thread_id}/messages"), ("patch", "/api/v2/messages/{message_id}"),
    ("delete", "/api/v2/messages/{message_id}"),
}
# The threads build's own route families: each one the server serves is in the document, and each documented one is
# served (spec() raises on that half).
THREAD_AREAS = ("/api/v2/threads", "/api/v2/calls", "/api/v2/federation", "/api/v2/slack/links",
                "/api/v2/uploads/threads", "/api/v2/messages/{message_id}", "/api/v2/rule-holds")


def test_every_thread_route_the_server_serves_is_documented(api):
    stable = {(m, p) for p, m, *_ in openapi_v2.STABLE}
    assert THREAD_CONTRACT <= stable
    served = set()
    for route in api.app.routes:
        path = re.sub(r"\{(\w+):path\}", r"{\1}", getattr(route, "path", ""))
        if path.startswith(THREAD_AREAS):
            served |= {(m.lower(), path) for m in getattr(route, "methods", None) or () if m not in ("HEAD", "OPTIONS")}
    documented = {(m, p) for m, p in stable if p.startswith(THREAD_AREAS)}
    assert served == documented, {"served, not documented": sorted(served - documented),
                                  "documented, not served": sorted(documented - served)}
