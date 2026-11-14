"""Recruiting a seat (backend/recruiting.py): bot-lifecycle-spec.md section 3, acceptance tests 6 to 8. Test 9 (Team >
Add bot makes a seat first) is the page's: ui/tests/team-groups.cjs."""

import json

import pytest

from backend import recruiting
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.helpers.api import as_member, get, headers, post  # noqa: F401
from backend.tests.helpers.strategy_bot import as_bot
from backend import bot_settings as BS
from backend.tests.helpers.recruiting import providers


def strategy_bot(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('strategy-owner','strategy-owner','Strategy Owner','','active',?)",
            (H.now(),),
        )
        if c.execute("INSERT OR IGNORE INTO bot_config(bot,owner_id) VALUES('strategy-owner','ana')").rowcount:
            BS.write(c, 'strategy-owner', {"runtime": "fake", "host": "keeper"})
    return as_bot(api, "strategy-owner")


def test_post_a_job_drafts_a_description_from_the_title_at_once_and_redraft_rewrites_it_once(api, monkeypatch):
    # At once means the first draft is composed from the catalog and never reaches the model, so nothing waits on one.
    # Checked by the calls made, not by the clock, which a loaded runner can stretch.
    model_calls = []
    monkeypatch.setattr(recruiting, "_rewrite_call", lambda *args, **kwargs: model_calls.append(args) or "")
    drafted = post(api, "seats/draft", {"title": "Customer support lead", "team": "Support"})
    assert drafted["source"] == "catalog" and model_calls == []
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
    again = post(api, "seats/draft", {"title": "Customer support lead", "reports_to": "assistant", "redraft": True})
    assert again["source"] == "model" and again["description"].startswith("Leads our support desk.")
    assert asked == [("Customer support lead", get(api, "bots/assistant")["display_name"])]
    for broken in (lambda *_: (_ for _ in ()).throw(TimeoutError("slow")), lambda *_: "Too short."):
        monkeypatch.setattr(recruiting, "_rewrite_call", broken)
        assert post(api, "seats/draft", {"title": "Customer support lead", "redraft": True}) == drafted
    post(api, "seats/draft", {"title": ""}, expected=422)

    # Only a person drafts, and the model hears only a manager the caller could post under: Cara, a member, gets her
    # own name sent but not the COO's, which is outside her chain; a name nobody has is dropped too.
    asked.clear()
    monkeypatch.setattr(recruiting, "_rewrite_call", rewrite)
    as_member(api, "ben@acme.example")
    for reports_to in ("assistant", "nobody-here", "human:ana"):
        post(
            api,
            "seats/draft",
            {"title": "Customer support lead", "reports_to": reports_to, "redraft": True},
            token="cara-test",
        )
    post(
        api,
        "seats/draft",
        {"title": "Customer support lead", "reports_to": "human:cara", "redraft": True},
        token="cara-test",
    )
    with api.app.state.store.read() as c:
        cara = H.human(c, "cara")["name"]
    assert asked == [("Customer support lead", "")] * 3 + [("Customer support lead", cara)]
    bot = as_bot(api, "assistant")
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
        assert drafted == {
            "description": "Owns the Rust services.\n\nWhat you'll do\n- Ship one tested change a day",
            "source": "model",
        }
    assert asked == [("", "Rust engineer"), ("", "iOS developer"), ("", "Data analyst")]
    # A title that is a template's job drafts from that template at once, without the model, two-letter work included.
    _, cards = recruiting._recruitable(api.app.state.onboarding)
    for title, template in (
        ("QA engineer", "qa-owner"),
        ("Security engineer", "security-engineer"),
        ("Marketing manager", "segment-marketing-owner"),
        ("Customer support lead", "support-owner"),
        ("Head of People", "head-of-people"),
        ("Developer Advocates", "developer-advocate"),
        ("Head of HR", "head-of-people"),
        ("HR generalist", "hr-generalist"),
        ("Head of engineering", "engineering-manager"),
        ("Director of marketing", "growth-manager"),
        ("Head of Human Resources", "head-of-people"),
        ("Head of Legal Operations", "legal-operations-manager"),
        ("Developer relations", "developer-advocate"),
        ("PR manager", "pr-manager"),
        ("VP Engineering", "engineering-manager"),
        ("Head of Compliance", "compliance-manager"),
        ("Head of Customer Success", "success-manager"),
        ("Account Director", "account-manager"),
        ("Human Resources Generalist", "hr-generalist"),
        ("Financial Planning Analyst", "fpa-analyst"),
        ("SRE", "site-reliability-engineer"),
        ("SDR", "outbound-sales-owner"),
        ("Site Reliability Manager", "site-reliability-engineer"),
        ("Sales Development Specialist", "outbound-sales-owner"),
        ("Public relations manager", "pr-manager"),
        ("Senior Software Developer", "feature-owner"),
        ("QA engineer (contract)", "qa-owner"),
        ("Security engineer - remote", "security-engineer"),
        ("Manager, Engineering", "engineering-manager"),
        ("Engineer, Security", "security-engineer"),
        ("Manager, Customer Success", "success-owner"),
        ("VP, Product Marketing", "growth-manager"),
        ("Sr. Security Engineer", "security-engineer"),
        ("Vice President, Product Marketing", "growth-manager"),
        ("Manager (Engineering)", "engineering-manager"),
        ("Analyst (Pricing)", "pricing-analyst"),
        ("Software Engineer, Security", "security-engineer"),
        ("Software Engineer (Security)", "security-engineer"),
        ("Software Engineer, Site Reliability", "site-reliability-engineer"),
        ("Engineer (QA) - Remote, US", "qa-owner"),
        ("Support Representative", "support-owner"),
        ("Customer Support Rep", "support-owner"),
        ("Customer Service Agent", "support-owner"),
        ("Director of Revenue Recognition", "revenue-accountant"),
        ("Employee Relations Manager", "hr-business-partner"),
    ):
        assert recruiting._best_card(cards, title)["template"] == template, title
    # A title ending in a kind of job wants that kind: a designer is not the UX Writer, an assistant not the Account
    # Executive, a machine learning engineer not the Learning and Development Specialist.
    for title in (
        "UX designer",
        "Executive assistant",
        "Machine learning engineer",
        "Product designer",
        "Accounting manager",
        "UX Designer (Contract)",
        "Rust engineer, platform",
        "UX Designer II",
        "UX Designer III",
    ):
        assert recruiting._best_card(cards, title) is None, title
    # Every template's own name, and its plural, is that template's job.
    misses = [
        card["name"]
        for card in cards
        for title in (card["name"], card["name"] + "s")
        if (recruiting._best_card(cards, title) or {}).get("template") != card["template"]
    ]
    assert misses == []
    asked.clear()
    assert post(api, "seats/draft", {"title": "QA engineer"})["source"] == "catalog" and asked == []
    monkeypatch.setattr(recruiting, "_rewrite_call", lambda *_: (_ for _ in ()).throw(TimeoutError("slow")))
    assert post(api, "seats/draft", {"title": "Rust engineer"}) == {"description": "", "source": "none"}

    # Applicants: no template is "strong" for a Rust engineer on "Engineer" alone; the QA Engineer is for a QA engineer.
    rust = post(api, "seats", {"title": "Rust engineer", "reports_to": "assistant"})
    assert "strong" not in {c["match"] for c in post(api, f"seats/{rust['id']}/recruit", {})["candidates"]}
    qa = post(api, "seats", {"title": "QA engineer", "reports_to": "assistant"})
    first = post(api, f"seats/{qa['id']}/recruit", {})["candidates"][0]
    assert first["template"] == "qa-owner" and first["match"] == "strong"


