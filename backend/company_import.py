"""Apply a setup-page company file through the installed app's ordinary onboarding.

The file describes a team, never an identity or permission grant. The same adapter
serves the owner's file upload and the installer. Its caller owns the transaction.
"""
import hashlib
import json

from pydantic import ValidationError

from . import company_file as C
from . import goals as G
from . import groups as Groups
from . import lifecycle
from . import kpis as K
from . import models as M
from . import onboarding as O
from .company_file import draft as D
from .company_file import starter
from .company_file.yamlfile import tool_list
from .store import H, Problem, encode

PLAN_KEY = "onboarding.company_file"
FAILED_KEY = "onboarding.company_import_failed"


def plan(onboarding, c, who, text):
    """Validate and translate without changing the company or trusting file owner IDs.

    The chart is laid out exactly (docs/onboarding.md, "Setup picks the chart"): a built-in role (the Assistant, BotOps)
    is a bot; every other role is a Recruiting seat with the file's reporting line, a seat under a seat included; a role
    a person holds is that person's, kept for the owner to invite (`people`). A line to someone not in Tico yet (a
    person the owner hasn't invited) goes to the owner until they are invited."""
    onboarding._owner(who)
    try:
        saved = starter.load(text)
    except C.DraftError as exc:
        raise Problem("company_file", str(exc), 422) from None
    draft = saved
    owner = onboarding.auth.owner_id(c)
    if not owner or not H.human(c, owner):
        raise Problem("company_file", "Sign in as this company's owner before importing its starter team", 409)
    cards = O.read_cards(onboarding.settings)
    by_name = {name: card for card in cards for name in (card["template"], card["slug"])}
    required = {onboarding._bot_slug(card["slug"]): card["template"] for card in cards if card["required"]}
    home = "human:" + owner
    owner_id = D.owner_id(draft)
    held = {bot["id"]: bot["person"] for bot in draft["bots"] if bot.get("person")}
    people_ids = {p["id"] for p in draft["people"]}
    ids, builtins, roles = {}, {}, {}
    for bot in draft["bots"]:
        if bot["id"] in held:
            continue
        card = by_name.get(bot["template"])
        if card is not None and card["required"]:
            slug = onboarding._bot_slug(card["slug"])
            if slug in builtins:
                raise Problem("company_file", "An employee ID conflicts with another employee or a built-in role", 422)
            ids[bot["id"]] = slug
            builtins[slug] = (bot, card)
        elif onboarding._bot_slug(bot["id"]) in required or bot["id"] in required:
            raise Problem("company_file", "An employee ID conflicts with another employee or a built-in role", 422)
        else:
            roles[bot["id"]] = (bot, card)

    def holder(at):
        """Who a line ends at when it reaches a person: the owner, or a person not in Tico until invited."""
        person = held.get(at, at)
        return None if person == owner_id else person

    def line(bot):
        """(reports_to, waiting on person) for a seat: another seat's key, a built-in's slug or the owner."""
        parent = D.manager_of(draft, bot)
        if parent in roles:
            return parent, None
        if parent in ids:
            return ids[parent], None
        if parent == owner_id or parent in held or parent in people_ids:
            return home, holder(parent)
        raise Problem("company_file", "An employee's reporting line cannot be applied", 422)

    selected = {}
    try:
        names = M.OnboardingNames(company_name=draft["company"]["name"],
                                  owner_name=(D.owner(draft) or {"name": ""})["name"])
        display = O.display_names(onboarding.settings, {"names": names.model_dump()})
        for slug, (bot, card) in builtins.items():
            parent = D.manager_of(draft, bot)
            # A built-in is a bot, and a bot reports to a bot or a person: under a seat, it reports where that seat does.
            seen = set()
            while parent in roles and parent not in seen:
                seen.add(parent)
                parent = D.manager_of(draft, roles[parent][0])
            parent = ids.get(parent) or home
            if parent == slug:
                parent = home
            instructions = O.render(card, display, bot["title"])["instructions"]
            if bot["reason"]:
                instructions += "\n\nWhy this role is on the company chart:\n" + bot["reason"]
            selected[slug] = M.OnboardingSelection(template=card["template"], display_name=bot["title"],
                                                   instructions=instructions, reports_to=parent)
        body = M.OnboardingDraft(names=names, answers=M.OnboardingAnswers(what_we_do=draft["company"]["about"]),
                                 selected=selected)
        seats = {}
        for key, (bot, card) in roles.items():
            reports_to, waits = line(bot)
            seats[key] = {"title": M.SeatCreate(title=bot["title"]).title, "template": bot["template"],
                          "description": O.job_description(card or {}, display, bot["reason"]),
                          "reports_to": reports_to, "waits_on": waits}
    except ValidationError:
        raise Problem("company_file", "A company name, title or instruction is too long for this Tico setup", 422) from None
    titles = {}
    for bot in draft["bots"]:
        if bot["id"] in held:
            titles.setdefault(held[bot["id"]], []).append(bot["title"])
    people = [{"id": p["id"], "name": p["name"], "title": ", ".join(titles.get(p["id"]) or []) or p["role"]}
              for p in draft["people"] if p["id"] != owner_id]
    fingerprint = hashlib.sha256(encode(saved).encode()).hexdigest()
    deferred = []
    if "models" in saved:
        deferred.append("model_choices")
    if tool_list(draft):
        deferred.append("tool_connections")
    if any(b.get("first_job") for b in draft["bots"]):
        deferred.append("first_jobs")
    if any(b.get("routine") for b in draft["bots"]):
        deferred.append("routines")
    summary = {"fingerprint": fingerprint, "company_name": names.company_name,
               "employees": [{"id": slug, "template": choice.template, "title": choice.display_name,
                              "reports_to": choice.reports_to, "start": "now"}
                             for slug, choice in selected.items()],
               "seats": [{"id": key, "template": seat["template"], "title": seat["title"],
                          "reports_to": seat["reports_to"], **({"waits_on": seat["waits_on"]} if seat["waits_on"] else {})}
                         for key, seat in seats.items()],
               "people": people,
               "goals": [{"id": goal["id"], "title": goal_title(goal), "owner": goal_owner(goal, owner_id, ids, required)}
                         for goal in draft["goals"]],
               "builtins": sorted(required), "deferred": deferred}
    return body, saved, seats, summary


