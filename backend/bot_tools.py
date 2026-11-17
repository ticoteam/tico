"""The Tools row on a bot's page: what the bot uses, at a glance (docs/creating-bots.md, "What people
see about a bot's tools").

Grants are what a bot can use: every tool it holds a key of in Tools (an API or MCP tool, a key on no tool, a website
account it may use) reaches its runs (credential-runtime) and is listed here, with no bot.yaml entry
(`_granted_tools`). The other rows, none of them a secret: the model and harness the bot runs on, from its stored config
and the company's provider choice; its repository, from the bot's record; and the `tools:` entries (older: `access:`)
of its bot.yaml (older: employee.yaml), optional notes on a tool (identity, scope, note, an MCP server of its own) that
the runner reads from the bot's checkout and reports on every heartbeat with whether each credential is on its computer
(runner-rs/src/declared_access.rs, `bots.<bot>.tools` in the readiness report). An entry and a grant for the same
service are one row, the entry's notes on the grant. No value ever passes through here: env is a variable's name.

Registering, changing or removing an entry (backend/tool_access.py NOTE) errs on getting the work done, since an entry
holds no secret: the bot itself (its own entries), BotOps, the Tools page's owner, a credential admin, and anyone who
may manage the bot (`Auth.bot_manager`), as themselves or through the Assistant or a built-in acting for them. An entry
that only names a tool the bot already holds (no identity, scope, note or MCP server) needs no line in bot.yaml: it is
answered with the grant, and no task is filed (`covered`). Otherwise the server, which holds no bot repository, checks
the entry against the schema bot.yaml uses (clients/access_entry.py, which also refuses anything that looks like a
credential), keeps it as a pending request, and opens a task for BotOps with the exact YAML. The Tools row shows the
request as "pending" until the runner's report lists the entry (or, for a removal, stops listing it, and for a change,
lists it as changed). A change keeps the entry where it is: one task with the whole changed entry, never a removal
followed by an add.
"""

from fastapi import Request

from clients import access_entry, harness_manifests, mcp_servers
from . import model_subscriptions
from . import models as M
from . import google_workspace, providers, read_cache, routines, runner_online
from .harnesses import EXTERNAL_HARNESSES, resolve_harness
from .store import H, Problem, encode, readiness_document, repo_url
from . import bot_settings as BS
from . import tool_access as TA
from .api_contract import api_v2

CREDENTIALS_NOTE = (
    "Store a needed Credential through Credentials or its chat card, and grant it to this bot. "
    "Never put a value in a task or repository. This entry names only the variable."
)

# A CLI's name is its manifest's; external agents have none.
HARNESS_NAMES = {
    **{h: row["name"] for h, row in harness_manifests.load().items()},
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
    "grokbot": "Grok Bot",
    "dots": "Dots",
    "muse": "Muse",
}
PROVIDER_LOGOS = {"openai": "openai", "anthropic": "anthropic", "google": "google"}