def test_marketing_and_engineering_titles_draft_from_the_template_that_is_the_job_by_any_of_its_names(api):
    """A job's other names are the job ("CMO", "SDET", "PPC Specialist", "Content Writer"); a word of a whole field is
    not ("Influencer Marketing Manager" is not the Head of Marketing, "Partner Marketing Manager" not the HR Business
    Partner); a job of a kind may be a template named by rank on the work ("Release Engineer" is the Release Owner)."""
    _, cards = recruiting._recruitable(api.app.state.onboarding)
    for title, template in (
        ("CMO", "growth-manager"),
        ("Chief Marketing Officer", "growth-manager"),
        ("CTO", "engineering-manager"),
        ("Head of Growth", "growth-manager"),
        ("PMM", "segment-marketing-owner"),
        ("Senior Product Marketer", "segment-marketing-owner"),
        ("Content Writer", "content-owner"),
        ("Copywriter", "content-owner"),
        ("Blog Writer", "content-owner"),
        ("Social Media Coordinator", "social-media-manager"),
        ("Events Coordinator", "event-marketing-manager"),
        ("Field Marketing Manager", "event-marketing-manager"),
        ("Lifecycle Marketing Manager", "email-owner"),
        ("Retention Marketing Manager", "email-owner"),
        ("Marketing Ops Specialist", "marketing-operations-manager"),
        ("Performance Marketing Manager", "search-ads-owner"),
        ("Paid Social Manager", "social-ads-owner"),
        ("PPC Specialist", "search-ads-owner"),
        ("Communications Manager", "pr-manager"),
        ("Online Reviews Manager", "reputation-manager"),
        ("SDET", "qa-owner"),
        ("Software Engineer in Test", "qa-owner"),
        ("Test Engineer", "qa-owner"),
        ("AppSec Engineer", "security-engineer"),
        ("Build Engineer", "devops-engineer"),
        ("CI/CD Engineer", "devops-engineer"),
        ("Release Engineer", "release-owner"),
        ("Developer Relations Engineer", "developer-advocate"),
        ("DevRel", "developer-advocate"),
        ("Documentation Engineer", "technical-writer"),
        ("Code Reviewer", "reviewer"),
        ("Software Engineer", "feature-owner"),
        ("Tax accountant", "tax-specialist"),
        ("Key account manager", "account-manager"),
        ("Software Development Engineer", "feature-owner"),
        ("Code Review Engineer", "reviewer"),
        ("Product Security Engineer", "security-engineer"),
        ("Product Security Engineer II", "security-engineer"),
        ("Product Security Engineers", "security-engineer"),
        ("Affiliate Marketing Manager", "affiliates-owner"),
    ):
        assert (recruiting._best_card(cards, title) or {}).get("template") == template, title
    for title in (
        "Influencer Marketing Manager",
        "Partner Marketing Manager",
        "Marketing Coordinator",
        "Digital Marketing Manager",
        "Brand Designer",  # the Design Owner is built in, so no card is hired for it
        "Backend Engineer",
        "Go Developer",
        "Data Scientist",
        "Tech Lead",
        "Executive assistant",
        "UX designer",
    ):
        assert recruiting._best_card(cards, title) is None, title


