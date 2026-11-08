"""Team rules: what is open by default and the owner can tighten (Settings > Humans).

One record, `rules`, holds only what the owner changed. Each rule is on by default, the fast way; turning it off puts
back the click or the limit the product had before. They are read wherever the decision is made: the Assistant's
direct writes (backend/assistant.py), what BotOps does without a card (backend/botops_act.py), the bots BotOps
manages in its own runs and who is a credential administrator (backend/auth.py), who stores a credential for their
own bot (backend/credentials.py), the SQL page and personal tokens, whether a bot goes ahead on its own default when
a person leaves its ask unanswered (backend/ask_deadlines.py), whether Tico support may be let in
(backend/support_access.py), whether the hourly stale ticket review runs (backend/stale_review.py), and whether a direct
chat starts fresh when the topic changes (backend/topic_change.py), whether Decide reads a quiet task before its stall
check stays silent (backend/stall_triage.py), and whether a bot manages its direct reports, and how many it may add or archive a week (backend/report_managers.py).
The stale review and fresh topics are off by default; those two and
triage an admin may turn too (ADMIN_RULES), and the server's environment may set their default (ENV_DEFAULTS).
"""
import json
import os

from .store import H

KEY = "rules"
DEFAULTS = {
    "assistant_direct": True,       # the Assistant makes tasks (for a person or a bot), comments and messages to bots without a card
    "assistant_approvals": True,    # a person may let their own Assistant decide their approvals (backend/assistant.py)
    "botops_direct": True,          # BotOps sets AI providers and raises limits without a card
    "botops_manages_bots": True,    # BotOps, in its own runs, manages every bot that is not built in (Auth.botops_manages)
    "admin_credentials": True,      # Admins store credentials, not only the owner
    "members_store_credentials": True,  # anyone on the team stores a credential for a bot they manage
    "admin_sql": True,              # Admins see the SQL page
    "member_tokens": True,          # members make personal tokens
    "bots_go_ahead": True,          # an ask with a reversible, internal default goes ahead after `go_ahead_days`
    "health_monitor_reports": True,  # the Health Monitor, once turned on, files Tico bug reports without a card
    "support_access": True,         # an owner or admin may let Tico support in from a ticket (backend/support_access.py)
    "stale_review": False,          # the hourly stale ticket review suggests updates to owners (backend/stale_review.py)
    "topic_fresh": False,           # a direct chat starts fresh when the topic changes (backend/topic_change.py)
    "stall_triage": True,           # Decide reads a quiet task that nothing has changed on before a stall check stays silent (backend/stall_triage.py)
    "managers_manage_reports": True,  # a bot changes its direct reports' model, task model, name and description (backend/report_managers.py)
    "org_rule_checks": True,        # a bot's outputs are checked against the org rules that apply to them (backend/org_rule_check.py)
}
# Rules an admin may change too, not only the owner (Settings > General).
ADMIN_RULES = ("stale_review", "topic_fresh", "stall_triage", "org_rule_checks")
# A rule whose default the server's environment sets (`TICO_STALE_REVIEW=1`, `TICO_TOPIC_FRESH=1` on Tico Team's own
# server, `TICO_STALL_TRIAGE=0` to start a server with triage off). A saved choice wins; until someone sets the rule it is not stored, so it keeps following the environment.
ENV_DEFAULTS = {"stale_review": "TICO_STALE_REVIEW", "topic_fresh": "TICO_TOPIC_FRESH",
                "stall_triage": "TICO_STALL_TRIAGE"}
# Numbers the owner sets, with their default and bounds.
NUMBERS = {
    "go_ahead_days": (3, 1, 14),    # days an ask with a default waits for a person before the bot goes ahead
    # Most reports a manager bot may add, and most it may archive or restore, each rolling week, through BotOps
    # (backend/report_managers.py). 0 turns that off.
    "report_creates_per_week": (3, 0, 50),
    "report_archives_per_week": (3, 0, 50),
}


def default_of(name):
    """A rule's default: DEFAULTS, or the server's environment for a rule in ENV_DEFAULTS that sets it."""
    value = os.environ.get(ENV_DEFAULTS.get(name, ""), "").strip()
    return value == "1" if value in ("0", "1") else DEFAULTS[name]


def _stored(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def load(c):
    """Every rule with its value: the stored choice, else its default."""
    stored = _stored(c)
    rules = {name: bool(stored[name]) if name in stored else default_of(name) for name in DEFAULTS}
    for name, (default, low, high) in NUMBERS.items():
        value = stored.get(name)
        rules[name] = value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else default
    return rules


def save(c, actor, changes):
    """Set the rules named in `changes`; returns all of them."""
    before = load(c)
    after = {**before, **{name: bool(value) for name, value in changes.items() if name in DEFAULTS and value is not None}}
    for name, (_, low, high) in NUMBERS.items():
        value = changes.get(name)
        if value is not None:
            after[name] = min(high, max(low, int(value)))
    stored = _stored(c)
    kept = {name: value for name, value in after.items()
            if name not in ENV_DEFAULTS or name in stored or changes.get(name) is not None}
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(kept, sort_keys=True)))
    if after != before:
        H.event(c, actor, "access.rules_updated", "", {"before": before, "after": after})
    if before["support_access"] and not after["support_access"]:
        from . import support_access
        support_access.end_all(c, actor, "turned_off")
    return after
