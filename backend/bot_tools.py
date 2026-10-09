"""The Tools row on a bot's page: what the bot uses, at a glance (docs/creating-bots.md, "What people
see about a bot's tools").

Three sources make the list, none of them a secret. The model and harness the bot runs on come from
its stored config and the company's provider choice. Its repository comes from the bot's record. The
rest are the `tools:` entries (older: `access:`) of its bot.yaml (older: employee.yaml), which the runner reads from the bot's checkout
and reports on every heartbeat with whether each credential is on its computer
(runner/declared_access.py, `bots.<bot>.tools` in the readiness report). A runner from before that
report yields the first two only. No value ever passes through here: env is a variable's name.

A person who manages a bot (`Auth.bot_manager`) can also register, change or remove a tool. The server holds no bot repository,
so it cannot edit bot.yaml: it checks the entry against the schema bot.yaml uses
(clients/access_entry.py, which also refuses anything that looks like a credential), keeps it as a
pending request, and opens a task for BotOps with the exact YAML. The Tools row shows the request as
"pending" until the runner's report lists the entry (or, for a removal, stops listing it, and for a change,
lists it as changed). A change keeps the entry where it is: one task with the whole changed entry, never a
removal followed by an add.
"""

import json

from fastapi import Request

from clients import access_entry, mcp_servers
from . import models as M
from . import providers, routines
from .harnesses import EXTERNAL_HARNESSES, resolve_harness
from .store import H, Problem, encode, readiness_document, repo_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_tool_requests(
 id TEXT PRIMARY KEY, bot TEXT NOT NULL REFERENCES bots(slug), kind TEXT NOT NULL,
 service TEXT NOT NULL, identity TEXT NOT NULL DEFAULT '', entry_json TEXT NOT NULL,
 task_id TEXT, requested_by TEXT NOT NULL, created TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
CREATE INDEX IF NOT EXISTS bot_tool_requests_bot ON bot_tool_requests(bot, state, kind);
"""
CREDENTIALS_NOTE = ("Store a needed Credential through Credentials or its chat card, and grant it to this bot. "
                    "Never put a value in a task or repository. This entry names only the variable.")

ONLINE_WITHIN_S = 60

HARNESS_NAMES = {"codex": "Codex", "claude": "Claude Code", "gemini": "Gemini CLI", "antigravity": "Antigravity",
                 "grok": "Grok Build", "pi": "pi", "cursor": "Cursor", "hermes": "Hermes", "openclaw": "OpenClaw", "grokbot": "Grok Bot", "dots": "Dots"}
PROVIDER_LOGOS = {"openai": "openai", "anthropic": "anthropic", "google": "google"}

SERVICE_NAMES = {
    "github": "GitHub", "github-app": "GitHub App", "slack": "Slack", "gmail": "Gmail", "google-calendar": "Google Calendar",
    "google-drive": "Google Drive", "google-workspace": "Google Workspace", "google-workspace-admin": "Google Workspace admin",
    "google-ads": "Google Ads", "meta-ads": "Meta Ads", "posthog": "PostHog", "mongodb": "MongoDB", "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "sqlite": "SQLite", "openai": "OpenAI",
    "anthropic": "Anthropic", "notion": "Notion", "linear": "Linear", "stripe": "Stripe", "aws": "AWS", "s3": "Amazon S3",
    "cloudflare": "Cloudflare", "zoom": "Zoom", "fireflies": "Fireflies", "linkedin": "LinkedIn", "x": "X", "reddit": "Reddit",
    "close-crm": "Close CRM", "calendly": "Calendly", "brex": "Brex", "mercury": "Mercury", "web-search": "Web search",
    "hugging-face": "Hugging Face", "elevenlabs": "ElevenLabs", "heygen": "HeyGen", "xai": "xAI", "gemini": "Gemini",
}
# The logo a service is drawn with: a Simple Icons slug for a mark ui/tool-icons.js bundles (or fireflies, which
# Simple Icons lacks: a frontend without one draws the name's first two letters). Anything else has none.
LOGO_KEYS = {
    "github": "github", "github-app": "github", "slack": "slack", "gmail": "gmail", "google-calendar": "googlecalendar",
    "google-drive": "googledrive", "google-workspace": "google", "google-workspace-admin": "google", "posthog": "posthog",
    "mongodb": "mongodb", "postgres": "postgresql", "postgresql": "postgresql", "mysql": "mysql", "openai": "openai",
    "anthropic": "anthropic", "notion": "notion", "linear": "linear", "stripe": "stripe", "aws": "amazonaws",
    "s3": "amazonaws", "cloudflare": "cloudflare", "zoom": "zoom", "fireflies": "fireflies",
}


def service_name(service):
    key = str(service or "").strip().lower()
    return SERVICE_NAMES.get(key) or key.replace("-", " ").replace("_", " ").title() or "Tool"


def _unique(used, base):
    name, n = base, 1
    while name in used:
        n += 1
        name = f"{base}-{n}"
    used.add(name)
    return name


def _model_tool(c, settings, bot, config, readiness, label):
    row = H.bot(c, bot) or {}
    runtime, model = providers.bot_choice(c, settings, config)
    runtime = runtime or row.get("runtime") or ""
    harness = resolve_harness(config, runtime)
    model = model or row.get("model") or ""
    catalog = providers.MODEL_BY_ID.get(model) or {}
    provider = catalog.get("provider") or ""
    scope = {}
    effort = config.get("reasoning_effort") or config.get("effort") or row.get("effort")
    if effort and effort != "as-configured":
        scope["effort"] = str(effort)
    fallback = config.get("fallback")
    if isinstance(fallback, dict) and fallback.get("model"):
        scope["fallback"] = "/".join(str(fallback[k]) for k in ("harness", "model") if fallback.get(k))
    status, problem = "unknown", ""
    state = (readiness.get("runtimes") or {}).get(runtime) if harness not in EXTERNAL_HARNESSES else None
    if isinstance(state, dict):
        if not state.get("installed"):
            status, problem = "problem", "Runtime is not installed on " + label
        elif state.get("authenticated") in ("missing", "failed", "rejected"):
            status, problem = "problem", state.get("detail") or "Runtime is not signed in on " + label
        else:
            status = "ready"
    tool = {"id": "model", "service": harness or runtime or "model", "name": HARNESS_NAMES.get(harness) or harness.title() or "Model",
            "logo_key": PROVIDER_LOGOS.get(provider), "identity": (provider + "/" + model) if provider and model else model,
            "can": ["use"], "scope": scope, "note": "", "status": status,
            "detail": "The AI model and harness this bot's turns run on."}
    if problem:
        tool["problem"] = problem
    return tool


def _repo_tool(settings, bot, repo, report):
    """The bot's own repository, when it has a browsable address."""
    url = repo_url(repo or "emp-" + bot, settings.github_owner)
    if not url:
        return None
    name = (url.split("github.com/", 1)[-1] if "github.com/" in url else url).removesuffix(".git")
    tool = {"id": "repo", "service": "github", "name": "GitHub", "logo_key": "github", "identity": name,
            "can": ["read", "write", "issues", "pull_requests"], "scope": {"repo": name}, "note": "", "url": url,
            "status": "unknown", "detail": "Own repository: instructions and memory"}
    if report.get("repository_present") is True:
        tool["status"] = "ready"
        tool["detail"] += "; checked out on its computer"
    elif report.get("repository_present") is False:
        tool["status"], tool["problem"] = "problem", "Repository is not checked out on the bot's computer"
    return tool


def _declared_tool(entry, used, label):
    service = str(entry.get("service") or "")
    env, credential = entry.get("env") or "", entry.get("credential") or "not-declared"
    status, problem, detail = "unknown", entry.get("problem") or "", ""
    if credential == "present" and entry.get("github_app"):
        status, detail = "ready", f"{env} is minted from the GitHub App for each run"
    elif credential == "present" and entry.get("granted"):
        status, detail = "ready", f"{env} is granted through the credential vault; it arrives when a run starts"
    elif credential == "present" and entry.get("held") and env == "GOOGLE_SA_KEY":
        status, detail = "ready", f"{env} is present (held by the computer); a run gets a short-lived token, never the key"
    elif credential == "present" and entry.get("held"):
        status, detail = "ready", f"{env} is held by the computer and given to each run"
    elif credential == "present":
        status, detail = "ready", f"{env} is set on {label}"
    elif credential == "missing" and env == "GOOGLE_SA_KEY":
        status, problem = "problem", problem or f"{label} has no Google service-account key (google-sa.json in its state directory)"
    elif credential == "missing":
        status, problem = "problem", problem or f"Credential missing on {label}"
    elif credential == "hub-vault":
        detail = "Granted through the credential vault; it arrives when a run starts"
    else:
        detail = "No credential is declared, so there is nothing to check"
    mcp = _mcp_view(entry.get("mcp"))
    if mcp and mcp["status"] in ("auth_failed", "unreachable") and not problem:
        problem = ("The MCP server at " + mcp["host"] + (" refused the credential" if mcp["status"] == "auth_failed"
                                                         else " did not answer"))
    elif mcp and not problem and status != "problem":
        detail = (detail + "; " if detail else "") + f"MCP server {mcp['host']} ({mcp['transport']}): " + (
            "reachable" if mcp["status"] == "reachable" else "not checked yet")
    if problem:
        status = "problem"
    tool = {"id": _unique(used, service.lower() or "tool"), "service": service, "name": service_name(service),
            "logo_key": LOGO_KEYS.get(service.strip().lower()), "identity": entry.get("identity") or "",
            "can": list(entry.get("can") or []), "scope": dict(entry.get("scope") or {}), "env": env,
            "note": entry.get("note") or "", "status": status, "detail": detail}
    if mcp:
        tool["kind"], tool["mcp"] = "mcp", mcp
    if problem:
        tool["problem"] = problem
    return tool


def _mcp_view(mcp):
    """What the tool's row shows of an MCP server: its host, transport and the runner's check. Never the headers."""
    if not isinstance(mcp, dict) or not mcp.get("url"):
        return None
    return {"host": mcp_servers.host_of(str(mcp["url"])), "transport": mcp.get("transport") or "http",
            "status": mcp.get("status") or "unchecked"}


ENTRY_KEYS = ("service", "identity", "can", "scope", "env", "note")


def _entry_of(raw):
    """A reported tool as a `tools:` entry again: the fields a person sets, the MCP block without the runner's check."""
    entry = {k: raw[k] for k in ENTRY_KEYS if raw.get(k)}
    if isinstance(raw.get("mcp"), dict) and raw["mcp"].get("url"):
        entry["mcp"] = {k: raw["mcp"][k] for k in ("url", "transport", "headers") if raw["mcp"].get(k)}
    return entry


def _same(a, b):
    """Whether two entries are the same tool: one service, acting as one identity."""
    norm = lambda entry: (str(entry.get("service") or "").strip().lower(), str(entry.get("identity") or "").strip().lower())
    return norm(a) == norm(b)


def _app_token(entry):
    return (entry.get("credential") == "missing" and str(entry.get("service") or "").lower() == "github"
            and entry.get("env") in ("GH_TOKEN", "GITHUB_TOKEN"))


def github_app_covers(c, bot):
    """Whether each run gets GH_TOKEN/GITHUB_TOKEN minted from the GitHub App: the App is connected and the bot's
    repository is in its organization (the same test as POST /api/v2/github/token; runner/git_credentials.py)."""
    if not H._has_table(c, "github_app"):
        return False
    app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    if not app or not app["org"]:
        return False
    from .github_app import repo_of
    from .shared_bots import declared, source_of
    config = declared(c, bot)
    if config.get("assignment_branch"):
        return False
    row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (source_of(config) or bot,)).fetchone()
    repo = repo_of(row["repo"] if row else "", app["org"])
    return bool(repo) and repo.split("/")[0].lower() == app["org"].lower()


