"""The setup page's draft engine (backend/company_file): spec tests 5-10, 18 and 19 of the tico.team/setup brain, the
starter pack, undo, pending and the company file round trip. Tests 3, 4 and 4b need the model and are ticket 46's."""
import copy
import datetime as dt
import json
import random

import pytest
import yaml

from backend import company_file as C
from backend.company_file import actions as A
from backend.company_file import draft as D

TODAY = dt.date(2026, 10, 17)
CREATED = dt.datetime(2026, 10, 17, 9, 30, tzinfo=dt.timezone.utc)
# Built at run time so no scanner mistakes the test for a leak.
OPENAI = "sk-" + "proj" + "A1b2C3d4E5f6G7h8J9k0L1m2"
GITHUB = "ghp" + "_" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def started(**fields):
    """A draft as the page sends it after the first turn: the starter pack on the chart."""
    turn = C.Turn(C.blank(), TODAY)
    return {**turn.finish(), **fields}


def run(draft, *steps):
    turn = C.Turn(draft, TODAY)
    results = [turn.apply(name, args) for name, args in steps]
    return turn.finish(), results


def titles(draft):
    return [b["title"] for b in draft["bots"]]


def test_an_empty_draft_starts_from_the_starter_pack_with_each_team_under_its_head():
    turn = C.Turn(C.blank(), TODAY)
    draft = turn.finish()
    pack = [b for b in draft["bots"] if not b["core"]]
    assert turn.seeded and len(pack) == len(C.PACK["bots"]) == 22
    assert [b["template"] for b in pack] == [row["template"] for row in C.PACK["bots"]]
    assert {b["template"] for b in draft["bots"] if b["core"]} == {"coo", "botops"}
    assert all(b["reason"] == "Part of Tico's starter pack." and not b["start"] for b in pack)
    heads = {}
    for bot, row in zip(pack, C.PACK["bots"]):
        head = heads.setdefault(row["team"], bot)
        assert D.manager_of(draft, bot) == ("you" if head is bot else head["id"]), bot["title"]
    # The three pr-reviewer cards stand side by side: titles are unique, templates need not be.
    assert titles(draft).count("Builder") == 1 and sum(b["template"] == "pr-reviewer" for b in pack) == 3
    assert "undo" not in draft, "the pack is where the chart starts, not a change to undo"
    # A chart with bots on it is never seeded again, even after the visitor took most of them off.
    trimmed = {**draft, "bots": draft["bots"][:3]}
    assert len(C.Turn(trimmed, TODAY).finish()["bots"]) == 3
    text = C.to_yaml(draft, CREATED)
    doc = yaml.safe_load(text)
    assert doc["version"] == 0.2 and len(doc["employees"]) == 24
    assert C.to_yaml(C.from_yaml(text), CREATED) == text


def test_5_the_visitor_s_own_edits_stand_and_a_removed_bot_stays_off():
    draft = started()
    draft["bots"] = [b for b in draft["bots"] if b["template"] != "bookkeeping"]
    draft["bots"][4]["title"] = "Ada"
    draft["removed"] = [{"template": "bookkeeping", "title": "Bookkeeper"}]
    after, results = run(draft, ("bot_set_start", {"bot": "Ada", "start": "now"}))
    assert results[0]["ok"] and "Bookkeeper" not in titles(after) and "Ada" in titles(after)
    assert after["removed"] == [{"template": "bookkeeping", "title": "Bookkeeper"}]
    after, results = run(after, ("bot_remove", {"bot": "Ada"}))
    assert [r["title"] for r in after["removed"]] == ["Bookkeeper", "Ada"]
    # Asked for again, it comes back and leaves the list.
    after, results = run(after, ("bot_add", {"template": "Bookkeeper", "reason": "Invoices eat your Fridays."}))
    assert results[0]["ok"] and "Bookkeeper" in titles(after) and [r["title"] for r in after["removed"]] == ["Ada"]