def test_strong_is_the_template_that_is_the_job_and_a_coding_job_has_the_cloud_agents_among_its_applicants(api):
    """Strong is as good a fit by name as the drafted template ("Head of Sales" is the Sales Manager's job, not every
    template that says "Sales"). A job writing code in Engineering lists the cloud coding agents Tico connects: first
    when no template is the job, after the ones that are; no head's template leads a job of a kind."""

    def found(title, description=""):
        seat = post(api, "seats", {"title": title, "reports_to": "assistant", "description": description})
        return post(api, f"seats/{seat['id']}/recruit", {})

    sales = found("Head of Sales")
    assert [c["template"] for c in sales["candidates"] if c["match"] == "strong"] == ["growth-manager"]
    assert sales["agents"] == [] and sales["agents_at"] == 1

    rust = found("Rust engineer")
    assert [a["id"] for a in rust["agents"]] == ["claude", "codex"] and rust["agents_at"] == 0
    assert {a["match"] for a in rust["agents"]} == {"strong"} and {a["source"] for a in rust["agents"]} == {"cloud"}
    assert rust["candidates"][0]["department_id"] == "engineering"
    assert rust["candidates"][0]["template"] != "engineering-manager"
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
        seat = post(api, "seats", {"title": title, "reports_to": "assistant", "description": ""})
        with api.app.state.store.read() as c:
            row = c.execute("SELECT * FROM seats WHERE id=?", (seat["id"],)).fetchone()
            card = recruiting.top_card(
                api.app.state.auth, api.app.state.onboarding, c, Identity("human:ana", "owner"), row
            )
        return card and card["department"]

    assert top("Engineer") == "engineering" and top("Tech Lead") is None


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
        return {
            "choices": [
                {"message": {"content": "Owns the Rust services.\n\nWhat you'll do\n- Ship one tested change a day"}}
            ]
        }

    monkeypatch.setattr(recruiting, "_gateway_answer", gateway_answer)
    monkeypatch.setattr(docs_qa, "key", lambda: (_ for _ in ()).throw(docs_qa.NoKey("no key")))
    monkeypatch.delenv("TICO_GATEWAY_URL", raising=False)
    monkeypatch.setenv("TICO_GATEWAY_KEY", "gw-test-key")
    drafted = post(api, "seats/draft", {"title": "Rust engineer"})
    assert drafted == {
        "description": "Owns the Rust services.\n\nWhat you'll do\n- Ship one tested change a day",
        "source": "model",
    }
    [(base, key, body)] = asked
    assert (base, key) == (G.DEFAULT_BASE, "gw-test-key") and body["model"] == recruiting.GATEWAY_REWRITE_MODEL
    assert body["stream"] is False and body["messages"][0]["role"] == "user"
    assert body["reasoning_effort"] == "low" and body["max_tokens"] >= 2000  # reasoning counts against the cap
    assert "the role \"Rust engineer\"" in body["messages"][0]["content"]

    monkeypatch.setenv("TICO_GATEWAY_KEY", "")
    assert post(api, "seats/draft", {"title": "Rust engineer"}) == {"description": "", "source": "none"}
    assert len(asked) == 1

    monkeypatch.setenv("TICO_GATEWAY_KEY", "gw-test-key")
    monkeypatch.setattr(docs_qa, "key", lambda: "gemini-test-key")
    gemini = {"candidates": [{"content": {"parts": [{"text": "Owns the Rust services, as Gemini wrote it."}]}}]}
    monkeypatch.setattr(recruiting, "urlopen", lambda request, timeout: io.BytesIO(json.dumps(gemini).encode()))
    assert (
        post(api, "seats/draft", {"title": "Rust engineer"})["description"]
        == "Owns the Rust services, as Gemini wrote it."
    )
    assert len(asked) == 1

    # A draft that needs no model never reads the gateway key, so a vault that is down cannot break it.
    from backend import gateway_connection

    monkeypatch.setattr(gateway_connection, "target", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vault down")))
    assert post(api, "seats/draft", {"title": "QA engineer"})["source"] == "catalog"


def test_the_strategy_bot_no_longer_drafts_and_people_s_words_stay_theirs(api):
    from backend import lifecycle

    with api.app.state.store.transaction() as c:
        # The seats table as the release before recruiting left it, with a person's text: an upgrade keeps it theirs.
        c.execute("DROP TABLE seats")
        c.execute(lifecycle.SCHEMA)
        c.execute(
            "INSERT INTO seats(id,title,description,reports_to,created_at,created_by,updated_at,updated_by) "
            "VALUES('s-old','Role','Keeps the books.','assistant',?,'human:ana',?,'human:ben')",
            (H.now(), H.now()),
        )
        lifecycle.ensure(c)
        owner = Identity("human:ana", "owner")
        chart = lifecycle.create_seat(
            api.app.state.auth,
            c,
            owner,
            "Analyst",
            "From the chart model.",
            "assistant",
            description_by=recruiting.CHART,
        )
    mine = post(api, "seats", {"title": "Support lead", "reports_to": "assistant", "description": "Answers email."})
    assert get(api, f"seats/{mine['id']}")["description_by"] == "person"
    assert get(api, "seats/s-old")["description_by"] == "person"
    shown = get(api, f"seats/{chart['id']}")
    assert shown["description_by"] == "chart" and "draft" not in shown and shown["can_edit"] is True
    for route in (f"seats/{mine['id']}/open", f"seats/{mine['id']}/draft"):
        assert api.post("/api/v2/" + route, headers=headers(), json={}).status_code in (404, 405)


def test_recruit_answers_at_once_with_up_to_eight_applicants_best_first_each_with_a_match_and_a_model(api):
    providers(api, "anthropic", "openai")
    seat = post(
        api,
        "seats",
        {
            "title": "Customer support lead",
            "reports_to": "assistant",
            "description": "Answers customer email and chat and keeps the help center current.",
        },
    )
    found = post(api, f"seats/{seat['id']}/recruit", {})
    candidates = found["candidates"]
    assert 3 < len(candidates) <= recruiting.CANDIDATES == 8
    assert "support" in candidates[0]["template"] and candidates[0]["match"] == "strong"
    tiers = [c["match"] for c in candidates]
    assert tiers == sorted(tiers, key=["strong", "good", ""].index)  # best first
    choices = {m["id"] for m in found["models"]}
    for c in candidates:
        assert (
            {"template", "name", "icon", "department", "line", "tags", "owns", "prerequisites", "match", "source"}
            <= set(c)
            and "reason" not in c
            and c["source"] == "template"
        )
        assert c["line"] and "\n" not in c["line"] and len(c["line"]) <= 90
        assert len(c["tags"]) <= 6 and all(
            n["state"] in ("connected", "on_hire", "not_set") for n in c["prerequisites"]
        )
        assert c["model"] in choices  # suggested, from a provider the team turned on
    assert {m["provider"] for m in found["models"]} <= {"anthropic", "openai"}

    # Recruiting is for whoever may fill the seat.
    as_member(api, "ben@acme.example")
    post(api, f"seats/{seat['id']}/recruit", {}, token="cara-test", expected=403)


def test_picking_a_candidate_creates_the_bot_in_onboarding_in_the_seat_and_only_then_its_repo(api):
    providers(api, "anthropic", "openai")
    seat = post(
        api,
        "seats",
        {
            "title": "Support lead",
            "reports_to": "assistant",
            "description": "Runs customer support and reports weekly on response times.",
        },
    )
    found = post(api, f"seats/{seat['id']}/recruit", {})
    pick = found["candidates"][0]
    other = next(m["id"] for m in found["models"] if m["id"] != pick["model"])
    with api.app.state.store.read() as c:
        before = {r["slug"] for r in c.execute("SELECT slug FROM bots")}

    # Nobody outside the seat's chain picks, and a model the team has not turned on is refused; nothing is created.
    as_member(api, "ben@acme.example")
    post(api, f"seats/{seat['id']}/hire", {"template": pick["template"]}, token="cara-test", expected=403)
    post(api, f"seats/{seat['id']}/hire", {"template": pick["template"], "model": "gemini-3-pro"}, expected=422)
    post(api, f"seats/{seat['id']}/hire", {"template": "botops"}, expected=422)  # a built-in is never a candidate
    with api.app.state.store.read() as c:
        assert {r["slug"] for r in c.execute("SELECT slug FROM bots")} == before

    below = post(api, "seats", {"title": "Support agent", "reports_to": "assistant"})
    with api.app.state.store.transaction() as c:  # a seat may report to another seat (the chart import makes these)
        c.execute("UPDATE seats SET reports_to=? WHERE id=?", ("seat:" + seat["id"], below["id"]))

    hired = post(
        api, f"seats/{seat['id']}/hire", {"template": pick["template"], "model": other, "name": "Support Desk"}
    )
    slug = hired["slug"]
    bot = get(api, f"bots/{slug}")
    assert (bot["stage"], bot["reports_to"], bot["display_name"], hired["name"]) == (
        "onboarding",
        "assistant",
        "Support Desk",
        "Support Desk",
    )
    with api.app.state.store.read() as c:
        hired_id = H.resolve_bot(c, slug)["id"]  # config and events are keyed by the bot's id, not its slug
        config = BS.read(c, hired_id)
        assert config["model"] == other
        # The repository is built from this definition once the bot exists (the computer's `materialize`).
        assert config["materialize"] is True and config["template"] == pick["template"]
        assert "## Job description\nRuns customer support" in config["instructions"]
        kinds = [
            r["kind"]
            for r in c.execute(
                "SELECT action AS kind FROM events WHERE target IN (?,?) ORDER BY rowid", (hired_id, seat["id"])
            )
        ]
    assert kinds.index("bot.definition_created") < kinds.index("bot.needs_onboarding")
    # The seat is filled: off the Recruiting list, and a second pick is refused.
    open_seats = {s["id"]: s for s in get(api, "seats")["seats"]}
    assert seat["id"] not in open_seats and open_seats[below["id"]]["reports_to"] == hired_id
    again = api.post(f"/api/v2/seats/{seat['id']}/hire", json={"template": pick["template"]}, headers=headers())
    assert again.status_code == 409 and "Support Desk" in again.text


def bot_line(api, hired):
    """What the bot reports to in its definition: a person or a bot, never a seat."""
    with api.app.state.store.read() as c:
        return c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (hired["bot"],)).fetchone()[0]


