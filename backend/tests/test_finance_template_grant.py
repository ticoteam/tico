"""A template's Finance grant at hire (backend/finance/template_grants.py; `finance:` in a card.yaml): the Procurement
Manager is given finance.ap and the company's expenses view once, when an owner or an admin hires it (the add-a-bot
form, a seat, a team build), and its draft purchase order carries its tags. A member's hire gets nothing, even once
Finance is set up. A second start grants nothing, a revoke stays, a hire before Finance is set up is granted once it
is, and another template's bot gets nothing. Fictional Acme data only."""
import itertools
import json

import pytest

from backend import hubdb as H
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (fixture)

OWNER = "human:ana"
AT = "2026-10-15T12:00:00.000000Z"
DAY = "2026-10-15"
MODEL = "gpt-6.1-sol"
_keys = (f"tf-{n}" for n in itertools.count())


def db(api):
    return api.app.state.store


def finance_on(api):
    with db(api).transaction() as c:
        FA.ensure_setup(c, db(api).settings.owner_email)


def start(api):
    """What app start does for the grant: the bots granted now, once Finance is set up."""
    with db(api).transaction() as c:
        return FA.template_ensure(c)


def act(api, who, kind, payload):
    with db(api).transaction() as c:
        return FA.act(c, who, kind, payload, next(_keys), at=AT)


def account(api, code):
    with db(api).read() as c:
        return str(c.execute("SELECT id FROM fin_accounts WHERE code=?", (code,)).fetchone()[0])


def granted(api, slug):
    """The Finance rights a bot holds now, and every view grant made to it, as the books read them."""
    with db(api).read() as c:
        rights = {r["right_name"] for r in c.execute(
            "SELECT right_name FROM fin_rights WHERE teammate=? AND revoked_at IS NULL", ("bot:" + slug,))}
        views = [dict(r) for r in c.execute(
            "SELECT id, what, whose, status FROM fin_view_grants WHERE subject=? ORDER BY created_at",
            ("bot:" + slug,))]
    return rights, views


def providers(api, *enabled):
    current = get(api, "providers")
    assert api.put("/api/v2/providers", headers=headers(), json={
        "enabled": list(enabled), "expected_revision": current["revision"]}).status_code == 200


def add_bot(api, template, token="ana-test"):
    """The add-a-bot form: a person adds a bot from a template (settings_admin.create_bot); the owner by default."""
    return post(api, "bots", {"display_name": template.title(), "description": "A test bot.", "status": "planned",
                              "template": template, "model": MODEL, "effort": "high", "harness": None,
                              "runner_id": None}, token=token)["slug"]


def seat_hire(api, template):
    """A seat filled from a candidate: recruiting.hire, the same create_bot underneath."""
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Procurement Manager", "reports_to": "coo"})
    return post(api, f"seats/{seat['id']}/hire", {"template": template})["slug"]


def team_hire(api, template):
    """A team built from picks: team_build.hire, a seat each then the seat's own hire."""
    providers(api, "anthropic", "openai")
    team = post(api, "groups", {"name": "Operations"})["id"]
    hired = post(api, "team-build/hire", {"team": team, "reports_to": "",
                                          "picks": [{"role": "Procurement Manager", "template": template}]})
    return hired["bots"][0]["slug"]


HIRES = {"form": lambda api: add_bot(api, "procurement"),
         "seat": lambda api: seat_hire(api, "procurement"),
         "team": lambda api: team_hire(api, "procurement")}


