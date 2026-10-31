"""Build a team (the web page "Build a team", ui/app/team-build.js): a briefing suggests the roles a team needs, and the
picks are hired into that team in one step.

`suggest` answers from the local recommender (backend/recruit_rank.py) only, so the briefing never leaves this server and
Tico HQ is not asked. Its roles are the team's department's suggested templates in the recommender's order. A role's
alternatives are the next templates that the team does not already have, and `on_team` marks a role that a bot already on
the team fills (a bot built from that template). Only the bots the caller may see count, here and in `hire`.

`hire` makes one Recruiting seat per pick in the team, then fills each with the seat page's own call
(backend/recruiting.py `hire`). The department head goes first, so the other seats report to it as they do when hired one
at a time. The whole request is one transaction: a refused pick leaves nothing behind.
"""
from typing import Annotated

from fastapi import Request
from pydantic import Field

from . import groups as Groups
from . import hubdb as H
from . import lifecycle
from . import models as M
from . import recruit
from . import recruit_rank as R
from . import recruiting
from .onboarding import load as load_onboarding, read_cards
from .store import Problem

ROLES = 8               # the roles a team's suggestion shows at most, and the picks one request may hire
ALTERNATIVES = 6        # the other candidates a role offers under Change
LINE = 90               # a candidate's one line, in characters
STRONG, GOOD = 6, 3     # how well a candidate fits the briefing, on recruit_rank's own score
CLOUD_ID = "devin"      # the cloud agent the team offers last, unchecked, as a second pair of hands
CLOUD_ROLE = "Second pair of hands"
OWNS = 3                # a candidate's owns, shown in the seat's job description and on the card


class TeamSuggest(M.Contract):
    team: Annotated[str, Field(min_length=1, max_length=100)]
    briefing: Annotated[str, Field(max_length=500)] = ""


class TeamPick(M.Contract):
    role: Annotated[str, Field(min_length=1, max_length=120)]
    template: Annotated[str, Field(min_length=1, max_length=80)]


class TeamHire(M.Contract):
    team: Annotated[str, Field(min_length=1, max_length=100)]
    reports_to: Annotated[str, Field(max_length=200)] = ""     # `human:<id>`, a bot's slug or `seat:<id>`; the caller by default
    picks: Annotated[list[TeamPick], Field(min_length=1, max_length=ROLES)]


# ------------------------------------------------------------------ who may
def _may_read(who):
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Only a person builds a team", 403)


def _may_build(who):
    """The same rule as a seat's pick (backend/recruiting.py `_may_hire`): a person, not for someone else unconfirmed."""
    if who.role not in ("owner", "human") or (who.via and not who.confirmed):
        raise Problem("forbidden", "Only a person hires a team: the owner, or someone the seats report up to", 403)


# ------------------------------------------------------------------ the team
def _group(c, auth, who, team):
    """The team as this caller sees it: its row in the sidebar (backend/groups.py `rows`, filtered as GET /v2/groups
    filters it), so a bot they cannot see is neither named nor counted as filling its role."""
    access = auth.bot_accesses(c, who)

    def seen(slug):
        return bool(access.get(slug, {}).get("see"))      # a bot the permissions do not name is hidden, not open
    row = next((g for g in Groups.rows(Groups._roster(c), Groups._configs(c), Groups._archived(c), seen)
                if g["id"] == team), None)
    if not row:
        raise Problem("not_found", "Team not found", 404)
    return row


def _department(value, group):
    """The department a team builds from: its own id (a team setup made), else the one with its name, else none."""
    ids = {row["id"] for row in value["departments"]}
    if group["id"] in ids:
        return group["id"]
    by_name = {row["name"].strip().lower(): row["id"] for row in value["departments"]}
    return by_name.get(group["name"].strip().lower(), "")


def _filled(c, group):
    """{template: the bot's slug} for the live bots on the team, by the template each one was built from."""
    configs = Groups._configs(c)
    return {configs[slug].get("template"): slug for slug in group["bots"] if configs.get(slug, {}).get("template")}


