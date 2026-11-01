"""Groups: how a team is divided into sub-teams (docs/org-chart.md).

A group is an id, a name, an optional parent group and its teammates, humans and bots. Groups nest. There is
no table of their own: the groups are `org_groups` in the roster (`registry_metadata` key `people`), a human is in
the group named by their `team`, and a bot in the one named by `team` in its config. A teammate is in one group at a
time. Who reports to whom (`reports_to`) is a separate thing and is never touched here.

  rows(...)                a group's row: id, name, parent, people, bots
  create / update / delete the changes the API and the tools make
  place(...)               the team builder: bots into the groups of their templates, made if they are not there yet
  team_groups(...)         a company file's team names as groups, one per name (company_import)
  link_bets(...)           setup's links from the groups it made to the strategy bets they serve
  migrate(c, settings)     once, for a team that had `teams`, org groups or derived departments: every one becomes a
                           group and every bot its group's member (idempotent; `groups_migrated` in the roster)

Built-in bots (the Assistant, BotOps, the Librarian, the Strategy Bot) and helpers stay outside groups.
"""
import json
import re

from . import access as Access
from . import hubdb as H
from . import models as M
from . import people as P
from . import task_caps
from .store import Problem, encode

SYSTEM_BOTS = ("assistant", "botops", "librarian", "goal-manager", "health-monitor", "task-manager", "bookkeeper", H.COUNSEL)


def _roster(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    return P.load(json.loads(row[0]) if row else {"people": H.humans(c)})


def _configs(c):
    """{slug: config with its `reports_to`}: what `team_of` reads."""
    out = {}
    for row in c.execute("SELECT bot,config,reports_to FROM bot_config"):
        config = json.loads(row["config"] or "{}")
        config["reports_to"] = row["reports_to"]
        out[row["bot"]] = config
    return out


def _archived(c):
    return {row["slug"] for row in c.execute("SELECT slug FROM bots WHERE state='archived'")}


def slug_of(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40]


def outside(settings, slug, config, helpers=None):
    """Built-in bots and helpers are not on the chart's groups."""
    if slug in SYSTEM_BOTS or slug == getattr(settings, "assistant_bot", ""):
        return True
    if helpers is None:
        from . import onboarding as O
        helpers = O.helper_templates(settings)
    return str((config or {}).get("template") or "") in helpers


def rows(roster, employees, archived=(), can_see=None, leads=False):
    """Every group in roster order: id, name, parent, and its `people` (ids) and `bots` (slugs)."""
    people, bots = {}, {}
    for person in P.visible_people(roster):
        if person.get("team"):
            people.setdefault(person["team"], []).append(person["id"])
    for slug in employees or {}:
        if slug in archived or (can_see and not can_see(slug)):
            continue
        team = P.team_of(slug, employees, roster)
        if team:
            bots.setdefault(team, []).append(slug)
    return [{"id": gid, "name": g["name"], "parent": g["parent"], **({"lead": g.get("lead") or ""} if leads else {}),
             "people": people.get(gid, []), "bots": sorted(bots.get(gid, [])), "order": g.get("order", i),
             "bet_id": g.get("bet_id")}
            for i, (gid, g) in sorted(enumerate(roster["org_groups"].items()), key=lambda row: row[1][1].get("order", row[0]))]


def refresh_teams(c, roster=None):
    """`bot_config.team`, the group each bot is in, read by the pages that list bots."""
    roster = roster or _roster(c)
    configs = _configs(c)
    for slug in configs:
        c.execute("UPDATE bot_config SET team=? WHERE bot=?", (P.team_of(slug, configs, roster), slug))


def _save(c, roster, people=None):
    Access.save_roster(c, {**roster, "groups_migrated": True, **({"people": people} if people is not None else {})})


def _set_bot(c, slug, gid):
    declared = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()[0] or "{}")
    if declared.get("team") and declared["team"] != gid:
        task_caps.forget(c, "bot:" + slug)      # its caps were its old group's (CONTRACT §3)
    was, declared["team"] = declared.get("team") or "", gid
    c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(declared), slug))
    _follow(c, "bot:" + slug, was, gid)


