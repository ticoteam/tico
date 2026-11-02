"""A picker export creates the selected team through normal owner-only onboarding."""
import copy
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import company_file as C
from backend import market_spend as MS
from backend.app import create_app
from backend.auth import Identity
from backend.company_file import starter
from backend.store import H, Problem, encode
from backend.tests.test_sample_company import OWNER_EMAIL, PEOPLE, SEED, environment, machine, rows, settings_for, signed_in


def company_text(reverse_builtins=False, through_new_parent=False, keep=("coo", "botops", "finance-lead", "accounts-payable"),
                 business_type=None):
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    if business_type is not None:
        draft["company"]["business_type"] = business_type
    draft["bots"] = [b for b in draft["bots"] if b["template"] in set(keep)]
    for bot in draft["bots"]:
        bot["start"] = bot["template"] != "accounts-payable"
        if bot["template"] == "accounts-payable":
            bot["title"] = "Acme Payables"
            bot["routine"] = "Review the books each week"
        if reverse_builtins and bot["template"] == "coo":
            bot["reports_to"] = "botops"
        if through_new_parent and bot["template"] in ("coo", "finance-lead"):
            bot["reports_to"] = "finance-lead" if bot["template"] == "coo" else "botops"
    return C.to_yaml(draft) + "models:\n  source: gateway\n  by_department:\n    finance: mid\n"


BUILTINS = {"coo", "botops", "librarian", "goal-manager", "task-manager", "bookkeeper", "tico-counsel"}
TWELVE = ("finance-lead", "controller", "payroll", "billing", "ar-followup", "sales-lead", "sales", "sdr-research",
          "marketing-lead", "content", "seo-visibility", "ops-manager")


def twelve_role_text():
    """A chosen chart of 12 roles under the built-ins: seats under seats three deep (Finance), a role reporting to the
    Assistant (Ops), and the rest on the model's own lines."""
    draft = C.from_yaml(company_text(keep=("coo", "botops", *TWELVE)))
    next(b for b in draft["bots"] if b["id"] == "ops-manager")["reports_to"] = "coo"
    return C.to_yaml(draft)


def seat_lines(api):
    """{title: what its seat reports to, by title}: a seat's line to another seat names that seat's title."""
    seats = {r["id"]: r for r in rows(api, "SELECT id,title,reports_to FROM seats WHERE closed_at IS NULL")}
    return {r["title"]: (seats[r["reports_to"][5:]]["title"] if r["reports_to"].startswith("seat:") else r["reports_to"])
            for r in seats.values()}


def test_a_chosen_chart_of_12_roles_lays_out_12_recruiting_seats_and_only_the_builtins_are_bots(environment):
    """Spec acceptance 1 and 20: every role a Recruiting seat with exactly its line; only the built-ins are bots, Active;
    no repository, computer slot or BotOps setup task for any other role."""
    api = environment()
    text = twelve_role_text()
    draft = C.from_yaml(text)
    titles = {b["id"]: b["title"] for b in draft["bots"]}
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    assert {e["id"] for e in summary["employees"]} == {"coo", "botops"}
    assert sorted(s["id"] for s in summary["seats"]) == sorted(TWELVE)
    done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": summary["fingerprint"]},
                    headers=signed_in())
    assert done.status_code == 200, done.text

    from backend.company_file import draft as D
    expected = {}
    for bot in draft["bots"]:
        if bot["id"] in TWELVE:
            parent = D.manager_of(draft, bot)
            expected[bot["title"]] = titles[parent] if parent in TWELVE else ("coo" if parent == "coo" else "human:owner")
    assert seat_lines(api) == expected and len(expected) == 12
    assert expected[titles["ar-followup"]] == titles["controller"] and expected[titles["controller"]] == titles["finance-lead"]
    assert expected[titles["ops-manager"]] == "coo"
    # Only the built-ins exist as bots, and none of them is Onboarding.
    bots = {r["slug"] for r in rows(api, "SELECT slug FROM bots")}
    assert bots == BUILTINS
    assert all(not r["onboarding_state"] or r["onboarding_state"] == "onboarded"
               for r in rows(api, "SELECT onboarding_state FROM bot_config"))
    # No BotOps setup task, repository or computer slot for any role.
    assert not rows(api, "SELECT id FROM tasks WHERE title LIKE 'Set up %'")
    assert not rows(api, "SELECT bot FROM bot_config WHERE bot NOT IN ('coo','botops','librarian','goal-manager','task-manager','bookkeeper','tico-counsel')")
    assert not rows(api, "SELECT bot FROM assignments WHERE bot NOT IN ('coo','botops','librarian','goal-manager','task-manager','bookkeeper','tico-counsel')")
    machine(api)
    assert {r["bot"] for r in rows(api, "SELECT bot FROM assignments")} <= BUILTINS
    assert {r["slug"]: r["state"] for r in rows(api, "SELECT slug,state FROM bots")}["botops"] == "active"
    # The org chart shows the 12 roles, each Recruiting, with those lines.
    org = api.get("/api/v2/org", headers=signed_in()).json()
    seats = {s["id"]: s for s in org["seats"]}
    assert len(seats) == 12 and {s["stage"] for s in seats.values()} == {"recruiting"}
    by_title = {s["title"]: s for s in seats.values()}
    assert by_title[titles["payroll"]]["org_parent"] == "s:" + by_title[titles["controller"]]["id"]
    assert by_title[titles["ops-manager"]]["org_parent"] == "b:coo"
    assert {b["id"]: b["stage"] for b in org["bots"]}["botops"] == "active"
    # The sidebar's chart reads the same seats.
    people = api.get("/api/humans", headers=signed_in()).json()
    assert {s["title"]: s["org_parent"] for s in people["seats"]} == {s["title"]: s["org_parent"] for s in seats.values()}


