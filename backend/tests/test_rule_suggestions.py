"""Rule suggestions (backend/rule_suggestions.py): the weekly job finds wake rules that mostly woke a bot for nothing and
stores one suggestion each; a person applies or dismisses it, with the right to edit that rule. Nothing changes by itself."""

from backend import rule_suggestions as RS
from backend import rules as R
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


def runs(api, rule_id, total, idle, days_ago=1, bot="cpo"):
    """`total` finished runs woken by `rule_id`, `idle` of which did nothing."""
    started = H.shift(H.now(), days=-days_ago)
    with api.app.state.store.transaction() as c:
        for n in range(total):
            c.execute("INSERT INTO turns(id,bot,started,rule_id,did_nothing) VALUES(?,?,?,?,?)",
                      (H.new_id(), bot, started, rule_id, int(n < idle)))


def team_rule(api, event="status_or_relation", then="wake", token="ana-test", **more):
    return post(api, "rules", {"scope_kind": "team", "event": event, "then": then, **more}, token=token)["rule"]


def propose(api):
    with api.app.state.store.transaction() as c:
        return RS.propose_demotions(c, H.now())


def open_ones(api, token="ben-test", status="open"):
    return get(api, "rules/suggestions?status=" + status, token=token)["suggestions"]


def rule(api, rule_id):
    with api.app.state.store.read() as c:
        return R.view(R.get(c, rule_id))


def test_only_rules_above_half_no_op_over_enough_runs_in_the_week_are_suggested_and_a_repeat_adds_none(api):
    noisy, fine, few, old = (team_rule(api, event=e)["id"] for e in ("status_or_relation", "assignment", "comment", "stall"))
    runs(api, noisy, 24, 15)
    runs(api, fine, 24, 5)
    runs(api, few, 10, 10)
    runs(api, old, 30, 30, days_ago=9)                       # outside the week
    runs(api, "stall", 30, 20)                               # a built-in
    runs(api, "person_message", 40, 40)                      # the floor: never
    runs(api, "routine", 40, 40)                             # scheduled work: never
    first = propose(api)
    assert len(first["made"]) == 2
    by_rule = {s["rule_id"]: s for s in open_ones(api)}
    assert set(by_rule) == {noisy, "stall"}
    assert by_rule[noisy]["kind"] == "note" and by_rule[noisy]["runs_7d"] == 24 and by_rule[noisy]["no_op_rate_7d"] == 0.625
    assert by_rule["stall"]["kind"] == "override"
    runs(api, noisy, 4, 4)
    assert propose(api)["made"] == []
    assert {s["rule_id"]: s["runs_7d"] for s in open_ones(api)}[noisy] == 28, "figures refresh, no second card"


def test_a_member_cannot_see_or_apply_a_suggestion_and_applying_changes_only_what_it_says_and_dismiss_puts_it_back(api):
    noisy = team_rule(api)
    runs(api, noisy["id"], 24, 15)
    propose(api)
    [one] = open_ones(api)
    assert open_ones(api, "cara-test") == []
    post(api, f"rules/suggestions/{one['id']}/apply", {}, token="cara-test", expected=403)
    assert rule(api, noisy["id"])["then"] == "wake", "refused: nothing changed"
    done = post(api, f"rules/suggestions/{one['id']}/apply", {}, token="ben-test")["suggestion"]
    assert done["status"] == "applied" and rule(api, noisy["id"])["then"] == "note"
    post(api, f"rules/suggestions/{one['id']}/apply", {}, token="ben-test", expected=409)
    assert [s["status"] for s in open_ones(api, status="all")] == ["applied"] and open_ones(api) == []
    post(api, f"rules/suggestions/{one['id']}/dismiss", {}, token="cara-test", expected=403)
    undone = post(api, f"rules/suggestions/{one['id']}/dismiss", {}, token="ben-test")["suggestion"]
    assert undone["status"] == "dismissed" and rule(api, noisy["id"])["then"] == "wake"


def test_a_rule_edited_since_is_not_applied_and_a_declined_one_is_not_suggested_again(api):
    edited, declined = team_rule(api)["id"], team_rule(api, event="comment")["id"]
    runs(api, edited, 24, 15)
    runs(api, declined, 24, 15)
    propose(api)
    by_rule = {s["rule_id"]: s for s in open_ones(api)}
    post(api, f"rules/{edited}", {"expected_revision": 1, "note": "keep"}, token="ben-test")
    stale = post(api, f"rules/suggestions/{by_rule[edited]['id']}/apply", {}, token="ben-test", expected=409)
    assert stale["error"]["code"] == "version_conflict" and rule(api, edited)["then"] == "wake"
    assert post(api, f"rules/suggestions/{by_rule[declined]['id']}/dismiss", {}, token="ben-test")["suggestion"]["status"] == "dismissed"
    again = propose(api)
    assert again["made"] and {s["rule_id"] for s in open_ones(api)} == {edited}, "the edited rule is looked at again; the declined one is not"
    assert rule(api, declined)["then"] == "wake"


def test_a_built_in_gets_a_team_override_that_dismiss_removes_and_a_changed_one_is_left_for_a_person(api):
    runs(api, "stall", 30, 20)
    propose(api)
    [one] = open_ones(api)
    done = post(api, f"rules/suggestions/{one['id']}/apply", {}, token="ben-test")["suggestion"]
    made = rule(api, done["made_rule_id"])
    assert made["scope_kind"] == "team" and made["event"] == "stall" and made["then"] == "note" and made["when"] == {}
    post(api, f"rules/{made['id']}", {"expected_revision": 1, "on": False}, token="ben-test")
    kept = post(api, f"rules/suggestions/{one['id']}/dismiss", {}, token="ben-test", expected=409)
    assert kept["error"]["code"] == "version_conflict" and rule(api, made["id"])["enabled"] is False
    post(api, f"rules/{made['id']}/delete", {}, token="ben-test")
    again = post(api, f"rules/suggestions/{one['id']}/dismiss", {}, token="ben-test")["suggestion"]
    assert again["status"] == "dismissed"


def test_the_weekly_job_runs_now_records_its_result_and_has_a_switch(api):
    runs(api, "stall", 30, 20)
    reply = post(api, "system-routines/propose_demotions/run", {}, token="ben-test")
    assert reply["job"]["last"]["result"] == "1 rule suggested" and len(open_ones(api)) == 1
    again = post(api, "system-routines/propose_demotions/run", {}, token="ben-test")
    assert again["job"]["last"]["result"] == "No rules to suggest"
    post(api, "system-routines/propose_demotions/run", {}, token="cara-test", expected=403)