def test_6_undo_takes_back_the_last_change_once_and_leaves_pending_alone():
    draft = started(pending=["sales"])
    after, results = run(draft, ("team_remove", {"team": "finance"}))
    assert results[0]["ok"] and not any(b["dept"] == "finance" for b in after["bots"])
    assert "pending" not in after["undo"] and "chat" not in after["undo"] and after["pending"] == ["sales"]
    after["pending"], after["chat"] = ["sales", "marketing"], [{"who": "me", "text": "undo that"}]
    restored, result = C.undo(after, TODAY)
    assert result["ok"] and D.content(restored)["bots"] == D.content(draft)["bots"]
    assert restored["pending"] == ["sales", "marketing"] and restored["chat"] == after["chat"]
    assert "undo" not in restored
    again, result = C.undo(restored, TODAY)
    assert not result["ok"] and again == restored
    # Moving between tabs is not a change: the undo from the turn before survives it.
    stepped, _ = run(after, ("step_go", {"step": "goals"}))
    assert stepped["undo"] == after["undo"] and stepped["step"] == "goals"


def test_7_the_file_matches_the_draft_and_reads_back_after_any_edits():
    rng = random.Random(7)
    odd = ["no", "yes: really", "# not a comment", "line\nbreak", "tab\there", "quote \" and \\ slash", " sep",
           "ctrl\x85\x7f", "émoji 🚀", "", "null", "-dash", "{brace}", "123", "1e5"]
    for _ in range(25):
        draft = started(chat=[{"who": "me", "text": "hi"}])
        turn = C.Turn(draft, TODAY)
        for _ in range(12):
            bots = [b["id"] for b in turn.draft["bots"]]
            goals = [g["id"] for g in turn.draft["goals"]] or ["none"]
            name, args = rng.choice([
                ("company_set", {"name": rng.choice(odd), "about": rng.choice(odd), "signals": ["small_team"]}),
                ("bot_add", {"template": rng.choice(["bookkeeping", "SEO", "Recruiter"]), "reason": rng.choice(odd),
                             "title": rng.choice(odd) or "Helper"}),
                ("bot_remove", {"bot": rng.choice(bots)}), ("bot_rename", {"bot": rng.choice(bots), "title": rng.choice(odd)}),
                ("bot_set_reports_to", {"bot": rng.choice(bots), "to": rng.choice(bots + ["you"])}),
                ("bot_set_first_job", {"bot": rng.choice(bots), "job": rng.choice(odd) or "Send the invoices"}),
                ("bot_set_routine", {"bot": rng.choice(bots), "routine": rng.choice(odd) or "Daily"}),
                ("team_remove", {"team": rng.choice(D.TEAMS)}),
                ("goal_add", {"title": rng.choice(odd) or "Grow", "owner": rng.choice(["Sam", "you", "Jo Kim"]),
                              "measure": "accounts", "unit": "#", "direction": "down", "target": rng.choice([3, 2.5, 1e-05]),
                              "baseline": 9, "deadline": "in 90 days"}),
                ("goal_add", {"title": "Steady", "measure": "response hours", "unit": "h", "direction": "range",
                              "min": 1, "max": 4}),
                ("goal_edit", {"goal": rng.choice(goals), "title": rng.choice(odd) or "Grow"}),
                ("person_set", {"name": rng.choice(["Maya", "Sam", "Jo"]), "role": rng.choice(["CEO", "Head of sales", ""])}),
                ("agent_set", {"runtime": "claude-code", "job": rng.choice(odd) or "Reviews pull requests"}),
                ("rules_set_never", {"never": ["send", "sign"]}), ("rules_set_review", {"review": "two"}),
                ("tools_set", {"connect": ["github", "mail"]}), ("undo", {}),
            ])
            turn.apply(name, args)
        draft = turn.finish()
        text = C.to_yaml(draft, CREATED)
        doc = yaml.safe_load(text)
        employees = [b for b in draft["bots"] if not b["existing"]]
        assert [e["title"] for e in doc["employees"] or []] == [b["title"] for b in employees]
        assert [e["reports_to"] for e in doc["employees"] or []] == [D.manager_of(draft, b) for b in employees]
        assert [g["title"] for g in doc["goals"]] == [g["title"] for g in draft["goals"]]
        assert doc["company"]["name"] == draft["company"]["name"]
        assert not {"undo", "chat", "removed", "pending"} & set(doc) and "isNew" not in text
        back = C.from_yaml(text)
        assert C.to_yaml(back, CREATED) == text
        # The file lists the visitor's own agents after Tico's bots.
        filed = sorted(draft["bots"], key=lambda b: b["existing"])
        removed = draft.get("removed", [])
        assert D.same_plan({**back, "removed": removed}, {**draft, "bots": filed, "seq": back["seq"], "removed": removed})


