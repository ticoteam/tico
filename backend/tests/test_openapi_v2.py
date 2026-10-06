"""The v2 contract a frontend builds on: served to signed-in callers, limited to the stable
resources, committed as docs/openapi/v2.json, and true to what the API answers."""

import re

from backend import openapi_v2
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)


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
    call("listBotRoutines", "get", "/api/v2/bots/ops/routines")
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
    call("getUsage", "get", "/api/v2/usage")
    call("getUsageLimits", "get", "/api/v2/usage/limits")
    call("setUsageDefault", "put", "/api/v2/usage/limits", json={"monthly_usd": 500, "count_subscription": False})
    call("setBotUsageLimit", "put", "/api/v2/usage/limits/ops", json={"daily_usd": 20})
    call("getUsage", "get", "/api/v2/usage", params={"bot": "ops", "from": "2026-01-01", "to": "2026-01-03"})
    call("getHealth", "get", "/api/v2/health")