def goal_owner(goal, owner_id, ids, builtins):
    """Who owns an imported goal on Goals. The file names the company's owner for a company goal (setup's
    recruiting/goals.py gives every company goal to the owner) and keeps no level or parent, so the owner's goals are
    the company's. Every other role on the chart is a Recruiting seat, which holds no work (backend/lifecycle.py), so
    its goal is the company's until the seat is recruited; check() has already given a goal whose owner left the chart
    to the owner. The built-in bots own no goals (Goals keeps them out of its tree), so a goal the file gives one, the
    Assistant's in a starter plan, is the company's too."""
    slug = ids.get(goal["owner"])
    return G.COMPANY if goal["owner"] == owner_id or slug is None or slug in builtins else "bot:" + slug


def goal_title(goal):
    """The file may leave a goal's title blank; Tico names every goal, so it is called by its measure instead."""
    return goal["title"].strip() or goal["measure"] or "Untitled goal"


def target(goal):
    """The file's numbers as the KPI link's target: an improvement needs both a target and its deadline, a range
    a min or a max. Anything less is a measure with no target yet."""
    if goal["direction"] == "range":
        if goal["min"] is None and goal["max"] is None:
            return {"kind": "none"}
        return {"kind": "maintain", "min": goal["min"], "max": goal["max"]}
    if goal["target"] is None or not goal["deadline"]:
        return {"kind": "none"}
    return {"kind": "improve", "target": goal["target"], "baseline": goal["baseline"], "deadline": goal["deadline"]}


def import_goals(c, actor, goals):
    """Each goal under its owner, with its measure as a KPI on it. The file holds a baseline and a target but never
    readings, so no history is made up. A target Tico would refuse (one on the wrong side of its baseline) is left
    off for the owner to set; the goal and its measure still land."""
    for goal, owner in goals:
        made = G.create(c, actor, goal_title(goal), owner)
        if not goal["measure"]:
            continue
        kpi = G.kpi_create(c, actor, owner, {"name": goal["measure"], "unit": goal["unit"], "direction": goal["direction"]})
        aim = target(goal)
        G.kpi_link(c, actor, made["id"], kpi["id"], {"kind": "none"} if K.validate_target(kpi, aim)[1] else aim)