def _follow(c, owner, was, gid):
    """Automations go with their owner to its new group."""
    from . import automations
    automations.follow(c, owner, was, gid)


def _named(groups, name, parent, skip=""):
    wanted = name.strip().lower()
    return any(g["id"] != skip and g["parent"] == parent and g["name"].strip().lower() == wanted for g in groups.values())


def _new_id(groups, name):
    base = slug_of(name) or "group"
    gid, n = base, 1
    while gid in groups:
        n += 1
        gid = f"{base}-{n}"
    return gid


def _check_members(c, settings, roster, configs, people, bots):
    for pid in people:
        person = P.person(pid, roster)
        if not person or person.get("hidden"):
            raise Problem("not_found", "Unknown human: " + pid, 404)
    for slug in bots:
        row = H.bot(c, slug)
        if not row or row.get("state") == "archived":
            raise Problem("not_found", "Unknown bot: " + slug, 404)
        if outside(settings, slug, configs.get(slug)):
            raise Problem("built_in", "Built-in bots stay outside groups", 422)


def _place(c, roster, gid, people, bots):
    """Put these humans and bots in `gid` (each leaves the group it was in); returns the changed human rows."""
    rosters = list(roster["people"])
    moved = []
    for i, person in enumerate(rosters):
        if person["id"] in people and person["team"] != gid:
            if person["team"]:
                task_caps.forget(c, "human:" + person["id"])
            rosters[i] = {**person, "team": gid}
            moved.append(rosters[i])
            _follow(c, "person:" + person["id"], person["team"], gid)
    for slug in bots:
        _set_bot(c, slug, gid)
    return rosters, moved


def _apply_members(c, settings, roster, gid, add, remove):
    configs = _configs(c)
    _check_members(c, settings, roster, configs, add.people, add.bots)
    people = list(roster["people"])
    changed = []
    for i, person in enumerate(people):
        if person["id"] in remove.people and person["team"] == gid:
            task_caps.forget(c, "human:" + person["id"])
            people[i] = {**person, "team": ""}
            changed.append(people[i])
            _follow(c, "person:" + person["id"], gid, "")
    for slug in remove.bots:
        if P.team_of(slug, configs, roster) == gid and slug in configs:
            _set_bot(c, slug, "")
    roster = {**roster, "people": people}
    people, moved = _place(c, roster, gid, add.people, add.bots)
    return people, changed + moved


def _finish(c, roster, people, changed):
    _save(c, roster, people)
    for row in changed:
        Access._sync_human(c, row)
    roster = _roster(c)
    refresh_teams(c, roster)
    return roster


def one(c, roster, gid, can_see=None):
    configs = _configs(c)
    return next(row for row in rows(roster, configs, _archived(c), can_see, True) if row["id"] == gid)


def _may_change(auth, who):
    if not (who.role == "owner" or auth.bot_admin(who)):
        raise Problem("forbidden", "Only an owner or an admin changes groups", 403)


def led_groups(c, who, roster=None):
    """The groups `who` (an actor) leads and every group below them."""
    roster = roster or _roster(c)
    configs = _configs(c)
    out = set()
    for gid in roster["org_groups"]:
        lead = lead_of(gid, roster, configs)
        if lead and task_caps.actor(lead) == who:
            out |= P.group_and_below(gid, roster)
    return out


def _lead_move(c, who, gid, body, roster):
    """A lead's move inside what it leads (CONTRACT §3): only an `add`, into a group it leads or one below, of teammates
    already in such a group. Anything else from a lead who is not an admin is the owner's."""
    if any(v is not None for v in (body.name, body.parent, body.lead, body.bet_id)) or body.remove.people \
            or body.remove.bots or not (body.add.people or body.add.bots):
        return False
    return _led_add(c, who, gid, roster, body.add.people, body.add.bots)


def _led_add(c, who, gid, roster, people=(), bots=()):
    """Whether a lead may add these people and bots to `gid`: a group it leads or one below, and each of them already
    in such a group. A bot being hired has no group yet, so a hire passes none and asks only about the group."""
    led = led_groups(c, who.actor, roster)
    if gid not in led:
        return False
    configs = _configs(c)
    homes = [P.team_of(slug, configs, roster) for slug in bots] + \
        [(P.person(pid, roster) or {}).get("team") for pid in people]
    return all(home in led for home in homes)


