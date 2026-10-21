"""A picker export creates the selected team through normal owner-only onboarding."""
import copy
import re

import pytest
from fastapi.testclient import TestClient

from backend import company_file as C
from backend.app import create_app
from backend.auth import Identity
from backend.company_file import starter
from backend.store import H, Problem, encode
from backend.tests.test_sample_company import OWNER_EMAIL, PEOPLE, SEED, environment, machine, rows, settings_for, signed_in


def company_text(reverse_builtins=False, through_new_parent=False):
    draft = C.Turn(C.blank()).finish()
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in {"coo", "botops", "finance-lead", "bookkeeping"}]
    for bot in draft["bots"]:
        bot["start"] = bot["template"] != "bookkeeping"
        if bot["template"] == "bookkeeping":
            bot["title"] = "Acme Bookkeeper"
            bot["routine"] = "Review the books each week"
        if reverse_builtins and bot["template"] == "coo":
            bot["reports_to"] = "botops"
        if through_new_parent and bot["template"] in ("coo", "finance-lead"):
            bot["reports_to"] = "finance-lead" if bot["template"] == "coo" else "botops"
    return C.to_yaml(draft) + "models:\n  source: gateway\n  by_department:\n    finance: mid\n"


def test_import_preserves_selected_team_builtins_and_later_choice_across_retry_and_runner_enrollment(environment):
    api = environment()
    body = {"company_yaml": company_text()}
    preview = api.post("/api/v2/onboarding/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    assert not rows(api, "SELECT slug FROM bots WHERE slug='bookkeeping'")
    body["fingerprint"] = preview.json()["fingerprint"]
    done = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert done.status_code == 200, done.text
    assert done.json()["names"]["company_name"] == "Acme Finance"
    bots = {r["slug"]: r for r in rows(api, "SELECT slug,display_name,state FROM bots")}
    assert bots["bookkeeping"]["display_name"] == "Acme Bookkeeper"
    assert bots["bookkeeping"]["state"] == "paused"
    assert "builder" not in bots and "head-of-product" not in bots
    parents = {r["bot"]: r["reports_to"] for r in rows(api, "SELECT bot,reports_to FROM bot_config")}
    assert {bot["id"]: parents[bot["id"]] for bot in preview.json()["employees"]} == {
        bot["id"]: bot["reports_to"] for bot in preview.json()["employees"]}
    assert parents["bookkeeping"] == "finance-lead"
    assert parents["finance-lead"] == "human:owner"
    templates = rows(api, "SELECT json_extract(config_json,'$.template') AS template FROM bot_config")
    assert sum(r["template"] == "assistant" for r in templates) == 1
    assert sum(r["template"] == "botops" for r in templates) == 1
    assert "model_choices" in done.json()["company_import"]["deferred"]
    assert "routines" in done.json()["company_import"]["deferred"]
    saved = rows(api, "SELECT value_json FROM registry_metadata WHERE key='onboarding.company_file'")[0]
    assert '"finance":"mid"' in saved["value_json"]
    before = rows(api, "SELECT id FROM tasks ORDER BY id")
    again = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert again.status_code == 200 and rows(api, "SELECT id FROM tasks ORDER BY id") == before
    machine(api)
    assert rows(api, "SELECT state FROM bots WHERE slug='bookkeeping'")[0]["state"] == "paused"


def test_import_requires_owner_and_matching_preview_and_refuses_replacing_a_configured_company(environment):
    api = environment()
    body = {"company_yaml": company_text(), "fingerprint": "0" * 64}
    refused = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert refused.status_code == 409
    assert not rows(api, "SELECT slug FROM bots WHERE slug='bookkeeping'")
    assert api.post("/api/v2/onboarding/company/import", json=body).status_code in (401, 403)
    api.app.state.store.settings.test_identities["helper-test"] = Identity("human:helper", "human",
                                                                          email="helper@sample.example")
    assert api.post("/api/v2/onboarding/company/preview", json={"company_yaml": body["company_yaml"]},
                    headers=signed_in() | {"Authorization": "Bearer helper-test"}).status_code == 403
    assert api.post("/api/v2/onboarding/company/import", json=body,
                    headers=signed_in() | {"Authorization": "Bearer helper-test"}).status_code == 403
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": body["company_yaml"]},
                       headers=signed_in()).json()
    body["fingerprint"] = preview["fingerprint"]
    assert api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in()).status_code == 200
    changed = copy.copy(body)
    changed["company_yaml"] = changed["company_yaml"].replace("Acme Finance", "Another Company")
    changed["fingerprint"] = api.post("/api/v2/onboarding/company/preview",
                                      json={"company_yaml": changed["company_yaml"]}, headers=signed_in()).json()["fingerprint"]
    assert api.post("/api/v2/onboarding/company/import", json=changed, headers=signed_in()).status_code == 409
    assert api.get("/api/v2/config", headers=signed_in()).json()["company_name"] == "Acme Finance"


