"""What a goal or KPI waits on someone for is one task from the Goal Manager (backend/goal_tasks.py): filed once,
in the person's Needs you and the Goals page's box, and marked done by Tico when the condition clears."""

from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


def goal_manager(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('goal-manager','Goal Manager','fake','','','','keeper','active',?)", (H.now(),))


def asks(api, owner):
    """Every Goal Manager task for `owner`: (title, status, note)."""
    with api.app.state.store.read() as c:
        return [tuple(r) for r in c.execute("SELECT title,status,note FROM tasks WHERE requester='bot:goal-manager' "
                                             "AND owner=? ORDER BY created", (owner,))]


def test_a_stale_kpi_is_one_task_for_its_owner_until_a_reading_or_an_archive_clears_it(api):
    goal_manager(api)
    kpi = post(api, "kpis", {"name": "Weekly signups", "owner": "me", "cadence": "weekly"})["kpi"]
    other = post(api, "kpis", {"name": "Churn", "owner": "me"})["kpi"]
    assert asks(api, "human:ana") == [("Log a new reading for Weekly signups", "open", ""),
                                      ("Log a new reading for Churn", "open", "")]
    # The status pass and any other write reconcile again: still one task each.
    post(api, "goals/refresh", {})
    post(api, "goals/refresh", {})
    assert len(asks(api, "human:ana")) == 2
    # It is an ordinary task: in her Needs you (Tasks and Decide) and, with its id, in the Goals page's box.
    box = get(api, "goals/needs-you")["items"]
    assert [(i["kind"], i["kpi_id"]) for i in box] == [("kpi_stale", kpi["id"]), ("kpi_stale", other["id"])]
    cards = {i["id"]: i for i in get(api, "needs-you")["items"]}
    assert set(cards) >= {i["task_id"] for i in box}
    assert (cards[box[0]["task_id"]]["kind"], cards[box[0]["task_id"]]["person_ask"]) == (
        "task", "Log a new reading for Weekly signups, or say why there is none.")      # what its Decide card leads with
    assert get(api, "goals/needs-you", "ben-test")["items"] == []
    # A reading clears one, an archive the other: Tico marks each done and says why.
    post(api, f"kpis/{kpi['id']}/readings", {"value": 120, "period_end": H.now()})
    post(api, f"kpis/{other['id']}/archive", {})
    assert asks(api, "human:ana") == [("Log a new reading for Weekly signups", "done", "Weekly signups has a fresh reading."),
                                      ("Log a new reading for Churn", "done", "Churn was archived.")]
    assert get(api, "goals/needs-you")["items"] == []
    # Restored, it waits again: a new task. One its owner finishes while it still waits is not filed again.
    post(api, f"kpis/{other['id']}/restore", {})
    again = get(api, "goals/needs-you")["items"]
    assert [i["kpi_id"] for i in again] == [other["id"]]
    task = get(api, f"tasks/{again[0]['task_id']}")["task"]
    post(api, f"tasks/{task['id']}", {"version": task["version"], "status": "done", "note": "We stopped tracking it."})
    post(api, "goals/refresh", {})
    assert [s for _, s, _ in asks(api, "human:ana")] == ["done", "done", "done"]


def test_a_red_kpi_goes_to_the_company_owner_and_a_proposal_to_its_decider(api):
    goal_manager(api)
    goal = post(api, "goals", {"title": "Keep uptime high", "owner": "company"})["goal"]
    kpi = post(api, "kpis", {"name": "Uptime", "unit": "%", "cadence": "daily", "goal_id": goal["id"],
                             "kind": "maintain", "min": 99})["kpi"]
    post(api, f"kpis/{kpi['id']}/readings", {"value": 97, "period_end": H.now()})
    # A company goal's red KPI is the environment owner's (Ana) to explain.
    red = [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "kpi_red"]
    assert [(i["title"], i["goal_id"]) for i in red] == [("Uptime is off track on Keep uptime high: say what's happening",
                                                          goal["id"])]
    post(api, f"kpis/{kpi['id']}/readings", {"value": 99.5, "period_end": H.now()})
    assert ("Uptime is off track on Keep uptime high: say what's happening", "done",
            "Uptime is no longer off track.") in asks(api, "human:ana")
    # A target change on Ben's goal is Ben's to confirm, whoever proposed it; deciding it finishes the task.
    mine = post(api, "goals", {"title": "Ship the stats page", "owner": "ben"})["goal"]
    measure = post(api, "kpis", {"name": "Pages shipped", "goal_id": mine["id"], "kind": "none"}, token="ben-test")["kpi"]
    proposal = post(api, "goal-proposals", {"kind": "kpi_target", "goal_id": mine["id"], "kpi_id": measure["id"],
                                            "payload": {"kind": "maintain", "min": 1}, "reason": "One a week"},
                    token="cara-test")["proposal"]
    box = [i for i in get(api, "goals/needs-you", "ben-test")["items"] if i["kind"] == "proposal"]
    assert [(i["title"], i["proposal"]["id"], i["proposal"]["may_decide"]) for i in box] == [
        ("Confirm the change to Pages shipped", proposal["id"], True)]
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "reject"}, token="ben-test")
    assert ("Confirm the change to Pages shipped", "done", "The proposal was rejected.") in asks(api, "human:ben")
