"""Team rules: what is open by default and the owner can tighten (Settings > Humans).

One record, `rules`, holds only what the owner changed. Each rule is on by default, the fast way; turning it off puts
back the click or the limit the product had before. They are read wherever the decision is made: the Assistant's
direct writes (backend/assistant.py), what BotOps does without a card (backend/botops_act.py), the bots BotOps
manages in its own runs and who is a credential administrator (backend/auth.py), who stores a credential for their
own bot (backend/credentials.py), the SQL page and personal tokens, whether a bot goes ahead on its own default when
a person leaves its ask unanswered (backend/ask_deadlines.py), and whether Tico support may be let in
(backend/support_access.py).
"""
import json

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
}
# Numbers the owner sets, with their default and bounds.
NUMBERS = {
    "go_ahead_days": (3, 1, 14),    # days an ask with a default waits for a person before the bot goes ahead
}


def load(c):
    """Every rule with its value: the stored choice, else its default."""
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    rules = {name: bool(stored[name]) if name in stored else default for name, default in DEFAULTS.items()}
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
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, json.dumps(after, sort_keys=True)))
    if after != before:
        H.event(c, actor, "access.rules_updated", "", {"before": before, "after": after})
    if before["support_access"] and not after["support_access"]:
        from . import support_access
        support_access.end_all(c, actor, "turned_off")
    return after
