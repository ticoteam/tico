"""The setup page's draft engine (backend/company_file): spec tests 5-10, 18 and 19 of the tico.team/setup brain, the
company models, undo, pending and the company file round trip. Tests 3, 4 and 4b need the model and are ticket 46's."""
import copy
import datetime as dt
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from backend import company_file as C
from backend.company_file import actions as A
from backend.company_file import draft as D
from backend.company_file import starter

TODAY = dt.date(2026, 10, 17)
CREATED = dt.datetime(2026, 10, 17, 9, 30, tzinfo=dt.timezone.utc)
# Built at run time so no scanner mistakes the test for a leak.
OPENAI = "sk-" + "proj" + "A1b2C3d4E5f6G7h8J9k0L1m2"
GITHUB = "ghp" + "_" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def started(**fields):
    """A draft as the page sends it after the first turn: the Everything model on the chart (no signals yet)."""
    turn = C.Turn(C.blank(), TODAY)
    return {**turn.finish(), **fields}


def run(draft, *steps):
    turn = C.Turn(draft, TODAY)
    results = [turn.apply(name, args) for name, args in steps]
    return turn.finish(), results


def titles(draft):
    return [b["title"] for b in draft["bots"]]


def first_chart(about, signals):
    """The chart the first turn lays out for a company with these signals, and BotOps's line about it."""
    draft = C.blank()
    draft["company"].update(about=about, signals=signals)
    turn = C.Turn(draft, TODAY)
    return turn.finish(), turn.started


def depts(draft):
    counts = {}
    for bot in draft["bots"]:
        if not bot["core"]:
            counts[bot["department"]] = counts.get(bot["department"], 0) + 1
    return counts


SPEC_COUNTS = {"everything": 72, "software": 50, "services": 37, "local": 40, "store": 38}


def test_1_each_model_has_its_spec_seat_count_and_every_seat_names_a_template_or_a_seat_to_build():
    """Org chart models spec test 1, against the data files."""
    real = {role["slug"] for role in C.ROLES}
    open_seats = {seat["slug"] for seat in C.SEATS}
    assert not open_seats, "all five seats the first models placed now have templates"
    assert {"controller", "equity-manager", "fundraising-manager", "scheduling-bookings", "quality-inspections"} <= real
    assert not open_seats & real, "a seat to build that has a template is a real seat"
    for key, count in SPEC_COUNTS.items():
        model = C.MODELS[key]
        assert model["model"] == key and [row["template"] for row in model["core"]] == ["coo", "botops"]
        seats = len(model["core"]) + len(model["seats"])
        assert abs(seats - count) <= 2, (key, seats)
        for row in model["seats"]:
            assert row["template"] in real | open_seats, (key, row)
            assert row["team"] in D.TEAMS and (row["team"] == A.BY_SLUG[row["template"]]["department"]
                                               or (key, row["template"]) == ("store", "pricing")), (key, row)
        titles = [row.get("title") or A.BY_SLUG[row["template"]]["name"] for row in model["seats"]]
        assert len(set(titles)) == len(titles), key
        # The laid-out model is exactly its file: built-ins first, then each seat once.
        draft = C.blank()
        A.model_use(draft, {"model": key}, TODAY, [])
        draft = D.check(draft)
        assert len(draft["bots"]) == seats and [b["title"] for b in draft["bots"][2:]] == titles
    for key in ("services", "local", "store"):
        assert not {row["team"] for row in C.MODELS[key]["seats"]} & {"product", "engineering"}, key


def test_the_first_chart_comes_from_the_model_the_company_s_signals_pick():
    for signals, key in ((["sells_software", "sells_to_businesses"], "software"), (["sells_services"], "services"),
                         (["onsite_jobs", "sells_services", "sells_to_consumers"], "local"),
                         (["sells_products", "sells_to_consumers"], "store"), ([], "everything"),
                         (["sells_to_businesses", "small_team"], "everything")):
        assert A.pick_model(signals) == key, signals
    draft, line = first_chart("We run an agency.", ["sells_services"])
    model = {row.get("title") or A.BY_SLUG[row["template"]]["name"] for row in C.MODELS["services"]["seats"]}
    assert model <= set(titles(draft)), "nothing the model has is dropped for a missing signal"
    assert {b["reason"] for b in draft["bots"] if b["title"] in model} == {"Part of the Services firm model."}
    # Services has no Support, Legal or Leadership head of its own: Everything's are added. No Product or Engineering.
    added = [b["title"] for b in draft["bots"] if b["reason"] == "Every chart has a head for each department."]
    assert added == ["Head of Customer Support", "General Counsel"]
    assert line == ("This looks like a services firm, so we started from the Services firm model and added the "
                    "Head of Customer Support and General Counsel.")
    assert not {"product", "engineering"} & set(depts(draft))
    assert "undo" not in draft, "the model is where the chart starts, not a change to undo"
    # Finance reporting lines: the Controller under the Head of Finance, the books under the Controller.
    by = {b["title"]: b for b in draft["bots"]}
    assert D.manager_of(draft, by["Controller"]) == by["Head of Finance"]["id"]
    assert D.manager_of(draft, by["Billing Specialist"]) == by["Controller"]["id"]
    assert D.manager_of(draft, by["FP&A Analyst"]) == by["Head of Finance"]["id"]
    # A chart with bots on it is never seeded again, even after the visitor took most of them off.
    trimmed = {**draft, "bots": draft["bots"][:3]}
    assert len(C.Turn(trimmed, TODAY).finish()["bots"]) == 3


def test_a_signal_about_how_the_company_works_adds_the_roles_that_do_that_work():
    draft, line = first_chart("An agency; clients email our support inbox.", ["sells_services", "has_support_inbox"])
    matched = [b["title"] for b in draft["bots"] if b["reason"] == "Fits what you said about how the company works."]
    assert "Support Agent" in matched and "Returns and Refunds Specialist" in matched
    assert "Support Agent" in line
    # A who-we-sell-to signal adds nothing of its own: the model already answers it.
    plain, _ = first_chart("An agency.", ["sells_services", "sells_to_businesses", "small_team"])
    assert not [b for b in plain["bots"] if b["reason"] == "Fits what you said about how the company works."]


def test_2_a_saas_for_dentists_starts_from_the_software_company_model():
    draft, line = first_chart("We build a SaaS app for dentists.", ["sells_software", "sells_to_businesses"])
    assert line.startswith("This looks like a software company, so we started from the Software company model")
    assert len(draft["bots"]) >= 50
    counts = depts(draft)
    assert all(counts.get(team, 0) >= 1 for team in ("legal", "hr", "finance", "operations")), counts
    assert counts["engineering"] == 9 and "Head of Product" in titles(draft)


