"""Audited bot settings, reversible changes, and transitions that apply at once."""

import difflib
import json
import re
from types import SimpleNamespace

from . import access as Access
from . import message_bots as MessageBots
from . import bot_access as BA
from . import models as M
from . import placement
from . import providers
from . import rooms
from . import shared_bots
from .auth import Identity
from .execution import _reported, stranded
from .harnesses import EXTERNAL_HARNESSES, HARNESS_BY_ID, normalize_fallback, resolve_harness, runtime_of
from .readiness import absolute, can_run
from .statuses import PARKED_SQL
from . import read_cache
from .store import H, P, Problem, bot_readiness, encode, readiness_document, repo_url


def _json(value, fallback=None):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return fallback

def repository_present(c, bot):
    from .agents import external_harness
    if external_harness(c, bot):
        return True
    row = c.execute("SELECT r.readiness_json FROM assignments a JOIN runners r ON r.id=a.runner_id "
                    "WHERE a.bot=?", (bot,)).fetchone()
    report = bot_readiness(row["readiness_json"], bot) if row else {}
    return bool(report.get("repository_present"))


class SettingsAdmin:
    def __init__(self, store, auth, execution, models, reset_sessions):
        self.store, self.auth, self.execution = store, auth, execution
        self.models, self.reset_sessions = models, reset_sessions
        self.settings = store.settings

    @staticmethod
    def _owner(who):
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner may change company bot settings", 403)

    def _creator(self, c, who):
        """Who may register a bot: the owner and Admins always; a member unless their create_bots is switched
        off, and only up to the company's limit of active bots each."""
        if who.role == "owner" or self.auth.bot_admin(who):
            return
        pid = H.actor_id(who.actor)
        if who.role != "human" or not Access.can_create_bots(P.person(pid, self._roster(c)), "member"):
            raise Problem("forbidden", "You may not add bots. Ask an owner or an admin to let you", 403)
        limit = Access.load_access(c, self.settings)["member_bot_limit"]
        have = self.counted_bots(c, who.actor)
        if have >= limit:
            raise Problem("bot_limit", f"You already have {have} active bots, the most a member may have ({limit}). "
                          "Archive one you no longer need, or ask an admin to raise the limit", 409)

    @staticmethod
    def counted_bots(c, actor, excluding=""):
        """The bots that count toward a member's limit: theirs that are not archived. A starter bot
        that is still `needs_setup` is parked, so it does not count until it is onboarded."""
        return c.execute("SELECT count(*) FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                         "WHERE bc.created_by=? AND b.state<>'archived' AND bc.bot<>? "
                         "AND COALESCE(bc.onboarding_state,'') NOT IN " + PARKED_SQL, (actor, excluding)).fetchone()[0]

    def _manager(self, c, who, bot, botops=False):
        """The one "may manage this bot" check (`Auth.bot_manager`): the owner, an Admin, one of the bot's
        owners, or a person it reports up to. They are also the people who always have full access to it.
        `botops`: BotOps in its own run may also make this change (`Auth.botops_manages`)."""
        if (who.role == "owner" or self.auth.bot_manager(c, who, bot)
                or (botops and self.auth.botops_manages(c, who, bot))):
            return
        raise Problem("forbidden", "You may change only bots you own or that report up to you", 403)

    @staticmethod
    def _config(c, bot):
        row = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
        if not row:
            raise Problem("not_found", "Bot not found", 404)
        return row

    @staticmethod
    def _effort(choice, requested=None, current=None):
        supported = tuple(choice.get("efforts") or ())
        value = str(requested or "").strip().lower()
        if requested is not None and value not in supported:
            raise Problem("effort", "Choose an effort supported by that model", 422)
        current = str(current or "").strip().lower()
        return value or (current if current in supported else choice.get("default_effort") or "")

    @staticmethod
    def _harness(choice, requested=None, current=None):
        supported = tuple(choice.get("harnesses") or (choice.get("runtime"),))
        value = str(requested or "").strip().lower()
        if requested is not None and value not in supported:
            raise Problem("harness", "Choose a harness that can run that model", 422)
        current = str(current or "").strip().lower()
        return value or (current if current in supported else (supported[0] if supported else ""))

    @staticmethod
    def _entry(row):
        config = _json(row["config_json"], {}) or {}
        config.update({"name": row["bot"], "description": row["description"],
                       "reports_to": row["reports_to"], "repo": row["repo"],
                       "thread_mode": row["thread_mode"] or config.get("thread_mode") or "personal"})
        return config

    def _entries(self, c):
        return {row["bot"]: self._entry(row) for row in read_cache.configs(c)}

    @staticmethod
    def _roster(c):
        row = read_cache.metadata(c, "people")
        return P.load(_json(row[0], {}) if row else {"people": H.humans(c)})

    def _team(self, c, bot, proposed=None):
        entries = self._entries(c)
        if proposed is not None:
            entries[bot] = proposed
        return P.team_of(bot, entries, self._roster(c))

    @staticmethod
    def _people(c, ids):
        unique = list(dict.fromkeys(ids))
        rows = [H.human(c, pid) for pid in unique]
        missing = [pid for pid, row in zip(unique, rows) if not row]
        if missing:
            raise Problem("not_found", "Unknown person: " + ", ".join(missing), 404)
        return unique, rows

    @staticmethod
    def _parent(c, bot, parent):
        """A bot reports to another bot, or to a person (`human:<id>`), never in a circle."""
        if not parent:
            return
        if str(parent).startswith("human:"):
            from . import views
            if not P.person(parent[6:], views.roster(c)):
                raise Problem("not_found", "Reports-to person was not found", 404)
            return
        if not H.bot(c, parent):
            raise Problem("not_found", "Reports-to bot was not found", 404)
        seen, current = {bot}, parent
        while current:
            if current in seen:
                raise Problem("hierarchy", "A bot cannot report to itself or one of its descendants", 422)
            seen.add(current)
            row = c.execute("SELECT reports_to,config_json FROM bot_config WHERE bot=?", (current,)).fetchone()
            if not row:
                break
            current = row["reports_to"] or (_json(row["config_json"], {}) or {}).get("reports_to")

    def definition(self, c, bot):
        config, row = self._config(c, bot), H.bot(c, bot)
        effective = shared_bots.follow(c, bot, _json(config["config_json"], {}) or {})
        repo = effective.get("repo") or config["repo"] or ("emp-" + bot)
        return {"slug": bot, "display_name": row["display_name"],
                "template": (_json(config["config_json"], {}) or {}).get("template") or "",
                "description": config["description"] or "", "reports_to": config["reports_to"],
                "bot_contact": (_json(config["config_json"], {}) or {}).get("bot_contact") or "open",
                "private_tasks_default": H.private_tasks_default(c, "bot:" + bot),
                "status": row["state"], "repo": repo,
                "repo_url": repo_url(repo, self.settings.github_owner),
                "thread_mode": config["thread_mode"] or "personal",
                "shared": bool((_json(config["config_json"], {}) or {}).get("shared")),
                "shared_from": shared_bots.source_of(_json(config["config_json"], {})),
                "temp": bool((_json(config["config_json"], {}) or {}).get("temp")),
                "operator": config["operator"], "revision": config["revision"],
                "model": effective.get("model") or row["model"], "runtime": effective.get("runtime") or row["runtime"],
                "effort": effective.get("reasoning_effort") or row["effort"],
                "session": effective.get("session"),
                "harness": resolve_harness(effective, effective.get("runtime") or row.get("runtime")),
                "fallback": normalize_fallback(effective.get("fallback"))}

    @staticmethod
    def refuse_retired(choice):
        """A retired model stays in the catalog for bots already on it; nothing new may pick it."""
        if choice.get("deprecated"):
            raise Problem("model", f"{choice['label']} is retired; choose a current model", 422)

    def validate_template(self, template):
        if not template:
            return
        from .onboarding import read_cards
        names = [card["template"] for card in read_cards(self.settings)]
        if template not in names:
            closest = difflib.get_close_matches(template, names, n=3)
            hint = " Closest: " + ", ".join(closest) + "." if closest else ""
            raise Problem("template", f"Unknown template {template}. Use hub_template_list.{hint}", 422)

    def template_repo_defaults(self, template):
        from pathlib import Path
        import yaml
        if not template:
            return {}
        try:
            manifest = yaml.safe_load((Path(self.settings.catalog_dir) / template / "card.yaml").read_text()) or {}
        except (OSError, yaml.YAMLError):
            return {}
        if not isinstance(manifest, dict):
            return {}
        mode, access = manifest.get("repo_access_mode"), manifest.get("repo_all_access", "read")
        return {"repo_access_mode": mode, "repo_all_access": access} if mode in ("own", "all") and access in ("read", "write") else {}

    def create_bot(self, c, who, body):
        self.validate_template(body.template)
        if body.template and "shared" not in body.model_fields_set:
            from .onboarding import read_cards
            card = next((card for card in read_cards(self.settings) if card["template"] == body.template), {})
            body = body.model_copy(update={"shared": bool(card.get("shared"))})
        self._creator(c, who)
        privileged = who.role == "owner" or self.auth.bot_admin(who)
        pid = H.actor_id(who.actor)
        existing = H.bot(c, body.slug)
        if existing and existing["state"] == "archived" and body.slug == self.settings.assistant_bot:
            # First run set the assistant aside; adding it later brings the same bot back.
            return self._restore(c, who, body)
        if existing:
            raise Problem("duplicate", "That bot slug already exists", 409)
        if not privileged and (self.auth.system_bot(body.slug) or body.slug == "coo"):
            # The company's own bots (the Assistant, BotOps, the Librarian, the COO) are added by an owner or an Admin.
            raise Problem("forbidden", "That name is reserved for the company's own bots; ask an owner or an admin", 403)
        manager = self._default_manager(c, body)
        if not manager and not privileged and not body.reports_to and body.slug != self.settings.assistant_bot:
            manager = "human:" + pid           # a member's bot hangs under them until they say otherwise
        if manager:
            body = body.model_copy(update={"reports_to": manager})
        default = providers.load(c, self.settings) if not body.model else {}
        choice = self.models.get(body.model or default.get("model"))
        if not choice:
            raise Problem("model", "Choose a Team default in Settings > AI providers" if not body.model
                          else "Choose one of the supported models", 422)
        self.refuse_retired(choice)
        effort = self._effort(choice, body.effort or default.get("effort") or None)
        harness = self._harness(choice, getattr(body, "harness", None))
        self._parent(c, body.slug, body.reports_to)
        runner = None
        if body.runner_id and harness in EXTERNAL_HARNESSES:
            raise Problem("harness", "A bot run by an external agent has no computer; leave the "
                          "computer unassigned and give it an agent credential after saving", 422)
        if body.runner_id:
            runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (body.runner_id,)).fetchone()
            if not runner:
                raise Problem("not_found", "Computer is not registered", 404)
        parent = (self._config(c, body.reports_to)
                  if body.reports_to and not str(body.reports_to).startswith("human:") else None)
        note = None
        if privileged:
            operator = body.operator or (runner["operator"] if runner else None) or (
                parent["operator"] if parent else pid)
        else:
            if body.operator and body.operator != pid:
                raise Problem("forbidden", "You may add bots only under your own name", 403)
            operator = pid
            if parent and not self.auth.bot_manager(c, who, body.reports_to):
                raise Problem("forbidden", "You may put a bot only under a bot you manage, or under yourself", 403)
            if runner and not runner["accepts_member_bots"] and runner["operator"] != pid:
                # A member's bot goes on their own computer, or one an admin has opened to members' bots.
                runner, note = None, ("That computer does not take bots members create, so " + body.display_name
                                      + " is registered but not placed. Ask an admin to place it, or to let that "
                                        "computer accept members' bots (Settings > Computers)")
        if not H.human(c, operator):
            raise Problem("not_found", "Computer operator is not on the roster", 404)
        owners, _ = self._people(c, body.owners or [operator])
        if runner and runner["operator"] != operator and privileged:
            raise Problem("operator", "The selected machine belongs to a different operator", 422)
        if note:
            body = body.model_copy(update={"status": "planned"})
        if body.slug == self.settings.assistant_bot and body.thread_mode != "personal":
            raise Problem("thread_mode", self.settings.assistant_name + " must use personal rooms", 422)
        repo = body.repo or ("bot-" + body.slug)
        config = {"name": body.slug, "display_name": body.display_name,
                  "description": body.description, "reports_to": body.reports_to,
                  "status": body.status, "repo": repo, "host": "keeper", "tasks": "hub",
                  "runtime": runtime_of(harness) or choice["runtime"], "model": choice["id"],
                  "harness": harness, "reasoning_effort": effort, "thread_mode": body.thread_mode, "shared": body.shared,
                  "private_tasks_default": (body.private_tasks_default if "private_tasks_default" in body.model_fields_set
                                            else body.template == "general-counsel"),
                  "model_managed_by": "cloud"}
        if body.template:
            config["template"] = body.template
            if privileged:
                config.update(self.template_repo_defaults(body.template))
        team = self._team(c, body.slug, config)
        now = H.now()
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES(?,?,?,?,?,'','keeper',?,?)",
                  (body.slug, body.display_name, runtime_of(harness) or choice["runtime"],
                   choice["id"], effort, body.status, now))
        c.execute(
            "INSERT INTO bot_config(bot,config_json,team,operator,owner_ids_json,description,reports_to,"
            "repo,thread_mode,definition_updated,definition_updated_by,bot_owners_json,created_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (body.slug, encode(config), team, operator, encode(owners), body.description,
             body.reports_to, repo, body.thread_mode, now, who.actor, encode([pid]), who.actor))
        assignment = None
        if runner:
            assignment = self.execution.assign(c, who, body.slug, SimpleNamespace(
                runner_id=runner["id"], expected_generation=0))
        elif body.status == "active" and harness not in EXTERNAL_HARNESSES:
            if placement.auto_place(c, self.execution, body.slug, who.actor):
                assignment = dict(c.execute("SELECT * FROM assignments WHERE bot=?", (body.slug,)).fetchone())
            elif not note:
                note = ("No computer takes " + body.display_name + " yet, so it is on but not running. Add a computer, or ask an "
                        "admin to open one to members' bots (Settings > Computers); it starts on its own when one can take it")
        H.event(c, who.actor, "bot.definition_created", body.slug,
                {"operator": operator, "owners": owners, "runner": body.runner_id})
        visible = self.auth.bot_accesses(c, who)
        same_name = [r["slug"] for r in c.execute(
            "SELECT slug FROM bots WHERE display_name=? COLLATE NOCASE AND slug<>? AND state<>'archived' ORDER BY slug",
            (body.display_name, body.slug)) if (visible.get(r["slug"]) or {}).get("see")]
        return {**self.definition(c, body.slug), "owners": owners, "assignment": assignment,
                "bot_owners": [pid], **({"note": note} if note else {}),
                **({"name_hint": "Also named " + body.display_name + ": " + ", ".join(same_name),
                    "matching_slugs": same_name} if same_name else {})}

    def register(self, c, who, body):
        """Register a bot with the server, planned, as `who`: the record BotOps then builds the repository for.
        Idempotent for the bot's own owners; a slug someone else holds is a 409. The model is the company's
        default until the bot's owner picks one."""
        self.validate_template(body.template)
        existing = H.bot(c, body.slug)
        if existing:
            if existing["state"] != "archived" and self.auth.bot_manager(c, who, body.slug):
                return {**self.definition(c, body.slug), "created": False,
                        "bot_owners": BA.owner_ids(self._config(c, body.slug)["bot_owners_json"])}
            raise Problem("duplicate", "That bot slug already exists" + (
                " and is archived; choose a fresh slug for a new bot. Restore only when asked"
                if existing["state"] == "archived" else ""), 409)
        company = providers.load(c, self.settings)
        # `hermes` is the model a bot run by a Hermes profile takes (the profile's own); the record then has a
        # credential instead of a computer (docs/hermes-agents.md).
        named = str(body.model or "").strip().lower()
        wanted = ("hermes-profile" if named in ("hermes", "hermes-profile")
                  else "openclaw-own" if named in ("openclaw", "openclaw-own") else body.model)
        choice = self.models.get(wanted or company.get("model")) or next(
            (m for m in self.models.values() if not m.get("deprecated")), None)
        if not choice:
            raise Problem("model", "Choose the company's AI provider first (Settings > Providers)", 422)
        payload = M.BotDefinitionCreate(
            slug=body.slug, display_name=body.display_name or body.slug.replace("-", " ").title(),
            description=body.description, reports_to=body.reports_to or who.actor, status="planned", repo="",
            model=choice["id"], effort=self._effort(choice, None), harness=None, owners=[], template=body.template)
        result = self.create_bot(c, who, payload)
        if body.template:
            row = self._config(c, body.slug)
            declared = _json(row["config_json"], {}) or {}
            declared["template"] = body.template
            from .onboarding import read_cards
            card = next((card for card in read_cards(self.settings) if card["template"] == body.template), {})
            declared["shared"] = bool(card.get("shared"))
            if card.get("session"):
                declared["session"] = card["session"]
            c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (encode(declared), body.slug))
            MessageBots.link(c, who.actor, body.slug, who.actor)      # BotOps built it for them: it is their message bot
        return {**result, **self.definition(c, body.slug), "created": True}

    def co_owners(self, c, who, bot, add=(), remove=()):
        """Add or remove co-owners of a bot. Any of its owners may; whoever it reports up to and the Admins
        stay owners whatever this list says."""
        self._manager(c, who, bot)
        config = self._config(c, bot)
        before = BA.owner_ids(config["bot_owners_json"])
        for pid in add:
            self._people(c, [pid])
        after = [p for p in dict.fromkeys([*before, *add]) if p not in set(remove)]
        if after == before:
            raise Problem("unchanged", "That is already who owns it", 409)
        c.execute("UPDATE bot_config SET bot_owners_json=?,revision=revision+1 WHERE bot=?", (encode(after), bot))
        self.record(c, who.actor, bot, "co_owners", before, after)
        H.event(c, who.actor, "bot.co_owners_changed", bot, {"before": before, "after": after})
        return {"bot": bot, "bot_owners": self.owner_rows(c, bot), "revision": config["revision"] + 1}

    def owner_rows(self, c, bot, config=None, roster=None):
        """`config` (its bot_config row) and `roster` when the caller has read them already."""
        config = config or self._config(c, bot)
        ids = list(dict.fromkeys([*BA.owner_ids(config["bot_owners_json"]), config["operator"]]))
        roster = roster if roster is not None else self._roster(c)
        return [{"id": i, "name": (P.person(i, roster) or {}).get("name") or i} for i in ids if i]

    def _default_manager(self, c, body):
        """Who a bot with no manager reports to when the company has no assistant to head the
        chart: the owner, so the org chart stays one tree under a person."""
        if body.reports_to or body.slug == self.settings.assistant_bot:
            return None
        assistant = H.bot(c, self.settings.assistant_bot)
        if assistant and assistant["state"] != "archived":
            return None
        owner = self.auth.owner_id(c)
        return "human:" + owner if owner and H.human(c, owner) else None

    def _restore(self, c, who, body):
        """An archived assistant, planned again under the name and description just given. It
        keeps its history; its computer is assigned the way any planned bot's is."""
        self.update_bot(c, who, body.slug, M.BotDefinitionUpdate(
            display_name=body.display_name, description=body.description, status="planned",
            expected_revision=self._config(c, body.slug)["revision"]))
        return {**self.definition(c, body.slug), "owners": self._people(c, body.owners)[0], "assignment": None}

    def archive(self, c, who, bot, body):
        # The assistant, BotOps, the Librarian and the Goal Manager are built in: nobody archives them, the owner included.
        if bot in (self.settings.assistant_bot, "botops", "librarian", "goal-manager"):
            raise Problem("system_bot", (self.settings.assistant_name if bot == self.settings.assistant_bot
                                         else "BotOps" if bot == "botops" else "The Librarian" if bot == "librarian"
                                         else "The Goal Manager")
                          + " is built in to every team and cannot be archived or deleted; you can pause or rename it", 409)
        self._manager(c, who, bot)
        if self._config(c, bot)["revision"] != body.expected_revision:
            raise Problem("version_conflict", "Bot configuration changed; refresh before saving", 409)
        if body.successor:
            self._manager(c, who, body.successor)
        return archive_bot(c, who.actor, bot, body.successor or "", revoke_agent=getattr(body, "revoke_agent", True))

    def restore(self, c, who, bot):
        """Bring an archived bot back: to the status it had when it was archived (active, paused or planned),
        else planned. Its routines and computer are not restored; an active bot is placed the way any is."""
        row = H.bot(c, bot)
        if not row:
            raise Problem("not_found", "Bot not found", 404)
        self._manager(c, who, bot)
        if row["state"] != "archived":
            raise Problem("not_archived", "This bot is not archived", 409)
        previous = c.execute("SELECT detail_json FROM events WHERE action='bot.archived' AND target=? "
                             "ORDER BY ts DESC LIMIT 1", (bot,)).fetchone()
        status = (_json(previous["detail_json"], {}) or {}).get("previous") if previous else None
        status = status if status in ("active", "paused", "planned") else "planned"
        self.update_bot(c, who, bot, M.BotDefinitionUpdate(status=status, expected_revision=self._config(c, bot)["revision"]))
        shared_bots.cascade_restore(c, who.actor, bot)
        H.event(c, who.actor, "bot.restored", bot, {"status": status})
        result = {"bot": bot, "status": status, "restored": True}
        from .agents import external_harness, row as agent_row
        if external_harness(c, bot):
            record = agent_row(c, bot)
            result["agent"] = {"harness": external_harness(c, bot), "credential": bool(record and not record["revoked_at"])}
        return result

    def computer_builds_repository(self, c, bot):
        from .onboarding import read_cards
        declared = _json(self._config(c, bot)["config_json"], {}) or {}
        return bool(declared.get("materialize") or any(
            card["template"] == declared.get("template") and card.get("bootstrap")
            for card in read_cards(self.settings)))

    def ensure_activation(self, c, who, bot):
        previous = H.bot(c, bot)["state"]
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (bot,))
        try:
            placement.auto_place(c, self.execution, bot, who.actor)
        finally:
            c.execute("UPDATE bots SET state=? WHERE slug=?", (previous, bot))
        # Branches clone an existing original repository; they never build a separate one.
        branch = shared_bots.source_of(shared_bots.declared(c, bot))
        if not branch and not repository_present(c, bot) and not self.computer_builds_repository(c, bot):
            raise Problem("repository_missing", "Its repository is not built yet. Ask BotOps to build it", 409)

    def update_bot(self, c, who, bot, body):
        self._manager(c, who, bot, botops=True)
        source = shared_bots.source_of(shared_bots.declared(c, bot))
        if source and (H.bot(c, source) or {}).get("state") == "archived":
            raise Problem("original_archived", "Restore the original before changing its branch's status", 409)
        # A branch follows its original's behaviour and controls its own reporting line.
        if body.model_fields_set - {"expected_revision", "on_behalf_of", "status", "reports_to"}:
            shared_bots.refuse_copy(c, bot)
        if "template" in body.model_fields_set:
            self.validate_template(body.template)
        config = self._config(c, bot)
        if config["revision"] != body.expected_revision:
            raise Problem("version_conflict", "Bot configuration changed; refresh before saving", 409)
        before = self.definition(c, bot)
        values = dict(before)
        for field in body.model_fields_set - {"expected_revision", "on_behalf_of"}:
            values[field] = getattr(body, field)
        values["description"] = values.get("description") or ""
        # Lifting a quarantine has its own route and rules (status_set, quarantine/clear).
        if before.get("status") == "quarantined" and values.get("status") != "quarantined":
            raise Problem("quarantined", "This bot is quarantined; clear the quarantine first", 409)
        self._parent(c, bot, values.get("reports_to"))
        if who.role != "owner" and values.get("reports_to") and not str(values["reports_to"]).startswith("human:"):
            parent = self._config(c, values["reports_to"])
            if parent["operator"] != H.actor_id(who.actor) and not self.auth.manages(c, who, "bot", values["reports_to"]):
                raise Problem("forbidden", "The parent bot belongs to a different operator", 403)
        if bot == self.settings.assistant_bot and values["thread_mode"] != "personal":
            raise Problem("thread_mode", self.settings.assistant_name + " must use personal rooms", 422)
        mode_changed = values["thread_mode"] != before["thread_mode"]
        if mode_changed and c.execute(
                "SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')", (bot,)).fetchone():
            raise Problem("busy", "Wait for the current bot turn before changing its room type", 409)
        declared = _json(config["config_json"], {}) or {}
        if values["status"] == "active" and before.get("status") != "active":
            self.ensure_activation(c, who, bot)
        if "session" in body.model_fields_set:
            declared["session"] = body.session or "bot"
        if "shared" in body.model_fields_set:
            declared["shared"] = bool(body.shared)
        if "template" in body.model_fields_set:
            declared["template"] = body.template or ""
        declared.update({"name": bot, "display_name": values["display_name"],
                         "description": values["description"], "reports_to": values.get("reports_to"),
                         "bot_contact": values.get("bot_contact") or "open",
                         "private_tasks_default": values.get("private_tasks_default") is True,
                         "status": values["status"], "repo": values["repo"],
                         "thread_mode": values["thread_mode"], "temp": bool(values.get("temp"))})
        c.execute("UPDATE bots SET display_name=?,state=? WHERE slug=?",
                  (values["display_name"], values["status"], bot))
        c.execute(
            "UPDATE bot_config SET config_json=?,description=?,reports_to=?,repo=?,thread_mode=?,"
            "definition_updated=?,definition_updated_by=?,revision=revision+1 WHERE bot=?",
            (encode(declared), values["description"], values.get("reports_to"), values["repo"],
             values["thread_mode"], H.now(), who.actor, bot))
        entries = self._entries(c)
        roster = self._roster(c)
        for slug in entries:
            c.execute("UPDATE bot_config SET team=? WHERE bot=?", (P.team_of(slug, entries, roster), slug))
        after = self.definition(c, bot)
        self.record(c, who.actor, bot, "definition", before, after)
        H.event(c, who.actor, "bot.definition_changed", bot,
                {"before": before, "after": after})
        return {**after, "previous_thread_mode": before["thread_mode"]}

    def snapshot(self, c, bot, field):
        config = self._config(c, bot)
        if field == "owners":
            return _json(config["owner_ids_json"], []) if config["owner_ids_json"] is not None else None
        if field == "access":
            return BA.document(config["access_json"])
        if field == "co_owners":
            return BA.owner_ids(config["bot_owners_json"])
        if field == "model":
            declared, row = _json(config["config_json"], {}), H.bot(c, bot)
            runtime = declared.get("runtime") or row.get("runtime") or ""
            return {"model": declared.get("model") or row.get("model") or "",
                    "runtime": runtime, "harness": resolve_harness(declared, runtime),
                    "effort": declared.get("reasoning_effort") or row.get("effort") or ""}
        if field == "fallback":
            return normalize_fallback((_json(config["config_json"], {}) or {}).get("fallback"))
        if field == "placement":
            row = c.execute("SELECT a.runner_id,a.generation,r.label,r.operator AS runner_operator "
                            "FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=?", (bot,)).fetchone()
            return {"runner_id": row["runner_id"] if row else None,
                    "generation": row["generation"] if row else 0,
                    "operator": config["operator"], "label": row["label"] if row else ""}
        if field == "definition":
            return self.definition(c, bot)
        raise ValueError(field)

    def record(self, c, actor, bot, field, before, after, transition_id=None):
        if before == after:
            return None
        change_id = H.new_id()
        # `via` says who really made the change for `actor`: BotOps, on their request, or the Assistant.
        c.execute("INSERT INTO settings_changes(id,bot,field,before_json,after_json,actor,transition_id,created,via) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (change_id, bot, field, encode(before), encode(after), actor, transition_id, H.now(),
                   H.VIA.get() or None))
        return change_id

    @staticmethod
    def execution_assignment(c, bot):
        row = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
        return row["runner_id"] if row else ""

    def _target(self, c, who, bot, body):
        if body.kind == "model":
            choice = self.models.get(body.model)
            if not choice:
                raise Problem("model", "Choose one of the supported models", 422)
            self.refuse_retired(choice)
            current = self.snapshot(c, bot, "model")
            effort = self._effort(choice, getattr(body, "effort", None), current.get("effort"))
            harness = self._harness(choice, getattr(body, "harness", None), current.get("harness"))
            runtime_name = runtime_of(harness) or choice["runtime"]
            # This bot's own sign-in on its computer (backend/readiness.py), never another bot's.
            can = can_run(c, bot, runtime_name, harness=harness, model=choice["id"])
            if can["can_run"] is False:
                raise Problem("runner_not_ready", can["problem"], 409, extra={"fix": can["fix"], "link": can["link"]})
            return {"model": choice["id"], "runtime": runtime_name, "harness": harness,
                    "effort": effort}
        runner = c.execute("SELECT id,label,operator,revoked_at,last_seen,readiness_json FROM runners WHERE id=?",
                           (body.runner_id,)).fetchone()
        if not runner or runner["revoked_at"]:
            raise Problem("not_found", "Computer is not registered", 404)
        if who.role != "owner" and runner["operator"] != H.actor_id(who.actor):
            raise Problem("forbidden", "Bot administrators may use only their own registered computers", 403)
        if not runner["last_seen"] or runner["last_seen"] <= H.shift(H.now(), seconds=-60):
            raise Problem("runner_not_ready", "The destination computer is offline", 409)
        # A runner reports only on bots already assigned to it, so a bot's first placement has
        # nothing to be ready yet: assigning it is what makes the runner set its repository up.
        if c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone():
            readiness = readiness_document(runner["readiness_json"])
            if readiness.get("schema_version") != 1:
                raise Problem("runner_not_ready", "Update the destination runner before moving this bot", 409)
            # The runner reports what the bot runs on, not what is stored: a bot on the company
            # default stores neither.
            desired = providers.fill(providers.load(c, self.settings),
                                     _json(self._config(c, bot)["config_json"], {}) or {})
            detail = readiness.get("bots", {}).get(bot)
            if not isinstance(detail, dict):
                # A computer that does not host the bot has no report on it, so it is judged by whether it can
                # run the bot's runtime. The move is what makes its runner clone the repository from GitHub,
                # which is refused when GitHub does not hold that history.
                if not readiness.get("runtimes"):
                    raise Problem("runner_not_ready", "Update the destination runner before moving this bot", 409)
                can = can_run(c, bot, str(desired.get("runtime") or ""), runner=runner["id"],
                              harness=str(desired.get("harness") or ""), model=str(desired.get("model") or ""))
                if can["can_run"] is False:
                    raise Problem("runner_not_ready", can["problem"], 409, extra={"fix": can["fix"], "link": can["link"]})
                source = self.execution_assignment(c, bot)
                blocked = stranded(c, self.store.settings, bot, source, runner["id"]) if source else ""
                if blocked:
                    raise Problem("repository_unpublished", blocked, 409)
                return {"runner_id": runner["id"], "label": runner["label"], "operator": runner["operator"]}
            if (str(detail.get("runtime") or "") != str(desired.get("runtime") or "")
                    or str(detail.get("model") or "") != str(desired.get("model") or "")):
                raise Problem("runner_not_ready",
                              "The destination configuration differs from the server; wait for its next heartbeat", 409)
            if detail.get("ready") is not True:
                # A destination with no checkout has nothing to report ready until it is assigned: assigning is what
                # makes its runner clone the repository. That is fine when GitHub holds the history, and a
                # refusal that names the cause when it does not.
                source = self.execution_assignment(c, bot)
                other = [p for p in detail.get("problems") or [] if p != "Missing bot repository or AGENT.md"]
                if source and detail.get("repository_present") is False and not other:
                    blocked = stranded(c, self.store.settings, bot, source, runner["id"])
                    if blocked:
                        raise Problem("repository_unpublished", blocked, 409)
                    if (_reported(c, source, bot)[1]).get("published") is True:
                        return {"runner_id": runner["id"], "label": runner["label"], "operator": runner["operator"]}
                raise Problem("runner_not_ready", (detail.get("problems") or
                              ["The destination computer is not ready for this bot"])[0], 409)
        return {"runner_id": runner["id"], "label": runner["label"], "operator": runner["operator"]}

    def begin(self, c, who, bot, body, undo_change_id=None):
        self._manager(c, who, bot)
        if body.kind == "model":
            shared_bots.refuse_copy(c, bot)
        config = self._config(c, bot)
        if config["revision"] != body.expected_revision:
            raise Problem("version_conflict", "Bot configuration changed; refresh before preparing the change", 409)
        target = self._target(c, who, bot, body)
        field = "model" if body.kind == "model" else "placement"
        current = self.snapshot(c, bot, field)
        unchanged = (field == "model" and current == target) or (
            field == "placement" and current["runner_id"] == target["runner_id"])
        if unchanged:
            raise Problem("unchanged", "That setting is already selected", 409)
        existing = c.execute("SELECT id FROM bot_transitions WHERE bot=? AND state IN ('preparing','blocked','prepared')",
                             (bot,)).fetchone()
        if existing:
            raise Problem("transition_pending", "Finish or cancel the existing prepared change first", 409)
        if field == "placement" and current["generation"] != body.expected_generation:
            raise Problem("version_conflict", "Machine assignment changed; refresh before preparing the move", 409)

        transition_id, now = H.new_id(), H.now()
        c.execute("INSERT INTO bot_transitions(id,bot,kind,target_json,requested_by,expected_revision,"
                  "expected_generation,state,undo_change_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (transition_id, bot, body.kind, encode(target), who.actor, body.expected_revision,
                   body.expected_generation, "preparing", undo_change_id, now, now))

        # The change applies at once. The bot's session is the bot's runtime's business: the
        # hub asks for no checkpoint turn and rebuilds nothing; a bot that wants what was said
        # before reads it with `hub conversation show`.
        c.execute("UPDATE bot_transitions SET state='prepared',updated=? WHERE id=?", (H.now(), transition_id))
        self._apply(c, who, c.execute("SELECT * FROM bot_transitions WHERE id=?", (transition_id,)).fetchone())
        return self.get(c, who, transition_id)

    def _apply(self, c, who, transition, *, without_checkpoint=False):
        self._manager(c, who, transition["bot"])
        config = self._config(c, transition["bot"])
        if config["revision"] != transition["expected_revision"]:
            raise Problem("version_conflict", "Bot settings changed while the checkpoint was being prepared", 409)
        self.execution.expire(c)
        if c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')", (transition["bot"],)).fetchone():
            raise Problem("busy", "Wait for the current bot turn to finish", 409)
        target, bot, field = _json(transition["target_json"], {}), transition["bot"], transition["kind"]
        field = "model" if field == "model" else "placement"
        before = self.snapshot(c, bot, field)
        c.execute("SAVEPOINT apply_bot_transition")
        try:
            if field == "model":
                declared = _json(config["config_json"], {})
                # A Settings choice is the live authority. The checked-in manifest remains
                # the bootstrap default, but must not make the runner reject an intentional
                # model change on its next heartbeat.
                declared.update({"model": target["model"], "runtime": target["runtime"],
                                 "harness": target.get("harness") or runtime_of(target["runtime"]) or target["runtime"],
                                 "reasoning_effort": target["effort"],
                                 "model_managed_by": "cloud"})
                c.execute("UPDATE bot_config SET config_json=?,revision=revision+1 WHERE bot=?", (encode(declared), bot))
                c.execute("UPDATE bots SET model=?,runtime=?,effort=? WHERE slug=?",
                          (target["model"], target["runtime"], target["effort"], bot))
            else:
                runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (target["runner_id"],)).fetchone()
                if not runner:
                    raise Problem("not_found", "The destination computer is no longer registered", 409)
                current = c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone()
                generation = current["generation"] if current else 0
                if generation != transition["expected_generation"]:
                    raise Problem("version_conflict", "Machine assignment changed while the checkpoint was being prepared", 409)
                c.execute("UPDATE bot_config SET operator=? WHERE bot=?", (runner["operator"], bot))
                self.execution.assign(c, who, bot, SimpleNamespace(
                    runner_id=runner["id"], expected_generation=generation))
                c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))
            reset = self.reset_sessions(c, bot)
            after = self.snapshot(c, bot, field)
            self.record(c, who.actor, bot, field, before, after, transition["id"])
            if transition["undo_change_id"]:
                c.execute("UPDATE settings_changes SET undone_by=?,undone_at=? WHERE id=? AND undone_at IS NULL",
                          (who.actor, H.now(), transition["undo_change_id"]))
            now = H.now()
            c.execute("UPDATE bot_transitions SET state='applied',error=NULL,without_checkpoint=?,"
                      "updated=?,applied_at=? WHERE id=?",
                      (int(without_checkpoint), now, now, transition["id"]))
            H.event(c, who.actor, "bot.transition_applied", bot,
                    {"transition": transition["id"], "kind": transition["kind"],
                     "checkpointed": not without_checkpoint, "sessions_reset": reset})
            c.execute("RELEASE apply_bot_transition")
        except Exception:
            c.execute("ROLLBACK TO apply_bot_transition")
            c.execute("RELEASE apply_bot_transition")
            raise

    def get(self, c, who, transition_id):
        transition = c.execute("SELECT * FROM bot_transitions WHERE id=?", (transition_id,)).fetchone()
        if not transition:
            raise Problem("not_found", "Prepared change not found", 404)
        self._manager(c, who, transition["bot"])
        checkpoints = []
        for row in c.execute("SELECT * FROM bot_transition_checkpoints WHERE transition_id=? ORDER BY conversation_id",
                             (transition_id,)):
            checkpoints.append({"conversation_id": row["conversation_id"], "state": row["state"],
                                "checkpoint": _json(row["checkpoint_json"]), "error": row["error"]})
        value = dict(transition)
        value["target"] = _json(value.pop("target_json"), {})
        value["checkpoints"] = checkpoints
        value["progress"] = {"prepared": sum(row["state"] == "prepared" for row in checkpoints),
                             "total": len(checkpoints)}
        if value["state"] == "applied":
            # What the bot runs on now, checked on the computer it is on now (a model change or a move).
            effective = providers.fill(providers.load(c, self.settings),
                                       _json(self._config(c, transition["bot"])["config_json"], {}) or {})
            value["readiness"] = absolute(can_run(c, transition["bot"], effective.get("runtime") or "",
                                                  harness=effective.get("harness") or "",
                                                  model=effective.get("model") or ""), self.settings.public_url)
        return value

    def force(self, c, who, transition_id):
        transition = c.execute("SELECT * FROM bot_transitions WHERE id=?", (transition_id,)).fetchone()
        if transition:
            self._manager(c, who, transition["bot"])
        if not transition or transition["state"] in ("applied", "cancelled"):
            raise Problem("state", "This prepared change is no longer pending", 409)
        active = c.execute("SELECT 1 FROM bot_transition_checkpoints p JOIN jobs j ON j.message_id=p.message_id "
                           "WHERE p.transition_id=? AND j.state IN ('leased','running','input')", (transition_id,)).fetchone()
        if active:
            raise Problem("busy", "A checkpoint is currently running; wait for it to finish", 409)
        c.execute("UPDATE jobs SET state='cancelled' WHERE message_id IN "
                  "(SELECT message_id FROM bot_transition_checkpoints WHERE transition_id=?) AND state='queued'", (transition_id,))
        self._apply(c, who, transition, without_checkpoint=True)
        return self.get(c, who, transition_id)

    def cancel(self, c, who, transition_id):
        transition = c.execute("SELECT * FROM bot_transitions WHERE id=?", (transition_id,)).fetchone()
        if transition:
            self._manager(c, who, transition["bot"])
        if not transition or transition["state"] in ("applied", "cancelled"):
            raise Problem("state", "This prepared change is no longer pending", 409)
        if c.execute("SELECT 1 FROM bot_transition_checkpoints p JOIN jobs j ON j.message_id=p.message_id "
                     "WHERE p.transition_id=? AND j.state IN ('leased','running','input')", (transition_id,)).fetchone():
            raise Problem("busy", "A checkpoint is currently running; wait for it to finish", 409)
        c.execute("UPDATE jobs SET state='cancelled' WHERE message_id IN "
                  "(SELECT message_id FROM bot_transition_checkpoints WHERE transition_id=?) AND state='queued'", (transition_id,))
        c.execute("UPDATE bot_transition_checkpoints SET state='cancelled' WHERE transition_id=? AND state='queued'",
                  (transition_id,))
        c.execute("UPDATE bot_transitions SET state='cancelled',updated=? WHERE id=?", (H.now(), transition_id))
        H.event(c, who.actor, "bot.transition_cancelled", transition["bot"], {"transition": transition_id})
        return self.get(c, who, transition_id)

    def history(self, c, who, limit=100):
        self._owner(who)
        limit = max(1, min(int(limit), 200))
        changes = []
        for row in c.execute("SELECT * FROM settings_changes ORDER BY created DESC LIMIT ?", (limit,)):
            value = dict(row)
            value["before"], value["after"] = _json(value.pop("before_json")), _json(value.pop("after_json"))
            try:
                current = self.snapshot(c, row["bot"], row["field"])
            except (ValueError, Problem, TypeError, KeyError):
                # A field an older release wrote, or a bot deleted since: history still shows it.
                current = None
            value["can_undo"] = (row["field"] in ("owners", "co_owners", "model", "placement", "fallback", "access")
                                 and not row["undone_at"] and current == value["after"])
            changes.append(value)
        transitions = [self.get(c, who, row[0]) for row in c.execute(
            "SELECT id FROM bot_transitions ORDER BY created DESC LIMIT ?", (limit,))]
        return {"changes": changes, "transitions": transitions}

    def undo(self, c, who, change_id, body):
        self._owner(who)
        change = c.execute("SELECT * FROM settings_changes WHERE id=?", (change_id,)).fetchone()
        if not change:
            raise Problem("not_found", "Settings change not found", 404)
        config = self._config(c, change["bot"])
        if config["revision"] != body.expected_revision:
            raise Problem("version_conflict", "Bot settings changed; refresh before undoing", 409)
        before, after = _json(change["before_json"]), _json(change["after_json"])
        if change["undone_at"] or self.snapshot(c, change["bot"], change["field"]) != after:
            raise Problem("not_latest", "A newer change replaced this value, so it cannot be undone directly", 409)
        self.execution.expire(c)
        if change["field"] in ("model", "placement") and c.execute(
                "SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')", (change["bot"],)).fetchone():
            raise Problem("busy", "Wait for the current bot turn to finish before undoing", 409)
        bot = change["bot"]
        if change["field"] == "owners":
            owner_json = None if before is None else encode(before)
            c.execute("UPDATE bot_config SET owner_ids_json=?,revision=revision+1 WHERE bot=?", (owner_json, bot))
            restored = self.snapshot(c, bot, "owners")
            self.record(c, who.actor, bot, "owners", after, restored)
            c.execute("UPDATE settings_changes SET undone_by=?,undone_at=? WHERE id=?", (who.actor, H.now(), change_id))
            H.event(c, who.actor, "settings.undo", bot, {"change": change_id, "field": change["field"]})
            return {"undone": True, "bot": bot, "field": change["field"],
                    "revision": config["revision"] + 1}
        if change["field"] == "co_owners":
            c.execute("UPDATE bot_config SET bot_owners_json=?,revision=revision+1 WHERE bot=?", (encode(before), bot))
            self.record(c, who.actor, bot, "co_owners", after, before)
            c.execute("UPDATE settings_changes SET undone_by=?,undone_at=? WHERE id=?", (who.actor, H.now(), change_id))
            H.event(c, who.actor, "settings.undo", bot, {"change": change_id, "field": change["field"]})
            return {"undone": True, "bot": bot, "field": change["field"], "revision": config["revision"] + 1}
        if change["field"] == "access":
            c.execute("UPDATE bot_config SET access_json=?,revision=revision+1 WHERE bot=?",
                      (BA.stored(BA.document(before)), bot))
            restored = self.snapshot(c, bot, "access")
            self.record(c, who.actor, bot, "access", after, restored)
            c.execute("UPDATE settings_changes SET undone_by=?,undone_at=? WHERE id=?", (who.actor, H.now(), change_id))
            H.event(c, who.actor, "settings.undo", bot, {"change": change_id, "field": change["field"]})
            return {"undone": True, "bot": bot, "field": change["field"], "revision": config["revision"] + 1}
        if change["field"] == "model":
            if before.get("model") not in self.models:
                raise Problem("not_reversible", "The previous model is no longer in the supported model list", 409)
            return self.begin(c, who, bot, SimpleNamespace(
                kind="model", model=before["model"], effort=before.get("effort"),
                harness=before.get("harness"), runner_id=None,
                expected_revision=body.expected_revision, expected_generation=0), undo_change_id=change_id)
        if change["field"] == "placement":
            if not before.get("runner_id"):
                raise Problem("not_reversible", "There was no previous computer to restore", 409)
            runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (before["runner_id"],)).fetchone()
            if not runner:
                raise Problem("not_reversible", "The previous computer is no longer registered", 409)
            current = c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone()
            return self.begin(c, who, bot, SimpleNamespace(
                kind="machine", model=None, harness=None, runner_id=runner["id"],
                expected_revision=body.expected_revision,
                expected_generation=current["generation"] if current else 0), undo_change_id=change_id)
        if change["field"] == "fallback":
            return self.set_fallback(c, who, bot, SimpleNamespace(
                fallback=SimpleNamespace(**before) if before else None,
                expected_revision=body.expected_revision), undo_change_id=change_id)
        raise Problem("not_reversible", "This settings change cannot be undone", 409)

    def team_names(self, c):
        """{group id: name}: the groups a person or bot can be in, and so the ones an access list can name."""
        roster = self._roster(c)
        names = {tid: tid.replace("-", " ").title() for tid in roster.get("teams") or {}}
        for gid, group in (roster.get("org_groups") or {}).items():
            names[gid] = group.get("name") or gid
        for person in roster.get("people") or []:
            if person.get("team"):
                names.setdefault(person["team"], person["team"].replace("-", " ").title())
        return names

    def access(self, c, who, bot):
        """Who may see, read and write to this bot, for the people who manage it."""
        self._manager(c, who, bot)
        config = self._config(c, bot)
        return {"bot": bot, **BA.document(config["access_json"]), "revision": config["revision"],
                "you": self.auth.bot_access(c, who, bot),
                "teams": [{"id": tid, "name": name} for tid, name in sorted(self.team_names(c).items())]}

    def set_access(self, c, who, bot, body):
        self._manager(c, who, bot)
        config = self._config(c, bot)
        if config["revision"] != body.revision:
            raise Problem("version_conflict", "Bot configuration changed; refresh before saving access", 409)
        roster = self._roster(c)
        people = {p["id"] for p in roster.get("people") or []} | {h["id"] for h in H.humans(c)}
        doc = BA.parse(body.model_dump(exclude={"revision"}), people, set(self.team_names(c)),
                       {row["slug"] for row in H.bots(c)})
        before = self.snapshot(c, bot, "access")
        if before == doc:
            raise Problem("unchanged", "That is already who has access", 409)
        c.execute("UPDATE bot_config SET access_json=?,revision=revision+1 WHERE bot=?", (BA.stored(doc), bot))
        if rooms.thread_mode(c, bot) == rooms.SHARED:
            rooms.sync_shared_room(c, self.auth, bot)
        self.record(c, who.actor, bot, "access", before, doc)
        H.event(c, who.actor, "bot.access_changed", bot, {"before": BA.summary(before), "after": BA.summary(doc)})
        if who.role == "owner":
            from . import access as owner_access
            owner_access.clear_bot_access_notice(c)
        return {"bot": bot, **doc, "revision": config["revision"] + 1}

    def set_fallback(self, c, who, bot, body, undo_change_id=None):
        self._manager(c, who, bot)
        shared_bots.refuse_copy(c, bot)
        config = self._config(c, bot)
        if config["revision"] != body.expected_revision:
            raise Problem("version_conflict", "Bot configuration changed; refresh before saving", 409)
        choice_body = body.fallback
        if choice_body is None:
            target = None
        else:
            if isinstance(choice_body, dict):
                model_id, harness_id = choice_body.get("model"), choice_body.get("harness")
                effort_id = choice_body.get("effort") or choice_body.get("reasoning_effort")
            else:
                model_id, harness_id = choice_body.model, choice_body.harness
                effort_id = getattr(choice_body, "effort", None) or getattr(choice_body, "reasoning_effort", None)
            if not HARNESS_BY_ID.get(harness_id):
                raise Problem("harness", "Choose one of the supported harnesses", 422)
            choice = self.models.get(model_id)
            if not choice:
                raise Problem("model", "Choose one of the supported models", 422)
            self.refuse_retired(choice)
            harness = self._harness(choice, harness_id)
            effort = self._effort(choice, effort_id)
            target = {"harness": harness, "model": choice["id"], "reasoning_effort": effort,
                      "runtime": runtime_of(harness)}
        before = self.snapshot(c, bot, "fallback")
        if before == target:
            raise Problem("unchanged", "That fallback is already selected", 409)
        declared = _json(config["config_json"], {}) or {}
        if target is None:
            declared.pop("fallback", None)
        else:
            declared["fallback"] = target
        c.execute("UPDATE bot_config SET config_json=?,revision=revision+1 WHERE bot=?",
                  (encode(declared), bot))
        after = self.snapshot(c, bot, "fallback")
        self.record(c, who.actor, bot, "fallback", before, after)
        if undo_change_id:
            c.execute("UPDATE settings_changes SET undone_by=?,undone_at=? WHERE id=? AND undone_at IS NULL",
                      (who.actor, H.now(), undo_change_id))
        H.event(c, who.actor, "bot.fallback_changed", bot, {"before": before, "after": after})
        return {"bot": bot, "fallback": after, "revision": config["revision"] + 1}


