"""A picker export creates the selected team through normal owner-only onboarding."""

import copy
import json
import re
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from backend.tests.helpers.migrations import forget_from, restore_config  # the column back for a rerun from before 0070
from backend import company_file as C
from backend import market_spend as MS
from backend import people_remove, views
from backend.app import create_app
from backend.auth import Identity
from backend.company_file import starter
from backend.store import H, Problem, encode
from backend.tests.helpers.sample_company import (
    OWNER_EMAIL,
    PEOPLE,
    SEED,
    booted,
    environment,
    machine,
    rows,
    settings_for,
    signed_in,
)
from backend import bot_settings as BS


def company_text(
    reverse_builtins=False,
    through_new_parent=False,
    keep=("assistant", "botops", "controller", "accounts-payable-specialist"),
):
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in set(keep)]
    for bot in draft["bots"]:
        bot["start"] = bot["template"] != "accounts-payable-specialist"
        if bot["template"] == "accounts-payable-specialist":
            bot["title"] = "Acme Payables"
            bot["routine"] = "Review the books each week"
        if reverse_builtins and bot["template"] == "assistant":
            bot["reports_to"] = "botops"
        if through_new_parent and bot["template"] in ("assistant", "controller"):
            bot["reports_to"] = "controller" if bot["template"] == "assistant" else "botops"
    return C.to_yaml(draft) + "models:\n  source: gateway\n  by_department:\n    finance: mid\n"


BUILTINS = {
    "assistant",
    "botops",
    "librarian",
    "strategy-owner",
    "general-manager",
    "finance-owner",
    "rules-owner",
    "design-owner",
    "updates-owner",
    "automations-owner",
}
TWELVE = (
    "operations-manager",
    "controller",
    "payroll-specialist",
    "billing-specialist",
    "accounts-receivable-specialist",
    "fpa-analyst",
    "inbound-sales-owner",
    "outbound-sales-owner",
    "growth-manager",
    "content-owner",
    "seo-owner",
    "project-coordinator",
)


def twelve_role_text():
    """A chosen chart of 12 roles under the built-ins: seats under seats three deep (the finance specialists under the
    Controller, under the Operations Manager), a role reporting to the Assistant (Ops), and the rest on the model's own
    lines."""
    draft = C.from_yaml(company_text(keep=("assistant", "botops", *TWELVE)))
    next(b for b in draft["bots"] if b["id"] == "operations-manager")["reports_to"] = "assistant"
    next(b for b in draft["bots"] if b["id"] == "controller")["reports_to"] = "operations-manager"
    return C.to_yaml(draft)


def seat_lines(api):
    """{title: what its seat reports to, by title}: a seat's line to another seat names that seat's title."""
    seats = {r["id"]: r for r in rows(api, "SELECT id,title,reports_to FROM seats WHERE closed_at IS NULL")}
    return {
        r["title"]: (seats[r["reports_to"][5:]]["title"] if r["reports_to"].startswith("seat:") else r["reports_to"])
        for r in seats.values()
    }


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
    assert {e["id"] for e in summary["employees"]} == {"assistant", "botops"}
    assert sorted(s["id"] for s in summary["seats"]) == sorted(TWELVE)
    done = api.post(
        "/api/v2/onboarding/company/import",
        json={"company_yaml": text, "fingerprint": summary["fingerprint"]},
        headers=signed_in(),
    )
    assert done.status_code == 200, done.text

    from backend.company_file import draft as D

    expected = {}
    for bot in draft["bots"]:
        if bot["id"] in TWELVE:
            parent = D.manager_of(draft, bot)
            expected[bot["title"]] = (
                titles[parent] if parent in TWELVE else ("assistant" if parent == "assistant" else "human:owner")
            )
    assert seat_lines(api) == expected and len(expected) == 12
    assert (
        expected[titles["accounts-receivable-specialist"]] == titles["controller"]
        and expected[titles["controller"]] == titles["operations-manager"]
    )
    assert expected[titles["operations-manager"]] == "assistant"
    # Only the built-ins exist as bots, and none of them is Onboarding.
    bots = {r["slug"] for r in rows(api, "SELECT slug FROM bots")}
    assert bots == BUILTINS
    assert all(
        not r["onboarding_state"] or r["onboarding_state"] == "onboarded"
        for r in rows(api, "SELECT onboarding_state FROM bot_config")
    )
    # No BotOps setup task, repository or computer slot for any role.
    assert not rows(api, "SELECT id FROM tasks WHERE title LIKE 'Set up %'")
    assert not rows(
        api,
        "SELECT bot FROM bot_config WHERE bot NOT IN ('assistant','botops','librarian','strategy-owner','general-manager','finance-owner','rules-owner','design-owner','updates-owner','automations-owner')",
    )
    assert not rows(
        api,
        "SELECT bot FROM assignments WHERE bot NOT IN ('assistant','botops','librarian','strategy-owner','general-manager','finance-owner','rules-owner','design-owner','updates-owner','automations-owner')",
    )
    machine(api)
    assert {r["bot"] for r in rows(api, "SELECT bot FROM assignments")} <= BUILTINS
    assert {r["slug"]: r["state"] for r in rows(api, "SELECT slug,state FROM bots")}["botops"] == "active"
    # The org chart shows the 12 roles, each Recruiting, with those lines.
    org = api.get("/api/v2/org", headers=signed_in()).json()
    seats = {s["id"]: s for s in org["seats"]}
    assert len(seats) == 12 and {s["stage"] for s in seats.values()} == {"recruiting"}
    by_title = {s["title"]: s for s in seats.values()}
    assert by_title[titles["payroll-specialist"]]["org_parent"] == "s:" + by_title[titles["controller"]]["id"]
    assert by_title[titles["operations-manager"]]["org_parent"] == "b:assistant"
    assert {b["id"]: b["stage"] for b in org["bots"]}["botops"] == "active"
    # The sidebar's chart reads the same seats.
    people = api.get("/api/humans", headers=signed_in()).json()
    assert {s["title"]: s["org_parent"] for s in people["seats"]} == {
        s["title"]: s["org_parent"] for s in seats.values()
    }
    # Each seat keeps the template the file placed it with: Recruit offers that one alone, whatever its title says.
    templates = {b["title"]: b["template"] for b in draft["bots"] if b["id"] in TWELVE}
    for bot_id in ("controller", "operations-manager"):
        found = api.post(f"/api/v2/seats/{by_title[titles[bot_id]]['id']}/recruit", json={}, headers=signed_in())
        assert found.status_code == 200, found.text
        assert [(c["template"], c["match"]) for c in found.json()["candidates"]] == [
            (templates[titles[bot_id]], "strong")
        ]
        assert found.json()["agents"] == []


@pytest.mark.parametrize("named,line", [(None, "general-manager"), ("assistant", "assistant")])
def test_a_file_that_names_no_manager_for_botops_leaves_it_under_the_general_manager(environment, named, line):
    """Tico Team on rc.7: a company file's BotOps went on the owner's line (the import staged it there) or kept the
    seed's line to the Assistant. It is the General Manager's, like a company with no file, unless the file names its
    manager. The seat count is the preview's: a built-in's own seat is counted with the built-ins."""
    api = environment()
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='assistant' WHERE bot='botops'")  # as the seed roster has it
    draft = C.from_yaml(company_text())
    if named:
        next(b for b in draft["bots"] if b["template"] == "botops")["reports_to"] = named
    text = C.to_yaml(draft)
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    assert {e["id"]: e["reports_to"] for e in summary["employees"]}["botops"] == line
    done = api.post(
        "/api/v2/onboarding/company/import",
        json={"company_yaml": text, "fingerprint": summary["fingerprint"]},
        headers=signed_in(),
    )
    assert done.status_code == 200, done.text
    lines = {r["bot"]: r["reports_to"] for r in rows(api, "SELECT bot, reports_to FROM bot_config")}
    assert lines["botops"] == line
    assert lines["general-manager"] == lines["assistant"] == lines["finance-owner"] == "human:owner"
    for slug in ("librarian", "strategy-owner", "rules-owner", "design-owner", "automations-owner", "updates-owner"):
        assert lines[slug] == "general-manager", slug
    assert done.json()["company_import"]["seats"] == len(summary["seats"])
    # A later start changes no line.
    with api.app.state.store.transaction() as c:
        api.app.state.onboarding.complete_company(c)
    assert {r["bot"]: r["reports_to"] for r in rows(api, "SELECT bot, reports_to FROM bot_config")} == lines


def test_a_seat_with_no_template_yet_is_a_recruiting_seat_like_any_other_role(environment, monkeypatch):
    monkeypatch.setattr(C.draft, "OPEN_SEATS", {"controller"})  # every model seat has a template now
    api = environment()
    text = company_text(keep=("assistant", "botops", "controller", "accounts-payable-specialist"))
    assert "no_bot_yet: true" in text
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    assert {s["id"]: s["reports_to"] for s in summary["seats"]} == {
        "controller": "human:owner",
        "accounts-payable-specialist": "controller",
    }
    assert "open_seats" not in summary["deferred"]


