#!/usr/bin/env python3
"""Who the humans are, and which of them a bot works for.

`registry/people.yaml` is the roster: the groups, the people, and who is the primary user of each
bot. `registry/employees.yaml` says who reports to whom. A group (`org_groups`: id, name, an optional
parent group) holds humans (their `team`) and bots (their `team` in the bot's config). A bot with no
group of its own takes the one its `reports_to` chain reaches: a group's `root` bot, which only
rosters written before groups (a `teams` block) have. Every bot has a person too (whoever claims its
group or the bot in `primary_for`, else `default_user`).

Pure: a parsed document in, decisions out. No files, no network, no clock. `backend/store.py`
reads the YAML and hands the document here; `backend/tests/test_people.py` covers the rules.

  load(doc)                              -> roster           normalised, always usable
  is_developer(pid, roster)              -> bool             named in the `developers` list
  team_of(slug, employees, roster)       -> group | None     the bot's group id
  primary_users(slug, roster, employees) -> [person, ...]    never empty
  bots_of(person, roster, employees)     -> [slug, ...]      what that person is primary for
  may_chat(email, slug, roster, employees) -> bool           only an assigned person may chat
  by_team(roster)                        -> {group: [id]}
  group_chain(gid, roster)               -> [gid, parent, ...]

A missing or empty file still gives a working roster: the environment owner, and nothing else.
"""
DEFAULT_PERSON = {"id": "", "name": "", "email": "", "title": "",
                  "team": "", "primary_for": [], "bot": None, "reports_to": "",
                  "inbox_bot": None, "hidden": False, "photo": "",
                  "slack": "", "slack_id": "", "github": "", "notify_slack_task_done": True,
                  "notify_slack_bot_messages": True, "slack_muted_bots": [], "phone": "", "about": "", "goals": "", "notes": "",
                  "directory": "", "external_id": "", "directory_left": False,
                  # What a member may do (docs/permissions.md): create bots (on by default), and add people
                  # (None = the default: coworkers in the company's email domain may, others may not).
                  "create_bots": True, "add_people": None}


def _clean(value):
    return str(value or "").strip()


def _list(values):
    return [_clean(v) for v in (values or []) if _clean(v)]


def _person(row):
    row = row if isinstance(row, dict) else {}
    pid = _clean(row.get("id")) or _clean(row.get("email")).split("@")[0]
    if not pid:
        return None
    reports_to = _clean(row.get("reports_to"))
    if reports_to == pid:
        reports_to = ""
    photo = _clean(row.get("photo"))
    if photo and not photo.startswith(("https://", "http://", "/")):
        photo = ""
    return {"id": pid,
            "name": _clean(row.get("name")) or pid.title(),
            "email": _clean(row.get("email")).lower(),
            "title": _clean(row.get("title")),
            "team": _clean(row.get("team")),
            "primary_for": _list(row.get("primary_for")),
            "bot": _clean(row.get("bot")) or None,
            "reports_to": reports_to,
            "inbox_bot": _clean(row.get("inbox_bot")) or None,
            "hidden": bool(row.get("hidden")),
            "photo": photo,
            "slack": _clean(row.get("slack")).lstrip("@"),
            "slack_id": _clean(row.get("slack_id")),
            # Their GitHub login, which a review request on a linked pull request names (backend/github.py).
            "github": _clean(row.get("github")).lstrip("@").lower(),
            "notify_slack_task_done": row.get("notify_slack_task_done") is not False,
            "notify_slack_bot_messages": row.get("notify_slack_bot_messages") is not False,
            "slack_muted_bots": _list(row.get("slack_muted_bots")),
            "phone": _clean(row.get("phone")),
            "about": _clean(row.get("about")),
            "goals": _clean(row.get("goals")),
            "notes": _clean(row.get("notes")),
            # Which directory feed owns this person ("" = added by hand, never touched by sync).
            "directory": _clean(row.get("directory")),
            "external_id": _clean(row.get("external_id")),
            "directory_left": bool(row.get("directory_left")),
            "create_bots": row.get("create_bots") is not False,
            # Off: still on the roster and the chart, but refused at sign-in and on every token (backend/auth.py).
            "sign_in": row.get("sign_in") is not False,
            "add_people": row.get("add_people") if isinstance(row.get("add_people"), bool) else None}