def fill(api, seat):
    pick = post(api, f"seats/{seat['id']}/recruit", {})["candidates"][0]["template"]
    return post(api, f"seats/{seat['id']}/hire", {"template": pick})


def test_seats_filled_under_open_manager_seats_keep_each_bot_on_a_line_that_never_circles(api):
    providers(api, "anthropic", "openai")
    # A bot hired under an open manager seat reports above it until that seat is hired.
    head = post(
        api,
        "seats",
        {
            "title": "Head of Marketing",
            "reports_to": "assistant",
            "description": "Leads marketing and sets the content plan.",
        },
    )
    writer = post(
        api,
        "seats",
        {
            "title": "Content Owner",
            "reports_to": "seat:" + head["id"],
            "description": "Writes the blog and the newsletter.",
        },
    )
    # The Hire page says who the bot reports to until the manager seat is hired, and that seat's title.
    assert get(api, f"seats/{writer['id']}")["reports_now"]["until"] == "Head of Marketing"
    assert "reports_now" not in get(api, f"seats/{head['id']}")

    hired = fill(api, writer)
    assert hired["reports_to"] == "assistant" and bot_line(api, hired) == "assistant"
    assert get(api, f"bots/{hired['slug']}")["stage"] == "onboarding"

    manager = fill(api, head)
    assert bot_line(api, hired) == manager["bot"]  # the writer moves under the Head of Marketing bot
    assert bot_line(api, manager) == "assistant"
    assert get(api, "seats")["seats"] == []

    # A chain of open seats hired bottom-up ends with each bot under the one above.
    top = post(api, "seats", {"title": "Head of Sales", "reports_to": "assistant", "description": "Leads sales."})
    middle = post(
        api, "seats", {"title": "Sales Lead", "reports_to": "seat:" + top["id"], "description": "Leads the sales team."}
    )
    bottom = post(api, "seats", {"title": "Sales Rep", "reports_to": "seat:" + middle["id"], "description": "Sells."})
    lowest = fill(api, bottom)
    assert bot_line(api, lowest) == "assistant"
    highest = fill(api, top)
    middling = fill(api, middle)
    assert bot_line(api, middling) == highest["bot"]
    assert bot_line(api, lowest) == middling["bot"]  # moved when the seat above it filled
    assert bot_line(api, highest) == "assistant"

    # A bot moved by hand under an open manager keeps that manager when the seat fills.
    head = post(api, "seats", {"title": "Head of Support", "reports_to": "assistant", "description": "Leads support."})
    writer = post(
        api,
        "seats",
        {"title": "Support Writer", "reports_to": "seat:" + head["id"], "description": "Writes the help pages."},
    )
    hired = fill(api, writer)
    revision = get(api, f"bots/{hired['slug']}")["revision"]
    post(api, f"bots/{hired['slug']}/definition", {"reports_to": "human:ben", "expected_revision": revision})
    fill(api, head)
    assert bot_line(api, hired) == "human:ben"

    # An open manager moved under its own hire does not circle when it fills.
    head = post(api, "seats", {"title": "Head of Finance", "reports_to": "assistant", "description": "Leads finance."})
    writer = post(
        api, "seats", {"title": "Bookkeeper", "reports_to": "seat:" + head["id"], "description": "Keeps the books."}
    )
    hired = fill(api, writer)  # placed under the owner, the line it was hired on
    assert (
        api.patch(
            f"/api/v2/seats/{head['id']}", headers=headers(), json={"reports_to": "bot:" + hired["slug"]}
        ).status_code
        == 200
    )
    manager = fill(api, head)  # filling must not close a circle
    assert bot_line(api, manager) == hired["bot"]
    assert bot_line(api, hired) == "assistant"
    with api.app.state.store.read() as c:  # walk up from the manager: no bot twice, ends at the top
        line, seen = manager["bot"], []
        while H.bot(c, line):
            assert line not in seen, "the bot hierarchy has a circle"
            seen.append(line)
            line = c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (line,)).fetchone()[0] or ""
        assert seen == [manager["bot"], hired["bot"], "assistant"]


