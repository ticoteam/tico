"""What a bot's `tools:` block (older: `access:`) says, cut down to what its page may show (docs/creating-bots.md,
"What people see about a bot's tools").

The runner reads bot.yaml (older: employee.yaml) from the bot's checkout and reports each entry on the heartbeat,
next to the bot's readiness. Only a fixed list of non-secret fields leaves the computer: the
service, the identity it acts as, the verbs, a few scope fields (database, channels, ...), the note,
and the *name* of the environment variable. Never a value. Whether the credential is on this
computer is a fact the server cannot see, so each entry carries it as `credential`.
"""

import re

from clients.access_entry import MAX_CAN, EntryError, clean_mcp, one_line, scope_of

MAX_ENTRIES = 30        # backend/models.py ToolAccess and BotReadiness.tools hold the same limits
DATABASE_SERVICES = ("postgres", "postgresql", "mysql", "mariadb", "sqlite", "mongodb")
# A URL with a password in it is a secret wherever it was typed.
URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)


def text(value, limit):
    return one_line(URL_PASSWORD.sub(r"\1", str(value)), limit) if value is not None else ""


def env_name(entry):
    """The variable the credential arrives in: the declared one, else the database default."""
    name = str(entry.get("env") or "").strip()
    if name:
        return name
    database = str(entry.get("database") or "").strip()
    if database and str(entry.get("service") or "").lower() in DATABASE_SERVICES:
        return "DB_" + database.upper().replace("-", "_") + "_URL"
    return ""


def credential_state(entry, name, environment):
    """present | missing | hub-vault | not-declared. Presence only: a 1Password reference counts as set
    (the turn resolves it), and the value is never read into the report."""
    if entry.get("vault") == "hub":
        return "hub-vault"
    if not name:
        return "not-declared"
    return "present" if str(environment.get(name) or "").strip() else "missing"


def declared_tools(access, environment, held=()):
    """The report rows for one bot's `access:` list; [] when it is absent or malformed. `held` names the
    variables this computer keeps for its bots instead of putting them in a run's environment (the Google
    key on a Docker runner, runner/mail_key.py): such a tool is present, marked `held`."""
    if not isinstance(access, list):
        return []
    rows = []
    for entry in access:
        if not isinstance(entry, dict) or not str(entry.get("service") or "").strip():
            continue
        name = env_name(entry)
        can = entry.get("can")
        can = [can] if isinstance(can, str) else can if isinstance(can, list) else []
        row = {"service": text(entry["service"], 100), "identity": text(entry.get("identity"), 300),
               "can": [text(verb, 40) for verb in can[:MAX_CAN] if text(verb, 40)],
               "scope": {k: ([text(i, 100) for i in v] if isinstance(v, list) else text(v, 200)) for k, v in scope_of(entry).items()}, "env": text(name, 100), "note": text(entry.get("note"), 500),
               "credential": credential_state(entry, name, environment)}
        if row["credential"] == "missing" and name in held:
            row["credential"], row["held"] = "present", True
        if entry.get("mcp") not in (None, {}, ""):
            # The validated block: a URL, a transport and headers that hold only `${VAR}` placeholders, so none of it is
            # a value. runner/service.py adds the reachability and the harness note.
            try:
                row["mcp"] = clean_mcp(entry["mcp"], name)
            except EntryError as exc:
                row["problem"] = text("The MCP block is not valid: " + str(exc), 300)
        if "{{" in str(entry.get("identity") or ""):
            row["problem"] = "The identity is still the template placeholder"
        rows.append(row)
        if len(rows) >= MAX_ENTRIES:
            break
    return rows


MAX_FORWARD = 20        # backend/models.py MailRequest holds the same limit


def mail_request(declared):
    """What bot.yaml asks for its mail: `outbound_send` and the `forward_to` addresses, or None when it asks for
    neither. A request only: the server holds the setting a person approved (backend/mail_settings.py), and Health
    says when bot.yaml asks for more."""
    if not isinstance(declared, dict):
        return None
    raw = declared.get("forward_to")
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    targets = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        addr = text(item, 320).strip().lower()
        if "@" in addr and " " not in addr and addr not in targets:
            targets.append(addr)
    on = declared.get("outbound_send") is True
    if not on and not targets:
        return None
    return {"outbound_send": on, "forward_to": targets[:MAX_FORWARD]}