def test_3_a_house_cleaning_company_starts_from_the_local_service_business_model():
    draft, line = first_chart("We're a house-cleaning company with 40 cleaners.", ["onsite_jobs", "sells_to_consumers"])
    assert line.startswith("This looks like a local service business, so we started from the Local service business model")
    counts = depts(draft)
    assert not {"product", "engineering"} & set(counts)
    assert all(counts["operations"] > n for team, n in counts.items() if team != "operations"), counts  # strictly
    assert {"Scheduling & Bookings", "Quality & Inspections", "Dispatcher", "Logistics Coordinator"} <= set(titles(draft))


def test_5_the_software_company_s_finance_holds_investor_relations_equity_and_fundraising_with_templates():
    draft, _ = first_chart("SaaS.", ["sells_software"])
    by = {b["title"]: b for b in draft["bots"]}
    for title in ("Investor Relations Manager", "Equity Manager", "Fundraising Manager"):
        assert by[title]["department"] == "finance" and by[title]["template"] not in D.OPEN_SEATS
    assert not [b for b in draft["bots"] if b["template"] in D.OPEN_SEATS]


def test_a_seat_with_no_template_goes_into_the_file_marked_no_bot_yet(monkeypatch):
    monkeypatch.setattr(D, "OPEN_SEATS", {"equity-manager"})
    draft, _ = first_chart("SaaS.", ["sells_software"])
    doc = yaml.safe_load(C.to_yaml(draft, CREATED))
    row = next(e for e in doc["employees"] if e["title"] == "Equity Manager")
    assert row["template"] == "equity-manager" and row["no_bot_yet"] is True
    assert C.to_yaml(C.from_yaml(C.to_yaml(draft, CREATED)), CREATED) == C.to_yaml(draft, CREATED)


def test_6_a_person_fills_a_seat_with_no_bot_yet():
    draft, _ = first_chart("SaaS.", ["sells_software"])
    after, results = run(draft, ("person_set", {"name": "me", "role": "Founder"}),
                         ("seat_fill", {"seat": "Equity Manager", "name": "ana"}))
    assert all(r["ok"] for r in results), results
    assert results[1]["said"] == "Ana holds the Equity Manager seat."
    ana = next(p for p in after["people"] if p["name"] == "Ana")
    seat = next(b for b in after["bots"] if b["title"] == "Equity Manager")
    assert seat["person"] == ana["id"] and ana["role"] == "Equity Manager" and ana["department"] == "finance"
    assert not ana["owner_id"]
    text = C.to_yaml(after, CREATED)
    assert "held_by: %s" % ana["id"] in text and C.to_yaml(C.from_yaml(text), CREATED) == text
    # One person holds one seat: moving Ana frees the first; a person who leaves frees theirs.
    moved, results = run(after, ("seat_fill", {"seat": "Fundraising Manager", "name": "Ana"}))
    assert results[0]["ok"] and [b["title"] for b in moved["bots"] if b.get("person")] == ["Fundraising Manager"]
    gone = D.check({**after, "people": [p for p in after["people"] if p["name"] != "Ana"]})
    assert not any(b.get("person") for b in gone["bots"])
    _, results = run(after, ("seat_fill", {"seat": "BotOps", "name": "Ana"}))
    assert not results[0]["ok"]
    # "cap table" names the Equity Manager role for bot_add.
    assert A.find_role("cap table")["slug"] == "equity-manager"
    # The built-in's old name and its new one both name it; a bare "CEO" is a person's title, not the bot.
    assert A.find_role("CEO Bot")["slug"] == A.find_role("Strategy Bot")["slug"] == "goal-manager"
    assert A.find_role("CEO") is None


def test_7_show_me_everything_switches_the_chart_to_the_everything_model():
    draft, _ = first_chart("SaaS.", ["sells_software"])
    draft, results = run(draft, ("person_set", {"name": "me", "role": "Founder"}),
                         ("seat_fill", {"seat": "Equity Manager", "name": "Ana"}),
                         ("model_use", {"model": "everything"}))
    assert all(r["ok"] for r in results), results
    assert results[2]["said"] == "The chart is now the Everything model (72 seats)."
    assert len(draft["bots"]) == 72 and depts(draft)["finance"] == 12
    assert next(b for b in draft["bots"] if b["title"] == "Equity Manager")["person"] == "ana", "a held seat stays"
    assert "undo" in draft, "switching models is a change the visitor can undo"
    for key in ("software", "services", "local", "store"):
        switched, results = run(draft, ("model_use", {"model": key}))
        held = 0 if key == "software" else 1  # the other models have no Equity Manager: Ana's seat comes along
        assert results[0]["ok"] and len(switched["bots"]) == SPEC_COUNTS[key] + held, key


def test_a_held_seat_renamed_in_another_case_survives_a_model_switch_and_keeps_its_reporting_line():
    draft, _ = first_chart("We do a bit of everything.", ["small_team"])
    seat = lambda d, template: next(b for b in d["bots"] if b["template"] == template)
    draft, results = run(draft, ("person_set", {"name": "me", "role": "Founder"}),
                         ("seat_fill", {"seat": "Controller", "name": "Ben"}),
                         ("bot_rename", {"bot": seat(draft, "controller")["id"], "title": "controller"}),
                         ("model_use", {"model": "software"}))
    assert all(r["ok"] for r in results), results
    bookkeeper = seat(draft, "billing")
    draft, results = run(draft, ("seat_fill", {"seat": bookkeeper["title"], "name": "Ana"}),
                         ("bot_rename", {"bot": bookkeeper["id"], "title": bookkeeper["title"].lower()}),
                         ("model_use", {"model": "everything"}))
    assert all(r["ok"] for r in results), results
    names = [b["title"].lower() for b in draft["bots"]]
    assert len(names) == len(set(names)), "no seat twice in another case"
    controller = seat(draft, "controller")
    assert controller["title"] == "controller" and controller["person"] == "ben"
    assert seat(draft, "billing")["person"] == "ana"
    # The held lowercase Controller still heads the seats under it, kept and new alike.
    assert seat(draft, "billing")["reports_to"] == controller["id"]
    assert seat(draft, "accounts-payable")["reports_to"] == controller["id"]


def test_filling_a_seat_on_an_empty_team_keeps_the_visitor_the_owner():
    draft, _ = first_chart("SaaS.", ["sells_software"])
    assert draft["people"] == []
    after, results = run(draft, ("seat_fill", {"seat": "Equity Manager", "name": "Ana"}))
    assert results[0]["ok"], results
    owner = D.owner(after)
    assert owner["name"] == "You" and owner["id"] != "ana"
    assert not next(p for p in after["people"] if p["name"] == "Ana")["owner_id"]
    doc = yaml.safe_load(C.to_yaml(after, CREATED))
    assert [p["name"] for p in doc["people"] if p.get("owner_id")] == ["You"]


def test_8_a_company_no_model_fits_starts_from_everything():
    draft, line = first_chart("We do a bit of everything.", ["small_team"])
    assert len(draft["bots"]) == 72
    assert line == "Nothing you said points to one kind of company, so we started from the Everything model."


