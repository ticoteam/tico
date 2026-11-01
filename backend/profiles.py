"""Bot profiles: how a bot is woken and what work it takes (quiet task flow, section 5).

`bot_config.profile` is `doer`, `reviewer`, `lead`, `manager`, `responder`, `monitor` or `custom`. A profile is a fixed set of
bot-scope built-in wake rows (rules.PROFILE_RULES) and a few numbers and flags:

    profile    wakes now                               everything else      pulls open tasks  sweeps  takes
    doer       people, hand-offs, asks, results        batched 15 min       yes               yes     any
    reviewer   its review/QA request, verdicts, people note                 review/QA tasks   no      review/QA requests
    lead       people, escalations, approvals, tickets daily digest         no                no      any
               it asked for that ended
    manager    people, work given to it, its timers,   note                 no                no      any
               its exceptions list
    responder  people                                  note                 no                no      none
    monitor    its watcher, people                     note                 yes               no      its watcher's tasks
    custom     as set: the rows are in the rules table

The numbers are the batch window and scope, the sweep gap (least time between two runs of the same sweep) and, for a
lead, the hour of the digest. They are the profile's own and a bot may carry its own values (`profile_options`): the
one a former Token saver bot keeps is a 30-minute window. `pulls`, `sweeps` and `takes` are fixed by the profile
except on `custom`, which reads them from its options. `pulls` limits only what Tico starts by itself when nothing woke
the bot; a task a wake rule woke it for (a lead's assignment) always runs. Nothing here grants a right: a profile
only decides what wakes a bot and which tasks it is given.

`wake_preset` is read from the profile: setting a profile writes the matching `token_saver` or `performance` into
`config.wake_preset` (30 minutes or more is Token saver), which is what the job queue's hold trigger and every
older client reads, and the old `POST /bots/{bot}/wake-preset` keeps working by changing only the profile's numbers.
A bot with no profile (a database from before profiles) runs as a doer on its old preset until `migrate` gives it
one. `custom` is the way out of a profile's fixed rows: its rows are copied into the rules table, marked
`created_by = 'profile'`, where they are edited like any bot rule; a named profile again drops them (after a confirm).
"""

import json
import re
from fnmatch import fnmatchcase
from typing import Literal

from pydantic import Field

from . import hubdb as H
from .models import Contract

DOER, REVIEWER, LEAD, MANAGER, RESPONDER, MONITOR, CUSTOM = (
    "doer", "reviewer", "lead", "manager", "responder", "monitor", "custom")
NAMES = (DOER, REVIEWER, LEAD, MANAGER, RESPONDER, MONITOR, CUSTOM)
MAX_SECONDS = 7 * 86400
TOKEN_SAVER_SECONDS = 30 * 60           # a window this long or longer is what older clients call Token saver
DIGEST_HOUR = 9                         # a lead's digest, in the team's time zone
MATERIALISED = "profile"                # `created_by` of the rows a custom profile copied into the rules table
# Traditional gate requests require a parent; explicit release QA can cover an entire release.
RELEASE_QA_TITLE = re.compile(
    r"^(?:(?:Post[- ](?:Release|Launch)|Release)\s+QA(?:\s+(?:pass|sweep))?|Post[- ]Release\s+checks?)\s*(?::|$)", re.I)
ACCEPTANCE_TITLE = re.compile(r"\blive acceptance\b", re.I)
# The numbers a former Token saver bot keeps.
TOKEN_SAVER_OPTIONS = {"batch_seconds": TOKEN_SAVER_SECONDS, "batch_scope": "bot", "sweep_seconds": 2 * 3600}

BASE = {"batch_seconds": 15 * 60, "batch_scope": "conversation", "sweep_seconds": 15 * 60, "digest_hour": None,
        "pulls": "all", "sweeps": True, "takes": "all"}