def test_import_never_replaces_an_existing_paused_employee(environment):
    api = environment()
    config = {"name": "bookkeeping", "status": "paused", "instructions": "Keep the existing employee unchanged"}
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {**SEED, "bookkeeping": config}, PEOPLE)
        c.execute("INSERT INTO bot_config(bot,config_json,operator,repo) VALUES(?,?,?,?)",
                  ("bookkeeping", encode(config), "owner", "emp-bookkeeping"))
    original = rows(api, "SELECT config_json FROM bot_config WHERE bot='bookkeeping'")
    body = {"company_yaml": company_text()}
    body["fingerprint"] = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in()).json()["fingerprint"]
    assert api.post("/api/v2/setup/company/import", json=body, headers=signed_in()).status_code == 409
    assert rows(api, "SELECT config_json FROM bot_config WHERE bot='bookkeeping'") == original


@pytest.mark.parametrize("through_new_parent", [False, True], ids=["reverse-builtins", "new-parent-chain"])
@pytest.mark.parametrize("bootstrap", [False, True], ids=["owner-api", "installer-bootstrap"])
def test_install_bootstrap_uses_selected_team_once_and_never_falls_back_to_sample(tmp_path, through_new_parent, bootstrap):
    from clients.environments import seed_registry
    seed_registry(tmp_path, "Owner", OWNER_EMAIL,
                  {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"})
    text = company_text(reverse_builtins=not through_new_parent, through_new_parent=through_new_parent)
    settings = settings_for(tmp_path)
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()
    with TestClient(app) as api:
        preview = api.post("/api/v2/setup/company/preview", json={"company_yaml": text}, headers=signed_in()).json()
        if not bootstrap:
            applied = api.post("/api/v2/setup/company/import", json={"company_yaml": text, "fingerprint": preview["fingerprint"]},
                               headers=signed_in())
            assert applied.status_code == 200, applied.text
    settings.company_file_data, settings.sample_company = starter.pack(text), True
    with TestClient(create_app(settings)) as api:
        assert rows(api, "SELECT display_name FROM bots WHERE slug='bookkeeping'")[0]["display_name"] == "Acme Bookkeeper"
        assert not rows(api, "SELECT slug FROM bots WHERE slug IN ('builder','head-of-product')")
        parents = {r["bot"]: r["reports_to"] for r in rows(api, "SELECT bot,reports_to FROM bot_config")}
        assert {b["id"]: parents[b["id"]] for b in preview["employees"]} == {
            b["id"]: b["reports_to"] for b in preview["employees"]}
        record = api.get("/api/v2/setup", headers=signed_in()).json()
        assert {slug: choice["reports_to"] for slug, choice in record["selected"].items()} == {
            b["id"]: b["reports_to"] for b in preview["employees"]}
        machine(api)
        assert rows(api, "SELECT state FROM bots WHERE slug='bookkeeping'")[0]["state"] == "paused"
        initial = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
        initial_bots = rows(api, "SELECT b.slug,b.display_name,b.state,bc.reports_to FROM bots b JOIN bot_config bc ON bc.bot=b.slug ORDER BY b.slug")
    with TestClient(create_app(settings)) as api:
        assert api.get("/api/v2/setup", headers=signed_in()).json()["company_import"] == initial
        assert rows(api, "SELECT b.slug,b.display_name,b.state,bc.reports_to FROM bots b JOIN bot_config bc ON bc.bot=b.slug ORDER BY b.slug") == initial_bots
    settings.company_file_data = starter.pack(company_text().replace("Acme Finance", "Another Company"))
    with pytest.raises(Problem, match="already set up"):
        with TestClient(create_app(settings)):
            pass


def test_reserved_alias_cannot_replace_a_custom_named_assistant_now_or_later(environment):
    api = environment(seed_roster=False, assistant_bot="ace")
    seed = {"ace": {"name": "ace", "status": "planned", "template": "assistant"},
            "botops": {"name": "botops", "status": "planned", "template": "botops"}}
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, seed, PEOPLE)
        for slug, config in seed.items():
            c.execute("INSERT INTO bot_config(bot,config_json,operator,repo) VALUES(?,?,?,?)",
                      (slug, encode(config), "owner", "bot-" + slug))
    before = rows(api, "SELECT bot,config_json FROM bot_config ORDER BY bot")
    draft = C.from_yaml(company_text())
    draft["bots"] = [b for b in draft["bots"] if b["template"] != "coo"]
    employee = next(b for b in draft["bots"] if b["template"] == "bookkeeping")
    employee["id"] = "coo"
    for start in (True, False):
        employee["start"] = start
        body = {"company_yaml": C.to_yaml(draft)}
        preview = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in())
        assert preview.status_code == 422 and "conflicts" in preview.text
        result = api.post("/api/v2/setup/company/import", json={**body, "fingerprint": "0" * 64}, headers=signed_in())
        assert result.status_code == 422 and "conflicts" in result.text
        assert rows(api, "SELECT bot,config_json FROM bot_config ORDER BY bot") == before
    assert len(rows(api, "SELECT bot FROM bot_config WHERE json_extract(config_json,'$.template')='assistant'")) == 1


