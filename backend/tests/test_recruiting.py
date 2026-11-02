"""Recruiting a seat (backend/recruiting.py): bot-lifecycle-spec.md section 3, acceptance tests 6 to 8. Test 9 (Team >
Add bot makes a seat first) is the page's: ui/tests/team-groups.cjs."""
import json

import pytest

from backend import recruiting
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.test_api import api, as_member, get, headers, post  # noqa: F401
from backend.tests.test_strategy_bot import as_bot


def strategy_bot(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,effort,state,created_at) "
                  "VALUES('goal-manager','goal-manager','Strategy Bot','','active',?)", (H.now(),))
        c.execute("INSERT OR IGNORE INTO bot_config(bot,config,operator) VALUES('goal-manager',?,'ana')",
                  ('{"runtime":"fake","host":"keeper"}',))
    return as_bot(api, "goal-manager")


def providers(api, *enabled):
    current = get(api, "providers")
    assert api.put("/api/v2/providers", headers=headers(), json={
        "enabled": list(enabled), "expected_revision": current["revision"]}).status_code == 200


def test_org_model_recommendations_are_only_the_published_starter_catalog(api):
    models = get(api, "org-models")["models"]
    software = next(model for model in models if model["model"] == "software")
    assert software["name"] == "Software company"
    assert {seat["team"] for seat in software["seats"]} >= {"engineering", "product"}
    assert any(seat["template"] == "engineering-lead" for seat in software["seats"])
    assert all(set(model) == {"model", "name", "about", "seats"} for model in models)
    assert all("bots" not in model and "people" not in model and "tasks" not in model for model in models)
    from clients import hubtools
    offered = {tool["name"] for tool in hubtools.listing(kind="assistant")}
    assert "hub_org_models" in offered and "hub_assistant_propose" in offered


def test_post_a_job_drafts_a_description_from_the_title_at_once_and_redraft_rewrites_it_once(api, monkeypatch):
    import time
    started = time.monotonic()
    drafted = post(api, "seats/draft", {"title": "Customer support lead", "team": "Support"})
    assert time.monotonic() - started < 1
    assert drafted["source"] == "catalog"
    text = drafted["description"]
    assert "\n\nWhat you'll do\n- " in text and "\n\nWorks in: " in text and not text.startswith("What")
    monkeypatch.setattr(recruiting, "_rewrite_call", lambda *_: (_ for _ in ()).throw(RuntimeError("no key")))
    assert post(api, "seats/draft", {"title": "Qwxz vrrb"}) == {"description": "", "source": "none"}
    # Nothing is stored: drafting is not posting.
    assert get(api, "seats")["seats"] == []

    # Redraft asks the model once, for this title and boss; a failure, or an answer too short to use, keeps the
    # composed text. The first draft never asks.
    asked = []
    def rewrite(composed, title, boss, deadline, gateway=None):
        asked.append((title, boss))
        return "Leads our support desk.\n\nWhat you'll do\n- Answer every ticket the same day"
    monkeypatch.setattr(recruiting, "_rewrite_call", rewrite)
    post(api, "seats/draft", {"title": "Customer support lead"})
    assert asked == []
    again = post(api, "seats/draft", {"title": "Customer support lead", "reports_to": "coo", "redraft": True})
    assert again["source"] == "model" and again["description"].startswith("Leads our support desk.")
    assert asked == [("Customer support lead", get(api, "bots/coo")["display_name"])]
    for broken in (lambda *_: (_ for _ in ()).throw(TimeoutError("slow")), lambda *_: "Too short."):
        monkeypatch.setattr(recruiting, "_rewrite_call", broken)
        assert post(api, "seats/draft", {"title": "Customer support lead", "redraft": True}) == drafted
    post(api, "seats/draft", {"title": ""}, expected=422)

    # Only a person drafts, and the model hears only a manager the caller could post under: Cara, a member, gets her
    # own name sent but not the COO's, which is outside her chain; a name nobody has is dropped too.
    asked.clear()
    monkeypatch.setattr(recruiting, "_rewrite_call", rewrite)
    as_member(api, "ben@acme.example")
    for reports_to in ("coo", "nobody-here", "human:ana"):
        post(api, "seats/draft", {"title": "Customer support lead", "reports_to": reports_to, "redraft": True}, token="cara-test")
    post(api, "seats/draft", {"title": "Customer support lead", "reports_to": "human:cara", "redraft": True}, token="cara-test")
    with api.app.state.store.read() as c:
        cara = H.human(c, "cara")["name"]
    assert asked == [("Customer support lead", "")] * 3 + [("Customer support lead", cara)]
    bot = as_bot(api, "coo")
    assert api.post("/api/v2/seats/draft", headers=bot, json={"title": "Customer support lead"}).status_code == 403


