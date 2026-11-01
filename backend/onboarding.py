"""First run: the catalog people pick bots from, and the record of what they chose.

A company starts with four built-in bots: the assistant, which is each person's private Assistant
chat (backend/assistant.py) and works in the background (Slack routing, meetings' Auto delivery),
BotOps, which builds every other bot, the Librarian, which answers questions from the company's
docs (backend/librarian.py), and the Strategy Bot, which keeps the KPIs (docs/goals-and-kpis.md). Onboarding
names the company, asks the first-run questions, and records the org chart the owner builds department by
department (the suggestions are backend/recruit.py), and on completion defines the chosen bots. A starter is
created at once, `needs_onboarding`, and its repository is materialized by the computer; every other template is
still a task for BotOps. Nothing here reaches a machine: it writes definitions and tasks.
"""

import difflib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import yaml

from clients.manifest import group_of, manifest_path, routines_of
from . import goals as G
from . import models as M
from . import providers
from . import beta, census, changelog, hosted_updates, releases, replication, runner_versions, support_asks, ui_bundle
from .config import ASSISTANT_NAME
from . import strategy_bot, lifecycle, rooms, routines, statuses
from . import access as Access
from . import groups as Groups
from . import message_bots as MessageBots
from .store import H, Problem, encode, readiness_document

KEY = "onboarding"
BOTOPS = "botops"
LIBRARIAN = "librarian"
GOAL_MANAGER = G.GOAL_MANAGER          # keeps the KPIs and sets goals' automatic colours (docs/goals-and-kpis.md)
ASSISTANT_TEMPLATE_SLUG = "coo"
CARD_FILE = "card.yaml"
INSTRUCTIONS_FILE = "AGENT.md"
# Filled wherever a person reads a card: a template names itself after the assistant, and
# its AGENT.md is written against the company that is about to adopt it. The wizard no longer
# asks for assistant_name; an empty one falls back to TICO_ASSISTANT_NAME (display_names).
PLACEHOLDERS = ("company_name", "app_name", "assistant_name", "bot_name")
NEEDS_SETUP = statuses.NEEDS_SETUP
ONBOARDED = "onboarded"
EMPTY_NAMES = {"company_name": "", "app_name": "", "assistant_name": "", "owner_name": "", "team_domain": "",
               "website": ""}
# `pains`, `pains_text` and `tools` are no longer asked. An older record keeps them and a client may still send them;
# nothing reads them. `departments` are the org builder's chosen departments, in order, and `briefings` the one-line
# answer given for each. `building` is setup's first step: the owner's one line, or a new idea in a few sentences.
EMPTY_ANSWERS = {"building": "", "what_we_do": "", "customers": "", "team_size": "", "work_arrives": [],
                 "repetitive_work": "", "pains": [], "pains_text": "",
                 "tools": [], "software_product": "", "departments": [], "briefings": {}}
ANSWER_LABELS = (("building", "What we're building"), ("what_we_do", "What we do"), ("customers", "Customers"),
                 ("team_size", "Team size"), ("work_arrives", "Work arrives by"),
                 ("repetitive_work", "Repetitive work"),
                 ("software_product", "Software is the product"),
                 ("departments", "Departments"))


def _json(value, fallback=None):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return fallback


def _line(item):
    """One list entry as a sentence. YAML reads `- The watchlist: names, queries` as a mapping;
    a card author meant the sentence, so it is put back together rather than shown as a dict."""
    if isinstance(item, dict):
        return "; ".join(f"{key}: {text}" for key, text in item.items())
    return str(item)


def _strings(value, limit=50):
    return [line for line in map(_line, value) if line][:limit] if isinstance(value, list) else []


def fill(text, names, bot_name=""):
    """Replace the four template placeholders with this company's own words."""
    values = {**names, "bot_name": bot_name}
    for key in PLACEHOLDERS:
        text = str(text).replace("{{" + key + "}}", str(values.get(key) or ""))
    return text


def render(card, names, bot_name=""):
    """One card as a person reads it. A card is written for whichever company adopts it, so
    every word it shows carries the placeholders, not only its AGENT.md."""
    name = fill(bot_name or card["name"], names)
    instructions = fill(card["instructions"], names, name)
    return {**card, "name": name,
            "summary": fill(card["summary"], names, name),
            "instructions": instructions,
            "onboarding": [{key: fill(text, names, name) for key, text in item.items()}
                           for item in card.get("onboarding", [])],
            "first_routine": {key: fill(value, names, name) if isinstance(value, str) else value
                              for key, value in card.get("first_routine", {}).items()},
            "example": fill(card.get("example", ""), names, name),
            "owns": [fill(item, names, name) for item in card["owns"]],
            "never": [fill(item, names, name) for item in card["never"]]}


def _prerequisites(value):
    """`{tool, why, required}` rows; anything else in the list is left out rather than guessed at."""
    rows = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and str(item.get("tool") or "").strip():
            rows.append({"tool": str(item["tool"]).strip(), "why": str(item.get("why") or ""),
                         "required": bool(item.get("required"))})
    return rows[:12]


def _routine(value):
    value = value if isinstance(value, dict) else {}
    return {"title": str(value.get("title") or ""), "cadence": str(value.get("cadence") or ""),
            "output": str(value.get("output") or ""), "draft_only": bool(value.get("draft_only"))} if value else {}


def _card(document, instructions):
    """One card.yaml as the API serves it: every field present, nothing extra."""
    template = str(document.get("template") or "").strip()
    return {"template": template, "slug": str(document.get("slug") or template).strip(),
            "name": str(document.get("name") or template), "required": bool(document.get("required")),
            "bootstrap": bool(document.get("bootstrap")),
            "shared": bool(document.get("shared")),
            # Unset follows the default (every task its own session); a card names `task` or `bot` only to differ.
            "session": document.get("session") if document.get("session") in ("task", "bot") else None,
            # The template that heads its department in the org builder (templates/groups.yaml `head`).
            "lead": bool(document.get("lead")),
            # Where the org builder shows it (backend/recruit_rank.py): its department (else its `pack`'s),
            # its Material Symbols icon, the words it matches, and whether it is pre-checked (`default`),
            # shown (`common`) or under More (`niche`).
            "department": str(group_of(document) or ""), "icon": str(document.get("icon") or ""),
            "tags": _strings(document.get("tags")), "suggest": str(document.get("suggest") or ""),
            "team_templates": _strings(document.get("team_templates")),
            # A helper serves a person (the Inbox Manager), sits outside the org chart and is offered on its own.
            "kind": "helper" if document.get("kind") == "helper" else "role",
            # A retired card is offered nowhere; a bot already built from it keeps it (`Onboarding.catalog`).
            "retired": bool(document.get("retired")),
            # Checked when the wizard first shows the card, and `when` says who wants it.
            "default": bool(document.get("default")), "when": str(document.get("when") or ""),
            "summary": str(document.get("summary") or ""),
            "owns": _strings(document.get("owns")), "never": _strings(document.get("never")),
            "runtime": str(document.get("runtime") or ""), "model": str(document.get("model") or ""),
            "reasoning_effort": str(document.get("reasoning_effort") or ""),
            "recommend_when": _strings(document.get("recommend_when")),
            # A card with a first routine and an onboarding conversation is a starter (docs/starter-bots.md);
            # the others are built by BotOps.
            "pack": str(document.get("pack") or ""), "pains": _strings(document.get("pains")),
            "prerequisites": _prerequisites(document.get("prerequisites")),
            "first_routine": _routine(document.get("first_routine")),
            "onboarding": [{"ask": str(item.get("ask") or ""), "why": str(item.get("why") or "")}
                           for item in (document.get("onboarding") if isinstance(document.get("onboarding"), list) else [])
                           if isinstance(item, dict)],
            "example_output": str(document.get("example_output") or ""),
            "approval_required": [],       # compatibility field; sending uses outbound_send
            "starter": bool(document.get("first_routine")) and bool(document.get("onboarding")),
            "instructions": instructions}