def test_people_who_hold_roles_are_kept_to_invite_and_the_seats_under_them_wait_with_the_owner(environment):
    api = environment()
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    keep = ("assistant", "botops", "payroll-specialist", "accounts-payable-specialist", "controller")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in keep]
    seat = {b["template"]: b["title"] for b in draft["bots"]}
    turn = C.Turn(draft)
    for name, args in (
        ("company_set", {"name": "Acme Finance", "about": "We help small firms manage their books."}),
        ("person_set", {"name": "me", "role": "Founder"}),
        ("seat_fill", {"seat": seat["accounts-payable-specialist"], "name": "Ana"}),
        ("seat_fill", {"seat": seat["controller"], "name": "Ben"}),
        ("goal_add", {"title": "Close the books by day 5", "owner": seat["controller"]}),
        ("goal_add", {"title": "Clean ledgers", "owner": "Ana"}),
    ):
        assert turn.apply(name, args)["ok"], name
    draft = turn.finish()
    next(b for b in draft["bots"] if b["template"] == "payroll-specialist")["reports_to"] = "ana"
    text = C.to_yaml(draft)
    assert "held_by: ana" in text and "held_by: ben" in text
    body = {"company_yaml": text}
    preview = api.post("/api/v2/onboarding/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    summary = preview.json()
    # Ana and Ben hold their roles: no seat and no bot for those. The Payroll Specialist is a seat under Ana, who is no one in
    # Tico until the owner invites her: it reports to the owner meanwhile.
    assert {e["template"] for e in summary["employees"]} == {"assistant", "botops"}
    assert [(s["id"], s["reports_to"], s.get("waits_on")) for s in summary["seats"]] == [
        ("payroll-specialist", "human:owner", "ana")
    ]
    assert [(p["id"], p["name"], p["title"]) for p in summary["people"]] == [
        ("ana", "Ana", seat["accounts-payable-specialist"]),
        ("ben", "Ben", seat["controller"]),
    ]
    assert not {"open_seats", "coworkers"} & set(summary["deferred"])
    assert {g["title"]: g["owner"] for g in summary["goals"]} == {
        "Close the books by day 5": "company",
        "Clean ledgers": "company",
    }
    body["fingerprint"] = summary["fingerprint"]
    done = api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in())
    assert done.status_code == 200, done.text
    assert not rows(
        api, "SELECT slug FROM bots WHERE slug IN ('accounts-payable-specialist','controller','payroll-specialist')"
    )
    finance = rows(api, "SELECT id,reports_to FROM seats")
    assert [r["reports_to"] for r in finance] == ["human:owner"]
    people = done.json()["company_import"]["people"]
    assert [(p["id"], p["seats"]) for p in people] == [("ana", [finance[0]["id"]]), ("ben", [])]
    saved = rows(api, "SELECT value FROM registry_metadata WHERE key='onboarding.company_file'")[0]["value"]
    assert '"person":"ana"' in saved and '"person":"ben"' in saved
    invited_from_setup(api, finance[0]["id"])
    an_older_releases_roster_add_retries_after_the_upgrade(api)
    # A coworker who holds no role still joins in Tico, not by import.
    loose = C.from_yaml(text)
    loose["people"].append(
        {"id": "cara", "name": "Cara", "role": "Designer", "dept": None, "owner": False, "isNew": True}
    )
    with pytest.raises(C.DraftError, match="add other coworkers in Tico"):
        starter.load(C.to_yaml(loose))


def invited_from_setup(api, finance):
    """The owner gives Ana's email and clicks Invite: she is on the roster and the seat that waited with the owner
    reports to her. This install signs in another way, so no invite email can go: the owner is told so, plainly."""
    invite = lambda key, email: api.post(
        "/api/v2/onboarding/company/people/%s/invite" % key, json={"email": email}, headers=signed_in()
    )
    refused = invite("ana", "ana@acme.example")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invite_unavailable"
    assert "Tell Ana to sign in at" in refused.json()["error"]["detail"]
    (ana,) = [
        p for p in api.get("/api/people", headers=signed_in()).json()["people"] if p["email"] == "ana@acme.example"
    ]
    assert rows(api, "SELECT reports_to FROM seats WHERE id=?", (finance,))[0]["reports_to"] == "human:" + ana["id"]
    record = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
    assert [(p["id"], p.get("person"), p["invited_at"]) for p in record["people"]] == [
        ("ana", ana["id"], None),
        ("ben", None, None),
    ]

    # On a Tico account sign-in the issuer sends it: Invite again sends to the person already added, once. A repeat of
    # one request (same Idempotency-Key, ui/app/api.ts) sends nothing more, nor does a copy overlapping it.
    from backend import company_import, invites

    keyed = lambda key, email="someone-else@acme.example": api.post(
        "/api/v2/onboarding/company/people/ana/invite",
        json={"email": email},
        headers={**signed_in(), "Idempotency-Key": key},
    )

    class Issuer:
        name, calls, overlap = "oidc", [], []
        answer = (202, {"status": "sent"})

        def invite(self, email):
            if not self.overlap:
                self.overlap.append(None)
                self.overlap[0] = keyed("k-1")
            self.calls.append(email)
            return self.answer

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
        # A repeat answers from its binding: the roster is not written again.
        with mock.patch.object(company_import, "add_invitee", side_effect=AssertionError("roster written")):
            assert keyed("k-1").status_code == 200 and len(Issuer.calls) == 2
        # A send the issuer refuses is bound too, and its refusal names the address: removal must scrub that.
        Issuer.answer = (202, {"error": "not_sent"})
        try:
            assert keyed("k-3").status_code == 422
        finally:
            Issuer.answer = (202, {"status": "sent"})
        sends = list(Issuer.calls)
        assert invite("nobody", "x@acme.example").status_code == 404
        # Removed since: the seats her invite moved under her are not her history, so Remove is offered, and they go
        # back to the owner removing her.
        assert api.post("/api/v2/people/%s" % ana["id"], json={"left": True}, headers=signed_in()).status_code == 200
        with api.app.state.store.read() as c:
            gone = next(p for p in views.roster(c)["people"] if p["id"] == ana["id"])
            assert people_remove.removable(c, gone) and people_remove.history(c, ana["id"]) == ""
            under = [r[0] for r in c.execute("SELECT id FROM seats WHERE reports_to=?", ("human:" + ana["id"],))]
        assert under
        assert api.delete("/api/v2/people/%s" % ana["id"], headers=signed_in()).status_code == 200
        with api.app.state.store.read() as c:
            assert not c.execute("SELECT 1 FROM seats WHERE reports_to=?", ("human:" + ana["id"],)).fetchone()
            assert all(
                c.execute("SELECT reports_to FROM seats WHERE id=?", (s,)).fetchone()[0].startswith("human:")
                for s in under
            )
        assert ana["id"] not in [p["id"] for p in api.get("/api/people", headers=signed_in()).json()["people"]]
        # Removed, a repeat of a bound key still answers from its binding and never invites her again. The binding keeps
        # neither the address (its hash) nor the refusal text that named it.
        bound = rows(
            api,
            "SELECT key,state,request_hash,error_detail FROM idempotency WHERE binding='invites' AND subject=?",
            (ana["id"],),
        )
        assert {row["state"] for row in bound} == {"completed", "refused"}
        assert all(row["request_hash"] == "" for row in bound)
        assert not [row for row in bound if "acme.example" in (row["error_detail"] or "")]
        (refused_key,) = [row["key"] for row in bound if row["state"] == "refused"]
        with mock.patch.object(company_import, "add_invitee", side_effect=AssertionError("roster written")):
            replayed = api.post(
                "/api/v2/onboarding/company/people/ana/invite",
                json={"email": "ana@acme.example"},
                headers={**signed_in(), "Idempotency-Key": refused_key},
            )
            assert replayed.status_code == 422 and replayed.json()["error"]["code"] == "invite_failed"
            assert replayed.json()["error"]["detail"] == invites.REMOVED
            assert keyed("k-1").status_code == 200 and Issuer.calls == sends
        assert ana["id"] not in [p["id"] for p in api.get("/api/people", headers=signed_in()).json()["people"]]
    finally:
        api.app.state.auth.proxy = None


def an_older_releases_roster_add_retries_after_the_upgrade(api):
    """An older release's onboarding route stored its roster add as an answer under the request's key before it bound
    the invite, so a refusal (or a crash) between the two left an answer with no binding and nothing sent. Migration
    0050 removes it: the retry after the upgrade sends once and answers, and its repeat sends nothing more."""
    import sqlite3
    from contextlib import closing

    from backend.store import digest

    url, key, body = "/api/v2/onboarding/company/people/ben/invite", "legacy-ben", {"email": "ben@acme.example"}
    refused = api.post(url, json=body, headers={**signed_in(), "Idempotency-Key": key})  # no sign-in mail here
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invite_unavailable"
    (ben,) = [p for p in api.get("/api/people", headers=signed_in()).json()["people"] if p["email"] == body["email"]]
    settings = api.app.state.store.settings
    with closing(sqlite3.connect(settings.db_path)) as c, c:
        assert not c.execute("SELECT 1 FROM idempotency WHERE key=?", (key,)).fetchone()
        # The older layout: the answers alone in `idempotency`, the invite bindings in invite_sends.
        c.execute(
            "CREATE TABLE idempotency_legacy(actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL, "
            "request_hash TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL, "
            "PRIMARY KEY(actor,operation,key))"
        )
        c.execute(
            "INSERT INTO idempotency_legacy SELECT actor,operation,key,request_hash,response,created_at "
            "FROM idempotency WHERE binding IS NULL"
        )
        c.execute(
            "CREATE TABLE invite_sends(actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL, "
            "request_hash TEXT NOT NULL, person TEXT NOT NULL, state TEXT NOT NULL, error_code TEXT, "
            "error_detail TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(actor,operation,key))"
        )
        c.execute(
            "INSERT INTO invite_sends SELECT actor,operation,key,request_hash,subject,"
            "CASE state WHEN 'pending' THEN 'sending' WHEN 'completed' THEN 'sent' ELSE state END,"
            "error_code,error_detail,created_at,coalesce(updated_at,created_at) FROM idempotency "
            "WHERE binding='invites'"
        )
        actor = c.execute("SELECT actor FROM invite_sends LIMIT 1").fetchone()[0]
        c.execute(
            "INSERT INTO idempotency_legacy VALUES(?,?,?,?,?,?)",
            (actor, url, key, digest(encode(body)), encode({"id": ben["id"]}), H.now()),
        )
        c.execute("DROP TABLE idempotency")
        c.execute("ALTER TABLE idempotency_legacy RENAME TO idempotency")
        restore_config(c)
        # A database from before the Pipelines v3 cut-over opens only with no task in it (migrations.refuse_old_tasks);
        # the complete company's own (the Bookkeeper's first asks) go.
        c.execute("DELETE FROM tasks")
        forget_from(c, "0050")

    class Issuer:
        name, calls = "oidc", []

        def invite(self, email):
            self.calls.append(email)
            return 202, {"status": "sent"}

    with TestClient(create_app(settings), follow_redirects=False) as upgraded:
        upgraded.app.state.auth.proxy = Issuer()
        for _ in range(2):
            again = upgraded.post(url, json=body, headers={**signed_in(), "Idempotency-Key": key})
            assert again.status_code == 200, again.text
            assert Issuer.calls == ["ben@acme.example"]
        with upgraded.app.state.store.read() as c:
            row = c.execute("SELECT binding,subject,state FROM idempotency WHERE key=?", (key,)).fetchone()
        assert tuple(row) == ("invites", ben["id"], "completed")


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
    assert seat_lines(api) == {"Controller": "human:owner", "Acme Payables": "Controller"}
    templates = rows(api, f"SELECT s.template AS template FROM bot_config bc {BS.JOIN}")
    assert sum(r["template"] == "assistant" for r in templates) == 1
    assert sum(r["template"] == "botops" for r in templates) == 1
    assert "model_choices" in done.json()["company_import"]["deferred"]
    assert "routines" in done.json()["company_import"]["deferred"]
    # A seat's job description is its role's text from the template.
    description = rows(api, "SELECT description FROM seats WHERE title='Acme Payables'")[0]["description"]
    assert description and "Owns:" in description
    saved = rows(api, "SELECT value FROM registry_metadata WHERE key='onboarding.company_file'")[0]
    assert '"finance":"mid"' in saved["value"]
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
    assert not rows(api, "SELECT slug FROM bots WHERE slug='accounts-payable-specialist'")
    assert api.post("/api/v2/onboarding/company/import", json=body).status_code in (401, 403)
    api.app.state.store.settings.test_identities["helper-test"] = Identity(
        "human:helper", "human", email="helper@sample.example"
    )
    assert (
        api.post(
            "/api/v2/onboarding/company/preview",
            json={"company_yaml": body["company_yaml"]},
            headers=signed_in() | {"Authorization": "Bearer helper-test"},
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/v2/onboarding/company/import",
            json=body,
            headers=signed_in() | {"Authorization": "Bearer helper-test"},
        ).status_code
        == 403
    )
    preview = api.post(
        "/api/v2/onboarding/company/preview", json={"company_yaml": body["company_yaml"]}, headers=signed_in()
    ).json()
    body["fingerprint"] = preview["fingerprint"]
    assert api.post("/api/v2/onboarding/company/import", json=body, headers=signed_in()).status_code == 200
    changed = copy.copy(body)
    changed["company_yaml"] = changed["company_yaml"].replace("Acme Finance", "Another Company")
    changed["fingerprint"] = api.post(
        "/api/v2/onboarding/company/preview", json={"company_yaml": changed["company_yaml"]}, headers=signed_in()
    ).json()["fingerprint"]
    assert api.post("/api/v2/onboarding/company/import", json=changed, headers=signed_in()).status_code == 409
    assert api.get("/api/v2/config", headers=signed_in()).json()["company_name"] == "Acme Finance"