SERVICE_NAMES = {
    "github": "GitHub",
    "github-app": "GitHub App",
    "slack": "Slack",
    "gmail": "Gmail",
    "google-calendar": "Google Calendar",
    "google-drive": "Google Drive",
    "google-workspace": "Google Workspace",
    "google-workspace-admin": "Google Workspace admin",
    "google-ads": "Google Ads",
    "meta-ads": "Meta Ads",
    "posthog": "PostHog",
    "mongodb": "MongoDB",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "sqlite": "SQLite",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "notion": "Notion",
    "linear": "Linear",
    "stripe": "Stripe",
    "aws": "AWS",
    "s3": "Amazon S3",
    "cloudflare": "Cloudflare",
    "zoom": "Zoom",
    "fireflies": "Fireflies",
    "linkedin": "LinkedIn",
    "x": "X",
    "reddit": "Reddit",
    "close-crm": "Close CRM",
    "calendly": "Calendly",
    "brex": "Brex",
    "mercury": "Mercury",
    "web-search": "Web search",
    "hugging-face": "Hugging Face",
    "elevenlabs": "ElevenLabs",
    "heygen": "HeyGen",
    "xai": "xAI",
    "gemini": "Gemini",
}
# The logo a service is drawn with: a Simple Icons slug for a mark ui/tool-icons.ts bundles (or fireflies, which
# Simple Icons lacks: a frontend without one draws the name's first two letters). Anything else has none.
LOGO_KEYS = {
    "github": "github",
    "github-app": "github",
    "slack": "slack",
    "gmail": "gmail",
    "google-calendar": "googlecalendar",
    "google-drive": "googledrive",
    "google-workspace": "google",
    "google-workspace-admin": "google",
    "posthog": "posthog",
    "mongodb": "mongodb",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "mysql": "mysql",
    "openai": "openai",
    "anthropic": "anthropic",
    "notion": "notion",
    "linear": "linear",
    "stripe": "stripe",
    "aws": "amazonaws",
    "s3": "amazonaws",
    "cloudflare": "cloudflare",
    "zoom": "zoom",
    "fireflies": "fireflies",
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


def _model_tool(c, settings, bot, config, readiness, label, runner=None):
    row = H.bot(c, bot) or {}
    runtime, model = providers.bot_choice(c, settings, config)
    runtime, model = runtime or "", model or ""
    harness = resolve_harness(config, runtime)
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
        elif state.get("authenticated") in ("missing", "failed", "rejected") and not (
            # The computer's company subscription signs this bot's turns in (backend/model_subscriptions.py).
            runner
            and runtime
            in model_subscriptions.covered_runtimes(c, runner, providers.fill(providers.load(c, settings), config))
        ):
            status, problem = "problem", state.get("detail") or "Runtime is not signed in on " + label
        else:
            status = "ready"
    tool = {
        "id": "model",
        "service": harness or runtime or "model",
        "name": HARNESS_NAMES.get(harness) or harness.title() or "Model",
        "logo_key": PROVIDER_LOGOS.get(provider),
        "identity": (provider + "/" + model) if provider and model else model,
        "can": ["use"],
        "scope": scope,
        "note": "",
        "status": status,
        "detail": "The AI model and harness this bot's turns run on.",
    }
    if problem:
        tool["problem"] = problem
    return tool


def _repo_tool(settings, bot, repo, report):
    """The bot's own repository, when it has a browsable address."""
    url = repo_url(repo or "emp-" + bot, settings.github_owner)
    if not url:
        return None
    name = (url.split("github.com/", 1)[-1] if "github.com/" in url else url).removesuffix(".git")
    tool = {
        "id": "repo",
        "service": "github",
        "name": "GitHub",
        "logo_key": "github",
        "identity": name,
        "can": ["read", "write", "issues", "pull_requests"],
        "scope": {"repo": name},
        "note": "",
        "url": url,
        "status": "unknown",
        "detail": "Own repository: instructions and memory",
    }
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
        status, detail = (
            "ready",
            f"{env} is present (held by the computer); a run gets a short-lived token, never the key",
        )
    elif credential == "present" and entry.get("held"):
        status, detail = "ready", f"{env} is held by the computer and given to each run"
    elif credential == "present":
        status, detail = "ready", f"{env} is set on {label}"
    elif credential == "missing" and env == "GOOGLE_SA_KEY":
        status, problem = (
            "problem",
            problem
            or f"{label} has no Google access: no service-account key (google-sa.json) and no connected Google account (google-oauth.json) in its state directory",
        )
    elif credential == "missing":
        status, problem = "problem", problem or f"Credential missing on {label}"
    elif credential == "hub-vault":
        detail = "Granted through the credential vault; it arrives when a run starts"
    else:
        detail = "No credential is declared, so there is nothing to check"
    mcp = _mcp_view(entry.get("mcp"))
    if mcp and mcp["status"] in ("auth_failed", "unreachable") and not problem:
        problem = (
            "The MCP server at "
            + mcp["host"]
            + (" refused the credential" if mcp["status"] == "auth_failed" else " did not answer")
        )
    elif mcp and not problem and status != "problem":
        detail = (
            (detail + "; " if detail else "")
            + f"MCP server {mcp['host']} ({mcp['transport']}): "
            + ("reachable" if mcp["status"] == "reachable" else "not checked yet")
        )
    if problem:
        status = "problem"
    tool = {
        "id": _unique(used, service.lower() or "tool"),
        "service": service,
        "name": service_name(service),
        "logo_key": LOGO_KEYS.get(service.strip().lower()),
        "identity": entry.get("identity") or "",
        "can": list(entry.get("can") or []),
        "scope": dict(entry.get("scope") or {}),
        "env": env,
        "note": entry.get("note") or "",
        "status": status,
        "detail": detail,
    }
    if mcp:
        tool["kind"], tool["mcp"] = "mcp", mcp
    if problem:
        tool["problem"] = problem
    return tool


def _mcp_view(mcp):
    """What the tool's row shows of an MCP server: its host, transport and the runner's check. Never the headers."""
    if not isinstance(mcp, dict) or not mcp.get("url"):
        return None
    return {
        "host": mcp_servers.host_of(str(mcp["url"])),
        "transport": mcp.get("transport") or "http",
        "status": mcp.get("status") or "unchecked",
    }


ENTRY_KEYS = ("service", "identity", "can", "scope", "env", "note")


def _entry_of(raw):
    """A reported tool as a `tools:` entry again: the fields a person sets, the MCP block without the runner's check."""
    entry = {k: raw[k] for k in ENTRY_KEYS if raw.get(k)}
    if isinstance(raw.get("mcp"), dict) and raw["mcp"].get("url"):
        entry["mcp"] = {k: raw["mcp"][k] for k in ("url", "transport", "headers") if raw["mcp"].get(k)}
    return entry


def _same(a, b):
    """Whether two entries are the same tool: one service, acting as one identity."""
    norm = lambda entry: (
        str(entry.get("service") or "").strip().lower(),
        str(entry.get("identity") or "").strip().lower(),
    )
    return norm(a) == norm(b)


def _app_token(entry):
    return (
        entry.get("credential") == "missing"
        and str(entry.get("service") or "").lower() == "github"
        and entry.get("env") in ("GH_TOKEN", "GITHUB_TOKEN")
    )


def github_app_covers(c, bot):
    """Whether each run gets GH_TOKEN/GITHUB_TOKEN minted from the GitHub App: the App is connected and the bot's
    repository is in its organization (the same test as POST /api/v2/github/token; runner_support/git_credentials.py)."""
    if not H._has_table(c, "github_app"):
        return False
    app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    if not app or not app["org"]:
        return False
    from .github_app import repo_of
    from .shared_bots import declared, source_of

    config = declared(c, bot)
    row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (source_of(config) or bot,)).fetchone()
    repo = repo_of(row["repo"] if row else "", app["org"])
    return bool(repo) and repo.split("/")[0].lower() == app["org"].lower()


def granted(c, bot, raw):
    """The reported tools, with a credential the computer says is missing counted present when the vault grants it to the bot.

    The runner reports only what its computer holds (a secrets file, a profile); a vault grant arrives with each run
    (`GET /api/v2/credential-runtime`), so the computer cannot see it but this server can. Only a grant a run would receive
    counts: not revoked, its delegation intact, and a stored value behind it, and so does a setting of a tool it holds
    such a key of (backend/tool_settings.py). A GitHub GH_TOKEN/GITHUB_TOKEN also counts
    when the connected GitHub App mints it for the bot. Names only, never a value."""
    if any(_app_token(e) for e in raw) and github_app_covers(c, bot):
        raw = [dict(e, credential="present", github_app=True) if _app_token(e) else e for e in raw]
    missing = {str(e.get("env")) for e in raw if e.get("credential") == "missing" and e.get("env")}
    if not missing:
        return raw
    from .credentials import granted
    from .read_cache import rows

    held = granted(c, "bot:" + bot)
    have = {
        row["env"]
        for row in rows(c, "SELECT id,env FROM credentials WHERE ciphertext IS NOT NULL AND env!=''")
        if row["env"] in missing and row["id"] in held
    }
    from .tool_settings import names

    have |= missing & set(names(c, held))  # a setting of a tool it holds a key of arrives with each run too
    return [
        dict(e, credential="present", granted=True) if e.get("credential") == "missing" and e.get("env") in have else e
        for e in raw
    ]


