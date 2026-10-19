"""The org builder: the local recommender, the catalog HQ is given, and the install's proxy to Tico HQ.

The proxy is a privacy boundary: a department's answer leaves the install only when the person's toggle is on and the
install allows it (not demo, TICO_TELEMETRY, DO_NOT_TRACK or the usage-count setting), and whatever HQ answers is cut to
this install's own template ids.
"""
import json
import re
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
    # Tags boost: an answer about resellers brings the niche card in, and a match outranks a default. Single words only
    # rank: the niche card shows no why, the common one its group's. The head stays first.
    asked = R.rank(built, "sales", "We sell through resellers and partners, and renewals matter")
    assert ids(asked) == ["sales-manager", "partner-scout", "account-manager", "sdr"]
    why = {row["template_id"]: row["why"] for row in asked["bots"]}
    assert why["partner-scout"] == "" and why["account-manager"] == "Common in Sales"
    # A short tag of two words, said whole, is the why.
    assert R.rank(built, "sales", "Our cold email gets no replies")["bots"][1] == {"template_id": "sdr",
                                                                                   "why": "Matches “cold email”"}
    assert "deal-desk" not in ids(asked)                                        # still niche, still not named
    # "What you do" counts too, at half the weight of the answer itself; a consumer-only company sinks a business card.
    assert "deal-desk" in ids(R.rank(built, "sales", "pricing", {"what": "enterprise contracts"}))
    assert ids(R.rank(built, "sales", "pricing contracts"))[1] == "deal-desk"
    assert ids(R.rank(built, "sales", "pricing contracts", {"sells_to": "consumers"}))[-1] == "deal-desk"
    # The same answer gives the same list; an unknown department gives nothing; ids only ever come from the department.
    assert R.rank(built, "sales", "resellers") == R.rank(built, "sales", "resellers")
    assert R.rank(built, "catering", "x") == {"bots": [], "suggested_default": []}
    assert set(ids(R.rank(built, "finance", "invoices sales resellers"))) <= {"controller", "collections"}


def test_a_why_is_never_a_single_word_a_word_of_a_name_or_a_phrase_most_of_the_group_carries():
    """First-run polish (Dev 236): "small property managers" put Matches “managers” on four Sales cards, "booking runs"
    (two summary words) on the SDR, and Matches “github” on most of Engineering. Each still ranks; none is a why."""
    built = R.build({"departments": [{"id": "engineering", "name": "Engineering", "head": "eng-lead"}]}, [
        card("eng-lead", "engineering", lead=True, tags=["github", "github actions", "weekly review"]),
        card("devops", "engineering", name="DevOps Engineer", tags=["github", "flaky-tests", "build times", "pager alert noise"],
             summary="Runs the build and keeps the deploys booking green."),
        card("qa", "engineering", name="QA Engineer", suggest="default", tags=["github", "github actions", "bugs"]),
        card("architect", "engineering", name="Software Architect", tags=["github actions", "design docs"]),
        card("release", "engineering", name="Release Manager", tags=["releases"]),
        card("eng-manager", "engineering", name="Engineering Manager", tags=["github actions", "delivery"],
             suggest="niche"),
        card("tech-writer", "engineering", name="Technical Writer", pains=["the docs runs behind every booking release"])])
    assert R._common([row for row in built["cards"]]) == {"github", "action"}
    asked = R.rank(built, "engineering", "Release managers fight flaky tests and github actions; booking runs late",
                   {"what": "Booking software for property managers"})
    why = {row["template_id"]: row["why"] for row in asked["bots"]}
    assert why == {"eng-lead": "Heads Engineering and reports to you",
                   "devops": "Matches “flaky tests”",            # not the summary's "booking runs"
                   "qa": "A starting point for Engineering",      # "github actions" is on most of the group
                   "architect": "Common in Engineering",
                   "release": "Common in Engineering",            # "managers", "release": words of names
                   "eng-manager": "",                             # niche, brought in by words alone
                   "tech-writer": "Common in Engineering"}        # two words of a long pains phrase
    assert ids(asked)[:2] == ["eng-lead", "devops"]               # the words still rank; "eng-manager" is in on them
    assert not any(re.fullmatch(r"Matches “[^ ”]*”", row["why"]) for row in asked["bots"])
    # A reason quotes the tag's words only as a phrase the answer wrote: in order, side by side, a hyphen or a small
    # word between them at most. Scattered, reversed or partial words still rank, but are no reason.
    for answer, said in [("Our flaky-tests block deploys", "Matches “flaky tests”"),
                         ("Tests are flaky again", ""), ("We have flaky deploys but reliable tests", ""),
                         ("Flaky engineering tests", ""), ("The pager alert noise never stops", "Matches “pager alert noise”"),
                         ("Pager noise all night", "")]:
        devops = next(row["why"] for row in R.rank(built, "engineering", answer)["bots"] if row["template_id"] == "devops")
        assert devops == said or not said and not devops.startswith("Matches"), (answer, devops)


