"""A picker export creates the selected team through normal owner-only onboarding."""
import copy

import pytest
from fastapi.testclient import TestClient

from backend import company_file as C
from backend.app import create_app
from backend.auth import Identity
from backend.company_file import starter
from backend.store import H, Problem, encode
from backend.tests.test_sample_company import OWNER_EMAIL, PEOPLE, SEED, environment, machine, rows, settings_for, signed_in


def company_text(reverse_builtins=False, through_new_parent=False, keep=("coo", "botops", "finance-lead", "bookkeeping")):
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in set(keep)]
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


def test_a_seat_with_no_bot_yet_makes_no_bot_and_the_bots_under_it_report_one_step_up(environment):
    api = environment()
    text = company_text(keep=("coo", "botops", "finance-lead", "controller", "bookkeeping"))
    assert "no_bot_yet: true" in text
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    employees = {e["template"]: e for e in summary["employees"]}
    assert "controller" not in employees and "open_seats" in summary["deferred"]
    assert employees["bookkeeping"]["reports_to"] == "finance-lead"


def test_people_who_hold_seats_come_along_in_the_plan_and_make_no_bot(environment):
    api = environment()
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    keep = ("coo", "botops", "finance-lead", "bookkeeping", "controller")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in keep]
    seat = {b["template"]: b["title"] for b in draft["bots"]}
    turn = C.Turn(draft)
    for name, args in (("company_set", {"name": "Acme Finance", "about": "We help small firms manage their books."}),
                       ("person_set", {"name": "me", "role": "Founder"}),
                       ("seat_fill", {"seat": seat["bookkeeping"], "name": "Ana"}),
                       ("seat_fill", {"seat": seat["controller"], "name": "Ben"})):
        assert turn.apply(name, args)["ok"], name
    text = C.to_yaml(turn.finish())
    assert "held_by: ana" in text and "held_by: ben" in text
    body = {"company_yaml": text}
    preview = api.post("/api/v2/onboarding/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    # Ana's seat has a template and Ben's has none yet: a person holds each, so neither makes a bot.
    assert {e["template"] for e in summary["employees"]} == {"assistant", "botops", "finance-lead"}
    assert {"open_seats", "coworkers"} <= set(summary["deferred"])
    body["fingerprint"] = summary["fingerprint"]
    done = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert done.status_code == 200, done.text
    assert not rows(api, "SELECT slug FROM bots WHERE slug IN ('bookkeeping','controller')")
    saved = rows(api, "SELECT value_json FROM registry_metadata WHERE key='onboarding.company_file'")[0]["value_json"]
    assert '"person":"ana"' in saved and '"person":"ben"' in saved
    # A coworker who holds no seat still joins in Tico, not by import.
    loose = C.from_yaml(text)
    loose["people"].append({"id": "cara", "name": "Cara", "role": "Designer", "dept": None, "owner": False, "isNew": True})
    with pytest.raises(C.DraftError, match="add other coworkers in Tico"):
        starter.load(C.to_yaml(loose))


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
    seats = sum(b["template"] in {s["slug"] for s in C.SEATS} for b in draft["bots"])  # no bot yet: no employee
    assert seats and len(preview.json()["employees"]) == 300 - seats
    done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": preview.json()["fingerprint"]},
                    headers=signed_in())
    assert done.status_code == 200, done.text
    imported = {r["bot"] for r in rows(api, "SELECT bot FROM bot_config")}
    assert all(e["id"] in imported for e in preview.json()["employees"])
    extra = "  - id: one-too-many\n    template: bookkeeping\n    title: One Too Many\n"
    over = text.replace("employees:\n", "employees:\n" + extra, 1)
    refused = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": over}, headers=signed_in())
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "company_file"