def agent_report(c, bot, tools):
    raw = []
    for tool in tools:
        try:
            entry = access_entry.clean(tool.model_dump())
        except access_entry.EntryError as exc:
            raise Problem(exc.code, str(exc), 422) from None
        raw.append(
            {key: entry[key] for key in ("service", "identity", "can", "env", "note", "mcp") if key in entry}
            | {"scope": access_entry.scope_of(entry), "credential": "not-declared"}
        )
    c.execute(
        "INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("agent-tools:" + bot, encode({"tools": raw, "reported_at": H.now()})),
    )
    reconcile(c, {bot: raw}, complete=True)


STATE_CONFIG_SQL = f"SELECT {BS.CONFIG} AS config,bc.repo FROM bot_config bc {BS.JOIN} WHERE bc.bot=?"
STATE_RUNNER_SQL = (
    "SELECT r.id,r.label,r.last_seen_at,r.revoked_at,r.readiness,r.capabilities FROM assignments a "
    "JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot=?"
)
REQUESTS_SQL = "SELECT * FROM bot_tool_requests WHERE bot=? AND state='pending' ORDER BY created_at,id"


def prime_state(c, bots):
    """Each bot's configuration, computer and pending tool requests, read together for every bot's listing (per 500
    bots, not three reads per bot). Outside a snapshot nothing is kept."""
    from .credentials import prime_granted
    from .read_cache import cache, prime, prime_rows

    if cache(c) is None:
        return
    prime_granted(c, ["bot:" + bot for bot in set(bots)])
    for part in H.chunks(sorted(set(bots))):
        marks = ",".join("?" * len(part))
        configs = {
            r["bot"]: r
            for r in c.execute(
                f"SELECT bot,{BS.CONFIG} AS config,repo FROM bot_config bc {BS.JOIN} WHERE bot IN ({marks})", part
            )
        }
        prime(c, STATE_CONFIG_SQL, {(s,): configs.get(s) for s in part})
        runners = {
            r["bot"]: r
            for r in c.execute(
                "SELECT a.bot,r.id,r.label,r.last_seen_at,r.revoked_at,r.readiness,r.capabilities FROM assignments a "
                f"JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot IN ({marks})",
                part,
            )
        }
        prime(c, STATE_RUNNER_SQL, {(s,): runners.get(s) for s in part})
        # The same computers, for the subscription lookups each Model row may make.
        prime(c, model_subscriptions.RUNNER_READINESS_SQL, {(r["id"],): r for r in runners.values()})
        prime(
            c,
            model_subscriptions.RUNNER_CAPABILITIES_SQL,
            {(r["id"],): None if r["revoked_at"] else r for r in runners.values()},
        )
        requests = {}
        for r in c.execute(
            f"SELECT * FROM bot_tool_requests WHERE bot IN ({marks}) AND state='pending' ORDER BY created_at,id", part
        ):
            requests.setdefault(r["bot"], []).append(r)
        prime_rows(c, REQUESTS_SQL, {(s,): requests.get(s, ()) for s in part})


def _state(c, settings, bot):
    from .read_cache import one

    row = one(c, STATE_CONFIG_SQL, (bot,))
    config = H._json(row["config"], {}) if row else {}
    config = config if isinstance(config, dict) else {}
    runner = one(c, STATE_RUNNER_SQL, (bot,))
    readiness = readiness_document(runner["readiness"]) if runner else {}
    report = (readiness.get("bots") or {}).get(bot)
    report = report if isinstance(report, dict) else {}
    label = (runner["label"] if runner else "") or "its computer"
    external = resolve_harness(config) in EXTERNAL_HARNESSES
    agent = (
        c.execute("SELECT profile,last_seen_at,revoked_at FROM agents WHERE bot=?", (bot,)).fetchone()
        if external
        else None
    )
    if external:
        stored = c.execute("SELECT value FROM registry_metadata WHERE key=?", ("agent-tools:" + bot,)).fetchone()
        report = H._json(stored[0], {}) if stored else {}
        label = (agent["profile"] if agent else "") or "its external profile"
    used = {"model", "repo"}
    raw = granted(c, bot, [entry for entry in report.get("tools") or [] if isinstance(entry, dict)])
    return {
        "row": row,
        "config": config,
        "runner": runner,
        "agent": agent,
        "readiness": readiness,
        "report": report,
        "label": label,
        "raw": raw,
        "declared": [_declared_tool(entry, used, label) for entry in raw],
    }


def _requests(c, bot):
    from .read_cache import rows

    return [dict(row, entry=H._json(row["entry"], {}) or {}) for row in rows(c, REQUESTS_SQL, (bot,))]


def _pending_tool(request):
    entry = request["entry"]
    service = entry.get("service") or request["service"]
    return {
        "id": "pending-" + request["id"],
        "service": service,
        "name": service_name(service),
        "logo_key": LOGO_KEYS.get(service),
        "identity": entry.get("identity") or "",
        "can": list(entry.get("can") or []),
        "scope": access_entry.scope_of(entry),
        "env": entry.get("env") or "",
        "note": entry.get("note") or "",
        "status": "pending",
        "pending": "add",
        "task_id": request["task_id"],
        "detail": "Waiting for BotOps to add it to the bot's bot.yaml; it shows as ready once the computer reports it",
        **({"kind": "mcp", "mcp": _mcp_view(entry["mcp"])} if _mcp_view(entry.get("mcp")) else {}),
    }


