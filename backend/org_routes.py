"""Org routes: the bot list and bot pages, and the org view."""

import json
from fastapi import Request
from . import (
    agents,
    app_delivery,
    lifecycle,
    profiles,
    report_managers,
    rooms,
    routines,
    start_places,
    task_models,
    task_privacy as privacy,
    teammate_access,
    update_feeds,
    views,
    wake_presets,
)
from .bot_rows import BotRows
from .harnesses import resolve_harness
from .settings_admin import compact_at as compact_at_of
from .store import H, P, Problem, repo_url


def register(app, auth, settings, settings_admin, store):
    # What a caller who may only see a bot is told about it: its name, role, who runs it and who it
    # reports to. Its status, machine, queue and configuration are its activity, which is Read.
    SEE_ONLY = (
        "slug",
        "display_name",
        "state",
        "description",
        "team",
        "owner_id",
        "reports_to",
        "owners",
        "thread_mode",
        "temp",
        "access",
        "bot_owners",
        "onboarding_state",
        "private_tasks_default",
        "stage",
        "can_complete_onboarding",
    )

    def bot_view(c, bot, level, access, registry_roster, registry_entries, who, rows):
        """One bot as the bot list and the bot detail show it: everything for a caller who may read
        it, only its profile for one who may only see it. `rows` (backend/bot_rows.py) holds what
        was read for every bot in the answer."""
        row = dict(bot)
        # runtime, model and host are the bot's declaration (bot_settings); a bot with no declaration has none of them.
        # The keeper is the only host there is, so a bot that declares none is a keeper bot.
        row.update({"model": "", "runtime": "", "host": "keeper"})
        config = rows.config(bot["id"])
        assignment = rows.assignments.get(bot["id"]) if level["read"] else None
        row["access"] = level
        row["team"] = config["team"] if config else None
        row["owner_id"] = config["owner_id"] if config else None
        row["revision"] = config["revision"] if config else None
        # `needs_setup` for a starter bot until it says its setup is done, then
        # `onboarded`; empty for every other bot (backend/onboarding.py).
        row["onboarding_state"] = (config["onboarding_state"] or "") if config else ""
        # Onboarding or Active (backend/lifecycle.py); a seat being recruited is not a bot and is never in this list.
        row["stage"] = lifecycle.stage(row["onboarding_state"])
        if row["stage"] == lifecycle.ONBOARDING:
            row["can_complete_onboarding"] = lifecycle.may_complete(auth, c, who, bot["id"])
            row["onboarding_ready"] = lifecycle.ready_of(config["config"]) if config else None
        if config:
            repo = config["repo"] or ("emp-" + bot["slug"])
            from .shared_bots import follow

            declared = follow(c, bot["id"], json.loads(config["config"]) if config["config"] else {}, rows.config)
            row.update(
                {
                    "model": declared.get("model") or "",
                    "runtime": declared.get("runtime") or "",
                    "host": declared.get("host") or "keeper",
                }
            )
            if declared.get("shared_from"):
                repo = declared.get("repo") or repo
                row.update(
                    {
                        "model": declared.get("model") or "",
                        "runtime": declared.get("runtime") or "",
                        "effort": declared.get("reasoning_effort") or "",
                        "session": declared.get("session"),
                        "fallback": declared.get("fallback"),
                    }
                )
            row.update(
                {
                    "description": config["description"] or "",
                    "harness": resolve_harness(declared, None),
                    "reports_to": config["reports_to"],
                    "repo": repo,
                    "repo_url": repo_url(repo, settings.github_owner),
                    # Its checkout's folder on a computer: clients find its files by this, never by its slug.
                    "folder": H.bot_folder(bot["id"], declared),
                    "bot_contact": declared.get("bot_contact") or "open",
                    "private_tasks_default": H.private_tasks_default(c, "bot:" + bot["id"], rows.declared),
                    "template": declared.get("template") or "",
                    "template_version": declared.get("template_version") or "",
                    "update_subscriptions": update_feeds.chosen(declared),
                    "shared": bool(declared.get("shared")),
                    "shared_from": str(declared.get("shared_from") or ""),
                    "temp": bool(declared.get("temp")),
                    "compact_at": compact_at_of(declared, rows.model(c, settings, declared)),
                    # What its tasks run on: a model (empty: its own), effort and context length; null is all the same.
                    "task_model": task_models.stored(declared),
                    "turns_at_once": start_places.bot_limit(json.loads(config["config"]) or {}),
                    # Plain words for a cheaper task model on a high-stakes bot, against its model now ("" else).
                    "task_model_warning": task_models.warning(
                        c, settings, bot["id"], declared, task_models.of(declared)
                    ),
                    # Whether it changes its direct reports (backend/report_managers.py): on unless turned off.
                    "manages_reports": report_managers.switch_on(declared),
                    "wake": wake_presets.effective(declared),
                    "profile": profiles.effective(declared),
                    "thread_mode": config["thread_mode"] or rooms.thread_mode(c, bot["id"], config),
                }
            )
        configured = json.loads(config["owner_ids"]) if config and config["owner_ids"] else None
        owner_rows = (
            [rows.human(owner) for owner in configured]
            if configured is not None
            else P.primary_users(bot["id"], registry_roster, registry_entries)
        )
        row["owners"] = [P.brief(owner) for owner in owner_rows if owner]
        row["bot_owners"] = settings_admin.owner_rows(c, bot["id"], config, registry_roster) if config else []
        if (
            row.get("reports_to")
            and not str(row["reports_to"]).startswith("human:")
            and not access.get(row["reports_to"], auth.FULL)["see"]
        ):
            row["reports_to"] = ""  # a bot this caller may not see is not named to them
        if not level["read"]:
            return {k: v for k, v in row.items() if k in SEE_ONLY}
        slug = bot["id"]
        row["task_flow"] = rows.task_flow.get(slug, {"going": 0, "waiting": 0})
        row["draining"] = slug in rows.draining
        # The one task its claims are limited to (POST /api/v2/bots/{bot}/control scope): its id when this reader
        # may read the task, or only that a limit is on.
        scope = rows.scopes.get(slug)
        scoped_task = H.task(c, scope) if scope else None
        row["claim_scoped"] = bool(scope)
        row["claim_task"] = scope if scoped_task and privacy.task_readable(c, who, scoped_task) else None
        status = rows.statuses.get(slug)
        row["status"] = privacy.status(c, who, dict(status), **rows.status_inputs[slug]) if status else status
        row["assignment"] = dict(assignment) if assignment else None
        row["online"] = bool(
            assignment
            and not assignment["revoked_at"]
            and assignment["last_seen_at"]
            and assignment["last_seen_at"] > H.shift(H.now(), seconds=-60)
        )
        row["queued"] = rows.queued[slug]
        row["notes"] = rows.notes[slug]
        harness = rows.external_harness(slug)
        external = agents.presence(c, slug, harness) if harness else None
        row["agent"] = external["agent"] if external else None
        # Delivered through a desktop app (backend/app_delivery.py): where, and online is that computer's.
        # `app_delivery_app`: the desktop app the bot could be delivered through, for its settings.
        row["app_delivery"] = app_delivery.view(c, declared) if config and harness else None
        row["app_delivery_app"] = app_delivery.HARNESS_APP.get(row.get("harness"))
        if external and not (row["app_delivery"] or {}).get("active"):
            row["online"] = external["online"]
        return row

    @app.get("/api/v2/bots")
    def bots(request: Request, include_archived: str | None = None, can: str | None = None):
        """The bots the caller may see, each with `access` (their own see, read and write on it).
        `?can=read` or `?can=write` keeps only the bots they hold that level on."""
        who = request.state.identity
        auth.domain(who)
        if can not in (None, "", "read", "write"):
            raise Problem("can", "can is read or write", 422)
        # Archived bots are gone from every picker; an admin view that needs them asks with ?include_archived=1.
        with_archived = (include_archived or "").lower() in ("1", "true", "yes")
        from .chat_goals import readable_active

        with store.read() as c:
            privacy.snapshot(c)
            registry_roster, registry_entries = views.roster(c), views.entries(c, settings.github_owner)
            access = auth.bot_accesses(c, who)
            shown = []
            for bot in H.bots(c):
                level = access.get(bot["id"], auth.FULL)
                if not level["see"] or (can and not level[can]):
                    continue
                if bot.get("state") == "archived" and not with_archived:
                    continue
                shown.append((bot, level))
            rows = BotRows(
                c, who, [bot["id"] for bot, _ in shown], {bot["id"] for bot, level in shown if level["read"]}
            )
            redact = teammate_access.bot_redactor(c, who, auth)  # people and groups the caller may not see
            return [
                redact(
                    {
                        **bot_view(c, bot, level, access, registry_roster, registry_entries, who, rows),
                        "goal_active": readable_active(c, auth, who, bot["id"], rows.goals.get(bot["id"], [])),
                    }
                )
                for bot, level in shown
            ]

    def cant_run(c, bot):
        """The bot page's top line (docs/pipelines-v3.md S14): why the bot can't run, as the never-stuck check reads it
        (task_wakes.cannot_run), and the one fix a person makes, or None while it can run."""
        from . import task_wakes as W

        cause = W.cannot_run(c, "bot:" + bot)
        if not cause:
            return None
        fixes = {
            W.STATE_CAUSES["paused"]: "resume",
            W.STATE_CAUSES["quarantined"]: "release_quarantine",
            W.STATE_CAUSES["planned"]: "finish_setup",
            W.ONBOARDING: "finish_setup",
            W.NO_COMPUTER: "assign_computer",
            W.CLOUD_STARTING: "open_computers",
            W.CLOUD_FAILED: "open_computers",
            W.RUNS_FAILING: "release_hold",
            W.LOGIN: "reconnect",
            W.COMPANY_PAUSED: "resume_company",
            W.CLOUD_LOCKED: "unlock_cloud",
            W.OUT_OF_CREDIT: "add_credit",
        }
        return {"cause": W.cause_line(c, "bot:" + bot, cause), "fix": fixes.get(cause)}

    @app.get("/api/v2/bots/{bot}")
    def bot_detail(request: Request, bot: str):
        """One bot: the profile a caller who can see it gets (name, role, who runs it, who it reports
        to, who it works for), and for one who can read it also its status, machine, queue, goals and
        which of its routines are on."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            row = H.bot(c, bot)
            if not row:
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            config = c.execute("SELECT reports_to,owner_id,goals FROM bot_config WHERE bot=?", (bot,)).fetchone()
            watched = [bot] + ([config["reports_to"]] if config and config["reports_to"] else [])
            access = auth.bot_accesses(c, who, watched)
            level = access[bot]
            value = bot_view(
                c,
                row,
                level,
                access,
                views.roster(c),
                views.entries(c, settings.github_owner),
                who,
                BotRows(c, who, [bot], {bot} if level["read"] else set()),
            )
            if config and level["read"]:
                value["goals"] = config["goals"] or ""
            if level["read"]:
                value["cant_run"] = cant_run(c, bot)
                value["routines"] = routines.of_bot(c, bot)
            reports = value.get("reports_to") or ""
            if reports.startswith("human:"):
                person = H.human(c, reports[6:])
                value["reports_to_name"] = (person or {}).get("name") or reports[6:]
            elif reports:
                reports_bot = H.bot(c, reports) or {}
                value["reports_to_name"] = reports_bot.get("display_name") or reports_bot.get("slug") or reports
            if value.get("owner_id"):
                value["owner_name"] = (H.human(c, value["owner_id"]) or {}).get("name") or value["owner_id"]
            return teammate_access.bot_redactor(c, who, auth)(value)

    @app.get("/api/v2/org")
    def org(request: Request, person: str | None = None, team: str | None = None, can: str | None = None):
        """The mixed people-and-bots org chart. Bots use this (and `tico team show` / `tico_team_show`) to
        find who handles a kind of work and how to reach them. Only the bots the caller may see,
        each with `access`, its `reports_to`, its `team` (its group's id), its `department` (its group's name)
        and its `template`; the groups come with their `parent`; `?can=read` or `?can=write` keeps those they hold that level on."""
        who = request.state.identity
        auth.domain(who)
        if can not in (None, "", "read", "write"):
            raise Problem("can", "can is read or write", 422)
        with store.read() as c:
            access = auth.bot_accesses(c, who)
            roster, configs = views.roster(c), views.entries(c)
            archived = {row["id"] for row in H.bots(c) if row.get("state") == "archived"}
            # The Strategy Owner heads the chart: bots move on the chart only; each keeps the `reports_to` it has
            # (backend/strategy_bot.py). A person who hid the Strategy Owner from their chart gets it without the Strategy
            # Bot, nothing moved under it.
            from . import strategy_bot

            hide_strategy = strategy_bot.hidden_for(c, who)
            chart = strategy_bot.chart_entries(c, settings, roster, configs, archived, hidden=hide_strategy)
            # A bot's bare person id in reports_to is that person (backend/people.py).
            view = P.org_view(roster, chart, archived, person_id=person or "", team=team or "", bare_people=True)
            view["org_groups"] = strategy_bot.chart_groups(
                roster, configs, chart, view["org_groups"], archived, hidden=hide_strategy
            )
            for row in view["bots"]:
                row["reports_to"] = P._clean((configs.get(row["id"]) or {}).get("reports_to"))
            live = {row["id"]: row for row in H.bots(c)}
            bots = []
            by_id = {row["id"]: row for row in view["bots"]}
            departments = P.bot_departments(configs, roster, archived)

            # On the chart: what the caller may see, less the Strategy Owner when they hid it (those under it hang from
            # the next thing up, as under a bot they may not see; their `reports_to` stays the stored one).
            def drawn(slug):
                return access.get(slug, auth.FULL)["see"] and not (hide_strategy and slug == strategy_bot.SLUG)

            for row in view["bots"]:
                level = access.get(row["id"], auth.FULL)
                if not drawn(row["id"]) or (can and not level[can]):
                    continue
                bot = live.get(row["id"]) or {}
                shown = {
                    **row,
                    "slug": bot.get("slug") or row["id"],
                    "name": bot.get("slug") or row["id"],
                    "display_name": bot.get("display_name") or row["display_name"],
                    "department": departments.get(row["id"], ""),
                    "template": configs.get(row["id"], {}).get("template") or "",
                    "status": bot.get("state") or "",
                    "access": level,
                    "onboarding_state": configs.get(row["id"], {}).get("onboarding_state") or "",
                }
                # A bot the caller may not see is not in the chart, so those under it hang from
                # the nearest thing above it that is: another bot, else its person or department.
                parent, seen = row["org_parent"], set()
                while parent.startswith("b:") and parent[2:] in by_id and parent not in seen and not drawn(parent[2:]):
                    seen.add(parent)
                    parent = by_id[parent[2:]]["org_parent"]
                shown["org_parent"] = parent
                if (
                    row["reports_to"]
                    and not row["reports_to"].startswith("human:")
                    and not access.get(row["reports_to"], auth.FULL)["see"]
                ):
                    shown["reports_to"] = (
                        parent[2:]
                        if parent.startswith("b:")
                        else ("human:" + parent[2:] if parent.startswith("p:") else "")
                    )
                bots.append(shown)
            # Recruiting seats hang where a bot in that place would; one under a bot the caller may not see hangs from
            # the nearest thing above it that they may, like that bot's own reports.
            placed = (
                {"b:" + r["id"] for r in bots}
                | {"p:" + r["id"] for r in view["people"]}
                | {"g:" + r["id"] for r in view["org_groups"]}
            )
            seats, every = [], lifecycle.all_seats(c)
            parents = {}
            for seat in c.execute("SELECT * FROM seats WHERE closed_at IS NULL ORDER BY created_at,id").fetchall():
                parent, seen = lifecycle.org_parent(seat, roster, chart, archived, every, True), set()
                while parent.startswith("b:") and parent[2:] in by_id and parent not in seen and not drawn(parent[2:]):
                    seen.add(parent)
                    parent = by_id[parent[2:]]["org_parent"]
                parents[seat["id"]] = parent

            def shown_here(parent, seen=()):
                """A seat under a seat is on this view while the top seat of its branch is."""
                if parent.startswith("s:") and parent[2:] in parents and parent not in seen:
                    return shown_here(parents[parent[2:]], (*seen, parent))
                return parent in placed

            for seat in c.execute("SELECT * FROM seats WHERE closed_at IS NULL ORDER BY created_at,id").fetchall():
                parent = parents[seat["id"]]
                if (person or team) and not shown_here(parent):
                    continue
                shown = {
                    k: v
                    for k, v in dict(seat).items()
                    if k in ("id", "title", "description", "team", "created_at", "agent", "agent_name", "request_state")
                }
                reports = seat["reports_to"]
                if reports and not reports.startswith("human:") and not access.get(reports, auth.FULL)["see"]:
                    reports = (
                        parent[2:]
                        if parent.startswith("b:")
                        else ("human:" + parent[2:] if parent.startswith("p:") else "")
                    )
                seats.append(
                    {
                        **shown,
                        "kind": "seat",
                        "stage": lifecycle.RECRUITING,
                        "reports_to": reports,
                        "org_parent": parent,
                    }
                )
            for row in bots:
                row["stage"] = lifecycle.stage(row["onboarding_state"])
            # People and groups the caller may not see are left out, and what hung under them moves up.
            people_hidden, groups_hidden = teammate_access.unseen(c, who, auth)
            return teammate_access.redact_chart(
                {**view, "bots": bots, "seats": seats}, roster, people_hidden, groups_hidden
            )
