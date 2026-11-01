"""Build a team (backend/team_build.py): a briefing suggests the roles a team needs, and the picks are hired into the team
in one step. Each pick is a seat then a bot, as a seat's own pick makes them (backend/tests/test_recruiting.py). The cloud
agents module (backend/cloud_agents.py) may or may not be there, so the suggestion is checked both ways."""
import json
import sys
import types

import pytest

import backend
from backend import groups as Groups
from backend import recruiting
from backend.store import Problem
from backend.tests.test_api import api, as_member, get, headers, post  # noqa: F401
from backend.tests.test_strategy_bot import as_bot

BRIEF = "A Rust API and a React web app, deployed on AWS, with GitHub pull requests and flaky tests"
CLOUD_ROLE = "Second pair of hands"
# Out of order on purpose: the role offers the agents Tico connects first, then the rest, each group in listing order.
CLOUDS = [{"id": "devin", "name": "Devin", "maker": "Cognition", "site": "devin.ai", "line": "A cloud engineer.",
           "supported": False, "connect_id": None},
          {"id": "claude", "name": "Claude Code", "maker": "Anthropic", "site": "claude.com", "line": "Codes in your repo.",
           "supported": True, "connect_id": "claude"},
          {"id": "droids", "name": "Droids", "maker": "Factory", "site": "factory.ai", "line": "Turns tickets into PRs.",
           "supported": False, "connect_id": None},
          {"id": "codex", "name": "Codex", "maker": "OpenAI", "site": "openai.com", "line": "Works in a sandbox.",
           "supported": True, "connect_id": "codex"}]
NOBODY = {"everyone": False, "people": [], "teams": [], "bots": []}


@pytest.fixture(params=[(False, False), (False, True), (True, False), (True, True)],
                ids=["absent", "absent-cached", "listed", "listed-cached"])
def cloud(request, monkeypatch):
    """backend/cloud_agents.py missing (its import fails) or there, listing its agents; and an earlier import of it either left
    on the package (`cached`) or not. `from . import cloud_agents` reads the package's attribute before the module table,
    so both are set for each case, and the test sees only what the case says."""
    listed, cached = request.param
    if cached:
        # An earlier import left a copy behind that answers the other way round: the suggestion must not read it.
        stale = types.ModuleType("backend.cloud_agents")
        stale.listing = (lambda: []) if listed else (lambda: [dict(agent) for agent in CLOUDS])
        monkeypatch.setattr(backend, "cloud_agents", stale, raising=False)
    if listed:
        module = types.ModuleType("backend.cloud_agents")
        module.listing = lambda: [dict(agent) for agent in CLOUDS]
        monkeypatch.setitem(sys.modules, "backend.cloud_agents", module)
        monkeypatch.setattr(backend, "cloud_agents", module, raising=False)
    else:
        monkeypatch.setitem(sys.modules, "backend.cloud_agents", None)     # the import fails
        monkeypatch.delattr(backend, "cloud_agents", raising=False)
    return listed


def providers(api, *enabled):
    current = get(api, "providers")
    assert api.put("/api/v2/providers", headers=headers(), json={
        "enabled": list(enabled), "expected_revision": current["revision"]}).status_code == 200


def engineering(api):
    return post(api, "groups", {"name": "Engineering"})["id"]


def suggest(api, team, briefing=BRIEF, token="ana-test", expected=200):
    r = api.post("/api/v2/team-build/suggest", headers=headers(token), json={"team": team, "briefing": briefing})
    assert r.status_code == expected, r.text
    return r.json()


def hire(api, team, picks, token="ana-test", expected=200, reports_to=""):
    r = api.post("/api/v2/team-build/hire", headers=headers(token),
                 json={"team": team, "reports_to": reports_to, "picks": picks})
    assert r.status_code == expected, r.text
    return r.json()