def test_9_the_cap_table_belongs_to_the_equity_manager_only():
    for role in C.ROLES:
        if role["slug"] != "equity-manager":
            assert "cap table" not in role["summary"].lower() and "cap-table" not in role["tags"], role["slug"]
    equity = next(role for role in C.ROLES if role["slug"] == "equity-manager")
    assert equity["department"] == "finance" and "cap table" in equity["summary"].lower() and equity["recommend_when"]
    secretary = next(role for role in C.ROLES if role["slug"] == "corporate-secretary")["summary"]
    assert all(word in secretary for word in ("board", "minutes", "entity register"))
    card = yaml.safe_load((Path(__file__).resolve().parents[2] / "templates/catalog/corporate-secretary/card.yaml").read_text())
    assert not [line for line in card["owns"] if "cap table" in line.lower()]


def test_5_the_visitor_s_own_edits_stand_and_a_removed_bot_stays_off():
    draft = started()
    draft["bots"] = [b for b in draft["bots"] if b["template"] != "billing"]
    draft["bots"][4]["title"] = "Ada"
    draft["removed"] = [{"template": "billing", "title": "Billing Specialist"}]
    after, results = run(draft, ("bot_set_start", {"bot": "Ada", "start": "now"}))
    assert results[0]["ok"] and "Billing Specialist" not in titles(after) and "Ada" in titles(after)
    assert after["removed"] == [{"template": "billing", "title": "Billing Specialist"}]
    after, results = run(after, ("bot_remove", {"bot": "Ada"}))
    assert [r["title"] for r in after["removed"]] == ["Billing Specialist", "Ada"]
    # Asked for again, it comes back and leaves the list.
    after, results = run(after, ("bot_add", {"template": "Billing Specialist", "reason": "Invoices eat your Fridays."}))
    assert results[0]["ok"] and "Billing Specialist" in titles(after) and [r["title"] for r in after["removed"]] == ["Ada"]