def test_a_draft_is_the_job_the_title_names_or_written_from_the_title_never_another_job(api, monkeypatch):
    """A word of rank or a kind of job ("Engineer", "Analyst") never makes a fit: "Rust engineer" is not the QA Engineer,
    whose name shares only "Engineer". With no template that is the job, the model writes one from the title alone
    (no template text to rewrite), under the same bound; with no model, the box stays empty."""
    asked = []
    def write(composed, title, boss, deadline, gateway=None):
        asked.append((composed, title))
        return "Owns the Rust services.\n\nWhat you'll do\n- Ship one tested change a day"
    monkeypatch.setattr(recruiting, "_rewrite_call", write)
    for title in ("Rust engineer", "iOS developer", "Data analyst"):
        drafted = post(api, "seats/draft", {"title": title})
        assert drafted == {"description": "Owns the Rust services.\n\nWhat you'll do\n- Ship one tested change a day",
                           "source": "model"}
    assert asked == [("", "Rust engineer"), ("", "iOS developer"), ("", "Data analyst")]
    # A title that is a template's job drafts from that template at once, without the model, two-letter work included.
    _, cards = recruiting._recruitable(api.app.state.onboarding)
    for title, template in (("QA engineer", "issue-triage"), ("Security engineer", "security-engineer"),
                            ("Marketing manager", "marketing-lead"), ("Customer support lead", "support-lead"),
                            ("Head of People", "people-lead"), ("Developer Advocates", "developer-advocate"),
                            ("Head of HR", "people-lead"), ("HR generalist", "people-hr"),
                            ("Head of engineering", "engineering-lead"), ("Director of marketing", "marketing-lead"),
                            ("Head of Human Resources", "people-lead"), ("Head of Legal Operations", "legal-ops"),
                            ("Developer relations", "developer-advocate"), ("PR manager", "pr"),
                            ("VP Engineering", "engineering-lead"), ("Head of Compliance", "compliance"),
                            ("Head of Customer Success", "customer-success"), ("Account Director", "account-manager"),
                            ("Human Resources Generalist", "people-hr"), ("Financial Planning Analyst", "spend-watcher"),
                            ("SRE", "incident-scribe"), ("SDR", "sdr-research"),
                            ("Site Reliability Manager", "incident-scribe"),
                            ("Sales Development Specialist", "sdr-research"), ("Public relations manager", "pr"),
                            ("Senior Software Developer", "pr-reviewer"), ("QA engineer (contract)", "issue-triage"),
                            ("Security engineer - remote", "security-engineer"),
                            ("Manager, Engineering", "engineering-manager"), ("Engineer, Security", "security-engineer"),
                            ("Manager, Customer Success", "customer-success"),
                            ("VP, Product Marketing", "product-marketing"), ("Sr. Security Engineer", "security-engineer"),
                            ("Vice President, Product Marketing", "product-marketing"),
                            ("Manager (Engineering)", "engineering-manager"), ("Analyst (Pricing)", "pricing"),
                            ("Software Engineer, Security", "security-engineer"),
                            ("Software Engineer (Security)", "security-engineer"),
                            ("Software Engineer, Site Reliability", "incident-scribe"),
                            ("Engineer (QA) - Remote, US", "issue-triage"),
                            ("Support Representative", "support"), ("Customer Support Rep", "support"),
                            ("Customer Service Agent", "support"),
                            ("Director of Revenue Recognition", "revenue-accountant"),
                            ("Employee Relations Manager", "hr-business-partner")):
        assert recruiting._best_card(cards, title)["template"] == template, title
    # A title ending in a kind of job wants that kind: a designer is not the UX Writer, an assistant not the Account
    # Executive, a machine learning engineer not the Learning and Development Specialist.
    for title in ("UX designer", "Executive assistant", "Machine learning engineer", "Product designer",
                  "Accounting manager", "UX Designer (Contract)", "Rust engineer, platform",
                  "UX Designer II", "UX Designer III"):
        assert recruiting._best_card(cards, title) is None, title
    # Every template's own name, and its plural, is that template's job.
    misses = [card["name"] for card in cards for title in (card["name"], card["name"] + "s")
              if (recruiting._best_card(cards, title) or {}).get("template") != card["template"]]
    assert misses == []
    asked.clear()
    assert post(api, "seats/draft", {"title": "QA engineer"})["source"] == "catalog" and asked == []
    monkeypatch.setattr(recruiting, "_rewrite_call", lambda *_: (_ for _ in ()).throw(TimeoutError("slow")))
    assert post(api, "seats/draft", {"title": "Rust engineer"}) == {"description": "", "source": "none"}

    # Applicants: no template is "strong" for a Rust engineer on "Engineer" alone; the QA Engineer is for a QA engineer.
    rust = post(api, "seats", {"title": "Rust engineer", "reports_to": "coo"})
    assert "strong" not in {c["match"] for c in post(api, f"seats/{rust['id']}/recruit", {})["candidates"]}
    qa = post(api, "seats", {"title": "QA engineer", "reports_to": "coo"})
    first = post(api, f"seats/{qa['id']}/recruit", {})["candidates"][0]
    assert first["template"] == "issue-triage" and first["match"] == "strong"


