"""Setup starts with strategy (backend/strategy_setup.py, docs/strategy.md#setup): the research job reads the team's
website through the fetch guard and drafts with one model call, never over the owner's own writing; setup keeps the
website and the one line, the org builder links groups to the bets they serve, and finishing setup hands the Librarian
its market task. No network and no model: names resolve through a table, sockets are scripted and the writer is a
fake. Examples use Acme."""

import io
import json
from pathlib import Path

import pytest
import yaml

from backend import strategy_prompt as SP
from backend import strategy_setup as SS
from backend.store import H
from backend.tests.test_api import api, get, post, put  # noqa: F401
from backend.tests.test_onboarding import (ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, LIBRARIAN_CARD,  # noqa: F401
                                           environment, signed_in)
from backend.tests.test_org_builder import CARDS, DEPARTMENTS

PUBLIC = "93.184.216.34"
HOME = (b"<html><head><title>Acme</title></head><body><h1>Rentals run by bots</h1>"
        b"<a href='/company/about-us'>About</a> <a href='https://acme.example/plans'>Plans</a>"
        b"<a href='https://elsewhere.example/customers'>Not ours</a></body></html>")
PAGES = {"/": HOME, "/company/about-us": b"<html><body><p>Two founders, ex-landlords.</p></body></html>",
         "/plans": b"<html><body><p>$29 a unit a month.</p></body></html>",
         "/customers": b"<html><body><p>Landlords with 1-20 units.</p></body></html>"}
FOUND = {"serve": "Small landlords with 1-20 units, who use spreadsheets today.",
         "sell": "Bots that run rentals, at $29 a unit a month.",
         "bets": [{"statement": "Prices low enough to win landlords the incumbents ignore",
                   "why": "Small landlords will not pay enterprise prices.", "rules_out": "White-glove onboarding",
                   "wrong_if": "Fewer than 1 in 10 trials convert"},
                  {"statement": "Landlords trust bots with tenant messages", "why": "", "rules_out": "",
                   "wrong_if": ""}]}


class Net:
    """DNS and sockets: `names` maps a host to its addresses; each connection answers the path it was asked for."""

    def __init__(self, names, pages=PAGES):
        self.names, self.pages, self.asked, self.connected = names, pages, [], []

    def resolver(self, host, port):
        return self.names[host]

    def opener(self, ip, port, tls, host, timeout):
        self.connected.append((ip, host))
        outer, sent = self, []

        class Sock:
            def sendall(self, data):
                sent.append(data.decode())

            def makefile(self, *a, **k):
                path = sent[0].split(" ")[1]
                outer.asked.append((host, path, sent[0]))
                body = outer.pages.get(path)
                status = "200 OK" if body is not None else "404 Not Found"
                body = body if body is not None else b"missing"
                head = f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n\r\n"
                return io.BytesIO(head.encode() + body)

            def close(self):
                pass
        return Sock()


@pytest.fixture
def research(monkeypatch):
    """The job runs inline, the network is a Net and the model a fake that records what it was sent."""
    net = Net({"acme.example": [PUBLIC]})
    sent = []

    def write(system, user):
        sent.append(json.loads(user))
        return FOUND
    state = {"write": write}
    monkeypatch.setattr(SS, "RESOLVER", net.resolver)
    monkeypatch.setattr(SS, "OPENER", net.opener)
    monkeypatch.setattr(SS, "spawn", lambda work: work())
    monkeypatch.setattr(SS, "writer", lambda c, settings, store: state["write"])
    monkeypatch.setenv("GH_TOKEN", "server-secret")
    net.sent, net.state = sent, state
    return net