def test_8_and_18_a_goal_in_words_gets_its_measure_deadline_and_owner():
    said = {"title": "50 wholesale accounts by March 1", "owner": "Sam", "measure": "wholesale accounts",
            "unit": "accounts", "direction": "up", "target": 50, "deadline": "by March 1"}
    draft, results = run(started(), ("goal_add", said))
    goal, sam = draft["goals"][0], D.owner(draft)
    assert results[0]["ok"] and "Added Sam to the team" in results[0]["said"]
    assert {key: goal[key] for key in ("measure", "unit", "direction", "target", "baseline", "deadline")} == {
        "measure": "wholesale accounts", "unit": "accounts", "direction": "up", "target": 50, "baseline": None,
        "deadline": "2027-03-01"}
    assert [p["name"] for p in draft["people"]] == ["You", "Sam"] and sam["name"] == "You"
    assert goal["owner"] == next(p["id"] for p in draft["people"] if p["name"] == "Sam")
    goals = yaml.safe_load(C.to_yaml(draft, CREATED))["goals"]
    assert goals[0]["owner"] == "sam" and goals[0]["deadline"] == dt.date(2027, 3, 1) and goals[0]["target"] == 50
    # "the next March 1": on March 2 that is a year on.
    assert A.deadline("March 1", dt.date(2027, 3, 2)) == "2028-03-01" == A.deadline("1 March", dt.date(2027, 3, 1))
    assert A.deadline("2027-06-30", TODAY) == A.deadline("end of June", TODAY) == "2027-06-30"
    for past in ("2026-10-01", "2026-02-30", "someday", "in 40 years"):
        with pytest.raises(A.Refusal):
            A.deadline(past, TODAY)


def test_19_a_goal_without_a_number_is_saved_with_its_measure_not_set_yet():
    draft, results = run(started(), ("goal_add", {"title": "happier customers"}))
    goal = draft["goals"][0]
    assert results[0]["ok"] and "not set yet" in results[0]["said"] and goal["measure"] == "" and goal["owner"] == "you"
    text = C.to_yaml(draft, CREATED)
    assert "measure: null  # not set yet: which number, and by when?" in text
    assert yaml.safe_load(text)["goals"][0]["measure"] is None
    # The answer, once given, makes it measurable; a half answer is refused.
    later, results = run(draft, ("goal_edit", {"goal": goal["id"], "measure": "NPS", "unit": "points", "target": 40}),
                         ("goal_edit", {"goal": goal["id"], "measure": "NPS", "unit": "points", "target": 40,
                                        "deadline": "end of quarter"}))
    assert not results[0]["ok"] and "deadline" in results[0]["refused"] and results[1]["ok"]
    assert later["goals"][0]["deadline"] == "2026-12-31"


def test_9_a_template_tico_doesn_t_have_is_refused_with_the_closest_real_one():
    draft = started()
    turn = C.Turn(draft, TODAY)
    before = copy.deepcopy(turn.draft)
    result = turn.apply("bot_add", {"template": "Invoice Chaser", "reason": "Late invoices."})
    assert not result["ok"] and "no \"Invoice Chaser\" template" in result["refused"]
    assert "The closest is" in result["refused"] and turn.draft == before
    near = A.closest_role("Invoice Chaser")
    assert near["slug"] in {r["slug"] for r in C.ROLES} and near["department"] == "finance"