def test_marketing_and_engineering_titles_draft_from_the_template_that_is_the_job_by_any_of_its_names(api):
    """A job's other names are the job ("CMO", "SDET", "PPC Specialist", "Content Writer"); a word of a whole field is
    not ("Influencer Marketing Manager" is not the Head of Marketing, "Partner Marketing Manager" not the HR Business
    Partner); a job of a kind may be a template named by rank on the work ("Release Engineer" is the Release Manager)."""
    _, cards = recruiting._recruitable(api.app.state.onboarding)
    for title, template in (("CMO", "marketing-lead"), ("Chief Marketing Officer", "marketing-lead"),
                            ("CTO", "engineering-lead"), ("Head of Growth", "marketing-lead"),
                            ("PMM", "product-marketing"), ("Senior Product Marketer", "product-marketing"),
                            ("Content Writer", "content"), ("Copywriter", "content"), ("Blog Writer", "content"),
                            ("Social Media Coordinator", "listening"), ("Events Coordinator", "events"),
                            ("Field Marketing Manager", "events"), ("Lifecycle Marketing Manager", "email-marketing"),
                            ("Retention Marketing Manager", "email-marketing"), ("Marketing Ops Specialist", "marketing-ops"),
                            ("Performance Marketing Manager", "paid-media"), ("Paid Social Manager", "paid-media"),
                            ("PPC Specialist", "paid-media"), ("Communications Manager", "pr"),
                            ("Online Reviews Manager", "reputation"), ("Brand Designer", "brand"),
                            ("SDET", "issue-triage"), ("Software Engineer in Test", "issue-triage"),
                            ("Test Engineer", "issue-triage"), ("AppSec Engineer", "security-engineer"),
                            ("Build Engineer", "devops-engineer"), ("CI/CD Engineer", "devops-engineer"),
                            ("Release Engineer", "release-notes"), ("Developer Relations Engineer", "developer-advocate"),
                            ("DevRel", "developer-advocate"), ("Documentation Engineer", "docs-writer"),
                            ("Code Reviewer", "pr-reviewer"), ("Software Engineer", "pr-reviewer"),
                            ("Tax accountant", "tax"), ("Key account manager", "account-manager"),
                            ("Software Development Engineer", "pr-reviewer"), ("Code Review Engineer", "pr-reviewer"),
                            ("Product Security Engineer", "security-engineer"),
                            ("Product Security Engineer II", "security-engineer"),
                            ("Product Security Engineers", "security-engineer")):
        assert (recruiting._best_card(cards, title) or {}).get("template") == template, title
    for title in ("Influencer Marketing Manager", "Partner Marketing Manager", "Affiliate Marketing Manager",
                  "Marketing Coordinator", "Digital Marketing Manager", "Backend Engineer", "Go Developer",
                  "Data Scientist", "Tech Lead", "Executive assistant", "UX designer"):
        assert recruiting._best_card(cards, title) is None, title