def may_add(c, auth, who, gid, people=(), bots=()):
    """Refuse (403) unless `who` may add these to group `gid`, the same rule `update` applies to an add: an owner, an
    admin, or a lead of the group or of one above it. Any route that puts a bot in a group asks this first."""
    if who.role == "owner" or auth.bot_admin(who) or _led_add(c, who, gid, _roster(c), people, bots):
        return
    raise Problem("forbidden", "Only an owner, an admin or the team's lead can add to it", 403)


def _snapshot(c):
    """Every group's row, by id, for the audit."""
    return {row["id"]: row for row in rows(_roster(c), _configs(c), _archived(c), leads=True)}


def _audit(c, actor, before, via=None):
    """One audit row per group a change touched (CONTRACT §5, object `group:<id>`): a move changes the group it left
    and the one it joined."""
    if before is None:
        return
    from . import audit_log
    after = _snapshot(c)
    for gid in sorted(set(before) | set(after)):
        if before.get(gid) != after.get(gid):
            audit_log.write(c, actor, f"group:{gid}", before.get(gid), after.get(gid), via)



def _set_bet(c, who, group, bet_id):
    """The strategy bet a group serves: the owner's link, like the bets themselves. "" unlinks it."""
    if bet_id is None:
        return
    from . import goal_routes
    from . import strategy
    if not goal_routes.company_editor(who):
        raise Problem("forbidden", "Only the owner links a group to a strategy bet", 403)
    bet_id = bet_id.strip()
    if bet_id and not strategy.bet(c, bet_id):
        raise Problem("not_found", "Unknown bet: " + bet_id, 404)
    if bet_id:
        group["bet_id"] = bet_id
    else:
        group.pop("bet_id", None)


def _next_order(groups):
    # Freeze legacy fallback positions before a new key changes their indices in sorted JSON.
    for i, group in enumerate(groups.values()):
        group.setdefault("order", i)
    return max((group["order"] for group in groups.values()), default=-1) + 1


def _lead_ref(value):
    """A group's lead as CONTRACT §3 writes it: `bot:<slug>` or `person:<id>`; "" for none."""
    text = str(value or "").strip()
    if not text:
        return ""
    kind, _, ident = text.partition(":")
    kind = "person" if kind == "human" else kind
    if kind not in ("bot", "person") or not ident:
        raise Problem("validation", "The lead is bot:<slug> or person:<id>", 422)
    return f"{kind}:{ident}"


def is_member(ref, gid, roster, configs):
    """Whether a teammate ref (`bot:<slug>`, `person:<id>`) is in the group `gid` itself."""
    kind, _, ident = str(ref or "").partition(":")
    if kind == "bot":
        return ident in configs and P.team_of(ident, configs, roster) == gid
    if kind in ("person", "human"):
        return (P.person(ident, roster) or {}).get("team") == gid
    return False


def lead_of(gid, roster, configs):
    """The group's lead while it is one of the group's teammates, else "": a lead who left leads nothing, whatever way
    it left (CONTRACT §3)."""
    lead = (roster["org_groups"].get(gid) or {}).get("lead") or ""
    return lead if lead and is_member(lead, gid, roster, configs) else ""


def _lead_check(c, roster, configs, gid, lead):
    """A lead asked for must exist and be a teammate of the group."""
    if not lead:
        return
    kind, _, ident = lead.partition(":")
    exists = (kind == "bot" and ident in configs) or (kind == "person" and P.person(ident, roster))
    if not exists:
        raise Problem("not_found", "Unknown lead: " + lead, 404)
    if not is_member(lead, gid, roster, configs):
        raise Problem("validation", "The lead must be in the group", 422)


