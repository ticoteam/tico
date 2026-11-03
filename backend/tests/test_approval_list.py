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
    assert len(row["summary"]) <= 200 and row["link"] == f"/api/v2/approvals/{approval['id']}"
    assert len(json.dumps(body)) < 64 * 1024


def test_an_unknown_status_is_refused(api):
    assert api.get("/api/v2/approvals?status=maybe", headers=headers("ana-test")).status_code == 422