def test_a_seat_with_no_template_yet_is_a_recruiting_seat_like_any_other_role(environment, monkeypatch):
    monkeypatch.setattr(C.draft, "OPEN_SEATS", {"controller"})  # every model seat has a template now
    api = environment()
    text = company_text(keep=("coo", "botops", "finance-lead", "controller", "accounts-payable"))
    assert "no_bot_yet: true" in text
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    assert {s["id"]: s["reports_to"] for s in summary["seats"]} == {
        "finance-lead": "human:owner", "controller": "finance-lead", "accounts-payable": "controller"}
    assert "open_seats" not in summary["deferred"]


def test_people_who_hold_roles_are_kept_to_invite_and_the_seats_under_them_wait_with_the_owner(environment):
    api = environment()
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    keep = ("coo", "botops", "finance-lead", "accounts-payable", "controller")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in keep]
    seat = {b["template"]: b["title"] for b in draft["bots"]}
    turn = C.Turn(draft)
    for name, args in (("company_set", {"name": "Acme Finance", "about": "We help small firms manage their books."}),
                       ("person_set", {"name": "me", "role": "Founder"}),
                       ("seat_fill", {"seat": seat["accounts-payable"], "name": "Ana"}),
                       ("seat_fill", {"seat": seat["controller"], "name": "Ben"}),
                       ("goal_add", {"title": "Close the books by day 5", "owner": seat["controller"]}),
                       ("goal_add", {"title": "Clean ledgers", "owner": "Ana"})):
        assert turn.apply(name, args)["ok"], name
    draft = turn.finish()
    next(b for b in draft["bots"] if b["template"] == "finance-lead")["reports_to"] = "ana"
    text = C.to_yaml(draft)
    assert "held_by: ana" in text and "held_by: ben" in text
    body = {"company_yaml": text}
    preview = api.post("/api/v2/onboarding/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    # Ana and Ben hold their roles: no seat and no bot for those. Finance Lead is a seat under Ana, who is no one in
    # Tico until the owner invites her: it reports to the owner meanwhile.
    assert {e["template"] for e in summary["employees"]} == {"assistant", "botops"}
    assert [(s["id"], s["reports_to"], s.get("waits_on")) for s in summary["seats"]] == [
        ("finance-lead", "human:owner", "ana")]
    assert [(p["id"], p["name"], p["title"]) for p in summary["people"]] == [
        ("ana", "Ana", seat["accounts-payable"]), ("ben", "Ben", seat["controller"])]
    assert not {"open_seats", "coworkers"} & set(summary["deferred"])
    assert {g["title"]: g["owner"] for g in summary["goals"]} == {"Close the books by day 5": "company", "Clean ledgers": "company"}
    body["fingerprint"] = summary["fingerprint"]
    done = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert done.status_code == 200, done.text
    assert not rows(api, "SELECT slug FROM bots WHERE slug IN ('accounts-payable','controller','finance-lead')")
    finance = rows(api, "SELECT id,reports_to FROM seats")
    assert [r["reports_to"] for r in finance] == ["human:owner"]
    people = done.json()["company_import"]["people"]
    assert [(p["id"], p["seats"]) for p in people] == [("ana", [finance[0]["id"]]), ("ben", [])]
    saved = rows(api, "SELECT value_json FROM registry_metadata WHERE key='onboarding.company_file'")[0]["value_json"]
    assert '"person":"ana"' in saved and '"person":"ben"' in saved
    invited_from_setup(api, finance[0]["id"])
    # A coworker who holds no role still joins in Tico, not by import.
    loose = C.from_yaml(text)
    loose["people"].append({"id": "cara", "name": "Cara", "role": "Designer", "dept": None, "owner": False, "isNew": True})
    with pytest.raises(C.DraftError, match="add other coworkers in Tico"):
        starter.load(C.to_yaml(loose))


def invited_from_setup(api, finance):
    """The owner gives Ana's email and clicks Invite: she is on the roster and the seat that waited with the owner
    reports to her. This install signs in another way, so no invite email can go: the owner is told so, plainly."""
    invite = lambda key, email: api.post("/api/v2/onboarding/company/people/%s/invite" % key, json={"email": email},
                                         headers=signed_in())
    refused = invite("ana", "ana@acme.example")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invite_unavailable"
    assert "Tell Ana to sign in at" in refused.json()["error"]["detail"]
    ana, = [p for p in api.get("/api/people", headers=signed_in()).json()["people"] if p["email"] == "ana@acme.example"]
    assert rows(api, "SELECT reports_to FROM seats WHERE id=?", (finance,))[0]["reports_to"] == "human:" + ana["id"]
    record = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
    assert [(p["id"], p.get("person"), p["invited_at"]) for p in record["people"]] == [
        ("ana", ana["id"], None), ("ben", None, None)]

    # On a Tico account sign-in the issuer sends it: Invite again sends to the person already added, once. A repeat of
    # one request (same Idempotency-Key, ui/app/api.js) sends nothing more, nor does a copy overlapping it.
    from backend import invites
    keyed = lambda key, email="someone-else@acme.example": api.post(
        "/api/v2/onboarding/company/people/ana/invite", json={"email": email},
        headers={**signed_in(), "Idempotency-Key": key})

    class Issuer:
        name, calls, overlap = "oidc", [], []

        def invite(self, email):
            if not self.overlap:
                self.overlap.append(None)
                self.overlap[0] = keyed("k-1")
            self.calls.append(email)
            return 202, {"status": "sent"}
    api.app.state.auth.proxy = Issuer()
    try:
        sent = keyed("k-1")
        assert sent.status_code == 200, sent.text
        assert Issuer.overlap[0].status_code == 422 and Issuer.overlap[0].json()["error"]["code"] == "invite_unknown"
        assert Issuer.calls == ["ana@acme.example"]
        assert [p["invited_at"] is not None for p in sent.json()["company_import"]["people"]] == [True, False]
        again = keyed("k-1")
        assert again.status_code == 200 and Issuer.calls == ["ana@acme.example"]
        assert again.json()["company_import"]["people"] == sent.json()["company_import"]["people"]
        # Sent, then the server failed before answering: the repeat records it and sends nothing more.
        record = invites.record
        invites.record = lambda *a: (_ for _ in ()).throw(RuntimeError("lost"))
        try:
            with pytest.raises(RuntimeError):
                keyed("k-2")
        finally:
            invites.record = record
        assert keyed("k-2").status_code == 200 and len(Issuer.calls) == 2
        assert invite("nobody", "x@acme.example").status_code == 404
    finally:
        api.app.state.auth.proxy = None


def test_import_lays_out_the_chart_once_across_retry_and_runner_enrollment(environment):
    api = environment()
    body = {"company_yaml": company_text()}
    preview = api.post("/api/v2/onboarding/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    assert not rows(api, "SELECT id FROM seats")
    body["fingerprint"] = preview.json()["fingerprint"]
    done = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert done.status_code == 200, done.text
    assert done.json()["names"]["company_name"] == "Acme Finance"
    assert {r["slug"] for r in rows(api, "SELECT slug FROM bots")} == BUILTINS
    assert seat_lines(api) == {"Head of Finance": "human:owner", "Acme Payables": "Head of Finance"}
    templates = rows(api, "SELECT json_extract(config_json,'$.template') AS template FROM bot_config")
    assert sum(r["template"] == "assistant" for r in templates) == 1
    assert sum(r["template"] == "botops" for r in templates) == 1
    assert "model_choices" in done.json()["company_import"]["deferred"]
    assert "routines" in done.json()["company_import"]["deferred"]
    # A seat's job description is its role's text from the template.
    description = rows(api, "SELECT description FROM seats WHERE title='Acme Payables'")[0]["description"]
    assert description and "Owns:" in description
    saved = rows(api, "SELECT value_json FROM registry_metadata WHERE key='onboarding.company_file'")[0]
    assert '"finance":"mid"' in saved["value_json"]
    before = (rows(api, "SELECT id FROM tasks ORDER BY id"), rows(api, "SELECT id FROM seats ORDER BY id"))
    again = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert again.status_code == 200
    assert (rows(api, "SELECT id FROM tasks ORDER BY id"), rows(api, "SELECT id FROM seats ORDER BY id")) == before
    machine(api)
    assert {r["slug"] for r in rows(api, "SELECT slug FROM bots")} == BUILTINS


def test_import_requires_owner_and_matching_preview_and_refuses_replacing_a_configured_company(environment):
    api = environment()
    body = {"company_yaml": company_text(), "fingerprint": "0" * 64}
    refused = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert refused.status_code == 409
    assert not rows(api, "SELECT slug FROM bots WHERE slug='accounts-payable'")
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


def sidebar_config(api):
    return api.get("/api/v2/config", headers=signed_in()).json()


def test_a_company_file_business_type_sets_the_sidebar_before_setup_completes(environment):
    """tico.dev/setup writes `company.business_type`; the import sets it before setup completes, so the company's defaults
    apply: a software company hides the Operations tabs its table leaves out."""
    api = environment()
    preview = preview_and_import(api, company_text(business_type="software"))
    assert preview["business_type"] == "software" and "warnings" not in preview
    assert sidebar_config(api)["business_type"] == "software"
    assert sidebar_config(api)["hidden_sections"] == ["operations.locations", "operations.equipment"]


def test_an_unknown_business_type_is_a_warning_and_the_import_still_imports(environment):
    api = environment()
    preview = preview_and_import(api, company_text(business_type="bakery"))
    assert len(preview["warnings"]) == 1 and '"bakery" is not one Tico knows' in preview["warnings"][0]
    assert "business_type" not in preview
    assert sidebar_config(api)["company_name"] == "Acme Finance"  # the import went through
    assert sidebar_config(api)["business_type"] == "other"      # no answer from the file: setup's own default


def test_a_file_without_a_business_type_leaves_the_sidebar_to_setup(environment):
    api = environment()
    preview = preview_and_import(api, company_text())
    assert "business_type" not in preview and "warnings" not in preview
    assert sidebar_config(api)["business_type"] == "other"


def test_a_business_type_a_person_chose_before_the_import_is_not_overridden(environment):
    api = environment()
    chosen = api.put("/api/v2/company/sidebar", json={"business_type": "retail"}, headers=signed_in())
    assert chosen.status_code == 200, chosen.text
    preview_and_import(api, company_text(business_type="software"))
    assert sidebar_config(api)["business_type"] == "retail"
    assert sidebar_config(api)["hidden_sections"] == ["operations"]


def test_reimporting_the_file_keeps_a_business_type_chosen_in_settings(environment):
    api = environment()
    text = company_text(business_type="software")
    preview_and_import(api, text)
    assert api.put("/api/v2/company/sidebar", json={"business_type": "retail"}, headers=signed_in()).status_code == 200
    again = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in()).json()
    done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": again["fingerprint"]},
                    headers=signed_in())
    assert done.status_code == 200, done.text
    assert sidebar_config(api)["business_type"] == "retail"