def test_strong_is_the_template_that_is_the_job_and_a_coding_job_has_the_cloud_agents_among_its_applicants(api):
    """Strong is as good a fit by name as the drafted template ("Head of Sales" is the Sales Manager's job, not every
    template that says "Sales"). A job writing code in Engineering lists the cloud coding agents Tico connects: first
    when no template is the job, after the ones that are; no head's template leads a job of a kind."""
    def found(title, description=""):
        seat = post(api, "seats", {"title": title, "reports_to": "coo", "description": description})
        return post(api, f"seats/{seat['id']}/recruit", {})

    sales = found("Head of Sales")
    assert [c["template"] for c in sales["candidates"] if c["match"] == "strong"] == ["sales-lead"]
    assert sales["agents"] == [] and sales["agents_at"] == 1

    rust = found("Rust engineer")
    assert [a["id"] for a in rust["agents"]] == ["claude", "codex"] and rust["agents_at"] == 0
    assert {a["match"] for a in rust["agents"]} == {"strong"} and {a["source"] for a in rust["agents"]} == {"cloud"}
    assert rust["candidates"][0]["department_id"] == "engineering"
    assert rust["candidates"][0]["template"] != "engineering-lead"
    assert all(c["match"] != "strong" for c in rust["candidates"])

    security = found("Security engineer")
    assert security["candidates"][0]["template"] == "security-engineer" and security["agents_at"] == 1
    assert {a["match"] for a in security["agents"]} == {"good"}

    for title in ("Influencer Marketing Manager", "Engineering Manager", "Technical Writer"):
        assert found(title)["agents"] == [], title
    influencer = found("Influencer Marketing Manager")
    assert {c["department_id"] for c in influencer["candidates"][:5]} == {"marketing"}
    assert all(c["match"] != "strong" for c in influencer["candidates"])

    # A cloud agent's add builds from the top template: one that fits, or Engineering's first for a coding seat.
    def top(title):
        seat = post(api, "seats", {"title": title, "reports_to": "coo", "description": ""})
        with api.app.state.store.read() as c:
            row = c.execute("SELECT * FROM seats WHERE id=?", (seat["id"],)).fetchone()
            card = recruiting.top_card(api.app.state.auth, api.app.state.onboarding, c, Identity("human:ana", "owner"), row)
        return card and card["department"]
    assert top("Engineer") == "engineering" and top("Tech Lead") is None


def test_a_model_draft_opens_in_the_catalog_s_voice():
    """However the model opens, the draft reads as a template does: "Owns...", never "I", "You", "AI teammate" or the
    title again."""
    for written, title in (("AI teammate for the Marketing Coordinator role. You own the calendar.", "Marketing Coordinator"),
                           ("AI Teammate, Frontend Engineer: you own the web interface.", "Frontend Engineer"),
                           ("I own the Rust services.", "Rust engineer"), ("AI Engineer owns the AI features.", "AI Engineer"),
                           ("You'll carry the pager.", "SRE"), ("Owns the pipelines.", "Data Engineer")):
        assert recruiting._voice(written + "\n\nWhat you'll do\n- Ship", title).split("\n")[0] in (
            "Owns the calendar.", "Owns the web interface.", "Owns the Rust services.", "Owns the AI features.",
            "Carries the pager.", "Owns the pipelines."), written
    assert recruiting._voice("You are the owner.", "X") == "You are the owner."