def granted(c, bot, raw):
    """The reported tools, with a credential the computer says is missing counted present when the vault grants it to the bot.

    The runner reports only what its computer holds (a secrets file, a profile); a vault grant arrives with each run
    (`GET /api/v2/credential-runtime`), so the computer cannot see it but this server can. Only a grant a run would receive
    counts: not revoked, its delegation intact, and a stored value behind it. A GitHub GH_TOKEN/GITHUB_TOKEN also counts
    when the connected GitHub App mints it for the bot. Names only, never a value."""
    if any(_app_token(e) for e in raw) and github_app_covers(c, bot):
        raw = [dict(e, credential="present", github_app=True) if _app_token(e) else e for e in raw]
    missing = {str(e.get("env")) for e in raw if e.get("credential") == "missing" and e.get("env")}
    if not missing:
        return raw
    from .credentials import granted
    held = granted(c, "bot:" + bot)
    have = {row["env"] for row in c.execute("SELECT id,env FROM credentials WHERE ciphertext IS NOT NULL AND env!=''")
            if row["env"] in missing and row["id"] in held}
    return [dict(e, credential="present", granted=True) if e.get("credential") == "missing" and e.get("env") in have else e
            for e in raw]


def agent_report(c, bot, tools):
    raw = []
    for tool in tools:
        try:
            entry = access_entry.clean(tool.model_dump())
        except access_entry.EntryError as exc:
            raise Problem(exc.code, str(exc), 422) from None
        raw.append({key: entry[key] for key in ("service", "identity", "can", "env", "note", "mcp") if key in entry}
                   | {"scope": access_entry.scope_of(entry), "credential": "not-declared"})
    c.execute("INSERT INTO registry_metadata(key,value_json) VALUES(?,?) "
              "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              ("agent-tools:" + bot, encode({"tools": raw, "reported_at": H.now()})))
    reconcile(c, {bot: raw}, complete=True)


def _state(c, settings, bot):
    row = c.execute("SELECT config_json,repo FROM bot_config WHERE bot=?", (bot,)).fetchone()
    config = H._json(row["config_json"], {}) if row else {}
    config = config if isinstance(config, dict) else {}
    runner = c.execute("SELECT r.label,r.last_seen,r.revoked_at,r.readiness_json FROM assignments a "
                       "JOIN runners r ON r.id=a.runner_id WHERE a.bot=?", (bot,)).fetchone()
    readiness = readiness_document(runner["readiness_json"]) if runner else {}
    report = (readiness.get("bots") or {}).get(bot)
    report = report if isinstance(report, dict) else {}
    label = (runner["label"] if runner else "") or "its computer"
    external = resolve_harness(config) in EXTERNAL_HARNESSES
    agent = c.execute("SELECT profile,last_seen,revoked_at FROM agents WHERE bot=?", (bot,)).fetchone() if external else None
    if external:
        stored = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", ("agent-tools:" + bot,)).fetchone()
        report = H._json(stored[0], {}) if stored else {}
        label = (agent["profile"] if agent else "") or "its external profile"
    used = {"model", "repo"}
    raw = granted(c, bot, [entry for entry in report.get("tools") or [] if isinstance(entry, dict)])
    return {"row": row, "config": config, "runner": runner, "agent": agent, "readiness": readiness, "report": report, "label": label,
            "raw": raw, "declared": [_declared_tool(entry, used, label) for entry in raw]}


def _requests(c, bot):
    return [dict(row, entry=H._json(row["entry_json"], {}) or {}) for row in c.execute(
        "SELECT * FROM bot_tool_requests WHERE bot=? AND state='pending' ORDER BY created,id", (bot,))]


def _pending_tool(request):
    entry = request["entry"]
    service = entry.get("service") or request["service"]
    return {"id": "pending-" + request["id"], "service": service, "name": service_name(service),
            "logo_key": LOGO_KEYS.get(service), "identity": entry.get("identity") or "", "can": list(entry.get("can") or []),
            "scope": access_entry.scope_of(entry), "env": entry.get("env") or "", "note": entry.get("note") or "",
            "status": "pending", "pending": "add", "task_id": request["task_id"],
            "detail": "Waiting for BotOps to add it to the bot's bot.yaml; it shows as ready once the computer reports it",
            **({"kind": "mcp", "mcp": _mcp_view(entry["mcp"])} if _mcp_view(entry.get("mcp")) else {})}


def listing(c, settings, bot):
    """Everything the row shows for one bot, from records this server already holds."""
    state = _state(c, settings, bot)
    runner, report, label = state["runner"], state["report"], state["label"]
    online = bool(runner and not runner["revoked_at"] and runner["last_seen"]
                  and runner["last_seen"] > H.shift(H.now(), seconds=-ONLINE_WITHIN_S))
    declared = state["declared"]
    external = resolve_harness(state["config"]) in EXTERNAL_HARNESSES
    if external:
        for tool in declared:
            if tool["status"] == "unknown":
                tool["detail"] = "Declared by the external profile; Tico has not checked its connection or credential"
    requests = _requests(c, bot)
    for request in requests:
        if request["kind"] in ("remove", "update"):
            for tool, entry in zip(declared, state["raw"]):
                if _same(entry, request["entry"]):
                    tool["pending"], tool["task_id"] = request["kind"], request["task_id"]
                    if external:
                        tool["detail"] = "Waiting for this external profile to report the changed tool list"
                        continue
                    tool["detail"] = ("Removal requested; waiting for BotOps to take it out of bot.yaml"
                                      if request["kind"] == "remove" else
                                      "A change is requested; waiting for BotOps to update it in bot.yaml")
    # The computer holds the Google key and gives a token, but only to a message bot the server has named
    # (routines.token_mailboxes): for any other bot "present" would be a promise its runs cannot keep.
    # Only the Google key: a computer also holds other credentials for its bots (a vault grant), and
    # those reach the run whoever its message bot is.
    google = lambda entry: entry.get("held") and entry.get("env") == "GOOGLE_SA_KEY"
    if any(google(entry) for entry in state["raw"]) and not routines.token_mailboxes(c, bot):
        for tool, entry in zip(declared, state["raw"]):
            if google(entry):
                tool.update(status="problem", held=True, detail="", problem=(
                    f"{label} holds the Google key, but {bot} is nobody's message bot on the server, so its runs get no mail "
                    "token. An owner or admin names it: POST /api/v2/access/people/<person> "
                    '{"inbox_bot": "' + bot + '", "mailbox": "<address>"}'))
    adding = [_pending_tool(r) for r in requests if r["kind"] == "add"
              and not any(_same(entry, r["entry"]) for entry in state["raw"])]
    if external:
        for tool in adding:
            tool["detail"] = "Waiting for this external profile to configure and report the tool"
    tools = [_model_tool(c, settings, bot, state["config"], state["readiness"], label)]
    repo = _repo_tool(settings, bot, state["row"] and state["row"]["repo"], report)
    if repo:
        matching = next((tool for tool in declared if tool["service"].lower() in ("github", "github-app")
                         and str(tool["scope"].get("repo") or tool["identity"]).lower() == repo["identity"].lower()), None)
        if matching:
            matching["detail"] += "; own repository: instructions and memory"
        else:
            tools.append(repo)
    from .repositories import access
    app_row = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    if repo and not app_row and repo in tools:
        repo["detail"] += "; uses the computer's own GitHub sign-in (the GitHub App is not connected)"
    from .github_app import repo_of
    own = repo_of(state['row']['repo'] if state['row'] else '', settings.github_owner)
    org = app_row['org'] if app_row else settings.github_owner or (own.split('/')[0] if own else '')
    extras = access(c, bot, org)['effective'] if state['row'] else []
    represented = {str(tool.get("scope", {}).get("repo") or tool.get("identity") or "").lower()
                   for tool in tools + declared if tool["service"].lower() in ("github", "github-app")}
    for index, grant in enumerate(extras):
        name = grant['full_name']
        if name.lower() in represented:
            continue
        tools.append({"id": f"github-extra-{index}", "service": "github", "name": "GitHub", "logo_key": "github",
                      "identity": name, "can": ["read", "write", "issues", "pull_requests"] if grant["access"] == "write" else ["read"], "scope": {"repo": name},
                      "note": "", "url": "https://github.com/" + name, "status": "unknown",
                      "detail": "Granted repository"})
    agent = state["agent"]
    if agent:
        online = bool(not agent["revoked_at"] and agent["last_seen"] and
                      agent["last_seen"] > H.shift(H.now(), seconds=-180))
    report_error = next((warning for warning in report.get("warnings") or []
                         if warning.startswith("Tool report rejected: ")), "")
    for tool in adding:
        if report_error:
            tool.update(status="problem", problem=report_error, detail=report_error)
    return {"bot": bot, "tools": tools + declared + adding, **({"report_error": report_error} if report_error else {}),
            "computer": label if runner else None, "online": online,
            "reported_at": runner["last_seen"] if runner else report.get("reported_at")}


# ----------------------------------------------------------------------------- registering
VERBS = {"add": ("Add", "to"), "remove": ("Remove", "from"), "update": ("Change", "on")}


def _task_text(verb, bot, name, entry, computer, repo=None):
    # The bot's recorded repository (`emp-<slug>` for a bot made before `bot-<slug>`), and its manifest under either name.
    where = str(repo or "bot-" + bot).rsplit("/", 1)[-1] + "/bot.yaml (employee.yaml in a repository not yet renamed)"
    if verb == "add":
        do = (f"Add this entry to the `tools:` list (older: `access:`) in {where}, keeping the entries already there, then commit locally:")
    elif verb == "update":
        do = (f"Replace the entry for this service and identity in the `tools:` list (older: `access:`) in {where} with this one "
              "(match it by service and identity; keep every other entry and do not remove and re-add it), then commit locally:")
    else:
        do = f"Remove this entry from the `tools:` list (older: `access:`) in {where} (match it by service and identity), then commit locally:"
    return "\n".join([
        f"{VERBS[verb][0]} {name} access {VERBS[verb][1]} {bot}.", "",
        do, "", "```yaml", access_entry.to_yaml(entry), "```", "",
        *(["The `mcp:` block is a remote MCP server: Tico's runner passes it to the bot's harness, and `${VAR}` in a header is "
           "filled from the bot's granted credential at run time. Keep the placeholder as it is; never write the value in "
           "the file. Once it is granted, check it with one read-only call (docs/connect-tools.md).", ""]
          if entry.get("mcp") else []),
        "Commit the bot repository locally and run `hub bot check " + bot + "`. The bot publishes its own repository "
        "with its own access on its next run. Say on this task what the check reported. "
        "Tico shows the entry on the bot's page once the computer's readiness report "
        + {"add": "lists it.", "update": "lists it as changed.", "remove": "no longer lists it."}[verb], "",
        CREDENTIALS_NOTE + (" Do not ask for the value here, and never commit it." if verb == "add" else "")
        + (f" The computer is {computer}." if computer else "")])


def _request_task(c, auth, who, verb, bot, name, entry, computer, taken, repo=None, title_prefix=""):
    from . import getting_started as G
    external = resolve_harness(H._json(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0], {})) in EXTERNAL_HARNESSES
    if not external:
        G._botops(c)
    title = f"{VERBS[verb][0]} {name} access {VERBS[verb][1]} {bot}"
    if title_prefix:
        title = title_prefix.strip() + " " + title
    if taken:
        title += f" ({taken + 1})"        # the hub refuses a second live task with the same title
    if external:
        text = (f"{VERBS[verb][0]} {name} in your external profile. Use the entry below as the tool's scope. "
                "For Hermes, configure the tool or MCP server in this profile's config.yaml; for OpenClaw, "
                "configure the profile's tool or skill. Keep credential values in the profile's credential store, never on this task.\n\n"
                + access_entry.to_yaml(entry) + "\nAfter checking the tool, report the full current list with "
                "hub_tool_report (or python3 hermes_agent.py call --profile <name> hub_tool_report '{\"tools\": [...]}'). "
                "Use an empty list when the last tool was removed. Then finish this task.")
        return G._task(c, auth, who, bot, title, text)
    return G._task(c, auth, who, G.BOTOPS, title, _task_text(verb, bot, name, entry, computer, repo))