def preview(onboarding, c, who, text):
    return plan(onboarding, c, who, text)[3]


def apply(onboarding, c, who, text, fingerprint):
    """Apply a confirmed plan once; retries never rebuild or replace a configured team."""
    body, saved, seats, summary = plan(onboarding, c, who, text)
    if fingerprint != summary["fingerprint"]:
        raise Problem("company_file_changed", "The company file changed; preview it again before importing", 409)
    record = O.load(c)
    previous = record.get("company_import") or {}
    if previous.get("fingerprint") == fingerprint:
        clear_failure(c)        # this file is the one already applied, so nothing from setup is left unimported
        return onboarding.view(c, who)
    builtins = set(summary["builtins"])
    existing = [r[0] for r in c.execute("SELECT slug FROM bots") if r[0] not in builtins]
    laid = c.execute("SELECT 1 FROM seats WHERE closed_at IS NULL LIMIT 1").fetchone()
    if record["completed"] or existing or laid:
        raise Problem("company_already_setup", "This company is already set up; import cannot replace its team", 409)
    # Create definitions under the owner before installing the desired graph.
    # Otherwise complete() can see a temporary cycle through the old seed while
    # it resolves a newly-created parent. Neither staging nor a partial chart is
    # visible outside this transaction; the confirmed choices are restored below.
    home = "human:" + onboarding.auth.owner_id(c)
    staging = body.model_copy(update={"selected": {
        slug: choice.model_copy(update={"reports_to": home}) for slug, choice in body.selected.items()}})
    onboarding.save(c, who, staging)
    onboarding.complete(c, who)
    # complete() preserves existing definitions, including the seeded built-ins'
    # managers. Apply the confirmed chart after all targets exist, parents first
    # so replacing the old seed hierarchy cannot create a temporary cycle.
    def parent_rank(slug):
        parent = body.selected[slug].reports_to
        return 1 + parent_rank(parent) if parent in body.selected else 0

    for slug in sorted(body.selected, key=parent_rank):
        row = c.execute("SELECT reports_to,revision FROM bot_config WHERE bot=?", (slug,)).fetchone()
        parent = body.selected[slug].reports_to
        if row["reports_to"] != parent:
            onboarding.admin.update_bot(c, who, slug, M.BotDefinitionUpdate(reports_to=parent, expected_revision=row["revision"]))
    # Every other role on the chart is a Recruiting seat: no bot, repository, computer slot or BotOps task.
    teams = Groups.seat_teams(c, onboarding.settings, sorted({seat["template"] for seat in seats.values()}))
    made = lifecycle.lay_out(c, who.actor, {key: {**seat, "team": teams.get(seat["template"], ""),
                                                  "description_by": "chart"}
                                            for key, seat in seats.items()})
    owners = {goal["id"]: goal["owner"] for goal in summary["goals"]}
    import_goals(c, who.actor, [(goal, owners[goal["id"]]) for goal in saved["goals"]])
    record = O.load(c)
    record["selected"] = {slug: choice.model_dump() for slug, choice in body.selected.items()}
    record["seats"] = made
    # The people who hold a role, to invite (Settings > People): each with the seats that report to them meanwhile.
    people = [{**person, "seats": sorted(made[key] for key, seat in seats.items() if seat["waits_on"] == person["id"])}
              for person in summary["people"]]
    record["company_import"] = {"fingerprint": fingerprint, "imported_at": H.now(), "by": who.actor,
                                "deferred": summary["deferred"], "seats": len(made), "people": people}
    onboarding._store(c, record, who.actor)
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (PLAN_KEY, encode(saved)))
    clear_failure(c)
    H.event(c, who.actor, "onboarding.company_imported", "", {"fingerprint": fingerprint,
                                                              "bots": sorted(body.selected), "seats": len(made)})
    return onboarding.view(c, who)