def test_the_job_reads_the_site_and_writes_unconfirmed_sections_and_proposed_bets(api, research):
    started = post(api, "strategy/draft-from", {"url": "acme.example", "text": "Bots for small landlords"})
    assert started["state"] == "running" and started["url"] == "https://acme.example"
    job = get(api, "strategy/draft-from/" + started["id"])
    assert job == get(api, "strategy/draft-from/latest") and job["state"] == "done", job
    # The home page, then its own about and plans links, then the usual customers address; never another site's.
    assert [path for _, path, _ in research.asked] == ["/", "/company/about-us", "/plans", "/customers"]
    assert {host for _, host in research.connected} == {"acme.example"}
    assert not any("server-secret" in request for _, _, request in research.asked)    # no credential of the server's
    (sent,) = research.sent
    assert sent["owner_words"] == "Bots for small landlords" and len(sent["pages"]) == 4
    assert "$29 a unit" in json.dumps(sent["pages"]) and "text" not in json.dumps(job["pages"])

    view = get(api, "strategy")
    sections = view["draft"]["sections"]
    assert sections["serve"].startswith("_Unconfirmed: drafted from acme.example._") and FOUND["serve"] in sections["serve"]
    assert FOUND["sell"] in sections["sell"] and sections["not"] == ""
    assert job["written"]["sections"] == ["sell", "serve"] and job["written"]["revision"] == view["draft"]["revision"]
    bets = {b["id"]: b for b in view["bets"]}
    assert set(job["written"]["bets"]) == set(bets) and {b["status"] for b in bets.values()} == {"proposed"}
    first = next(b for b in bets.values() if b["rules_out"])
    assert first["wrong_if"]["kind"] == "text" and first["wrong_if"]["check_by"] > H.now()[:10]
    assert get(api, "strategy/versions") == []                                     # nothing is published

    # The same research again: the owner's sections stay theirs and no bet is proposed twice.
    write = put(api, "strategy/draft", {"revision": view["draft"]["revision"], "sections": {"serve": "Mine."}})
    again = get(api, "strategy/draft-from/" + post(api, "strategy/draft-from", {"url": "acme.example"})["id"])
    assert again["state"] == "done" and again["written"]["bets"] == [] and again["written"]["sections"] == []
    assert get(api, "strategy")["draft"]["sections"]["serve"] == write["draft"]["sections"]["serve"] == "Mine."


def fixture_answer():
    """The model's answer for the shared fixture company (fictional Acme Build): the strategy block's summary, four
    sections and bets, as the prompt asks for them (it asks for no vision)."""
    block = json.loads((Path(__file__).parent / "fixtures" / "company_strategy" / "draft.json").read_text())["strategy"]
    return {"summary": block["summary"], **block["sections"],
            "bets": [{k: b[k] for k in SP.BET_FIELDS} for b in block["bets"]]}


def test_the_shared_prompt_drafts_summary_four_sections_and_three_bets_and_no_vision(api, research):
    answer = fixture_answer()
    found = SS.draft(lambda s, u: {**answer, "vision": "Ignored."}, {"text": "", "url": ""}, [])
    assert set(SP.SCHEMA["required"]) == set(answer) == set(found) - {"vision"} and found["vision"] == ""
    assert "vision" not in SP.FIELDS and "vision" not in SP.INSTRUCTIONS
    assert SS.INSTRUCTIONS == SP.INSTRUCTIONS and "Never invent a number" in SP.INSTRUCTIONS
    research.state["write"] = lambda system, user: answer if system == SP.INSTRUCTIONS else pytest.fail(system)
    job = get(api, "strategy/draft-from/" + post(api, "strategy/draft-from", {"text": "Schedules for field crews"})["id"])
    assert job["state"] == "done" and job["written"]["fields"] == ["summary"], job
    assert job["written"]["sections"] == ["know", "not", "sell", "serve"] and len(job["written"]["bets"]) == 3
    draft = get(api, "strategy")["draft"]
    assert draft["summary"] == answer["summary"] and draft["vision"] == ""
    for key in ("serve", "sell", "know", "not"):
        assert draft["sections"][key] == SS.unconfirmed(answer[key], "your description")
    assert {b["status"] for b in get(api, "strategy")["bets"]} == {"proposed"}
    assert get(api, "strategy/versions") == []


def test_the_job_gives_up_and_reports_when_the_owner_wrote_the_draft_meanwhile(api, research):
    def owner_writes(system, user):
        revision = get(api, "strategy")["draft"]["revision"]
        put(api, "strategy/draft", {"revision": revision, "summary": "Acme runs rentals with bots."})
        return FOUND
    research.state["write"] = owner_writes
    job = get(api, "strategy/draft-from/" + post(api, "strategy/draft-from", {"text": "Bots for landlords"})["id"])
    assert job["state"] == "gave_up" and job["error"]["code"] == "edited" and job["written"] is None
    assert job["found"]["serve"] == FOUND["serve"] and len(job["found"]["bets"]) == 2
    view = get(api, "strategy")
    assert view["draft"]["summary"] == "Acme runs rentals with bots." and view["draft"]["sections"]["serve"] == ""
    assert view["bets"] == []


def test_no_model_answers_at_once_and_only_the_owner_starts_or_reads_a_job(api, monkeypatch):
    monkeypatch.setattr(SS, "writer", lambda c, settings, store: None)
    monkeypatch.setattr(SS, "spawn", lambda work: pytest.fail("no job runs without a model"))
    job = post(api, "strategy/draft-from", {"url": "https://acme.example", "text": "Bots for landlords"})
    assert job["state"] == "no_model" and job["finished"] and get(api, "strategy/draft-from/latest")["id"] == job["id"]
    post(api, "strategy/draft-from", {}, expected=422)                               # neither a website nor words
    post(api, "strategy/draft-from", {"text": "x"}, token="ben-test", expected=403)
    get(api, "strategy/draft-from/" + job["id"], token="ben-test", expected=403)
    get(api, "strategy/draft-from/nope", expected=404)