SPEC = {
    DOER: {**BASE, "label": "Doer",
           "description": "Works the tasks it is given. News about them is batched; people and hand-offs wake it at once."},
    REVIEWER: {**BASE, "label": "Reviewer", "pulls": "review", "sweeps": False, "takes": "review",
               "description": "Checks other bots' work. Wakes for its review or QA request and for people; "
                              "takes review and QA tasks only."},
    LEAD: {**BASE, "label": "Lead", "pulls": "none", "sweeps": False, "digest_hour": DIGEST_HOUR, "batch_scope": "bot",
           "description": "Directs others. Wakes for people, escalations and approvals; the rest is one digest a day."},
    # A type's manager that works by exception (backend/pipeline_exceptions.py): ticket news is a note for its next run.
    MANAGER: {**BASE, "label": "Manager", "pulls": "none", "sweeps": False, "batch_scope": "bot",
              "description": "Works from one exceptions list, sent about hourly when it has a new entry. Wakes for people, "
                             "work given to it and its own timers; ticket news, verdicts and subtask results wait for "
                             "its next run."},
    RESPONDER: {**BASE, "label": "Responder", "pulls": "none", "sweeps": False, "takes": "none",
                "description": "Answers people in chat. Wakes for people only and takes no tasks."},
    MONITOR: {**BASE, "label": "Monitor", "sweeps": False, "takes": "watcher",
              "description": "Watches something. Wakes for its watcher and for people; takes its watcher's tasks only."},
    CUSTOM: {**BASE, "label": "Custom",
             "description": "Its own wake rules, edited on the Rules page, and its own settings for what it pulls, "
                            "sweeps and takes."},
}
NUMBER_KEYS = ("batch_seconds", "batch_scope", "sweep_seconds", "digest_hour")
FLAG_KEYS = ("pulls", "sweeps", "takes")
CHOICES = {"pulls": ("all", "review", "none"), "takes": ("all", "review", "watcher", "none"), "batch_scope": ("bot", "conversation")}


# ---------------------------------------------------------------------------------------------------- reading
# SQL for "this bot's profile takes no task", on a bot_config row aliased `{a}`: a routine of such a bot never fires.
TAKES_NONE_SQL = ("(json_extract({a}.config,'$.profile')='responder' OR (json_extract({a}.config,'$.profile')="
                  "'custom' AND json_extract({a}.config,'$.profile_options.takes')='none'))")


def name_of(config):
    """The profile this config names, or None before it has one."""
    name = (config or {}).get("profile") if isinstance(config, dict) else None
    return name if name in NAMES else None


def _valid(key, value):
    if key in ("batch_seconds", "sweep_seconds"):
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SECONDS
    if key == "digest_hour":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23
    if key == "sweeps":
        return isinstance(value, bool)
    return value in CHOICES[key]


def options_of(config):
    """The bot's own values over its profile's: only the keys that profile reads and that are valid."""
    name = name_of(config)
    raw = config.get("profile_options") if name and isinstance(config.get("profile_options"), dict) else {}
    allowed = tuple(k for k in NUMBER_KEYS if k != "digest_hour" or name in (LEAD, CUSTOM)) + (FLAG_KEYS if name == CUSTOM else ())
    return {k: v for k, v in raw.items() if k in allowed and _valid(k, v)}


def spec_of(config):
    """The profile's numbers and flags with the bot's own values on top, or None for a bot with no profile."""
    name = name_of(config)
    if not name:
        return None
    return {**SPEC[name], **options_of(config)}


def numbers(config):
    """{batch_seconds, batch_scope, sweep_seconds, digest_hour} for a bot with a profile, else None (the old preset's
    values then stand: backend/wake_presets.py)."""
    spec = spec_of(config)
    return {k: spec[k] for k in NUMBER_KEYS} if spec else None


def legacy_preset(config):
    """The `token_saver` or `performance` an older client sees for this config."""
    spec = spec_of(config)
    if spec is None:
        return None
    return "token_saver" if spec["batch_seconds"] >= TOKEN_SAVER_SECONDS else "performance"


def effective(config):
    """What the bot's settings and the API show: the profile, its words and the values it runs on."""
    from . import wake_presets
    name, spec = name_of(config), spec_of(config)
    chosen = name is not None
    if not chosen:
        old = wake_presets.values()[wake_presets.preset_of(config)]
        spec = {**SPEC[DOER], "batch_seconds": old["batch_seconds"], "batch_scope": old["batch_scope"],
                "sweep_seconds": old["sweep_seconds"]}
    shown = name or DOER
    out = {"name": shown, "label": SPEC[shown]["label"], "description": SPEC[shown]["description"], "chosen": chosen,
           **{k: spec[k] for k in (*NUMBER_KEYS, *FLAG_KEYS)}, "options": options_of(config),
           "summary": summary(shown, spec)}
    if shown != LEAD and shown != CUSTOM:
        out["digest_hour"] = None
    return out


