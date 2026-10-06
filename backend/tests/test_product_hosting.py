"""Hosting follows people.yaml: a team's primary person runs that team's bots, owner the rest."""

from backend.tests.test_api import api, get, post, runner  # noqa: F401


def test_enrollment_delegates_all_product_bots_but_no_other_teams(api):
    machine = runner(api, "ben", "Ben product Mac")
    eligible = get(api, "runners/eligible", machine["token"])
    assert {r["bot"] for r in eligible} == {"cpo", "product-design"}
    for bot in eligible:
        post(api, "runners/adopt", {"bot": bot["bot"], "expected_generation": bot["generation"]}, machine["token"])
    assigned = get(api, "runners/assignments", machine["token"])
    assert {r["bot"] for r in assigned} == {"cpo", "product-design"}
    post(api, "runners/adopt", {"bot": "finance", "expected_generation": 0}, machine["token"], expected=403)