def listing(c, settings, bot, reads=None):
    """Everything the row shows for one bot, from records this server already holds. `reads` (a repositories.Reads
    covering `bot`) when the caller lists many bots."""
    slug = (H.bot(c, bot) or {}).get("slug") or bot
    state = _state(c, settings, bot)
    runner, report, label = state["runner"], state["report"], state["label"]
    online = bool(runner and not runner["revoked_at"] and runner_online.is_online(runner["last_seen_at"]))
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
                    tool["detail"] = (
                        "Removal requested; waiting for BotOps to take it out of bot.yaml"
                        if request["kind"] == "remove"
                        else "A change is requested; waiting for BotOps to update it in bot.yaml"
                    )
    # The computer holds the Google key and gives a token, but only to a message bot the server has named
    # (routines.token_mailboxes): for any other bot "present" would be a promise its runs cannot keep.
    # Only the Google key: a computer also holds other credentials for its bots (a vault grant), and
    # those reach the run whoever its message bot is.
    google = lambda entry: entry.get("held") and entry.get("env") == "GOOGLE_SA_KEY"
    covered = routines.token_mailboxes(c, bot)
    if covered and all(google_workspace.authority(c, box) == "workspace" for box in covered):
        # The server holds the Google Workspace key and mints each run's token (plan v5): this bot needs no key on its
        # computer, and its mailbox's workspace is the one the server covers.
        for tool, entry in zip(declared, state["raw"]):
            if entry.get("env") == "GOOGLE_SA_KEY":
                tool.update(status="ready", problem="", detail="ready (Google Workspace on the server)")
    elif any(google(entry) for entry in state["raw"]) and not covered:
        for tool, entry in zip(declared, state["raw"]):
            if google(entry):
                tool.update(
                    status="problem",
                    held=True,
                    detail="",
                    problem=(
                        f"{label} holds the Google key, but {slug} is nobody's message bot on the server, so its runs get no mail "
                        "token. An owner or admin names it: POST /api/v2/access/humans/<person> "
                        '{"inbox_bot": "' + bot + '", "mailbox": "<address>"}'
                    ),
                )
    adding = [
        _pending_tool(r)
        for r in requests
        if r["kind"] == "add" and not any(_same(entry, r["entry"]) for entry in state["raw"])
    ]
    if external:
        for tool in adding:
            tool["detail"] = "Waiting for this external profile to configure and report the tool"
    tools = [_model_tool(c, settings, bot, state["config"], state["readiness"], label, state["runner"])]
    repo = _repo_tool(settings, slug, state["row"] and state["row"]["repo"], report)
    if repo:
        matching = next(
            (
                tool
                for tool in declared
                if tool["service"].lower() in ("github", "github-app")
                and str(tool["scope"].get("repo") or tool["identity"]).lower() == repo["identity"].lower()
            ),
            None,
        )
        if matching:
            matching["detail"] += "; own repository: instructions and memory"
        else:
            tools.append(repo)
    from .repositories import access

    app_row = read_cache.one(c, "SELECT org, installation_id FROM github_app WHERE id='app'")
    # Connected and installed, as the GitHub card counts it: an App that was never installed reaches no repository.
    if repo and not (app_row and app_row["installation_id"]) and repo in tools:
        repo["detail"] += "; not backed up: GitHub is not connected"
    from .github_app import repo_of

    own = repo_of(state['row']['repo'] if state['row'] else '', settings.github_owner)
    org = app_row['org'] if app_row else settings.github_owner or (own.split('/')[0] if own else '')
    extras = access(c, bot, org, reads=reads)['effective'] if state['row'] else []
    represented = {
        str(tool.get("scope", {}).get("repo") or tool.get("identity") or "").lower()
        for tool in tools + declared
        if tool["service"].lower() in ("github", "github-app")
    }
    for index, grant in enumerate(extras):
        name = grant['full_name']
        if name.lower() in represented:
            continue
        tools.append(
            {
                "id": f"github-extra-{index}",
                "service": "github",
                "name": "GitHub",
                "logo_key": "github",
                "identity": name,
                "can": ["read", "write", "issues", "pull_requests"] if grant["access"] == "write" else ["read"],
                "scope": {"repo": name},
                "note": "",
                "url": "https://github.com/" + name,
                "status": "unknown",
                "detail": "Granted repository",
            }
        )
    agent = state["agent"]
    if agent:
        online = bool(
            not agent["revoked_at"] and agent["last_seen_at"] and agent["last_seen_at"] > H.shift(H.now(), seconds=-180)
        )
    report_error = next(
        (warning for warning in report.get("warnings") or [] if warning.startswith("Tool report rejected: ")), ""
    )
    for tool in adding:
        if report_error:
            tool.update(status="problem", problem=report_error, detail=report_error)
    return {
        "bot": bot,
        "tools": tools + declared + adding,
        **({"report_error": report_error} if report_error else {}),
        "computer": label if runner else None,
        "online": online,
        "reported_at": runner["last_seen_at"] if runner else report.get("reported_at"),
    }


# ----------------------------------------------------------------------------- granted in Tools
GRANT_KEYS_SQL = (
    "SELECT k.id, k.name, k.env, k.tool_id, k.access_level, k.kind, k.holder, k.ciphertext IS NOT NULL AS stored, "
    "t.name AS tool_name, t.service, COALESCE(t.kind,'api') AS tool_kind, COALESCE(t.mcp_url,'') AS mcp_url "
    "FROM credentials k LEFT JOIN tools t ON t.id=k.tool_id ORDER BY lower(coalesce(t.name, k.name)), k.id"
)
GRANT_LEVELS_SQL = "SELECT credential_id, access_level FROM credential_grants WHERE subject=? AND revoked_at IS NULL"
LEVEL_WORDS = {"read": "Read", "read_write": "Read and write"}


