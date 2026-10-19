"""A person lets a manager bot manage the bots that report to it (docs/permissions.md, "A manager bot that manages its
reports").

A grant is one row in `bot_report_grants`: the manager bot, the person who granted it, which of SCOPES it covers and two
weekly caps. It lives outside the bot's definition, so no bot ever grants itself, and only a person who manages the
manager bot makes one, in their own click or call (`save`). What it lends is decided in `lent`: the grantor's own
rights, on the bots whose `reports_to` is the manager, in a run that only the grantor's words started (`grantors_run`).
A route takes that identity from `Auth.report_manager`, and every write done that way is the grantor's, via
`report_manager`, with the manager bot and the grant named in its event (`H.DELEGATION`). The `instructions` scope lends
no identity: the run's GitHub token also writes each direct report's own repository (`instruction_reports`,
backend/github_app.py).
"""
from .store import H, Problem

SCOPES = ("routines", "model", "settings", "archive", "create", "instructions")
# The `via` a lent identity carries and every event it writes records. Not `manager`: that is a bot profile
# (backend/profiles.py), which grants nothing.
VIA = "report_manager"
# What the `settings` scope changes in a report's definition: what people call it and what it says it does. Who it
# reports to, its status, repository, contact and the rest stay with the people who manage it.
SETTINGS_FIELDS = frozenset({"display_name", "description"})
# The actions a weekly cap counts, by the event each leaves.
CAPPED = {"create": "bot.definition_created", "archive": "bot.archived"}
CAP_DEFAULT, CAP_MAX = 3, 50
WEEK_DAYS = 7
# Refs that say someone else relayed a person's message (the Assistant, Slack, a routed or live-meeting message): it
# starts no run with their rights.
RELAYED = ("via", "assistant", "slack", "routing", "live_meeting")


def _row(row):
    if not row:
        return None
    grant = dict(row)
    grant["scopes"] = [s for s in (H._json(grant.pop("scopes_json"), []) or []) if s in SCOPES]
    return grant


def active(c, manager):
    """The manager bot's grant in force, its `scopes` a list, or None."""
    return _row(c.execute("SELECT * FROM bot_report_grants WHERE manager_bot=? AND revoked IS NULL",
                          (manager,)).fetchone())


def _grantors(c, manager):
    """Everyone who ever granted this bot: the actors its delegated writes are recorded under."""
    return [r[0] for r in c.execute("SELECT DISTINCT granted_by FROM bot_report_grants WHERE manager_bot=?", (manager,))]


def _delegated(c, manager, action, target=None, since=None):
    """The `action` events this manager bot made under a grant, newest first, cut to `target` and `since`."""
    people = _grantors(c, manager)
    if not people:
        return []
    sql = ("SELECT target FROM events WHERE actor IN (%s) AND action=? AND json_extract(detail_json,'$.via')=? "
           "AND json_extract(detail_json,'$.delegation.bot')=?" % ",".join("?" * len(people)))
    args = [*people, action, VIA, manager]
    if target is not None:
        sql, args = sql + " AND target=?", [*args, target]
    if since is not None:
        sql, args = sql + " AND ts>=?", [*args, since]
    return [r[0] for r in c.execute(sql, args)]


def used(c, manager, scope):
    """How many of `scope`'s capped actions the manager bot made in the last WEEK_DAYS, under any of its grants: a new
    grant does not start the count again."""
    return len(_delegated(c, manager, CAPPED[scope], since=H.shift(H.now(), days=-WEEK_DAYS)))


def check_cap(c, grant, scope):
    limit = int(grant[scope + "_per_week"])
    done = used(c, grant["manager_bot"], scope)
    if done >= limit:
        verb = "created" if scope == "create" else "archived"
        raise Problem("report_cap", f"{grant['manager_bot']} has {verb} {done} bot{'s' if done != 1 else ''} in the last "
                      f"{WEEK_DAYS} days, the most its grant allows ({limit} a week). Ask the person who granted it to "
                      f"raise {scope}_per_week, or wait", 409)


def created_through(c, manager, bot):
    """Whether the manager bot registered `bot` under a grant: the bot it may then place and take live (`create`)."""
    return bool(_delegated(c, manager, CAPPED["create"], target=bot))