def _bot(c, slug, icon):
    """The bot on the team that fills a role: its slug, the name it goes by, and its template's icon."""
    return {"slug": slug, "name": (H.bot(c, slug) or {}).get("display_name") or slug, "icon": icon}


def _about(c, who):
    """What the company is and its strategy bets, as the org builder's recommender reads them (backend/recruit.py)."""
    bets = recruit.strategy_bets(c, who)
    return {**recruit._about(load_onboarding(c)["answers"]), "bets": recruit.bets_text(bets)}


# ------------------------------------------------------------------ candidates
def _briefing(value):
    return " ".join(str(value or "").split())[:R.BRIEFING_LIMIT]


def _match(card, words, common):
    score, _ = R._match(card, words, common)
    return "strong" if score >= STRONG else "good" if score >= GOOD else ""


def _item(card, raw, match):
    """A candidate as the web app shows it (the contract in the build spec): a Tico template, one line about it, its
    tags, what it owns and what it needs."""
    return {"template": card["template"], "name": card["name"], "icon": card["icon"], "department": card["department"],
            "line": R._first_sentence(card["summary"], LINE), "tags": card["tags"][:8],
            "owns": [item[:160] for item in raw.get("owns", [])[:OWNS]],
            "prerequisites": [{"tool": row["tool"], "required": row["required"]} for row in raw.get("prerequisites", [])[:4]],
            "match": match, "source": "template"}


def _cloud_agent():
    """Devin as a cloud agent, when backend/cloud_agents.py is there to list them (GET /v2/cloud-agents); else none."""
    try:
        from . import cloud_agents
    except ImportError:
        return None
    listing = getattr(cloud_agents, "listing", None)
    agent = next((row for row in listing() if row.get("id") == CLOUD_ID), None) if callable(listing) else None
    if not agent:
        return None
    return {"id": agent["id"], "name": agent["name"], "maker": agent.get("maker", ""), "site": agent.get("site", ""),
            "line": str(agent.get("line") or "")[:LINE], "supported": bool(agent.get("supported")),
            "connect_id": agent.get("connect_id"), "source": "cloud", "template": "", "icon": "", "department": "",
            "tags": [], "owns": [], "prerequisites": [], "match": ""}


def suggest(auth, onboarding, c, who, team, briefing=""):
    """The roles a team of bots needs for this briefing: one per suggested template, the head first, each with its
    alternatives and whether a bot on the team already fills it. Deterministic: the same answer gives the same roles."""
    _may_read(who)
    group = _group(c, auth, who, team)
    value = recruit.catalog(onboarding.settings)
    dept = _department(value, group)
    if not dept:
        return {"team": team, "department": "", "roles": []}
    text = _briefing(briefing)
    ranked = R.rank(value, dept, text, _about(c, who), limit=len(value["cards"]))
    picked = [row["template_id"] for row in ranked["bots"]]
    members = {card["template"]: card for card in value["cards"] if card["department"] == dept}
    filled = _filled(c, group)
    words, common = R._words(text), R._common(list(members.values()))
    raw = {row["template"]: row for row in read_cards(onboarding.settings)}
    suggested = set(ranked["suggested_default"])
    shown = picked[:ROLES]
    # The rest of the department, the recommender's leftovers first, as the alternatives a role can take instead.
    rest = picked[ROLES:] + sorted((t for t in members if t not in picked),
                                                key=lambda t: ({"default": 0, "common": 1}.get(members[t]["suggest"], 2),
                                                               members[t]["name"].lower()))
    pool = [t for t in rest if t not in filled and t not in shown][:ALTERNATIVES]
    roles = []
    for template in shown:
        card = members[template]
        match = _match(card, words, common)
        on_team = template in filled
        roles.append({"role": card["name"], "pick": _item(card, raw.get(template, {}), match),
                      "alternatives": [] if on_team else [_item(members[t], raw.get(t, {}), _match(members[t], words, common))
                                                          for t in pool],
                      "on_team": on_team,
                      "bot": _bot(c, filled[template], card["icon"]) if on_team else None,
                      "checked": not on_team and (template in suggested or match in ("strong", "good"))})
    agent = _cloud_agent()
    if agent:
        roles.append({"role": CLOUD_ROLE, "pick": agent, "alternatives": [], "on_team": False, "checked": False})
    return {"team": team, "department": dept, "roles": roles}