def _granted_tools(c, bot):
    """The tools `bot` can use through Tools, whatever its bot.yaml says: each API or MCP tool it holds a key of (at
    its highest level), each key on no tool, and each website tool with an account it may use. Names and levels only,
    never a value. A bot holding a working key is never shown as having nothing connected."""
    from . import api_tools, website_tools
    from .credentials import explicitly_granted, granted as held_ids
    from .read_cache import one, rows

    subject = "bot:" + bot
    held = held_ids(c, subject)
    by_default = held - explicitly_granted(c, subject)  # read by default (backend/default_read.py)
    levels = {r["credential_id"]: r["access_level"] for r in rows(c, GRANT_LEVELS_SQL, (subject,))}
    found = {}
    for key in rows(c, GRANT_KEYS_SQL):
        if key["kind"] == "login":
            if not (key["holder"] == subject or (not key["holder"] and website_tools.can_use(c, subject, key))):
                continue
            level = "read_write"
        elif key["id"] in by_default:
            level = "read"
        elif key["id"] in held:
            level = "read" if key["access_level"] == "read" or levels.get(key["id"]) == "read" else "read_write"
        else:
            continue
        group = key["tool_id"] or "key:" + key["id"]
        item = found.setdefault(
            group, {"key": key, "level": level, "stored": False, "envs": [], "default": key["id"] in by_default}
        )
        if level == "read_write":
            item["level"] = "read_write"
        item["stored"] = item["stored"] or bool(key["stored"])
        if key["env"] and key["env"] not in item["envs"]:
            item["envs"].append(key["env"])
    out = []
    for group, item in found.items():
        key, level = item["key"], item["level"]
        tool_id = key["tool_id"]
        kind = "website" if key["tool_kind"] == "website" else "mcp" if key["mcp_url"] else "api" if tool_id else "key"
        service = str(key["service"] or "").strip().lower()
        status, problem = "ready", ""
        if tool_id and kind in ("api", "mcp"):
            tool = one(c, "SELECT * FROM tools WHERE id=?", (tool_id,))
            if tool is not None:
                state = api_tools.state_of(c, tool)
                if state["tone"] == "fail":
                    status, problem = "problem", state["text"]
        elif not item["stored"] and kind != "website":
            status, problem = "problem", "No key stored"
        href = (
            f"#/tools/{'website' if kind == 'website' else 'api'}/{tool_id}"
            if tool_id
            else "#/tools/keys?show=credentials"
        )
        row = {
            "id": "granted-" + group.replace(":", "-"),
            "service": service,
            "name": key["tool_name"] or key["name"] or service_name(service),
            "logo_key": LOGO_KEYS.get(service),
            "identity": "",
            "can": ["read"] if level == "read" else ["read", "write"],
            "scope": {},
            "env": ", ".join(item["envs"]),
            "note": "",
            "status": status,
            "detail": "Everyone can read" if item["default"] else "Granted in Tools: " + LEVEL_WORDS[level],
            "granted": {
                "level": level,
                "kind": kind,
                "href": href,
                **({"tool_id": tool_id} if tool_id else {}),
                **({"default": True} if item["default"] else {}),
            },
        }
        if kind == "mcp":
            row["kind"] = "mcp"
        if problem:
            row["problem"] = problem
        out.append(row)
    return out


def _choices(c, bot, tools):
    """A row per variable several Everyone-can-read tools would deliver and `bot` holds no grant for
    (backend/default_read.py `choices`): it gets none of them until a person picks one, so the row says so. Left out
    when a row already shows that variable (a bot.yaml entry naming it)."""
    from . import default_read
    from .credentials import explicitly_granted

    shown = {e.strip() for tool in tools for e in str(tool.get("env") or "").split(",") if e.strip()}
    out = []
    for env, tool_ids in sorted(default_read.choices(c, "bot:" + bot, explicitly_granted(c, "bot:" + bot)).items()):
        if env in shown:
            continue
        names = [
            r["name"]
            for r in c.execute("SELECT name FROM tools WHERE id IN (%s)" % ",".join("?" * len(tool_ids)), tool_ids)
        ]
        out.append(
            {
                "id": "choose-" + env.lower(),
                "service": "",
                "name": env,
                "logo_key": None,
                "identity": "",
                "can": [],
                "scope": {},
                "env": env,
                "note": "",
                "status": "unknown",
                "detail": "Several to choose from: ask (" + ", ".join(sorted(names)) + ")",
                "choose": sorted(tool_ids),
            }
        )
    return out


def _merge_granted(tools, granted):
    """One row per service: a declared entry and a grant for the same service (or the same variable) are one row, the
    declared one (its identity, scope and note) marked granted; the grants with no declared entry follow as their own
    rows. A grant is what the bot can use, so a working grant makes the row ready whatever variable the entry names."""
    rest = []
    for grant in granted:
        envs = set(filter(None, (e.strip() for e in grant["env"].split(","))))
        match = next(
            (
                tool
                for tool in tools
                if tool.get("id") not in ("model", "repo")
                and not tool.get("granted")
                and (
                    (grant["service"] and str(tool.get("service") or "").strip().lower() == grant["service"])
                    or (tool.get("env") and tool["env"] in envs)
                )
            ),
            None,
        )
        if match is None:
            rest.append(grant)
            continue
        match["granted"] = grant["granted"]
        missing = str(match.get("problem") or "").startswith("Credential missing on ")
        if grant["status"] == "ready" and not match.get("pending") and (match.get("status") == "unknown" or missing):
            # The key reaches each run from the vault: an entry naming another variable for it is a note, not a gap.
            match.pop("problem", None)
            match.update(status="ready", detail=grant["detail"])
        elif grant.get("problem") and match.get("status") != "problem":
            match.update(status="problem", problem=grant["problem"])
    return tools + rest


# ----------------------------------------------------------------------------- registering
VERBS = {"add": ("Add", "to"), "remove": ("Remove", "from"), "update": ("Change", "on")}


