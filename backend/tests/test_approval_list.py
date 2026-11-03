"""`GET /api/v2/approvals`: the approvals addressed to the caller, as Needs you counts them, filtered and paged. A list never
shows an approval the caller could not open with GET /api/v2/approvals/{aid}."""

import json

from backend import hubdb as H
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_batch import approval_for_ana


def listed(api, query="", token="ana-test"):
    return get(api, "approvals" + (("?" + query) if query else ""), token)


def needs_approval_ids(api, token="ana-test"):
    return {i["subject"]["id"] for i in get(api, "needs-you", token)["items"]
            if (i.get("subject") or {}).get("type") == "approval"}


def approval_row(api, aid):
    with api.app.state.store.read() as c:
        return dict(H.approval(c, aid))


def test_pending_is_the_needs_you_approvals_and_their_count(api):
    _, _, finance = setup_attempt(api, "finance")
    first = approval_for_ana(api, token=finance["token"], payload={"amount": 10, "account": "ads", "what": "A"})
    second = approval_for_ana(api, token=finance["token"], payload={"amount": 20, "account": "ads", "what": "B"})
    page = listed(api)
    assert page["total"] == len(needs_approval_ids(api)) == 2
    assert {r["id"] for r in page["approvals"]} == {first["id"], second["id"]} == needs_approval_ids(api)


def test_another_persons_approvals_never_show_up(api):
    _, _, finance = setup_attempt(api, "finance")
    mine = approval_for_ana(api, token=finance["token"])
    assert mine["id"] in {r["id"] for r in listed(api)["approvals"]}
    assert listed(api, token="cara-test")["approvals"] == []
    assert api.get(f"/api/v2/approvals/{mine['id']}", headers=headers("cara-test")).status_code == 403


def test_a_decided_approval_leaves_pending_and_shows_under_its_status(api):
    _, _, finance = setup_attempt(api, "finance")
    approval = approval_for_ana(api, token=finance["token"])
    post(api, f"approvals/{approval['id']}", {"decision": "declined", "note": "Not now"}, token="ana-test")
    assert approval["id"] not in {r["id"] for r in listed(api)["approvals"]}
    assert [(r["id"], r["status"]) for r in listed(api, "status=declined")["approvals"]] == [(approval["id"], "declined")]
    assert listed(api, "status=approved")["approvals"] == []


def test_from_before_and_since_narrow_the_list(api):
    _, _, finance = setup_attempt(api, "finance")
    approval = approval_for_ana(api, token=finance["token"])
    row = approval_row(api, approval["id"])
    day = row["created_at"][:10]
    assert [r["id"] for r in listed(api, "from=" + row["requested_by_id"])["approvals"]] == [approval["id"]]
    assert listed(api, "from=bot:nobody")["approvals"] == []
    assert [r["id"] for r in listed(api, f"since={day}")["approvals"]] == [approval["id"]]
    assert listed(api, f"before={day}")["approvals"] == []


def test_pages_are_offsets_and_only_the_last_page_has_no_next(api):
    _, _, finance = setup_attempt(api, "finance")
    made = {approval_for_ana(api, token=finance["token"],
                             payload={"amount": n, "account": "ads", "what": f"Item {n}"})["id"] for n in range(3)}
    first = listed(api, "limit=2")
    second = listed(api, f"limit=2&offset={first['next']}")
    assert (len(first["approvals"]), first["next"], first["total"]) == (2, 2, 3)
    assert (len(second["approvals"]), second["next"]) == (1, None)
    assert {r["id"] for r in first["approvals"]} | {r["id"] for r in second["approvals"]} == made


def test_a_row_has_its_fields_and_a_long_summary_is_one_line_within_the_page_budget(api):
    _, _, finance = setup_attempt(api, "finance")
    approval = approval_for_ana(api, token=finance["token"], payload={"amount": 5, "account": "ads", "what": "x" * 5000})
    body = listed(api)
    [row] = body["approvals"]
    assert set(row) == {"id", "status", "from_actor", "summary", "message_id", "task_id", "created", "link"}
    assert row["id"] == approval["id"] and row["status"] == "pending"
    assert len(row["summary"]) <= 200
    assert row["task_id"] is None and row["link"].startswith("#/thread/") and row["link"].endswith("?m=" + row["message_id"])
    assert len(json.dumps(body)) < 64 * 1024