def test_without_a_gemini_key_a_draft_is_written_on_the_tico_gateway_or_stays_empty(api, monkeypatch):
    """A hosted install has no Gemini key: the title's draft is written on the Tico AI Gateway with the company's key,
    and with no gateway key it stays empty. Gemini still answers first when it has a key. The network is stubbed: the
    Gemini lookup raises or answers, and the Gateway's answer is canned."""
    import io
    from clients import docs_qa
    from clients import gateway as G
    asked = []
    def gateway_answer(base, key, body, deadline):
        asked.append((base, key, body))
        return {"choices": [{"message": {"content": "Owns the Rust services.\n\nWhat you'll do\n- Ship one tested change a day"}}]}
    monkeypatch.setattr(recruiting, "_gateway_answer", gateway_answer)
    monkeypatch.setattr(docs_qa, "key", lambda: (_ for _ in ()).throw(docs_qa.NoKey("no key")))
    monkeypatch.delenv("TICO_GATEWAY_URL", raising=False)
    monkeypatch.setenv("TICO_GATEWAY_KEY", "gw-test-key")
    drafted = post(api, "seats/draft", {"title": "Rust engineer"})
    assert drafted == {"description": "Owns the Rust services.\n\nWhat you'll do\n- Ship one tested change a day", "source": "model"}
    [(base, key, body)] = asked
    assert (base, key) == (G.DEFAULT_BASE, "gw-test-key") and body["model"] == recruiting.GATEWAY_REWRITE_MODEL
    assert body["stream"] is False and body["messages"][0]["role"] == "user"
    assert body["reasoning_effort"] == "low" and body["max_tokens"] >= 2000     # reasoning counts against the cap
    assert "the role \"Rust engineer\"" in body["messages"][0]["content"]

    monkeypatch.setenv("TICO_GATEWAY_KEY", "")
    assert post(api, "seats/draft", {"title": "Rust engineer"}) == {"description": "", "source": "none"}
    assert len(asked) == 1

    monkeypatch.setenv("TICO_GATEWAY_KEY", "gw-test-key")
    monkeypatch.setattr(docs_qa, "key", lambda: "gemini-test-key")
    gemini = {"candidates": [{"content": {"parts": [{"text": "Owns the Rust services, as Gemini wrote it."}]}}]}
    monkeypatch.setattr(recruiting, "urlopen", lambda request, timeout: io.BytesIO(json.dumps(gemini).encode()))
    assert post(api, "seats/draft", {"title": "Rust engineer"})["description"] == "Owns the Rust services, as Gemini wrote it."
    assert len(asked) == 1

    # A draft that needs no model never reads the gateway key, so a vault that is down cannot break it.
    from backend import gateway_connection
    monkeypatch.setattr(gateway_connection, "target", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vault down")))
    assert post(api, "seats/draft", {"title": "QA engineer"})["source"] == "catalog"


def test_a_job_description_never_shows_the_bot_s_own_files_or_limits(api):
    """What a card owns says where the bot keeps things and that it stays in its lane; a job description says the work.
    No draft, and no applicant's day one, shows a file path or "within the requested work"."""
    _, cards = recruiting._recruitable(api.app.state.onboarding)
    assert recruiting._plain("The label scheme it works from (knowledge/labels.md, knowledge/repro-checklist.md)") == (
        "The label scheme it works from")
    assert recruiting._plain("Every issue it handles, within the requested work and Tools") == "Every issue it handles"
    assert recruiting._plain("What the team does, in knowledge/company.md") == "What the team does"
    assert recruiting._plain("Specs in Given/When/Then (red/amber/green)") == "Specs in Given/When/Then (red/amber/green)"
    for card in cards:
        text = recruiting.compose(card).split("Works in:")[0]
        day_one = [recruiting._plain(item, 400) for item in card["raw"]["owns"][:6]]
        for words in [text, *day_one]:
            assert "knowledge/" not in words and ".md" not in words and "requested work" not in words, (card["name"], words)


def test_redraft_is_bounded_in_all_and_never_leaves_a_rehearsal(api, monkeypatch):
    import io
    import threading
    import time
    from urllib.request import Request
    from clients import docs_qa
    composed = post(api, "seats/draft", {"title": "Customer support lead"})
    body = {"title": "Customer support lead", "redraft": True}
    monkeypatch.setattr(recruiting, "REWRITE_SECONDS", 0.3)
    opened = []

    class Answer(io.BytesIO):
        """A model's answer; `pause` is how long each piece takes to arrive."""
        def __init__(self, data, pause=0.0):
            super().__init__(data)
            self.pause, self.reads = pause, []
        def read1(self, n=-1):
            self.reads.append(n)
            time.sleep(self.pause)
            return super().read1(n)

    def serve(answer):
        def urlopen(request, timeout):
            opened.append(timeout)
            return answer
        monkeypatch.setattr(recruiting, "urlopen", urlopen)

    def workers():
        return sum(t.name == "seat-draft-rewrite" for t in threading.enumerate())

    def drained():
        for _ in range(100):
            if not workers():
                return
            time.sleep(0.02)
        raise AssertionError("a rewrite worker outlived its deadline")

    def timed():
        started = time.monotonic()
        out = post(api, "seats/draft", body)
        return out, time.monotonic() - started

    # The bound covers finding the key: a secret store that is slow does not hold the draft, and no request starts once
    # the deadline has passed. However many drafts time out, at most two workers are ever alive.
    serve(Answer(b"{}"))
    monkeypatch.setattr(docs_qa, "key", lambda: time.sleep(0.6) or "k")
    out, took = timed()
    assert out == composed and took < 1.5
    alive = []
    for _ in range(8):
        assert post(api, "seats/draft", body) == composed
        alive.append(workers())
    assert max(alive) <= 2
    drained()
    assert opened == [], "no request starts after the deadline"

    # Reading the answer is bounded too, against a real server that sends a byte every 50 ms and so never lets a read
    # time out: both workers are cut off at the deadline and end within half a second of it, and the next rewrite
    # then gets a slot.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import urlopen as real_urlopen

    class Trickle(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "10000")
            self.end_headers()
            try:
                for _ in range(10000):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass                        # the client hung up at its deadline
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(docs_qa, "key", lambda: "k")
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        monkeypatch.setattr(recruiting, "urlopen",
                            lambda request, timeout: opened.append(timeout) or real_urlopen(Request(url, data=request.data), timeout=timeout))
        started = time.monotonic()
        both = [threading.Thread(target=lambda: recruiting._rewrite("Composed.", "Support lead", "")) for _ in range(2)]
        for one in both:
            one.start()
        time.sleep(0.1)
        assert workers() == 2
        drained()
        assert time.monotonic() - started <= recruiting.REWRITE_SECONDS + 0.5
        assert opened and all(0 < t <= 0.3 for t in opened)
        serve(Answer(json.dumps({"candidates": [{"content": {"parts": [{"text": "Leads support."}]}}]}).encode()))
        assert recruiting._rewrite("Composed.", "Support lead", "") == "Leads support."
    finally:
        server.shutdown()
        server.server_close()

    # A real server that answers at once with a Content-Length body is read to its end and used: http.client closes
    # its reader when the body is complete, and the loop must stop there rather than touch the closed socket.
    whole = json.dumps({"candidates": [{"content": {"parts": [{"text": "Leads the support desk."}]}}]}).encode()

    class Whole(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(whole)))
            self.end_headers()
            self.wfile.write(whole)
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Whole)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        monkeypatch.setattr(recruiting, "urlopen", lambda request, timeout: real_urlopen(Request(url, data=request.data), timeout=timeout))
        assert recruiting._rewrite("Composed.", "Support lead", "") == "Leads the support desk."
    finally:
        server.shutdown()
        server.server_close()

    # An answer longer than the cap is not read past it, and is not used.
    huge = json.dumps({"candidates": [{"content": {"parts": [{"text": "Leads support. " * 20000}]}}]}).encode()
    big = Answer(huge)
    serve(big)
    assert post(api, "seats/draft", body) == composed and sum(big.reads) <= recruiting.REWRITE_BYTES + 1
    fine = json.dumps({"candidates": [{"content": {"parts": [{"text": "Leads our support desk, every day of the week."}]}}]}).encode()
    serve(Answer(fine))
    assert post(api, "seats/draft", body)["source"] == "model"

    # A rehearsal or a demo never reads a key or asks a model.
    touched = []
    monkeypatch.setattr(docs_qa, "key", lambda: touched.append("key") or "k")
    monkeypatch.setattr(recruiting, "urlopen", lambda *a, **k: touched.append("model") or Answer(fine))
    for flag in ("rehearsal", "demo"):
        monkeypatch.setattr(api.app.state.onboarding.settings, flag, True)
        assert post(api, "seats/draft", body) == composed
        assert post(api, "seats/draft", {"title": "Rust engineer"}) == {"description": "", "source": "none"}
        monkeypatch.setattr(api.app.state.onboarding.settings, flag, False)
    assert touched == []