def test_the_shipped_catalog_has_a_head_for_every_department_and_the_setup_page_role_list_is_current():
    """Every department's head is a card in it, and the setup page's role list (backend/company_file/roles.json) is what
    scripts/build_catalog_json.py builds today. Tico HQ's copies of the catalog and the engine are built in
    ticoteam/tico-services from a published release, with `--out` (below)."""
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


def test_out_writes_what_tico_hq_serves_into_the_directory_it_is_given(tmp_path):
    """`scripts/build_catalog_json.py --out DIR`, as ticoteam/tico-services' sync job runs it at a published release:
    catalog.json and onboarding-actions.json straight into DIR, from the same `build()` and role list as the server and
    the setup page; the recommender and the draft engine copied with their generated header, so the scrubber HQ runs on
    setup-website attachments is backend/company_file/keys.py unchanged. Its `--check` holds DIR to them."""
    script, out = str(ROOT / "scripts" / "build_catalog_json.py"), tmp_path / "sync"
    roles = (ROOT / "backend" / "company_file" / "roles.json").read_text()
    made = subprocess.run([sys.executable, script, "--out", str(out)], capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    assert (ROOT / "backend" / "company_file" / "roles.json").read_text() == roles            # the repository untouched
    assert {p.name for p in out.iterdir()} == {"catalog.json", "onboarding-actions.json", "recruit_rank.py", "company_file"}
    built = recruit.catalog(SimpleNamespace(catalog_dir=ROOT / "templates" / "catalog"))
    catalog = json.loads((out / "catalog.json").read_text())
    assert catalog["version"] == built["version"] and catalog["departments"] == built["departments"]
    planner = json.loads((out / "onboarding-actions.json").read_text())
    actions = json.loads((ROOT / "backend" / "company_file" / "actions.json").read_text())
    assert planner["roles"] == json.loads(roles)["roles"] and planner["actions"] == actions["actions"]
    assert planner["starter_pack"] == json.loads((ROOT / "backend" / "company_file" / "starter_pack.json").read_text())
    generated = "# Generated by scripts/build_catalog_json.py from backend/%s. Do not edit; edit that file.\n"
    assert (out / "recruit_rank.py").read_text() == generated % "recruit_rank.py" + (ROOT / "backend" / "recruit_rank.py").read_text()
    for source in (ROOT / "backend" / "company_file").glob("*.py"):
        copy = (out / "company_file" / source.name).read_text()
        assert copy == generated % ("company_file/" + source.name) + source.read_text(), source.name
    check = [sys.executable, script, "--out", str(out), "--check"]
    assert subprocess.run(check, capture_output=True).returncode == 0
    (out / "company_file" / "keys.py").write_text("SCRUB = []\n")
    (out / "company_file" / "old.py").write_text("")
    stale = subprocess.run(check, capture_output=True, text=True)
    assert stale.returncode == 1 and "keys.py" in stale.stderr and "old.py" in stale.stderr
    assert subprocess.run([sys.executable, script, "--out", str(out)], capture_output=True).returncode == 0
    assert not (out / "company_file" / "old.py").exists() and subprocess.run(check, capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, script, "--out"], capture_output=True).returncode == 2


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

    # An HQ on the older engine says Matches “<word>”: the install's own why replaces it (none for a niche card).
    hq.reply["json"] = {"bots": [{"template_id": "sales-manager", "why": "Matches “managers”"},
                                 {"template_id": "account-manager", "why": "Matches “renewals”"},
                                 {"template_id": "partner-scout", "why": "Matches “resellers”"},
                                 {"template_id": "sdr", "why": "Books your demos"}]}
    assert {row["template_id"]: row["why"] for row in ask(api).json()["bots"]} == {
        "sales-manager": "Heads Sales and reports to you", "account-manager": "Common in Sales", "partner-scout": "",
        "sdr": "Books your demos"}

    # The person's toggle off: nothing leaves, and the local recommender answers.
    local = ask(api, share=False).json()
    assert len(hq.seen) == 2 and local["source"] == "local" and local["shared"] is False
    assert local["bots"][0]["template_id"] == "sales-manager"

    # HQ down, slow or talking nonsense: the local answer, never an error.
    hq.reply["json"] = httpx.ConnectTimeout("slow")
    fallback = ask(api)
    assert fallback.status_code == 200 and fallback.json()["source"] == "local"
    sent = len(hq.seen)

    # The install's own switches beat the toggle: DO_NOT_TRACK, TICO_TELEMETRY=off, the usage-count setting.
    for name, value, reason in (("DO_NOT_TRACK", "1", "DO_NOT_TRACK"), ("TICO_TELEMETRY", "off", "TICO_TELEMETRY")):
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