def grantors_run(c, auth, attempt_id, manager, grantor):
    """How this run of the manager bot started, when only the grantor's words (and the product's) did: `automation`, a
    task one of the bot's timed automations opened, whose every version and change only the grantor wrote, not through
    anyone (`botops_act._owners_automation_task`); `routine`, the same for a keeper-opened timed routine task
    (`botops_act._owners_routine_task`); `chat`, the grantor's own message in their own chat with the bot; `comment`,
    their own comment on a task they asked the bot for. Else "". Any run that carries another's words (a person, a bot,
    the Assistant, Slack, an automation the bot or anyone else rewrote) lends nothing."""
    from . import botops_act
    product = botops_act.keeper_turn(c, attempt_id, manager)
    if product:
        task_id = H.message_task_id(product)
        task = H.task(c, task_id) if task_id else None
        if not task:
            return ""

        # The grantor's own edits keep it theirs; one through anyone (the Assistant, BotOps, a manager) does not.
        def mine(actor, via):
            return actor == grantor and not via
        refs = product.get("refs") or {}
        if refs.get("automation"):
            return "automation" if botops_act._owners_automation_task(c, auth, task, refs, bot=manager,
                                                                      writer=mine) else ""
        return "routine" if botops_act._owners_routine_task(c, auth, task, bot=manager, writer=mine) else ""
    turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot=?",
                     (attempt_id, manager)).fetchone() if attempt_id else None
    msg = H.message(c, turn["message_id"]) if turn and turn["message_id"] else None
    refs = (msg or {}).get("refs") or {}
    if not msg or msg["from_actor"] != grantor or any(refs.get(key) for key in RELAYED):
        return ""
    task_id = H.message_task_id(msg)
    if refs.get("comment"):
        task = H.task(c, task_id) if task_id else None
        return "comment" if task and task["owner"] == "bot:" + manager and task["requester"] == grantor else ""
    conversation = H.conversation(c, msg["conversation_id"]) or {}
    if task_id or conversation.get("task_id") or conversation.get("scope") == "task" or msg["kind"] not in ("say", "answer"):
        return ""
    return "chat" if set(conversation.get("participants") or []) == {grantor, "bot:" + manager} else ""


def _reaches(c, auth, manager, grantor, target, scope):
    """Whether the grant reaches `target`: it reports straight to the manager, is not built in, and the grantor still
    manages it; under `create`, only a bot the manager registered under a grant (to place it and take it live)."""
    row = c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (target,)).fetchone()
    if not row or row["reports_to"] != manager or target == manager or auth.system_bot(target):
        return False
    if scope == "create" and not created_through(c, manager, target):
        return False
    return auth.bot_manager(c, grantor, target)


def lent(c, auth, manager, attempt_id, scope=None, targets=(None,)):
    """What this run of the manager bot borrows under its grant: (the grantor's identity, the grant, how the run
    started, the `targets` it reaches), or None when it borrows nothing. A target is a bot's slug, or None for one it is
    about to create; `scope` is one of SCOPES, or None for any (a read). All of these hold: the manager is not built in;
    its grant has the scope; the team rule `managers_manage_reports` is on; the grantor still manages it; at least one
    target is reached (`_reaches`); and only the grantor's words started the run (`grantors_run`). It never widens what
    the grantor may do: each route still checks the grantor's own rights."""
    from . import team_rules
    if not attempt_id or manager == H.FLEET_MAINTAINER or auth.system_bot(manager):
        return None             # BotOps has its own rules (`Auth.botops_manages`); a built-in bot takes no grant
    grant = active(c, manager)
    if not grant or (scope is not None and scope not in grant["scopes"]):
        return None
    if not team_rules.load(c)["managers_manage_reports"]:
        return None
    try:
        grantor = auth.identity_for_actor(c, grant["granted_by"])
    except Problem:
        return None
    if not auth.bot_manager(c, grantor, manager):
        return None
    reached = [t for t in targets if t is None or _reaches(c, auth, manager, grantor, t, scope)]
    if not reached:
        return None
    run = grantors_run(c, auth, attempt_id, manager, grantor.actor)
    return (grantor, grant, run, reached) if run else None


def holds_rights(c, auth, manager, attempt_id):
    """Whether this run of the manager bot could lend its grant: one is in force and only the grantor's words started the
    run (`grantors_run`). Such a run carries no other bot's quiet notes or next-run tasks (Execution.claim): they wait
    for the bot's next ordinary run, so nobody else's words reach a run that holds the grantor's rights."""
    grant = active(c, manager)
    if not grant or not attempt_id:
        return False
    try:
        grantor = auth.identity_for_actor(c, grant["granted_by"])
    except Problem:
        return False
    return bool(grantors_run(c, auth, attempt_id, manager, grantor.actor))


def writes_reports(c, manager):
    """Whether the manager bot's grant in force has `instructions`: its GitHub token then depends on the run asking."""
    grant = active(c, manager)
    return bool(grant and "instructions" in grant["scopes"])