def load(doc, owner=None):
    """The roster, normalised.

    `owner` is the environment's owner as `{"id", "email"}`. It only fills in a document that
    names no `default_user`, so a brand new environment still has exactly one person and no
    company's address is ever invented for somebody else.
    """
    doc = doc if isinstance(doc, dict) else {}
    owner = owner if isinstance(owner, dict) else {}
    people, seen = [], set()
    for row in doc.get("people") or []:
        person = _person(row)
        if person and person["id"] not in seen:
            seen.add(person["id"])
            people.append(person)
    roots = {}
    for name, body in (doc.get("teams") or {}).items():
        root = _clean((body or {}).get("root")) if isinstance(body, dict) else _clean(body)
        if _clean(name) and root:
            roots[_clean(name)] = root
    owner_id = _clean(owner.get("id")) or _clean(owner.get("email")).split("@")[0]
    default = _clean(doc.get("default_user")) or owner_id or (people[0]["id"] if people else "")
    if default and not any(p["id"] == default for p in people):
        people.insert(0, {**DEFAULT_PERSON, "id": default, "name": default.title(),
                          "email": _clean(owner.get("email")).lower() if default == owner_id else "",
                          "reports_to": "", "inbox_bot": None})
    groups, gseen = {}, set()
    for name, body in (doc.get("org_groups") or {}).items():
        gid = _clean(name)
        if not gid or gid in gseen or not isinstance(body, dict):
            continue
        gseen.add(gid)
        groups[gid] = {"id": gid, "name": _clean(body.get("name")) or gid.replace("-", " ").title(),
                       "reports_to": _clean(body.get("reports_to")), "parent": _clean(body.get("parent")),
                       "root": _clean(body.get("root"))}
    for gid, root in roots.items():             # a roster written before groups: each team is a group
        group = groups.setdefault(gid, {"id": gid, "name": gid.replace("-", " ").title(), "reports_to": "",
                                        "parent": "", "root": ""})
        group["root"] = group["root"] or root
    for gid, group in groups.items():           # a parent that is not a group, or that leads back here, is no parent
        at, seen = group["parent"], {gid}
        while at in groups and at not in seen:
            seen.add(at)
            at = groups[at]["parent"]
        if at in seen or group["parent"] not in groups:
            group["parent"] = ""
    return {"default_user": default, "people": people, "developers": _list(doc.get("developers")),
            "org_groups": groups, "teams": {gid: {"root": g["root"]} for gid, g in groups.items() if g["root"]},
            "groups_migrated": bool(doc.get("groups_migrated"))}


def group_chain(gid, roster):
    """[gid, its parent, its parent's parent, ...]: the groups a member of `gid` is in. Empty for no such group."""
    groups = (roster or {}).get("org_groups") or {}
    out, at = [], _clean(gid)
    while at in groups and at not in out:
        out.append(at)
        at = groups[at].get("parent") or ""
    return out


def group_and_below(gid, roster):
    """`gid` and every group nested under it, at any depth."""
    groups = (roster or {}).get("org_groups") or {}
    found = {_clean(gid)} if _clean(gid) in groups else set()
    changed = True
    while changed:
        changed = False
        for other, group in groups.items():
            if other not in found and group.get("parent") in found:
                found.add(other)
                changed = True
    return found


def is_developer(pid, roster):
    """Does this person have developer access to the hub and the bot repos? (`developers` in
    registry/people.yaml.) It changes no permission here; it is context, so an answer knows
    whether to talk about repos and pull requests or about the app."""
    return _clean(pid) in set((roster or {}).get("developers") or [])


def person(pid, roster):
    return next((p for p in (roster or {}).get("people") or [] if p["id"] == _clean(pid)), None)


def person_by_email(email, roster):
    """A roster person by verified email. Unknown allowed-domain users deliberately miss."""
    wanted = _clean(email).lower()
    return next((p for p in (roster or {}).get("people") or []
                 if str(p.get("email") or "").lower() == wanted), None)


def default_person(roster):
    roster = roster or load({})
    return person(roster.get("default_user"), roster) or dict(DEFAULT_PERSON)


def team_of(slug, employees, roster):
    """The group a bot is in: the one its config names (`team`, "" for none), else the one its manager is in, up the
    `reports_to` chain, else the group whose `root` bot it reports up to (a roster from before groups).

    `employees` is {slug: entry} as the registry gives it; a root bot is in its own group.
    """
    groups = (roster or {}).get("org_groups") or {}
    roots = {g["root"]: gid for gid, g in groups.items() if g.get("root")}
    at, seen = _clean(slug), set()
    while at:
        if at in seen or at.startswith("human:"):
            return None                    # no group of its own and none up this chain
        entry = (employees or {}).get(at) or {}
        named = entry.get("team")
        if named is not None and (_clean(named) in groups or not _clean(named)):
            return _clean(named) or None   # "" says: in no group, whatever its manager is in
        if at in roots:
            return roots[at]
        seen.add(at)
        at = _clean(entry.get("reports_to"))
    return None