def _task_text(verb, bot, name, entry, computer, repo=None):
    # The bot's recorded repository (`emp-<slug>` for a bot made before `bot-<slug>`), and its manifest under either name.
    where = str(repo or "bot-" + bot).rsplit("/", 1)[-1] + "/bot.yaml (employee.yaml in a repository not yet renamed)"
    if verb == "add":
        do = f"Add this entry to the `tools:` list (older: `access:`) in {where}, keeping the entries already there, then commit locally:"
    elif verb == "update":
        do = (
            f"Replace the entry for this service and identity in the `tools:` list (older: `access:`) in {where} with this one "
            "(match it by service and identity; keep every other entry and do not remove and re-add it), then commit locally:"
        )
    else:
        do = f"Remove this entry from the `tools:` list (older: `access:`) in {where} (match it by service and identity), then commit locally:"
    return "\n".join(
        [
            f"{VERBS[verb][0]} {name} access {VERBS[verb][1]} {bot}.",
            "",
            do,
            "",
            "```yaml",
            access_entry.to_yaml(entry),
            "```",
            "",
            *(
                [
                    "The `mcp:` block is a remote MCP server: Tico's runner passes it to the bot's harness, and `${VAR}` in a header is "
                    "filled from the bot's granted credential at run time. Keep the placeholder as it is; never write the value in "
                    "the file. Once it is granted, check it with one read-only call (docs/connect-tools.md).",
                    "",
                ]
                if entry.get("mcp")
                else []
            ),
            "Commit the bot repository locally and run `tico bot check "
            + bot
            + "`. The bot publishes its own repository "
            "with its own access on its next run. Say on this task what the check reported. "
            "Tico shows the entry on the bot's page once the computer's readiness report "
            + {"add": "lists it.", "update": "lists it as changed.", "remove": "no longer lists it."}[verb],
            "",
            CREDENTIALS_NOTE
            + (" Do not ask for the value here, and never commit it." if verb == "add" else "")
            + (f" The computer is {computer}." if computer else ""),
        ]
    )


def _request_task(c, auth, who, verb, bot, name, entry, computer, taken, repo=None, title_prefix=""):
    from . import getting_started as G

    external = resolve_harness(H._json(c.execute(BS.ONE_SQL, (bot,)).fetchone()[0], {})) in EXTERNAL_HARNESSES
    if not external:
        G._botops(c)
    slug = (H.bot(c, bot) or {}).get("slug") or bot
    title = f"{VERBS[verb][0]} {name} access {VERBS[verb][1]} {slug}"
    if title_prefix:
        title = title_prefix.strip() + " " + title
    if taken:
        title += f" ({taken + 1})"  # the hub refuses a second live task with the same title
    if external:
        text = (
            f"{VERBS[verb][0]} {name} in your external profile. Use the entry below as the tool's scope. "
            "For Hermes, configure the tool or MCP server in this profile's config.yaml; for OpenClaw, "
            "configure the profile's tool or skill. Keep credential values in the profile's credential store, never on this task.\n\n"
            + access_entry.to_yaml(entry)
            + "\nAfter checking the tool, report the full current list with "
            "tico_tool_report (or python3 hermes_agent.py call --profile <name> tico_tool_report '{\"tools\": [...]}'). "
            "Use an empty list when the last tool was removed. Then finish this task."
        )
        return G._task(c, auth, who, bot, title, text)
    return G._task(c, auth, who, G.BOTOPS, title, _task_text(verb, slug, name, entry, computer, repo))


def covered(c, bot, entry):
    """The bot's grant row (`_granted_tools`) that already gives what `entry` names, by its service or its variable,
    when the entry adds no note (identity, scope, note or an MCP server): such an entry needs no bot.yaml line, and so no
    task for BotOps. None otherwise."""
    if entry.get("identity") or entry.get("note") or entry.get("mcp") or access_entry.scope_of(entry):
        return None
    service, env = str(entry.get("service") or "").strip().lower(), str(entry.get("env") or "")
    for row in _granted_tools(c, bot):
        envs = {e.strip() for e in row["env"].split(",") if e.strip()}
        if (service and row["service"] == service) or (env and env in envs):
            return row
    return None


def register(c, auth, settings, who, bot, body, admins=()):
    auth.domain(who)
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    TA.require(
        c, who, TA.bot(bot), action=TA.NOTE, admins=admins, auth=auth
    )  # entries are notes: backend/tool_access.py
    try:
        entry = access_entry.clean(body.model_dump())
    except access_entry.EntryError as exc:
        raise Problem(exc.code, str(exc), 422) from None
    if body.dry_run:
        return {"ok": True, "yaml": access_entry.to_yaml(entry), "credentials": CREDENTIALS_NOTE}
    grant = covered(c, bot, entry)
    if grant is not None:
        # Runs are built from grants (credential-runtime): a granted tool reaches the bot and shows on its Tools tab
        # with no bot.yaml entry, so there is nothing to register and no task to file.
        return {
            "tool": grant,
            "granted": True,
            "task_id": None,
            "yaml": access_entry.to_yaml(entry),
            "credentials": CREDENTIALS_NOTE,
        }
    state = _state(c, settings, bot)
    if any(_same(entry, declared) for declared in state["raw"]) or any(
        r["kind"] == "add" and _same(entry, r["entry"]) for r in _requests(c, bot)
    ):
        raise Problem("duplicate", "This bot already has that tool, or a request for it is waiting for BotOps", 409)
    taken = c.execute(
        "SELECT count(*) FROM bot_tool_requests WHERE bot=? AND service=? AND kind='add' AND state='pending'",
        (bot, entry["service"]),
    ).fetchone()[0]
    name = service_name(entry["service"])
    task = _request_task(
        c,
        auth,
        who,
        "add",
        bot,
        name,
        entry,
        state["runner"] and state["label"],
        taken,
        state["row"] and state["row"]["repo"],
        body.title_prefix,
    )
    rid = H.new_id()
    c.execute(
        "INSERT INTO bot_tool_requests(id,bot,kind,service,identity,entry,task_id,requested_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (rid, bot, "add", entry["service"], entry.get("identity", ""), encode(entry), task["id"], who.actor, H.now()),
    )
    H.event(c, who.actor, "bot.tool_requested", bot, {"service": entry["service"], "task": task["id"]})
    tool = _pending_tool(dict(id=rid, entry=entry, service=entry["service"], task_id=task["id"]))
    if resolve_harness(state["config"]) in EXTERNAL_HARNESSES:
        tool["detail"] = "Waiting for this external profile to configure and report the tool"
    return {"tool": tool, "task_id": task["id"], "yaml": access_entry.to_yaml(entry), "credentials": CREDENTIALS_NOTE}