def test_10_key_shaped_strings_never_reach_the_draft_or_the_file():
    draft = started(chat=[{"who": "me", "text": "my key is " + OPENAI}])
    draft["company"]["about"] = "We roast coffee. password: hunter22 and " + AWS + ' {"pwd": "Zq1"}'
    turn = C.Turn(draft, TODAY)
    turn.apply("bot_add", {"template": "github", "reason": "Use " + GITHUB, "title": "Repo bot"})
    turn.apply("goal_add", {"title": "token=" + "x9" * 20})
    turn.apply("company_set", {"name": '{"password": "Wombat9"}'})
    out = turn.finish()
    text = json.dumps(out) + C.to_yaml(out, CREATED)
    assert turn.keys_removed and "[removed]" in text
    for secret in (OPENAI, GITHUB, AWS, "hunter22", "x9" * 20, "Zq1", "Wombat9"):
        assert secret not in text
    # The file writer scrubs on its own: a page draft can reach it without a turn.
    direct = started()
    direct["people"] = [{"id": "maya", "name": "Maya", "role": "Key " + OPENAI, "dept": None, "owner": True}]
    assert OPENAI not in C.to_yaml(direct, CREATED)
    assert C.apply_action(started(), "step_go", {"step": "goals"}, TODAY)[1]["keys_removed"] is False
    # The words of every role, and an ordinary sentence about tokens, are left alone.
    for role in C.ROLES:
        for field in ("name", "summary", "when", "first_routine"):
            assert C.scrub(role[field]) == (role[field], False), (role["slug"], field)
    assert C.scrub("The token is optional; the api key is set on your install.")[1] is False


# Every label the scrubber knows, each value length a pasted password can have, in each way a value is written.
LABELS = ["password", "passwd", "passcode", "pwd", "PWD", "secret", "token", "api_key", "api key", "API-Key", "access_key",
          "client_secret", "client secret", "private_key", "private key"]
FORMS = ["{l}: {v}", "{l}={v}", "{l} = {v}", '"{l}": "{v}"', '{{"{l}":"{v}", "name": "Ann"}}', "{{'{l}': '{v}'}}",
         "{l}: '{v}'", '{{\\"{l}\\": \\"{v}\\", \\"name\\": \\"Ann\\"}}']


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("size", [1, 2, 3, 4])
@pytest.mark.parametrize("form", FORMS)
def test_10_every_assigned_credential_value_is_removed_whatever_its_length(label, size, form):
    text = "Here: " + form.format(l=label, v="Qzxw"[:size]) + " thanks"
    out, hit = C.scrub(text)
    assert hit and out == "Here: " + form.format(l=label, v="[removed]") + " thanks"
    assert C.scrub(out) == (out, False)


@pytest.mark.parametrize("text, out", [
    ('{"password": "a\\"b c", "name": "Ann"}', '{"password": "[removed]", "name": "Ann"}'),
    ('{"secret": "ab\\\\", "name": "Ann"}', '{"secret": "[removed]", "name": "Ann"}'),
    ('{"token": "a\\\\\\"b", "name": "Ann"}', '{"token": "[removed]", "name": "Ann"}'),
    ("{'pwd': 'it\\'s', 'name': 'Ann'}", "{'pwd': '[removed]', 'name': 'Ann'}"),
    ("secret: 'it''s here', name: Ann", "secret: '[removed]', name: Ann"),
    ('{\\"api_key\\": \\"a b\\", \\"name\\": \\"Ann\\"}', '{\\"api_key\\": \\"[removed]\\", \\"name\\": \\"Ann\\"}'),
    ('password: "ab cd\nname: Ann', "password: [removed]\nname: Ann"),
    ("password: ab, name: Ann; role: CEO}", "password: [removed], name: Ann; role: CEO}"),
    ("the password is Qz", "the password is [removed]"),
    ("token: 1234", "token: [removed]"), ('{"token": "5"}', '{"token": "[removed]"}'), ("secret: 5", "secret: [removed]"),
])
def test_10_a_quoted_value_goes_whole_past_escapes_and_its_neighbours_stay(text, out):
    assert C.scrub(text) == (out, True)
    assert C.scrub(out) == (out, False)


@pytest.mark.parametrize("text", [
    "The token is optional", "the api key is set on your install", "Our secret is good coffee", "token: 5 per user",
    "Token: 120 a day", '{"token": 12}', "password:\nname: Ann", '{"password": ""}', "password: [removed]",
    '{"password": "[removed]"}', "Keys are connected later; no tokens or passwords here.",
])
def test_10_prose_counts_and_removed_marks_are_left_alone(text):
    assert C.scrub(text) == (text, False)