def summary(name, spec):
    """One line saying what the numbers mean for this profile."""
    from .wake_presets import _duration
    sweep = f"; a stall check or routine runs at most every {_duration(spec['sweep_seconds'])}" if spec["sweeps"] else ""
    if name == DOER or (name == CUSTOM and spec["sweeps"]):
        return f"News about its tasks waits up to {_duration(spec['batch_seconds'])}{sweep}."
    if name == CUSTOM and spec["digest_hour"] is not None:     # a Lead's description and hour picker say it already
        hour = spec["digest_hour"]
        return f"The rest of the news arrives once a day, at {hour:02d}:00 team time."
    return ""


def choices():
    """Every profile with the rows it adds, for a picker: one line of description each."""
    from . import rules
    out = []
    for name in NAMES:
        spec = SPEC[name]
        out.append({"name": name, "label": spec["label"], "description": spec["description"],
                    **{k: spec[k] for k in (*NUMBER_KEYS, *FLAG_KEYS)},
                    "rules": [{"id": r["id"], "event": r["event"], "when": r["when"], "then": r["then_outcome"],
                               "words": r["words"]} for r in rules.PROFILE_RULES.get(name, ())]})
    return out


def of_bot(c, bot):
    """The bot's profile name as the engine reads it (doer when it has none)."""
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return name_of(H._json(row[0], {}) if row else {}) or DOER


def config_of(c, bot):
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return H._json(row[0], {}) or {} if row else {}


def sweeps_on(c, bot):
    """Whether a stall or status check may wake this bot's tasks (rules.sweeps_on)."""
    spec = spec_of(config_of(c, bot))
    return True if spec is None else bool(spec["sweeps"])


# ---------------------------------------------------------------------------------------------------- the tasks it takes
def is_review_task(c, row, *, parent_id=None):
    """One classification for creation, assignment, pull, wakes and Health.

    Explicit release QA may be top-level. Gate and acceptance requests need a parent;
    an untitled delegated child also qualifies while its parent is in a review/QA step.
    `parent_id` lets creation check the same rule before the child row exists.
    """
    from . import auto_tasks
    title = str(row.get("title") or "").strip()
    if RELEASE_QA_TITLE.match(title):
        return True
    parent_id = parent_id or (H.TR.parent_of(c, row["id"]) if row.get("id") else None)
    if not parent_id:
        return False
    return bool(_check_title(c, H.task(c, parent_id), title) or ACCEPTANCE_TITLE.search(title)
                or auto_tasks.review(c, row, parent_id=parent_id))


def _check_title(c, parent, title):
    """A request titled for one of the parent board's check steps (backend/step_flow.titled), or a Security check."""
    from . import step_flow
    from .check_pools import is_security
    return bool(parent) and (is_security(title) or step_flow.is_kind(c, parent, title))


def is_check(c, row):
    """A check that finishes only with a verdict: titled for a check step of its ticket's board (Review 1/2, QA or Post
    Release QA on the Dev board), or Security, under a ticket another owner
    holds, or a bot's own check run on another model (H.check_model). A builder's own child titled "QA: add fixtures"
    and other work handed out while its ticket sits in a review step are ordinary tasks, and so is a person's own
    child whatever its title: "Security: confirm the vendor contract" finishes when the person finishes it."""
    owner = str(row.get("owner") or "")
    if H.is_bot(owner) and H.check_model(c, row["id"], owner[len("bot:"):]):
        return True
    if H.is_human(owner):
        return False
    parent = H.task(c, H.TR.parent_of(c, row["id"]) or "")
    title = str(row.get("title") or "").strip()
    return bool(parent and parent["owner"] != owner and
                (_check_title(c, parent, title) or RELEASE_QA_TITLE.match(title)))