def test_import_never_replaces_an_existing_paused_employee(environment):
    api = environment()
    config = {
        "name": "accounts-payable-specialist",
        "status": "paused",
        "instructions": "Keep the existing employee unchanged",
    }
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {**SEED, "accounts-payable-specialist": config}, PEOPLE)
        c.execute(
            'INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)',
            ('accounts-payable-specialist', 'owner', 'emp-bookkeeping'),
        )
        BS.write(c, 'accounts-payable-specialist', config)
    original = rows(
        api, f"SELECT {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bot='accounts-payable-specialist'"
    )
    body = {"company_yaml": company_text()}
    body["fingerprint"] = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in()).json()[
        "fingerprint"
    ]
    assert api.post("/api/v2/setup/company/import", json=body, headers=signed_in()).status_code == 409
    assert (
        rows(api, f"SELECT {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bot='accounts-payable-specialist'")
        == original
    )


# The two axes are independent: the diagonal covers both values of each.
@pytest.mark.parametrize(
    "bootstrap, through_new_parent",
    [(False, False), (True, True)],
    ids=["owner-api-reverse-builtins", "installer-bootstrap-new-parent-chain"],
)
def test_install_bootstrap_lays_out_the_chosen_chart_once_and_never_falls_back_to_sample(
    tmp_path, through_new_parent, bootstrap
):
    from clients.environments import seed_registry

    seed_registry(
        tmp_path,
        "Owner",
        OWNER_EMAIL,
        {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"},
    )
    text = company_text(reverse_builtins=not through_new_parent, through_new_parent=through_new_parent)
    settings = settings_for(tmp_path)
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()
    with TestClient(app) as api:
        preview = api.post("/api/v2/setup/company/preview", json={"company_yaml": text}, headers=signed_in()).json()
        if not bootstrap:
            applied = api.post(
                "/api/v2/setup/company/import",
                json={"company_yaml": text, "fingerprint": preview["fingerprint"]},
                headers=signed_in(),
            )
            assert applied.status_code == 200, applied.text
    settings.company_file_data, settings.sample_company = starter.pack(text), True
    with TestClient(create_app(settings)) as api:
        assert {r["slug"] for r in rows(api, "SELECT slug FROM bots")} == BUILTINS
        assert seat_lines(api) == {
            "Controller": "botops" if through_new_parent else "human:owner",
            "Acme Payables": "Controller",
        }
        parents = {r["bot"]: r["reports_to"] for r in rows(api, "SELECT bot,reports_to FROM bot_config")}
        assert {b["id"]: parents[b["id"]] for b in preview["employees"]} == {
            b["id"]: b["reports_to"] for b in preview["employees"]
        }
        record = api.get("/api/v2/setup", headers=signed_in()).json()
        assert record["company_import_failed"] is None
        assert {slug: choice["reports_to"] for slug, choice in record["selected"].items()} == {
            b["id"]: b["reports_to"] for b in preview["employees"]
        }
        machine(api)
        initial = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
        initial_seats = rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id")
    with TestClient(create_app(settings)) as api:
        assert api.get("/api/v2/setup", headers=signed_in()).json()["company_import"] == initial
        assert rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id") == initial_seats
    # A changed file never replaces a set-up company, and a company whose team is in shows no company file failure.
    settings.company_file_data = starter.pack(company_text().replace("Acme Finance", "Another Company"))
    with TestClient(create_app(settings)) as api:
        assert api.get("/api/v2/setup", headers=signed_in()).json()["company_import_failed"] is None
        assert rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id") == initial_seats


@pytest.mark.parametrize("broken", ["unreadable", "conflicting-id", "empty-file", "oversized-file"])
def test_a_chosen_chart_that_fails_to_import_is_shown_to_the_owner_and_no_sample_takes_its_place(
    tmp_path, monkeypatch, broken
):
    """Spec acceptance 2 (first-v1 rule): the server starts with the built-ins only, nothing of the file is left, no
    sample company is made, and the owner sees why on first run and on Health. A later import that works clears it.
    A file setup copied to the server (TICO_COMPANY_FILE) that is empty, missing or too big is such a failure too."""
    from backend.config import Settings
    from clients.environments import seed_registry

    seed_registry(
        tmp_path,
        "Owner",
        OWNER_EMAIL,
        {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"},
    )
    settings = settings_for(tmp_path)
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()
    good = company_text()
    conflicting = C.from_yaml(good)
    conflicting["bots"] = [b for b in conflicting["bots"] if b["template"] != "assistant"]
    next(b for b in conflicting["bots"] if b["template"] == "accounts-payable-specialist")["id"] = (
        "assistant"  # the Assistant's own slug
    )
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
            read.company_file_data,
            read.company_file_configured,
            read.company_file_error,
        )
        assert settings.company_file_configured and not settings.company_file_data
    else:
        settings.company_file_data = (
            "not a packed company file" if broken == "unreadable" else starter.pack(C.to_yaml(conflicting))
        )
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
        done = api.post(
            "/api/v2/setup/company/import",
            json={"company_yaml": good, "fingerprint": preview["fingerprint"]},
            headers=signed_in(),
        )
        assert done.status_code == 200, done.text
        assert done.json()["company_import_failed"] is None


def test_a_missing_company_file_is_a_configured_chart_with_its_reason_kept(tmp_path, monkeypatch):
    """Settings.from_env: a TICO_COMPANY_FILE that can't be opened (the OSError branch) is still a configured chart, with
    no data and a reason the owner sees; it never starts as some other team."""
    from backend.config import Settings

    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.delenv("TICO_REHEARSAL", raising=False)
    monkeypatch.delenv("TICO_COMPANY_FILE_B64", raising=False)
    monkeypatch.setenv("TICO_COMPANY_FILE", str(tmp_path / "gone.b64"))
    read = Settings.from_env()
    assert read.company_file_configured and not read.company_file_data
    assert "can't be read" in read.company_file_error


def packed_as_is(saved):
    """A plan packed the way HQ hands it to a server (starter.pack), without this release checking it first: a plan an
    older or newer chart wrote."""
    import base64
    import gzip

    return base64.b64encode(gzip.compress(starter.canonical(saved).encode(), mtime=0)).decode("ascii")


def unknown_role_plan(text):
    """`text`'s plan with its Acme Payables role on a template this release doesn't have."""
    saved = starter.load(text)
    next(b for b in saved["bots"] if b["title"] == "Acme Payables")["template"] = "no-such-role"
    return packed_as_is(saved)


def starts(tmp_path):
    """(start(data, configured, error) -> a client on a new app with that company file setting, failed(api))."""
    from clients.environments import seed_registry

    seed_registry(
        tmp_path,
        "Owner",
        OWNER_EMAIL,
        {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"},
    )
    settings = settings_for(tmp_path)
    app = create_app(settings)
    app.state.store.initialize()
    app.state.store.seed()

    def start(data="", configured=True, error=""):
        settings.company_file_data, settings.company_file_configured, settings.company_file_error = (
            data,
            configured,
            error,
        )
        return TestClient(create_app(settings))

    def failed(api):
        return api.get("/api/v2/setup", headers=signed_in()).json()["company_import_failed"]

    return start, failed


def test_a_refused_start_file_names_the_role_for_the_owner_and_logs_the_field(tmp_path, caplog):
    """The reason a start file was refused is the owner's words, naming the role by its title, on the welcome page, on
    Health and in the event; the server log keeps the field the check refused, and neither holds the file's text."""
    start, failed = starts(tmp_path)
    good = company_text()
    reason = "Acme Payables names a role template this version of Tico doesn't have: no-such-role"
    with caplog.at_level("WARNING", logger="backend.company_import"), start(unknown_role_plan(good)) as api:
        assert failed(api)["reason"] == reason
        checks = {row["id"]: row for row in api.get("/api/v2/health", headers=signed_in()).json()["checks"]}
        assert reason in checks["company_import"]["summary"]
        event = rows(api, "SELECT detail FROM events WHERE action='onboarding.company_import_failed'")
        assert [json.loads(e["detail"])["reason"] for e in event] == [reason]
    logged = [r.getMessage() for r in caplog.records if r.name == "backend.company_import"]
    assert any(".template is not one of Tico's role templates" in line for line in logged), logged
    assert not any("Acme" in line or "We help small firms" in line for line in logged), logged


def test_a_company_already_set_up_never_shows_a_company_file_failure_at_start(tmp_path):
    """Tico Team's case: a failed start, then the owner uploads a fixed file, while the server's company file setting
    still names the original plan. Later starts leave the team as it is, with no Health line, no event and no welcome
    card, whether that file doesn't load (a template this release lacks), loads but differs from what was imported
    (an older release's slugs, read as today's), or can't be read. A failure kept from before ends at the next start."""
    start, failed = starts(tmp_path)
    good = company_text()
    broken = unknown_role_plan(good)
    # The original plan, on an older release's slug and with one title the owner fixed in the upload: it loads (the
    # old slug reads as today's), but it isn't the file imported.
    older = starter.load(good.replace("Acme Payables", "Payables"))
    next(b for b in older["bots"] if b["title"] == "Payables")["template"] = "accounts-payable"
    older = packed_as_is(older)
    assert starter.unpack(older) != starter.load(good)

    def failures(api):
        return rows(api, "SELECT count(*) AS n FROM events WHERE action='onboarding.company_import_failed'")[0]["n"]

    with start(broken) as api:
        assert "Acme Payables" in failed(api)["reason"]
        body = {"company_yaml": good}
        body["fingerprint"] = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in()).json()[
            "fingerprint"
        ]
        done = api.post("/api/v2/setup/company/import", json=body, headers=signed_in())
        assert done.status_code == 200 and done.json()["company_import_failed"] is None and done.json()["completed"]
        seats = rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id")
        kept = failures(api)
    for data, error in (
        (broken, ""),
        (older, ""),
        (starter.pack(good.replace("Acme Finance", "Another Company")), ""),
        ("", "The company file setup copied to this server can't be read (no access)"),
    ):
        with start(data, error=error) as api:
            assert failed(api) is None
            assert "company_import" not in {
                r["id"] for r in api.get("/api/v2/health", headers=signed_in()).json()["checks"]
            }
            assert rows(api, "SELECT id,title,reports_to FROM seats ORDER BY id") == seats
            assert failures(api) == kept
    with start(broken) as api:  # one kept from before (an older release kept them on set-up companies too)
        with api.app.state.store.transaction() as c:
            c.execute(
                "INSERT INTO registry_metadata VALUES(?,?)",
                ("onboarding.company_import_failed", encode({"at": "x", "reason": "old"})),
            )
    with start(broken) as api:
        assert failed(api) is None


def test_hqs_starting_chart_from_an_older_release_imports_at_first_start_on_its_renamed_templates(tmp_path):
    """HQ plans with an older release's chart (rc.6 refused HQ's `pr-reviewer` and `bookkeeper`): the server reads each
    old slug as the template that has the role now. Two old roles that fold into one built-in (the Bookkeeper and a
    Finance Lead) are that one built-in, and a line to either ends at it."""
    from backend.tests.helpers.template_renames import CHARTS, employees_file

    chart = copy.deepcopy(CHARTS["0.6 hotmic"])
    assert {"pr-reviewer", "bookkeeper"} <= {r["template"] for r in chart}
    chart.append(
        {
            "id": "finance-lead",
            "template": "finance-lead",
            "title": "Finance Lead",
            "department": "finance",
            "reports_to": None,
            "team": "Corporate",
            "live": False,
        }
    )
    chart.append(
        {
            "id": "controller",
            "template": "controller",
            "title": "Controller",
            "department": "finance",
            "reports_to": "finance-lead",
            "team": "Corporate",
            "live": False,
        }
    )
    start, failed = starts(tmp_path)
    with start(starter.pack(employees_file(chart), starter.MAX_PACKED)) as api:
        assert failed(api) is None
        seats = {r["title"]: r for r in rows(api, "SELECT title,template,reports_to FROM seats")}
        assert seats["Reviewer 1"]["template"] == seats["Reviewer 2"]["template"] == "reviewer"
        assert seats["Finance Owner"]["template"] == "finance-owner" and "Finance Lead" not in seats
        assert seats["Controller"]["reports_to"] == "finance-owner"
        assert {"reviewer-1", "reviewer-2", "finance-owner"} <= {r["slug"] for r in rows(api, "SELECT slug FROM bots")}
        assert not {r["template"] for r in seats.values()} & {"pr-reviewer", "bookkeeper", "finance-lead"}


def started_tree(api):
    """What a start left: each bot's template and the slug it reports to, each open seat's template, each pipeline's
    manager by slug, and how many bots and seats there are."""
    bots = rows(
        api,
        "SELECT b.id, b.slug, s.template, bc.reports_to FROM bots b JOIN bot_config bc ON bc.bot=b.id "
        "JOIN bot_settings s ON s.bot_id=b.id WHERE b.state<>'archived'",
    )
    slugs = {"bot:" + r["id"]: r["slug"] for r in bots} | {r["id"]: r["slug"] for r in bots}
    return {
        "bots": {r["slug"]: (r["template"], slugs.get(r["reports_to"], r["reports_to"])) for r in bots},
        "seats": sorted(r["template"] for r in rows(api, "SELECT template FROM seats WHERE closed_at IS NULL")),
        "pipelines": {
            r["id"]: slugs.get(r["manager_id"], r["manager_id"])
            for r in rows(api, "SELECT id, manager_id FROM pipelines WHERE retired_at IS NULL")
        },
        "count": (len(bots), rows(api, "SELECT count(*) AS n FROM seats")[0]["n"]),
    }


def test_hqs_chart_with_pipeline_managers_starts_one_manager_per_team_running_its_teams_pipelines(tmp_path):
    """HQ still plans a 0.6 company with the chart that had a Team Manager seat and a Pipeline Manager per pipeline. A
    server started on it has one Manager bot per team, who manages every pipeline of that team (none is left with the
    General Manager standing in); a second start makes nothing; and it is the company a chart drawn in today's shape
    starts."""
    from backend import pipeline_presets_more as more
    from backend.tests.helpers.template_renames import CHARTS, employees_file

    golden = json.loads((Path(__file__).parent / "fixtures" / "chart_formula_golden.json").read_text())
    todays = next(case for case in golden["cases"] if case["name"] == "hotmic")["expected"]["employees"]
    theirs = CHARTS["0.6 hotmic"]
    assert sum(row["template"] == "pipeline-manager" for row in theirs) == 7
    trees = []
    for name, chart in (("theirs", theirs), ("todays", todays)):
        (tmp_path / name).mkdir()
        start, failed = starts(tmp_path / name)
        packed = starter.pack(employees_file(chart), starter.MAX_PACKED)
        with start(packed) as api:
            assert failed(api) is None
            first = started_tree(api)
        with start(packed) as api:
            assert started_tree(api) == first, name  # a later start makes no bot or seat and moves no line
        managers = {slug: row for slug, row in first["bots"].items() if slug.endswith("-manager")}
        assert managers == {
            "general-manager": ("general-manager", managers["general-manager"][1]),
            "product-manager": ("product-manager", "human:owner"),
            "engineering-manager": ("engineering-manager", "human:owner"),
            "growth-manager": ("growth-manager", "human:owner"),
            "success-manager": ("success-manager", "human:owner"),
        }
        assert not [slug for slug in first["bots"] if "pipeline-manager" in slug or "team-manager" in slug]
        assert "pipeline-manager" not in first["seats"] and first["seats"].count("corporate-manager") == 1
        expected = {"product": "product-manager", **more.MANAGERS}
        assert {pid: first["pipelines"][pid] for pid in expected} == expected
        assert "engineering" not in first["pipelines"]  # Product replaced the pair
        lines = {template: above for template, above in first["bots"].values()}
        assert lines["segment-product-owner"] == "product-manager"
        assert first["bots"]["builder"][1] == "engineering-manager"
        trees.append(first)
    assert trees[0] == trees[1]


def test_every_way_out_of_a_failed_setup_import_clears_it(tmp_path):
    """Reverse state of the kept start failure, before setup is complete: the file setting removed and a dismiss
    through the API each end it; a start with the same broken file shows it again."""
    start, failed = starts(tmp_path)
    broken = unknown_role_plan(company_text())
    with start(error="The company file setup copied to this server can't be read (no access)") as api:
        assert "can't be read" in failed(api)["reason"]
        checks = {row["id"]: row for row in api.get("/api/v2/health", headers=signed_in()).json()["checks"]}
        assert checks["company_import"]["status"] == "bad"
    with start(configured=False) as api:  # the file setting is removed
        assert failed(api) is None
    with start(broken) as api:
        assert failed(api)
        dismissed = api.post("/api/v2/setup/company/import-failure/dismiss", json={}, headers=signed_in())
        assert dismissed.status_code == 200 and dismissed.json()["company_import_failed"] is None
        assert not {row["id"] for row in api.get("/api/v2/health", headers=signed_in()).json()["checks"]} & {
            "company_import"
        }
    with start(broken) as api:
        assert failed(api)


def test_reserved_alias_cannot_replace_a_custom_named_assistant_now_or_later(environment):
    api = environment(seed_roster=False, assistant_bot="ace")
    seed = {
        "ace": {"name": "ace", "status": "planned", "template": "assistant"},
        "botops": {"name": "botops", "status": "planned", "template": "botops"},
    }
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, seed, PEOPLE)
        for slug, config in seed.items():
            c.execute('INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)', (slug, 'owner', 'bot-' + slug))
            BS.write(c, slug, config)
    before = rows(api, f"SELECT bot,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} ORDER BY bot")
    draft = C.from_yaml(company_text())
    draft["bots"] = [b for b in draft["bots"] if b["template"] != "assistant"]
    employee = next(b for b in draft["bots"] if b["template"] == "accounts-payable-specialist")
    employee["id"] = "assistant"
    for start in (True, False):
        employee["start"] = start
        body = {"company_yaml": C.to_yaml(draft)}
        preview = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in())
        assert preview.status_code == 422 and "conflicts" in preview.text
        result = api.post("/api/v2/setup/company/import", json={**body, "fingerprint": "0" * 64}, headers=signed_in())
        assert result.status_code == 422 and "conflicts" in result.text
        assert rows(api, f"SELECT bot,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} ORDER BY bot") == before
    assert len(rows(api, f"SELECT bot FROM bot_config bc {BS.JOIN} WHERE s.template='assistant'")) == 1


