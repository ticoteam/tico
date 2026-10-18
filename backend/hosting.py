"""Tico-hosted: a server Tico runs, for its own team (`TICO_HOSTED=own`) or for an invited company (`invited`), set
only by the hosting preset (`Settings.hosted`, backend/config.py). Unset is a server its company runs itself, and
nothing here changes anything there.

On a hosted server Tico never keeps a customer's Claude sign-in token. Anthropic lets a platform host Claude Code with
each person signing in with their own subscription, but the host "may not collect, store, or intermediate Claude.ai
credentials or session tokens" (code.claude.com/docs/en/legal-and-compliance, read 2026-10-18). So on a hosted server:
- `refuse` turns a Claude OAuth token away before it is stored: as a Computer key (Settings > Computers), as the
  company's shared key or any other credential, or from a computer's credential migration.
- `withheld` keeps one stored before this out of every reply that hands credentials to a computer or a bot run.
- `purge`, at each start, deletes the ones still stored: only those secrets, with their grants and computer key rows.
Self-hosted servers keep Claude OAuth-token keys as before; API keys are unaffected everywhere.
"""

import logging

from .store import H, Problem

CLAUDE_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
# `claude setup-token` makes `sk-ant-oat01-…`; an Anthropic API key is `sk-ant-api03-…`.
TOKEN_PREFIX = "sk-ant-oat"
REFUSED = ("A server Tico hosts can't keep a Claude sign-in token (Anthropic's terms): sign in on the computer itself "
           "(Settings > Computers > Sign in), or use an Anthropic API key.")


def hosted(settings):
    """Whether Tico runs this server (`own` or `invited`); `settings` is the server's Settings."""
    return bool(settings is not None and settings.hosted)


def claude_token(env, value=""):
    """Whether a credential is a Claude sign-in token: stored under its variable, or a token pasted under another."""
    return env == CLAUDE_TOKEN or str(value or "").strip().startswith(TOKEN_PREFIX)


def refuse(settings, env, value=""):
    """Before a credential is stored: a Claude sign-in token is refused on a hosted server. The value is never named."""
    if hosted(settings) and claude_token(env, value):
        raise Problem("claude_token", REFUSED, 422)


def withheld(settings, env, value=""):
    """Whether a stored credential must not reach a computer or a bot run: a Claude sign-in token, on a hosted server."""
    return hosted(settings) and claude_token(env, value)


def key_names(settings, names):
    """The variables a Computer key may be added as on this server: without the Claude sign-in token when hosted."""
    return tuple(name for name in names if not withheld(settings, name))


def purge(c, settings, vault=None):
    """Delete every Claude sign-in token still stored on a hosted server, and nothing else: each one's credential row,
    its grants and its computer key row. Found by variable, and with a configured vault by value too (a token pasted
    under another name). A computer that used one is given no key for it from its next key fetch, and takes its copy
    out (runner-rs/src/signin.rs `team_model_keys`); it signs in again, or gets an Anthropic API key. Returns how many."""
    if not hosted(settings):
        return 0
    rows = c.execute("SELECT id, env, kind, source, ciphertext, nonce FROM credentials").fetchall()
    found = []
    for row in rows:
        if row["env"] == CLAUDE_TOKEN:
            found.append(row)
        elif row["ciphertext"] is not None and vault is not None and vault.configured:
            try:
                value = vault.cipher.decrypt(c, row)
            except Problem:
                continue
            if claude_token("", value):
                found.append(row)
    for row in found:
        c.execute("DELETE FROM computer_keys WHERE credential_id=?", (row["id"],))
        c.execute("DELETE FROM credential_grants WHERE credential_id=?", (row["id"],))
        c.execute("DELETE FROM credentials WHERE id=?", (row["id"],))
        H.event(c, "system", "credential.claude_token_removed", row["id"],
                {"env": row["env"], "source": row["source"] or "", "reason": "hosted"})
    if found:
        logging.getLogger("tico.hosting").warning("Removed %d stored Claude sign-in token(s): a hosted server keeps none",
                                                  len(found))
    return len(found)