def leads_follow(c, cause):
    """A lead who left its group (an add to another group, a remove, its own
    move) is cleared in the same write: one audit row per group, the hub's own change with `via` naming the change that
    moved it, and one notice to the top of the org."""
    roster, configs = _roster(c), _configs(c)
    gone = {gid: g["lead"] for gid, g in roster["org_groups"].items()
            if g.get("lead") and not lead_of(gid, roster, configs)}
    if not gone:
        return
    before = _snapshot(c)
    for gid in gone:
        roster["org_groups"][gid]["lead"] = ""
    _save(c, roster)
    _audit(c, H.KEEPER, before, cause)
    from .proposals import decider
    top = decider(c)
    for gid, lead in sorted(gone.items()):
        if top:
            who = task_caps.actor(lead) or lead
            H.say(c, H.KEEPER, top, f"{task_caps._name(c, who)} left {roster['org_groups'][gid]['name']}; "
                                    "it has no lead now.", kind="notice", refs={"group": gid})


def create(c, auth, settings, who, body, via=None):
    _may_change(auth, who)
    before = _snapshot(c)
    roster = _roster(c)
    groups = roster["org_groups"]
    parent = (body.parent or "").strip()
    if parent and parent not in groups:
        raise Problem("not_found", "Unknown group: " + parent, 404)
    if _named(groups, body.name, parent):
        raise Problem("conflict", "There is already a group with that name there", 409)
    gid = _new_id(groups, body.name)
    groups[gid] = {"id": gid, "name": body.name.strip(), "reports_to": "", "parent": parent, "root": "",
                   "order": _next_order(groups)}
    _set_bet(c, who, groups[gid], body.bet_id)
    if body.lead:
        groups[gid]["lead"] = _lead_ref(body.lead)
    people, changed = _apply_members(c, settings, roster, gid, body.add, M.GroupMembers())
    roster = _finish(c, roster, people, changed)
    _lead_check(c, roster, _configs(c), gid, groups[gid].get("lead", ""))
    H.event(c, who.actor, "group.created", gid, {"name": body.name.strip(), "parent": parent})
    _audit(c, who.actor, before, via)
    leads_follow(c, via or f"group:{gid}")
    return one(c, _roster(c), gid)


def update(c, auth, settings, who, gid, body, via=None):
    roster = _roster(c)
    if not (who.role == "owner" or auth.bot_admin(who) or _lead_move(c, who, gid, body, roster)):
        _may_change(auth, who)
    before = _snapshot(c)
    groups = roster["org_groups"]
    if gid not in groups:
        raise Problem("not_found", "Group not found", 404)
    group = groups[gid]
    if body.parent is not None:
        parent = body.parent.strip()
        if parent and parent not in groups:
            raise Problem("not_found", "Unknown group: " + parent, 404)
        if parent and parent in P.group_and_below(gid, roster):
            raise Problem("hierarchy", "A group cannot be moved under itself or a group inside it", 422)
        group["parent"] = parent
    if body.name is not None:
        group["name"] = body.name.strip()
    _set_bet(c, who, group, body.bet_id)
    if body.lead is not None:
        group["lead"] = _lead_ref(body.lead)
    if _named(groups, group["name"], group["parent"], skip=gid):
        raise Problem("conflict", "There is already a group with that name there", 409)
    people, changed = _apply_members(c, settings, roster, gid, body.add, body.remove)
    roster = _finish(c, roster, people, changed)
    if body.lead is not None:
        _lead_check(c, roster, _configs(c), gid, group.get("lead", ""))
    H.event(c, who.actor, "group.updated", gid, {"name": body.name, "parent": body.parent, "bet_id": body.bet_id,
                                                  "added": {"people": body.add.people, "bots": body.add.bots},
                                                  "removed": {"people": body.remove.people, "bots": body.remove.bots}})
    _audit(c, who.actor, before, via)
    leads_follow(c, via or f"group:{gid}")
    return one(c, _roster(c), gid)


