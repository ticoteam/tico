"""The goals, KPIs, check-ins and proposals API (docs/goals-and-kpis.md).

Who may do what, in one place. Company goals and KPIs are visible to everyone signed in; a bot's own
are visible to whoever may Read the bot (docs/permissions.md). A goal's owner, the owner of the goal
it serves and anyone above the owner on the org chart set its colour and its KPIs. A KPI's owner (or
anyone above them; the company owner for a company KPI) edits it and its readings. The Goal Manager,
the built-in steward of every KPI, reads all of them and writes readings and automatic colours, and
nothing else: a change to a definition or a target it is judged against is a proposal that a
person confirms.
"""

from fastapi import Request

from . import botkpis
from . import goals as G
from . import kpis as K
from . import models as M
from . import views
from .store import H, Problem


def install(app, store, auth, mutate, settings):
    # ------------------------------------------------------------------ who may
    def org_shape(c):
        roster, entries = views.roster(c), views.entries(c)
        archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
        return roster, entries, archived

    def is_manager(who):
        """The Goal Manager acting as itself."""
        return who.role == "bot" and who.actor == G.AUTO_ACTOR

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

    def stands_above(c, who, owner):
        """Whether `who` is the owner of the environment or sits above `owner` on the org chart:
        the tasks board's movers rule, over people and bots alike."""
        if who.role == "owner":
            return True
        if who.role not in ("human", "bot"):
            return False
        roster, entries, archived = org_shape(c)
        return who.actor in G.above(owner, roster, entries, archived)

    def parent_owner(c, row):
        parent = G.goal(c, row["parent_id"]) if row.get("parent_id") else None
        return parent["owner"] if parent else None

    def may_colour(c, who, row):
        """The goal's owner, the owner of the goal it serves, or anyone above the owner."""
        if is_manager(who):
            return False
        return who.actor in (row["owner"], parent_owner(c, row)) or stands_above(c, who, row["owner"])

    def may_edit_kpi(c, who, record):
        if who.role == "owner":
            return True
        if is_manager(who) or record["owner"] == G.COMPANY:
            return False
        return who.actor == record["owner"] or stands_above(c, who, record["owner"])

    def may_log(c, who, record):
        """Whoever keeps the KPI, the Goal Manager, or the owner of a goal that uses it."""
        if is_manager(who) or may_edit_kpi(c, who, record):
            return True
        return any(may_colour(c, who, g) for g in (G.goal(c, link["goal_id"]) for link in K.links_of(c, kpi_id=record["id"])) if g)

    def goal_or_404(c, goal_id):
        row = G.goal(c, goal_id)
        if not row:
            raise Problem("not_found", "Unknown goal", 404)
        return row

    def hidden_owners(c, who):
        if is_manager(who):
            return set()
        return {"bot:" + slug for slug in auth.unreadable_bots(c, who)}

    def readable_goals(c, who, rows):
        """A bot's goals are its work: a caller sees them only when they may Read the bot (People's goals stay the
        company's, as before). The Goal Manager reads every goal, as it keeps every KPI."""
        hidden = hidden_owners(c, who)
        return [row for row in rows if row["owner"] not in hidden] if hidden else list(rows)

    def kpi_or_404(c, who, kpi_id):
        record = K.kpi(c, kpi_id)
        if not record or record["owner"] in hidden_owners(c, who):
            raise Problem("not_found", "Unknown KPI", 404)
        return record

    def refuse_manager(who, what):
        if is_manager(who):
            raise Problem("forbidden", f"The Goal Manager proposes {what}; the owner confirms it "
                          "(POST /api/v2/goal-proposals)", 403)

    def owner_names(c, rows):
        names = {}
        for row in rows:
            owner = row["owner"]
            if owner in names:
                continue
            if owner == G.COMPANY:
                names[owner] = {"kind": "company", "id": G.COMPANY, "name": settings.company_name or "Company"}
            elif H.is_bot(owner):
                bot = H.bot(c, H.actor_id(owner)) or {}
                names[owner] = {"kind": "bot", "id": H.actor_id(owner),
                                "name": bot.get("display_name") or H.actor_id(owner),
                                "status": H.status(c, H.actor_id(owner))}
            else:
                human = H.human(c, H.actor_id(owner)) or {}
                names[owner] = {"kind": "person", "id": H.actor_id(owner), "name": human.get("name") or H.actor_id(owner)}
        return names

    def decider(c, who, item):
        """Whether this person may confirm the proposal: a person's own decision, by whoever owns what changes."""
        if who.role not in ("owner", "human") or who.via:
            return False
        goal = G.goal(c, item["goal_id"]) if item["goal_id"] else None
        record = K.kpi(c, item["kpi_id"]) if item["kpi_id"] else None
        by_goal = bool(goal) and may_colour(c, who, goal)
        by_kpi = bool(record) and may_edit_kpi(c, who, record)
        if item["kind"] in ("goal_wording", "flag"):
            return by_goal
        if item["kind"] == "kpi_definition":
            return by_kpi
        return by_goal or by_kpi

    # ------------------------------------------------------------------ goals
    @app.get("/api/v2/goals")
    def goals_list(request: Request, owner: str | None = None, all: bool = False, status: str | None = None):
        """`hub goal list`: mine, the chain above and my reports' by default; `all` is every goal."""
        who = request.state.identity
        auth.domain(who)
        with store.transaction() as c:
            if all:
                statuses = tuple(status.split(",")) if status else None
                rows = readable_goals(c, who, G.goals(c, status=statuses, live_only=not statuses))
                return {"goals": rows}
            actor = goal_owner(c, who, owner)
            roster, entries, archived = org_shape(c)
            out = G.for_actor(c, actor, roster, entries, archived)
            if who.role == "bot" and actor == who.actor:
                G.mark_read(c, who.actor, [g["id"] for g in out["goals"] + out["chain"]])
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
            by_goal = K.goal_views(c, [r["id"] for r in rows])
            said = {}
            for row in c.execute("SELECT * FROM goal_checkins k WHERE ts=(SELECT max(ts) FROM goal_checkins "
                                 "WHERE goal_id=k.goal_id)"):
                said[row["goal_id"]] = dict(row)
            for row in rows:
                row["kpis"] = by_goal.get(row["id"], [])
                row["open_tasks"] = open_counts.get(row["id"], 0)
                row["checkin"] = said.get(row["id"])
            unaligned = {r["owner"]: r["n"] for r in c.execute(
                "SELECT owner, count(*) AS n FROM tasks WHERE goal_id IS NULL AND status IN "
                f"('open','doing','waiting') AND {readable} GROUP BY owner")}
            hidden = hidden_owners(c, who)
            unaligned = {owner: n for owner, n in unaligned.items() if owner not in hidden}
            loose = [dict(r) for r in c.execute("SELECT * FROM kpis WHERE archived_at IS NULL "
                                                "AND id NOT IN (SELECT kpi_id FROM goal_kpis) "
                                                "ORDER BY created") if r["owner"] not in hidden]
            data = K.effective_many(c, [r["id"] for r in loose])
            other = [K.view(r, data.get(r["id"], []), None) for r in loose]
            names.update(owner_names(c, other))
            visible = {r["id"] for r in rows}
            pending = [p for p in G.proposals(c, "pending") if not p["goal_id"] or p["goal_id"] in visible]
            return {"goals": rows, "owners": names, "unaligned": unaligned, "other_kpis": other,
                    "proposals": [{**p, "may_decide": decider(c, who, p)} for p in pending]}

    @app.get("/api/v2/goals/needs-you")
    def goals_needs_you(request: Request):
        """What waits on the caller here: red KPIs on goals they own, stale KPIs they own, and definitions and
        targets they are asked to confirm."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            def mine(owner):
                return owner == who.actor or (owner == G.COMPANY and who.role == "owner")
            items = []
            goals = [g for g in readable_goals(c, who, G.goals(c, live_only=True)) if mine(g["owner"])]
            for goal_id, kpis in K.goal_views(c, [g["id"] for g in goals]).items():
                title = next(g["title"] for g in goals if g["id"] == goal_id)
                for k in kpis:
                    if k["status"] == "red":
                        items.append({"kind": "kpi_red", "goal_id": goal_id, "goal_title": title, "kpi_id": k["id"],
                                      "kpi_name": k["name"], "reason": k["reason"]})
            owned = [dict(r) for r in c.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created")
                     if mine(r["owner"])]
            data = K.effective_many(c, [r["id"] for r in owned])
            for record in owned:
                rows = data.get(record["id"], [])
                state = K.assess(record, None, rows)
                if state["freshness"] != "fresh":
                    items.append({"kind": "kpi_stale", "kpi_id": record["id"], "kpi_name": record["name"],
                                  "freshness": state["freshness"], "reason": state["reason"]})
            for p in G.proposals(c, "pending"):
                if decider(c, who, p):
                    goal = G.goal(c, p["goal_id"]) if p["goal_id"] else None
                    record = K.kpi(c, p["kpi_id"]) if p["kpi_id"] else None
                    items.append({"kind": "proposal", "proposal": p, "goal_title": goal and goal["title"],
                                  "kpi_name": record and record["name"]})
            return {"actor": who.actor, "items": items}

    @app.post("/api/v2/goals/refresh")
    def goals_refresh(request: Request, body: M.GoalRefresh):
        """The Goal Manager's status pass: work every live goal's automatic colour out again (a goal a
        person set only gets a suggestion). The owner may run it too."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            if not (is_manager(who) or who.role == "owner"):
                raise Problem("forbidden", "The Goal Manager or the owner runs the status pass", 403)
            return G.refresh(c, body.goal_ids)
        return mutate(request, body, work)

    @app.get("/api/v2/goals/{gid}")
    def goal_show(request: Request, gid: str):
        who = request.state.identity
        auth.domain(who)
        with store.transaction() as c:
            row = goal_or_404(c, gid)
            if not readable_goals(c, who, [row]):
                raise Problem("not_found", "Unknown goal", 404)
            if who.role == "bot":
                G.mark_read(c, who.actor, [gid])
            return {"goal": G.view(c, row, auth.task_sql(c, who))}

    @app.post("/api/v2/goals")
    def goal_create(request: Request, body: M.GoalCreate):
        who = request.state.identity
        def work(c):
            owner = goal_owner(c, who, body.owner)
            parent = goal_or_404(c, body.parent_id) if body.parent_id else None
            # A goal needs no parent: a person or a bot may set their own, and it is simply unlinked.
            if who.role != "owner":
                if not (owner == who.actor or (parent and parent["owner"] == who.actor) or stands_above(c, who, owner)):
                    raise Problem("forbidden", "You can set a goal for yourself, under a goal you own, "
                                  "or for someone below you on the org chart", 403)
            row = G.create(c, who.actor, body.title, owner, body.parent_id, body.body, top=body.top)
            return {"goal": G.view(c, row)}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}/status")
    def goal_status(request: Request, gid: str, body: M.GoalStatus):
        """Set the colour by hand: it sticks, with the setter's name and note, until a person hands it back."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(who, "a colour with the automatic status pass, and never sets one by hand")
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, or someone "
                              "above them sets the colour", 403)
            # A proposed goal's first colour is its acceptance: the parent's owner or someone above
            # gives it, not the proposer.
            # Removing one is not accepting it: whoever may edit a goal may drop it (an emptied goal on the Goals page is removed).
            if row["status"] is None and row.get("parent_id") and body.status != "dropped" and not (
                    who.actor == parent_owner(c, row) or stands_above(c, who, row["owner"])):
                raise Problem("forbidden", "A proposed goal gets its first colour from whoever owns the goal "
                              "it serves; that is the acceptance", 403)
            return {"goal": G.view(c, G.set_status(c, who.actor, gid, body.status, body.note))}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}/status/auto")
    def goal_status_auto(request: Request, gid: str, body: M.Empty):
        """"Let Goal Manager set it": a person ends their override and the colour is worked out again now."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            if is_manager(who) or not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, or someone "
                              "above them hands the colour back", 403)
            return {"goal": G.view(c, G.hand_back(c, who.actor, gid))}
        return mutate(request, body, work)

    @app.post("/api/v2/goals/{gid}")
    def goal_update(request: Request, gid: str, body: M.GoalUpdate):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            refuse_manager(who, "clearer wording or a change of goal")
            # Handing a goal to someone else is for whoever sits above; linking it to the goal it
            # supports is also its owner's (the link icon on the Goals page), and the new goal's owner
            # sees it there.
            if body.owner is not None and not (who.actor == parent_owner(c, row) or stands_above(c, who, row["owner"])):
                raise Problem("forbidden", "Moving a goal is for the owner of the goal it serves or someone "
                              "above its owner", 403)
            structural = body.parent_id is not None or body.owner is not None
            if body.parent_id is not None and not may_colour(c, who, row):
                raise Problem("forbidden", "Linking a goal is for its owner, the owner of the goal it serves, "
                              "or someone above its owner", 403)
            if not structural and not may_colour(c, who, row):
                raise Problem("forbidden", "You cannot edit this goal", 403)
            owner = goal_owner(c, who, body.owner) if body.owner else None
            return {"goal": G.view(c, G.update(c, who.actor, gid, title=body.title, body=body.body,
                                               parent_id=body.parent_id, owner=owner, rank=body.rank,
                                               top=body.top))}
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
        """The owner's own words on how it is going (a person, or the Goal Manager recording their answer)."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            row = goal_or_404(c, gid)
            if not (may_colour(c, who, row) or is_manager(who)):
                raise Problem("forbidden", "The goal's owner, the owner of the goal it serves, someone above them "
                              "or the Goal Manager records a check-in", 403)
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
            refuse_manager(who, "a KPI for a goal")
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner or someone above them adds a measure", 403)
            target = {k: getattr(body, k) for k in ("kind", "baseline", "baseline_at", "target", "deadline", "min", "max")}
            if body.kpi_id:
                kpi_or_404(c, who, body.kpi_id)
                G.kpi_link(c, who.actor, gid, body.kpi_id, target)
                return {"kpi": views_of(c, gid, body.kpi_id)}
            if not body.name:
                raise Problem("validation", "Give a kpi_id to link, or a name to make a new KPI", 422)
            owner = goal_owner(c, who, body.owner) if body.owner else row["owner"]
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
            refuse_manager(who, "a target it is judged against")
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
            refuse_manager(who, "removing a KPI from a goal")
            if not may_colour(c, who, row):
                raise Problem("forbidden", "The goal's owner or someone above them removes a measure", 403)
            G.kpi_unlink(c, who.actor, gid, kid)
            return {"goal": G.view(c, G.goal(c, gid))}
        return mutate(request, body, work)

    # ------------------------------------------------------------------ KPIs
    def kpi_detail(c, who, record):
        rows = K.readings(c, record["id"])
        effective = [r for r in rows if not r.get("superseded_by")]
        links = []
        for link in K.links_of(c, kpi_id=record["id"]):
            goal = G.goal(c, link["goal_id"])
            if not goal or goal["owner"] in hidden_owners(c, who):
                continue
            state = K.assess(record, link, effective)
            links.append({**link, "goal_title": goal["title"], "goal_owner": goal["owner"],
                          "goal_status": goal["status"], "target_label": K.target_label(record, link),
                          "status": state["status"], "reason": state["reason"], "expected": state["expected"]})
        goal_ids = [link["goal_id"] for link in links]
        marks = ",".join("?" * len(goal_ids))
        said = H._rows(c.execute(
            f"SELECT * FROM goal_checkins WHERE kpi_id=? OR goal_id IN ({marks}) ORDER BY ts DESC LIMIT 5",
            (record["id"], *goal_ids))) if goal_ids else H._rows(c.execute(
                "SELECT * FROM goal_checkins WHERE kpi_id=? ORDER BY ts DESC LIMIT 5", (record["id"],)))
        return {"kpi": K.view(record, effective), "links": links, "readings": rows,
                "definitions": [] if K.auto(record["id"]) else K.definitions(c, record["id"]),
                "checkins": said, "proposals": [{**p, "may_decide": decider(c, who, p)} for p in G.proposals(c, "pending", kpi_id=record["id"])],
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
            records = [dict(r) for r in c.execute(sql + " ORDER BY created", args)
                       if r["owner"] not in hidden and (not actor or r["owner"] == actor)]
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
            refuse_manager(who, "a new KPI (a proposal of kind goal_kpi)")
            goal = goal_or_404(c, body.goal_id) if body.goal_id else None
            if goal and not may_colour(c, who, goal):
                raise Problem("forbidden", "The goal's owner or someone above them adds a measure", 403)
            owner = goal_owner(c, who, body.owner) if body.owner else (goal["owner"] if goal else who.actor)
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
            refuse_manager(who, "a change to a definition")
            if K.auto(kid):
                raise Problem("forbidden", "Tico computes this KPI itself; its definition is fixed", 403)
            if not may_edit_kpi(c, who, record):
                raise Problem("forbidden", "The KPI's owner or someone above them changes it", 403)
            owner = goal_owner(c, who, body.owner) if body.owner else None
            fields = body.model_dump(exclude={"owner"})
            G.kpi_edit(c, who.actor, kid, fields, owner)
            return kpi_detail(c, who, K.kpi(c, kid))
        return mutate(request, body, work)

    def kpi_archive_change(request, kid, body, archived):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            record = kpi_or_404(c, who, kid)
            refuse_manager(who, "archiving or restoring a KPI definition")
            if K.auto(kid) or not may_edit_kpi(c, who, record):
                raise Problem("forbidden", "The KPI's owner or someone above them archives or restores it", 403)
            updated = K.set_archived(c, who.actor, kid, archived)
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
                raise Problem("forbidden", "The KPI's owner, the Goal Manager, or the owner of a goal that uses it "
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
                goal = G.goal(c, p["goal_id"]) if p["goal_id"] else None
                record = K.kpi(c, p["kpi_id"]) if p["kpi_id"] else None
                if (goal and goal["owner"] in hidden) or (record and record["owner"] in hidden):
                    continue
                rows.append({**p, "goal_title": goal and goal["title"], "kpi_name": record and record["name"],
                             "may_decide": decider(c, who, p)})
            return {"proposals": rows}

    @app.post("/api/v2/goal-proposals")
    def proposal_create(request: Request, body: M.GoalProposal):
        """Propose a change the caller may not make: clearer wording, a KPI for a goal, a new definition,
        a target, or a flag (vague, duplicate, unmeasured). The owner confirms or rejects."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            goal = goal_or_404(c, body.goal_id) if body.goal_id else None
            record = kpi_or_404(c, who, body.kpi_id) if body.kpi_id else None
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
            return {"proposal": G.decide(c, who.actor, pid, body.decision, body.note)}
        return mutate(request, body, work)