def test_the_strategy_bot_no_longer_drafts_and_people_s_words_stay_theirs(api):
    from backend import lifecycle
    with api.app.state.store.transaction() as c:
        # The seats table as the release before recruiting left it, with a person's text: an upgrade keeps it theirs.
        c.execute("DROP TABLE seats")
        c.execute(lifecycle.SCHEMA)
        c.execute("INSERT INTO seats(id,title,description,reports_to,created_at,created_by,updated_at,updated_by) "
                  "VALUES('s-old','Role','Keeps the books.','coo',?,'human:ana',?,'human:ben')", (H.now(), H.now()))
        lifecycle.ensure(c)
        owner = Identity("human:ana", "owner")
        chart = lifecycle.create_seat(api.app.state.auth, c, owner, "Analyst", "From the chart model.", "coo",
                                      description_by=recruiting.CHART)
    mine = post(api, "seats", {"title": "Support lead", "reports_to": "coo", "description": "Answers email."})
    assert get(api, f"seats/{mine['id']}")["description_by"] == "person"
    assert get(api, "seats/s-old")["description_by"] == "person"
    shown = get(api, f"seats/{chart['id']}")
    assert shown["description_by"] == "chart" and "draft" not in shown and shown["can_edit"] is True
    for route in (f"seats/{mine['id']}/open", f"seats/{mine['id']}/draft"):
        assert api.post("/api/v2/" + route, headers=headers(), json={}).status_code in (404, 405)


