"""Right after the owner connects GitHub on the default company (20 bots, one shared computer): organization, "All
repositories", "Let Tico create bot repositories" ticked (docs/github-app.md "Set up").

The company is the one setup makes, the routes are the server's own, and only GitHub is a fake. Each test is one thing
the owner or a bot would notice: every bot gets its repository without anyone asking, once, whatever GitHub does on the
way (a lost answer, an outage, a rate limit, a refused permission, a name already there); a second return from GitHub
lands on Tools; and one tap on the product repository gives each seat its access.
"""

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from backend import bot_repo_maker as maker
from backend import heartbeat
from backend.tests.helpers.github_app import gh  # noqa: F401
from backend.tests.helpers.sample_company import booted, environment, machine, rows, signed_in  # noqa: F401

ORG = "ticoteam"
BROWSER = {"accept": "text/html,application/xhtml+xml"}


class Clock:
    """The maker's clock: its passes are spaced and its waits are timed by it."""

    def __init__(self):
        self.at = 1_800_000_000.0

    def __call__(self):
        return self.at

    def forward(self, seconds):
        self.at += seconds


@pytest.fixture
def company(environment, gh):
    """The default company with its bots on one computer, and GitHub's organization with the product repository."""
    api = environment()
    assert api.put("/api/v2/providers", json={"enabled": ["anthropic"]}, headers=signed_in()).status_code == 200
    computer = machine(api)
    body = {
        "names": {"company_name": "Kiln & Co", "app_name": "Tico", "assistant_name": "Assistant"},
        "answers": {"what_we_do": "We make software.", "customers": "consumers", "team_size": "1 (just me)"},
        "selected": {},
    }
    assert api.put("/api/v2/onboarding", json=body, headers=signed_in()).status_code == 200
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    api.computer = computer["runner_id"]
    place(api)
    gh.installations = [{"id": 77, "account": {"login": ORG}}]
    gh.accounts = {ORG: "Organization"}
    gh.conversion_owner = {"login": ORG, "type": "Organization"}
    gh.permissions = {"administration": "write", "contents": "write", "metadata": "read"}
    gh.repositories = [{"full_name": f"{ORG}/tico", "default_branch": "main"}]
    api.runner = {"Authorization": "Bearer " + computer["token"]}
    api.clock = Clock()
    gh.clock = api.clock
    return api


def place(api):
    """Every bot runs on the hosted company's one shared computer."""
    with api.app.state.store.transaction() as c:
        for bot in c.execute("SELECT id FROM bots WHERE id NOT IN (SELECT bot FROM assignments)").fetchall():
            c.execute(
                "INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'human:owner')",
                (bot["id"], api.computer, "2026-11-11T00:00:00Z"),
            )


def bots(api):
    return [r["slug"] for r in rows(api, "SELECT slug FROM bots WHERE state<>'archived' ORDER BY slug")]


def connect(api, installed=True):
    """The owner's connect: the form, GitHub's Create, the return, and the install's return. Returns the state."""
    setup = api.get("/api/v2/github/app/manifest", params={"org": ORG}, headers=signed_in())
    assert setup.status_code == 200, setup.text
    state = setup.json()["state"]
    back = api.get(
        "/api/v2/github/app/callback",
        params={"code": "c1", "state": state},
        headers={**signed_in(), **BROWSER},
        follow_redirects=False,
    )
    # The install page is the App's as GitHub named it (the person may change the name on GitHub's page).
    assert back.status_code == 302 and "/apps/acme-tico/installations/new" in back.headers["location"], back.text
    if installed:
        done = api.get("/api/v2/github/app/installed", headers=signed_in(), follow_redirects=False)
        assert done.headers["location"] == "/#/tools/github?github=connected", done.text
    return state


def creates(gh):
    """How many times GitHub was asked to create each repository."""
    return Counter(call[2]["name"] for call in gh.calls if call[0] == "POST" and call[1] == f"/orgs/{ORG}/repos")


def make(api, passes=20, gap=maker.GAP):
    """The server's poll, `passes` times, `gap` seconds apart. Returns what each pass made."""
    out = []
    for _ in range(passes):
        out.append(maker.run(api.app.state.github_app, now=api.clock))
        api.clock.forward(gap)
    return out