def refusal(c, name, config, actor, title=None, parent=None, routine=False, *, owner=None):
    """(rule, why): the refusal rule and the words saying what to do instead, when the bot's profile means it takes no
    such task; None when it does. A Responder is refused like a synced bot (`external-bot`).
    A task its watcher (Tico's own actor) opens or a routine runs is its own work; a Responder takes no task at
    all. `parent` is the id of the ticket the task is filed under."""
    spec = spec_of(config)
    if spec is None or spec["takes"] == "all":
        return None
    takes = spec["takes"]
    if takes == "none":
        return ("external-bot", f"{name} answers people in chat and takes no tasks (its profile is Responder), so it never "
                "sees tasks. Write to it in its chat, or give this task to a bot that works tasks.")
    if actor == H.KEEPER or routine:
        return None
    if takes == "review":
        if is_review_task(c, {"title": title, "owner": owner}, parent_id=parent):
            return None
        return ("profile", f"{name} is a reviewer: it takes review and QA requests only: explicit release QA, "
                "or review, QA and acceptance subtasks under the ticket they check. File this that way, or give the task to a "
                f"bot that does the work. If {name} should do ordinary tasks, change its profile.")
    return ("profile", f"{name} is a monitor: its watcher files its tasks. Give this task to a bot that does the work, "
            f"or change {name}'s profile.")


def pull_refusal(c, row, config):
    """'auto_start_off' when the owner's profile does not pull this open task by itself, else None. Only
    auto_tasks.missing (the idle claim's own start) asks: a job a wake rule queued is claimed and started whatever the
    profile pulls (auto_tasks.refusal with `woken`)."""
    spec = spec_of(config)
    if spec is None or spec["pulls"] == "all":
        return None
    if spec["pulls"] == "review" and is_review_task(c, row):
        return None
    return "auto_start_off"


# ---------------------------------------------------------------------------------------------------- changing it
class BotProfile(Contract):
    """Set a bot's profile. A named profile takes its own values (send only the numbers to change); the numbers
    set again each time. `pulls`, `sweeps` and `takes` are for `custom`, whose rows are edited on the Rules page.
    Leaving `custom` for a named profile drops the rows it copied into the rules table: `confirm_drop_rules`."""
    profile: Literal["doer", "reviewer", "lead", "manager", "responder", "monitor", "custom"]
    expected_revision: int = Field(ge=1)
    batch_seconds: int | None = Field(default=None, ge=0, le=MAX_SECONDS)
    batch_scope: Literal["bot", "conversation"] | None = None
    sweep_seconds: int | None = Field(default=None, ge=0, le=MAX_SECONDS)
    digest_hour: int | None = Field(default=None, ge=0, le=23)
    pulls: Literal["all", "review", "none"] | None = None
    sweeps: bool | None = None
    takes: Literal["all", "review", "watcher", "none"] | None = None
    confirm_drop_rules: bool = False


def _given(body):
    return {k: getattr(body, k) for k in (*NUMBER_KEYS, *FLAG_KEYS) if getattr(body, k, None) is not None}


def apply(declared, name, options):
    """Write the profile and its options into a config dict, with the `wake_preset` older readers use."""
    declared["profile"] = name
    if options:
        declared["profile_options"] = options
    else:
        declared.pop("profile_options", None)
    declared["wake_preset"] = legacy_preset(declared)
    return declared


def apply_preset(declared, preset):
    """The old wake-preset setting, on a bot with a profile: only its numbers change, never the profile."""
    options = {k: v for k, v in (declared.get("profile_options") or {}).items()
               if k not in TOKEN_SAVER_OPTIONS}
    if preset == "token_saver":
        options.update(TOKEN_SAVER_OPTIONS)
    return apply(declared, name_of(declared), options)


def snapshot(config):
    """What a settings change records and Undo restores."""
    return {"profile": name_of(config), "options": options_of(config)}


def materialised(c, bot):
    """How many of a profile's rules this bot has of its own: rows still in the rules table, and the ones in its "What
    wakes" automation."""
    from . import redesign_migration
    return c.execute("SELECT count(*) FROM rules WHERE scope_kind='bot' AND scope_id=? AND created_by=?",
                     (bot, MATERIALISED)).fetchone()[0] + redesign_migration.wake_rules_by(c, "bot", bot, MATERIALISED)