def primary_users(slug, roster, employees):
    """Everyone who is primary for this bot: by its slug, by its group (or one that group is in), else the default user."""
    roster = roster or load({})
    slug = _clean(slug)
    entry = (employees or {}).get(slug) or {}
    # The cloud registry can replace broad team/default ownership for one bot. This is an
    # explicit list rather than an additive exception, so an owner's company-wide `*` may be
    # removed from an individual bot without weakening the fallback for every other bot.
    if "owner_ids" in entry:
        wanted_ids = set(_list(entry.get("owner_ids")))
        explicit = [p for p in roster["people"] if p["id"] in wanted_ids]
        if explicit:
            return explicit
    wanted = {slug, *group_chain(team_of(slug, employees, roster), roster)} - {""}
    hits = [p for p in roster["people"] if "*" in p["primary_for"] or wanted & set(p["primary_for"])]
    # A bot placed under a person on the org chart is that person's to talk to.
    boss = reports_to_person(slug, employees)
    if boss and all(p["id"] != boss for p in hits):
        lead = person(boss, roster)
        if lead:
            hits.append(lead)
    return hits or [default_person(roster)]


def reports_to_person(slug, employees):
    """The person a bot reports to directly (`reports_to: human:<id>`), or ''."""
    reports = _clean(((employees or {}).get(_clean(slug)) or {}).get("reports_to"))
    return reports[6:] if reports.startswith("human:") else ""


def manages(pid, kind, ident, roster, employees, archived=()):
    """Whether `pid` sits above this person or bot on the org chart: the one permission rule for
    editing someone else's profile, bot or place in the chart (users can
    edit their own info, or the info of their subordinate bots/humans)."""
    pid = _clean(pid)
    if not pid:
        return False
    if kind == "person" and _clean(ident) == pid:
        return True
    parent, seen = org_parent(kind, ident, roster, employees, archived), set()
    while parent and parent not in seen:
        seen.add(parent)
        if parent == "p:" + pid:
            return True
        head, _, rest = parent.partition(":")
        if head == "g":
            group = ((roster or {}).get("org_groups") or {}).get(rest) or {}
            parent = ("p:" + _clean(group.get("reports_to"))) if _clean(group.get("reports_to")) else ""
        elif head == "p":
            parent = org_parent("person", rest, roster, employees, archived)
        elif head == "b":
            parent = org_parent("bot", rest, roster, employees, archived)
        else:
            parent = ""
    return False


def bots_of(pid, roster, employees):
    """The bots this person is the primary user of, in registry order."""
    return [slug for slug in (employees or {})
            if any(p["id"] == _clean(pid) for p in primary_users(slug, roster, employees))]


def may_chat(email, slug, roster, employees):
    """True only when this verified person is one of the bot's computed primary users.

    `primary_users` includes the documented `default_user` fallback, so an otherwise unclaimed
    bot still has exactly one person who can talk to it. An email that Cloudflare allowed but the
    people registry does not know gets no chat access.
    """
    who = person_by_email(email, roster)
    if not who:
        return False
    return any(p["id"] == who["id"] for p in primary_users(slug, roster, employees))


def by_team(roster):
    """{group: [person id, ...]} for every group in the roster, plus the ones people name."""
    roster = roster or load({})
    out = {name: [] for name in roster.get("org_groups") or {}}
    for p in roster["people"]:
        if p["team"]:
            out.setdefault(p["team"], []).append(p["id"])
    return out


def brief(person_row):
    """What a bot page shows about a person: no title, no team, just who to talk to."""
    out = {"id": person_row["id"], "name": person_row["name"], "email": person_row["email"]}
    if person_row.get("photo"):
        out["photo"] = person_row["photo"]
    from . import people_photos
    if person_row.get("id") and people_photos.may_have(None, person_row.get("email"), person_row.get("photo")):
        out["photo_url"] = "/api/humans/" + person_row["id"] + "/photo"
    return out


def visible_people(roster):
    """People shown on the org tree and people pages. Hidden rows stay on the roster for mail."""
    return [p for p in (roster or {}).get("people") or [] if not p.get("hidden")]


