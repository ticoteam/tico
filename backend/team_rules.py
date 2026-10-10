"""Team rules: what is open by default and the owner can tighten (Settings > Humans).

One record, `rules`, holds only what the owner changed. Each rule is on by default, the fast way; turning it off puts
back the click or the limit the product had before. They are read wherever the decision is made: the Assistant's
direct writes (backend/assistant.py), what BotOps does without a card (backend/botops_act.py), the bots BotOps
manages in its own runs and who is a credential administrator (backend/auth.py), the SQL page and personal tokens.
"""
import json

from .store import H

KEY = "rules"
DEFAULTS = {
    "assistant_direct": True,       # the Assistant makes tasks for bots, comments and messages to bots without a card
    "botops_direct": True,          # BotOps sets AI providers and raises limits without a card
    "botops_manages_bots": True,    # BotOps, in its own runs, manages every bot that is not built in (Auth.botops_manages)
    "admin_credentials": True,      # Admins store credentials, not only the owner
    "admin_sql": True,              # Admins see the SQL page
    "member_tokens": True,          # members make personal tokens
}


def load(c):
    """Every rule with its value: the stored choice, else its default."""
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    return {name: bool(stored[name]) if name in stored else default for name, default in DEFAULTS.items()}


def save(c, actor, changes):
    """Set the rules named in `changes`; returns all of them."""
    before = load(c)
    after = {**before, **{name: bool(value) for name, value in changes.items() if name in DEFAULTS and value is not None}}
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, json.dumps(after, sort_keys=True)))
    if after != before:
        H.event(c, actor, "access.rules_updated", "", {"before": before, "after": after})
    return after