def test_a_bot_or_an_unconfirmed_assistant_never_picks_and_a_refused_pick_leaves_nothing(api, monkeypatch):
    providers(api, "anthropic", "openai")
    strategy_token = strategy_bot(api)
    seat = post(
        api, "seats", {"title": "Support lead", "reports_to": "assistant", "description": "Answers customer email."}
    )
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
            return (
                {r["slug"] for r in c.execute("SELECT slug FROM bots")},
                dict(
                    c.execute("SELECT filled_by,closed_at,reports_to FROM seats WHERE id=?", (seat["id"],)).fetchone()
                ),
                c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Set up %'").fetchone()[0],
            )

    before = state()
    for caller in (strategy_token, headers("ana-assistant")):  # a bot; the Assistant's own turn for the owner
        assert (
            api.post(
                f"/api/v2/seats/{seat['id']}/hire", headers=caller, json={"template": pick["template"]}
            ).status_code
            == 403
        )
    # The pick's own rule, behind the route's: a bot, or anyone acting for a person without their confirmation.
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM seats WHERE id=?", (seat["id"],)).fetchone()
        for who in (Identity("bot:strategy-owner", "bot"), Identity("human:ana", "owner", via="botops")):
            with pytest.raises(Problem, match="Only a person picks"):
                recruiting._may_hire(api.app.state.auth, c, who, row)
    refused = api.post(f"/api/v2/seats/{seat['id']}/hire", headers=headers(), json={"template": pick["template"]})
    assert refused.status_code == 409 and "BotOps" in refused.text
    assert state() == before