def unregister(c, auth, settings, who, bot, tool_id, admins=()):
    auth.domain(who)
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    TA.require(
        c, who, TA.bot(bot), action=TA.NOTE, admins=admins, auth=auth
    )  # entries are notes: backend/tool_access.py
    requests = _requests(c, bot)
    if tool_id.startswith("pending-"):
        found = next((r for r in requests if "pending-" + r["id"] == tool_id and r["kind"] == "add"), None)
        if not found:
            raise Problem("not_found", "That request is not waiting any more", 404)
        c.execute("UPDATE bot_tool_requests SET state='canceled' WHERE id=?", (found["id"],))
        H.attempt(c, H.task_close, H.KEEPER, found["task_id"], "Tool request withdrawn; do not apply this change.")
        H.event(c, who.actor, "bot.tool_request_canceled", bot, {"service": found["service"], "task": found["task_id"]})
        return {
            "canceled": True,
            "task_id": found["task_id"],
            "detail": "The request is withdrawn. If BotOps already added the entry it stays until you remove it.",
        }
    if tool_id in ("model", "repo"):
        raise Problem(
            "tool",
            "A bot always has read, write, issues and pull requests on its own repository, and its model is chosen in "
            "its settings; neither is a Tool you change or remove here",
            422,
        )
    state = _state(c, settings, bot)
    index = next((i for i, tool in enumerate(state["declared"]) if tool["id"] == tool_id), None)
    if index is None:
        raise Problem("not_found", "This bot does not declare that tool", 404)
    entry = _entry_of(state["raw"][index])
    if any(r["kind"] == "remove" and _same(entry, r["entry"]) for r in requests):
        raise Problem("duplicate", "Its removal is already waiting for BotOps", 409)
    if any(r["kind"] == "update" and _same(entry, r["entry"]) for r in requests):
        raise Problem("duplicate", "A change to it is already waiting for BotOps; remove it once that is done", 409)
    flat = {
        "service": entry["service"],
        **({"identity": entry["identity"]} if entry.get("identity") else {}),
        "can": entry.get("can") or [],
        **(entry.get("scope") or {}),
        **({"mcp": entry["mcp"]} if entry.get("mcp") else {}),
        **({"env": entry["env"]} if entry.get("env") else {}),
        **({"note": entry["note"]} if entry.get("note") else {}),
    }
    taken = c.execute(
        "SELECT count(*) FROM bot_tool_requests WHERE bot=? AND service=? AND kind='remove' AND state='pending'",
        (bot, entry["service"]),
    ).fetchone()[0]
    name = service_name(entry["service"])
    task = _request_task(
        c, auth, who, "remove", bot, name, flat, state["label"], taken, state["row"] and state["row"]["repo"]
    )
    c.execute(
        "INSERT INTO bot_tool_requests(id,bot,kind,service,identity,entry,task_id,requested_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            H.new_id(),
            bot,
            "remove",
            entry["service"],
            entry.get("identity", ""),
            encode(entry),
            task["id"],
            who.actor,
            H.now(),
        ),
    )
    H.event(c, who.actor, "bot.tool_removal_requested", bot, {"service": entry["service"], "task": task["id"]})
    return {"removal": True, "tool": tool_id, "task_id": task["id"]}


def _declared_entry(state, tool_id):
    """The raw entry of the bot's declared tool with this id, or a Problem."""
    if tool_id in ("model", "repo"):
        raise Problem(
            "tool",
            "A bot always has read, write, issues and pull requests on its own repository, and its model is chosen in "
            "its settings; neither is a Tool you change or remove here",
            422,
        )
    index = next((i for i, tool in enumerate(state["declared"]) if tool["id"] == tool_id), None)
    if index is None:
        raise Problem("not_found", "This bot does not declare that tool", 404)
    return _entry_of(state["raw"][index])


def _scope(entry):
    """An entry's scope as {key: [text]}: one value and a list of one are the same scope."""
    return {
        k: [str(i) for i in (v if isinstance(v, list) else [v])]
        for k, v in access_entry.scope_of(entry.get("scope") or entry).items()
    }


def _changed(entry, reported):
    """Whether a reported entry already says what `entry` (a change's new entry) says."""
    return (
        list(reported.get("can") or []) == list(entry.get("can") or [])
        and _scope(reported) == _scope(entry)
        and str(reported.get("note") or "") == str(entry.get("note") or "")
        and _entry_of(reported).get("mcp") == entry.get("mcp")
    )