def delete(c, auth, who, gid):
    """The group goes; the groups and teammates in it move up to the group it was in (or to no group)."""
    _may_change(auth, who)
    before = _snapshot(c)
    roster = _roster(c)
    groups = roster["org_groups"]
    if gid not in groups:
        raise Problem("not_found", "Group not found", 404)
    up = groups[gid]["parent"]
    configs = _configs(c)
    for slug in configs:
        if P.team_of(slug, configs, roster) == gid:
            _set_bot(c, slug, up)
    for group in groups.values():
        if group["parent"] == gid:
            group["parent"] = up
    people, changed = [], []
    for person in roster["people"]:
        if person["team"] == gid:
            task_caps.forget(c, "human:" + person["id"])
            _follow(c, "person:" + person["id"], gid, up)
            person = {**person, "team": up}
            changed.append(person)
        people.append(person)
    from . import automations
    automations.regroup(c, gid, up)
    del groups[gid]
    c.execute("DELETE FROM subscription_assignments WHERE scope='group' AND target=?", (gid,))
    _finish(c, roster, people, changed)
    H.event(c, who.actor, "group.deleted", gid, {"moved_to": up})
    _audit(c, who.actor, before)
    return {"id": gid, "deleted": True, "moved_to": up}


# ----------------------------------------------------------------------------- the team builder
def ensure(roster, gid, name):
    """The id of the group for a template group: the one with that id, else with that name, else a new one."""
    groups = roster["org_groups"]
    if gid in groups:
        return gid
    for other, group in groups.items():
        if group["name"].strip().lower() == name.strip().lower():
            return other
    groups[gid] = {"id": gid, "name": name, "reports_to": "", "parent": "", "root": "",
                   "order": _next_order(groups)}
    return gid


def place(c, settings, slugs, group_order=()):
    """The team builder made these bots: each one goes in the group of its template (the group its card is in), which
    is made when the team has none yet. A bot that already has a group of its own keeps it."""
    from . import recruit
    configs = _configs(c)
    todo = [s for s in slugs if s in configs and configs[s].get("team") is None]
    if not todo:
        return
    homes = recruit.template_groups(settings)
    roster = _roster(c)
    made = set(roster["org_groups"])
    # Stored JSON sorts object keys, so bot creation order cannot preserve the wizard's chart order.
    # Only newly made groups take this order; an existing team's groups keep their positions.
    rank = {gid: i for i, gid in enumerate(group_order)}
    todo.sort(key=lambda slug: rank.get(homes.get(str(configs[slug].get("template") or ""), {}).get("id"), len(rank)))
    for slug in todo:
        home = homes.get(str(configs[slug].get("template") or ""))
        if home and not outside(settings, slug, configs[slug]):
            gid = ensure(roster, home["id"], home["name"])
            _set_bot(c, slug, gid)
    if set(roster["org_groups"]) != made:
        _save(c, roster)
    refresh_teams(c, roster)


def link_bets(c, links):
    """Setup's links from the groups it made to the strategy bets they serve ({group id: bet id}, from
    backend/recruit.py `bet_for`). A group already linked keeps its bet. Returns the group ids linked."""
    roster = _roster(c)
    done = []
    for gid, bet_id in (links or {}).items():
        group = roster["org_groups"].get(gid)
        if group and bet_id and not group.get("bet_id"):
            group["bet_id"] = bet_id
            done.append(gid)
    if done:
        _save(c, roster)
    return done


def seat_teams(c, settings, templates, group_order=()):
    """{template: group id} for seats setup lays out (backend/lifecycle.py `lay_out`): the group a bot of that template
    would go in (`place`), made when the team has none yet, new groups in `group_order` as `place` makes them. A template
    with no group gets none."""
    from . import recruit
    homes = recruit.template_groups(settings)
    roster = _roster(c)
    made, out = set(roster["org_groups"]), {}
    rank = {gid: i for i, gid in enumerate(group_order)}
    for template in sorted(templates, key=lambda t: rank.get((homes.get(str(t or "")) or {}).get("id"), len(rank))):
        home = homes.get(str(template or ""))
        if home:
            out[template] = ensure(roster, home["id"], home["name"])
    if set(roster["org_groups"]) != made:
        _save(c, roster)
    return out


def team_groups(c, names):
    """{team name: group id} for a company file's teams, `names` in file order. A name is the group with that name (any
    case, as `ensure` finds it), else a new group made at the top with the name's id: an id is never another group's,
    so a team named like a group of another name gets a group of its own. Made once per name."""
    roster = _roster(c)
    made, out = set(roster["org_groups"]), {}
    for name in names:
        if name not in out:
            out[name] = ensure(roster, _new_id(roster["org_groups"], name), name)
    if set(roster["org_groups"]) != made:
        _save(c, roster)
    return out