def test_a_seat_set_up_with_a_template_hires_only_that_template_and_a_removed_one_pins_nothing(api):
    providers(api, "anthropic", "openai")
    seat = post(
        api, "seats", {"title": "Rust engineer", "reports_to": "assistant", "description": "Writes the Rust core."}
    )
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE seats SET template='security-engineer' WHERE id=?", (seat["id"],))

    # Another Recruit template is refused, plainly, and nothing is created.
    with api.app.state.store.read() as c:
        before = {r["slug"] for r in c.execute("SELECT slug FROM bots")}
    refused = api.post(f"/api/v2/seats/{seat['id']}/hire", headers=headers(), json={"template": "engineering-manager"})
    assert refused.status_code == 422 and "set up with" in refused.text, refused.text
    with api.app.state.store.read() as c:
        assert {r["slug"] for r in c.execute("SELECT slug FROM bots")} == before

    hired = post(api, f"seats/{seat['id']}/hire", {"template": "security-engineer"})
    assert hired["template"] == "security-engineer" and hired["seat"] == seat["id"]

    # A pinned template removed from the catalog no longer pins the seat: any applicant may be hired.
    gone = post(api, "seats", {"title": "Support lead", "reports_to": "assistant", "description": "Answers customers."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE seats SET template='retired-template' WHERE id=?", (gone["id"],))
    offered = post(api, f"seats/{gone['id']}/recruit", {})["candidates"]
    assert len(offered) > 1
    assert post(api, f"seats/{gone['id']}/hire", {"template": offered[0]["template"]})["seat"] == gone["id"]


def test_a_seat_set_up_with_a_template_is_that_job_and_a_coding_one_in_engineering_still_has_the_cloud_agents(api):
    """A seat setup placed with a template is offered that template alone, strong, whatever its title says. Its
    department is the template's own, so only an engineering seat that codes lists the cloud agents, after it."""

    def pinned(title, template):
        seat = post(api, "seats", {"title": title, "reports_to": "assistant"})
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE seats SET template=? WHERE id=?", (template, seat["id"]))
        return post(api, f"seats/{seat['id']}/recruit", {})

    rust = pinned("Rust engineer", "security-engineer")
    assert [(c["template"], c["match"]) for c in rust["candidates"]] == [("security-engineer", "strong")]
    assert rust["department"] == "engineering" and rust["agents_at"] == 1
    assert [(a["id"], a["match"]) for a in rust["agents"]] == [("claude", "good"), ("codex", "good")]

    # The title says engineer, but the template is Marketing's: the seat is Marketing's, so no agents.
    marketing = pinned("Rust engineer", "growth-manager")
    assert [c["template"] for c in marketing["candidates"]] == ["growth-manager"]
    assert marketing["department"] == "marketing" and marketing["agents"] == []


def test_a_hire_keeps_the_model_harness_effort_and_window_picked_with_it(api):
    providers(api, "anthropic", "openai")
    seat = post(
        api, "seats", {"title": "Rust engineer", "reports_to": "assistant", "description": "Writes the Rust core."}
    )
    card = post(api, f"seats/{seat['id']}/recruit", {})
    model = next(m for m in card["models"] if m["id"].startswith("claude-"))["id"]
    hired = post(
        api,
        f"seats/{seat['id']}/hire",
        {
            "template": card["candidates"][0]["template"],
            "model": model,
            "harness": "claude",
            "effort": "high",
            "compact_at": 600_000,
        },
    )
    bots = api.get("/api/v2/bots", headers=headers()).json()
    rows = bots["bots"] if isinstance(bots, dict) else bots
    bot = next(b for b in rows if hired["bot"] in (b.get("id"), b.get("slug")))
    assert (bot["model"], bot["compact_at"]) == (model, 600_000)