def vendor_and_job(api):
    """The owner's set-up for a purchase: a vendor, a shop and a job."""
    shop = str(act(api, OWNER, "location_create", {"name": "Acme North shop", "kind": "store"}).result["location_id"])
    job = str(act(api, OWNER, "project_create", {"name": "Install"}).result["project_id"])
    vendor = str(act(api, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
    return vendor, shop, job


def draft_tagged_po(api, bot):
    """The bot drafts a PO line tagged with the shop and job; its send and an approval over the limit ask the owner."""
    vendor, shop, job = vendor_and_job(api)
    line = {"account_id": account(api, "6100"), "amount": 150_000, "description": "condenser",
            "tags": {"location": shop, "project": job}}
    made = act(api, "bot:" + bot, "po_create", {"vendor_id": vendor, "po_date": DAY, "lines": [line]})
    assert made.status == "committed", made.reason
    po = str(made.result["po_id"])
    with db(api).read() as c:
        assert FR.order(c, OWNER, po)["lines"][0]["tags"] == {"location": shop, "project": job}
    sent = act(api, "bot:" + bot, "po_send", {"po_id": po})
    assert (sent.status, sent.approver) == ("needs_approval", OWNER), sent
    with db(api).transaction() as c:
        H.approval_decide(c, OWNER, str(sent.approval_id), "approved")
        assert FA.approval_decided(c, str(sent.approval_id), AT).status == "committed"
    asked = act(api, "bot:" + bot, "po_approve", {"po_id": po})
    assert (asked.status, asked.approver) == ("needs_approval", OWNER), asked


@pytest.mark.parametrize("path", ["form", "seat", "team"])
def test_the_procurement_manager_gets_finance_ap_and_the_company_expenses_view_however_it_is_hired(api, path):
    finance_on(api)
    slug = HIRES[path](api)
    rights, views = granted(api, slug)
    assert rights == {"finance.ap"}
    assert [(v["what"], v["whose"], v["status"]) for v in views] == [("expenses", "company", "active")]
    draft_tagged_po(api, slug)


def test_a_private_expense_line_stays_out_of_the_bots_view(api):
    finance_on(api)
    slug = add_bot(api, "procurement")
    vendor, shop, job = vendor_and_job(api)
    assert act(api, OWNER, "private_lines_set", {"account_id": account(api, "6100")}).status == "committed"
    line = {"account_id": account(api, "6100"), "amount": 150_000, "description": "condenser",
            "tags": {"location": shop, "project": job}}
    refused = act(api, "bot:" + slug, "po_create", {"vendor_id": vendor, "po_date": DAY, "lines": [line]})
    assert refused.status != "committed" and "in your view" in str(refused.reason), refused


def test_a_second_start_grants_nothing_and_a_revoke_stays_revoked(api):
    finance_on(api)
    slug = add_bot(api, "procurement")
    before = granted(api, slug)
    grant_id = before[1][0]["id"]
    assert start(api) == [] and granted(api, slug) == before
    assert act(api, OWNER, "right_revoke", {"teammate": "bot:" + slug, "right": "finance.ap"}).status == "committed"
    assert act(api, OWNER, "view_revoke", {"grant_id": grant_id}).status == "committed"
    assert start(api) == []
    rights, views = granted(api, slug)
    assert rights == set() and [v["status"] for v in views] == ["revoked"]


def test_a_hire_before_finance_is_set_up_goes_through_and_is_granted_once_it_is(api):
    slug = add_bot(api, "procurement")                      # no Finance book yet: the hire still goes through
    assert granted(api, slug) == (set(), [])
    assert start(api) == []
    finance_on(api)
    assert start(api) == [slug]
    rights, views = granted(api, slug)
    assert rights == {"finance.ap"} and [(v["what"], v["status"]) for v in views] == [("expenses", "active")]
    assert start(api) == []


def test_another_templates_bot_gets_no_finance_grant(api):
    finance_on(api)
    slug = add_bot(api, "accounts-payable")
    assert granted(api, slug) == (set(), [])
    assert start(api) == []


def expense(api):
    """An expense the books hold, posted the way the view tests post one (paid from the bank): a line a member's bot
    must not see. Returns a vendor for the bot's purchase attempt."""
    vendor = str(act(api, OWNER, "contact_create", {"name": "Acme Parts", "kind": "vendor"}).result["contact_id"])
    spent, bank = account(api, "6100"), account(api, "1000")
    with db(api).transaction() as c:
        FA.post_entry(c, {"date": DAY, "source": "usage", "source_id": "tf-expense", "lines": [
            {"account_id": spent, "amount": 150_000, "tags": {}}, {"account_id": bank, "amount": -150_000}]}, at=AT)
    return vendor


def expenses_shown(api, viewer):
    """The expense rows and total a viewer's expenses read shows for October, by team."""
    with db(api).read() as c:
        shown = FR.expenses(c, viewer, "2026-10-01", "2026-10-31", "team", at=AT)
    return shown["rows"], shown["total"]


def test_a_members_hire_gets_no_rights_and_no_view_even_once_finance_is_set_up(api):
    slug = add_bot(api, "procurement", token="cara-test")            # a member, and no Finance book yet
    with db(api).read() as c:
        recorded = c.execute("SELECT actor, detail_json FROM events WHERE action=? AND target=?",
                             ("bot.finance_not_granted", slug)).fetchall()
    assert [(r["actor"], json.loads(r["detail_json"])["creator"]) for r in recorded] == [("human:cara", "human:cara")]
    assert granted(api, slug) == (set(), [])
    finance_on(api)
    assert start(api) == [] and granted(api, slug) == (set(), [])
    vendor = expense(api)
    refused = act(api, "bot:" + slug, "po_create", {"vendor_id": vendor, "po_date": DAY, "lines": [
        {"account_id": account(api, "6100"), "amount": 150_000, "description": "condenser"}]})
    assert refused.status != "committed", refused
    assert expenses_shown(api, "bot:" + slug) == ([], 0)            # the bot's expenses read is refused the books
    assert expenses_shown(api, OWNER)[1] == 150_000


def test_a_member_who_fills_a_seat_under_them_gets_no_rights(api):
    finance_on(api)
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Procurement Manager", "reports_to": "human:cara"})
    slug = post(api, f"seats/{seat['id']}/hire", {"template": "procurement"}, token="cara-test")["slug"]
    assert granted(api, slug) == (set(), [])


def test_a_members_team_build_gets_no_rights(api):
    finance_on(api)
    providers(api, "anthropic", "openai")
    # cara leads the team, so she may add to it
    team = post(api, "groups", {"name": "Operations", "lead": "person:cara", "add": {"people": ["cara"]}})["id"]
    hired = post(api, "team-build/hire", {"team": team, "reports_to": "",
                                          "picks": [{"role": "Procurement Manager", "template": "procurement"}]},
                 token="cara-test")
    assert granted(api, hired["bots"][0]["slug"]) == (set(), [])


def test_an_admins_hire_is_granted_like_an_owners(api):
    finance_on(api)
    slug = add_bot(api, "procurement", token="ben-test")
    rights, views = granted(api, slug)
    assert rights == {"finance.ap"}
    assert [(v["what"], v["whose"], v["status"]) for v in views] == [("expenses", "company", "active")]
    draft_tagged_po(api, slug)


def test_the_card_declares_ap_and_an_expenses_view_and_a_bad_declaration_grants_only_what_is_valid(api, tmp_path):
    catalog = db(api).settings.catalog_dir
    assert FA.template_declared(catalog, "procurement") == {"rights": ["finance.ap"], "view": "expenses"}
    assert FA.template_declared(catalog, "accounts-payable") == {}
    (tmp_path / "odd").mkdir()
    (tmp_path / "odd" / "card.yaml").write_text(
        "template: odd\nfinance:\n  rights: [finance.admin, finance.nope, finance.ap]\n  view: nope\n")
    assert FA.template_declared(tmp_path, "odd") == {"rights": ["finance.ap"], "view": ""}