def archive_bot(c, actor, bot, successor="", revoke_agent=True):
    """Take a bot off the org chart and out of work (#535). Its routines are deleted, queued
    turns cancelled and its computer released; open tasks it owns or requested go to `successor`
    (a bot slug) or else its operator; bots reporting to it move up to its own parent. A bot that
    roots a team hands the team to `successor`, which then reports to nobody. Nothing is
    deleted: the rows, tasks and history stay, and the repository is untouched."""
    config = c.execute("SELECT * FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if not config or not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    if successor and (successor == bot or not H.bot(c, successor)
                      or H.bot(c, successor).get("state") == "archived"):
        raise Problem("not_found", "Successor bot not found", 404)
    shared_bots.cascade_archive(c, actor, bot)
    ts, parent = H.now(), config["reports_to"]
    previous = H.bot(c, bot)["state"]
    declared = _json(config["config_json"], {}) or {}
    declared.pop("branch_previous_status", None)
    declared["status"] = "archived"
    c.execute("UPDATE bots SET state='archived' WHERE slug=?", (bot,))
    c.execute("UPDATE bot_config SET config_json=?,revision=revision+1,definition_updated=?,"
              "definition_updated_by=? WHERE bot=?", (encode(declared), ts, actor, bot))
    c.execute("DELETE FROM assignments WHERE bot=?", (bot,))
    c.execute("UPDATE jobs SET state='cancelled' WHERE bot=? AND state='queued'", (bot,))
    for row in c.execute("SELECT id FROM schedules WHERE bot=? AND deleted_at IS NULL", (bot,)).fetchall():
        c.execute("INSERT INTO schedule_config(schedule_id,enabled) VALUES(?,0) "
                  "ON CONFLICT(schedule_id) DO UPDATE SET enabled=0", (row["id"],))
    c.execute("UPDATE schedules SET deleted_at=COALESCE(deleted_at,?),updated_at=? WHERE bot=?",
              (ts, ts, bot))

    # The team it rooted passes to the successor, which now heads it.
    people = read_cache.metadata(c, "people")
    roster = _json(people["value_json"], {}) if people else {}
    rooted = [name for name, team in (roster.get("teams") or {}).items()
              if isinstance(team, dict) and team.get("root") == bot]
    if rooted and successor:
        for name in rooted:
            roster["teams"][name]["root"] = successor
        _set_parent(c, actor, successor, None, ts)
    # People whose notes or mail went to it fall back to the defaults (the COO), never to the
    # successor: a successor is someone else's bot as often as not.
    cleared = [p for p in roster.get("people") or [] if bot in (p.get("bot"), p.get("inbox_bot"))]
    for p in cleared:
        for key in ("bot", "inbox_bot"):
            if p.get(key) == bot:
                p[key] = None
    if (rooted and successor) or cleared:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(roster),))
    for row in c.execute("SELECT bot FROM bot_config WHERE reports_to=?", (bot,)).fetchall():
        if row["bot"] != successor or not rooted:
            _set_parent(c, actor, row["bot"], successor or parent, ts)

    heir = "bot:" + successor if successor else H.human_actor(config["operator"])
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    for field in ("owner", "requester"):
        for task in c.execute(f"SELECT id FROM tasks WHERE {field}=? AND status IN ({marks})",
                              ("bot:" + bot, *H.ACTIVE_STATUSES)).fetchall():
            H._task_event(c, task["id"], actor, field, "bot:" + bot, heir, "Its bot was removed")
            c.execute(f"UPDATE tasks SET {field}=?,updated=?,version=version+1 WHERE id=?",
                      (heir, ts, task["id"]))
    H._recount(c, "bot:" + bot)
    H._recount(c, heir)

    entries = {row["bot"]: SettingsAdmin._entry(row) for row in read_cache.configs(c)}
    loaded = P.load(roster or {"people": H.humans(c)})
    for slug in entries:
        c.execute("UPDATE bot_config SET team=? WHERE bot=?", (P.team_of(slug, entries, loaded), slug))
    H.event(c, actor, "bot.archived", bot, {"successor": successor or None, "heir": heir, "previous": previous})
    result = {"bot": bot, "status": "archived", "successor": successor or None}
    from .agents import external_harness, revoke_credential, row as agent_row
    harness = external_harness(c, bot)
    if harness:
        # An external agent is not stopped by archiving: it keeps its credential and keeps reporting in to a
        # bot that no longer answers. Say so, and revoke the credential unless the person chose not to.
        record = agent_row(c, bot)
        live = bool(record and not record["revoked_at"])
        revoked = bool(live and revoke_agent)
        if revoked:
            revoke_credential(c, SimpleNamespace(actor=actor), bot)
        name = (H.bot(c, bot) or {}).get("display_name") or bot
        label = {"hermes": "Hermes", "openclaw": "OpenClaw"}.get(harness, harness)
        result["agent"] = {
            "harness": harness, "stopped": True, "credential_revoked": revoked,
            "detail": (name + "'s " + label + " agent will stop" + (": its credential is revoked." if revoked else (
                ", but its credential still works and it keeps reporting in until you revoke it." if live else ".")))}
    return result


def _set_parent(c, actor, bot, parent, ts):
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    declared = _json(row["config_json"], {}) or {}
    declared["reports_to"] = parent
    c.execute("UPDATE bot_config SET reports_to=?,config_json=?,revision=revision+1,"
              "definition_updated=?,definition_updated_by=? WHERE bot=?",
              (parent, encode(declared), ts, actor, bot))