def test_10_short_values_are_cut_at_every_door_and_each_one_is_reported():
    def about(text):
        draft = started()
        draft["company"]["about"] = text
        return draft

    # The draft as sent: a text field, the chat and the undo copy, each on its own.
    turn = C.Turn(about('{"secret": "Qz"}'), TODAY)
    assert turn.keys_removed and turn.finish()["company"]["about"] == '{"secret": "[removed]"}'
    turn = C.Turn(started(chat=[{"who": "me", "text": 'api_key="Qz"'}]), TODAY)
    assert turn.keys_removed and turn.finish()["chat"][0]["text"] == 'api_key="[removed]"'
    sent = started()
    sent["undo"] = D.content(about("token: Qz"))
    turn = C.Turn(sent, TODAY)
    assert turn.keys_removed and turn.apply("undo", {})["ok"]
    assert turn.finish()["company"]["about"] == "token: [removed]"
    # An action's input, and the refusal naming an action that doesn't exist.
    turn = C.Turn(started(), TODAY)
    result = turn.apply("company_set", {"about": "client secret: Qz"})
    assert turn.keys_removed and "Qz" not in json.dumps(result)
    assert turn.finish()["company"]["about"] == "client secret: [removed]"
    turn = C.Turn(started(), TODAY)
    result = turn.apply(OPENAI, {})
    assert turn.keys_removed and not result["ok"] and OPENAI[:12] not in result["refused"]
    turn = C.Turn(started(), TODAY)
    result = turn.apply("company_set", {OPENAI: "x"})
    assert turn.keys_removed and not result["ok"] and OPENAI[:12] not in result["refused"]
    # validate, reading a file, and writing one straight from a page draft.
    assert C.validate(about("pwd: Qz"))["company"]["about"] == "pwd: [removed]"
    clean = C.to_yaml(started(), CREATED)
    assert 'about: ""' in clean
    read = C.from_yaml(clean.replace('about: ""', 'about: "{\\"password\\": \\"Qz\\"}"'))
    assert read["company"]["about"] == '{"password": "[removed]"}'
    direct = started()
    direct["people"] = [{"id": "maya", "name": "Maya", "role": "secret=Qz", "dept": None, "owner": True}]
    assert yaml.safe_load(C.to_yaml(direct, CREATED))["people"][0]["role"] == "secret=[removed]"


def test_10_scrubbing_copies_and_a_second_pass_finds_nothing():
    draft = started(chat=[{"who": "me", "text": "password: Qz and " + OPENAI}])
    draft["company"]["about"] = '{"token": "a\\"b"}'
    draft["extra_" + GITHUB] = 1
    sent = copy.deepcopy(draft)
    once, hit = C.scrub_all(draft)
    assert hit and draft == sent and GITHUB not in json.dumps(once)
    assert C.scrub_all(once) == (once, False)
    del draft["extra_" + GITHUB]
    sent = copy.deepcopy(draft)
    turn = C.Turn(draft, TODAY)
    turn.apply("company_set", {"name": "pwd: Qz"})
    out = turn.finish()
    assert draft == sent and C.scrub_all(out) == (out, False)
    assert C.Turn(out, TODAY).keys_removed is False
    assert C.from_yaml(C.to_yaml(out, CREATED))["company"] == out["company"]


