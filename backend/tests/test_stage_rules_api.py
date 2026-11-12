"""Stage rules over HTTP (Stage rules spec, step 1, part 3; acceptance 8 and 9): the rules that apply to a stage,
grouped; who adds, edits and retires them; a bot only suggests and a person accepts or skips; the Rules page's groups
with a count each, Built in read-only.

The org (backend/tests/helpers/org_rules.py): Ana owns the company, Ben and Cara are plain members, Ops is a bot.
Engineering is managed by Ben."""

from backend.tests.helpers import engine as X
from backend.tests.helpers.api import as_member, get, post
from backend.tests.helpers.org_rules import org

BUILD, FIX = "eng-build", "eng-fixes-1"


def engineering(api):
    org(api)
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        X.pipeline(c, "eng", "Engineering", stages=({"name": "Build"}, {"name": "Fixes 1"}), manager="human:ben")
        X.pipeline(c, "prod", "Product", stages=({"name": "Spec"},))


def add(api, body, token="ana-test", expected=200):
    return post(api, "stage-rules", body, token=token, expected=expected)


OWNER_ON_TEAM = {
    "name": "Owner on the team",
    "pipeline": "eng",
    "stages": [BUILD],
    "event": "owner_changes",
    "cond": {"owner_not_on_team": "engineering"},
    "then": "block",
    "reason": "The owner is not on the Engineering team.",
}


def test_the_stage_panel_lists_every_rule_that_applies_grouped_most_specific_first(api):
    engineering(api)
    build = add(api, OWNER_ON_TEAM)
    whole = add(api, {"name": "Quiet bot tasks", "pipeline": "eng", "event": "timer",
                      "timer": {"kind": "quiet", "hours": 6}, "then": "tell_manager"})
    company = add(api, {"name": "Snooze only for a real wait", "event": "bot_snoozes",
                        "jev": "Is the bot waiting on something outside, or blocked?", "then": "block"})
    other_stage = add(api, {**OWNER_ON_TEAM, "name": "Fixes only", "stages": [FIX]})
    other_pipeline = add(api, {"name": "Spec rule", "pipeline": "prod", "event": "enters", "then": "tell_owner"})

    got = get(api, "stage-rules?pipeline=eng&stage=" + BUILD, token="cara-test")
    ids = [r["id"] for r in got["items"]]
    # The default Snooze only for a real wait is the company's, from the start.
    assert ids == [build["id"], whole["id"], "sr_real_wait", company["id"]]
    assert other_stage["id"] not in ids and other_pipeline["id"] not in ids
    assert [r["scope"] for r in got["items"]] == ["stage", "pipeline", "company", "company"]
    # Each rule says what it costs: a Jev question is one call per event, the rest are free.
    jev = "one Jev call per event"
    assert [r["cost"] for r in got["items"]] == ["free", "free", jev, jev]
    assert got["items"][0]["stage_names"] == ["Build"] and got["items"][0]["pipeline_name"] == "Engineering"
    # Built in is listed, and read-only: it has no id to edit.
    assert got["built_in"] and all("id" not in b for b in got["built_in"])
    # Cara reads them all and changes none.
    assert not any(r["can_edit"] for r in got["items"]) and got["may_add"] is False


def test_the_pipelines_manager_adds_edits_and_retires_a_stage_rule_a_member_cannot(api):
    engineering(api)
    add(api, OWNER_ON_TEAM, token="cara-test", expected=403)
    made = add(api, OWNER_ON_TEAM, token="ben-test")
    assert made["status"] == "active" and made["can_edit"] is True and made["stages"] == [BUILD]
    assert get(api, "stage-rules?pipeline=eng&stage=" + BUILD, token="ben-test")["may_add"] is True

    path = f"stage-rules/{made['id']}"
    post(api, path, {"revision": made["revision"], "reason": "Not on Engineering."}, token="cara-test", expected=403)
    edited = post(api, path, {"revision": made["revision"], "reason": "Not on Engineering."}, token="ben-test")
    assert edited["reason"] == "Not on Engineering." and edited["revision"] == made["revision"] + 1
    # An edit against the revision read before is refused.
    post(api, path, {"revision": made["revision"], "name": "Late"}, token="ben-test", expected=409)
    # A stage of another pipeline is refused.
    post(api, path, {"revision": edited["revision"], "stages": ["prod-spec"]}, token="ben-test", expected=422)

    post(api, path + "/retire", {"revision": edited["revision"]}, token="cara-test", expected=403)
    post(api, path + "/retire", {"revision": edited["revision"]}, token="ben-test")
    assert made["id"] not in [r["id"] for r in get(api, "stage-rules?pipeline=eng")["items"]]
    retired = get(api, "stage-rules?pipeline=eng&status=retired")["items"]
    assert [r["id"] for r in retired] == [made["id"]]