def test_import_never_replaces_an_existing_paused_employee(environment):
    api = environment()
    config = {"name": "accounts-payable", "status": "paused", "instructions": "Keep the existing employee unchanged"}
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {**SEED, "accounts-payable": config}, PEOPLE)
        c.execute("INSERT INTO bot_config(bot,config_json,operator,repo) VALUES(?,?,?,?)",
                  ("accounts-payable", encode(config), "owner", "emp-bookkeeping"))
    original = rows(api, "SELECT config_json FROM bot_config WHERE bot='accounts-payable'")
    body = {"company_yaml": company_text()}
    body["fingerprint"] = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in()).json()["fingerprint"]
    assert api.post("/api/v2/setup/company/import", json=body, headers=signed_in()).status_code == 409
    assert rows(api, "SELECT config_json FROM bot_config WHERE bot='accounts-payable'") == original


@pytest.mark.parametrize("through_new_parent", [False, True], ids=["reverse-builtins", "new-parent-chain"])
@pytest.mark.parametrize("bootstrap", [False, True], ids=["owner-api", "installer-bootstrap"])
def test_install_bootstrap_lays_out_the_chosen_chart_once_and_never_falls_back_to_sample(tmp_path, through_new_parent, bootstrap):
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
        assert {r["slug"] for r in rows(api, "SELECT slug FROM bots")} == BUILTINS
        assert seat_lines(api) == {"Head of Finance": "botops" if through_new_parent else "human:owner",
                                   "Acme Payables": "Head of Finance"}
        parents = {r["bot"]: r["reports_to"] for r in rows(api, "SELECT bot,reports_to FROM bot_config")}
        assert {b["id"]: parents[b["id"]] for b in preview["employees"]} == {
            b["id"]: b["reports_to"] for b in preview["employees"]}
        record = api.get("/api/v2/setup", headers=signed_in()).json()
        assert record["company_import_failed"] is None
        assert {slug: choice["reports_to"] for slug, choice in record["selected"].items()} == {
            b["id"]: b["reports_to"] for b in preview["employees"]}
        machine(api)
        initial = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
        initial_seats = rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id")
    with TestClient(create_app(settings)) as api:
        assert api.get("/api/v2/setup", headers=signed_in()).json()["company_import"] == initial
        assert rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id") == initial_seats
    # A changed file never replaces a set-up company. The server still starts, and the owner sees why.
    settings.company_file_data = starter.pack(company_text().replace("Acme Finance", "Another Company"))
    with TestClient(create_app(settings)) as api:
        failed = api.get("/api/v2/setup", headers=signed_in()).json()["company_import_failed"]
        assert "already set up" in failed["reason"]
        assert rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id") == initial_seats