def materialise(c, bot, source):
    """Copy a profile's rows into the rules table for this bot, to be edited as any bot rule. Returns how many."""
    from . import rules
    from .store import Problem
    found = rules.PROFILE_RULES.get(source, ())
    if not found:
        return 0
    if c.execute("SELECT count(*) FROM rules").fetchone()[0] + len(found) > rules.MAX_ROWS:
        raise Problem("conflict", "The rule list is full; delete a rule before this bot can have its own", 409)
    start = (c.execute("SELECT max(position) FROM rules WHERE scope_kind='bot' AND scope_id=?", (bot,)).fetchone()[0]
             or 0) + 1
    at = H.now()
    for n, row in enumerate(found):
        c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,position,"
                  "note,created_by,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?,1)",
                  ("rule-" + H.new_id(), "bot", bot, row["event"], row["condition"], row["then_outcome"],
                   row["params"], start + n, f"From the {SPEC[source]['label']} profile: {row['words']}"[:500],
                   MATERIALISED, at, at))
    from . import redesign_migration
    redesign_migration.adopt(c, "wake_rules", ("bot", bot))     # after the cutover: in its What wakes automation
    return len(found)


def drop_materialised(c, bot):
    from . import redesign_migration
    c.execute("DELETE FROM rules WHERE scope_kind='bot' AND scope_id=? AND created_by=?", (bot, MATERIALISED))
    redesign_migration.drop_wake_rules(c, "bot", bot, MATERIALISED)


def set_profile(admin, c, who, bot, body, undo_change_id=None):
    """Set the bot's profile (SettingsAdmin's pattern: Strategy Bot gate, manager check, revision check, audit row,
    undo)."""
    from . import strategy_acts as CA
    from .store import Problem, encode
    CA.gate(c, who.actor, "cost", f"change how {bot} wakes and what tasks it takes")
    admin._manager(c, who, bot, botops=True)
    config = admin._config(c, bot)
    if config["revision"] != body.expected_revision:
        raise Problem("version_conflict", "Bot configuration changed; refresh before saving", 409)
    declared = H._json(config["config"], {}) or {}
    before, prior = snapshot(declared), name_of(declared)
    name, given = body.profile, _given(body)
    custom_only = [k for k in FLAG_KEYS if k in given]
    if name != CUSTOM and custom_only:
        raise Problem("invalid", f"{', '.join(custom_only)} can be set on the Custom profile only", 422)
    if given.get("digest_hour") is not None and name not in (LEAD, CUSTOM):
        raise Problem("invalid", "digest_hour is for the Lead and Custom profiles", 422)
    dropped = 0
    if prior == CUSTOM and name != CUSTOM:
        dropped = materialised(c, bot)
        if dropped and not body.confirm_drop_rules:
            raise Problem("conflict", f"{bot} has {dropped} wake rule{'s' if dropped != 1 else ''} of its own from the "
                          "Custom profile; they are deleted when it changes profile. Send confirm_drop_rules to go on", 409)
        drop_materialised(c, bot)
    if name == CUSTOM:
        if prior == CUSTOM:
            options = {**options_of(declared), **given}
        else:
            # Start from what the bot ran on: the profile's numbers and flags, its rows copied for editing.
            source = prior or DOER
            options = {k: v for k, v in {**SPEC[source], **options_of(declared)}.items() if k in (*NUMBER_KEYS, *FLAG_KEYS)
                       and v is not None and v != BASE[k]}
            options.update(given)
            materialise(c, bot, source)
    else:
        options = given
    apply(declared, name, {k: v for k, v in options.items() if _valid(k, v)})
    after = snapshot(declared)
    c.execute("UPDATE bot_config SET config=?,revision=revision+1 WHERE bot=?", (encode(declared), bot))
    from . import wake_presets
    wake_presets.replan(c, bot)
    admin.record(c, who.actor, bot, "profile", before, after)
    if undo_change_id:
        c.execute("UPDATE settings_changes SET undone_by=?,undone_at=? WHERE id=? AND undone_at IS NULL",
                  (who.actor, H.now(), undo_change_id))
    H.event(c, who.actor, "bot.profile_changed", bot, {"before": before, "after": after, "dropped_rules": dropped})
    return view(c, bot, {"config": encode(declared), "revision": config["revision"] + 1})