def test_a_300_bot_company_file_imports_and_a_301_bot_file_is_refused_plainly(environment):
    from backend.tests.test_company_file import chart

    api = environment()
    draft = chart(300)
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    text = C.to_yaml(draft)
    assert len(starter.canonical(starter.load(text)).encode()) > 131072      # past the old 128 KB plan limit
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["employees"]) == 300
    done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": preview.json()["fingerprint"]},
                    headers=signed_in())
    assert done.status_code == 200, done.text
    imported = {r["bot"] for r in rows(api, "SELECT bot FROM bot_config")}
    assert all(e["id"] in imported for e in preview.json()["employees"])
    extra = "  - id: one-too-many\n    template: bookkeeping\n    title: One Too Many\n"
    over = text.replace("employees:\n", "employees:\n" + extra, 1)
    refused = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": over}, headers=signed_in())
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "company_file"


def goals_text(person=True):
    """A company file with a company goal (the owner's), a bot's goal with a target and a range goal not measured yet."""
    draft = C.from_yaml(company_text())
    if person:
        draft["people"] = [{"id": "dana", "name": "Dana Ruiz", "role": "Founder", "dept": "leadership", "owner": True}]
    draft["goals"] = [
        {"id": "g-revenue", "title": "Reach 40 paying firms", "owner": "dana" if person else "you",
         "measure": "Paying firms", "unit": "firms", "direction": "up", "target": 40, "baseline": 12,
         "deadline": "2027-01-31"},
        {"id": "g-close", "title": "Close every month's books in five days", "owner": "finance-lead",
         "measure": "Days to close the books", "unit": "days", "direction": "range", "min": 0, "max": 5},
        {"id": "g-later", "title": "Answer every bookkeeping question", "owner": "bookkeeping", "measure": "",
         "unit": "", "direction": "up", "target": None, "baseline": None, "deadline": None},
        # Up, but its target is below its baseline: Tico refuses that target, and the import still lands the goal.
        {"id": "g-backwards", "title": "Grow referrals", "owner": "finance-lead", "measure": "Referrals a month",
         "unit": "", "direction": "up", "target": 5, "baseline": 9, "deadline": "2027-01-31"},
        # A blank title is allowed in the file, never on Goals.
        {"id": "g-blank", "title": " ", "owner": "bookkeeping", "measure": "Questions answered", "unit": "",
         "direction": "up", "target": None, "baseline": None, "deadline": None}]
    return C.to_yaml(draft)


@pytest.mark.parametrize("person", [True, False], ids=["named-owner", "you"])
@pytest.mark.parametrize("bootstrap", [False, True], ids=["owner-api", "installer-bootstrap"])
def test_imported_goals_show_on_goals_with_their_owner_and_measure(tmp_path, environment, person, bootstrap):
    text = goals_text(person)
    if bootstrap:
        from clients.environments import seed_registry
        seed_registry(tmp_path, "Owner", OWNER_EMAIL,
                      {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"})
        settings = settings_for(tmp_path)
        app = create_app(settings)
        app.state.store.initialize()
        app.state.store.seed()
        settings.company_file_data, settings.sample_company = starter.pack(text), True
        with TestClient(create_app(settings)) as api:
            imported_goals_on_goals_page(api, text)
    else:
        api = environment()
        preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in()).json()
        assert "goals" not in preview["deferred"]
        assert [(g["title"], g["owner"]) for g in preview["goals"]] == [
            ("Reach 40 paying firms", "company"), ("Close every month's books in five days", "bot:finance-lead"),
            ("Answer every bookkeeping question", "bot:bookkeeping"), ("Grow referrals", "bot:finance-lead"),
            ("Questions answered", "bot:bookkeeping")]
        done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": preview["fingerprint"]},
                        headers=signed_in())
        assert done.status_code == 200, done.text
        imported_goals_on_goals_page(api, text)