def test_6_undo_takes_back_the_last_change_once_and_leaves_pending_alone():
    draft = started(pending=["sales"])
    after, results = run(draft, ("team_remove", {"team": "finance"}))
    assert results[0]["ok"] and not any(b["department"] == "finance" for b in after["bots"])
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
                ("bot_add", {"template": rng.choice(["billing", "SEO", "Recruiter"]), "reason": rng.choice(odd),
                             "title": rng.choice(odd) or "Helper"}),
                ("bot_remove", {"bot": rng.choice(bots)}), ("bot_rename", {"bot": rng.choice(bots), "title": rng.choice(odd)}),
                ("bot_set_reports_to", {"bot": rng.choice(bots), "to": rng.choice(bots + ["you"])}),
                ("bot_set_first_job", {"bot": rng.choice(bots), "job": rng.choice(odd) or "Send the invoices"}),
                ("bot_set_routine", {"bot": rng.choice(bots), "routine": rng.choice(odd) or "Daily"}),
                ("team_remove", {"team": rng.choice(D.TEAMS)}),
                ("goal_add", {"title": rng.choice(odd) or "Grow", "owner_id": rng.choice(["Sam", "you", "Jo Kim"]),
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
    said = {"title": "50 wholesale accounts by March 1", "owner_id": "Sam", "measure": "wholesale accounts",
            "unit": "accounts", "direction": "up", "target": 50, "deadline": "by March 1"}
    draft, results = run(started(), ("goal_add", said))
    goal, sam = draft["goals"][0], D.owner(draft)
    assert results[0]["ok"] and "Added Sam to the team" in results[0]["said"]
    assert {key: goal[key] for key in ("measure", "unit", "direction", "target", "baseline", "deadline")} == {
        "measure": "wholesale accounts", "unit": "accounts", "direction": "up", "target": 50, "baseline": None,
        "deadline": "2027-03-01"}
    assert [p["name"] for p in draft["people"]] == ["You", "Sam"] and sam["name"] == "You"
    assert goal["owner_id"] == next(p["id"] for p in draft["people"] if p["name"] == "Sam")
    goals = yaml.safe_load(C.to_yaml(draft, CREATED))["goals"]
    assert goals[0]["owner_id"] == "sam" and goals[0]["deadline"] == dt.date(2027, 3, 1) and goals[0]["target"] == 50
    # "the next March 1": on March 2 that is a year on.
    assert A.deadline("March 1", dt.date(2027, 3, 2)) == "2028-03-01" == A.deadline("1 March", dt.date(2027, 3, 1))
    assert A.deadline("2027-06-30", TODAY) == A.deadline("end of June", TODAY) == "2027-06-30"
    for past in ("2026-10-01", "2026-02-30", "someday", "in 40 years"):
        with pytest.raises(A.Refusal):
            A.deadline(past, TODAY)


def test_19_a_goal_without_a_number_is_saved_with_its_measure_not_set_yet():
    draft, results = run(started(), ("goal_add", {"title": "happier customers"}))
    goal = draft["goals"][0]
    assert results[0]["ok"] and "not set yet" in results[0]["said"] and goal["measure"] == "" and goal["owner_id"] == "you"
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
    direct["people"] = [{"id": "maya", "name": "Maya", "role": "Key " + OPENAI, "department": None, "owner": True}]
    assert OPENAI not in C.to_yaml(direct, CREATED)
    assert C.apply_action(started(), "step_go", {"step": "goals"}, TODAY)[1]["keys_removed"] is False
    # The words of every role, and an ordinary sentence about tokens, are left alone.
    for role in C.ROLES:
        for field in ("name", "summary", "when", "first_routine"):
            assert C.scrub(role[field]) == (role[field], False), (role["slug"], field)
    assert C.scrub("The token is optional; the api key is set on your install.")[1] is False



def test_10_numbered_ids_from_titles_survive_a_turn_the_file_and_the_import():
    """A long id made from a numbered title is a name, not a key; real key shapes beside it still go."""
    titles = ["Accounts Receivable Specialist 2", "Senior Customer Success Manager 12", "B2B Enterprise Account Executive 3",
              "2nd Line Customer Support Specialist 4"]
    templates = ["ar-followup", "customer-success", "account-manager", "customer-success"]
    turn = C.Turn(started(), TODAY)
    turn.apply("company_set", {"name": "Acme Collections"})
    for title, template in zip(titles, templates):
        turn.apply("bot_add", {"template": template, "title": title, "reason": "Our chart has " + title})
    out = turn.finish()
    ids = [b["id"] for b in out["bots"] if b["title"] in titles]
    assert "accounts-receivable-specialist-2" in ids and len(ids) == 4 and turn.keys_removed is False
    back = starter.load(C.to_yaml(out, CREATED))
    assert [b["id"] for b in back["bots"] if b["title"] in titles] == ids
    for key in ("3f9a2b7c-1d4e-4a5b-9c8d-7e6f5a4b3c2d", "k3j-9x2-q8p-m4z-w1r-t6y-b7n-c5v-h2g",
                "team-k3j-9x2-q8p-m4z-w1r-t6y-b7n-c5v", "qwe-831-zxc-476-rty-920-uio-153-pas",
                "x7Gk2pQ9vL4mN8rT1wY6zB3cD5fH0jKs",
                # word-shaped beside a key: mixed case, or one long mixed run after a word
                "Accounts-Receivable-Specialist-2-Northwest-Zq9", "billing-x7gk2pq9vl4mn8rt1wy6zb3cd5fh"):
        assert C.scrub("id " + key) == ("id [removed]", True), key

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


def test_10_a_password_in_json_inside_a_json_string_goes_whole():
    # The page can send JSON text in a field, so a pasted credential object arrives encoded twice. Synthetic values.
    sent = json.dumps({"about": json.dumps({"password": 'Qz"x\\ Kumquat', "name": "Ann"})})
    gone = json.dumps({"about": json.dumps({"password": "[removed]", "name": "Ann"})})
    assert C.scrub(sent) == (gone, True) and C.scrub(gone) == (gone, False)
    draft = started()
    draft["company"]["about"] = sent
    turn = C.Turn(draft, TODAY)
    assert turn.keys_removed and turn.finish()["company"]["about"] == gone
    assert "Kumquat" not in C.to_yaml(draft, CREATED)


# A label at the end of a longer name, as an env file writes it: an explicit = or : and the whole value goes.
PREFIXED = ["DB_PASSWORD", "STRIPE_API_KEY", "GITHUB_TOKEN", "api_token", "MY_APP_CLIENT_SECRET"]
ASSIGNED = ["{l}={v}", "{l}: {v}", "export {l}={v}", '{l}="{v}"', "{l}='{v}'", '{{"{l}": "{v}", "name": "Ann"}}',
            "{l}={v}\nNAME=Ann"]


@pytest.mark.parametrize("label", PREFIXED)
@pytest.mark.parametrize("value", ["Q", "Qz", "5", "Qz\"x"])
@pytest.mark.parametrize("form", ASSIGNED)
def test_10_a_label_ending_a_longer_name_loses_its_value(label, value, form):
    if '"{v}"' in form:
        value = value.replace('"', '\\"')
    text = "Set " + form.format(l=label, v=value) + " then go"
    out, hit = C.scrub(text)
    assert hit and out == "Set " + form.format(l=label, v="[removed]") + " then go"
    assert C.scrub(out) == (out, False)


@pytest.mark.parametrize("text", [
    "PASSWORD_FILE=/run/pw", "TOKEN_COUNT=5", "MAX_TOKENS=5", "x_api_keys=abc", "DB_PASSWORD is set on the install",
    "token: 5 per user", "Token: 120 a day", "the api_key is optional",
])
def test_10_names_that_only_contain_a_label_are_left_alone(text):
    assert C.scrub(text) == (text, False)


# 32 or more letters and digits in a row are key-shaped however regular they look; HQ's copy of the scrubber has the
# same rule. Invented strings, built at run time, each too regular for the randomness check alone.
REGULAR = ["a1" * 16, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA7", "key" + "0" * 29, "Ab12" * 10]


@pytest.mark.parametrize("value", REGULAR)
def test_10_a_long_run_of_letters_and_digits_is_removed_however_regular(value):
    assert C.scrub("Use " + value + " now") == ("Use [removed] now", True)


@pytest.mark.parametrize("value", [REGULAR[0] + "-", REGULAR[0] + "_", REGULAR[0] + "=", "x_" + REGULAR[0] + "=="])
def test_10_a_long_run_inside_a_longer_token_is_removed(value):
    out, cut = C.scrub("Use " + value + " now")
    assert cut and REGULAR[0] not in out and "[removed]" in out


@pytest.mark.parametrize("text", [
    "aaaaaaaa-0000-aaaa-0000-aaaaaaaaaaaa",                  # a UUID: dashes, so not a letters-and-digits run
    "a1" * 15 + "a",                                         # 31 letters and digits
    "Supercalifragilisticexpialidociousness",                # letters only
    "12345678901234567890123456789012345",                   # digits only
])
def test_10_a_uuid_and_runs_short_of_the_shape_stay(text):
    assert C.scrub(text) == (text, False)


def test_10_an_env_line_is_cut_in_the_turn_and_the_file():
    draft = started()
    draft["company"]["about"] = "Our .env: DB_PASSWORD=Qz STRIPE_API_KEY='ab' GITHUB_TOKEN: 5 NAME=Ann"
    gone = "Our .env: DB_PASSWORD=[removed] STRIPE_API_KEY='[removed]' GITHUB_TOKEN: [removed] NAME=Ann"
    turn = C.Turn(draft, TODAY)
    assert turn.keys_removed and turn.finish()["company"]["about"] == gone
    assert yaml.safe_load(C.to_yaml(draft, CREATED))["company"]["about"] == gone


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
    direct["people"] = [{"id": "maya", "name": "Maya", "role": "secret=Qz", "department": None, "owner": True}]
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
    ("nonsense", {}), ("bot_add", {"template": "billing"}), ("bot_add", {"template": "billing", "reason": ""}),
    ("bot_add", {"template": "billing", "reason": "x", "colour": "red"}),
    ("bot_add", {"template": "billing", "reason": "x", "title": "Builder"}),
    ("bot_add", {"template": "botops", "reason": "a second one"}),
    ("bot_add", {"template": "billing", "reason": "x" * 301}), ("bot_add", {"template": 7, "reason": "x"}),
    ("bot_remove", {"bot": "botops"}), ("bot_remove", {"bot": "ghost"}), ("bot_rename", {"bot": "builder", "title": "Reviewer 1"}),
    ("bot_rename", {"bot": "builder", "title": " "}), ("bot_set_reports_to", {"bot": "engineering-lead", "to": "builder"}),
    ("bot_set_reports_to", {"bot": "builder", "to": "builder"}), ("bot_set_reports_to", {"bot": "builder", "to": "Dispatcher"}),
    ("bot_set_reports_to", {"bot": "builder", "to": "Nobody Here"}), ("bot_set_start", {"bot": "builder", "start": "soon"}),
    ("bot_set_start", {"bot": "botops", "start": "later"}), ("bot_set_first_job", {"bot": "coo", "job": "x"}),
    ("bot_set_routine", {"bot": "builder", "routine": "x" * 201}), ("team_remove", {"team": "core"}),
    ("team_remove", {"team": "kitchen"}), ("goal_add", {"title": ""}), ("goal_add", {"title": "Grow", "target": 5}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "up", "target": 5,
                  "deadline": "2020-01-01"}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "up", "target": float("inf"),
                  "deadline": "June"}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "up", "target": True, "deadline": "June"}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "range", "min": 5, "max": 1}),
    ("goal_add", {"title": "Grow", "measure": "sales", "unit": "$", "direction": "sideways", "target": 1, "deadline": "June"}),
    ("goal_add", {"title": "Grow", "owner": "Dispatcher"}), ("goal_edit", {"goal": "ghost", "title": "x"}),
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
                {**good, "goals": [{"id": "g", "title": "x", "owner_id": "you", "measure": "m", "unit": "", "direction": "up",
                                    "target": float("nan"), "deadline": None}]},
                {**good, "goals": [{"id": "g", "title": "x", "owner_id": "you", "measure": "", "target": "50", "due_at": "x"}]},
                {**good, "bots": good["bots"] * 2}, {**good, "company": {**good["company"], "about": "x" * 300001}},
                {**good, "bots": good["bots"][:2] + [{**good["bots"][2], "template": "wizard"}]}):
        with pytest.raises(C.DraftError):
            C.Turn(bad, TODAY)
    for bad in ("", "version: 0.1\n", "- a\n", "version: 0.2\nemployees:\n  - template: wizard\n", "{"):
        with pytest.raises(C.DraftError):
            C.from_yaml(bad)


