"""Build a team (backend/team_build.py): a briefing suggests the roles a team needs, and the picks are hired into the team
in one step. Each pick is a seat then a bot, as a seat's own pick makes them (backend/tests/test_recruiting.py)."""
from backend import recruiting
from backend.store import H, Problem
from backend.tests.test_api import api, as_member, get, headers, post  # noqa: F401
from backend.tests.test_strategy_bot import as_bot

BRIEF = "A Rust API and a React web app, deployed on AWS, with GitHub pull requests and flaky tests"


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


def team_bots(api, team):
    return {bot for row in get(api, "groups") if row["id"] == team for bot in row["bots"]}


def test_the_suggestion_is_the_departments_roles_head_first_with_compact_candidates(api):
    team = engineering(api)
    found = suggest(api, team)
    roles = found["roles"]
    assert found["department"] == "engineering" and 2 <= len(roles) <= 8
    assert roles[0]["pick"]["template"] == "engineering-lead" and roles[0]["checked"] and not roles[0]["on_team"]
    templates = [role["pick"]["template"] for role in roles]
    for role in roles:
        pick = role["pick"]
        assert set(pick) == {"template", "name", "icon", "department", "line", "tags", "owns", "prerequisites", "match",
                             "source"}
        assert pick["source"] == "template" and pick["department"] == "engineering"
        assert pick["match"] in ("strong", "good", "") and len(pick["line"]) <= 90 and "\n" not in pick["line"]
        assert len(role["alternatives"]) <= 6
        assert not {alt["template"] for alt in role["alternatives"]} & set(templates)     # never a role's own pick
    assert any(role["pick"]["match"] == "strong" for role in roles)                       # the briefing names GitHub work
    # A team that is not a department (a name no template family has) suggests nothing, rather than someone else's roles.
    custom = post(api, "groups", {"name": "Ops crew"})["id"]
    assert suggest(api, custom) == {"team": custom, "department": "", "roles": []}


def test_a_role_a_bot_on_the_team_already_fills_is_on_the_team_and_not_offered_again(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    hired = hire(api, team, [{"role": "Head of Engineering", "template": "engineering-lead"}])
    assert [bot["template"] for bot in hired["bots"]] == ["engineering-lead"]
    found = suggest(api, team)
    lead = next(role for role in found["roles"] if role["pick"]["template"] == "engineering-lead")
    assert lead["on_team"] and not lead["checked"] and lead["alternatives"] == []
    # The role names the bot that fills it, not the template it was built from.
    slug = hired["bots"][0]["slug"]
    assert lead["bot"]["slug"] == slug and lead["bot"]["name"] == get(api, f"bots/{slug}")["display_name"]
    assert all(role["pick"]["template"] != "engineering-lead" for role in found["roles"] if not role["on_team"])


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


def test_only_a_person_suggests_and_hires_and_a_member_cannot_place_under_someone_else(api):
    providers(api, "anthropic", "openai")
    team = engineering(api)
    bot = as_bot(api, "coo")
    r = api.post("/api/v2/team-build/suggest", headers=bot, json={"team": team, "briefing": BRIEF})
    assert r.status_code == 403
    r = api.post("/api/v2/team-build/hire", headers=bot,
                 json={"team": team, "reports_to": "", "picks": [{"role": "x", "template": "pr-reviewer"}]})
    assert r.status_code == 403
    # A member may build under himself; under the owner, the seat rule refuses, and nothing is hired.
    as_member(api, "ben@acme.example")
    before = counts(api)
    hire(api, team, [{"role": "x", "template": "pr-reviewer"}], token="ben-test", expected=403, reports_to="human:ana")
    assert counts(api) == before


def test_an_unknown_team_is_not_found(api):
    suggest(api, "no-such-team", expected=404)
    hire(api, "no-such-team", [{"role": "x", "template": "pr-reviewer"}], expected=404)