def test_a_300_bot_company_file_imports_and_a_301_bot_file_is_refused_plainly(environment):
    from backend.tests.helpers.company_file import chart

    api = environment()
    draft = chart(300)
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    text = C.to_yaml(draft)
    assert len(starter.canonical(starter.load(text)).encode()) > 131072  # past the old 128 KB plan limit
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in())
    assert preview.status_code == 200, preview.text
    builtins = sum(b["template"] in BUILTINS for b in draft["bots"])
    assert len(preview.json()["employees"]) == builtins and len(preview.json()["seats"]) == 300 - builtins
    done = api.post(
        "/api/v2/onboarding/company/import",
        json={"company_yaml": text, "fingerprint": preview.json()["fingerprint"]},
        headers=signed_in(),
    )
    assert done.status_code == 200, done.text
    imported = {r["bot"] for r in rows(api, "SELECT bot FROM bot_config")}
    assert all(e["id"] in imported for e in preview.json()["employees"])
    assert len(rows(api, "SELECT id FROM seats WHERE closed_at IS NULL")) == 300 - builtins
    extra = "  - id: one-too-many\n    template: accounts-payable-specialist\n    title: One Too Many\n"
    over = text.replace("employees:\n", "employees:\n" + extra, 1)
    refused = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": over}, headers=signed_in())
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "company_file"