def test_pending_rides_along_outside_the_file_and_the_undo_and_lets_go_of_a_team_that_is_gone():
    draft = started(pending=["sales", "marketing", "legal"])
    draft["bots"] = [b for b in draft["bots"] if b["department"] != "legal"]
    after, _ = run(draft, ("bot_rename", {"bot": "builder", "title": "Ada"}))
    assert after["pending"] == ["sales", "marketing"], "no legal team on the chart"
    assert "pending" not in after["undo"] and "pending" not in C.to_yaml(after, CREATED)
    after, _ = run(after, ("team_remove", {"team": "sales"}))
    assert after["pending"] == ["marketing"]
    assert C.from_yaml(C.to_yaml(after, CREATED)).get("pending") is None


def test_one_bot_has_the_first_job_and_it_starts_now():
    draft, results = run(started(), ("bot_set_first_job", {"bot": "billing", "job": "Chase last month's invoices"}),
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


def test_a_bot_s_team_is_written_only_when_set_and_reads_back_as_it_was_written():
    """The name of a bot's group rides in the file as `team`, for an employee and for an agent the visitor runs."""
    draft, _ = run(started(), ("agent_set", {"runtime": "codex", "job": "Reviews pull requests"}))
    staff = next(b for b in draft["bots"] if not b["core"] and not b["existing"])
    agent = next(b for b in draft["bots"] if b["existing"])
    staff["team"], agent["team"] = "Lab network", "Biopharma accounts"
    text = C.to_yaml(draft, CREATED)
    doc = yaml.safe_load(text)
    assert [e["team"] for e in doc["employees"] if "team" in e] == ["Lab network"]
    assert [e["team"] for e in doc["existing_agents"]] == ["Biopharma accounts"]
    assert C.to_yaml(C.from_yaml(text), CREATED) == text
    assert {b["title"]: b["team"] for b in C.from_yaml(text)["bots"] if "team" in b} == {
        staff["title"]: "Lab network", agent["title"]: "Biopharma accounts"}
    # A bot with no team has no such line, and the draft holds none.
    assert all("team" not in e for e in yaml.safe_load(C.to_yaml(started(), CREATED))["employees"])


def test_a_team_is_trimmed_clipped_and_scrubbed_on_read_and_a_blank_one_is_no_team():
    doc = yaml.safe_load(C.to_yaml(started(), CREATED))
    first, blank, secret, wrong = doc["employees"][:4]
    first["team"], blank["team"], secret["team"] = "  " + "L" * 50 + "  ", "   ", GITHUB
    bots = {b["id"]: b for b in C.from_yaml(yaml.safe_dump(doc, sort_keys=False))["bots"]}
    assert bots[first["id"]]["team"] == "L" * 40
    assert "team" not in bots[blank["id"]]
    assert bots[secret["id"]]["team"] == "[removed]"
    wrong["team"] = ["Lab", "network"]
    with pytest.raises(D.DraftError, match="team"):
        C.from_yaml(yaml.safe_dump(doc, sort_keys=False))
    draft = started()
    draft["bots"][0]["team"] = "x" * 41
    with pytest.raises(D.DraftError, match="team"):
        C.to_yaml(draft, CREATED)


def test_a_team_rides_in_the_starter_plan_as_text_and_packed():
    draft = started()
    draft["company"]["name"] = "Acme Build"
    draft["bots"] = draft["bots"][:4]           # a small chart: the packed plan must fit the 7 KB environment limit
    next(b for b in draft["bots"] if not b["core"])["team"] = "Lab network"
    text = C.to_yaml(draft, CREATED)
    saved = starter.load(text)
    assert [b["team"] for b in saved["bots"] if "team" in b] == ["Lab network"]
    assert starter.unpack(starter.pack(text)) == saved


def test_a_reports_to_whose_target_left_the_chart_falls_back_to_the_line_the_file_draws():
    draft, _ = run(started(), ("person_set", {"name": "Maya", "role": "CEO"}), ("person_set", {"name": "Jo", "role": "Ops"}),
                   ("bot_set_reports_to", {"bot": "Builder", "to": "Jo"}))
    assert next(b for b in draft["bots"] if b["title"] == "Builder")["reports_to"] == "jo"
    # The visitor took Jo off the team on the page; an invented id is treated the same way.
    sent = {**draft, "people": [p for p in draft["people"] if p["name"] != "Jo"]}
    next(b for b in sent["bots"] if b["title"] == "Reviewer 1")["reports_to"] = "nobody"
    out = C.Turn(sent, TODAY).finish()
    assert not any(b.get("reports_to") for b in out["bots"] if b["title"] in ("Builder", "Reviewer 1"))
    doc = yaml.safe_load(C.to_yaml(out, CREATED))
    assert {e["title"]: e["reports_to"] for e in doc["employees"]}["Builder"] == "engineering-lead"
    assert C.to_yaml(C.from_yaml(C.to_yaml(out, CREATED)), CREATED) == C.to_yaml(out, CREATED)



def test_the_copy_tico_hq_serves_runs_the_same_engine(tmp_path):
    """Tico HQ (ticoteam/tico-services) serves tico.team/setup from `scripts/build_catalog_json.py --out DIR`'s
    company_file/: imported from there, by itself, it gives the same file, actions, company models and roles."""
    root = Path(__file__).resolve().parents[2]
    made = subprocess.run([sys.executable, str(root / "scripts" / "build_catalog_json.py"), "--out", str(tmp_path)],
                          capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    spec = importlib.util.spec_from_file_location("exported_company_file", tmp_path / "company_file" / "__init__.py",
                                                  submodule_search_locations=[str(tmp_path / "company_file")])
    H = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = H
    try:
        spec.loader.exec_module(H)
        draft = started()
        assert H.to_yaml(draft, CREATED) == C.to_yaml(draft, CREATED)
        assert H.SPEC == C.SPEC and H.MODELS == C.MODELS and H.SEATS == C.SEATS and H.ROLES == C.ROLES
        assert Path(H.draft.__file__).parent == tmp_path / "company_file"
        removed = H.scrub("key sk-" + "a" * 40)
        assert removed == C.scrub("key sk-" + "a" * 40) and removed[1] is True
    finally:
        for name in [n for n in sys.modules if n == spec.name or n.startswith(spec.name + ".")]:
            del sys.modules[name]


# ------------------------------------------------------------------------------- seat guard (Org chart models 2/7)
def tag(i):
    """A title suffix without digits: a title like "Account Manager 7" slugs to an id the key scrubber can cut."""
    return "".join("ABCDEFGHIJKLMNOPQRSTUVWXYZ"[d] for d in (i // 26, i % 26))


def chart(bots, people=1):
    """The starter chart grown (or cut) to `bots` bots and `people` people, through the chat's own actions."""
    draft = started()
    draft["bots"] = draft["bots"][:bots]
    roles = [r for r in C.ROLES if not r["core"]]                          # built-ins stay one each
    turn = C.Turn(draft, TODAY)
    for i in range(bots - len(turn.draft["bots"])):
        role = roles[i % len(roles)]
        result = turn.apply("bot_add", {"template": role["slug"], "title": "%s %s" % (role["name"], tag(i)),
                                        "reason": "The owner asked for one more."})
        assert result["ok"], result
    for i in range(people - len(turn.draft["people"])):
        assert turn.apply("person_set", {"name": "Person %s" % tag(i), "role": "Account Manager"})["ok"]
    draft = turn.finish()
    assert (len(draft["bots"]), len(draft["people"])) == (bots, people)
    return draft


@pytest.mark.parametrize("seat", [61, 100, 300])
def test_4_the_chat_adds_a_61st_100th_and_300th_bot_with_no_limit_message(seat):
    draft, (result,) = run(chart(seat - 1), ("bot_add", {"template": "billing", "title": "Extra Billing Specialist",
                                                          "reason": "More invoices."}))
    assert result["ok"] and len(draft["bots"]) == seat and "Extra Billing Specialist" in titles(draft)
    assert not any(word in result["said"].lower() for word in ("limit", "most", "plan can hold"))
    assert C.Turn(draft, TODAY).finish()["bots"] == draft["bots"]          # and the page can send it back


def test_the_301st_seat_is_refused_with_a_plain_error_and_a_301_bot_draft_is_refused():
    full = chart(300)
    for name, args in (("bot_add", {"template": "billing", "title": "Extra Billing Specialist", "reason": "x"}),
                       ("agent_set", {"runtime": "codex", "job": "Writes our code."})):
        turn = C.Turn(full, TODAY)
        result = turn.apply(name, args)
        assert result == {"ok": False, "refused": "A chart can have at most 300 bots."} and len(turn.draft["bots"]) == 300
    extra = {**full["bots"][-1], "id": "one-too-many", "title": "One Too Many"}
    with pytest.raises(C.DraftError, match="bots has more than 300 entries"):
        C.Turn({**full, "bots": full["bots"] + [extra]}, TODAY)


def test_a_41st_person_joins_and_the_301st_is_refused_plainly():
    draft, (result,) = run(chart(24, people=40), ("person_set", {"name": "Ana Lima", "role": "Head of sales"}))
    assert result["ok"] and len(draft["people"]) == 41 and "limit" not in result["said"].lower()
    full = chart(24, people=300)
    turn = C.Turn(full, TODAY)
    assert turn.apply("person_set", {"name": "Ana Lima"}) == {"ok": False, "refused": "A chart can have at most 300 people."}
    extra = {**full["people"][-1], "id": "one-too-many", "name": "One Too Many"}
    with pytest.raises(C.DraftError, match="people has more than 300 entries"):
        C.Turn({**full, "people": full["people"] + [extra]}, TODAY)


def test_a_full_300_seat_chart_with_its_undo_and_chat_fits_the_draft_and_the_company_file():
    draft, _ = run(chart(300, people=300), ("bot_rename", {"bot": "billing", "title": "Books"}))
    draft["chat"] = [{"who": "me" if i % 2 else "bot", "text": "x" * D.LIMITS["chat_text"]} for i in range(D.LIMITS["chat"])]
    assert len(draft["undo"]["bots"]) == 300
    assert C.Turn(draft, TODAY).finish()["bots"] == draft["bots"]
    text = C.to_yaml(draft, CREATED)
    back = C.from_yaml(text)
    assert len(back["bots"]) == 300 and len(back["people"]) == 300
    # The starter import takes the owner and the bots: all 300 of them.
    owner_only = {**draft, "company": {**draft["company"], "name": "Acme"}, "people": [p for p in draft["people"] if p["owner_id"]]}
    assert len(starter.load(C.to_yaml(owner_only, CREATED))["bots"]) == 300


def test_the_chat_adds_goals_up_to_the_goal_limit_and_refuses_the_next_in_plain_words():
    """The chat's own goals stay inside the limit the file check uses (Dev 449): the 325th lands, the 326th is refused,
    and the full plan passes the check and its file reads back."""
    draft = started()
    draft["goals"] = [{"id": "goal-%d" % i, "title": "Goal %d" % i, "owner_id": "you", "measure": "", "unit": "",
                       "direction": "up", "target": None, "baseline": None, "deadline": None}
                      for i in range(C.LIMITS["goals"] - 1)]
    draft, (last, over) = run(draft, ("goal_add", {"title": "The last goal"}), ("goal_add", {"title": "One too many"}))
    assert C.LIMITS["goals"] == 325 and last["ok"] and len(draft["goals"]) == 325
    assert not over["ok"] and over["refused"] == "The plan has the most goals it can hold (325)."
    assert C.Turn(draft, TODAY).finish()["goals"] == draft["goals"]
    assert len(C.from_yaml(C.to_yaml(draft, CREATED))["goals"]) == 325


# ------------------------------------------------------------------ goals from POST /goals keep their layer (Dev 321 parity)
def layered_goals():
    """A draft with a company goal, a group goal serving it and a role goal serving that, as the page keeps them."""
    draft = started()
    owner = D.owner(draft)["id"] if D.owner(draft) else "you"
    aim = {"measure": "things", "unit": "things", "direction": "up", "target": 4, "baseline": None, "deadline": "2027-01-15"}
    dept = "marketing"
    draft["goals"] = [
        {"id": "goal-1", "title": "Sign 4 customers", "owner_id": owner, "level": "company", "parent": None, **aim},
        {"id": "goal-2", "title": "Publish 2 guides", "owner_id": owner, "level": "group", "parent": "goal-1", "dept": dept, **aim},
        {"id": "goal-3", "title": "Hand in work early", "owner_id": owner, "level": "role", "parent": "goal-2", "dept": dept, **aim}]
    return draft


def layers(draft):
    return [(g["id"], g.get("level"), g.get("parent"), g.get("department")) for g in draft["goals"]]


def test_goals_with_a_level_parent_and_dept_pass_the_check_and_keep_them():
    draft = layered_goals()
    assert layers(D.check(draft)) == layers(draft)
    kept, results = run(draft, ("goal_edit", {"goal": "goal-3", "title": "Hand in each guide early"}))
    assert results[0]["ok"] and layers(kept) == layers(draft)
    assert layers(kept["undo"]) == layers(draft)


def test_a_parent_not_among_the_goals_or_itself_is_let_go_not_refused():
    draft = layered_goals()
    draft["goals"][1]["parent"] = "goal-999"
    draft["goals"][2]["parent"] = "goal-3"
    out = D.check(draft)
    assert out["goals"][1]["parent"] is None and out["goals"][2]["parent"] is None
    assert out["goals"][1]["level"] == "group" and out["goals"][1]["department"] == draft["goals"][1]["department"]


def test_the_company_file_holds_no_level_parent_or_dept():
    text = C.to_yaml(layered_goals(), CREATED)
    assert "level:" not in text and "parent:" not in text
    assert [g["title"] for g in yaml.safe_load(text)["goals"]] == ["Sign 4 customers", "Publish 2 guides", "Hand in work early"]


@pytest.mark.parametrize("field, value", [("level", "team"), ("level", 2), ("dept", "space"), ("parent", "Not an id!"),
                                          ("kind", "role")])
def test_a_goal_with_a_bad_level_dept_or_parent_or_another_field_is_still_refused(field, value):
    draft = layered_goals()
    draft["goals"][-1][field] = value
    with pytest.raises(D.DraftError):
        D.check(draft)


# ------------------------------------------------------------------------------------- the strategy block (format 0.3)
FIXTURES = Path(__file__).parent / "fixtures" / "company_strategy"
FIXTURE_CREATED = dt.datetime(2026, 10, 25, 12, tzinfo=dt.timezone.utc)


def fixture_draft():
    return json.loads((FIXTURES / "draft.json").read_text())


def test_a_plan_with_a_strategy_is_written_as_format_0_3_exactly_as_the_setup_page_writes_it():
    """The same text the page's toYAML() writes (both repos test against this file); it reads back to the same file."""
    text = (FIXTURES / "company.yaml").read_text()
    assert C.to_yaml(fixture_draft(), FIXTURE_CREATED) == text
    back = C.from_yaml(text)
    assert C.to_yaml(back, FIXTURE_CREATED) == text
    assert back["strategy"] == D.check(fixture_draft())["strategy"]
    assert [g.get("bet") for g in back["goals"]] == ["b1", "b2"]
    assert yaml.safe_load(text)["version"] == 0.3 and "# Format 0.3." in text


def test_a_plan_without_a_strategy_is_still_format_0_2_byte_for_byte():
    draft = fixture_draft()
    del draft["strategy"]
    for goal in draft["goals"]:
        goal.pop("bet")
    text = C.to_yaml(draft, FIXTURE_CREATED)
    assert "version: 0.2\n" in text and "# Format 0.2." in text and "strategy" not in text and "bet:" not in text
    # A blank strategy (the chat touched it and cleared it) writes no block either.
    assert C.to_yaml({**draft, "strategy": C.blank_strategy()}, FIXTURE_CREATED) == text
    assert "strategy" not in C.from_yaml(text)


def test_the_starter_import_keeps_the_strategy_and_a_file_without_one_saves_as_before():
    text = (FIXTURES / "company.yaml").read_text()
    saved = starter.load(text)
    assert saved["strategy"]["bets"][0]["id"] == "b1" and saved["goals"][0]["bet"] == "b1"
    assert C.from_yaml(starter.file_text(text))["strategy"] == saved["strategy"]
    draft = fixture_draft()
    del draft["strategy"]
    assert "strategy" not in starter.load(C.to_yaml(draft, FIXTURE_CREATED))


def test_a_strategy_is_let_go_of_quietly_where_the_page_can_t_help_it_and_refused_where_it_is_wrong():
    draft = fixture_draft()
    s = draft["strategy"]
    s["market"]["segments"][1]["parent"] = "gone"            # a parent that left: the segment moves to the top
    s["market"]["segments"][0]["evidence"] = []               # a size without a source: the size goes
    s["market"]["players"][0]["segments"].append("gone")      # a player's segment that left
    draft["goals"][1]["bet"] = "b5"                           # a bet that is gone: the goal keeps going without it
    out = D.check(draft)
    segments = {g["key"]: g for g in out["strategy"]["market"]["segments"]}
    assert segments["schedules"]["parent"] is None and segments["gc-suites"]["usd"] is None
    assert out["strategy"]["market"]["players"][0]["segments"] == ["gc-suites", "schedules"]
    assert out["goals"][0]["bet"] == "b1" and "bet" not in out["goals"][1]
    # Every key inside the block is optional; what is missing comes back empty.
    assert D.check({**fixture_draft(), "strategy": {"summary": "Short."}})["strategy"] == {
        **C.blank_strategy(), "summary": "Short."}
    for path, value in ((("bets", 0, "id"), "b9"), (("bets", 1, "id"), "b1"), (("market", "segments", 1, "key"), "bim"),
                        (("market", "segments", 0, "ours"), True), (("market", "segments", 2, "method"), "guess"),
                        (("market", "players", 0, "url"), "ftp://example.com"), (("listening", "posts", 0, "tag"), "x"),
                        (("summary",), "x" * 401), (("bets", 0, "check_by"), "soon"), (("colour",), "red")):
        bad = fixture_draft()
        *parents, last = path
        target = bad["strategy"]
        for key in parents:
            target = target[key]
        target[last] = value
        with pytest.raises(C.DraftError):
            D.check(bad)


def test_the_chat_edits_the_strategy_and_the_turn_names_each_part_it_touched():
    turn = C.Turn(fixture_draft(), TODAY)
    said = [turn.apply(*step) for step in (
        ("set_strategy_text", {"field": "know", "text": "Supers text photos all day."}),
        ("set_bet", {"id": "b2", "wrong_if": "Cycles run past 60 days"}),
        ("add_bet", {"statement": "Price per crew", "why": "Crews are the unit", "rules_out": "Per-seat", "wrong_if": ""}),
        ("drop_bet", {"id": "b1"}),
        ("add_listening_request", {"text": "Builders asking about crew pricing"}),
        ("drop_listening_request", {"text": "superintendents asking for a better schedule app"}),
        ("resize_market", {"note": "Count specialty contractors too"}))]
    assert all(r["ok"] for r in said), said
    draft = turn.finish()
    s = draft["strategy"]
    assert s["sections"]["know"] == "Supers text photos all day."
    assert [b["id"] for b in s["bets"]] == ["b2", "b3", "b4"] and s["bets"][-1]["check_by"] == "2027-01-15"
    assert "bet" not in draft["goals"][0] and draft["goals"][1]["bet"] == "b2"
    assert s["listening"]["requests"] == ["Competitor changelogs: Trestle, FieldCraft", "Builders asking about crew pricing"]
    assert turn.changed == ["sections.know", "bets.b2", "bets.b4", "goals", "bets.b1", "listening.requests"]
    assert turn.market_note == "Count specialty contractors too"
    assert C.from_yaml(C.to_yaml(draft, CREATED))["strategy"] == s


@pytest.mark.parametrize("name,args", [
    ("set_strategy_text", {"field": "mission", "text": "x"}), ("set_strategy_text", {"field": "know", "text": "x" * 2001}),
    ("set_bet", {"id": "b9", "why": "x"}), ("set_bet", {"id": "b1"}), ("set_bet", {"id": "b1", "statement": ""}),
    ("add_bet", {"statement": "x", "why": "", "rules_out": ""}), ("drop_bet", {"id": "b4"}),
    ("add_listening_request", {"text": "competitor changelogs: trestle, fieldcraft"}),
    ("drop_listening_request", {"text": "nothing like it"}), ("resize_market", {"note": ""})])
def test_a_bad_strategy_action_is_refused_in_words_and_changes_nothing(name, args):
    turn = C.Turn(fixture_draft(), TODAY)
    before = copy.deepcopy(turn.draft)
    result = turn.apply(name, args)
    assert result["ok"] is False and result["refused"] and turn.draft == before and turn.changed == []


def test_the_strategy_holds_its_limits_and_says_so_plainly():
    draft = fixture_draft()
    turn = C.Turn(draft, TODAY)
    assert all(turn.apply("add_bet", {"statement": "Bet %d" % i, "why": "", "rules_out": "", "wrong_if": ""})["ok"]
               for i in range(2))
    over = turn.apply("add_bet", {"statement": "One too many", "why": "", "rules_out": "", "wrong_if": ""})
    assert over == {"ok": False, "refused": "The strategy has 5 bets, the most it holds. Drop one first."}


def test_a_full_strategy_at_every_limit_beside_a_300_seat_chart_fits_the_starter_import():
    L = D.LIMITS

    def prose(n):
        """Text at full length that the key scrubber leaves alone (a long run of letters reads as a key)."""
        return ("a word " * n)[:n]

    url = ("https://example.com" + "/a-path" * L["url"])[:L["url"]]
    strategy = {
        "summary": prose(L["strategy_line"]), "vision": prose(L["strategy_line"]),
        "sections": {key: prose(L["section"]) for key in D.SECTIONS},
        "bets": [{"id": "b%d" % i, "statement": prose(L["statement"]), "why": prose(L["bet_text"]),
                  "rules_out": prose(L["bet_text"]), "wrong_if": prose(L["bet_text"]), "check_by": "2027-01-23"}
                 for i in range(1, L["bets"] + 1)],
        "market": {"definition": {"root": prose(L["market_root"]), "in": prose(L["market_scope"]),
                                  "out": prose(L["market_scope"]), "year": 2026},
                   "segments": [{"key": "seg-%d" % i, "name": prose(L["segment_name"]), "parent": None, "usd": 10 ** 12,
                                 "method": "top_down", "arithmetic": prose(L["arithmetic"]),
                                 "evidence": [url] * L["evidence"], "ours": False, "next": False}
                                for i in range(L["segments"])],
                   "players": [{"name": prose(L["player_name"]), "url": url,
                                "segments": ["seg-%d" % i for i in range(L["segments"])]} for _ in range(L["players"])]},
        "listening": {"requests": [("%d " % i + prose(L["request"]))[:L["request"]] for i in range(L["requests"])],
                      "posts": [{"source": "web", "url": url, "title": prose(L["post_title"]), "when": "2026-10-24",
                                 "tag": "market"} for _ in range(L["posts"])]}}
    draft, _ = run(chart(300, people=300), ("bot_rename", {"bot": "billing", "title": "Books"}))
    owner_only = {**draft, "company": {**draft["company"], "name": "Acme"}, "people": [p for p in draft["people"] if p["owner_id"]],
                  "strategy": strategy}
    saved = starter.load(C.to_yaml(owner_only, CREATED))
    assert saved["strategy"] == D.check(owner_only)["strategy"] and len(saved["bots"]) == 300
    assert len(starter.canonical(saved).encode()) < starter.MAX_BYTES


@pytest.mark.parametrize("url", ["https://alice:pw@example.com/x", "https://alice@example.com", "https://[",
                                 "https://example.com:abc/", "https://[::1"])
def test_an_address_with_a_user_name_or_one_that_can_t_be_read_is_refused_plainly(url):
    for path in (("market", "players", 0, "url"), ("listening", "posts", 0, "url"), ("market", "segments", 0, "evidence")):
        bad = fixture_draft()
        *parents, last = path
        target = bad["strategy"]
        for key in parents:
            target = target[key]
        target[last] = [url] if last == "evidence" else url
        with pytest.raises(C.DraftError, match="no user name or password"):
            D.check(bad)


def test_a_bet_added_to_a_plan_with_no_strategy_starts_the_block_and_undo_takes_it_back():
    draft, (added,) = run(started(), ("add_bet", {"statement": "Own the phone", "why": "", "rules_out": "", "wrong_if": ""}))
    assert added["ok"] and draft["strategy"]["bets"][0]["id"] == "b1" and "strategy" not in draft["undo"]
    assert "version: 0.3" in C.to_yaml(draft, CREATED)
    undone, _ = run(draft, ("undo", {}))
    assert "strategy" not in undone


def test_a_saved_draft_with_a_retired_template_loses_that_seat_and_keeps_the_rest():
    draft = started()
    controller = next(b for b in draft["bots"] if b["template"] == "controller")
    old = {**copy.deepcopy(controller), "id": "books", "template": "bookkeeping", "title": "Acme Books", "lead": False,
           "reports_to": controller["id"]}
    under = {**copy.deepcopy(controller), "id": "books-helper", "template": "billing", "title": "Acme Billing",
             "lead": False, "reports_to": "books"}
    built = {**copy.deepcopy(controller), "id": "old-books", "template": "bookkeeping", "title": "Our Books",
             "lead": False, "existing": True, "runtime": "hermes"}
    draft["bots"] = [b for b in draft["bots"] if b["template"] != "billing"] + [old, under, built]
    out = D.check(draft)
    by_id = {b["id"]: b for b in out["bots"]}
    assert "books" not in by_id and "old-books" in by_id
    assert "reports_to" not in by_id["books-helper"]