def test_recruit_answers_at_once_with_up_to_eight_applicants_best_first_each_with_a_match_and_a_model(api):
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Customer support lead", "reports_to": "coo",
                               "description": "Answers customer email and chat and keeps the help center current."})
    found = post(api, f"seats/{seat['id']}/recruit", {})
    candidates = found["candidates"]
    assert 3 < len(candidates) <= recruiting.CANDIDATES == 8
    assert "support" in candidates[0]["template"] and candidates[0]["match"] == "strong"
    tiers = [c["match"] for c in candidates]
    assert tiers == sorted(tiers, key=["strong", "good", ""].index)        # best first
    choices = {m["id"] for m in found["models"]}
    for c in candidates:
        assert {"template", "name", "icon", "department", "line", "tags", "owns", "prerequisites", "match",
                "source"} <= set(c) and "reason" not in c and c["source"] == "template"
        assert c["line"] and "\n" not in c["line"] and len(c["line"]) <= 90
        assert len(c["tags"]) <= 6 and all(n["state"] in ("connected", "on_hire", "not_set") for n in c["prerequisites"])
        assert c["model"] in choices                                 # suggested, from a provider the team turned on
    assert {m["provider"] for m in found["models"]} <= {"anthropic", "openai"}

    # Recruiting is for whoever may fill the seat.
    as_member(api, "ben@acme.example")
    post(api, f"seats/{seat['id']}/recruit", {}, token="cara-test", expected=403)