# A template's avatar symbol: a Material Symbols name (card.yaml `icon`). Read on every bot list, so
# the names are parsed again only when a card file changes.
ICON_NAME = re.compile(r"^[a-z0-9_]{1,48}$")
_icon_cache = {"key": None, "icons": {}, "helpers": frozenset(), "leads": frozenset()}


def template_icons(settings):
    """{template: icon name} for every card that names a well-formed icon."""
    return _template_marks(settings)["icons"]


def helper_templates(settings):
    """The templates whose card says `kind: helper`: a bot made from one serves a person and sits outside the org chart."""
    return _template_marks(settings)["helpers"]


def lead_templates(settings):
    """The templates whose card says `lead: true`: a department's Head (backend/recruit_rank.py)."""
    return _template_marks(settings)["leads"]


def _template_marks(settings):
    root = Path(settings.catalog_dir)
    try:
        files = sorted(root.glob("*/" + CARD_FILE))
        key = (str(root), tuple((p.name, p.parent.name, p.stat().st_mtime_ns) for p in files))
    except OSError:
        return {"icons": {}, "helpers": frozenset(), "leads": frozenset()}
    if _icon_cache["key"] != key:
        icons, helpers, leads = {}, set(), set()
        for path in files:
            try:
                document = yaml.safe_load(path.read_text())
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(document, dict):
                continue
            template, icon = str(document.get("template") or "").strip(), str(document.get("icon") or "").strip()
            if template and ICON_NAME.match(icon):
                icons[template] = icon
            if template and document.get("kind") == "helper":
                helpers.add(template)
            if template and document.get("lead") is True:
                leads.add(template)
        _icon_cache.update(key=key, icons=icons, helpers=frozenset(helpers), leads=frozenset(leads))
    return _icon_cache


_card_cache = {"key": None, "cards": []}
_YAML = getattr(yaml, "CSafeLoader", yaml.SafeLoader)      # libyaml when the wheel has it: ten times faster on a card


def read_cards(settings):
    """Every template on disk, unrendered. A missing or half-written catalog yields the cards
    that do parse: onboarding still runs, it just has less to offer. Parsing 90-odd cards takes
    seconds, and a save asks once per bot it holds, so they are parsed again only when a file changes."""
    root = Path(settings.catalog_dir)
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
        key = (str(root), tuple((f.name, *(_mtime(f / name) for name in (CARD_FILE, INSTRUCTIONS_FILE))) for f in folders))
    except OSError:
        return []
    if _card_cache["key"] != key:
        cards = []
        for folder in folders:
            try:
                document = yaml.load((folder / CARD_FILE).read_text(), Loader=_YAML)
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(document, dict) or not str(document.get("template") or "").strip():
                continue
            try:
                instructions = (folder / INSTRUCTIONS_FILE).read_text()
            except OSError:
                instructions = ""
            card = _card(document, instructions)
            example = card["example_output"]
            path = (folder / example).resolve()
            try:
                card["example"] = path.read_text() if example and path.is_relative_to(folder.resolve()) else ""
            except (OSError, UnicodeError):
                card["example"] = ""
            cards.append(card)
        _card_cache.update(key=key, cards=cards)
    return [dict(card) for card in _card_cache["cards"]]


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def load(c):
    """The stored record, with every field a client reads present even before a first save."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    stored = (_json(row[0], {}) if row else {}) or {}
    return {"names": {**EMPTY_NAMES, **(stored.get("names") or {})},
            "answers": {**EMPTY_ANSWERS, **{k: v for k, v in (stored.get("answers") or {}).items() if k != "never_without_person"}},
            "selected": dict(stored.get("selected") or {}),
            "completed": stored.get("completed") or None,
            "assigned_to": stored.get("assigned_to") or None,
            # When the company started as the sample company (backend/sample_company.py): when, by whom, what it made.
            "sample": stored.get("sample") or None,
            "company_import": stored.get("company_import") or None,
            # {pick: seat id}: the roles setup laid out as Recruiting seats (`Onboarding.complete`).
            "seats": dict(stored.get("seats") or {}),
            "updated_at": stored.get("updated") or "", "updated_by": stored.get("updated_by") or ""}


def display_names(settings, record):
    """The environment's configured names, with whatever onboarding saved on top."""
    configured = {"company_name": settings.company_name, "app_name": settings.app_name,
                  "assistant_name": settings.assistant_name}
    names = {key: str(record["names"].get(key) or "") or value for key, value in configured.items()}
    # An assistant named after the company was the default of an earlier release (name_default_assistant renames the bot).
    if names["assistant_name"].strip().lower() == names["company_name"].strip().lower():
        names["assistant_name"] = ASSISTANT_NAME
    return names


def needed(c, who, record):
    """Whether the owner still owes us the first-run wizard.

    A company that already runs active bots is past its first run even if nobody ever finished
    the wizard: deployments older than onboarding, or one set up by hand, must not be sent back
    to screen one.
    """
    if who is None or who.role != "owner" or record["completed"]:
        return False
    active = c.execute("SELECT 1 FROM bots WHERE state='active' LIMIT 1").fetchone()
    return active is None


def running_in_docker():
    """Whether this server is a container (Docker or Podman), which is how nearly every install runs."""
    return bool(os.environ.get("TICO_IN_DOCKER")) or any(Path(marker).exists() for marker in ("/.dockerenv", "/run/.containerenv"))


def config_view(c, settings, who=None):
    """What every client needs to name this environment: the env settings, the names
    onboarding saved over them, and whether the owner still owes us the first-run wizard."""
    record = load(c)
    value = {**settings.environment(), **display_names(settings, record)}
    value["onboarding_needed"] = needed(c, who, record)
    chosen = providers.load(c, settings)
    value["providers_configured"] = providers.configured(chosen)
    # The model a computer's sign-in is for; empty until a provider is chosen, so no sign-in is asked before then. A
    # computer with no bots yet installs this one's harness (runner-rs/src/commands.rs want_harnesses).
    value["default_runtime"] = providers.default_runtime(chosen)
    if who is not None:
        value["enabled_providers"] = list(chosen["enabled"])      # older runners install these providers' harnesses
        # Where a runner sends a turn whose subscription hit its limit, when the bot names no fallback.
        value["fallback_models"] = providers.default_fallbacks(chosen)
        if getattr(who, "runner_id", None):
            # The Gateway also runs turns before subscription sign-in under the recommended choice,
            # even when automatic quota fallback is off. Its harness must be installed before a claim
            # can pass readiness; it is an optional computer default, not the bot's saved runtime.
            from . import models_preset
            value["gateway_runtime"] = (models_preset.fallback_row(chosen) or {}).get("runtime", "")
            # This computer's logins a usage limit holds (backend/login_holds.py): a runner that restarted, and so
            # forgot the limits it met, still skips them in its fallback choice until they end.
            from . import login_holds
            from .store import H
            value["login_holds"] = [{"profile": h["profile"], "runtime": h["runtime"], "until": h["until"]}
                                    for h in login_holds.on_runner(c, who.runner_id) if (h.get("until") or "") > H.now()]
        # A runner names a folder its turns can't write (Muse Code's sessions) apart from its sign-in only to a server
        # that takes the field (runner-rs/src/fallback.rs server_takes_data_problem).
        value["readiness_data_problem"] = True
        value["computer_logins_v1"] = True
        # Likewise whether a tool's turns run as their own OS user (runner-rs/src/fallback.rs server_takes_turn_user).
        value["readiness_turn_user"] = True
        # And whether it is a cloud machine's trust group (`trust`, runner-rs/src/fallback.rs server_takes_trust).
        value["readiness_trust"] = True
        # Where a bucket's objects open (TICO_S3_VIEW_URLS): the UI links s3:// URIs, `hub` rewrites them before sending.
        value["s3_view_urls"] = dict(settings.s3_view_urls)
        # Pages and SVG from Tico file hosting: the desktop app follows a frame to this origin, only on its signed
        # visual paths (app/src/visual_origin.rs), when the install has no visual address of its own.
        from . import tico_storage, visuals
        value["visuals"] = visuals.shown(settings, c)
        value["visual_files"] = "" if settings.visual_origin else tico_storage.visual_address(c)[0]
    value["in_docker"] = running_in_docker()      # the first run offers a Linux computer first when the server is one
    value["version"] = releases.version()
    value["ui_build"] = ui_bundle.build_id(settings.ui_dir)      # an open page compares it with its own (ui/app/notices.js)
    value["update"] = hosted_updates.notice(c, settings)     # with the release Tico support scheduled, if any
    if who is not None and who.role in ("owner", "human"):
        value["changelog"] = changelog.summary(c, settings, who.actor)
    value["usage_count_notice"] = census.notice_due(c, settings, who)
    # "How is Tico working for you?", when this person is due to be asked (backend/support_asks.py).
    value["feedback_question"] = support_asks.feedback_due(c, settings, who)
    value["support_on"] = support_asks.support_on(settings)      # the page hides "Want help setting up?" without it
    value["backup"] = replication.status()
    value["runner_compat"] = runner_versions.desired()
    value["beta"] = beta.listing()      # the Beta pill and Help's Beta list (ui/app/state.js betaPill)
    from . import update_feeds
    value["update_feeds"] = update_feeds.status()   # what Subscribe to updates can send (settings-bot-editor.js)
    return value


