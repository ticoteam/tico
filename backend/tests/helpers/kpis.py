"""Helpers that other tests share, moved out of test_kpis.py so they import no test file."""

from backend.tests.helpers.api import post


# ------------------------------------------------------------------ the API
def goal_with_kpi(api, target=None, token="ana-test", owner="ana", cadence="daily"):
    goal = post(api, "goals", {"title": "Raise activation", "owner": owner}, token=token)["goal"]
    body = {
        "name": "Activation",
        "unit": "%",
        "cadence": cadence,
        "definition": "Signed-up accounts that finish setup",
        "goal_id": goal["id"],
        **(target or {"kind": "improve", "baseline": 40, "target": 70, "deadline": "2099-12-31"}),
    }
    return goal, post(api, "kpis", body, token=token)["kpi"]


def log(api, kpi_id, value, token="ana-test", **more):
    return post(api, f"kpis/{kpi_id}/readings", {"value": value, **more}, token=token)["reading"]