def test_picking_a_candidate_creates_the_bot_in_onboarding_in_the_seat_and_only_then_its_repo(api):
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "coo",
                               "description": "Runs customer support and reports weekly on response times."})
    found = post(api, f"seats/{seat['id']}/recruit", {})
    pick = found["candidates"][0]
    other = next(m["id"] for m in found["models"] if m["id"] != pick["model"])
    with api.app.state.store.read() as c:
        before = {r["slug"] for r in c.execute("SELECT slug FROM bots")}

    # Nobody outside the seat's chain picks, and a model the team has not turned on is refused; nothing is created.
    as_member(api, "ben@acme.example")
    post(api, f"seats/{seat['id']}/hire", {"template": pick["template"]}, token="cara-test", expected=403)
    post(api, f"seats/{seat['id']}/hire", {"template": pick["template"], "model": "gemini-3-pro"}, expected=422)
    post(api, f"seats/{seat['id']}/hire", {"template": "botops"}, expected=422)       # a built-in is never a candidate
    with api.app.state.store.read() as c:
        assert {r["slug"] for r in c.execute("SELECT slug FROM bots")} == before

    below = post(api, "seats", {"title": "Support agent", "reports_to": "coo"})
    with api.app.state.store.transaction() as c:   # a seat may report to another seat (the chart import makes these)
        c.execute("UPDATE seats SET reports_to=? WHERE id=?", ("seat:" + seat["id"], below["id"]))

    hired = post(api, f"seats/{seat['id']}/hire", {"template": pick["template"], "model": other, "name": "Support Desk"})
    slug = hired["slug"]
    bot = get(api, f"bots/{slug}")
    assert (bot["stage"], bot["reports_to"], bot["display_name"], hired["name"]) == (
        "onboarding", "coo", "Support Desk", "Support Desk")
    with api.app.state.store.read() as c:
        hired_id = H.resolve_bot(c, slug)["id"]          # config and events are keyed by the bot's id, not its slug
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (hired_id,)).fetchone()[0])
        assert config["model"] == other
        # The repository is built from this definition once the bot exists (the computer's `materialize`).
        assert config["materialize"] is True and config["template"] == pick["template"]
        assert "## Job description\nRuns customer support" in config["instructions"]
        kinds = [r["kind"] for r in c.execute("SELECT action AS kind FROM events WHERE target IN (?,?) ORDER BY rowid",
                                              (hired_id, seat["id"]))]
    assert kinds.index("bot.definition_created") < kinds.index("bot.needs_onboarding")
    # The seat is filled: off the Recruiting list, and a second pick is refused.
    open_seats = {s["id"]: s for s in get(api, "seats")["seats"]}
    assert seat["id"] not in open_seats and open_seats[below["id"]]["reports_to"] == hired_id
    again = api.post(f"/api/v2/seats/{seat['id']}/hire", json={"template": pick["template"]}, headers=headers())
    assert again.status_code == 409 and "Support Desk" in again.text


def test_a_bot_or_an_unconfirmed_assistant_never_picks_and_a_refused_pick_leaves_nothing(api, monkeypatch):
    providers(api, "anthropic", "openai")
    strategy_token = strategy_bot(api)
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "coo", "description": "Answers customer email."})
    pick = post(api, f"seats/{seat['id']}/recruit", {})["candidates"][0]
    # A template that is not a starter: BotOps builds its repository, so a team with no BotOps cannot hire it.
    real = recruiting._recruitable
    def no_starters(onboarding):
        value, cards = real(onboarding)
        return value, [{**card, "raw": {**card["raw"], "starter": False}} for card in cards]
    monkeypatch.setattr(recruiting, "_recruitable", no_starters)
    api.app.state.store.settings.test_identities["ana-assistant"] = Identity("human:ana", "owner", via="assistant")

    def state():
        with api.app.state.store.read() as c:
            return ({r["slug"] for r in c.execute("SELECT slug FROM bots")},
                    dict(c.execute("SELECT filled_by,closed_at,reports_to FROM seats WHERE id=?", (seat["id"],)).fetchone()),
                    c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Set up %'").fetchone()[0])
    before = state()
    for caller in (strategy_token, headers("ana-assistant")):          # a bot; the Assistant's own turn for the owner
        assert api.post(f"/api/v2/seats/{seat['id']}/hire", headers=caller,
                        json={"template": pick["template"]}).status_code == 403
    # The pick's own rule, behind the route's: a bot, or anyone acting for a person without their confirmation.
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM seats WHERE id=?", (seat["id"],)).fetchone()
        for who in (Identity("bot:goal-manager", "bot"), Identity("human:ana", "owner", via="botops")):
            with pytest.raises(Problem, match="Only a person picks"):
                recruiting._may_hire(api.app.state.auth, c, who, row)
    refused = api.post(f"/api/v2/seats/{seat['id']}/hire", headers=headers(), json={"template": pick["template"]})
    assert refused.status_code == 409 and "BotOps" in refused.text
    assert state() == before