def view(c, bot, config):
    declared = H._json(config["config"], {}) or {}
    return {"bot": bot, "profile": effective(declared), "wake": _wake(declared), "rules": materialised(c, bot),
            "revision": config["revision"]}


def _wake(declared):
    from . import wake_presets
    return wake_presets.effective(declared)


# ---------------------------------------------------------------------------------------------------- the upgrade
CUSTOM_SLUGS = ("botops", "health-monitor", "task-manager")
RESPONDER_SLUGS = ("assistant",)
MONITOR_SLUGS = ("*-monitor", "*-watch", "listening", "sentry-triage")
REVIEWER_SLUGS = ("*reviewer*", "qa-*", "security*", "pr-reviewer-sol", "reviewer-*")
LEAD_SLUGS = ("engineering-lead", "product-lead", "marketing-lead", "support-lead", "cmo", "cro", "coo", "cto", "cpo",
              "*-manager")
# The Librarian answers questions asked through `hub doc ask`, which are not tasks, but its nightly routines are: it
# becomes a Custom profile that wakes on its own timers and takes the tasks they open.
LIBRARIAN = "librarian"


def default_for(c, slug, declared):
    """The profile the upgrade gives a bot, by what it is called and what it is: (profile, options, copied-from)."""
    from .harnesses import SYNCED_HARNESSES, resolve_harness
    preset = TOKEN_SAVER_OPTIONS if declared.get("wake_preset") == "token_saver" else {}
    if slug in CUSTOM_SLUGS:
        return CUSTOM, {}, DOER
    if slug == LIBRARIAN:
        return CUSTOM, {"takes": "all", "pulls": "none", "sweeps": False}, MONITOR
    if slug in RESPONDER_SLUGS or resolve_harness(declared) in SYNCED_HARNESSES:
        return RESPONDER, {}, None
    if any(fnmatchcase(slug, p) for p in MONITOR_SLUGS):
        return MONITOR, {}, None
    if any(fnmatchcase(slug, p) for p in REVIEWER_SLUGS):
        # The Builder is called pr-reviewer in the starter factory and owns the Dev tickets: it stays a doer.
        if slug == "pr-reviewer" and _owns_dev_tickets(c, slug):
            return DOER, preset, None
        return REVIEWER, {}, None
    if any(fnmatchcase(slug, p) for p in LEAD_SLUGS):
        return LEAD, {}, None
    return DOER, preset, None


def _owns_dev_tickets(c, slug):
    """Whether the bot owns a task of the engineering board's type (Dev tickets, found by role or name)."""
    for (type_id,) in c.execute("SELECT DISTINCT type_id FROM tasks WHERE owner=?", ("bot:" + slug,)).fetchall():
        kind = H.type_get(c, type_id) or {}
        if kind.get("role") == "engineering" or kind.get("name") == "Dev tickets":
            return True
    return False


def migrate(c):
    """Give every bot with no profile one, by its name (`default_for`), and tell each owner once, in one notice that
    names every bot of theirs and its profile. Runs at each start and touches only bots with no profile, so a bot is
    migrated once, whatever its owner changes afterwards."""
    from . import rules
    from .store import encode
    told = {}
    for row in c.execute("SELECT bc.bot,bc.config,bc.operator,bc.bot_owners,b.state,b.display_name FROM bot_config bc "
                         "JOIN bots b ON b.slug=bc.bot ORDER BY bc.bot").fetchall():
        declared = H._json(row["config"], {}) or {}
        if not isinstance(declared, dict) or name_of(declared):
            continue
        slug = row["bot"]
        try:
            name, options, source = default_for(c, slug, declared)
        except Exception:  # noqa: BLE001 - a start never fails on a guess about one bot: it is a Doer
            name, options, source = DOER, {}, None
        apply(declared, name, options)
        c.execute("UPDATE bot_config SET config=?,revision=revision+1 WHERE bot=?", (encode(declared), slug))
        if name == CUSTOM and source and rules.PROFILE_RULES.get(source):
            materialise(c, slug, source)
        H.event(c, H.KEEPER, "bot.profile_migrated", slug, {"profile": name, "options": options})
        if row["state"] == "archived":
            continue
        owner = row["operator"] or next(iter(_owner_ids(row["bot_owners"])), "") or H.default_human(c)
        if owner and H.human(c, owner):
            told.setdefault(owner, []).append((row["display_name"] or slug, slug, name))
    for owner, bots in told.items():
        lines = [f"- {label} ({slug}): {SPEC[name]['label']}" for label, slug, name in bots[:40]]
        more = f"\n...and {len(bots) - 40} more." if len(bots) > 40 else ""
        H.notice(c, H.KEEPER, H.human_actor(owner),
                 "Tico gave each of your bots a profile, which decides what wakes it and what tasks it takes:\n"
                 + "\n".join(lines) + more + "\nChange one in Settings > Bots > Edit, or with `hub bot profile <bot> <name>`.")
    return sum(len(v) for v in told.values())