def imported_goals_on_goals_page(api, text):
    tree = api.get("/api/v2/goals/tree", headers=signed_in()).json()
    goals = {g["title"]: g for g in tree["goals"]}
    assert {title: g["owner"] for title, g in goals.items()} == {
        "Reach 40 paying firms": "company", "Close every month's books in five days": "bot:finance-lead",
        "Answer every bookkeeping question": "bot:bookkeeping", "Grow referrals": "bot:finance-lead",
        "Questions answered": "bot:bookkeeping"}
    assert tree["owners"]["company"]["kind"] == "company"
    assert tree["owners"]["bot:finance-lead"]["name"] == "Head of Finance"
    revenue, = goals["Reach 40 paying firms"]["kpis"]
    assert (revenue["name"], revenue["unit"], revenue["direction"]) == ("Paying firms", "firms", "up")
    assert {k: revenue["link"][k] for k in ("kind", "baseline", "target", "deadline")} == {
        "kind": "improve", "baseline": 12.0, "target": 40.0, "deadline": "2027-01-31"}
    close, = goals["Close every month's books in five days"]["kpis"]
    assert {k: close["link"][k] for k in ("kind", "min", "max")} == {"kind": "maintain", "min": 0.0, "max": 5.0}
    assert goals["Answer every bookkeeping question"]["kpis"] == []
    backwards, = goals["Grow referrals"]["kpis"]
    assert (backwards["name"], backwards["link"]["kind"]) == ("Referrals a month", "none")
    # The file holds targets, never readings: no history is made up.
    assert not rows(api, "SELECT id FROM kpi_readings")
    mine = api.get("/api/v2/goals", params={"owner": "finance-lead"}, headers=signed_in()).json()
    assert [g["title"] for g in mine["goals"]] == ["Close every month's books in five days", "Grow referrals"]
    record = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
    assert "goals" not in record["deferred"]
    again = api.post("/api/v2/onboarding/company/import",
                     json={"company_yaml": text, "fingerprint": record["fingerprint"]}, headers=signed_in())
    assert again.status_code == 200 and len(rows(api, "SELECT id FROM goals")) == 5


@pytest.mark.parametrize("bootstrap", [False, True], ids=["owner-api", "installer-bootstrap"])
def test_imported_goals_follow_a_renamed_assistant_and_an_untitled_unmeasured_goal_gets_no_kpi(tmp_path, environment,
                                                                                               bootstrap):
    draft = C.from_yaml(company_text())
    draft["goals"] = [
        {"id": "g-week", "title": "Run the week", "owner": "coo", "measure": "Weekly reviews held", "unit": "",
         "direction": "up", "target": None, "baseline": None, "deadline": None},
        {"id": "g-blank", "title": "", "owner": "coo", "measure": "", "unit": "", "direction": "up", "target": None,
         "baseline": None, "deadline": None}]
    text = C.to_yaml(draft)
    owners = [("Run the week", "bot:ace"), ("Untitled goal", "bot:ace")]
    if bootstrap:
        from clients.environments import seed_registry
        registry = seed_registry(tmp_path, "Owner", OWNER_EMAIL,
                                 {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"})
        for name in ("employees.yaml", "people.yaml"):      # an installer run with TICO_ASSISTANT_BOT=ace
            path = registry / name
            path.write_text(re.sub(r"\bcoo\b", "ace", path.read_text()))
        settings = settings_for(tmp_path, assistant_bot="ace")
        app = create_app(settings)
        app.state.store.initialize()
        app.state.store.seed()
        settings.company_file_data, settings.sample_company = starter.pack(text), True
        with TestClient(create_app(settings)) as api:
            renamed_assistant_goals_on_goals_page(api, owners)
    else:
        api = environment(seed_roster=False, assistant_bot="ace")
        seed = {"ace": {"name": "ace", "status": "planned", "template": "assistant"},
                "botops": {"name": "botops", "status": "planned", "template": "botops"}}
        with api.app.state.store.transaction() as c:
            H.sync_registry(c, seed, PEOPLE)
            for slug, config in seed.items():
                c.execute("INSERT INTO bot_config(bot,config_json,operator,repo) VALUES(?,?,?,?)",
                          (slug, encode(config), "owner", "bot-" + slug))
        preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in()).json()
        assert [(g["title"], g["owner"]) for g in preview["goals"]] == owners
        done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": preview["fingerprint"]},
                        headers=signed_in())
        assert done.status_code == 200, done.text
        renamed_assistant_goals_on_goals_page(api, owners)


def renamed_assistant_goals_on_goals_page(api, owners):
    goals = {g["title"]: g for g in api.get("/api/v2/goals/tree", headers=signed_in()).json()["goals"]}
    assert sorted((title, g["owner"]) for title, g in goals.items()) == owners
    week, = goals["Run the week"]["kpis"]
    assert rows(api, "SELECT owner FROM kpis WHERE id=?", (week["id"],)) == [{"owner": "bot:ace"}]
    assert goals["Untitled goal"]["kpis"] == []
    assert not rows(api, "SELECT id FROM goals WHERE owner='bot:coo'")