def register(c, auth, settings_admin, settings, who, bot, body):
    auth.domain(who)
    settings_admin._manager(c, who, bot)      # Auth.bot_manager
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    try:
        entry = access_entry.clean(body.model_dump())
    except access_entry.EntryError as exc:
        raise Problem(exc.code, str(exc), 422) from None
    if body.dry_run:
        return {"ok": True, "yaml": access_entry.to_yaml(entry), "credentials": CREDENTIALS_NOTE}
    state = _state(c, settings, bot)
    if any(_same(entry, declared) for declared in state["raw"]) or any(
            r["kind"] == "add" and _same(entry, r["entry"]) for r in _requests(c, bot)):
        raise Problem("duplicate", "This bot already has that tool, or a request for it is waiting for BotOps", 409)
    taken = c.execute("SELECT count(*) FROM bot_tool_requests WHERE bot=? AND service=? AND kind='add' AND state='pending'",
                      (bot, entry["service"])).fetchone()[0]
    name = service_name(entry["service"])
    task = _request_task(c, auth, who, "add", bot, name, entry, state["runner"] and state["label"], taken,
                         state["row"] and state["row"]["repo"], body.title_prefix)
    rid = H.new_id()
    c.execute("INSERT INTO bot_tool_requests(id,bot,kind,service,identity,entry_json,task_id,requested_by,created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (rid, bot, "add", entry["service"], entry.get("identity", ""), encode(entry),
                                            task["id"], who.actor, H.now()))
    H.event(c, who.actor, "bot.tool_requested", bot, {"service": entry["service"], "task": task["id"]})
    tool = _pending_tool(dict(id=rid, entry=entry, service=entry["service"], task_id=task["id"]))
    if resolve_harness(state["config"]) in EXTERNAL_HARNESSES:
        tool["detail"] = "Waiting for this external profile to configure and report the tool"
    return {"tool": tool, "task_id": task["id"], "yaml": access_entry.to_yaml(entry), "credentials": CREDENTIALS_NOTE}


