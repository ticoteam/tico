"""The goals, KPIs, check-ins and proposals API (docs/goals-and-kpis.md).

Who may do what, in one place. Company goals and KPIs are visible to everyone signed in; a bot's own
are visible to whoever may Read the bot (docs/permissions.md). A goal's owner, the owner of the goal
it serves and anyone above the owner on the org chart set its colour and its KPIs. A KPI's owner (or
anyone above them; the company owner for a company KPI) edits it and its readings. The Strategy Bot,
the built-in steward of every KPI, reads all of them and writes readings and automatic colours, and
nothing else: a change to a definition or a target it is judged against is a proposal that a
person confirms.
"""

import json
import re

from fastapi import Request

from . import botkpis
from . import strategy_acts as CA
from . import strategy_bot
from . import strategy_merge as CM
from . import strategy_suggest as CS
from . import company as C
from . import goal_asks as GA
from . import goal_read_back as GR
from . import goals as G
from . import kpis as K
from . import models as M
from . import names as N
from . import strategy as S
from . import views
from .store import H, Problem


DRAFT_PAGE_KINDS = ("brand_draft", "gtm_draft")


def company_editor(who):
    """The owner, on their own click: never an admin or a member, never a bot (the Strategy Bot proposes), never someone
    acting for the owner. The Strategy Bot's level, its regular checks, the vision and the strategy are theirs."""
    return who.role == "owner" and not who.via


def strategy_visible(who, proposal, auth=None):
    """A strategy proposal carries unpublished draft text (strategy_draft) or every affected goal's title
    (strategy_realign): the owner reads it, and the bot that proposed it; no one else. A brand or GTM draft proposal
    (backend/brand_gtm.py) is the same for the owner and the admins, who write those drafts."""
    if proposal["kind"] in DRAFT_PAGE_KINDS:
        from . import brand_gtm
        return (auth is not None and brand_gtm.admin(auth, who)) or proposal.get("proposed_by") == who.actor
    if proposal["kind"] not in ("strategy_draft", "strategy_realign"):
        return True
    return company_editor(who) or proposal.get("proposed_by") == who.actor


def _hidden_owners(c, auth, who):
    """The bots whose goals this caller may not read, as owner actors. The Strategy Bot reads every goal."""
    if who.role == "bot" and who.actor == G.AUTO_ACTOR:
        return set()
    return {"bot:" + slug for slug in auth.unreadable_bots(c, who)}


def readable_goals(c, auth, who, rows):
    """A bot's goals are its work: a caller sees them only when they may Read the bot (People's goals stay the
    team's, as before). The Strategy Bot reads every goal, as it keeps every KPI."""
    hidden = _hidden_owners(c, auth, who)
    return [row for row in rows if row["owner_id"] not in hidden] if hidden else list(rows)


_readable_goals = readable_goals          # install's closures of the same name call these


def org_shape(c):
    roster, entries = views.roster(c), views.entries(c)
    archived = {row["id"] for row in H.bots(c) if row.get("state") == "archived"}
    return roster, entries, archived


def is_manager(who):
    """The Strategy Bot acting as itself."""
    return who.role == "bot" and who.actor == G.AUTO_ACTOR


def stands_above(c, who, owner):
    """Whether `who` is the owner of the environment or sits above `owner` on the org chart:
    the tasks board's movers rule, over people and bots alike."""
    if who.role == "owner":
        return True
    if who.role not in ("human", "bot"):
        return False
    roster, entries, archived = org_shape(c)
    return who.actor in G.above(owner, roster, entries, archived, True)


def may_edit_kpi(c, who, record):
    """Whether the KPI's owner, or someone above them on the org chart, may change it."""
    if who.role == "owner":
        return True
    if is_manager(who) or record["owner_id"] == G.COMPANY:
        return False
    return who.actor == record["owner_id"] or stands_above(c, who, record["owner_id"])


def kpi_editable(c, who, record):
    """Whether the KPI update route lets `who` change this KPI: the Strategy Bot proposes instead, and a KPI Tico
    computes is fixed. The growth KPI list flags each KPI with this."""
    return not is_manager(who) and not K.auto(record["id"]) and may_edit_kpi(c, who, record)