def goals_text(person=True):
    """A company file with a company goal (the owner's), a bot's goal with a target and a range goal not measured yet."""
    draft = C.from_yaml(company_text())
    if person:
        draft["people"] = [{"id": "dana", "name": "Dana Ruiz", "role": "Founder", "dept": "leadership", "owner": True}]
    draft["goals"] = [
        {
            "id": "g-revenue",
            "title": "Reach 40 paying firms",
            "owner": "dana" if person else "you",
            "measure": "Paying firms",
            "unit": "firms",
            "direction": "up",
            "target": 40,
            "baseline": 12,
            "deadline": "2027-01-31",
        },
        {
            "id": "g-close",
            "title": "Close every month's books in five days",
            "owner": "controller",
            "measure": "Days to close the books",
            "unit": "days",
            "direction": "range",
            "min": 0,
            "max": 5,
        },
        {
            "id": "g-later",
            "title": "Answer every bookkeeping question",
            "owner": "accounts-payable-specialist",
            "measure": "",
            "unit": "",
            "direction": "up",
            "target": None,
            "baseline": None,
            "deadline": None,
        },
        # Up, but its target is below its baseline: Tico refuses that target, and the import still lands the goal.
        {
            "id": "g-backwards",
            "title": "Grow referrals",
            "owner": "controller",
            "measure": "Referrals a month",
            "unit": "",
            "direction": "up",
            "target": 5,
            "baseline": 9,
            "deadline": "2027-01-31",
        },
        # A blank title is allowed in the file, never on Goals.
        {
            "id": "g-blank",
            "title": " ",
            "owner": "accounts-payable-specialist",
            "measure": "Questions answered",
            "unit": "",
            "direction": "up",
            "target": None,
            "baseline": None,
            "deadline": None,
        },
    ]
    return C.to_yaml(draft)


