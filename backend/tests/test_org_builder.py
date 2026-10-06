"""The org builder: the local recommender, the catalog HQ is given, and the install's proxy to Tico HQ.

The proxy is a privacy boundary: a department's answer leaves the install only when the person's toggle is on and the
install allows it (not demo, TICO_TELEMETRY, DO_NOT_TRACK or the usage-count setting), and whatever HQ answers is cut to
this install's own template ids.
"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from backend import recruit
from backend import recruit_rank as R
from backend.tests.test_onboarding import (ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, as_person,  # noqa: F401
                                           environment, signed_in)

ROOT = Path(__file__).resolve().parents[2]
DEPARTMENTS = {"departments": [
    {"id": "sales", "name": "Sales", "head": "sales-manager", "icon": "trending_up", "question": "What kind of sales?"},
    {"id": "finance", "name": "Finance", "head": "controller", "icon": "account_balance"},
    {"id": "engineering", "name": "Engineering", "head": "eng-lead", "icon": "code", "software_only": True}]}


def card(template, department="", **fields):
    return {"template": template, "name": fields.pop("name", template.replace("-", " ").title()),
            "summary": fields.pop("summary", "Does " + template + " work."), "department": department, **fields}


CARDS = [
    card("sales-manager", "sales", lead=True, suggest="default", icon="groups", summary="Runs the pipeline review."),
    card("sdr", "sales", suggest="default", tags=["cold email", "outbound"], summary="Writes first-touch emails."),
    card("account-manager", "sales", suggest="common", tags=["renewals", "upsell"], summary="Keeps accounts growing."),
    card("partner-scout", "sales", suggest="niche", tags=["partners", "resellers"], summary="Finds resellers."),
    card("deal-desk", "sales", suggest="niche", tags=["contracts", "pricing"], summary="Prices big deals.",
         recommend_when=["sells_to_businesses"]),
    card("controller", "finance", lead=True, summary="Closes the books."),
    card("collections", pack="operations", summary="Chases late invoices."),         # no department: its pack decides
    card("assistant", required=True),                                               # built in: never offered
]


def catalog():
    return R.build(DEPARTMENTS, CARDS)


def ids(answer):
    return [row["template_id"] for row in answer["bots"]]


def test_a_department_starts_with_its_head_and_defaults_and_the_answer_brings_in_a_niche_bot():
    built = catalog()
    assert [row["id"] for row in built["departments"]] == ["sales", "finance", "engineering"]
    assert "assistant" not in {row["template"] for row in built["cards"]}
    plain = R.rank(built, "sales", "")
    # The head first, then `default`, then `common`; a `niche` card waits under More until the answer names it.
    assert ids(plain) == ["sales-manager", "sdr", "account-manager"]
    assert plain["suggested_default"] == ["sales-manager", "sdr"]
    assert plain["bots"][0]["why"] == "Heads Sales and reports to you"
    assert plain["bots"][2]["why"] == "Common in Sales"
    # Tags boost: an answer about resellers brings the niche card in, and a match outranks a default, with the words it
    # matched as the why. The head stays first.
    asked = R.rank(built, "sales", "We sell through resellers and partners, and renewals matter")
    assert ids(asked) == ["sales-manager", "partner-scout", "account-manager", "sdr"]
    why = {row["template_id"]: row["why"] for row in asked["bots"]}
    assert why["partner-scout"] == "Matches “partners”, “resellers”" and why["account-manager"] == "Matches “renewals”"
    assert "deal-desk" not in ids(asked)                                        # still niche, still not named
    # "What you do" counts too, at half the weight of the answer itself; a consumer-only company sinks a business card.
    assert "deal-desk" in ids(R.rank(built, "sales", "pricing", {"what": "enterprise contracts"}))
    assert ids(R.rank(built, "sales", "pricing contracts"))[1] == "deal-desk"
    assert ids(R.rank(built, "sales", "pricing contracts", {"sells_to": "consumers"}))[-1] == "deal-desk"
    # The same answer gives the same list; an unknown department gives nothing; ids only ever come from the department.
    assert R.rank(built, "sales", "resellers") == R.rank(built, "sales", "resellers")
    assert R.rank(built, "catering", "x") == {"bots": [], "suggested_default": []}
    assert set(ids(R.rank(built, "finance", "invoices sales resellers"))) <= {"controller", "collections"}


def test_the_catalog_takes_the_department_from_the_card_then_the_head_then_the_pack_and_an_icon_always():
    built = catalog()
    rows = {row["template"]: row for row in built["cards"]}
    assert rows["controller"]["department"] == "finance" and rows["controller"]["lead"]
    assert "collections" not in rows                                  # "operations" is not a department in this file
    assert rows["controller"]["icon"] == "account_balance"            # no icon of its own: its department's
    assert rows["controller"]["suggest"] == "common"                  # no `suggest`: shown, not pre-checked
    assert rows["deal-desk"]["business_only"] and not rows["sdr"]["business_only"]
    assert R.build(DEPARTMENTS, CARDS)["version"] == built["version"]
    assert R.build(DEPARTMENTS, CARDS[1:])["version"] != built["version"]


def test_the_shipped_catalog_has_a_head_for_every_department_and_hq_has_a_current_copy():
    """Every department's head is a card in it, and hq/catalog.json and hq/recruit_rank.py are what
    scripts/build_catalog_json.py builds today, with the same version the server computes."""
    settings = SimpleNamespace(catalog_dir=ROOT / "templates" / "catalog")
    built = recruit.catalog(settings)
    departments = yaml.safe_load((ROOT / "templates" / "groups.yaml").read_text())["departments"]
    assert [row["id"] for row in built["departments"]] == [row["id"] for row in departments] == list(R.DEPARTMENT_IDS)
    for dept in built["departments"]:
        assert R.head_of(built, dept["id"]) == dept["head"], dept["id"]
        assert dept["icon"] and dept["question"] and dept["placeholder"], dept["id"]
    assert all(row["icon"] and row["summary"] for row in built["cards"])
    check = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_catalog_json.py"), "--check"],
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert json.loads((ROOT / "hq" / "catalog.json").read_text())["version"] == built["version"]


@pytest.fixture
def hq(monkeypatch):
    """Tico HQ as the install sees it: every request recorded, the answer set per test."""
    seen, reply = [], {"status": 200, "json": {}}

    def handler(request):
        seen.append(json.loads(request.content))
        if isinstance(reply["json"], Exception):
            raise reply["json"]
        return httpx.Response(reply["status"], json=reply["json"])
    monkeypatch.setattr(recruit, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("TICO_HQ_URL", "https://hq.test")
    for name in ("TICO_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(seen=seen, reply=reply)


def org(environment):
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")] + [(row, "") for row in CARDS[:-1]])
    # The older file name is still read (backend/recruit.py).
    (Path(api.app.state.store.settings.catalog_dir).parent / "departments.yaml").write_text(yaml.safe_dump(DEPARTMENTS))
    api.put("/api/v2/onboarding", headers=signed_in(), json={"answers": {
        "what_we_do": "We sell scheduling software to clinics", "customers": "businesses", "software_product": "yes"}})
    return api


def ask(api, share=True, briefing="Mostly resellers", department="sales", who=None):
    return api.post("/api/v2/onboarding/recruit", headers=who or signed_in(),
                    json={"department": department, "briefing": briefing, "share": share})


def test_the_install_sends_an_answer_to_hq_only_when_the_toggle_is_on_and_keeps_only_its_own_ids(environment, hq,
                                                                                                    monkeypatch):
    api = org(environment)
    listing = api.get("/api/v2/onboarding/departments", headers=signed_in()).json()
    assert listing["hq"] == {"available": True, "off_by": ""}
    assert {row["id"] for row in listing["departments"]} == {"sales", "finance", "engineering"}
    assert {"template", "department", "icon", "suggest", "summary", "lead"} <= set(listing["cards"][0])

    # On: exactly the department, the answer, three short facts and the catalog version; nothing else.
    hq.reply["json"] = {"bots": [{"template_id": "partner-scout", "why": "Resellers\nare your channel"},
                                 {"template_id": "controller", "why": "another department"},
                                 {"template_id": "made-up", "why": "not in the catalog"},
                                 {"template_id": "partner-scout", "why": "twice"}],
                        "suggested_default": ["partner-scout", "made-up"]}
    answer = ask(api).json()
    assert hq.seen == [{"department": "sales", "briefing": "Mostly resellers", "catalog_version": listing["version"],
                        "about": {"what": "We sell scheduling software to clinics", "sells_to": "businesses",
                                  "software": True}}]
    assert answer == {"bots": [{"template_id": "partner-scout", "why": "Resellers are your channel"}],
                      "suggested_default": ["partner-scout"], "source": "hq", "shared": True, "off_by": ""}

    # The person's toggle off: nothing leaves, and the local recommender answers.
    local = ask(api, share=False).json()
    assert len(hq.seen) == 1 and local["source"] == "local" and local["shared"] is False
    assert local["bots"][0]["template_id"] == "sales-manager"

    # HQ down, slow or talking nonsense: the local answer, never an error.
    hq.reply["json"] = httpx.ConnectTimeout("slow")
    fallback = ask(api)
    assert fallback.status_code == 200 and fallback.json()["source"] == "local"
    sent = len(hq.seen)

    # The install's own switches beat the toggle: DO_NOT_TRACK, TICO_TELEMETRY=off, the usage-count setting.
    for name, value, reason in (("DO_NOT_TRACK", "1", "DO_NOT_TRACK"),):
        monkeypatch.setenv(name, value)
        off = ask(api).json()
        assert (off["source"], off["shared"], off["off_by"]) == ("local", False, reason)
        assert api.get("/api/v2/onboarding/departments", headers=signed_in()).json()["hq"] == {
            "available": False, "off_by": reason}
        monkeypatch.delenv(name)
    api.app.state.census.set_enabled(False, "human:owner")
    assert ask(api).json()["off_by"] == "setting"
    assert len(hq.seen) == sent

    # Only the owner and bot administrators ask, and only about a real department with a short answer.
    assert ask(api, who=as_person(api, "quinn")).status_code == 403
    # A demo never asks, whatever else is set.
    assert recruit.off_reason(None, SimpleNamespace(demo=True)) == "demo"