def progress(api):
    return api.get("/api/v2/github/app", headers=signed_in()).json()["bot_repositories"]


def token(api, gh, slug):
    """A turn's GitHub token for `slug`, with GitHub answering for exactly the repositories it has."""
    gh.missing = {"bot-" + name for name in bots(api)} - {name.split("/")[1] for name in gh.created_repositories}
    return api.post("/api/v2/github/token", json={"bot": slug}, headers=api.runner)


def test_every_bot_gets_its_repository_without_anyone_asking_once_each_and_a_few_at_a_time(company, gh):
    api = company
    connect(api)
    team = bots(api)
    assert len(team) == 20 and "archive" not in team
    before = token(api, gh, "feature-owner")
    assert before.status_code == 409 and before.json()["error"]["code"] == "github_repo_missing", before.text
    assert progress(api) == {"made": 0, "taken": 0, "waiting": 20, "problem": ""}
    # That refused token leaves a "not reachable" row for a repository GitHub does not have; it must not count as made
    # or as someone else's.
    listed = api.get("/api/v2/repositories", headers=signed_in()).json()["repositories"]
    assert [r["reachable"] for r in listed if r["full_name"] == f"{ORG}/bot-feature-owner"] == [False]

    # The server's own poll makes them: nobody asked BotOps, and nobody ran `tico bot repo-create`.
    ctx = SimpleNamespace(app=api.app, store=api.app.state.store, settings=api.app.state.store.settings)
    first = len(creates(gh))
    heartbeat.repository_waits(ctx)
    assert 0 < len(creates(gh)) - first <= maker.PER_PASS, "the server's poll made no bot repository"
    made = make(api)
    assert all(len(one) <= maker.PER_PASS for one in made), "more than a few creates in one pass"
    asked = creates(gh)
    assert sorted(asked) == sorted("bot-" + slug for slug in team), "a bot was left without a repository"
    assert set(asked.values()) == {1}, f"GitHub was asked twice for {[n for n, k in asked.items() if k > 1]}"
    assert progress(api) == {"made": 20, "taken": 0, "waiting": 0, "problem": ""}
    # Every bot's turn now gets a token for its own repository, so its computer can publish its history.
    for slug in team:
        answer = token(api, gh, slug)
        assert answer.status_code == 200, f"{slug}: {answer.text}"
        assert answer.json()["repository"] == f"{ORG}/bot-{slug}", slug
    audited = rows(api, "SELECT actor,detail FROM events WHERE action='github.repo_created'")
    assert len(audited) == 20 and {r["actor"] for r in audited} == {"system"}
    # Nothing more is asked of GitHub once they are all there.
    calls = len(gh.calls)
    assert make(api, passes=3) == [[], [], []] and len(gh.calls) == calls


def test_a_create_whose_answer_was_lost_is_this_companys_on_the_next_try_never_someone_elses(company, gh):
    api = company
    connect(api)
    gh.lose_next_create_response = True  # GitHub made the repository; its answer never arrived
    assert make(api, passes=1) == [[]]
    assert progress(api)["problem"], "Tools > GitHub says nothing about the create that failed"
    api.clock.forward(maker.LONGEST_WAIT)
    make(api)
    assert progress(api) == {"made": 20, "taken": 0, "waiting": 0, "problem": ""}
    assert not rows(api, "SELECT 1 FROM registry_metadata WHERE key='bot-repositories-taken' AND value LIKE '%bot-%'")
    lost = next(name for name, count in creates(gh).items() if count == 2)
    answer = token(api, gh, lost.removeprefix("bot-"))
    assert answer.status_code == 200 and answer.json()["repository"] == f"{ORG}/{lost}", answer.text
    # By hand, the same: `tico bot repo-create` on one GitHub already made for this company says so.
    again = api.post(
        "/api/v2/github/repos", json={"slug": lost.removeprefix("bot-"), "empty": True}, headers=signed_in()
    )
    assert again.status_code == 200 and again.json()["existing"] is True, again.text