def test_hub_sql_keeps_the_research_job_to_the_owner(api, monkeypatch):
    monkeypatch.setattr(SS, "writer", lambda c, settings, store: None)
    post(api, "strategy/draft-from", {"text": "Unannounced: bots for landlords"})
    query = {"sql": "SELECT key, value FROM registry_metadata WHERE key=?", "params": [SS.KEY]}
    assert "Unannounced" in post(api, "sql", query)["rows"][0][1]
    assert post(api, "sql", query, token="ben-test")["rows"] == []                  # a bot administrator


def test_hub_sql_keeps_an_imported_company_files_unpublished_strategy_to_the_owner(api):
    from backend import company_import
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  (company_import.PLAN_KEY, json.dumps({"strategy": {"bets": [{"statement": "Unannounced bet"}]}})))
    query = {"sql": "SELECT value FROM registry_metadata WHERE key=?", "params": [company_import.PLAN_KEY]}
    assert "Unannounced bet" in post(api, "sql", query)["rows"][0][0]
    assert post(api, "sql", query, token="ben-test")["rows"] == []                  # a bot administrator
    keys = [r[0] for r in post(api, "sql", {"sql": "SELECT key FROM registry_metadata"}, token="ben-test")["rows"]]
    assert "people" in keys and SS.KEY not in keys


def test_an_internal_address_is_refused_before_any_connection(api, research):
    for url in ("http://127.0.0.1/", "http://localhost:8080", "http://[::1]/", "ftp://acme.example",
                "http://169.254.169.254/latest/meta-data", "https://acme.example:abc"):
        refused = post(api, "strategy/draft-from", {"url": url}, expected=422)
        assert refused["error"]["code"] == "validation", url
    # A public-looking name that resolves inside: the fetch refuses it on the job, and never connects.
    research.names["intranet.acme.example"] = ["10.0.0.5"]
    job = get(api, "strategy/draft-from/" + post(api, "strategy/draft-from", {"url": "intranet.acme.example"})["id"])
    assert job["state"] == "failed" and job["error"]["code"] == "private_address"
    assert research.connected == [] and research.sent == []


def test_setup_keeps_the_website_and_the_one_line_and_an_older_client_keeps_them(environment):
    api = environment()
    body = {"names": {"company_name": "Acme", "website": " acme.example "},
            "answers": {"building": "Bots that run small rentals.", "what_we_do": "Rentals"}, "selected": {}}
    saved = api.put("/api/v2/onboarding", json=body, headers=signed_in()).json()
    assert saved["names"]["website"] == "https://acme.example" and saved["answers"]["building"] == "Bots that run small rentals."
    older = api.put("/api/v2/onboarding", json={"names": {"company_name": "Acme"}, "answers": {"what_we_do": "Rentals"},
                                                "selected": {}}, headers=signed_in()).json()
    assert older["names"]["website"] == "https://acme.example" and older["answers"]["building"] == "Bots that run small rentals."
    assert api.get("/api/v2/strategy", headers=signed_in()).json()["website"] == "https://acme.example"
    bad = api.put("/api/v2/onboarding", json={**body, "names": {"website": "mailto:ana@acme.example"}}, headers=signed_in())
    assert bad.status_code == 422
    # An internal address is refused when saved, as the research refuses it: nothing reaches the Librarian's task.
    for url in ("http://10.0.0.1", "http://169.254.169.254/latest", "http://[::1]/", "intranet.local"):
        bad = api.put("/api/v2/onboarding", json={**body, "names": {"website": url}}, headers=signed_in())
        assert bad.status_code == 422 and bad.json()["error"]["reason"] == "private_address", url
    assert api.get("/api/v2/strategy", headers=signed_in()).json()["website"] == "https://acme.example"


