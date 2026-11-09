"""One send policy: each bot's old "send without approval" switch (bot_mail_settings) folds into its layer of the
sending policy (email_sending_policies, backend/email_sending.py), and the table goes.

Nothing here widens what a bot may send:

  bot_mail_settings.outbound_send = 0, bot layer mode on_their_own or unset  ->  bot layer mode ask_first
  bot_mail_settings.outbound_send = 0, bot layer mode ask_first or never     ->  unchanged
  bot_mail_settings.outbound_send = 1                                         ->  unchanged (the layer already says it,
                                                                                  or the bot inherits Ask first)
  bot_mail_settings.forward_to                                                ->  dropped: a forward is a send, decided
                                                                                  and approved like any other
  a bot layer's `legacy` and `legacy_allowances` (the pre-cutover exception)  ->  dropped: they only lifted asks

The legacy daily cap (20, or the registry's) stays on the layer as an ordinary bot cap. org_rule_misses (mail checks
the connector reported unanswered) goes too: the send queue checks a bot's mail itself.
"""

import json
from datetime import datetime, timezone

STRICTER = {"on_their_own": "ask_first"}


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.execute("DROP TABLE IF EXISTS org_rule_misses")
    if "email_sending_policies" not in tables:
        conn.execute("DROP TABLE IF EXISTS bot_mail_settings")
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    layers = {scope: json.loads(settings or "{}") for scope, settings in
              conn.execute("SELECT scope, settings FROM email_sending_policies WHERE scope LIKE 'bot:%'")}
    changed = set()
    for scope, settings in layers.items():
        if "legacy" in settings or "legacy_allowances" in settings:
            settings.pop("legacy", None)
            settings.pop("legacy_allowances", None)
            changed.add(scope)
    if "bot_mail_settings" in tables:
        for bot, on in conn.execute("SELECT bot, outbound_send FROM bot_mail_settings").fetchall():
            if on:
                continue
            scope = "bot:" + str(bot)
            settings = layers.setdefault(scope, {})
            mode = settings.get("mode", "on_their_own")
            if mode in STRICTER:
                settings["mode"] = STRICTER[mode]
                changed.add(scope)
    for scope in sorted(changed):
        conn.execute("INSERT INTO email_sending_policies(scope, settings, updated_at, updated_by) VALUES(?,?,?,?) "
                     "ON CONFLICT(scope) DO UPDATE SET settings=excluded.settings, updated_at=excluded.updated_at, "
                     "updated_by=excluded.updated_by", (scope, json.dumps(layers[scope], sort_keys=True), now,
                                                        "migration"))
    conn.execute("DROP TABLE IF EXISTS bot_mail_settings")