@pytest.mark.parametrize(
    "refusal, words",
    [((502, {}), "HTTP 502"), ((403, {"retry-after": "60"}), "limiting requests"), ((429, {}), "limiting requests")],
)
def test_github_failing_or_limiting_waits_longer_each_time_then_finishes(company, gh, refusal, words):
    api = company
    connect(api)
    make(api, passes=2)
    assert progress(api)["made"] == 2 * maker.PER_PASS
    gh.create_refusal = refusal
    asked = sum(creates(gh).values())
    make(api, passes=1)
    assert sum(creates(gh).values()) == asked + 1, "after a refusal the pass went on asking GitHub"
    said = progress(api)
    assert words in said["problem"] and "try again" in said["problem"], said
    # It waits: a minute, then two; the poll in between asks GitHub nothing.
    make(api, passes=3)
    assert sum(creates(gh).values()) == asked + 1
    api.clock.forward(60)
    make(api, passes=1)
    assert sum(creates(gh).values()) == asked + 2
    api.clock.forward(60)
    make(api, passes=1)
    assert sum(creates(gh).values()) == asked + 2, "the second wait was no longer than the first"
    # By hand meanwhile: the answer says to try again, not to create it manually.
    by_hand = api.post("/api/v2/github/repos", json={"slug": "updates-owner", "empty": True}, headers=signed_in())
    assert by_hand.status_code == 502 and by_hand.json()["error"]["retryable"] is True, by_hand.text
    assert "manually" not in by_hand.json()["error"]["detail"]
    gh.create_refusal = None
    api.clock.forward(maker.LONGEST_WAIT)
    make(api)
    assert progress(api) == {"made": 20, "taken": 0, "waiting": 0, "problem": ""}
    assert sorted(gh.created_repositories) == sorted(f"{ORG}/bot-{slug}" for slug in bots(api))


def test_a_refused_permission_stops_it_until_the_owner_accepts_it_again(company, gh):
    api = company
    connect(api)
    make(api, passes=1)
    gh.permissions = {**gh.permissions, "administration": "read"}  # the organization took Administration back
    api.clock.forward(120)  # past the minute the live permissions are trusted for
    api.app.state.github_app.live.clear()
    asked, calls = sum(creates(gh).values()), None
    make(api, passes=1)
    calls = len(gh.calls)
    assert make(api, passes=5) == [[]] * 5
    assert len(gh.calls) == calls and sum(creates(gh).values()) == asked, "it kept asking GitHub after the refusal"
    status = api.get("/api/v2/github/app", headers=signed_in()).json()
    assert status["administration"] is False and status["bot_repositories"]["waiting"] == 20 - maker.PER_PASS
    gh.permissions = {**gh.permissions, "administration": "write"}
    checked = api.post("/api/v2/github/app/check-permissions", json={}, headers=signed_in())
    assert checked.json()["administration"] is True, checked.text
    make(api)
    assert progress(api)["made"] == 20


def test_a_name_someone_else_has_is_left_alone_and_the_rest_are_made(company, gh):
    api = company
    gh.taken = {f"{ORG}/bot-assistant"}  # there for years, not in the App's listing: another company's
    connect(api)
    make(api)
    assert progress(api) == {"made": 19, "taken": 1, "waiting": 0, "problem": ""}
    assert creates(gh)["bot-assistant"] == 1, "a taken name was asked for again"
    answer = token(api, gh, "assistant")  # the bot works from its computer: no token, and no refusal either
    assert answer.status_code == 200 and answer.json()["repository"] == "" and answer.json()["history"], answer.text


def test_a_bot_hired_later_gets_one_the_same_way(company, gh):
    api = company
    connect(api)
    make(api)
    hired = api.post("/api/v2/bots", json={"display_name": "Data Analyst"}, headers=signed_in())
    assert hired.status_code == 200, hired.text
    slug = hired.json()["slug"]
    place(api)
    assert progress(api)["waiting"] == 1
    make(api, passes=2)
    assert creates(gh)["bot-" + slug] == 1 and progress(api)["waiting"] == 0
    assert token(api, gh, slug).json()["repository"] == f"{ORG}/bot-{slug}"