def team_lead(team, roster):
    """The person in charge of a bot group: first visible person who claims that group by name, else the group it is in.

    A company-wide `*` claim is not a group lead. None if nobody claims the group that way.
    """
    team = _clean(team)
    if not team:
        return None
    for gid in group_chain(team, roster) or [team]:
        for p in visible_people(roster):
            claimed = set(p.get("primary_for") or [])
            if gid in claimed and "*" not in claimed:
                return p
    return None


def org_group_under(team, lead_id, roster):
    """Department id when `team` is a named group hanging under this lead, else None."""
    g = ((roster or {}).get("org_groups") or {}).get(_clean(team))
    if g and g.get("reports_to") == _clean(lead_id):
        return g["id"]
    return None


def org_parent(kind, ident, roster, employees, archived=()):
    """Parent key for the mixed org tree: `p:<id>`, `b:<slug>`, `g:<group>`, or '' for a root.

    People follow `reports_to`, except a named department under that boss becomes `g:<team>`.
    A bot follows its `reports_to` bot when that bot is still on the tree; otherwise it hangs
    under its team's department group, else the person who leads the team, else the founder.
    """
    archived = set(archived or ())
    if kind == "person":
        p = person(ident, roster)
        if not p or p.get("hidden"):
            return ""
        boss = person(_clean(p.get("reports_to")), roster)
        if not boss or boss.get("hidden"):
            return ""
        group = org_group_under(p.get("team"), boss["id"], roster)
        return ("g:" + group) if group else "p:" + boss["id"]
    slug = _clean(ident)
    if not slug or slug in archived:
        return ""
    reports = _clean(((employees or {}).get(slug) or {}).get("reports_to"))
    while reports and reports in archived:
        reports = _clean(((employees or {}).get(reports) or {}).get("reports_to"))
    if reports and not reports.startswith("human:") and reports not in archived and reports not in (employees or {}):
        # A boss that is not a bot here at all (an assistant the company chose not to have):
        # the bot hangs under the owner instead of vanishing from the chart.
        default = (roster or {}).get("default_user")
        reports = "human:" + _clean(default) if default else reports
    if reports.startswith("human:"):
        boss = person(reports[6:], roster)
        if boss and not boss.get("hidden"):
            group = org_group_under(team_of(slug, employees, roster), boss["id"], roster)
            return ("g:" + group) if group else "p:" + boss["id"]
        reports = ""
    if reports and reports not in archived and reports in (employees or {}):
        return "b:" + reports
    team = team_of(slug, employees, roster)
    lead = team_lead(team, roster)
    hang = lead or next((p for p in visible_people(roster) if not _clean(p.get("reports_to"))), None)
    if not hang:
        return ""
    group = org_group_under(team, hang["id"], roster)
    return ("g:" + group) if group else "p:" + hang["id"]


def profile(person_row):
    """What a bot (or a person page) needs to know about a human: who they are, how to reach them."""
    p = person_row or {}
    handles = [x for x in (p.get("primary_for") or []) if x != "*"]
    if "*" in (p.get("primary_for") or []):
        handles = ["*"] + handles
    return {"id": p.get("id") or "",
            "name": p.get("name") or "",
            "email": p.get("email") or "",
            "title": p.get("title") or "",
            "team": p.get("team") or "",
            "primary_for": list(p.get("primary_for") or []),
            "handles": handles,
            "bot": p.get("bot"),
            "reports_to": p.get("reports_to") or "",
            "slack": p.get("slack") or "",
            "slack_id": p.get("slack_id") or "",
            "github": p.get("github") or "",
            "notify_slack_task_done": p.get("notify_slack_task_done") is not False,
            "notify_slack_bot_messages": p.get("notify_slack_bot_messages") is not False,
            "slack_muted_bots": list(p.get("slack_muted_bots") or []),
            "phone": p.get("phone") or "",
            "about": p.get("about") or "",
            "goals": p.get("goals") or "",
            "notes": p.get("notes") or ""}