@pytest.mark.parametrize(
    "person, bootstrap", [(True, False), (False, True)], ids=["owner-api-named-owner", "installer-bootstrap-you"]
)
def test_imported_goals_show_on_goals_with_their_owner_and_measure(tmp_path, environment, person, bootstrap):
    text = goals_text(person)
    if bootstrap:
        from clients.environments import seed_registry

        seed_registry(
            tmp_path,
            "Owner",
            OWNER_EMAIL,
            {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"},
        )
        settings = settings_for(tmp_path)
        app = create_app(settings)
        app.state.store.initialize()
        app.state.store.seed()
        settings.company_file_data, settings.sample_company = starter.pack(text), True
        with TestClient(create_app(settings)) as api:
            imported_goals_on_goals_page(api, text)
    else:
        api = environment()
        preview = api.post(
            "/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in()
        ).json()
        assert "goals" not in preview["deferred"]
        assert [(g["title"], g["owner"]) for g in preview["goals"]] == [
            ("Reach 40 paying firms", "company"),
            ("Close every month's books in five days", "company"),
            ("Answer every bookkeeping question", "company"),
            ("Grow referrals", "company"),
            ("Questions answered", "company"),
        ]
        done = api.post(
            "/api/v2/onboarding/company/import",
            json={"company_yaml": text, "fingerprint": preview["fingerprint"]},
            headers=signed_in(),
        )
        assert done.status_code == 200, done.text
        imported_goals_on_goals_page(api, text)


def imported_goals_on_goals_page(api, text):
    tree = api.get("/api/v2/goals/tree", headers=signed_in()).json()
    goals = {g["title"]: g for g in tree["goals"]}
    assert {title: g["owner_id"] for title, g in goals.items()} == {
        "Reach 40 paying firms": "company",
        "Close every month's books in five days": "company",
        "Answer every bookkeeping question": "company",
        "Grow referrals": "company",
        "Questions answered": "company",
    }
    # A Recruiting seat holds no work, so the goals the file gives its roles wait with the company.
    assert tree["owners"]["company"]["kind"] == "company" and set(tree["owners"]) == {"company"}
    (revenue,) = goals["Reach 40 paying firms"]["kpis"]
    assert (revenue["name"], revenue["unit"], revenue["direction"]) == ("Paying firms", "firms", "up")
    assert {k: revenue["link"][k] for k in ("kind", "baseline", "target", "deadline")} == {
        "kind": "improve",
        "baseline": 12.0,
        "target": 40.0,
        "deadline": "2027-01-31",
    }
    (close,) = goals["Close every month's books in five days"]["kpis"]
    assert {k: close["link"][k] for k in ("kind", "min", "max")} == {"kind": "maintain", "min": 0.0, "max": 5.0}
    assert goals["Answer every bookkeeping question"]["kpis"] == []
    (backwards,) = goals["Grow referrals"]["kpis"]
    assert (backwards["name"], backwards["link"]["kind"]) == ("Referrals a month", "none")
    # The file holds targets, never readings: no history is made up.
    assert not rows(api, "SELECT id FROM kpi_readings")
    record = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
    assert "goals" not in record["deferred"]
    again = api.post(
        "/api/v2/onboarding/company/import",
        json={"company_yaml": text, "fingerprint": record["fingerprint"]},
        headers=signed_in(),
    )
    assert again.status_code == 200 and len(rows(api, "SELECT id FROM goals")) == 5


@pytest.mark.parametrize("bootstrap", [True], ids=["installer-bootstrap"])
def test_a_renamed_assistants_goals_import_as_company_goals_and_an_untitled_unmeasured_goal_gets_no_kpi(
    tmp_path, environment, bootstrap
):
    # Goals keeps the built-in bots out of its tree, so a goal the file gives the Assistant must land on the company.
    draft = C.from_yaml(company_text())
    draft["goals"] = [
        {
            "id": "g-week",
            "title": "Run the week",
            "owner": "assistant",
            "measure": "Weekly reviews held",
            "unit": "",
            "direction": "up",
            "target": None,
            "baseline": None,
            "deadline": None,
        },
        {
            "id": "g-blank",
            "title": "",
            "owner": "assistant",
            "measure": "",
            "unit": "",
            "direction": "up",
            "target": None,
            "baseline": None,
            "deadline": None,
        },
    ]
    text = C.to_yaml(draft)
    owners = [("Run the week", "company"), ("Untitled goal", "company")]
    if bootstrap:
        from clients.environments import seed_registry

        registry = seed_registry(
            tmp_path,
            "Owner",
            OWNER_EMAIL,
            {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"},
        )
        for name in ("employees.yaml", "people.yaml"):  # an installer run with TICO_ASSISTANT_BOT=ace
            path = registry / name
            path.write_text(re.sub(r"\bassistant\b", "ace", path.read_text()))
        settings = settings_for(tmp_path, assistant_bot="ace")
        app = create_app(settings)
        app.state.store.initialize()
        app.state.store.seed()
        settings.company_file_data, settings.sample_company = starter.pack(text), True
        with TestClient(create_app(settings)) as api:
            renamed_assistant_goals_on_goals_page(api, owners)
    else:
        api = environment(seed_roster=False, assistant_bot="ace")
        seed = {
            "ace": {"name": "ace", "status": "planned", "template": "assistant"},
            "botops": {"name": "botops", "status": "planned", "template": "botops"},
        }
        with api.app.state.store.transaction() as c:
            H.sync_registry(c, seed, PEOPLE)
            for slug, config in seed.items():
                c.execute('INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)', (slug, 'owner', 'bot-' + slug))
                BS.write(c, slug, config)
        preview = api.post(
            "/api/v2/onboarding/company/preview", json={"company_yaml": text}, headers=signed_in()
        ).json()
        assert [(g["title"], g["owner"]) for g in preview["goals"]] == owners
        done = api.post(
            "/api/v2/onboarding/company/import",
            json={"company_yaml": text, "fingerprint": preview["fingerprint"]},
            headers=signed_in(),
        )
        assert done.status_code == 200, done.text
        renamed_assistant_goals_on_goals_page(api, owners)


def renamed_assistant_goals_on_goals_page(api, owners):
    goals = {g["title"]: g for g in api.get("/api/v2/goals/tree", headers=signed_in()).json()["goals"]}
    assert sorted((title, g["owner_id"]) for title, g in goals.items()) == owners
    (week,) = goals["Run the week"]["kpis"]
    assert rows(api, "SELECT owner_id FROM kpis WHERE id=?", (week["id"],)) == [{"owner_id": "company"}]
    assert goals["Untitled goal"]["kpis"] == []
    assert not rows(api, "SELECT id FROM goals WHERE owner_id IN ('bot:ace','bot:assistant')")


@pytest.mark.parametrize("count", [300])
def test_a_full_plan_too_big_for_the_env_starts_the_server_from_its_file(tmp_path, monkeypatch, count):
    """AWS and SSH setup copy a packed plan to a file and TICO_COMPANY_FILE names it (compose.yaml)."""
    from backend.config import Settings
    from backend.tests.helpers.company_file import chart
    from clients.environments import seed_registry

    draft = C.Turn(C.blank()).finish() if count == 72 else chart(count)
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    packed = starter.pack(C.to_yaml(draft), starter.MAX_PACKED)
    assert len(packed) > starter.ENV_PACKED
    with pytest.raises(C.DraftError, match="too large for automatic setup"):
        starter.pack(C.to_yaml(draft))  # the env path's limit is unchanged
    plan = tmp_path / "plan.b64"
    plan.write_text(packed + "\n")
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.delenv("TICO_REHEARSAL", raising=False)
    monkeypatch.delenv("TICO_COMPANY_FILE_B64", raising=False)
    monkeypatch.setenv("TICO_COMPANY_FILE", str(plan))
    assert Settings.from_env().company_file_data == packed
    monkeypatch.setenv("TICO_COMPANY_FILE_B64", "from-the-env")
    assert Settings.from_env().company_file_data == "from-the-env"  # the env still wins (self-install, older setups)
    monkeypatch.delenv("TICO_COMPANY_FILE_B64")

    seed_registry(
        tmp_path,
        "Owner",
        OWNER_EMAIL,
        {"company_name": "Acme Finance", "app_name": "Tico", "assistant_name": "Assistant"},
    )
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
    from backend.tests.helpers.company_file import started

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
        return {
            "id": "goal-%d" % (len(draft["goals"]) + 1),
            "owner": owner,
            "title": (title + ", every single week of the quarter" * 8)[: C.LIMITS["goal_title"]],
            "measure": "m" * C.LIMITS["measure"],
            "unit": "u" * C.LIMITS["unit"],
            "direction": "up",
            "target": 4,
            "baseline": None,
            "deadline": "2027-01-15",
        }

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
    done = api.post(
        "/api/v2/onboarding/company/import",
        json={"company_yaml": text, "fingerprint": preview.json()["fingerprint"]},
        headers=signed_in(),
    )
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


def test_a_329_goal_company_file_imports_and_a_330_goal_file_is_refused_plainly(environment):
    """329 = 3 company goals + 2 for each department + 1 for each of the most bots a chart holds, as tico-services
    allows; one past it is still refused, before anything is created."""
    from backend.company_file import draft as D
    from backend.tests.helpers.company_file import chart

    assert C.LIMITS["goals"] == 329 >= 3 + 2 * len(D.DEPARTMENTS) + C.LIMITS["bots"]
    draft = every_goal(chart(300))
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    assert 20 < len(draft["goals"]) <= C.LIMITS["goals"]  # every goal /goals writes for a full chart
    while len(draft["goals"]) < C.LIMITS["goals"]:  # a visitor's own goals fill the rest
        draft["goals"].append({**draft["goals"][0], "id": "goal-%d" % (len(draft["goals"]) + 1)})
    text = C.to_yaml(draft)
    assert len(C.from_yaml(text)["goals"]) == 329
    assert (
        len(starter.canonical(starter.load(text)).encode()) <= starter.MAX_BYTES
    )  # our largest file fits our own check
    api = environment()
    # Written by hand: our own writer refuses to make a 330-goal file.
    extra = (
        "  - id: goal-330\n    title: One goal too many\n    owner: %s\n    measure: null\n    unit: ''\n"
        "    direction: up\n" % draft["goals"][0]["owner"]
    )
    over = text.replace("\nrules:\n", "\n" + extra + "rules:\n", 1)
    assert over != text
    for path, body in (("preview", {}), ("import", {"fingerprint": "0" * 64})):
        refused = api.post(
            "/api/v2/onboarding/company/" + path, json={"company_yaml": over, **body}, headers=signed_in()
        )
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "company_file", refused.text
        assert "goals has more than 329 entries" in refused.text
    assert not rows(api, "SELECT id FROM goals")
    preview = preview_and_import(api, text)
    assert len(preview["goals"]) == 329 and len(rows(api, "SELECT id FROM goals")) == 329


# ------------------------------------------------------------------------------------- the strategy block (format 0.3)
STRATEGY_FILE = Path(__file__).parent / "fixtures" / "company_strategy" / "company.yaml"


def test_a_strategy_block_imports_as_an_unconfirmed_draft_with_its_market_watches_and_insights(environment):
    """The fictional Acme Build file both repos test against: everything the setup page drafted lands for the owner to
    confirm, nothing published, and each goal on the bet it came from."""
    api = environment()
    summary = preview_and_import(api, STRATEGY_FILE.read_text())["strategy"]
    assert [b["id"] for b in summary["bets"]] == ["b1", "b2", "b3"] and summary["sections"][-1] == "watching"
    assert summary["market"] == {
        "root": "Construction management software",
        "segments": 4,
        "sizes": 3,
        "players": ["Trestle", "FieldCraft"],
    }
    assert summary["watches"] == ["https://example.com/trestle", "https://example.com/fieldcraft"]
    assert summary["insights"] == 2

    strategy = api.get("/api/v2/strategy", headers=signed_in()).json()
    draft = strategy["draft"]
    assert draft["summary"].startswith("Win field crews") and draft["vision"].startswith("Every crew")
    assert draft["sections"]["serve"] == (
        "_Unconfirmed: drafted from the company file._\n\nSuperintendents and "
        "foremen at general contractors with 50 to 500 staff."
    )
    assert all(draft["sections"][k].startswith("_Unconfirmed") for k in ("sell", "know", "not"))
    assert draft["sections"]["how"] == ""  # the file has four sections; How we sell is not in it
    assert draft["sections"]["extra"]["watching"] == {
        "title": "Watching",
        "body": "- Competitor changelogs: Trestle, FieldCraft\n- Superintendents asking for a better schedule app",
    }
    assert api.get("/api/v2/strategy/versions", headers=signed_in()).json() == []
    bets = {b["statement"]: b for b in strategy["bets"]}
    assert {b["status"] for b in bets.values()} == {"proposed"} and len(bets) == 3
    phone = bets["Win the superintendent's phone"]
    assert phone["wrong_if"] == {
        "kind": "text",
        "text": "Fewer than 3 in 10 supers open it daily after 60 days",
        "check_by": "2027-01-23",
    }
    assert bets["Photos are the input"]["wrong_if"]["check_by"] > H.now()[:10]  # none in the file: 90 days out
    goals = {r["title"]: r["bet_id"] for r in rows(api, "SELECT title, bet_id FROM goals")}
    assert goals == {
        "Daily active supers": phone["id"],
        "Mid-size deals closed": bets["Mid-size before enterprise"]["id"],
    }

    # The market by spend: the definition, segments under its root, each size proposed on its own evidence.
    with api.app.state.store.read() as c:
        definition = MS.definition(c)
    assert definition["includes"] == ["Scheduling, field and project tools for builders"]
    assert definition["excludes"] == ["Design and CAD tools"] and definition["year"] == 2026
    edges = {
        (r["src"], r["rel"], r["dst"])
        for r in rows(api, "SELECT from_entity_id AS src, link_key AS rel, to_entity_id AS dst FROM entity_links")
    }
    root = definition["root"]
    assert {
        ("segment/gc-suites", "member_of", root),
        ("segment/schedules", "member_of", "segment/gc-suites"),
        ("segment/bim", "member_of", root),
        ("segment/specialty-pm", "member_of", root),
        ("company/self", "operates_in", "segment/schedules"),
        ("organization/trestle", "operates_in", "segment/gc-suites"),
        ("organization/fieldcraft", "operates_in", "segment/schedules"),
        ("organization/trestle", "competes_with", "company/self"),
    } <= edges
    sizes = {r["segment_id"]: r for r in rows(api, "SELECT * FROM market_sizes")}
    assert set(sizes) == {"segment/gc-suites", "segment/schedules", "segment/bim"}  # no evidence, no size
    assert {r["status"] for r in sizes.values()} == {"proposed"}
    assert (sizes["segment/schedules"]["spend_usd"], sizes["segment/schedules"]["method"]) == (400000000, "bottom_up")
    assert sizes["segment/schedules"]["arithmetic"] == "Fictional fixture: 830k users x $40 x 12"
    cited = rows(
        api,
        "SELECT source_url FROM market_evidence WHERE id IN (SELECT value FROM json_each(?))",
        (sizes["segment/bim"]["evidence_ids"],),
    )
    assert cited == [{"source_url": "https://example.com/bim"}]
    assert '"next": true' in rows(api, "SELECT properties FROM entities WHERE id='segment/bim'")[0]["properties"]
    # Rival sites are watched; the posts wait as new insights for the Librarian's curate.
    assert {
        (r["entity_id"], r["url"], r["kind"]) for r in rows(api, "SELECT * FROM entity_watches WHERE active=1")
    } == {
        ("organization/trestle", "https://example.com/trestle", "home"),
        ("organization/fieldcraft", "https://example.com/fieldcraft", "home"),
    }
    insights = rows(api, "SELECT claim, source_url, status FROM market_insights ORDER BY reported_at")
    assert [(i["source_url"], i["status"]) for i in insights] == [
        ("https://news.ycombinator.com/item?id=1", "new"),
        ("https://example.com/post", "new"),
    ]
    record = api.get("/api/v2/setup", headers=signed_in()).json()["company_import"]
    assert record["strategy"]["skipped"] == [] and len(record["strategy"]["bets"]) == 3


def test_a_0_2_file_without_a_strategy_imports_as_before_and_adds_nothing_to_strategy_or_market(environment):
    api = environment()
    text = company_text()
    assert "version: 0.2\n" in text
    assert "strategy" not in preview_and_import(api, text)
    assert api.get("/api/v2/strategy", headers=signed_in()).json()["bets"] == []
    # The company is complete once imported (Onboarding.complete_company): the Bookkeeper's outside obligations name the
    # authority each is owed to (finance/tax.py seed_dues), which is Finance's, not the file's.
    owed = "name='IRS' OR name LIKE '% tax authority'"
    assert rows(api, f"SELECT * FROM entities WHERE NOT ({owed})") == [], "entities"
    for table in ("market_sizes", "entity_watches", "market_insights"):
        assert rows(api, f"SELECT * FROM {table}") == [], table
    assert {r["bet_id"] for r in rows(api, "SELECT bet_id FROM goals")} <= {None}


def strategy_draft():
    return json.loads((STRATEGY_FILE.parent / "draft.json").read_text())


def test_a_segment_named_like_the_root_gets_its_own_id_and_keeps_its_size_children_and_players(environment):
    api = environment()
    draft = strategy_draft()
    draft["strategy"]["market"]["definition"]["root"] = "GC suites"  # the root set_definition makes first
    preview_and_import(api, C.to_yaml(draft))
    with api.app.state.store.read() as c:
        assert MS.definition(c)["root"] == "segment/gc-suites"
    edges = {
        (r["src"], r["rel"], r["dst"])
        for r in rows(api, "SELECT from_entity_id AS src, link_key AS rel, to_entity_id AS dst FROM entity_links")
    }
    assert {
        ("segment/gc-suites-2", "member_of", "segment/gc-suites"),
        ("segment/schedules", "member_of", "segment/gc-suites-2"),
        ("organization/trestle", "operates_in", "segment/gc-suites-2"),
    } <= edges
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
    assert {r["entity_id"] for r in rows(api, "SELECT entity_id FROM entity_watches")} == {"organization/fieldcraft"}
    assert "segment/gc-suites" not in {r["segment_id"] for r in rows(api, "SELECT segment_id FROM market_sizes")}
    assert rows(api, "SELECT id FROM entities WHERE id='organization/trestle'")  # the player still lands


# A made-up company (Acme Build): two sales teams, a sales seat with no team, and one whose team is "sales" in lower
# case.
TEAM_SEATS = [
    ("Lab Partnerships Manager", "lead-generation-owner", "Lab network"),
    ("Lab Outreach Rep", "outbound-sales-owner", "Lab network"),
    ("Biopharma Sales Lead", "sales-operations-manager", "Biopharma accounts"),
    ("Biopharma Account Executive", "inbound-sales-owner", "Biopharma accounts"),
    ("Renewals Manager", "account-manager", ""),
    ("Inside Sales Rep", "outbound-sales-owner", "sales"),
]


def team_text():
    """The services chart cut to the built-ins, with TEAM_SEATS added; the Assistant carries a team too."""
    draft = C.blank()
    draft["company"].update(
        name="Acme Build", about="We sell lab data to biopharma accounts.", signals=["sells_services"]
    )
    draft = C.Turn(draft).finish()
    draft["bots"] = [b for b in draft["bots"] if b["template"] in ("assistant", "botops")]
    turn = C.Turn(draft)
    for title, template, _ in TEAM_SEATS:
        assert turn.apply("bot_add", {"template": template, "title": title, "reason": "Sells lab data."})["ok"], title
    draft = turn.finish()
    names = {title: team for title, _, team in TEAM_SEATS if team}
    for bot in draft["bots"]:
        if bot["title"] in names:
            bot["team"] = names[bot["title"]]
        elif bot["template"] == "assistant":
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
    assert placed == {
        "Lab Partnerships Manager": "Lab network",
        "Lab Outreach Rep": "Lab network",
        "Biopharma Sales Lead": "Biopharma accounts",
        "Biopharma Account Executive": "Biopharma accounts",
        "Renewals Manager": "Sales",
        "Inside Sales Rep": "Sales",
    }
    # One group per name, its id the name's slug, made in file order. A sales seat with no team stays in the Sales group,
    # and a team written "sales" joins that same group instead of making a second one.
    assert names.count("Lab network") == names.count("Biopharma accounts") == names.count("Sales") == 1
    assert {"lab-network", "biopharma-accounts"} <= set(by_id)
    assert names.index("Lab network") < names.index("Biopharma accounts")
    # The Assistant and BotOps are built-ins: whatever team the file gives them, BotOps sits in the General group and
    # the Assistant, which reports to its person, in none.
    teams = {r["bot"]: r["team"] for r in rows(api, "SELECT bot, team FROM bot_config")}
    assert teams["botops"] == "built-in" and not teams["assistant"]


def test_import_keeps_where_the_company_serves_and_each_segments_own_places(environment):
    from backend import onboarding as O

    api = environment()
    draft = strategy_draft()
    draft["company"]["regions"] = ["Worldwide", "united states"]
    draft["strategy"]["market"]["segments"][0]["regions"] = ["Texas", "TEXAS"]
    preview_and_import(api, C.to_yaml(draft))
    with api.app.state.store.read() as c:
        assert O.load(c)["answers"]["regions"] == ["Worldwide", "united states"]
    gc = rows(api, "SELECT properties, summary FROM entities WHERE id='segment/gc-suites'")[0]
    assert gc["summary"] == "Serves: Texas."
    assert "regions" not in json.loads(gc["properties"] or "{}")
    bim = rows(api, "SELECT properties, summary FROM entities WHERE id='segment/bim'")[0]
    assert bim["summary"] == "" and "regions" not in json.loads(bim["properties"] or "{}")


def test_an_import_that_names_no_places_keeps_the_places_already_saved(environment):
    from backend import onboarding as O

    api = environment()
    saved = api.put(
        "/api/v2/onboarding",
        json={
            "names": {"company_name": "Acme", "app_name": "Atlas", "assistant_name": "Morgan"},
            "answers": {"what_we_do": "Kits.", "regions": ["Worldwide", "Texas"]},
            "selected": {},
        },
        headers=signed_in(),
    )
    assert saved.status_code == 200, saved.text
    draft = strategy_draft()
    draft["company"].pop("regions", None)
    preview_and_import(api, C.to_yaml(draft))
    with api.app.state.store.read() as c:
        assert O.load(c)["answers"]["regions"] == ["Worldwide", "Texas"]


# ----------------------------------------------------------------------------- live seats and teams
# (title, template, the title of the seat it reports to or "" for the owner, live, team). A live seat is made as a bot
# that fills it; the QA Owner is a live seat, and the Growth Manager a plain one.
LIVE_SEATS = (
    ("Engineering Manager", "engineering-manager", "", False, "Engineering"),
    ("Lead Builder", "builder", "Engineering Manager", True, "Engineering"),
    ("Code Reviewer", "reviewer", "Lead Builder", True, "Engineering"),
    ("QA Owner", "qa-owner", "Engineering Manager", True, "Engineering"),
    ("Growth Manager", "growth-manager", "", False, "Growth"),
)


def live_text():
    """The services chart cut to the built-ins, with the Finance Owner (the Bookkeeper, in Corporate) and LIVE_SEATS.
    The file puts the Finance Owner's row in as the chart formula does, since a finished draft has none."""
    from backend.company_file import draft as D

    draft = C.blank()
    draft["company"].update(name="Acme Live", about="We build things.", signals=["sells_services"])
    draft = C.Turn(draft).finish()
    bookkeeper = copy.deepcopy(next(b for b in draft["bots"] if b["id"] == "assistant"))
    bookkeeper.update(
        id="finance-owner",
        template="finance-owner",
        title="Finance Owner",
        reason="Keeps the books.",
        team="Corporate",
        lead=False,
        core=True,
    )
    draft["bots"] = [b for b in draft["bots"] if b["template"] in ("assistant", "botops")] + [bookkeeper]
    turn = C.Turn(draft)
    for title, template, _, _, _ in LIVE_SEATS:
        assert turn.apply("bot_add", {"template": template, "title": title, "reason": "Does the work."})["ok"], title
    draft = turn.finish()
    ids = {b["title"]: b["id"] for b in draft["bots"]}
    for title, _, parent, live, team in LIVE_SEATS:
        bot = next(b for b in draft["bots"] if b["title"] == title)
        bot["team"] = team
        if live:
            bot["live"] = True
        bot["reports_to"] = ids[parent] if parent else D.owner_id(draft)
    return C.to_yaml(draft)


def bot_ids(api):
    """{slug: bot id}: a bot made by setup has its own id, and its slug is the chart's key for it."""
    return {r["slug"]: r["id"] for r in rows(api, "SELECT id,slug FROM bots")}


def stored_config(api, slug):
    with api.app.state.store.read() as c:
        return BS.read(c, bot_ids(api)[slug])


def stored_line(api, slug):
    return rows(api, "SELECT reports_to FROM bot_config WHERE bot='%s'" % bot_ids(api)[slug])[0]["reports_to"]


def test_a_live_seat_is_made_as_a_bot_that_fills_its_seat_and_reports_where_its_line_ends(environment):
    """Spec 8.6: a live seat is created as a bot and fills its seat. A bot cannot report to a seat, so a bot under a seat
    reports to the person or bot that seat answers to, and a live seat under a live seat reports to the bot that fills it.
    Each gets its setup task from BotOps, as any picked role does."""
    api = environment()
    preview_and_import(api, live_text())
    ids = bot_ids(api)
    lead, review = stored_config(api, "lead-builder"), stored_config(api, "code-reviewer")
    assert lead["template"] == "builder" and review["template"] == "reviewer"
    assert lead["placed_under"] == "human:owner" and stored_line(api, "lead-builder") == "human:owner"
    assert review["placed_under"] == ids["lead-builder"] and stored_line(api, "code-reviewer") == ids["lead-builder"]
    seats = {r["title"]: r for r in rows(api, "SELECT title,filled_by,closed_at FROM seats")}
    assert seats["Lead Builder"]["filled_by"] == ids["lead-builder"] and seats["Lead Builder"]["closed_at"]
    assert seats["Code Reviewer"]["filled_by"] == ids["code-reviewer"] and seats["Code Reviewer"]["closed_at"]
    assert seats["Growth Manager"]["closed_at"] is None  # a plain seat stays open
    tasks = {r["title"] for r in rows(api, "SELECT title FROM tasks WHERE title LIKE 'Set up %'")}
    assert {
        "Set up Lead Builder from the builder template",
        "Set up Code Reviewer from the reviewer template",
    } <= tasks


def test_live_bots_and_the_finance_owner_sit_in_their_teams_and_the_built_ins_in_the_built_in_group(environment):
    api = environment()
    preview_and_import(api, live_text())
    names = {g["id"]: g["name"] for g in api.get("/api/v2/groups", headers=signed_in()).json()}
    slugs = {r["id"]: r["slug"] for r in rows(api, "SELECT id,slug FROM bots")}
    teams = {slugs[r["bot"]]: names.get(r["team"], "") for r in rows(api, "SELECT bot,team FROM bot_config")}
    assert teams["lead-builder"] == teams["code-reviewer"] == teams["qa-owner"] == "Engineering"
    assert teams["finance-owner"] == "Corporate"  # the Finance Owner is on the chart, not in the General group
    assert (teams["assistant"], teams["botops"]) == ("", "General")
    assert names["built-in"] == "General"
    # The chart's General group lists its members' ids (the UI draws the group from this row).
    built_in = next(g for g in api.get("/api/v2/groups", headers=signed_in()).json() if g["id"] == "built-in")
    ids = bot_ids(api)
    assert ids["botops"] in built_in["bots"]
    assert ids["finance-owner"] not in built_in["bots"] and ids["assistant"] not in built_in["bots"]


def test_a_live_seat_whose_template_Tico_lacks_is_refused_in_the_preview(environment, monkeypatch):
    from backend import onboarding as O

    api = environment()
    real = O.read_cards
    monkeypatch.setattr(
        O, "read_cards", lambda settings: [card for card in real(settings) if card["template"] != "builder"]
    )
    preview = api.post("/api/v2/onboarding/company/preview", json={"company_yaml": live_text()}, headers=signed_in())
    assert preview.status_code == 422 and "live seat needs a template" in preview.text


# ------------------------------------------------------------------ the default chart, on every way a company starts
# The bots a new company starts with: each team's Manager, who runs its pipelines, the Product and Engineering stage
# owners, the QA Owner, and the Legal Owner, who runs Legal.
LIVE_DEFAULT = (
    "product-manager",
    "product-owner",
    "engineering-manager",
    "builder",
    "reviewer-1",
    "reviewer-2",
    "qa-owner",
    "release-owner",
    "growth-manager",
    "success-manager",
    "legal-owner",
)


def default_chart_text():
    """The company file the site writes for a company that picked no segment or bet: the chart formula's default output
    (backend/company_file/chart_formula.py) under the Assistant and BotOps. The Finance Owner row is the Bookkeeper."""
    from backend.company_file import chart_formula as F
    from backend.company_file import draft as D

    draft = C.blank()
    draft["company"].update(name="Acme Default", about="We build things.", signals=["sells_services"])
    draft = C.Turn(draft).finish()
    owner = D.owner_id(draft)
    draft["bots"] = [b for b in draft["bots"] if b["template"] in ("assistant", "botops")]
    for row in F.build({})["employees"]:
        draft["bots"].append(
            {
                **{key: row[key] for key in ("id", "template", "title", "reason", "team", "live")},
                "dept": row["department"],
                "lead": False,
                "core": row["template"] == "finance-owner",
                "start": True,
                "routine": "",
                "tools": [],
                "about": "",
                "runtime": None,
                "existing": False,
                "reports_to": row["reports_to"] or owner,
            }
        )
    return C.to_yaml(draft)


def test_setup_with_no_chart_starts_the_default_chart_and_its_live_bots_once(environment):
    """Review finding 4: a company that finishes setup without a chart (POST /api/v2/onboarding/complete with nothing
    picked) gets the default chart, as the site's file would give it: its live seats as bots, the rest Recruiting.
    Completing again changes nothing."""
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    ids = bot_ids(api)
    assert set(LIVE_DEFAULT) <= set(ids)
    seats = {r["title"]: r for r in rows(api, "SELECT id,title,filled_by,closed_at FROM seats")}
    for title, slug in (
        ("QA Owner", "qa-owner"),
        ("Release Owner", "release-owner"),
        ("Product Owner", "product-owner"),
    ):
        assert seats[title]["filled_by"] == ids[slug], title
    # Each team has one Manager, a bot from the start except Corporate's, which is a seat to fill. Every live seat
    # of a team reports to its Manager.
    for title, slug in (("Product Manager", "product-manager"), ("Growth Manager", "growth-manager")):
        assert seats[title]["filled_by"] == ids[slug], title
    assert seats["Corporate Manager"]["closed_at"] is None and not seats["Corporate Manager"]["filled_by"]
    assert not [title for title in seats if title.endswith(("Team Manager", "Pipeline Manager"))]
    assert stored_line(api, "product-owner") == ids["product-manager"]
    assert {stored_line(api, slug) for slug in ("builder", "reviewer-1", "qa-owner", "release-owner")} == {
        ids["engineering-manager"]
    }
    assert {stored_line(api, slug) for slug in LIVE_DEFAULT if slug.endswith("-manager")} == {"human:owner"}
    before = (len(rows(api, "SELECT id FROM seats")), len(ids))
    again = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert again.status_code == 200, again.text
    assert (len(rows(api, "SELECT id FROM seats")), len(bot_ids(api))) == before


def test_setup_lists_the_default_chart_by_team_and_leaves_out_the_teams_switched_off(environment):
    """Setup's Your team step reads the default chart by team from GET /api/v2/setup and switches teams off with
    `answers.teams_off`: completing then makes no seat of a team that is off, and the rest as before. Chart-agnostic:
    the chart's teams and titles are the chart formula's to change."""
    api = environment()
    view = api.get("/api/v2/setup", headers=signed_in()).json()
    teams = view["default_chart"]
    assert len(teams) >= 2 and all(row["on"] and row["roles"] for row in teams)
    # The built-ins' own team, always on: its manager first and every other built-in in it reporting to it; no role is
    # in two teams, and the Assistant (it reports to its person) is in none.
    general = [row for row in teams if row["locked"]]
    assert len(general) == 1 and general[0] == teams[-1]
    head, *rest = general[0]["roles"]
    assert head["reports_to"] is None and rest and {role["reports_to"] for role in rest} == {head["id"]}
    every = [role["title"] for row in teams for role in row["roles"]]
    assert len(every) == len(set(every))
    assert "assistant" not in {role["id"] for row in teams for role in row["roles"]}
    teams = [row for row in teams if not row["locked"]]
    for row in teams:
        ids = {role["id"] for role in row["roles"]}
        assert all(role["reports_to"] in ids for role in row["roles"] if role["reports_to"])
        assert any(role["reports_to"] is None for role in row["roles"]), "each team has a top seat"
    assert view["machine"]["cloud"] is False
    off, kept = teams[-1], teams[:-1]
    saved = api.put("/api/v2/setup", json={"answers": {"teams_off": [off["team"]]}}, headers=signed_in())
    assert saved.status_code == 200, saved.text
    assert {row["team"]: row["on"] for row in saved.json()["default_chart"]}[off["team"]] is False
    # A client that does not send it keeps it.
    api.put("/api/v2/setup", json={"answers": {"building": "Rentals"}}, headers=signed_in())
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert done.json()["default_chart"] == []
    titles = {r["title"] for r in rows(api, "SELECT title FROM seats")}
    others = {role["title"] for row in kept for role in row["roles"]}
    assert titles & others
    assert not titles & ({role["title"] for role in off["roles"]} - others)


def test_a_default_team_of_built_ins_only_is_locked():
    """A team every seat of which is a built-in is always made: setup cannot switch it off."""
    from backend import company_import

    cards = {"gm": {"required": True}, "ops": {"required": True}, "pm": {"required": False}}
    rows = [
        {"team": "General", "template": "gm"},
        {"team": "General", "template": "ops"},
        {"team": "Product", "template": "pm"},
        {"team": "Corporate", "template": "ops"},
        {"team": "Corporate", "template": "pm"},
    ]
    assert company_import.locked_teams(rows, cards) == {"General"}


def _reviewer_models(api, enabled):
    done = api.put("/api/v2/providers", json={"enabled": enabled}, headers=signed_in())
    assert done.status_code == 200, done.text
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    return {slug: stored_config(api, slug).get("model") for slug in ("reviewer-1", "reviewer-2")}


def test_reviewer_2_runs_codex_when_the_company_has_it(environment):
    """D12: the second review is a different model's. With OpenAI enabled, Reviewer 2 runs Codex."""
    from backend import providers as P

    models = _reviewer_models(environment(), ["anthropic", "openai"])
    assert models["reviewer-2"] == P.recommended("openai")["id"]
    assert models["reviewer-1"] != models["reviewer-2"]


def test_reviewer_2_runs_another_claude_model_when_the_company_has_no_codex(environment):
    from backend import providers as P

    models = _reviewer_models(environment(), ["anthropic"])
    assert models["reviewer-1"] == P.recommended("anthropic")["id"]
    assert P.MODEL_BY_ID[models["reviewer-2"]]["provider"] == "anthropic"
    assert models["reviewer-2"] != models["reviewer-1"]


def test_a_new_company_set_up_with_the_default_chart_on_a_fresh_database_has_its_pipelines_and_its_bots(tmp_path):
    """The setup path every new company runs, below the routes: a fresh database, the roster a new install seeds, and
    the owner completing setup with nothing picked. It ends with General, Product and Engineering (no answer to
    "Software is the product" is yes), the built-ins and the
    default chart's live bots made, the rest as seats, and nothing recorded as failed."""
    from backend.auth import Auth
    from backend.execution import Execution
    from backend.onboarding import Onboarding
    from backend.providers import MODEL_BY_ID
    from backend.settings_admin import SettingsAdmin
    from backend.store import Store

    store = Store(settings_for(tmp_path))
    store.initialize()
    with store.transaction() as c:
        H.sync_registry(c, SEED, PEOPLE)
        c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO NOTHING", (encode(PEOPLE),))
        for slug, config in SEED.items():
            c.execute("INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)", (slug, "owner", "bot-" + slug))
            BS.write(c, slug, config)
    auth = Auth(store)
    execution = Execution(store, auth)
    admin = SettingsAdmin(store, auth, execution, MODEL_BY_ID, lambda *args, **kwargs: None)
    onboarding = Onboarding(store, auth, admin, execution, MODEL_BY_ID)
    with store.transaction() as c:
        onboarding.complete(c, Identity("human:owner", "owner", OWNER_EMAIL))
    with store.read() as c:
        assert [r[0] for r in c.execute("SELECT id FROM pipelines ORDER BY id")] == [
            "customer",
            "finance",  # the default chart's Bookkeeper is live: Finance (private) with it
            "general",
            "growth-experiments",
            "growth-monitoring",  # with Growth Experiments, by the same seat
            "lead",
            "legal",  # and the Legal Owner: Legal (private)
            "opportunity",
            "product",
            "success-experiments",
            "success-monitoring",
        ]
        bots = {r[0] for r in c.execute("SELECT slug FROM bots")}
        assert {"assistant", "botops", "general-manager", *LIVE_DEFAULT} <= bots
        assert c.execute("SELECT count(*) FROM seats WHERE closed_at IS NULL").fetchone()[0] > 0
        assert not c.execute("SELECT action FROM events WHERE action LIKE '%failed%'").fetchall()


def test_setup_leaves_a_company_whose_work_is_already_set_up_alone(environment):
    """The guard: a company with a pipeline of its own (beyond General) gets no default chart."""
    from backend import pipeline_settings

    api = environment()
    with api.app.state.store.transaction() as c:
        stage = {"name": "Take the order", "owner": "assignee", "requirements": [{"kind": "owner_done"}]}
        pipeline_settings.save(c, "human:owner", {"name": "Orders", "prefix": "OR", "stages": [stage]})
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert not set(LIVE_DEFAULT) & set(bot_ids(api))
    assert not rows(api, "SELECT id FROM seats")


def test_the_finance_owner_keeps_its_corporate_seat_and_moves_under_the_corporate_manager_once_hired(environment):
    """Review finding 7: the Bookkeeper is the Finance Owner on the default chart, under the Corporate Manager seat.
    Its seat is filled by the built-in bot (no second bot), and hiring the manager moves the Bookkeeper under it."""
    api = environment()
    preview = preview_and_import(api, default_chart_text())
    assert "finance-owner" not in {s["id"] for s in preview["seats"]}  # it works from day one: not a Recruiting seat
    ids = bot_ids(api)
    assert sorted(slug for slug in ids if slug == "finance-owner" or slug.startswith("finance")) == ["finance-owner"]
    seats = {r["title"]: r for r in rows(api, "SELECT id,title,filled_by,reports_to FROM seats")}
    finance = seats["Finance Owner"]
    assert finance["filled_by"] == ids["finance-owner"]
    assert finance["reports_to"] == "seat:" + seats["Corporate Manager"]["id"]
    assert stored_line(api, "finance-owner") == stored_config(api, "finance-owner")["placed_under"] == "human:owner"
    assert not rows(api, "SELECT id FROM tasks WHERE title LIKE 'Set up Finance Owner%'")
    hired = api.post(
        "/api/v2/seats/%s/hire" % seats["Corporate Manager"]["id"],
        json={"template": "corporate-manager"},
        headers=signed_in(),
    )
    assert hired.status_code == 200, hired.text
    assert stored_line(api, "finance-owner") == hired.json()["bot"]