def add_invitee(onboarding, c, who, key, email):
    """One person the imported file names, now on the roster with the email the owner typed (and on the sign-in allow
    list); the seats that waited with the owner report to them from now on. Their roster row, the same one again when
    they were already added (Invite sends again)."""
    from . import access as Access
    from . import views

    if who.role != "owner":
        raise Problem("forbidden", "Only the owner invites the people from setup", 403)
    record = O.load(c)
    imported = record.get("company_import") or {}
    people = list(imported.get("people") or [])
    at = next((i for i, person in enumerate(people) if person.get("id") == key), None)
    if at is None:
        raise Problem("not_found", "That person isn't in the company file from setup", 404)
    entry = people[at]
    roster = views.roster(c)
    if entry.get("person"):
        row = next((p for p in roster["people"] if p["id"] == entry["person"] and not p.get("hidden")), None)
        if row:
            return row
    _, row = Access.add_person(c, who.actor, roster, name=entry["name"], email=email, title=entry.get("title") or "")
    home = "human:" + onboarding.auth.owner_id(c)
    for seat_id in entry.get("seats") or []:          # a seat someone has moved since stays where they put it
        seat = c.execute("SELECT reports_to FROM seats WHERE id=? AND closed_at IS NULL", (seat_id,)).fetchone()
        if seat and seat["reports_to"] == home:
            c.execute("UPDATE seats SET reports_to=?,updated=?,updated_by=? WHERE id=?",
                      ("human:" + row["id"], H.now(), who.actor, seat_id))
            H.event(c, who.actor, "seat.updated", seat_id, {"fields": ["reports_to"]})
    people[at] = {**entry, "person": row["id"]}
    record["company_import"] = {**imported, "people": people}
    onboarding._store(c, record, who.actor)
    return row


def load_on_start(onboarding, c):
    """The installer-configured plan is applied before any generic sample company.

    A changed plan never replaces a configured company. An error raises, and the caller (`start`) keeps it for the
    owner to see instead of stopping the server or installing a different team."""
    settings = onboarding.settings
    if settings.company_file_error:
        raise Problem("company_file", settings.company_file_error, 422)
    if not settings.company_file_data.strip():
        raise Problem("company_file", "The company file setup gave this server is empty", 422)
    draft = starter.unpack(settings.company_file_data)
    if (O.load(c).get("company_import") or {}).get("fingerprint") == hashlib.sha256(encode(draft).encode()).hexdigest():
        clear_failure(c)        # a file fixed back to the one already applied ends an earlier start's failure
        return None
    who = onboarding.auth.owner_identity(c)
    summary = preview(onboarding, c, who, draft)
    return apply(onboarding, c, who, draft, summary["fingerprint"])


def start(onboarding, store, telemetry=None):
    """Apply the setup's company file on server start (spec test 2). A file that can't be applied leaves nothing of
    itself behind (its transaction rolls back), never puts a generic sample company in its place, and is kept as
    `onboarding.company_import_failed` for the owner to see on first run and on Health; the server still starts, with the
    built-ins only. A later import that works clears it."""
    try:
        with store.transaction() as c:
            load_on_start(onboarding, c)
        return None
    except Exception as exc:
        if isinstance(exc, Problem):
            reason = str(exc.detail)
        else:
            reason = "Tico couldn't read or apply it"
            if telemetry:
                telemetry.capture("company_import", exc)
        failure = {"at": H.now(), "reason": reason[:500]}
        with store.transaction() as c:
            c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                      (FAILED_KEY, encode(failure)))
            H.event(c, "system", "onboarding.company_import_failed", "", {"reason": failure["reason"]})
        return failure


def clear_failure(c, actor=""):
    """End a kept start failure: an import that works, the applied file again, the file setting removed, a team set up
    on the welcome page, or the owner dismissing it. An actor records who dismissed it."""
    gone = c.execute("DELETE FROM registry_metadata WHERE key=?", (FAILED_KEY,)).rowcount
    if gone and actor:
        H.event(c, actor, "onboarding.company_import_failure_dismissed", "", {})
    return bool(gone)


def forget(store):
    """No company file is configured any more, so a failure kept from one that was is no longer true (app startup)."""
    with store.transaction() as c:
        clear_failure(c)


def dismiss(onboarding, c, who):
    """The owner's way out of a kept failure they will not fix by import (a changed file on a company already set up,
    say): Health and the welcome page stop showing it. A server start with the same file records it again."""
    onboarding._owner(who)
    clear_failure(c, who.actor)
    return onboarding.view(c, who)


def failure(c):
    """Why the setup's company file wasn't imported on server start, or None (`start`)."""
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (FAILED_KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except ValueError:
        value = None
    return value if isinstance(value, dict) else None