def unregister(c, auth, settings_admin, settings, who, bot, tool_id):
    auth.domain(who)
    settings_admin._manager(c, who, bot)      # Auth.bot_manager
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    requests = _requests(c, bot)
    if tool_id.startswith("pending-"):
        found = next((r for r in requests if "pending-" + r["id"] == tool_id and r["kind"] == "add"), None)
        if not found:
            raise Problem("not_found", "That request is not waiting any more", 404)
        c.execute("UPDATE bot_tool_requests SET state='cancelled' WHERE id=?", (found["id"],))
        H.task_close(c, H.KEEPER, found["task_id"], note="Tool request withdrawn; do not apply this change.")
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (found["task_id"],))
        H.event(c, who.actor, "bot.tool_request_cancelled", bot, {"service": found["service"], "task": found["task_id"]})
        return {"cancelled": True, "task_id": found["task_id"],
                "detail": "The request is withdrawn. If BotOps already added the entry it stays until you remove it."}
    if tool_id in ("model", "repo"):
        raise Problem("tool", "A bot always has read, write, issues and pull requests on its own repository, and its model is chosen in "
                      "its settings; neither is a Tool you change or remove here", 422)
    state = _state(c, settings, bot)
    index = next((i for i, tool in enumerate(state["declared"]) if tool["id"] == tool_id), None)
    if index is None:
        raise Problem("not_found", "This bot does not declare that tool", 404)
    entry = _entry_of(state["raw"][index])
    if any(r["kind"] == "remove" and _same(entry, r["entry"]) for r in requests):
        raise Problem("duplicate", "Its removal is already waiting for BotOps", 409)
    if any(r["kind"] == "update" and _same(entry, r["entry"]) for r in requests):
        raise Problem("duplicate", "A change to it is already waiting for BotOps; remove it once that is done", 409)
    flat = {"service": entry["service"], **({"identity": entry["identity"]} if entry.get("identity") else {}),
            "can": entry.get("can") or [], **(entry.get("scope") or {}),
            **({"mcp": entry["mcp"]} if entry.get("mcp") else {}),
            **({"env": entry["env"]} if entry.get("env") else {}), **({"note": entry["note"]} if entry.get("note") else {})}
    taken = c.execute("SELECT count(*) FROM bot_tool_requests WHERE bot=? AND service=? AND kind='remove' AND state='pending'",
                      (bot, entry["service"])).fetchone()[0]
    name = service_name(entry["service"])
    task = _request_task(c, auth, who, "remove", bot, name, flat, state["label"], taken,
                         state["row"] and state["row"]["repo"])
    c.execute("INSERT INTO bot_tool_requests(id,bot,kind,service,identity,entry_json,task_id,requested_by,created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (H.new_id(), bot, "remove", entry["service"], entry.get("identity", ""),
                                            encode(entry), task["id"], who.actor, H.now()))
    H.event(c, who.actor, "bot.tool_removal_requested", bot, {"service": entry["service"], "task": task["id"]})
    return {"removal": True, "tool": tool_id, "task_id": task["id"]}