def install(app, store, auth, mutate, settings):
    write = mutate

    def mutate(request, body, work):
        """Every goals write, then the Strategy Bot's asks brought up to date in the same transaction: a reading
        logged or a proposal decided closes its task at once, not at the next hourly pass."""
        def work_then_ask(c):
            # A save the owner agreed to in words (backend/goal_read_back.py): checked again inside this write, so two
            # saves of one step never both land, and recorded with the history rows it wrote.
            claim = GR.active() and agreed_save(c, request)
            mark = c.execute("SELECT coalesce(max(rowid), 0) FROM goal_events").fetchone()[0] if claim else 0
            out = work(c)
            if claim:
                gid = GR.record(c, request.state.identity.actor, claim, mark, out) or claim.get("goal_id")
                if isinstance(out, dict):
                    out = {**out, "read_back": {"change": claim["change"], "step": claim["step"] + 1,
                                                "steps": claim["steps"], "saved": True,
                                                "link": gid and goal_link(gid)}}
            try:
                GA.sync(c, auth.owner_id(c), reads=GA.readers(c, auth))
            except Exception as exc:          # the person's write stands; the hourly pass asks instead
                if not c.in_transaction:
                    raise
                GA.log.warning("Strategy Bot asks not brought up to date: %s", type(exc).__name__)
            return out
        return write(request, body, work_then_ask)

    # ------------------------------------------------------------------ who may
    def goal_owner(c, who, value):
        """A slug, a person id, `me` or `company`, as the actor string a goal or KPI is owned by."""
        if value in ("me", "self", None, ""):
            auth.domain(who)
            return who.actor
        if value == G.COMPANY:
            if who.role != "owner":
                raise Problem("forbidden", "A company goal is the owner's to set", 403)
            return G.COMPANY
        return auth.target(c, who, value)

    def parent_owner(c, row):
        parent = G.goal(c, row["parent_id"]) if row.get("parent_id") else None
        return parent["owner_id"] if parent else None

    def may_colour(c, who, row):
        """The goal's owner, the owner of the goal it serves, or anyone above the owner."""
        if is_manager(who):
            return False
        return who.actor in (row["owner_id"], parent_owner(c, row)) or stands_above(c, who, row["owner_id"])

    def may_log(c, who, record):
        """Whoever keeps the KPI, the Strategy Bot, or the owner of a goal that uses it."""
        if is_manager(who) or may_edit_kpi(c, who, record):
            return True
        return any(may_colour(c, who, g) for g in (G.goal(c, link["goal_id"]) for link in K.links_of(c, kpi_id=record["id"])) if g)

    def goal_or_404(c, goal_id):
        row = G.goal(c, goal_id)
        if not row:
            raise Problem("not_found", "Unknown goal", 404)
        return row

    def hidden_owners(c, who):
        return _hidden_owners(c, auth, who)

    def readable_goals(c, who, rows):
        return _readable_goals(c, auth, who, rows)

    def kpi_or_404(c, who, kpi_id):
        record = K.kpi(c, kpi_id)
        if not record or record["owner_id"] in hidden_owners(c, who):
            raise Problem("not_found", "Unknown KPI", 404)
        return record

    def refuse_manager(c, who, what, gated=""):
        """`gated` says what was tried when it changes a goal or a target: the Strategy Bot's try becomes the owner's
        proposal (backend/strategy_acts.py)."""
        if is_manager(who):
            if gated:
                CA.gate(c, who.actor, "target", gated)
            raise Problem("forbidden", f"The {strategy_bot.name(c)} proposes {what}; the owner confirms it "
                          "(POST /api/v2/goal-proposals)", 403)

    def owner_names(c, rows):
        names = {}
        for row in rows:
            owner = row["owner_id"]
            if owner in names:
                continue
            if owner == G.COMPANY:
                names[owner] = {"kind": "company", "id": G.COMPANY, "name": settings.company_name or "Company"}
            elif H.is_bot(owner):
                bot = H.bot(c, H.actor_id(owner)) or {}
                names[owner] = {"kind": "bot", "id": H.actor_id(owner),
                                "name": bot.get("display_name") or bot.get("slug") or H.actor_id(owner),
                                "status": H.status_live(c, H.actor_id(owner))}
            else:
                human = H.human(c, H.actor_id(owner)) or {}
                names[owner] = {"kind": "person", "id": H.actor_id(owner), "name": human.get("name") or H.actor_id(owner)}
        return names

    def in_words(who):
        """The owner's yes to a goal read-back, saved by their Assistant in their chat (backend/goal_read_back.py)."""
        return who.role == "owner" and who.via == "assistant" and bool(GR.active())

    def decider(c, who, item):
        """Whether this person may confirm the proposal: a person's own decision, by whoever owns what changes (or the
        owner's yes to its read-back)."""
        if who.role not in ("owner", "human") or (who.via and not in_words(who)):
            return False
        if item["kind"] in ("strategy_draft", "strategy_realign"):     # the strategy is the owner's (backend/strategy.py)
            return company_editor(who)
        if item["kind"] in DRAFT_PAGE_KINDS:                         # the brand and GTM drafts are the admins'
            from . import brand_gtm
            return brand_gtm.admin(auth, who)
        goal = G.goal(c, item["goal_id"]) if item["goal_id"] else None
        record = K.kpi(c, item["kpi_id"]) if item["kpi_id"] else None
        by_goal = bool(goal) and may_colour(c, who, goal)
        by_kpi = bool(record) and may_edit_kpi(c, who, record)
        if item["kind"] in ("goal_wording", "flag", "goal_why"):
            return by_goal
        if item["kind"] == "kpi_definition":
            return by_kpi
        return by_goal or by_kpi

    def kpi_hidden(c, p, hidden):
        """A suggestion about a KPI whose owner the reader may not read: its KPI's, as the KPI is."""
        record = K.kpi(c, p["kpi_id"]) if p["kpi_id"] and hidden else None
        return bool(record) and record["owner_id"] in hidden

    def reason_hidden(c, p, hidden):
        """Whether a turned-down suggestion's reason is kept from this reader: one who may not read a bot it is about,
        the goal's or the measure's (kpi_hidden hides the suggestion with it), a reused measure's (whose card shows
        "a measure you cannot see") or the owner a new measure would have."""
        if not hidden:
            return False
        goal = G.goal(c, p["goal_id"]) if p["goal_id"] else None
        if kpi_hidden(c, p, hidden) or (goal and goal["owner_id"] in hidden):
            return True
        pl = p.get("payload") or {}
        if p["kind"] != "goal_kpi":
            return False
        used = K.kpi(c, pl["kpi_id"]) if pl.get("kpi_id") else None
        named = H.resolve_actor(c, str(pl.get("owner") or "")) or ""
        return bool(used and used["owner_id"] in hidden) or bool(named) and named in hidden

    def reason_view(c, who, p, hidden):
        """A suggestion as this reader may read its reason: none from a bot they may not read (a note typed with a yes
        neither), and `may_reason`, whether
        they may still add one (the person who turned it down, on their own click, while it has none)."""
        kept = p["status"] != "pending" and reason_hidden(c, p, hidden)
        may = (p["status"] == "rejected" and not p.get("decision_note") and p.get("decided_by") == who.actor
               and who.role in ("owner", "human") and not who.via and not kept)
        return {**p, **({"decision_note": ""} if kept else {}), "may_reason": may}

    def readable_kpis(views, hidden):
        """A goal's KPI lines without the ones whose owner the reader may not read, as kpi_or_404 hides them: a
        goal the reader sees may still be measured by a bot's KPI they may not read."""
        return [v for v in views if v.get("owner_id") not in hidden] if hidden else views

    def exposes(c, goal_owner_actor, kpi_owner):
        """Whether a KPI of `kpi_owner` on a goal of `goal_owner_actor` would show to someone who may read the goal
        but not the KPI. A goal names its KPIs to every reader: its lines, history and the automatic colour's
        reason ("<KPI> 7 above max 3"), so a bot's KPI goes only on a goal no one else reads than its own readers."""
        if not H.is_bot(kpi_owner) or kpi_owner == goal_owner_actor:
            return False
        from .auth import Identity
        slug = H.actor_id(kpi_owner)
        goal_bot = H.actor_id(goal_owner_actor) if H.is_bot(goal_owner_actor) else None
        readers = []
        for person in H.humans(c):
            try:
                readers.append(auth.identity_for_actor(c, H.human_actor(person["id"])))
            except Problem:
                continue                    # no longer on the roster: reads nothing
        readers += [Identity(H.bot_actor(b["id"]), "bot") for b in H.bots(c)
                    if H.bot_actor(b["id"]) != G.AUTO_ACTOR]
        for reader in readers:
            access = auth.bot_accesses(c, reader, [s for s in (slug, goal_bot) if s])
            if (not goal_bot or access[goal_bot]["read"]) and not access[slug]["read"]:
                return True
        return False

    def refuse_exposed(c, goal_row, kpi_owner):
        if exposes(c, goal_row["owner_id"], kpi_owner):
            name = (H.bot(c, H.actor_id(kpi_owner)) or {}).get("slug") or H.actor_id(kpi_owner)
            raise Problem("forbidden", f"That KPI is {name}'s, and some who read this goal may not "
                          "read it: a goal shows its KPIs to everyone who reads it. Use it on a goal of "
                          f"{name}'s, or a KPI everyone may read", 403)

    def hidden_names(c, hidden):
        marks = ",".join("?" * len(hidden))
        return {r[0] for r in c.execute(f"SELECT name FROM kpis WHERE owner_id IN ({marks})", tuple(hidden)) if r[0]}

    def names_any(text, names):
        return bool(text) and any(name in str(text) for name in names)

    def scrub_status(row, names):
        """A link made before links were checked (exposes) may still colour a goal with a hidden KPI's reason, or
        suggest a colour for one a person set with it."""
        for key in ("status_note", "suggest_note"):
            if names_any(row.get(key), names):
                row[key] = "" if key == "status_note" else None

    def scrub_rows(c, who, rows):
        hidden = hidden_owners(c, who)
        secret = hidden_names(c, hidden) if hidden else set()
        if secret:
            for row in rows:
                scrub_status(row, secret)
        return rows

    def goal_view(c, who, row, *visible):
        """One goal (G.view) as this caller may read it: no hidden KPI's line, no suggestion about one, and no
        history line or colour reason naming one (links refuse them now; this is for a link made before)."""
        out, hidden = G.view(c, row, *visible), hidden_owners(c, who)
        if hidden:
            out["kpis"] = readable_kpis(out["kpis"], hidden)
            out["proposals"] = [p for p in out["proposals"] if not kpi_hidden(c, p, hidden)]
            names = hidden_names(c, hidden)
            out["events"] = [e for e in out["events"] if not any(names_any(e.get(k), names) for k in ("old", "new", "note"))]
            scrub_status(out, names)
            # A decision's history line carries the note it was given: kept from a reader who may not read its bot.
            kept = {p["decision_note"].strip() for p in G.proposals(c, None, goal_id=row["id"])
                    if p["status"] != "pending" and p["decision_note"] and reason_hidden(c, p, hidden)}
            out["events"] = [{**e, "note": ""} if e.get("field") == "proposal" and (e.get("note") or "").strip() in kept
                             else e for e in out["events"]]
        return out

    def refuse_bot_why(who):
        if who.role not in ("owner", "human") or (who.via and not in_words(who)):
            raise Problem("forbidden", "A person sets a goal's Why on their own click; a bot proposes one "
                          "(POST /api/v2/goal-proposals, kind goal_why)", 403)

    def company_view(c, who):
        # The vision has one source, the strategy: the owner reads the draft's, everyone else the published one.
        record = {**C.profile(c), "vision": S.vision(c, editor=company_editor(who))}
        by = record.get("updated_by")
        known = N.lookup(c, {by}) if by else {}
        return {"company": {**record, "updated_by_name": known.get(by, H.actor_id(by or "") or "")},
                "may_edit": company_editor(who)}

    # ------------------------------------------------------------------ the company
    @app.get("/api/v2/company")
    def company_show(request: Request):
        """The company's Vision and Description, at the top of Goals."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return company_view(c, who)

    @app.post("/api/v2/company")
    def company_update(request: Request, body: M.CompanyUpdate):
        """The owner writes the Vision and the Description; every change is kept with who and when."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            asked = body.model_dump(exclude_none=True)
            CA.gate(c, who.actor, "vision", "change the company's " + (" and ".join(
                f"{k} to \"{v}\"" for k, v in asked.items()) or "vision or description"))
            if not company_editor(who):
                raise Problem("forbidden", "Only the owner writes the company's Vision and Description, on their own "
                              f"click; an admin or a member asks them, and a bot, the {strategy_bot.name(c)} included, "
                              "proposes", 403)
            if body.vision is not None:
                S.update_draft(c, who.actor, vision=body.vision)
            if body.description is not None:
                C.update(c, who.actor, description=body.description)
            return company_view(c, who)
        return mutate(request, body, work)

    # ------------------------------------------------------------------ how much the Strategy Bot does
    # The /api/v2/ceo-bot paths are the Strategy Bot's old ones: clients built before the rename still call them, so
    # each stays a hidden alias of the route under it.
    @app.post("/api/v2/ceo-bot/level", include_in_schema=False)
    @app.post("/api/v2/strategy-bot/level")
    def strategy_bot_level(request: Request, body: M.StrategyBotLevel):
        """Settings > Strategy Bot, "How much your Strategy Bot does": the owner picks Passive (1), Suggests actions (2)
        or Acts on its own (3) on their own click; each change is kept with who and when. An admin or a member never
        does, nor a bot, the Strategy Bot included, nor the Assistant acting for the owner."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            if not company_editor(who):
                raise Problem("forbidden",
                              f"Only the owner chooses how much the {strategy_bot.name(c)} does, on their own "
                              "click; no admin or bot changes it, the Strategy Bot included", 403)
            strategy_bot.set_level(c, who.actor, body.level)
            return strategy_bot.named(c, strategy_bot.view(c))
        return mutate(request, body, work)

    @app.post("/api/v2/ceo-bot/checks", include_in_schema=False)
    @app.post("/api/v2/strategy-bot/checks")
    def strategy_bot_checks(request: Request, body: M.StrategyBotChecks):
        """Settings > Strategy Bot, "Regular checks": the owner turns the KPI pass and the automatic goal colours on or
        off, on their own click. Off, neither runs until they turn them back on; their chat with the Strategy Bot still
        works."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            if not company_editor(who):
                raise Problem("forbidden",
                              f"Only the owner turns the {strategy_bot.name(c)}'s regular checks on or off, "
                              "on their own click", 403)
            strategy_bot.set_checks(c, who.actor, body.on)
            return strategy_bot.named(c, strategy_bot.view(c))
        return mutate(request, body, work)

    def strategy_itself(c, who, what):
        if who.actor != "bot:" + strategy_bot.SLUG or who.via:
            raise Problem("forbidden", f"Only the {strategy_bot.name(c)} {what}", 403)

    @app.post("/api/v2/ceo-bot/merge", include_in_schema=False)
    @app.post("/api/v2/strategy-bot/merge")
    def strategy_bot_merge(request: Request, body: M.StrategyBotMerge):
        """At level 2 or 3, the Strategy Bot makes several bots' asks waiting on one person a single item of its own;
        the person's answer to it goes to each bot that asked (backend/strategy_merge.py)."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            strategy_itself(c, who, "merges the asks waiting on a person")
            person = H.resolve_actor(c, body.person) or body.person
            return CM.merge(c, who.actor, person, body.title, body.question, body.tasks)
        return mutate(request, body, work)

    @app.post("/api/v2/ceo-bot/level3-offer", include_in_schema=False)
    @app.post("/api/v2/strategy-bot/level3-offer")
    def strategy_bot_level3_offer(request: Request, body: M.Empty):
        """The Strategy Bot records that its Monday note offered level 3: refused unless 10 approvals in a row at level
        2 make it due, and never more often than every 30 days. Offering never changes the level."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            strategy_itself(c, who, "offers level 3, in its Monday note")
            return {"level3_offer": CS.offer_made(c, who.actor)}
        return mutate(request, body, work)

    # ------------------------------------------------------------------ what the Strategy Bot does alone (level 3)
    @app.post("/api/v2/ceo-bot/decisions", include_in_schema=False)
    @app.post("/api/v2/strategy-bot/decisions")
    def strategy_bot_decide(request: Request, body: M.StrategyBotDecision):
        """The Strategy Bot only, at level 3: one decision made alone, with its reason: bot tasks created, moved to the
        top of their queue, given to another bot or closed, all under one action id in the Strategy Bot log. What it may
        not do alone (a person's task, a bot offline, a refusal) becomes one proposal in the owner's Needs you, never
        dropped."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            strategy_itself(c, who, "decides alone, at level 3")
            def give(owner, task_id):
                # The checks a task create or reassign by this caller passes (POST /api/v2/tasks, /tasks/{id}).
                try:
                    auth.require_bot_contact(c, who, auth.target(c, who, owner, need="write"), task_id=task_id,
                                             kind="task")
                except Problem as exc:
                    return exc.detail
                return None
            made = CA.decide(c, who.actor, body.reason, [x.model_dump(exclude_none=True) for x in body.changes],
                             body.goal_id or None, visible=auth.task_sql(c, who), give=give,
                             sees=lambda owner: auth.bot_access(c, who, H.actor_id(owner))["see"])
            return CA.receipt(CA.entry(c, made, auth.task_sql(c, who)))
        return mutate(request, body, work)

    @app.get("/api/v2/ceo-bot/log", include_in_schema=False)
    @app.get("/api/v2/strategy-bot/log")
    def strategy_bot_log(request: Request, before: str | None = None, limit: int = CA.LOG_PAGE):
        """The Strategy Bot log, newest first: each decision it made alone with its reason and what it changed, and each
        thing it proposed instead because it stays the owner's. The owner reads it, who decides on its proposals and
        undoes its decisions: an entry shows bot task activity (owners, status, queue places) that a person or bot may
        not be allowed to read, so nobody else does."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if who.role != "owner":
                raise Problem("forbidden", f"Only the owner reads the {strategy_bot.name(c)} log", 403)
            out = CA.log(c, auth.task_sql(c, who), before, limit)
            out["can_undo"] = company_editor(who)
            return out

    @app.post("/api/v2/ceo-bot/log/{action_id}/undo", include_in_schema=False)
    @app.post("/api/v2/strategy-bot/log/{action_id}/undo")
    def strategy_bot_undo(request: Request, action_id: str, body: M.Empty):
        """The owner, on their own click, undoes one decision the Strategy Bot made alone: the tasks it changed go back
        where nobody changed them since, a later change is kept and named, and each bot whose task changed is told."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            if not company_editor(who):
                raise Problem("forbidden",
                              f"Only the owner undoes what the {strategy_bot.name(c)} did, on their own click", 403)
            return CA.entry(c, CA.undo(c, who.actor, action_id), auth.task_sql(c, who))
        return mutate(request, body, work)

    @app.get("/api/v2/company/history")
    def company_history(request: Request):
        """Every change to the Vision and the Description, newest first, with who made it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            rows = C.history(c)
            known = N.lookup(c, {r["actor"] for r in rows})
            return {"events": [{**r, "actor_name": known.get(r["actor"], H.actor_id(r["actor"]))} for r in rows]}

    # ------------------------------------------------------------------ goals
    def mark_read(who, due):
        if due:
            with store.transaction() as c:
                G.mark_read(c, who.actor, due)

    @app.get("/api/v2/goals")
    def goals_list(request: Request, owner: str | None = None, all: bool = False, status: str | None = None):
        """`hub goal list`: mine, the chain above and my reports' by default; `all` is every goal, or every one
        `owner` owns."""
        who = request.state.identity
        auth.domain(who)
        due = []
        with store.read() as c:
            if all:
                statuses = tuple(status.split(",")) if status else None
                whose = goal_owner(c, who, owner) if owner else None
                rows = readable_goals(c, who, G.goals(c, owner=whose, status=statuses, live_only=not statuses))
                return {"goals": scrub_rows(c, who, rows)}
            actor = goal_owner(c, who, owner)
            roster, entries, archived = org_shape(c)
            out = G.for_actor(c, actor, roster, entries, archived)
            for key in ("goals", "chain", "reports", "company"):
                scrub_rows(c, who, out[key])
            if who.role == "bot" and actor == who.actor:
                due = G.reads_due(c, who.actor, [g["id"] for g in out["goals"] + out["chain"]])
        mark_read(who, due)
        return out

    @app.get("/api/v2/goals/tree")
    def goals_tree(request: Request):
        """Every goal with its owner's name and kind, its KPIs (each with its target and colour), and the KPIs no
        goal uses, for the page's tree and list."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            rows = readable_goals(c, who, G.goals(c, live_only=False))
            names = owner_names(c, rows)
            # Counted over the tasks this caller may read, so a hidden bot's work adds nothing.
            readable = auth.task_sql(c, who)
            open_counts = {r[0]: r[1] for r in c.execute(
                "SELECT goal_id, count(*) FROM tasks WHERE goal_id IS NOT NULL AND status IN "
                f"('open','doing','waiting') AND {readable} GROUP BY goal_id")}
            by_goal = K.goal_views(c, [r["id"] for r in rows], include_archived=True)
            said = {}
            for row in c.execute("SELECT * FROM goal_checkins k WHERE created_at=(SELECT max(created_at) FROM goal_checkins "
                                 "WHERE goal_id=k.goal_id)"):
                said[row["goal_id"]] = dict(row)
            hidden = hidden_owners(c, who)
            secret = hidden_names(c, hidden) if hidden else set()
            for row in rows:
                row["kpis"] = readable_kpis(by_goal.get(row["id"], []), hidden)
                scrub_status(row, secret)
                row["open_tasks"] = open_counts.get(row["id"], 0)
                row["checkin"] = said.get(row["id"])
            unaligned = {r["owner_id"]: r["n"] for r in c.execute(
                "SELECT owner_id, count(*) AS n FROM tasks WHERE goal_id IS NULL AND status IN "
                f"('open','doing','waiting') AND {readable} GROUP BY owner_id")}
            unaligned = {owner: n for owner, n in unaligned.items() if owner not in hidden}
            loose = [dict(r) for r in c.execute("SELECT * FROM kpis WHERE archived_at IS NULL "
                                                "AND id NOT IN (SELECT kpi_id FROM goal_kpis) "
                                                "ORDER BY created_at") if r["owner_id"] not in hidden]
            data = K.effective_many(c, [r["id"] for r in loose])
            other = [K.view(r, data.get(r["id"], []), None) for r in loose]
            names.update(owner_names(c, other))
            visible = {r["id"] for r in rows}
            pending = [p for p in G.proposals(c, "pending") if (not p["goal_id"] or p["goal_id"] in visible)
                       and not kpi_hidden(c, p, hidden) and strategy_visible(who, p, auth)]
            editor = company_editor(who)
            return {"goals": rows, "owners": names, "unaligned": unaligned, "other_kpis": other,
                    "proposals": [{**p, "card": G.card(c, p, hidden, editor), "may_decide": decider(c, who, p)}
                                  for p in pending]}

    @app.get("/api/v2/goals/needs-you")
    def goals_needs_you(request: Request):
        """What waits on the caller here: red KPIs on goals they own, stale KPIs they own, and definitions and
        targets they are asked to confirm. For a person each is a Strategy Bot task (backend/goal_asks.py), shown
        with it; one they closed while its cause stands is left out."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            wanted = GA.wanted(c, auth.owner_id(c), reads=GA.readers(c, auth))
            # Red and stale KPIs are the asker's; a proposal shows to everyone who may decide it, with the task
            # only for the one it is asked of.
            items = [i for i in wanted if i["kind"] != "proposal" and i["owner"] == who.actor]
            asked = {i["ref"]: i for i in wanted if i["kind"] == "proposal"}
            hidden, editor = hidden_owners(c, who), company_editor(who)
            for p in G.proposals(c, "pending"):
                # A Why waits on its goal, not here: the Strategy Bot asks for them as one item, above Passive.
                if p["kind"] not in ("goal_why", "recovery") and decider(c, who, p):
                    goal = G.goal(c, p["goal_id"]) if p["goal_id"] else None
                    record = K.kpi(c, p["kpi_id"]) if p["kpi_id"] else None
                    # Deciding for a visible KPI does not make a hidden goal's target proposal or title visible.
                    if (goal and goal["owner_id"] in hidden) or (p["kind"] != "goal_kpi" and record
                                                                 and record["owner_id"] in hidden):
                        continue
                    item = asked.get(p["id"]) or {"kind": "proposal", "ref": p["id"], "proposal": p,
                                                  "goal_title": goal and goal["title"],
                                                  "kpi_name": record and record["name"]}
                    items.append({**item, "proposal": {**item["proposal"], "card": G.card(c, p, hidden, editor)}})
            return {"actor": who.actor, "items": GA.for_person(c, who.actor, items)}

    # ------------------------------------------------------------------ goals agreed in words (backend/goal_read_back.py)
    def goal_link(gid):
        return settings.public_url.rstrip("/") + "/#/goals/" + gid

    def agreed_save(c, request):
        """The read-back save the middleware let through (backend/app.py), checked again in this write."""
        who, record = request.state.identity, GR.active()
        claim = GR.agreement(c, who, record["cited"], request.method, request.url.path,
                             getattr(request, "_body", b"") or b"{}", auth=auth)
        if claim["goal_read_back"] != record["goal_read_back"] or claim["change"] != record["change"] \
                or claim["step"] != record["step"]:
            raise GR.not_agreed("The read-back changed while this was saved")
        m = re_goal.fullmatch(request.url.path)
        if m:
            claim["goal_id"] = m.group(1)
        elif request.url.path.endswith("/decide"):
            claim["goal_id"] = (G.proposal(c, request.url.path.split("/")[-2]) or {}).get("goal_id")
        return claim

    re_goal = re.compile(r"/api/v2/goals/([^/]+)(?:/.*)?")

    def plain(value):
        from .assistant import plain_line
        return plain_line(value or "")

    def q(value):
        return "“" + plain(value) + "”"

    def bold(title):
        title = plain(title)
        return f"**{title}**" if "*" not in title else q(title)

    def owner_name(c, actor):
        return owner_names(c, [{"owner_id": actor}])[actor]["name"]

    def fields_of(n, raw, allowed):
        extra = sorted(str(k) for k in raw if k != "kind" and k not in allowed)
        if extra:
            raise Problem("validation", f"Change {n} ({raw.get('kind')}) does not take {', '.join(extra)}", 422)

    def bet_words(c, bet_id):
        return (S.bet(c, bet_id) or {}).get("statement") or bet_id

    def measure_plan(c, who, n, goal_row_owner, measure, target):
        """A measure for a goal: an existing KPI or a new one, with the target on the link, as the save's body and in
        words."""
        if not isinstance(measure, dict) or (target is not None and not isinstance(target, dict)):
            raise Problem("validation", f"Change {n}: a measure and its target are objects", 422)
        fields_of(n, measure, GR.MEASURE_FIELDS)
        fields_of(n, target or {}, GR.TARGET_FIELDS)
        body = {k: v for k, v in measure.items() if v is not None}
        body.update({k: v for k, v in (target or {}).items() if v is not None})
        if body.get("owner"):
            body["owner"] = goal_owner(c, who, body["owner"])
        if body.get("name"):
            body["name"] = plain(body["name"])
        try:
            M.GoalKpiLink(**body)
        except Exception as exc:
            raise Problem("validation", f"Change {n}: the measure is not complete ({type(exc).__name__})", 422)
        if body.get("kpi_id"):
            record = kpi_or_404(c, who, body["kpi_id"])
            spec, words = record, q(record["name"])
            owner = record["owner_id"]
        elif body.get("name"):
            spec = {"direction": body.get("direction") or "up", "unit": body.get("unit") or ""}
            details = [f"definition {q(body['definition'])}" if body.get("definition") else "",
                       f"unit {q(body['unit'])}" if body.get("unit") else "",
                       {"up": "higher is better", "down": "lower is better", "range": "kept in a range"}[spec["direction"]],
                       body.get("cadence") or "weekly"]
            words = f"{q(body['name'])} (new: {', '.join(d for d in details if d)})"
            owner = body.get("owner") or goal_row_owner
        else:
            raise Problem("validation", f"Change {n}: name the measure, or give the kpi_id of one", 422)
        _, error = K.validate_target(spec, target or {"kind": "none"})
        if error:
            raise Problem("validation", f"Change {n}: {error}", 422)
        aimed = bool(target) and (target.get("kind") or "none") != "none"
        return body, spec, owner, words + (", target " + GR.target_words(spec, target) if aimed else ", no target")

    def suggestion_words(c, p):
        card, pl = G.card(c, p, set(), True), p.get("payload") or {}
        was_now = lambda rows: "; ".join(f"{r['label']} was {q(r['now'] or 'none')}, now {q(r['proposed'] or 'none')}"
                                         for r in rows)
        kind = p["kind"]
        if kind == "goal_wording":
            return was_now(card.get("changes") or [])
        if kind == "kpi_definition":
            return f"{q(card.get('kpi_name') or '')}: " + was_now(card.get("changes") or [])
        if kind == "goal_kpi":
            label = (card.get("target_label") or "").removeprefix("→ ")
            return f"add the measure {q((card.get('measure') or {}).get('name') or '')}, target {label or 'none'}"
        if kind == "kpi_target":
            return (f"the target of {q(card.get('kpi_name') or '')} was "
                    f"{(card.get('now_label') or '').removeprefix('→ ') or 'none'}, now "
                    f"{(card.get('target_label') or '').removeprefix('→ ') or 'none'}")
        if kind == "flag":
            return f"flag it as {plain(pl.get('issue'))}" + (f": {plain(p.get('reason'))}" if p.get("reason") else "")
        row = G.goal(c, p["goal_id"]) or {}
        return f"Why was {q(row.get('why') or 'none')}, now {q(pl.get('why') or '')}"

    def read_back_plan(c, who, n, raw):
        """One change as the saves it stands for and the line the owner reads; 403 when it stays a click."""
        if not isinstance(raw, dict):
            raise Problem("validation", f"Change {n} is not an object", 422)
        if why := GR.click_reason(raw):
            raise Problem("stays_a_click", why, 403)
        read_owners = GR.read_back_access(c, auth, raw)
        kind = raw.get("kind")
        if kind not in GR.KINDS:
            raise Problem("validation", f"Change {n}: kind is one of {', '.join(GR.KINDS)}", 422)
        steps, watch = [], []
        step = lambda path, body: steps.append({"method": "POST", "path": path, "body": body})
        if kind == "goal_create":
            fields_of(n, raw, (*GR.GOAL_FIELDS, "measure", "target"))
            title = plain(raw.get("title"))
            if not title or not raw.get("owner"):
                raise Problem("validation", f"Change {n}: a new goal needs a title and an owner", 422)
            owner = goal_owner(c, who, raw["owner"])
            parent = goal_or_404(c, raw["parent_id"]) if raw.get("parent_id") else None
            bet = raw.get("bet_id") or None
            if bet and not S.bet(c, bet):
                raise Problem("not_found", f"Change {n}: no strategy bet {bet}", 404)
            why = plain(raw.get("why"))
            step("/api/v2/goals", {"title": title, "owner_id": owner, "parent_id": parent and parent["id"], "bet_id": bet})
            words = [bold(title), "owner " + owner_name(c, owner)]
            if parent:
                words.append("supports " + q(parent["title"]))
            if bet:
                words.append("serves the bet " + q(bet_words(c, bet)))
            if why:
                step(f"/api/v2/goals/{GR.NEW}/why", {"why": why})
                words.append("Why " + q(why))
            if raw.get("measure") is not None:
                body, _, kowner, said = measure_plan(c, who, n, owner, raw["measure"], raw.get("target"))
                refuse_exposed(c, {"owner_id": owner}, kowner)
                step(f"/api/v2/goals/{GR.NEW}/kpis", body)
                words.append("measure " + said)
            elif raw.get("target") is not None:
                raise Problem("validation", f"Change {n}: a target goes with a measure", 422)
            text = " · ".join(words)
        elif kind == "goal_update":
            fields_of(n, raw, ("goal_id", *GR.GOAL_FIELDS))
            row = goal_or_404(c, str(raw.get("goal_id") or ""))
            watch.append(row["id"])
            sets, said = {}, []
            if raw.get("title") is not None:
                title = plain(raw["title"])
                if not title:
                    raise Problem("validation", f"Change {n}: a goal keeps a title", 422)
                if title != row["title"]:
                    sets["title"] = title
                    said.append(f"title was {q(row['title'])}, now {q(title)}")
            if raw.get("owner") is not None:
                owner = goal_owner(c, who, raw["owner"])
                if owner != row["owner_id"]:
                    sets["owner"] = owner
                    said.append(f"owner was {owner_name(c, row['owner_id'])}, now {owner_name(c, owner)}")
            if raw.get("parent_id") is not None:
                parent = goal_or_404(c, raw["parent_id"]) if raw["parent_id"] else None
                if (parent and parent["id"]) != (row["parent_id"] or None):
                    old = G.goal(c, row["parent_id"]) if row["parent_id"] else None
                    sets["parent_id"] = parent["id"] if parent else ""
                    said.append(f"supports was {q(old['title']) if old else 'nothing'}, "
                                f"now {q(parent['title']) if parent else 'nothing'}")
            if raw.get("bet_id") is not None:
                bet = raw["bet_id"] or None
                if bet and not S.bet(c, bet):
                    raise Problem("not_found", f"Change {n}: no strategy bet {bet}", 404)
                if bet != (row.get("bet_id") or None):
                    sets["bet_id"] = bet or ""
                    said.append(f"bet was {q(bet_words(c, row['bet_id'])) if row.get('bet_id') else 'none'}, "
                                f"now {q(bet_words(c, bet)) if bet else 'none'}")
            if sets:
                step(f"/api/v2/goals/{row['id']}", sets)
            if raw.get("why") is not None:
                why = plain(raw["why"])
                if why != (row.get("why") or ""):
                    step(f"/api/v2/goals/{row['id']}/why", {"why": why})
                    said.append(f"Why was {q(row.get('why')) if row.get('why') else 'none'}, "
                                f"now {q(why) if why else 'none'}")
            if not said:
                raise Problem("no_change", f"Change {n} changes nothing: {q(row['title'])} already reads that way", 409)
            text = f"Change {bold(row['title'])}: " + "; ".join(said)
        elif kind == "measure_add":
            fields_of(n, raw, ("goal_id", "measure", "target"))
            row = goal_or_404(c, str(raw.get("goal_id") or ""))
            watch.append(row["id"])
            body, _, kowner, said = measure_plan(c, who, n, row["owner_id"], raw.get("measure"), raw.get("target"))
            if body.get("kpi_id") and K.link(c, row["id"], body["kpi_id"]):
                raise Problem("no_change", f"Change {n}: that measure is on {q(row['title'])} already; change its "
                              "target instead (target_set)", 409)
            refuse_exposed(c, row, kowner)
            step(f"/api/v2/goals/{row['id']}/kpis", body)
            text = f"Add the measure {said} to {bold(row['title'])}"
        elif kind in ("target_set", "measure_remove"):
            fields_of(n, raw, ("goal_id", "kpi_id", "target") if kind == "target_set" else ("goal_id", "kpi_id"))
            row = goal_or_404(c, str(raw.get("goal_id") or ""))
            record = kpi_or_404(c, who, str(raw.get("kpi_id") or ""))
            link = K.link(c, row["id"], record["id"])
            if not link:
                raise Problem("not_found", f"Change {n}: {q(record['name'])} is not a measure of {q(row['title'])}", 404)
            watch.append(row["id"])
            if kind == "measure_remove":
                step(f"/api/v2/goals/{row['id']}/kpis/{record['id']}/unlink", {})
                text = f"Take the measure {q(record['name'])} off {bold(row['title'])} (the measure and its readings stay)"
            else:
                target = raw.get("target")
                if not isinstance(target, dict):
                    raise Problem("validation", f"Change {n}: give the target (kind none takes it away)", 422)
                fields_of(n, target, GR.TARGET_FIELDS)
                target = {k: v for k, v in target.items() if v is not None}
                _, error = K.validate_target(record, target)
                if error:
                    raise Problem("validation", f"Change {n}: {error}", 422)
                was, now = GR.target_words(record, link), GR.target_words(record, target)
                if was == now:
                    raise Problem("no_change", f"Change {n} changes nothing: the target is {was} already", 409)
                step(f"/api/v2/goals/{row['id']}/kpis/{record['id']}", target)
                text = f"Change the target of {q(record['name'])} on {bold(row['title'])}: was {was}, now {now}"
        else:
            fields_of(n, raw, ("proposal_id", "decision"))
            p = G.proposal(c, str(raw.get("proposal_id") or ""))
            if not p:
                raise Problem("not_found", f"Change {n}: no such suggestion", 404)
            if p["kind"] not in GR.SUGGESTIONS:
                raise Problem("stays_a_click", GR.CLICK["recovery" if p["kind"] == "recovery" else "strategy"], 403)
            if p["status"] != "pending":
                raise Problem("conflict", f"Change {n}: that suggestion was {p['status']} already", 409)
            if raw.get("decision") not in ("confirm", "reject"):
                raise Problem("validation", f"Change {n}: a suggestion's decision is confirm or reject", 422)
            row = G.goal(c, p["goal_id"]) if p["goal_id"] else None
            if row:
                watch.append(row["id"])
            step(f"/api/v2/goal-proposals/{p['id']}/decide", {"decision": raw["decision"]})
            about = bold(row["title"]) if row else q((K.kpi(c, p["kpi_id"]) or {}).get("name") or "")
            text = (("Accept" if raw["decision"] == "confirm" else "Turn down") + f" the suggestion on {about}: "
                    + suggestion_words(c, p))
        return {"n": n, "kind": kind, "text": text, "steps": steps, "watch": watch,
                "read_owners": read_owners}

    def read_back_trial(c, who, changes):
        """Every change made as the owner's own click would make it, in order, then rolled back: what could not be
        saved is refused now, before the owner reads it."""
        c.execute("SAVEPOINT goal_read_back")
        try:
            for change in changes:
                made = None
                for st in change["steps"]:
                    path, body = st["path"].replace(GR.NEW, made or GR.NEW), st["body"]
                    try:
                        if path == "/api/v2/goals":
                            made = G.create(c, who.actor, body["title"], body["owner"], body["parent_id"],
                                            bet_id=body["bet_id"])["id"]
                        elif path.endswith("/why"):
                            C.set_why(c, who.actor, path.split("/")[-2], body["why"])
                        elif path.endswith("/unlink"):
                            G.kpi_unlink(c, who.actor, path.split("/")[-4], path.split("/")[-2])
                        elif path.endswith("/decide"):
                            G.decide(c, who.actor, path.split("/")[-2], body["decision"])
                        elif path.endswith("/kpis"):
                            gid, target = path.split("/")[-2], {k: body.get(k) for k in GR.TARGET_FIELDS}
                            if body.get("kpi_id"):
                                G.kpi_link(c, who.actor, gid, body["kpi_id"], target)
                            else:
                                G.kpi_create(c, who.actor, body.get("owner") or G.goal(c, gid)["owner_id"],
                                             {k: body.get(k) for k in ("name", "definition", "unit", "direction",
                                                                       "cadence", "source_note") if k in body},
                                             goal_id=gid, target=target)
                        elif "/kpis/" in path:
                            G.kpi_link(c, who.actor, path.split("/")[-3], path.split("/")[-1], body)
                        else:
                            G.update(c, who.actor, path.split("/")[-1], **body)
                    except (Problem, H.Refused) as exc:
                        # A rule's refusal (backend/hubdb.py refuse) is rolled back with the trial: it is not the owner's.
                        status = getattr(exc, "status", 422)
                        raise Problem(getattr(exc, "code", "validation") if status != 400 else "validation",
                                      f"Change {change['n']} can't be saved: {exc.detail}",
                                      status if status != 400 else 422) from None
        finally:
            c.execute("ROLLBACK TO goal_read_back")
            c.execute("RELEASE goal_read_back")

    @app.post("/api/v2/goals/read-back")
    def goal_read_back(request: Request, body: M.GoalReadBack):
        """The owner's Assistant, in their own chat: Tico writes the read-back of up to 20 changes as its reply and
        stores the exact saves; the owner's yes to it is what lets them through (backend/goal_read_back.py)."""
        who = request.state.identity
        if not GR.strategy_turn(who):
            raise Problem("forbidden", "Only the owner's Assistant, in their own chat, reads back goal changes", 403)

        def work(c):
            from . import assistant
            room = assistant.find_room(c, who.actor)
            if not room:
                raise Problem("not_found", "No Assistant chat", 404)
            changes = [read_back_plan(c, who, n, raw) for n, raw in enumerate(body.changes, 1)]
            seen = set()
            for change in changes:
                signature = json.dumps(change["steps"], sort_keys=True)
                if signature in seen:
                    raise Problem("validation", "A change is repeated; include each change only once", 422)
                seen.add(signature)
            read_back_trial(c, who, changes)
            text = GR.text(changes)
            # The turn's reply: its closing text is not posted after it (backend/execution.py), so the owner's next
            # message answers this and nothing else.
            turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                             (who.attempt_id,)).fetchone()
            msg = H._write_message(c, who.task_actor, who.actor, text, room, "say",
                                   {"assistant": True, "read_back": True, "goal_read_back": True},
                                   turn["message_id"] if turn else None, None, delivered_at=H.now())
            H.event(c, who.actor, GR.EVENT, msg["id"], {"changes": changes})
            return {"message_id": msg["id"], "text": text,
                    "changes": [{"n": ch["n"], "kind": ch["kind"], "text": ch["text"],
                                 "steps": [GR.shown(st) for st in ch["steps"]]} for ch in changes]}
        return write(request, body, work)

    @app.get("/api/v2/goals/read-back/{mid}")
    def goal_read_back_show(request: Request, mid: str):
        """One read-back as it stands: each change, its saves (the exact calls), whether it is saved, and, in the turn
        the owner's reply started, whether their reply says yes to it and why not."""
        who = request.state.identity
        if not (GR.strategy_turn(who) or company_editor(who)):
            raise Problem("forbidden", "The owner or their Assistant in their chat reads a goal read-back", 403)
        with store.read() as c:
            back = GR.stored(c, mid)
            if not back or (H.conversation(c, back["conversation_id"]) or {}).get("owner_id") != who.actor:
                raise Problem("not_found", "No such read-back", 404)
            done = GR.saves(c, mid)
            GR.read_back_access_stored(c, auth, back["changes"], done)
            yes, why_not = None, ""
            if GR.strategy_turn(who):
                turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                                 (who.attempt_id,)).fetchone()
                try:
                    answered, yes, _ = GR.answered(c, who, turn["message_id"] if turn else "")
                    if answered["id"] != mid:
                        yes, why_not = None, "The owner's reply answers a later read-back"
                except Problem as exc:
                    why_not = exc.detail.split("; nothing was saved")[0]
            out = []
            for ch in back["changes"]:
                saved = all((ch["n"], i) in done for i in range(len(ch["steps"])))
                gid = next((d.get("goal_id") for (n, _), d in sorted(done.items()) if n == ch["n"] and d.get("goal_id")),
                           None) or (ch["watch"][0] if ch.get("watch") else None)
                said = [] if saved else GR.changed_since(c, back, ch, done)
                if saved:
                    reason = ""
                elif said:
                    reason = "Since the read-back " + "; ".join(said)
                elif yes is not None:
                    reason = "" if ch["n"] in yes else "The owner did not say yes to it"
                else:
                    reason = why_not
                out.append({"n": ch["n"], "kind": ch["kind"], "text": ch["text"],
                            "steps": [{**GR.shown(st, GR.step_path(st, ch, done)), "saved": (ch["n"], i) in done}
                                      for i, st in enumerate(ch["steps"])],
                            "saved": saved, "agreed": None if yes is None else ch["n"] in yes, "reason": reason,
                            "link": gid and goal_link(gid)})
            return {"message_id": mid, "created_at": back["created_at"], "changes": out}

    @app.post("/api/v2/goals/refresh")
    def goals_refresh(request: Request, body: M.GoalRefresh):
        """The Strategy Bot's status pass: work every live goal's automatic colour out again (a goal a
        person set only gets a suggestion). The owner may run it too."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            if not (is_manager(who) or who.role == "owner"):
                raise Problem("forbidden", f"The {strategy_bot.name(c)} or the owner runs the status pass", 403)
            return G.refresh(c, body.goal_ids)
        return mutate(request, body, work)

    @app.get("/api/v2/goals/{gid}")
    def goal_show(request: Request, gid: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = goal_or_404(c, gid)
            if not readable_goals(c, who, [row]):
                raise Problem("not_found", "Unknown goal", 404)
            due = G.reads_due(c, who.actor, [gid]) if who.role == "bot" else []
            out = {"goal": goal_view(c, who, row, auth.task_sql(c, who))}
        mark_read(who, due)
        return out

    @app.post("/api/v2/goals")
    def goal_create(request: Request, body: M.GoalCreate):
        who = request.state.identity
        def work(c):
            owner = goal_owner(c, who, body.owner)
            parent = goal_or_404(c, body.parent_id) if body.parent_id else None
            # A goal needs no parent: a person or a bot may set their own, and it is simply unlinked.
            if who.role != "owner":
                if not (owner == who.actor or (parent and parent["owner_id"] == who.actor) or stands_above(c, who, owner)):
                    raise Problem("forbidden", "You can set a goal for yourself, under a goal you own, "
                                  "or for someone below you on the org chart", 403)
            row = G.create(c, who.actor, body.title, owner, body.parent_id, body.body, top=body.top,
                           bet_id=body.bet_id)
            return {"goal": goal_view(c, who, row)}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}/status")
    def goal_status(request: Request, gid: str, body: M.GoalStatus):
        """Set the colour by hand: it sticks, with the setter's name and note, until a person hands it back."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(c, who, "a colour with the automatic status pass, and never sets one by hand")
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, or someone "
                              "above them sets the colour", 403)
            return {"goal": goal_view(c, who, G.set_status(c, who.actor, gid, body.status, body.note))}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}/reopen")
    def goal_reopen(request: Request, gid: str, body: M.GoalReopen):
        """Reopen a done or dropped goal with a colour and one line why, or `auto` for the colour it had, worked out
        again: whoever may set its colour. `undo` alone puts a removed goal back as it was, from its history."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(c, who, "a colour with the automatic status pass, and never reopens a goal")
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, or someone "
                              "above them reopens it", 403)
            return {"goal": goal_view(c, who, G.reopen(c, who.actor, gid, body.status, body.note, body.undo))}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}/status/auto")
    def goal_status_auto(request: Request, gid: str, body: M.Empty):
        """"Let Strategy Bot set it": a person ends their override and the colour is worked out again now."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            if is_manager(who) or not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, or someone "
                              "above them hands the colour back", 403)
            return {"goal": goal_view(c, who, G.hand_back(c, who.actor, gid))}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}")
    def goal_update(request: Request, gid: str, body: M.GoalUpdate):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(c, who, "clearer wording or a change of goal",
                           gated=f"change the goal \"{row['title']}\""
                                 + (f" to \"{body.title}\"" if body.title and body.title != row["title"] else ""))
            # Handing a goal to someone else is for whoever sits above; linking it to the goal it
            # supports is also its owner's (the link icon on the Goals page), and the new goal's owner
            # sees it there.
            if body.owner is not None and not (who.actor == parent_owner(c, row) or stands_above(c, who, row["owner_id"])):
                raise Problem("forbidden", "Moving a goal is for the owner of the goal it serves or someone "
                              "above its owner", 403)
            structural = body.parent_id is not None or body.owner is not None
            # Linking it to the goal it serves, or to the strategy bet it serves, is the same right.
            if (body.parent_id is not None or body.bet_id is not None) and not may_colour(c, who, row):
                raise Problem("forbidden", "Linking a goal is for its owner, the owner of the goal it serves, "
                              "or someone above its owner", 403)
            if not structural and not may_colour(c, who, row):
                raise Problem("forbidden", "You cannot edit this goal", 403)
            owner = goal_owner(c, who, body.owner) if body.owner else None
            if owner and owner != row["owner_id"]:
                # Its KPIs go with it: a bot's KPI moves only to an owner whose goal its own readers alone read.
                for link in K.links_of(c, goal_id=gid):
                    record = K.kpi(c, link["kpi_id"])
                    if record:
                        refuse_exposed(c, {**row, "owner_id": owner}, record["owner_id"])
            return {"goal": goal_view(c, who, G.update(c, who.actor, gid, title=body.title, body=body.body,
                                               parent_id=body.parent_id, owner=owner, rank=body.rank,
                                               top=body.top, bet_id=body.bet_id))}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}/why")
    def goal_why(request: Request, gid: str, body: M.GoalWhy):
        """Write a goal's Why (one line tying it to the vision) or clear it; whoever may edit the goal, a person."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_bot_why(who)
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, or someone above them "
                              "sets its Why", 403)
            C.set_why(c, who.actor, gid, body.why)
            return {"goal": goal_view(c, who, G.goal(c, gid), auth.task_sql(c, who))}
        return mutate(request, body, work)

    # ------------------------------------------------------------------ check-ins
    @app.get("/api/v2/goals/{gid}/checkins")
    def goal_checkins(request: Request, gid: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = goal_or_404(c, gid)
            if not readable_goals(c, who, [row]):
                raise Problem("not_found", "Unknown goal", 404)
            return {"goal_id": gid, "checkins": G.checkins(c, gid)}

    @app.post("/api/v2/goals/{gid}/checkins")
    def goal_checkin(request: Request, gid: str, body: M.GoalCheckin):
        """The owner's own words on how it is going (a person, or the Strategy Bot recording their answer)."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            if not (may_colour(c, who, row) or is_manager(who)):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, someone above them "
                              f"or the {strategy_bot.name(c)} records a check-in", 403)
            source = None
            if body.from_actor:
                source = auth.target(c, who, body.from_actor)
            return {"checkin": G.checkin_add(c, who.actor, gid, body.body, body.signal, source, body.kpi_id)}
        return mutate(request, body, work)

    # ------------------------------------------------------------------ KPIs on goals
    @app.post("/api/v2/goals/{gid}/kpis")
    def goal_kpi_link(request: Request, gid: str, body: M.GoalKpiLink):
        """Link a goal to a KPI (`kpi_id`), or make a new one and link it (`name`). The target is on the link."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(c, who, "a KPI for a goal")
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner or someone above them adds a measure", 403)
            target = {k: getattr(body, k) for k in ("kind", "baseline", "baseline_at", "target", "deadline", "min", "max")}
            if body.kpi_id:
                refuse_exposed(c, row, kpi_or_404(c, who, body.kpi_id)["owner_id"])
                G.kpi_link(c, who.actor, gid, body.kpi_id, target)
                return {"kpi": views_of(c, gid, body.kpi_id)}
            if not body.name:
                raise Problem("validation", "Give a kpi_id to link, or a name to make a new KPI", 422)
            owner = goal_owner(c, who, body.owner) if body.owner else row["owner_id"]
            refuse_exposed(c, row, owner)
            made = G.kpi_create(c, who.actor, owner, {k: getattr(body, k) for k in (
                "name", "definition", "unit", "direction", "cadence", "source_note")}, goal_id=gid, target=target)
            return {"kpi": views_of(c, gid, made["id"])}
        return mutate(request, body, work)

    def views_of(c, goal_id, kpi_id):
        return next(v for v in K.goal_views(c, [goal_id])[goal_id] if v["id"] == kpi_id)

    @app.post("/api/v2/goals/{gid}/kpis/{kid}")
    def goal_kpi_target(request: Request, gid: str, kid: str, body: M.KpiTarget):
        """Set the target on a goal's link to a KPI: an improvement (baseline, target, deadline), a range, or none."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(c, who, "a target it is judged against",
                           gated=f"change the target of the goal \"{row['title']}\" to "
                                 + ", ".join(f"{k} {v}" for k, v in body.model_dump(exclude_none=True).items()))
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner or someone above them sets a target", 403)
            kpi_or_404(c, who, kid)
            if not K.link(c, gid, kid):
                raise Problem("not_found", "That KPI is not linked to this goal", 404)
            G.kpi_link(c, who.actor, gid, kid, body.model_dump())
            return {"kpi": views_of(c, gid, kid)}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}/kpis/{kid}/unlink")
    def goal_kpi_unlink(request: Request, gid: str, kid: str, body: M.Empty):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(c, who, "removing a KPI from a goal")
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner or someone above them removes a measure", 403)
            G.kpi_unlink(c, who.actor, gid, kid)
            return {"goal": goal_view(c, who, G.goal(c, gid))}
        return mutate(request, body, work)

    # ------------------------------------------------------------------ KPIs
    def kpi_detail(c, who, record):
        hidden = hidden_owners(c, who)
        rows = K.readings(c, record["id"])
        effective = [r for r in rows if not r.get("superseded_by")]
        links = []
        for link in K.links_of(c, kpi_id=record["id"]):
            goal = G.goal(c, link["goal_id"])
            if not goal or goal["owner_id"] in hidden:
                continue
            state = K.assess(record, link, effective)
            links.append({**link, "goal_title": goal["title"], "goal_owner": goal["owner_id"],
                          "goal_status": goal["status"], "target_label": K.target_label(record, link),
                          "status": state["status"], "reason": state["reason"], "expected": state["expected"]})
        goal_ids = [link["goal_id"] for link in links]
        # A target suggestion on a goal the reader may not read is that goal's, as its link is.
        pending = [p for p in G.proposals(c, "pending", kpi_id=record["id"]) if not p["goal_id"] or p["goal_id"] in goal_ids]
        marks = ",".join("?" * len(goal_ids))
        said = H._rows(c.execute(
            f"SELECT * FROM goal_checkins WHERE kpi_id=? OR goal_id IN ({marks}) ORDER BY created_at DESC LIMIT 5",
            (record["id"], *goal_ids))) if goal_ids else H._rows(c.execute(
                "SELECT * FROM goal_checkins WHERE kpi_id=? ORDER BY created_at DESC LIMIT 5", (record["id"],)))
        return {"kpi": K.view(record, effective), "links": links, "readings": rows,
                "definitions": [] if K.auto(record["id"]) else K.definitions(c, record["id"]),
                "checkins": said, "proposals": [{**p, "card": G.card(c, p, hidden, company_editor(who)),
                                                  "may_decide": decider(c, who, p)} for p in pending],
                "may_edit": may_edit_kpi(c, who, record), "may_log": may_log(c, who, record) and not K.auto(record["id"])}

    @app.get("/api/v2/kpis")
    def kpi_list(request: Request, goal_id: str | None = None, owner: str | None = None, unlinked: bool = False,
                 auto_for: str | None = None, include_archived: bool = False):
        """The KPIs the caller may see. `unlinked` keeps the ones no goal uses; `auto_for` is one bot's five
        automatic KPIs (Read on the bot)."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if auto_for:
                auto_for = (H.resolve_bot(c, auto_for) or {}).get("id", auto_for)
                auth.require_read(c, who, auto_for)
                return {"kpis": botkpis.for_bot(c, auto_for)}
            hidden = hidden_owners(c, who)
            actor = goal_owner(c, who, owner) if owner else None
            sql, args, clauses = "SELECT * FROM kpis", [], []
            if goal_id:
                clauses.append("id IN (SELECT kpi_id FROM goal_kpis WHERE goal_id=?)")
                args.append(goal_id)
            elif unlinked:
                clauses.append("id NOT IN (SELECT kpi_id FROM goal_kpis)")
            if not include_archived:
                clauses.append("archived_at IS NULL")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            records = [dict(r) for r in c.execute(sql + " ORDER BY created_at", args)
                       if r["owner_id"] not in hidden and (not actor or r["owner_id"] == actor)]
            data = K.effective_many(c, [r["id"] for r in records])
            out = []
            for record in records:
                item = K.view(record, data.get(record["id"], []), None)
                item["goals"] = [{"goal_id": link["goal_id"], "kind": link["kind"]} for link in K.links_of(c, kpi_id=record["id"])]
                out.append(item)
            return {"kpis": out}

    @app.post("/api/v2/kpis")
    def kpi_create(request: Request, body: M.KpiCreate):
        """A new KPI. With `goal_id` it is linked to that goal, with the target fields on the link."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            refuse_manager(c, who, "a new KPI (a proposal of kind goal_kpi)")
            goal = goal_or_404(c, body.goal_id) if body.goal_id else None
            if goal and not may_colour(c, who, goal):
                raise Problem("forbidden", "The goal's owner or someone above them adds a measure", 403)
            owner = goal_owner(c, who, body.owner) if body.owner else (goal["owner_id"] if goal else who.actor)
            if goal:
                refuse_exposed(c, goal, owner)
            target = {k: getattr(body, k) for k in ("kind", "baseline", "baseline_at", "target", "deadline", "min", "max")}
            made = G.kpi_create(c, who.actor, owner, {k: getattr(body, k) for k in (
                "name", "definition", "unit", "direction", "cadence", "source_note")}, goal_id=body.goal_id, target=target)
            return kpi_detail(c, who, made)
        return mutate(request, body, work)

    @app.get("/api/v2/kpis/{kid}")
    def kpi_show(request: Request, kid: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return kpi_detail(c, who, kpi_or_404(c, who, kid))

    @app.post("/api/v2/kpis/{kid}")
    def kpi_update(request: Request, kid: str, body: M.KpiUpdate):
        """Change a KPI. A change to what it measures is a new definition version."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            record = kpi_or_404(c, who, kid)
            refuse_manager(c, who, "a change to a definition")
            if K.auto(kid):
                raise Problem("forbidden", "Tico computes this KPI itself; its definition is fixed", 403)
            if not may_edit_kpi(c, who, record):
                raise Problem("forbidden", "The KPI's owner or someone above them changes it", 403)
            owner = goal_owner(c, who, body.owner) if body.owner else None
            from . import growth
            if body.growth_role == "north_star" and "growth_role" in body.model_fields_set:
                # Naming a North Star moves the role off the old one, so the caller must be able to change that too.
                for old in H._rows(c.execute("SELECT * FROM kpis WHERE growth_role='north_star' AND id<>?", (kid,))):
                    if not kpi_editable(c, who, old):
                        raise Problem("forbidden", "Only whoever may change the current North Star KPI moves "
                                      "the role", 403)
            fields = body.model_dump(exclude={"owner", *growth.KPI_FIELDS})
            G.kpi_edit(c, who.actor, kid, fields, owner)
            growth.kpi_fields(c, who.actor, kid, {k: getattr(body, k) for k in growth.KPI_FIELDS
                                                  if k in body.model_fields_set})
            return kpi_detail(c, who, K.kpi(c, kid))
        return mutate(request, body, work)

    def kpi_archive_change(request, kid, body, archived):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            record = kpi_or_404(c, who, kid)
            refuse_manager(c, who, "archiving or restoring a KPI definition")
            if K.auto(kid) or not may_edit_kpi(c, who, record):
                raise Problem("forbidden", "The KPI's owner or someone above them archives or restores it", 403)
            updated = K.set_archived(c, who.actor, kid, archived)
            for link in K.links_of(c, kpi_id=kid):
                G.apply_auto(c, link["goal_id"])
            return kpi_detail(c, who, updated)
        return mutate(request, body, work)

    @app.post("/api/v2/kpis/{kid}/archive")
    def kpi_archive(request: Request, kid: str, body: M.Empty):
        """Hide a stored KPI from active lists and freshness checks. Its history and links stay intact."""
        return kpi_archive_change(request, kid, body, True)

    @app.post("/api/v2/kpis/{kid}/restore")
    def kpi_restore(request: Request, kid: str, body: M.Empty):
        """Restore an archived KPI to active lists without changing its history or links."""
        return kpi_archive_change(request, kid, body, False)

    @app.get("/api/v2/kpis/{kid}/readings")
    def kpi_readings(request: Request, kid: str, effective: bool = False):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            record = kpi_or_404(c, who, kid)
            return {"kpi": record, "readings": K.readings(c, kid, effective=effective)}

    @app.post("/api/v2/kpis/{kid}/readings")
    def kpi_log(request: Request, kid: str, body: M.KpiReading):
        """A reading: a fact with a period, evidence and a quality. Never edited; a correction is a new
        reading that names the one it supersedes."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            record = kpi_or_404(c, who, kid)
            if not may_log(c, who, record):
                raise Problem("forbidden",
                              f"The KPI's owner, the {strategy_bot.name(c)}, or the owner of a goal that uses it "
                              "logs a reading", 403)
            fields = body.model_dump(exclude={"value", "note", "source", "at"})
            return {"reading": G.kpi_log(c, who.actor, kid, body.value, body.note, body.source, body.at, **fields)}
        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/kpis")
    def bot_kpis(request: Request, bot: str):
        """A bot's automatic KPIs, computed from Tico's own data: tasks done, time to first response, approval
        rate, failed runs, model cost. Read on the bot."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            return {"bot": bot, "kpis": botkpis.for_bot(c, bot)}

    # ------------------------------------------------------------------ proposals
    @app.get("/api/v2/goal-proposals")
    def proposal_list(request: Request, status: str = "pending", goal_id: str | None = None, kpi_id: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            hidden = hidden_owners(c, who)
            rows = []
            for p in G.proposals(c, status if status != "all" else None, goal_id, kpi_id):
                if not strategy_visible(who, p, auth):
                    continue
                goal = G.goal(c, p["goal_id"]) if p["goal_id"] else None
                record = K.kpi(c, p["kpi_id"]) if p["kpi_id"] else None
                if (goal and goal["owner_id"] in hidden) or (record and record["owner_id"] in hidden):
                    continue
                rows.append({**reason_view(c, who, p, hidden), "goal_title": goal and goal["title"],
                             "kpi_name": record and record["name"], "card": G.card(c, p, hidden, company_editor(who)),
                             "may_decide": decider(c, who, p)})
            return {"proposals": rows}

    @app.post("/api/v2/goal-proposals")
    def proposal_create(request: Request, body: M.GoalProposal):
        """Propose a change the caller may not make: clearer wording, a KPI for a goal, a new definition,
        a target, or a flag (vague or duplicate). The owner confirms or rejects."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            goal = goal_or_404(c, body.goal_id) if body.goal_id else None
            if body.kpi_id:
                kpi_or_404(c, who, body.kpi_id)
            if goal and not readable_goals(c, who, [goal]):
                raise Problem("not_found", "Unknown goal", 404)
            return {"proposal": G.propose(c, who.actor, body.kind, body.goal_id, body.kpi_id, body.payload, body.reason)}
        return mutate(request, body, work)

    @app.post("/api/v2/goal-proposals/{pid}/decide")
    def proposal_decide(request: Request, pid: str, body: M.GoalProposalDecision):
        """A person's own decision: confirm makes the change as them, reject drops it."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            item = G.proposal(c, pid)
            if not item:
                raise Problem("not_found", "Unknown proposal", 404)
            if not decider(c, who, item):
                raise Problem("forbidden", "Whoever owns the goal or the KPI decides, and only a person, on their own click", 403)
            reused = (item.get("payload") or {}).get("kpi_id") if item["kind"] == "goal_kpi" else None
            record = K.kpi(c, reused) if reused and body.decision == "confirm" else None
            if record and record["owner_id"] in hidden_owners(c, who):
                # As linking it by hand is (kpi_or_404): no one puts a measure they may not read on a goal.
                raise Problem("forbidden", "That measure is one you may not read, so you cannot add it; leave it, "
                              "or someone who may read it adds it", 403)
            if item["kind"] == "kpi_target" and body.decision == "confirm" and item["goal_id"] and item["kpi_id"] \
                    and not K.link(c, item["goal_id"], item["kpi_id"]):
                # A target for a KPI the goal does not use yet links it (backend/goals.py decide): the same rule.
                target_kpi = K.kpi(c, item["kpi_id"])
                if target_kpi:
                    refuse_exposed(c, goal_or_404(c, item["goal_id"]), target_kpi["owner_id"])
            if item["kind"] == "goal_kpi" and body.decision == "confirm":
                goal = goal_or_404(c, item["goal_id"])
                if record:
                    refuse_exposed(c, goal, record["owner_id"])
                elif not reused:
                    named = (item.get("payload") or {}).get("owner_id")
                    named = (G.COMPANY if named == G.COMPANY else H.resolve_actor(c, named)) if named else None
                    refuse_exposed(c, goal, named or goal["owner_id"])
            done = G.decide(c, who.actor, pid, body.decision, body.note, why=body.why, option=body.option,
                            moves=body.moves, target=body.target.model_dump() if body.target else None)
            return {"proposal": reason_view(c, who, done, hidden_owners(c, who))}
        return mutate(request, body, work)

    @app.post("/api/v2/goal-proposals/{pid}/note")
    def proposal_reason(request: Request, pid: str, body: M.GoalProposalReason):
        """After "No, leave it": the person who turned the suggestion down tells the Strategy Bot why, in one line, once."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            item = G.proposal(c, pid)
            if not item or not strategy_visible(who, item, auth):
                raise Problem("not_found", "Unknown proposal", 404)
            if who.role not in ("owner", "human") or who.via:
                raise Problem("forbidden", "The person who turned the suggestion down adds its reason, on their own click",
                              403)
            hidden = hidden_owners(c, who)
            if reason_hidden(c, item, hidden):
                # Its reason is kept from them (criterion 14), so they get no box to write one either.
                raise Problem("forbidden", "This suggestion is about a bot you may not read, so its reason is not "
                              "yours to add", 403)
            return {"proposal": reason_view(c, who, G.add_reason(c, who.actor, pid, body.note), hidden)}
        return mutate(request, body, work)