# ------------------------------------------------------------------ hire
def _job(raw):
    """The job description a seat takes from its card: the card's summary, then what the role owns."""
    lines = [raw.get("summary", "")]
    if raw.get("owns"):
        lines += ["Owns:"] + ["- " + item for item in raw["owns"][:12]]
    return "\n".join(line for line in lines if line).strip()


def hire(auth, onboarding, c, who, team, reports_to, picks):
    """Hire the picks into the team: one seat each, the head first and the rest under it, then each seat filled as
    backend/recruiting.py `hire` fills one. Returns the bots made, in the order they were hired."""
    _may_build(who)
    group = _group(c, auth, who, team)
    Groups.may_add(c, auth, who, team)      # a seat in the team is an add to it, the rule a move into it obeys
    value = recruit.catalog(onboarding.settings)
    dept = _department(value, group)
    offered = {card["template"]: card for card in recruiting._recruitable(onboarding)[1] if card["department"] == dept}
    chosen, seen = [], set()
    for pick in picks:
        card = offered.get(pick.template)
        if not card:
            raise Problem("template", "Pick one of the templates this team offers", 422)
        if pick.template in seen:
            raise Problem("template", "Pick each template once", 422)
        seen.add(pick.template)
        chosen.append((pick.role, card))
    if not chosen:
        raise Problem("picks", "Pick at least one role", 422)
    chosen.sort(key=lambda item: not item[1]["lead"])        # the head first: the seats under it hang from its bot
    filled = _filled(c, group)
    head_bot = filled.get(R.head_of(value, dept)) if dept else None
    owner = str(reports_to or "").strip() or who.actor
    seats, head_seat = [], None
    for role, card in chosen:
        if card["lead"]:
            parent = owner
        elif head_seat:
            parent = lifecycle.SEAT_PREFIX + head_seat
        else:
            parent = head_bot or owner
        seat = lifecycle.create_seat(auth, c, who, card["name"], _job(card["raw"]), parent, team,
                                     description_by=recruiting.CHART)
        if card["lead"]:
            head_seat = seat["id"]
        seats.append((role, card, seat["id"]))
    bots = []
    for role, card, seat_id in seats:
        hired = recruiting.hire(auth, onboarding, c, who, seat_id, M.SeatHire(template=card["template"]))
        if (Groups._configs(c).get(hired["slug"]) or {}).get("team") != team:
            Groups._set_bot(c, hired["slug"], team)     # the team's group is the bot's own (backend/groups.py)
        bots.append({"slug": hired["slug"], "name": card["name"], "template": card["template"], "role": role,
                     "stage": hired["stage"]})
    H.event(c, who.actor, "team.built", team, {"bots": [bot["slug"] for bot in bots]})
    return {"team": team, "bots": bots}


# ------------------------------------------------------------------ routes
def install(app, store, auth, mutate, onboarding):
    @app.post("/api/v2/team-build/suggest")
    def team_build_suggest(request: Request, body: TeamSuggest):
        """The roles a team needs for a briefing, from the local recommender: no outside service."""
        who = request.state.identity
        auth.domain(who)
        # One snapshot: the team's membership and the caller's access to its bots are read as they stood together.
        with store.read_transaction() as c:
            return suggest(auth, onboarding, c, who, body.team, body.briefing)

    @app.post("/api/v2/team-build/hire")
    def team_build_hire(request: Request, body: TeamHire):
        """Hire the picked roles into a team: a seat each, then each seat filled (backend/recruiting.py `hire`)."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return hire(auth, onboarding, c, who, body.team, body.reports_to, body.picks)
        return mutate(request, body, work)