# ----------------------------------------------------------------------------- the migration
def migrate(c, settings):
    """Every team, org group and derived department becomes a group; every bot its group's member. Idempotent.

    * a `teams` entry (a group with a root bot) and an org group are one group each, by id; a human's `team` that
      names neither becomes one too;
    * a bot with no group of its own takes what the old rules gave it: its team, else its template's group in the
      team builder's catalog, else its manager's, up the `reports_to` chain;
    * every live bot then names its group in its config (`team`, "" for none), the roots go, and the roster says
      `groups_migrated`. `reports_to` is not touched.
    """
    row = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    if not row:
        return False
    doc = json.loads(row[0] or "{}")
    if doc.get("groups_migrated"):
        return False
    roster = P.load(doc)
    groups, before = roster["org_groups"], len(roster["org_groups"])
    retagged = []
    for person in doc.get("people") or []:
        team = str(person.get("team") or "").strip()
        if team and team not in groups:
            gid = slug_of(team) or team
            if gid not in groups:
                groups[gid] = {"id": gid, "name": team if gid != team else team.replace("-", " ").title(),
                               "reports_to": "", "parent": "", "root": ""}
            if gid != team:
                person["team"] = gid
                retagged.append(person)
    configs, archived = _configs(c), _archived(c)
    homes = []

    def template_home(template):
        if not homes:                        # the catalog is read once, and only if a bot needs it
            from . import recruit
            homes.append(recruit.template_groups(settings))
        return homes[0].get(template)

    from . import onboarding as O
    helpers = O.helper_templates(settings)
    own = {}
    for slug, config in configs.items():
        if slug in archived:
            continue
        team = P.team_of(slug, configs, roster)
        if team is None and config.get("team") is None and config.get("template") and not outside(settings, slug, config, helpers):
            home = template_home(str(config["template"]))
            team = ensure(roster, home["id"], home["name"]) if home else None
        if team:
            own[slug] = team
    placed = {}
    for slug, config in configs.items():
        if slug in archived:
            continue
        at, seen = slug, set()
        while at and at in configs and at not in seen and at not in archived:
            if at in own:
                placed[slug] = own[at]
                break
            seen.add(at)
            at = str(configs[at].get("reports_to") or "")
    for slug, config in configs.items():
        if slug not in archived and config.get("team") is None:      # a bot that names its group keeps it
            _set_bot(c, slug, "" if outside(settings, slug, config, helpers) else placed.get(slug, ""))
    doc["org_groups"] = {gid: {k: v for k, v in {"name": g["name"], "parent": g["parent"],
                                                  "reports_to": g["reports_to"]}.items() if v}
                         for gid, g in groups.items()}
    doc.pop("teams", None)
    doc["groups_migrated"] = True
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))
    for person in retagged:
        if H.human(c, person.get("id")):
            Access._sync_human(c, P._person(person))
    refresh_teams(c)
    H.event(c, H.KEEPER, "groups.migrated", "", {"groups": len(groups), "new": len(groups) - before,
                                                  "bots": sum(1 for g in placed.values() if g)})
    return True


# ----------------------------------------------------------------------------- routes
def install(app, store, auth, mutate, settings):
    from fastapi import Request

    @app.get("/api/v2/groups")
    def group_list(request: Request):
        """The groups (nested by `parent`) with their humans and the bots the caller may see."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            access = auth.bot_accesses(c, who)
            return rows(_roster(c), _configs(c), _archived(c), lambda slug: access.get(slug, auth.FULL)["see"], True)

    @app.post("/api/v2/groups")
    def group_create(request: Request, body: M.GroupCreate):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return create(c, auth, settings, who, body)
        return mutate(request, body, work)

    @app.patch("/api/v2/groups/{gid}")
    def group_update(request: Request, gid: str, body: M.GroupUpdate):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return update(c, auth, settings, who, gid, body)
        return mutate(request, body, work)

    @app.delete("/api/v2/groups/{gid}")
    def group_delete(request: Request, gid: str):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return delete(c, auth, who, gid)
        return mutate(request, M.Empty(), work)