@pytest.mark.parametrize("broken", ["unreadable", "conflicting-id", "empty-file", "missing-file", "oversized-file"])
def test_a_chosen_chart_that_fails_to_import_is_shown_to_the_owner_and_no_sample_takes_its_place(tmp_path, monkeypatch,
                                                                                                broken):
    """Spec acceptance 2 (first-v1 rule): the server starts with the built-ins only, nothing of the file is left, no
    sample company is made, and the owner sees why on first run and on Health. A later import that works clears it.
    A file setup copied to the server (TICO_COMPANY_FILE) that is empty, missing or too big is such a failure too."""
    from backend.config import Settings
    from clients.environments import seed_registry
    seed_registry(tmp_path, "Owner", OWNER_EMAIL,
                  {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"})
    settings = settings_for(tmp_path)
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()
    good = company_text()
    conflicting = C.from_yaml(good)
    conflicting["bots"] = [b for b in conflicting["bots"] if b["template"] != "coo"]
    next(b for b in conflicting["bots"] if b["template"] == "accounts-payable")["id"] = "coo"   # the Assistant's own slug
    settings.sample_company = True
    if broken.endswith("-file"):
        plan = tmp_path / "plan.b64"
        if broken == "empty-file":
            plan.write_text(" \n\n")
        elif broken == "oversized-file":
            plan.write_text("x" * (starter.MAX_PACKED + 1))
        monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
        monkeypatch.delenv("TICO_REHEARSAL", raising=False)
        monkeypatch.delenv("TICO_COMPANY_FILE_B64", raising=False)
        monkeypatch.setenv("TICO_COMPANY_FILE", str(plan))
        read = Settings.from_env()
        settings.company_file_data, settings.company_file_configured, settings.company_file_error = (
            read.company_file_data, read.company_file_configured, read.company_file_error)
        assert settings.company_file_configured and not settings.company_file_data
    else:
        settings.company_file_data = "not a packed company file" if broken == "unreadable" else starter.pack(
            C.to_yaml(conflicting))
    with TestClient(create_app(settings)) as api:
        record = api.get("/api/v2/setup", headers=signed_in()).json()
        failed = record["company_import_failed"]
        assert failed and failed["reason"] and failed["at"]
        assert record["sample"] is None and record["company_import"] is None
        assert not rows(api, "SELECT id FROM seats")
        assert {r["slug"] for r in rows(api, "SELECT slug FROM bots")} <= BUILTINS
        assert api.get("/api/v2/config", headers=signed_in()).json()["onboarding_needed"] is True
        checks = {row["id"]: row for row in api.get("/api/v2/health", headers=signed_in()).json()["checks"]}
        assert checks["company_import"]["status"] == "bad" and failed["reason"] in checks["company_import"]["summary"]
        # The owner imports a file that works: the failure is gone.
        preview = api.post("/api/v2/setup/company/preview", json={"company_yaml": good}, headers=signed_in()).json()
        done = api.post("/api/v2/setup/company/import", json={"company_yaml": good, "fingerprint": preview["fingerprint"]},
                        headers=signed_in())
        assert done.status_code == 200, done.text
        assert done.json()["company_import_failed"] is None


def test_every_way_out_of_a_failed_setup_import_clears_it_on_a_company_already_set_up(tmp_path):
    """Reverse state of the kept start failure: the applied file again (at start or imported), the file setting removed,
    and the owner's Dismiss each end it, even once the company is set up and an import can't replace its team."""
    from clients.environments import seed_registry
    seed_registry(tmp_path, "Owner", OWNER_EMAIL,
                  {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"})
    good, settings = company_text(), settings_for(tmp_path)
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()
    settings.company_file_data = starter.pack(good)
    changed = starter.pack(good.replace("Acme Finance", "Another Company"))

    def start(data="", configured=True, error=""):
        settings.company_file_data, settings.company_file_configured, settings.company_file_error = data, configured, error
        return TestClient(create_app(settings))

    def failed(api):
        return api.get("/api/v2/setup", headers=signed_in()).json()["company_import_failed"]

    with start(settings.company_file_data) as api:
        assert failed(api) is None and api.get("/api/v2/setup", headers=signed_in()).json()["completed"]
    with start(error="The company file setup copied to this server can't be read (no access)") as api:
        assert "can't be read" in failed(api)["reason"]
    with start(starter.pack(good)) as api:              # the file is fixed: it is the one already applied
        assert failed(api) is None
    with start(changed) as api:
        assert "already set up" in failed(api)["reason"]
        checks = {row["id"]: row for row in api.get("/api/v2/health", headers=signed_in()).json()["checks"]}
        assert "dismiss" in checks["company_import"]["summary"]
        body = {"company_yaml": good}
        body["fingerprint"] = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in()).json()["fingerprint"]
        done = api.post("/api/v2/setup/company/import", json=body, headers=signed_in())
        assert done.status_code == 200 and done.json()["company_import_failed"] is None
    with start(changed) as api:
        assert failed(api)
    with start(configured=False) as api:                # the file setting is removed
        assert failed(api) is None
    with start(changed) as api:
        assert failed(api)
        dismissed = api.post("/api/v2/setup/company/import-failure/dismiss", json={}, headers=signed_in())
        assert dismissed.status_code == 200 and dismissed.json()["company_import_failed"] is None
        assert not {row["id"] for row in api.get("/api/v2/health", headers=signed_in()).json()["checks"]} & {"company_import"}


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
    employee = next(b for b in draft["bots"] if b["template"] == "accounts-payable")
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
    builtins = sum(b["template"] in BUILTINS for b in draft["bots"])
    assert len(preview.json()["employees"]) == builtins and len(preview.json()["seats"]) == 300 - builtins
    done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": preview.json()["fingerprint"]},
                    headers=signed_in())
    assert done.status_code == 200, done.text
    imported = {r["bot"] for r in rows(api, "SELECT bot FROM bot_config")}
    assert all(e["id"] in imported for e in preview.json()["employees"])
    assert len(rows(api, "SELECT id FROM seats WHERE closed_at IS NULL")) == 300 - builtins
    extra = "  - id: one-too-many\n    template: accounts-payable\n    title: One Too Many\n"
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
        {"id": "g-later", "title": "Answer every bookkeeping question", "owner": "accounts-payable", "measure": "",
         "unit": "", "direction": "up", "target": None, "baseline": None, "deadline": None},
        # Up, but its target is below its baseline: Tico refuses that target, and the import still lands the goal.
        {"id": "g-backwards", "title": "Grow referrals", "owner": "finance-lead", "measure": "Referrals a month",
         "unit": "", "direction": "up", "target": 5, "baseline": 9, "deadline": "2027-01-31"},
        # A blank title is allowed in the file, never on Goals.
        {"id": "g-blank", "title": " ", "owner": "accounts-payable", "measure": "Questions answered", "unit": "",
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
            ("Reach 40 paying firms", "company"), ("Close every month's books in five days", "company"),
            ("Answer every bookkeeping question", "company"), ("Grow referrals", "company"),
            ("Questions answered", "company")]
        done = api.post("/api/v2/onboarding/company/import", json={"company_yaml": text, "fingerprint": preview["fingerprint"]},
                        headers=signed_in())
        assert done.status_code == 200, done.text
        imported_goals_on_goals_page(api, text)


def imported_goals_on_goals_page(api, text):
    tree = api.get("/api/v2/goals/tree", headers=signed_in()).json()
    goals = {g["title"]: g for g in tree["goals"]}
    assert {title: g["owner"] for title, g in goals.items()} == {
        "Reach 40 paying firms": "company", "Close every month's books in five days": "company",
        "Answer every bookkeeping question": "company", "Grow referrals": "company",
        "Questions answered": "company"}
    # A Recruiting seat holds no work, so the goals the file gives its roles wait with the company.
    assert tree["owners"]["company"]["kind"] == "company" and set(tree["owners"]) == {"company"}
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
    record = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
    assert "goals" not in record["deferred"]
    again = api.post("/api/v2/onboarding/company/import",
                     json={"company_yaml": text, "fingerprint": record["fingerprint"]}, headers=signed_in())
    assert again.status_code == 200 and len(rows(api, "SELECT id FROM goals")) == 5


@pytest.mark.parametrize("bootstrap", [False, True], ids=["owner-api", "installer-bootstrap"])
def test_a_renamed_assistants_goals_import_as_company_goals_and_an_untitled_unmeasured_goal_gets_no_kpi(tmp_path, environment,
                                                                                                       bootstrap):
    # Goals keeps the built-in bots out of its tree, so a goal the file gives the Assistant must land on the company.
    draft = C.from_yaml(company_text())
    draft["goals"] = [
        {"id": "g-week", "title": "Run the week", "owner": "coo", "measure": "Weekly reviews held", "unit": "",
         "direction": "up", "target": None, "baseline": None, "deadline": None},
        {"id": "g-blank", "title": "", "owner": "coo", "measure": "", "unit": "", "direction": "up", "target": None,
         "baseline": None, "deadline": None}]
    text = C.to_yaml(draft)
    owners = [("Run the week", "company"), ("Untitled goal", "company")]
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
    assert rows(api, "SELECT owner FROM kpis WHERE id=?", (week["id"],)) == [{"owner": "company"}]
    assert goals["Untitled goal"]["kpis"] == []
    assert not rows(api, "SELECT id FROM goals WHERE owner IN ('bot:ace','bot:coo')")


@pytest.mark.parametrize("count", [72, 300])
def test_a_full_plan_too_big_for_the_env_starts_the_server_from_its_file(tmp_path, monkeypatch, count):
    """AWS and SSH setup copy a packed plan to a file and TICO_COMPANY_FILE names it (compose.yaml)."""
    from backend.config import Settings
    from backend.tests.test_company_file import chart
    from clients.environments import seed_registry

    draft = C.Turn(C.blank()).finish() if count == 72 else chart(count)
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    packed = starter.pack(C.to_yaml(draft), starter.MAX_PACKED)
    assert len(packed) > starter.ENV_PACKED
    with pytest.raises(C.DraftError, match="too large for automatic setup"):
        starter.pack(C.to_yaml(draft))                                    # the env path's limit is unchanged
    plan = tmp_path / "plan.b64"
    plan.write_text(packed + "\n")
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.delenv("TICO_REHEARSAL", raising=False)
    monkeypatch.delenv("TICO_COMPANY_FILE_B64", raising=False)
    monkeypatch.setenv("TICO_COMPANY_FILE", str(plan))
    assert Settings.from_env().company_file_data == packed
    monkeypatch.setenv("TICO_COMPANY_FILE_B64", "from-the-env")
    assert Settings.from_env().company_file_data == "from-the-env"   # the env still wins (self-install, older setups)
    monkeypatch.delenv("TICO_COMPANY_FILE_B64")

    seed_registry(tmp_path, "Owner", OWNER_EMAIL, {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"})
    settings = settings_for(tmp_path)
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()
    settings.company_file_data = packed
    with TestClient(create_app(settings)) as api:
        # Only the built-ins are bots; every other role the file names is a Recruiting seat, and a role a person
        # holds is theirs (kept to invite), so neither.
        assert api.get("/api/v2/setup", headers=signed_in()).json()["company_import_failed"] is None
        assert {r["slug"] for r in rows(api, "SELECT slug FROM bots")} <= BUILTINS
        builtins = sum(b["template"] in BUILTINS for b in draft["bots"])
        held = sum(bool(b.get("person")) for b in draft["bots"])
        assert len(draft["bots"]) == count and builtins < count
        assert len(rows(api, "SELECT id FROM seats WHERE closed_at IS NULL")) == count - builtins - held > 0


# ------------------------------------------------------------------------- every goal setup's /goals writes (Dev 449)
def starter_chart():
    """The setup page's starter chart: the Assistant and BotOps, six department heads and 16 roles under them."""
    from backend.tests.test_company_file import started

    draft = started()
    roles = {"engineering": 8, "finance": 2, "marketing": 2, "sales": 2, "support": 2, "product": 0}
    keep = [b for b in draft["bots"] if b["core"] or (b["lead"] and b["dept"] in roles)]
    for dept, count in roles.items():
        keep += [b for b in draft["bots"] if b["dept"] == dept and not b["lead"] and not b["core"]][:count]
    draft["bots"] = keep
    draft["people"] = [{"id": "dana", "name": "Dana Ruiz", "role": "Founder", "dept": "leadership", "owner": True}]
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    return C.Turn(draft).finish()


def every_goal(draft):
    """`draft` with every goal setup's `POST /goals` (tico-services recruiting/goals.py) writes for it: the company's 3,
    2 for each department (its head's) and 1 for each bot that is neither built in nor a head, each title, measure and
    unit as long as the file allows."""
    def goal(owner, title):
        return {"id": "goal-%d" % (len(draft["goals"]) + 1), "owner": owner,
                "title": (title + ", every single week of the quarter" * 8)[:C.LIMITS["goal_title"]],
                "measure": "m" * C.LIMITS["measure"], "unit": "u" * C.LIMITS["unit"], "direction": "up",
                "target": 4, "baseline": None, "deadline": "2027-01-15"}

    owner = next(p["id"] for p in draft["people"] if p["owner"])
    for i in range(3):
        draft["goals"].append(goal(owner, "Company goal %s" % "ABC"[i]))
    for dept in dict.fromkeys(b["dept"] for b in draft["bots"] if not b["core"]):
        head = next((b["id"] for b in draft["bots"] if b["lead"] and b["dept"] == dept), owner)
        for i in range(2):
            draft["goals"].append(goal(head, "%s goal %s" % (dept, "AB"[i])))
    for bot in (b for b in draft["bots"] if not b["core"] and not b["lead"]):
        draft["goals"].append(goal(bot["id"], "Goal for %s" % bot["id"]))
    return draft


def preview_and_import(api, text):
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    done = api.post("/api/v2/onboarding/company/import",
                    json={"company_yaml": text, "fingerprint": preview.json()["fingerprint"]}, headers=signed_in())
    assert done.status_code == 200, done.text
    return preview.json()


def test_the_starter_charts_full_company_file_with_all_31_goals_imports(environment):
    """The page's full company file (tico.company.yaml) for the starter chart holds 23-31 goals; at 20 it was refused."""
    draft = every_goal(starter_chart())
    assert len(draft["goals"]) == 3 + 2 * 6 + 16 == 31
    text = C.to_yaml(draft)
    api = environment()
    preview = preview_and_import(api, text)
    assert len(preview["goals"]) == 31 and "goals" not in preview["deferred"]
    assert len(rows(api, "SELECT id FROM goals")) == 31


def test_a_325_goal_company_file_imports_and_a_326_goal_file_is_refused_plainly(environment):
    """325 = 3 company goals + 2 for each department + 1 for each of the most bots a chart holds, as tico-services
    allows; one past it is still refused, before anything is created."""
    from backend.company_file import draft as D
    from backend.tests.test_company_file import chart

    assert C.LIMITS["goals"] == 325 >= 3 + 2 * len(D.DEPARTMENTS) + C.LIMITS["bots"]
    draft = every_goal(chart(300))
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    assert 20 < len(draft["goals"]) <= C.LIMITS["goals"]                 # every goal /goals writes for a full chart
    while len(draft["goals"]) < C.LIMITS["goals"]:                     # a visitor's own goals fill the rest
        draft["goals"].append({**draft["goals"][0], "id": "goal-%d" % (len(draft["goals"]) + 1)})
    text = C.to_yaml(draft)
    assert len(C.from_yaml(text)["goals"]) == 325
    assert len(starter.canonical(starter.load(text)).encode()) <= starter.MAX_BYTES     # our largest file fits our own check
    api = environment()
    # Written by hand: our own writer refuses to make a 326-goal file.
    extra = ("  - id: goal-326\n    title: One goal too many\n    owner: %s\n    measure: null\n    unit: ''\n"
             "    direction: up\n" % draft["goals"][0]["owner"])
    over = text.replace("\nrules:\n", "\n" + extra + "rules:\n", 1)
    assert over != text
    for path, body in (("preview", {}), ("import", {"fingerprint": "0" * 64})):
        refused = api.post("/api/v2/onboarding/company/" + path, json={"company_yaml": over, **body}, headers=signed_in())
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "company_file", refused.text
        assert "goals has more than 325 entries" in refused.text
    assert not rows(api, "SELECT id FROM goals")
    preview = preview_and_import(api, text)
    assert len(preview["goals"]) == 325 and len(rows(api, "SELECT id FROM goals")) == 325


# ------------------------------------------------------------------------------------- the strategy block (format 0.3)
STRATEGY_FILE = Path(__file__).parent / "fixtures" / "company_strategy" / "company.yaml"


def test_a_strategy_block_imports_as_an_unconfirmed_draft_with_its_market_watches_and_insights(environment):
    """The fictional Acme Build file both repos test against: everything the setup page drafted lands for the owner to
    confirm, nothing published, and each goal on the bet it came from."""
    api = environment()
    summary = preview_and_import(api, STRATEGY_FILE.read_text())["strategy"]
    assert [b["id"] for b in summary["bets"]] == ["b1", "b2", "b3"] and summary["sections"][-1] == "watching"
    assert summary["market"] == {"root": "Construction management software", "segments": 4, "sizes": 3,
                                 "players": ["Trestle", "FieldCraft"]}
    assert summary["watches"] == ["https://example.com/trestle", "https://example.com/fieldcraft"]
    assert summary["insights"] == 2

    strategy = api.get("/api/v2/strategy", headers=signed_in()).json()
    draft = strategy["draft"]
    assert draft["summary"].startswith("Win field crews") and draft["vision"].startswith("Every crew")
    assert draft["sections"]["serve"] == ("_Unconfirmed: drafted from the company file._\n\nSuperintendents and "
                                          "foremen at general contractors with 50 to 500 staff.")
    assert all(draft["sections"][k].startswith("_Unconfirmed") for k in ("sell", "know", "not"))
    assert draft["sections"]["extra"]["watching"] == {
        "title": "Watching", "body": "- Competitor changelogs: Trestle, FieldCraft\n"
                                     "- Superintendents asking for a better schedule app"}
    assert api.get("/api/v2/strategy/versions", headers=signed_in()).json() == []
    bets = {b["statement"]: b for b in strategy["bets"]}
    assert {b["status"] for b in bets.values()} == {"proposed"} and len(bets) == 3
    phone = bets["Win the superintendent's phone"]
    assert phone["wrong_if"] == {"kind": "text", "text": "Fewer than 3 in 10 supers open it daily after 60 days",
                                 "check_by": "2027-01-23"}
    assert bets["Photos are the input"]["wrong_if"]["check_by"] > H.now()[:10]       # none in the file: 90 days out
    goals = {r["title"]: r["bet_id"] for r in rows(api, "SELECT title, bet_id FROM goals")}
    assert goals == {"Daily active supers": phone["id"], "Mid-size deals closed": bets["Mid-size before enterprise"]["id"]}

    # The market by spend: the definition, segments under its root, each size proposed on its own evidence.
    with api.app.state.store.read() as c:
        definition = MS.definition(c)
    assert definition["includes"] == ["Scheduling, field and project tools for builders"]
    assert definition["excludes"] == ["Design and CAD tools"] and definition["year"] == 2026
    edges = {(r["src"], r["rel"], r["dst"]) for r in rows(api, "SELECT src, rel, dst FROM market_edges")}
    root = definition["root"]
    assert {("segment/gc-suites", "member_of", root), ("segment/schedules", "member_of", "segment/gc-suites"),
            ("segment/bim", "member_of", root), ("segment/specialty-pm", "member_of", root),
            ("company/self", "operates_in", "segment/schedules"), ("company/trestle", "operates_in", "segment/gc-suites"),
            ("company/fieldcraft", "operates_in", "segment/schedules"),
            ("company/trestle", "competes_with", "company/self")} <= edges
    sizes = {r["segment_id"]: r for r in rows(api, "SELECT * FROM market_sizes")}
    assert set(sizes) == {"segment/gc-suites", "segment/schedules", "segment/bim"}      # no evidence, no size
    assert {r["status"] for r in sizes.values()} == {"proposed"}
    assert (sizes["segment/schedules"]["spend_usd"], sizes["segment/schedules"]["method"]) == (400000000, "bottom_up")
    assert sizes["segment/schedules"]["arithmetic"] == "Fictional fixture: 830k users x $40 x 12"
    cited = rows(api, "SELECT source_url FROM market_evidence WHERE id IN (SELECT value FROM json_each(?))",
                 (sizes["segment/bim"]["evidence_ids"],))
    assert cited == [{"source_url": "https://example.com/bim"}]
    assert '"next": true' in rows(api, "SELECT properties FROM market_entities WHERE id='segment/bim'")[0]["properties"]
    # The Growth segments start as Where we play shows them.
    assert {(r["segment_id"], r["status"]) for r in rows(api, "SELECT segment_id, status FROM growth_segments")} == {
        ("segment/schedules", "serving"), ("segment/bim", "next")}
    # Rival sites are watched; the posts wait as new insights for the Librarian's curate.
    assert {(r["entity_id"], r["url"], r["kind"]) for r in rows(api, "SELECT * FROM market_watches WHERE active=1")} == {
        ("company/trestle", "https://example.com/trestle", "home"),
        ("company/fieldcraft", "https://example.com/fieldcraft", "home")}
    insights = rows(api, "SELECT claim, source_url, status FROM market_insights ORDER BY reported_at")
    assert [(i["source_url"], i["status"]) for i in insights] == [("https://news.ycombinator.com/item?id=1", "new"),
                                                                  ("https://example.com/post", "new")]
    record = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
    assert record["strategy"]["skipped"] == [] and len(record["strategy"]["bets"]) == 3


def test_a_0_2_file_without_a_strategy_imports_as_before_and_adds_nothing_to_strategy_or_market(environment):
    api = environment()
    text = company_text()
    assert "version: 0.2\n" in text
    assert "strategy" not in preview_and_import(api, text)
    assert api.get("/api/v2/strategy", headers=signed_in()).json()["bets"] == []
    for table in ("market_entities", "market_sizes", "market_watches", "market_insights"):
        assert rows(api, f"SELECT * FROM {table}") == [], table
    assert {r["bet_id"] for r in rows(api, "SELECT bet_id FROM goals")} <= {None}


def strategy_draft():
    return json.loads((STRATEGY_FILE.parent / "draft.json").read_text())


def test_a_segment_named_like_the_root_gets_its_own_id_and_keeps_its_size_children_and_players(environment):
    api = environment()
    draft = strategy_draft()
    draft["strategy"]["market"]["definition"]["root"] = "GC suites"            # the root set_definition makes first
    preview_and_import(api, C.to_yaml(draft))
    with api.app.state.store.read() as c:
        assert MS.definition(c)["root"] == "segment/gc-suites"
    edges = {(r["src"], r["rel"], r["dst"]) for r in rows(api, "SELECT src, rel, dst FROM market_edges")}
    assert {("segment/gc-suites-2", "member_of", "segment/gc-suites"),
            ("segment/schedules", "member_of", "segment/gc-suites-2"),
            ("company/trestle", "operates_in", "segment/gc-suites-2")} <= edges
    assert "segment/gc-suites-2" in {r["segment_id"] for r in rows(api, "SELECT segment_id FROM market_sizes")}
    assert api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]["strategy"]["skipped"] == []


def test_an_address_with_a_user_name_never_lands_in_market_evidence_whatever_wrote_the_plan(environment):
    """The file's check refuses it; the import drops it too, for a plan that reached it some other way."""
    api = environment()
    from backend import company_import as CI
    strategy = C.validate(strategy_draft())["strategy"]
    secret = "https://alice:pw@example.com/"
    strategy["market"]["players"][0]["url"] = secret + "trestle"
    strategy["market"]["segments"][0]["evidence"] = [secret + "gc"]
    strategy["listening"]["posts"][0]["url"] = secret + "post"
    with api.app.state.store.transaction() as c:
        CI.import_strategy(c, "human:owner", strategy, "Acme Build")
    assert not [r for r in rows(api, "SELECT source_url FROM market_evidence") if "alice" in r["source_url"]]
    assert not [r for r in rows(api, "SELECT source_url FROM market_insights") if "alice" in r["source_url"]]
    assert {r["entity_id"] for r in rows(api, "SELECT entity_id FROM market_watches")} == {"company/fieldcraft"}
    assert "segment/gc-suites" not in {r["segment_id"] for r in rows(api, "SELECT segment_id FROM market_sizes")}
    assert rows(api, "SELECT id FROM market_entities WHERE id='company/trestle'")       # the player still lands


# A made-up company (Acme Build): two sales teams, a sales seat with no team, and one whose team is "sales" in lower
# case.
TEAM_SEATS = [
    ("Lab Partnerships Manager", "sales", "Lab network"),
    ("Lab Outreach Rep", "sdr-research", "Lab network"),
    ("Biopharma Sales Lead", "sales-lead", "Biopharma accounts"),
    ("Biopharma Account Executive", "sales", "Biopharma accounts"),
    ("Renewals Manager", "account-manager", ""),
    ("Inside Sales Rep", "sdr-research", "sales"),
]


def team_text():
    """The services chart cut to the built-ins, with TEAM_SEATS added; the Assistant carries a team too."""
    draft = C.blank()
    draft["company"].update(name="Acme Build", about="We sell lab data to biopharma accounts.",
                            signals=["sells_services"])
    draft = C.Turn(draft).finish()
    draft["bots"] = [b for b in draft["bots"] if b["template"] in ("coo", "botops")]
    turn = C.Turn(draft)
    for title, template, _ in TEAM_SEATS:
        assert turn.apply("bot_add", {"template": template, "title": title, "reason": "Sells lab data."})["ok"], title
    draft = turn.finish()
    names = {title: team for title, _, team in TEAM_SEATS if team}
    for bot in draft["bots"]:
        if bot["title"] in names:
            bot["team"] = names[bot["title"]]
        elif bot["template"] == "coo":
            bot["team"] = "Lab network"
    return C.to_yaml(draft)


def test_two_teams_in_one_department_import_as_two_groups_each_holding_its_own_seats(environment):
    api = environment()
    preview_and_import(api, team_text())
    groups = api.get("/api/v2/groups", headers=signed_in()).json()
    names = [g["name"] for g in groups]
    by_id = {g["id"]: g["name"] for g in groups}
    seats = rows(api, "SELECT title,team FROM seats WHERE closed_at IS NULL")
    placed = {r["title"]: by_id.get(r["team"], "") for r in seats}
    assert placed == {"Lab Partnerships Manager": "Lab network", "Lab Outreach Rep": "Lab network",
                      "Biopharma Sales Lead": "Biopharma accounts", "Biopharma Account Executive": "Biopharma accounts",
                      "Renewals Manager": "Sales", "Inside Sales Rep": "Sales"}
    # One group per name, its id the name's slug, made in file order. A sales seat with no team stays in the Sales group,
    # and a team written "sales" joins that same group instead of making a second one.
    assert names.count("Lab network") == names.count("Biopharma accounts") == names.count("Sales") == 1
    assert {"lab-network", "biopharma-accounts"} <= set(by_id)
    assert names.index("Lab network") < names.index("Biopharma accounts")
    # The Assistant and BotOps are built-ins: outside groups, whatever team the file gives them.
    assert not [r for r in rows(api, "SELECT bot,team FROM bot_config WHERE bot IN ('coo','botops')") if r["team"]]