BAD = [
    ("nonsense", {}), ("bot_add", {"template": "bookkeeping"}), ("bot_add", {"template": "bookkeeping", "reason": ""}),
    ("bot_add", {"template": "bookkeeping", "reason": "x", "colour": "red"}),
    ("bot_add", {"template": "bookkeeping", "reason": "x", "title": "Builder"}),
    ("bot_add", {"template": "botops", "reason": "a second one"}),
    ("bot_add", {"template": "bookkeeping", "reason": "x" * 301}), ("bot_add", {"template": 7, "reason": "x"}),
    ("bot_remove", {"bot": "botops"}), ("bot_remove", {"bot": "ghost"}), ("bot_rename", {"bot": "builder", "title": "Reviewer 1"}),
    ("bot_rename", {"bot": "builder", "title": " "}), ("bot_set_reports_to", {"bot": "engineering-lead", "to": "builder"}),
    ("bot_set_reports_to", {"bot": "builder", "to": "builder"}), ("bot_set_reports_to", {"bot": "builder", "to": "Recruiter"}),
    ("bot_set_reports_to", {"bot": "builder", "to": "Nobody Here"}), ("bot_set_start", {"bot": "builder", "start": "soon"}),
    ("bot_set_start", {"bot": "botops", "start": "later"}), ("bot_set_first_job", {"bot": "coo", "job": "x"}),
    ("bot_set_routine", {"bot": "builder", "routine": "x" * 201}), ("team_remove", {"team": "core"}),
    ("team_remove", {"team": "legal"}), ("goal_add", {"title": ""}), ("goal_add", {"title": "Grow", "target": 5}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "up", "target": 5,
                  "deadline": "2020-01-01"}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "up", "target": float("inf"),
                  "deadline": "June"}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "up", "target": True, "deadline": "June"}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "range", "min": 5, "max": 1}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "sideways", "target": 1, "deadline": "June"}),
    ("goal_add", {"title": "Grow", "owner": "Recruiter"}), ("goal_edit", {"goal": "ghost", "title": "x"}),
    ("goal_remove", {"goal": "ghost"}), ("person_set", {"name": ""}), ("person_set", {"person": "ghost", "name": "Al"}),
    ("agent_set", {"runtime": "skynet", "job": "x"}), ("agent_set", {"agent": "builder", "runtime": "codex", "job": "x"}),
    ("rules_set_never", {"never": ["send", "launch"]}), ("rules_set_review", {"review": "none"}),
    ("tools_set", {"connect": ["bank"]}), ("step_go", {"step": "company-ish"}), ("undo", {"steps": 2}), ("undo", {}),
    ("company_set", {}), ("company_set", {"signals": ["rich"]}), ("company_set", {"name": "x" * 81}),
    ("rules_use_defaults", {"mode": "custom"}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "up", "target": 5,
                  "deadline": "in 9999 years"}),
]


@pytest.mark.parametrize("name,args", BAD, ids=["%s-%d" % (row[0], i) for i, row in enumerate(BAD)])
def test_a_bad_action_is_refused_in_words_and_changes_nothing(name, args):
    turn = C.Turn(started(), TODAY)
    before = copy.deepcopy(turn.draft)
    result = turn.apply(name, args)
    assert result["ok"] is False and result["refused"] and turn.draft == before
    assert "undo" not in turn.finish()


def test_a_draft_that_breaks_its_shape_or_limits_is_refused():
    good = started()
    for bad in ({**good, "extra": 1}, {**good, "seq": 0}, {**good, "step": "x"}, {**good, "bots": "all"},
                {**good, "pending": ["sales"] * 2}, {**good, "pending": ["a%d" % i for i in range(11)]},
                {**good, "chat": [{"who": "system", "text": "x"}]}, {**good, "undo": {**good, "chat": []}},
                {**good, "goals": [{"id": "g", "title": "x", "owner": "you", "measure": "m", "unit": "", "direction": "up",
                                    "target": float("nan"), "deadline": None}]},
                {**good, "goals": [{"id": "g", "title": "x", "owner": "you", "measure": "", "target": "50", "due": "x"}]},
                {**good, "bots": good["bots"] * 2}, {**good, "company": {**good["company"], "about": "x" * 300001}},
                {**good, "bots": good["bots"][:2] + [{**good["bots"][2], "template": "wizard"}]}):
        with pytest.raises(C.DraftError):
            C.Turn(bad, TODAY)
    for bad in ("", "version: 0.1\n", "- a\n", "version: 0.2\nemployees:\n  - template: wizard\n", "{"):
        with pytest.raises(C.DraftError):
            C.from_yaml(bad)