def _declared_entry(state, tool_id):
    """The raw entry of the bot's declared tool with this id, or a Problem."""
    if tool_id in ("model", "repo"):
        raise Problem("tool", "A bot always has read, write, issues and pull requests on its own repository, and its model is chosen in "
                      "its settings; neither is a Tool you change or remove here", 422)
    index = next((i for i, tool in enumerate(state["declared"]) if tool["id"] == tool_id), None)
    if index is None:
        raise Problem("not_found", "This bot does not declare that tool", 404)
    return _entry_of(state["raw"][index])


def _scope(entry):
    """An entry's scope as {key: [text]}: one value and a list of one are the same scope."""
    return {k: [str(i) for i in (v if isinstance(v, list) else [v])]
            for k, v in access_entry.scope_of(entry.get("scope") or entry).items()}


def _changed(entry, reported):
    """Whether a reported entry already says what `entry` (a change's new entry) says."""
    return (list(reported.get("can") or []) == list(entry.get("can") or [])
            and _scope(reported) == _scope(entry) and str(reported.get("note") or "") == str(entry.get("note") or "")
            and _entry_of(reported).get("mcp") == entry.get("mcp"))


def update(c, auth, settings_admin, settings, who, bot, tool_id, body):
    """Change a declared tool in place: only `can`, `scope`, `note` and an MCP server's `mcp` block. The server holds no bot repository, so this is
    the add path's way: a pending request and one task for BotOps with the whole changed entry. Nothing is removed."""
    auth.domain(who)
    settings_admin._manager(c, who, bot)      # Auth.bot_manager
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    if tool_id.startswith("pending-"):
        raise Problem("tool", "That tool is not on the bot yet; withdraw the request and add it again with the change", 422)
    if body.can is None and body.scope is None and body.note is None and not body.mcp:
        raise Problem("entry", "Say what changes: can, scope, note or mcp", 422)
    state = _state(c, settings, bot)
    old = _declared_entry(state, tool_id)
    scope = dict(old.get("scope") or {})
    for key, value in (body.scope or {}).items():
        if value in (None, "", []):
            scope.pop(key, None)
        else:
            scope[key] = value
    merged = {**old, "scope": scope}
    if body.can is not None:
        merged["can"] = body.can
    if body.note is not None:
        merged["note"] = body.note
    if body.mcp:
        if not (old.get("mcp") or body.mcp.get("url")):
            raise Problem("entry", "That tool is not an MCP server yet; send its url as well", 422)
        merged["mcp"] = {**(old.get("mcp") or {}), **body.mcp}
    try:
        entry = access_entry.clean(merged)
    except access_entry.EntryError as exc:
        raise Problem(exc.code, str(exc), 422) from None
    if _changed(entry, old):
        raise Problem("unchanged", "The tool already says that", 409)
    requests = _requests(c, bot)
    if any(r["kind"] in ("update", "remove") and _same(old, r["entry"]) for r in requests):
        raise Problem("duplicate", "A change or removal of this tool is already waiting for BotOps", 409)
    taken = c.execute("SELECT count(*) FROM bot_tool_requests WHERE bot=? AND service=? AND kind='update' AND state='pending'",
                      (bot, entry["service"])).fetchone()[0]
    task = _request_task(c, auth, who, "update", bot, service_name(entry["service"]), entry, state["label"], taken,
                         state["row"] and state["row"]["repo"], body.title_prefix)
    c.execute("INSERT INTO bot_tool_requests(id,bot,kind,service,identity,entry_json,task_id,requested_by,created) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (H.new_id(), bot, "update", entry["service"], entry.get("identity", ""),
                                            encode(entry), task["id"], who.actor, H.now()))
    H.event(c, who.actor, "bot.tool_update_requested", bot, {"service": entry["service"], "task": task["id"]})
    return {"update": True, "tool": tool_id, "task_id": task["id"], "yaml": access_entry.to_yaml(entry),
            "credentials": CREDENTIALS_NOTE}