def org_view(roster, employees, archived=(), person_id="", team=""):
    """The mixed org chart for bots and the hub: visible people (with contact and goals),
    live bots, and the groups, each with an `org_parent` key
    (`p:<id>` / `b:<slug>` / `g:<group>` / ''). A person or bot carries its group as `team`; a group carries
    its `parent` group. `team` keeps one group and the groups under it."""
    archived = set(archived or ())
    employees = employees or {}
    wanted, team = _clean(person_id), _clean(team)
    inside = group_and_below(team, roster) if team else set()
    people_rows = []
    for p in visible_people(roster):
        if team and p.get("team") not in inside and not (inside & set(p.get("primary_for") or [])) \
                and "*" not in (p.get("primary_for") or []):
            continue
        row = profile(p)
        row["org_parent"] = org_parent("person", p["id"], roster, employees, archived)
        people_rows.append(row)
    bot_rows = []
    for slug, entry in employees.items():
        if slug in archived:
            continue
        bot_team = team_of(slug, employees, roster)
        if team and bot_team not in inside:
            continue
        bot_rows.append({
            "id": slug,
            "name": slug,
            "display_name": _clean((entry or {}).get("display_name")) or slug,
            "description": _clean((entry or {}).get("description")),
            "team": bot_team or "",
            "reports_to": _clean((entry or {}).get("reports_to")),
            "org_parent": org_parent("bot", slug, roster, employees, archived),
            "owners": [u["id"] for u in primary_users(slug, roster, employees)],
        })
    group_rows = []
    for i, (gid, g) in enumerate(((roster or {}).get("org_groups") or {}).items()):
        if team and gid not in inside:
            continue
        boss = person(g.get("reports_to"), roster)
        parent = ("g:" + g["parent"]) if g.get("parent") else ("p:" + boss["id"]) if boss and not boss.get("hidden") else ""
        group_rows.append({"id": gid, "name": g.get("name") or gid, "reports_to": g.get("reports_to") or "",
                           "parent": g.get("parent") or "", "org_parent": parent, "order": i})
    if wanted:
        keep = {"p:" + wanted}
        changed = True
        while changed:
            changed = False
            for row in group_rows:
                key = "g:" + row["id"]
                if row["org_parent"] in keep and key not in keep:
                    keep.add(key)
                    changed = True
            for row in people_rows:
                key = "p:" + row["id"]
                if row["org_parent"] in keep and key not in keep:
                    keep.add(key)
                    changed = True
            for row in bot_rows:
                key = "b:" + row["id"]
                if row["org_parent"] in keep and key not in keep:
                    keep.add(key)
                    changed = True
        people_rows = [r for r in people_rows if "p:" + r["id"] in keep]
        bot_rows = [r for r in bot_rows if "b:" + r["id"] in keep]
        # The groups the people and bots kept are in, and the groups those are in, stay so the chart still reads.
        shown = {gid for r in people_rows + bot_rows for gid in group_chain(r["team"], roster)} | {
            r["id"] for r in group_rows if "g:" + r["id"] in keep}
        group_rows = [r for r in group_rows if r["id"] in shown]
    return {"people": people_rows, "bots": bot_rows, "org_groups": group_rows,
            "teams": dict((roster or {}).get("teams") or {})}


def bot_departments(employees, roster, archived=()):
    """{slug: group name} for every live bot that is in a group. A bot in none is left out."""
    employees, roster = employees or {}, roster or {}
    groups = roster.get("org_groups") or {}
    out = {}
    for slug in employees:
        team = team_of(slug, employees, roster) if slug not in set(archived or ()) else None
        if team in groups:
            out[slug] = groups[team].get("name") or team
    return out


def reports(pid, roster):
    """Direct reports of this person, in roster order."""
    pid = _clean(pid)
    return [p for p in (roster or {}).get("people") or [] if p.get("reports_to") == pid]


def below(pid, roster):
    """This person, then everyone who reports to them (recursively), in roster order.

    A missing or cyclic `reports_to` is skipped. The person themselves is always first when
    they exist, even if they have no email yet.
    """
    roster = roster or {}
    root = person(pid, roster)
    if not root:
        return []
    seen, out = {root["id"]}, [root]
    changed = True
    while changed:
        changed = False
        for p in roster.get("people") or []:
            if p["id"] in seen or p.get("reports_to") not in seen:
                continue
            seen.add(p["id"])
            out.append(p)
            changed = True
    return out


def mailboxes_below(pid, roster):
    """Email addresses this person may process: their own, then everyone below them.

    Empty addresses are dropped. Order is stable: self first, then roster order of reports.
    """
    seen, out = set(), []
    for p in below(pid, roster):
        email = _clean(p.get("email")).lower()
        if email and "@" in email and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def inbox_person(slug, roster):
    """The person whose `inbox_bot` is this employee slug, or None."""
    slug = _clean(slug)
    if not slug:
        return None
    return next((p for p in (roster or {}).get("people") or [] if p.get("inbox_bot") == slug), None)