def test_a_company_rule_is_the_owners_and_admins_alone(api):
    engineering(api)
    add(api, {"name": "Everywhere", "event": "enters", "then": "tell_owner"}, token="ben-test", expected=403)
    add(api, {"name": "Everywhere", "event": "enters", "then": "tell_owner"}, token="ana-test")
    # A company rule picks no stages.
    add(api, {"name": "Bad", "event": "enters", "then": "tell_owner", "stages": [BUILD]}, expected=422)


def test_a_bot_only_suggests_and_a_person_accepts_or_skips(api):
    engineering(api)
    made = add(api, OWNER_ON_TEAM, token="ops-test")
    assert made["status"] == "suggested" and made["created_by"] == "bot:ops"
    # A suggestion does not apply yet.
    assert made["id"] not in [r["id"] for r in get(api, "stage-rules?pipeline=eng&stage=" + BUILD)["items"]]
    assert [r["id"] for r in get(api, "stage-rules?status=suggested")["items"]] == [made["id"]]
    # A bot never accepts or skips.
    for action in ("accept", "skip"):
        post(api, f"stage-rules/{made['id']}/{action}", {}, token="ops-test", expected=403)
    # A person who may change the pipeline accepts it: it applies.
    post(api, f"stage-rules/{made['id']}/accept", {}, token="cara-test", expected=403)
    accepted = post(api, f"stage-rules/{made['id']}/accept", {}, token="ben-test")
    assert accepted["status"] == "active"
    assert made["id"] in [r["id"] for r in get(api, "stage-rules?pipeline=eng&stage=" + BUILD)["items"]]

    # A bot's change to a rule in force is a suggestion too; the rule stays as it was until a person accepts it.
    change = post(api, f"stage-rules/{made['id']}",
                  {"revision": accepted["revision"], "reason": "Ask the manager."}, token="ops-test")
    assert change["status"] == "suggested" and change["suggests"] == made["id"]
    assert get(api, f"stage-rules/{made['id']}")["reason"] == OWNER_ON_TEAM["reason"]
    post(api, f"stage-rules/{change['id']}/skip", {}, token="ben-test")
    assert get(api, "stage-rules?status=suggested")["items"] == []
    # Accept and skip are for suggestions; retire for a rule in force.
    post(api, f"stage-rules/{made['id']}/accept", {}, token="ben-test", expected=409)
    # A bot's retire is a suggestion to retire it: the rule stays in force until a person accepts.
    gone = post(api, f"stage-rules/{made['id']}/retire", {"why": "Covered elsewhere."}, token="ops-test")
    assert gone["status"] == "suggested" and gone["suggests"] == made["id"]
    assert made["id"] in [r["id"] for r in get(api, "stage-rules?pipeline=eng&stage=" + BUILD)["items"]]
    post(api, f"stage-rules/{gone['id']}/accept", {}, token="ben-test")
    assert made["id"] not in [r["id"] for r in get(api, "stage-rules?pipeline=eng&stage=" + BUILD)["items"]]
    # --mine: only the rules the caller wrote or suggested.
    add(api, {"name": "Ana's", "event": "enters", "then": "tell_owner"})
    assert [r["id"] for r in get(api, "stage-rules?status=any&mine=true", token="ops-test")["items"]] == [
        made["id"],
        change["id"],
        gone["id"],
    ]


def test_the_rules_page_lists_every_kind_of_rule_in_its_group_with_a_count(api):
    engineering(api)
    before = {g["key"]: g["count"] for g in get(api, "rule-groups")["groups"]}
    add(api, OWNER_ON_TEAM)
    add(api, {**OWNER_ON_TEAM, "name": "Fixes only", "stages": [FIX]})
    add(api, {"name": "From a bot", "pipeline": "eng", "event": "enters", "then": "tell_owner"}, token="ops-test")
    post(api, "rules", {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})
    post(api, "rules", {"kind": "internal", "text": "Cite sources."}, token="ops-test")

    got = get(api, "rule-groups", token="cara-test")
    keys = [g["key"] for g in got["groups"]]
    assert keys == ["internal", "external", "stages", "due", "wakes", "automations", "builtin", "suggested"]
    labels = [g["label"] for g in got["groups"]]
    assert labels == ["Company", "Outside", "Pipelines and stages", "Due", "Wakes", "Automations on tasks", "Built in",
                      "Suggested"]
    count = {g["key"]: g["count"] for g in got["groups"]}
    assert count["internal"] == 1 and count["stages"] == before["stages"] + 2
    # Suggested holds a bot's org rule and its stage rule.
    assert count["suggested"] == 2
    assert count["builtin"] == len(got["built_in"]) > 0
    assert count["wakes"] == len(got["wakes"]) and count["automations"] == len(got["automations"])


def test_reads_name_a_real_pipeline_and_stage(api):
    engineering(api)
    get(api, "stage-rules?pipeline=nope", expected=404)
    get(api, "stage-rules?pipeline=eng&stage=prod-spec", expected=404)
    get(api, "stage-rules?stage=" + BUILD, expected=422)
    get(api, "stage-rules/sr_missing", expected=404)