def reconcile(c, reported, complete=False):
    """Close the requests a heartbeat's report has caught up with. `reported` is {bot: the report's tools}.
    A listed entry closes its request to add it; an entry that is gone closes its request to remove it,
    once the report lists something (a runner from before the report lists nothing at all) or the
    request is ten minutes old."""
    for bot in [row[0] for row in c.execute("SELECT DISTINCT bot FROM bot_tool_requests WHERE state='pending'")]:
        if bot not in reported:
            continue
        raw = [entry for entry in reported[bot] or [] if isinstance(entry, dict)]
        for request in _requests(c, bot):
            listed = any(_same(entry, request["entry"]) for entry in raw)
            if request["kind"] == "update":
                settled = any(_same(entry, request["entry"]) and _changed(request["entry"], entry) for entry in raw)
            else:
                settled = listed if request["kind"] == "add" else (not listed and (
                    complete or raw or request["created"] < H.shift(H.now(), seconds=-600)))
            if settled:
                c.execute("UPDATE bot_tool_requests SET state='done' WHERE id=?", (request["id"],))


def install(app, store, auth, mutate, settings_admin, requester=None):
    """`requester(c, who)` is the person BotOps is acting for when `who` is BotOps in a turn a person's chat message
    started, else `who`: a tool is a bot's owner's to add, and BotOps is nobody's bot but the requester's to manage."""
    acting = requester or (lambda c, who: who)

    @app.get("/api/v2/bots/{bot}/tools")
    def bot_tools(request: Request, bot: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            auth.require_read(c, who, bot)      # a 404 for someone who cannot even see the bot
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            result = listing(c, store.settings, bot)
            from . import task_privacy as privacy
            def redact(value):
                if isinstance(value, dict):
                    tid = value.get("task_id")
                    if tid and not privacy.task_readable(c, who, H.task(c, tid)):
                        value.pop("task_id", None)
                        value.pop("pending", None)
                    for item in value.values():
                        redact(item)
                elif isinstance(value, list):
                    for item in value:
                        redact(item)
            redact(result)
            return result

    @app.post("/api/v2/bots/{bot}/tools")
    def register_tool(request: Request, bot: str, body: M.ToolRegister):
        """Register a tool for a bot: checked, kept as a pending request, and handed to BotOps as a task
        with the exact `tools:` entry. A credential is never accepted; `env` is a variable's name."""
        who = request.state.identity
        return mutate(request, body, lambda c: register(c, auth, settings_admin, store.settings, acting(c, who), bot, body))

    def remove(request, bot, tool_id):
        who = request.state.identity
        return mutate(request, M.Empty(), lambda c: unregister(c, auth, settings_admin, store.settings, acting(c, who), bot, tool_id))

    @app.post("/api/v2/bots/{bot}/tools/{tool_id}/update")
    def update_tool(request: Request, bot: str, tool_id: str, body: M.ToolUpdate):
        """Change a declared tool's `can`, `scope` or `note` in place: one task for BotOps with the changed entry,
        the tool shown as pending its change. BotOps acts for the person who asked it, like `register_tool`."""
        who = request.state.identity
        return mutate(request, body, lambda c: update(c, auth, settings_admin, store.settings, acting(c, who), bot, tool_id, body))

    @app.delete("/api/v2/bots/{bot}/tools/{tool_id}")
    def delete_tool(request: Request, bot: str, tool_id: str):
        """Ask BotOps to remove a declared tool (or withdraw a pending request)."""
        return remove(request, bot, tool_id)

    @app.post("/api/v2/bots/{bot}/tools/{tool_id}/delete")
    def delete_tool_post(request: Request, bot: str, tool_id: str, body: M.Empty):
        """The same as DELETE, for clients (the hub CLI, MCP) that only send GET and POST."""
        return remove(request, bot, tool_id)