def test_a_run_cut_off_or_left_with_nothing_to_read_stops_and_never_writes(environment, monkeypatch):
    monkeypatch.setattr(SS, "spawn", lambda work: None)                              # the run never answers by itself
    monkeypatch.setattr(SS, "writer", lambda c, settings, store: lambda system, user: FOUND)
    api = environment()
    store = api.app.state.store
    body = {"names": {"company_name": "Acme", "website": ""}, "answers": {"building": "Bots that run small rentals."},
            "selected": {}}
    assert api.put("/api/v2/onboarding", json=body, headers=signed_in()).status_code == 200
    start = lambda: api.post("/api/v2/strategy/draft-from", json={"text": "Bots that run small rentals."},
                             headers=signed_in()).json()
    latest = lambda: api.get("/api/v2/strategy/draft-from/latest", headers=signed_in()).json()
    # The owner clears the website and the line: nothing is left to draft from, so the run stops.
    assert start()["state"] == "running"
    cleared = api.put("/api/v2/onboarding", json={**body, "answers": {"building": ""}}, headers=signed_in())
    assert cleared.status_code == 200, cleared.json()
    assert latest()["state"] == "failed" and latest()["error"]["code"] == "cleared"
    # A restart: no thread of the new process runs it (install stops every running job at start-up).
    start()
    with store.transaction() as c:
        SS.stop_running(c)
    assert latest()["state"] == "failed" and latest()["error"]["code"] == "interrupted"
    # A run past STALE_S was already shown as failed: when it answers at last, it writes nothing.
    job = start()
    with store.transaction() as c:
        SS._put(c, {**SS._find(c, job["id"]), "started": H.shift(H.now(), seconds=-SS.STALE_S - 1)})
        SS.finish(c, job["id"], FOUND)
    assert latest()["state"] == "failed" and latest()["written"] is None
    assert api.get("/api/v2/strategy", headers=signed_in()).json()["bets"] == []


def test_the_org_builder_names_the_bet_a_group_serves_and_setup_stores_it(environment):
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")] + [(row, "") for row in CARDS[:-1]])
    (Path(api.app.state.store.settings.catalog_dir).parent / "groups.yaml").write_text(yaml.safe_dump(DEPARTMENTS))
    ask = lambda: api.post("/api/v2/onboarding/recruit", headers=signed_in(),
                           json={"department": "sales", "briefing": "Founders sell", "share": False}).json()
    assert ask()["bet"] is None                                                       # no bets: as before
    revision = api.get("/api/v2/strategy", headers=signed_in()).json()["draft"]["revision"]
    made = api.post("/api/v2/strategy/bets", headers=signed_in(), json={
        "statement": "Prices low enough to win small clinics", "rules_out": "Enterprise contracts",
        "revision": revision}).json()["bet"]
    api.post("/api/v2/strategy/bets", headers=signed_in(), json={"statement": "Clinics book online"})
    answer = ask()
    assert answer["bet"] == {"id": made["id"], "statement": made["statement"]} and answer["source"] == "local"
    assert "deal-desk" in [row["template_id"] for row in answer["bots"]]             # the pricing bet brings it in
    api.put("/api/v2/onboarding", headers=signed_in(), json={
        "answers": {"departments": ["sales", "finance"], "briefings": {"sales": "Founders sell"}},
        "selected": {"sdr": {"template": "sdr", "display_name": "SDR"},
                     "controller": {"template": "controller", "display_name": "Controller"}}})
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    groups = {g["id"]: g for g in api.get("/api/v2/groups", headers=signed_in()).json()}
    assert groups["sales"]["bet_id"] == made["id"] and groups["finance"]["bet_id"] is None


def test_finishing_setup_with_a_website_files_the_librarians_market_task_once(environment):
    api = environment(seed={}, cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (LIBRARIAN_CARD, "# L\n")])
    api.put("/api/v2/onboarding", headers=signed_in(), json={
        "names": {"company_name": "Acme", "website": "https://acme.example"},
        "answers": {"building": "Bots that run small rentals."}, "selected": {}})
    for _ in range(2):
        assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    with api.app.state.store.read() as c:
        tasks = [dict(r) for r in c.execute("SELECT title, body FROM tasks WHERE owner='bot:librarian'")]
    assert [t["title"] for t in tasks] == ["Set up the market map"]
    assert "https://acme.example" in tasks[0]["body"] and "Bots that run small rentals." in tasks[0]["body"]
    market = api.get("/api/v2/librarian/setup", headers=signed_in()).json()["market"]
    assert market["task"]["id"] and market["answers"]["sell"].startswith("https://acme.example")


def test_finishing_setup_without_a_website_files_no_market_task(environment):
    api = environment(seed={}, cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (LIBRARIAN_CARD, "# L\n")])
    api.put("/api/v2/onboarding", headers=signed_in(), json={"answers": {"building": "A new idea"}, "selected": {}})
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:librarian'").fetchone()


def test_a_bot_administrator_recruits_from_the_published_bets_never_the_draft(api):
    from backend import recruit
    from backend.auth import Identity
    from backend.tests.test_strategy import publish, ready
    ready(api, statement="Small landlords pay for bots")
    publish(api)
    post(api, "strategy/bets", {"statement": "Unannounced: a franchise model"})
    owner, admin = Identity("human:ana", "owner"), Identity("human:riley", "human")
    with api.app.state.store.read() as c:
        assert [b["statement"] for b in recruit.strategy_bets(c, owner)] == [
            "Small landlords pay for bots", "Unannounced: a franchise model"]
        assert [b["statement"] for b in recruit.strategy_bets(c, admin)] == ["Small landlords pay for bots"]