def test_pending_rides_along_outside_the_file_and_the_undo_and_lets_go_of_a_team_that_is_gone():
    draft = started(pending=["sales", "marketing", "legal"])
    after, _ = run(draft, ("bot_rename", {"bot": "builder", "title": "Ada"}))
    assert after["pending"] == ["sales", "marketing"], "no legal team on the chart"
    assert "pending" not in after["undo"] and "pending" not in C.to_yaml(after, CREATED)
    after, _ = run(after, ("team_remove", {"team": "sales"}))
    assert after["pending"] == ["marketing"]
    assert C.from_yaml(C.to_yaml(after, CREATED)).get("pending") is None


def test_one_bot_has_the_first_job_and_it_starts_now():
    draft, results = run(started(), ("bot_set_first_job", {"bot": "bookkeeping", "job": "Chase last month's invoices"}),
                         ("bot_set_first_job", {"bot": "Builder", "job": "Fix the signup bug"}))
    marked = [b for b in draft["bots"] if b.get("first_job")]
    assert all(r["ok"] for r in results) and [(b["title"], b["start"]) for b in marked] == [("Builder", True)]
    assert [e.get("first_job") for e in yaml.safe_load(C.to_yaml(draft, CREATED))["employees"] if e.get("first_job")] == [
        "Fix the signup bug"]


def test_an_agent_the_visitor_already_runs_keeps_only_what_the_file_can_hold():
    draft, results = run(started(), ("agent_set", {"runtime": "codex", "job": "Reviews pull requests"}),
                         ("bot_set_first_job", {"bot": "Builder", "job": "Fix the signup bug"}))
    agent = next(b for b in draft["bots"] if b["existing"])
    turn = C.Turn(draft, TODAY)
    before = copy.deepcopy(turn.draft)
    for name, args in (("bot_set_first_job", {"bot": agent["id"], "job": "x"}), ("bot_set_start", {"bot": agent["id"], "start": "later"}),
                       ("bot_set_routine", {"bot": agent["id"], "routine": "Daily"})):
        assert not turn.apply(name, args)["ok"] and turn.draft == before, name
    # A page draft that gives its own agent a start, routine or first job is read as the file reads it back.
    sent = copy.deepcopy(draft)
    next(b for b in sent["bots"] if b["existing"]).update(start=False, routine="Daily", first_job="x")
    text = C.to_yaml(sent, CREATED)
    assert C.to_yaml(C.from_yaml(text), CREATED) == text == C.to_yaml(draft, CREATED)
    assert [b["title"] for b in C.from_yaml(text)["bots"] if b.get("first_job")] == ["Builder"]
    # No rule needing a person is written as an empty list, not as nothing.
    none, _ = run(draft, ("rules_set_never", {"never": []}))
    assert yaml.safe_load(C.to_yaml(none, CREATED))["rules"]["never_without_a_person"] == []


def test_a_reports_to_whose_target_left_the_chart_falls_back_to_the_line_the_file_draws():
    draft, _ = run(started(), ("person_set", {"name": "Maya", "role": "CEO"}), ("person_set", {"name": "Jo", "role": "Ops"}),
                   ("bot_set_reports_to", {"bot": "Builder", "to": "Jo"}))
    assert next(b for b in draft["bots"] if b["title"] == "Builder")["reports_to"] == "jo"
    # The visitor took Jo off the team on the page; an invented id is treated the same way.
    sent = {**draft, "people": [p for p in draft["people"] if p["name"] != "Jo"]}
    next(b for b in sent["bots"] if b["title"] == "Reviewer 1")["reports_to"] = "nobody"
    out = C.Turn(sent, TODAY).finish()
    assert not any(b.get("reports_to") for b in out["bots"])
    doc = yaml.safe_load(C.to_yaml(out, CREATED))
    assert {e["title"]: e["reports_to"] for e in doc["employees"]}["Builder"] == "engineering-lead"
    assert C.to_yaml(C.from_yaml(C.to_yaml(out, CREATED)), CREATED) == C.to_yaml(out, CREATED)


def test_hq_s_copy_runs_the_same_engine():
    from hq import company_file as H

    draft = started()
    assert H.to_yaml(draft, CREATED) == C.to_yaml(draft, CREATED)
    assert H.SPEC == C.SPEC and H.PACK == C.PACK and H.ROLES == C.ROLES