def test_a_task_approval_links_to_the_task(api):
    _, _, finance = setup_attempt(api, "finance")
    task = post(api, "tasks", {"title": "Run the LinkedIn ads", "body": "Q4", "owner": "bot:finance"})
    approval_for_ana(api, token=finance["token"], task=task["id"])
    [row] = listed(api)["approvals"]
    assert row["task_id"] == task["id"] and row["link"] == "#/task/" + task["id"]


def test_long_unicode_summaries_stay_under_the_byte_cap_and_paging_reaches_every_row(api):
    _, _, finance = setup_attempt(api, "finance")
    made = {approval_for_ana(api, token=finance["token"],
                             payload={"amount": n, "account": "ads", "what": ("漢字🙂ü " * 200) + str(n)})["id"]
            for n in range(100)}
    first = api.get("/api/v2/approvals?limit=100", headers=headers("ana-test"))
    assert first.status_code == 200 and len(first.content) <= 64 * 1024
    body = first.json()
    assert "漢字" in body["approvals"][0]["summary"] and body["total"] == 100
    assert body["next"] == len(body["approvals"]) and len(body["approvals"]) < 100
    seen, offset = [r["id"] for r in body["approvals"]], body["next"]
    while offset is not None:
        page_body = listed(api, f"limit=100&offset={offset}")
        assert len(json.dumps(page_body).encode()) <= 64 * 1024
        seen += [r["id"] for r in page_body["approvals"]]
        offset = page_body["next"]
    assert len(seen) == len(set(seen)) == len(made) and set(seen) == made


def test_an_unknown_status_is_refused(api):
    assert api.get("/api/v2/approvals?status=maybe", headers=headers("ana-test")).status_code == 422


def history_statements(api, monkeypatch, query, token="ana-test"):
    """GET /api/v2/approvals with `query` (after one warm-up read), and the SQL statements that request ran."""
    import contextlib
    store, seen = api.app.state.store, []
    api.get("/api/v2/approvals?" + query, headers=headers(token))
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get("/api/v2/approvals?" + query, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), seen


def decide_many(api, token, n, decision):
    for _ in range(n):
        approval = approval_for_ana(api, token=token)
        post(api, f"approvals/{approval['id']}", {"decision": decision, "note": "Noted"}, token="ana-test")


def test_history_reads_a_bounded_number_of_statements_however_many_decided_approvals(api, monkeypatch):
    """The decided history is read in one windowed query: the statements a page takes do not grow with the number of
    decided approvals in the company (30 or 120 here, the same statements for a 2-row page)."""
    _, _, finance = setup_attempt(api, "finance")
    decide_many(api, finance["token"], 30, "declined")
    few_body, few = history_statements(api, monkeypatch, "status=declined&limit=2")
    decide_many(api, finance["token"], 90, "declined")
    many_body, many = history_statements(api, monkeypatch, "status=declined&limit=2")
    assert few_body["total"] == 30 and many_body["total"] == 120
    assert len(many_body["approvals"]) == len(few_body["approvals"]) == 2 and many_body["next"] == 2
    assert len(many) <= len(few) + 2, (len(few), len(many))


def test_the_history_join_is_served_by_an_index_not_a_scan(api, monkeypatch):
    """The join of a person's messages to their approvals uses approvals_message (migration 0049), and neither table is
    scanned: a scan would read every approval in the company for one person's history."""
    _, _, finance = setup_attempt(api, "finance")
    decide_many(api, finance["token"], 40, "approved")
    with api.app.state.store.transaction() as c:
        c.execute("ANALYZE")
    _, seen = history_statements(api, monkeypatch, "status=approved")
    [sql] = [s for s in seen if "JOIN messages m ON m.id=a.message_id" in s and "ORDER BY a.created_at" in s]
    with api.app.state.store.read() as c:
        plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql)]
    assert any("approvals_message" in line for line in plan), plan
    assert not [line for line in plan if line.startswith(("SCAN approvals", "SCAN messages"))], plan