def _answer_lines(answers):
    lines = []
    for key, label in ANSWER_LABELS:
        value = answers.get(key)
        text = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value or "")
        lines.append("- " + label + ": " + (text or "not answered"))
    # What the owner said about each department in the org builder, so a bot starts from their own words.
    for department, text in (answers.get("briefings") or {}).items():
        if str(text or "").strip():
            lines.append("- " + str(department) + " today: " + str(text).strip())
    return lines


def setup_body(slug, choice, answers):
    """What BotOps needs to build one bot without asking: the names, the reviewed
    instructions verbatim, and what the team said about itself."""
    return "\n".join([
        "Create bot-" + slug + " from the " + choice["template"] + " template and bring "
        + choice["display_name"] + " up.",
        "",
        "- slug: " + slug,
        "- template: " + choice["template"],
        "- display name: " + choice["display_name"],
        "- requested description and limits: " + (choice.get("description") or "not specified"),
        "Use this request's scope. Do not inherit a previous bot's tools, repository or Routines.",
        "",
        "Instructions the owner reviewed, for AGENT.md:",
        "",
        "```markdown",
        choice["instructions"].strip(),
        "```",
        "",
        "What the team told us during Setup:",
        *_answer_lines(answers),
    ])


def job_description(card, names, reason="", instructions="", display_name=""):
    """A seat's job description as setup writes it: the role's text from its template (what it does and owns), why the
    company chart has it, then what the owner changed in the template's instructions during setup. Those changes are
    the duties and limits the owner reviewed, so recruiting must see them; the template's own instructions stay with the
    template, which keeps the description short. Unedited or empty instructions add nothing. Anyone with access to the
    seat edits it from there."""
    raw = card or {}
    card = render(raw, names) if raw else {}
    parts = [str(card.get("summary") or "").strip()]
    owns = [line for line in card.get("owns") or [] if str(line).strip()]
    if owns:
        parts.append("Owns:\n" + "\n".join("- " + str(line).strip() for line in owns))
    if str(reason or "").strip():
        parts.append("Why this role is on the company chart:\n" + str(reason).strip())
    parts.append(_instruction_changes(raw, names, instructions, display_name))
    return "\n\n".join(part for part in parts if part)


def _instruction_changes(card, names, instructions, display_name=""):
    """The lines the owner added to or took out of a template's instructions, or "" when they left them as shown. The
    wizard shows them filled with the company's names and, once saved, the role's own name (`Onboarding.catalog`)."""
    text = str(instructions or "").strip()
    if not text or not card:
        return ""
    shown = [render(card, names, name)["instructions"].strip() for name in ("", display_name)]
    if text in shown:
        return ""
    base = min(shown, key=lambda default: sum(1 for line in difflib.ndiff(default.splitlines(), text.splitlines())
                                                if line[:1] in "+-"))
    added, removed, kept = [], [], 0
    for line in difflib.ndiff(base.splitlines(), text.splitlines()):
        if line[:2] in ("+ ", "- ") and line[2:].strip():
            (added if line[0] == "+" else removed).append(line[2:].rstrip())
        elif line[:2] == "  " and line[2:].strip():
            kept += 1
    if len(removed) > kept:
        # Mostly rewritten: the owner's text is the instructions, and a list of the template's lines would bury it.
        return "The owner wrote this role's instructions in setup:\n" + text
    out = ["The owner changed this role's instructions in setup."]
    if added:
        out.append("Added:\n" + "\n".join(added))
    if removed:
        out.append("Taken out:\n" + "\n".join(removed))
    return "\n\n".join(out) if added or removed else ""