def _owner_ids(raw):
    from . import bot_access as BA
    return BA.owner_ids(raw)


# ---------------------------------------------------------------------------------------------------- Health
IDLE_DAYS = 7
IDLE_MIN_RUNS = 3
OPEN = "(" + ",".join(f"'{s}'" for s in H.ACTIVE_STATUSES) + ")"


def mismatches(c):
    """Profiles that look wrong, for the Health Monitor: a reviewer holding tasks that are not reviews, a responder
    with tasks queued, a doer whose runs for a week all did nothing. [(kind, bot, count)]."""
    out = []
    since = H.shift(H.now(), days=-IDLE_DAYS)
    for row in c.execute("SELECT bc.bot,bc.config FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                         "WHERE b.state='active' ORDER BY bc.bot").fetchall():
        declared = H._json(row["config"], {}) or {}
        name, bot = name_of(declared), row["bot"]
        if name == REVIEWER:
            tasks = H._rows(c.execute(f"SELECT * FROM tasks WHERE owner=? AND status IN {OPEN} LIMIT 200",
                                      ("bot:" + bot,)))
            other = sum(1 for t in tasks if not is_review_task(c, t))
            if other:
                out.append(("reviewer_holds_tasks", bot, other))
        elif name == RESPONDER:
            n = c.execute("SELECT count(*) FROM tasks WHERE owner=? AND status IN ('open','doing')",
                          ("bot:" + bot,)).fetchone()[0]
            if n:
                out.append(("responder_has_tasks", bot, n))
        elif name == DOER:
            seen = c.execute("SELECT count(*) AS n, sum(coalesce(did_nothing,0)=1) AS idle FROM turns WHERE bot=? "
                             "AND started_at>? AND finished_at IS NOT NULL", (bot, since)).fetchone()
            created = c.execute("SELECT created_at FROM bots WHERE slug=?", (bot,)).fetchone()
            if (seen["n"] >= IDLE_MIN_RUNS and seen["idle"] == seen["n"]
                    and not (created and created[0] and created[0] > since)):
                out.append(("doer_idle", bot, seen["n"]))
    return out


# ---------------------------------------------------------------------------------------------------- the routes
def install(app, store, auth, mutate, settings_admin):
    from fastapi import Request
    from .store import Problem

    def seeing(c, who, bot):
        auth.domain(who)
        if not H.bot(c, bot):
            raise Problem("not_found", "Bot not found", 404)
        auth.require_see(c, who, bot)

    @app.get("/api/v2/profiles")
    def profile_choices(request: Request):
        """Every profile with its one-line description, its values and the wake rows it adds, for a picker."""
        auth.domain(request.state.identity)
        return {"profiles": choices()}

    @app.get("/api/v2/bots/{bot}/profile")
    def bot_profile(request: Request, bot: str):
        """The bot's profile and the values it runs on."""
        who = request.state.identity
        with store.read() as c:
            seeing(c, who, bot)
            return view(c, bot, settings_admin._config(c, bot))

    @app.post("/api/v2/bots/{bot}/profile")
    def bot_profile_set(request: Request, bot: str, body: BotProfile):
        """Set the bot's profile; the revision you read must still be current (409 version_conflict otherwise)."""
        who = request.state.identity

        def work(c):
            seeing(c, who, bot)
            return set_profile(settings_admin, c, who, bot, body)
        return mutate(request, body, work)