def test_installed_on_chosen_repositories_or_without_the_box_ticked_nothing_is_made(company, gh):
    api = company
    gh.selection, gh.selected_repositories = "selected", {"tico"}
    connect(api)
    assert make(api, passes=3) == [[], [], []] and not creates(gh)


def test_a_second_return_from_github_lands_on_tools_and_changes_nothing(company, gh):
    api = company
    state = connect(api)
    saved = rows(api, "SELECT app_id,slug,org FROM github_app")
    exchanges = len(gh.of("/conversions"))
    # Back to GitHub's Create page and Create again, or the same return loaded twice: a used-up state.
    for code in ("c1", "c2"):
        again = api.get(
            "/api/v2/github/app/callback",
            params={"code": code, "state": state},
            headers={**signed_in(), **BROWSER},
            follow_redirects=False,
        )
        assert again.status_code == 302 and again.headers["location"] == "/#/tools/github?github=again", again.text
    assert rows(api, "SELECT app_id,slug,org FROM github_app") == saved, "a second return replaced the saved App"
    assert len(gh.of("/conversions")) == exchanges, "a second return's code was exchanged"
    # An agent's call still gets the refusal as data.
    agent = api.get("/api/v2/github/app/callback", params={"code": "c1", "state": state}, headers=signed_in())
    assert agent.status_code == 400 and agent.json()["error"]["code"] == "github_state"
    # Before any App is saved, a stale return says the link expired.
    api.post("/api/v2/github/app/disconnect", json={}, headers=signed_in())
    stale = api.get(
        "/api/v2/github/app/callback",
        params={"code": "c1", "state": state},
        headers={**signed_in(), **BROWSER},
        follow_redirects=False,
    )
    assert stale.headers["location"] == "/#/tools/github?github=expired"


def test_one_tap_on_the_product_repository_gives_each_seat_its_access(company, gh):
    api = company
    connect(api)
    make(api)
    listed = api.get("/api/v2/repositories", headers=signed_in()).json()
    # Nothing is put In use for the person; the one product repository is there to choose.
    assert [r["full_name"] for r in listed["repositories"] if not r["bot_repo"] and not r["enabled"]] == [f"{ORG}/tico"]
    assert not [r for r in listed["repositories"] if r["enabled"]]
    tapped = api.put(f"/api/v2/repositories/{ORG}/tico", json={"enabled": True}, headers=signed_in())
    assert tapped.status_code == 200, tapped.text
    want = {
        "feature-owner": "write",
        "release-owner": "write",
        "reviewer-1": "read",
        "reviewer-2": "read",
        "qa-owner": "read",
        "product-manager": "read",
        "engineering-manager": "read",
    }
    for slug, level in want.items():
        answer = token(api, gh, slug).json()
        mine = [t for t in answer["tokens"] if f"{ORG}/tico" in t["repositories"]]
        assert [t["access"] for t in mine] == [level], f"{slug} should {level} {ORG}/tico: {json.dumps(answer)[:300]}"
    # The write is the seat's by role, with no card for the owner: a push or a pull request is never a third sign-off.
    assert not rows(api, "SELECT 1 FROM assistant_actions")
    needs = api.get("/api/v2/needs-you", headers=signed_in()).json()
    assert not [i for i in needs.get("items", []) if "repositor" in json.dumps(i).lower()], needs
    # A person sees it on the bot's Repositories: All repos in use, Write (the older github-repos list stays empty).
    seen = api.get("/api/v2/bots/feature-owner/repositories", headers=signed_in()).json()
    assert (seen["mode"], seen["all_access"]) == ("all", "write")
    assert {"full_name": f"{ORG}/tico", "access": "write"}.items() <= next(
        r for r in seen["effective"] if r["full_name"] == f"{ORG}/tico"
    ).items()
    assert api.get("/api/v2/bots/feature-owner/github-repos", headers=signed_in()).json()["repositories"] == []
    for slug in ("assistant", "botops", "general-manager", "growth-manager"):
        assert f"{ORG}/tico" not in token(api, gh, slug).json()["repositories"], slug