class Onboarding:
    def __init__(self, store, auth, settings_admin, execution, models):
        self.store, self.auth, self.admin = store, auth, settings_admin
        self.execution, self.models = execution, models
        self.settings = store.settings

    # ------------------------------------------------------------------ access
    @staticmethod
    def _owner(who):
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner may set this company up", 403)

    def require_reader(self, who):
        """The owner and bot administrators run onboarding; runners and bots read the answers
        so they can write knowledge/company.md into the repositories they materialize."""
        if who.role in ("runner", "bot") or self.auth.bot_admin(who):
            return
        raise Problem("forbidden", "Only the owner or a bot administrator may read onboarding", 403)

    # ------------------------------------------------------------------ catalog
    def catalog(self, c, rendered=True):
        cards = [card for card in read_cards(self.settings) if not card["retired"] or H.resolve_bot(c, card["slug"])]
        if rendered:
            record = load(c)
            names = display_names(self.settings, record)
            # A bot the person already named and renamed reads under that name, not the card's.
            cards = [render(card, names, (record["selected"].get(card["slug"]) or {}).get("display_name"))
                     for card in cards]
        # What the company must have comes first; the rest reads as an alphabetical menu.
        cards.sort(key=lambda card: (not card["required"], card["name"].lower()))
        return cards

    def _template(self, name):
        card = next((card for card in read_cards(self.settings) if card["template"] == name), None)
        if not card:
            raise Problem("template", "No such template in the catalog: " + str(name), 422)
        return card

    # ------------------------------------------------------------------ reads
    def view(self, c, who):
        record = load(c)
        owner = self.auth.owner_id(c)
        # Where every department head reports: the owner, at the top of the chart.
        home = "human:" + owner if owner and H.human(c, owner) else ""
        from . import company_import, invites
        imported = record.get("company_import")
        if imported and imported.get("people"):
            sent = invites.sent(c)
            record = {**record, "company_import": {**imported, "people": [
                {**person, "invited_at": (sent.get(person.get("person") or "") or {}).get("at")}
                for person in imported["people"]]}}
        return {**record, "home": home, "bots": self._bots(c), "machine": self._machine(c),
                "needed": needed(c, who, record), "company_import_failed": company_import.failure(c)}

    def _bots(self, c):
        rows = []
        labels = {r["id"]: r["slug"] for r in c.execute("SELECT id,slug FROM bots")}   # one read: a manager's label
        for row in c.execute("SELECT bot,config,onboarding_state,reports_to FROM bot_config ORDER BY bot").fetchall():
            declared = _json(row["config"], {}) or {}
            bot = H.bot(c, row["bot"]) or {}
            if bot.get("state") == "archived":          # the assistant a company chose not to have
                continue
            machine, present = self._placement(c, row["bot"])
            rows.append({"id": row["bot"], "slug": bot.get("slug") or row["bot"],
                         "display_name": bot.get("display_name") or bot.get("slug") or row["bot"],
                         "status": bot.get("state") or "", "template": declared.get("template") or "",
                         "setup_task_id": declared.get("setup_task_id") or None,
                         "onboarding_state": row["onboarding_state"] or "",
                         "reports_to": row["reports_to"] or "",
                         "reports_to_slug": labels.get(row["reports_to"] or "", row["reports_to"] or ""),
                         "assigned_to": machine, "repository_present": present})
        return rows

    @staticmethod
    def _placement(c, slug):
        """The machine this bot runs on, and what it last said about the repository. A null
        repository report means no machine has looked yet, not that the repository is missing."""
        row = c.execute("SELECT r.id,r.label,r.readiness FROM assignments a "
                        "JOIN runners r ON r.id=a.runner_id WHERE a.bot=?", (slug,)).fetchone()
        if not row:
            return None, None
        reported = readiness_document(row["readiness"]).get("bots", {}).get(slug)
        return ({"runner_id": row["id"], "label": row["label"]},
                bool(reported.get("repository_present")) if isinstance(reported, dict) else None)

    @staticmethod
    def _machine(c):
        recent = H.shift(H.now(), seconds=-60)
        runners = [{"id": row["id"], "label": row["label"],
                    "online": bool(row["last_seen_at"] and row["last_seen_at"] > recent)}
                   for row in c.execute("SELECT id,label,last_seen_at FROM runners "
                                        "WHERE revoked_at IS NULL ORDER BY created_at")]
        return {"runners": runners, "enrolled": bool(runners)}

    # ------------------------------------------------------------------ writes
    def save(self, c, who, body):
        self._owner(who)
        record = load(c)
        selected = {}
        known = {card["template"] for card in read_cards(self.settings)}
        for slug, choice in body.selected.items():
            if choice.template not in known:
                raise Problem("template", "No such template in the catalog: " + str(choice.template), 422)
            selected[slug] = {"template": choice.template, "display_name": choice.display_name,
                              "instructions": choice.instructions, "reports_to": choice.reports_to}
            self._reports_to_valid(c, slug, choice.reports_to)
        names = body.names.model_dump()
        if "team_domain" in body.names.model_fields_set:
            names["team_domain"] = Access.team_domain(names["team_domain"])
        else:
            names["team_domain"] = record["names"].get("team_domain") or ""      # a client that never asks keeps it
        if "website" in body.names.model_fields_set:
            from .strategy_setup import normalize_url, refuse_unsafe
            try:
                names["website"] = normalize_url(names["website"])
            except ValueError as exc:
                raise Problem("validation", str(exc), 422) from None
            if names["website"]:
                refuse_unsafe(names["website"])
        else:
            names["website"] = record["names"].get("website") or ""
        answers = body.answers.model_dump()
        answers["building"] = answers["building"].strip() if "building" in body.answers.model_fields_set \
            else record["answers"].get("building") or ""
        if not names["website"] and not answers["building"] and (record["names"].get("website") or
                                                                 record["answers"].get("building")):
            from .strategy_setup import stop_running
            stop_running(c, {"code": "cleared", "message": "The website and the line were cleared"})
        record.update(names=names, answers=answers, selected=selected)
        # The owner's name goes on their roster entry (first run only knew their email).
        owner = self.auth.owner_id(c)
        if owner and body.names.owner_name.strip():
            Access.rename_person(c, rooms.roster(c), owner, body.names.owner_name)
        self._store(c, record, who.actor)
        H.event(c, who.actor, "onboarding.saved", "", {"selected": sorted(selected)})
        return self.view(c, who)

    def _reports_to_valid(self, c, slug, reports_to):
        """A person on the chart (`human:<id>`), or a bot that exists now or is being created with it."""
        if not reports_to:
            return
        if reports_to.startswith("human:"):
            if not H.human(c, reports_to[6:]):
                raise Problem("not_found", "Reports-to person was not found: " + reports_to[6:], 404)
        elif reports_to == slug:
            raise Problem("hierarchy", "A bot cannot report to itself", 422)

    def complete(self, c, who, models=None, seats=True):
        """Deploy the chart setup picked (docs/onboarding.md, "Setup picks the chart"): the built-ins (BotOps, the
        Librarian, the Strategy Bot and the Assistant) are created as bots, Active from day one, and every other role is
        laid out as a Recruiting seat with its reporting line: no bot, repository, computer slot or BotOps task until
        someone recruits it. `seats` False creates every pick as a bot, as the sample company does
        (backend/sample_company.py). `models` names the model a new bot runs on, by slug, in place of its card's (the
        sample company spreads its bots over two subscriptions); a model whose provider is off is ignored."""
        self._owner(who)
        record = load(c)
        names = display_names(self.settings, record)
        cards = {card["template"]: card for card in read_cards(self.settings)}
        plan = self._plan(record, cards, names)
        owner = self.auth.owner_id(c)
        home = "human:" + owner if owner and H.human(c, owner) else ""
        roles = {}
        if seats:
            for slug, choice in plan.items():
                raw = cards.get(choice["template"]) or {}
                # A message bot (`kind: helper`, the Inbox Manager) serves one person and is never on the chart, so it
                # is made as a bot linked to its mailbox, like the built-ins; only a role becomes a seat.
                if not (raw.get("required") or raw.get("bootstrap") or raw.get("kind") == "helper") and not H.resolve_bot(c, slug):
                    roles[slug] = choice
            plan = {slug: choice for slug, choice in plan.items() if slug not in roles}
        rank, parents, ids = 0, {}, {}
        for slug, choice in plan.items():
            raw = cards.get(choice["template"])
            card = render(raw, names, choice["display_name"]) if raw else {}
            if (models or {}).get(slug):
                card["model"] = models[slug]
            starter = bool(card.get("starter")) and not card.get("bootstrap")
            reports_to = str(choice.get("reports_to") or "") or (home if starter else "")
            found = H.resolve_bot(c, reports_to) if reports_to and not reports_to.startswith("human:") else None
            now = reports_to if reports_to.startswith("human:") else (found or {}).get("id", "")
            bot_id = self._define(c, who, slug, choice, card, reports_to=now)
            ids[slug] = bot_id
            MessageBots.link(c, who.actor, bot_id, who.actor)
            if reports_to and not found and not reports_to.startswith("human:"):
                parents[bot_id] = reports_to          # it reports to a bot that is created later in this plan
            if starter:
                # A starter is created whole, now: parked until its first conversation, no BotOps task.
                self._make_starter(c, who, bot_id, choice["template"], rank)
                rank += 1
            else:
                self._setup_task(c, who, bot_id, choice, card, record["answers"])
            if card.get("bootstrap"):
                self._seed_routines(c, who, bot_id, choice["template"])
        for bot_id, parent in parents.items():
            parent = ids.get(parent, parent)
            row = c.execute("SELECT revision,reports_to FROM bot_config WHERE bot=?", (bot_id,)).fetchone()
            if row["reports_to"] != parent:
                self.admin.update_bot(c, who, bot_id, M.BotDefinitionUpdate(
                    reports_to=parent, expected_revision=row["revision"]))
        # The team builder builds the chart by group: each bot goes in the group of its template, made if the team
        # has none yet (backend/groups.py).
        before = set(Groups._roster(c)["org_groups"])
        Groups.place(c, self.settings, [ids[slug] for slug in plan], group_order=record["answers"]["departments"])
        laid = self._lay_out(c, who, record, roles, cards, names, home)
        self._link_bets(c, record, before)
        if not record["completed"]:
            # A new company starts caught up: the releases shipped before it existed are not news to its owner.
            changelog.mark_read(c, self.settings, who.actor, [row["id"] for row in changelog.products(c, self.settings)])
        # Completing twice keeps the moment the company actually finished; the chart's picks stay in `selected`.
        record.update(selected={**plan, **roles}, completed=record["completed"] or H.now(), seats=laid)
        self._wire(c, record, who.actor)
        self._store(c, record, who.actor)
        self._market_task(c, who, record)
        # Setting the team up here is one of the two ways out Health offers for a setup file that wasn't imported.
        from . import company_import
        company_import.clear_failure(c)
        H.event(c, who.actor, "onboarding.completed", "", {"bots": sorted(plan), "seats": sorted(roles)})
        return self.view(c, who)

    def _link_bets(self, c, record, before):
        """Each group this setup made that clearly serves one of the strategy draft's bets names it (`bet_id`): the
        same match the org builder showed (backend/recruit.py `bet_for`), from the department and the owner's answer
        for it. With no bets, nothing."""
        from . import recruit
        bets = recruit.strategy_bets(c)
        made = set(Groups._roster(c)["org_groups"]) - before
        if not bets or not made:
            return []
        value = recruit.catalog(self.settings)
        briefings = record["answers"].get("briefings") or {}
        links = {}
        for gid in made:
            served = recruit.bet_for(value, gid, bets, str(briefings.get(gid) or ""))
            if served:
                links[gid] = served["id"]
        return Groups.link_bets(c, links)

    def _market_task(self, c, who, record):
        """Setup gave the team's website: the Librarian's "Set up the market map" is filed from it now, once, unless
        the owner already filed it from the Market page (backend/librarian_setup.py). Its playbook reads the strategy for
        the rest (templates/catalog/librarian/playbooks/market-setup.md)."""
        from . import getting_started as GS
        from . import librarian_setup as LS
        website = str(record["names"].get("website") or "").strip()
        librarian = H.bot(c, LIBRARIAN) if website else None
        if not librarian or librarian["state"] == "archived":
            return None
        state = LS._load(c, who.actor, LS.MARKET_KEY)
        if state.get("task_id"):
            return None
        building = str(record["answers"].get("building") or record["answers"].get("what_we_do") or "").strip()
        answers = {**{key: "" for key in LS.QUESTIONS}, "sell": "\n".join(x for x in (website, building) if x)}
        # Best effort: finishing setup never fails because the Librarian could not be handed the task.
        c.execute("SAVEPOINT setup_market_task")
        try:
            task = GS._task(c, self.auth, who, LIBRARIAN, GS._market_title(c, "bot:" + LIBRARIAN, who.actor),
                            LS.market_text(answers))
        except (H.Refused, Problem):
            c.execute("ROLLBACK TO setup_market_task")
            c.execute("RELEASE setup_market_task")
            return None
        c.execute("RELEASE setup_market_task")
        LS._save(c, who.actor, LS.MARKET_KEY, {**state, "answers": answers, "task_id": task["id"], "skipped": False})
        H.event(c, who.actor, "getting_started.market_requested", LIBRARIAN, {"task": task["id"], "setup": True})
        return task

    def _lay_out(self, c, who, record, roles, cards, names, home):
        """The picked roles as Recruiting seats (backend/lifecycle.py `lay_out`), each with the job description its
        template gives and its reporting line. Completing twice lays out none again: {pick: seat id} is kept on the
        record."""
        laid = dict(record.get("seats") or {})
        todo = {slug: choice for slug, choice in roles.items() if slug not in laid}
        if not todo:
            return laid
        teams = Groups.seat_teams(c, self.settings, sorted({choice["template"] for choice in todo.values()}),
                                  group_order=record["answers"]["departments"])

        def line(choice):
            parent = str(choice.get("reports_to") or "")
            if parent in todo:
                return parent
            if parent in laid:
                return lifecycle.SEAT_PREFIX + laid[parent]
            found = H.resolve_bot(c, parent) if parent else None
            if parent.startswith("human:") or found:
                return found["id"] if found else parent
            return home
        rows = {slug: {"title": choice["display_name"], "team": teams.get(choice["template"], ""),
                       "description": job_description(cards.get(choice["template"]) or {}, names, choice.get("reason", ""),
                                                      choice.get("instructions", ""), choice["display_name"]),
                       # The owner reviewed the setup description and edited duties/limits before it became a seat.
                       # Keep those words instead of asking the Strategy Bot to replace them on the first open.
                       "description_by": who.actor,
                       "reports_to": line(choice)}
                for slug, choice in todo.items()}
        return {**laid, **lifecycle.lay_out(c, who.actor, rows)}

    def start_sample(self, c, who):
        """The welcome's "Start with a sample company": this new company becomes Tico's sample company
        (backend/sample_company.py). The owner only; refused once the company is set up."""
        self._owner(who)
        from . import sample_company
        return sample_company.load(self, c, who)

    def _make_starter(self, c, who, slug, template, rank=None):
        """A starter template's bot, as first run creates it: it records the template and its version,
        waits for the computer to materialize its repository (`materialize`), seeds its first routine
        off, and is `needs_onboarding` until it says its setup is done. Starting the setup turns that
        routine on (`arm_first_routine`). Nothing claims work for it before then except a message from
        a person (execution.candidate)."""
        declared = self._declared(c, slug)
        declared.update(template=template, template_version=releases.version(), materialize=True)
        if rank is not None:
            declared["setup_rank"] = rank
        self._write_config(c, slug, declared)
        c.execute("UPDATE bot_config SET onboarding_state=? WHERE bot=? AND COALESCE(onboarding_state,'')<>?",
                  (NEEDS_SETUP, slug, ONBOARDED))
        self._seed_routines(c, who, slug, template)
        H.event(c, who.actor, "bot.needs_onboarding", slug, {"template": template})

    def onboarded(self, c, who, slug, arm=True, summary=""):
        """Mark onboarding complete: a person above the bot on the org chart (its manager, their managers, the owner)
        says it is done learning, so it becomes Active, its routines run and its work is claimed (backend/lifecycle.py).
        The bot's own call is its "I'm ready" (`lifecycle.ready`) and changes no stage. A member's bot counts toward
        their limit from here on, so the limit is checked now. `arm` False leaves its first routine off (the sample
        company's bots, set up by the sample's own answers)."""
        slug = (H.resolve_bot(c, slug) or {}).get("id", slug)
        if not H.bot(c, slug):
            raise Problem("not_found", "Bot not found", 404)
        row = c.execute("SELECT onboarding_state,created_by FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if who.role == "bot" and H.actor_id(who.actor) == slug:
            if not statuses.is_parked(row["onboarding_state"]):
                return {"bot": slug, "onboarding_state": row["onboarding_state"] or "", "stage": lifecycle.ACTIVE,
                        "changed": False}
            return {**lifecycle.ready(c, who, slug, summary), "onboarding_state": row["onboarding_state"],
                    "changed": False}
        if not lifecycle.may_complete(self.auth, c, who, slug):
            raise Problem("forbidden", "Only a person above this bot on the org chart (its manager, their managers or "
                          "the owner) marks its onboarding complete", 403)
        if not statuses.is_parked(row["onboarding_state"]):
            return {"bot": slug, "onboarding_state": row["onboarding_state"] or "", "stage": lifecycle.ACTIVE,
                    "changed": False}
        if self.auth.member_bot(c, slug):
            limit = Access.load_access(c, self.settings)["member_bot_limit"]
            if self.admin.counted_bots(c, row["created_by"], excluding=slug) >= limit:
                raise Problem("bot_limit", "The person who owns this bot already has the most active bots a member may "
                              f"have ({limit}). Archive one they no longer need, or ask an admin to raise the limit", 409)
        declared = self._declared(c, slug)
        declared.update(onboarded_at=H.now(), onboarded_by=who.actor)
        self._write_config(c, slug, declared)
        c.execute("UPDATE bot_config SET onboarding_state=? WHERE bot=?", (ONBOARDED, slug))
        H.event(c, who.actor, "bot.onboarded", slug, {})
        H.setup_finished(c, slug)
        # A bot whose setup was never started (Finish with defaults on Overview) gets its first routine as Start setup
        # would have given it; once only, so one already armed or turned off by a person is left alone.
        routine = self.arm_first_routine(c, who, slug) if arm else None
        return {"bot": slug, "onboarding_state": ONBOARDED, "stage": lifecycle.ACTIVE, "changed": True, "routine": routine}

    def attach_template(self, c, who, slug, template, instructions, *, queue_build=False, title_prefix=""):
        """Starters are built by their Computer; custom bots get a BotOps task."""
        slug = (H.resolve_bot(c, slug) or {}).get("id", slug)
        record = load(c)
        row = H.bot(c, slug) or {}
        display = row.get("display_name") or row.get("slug") or slug
        card = render(self._template(template), display_names(self.settings, record), display)
        declared = self._declared(c, slug)
        choice = {"template": template, "display_name": display,
                  "instructions": instructions or card["instructions"],
                  "description": self.admin.definition(c, slug).get("description") or "",
                  "title_prefix": title_prefix}
        if choice["description"]:
            choice["instructions"] += "\n\n## Requested scope\n" + choice["description"] + "\n"
        declared.update(template=template, instructions=choice["instructions"])
        self._write_config(c, slug, declared)
        if card.get("starter") and not card.get("bootstrap"):
            self._make_starter(c, who, slug, template)
            MessageBots.link(c, who.actor, slug, who.actor)
            return {"template": template, "setup_task_id": None, "onboarding_state": NEEDS_SETUP}
        if queue_build and not card.get("bootstrap") and not H.bot(c, BOTOPS):
            raise Problem("not_found", "Add BotOps from Templates to build this bot", 409)
        declared["template_version"] = releases.version()
        self._write_config(c, slug, declared)
        MessageBots.link(c, who.actor, slug, who.actor)          # a message bot is its person's from the start
        return {"template": template,
                "setup_task_id": self._setup_task(c, who, slug, choice, card, record["answers"])}

    def assign_pending(self, c, runner_id):
        """Give every catalog bot without a machine to this one. The runner bootstraps only
        the bots assigned to it, so a bot nobody placed never gets its repository. This is the
        same call Settings makes, so generations, refusals and audit events are identical."""
        who = self.auth.owner_identity(c)
        placed = []
        computer = c.execute("SELECT operator,accepts_member_bots FROM runners WHERE id=? AND revoked_at IS NULL",
                             (runner_id,)).fetchone()
        for row in c.execute("SELECT bc.bot,bc.config,bc.operator FROM bot_config bc "
                             "LEFT JOIN bots b ON b.id=bc.bot WHERE COALESCE(b.state,'active')<>'archived' "
                             "ORDER BY bc.bot").fetchall():
            if not (_json(row["config"], {}) or {}).get("template"):
                continue
            if c.execute("SELECT 1 FROM assignments WHERE bot=?", (row["bot"],)).fetchone():
                continue                     # a bot a person already placed is never moved
            if c.execute("SELECT 1 FROM cloud_machines WHERE bot=? AND mode='cloud'", (row["bot"],)).fetchone():
                continue                     # it runs on a cloud machine (backend/cloud_machines.py)
            if self.auth.member_bot(c, row["bot"]) and not (
                    computer and (computer["accepts_member_bots"] or computer["operator"] == row["operator"])):
                continue                     # a member's bot goes on its member's computer, or one open to members' bots
            try:
                self.execution.assign(c, who, row["bot"], SimpleNamespace(
                    runner_id=runner_id, expected_generation=0))
            except Problem as refusal:
                if refusal.code not in ("inbox_isolation", "shared_runner"):
                    raise
                continue                     # an inbox or a shared checkout cannot take a second bot here
            placed.append(row["bot"])
        return placed

    def turn_on_assistant(self, c, who):
        """The owner's one click on the Assistant tab: bring the company's assistant back (the v0.2.1
        restore of an archived one) or add it from the catalog, put it on the computer BotOps runs
        on and activate it. With no computer yet it is left planned, and the answer says so."""
        return self._turn_on(c, who, self.settings.assistant_bot, "assistant",
                             self.settings.assistant_name, "assistant.turned_on")

    def turn_on_librarian(self, c, who):
        """The Docs page's Turn on Librarian: the same for the built-in docs bot (docs/librarian.md)."""
        return self._turn_on(c, who, LIBRARIAN, LIBRARIAN, "Librarian", "librarian.turned_on")

    def name_default_assistant(self, c):
        """An assistant named after the company (the old default) becomes "Assistant", once. Idempotent:
        a name anyone chose since, or the one already set, is left alone."""
        slug = self.settings.assistant_bot
        row = H.bot(c, slug)
        company = display_names(self.settings, load(c))["company_name"].strip().lower()
        if not row or not company or (row["display_name"] or "").strip().lower() != company:
            return False
        c.execute("UPDATE bots SET display_name='Assistant' WHERE id=?", (slug,))
        config = c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if config and config["config"]:
            try:
                declared = json.loads(config["config"])
                declared["display_name"] = "Assistant"
                c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(declared), slug))
            except ValueError:
                pass
        return True

    def turn_on_health_monitor(self, c, who):
        """Settings > Health's switch: the optional Health Monitor (docs/health-monitor.md). Never added on its own."""
        from .health_monitor import NAME, SLUG
        return self._turn_on(c, who, SLUG, SLUG, NAME, "health_monitor.turned_on")

    def turn_on_goal_manager(self, c, who):
        """The same for the built-in Strategy Bot (docs/strategy-bot.md)."""
        return self._turn_on(c, who, GOAL_MANAGER, GOAL_MANAGER, strategy_bot.name(c), "goal_manager.turned_on")

    def ensure_librarian(self, c):
        """A company set up before the Librarian was built in gets it without anyone clicking, once
        it can run: the owner is on the roster, a model is chosen and a computer is enrolled. Called
        when the server starts (an update) and when a computer enrolls; with any of those missing it
        does nothing, and the owner's Turn on Librarian stays available. An owner who paused it
        keeps it paused: only a missing or unplaced one is touched."""
        return self._ensure_builtin(c, LIBRARIAN, "Librarian", "librarian.turned_on")

    def ensure_goal_manager(self, c):
        """The same for the Strategy Bot: a company from before it was built in gets it once it can run it."""
        return self._ensure_builtin(c, GOAL_MANAGER, strategy_bot.name(c), "goal_manager.turned_on")

    def ensure_task_manager(self, c):
        """The same for the Task Manager (docs/task-manager.md): a company from before it was built in gets it once it can
        run it. It is a helper outside the org chart, so no chart or seat changes."""
        return self._ensure_builtin(c, "task-manager", "Task Manager", "task_manager.turned_on")

    def ensure_bookkeeper(self, c):
        """The same for the Bookkeeper (docs/bookkeeper.md), once Finance is set up (backend/bookkeeper.py `ensure`
        checks that and adds its defaults). A helper outside the org chart, like the Task Manager."""
        return self._ensure_builtin(c, "bookkeeper", "Bookkeeper", "bookkeeper.turned_on")

    def ensure_counsel(self, c):
        """The same for Counsel (docs/rules-plan.md §5): the helper that reads documents for Rules. A company from before it
        was built in gets it once it can run it, and it is outside the org chart."""
        return self._ensure_builtin(c, H.COUNSEL, "Counsel", "counsel.turned_on")

    def turn_on_task_manager(self, c, who):
        """Bring the Task Manager back after it was paused or archived by an older version (its report is plain code and
        keeps running either way, but the tasks it opens need the bot to be active)."""
        return self._turn_on(c, who, "task-manager", "task-manager", "Task Manager", "task_manager.turned_on")

    def _ensure_builtin(self, c, slug, name, event):
        if not load(c)["completed"] or not providers.configured(providers.load(c, self.settings)):
            return None
        if not c.execute("SELECT 1 FROM runners WHERE revoked_at IS NULL").fetchone():
            return None
        row = H.bot(c, slug)
        if row and (row["state"] != "planned" or c.execute("SELECT 1 FROM assignments WHERE bot=?", (slug,)).fetchone()):
            return None
        if row and row["state"] == "archived":
            return None
        if not any(card["template"] == slug for card in read_cards(self.settings)):
            return None
        # Best effort and all or nothing: an update or an enrollment never fails because of this.
        c.execute("SAVEPOINT ensure_" + slug.replace("-", "_"))
        try:
            who = self.auth.owner_identity(c)
            done = self._turn_on(c, who, slug, slug, name, event)
        except Problem:
            c.execute("ROLLBACK TO ensure_" + slug.replace("-", "_"))
            done = None
        c.execute("RELEASE ensure_" + slug.replace("-", "_"))
        return done

    def _turn_on(self, c, who, slug, template, name, event):
        row = H.bot(c, slug)
        if row and row["state"] == "active":
            return {"bot": slug, "state": "active", "restored": False}
        if row and row["state"] not in ("archived", "planned", "paused"):
            raise Problem("state", "The " + name + " is " + row["state"]
                          + "; a person changes that in Settings", 409)
        record = load(c)
        card = render(self._template(template), display_names(self.settings, record), name)
        choice = {"template": card["template"], "display_name": card["name"], "instructions": card["instructions"]}
        restored = bool(row and row["state"] == "archived")
        if restored:
            # The restore keeps the bot's own model and settings; only the name and description are renewed.
            self.admin.create_bot(c, who, M.BotDefinitionCreate(
                slug=slug, display_name=choice["display_name"], description=str(card.get("summary") or ""),
                status="planned", repo=self._recorded_repo(c, slug), thread_mode="personal", model=row.get("model") or "restore",
                effort=row.get("effort") or "high", owners=[H.actor_id(who.actor)]))
        elif not row:
            self._define(c, who, slug, choice, card)
        if not c.execute("SELECT 1 FROM assignments WHERE bot=?", (slug,)).fetchone():
            # BotOps' computer first, then the oldest; one an inbox bot keeps to itself, or one that may not
            # host this bot (another member's), is skipped.
            botops = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (BOTOPS,)).fetchone()
            machines = [r["id"] for r in c.execute("SELECT id FROM runners WHERE revoked_at IS NULL ORDER BY created_at")]
            if botops and botops["runner_id"] in machines:
                machines.remove(botops["runner_id"])
                machines.insert(0, botops["runner_id"])
            refusal = None
            for runner_id in machines:
                try:
                    self.execution.assign(c, who, slug, SimpleNamespace(runner_id=runner_id, expected_generation=0))
                    break
                except Problem as problem:
                    if problem.code not in ("inbox_isolation", "shared_runner", "forbidden", "computer_closed"):
                        raise
                    refusal = refusal or problem
            else:
                if refusal:
                    raise refusal
        placed = bool(c.execute("SELECT 1 FROM assignments WHERE bot=?", (slug,)).fetchone())
        if placed and H.bot(c, slug)["state"] != "active":
            self.admin.update_bot(c, who, slug, M.BotDefinitionUpdate(
                status="active", expected_revision=self.admin._config(c, slug)["revision"]))
        self._seed_routines(c, who, slug, template)
        if slug == GOAL_MANAGER:
            G.arm_pass(c)                                # KPIs already exist: its daily pass may start
        H.event(c, who.actor, event, slug, {"restored": restored, "placed": placed})
        return {"bot": slug, "state": H.bot(c, slug)["state"], "restored": restored, "placed": placed}

    @staticmethod
    def _recorded_repo(c, slug):
        """A restored bot keeps the repository its record names (`emp-<slug>` for one made before `bot-<slug>`)."""
        row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (slug,)).fetchone()
        return (row["repo"] if row and row["repo"] else "") or "bot-" + ((H.bot(c, slug) or {}).get("slug") or slug)

    def _template_routines(self, c, template):
        """The template's `routines:` (older: `schedules:`) as validated routines, or [] when it declares none."""
        from clients.routines import validate_schedules
        folder = Path(self.settings.catalog_dir) / template
        try:
            declared = routines_of(yaml.safe_load(manifest_path(folder).read_text()) or {})
            names = display_names(self.settings, load(c))
            return validate_schedules(declared, lambda rel: fill((folder / rel).read_text(), names))
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            return []

    def _seed_routines(self, c, who, slug, template):
        """The template's `routines:` (older: `schedules:`) become the bot's first routines, once. A routine a person
        changed or deleted is never put back: only a key the bot has never had is created. (A bot
        BotOps builds gets these from `hub bot create`; a built-in one is made here.)"""
        made = []
        for entry in self._template_routines(c, template):
            if c.execute("SELECT 1 FROM schedules WHERE bot=? AND routine_key=?", (slug, entry["id"])).fetchone():
                continue
            routines.create(c, who.actor, slug, {"title": entry["title"], "text": entry["instructions"],
                                                 "cron": entry["cron"], "on": entry["on"],
                                                 "timezone": entry["timezone"],
                                                 "enabled": entry["enabled"]}, key=entry["id"])
            made.append(entry["id"])
        return made

    def start_setup(self, c, who, slug):
        """A person who manages a parked starter says anything to it: its setup has begun, so its first routine
        goes on (`arm_first_routine`). Anyone else's message to it changes nothing."""
        slug = (H.resolve_bot(c, slug) or {}).get("id", slug)
        row = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if (row and statuses.is_parked(row["onboarding_state"]) and who.role in ("owner", "human")
                and (self.auth.bot_manager(c, who, slug) or self.auth.operator(c, who, slug))):
            return self.arm_first_routine(c, who, slug)
        return None

    def arm_first_routine(self, c, who, slug):
        """Setting a bot up turns its first routine on: the template seeds it off, and starting the setup
        (Start setup, go-live) is the go-ahead, so nobody approves it separately. Once only: a routine a
        person explicitly turns off before or after activation stays off. Returns the routine's key, or None."""
        declared = self._declared(c, slug)
        if declared.get("routine_armed") or not declared.get("template"):
            return None
        first = next(iter(self._template_routines(c, declared["template"])), None)
        sid = first and c.execute("SELECT id FROM schedules WHERE bot=? AND routine_key=?",
                                  (slug, first["id"])).fetchone()
        # The routine as it is now: its automation once it moved (backend/routines.py `row`).
        found = sid and routines.row(c, sid["id"])
        if not found or found["deleted_at"]:
            return None
        declared["routine_armed"] = first["id"]
        self._write_config(c, slug, declared)
        explicitly_disabled = c.execute("SELECT 1 FROM events WHERE action='routine.updated' AND target=? "
                                        "AND json_extract(detail,'$.enabled')=0 LIMIT 1", (found["id"],)).fetchone()

        if not found["enabled"] and not explicitly_disabled:
            routines.update(c, who.actor, found["id"], {"enabled": True})
            H.event(c, who.actor, "bot.routine_armed", slug, {"routine": first["id"]})
        return first["id"]

    def on_runner_enrolled(self, c, runner_id, operator):
        """Enrolling the owner's Mac after the wizard finishes wires it up too, so the order
        the company happens to do things in does not decide whether its bots ever start."""
        from .shared_bots import place_pending
        branches = place_pending(c, operator, runner_id, self.admin, self.execution, self.settings.github_owner)
        if operator != self.auth.owner_id(c):
            return branches
        record = load(c)
        if not record["completed"]:
            return branches
        self.ensure_librarian(c)              # a company from before it was built in
        self.ensure_goal_manager(c)
        self.ensure_task_manager(c)
        from . import bookkeeper
        bookkeeper.ensure(c, self, self.auth)
        self.ensure_counsel(c)
        placed = self._wire(c, record, "human:" + operator, runner_id)
        self._store(c, record, "human:" + operator)
        return branches + placed

    def _wire(self, c, record, actor, runner_id=None):
        """Hand the unplaced bots to a machine and record which one took them. With several
        enrolled, the newest wins: it is the one the person just set up."""
        if runner_id:
            row = c.execute("SELECT id,label FROM runners WHERE id=? AND revoked_at IS NULL",
                            (runner_id,)).fetchone()
        else:
            row = c.execute("SELECT id,label FROM runners WHERE operator=? AND revoked_at IS NULL "
                            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (self.auth.owner_id(c),)).fetchone()
            if not row:
                # The owner changed after the machine enrolled; a lone online machine is still
                # the one this company's bots belong on.
                online = c.execute("SELECT id,label FROM runners WHERE revoked_at IS NULL AND last_seen_at>?",
                                   (H.shift(H.now(), seconds=-60),)).fetchall()
                row = online[0] if len(online) == 1 else None
                if row:
                    # Only the owner's machines may host every bot, so this one becomes the owner's.
                    c.execute("UPDATE runners SET operator=? WHERE id=?", (self.auth.owner_id(c), row["id"]))
                    H.event(c, actor, "runner.adopted", row["id"], {"operator": self.auth.owner_id(c)})
        if not row:
            self._adopt_cloud(c, actor)
            return []
        placed = self.assign_pending(c, row["id"])
        self._activate_bootstrap(c, actor)
        if placed:
            # Only a machine that actually took bots is the one onboarding wired things to.
            record["assigned_to"] = {"runner_id": row["id"], "label": row["label"]}
            H.event(c, actor, "onboarding.assigned", row["id"], {"bots": placed})
        return placed

    def _adopt_cloud(self, c, actor):
        """Tico Cloud with no computer yet: bots run on cloud machines (shared, or their own by role), made for work,
        and the bootstrap bots are activated on them. Nothing until HQ has listed its sizes (`on_cloud_offered`)."""
        from . import cloud_machines
        if not cloud_machines.offered(c, self.settings):
            return False
        for found in c.execute("SELECT bc.bot FROM bot_config bc JOIN bots b ON b.id=bc.bot LEFT JOIN "
                               "assignments a ON a.bot=bc.bot WHERE a.bot IS NULL AND b.state<>'archived' "
                               "ORDER BY bc.bot").fetchall():
            cloud_machines.adopt(c, found["bot"], actor)
        self._activate_bootstrap(c, actor)
        return True

    def on_cloud_offered(self, c):
        """HQ has just listed its machine sizes (the cloud machines manager's sync). A setup finished before then, with
        no computer, left its bots unplaced and its bootstrap bots planned: they go on cloud machines now, the same way
        as had the sizes been known when it finished."""
        if load(c)["completed"]:
            self._adopt_cloud(c, H.KEEPER)

    def _activate_bootstrap(self, c, actor):
        """Activate the bootstrap bots (the assistant, BotOps) and the starters once a machine hosts
        them. Every other bot is set up by BotOps, and BotOps cannot be handed a task while it is
        planned, so leaving these to a separate click strands the whole setup."""
        cards = {card["template"]: card for card in read_cards(self.settings)}
        who = self.auth.owner_identity(c)
        for row in c.execute("SELECT bc.bot,bc.revision,bc.config FROM bot_config bc JOIN bots b ON b.id=bc.bot "
                             "WHERE b.state='planned' AND (EXISTS(SELECT 1 FROM assignments a WHERE a.bot=bc.bot) OR "
                             "EXISTS(SELECT 1 FROM cloud_machines m WHERE m.bot=bc.bot AND m.mode='cloud')) "
                             "ORDER BY bc.bot").fetchall():
            declared = _json(row["config"], {}) or {}
            # A starter is parked (`needs_onboarding`), so activating it lets it answer a person and nothing else.
            if not ((cards.get(declared.get("template")) or {}).get("bootstrap") or declared.get("materialize")):
                continue
            self.admin.update_bot(c, who, row["bot"], M.BotDefinitionUpdate(
                status="active", expected_revision=row["revision"]))
            H.event(c, actor, "onboarding.activated", row["bot"], {})

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _store(c, record, actor):
        record = {**record, "updated_at": H.now(), "updated_by": actor}
        # The document holds `updated`, as it always has; only what the code hands out calls it `updated_at`.
        stored = {**{k: v for k, v in record.items() if k != "updated_at"}, "updated": record["updated_at"]}
        c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                  "value=excluded.value", (KEY, encode(stored)))
        return record

    def _plan(self, record, cards, names):
        """What onboarding builds: the templates the product requires first (BotOps), so it exists
        before anything asks it for work, then everything the person picked. The assistant is a
        pick like any other; its card slug is the environment's assistant bot."""
        plan = {}
        for card in sorted(cards.values(), key=lambda card: card["slug"]):
            if not card["required"]:
                continue
            card = render(card, names)
            plan[self._bot_slug(card["slug"])] = {"template": card["template"], "display_name": card["name"],
                                                  "instructions": card["instructions"]}
        for slug, choice in record["selected"].items():
            plan[self._bot_slug(slug)] = dict(choice)
        return plan

    def _bot_slug(self, card_slug):
        return self.settings.assistant_bot if card_slug == ASSISTANT_TEMPLATE_SLUG else card_slug

    @staticmethod
    def _declared(c, slug):
        row = c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if not row:
            raise Problem("not_found", "Bot not found: " + str(slug), 404)
        return _json(row["config"], {}) or {}

    @staticmethod
    def _write_config(c, slug, declared):
        c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(declared), slug))

    def _runtime(self, c, card):
        """The model a bot built from this card runs on: the card's own pick when it names a
        current model of an enabled provider, else the company default, else the first enabled
        provider's recommended model. With no provider chosen it is None, never a vendor: the
        bot follows the company default and runs once one is chosen."""
        company = providers.load(c, self.settings)
        named = self.models.get(str(card.get("model") or ""))
        own = {}
        if (named and not named.get("deprecated") and named.get("provider") in company["enabled"]):
            own = {"model": named["id"], "runtime": named["runtime"]}
        try:
            runtime, model = providers.resolve(company, own, what="Onboarding")
        except providers.NoProvider:
            return None
        choice = self.models[model]
        effort = str(card.get("reasoning_effort") or "").strip().lower()
        return choice["id"], (effort if effort in tuple(choice.get("efforts") or ())
                              else choice.get("default_effort") or "")

    def _define(self, c, who, slug, choice, card, reports_to=""):
        """Create the bot, or bring an existing definition up to the chosen name. A bot that
        is already running is never demoted back to planned."""
        summary = str(card.get("summary") or "")
        if slug == GOAL_MANAGER:
            # "Strategy Bot", or the owner's name for it: never back
            choice = {**choice, "display_name": strategy_bot.name(c)}
        found = H.resolve_bot(c, slug)
        bot_id = (found or {}).get("id", slug)
        existing = c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot_id,)).fetchone()
        if not existing:
            picked = self._runtime(c, card)
            # No provider yet: a stand-in model satisfies the definition, then the bot is left to follow the company default.
            model, effort = picked or next(
                (row["id"], row.get("default_effort") or "") for row in self.models.values()
                if row.get("provider") and not row.get("deprecated"))
            self.admin.create_bot(c, who, M.BotDefinitionCreate(
                slug=slug, display_name=choice["display_name"], description=summary,
                status="planned", repo="bot-" + slug, thread_mode="personal",
                reports_to=reports_to or None, shared=bool(card.get("shared")), template=choice["template"],
                model=model, effort=effort, owners=[H.actor_id(who.actor)]))
            bot_id = H.resolve_bot(c, slug)["id"]          # the id create_bot gave it
            if card.get("session") == "task":
                c.execute("UPDATE bot_config SET config=json_set(config,'$.session','task') WHERE bot=?", (bot_id,))
            if not picked:
                self._follow_default(c, bot_id)
        else:
            before = self.admin.definition(c, bot_id)
            if (before["display_name"], before["description"]) != (choice["display_name"], summary):
                self.admin.update_bot(c, who, bot_id, M.BotDefinitionUpdate(
                    display_name=choice["display_name"], description=summary,
                    expected_revision=existing["revision"]))
        declared = self._declared(c, bot_id)
        declared.update(template=choice["template"], instructions=choice["instructions"],
                        template_version=releases.version())
        self._write_config(c, bot_id, declared)
        return bot_id

    def _follow_default(self, c, slug):
        """Leave a bot's runtime and model unnamed, so it runs on the company's default once one is chosen
        (providers.fill), the way a bot from the registry with none does."""
        declared = self._declared(c, slug)
        for key in ("runtime", "model", "harness", "reasoning_effort"):
            declared.pop(key, None)
        self._write_config(c, slug, declared)
        c.execute("UPDATE bots SET runtime='',model='',effort='' WHERE id=?", (slug,))

    def _setup_task(self, c, who, slug, choice, card, answers):
        """BotOps builds every bot people picked. A bootstrap template (the assistant, BotOps
        itself) is materialized by the machine at first run, so it gets no task."""
        if card.get("bootstrap") or not H.bot(c, BOTOPS) or slug == BOTOPS:
            return None
        declared = self._declared(c, slug)
        if declared.get("setup_task_id"):
            return declared["setup_task_id"]          # completing twice never reopens the work
        title = ("Set up " + choice["display_name"] + " from the " + choice["template"]
                 + " template")
        if choice.get("title_prefix"):
            title = choice["title_prefix"].strip() + " " + title
        from .botops_act import request_task
        task = request_task(c, self.auth, who, title, setup_body((H.bot(c, slug) or {}).get("slug", slug), choice, answers))
        declared["setup_task_id"] = task["id"]
        self._write_config(c, slug, declared)
        return task["id"]