def counts(api):
    with api.app.state.store.read() as c:
        return (c.execute("SELECT COUNT(*) FROM seats WHERE closed_at IS NULL").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM bots").fetchone()[0])


def team_bots(api, team, token="ana-test"):
    return {bot for row in get(api, "groups", token=token) if row["id"] == team for bot in row["bots"]}


def lead_of(found):
    """The suggestion's role for the head of engineering."""
    return next(role for role in found["roles"] if role["pick"]["template"] == "engineering-lead")


def test_the_suggestion_is_the_departments_roles_head_first_with_compact_candidates(api, cloud):
    team = engineering(api)
    found = suggest(api, team)
    roles = found["roles"]
    # The template roles come first, the head first; the cloud role, when the agent is there, comes last and unchecked.
    templates = [role for role in roles if role["pick"]["source"] == "template"]
    clouds = [role for role in roles if role["pick"]["source"] == "cloud"]
    assert found["department"] == "engineering" and 2 <= len(templates) <= 8
    assert roles[:len(templates)] == templates
    assert len(clouds) == (1 if cloud else 0) and roles[len(templates):] == clouds
    for role in clouds:
        # The pick is the first agent Tico connects (added as a bot); the others follow under Change, connected first.
        assert role["role"] == CLOUD_ROLE and role["pick"]["id"] == "claude" and role["pick"]["supported"]
        assert [alt["id"] for alt in role["alternatives"]] == ["codex", "devin", "droids"]
        assert [alt["supported"] for alt in role["alternatives"]] == [True, False, False]
        for agent in [role["pick"], *role["alternatives"]]:
            assert agent["source"] == "cloud" and agent["template"] == "" and len(agent["line"]) <= 90
        assert not role["checked"] and not role["on_team"]
    assert templates[0]["pick"]["template"] == "engineering-lead"
    assert templates[0]["checked"] and not templates[0]["on_team"]
    picked = [role["pick"]["template"] for role in templates]
    for role in templates:
        pick = role["pick"]
        assert set(pick) == {"template", "name", "icon", "department", "line", "tags", "owns", "prerequisites", "match",
                             "source"}
        assert pick["source"] == "template" and pick["department"] == "engineering"
        assert pick["match"] in ("strong", "good", "") and len(pick["line"]) <= 90 and "\n" not in pick["line"]
        assert len(role["alternatives"]) <= 6
        assert not {alt["template"] for alt in role["alternatives"]} & set(picked)      # never a role's own pick
    assert any(role["pick"]["match"] == "strong" for role in templates)                 # the briefing names GitHub work
    # A team that is not a department (a name no template family has) suggests nothing, rather than someone else's roles.
    custom = post(api, "groups", {"name": "Ops crew"})["id"]
    assert suggest(api, custom) == {"team": custom, "department": "", "roles": []}


def test_a_role_a_bot_on_the_team_already_fills_is_on_the_team_and_not_offered_again(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    hired = hire(api, team, [{"role": "Head of Engineering", "template": "engineering-lead"}])
    assert [bot["template"] for bot in hired["bots"]] == ["engineering-lead"]
    found = suggest(api, team)
    lead = lead_of(found)
    assert lead["on_team"] and not lead["checked"] and lead["alternatives"] == []
    # The role names the bot that fills it, not the template it was built from.
    slug = hired["bots"][0]["slug"]
    assert lead["bot"]["slug"] == slug and lead["bot"]["name"] == get(api, f"bots/{slug}")["display_name"]
    assert all(role["pick"]["template"] != "engineering-lead" for role in found["roles"] if not role["on_team"])


def test_a_bot_the_caller_cannot_see_is_neither_named_nor_counted_on_the_team(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    slug = hire(api, team, [{"role": "Head of Engineering", "template": "engineering-lead"}])["bots"][0]["slug"]
    # Its name is its own (a template's name is public), so a leak of the name would show.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET display_name=? WHERE slug=?", ("Rhea Quill", slug))
    name = "Rhea Quill"
    current = get(api, f"bots/{slug}/access")
    r = api.put(f"/api/v2/bots/{slug}/access", headers=headers(), json={
        "see": NOBODY, "read": NOBODY, "write": NOBODY, "revision": current["revision"]})
    assert r.status_code == 200, r.text
    # The owner sees the bot on the team, by its name. Cara is on none of its lists: the role is open, the bot hidden.
    owners = lead_of(suggest(api, team))
    assert owners["bot"] == {"slug": slug, "name": name, "icon": owners["pick"]["icon"]}
    assert slug not in team_bots(api, team, token="cara-test")
    seen_by_cara = suggest(api, team, token="cara-test")
    head = lead_of(seen_by_cara)
    assert not head["on_team"] and head["bot"] is None and head["checked"]
    text = json.dumps(seen_by_cara)
    assert slug not in text and name not in text


def test_a_bot_the_permissions_do_not_name_is_hidden_from_the_team_even_for_the_owner(api, monkeypatch):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    slug = hire(api, team, [{"role": "Head of Engineering", "template": "engineering-lead"}])["bots"][0]["slug"]
    assert lead_of(suggest(api, team))["on_team"]
    # The permissions come back without this bot: a missing entry is hidden, not open to everyone.
    auth = api.app.state.auth
    real = auth.bot_accesses
    monkeypatch.setattr(auth, "bot_accesses",
                        lambda c, who, slugs=None: {k: v for k, v in real(c, who, slugs).items() if k != slug})
    found = suggest(api, team)
    head = lead_of(found)
    assert not head["on_team"] and head["bot"] is None and head["checked"]
    assert slug not in json.dumps(found)


def test_the_team_and_the_callers_access_are_read_as_one_snapshot(api, monkeypatch):
    """A bot that leaves the team after the permissions are read is still on the team for that answer: the permissions and
    the membership come from one snapshot of the database (store.read_transaction)."""
    providers(api, "anthropic", "openai")
    team = engineering(api)
    slug = hire(api, team, [{"role": "Head of Engineering", "template": "engineering-lead"}])["bots"][0]["slug"]
    store, auth = api.app.state.store, api.app.state.auth
    real = auth.bot_accesses
    moved = []

    def then_leave_the_team(c, who, slugs=None):
        out = real(c, who, slugs)
        if not moved:
            moved.append(slug)
            with store.transaction() as writer:           # another connection commits while this answer is being read
                Groups._set_bot(writer, slug, "")
        return out
    monkeypatch.setattr(auth, "bot_accesses", then_leave_the_team)
    before = lead_of(suggest(api, team))
    assert moved == [slug]
    assert before["on_team"] and before["bot"]["slug"] == slug
    # The next answer reads the move.
    after = lead_of(suggest(api, team))
    assert not after["on_team"] and after["bot"] is None


def test_hiring_makes_one_seat_per_pick_in_the_team_with_the_head_first_and_fills_each(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    seats_before, bots_before = counts(api)
    # Picked out of order on purpose: the head is hired first, and the others hang from its bot.
    hired = hire(api, team, [{"role": "DevOps engineer", "template": "devops-engineer"},
                             {"role": "Head of Engineering", "template": "engineering-lead"},
                             {"role": "Security engineer", "template": "security-engineer"}])
    bots = {bot["template"]: bot for bot in hired["bots"]}
    assert [bot["template"] for bot in hired["bots"]] == ["engineering-lead", "devops-engineer", "security-engineer"]
    assert {bot["stage"] for bot in hired["bots"]} == {"onboarding"}
    head = bots["engineering-lead"]["slug"]
    assert get(api, f"bots/{head}")["reports_to"] == "human:ana"
    for template in ("devops-engineer", "security-engineer"):
        assert get(api, f"bots/{bots[template]['slug']}")["reports_to"] == head
    assert team_bots(api, team) == {bot["slug"] for bot in hired["bots"]}      # the team's group is theirs
    assert counts(api) == (seats_before, bots_before + 3)                       # every seat filled, none left open
    assert not any(seat["title"] in {"Head of Engineering", "DevOps Engineer"} for seat in get(api, "seats")["seats"])
    # The same team's suggestion now has those roles on the team.
    on = {role["pick"]["template"] for role in suggest(api, team)["roles"] if role["on_team"]}
    assert on == {"engineering-lead", "devops-engineer", "security-engineer"}


def test_a_pick_outside_the_team_a_repeat_or_none_is_refused_and_nothing_is_made(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    before = counts(api)
    hire(api, team, [{"role": "x", "template": "sales-lead"}], expected=422)          # another department's template
    hire(api, team, [{"role": "x", "template": "pr-reviewer"}, {"role": "y", "template": "pr-reviewer"}], expected=422)
    hire(api, team, [], expected=422)
    assert counts(api) == before


def test_a_refused_pick_partway_through_leaves_no_seat_and_no_bot(api, monkeypatch):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    before = counts(api)
    real = recruiting.hire
    calls = []

    def second_refused(auth, onboarding, c, who, seat_id, body):
        calls.append(body.template)
        if len(calls) == 2:
            raise Problem("botops", "BotOps builds this bot, and this team has no BotOps; add it from Templates first", 409)
        return real(auth, onboarding, c, who, seat_id, body)
    monkeypatch.setattr(recruiting, "hire", second_refused)
    hire(api, team, [{"role": "a", "template": "pr-reviewer"}, {"role": "b", "template": "docs-writer"}], expected=409)
    assert counts(api) == before and team_bots(api, team) == set()


def test_a_member_cannot_hire_into_a_team_they_do_not_lead_and_an_admin_can(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    # Ben is an admin in the fixture, so he may hire into the team.
    hired = hire(api, team, [{"role": "x", "template": "pr-reviewer"}], token="ben-test")
    assert [bot["template"] for bot in hired["bots"]] == ["pr-reviewer"]
    # As a plain member he may not, even reporting to himself, which a seat alone would allow: nothing is made.
    as_member(api, "ben@acme.example")
    before = counts(api)
    hire(api, team, [{"role": "x", "template": "docs-writer"}], token="ben-test", expected=403, reports_to="human:ben")
    assert counts(api) == before and len(team_bots(api, team)) == 1


def test_only_a_person_suggests_and_hires(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    bot = as_bot(api, "coo")
    r = api.post("/api/v2/team-build/suggest", headers=bot, json={"team": team, "briefing": BRIEF})
    assert r.status_code == 403
    r = api.post("/api/v2/team-build/hire", headers=bot,
                 json={"team": team, "reports_to": "", "picks": [{"role": "x", "template": "pr-reviewer"}]})
    assert r.status_code == 403


def test_an_unknown_team_is_not_found(api):
    suggest(api, "no-such-team", expected=404)
    hire(api, "no-such-team", [{"role": "x", "template": "pr-reviewer"}], expected=404)