def update(c, auth, settings, who, bot, tool_id, body, admins=()):
    """Change a declared tool in place: only `can`, `scope`, `note` and an MCP server's `mcp` block. The server holds no bot repository, so this is
    the add path's way: a pending request and one task for BotOps with the whole changed entry. Nothing is removed."""
    auth.domain(who)
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
    if not H.bot(c, bot):
        raise Problem("not_found", "Bot not found", 404)
    TA.require(
        c, who, TA.bot(bot), action=TA.NOTE, admins=admins, auth=auth
    )  # entries are notes: backend/tool_access.py
    if tool_id.startswith("pending-"):
        raise Problem(
            "tool", "That tool is not on the bot yet; withdraw the request and add it again with the change", 422
        )
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
    taken = c.execute(
        "SELECT count(*) FROM bot_tool_requests WHERE bot=? AND service=? AND kind='update' AND state='pending'",
        (bot, entry["service"]),
    ).fetchone()[0]
    task = _request_task(
        c,
        auth,
        who,
        "update",
        bot,
        service_name(entry["service"]),
        entry,
        state["label"],
        taken,
        state["row"] and state["row"]["repo"],
        body.title_prefix,
    )
    c.execute(
        "INSERT INTO bot_tool_requests(id,bot,kind,service,identity,entry,task_id,requested_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            H.new_id(),
            bot,
            "update",
            entry["service"],
            entry.get("identity", ""),
            encode(entry),
            task["id"],
            who.actor,
            H.now(),
        ),
    )
    H.event(c, who.actor, "bot.tool_update_requested", bot, {"service": entry["service"], "task": task["id"]})
    return {
        "update": True,
        "tool": tool_id,
        "task_id": task["id"],
        "yaml": access_entry.to_yaml(entry),
        "credentials": CREDENTIALS_NOTE,
    }


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
                settled = (
                    listed
                    if request["kind"] == "add"
                    else (not listed and (complete or raw or request["created_at"] < H.shift(H.now(), seconds=-600)))
                )
            if settled:
                c.execute("UPDATE bot_tool_requests SET state='done' WHERE id=?", (request["id"],))


def install(app, store, auth, mutate, settings_admin, requester=None):
    """`requester(c, who)` is the person BotOps is acting for when `who` is BotOps in a turn a person's chat message
    started, else `who`: a tool is a bot's owner's to add, and BotOps is nobody's bot but the requester's to manage."""
    acting = requester or (lambda c, who: who)

    def admins():
        vault = getattr(app.state, "vault", None)
        return vault.admins if vault is not None else ()

    def shown_grants(c, who, bot):
        """The bot's grants this reader may know of: a tool they may see (`api_tools.visible`), or one for Everyone when
        they are a credential admin, the bot itself or someone who manages it; a key on no tool for those three only. An
        Only listed tool stays hidden from anyone it does not list."""
        from . import api_tools
        from .credentials import administrator

        rows, keep = _granted_tools(c, bot), []
        close = administrator(c, who, admins()) or who.actor == "bot:" + bot or auth.bot_manager(c, who, bot)
        for row in rows:
            tool_id = row["granted"].get("tool_id")
            tool = c.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone() if tool_id else None
            if tool is not None:
                if api_tools.visible(c, who, tool, admins(), auth) or (close and api_tools.audience_of(tool) is None):
                    keep.append(row)
            elif close:
                keep.append(row)
        return keep

    @app.get(
        "/api/v2/bots/{bot}/tools",
        **api_v2(
            "Bots",
            "listBotTools",
            "The tools a bot uses, for the row at the top of its page: its model and harness, its repository, each declared `tools:` entry and each tool granted to it in Tools, with its status; names and verbs, never a secret",
            "BotTools",
        ),
    )
    def bot_tools(request: Request, bot: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.require_read(c, who, bot)  # a 404 for someone who cannot even see the bot
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            result = listing(c, store.settings, bot)
            result["tools"] = _merge_granted(result["tools"], shown_grants(c, who, bot))
            from .credentials import administrator

            if administrator(c, who, admins()) or who.actor == "bot:" + bot or auth.bot_manager(c, who, bot):
                result["tools"] += _choices(c, bot, result["tools"])
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

    @app.post(
        "/api/v2/bots/{bot}/tools",
        **api_v2(
            "Bots",
            "registerBotTool",
            "Add a note entry for a bot you manage: validated, kept as a pending request and handed to BotOps as a task with the exact `tools:` entry. One that only names a tool the bot already holds through a grant opens no task. Never a credential: `env` is a variable's name",
            "BotToolRegistered",
        ),
    )
    def register_tool(request: Request, bot: str, body: M.ToolRegister):
        """Register a tool for a bot: checked, kept as a pending request, and handed to BotOps as a task
        with the exact `tools:` entry. A credential is never accepted; `env` is a variable's name."""
        who = request.state.identity
        return mutate(
            request,
            body,
            lambda c: register(c, auth, store.settings, acting(c, who), bot, body, admins()),
        )

    def remove(request, bot, tool_id):
        who = request.state.identity
        return mutate(
            request,
            M.Empty(),
            lambda c: unregister(c, auth, store.settings, acting(c, who), bot, tool_id, admins()),
        )

    @app.post(
        "/api/v2/bots/{bot}/tools/{tool_id}/update",
        **api_v2(
            "Bots",
            "updateBotTool",
            "Change a declared tool's `can`, `scope` or `note` in place (bot managers): one BotOps task with the changed entry; the tool shows `pending: update` until the computer reports it",
            "BotToolUpdated",
        ),
    )
    def update_tool(request: Request, bot: str, tool_id: str, body: M.ToolUpdate):
        """Change a declared tool's `can`, `scope` or `note` in place: one task for BotOps with the changed entry,
        the tool shown as pending its change. BotOps acts for the person who asked it, like `register_tool`."""
        who = request.state.identity
        return mutate(
            request,
            body,
            lambda c: update(c, auth, store.settings, acting(c, who), bot, tool_id, body, admins()),
        )

    @app.delete(
        "/api/v2/bots/{bot}/tools/{tool_id}",
        **api_v2(
            "Bots",
            "removeBotTool",
            "Ask BotOps to remove a declared tool, or withdraw a pending request (bot managers)",
            "BotToolRemoval",
        ),
    )
    def delete_tool(request: Request, bot: str, tool_id: str):
        """Ask BotOps to remove a declared tool (or withdraw a pending request)."""
        return remove(request, bot, tool_id)

    @app.post(
        "/api/v2/bots/{bot}/tools/{tool_id}/delete",
        **api_v2(
            "Bots", "removeBotToolPost", "The same as DELETE, for clients that only send GET and POST", "BotToolRemoval"
        ),
    )
    def delete_tool_post(request: Request, bot: str, tool_id: str, body: M.Empty):
        """The same as DELETE, for clients (the tico CLI, MCP) that only send GET and POST."""
        return remove(request, bot, tool_id)