def instruction_reports(c, auth, manager, attempt_id):
    """The direct reports whose own repository this run of the manager bot may write under `instructions`, and the run
    that decided it (for the audit), or (set(), None). A report must still be on the chart (not archived) and not a
    branch of another bot: a branch's repository is its original's, which the grant does not reach."""
    from .shared_bots import declared, source_of
    reports = [r[0] for r in c.execute("SELECT bc.bot FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                                       "WHERE bc.reports_to=? AND b.state<>'archived' ORDER BY bc.bot", (manager,))]
    found = lent(c, auth, manager, attempt_id, "instructions", reports) if reports else None
    if not found:
        return set(), None
    grantor, grant, run, reached = found
    return ({bot for bot in reached if not source_of(declared(c, bot))},
            {"attempt": attempt_id, "rights": grantor.actor, "grant": grant["id"], "run": run})


def may_grant(auth, c, who, manager):
    """Why this caller may not grant the manager bot anything, or "". A person, as themselves (not BotOps or the
    Assistant acting for them, not Tico support), who manages the bot. A built-in bot works by its own rules."""
    if who.role not in ("owner", "human") or who.via or who.support:
        return "Only a person who manages the bot grants this, in their own click or call; a bot, BotOps and the Assistant may not"
    if auth.system_bot(manager):
        return "A built-in bot works by its own rules and takes no report grant"
    if not auth.bot_manager(c, who, manager):
        return "You may grant this only for a bot you manage"
    return ""


def may_revoke(auth, c, who, manager, grant):
    """Why this caller may not end the grant, or "": its grantor, a person who manages the bot, the owner or an admin."""
    if who.role not in ("owner", "human") or who.via or who.support:
        return "Only a person ends a report grant, in their own click or call"
    if (grant and who.actor == grant["granted_by"]) or auth.bot_admin(who) or auth.bot_manager(c, who, manager):
        return ""
    return "You may end this only for a bot you manage"


def view(c, auth, who, manager):
    from . import team_rules
    grant = active(c, manager)
    reports = [r[0] for r in c.execute("SELECT bc.bot FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                                       "WHERE bc.reports_to=? AND b.state<>'archived' ORDER BY bc.bot", (manager,))]
    seen = auth.bot_accesses(c, who, reports) if reports else {}
    return {"bot": manager, "scopes": list(SCOPES),
            "grant": {"id": grant["id"], "scopes": grant["scopes"], "granted_by": grant["granted_by"],
                      "created": grant["created"], "create_per_week": grant["create_per_week"],
                      "archive_per_week": grant["archive_per_week"],
                      "used": {scope: used(c, manager, scope) for scope in CAPPED}} if grant else None,
            "reports": [slug for slug in reports if (seen.get(slug) or {}).get("see")],
            "rule_on": bool(team_rules.load(c)["managers_manage_reports"]),
            "can_grant": not may_grant(auth, c, who, manager),
            "can_revoke": bool(grant) and not may_revoke(auth, c, who, manager, grant)}


def _cap(value, current):
    if value is None:
        return current
    return min(CAP_MAX, max(0, int(value)))


def save(c, auth, who, manager, scopes, create_per_week=None, archive_per_week=None):
    """Grant the manager bot `scopes`, replacing what it had; an empty list ends the grant. A change is a new grant (the
    old one is recorded as ended), so each row says exactly who granted what and when."""
    bot = H.bot(c, manager)
    if not bot or bot.get("state") == "archived":
        raise Problem("not_found", "Bot not found", 404)
    wanted = list(dict.fromkeys(scopes or []))
    unknown = [s for s in wanted if s not in SCOPES]
    if unknown:
        raise Problem("scopes", "Unknown scope " + ", ".join(unknown) + ". The scopes: " + ", ".join(SCOPES), 422)
    current = active(c, manager)
    if not wanted:
        if not current:
            return view(c, auth, who, manager)
        if why := may_revoke(auth, c, who, manager, current):
            raise Problem("forbidden", why, 403)
        _end(c, who, current)
        return view(c, auth, who, manager)
    if why := may_grant(auth, c, who, manager):
        raise Problem("forbidden", why, 403)
    create = _cap(create_per_week, current["create_per_week"] if current else CAP_DEFAULT)
    archive = _cap(archive_per_week, current["archive_per_week"] if current else CAP_DEFAULT)
    ordered = [s for s in SCOPES if s in wanted]
    if (current and current["granted_by"] == who.actor and current["scopes"] == ordered
            and (current["create_per_week"], current["archive_per_week"]) == (create, archive)):
        return view(c, auth, who, manager)
    if current:
        _end(c, who, current)
    grant_id = H.new_id()
    c.execute("INSERT INTO bot_report_grants(id,manager_bot,granted_by,scopes_json,create_per_week,archive_per_week,created) "
              "VALUES(?,?,?,?,?,?,?)", (grant_id, manager, who.actor, H._dump(ordered), create, archive, H.now()))
    H.event(c, who.actor, "bot.report_grant_created", manager,
            {"grant": grant_id, "scopes": ordered, "create_per_week": create, "archive_per_week": archive})
    return view(c, auth, who, manager)


def _end(c, who, grant):
    c.execute("UPDATE bot_report_grants SET revoked=?,revoked_by=? WHERE id=? AND revoked IS NULL",
              (H.now(), who.actor, grant["id"]))
    H.event(c, who.actor, "bot.report_grant_revoked", grant["manager_bot"], {"grant": grant["id"]})
