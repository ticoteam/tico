#!/usr/bin/env python3
"""The company's one Slack connector. Every bot calls this; nobody calls the Slack API directly.

  connectors/slack.py doctor
  connectors/slack.py channels
  connectors/slack.py history --as doc-updater --channel '#release_notes' --since yesterday --threads --format md
  connectors/slack.py post --as doc-updater --channel '#agents' --text 'shipped 3 docs'
  connectors/slack.py join --channel C0000000002
  connectors/slack.py dm --as doc-updater --to someone@example.com --text 'the pricing doc is live'
  connectors/slack.py inbox --since 24h --format md --mark
  connectors/slack.py file --as qa-triage --id F0123ABCD

One Slack app serves the whole company. The token is `SLACK_BOT_TOKEN` in the run's environment
(the runner loads it from <projects>/secrets/_shared.env). Install: see connectors/README.md.

Posting is gated in code, not in prompts:
  - the employee's bot.yaml (older: emp-<slug>/employee.yaml) must declare tools: service slack with `post` in `can`
  - the channel must be on the company's Slack channel list (Settings > Tools > Slack, `hub slack channel list`)
    and that entry must allow bots to post (it does unless an owner or admin turned it off)
  - externally shared (Slack Connect) channels are refused: posting there is an outbound send
  - text over 4000 characters is refused
  - every accepted post is appended to <projects>/runtime/slack-audit.jsonl

Channel reads are scoped when an employee's Slack access entry declares `channels:`; a channel whose list entry
names the bot as a reader is in that scope too. `history` uses `--as <slug>`, or `HUB_BOT` (older: `HUB_EMPLOYEE`)
inside a hosted run, and refuses any channel outside the scope before calling Slack. Manifests without
`channels:` retain their existing broad read access; new narrowly-scoped monitors should always declare it.

The channel list lives in Tico's database. In a hosted run (`HUB_API_URL` and `HUB_TOKEN`) this script reads it from
the hub; the old registry/slack-channels.yaml is read too, until an owner or admin imports it in Settings.

DM reads can likewise be disabled with `dms: false`. `inbox` uses `--as <slug>` or
`HUB_BOT` and refuses before calling Slack when that flag is present.

DMs are gated the same way, plus one more rule: a bot may only DM a company human.
  - the employee needs the same `post` verb on its slack access entry
  - every recipient must resolve to a Slack user whose email is in registry/hub-access.yaml
    (`owner` plus `allowed`) - the list of humans who may use the hub
  - bots and deactivated accounts are refused; a DM to several people opens a group DM
  - text over 4000 characters is refused, and every accepted DM is audited with kind "dm"

Files people share (a screenshot in a DM or a channel) show as `[file F...] name` in `history`, `inbox` and
a routed Slack message. `file --id F...` downloads one with the bot token (the app needs `files:read`), only
from a channel the bot may read or a DM when its DMs are not off, and only from Slack's own file host. By
default it lands in this user's own slack-files-<uid> folder under the temp folder (made 0700; one owned by
someone else is refused), named <id>-<name> with the name cut to 200 bytes and control characters dropped.

Nothing in Slack wakes a bot. People DM the bot; `inbox` is how a run reads those DMs.

Exit codes: 0 ok, 1 failure (auth, network, Slack error), 2 policy refusal.
"""

import argparse, json, os, re, stat, sys, tempfile, time, unicodedata
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:                                     # pragma: no cover - py<3.9
    ZoneInfo = None
try:
    import yaml
except ImportError:                                     # pragma: no cover
    yaml = None

HUB = Path(__file__).resolve().parent.parent            # .../tico
# The workspace with the bot repositories. A source checkout sits beside them; an installed release or
# a Linux runner does not, and the runner names the workspace in TICO_PROJECTS_DIR, as mail/ reads it.
PROJECTS = Path(os.environ.get("TICO_PROJECTS_DIR") or HUB.parent).expanduser()   # .../tico-work
REGISTRY = Path(os.environ.get("TICO_REGISTRY_DIR") or HUB / "registry")
CHANNELS_FILE = REGISTRY / "slack-channels.yaml"
HUB_ACCESS_FILE = REGISTRY / "hub-access.yaml"
AUDIT_FILE = PROJECTS / "runtime" / "slack-audit.jsonl"
USER_CACHE = PROJECTS / "runtime" / "slack-users.json"
DIRECTORY_CACHE = PROJECTS / "runtime" / "slack-user-directory.json"
INBOX_FILE = PROJECTS / "runtime" / "slack-inbox.json"
USER_CACHE_TTL = 24 * 3600
MAX_POST_CHARS = 4000
MAX_FILE_BYTES = 50 * 1024 * 1024
# Outside every repository, and one folder per local user: a shared /tmp must not hand it to someone else.
FILES_DIR = Path(tempfile.gettempdir()) / (f"slack-files-{os.getuid()}" if hasattr(os, "getuid")
                                           else "slack-files")
MAX_NAME_BYTES = 200                                    # file systems refuse a name over 255 bytes
DEFAULT_TZ = "America/Los_Angeles"
API = "https://slack.com/api/"
SECRETS_HINT = "<projects>/secrets/_shared.env"
NOISE_SUBTYPES = {"channel_join", "channel_leave", "group_join", "group_leave"}

JSON_ERRORS = False                                     # set by --json / --format json


# ---------------------------------------------------------------- errors

class Failure(Exception):
    """Something went wrong (auth, network, Slack said no). Exit 1."""
    def __init__(self, msg, hint=""):
        super().__init__(msg)
        self.msg, self.hint = msg, hint


class Refused(Failure):
    """A hub policy refused the action. Exit 2."""


ERROR_HINTS = {
    "invalid_auth": "SLACK_BOT_TOKEN is not valid. Reinstall the app and copy the Bot User OAuth "
                    "Token (xoxb-...) into " + SECRETS_HINT + ".",
    "not_authed": "No token was sent. Set SLACK_BOT_TOKEN in " + SECRETS_HINT + ".",
    "account_inactive": "The Slack app's bot user is deactivated. Reinstall the app.",
    "token_revoked": "The token was revoked. Reinstall the app and update " + SECRETS_HINT + ".",
    "channel_not_found": "No such channel for this token. Check the channel in "
                         "Settings > Tools > Slack. A private channel is invisible until "
                         "someone runs /invite @hub in it.",
    "not_in_channel": "The bot is not a member of that channel. Public: run "
                      "`connectors/slack.py join --channel <id>`. Private: ask the owner to run "
                      "/invite @hub in the channel.",
    "is_archived": "That channel is archived. Nothing can be posted to it.",
    "msg_too_long": "Slack rejected the message as too long. Split it.",
    "restricted_action": "Workspace settings forbid this app from posting there. Ask the owner.",
    "cant_invite_self": "The bot is already in that channel.",
    "method_not_supported_for_channel_type": "That method does not work on this channel type "
                                             "(DMs and Slack Connect channels are not supported).",
    "users_not_found": "No Slack account in this workspace has that email. Check the address, or "
                       "ask the owner whether that person is in Slack at all.",
    "user_not_found": "No such Slack user id for this token.",
    "cannot_dm_bot": "Slack will not let one app DM another bot. Bots talk through hub Issues.",
    "user_not_visible": "This token cannot see that user. The app needs the users:read scope.",
}

HUB_ACCESS_HINT = ("add them to registry/hub-access.yaml if they work at the company; "
                   "bots never DM outside the company")


def slack_error(method, data):
    code = data.get("error", "unknown_error")
    hint = ERROR_HINTS.get(code, "")
    if code == "missing_scope":
        needed = data.get("needed", "?")
        hint = (f"The Slack app is missing the {needed} bot scope (has: {data.get('provided','?')}). "
                f"Add it at api.slack.com/apps > OAuth & Permissions > Bot Token Scopes, reinstall "
                f"the app, then update SLACK_BOT_TOKEN in {SECRETS_HINT}. "
                f"connectors/slack-app-manifest.yaml lists every scope this tool needs.")
    return Failure(f"Slack {method} failed: {code}", hint)


# ---------------------------------------------------------------- http

def _request(url, data, headers, timeout):
    """The only place that touches the network. Tests replace this."""
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                                     # the 30x comes back as itself


def _download(url, headers, timeout, limit):
    """A file's bytes from url_private. Redirects are not followed: Slack answers a token without
    files:read with a redirect to its sign-in page, and the token must not travel to another host.
    Tests replace this."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(limit + 1)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b""


def token_or_die():
    tok = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if not tok:
        raise Failure("SLACK_BOT_TOKEN is not set in this run's environment.",
                      "The owner installs the company Slack app once and puts "
                      f"SLACK_BOT_TOKEN=xoxb-... into {SECRETS_HINT} (chmod 600). "
                      "See connectors/README.md. Open a needs-human Issue rather than improvising.")
    return tok


def call(method, params=None, post=False, token=None, timeout=30, sleep=time.sleep):
    """One Slack Web API call. Honours 429 Retry-After; retries 5xx three times."""
    token = token or token_or_die()
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    headers = {"Authorization": "Bearer " + token, "User-Agent": "tico-slack/1"}
    url, body = API + method, None
    if post:
        body = json.dumps(params).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    else:
        q = urllib.parse.urlencode(params)
        url += ("?" + q) if q else ""

    last = ""
    for attempt in range(4):                            # 1 try + 3 retries
        try:
            status, hdrs, raw = _request(url, body, headers, timeout)
        except Exception as e:                          # URLError, socket timeout, ...
            last = f"cannot reach Slack ({e})"
            if attempt == 3:
                raise Failure(f"Slack {method}: {last}", "Check the machine's network.")
            sleep(2 ** attempt)
            continue
        if status == 429:
            wait = 1
            for k, v in hdrs.items():
                if k.lower() == "retry-after":
                    try:
                        wait = int(float(v))
                    except (TypeError, ValueError):
                        wait = 1
            last = f"rate limited, Retry-After {wait}s"
            if attempt == 3:
                raise Failure(f"Slack {method}: {last}",
                              "Slow down: fewer channels or a shorter --since window.")
            sleep(wait + 1)
            continue
        if 500 <= status < 600:
            last = f"HTTP {status}"
            if attempt == 3:
                raise Failure(f"Slack {method}: {last} after 3 retries", "Slack is having trouble.")
            sleep(2 ** attempt)
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise Failure(f"Slack {method}: unreadable response (HTTP {status})")
        if not data.get("ok"):
            raise slack_error(method, data)
        return data
    raise Failure(f"Slack {method}: {last}")            # pragma: no cover


def paginate(method, params, key, token=None, max_pages=50):
    """Cursor pagination, flattened."""
    out, cursor, params = [], "", dict(params)
    for _ in range(max_pages):
        if cursor:
            params["cursor"] = cursor
        data = call(method, params, token=token)
        out.extend(data.get(key) or [])
        cursor = ((data.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    return out


# ---------------------------------------------------------------- time (pure)

def zone(name):
    if not name:
        name = DEFAULT_TZ
    if ZoneInfo is None:                                # pragma: no cover
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        raise Failure(f"unknown timezone {name!r}", "Use an IANA name like America/Los_Angeles.")


REL_RE = re.compile(r"^(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days|w|week|weeks)$")
REL_UNITS = {"m": 60, "min": 60, "mins": 60, "minutes": 60,
             "h": 3600, "hr": 3600, "hrs": 3600, "hours": 3600,
             "d": 86400, "day": 86400, "days": 86400,
             "w": 604800, "week": 604800, "weeks": 604800}


def day_start(d, tz):
    """Midnight of a date in tz, DST-safe."""
    return datetime(d.year, d.month, d.day, tzinfo=tz)


def parse_when(text, tz, now):
    """'now' | '24h' | '90m' | 'today' | 'yesterday' | ISO date or datetime -> aware datetime."""
    s = (text or "").strip().lower()
    if not s:
        raise Failure("empty time value")
    if s == "now":
        return now
    if s == "today":
        return day_start(now.date(), tz)
    if s == "yesterday":
        return day_start(now.date() - timedelta(days=1), tz)
    m = REL_RE.match(s)
    if m:
        return now - timedelta(seconds=int(m.group(1)) * REL_UNITS[m.group(2)])
    try:
        dt = datetime.fromisoformat((text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise Failure(f"cannot read time {text!r}",
                      "Use an ISO date/time (2026-09-01, 2026-09-01T09:00), a relative window "
                      "(24h, 90m, 7d), 'today', or 'yesterday'.")
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def time_range(since, until, tz_name=DEFAULT_TZ, now=None):
    """(start, end) as aware datetimes. 'yesterday' with no --until is the whole previous day."""
    tz = zone(tz_name)
    now = now.astimezone(tz) if now else datetime.now(tz)
    since = (since or "24h").strip()
    start = parse_when(since, tz, now)
    if until:
        end = parse_when(until, tz, now)
        if until.strip().lower() in ("yesterday", "today"):
            end = day_start(end.date() + timedelta(days=1), tz)   # inclusive of that whole day
    elif since.lower() == "yesterday":
        end = day_start(start.date() + timedelta(days=1), tz)
    else:
        end = now
    if end <= start:
        raise Failure("the time range is empty", "--until must be after --since.")
    return start, end


# ---------------------------------------------------------------- text (pure)

USER_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
CHAN_RE = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]*))?>")
LINK_RE = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]*))?>")
BANG_RE = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")


def humanize(text, users=None):
    """Slack markup -> plain text a model can read."""
    users = users or {}
    s = text or ""
    s = USER_RE.sub(lambda m: "@" + users.get(m.group(1), m.group(1)), s)
    s = CHAN_RE.sub(lambda m: "#" + (m.group(2) or m.group(1)), s)
    s = LINK_RE.sub(lambda m: (f"{m.group(2)} ({m.group(1)})" if m.group(2) else m.group(1)), s)
    s = BANG_RE.sub(lambda m: "@" + m.group(1), s)
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def permalink(team_url, channel, ts, thread_ts=None):
    base = (team_url or "https://slack.com/").rstrip("/")
    link = f"{base}/archives/{channel}/p{str(ts).replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={channel}"
    return link


def _indent(text, pad):
    lines = (text or "").splitlines() or ["(no text)"]
    return [pad + ln if ln.strip() else pad.rstrip() for ln in lines]


def render_md(sections, tz_name=DEFAULT_TZ):
    """sections: [{id, name, start, end, messages:[{time, author, text, permalink, replies:[...]}]}]"""
    out = ["# Slack history", ""]
    for s in sections:
        title = f"#{s['name']} ({s['id']})" if s.get("name") else s["id"]
        msgs = s.get("messages") or []
        replies = sum(len(m.get("replies") or []) for m in msgs)
        out.append(f"## {title}")
        out.append(f"{s.get('start','')} to {s.get('end','')} ({tz_name}) - "
                   f"{len(msgs)} messages, {replies} thread replies")
        out.append("")
        if not msgs:
            out += ["Nothing in this range.", ""]
            continue
        for m in msgs:
            out.append(f"- {m.get('time','')} {m.get('author','unknown')} - {m.get('permalink','')}")
            out += _indent(m.get("text", ""), "  ")
            for r in m.get("replies") or []:
                out.append(f"  - {r.get('time','')} {r.get('author','unknown')} (reply) - "
                           f"{r.get('permalink','')}")
                out += _indent(r.get("text", ""), "    ")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_inbox_md(sections, tz_name=DEFAULT_TZ):
    """sections: [{id, kind, who, emails, messages:[{ts, time, author, email, text, thread_ts}]}]

    One section per person (or group DM), messages oldest first.
    """
    live = [s for s in sections if s.get("messages")]
    out = ["# Slack DMs to the bot", ""]
    if not live:
        out += [f"No new DMs ({tz_name}).", ""]
        return "\n".join(out).rstrip() + "\n"
    for s in live:
        msgs = s["messages"]
        out.append(f"## {s.get('who') or s['id']} ({s['id']})")
        kind = "group DM" if s.get("kind") == "mpim" else "DM"
        out.append(f"{len(msgs)} new {kind} message{'' if len(msgs) == 1 else 's'} "
                   f"({tz_name}), oldest first")
        to = " ".join(f"--to {e}" for e in (s.get("emails") or []) if e)
        if to:
            out.append(f"reply: connectors/slack.py dm --as <slug> {to} "
                       f"--thread {msgs[-1].get('thread_ts') or msgs[-1].get('ts')} --text '...'")
        out.append("")
        for m in msgs:
            who = m.get("author", "unknown")
            if m.get("email"):
                who += f" <{m['email']}>"
            out.append(f"- {m.get('time','')} {who} - ts {m.get('ts','')}")
            out += _indent(m.get("text", ""), "  ")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- policy (pure)

def load_yaml(path, what):
    if yaml is None:                                    # pragma: no cover
        raise Failure("PyYAML is missing", "The hub needs PyYAML: python3 -m pip install pyyaml")
    p = Path(path)
    if not p.exists():
        raise Refused(f"{what} not found at {p}")
    try:
        return yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        raise Failure(f"{what} at {p} is not valid YAML: {e}")


SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def bot_folder(slug):
    """The bot's folder: for this run's own bot the folder the runner names (HUB_BOT_FOLDER: a bot made with a UUID id
    is not in bot-<id>, and a rename keeps its folder), else bot-<slug> if it exists, else emp-<slug> if that does,
    else bot-<slug> (this repeats clients/manifest.py)."""
    if not SLUG.fullmatch(slug or ""):                  # a slug is one folder name: never a path out of PROJECTS
        raise Refused(f"{slug!r} is not a bot slug")
    own = os.environ.get("HUB_BOT_FOLDER") or ""
    if own and SLUG.fullmatch(own) and slug.lower() == bot_from_env().lower():
        return PROJECTS / own
    for prefix in ("bot-", "emp-"):
        if (PROJECTS / (prefix + slug)).exists():
            return PROJECTS / (prefix + slug)
    return PROJECTS / ("bot-" + slug)


def manifest_of(slug):
    """(path, `<folder>/<file>` label) of the bot's manifest: bot.yaml, else the older employee.yaml."""
    folder = bot_folder(slug)
    for name in ("bot.yaml", "employee.yaml"):
        if (folder / name).is_file():
            return folder / name, f"{folder.name}/{name}"
    return folder / "bot.yaml", f"{folder.name}/bot.yaml"


def load_manifest(slug):
    path, label = manifest_of(slug)
    return load_yaml(path, label)


def declared_tools(manifest):
    """The manifest's `tools:` list, else the older `access:`."""
    return (manifest["tools"] if "tools" in manifest else manifest.get("access")) or []


def bot_from_env():
    return (os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE") or "").strip()


def slack_access(manifest, slug):
    """The employee's `tools:` (older: `access:`) entry for slack, or a refusal explaining what to add."""
    for entry in declared_tools(manifest):
        if isinstance(entry, dict) and str(entry.get("service", "")).lower() == "slack":
            return entry
    raise Refused(f"{slug} does not declare Slack access.",
                  "Add a `tools:` entry with service: slack to %s. "
                  "Changing tools: is the owner's call - open an Issue with owner:<owner handle> and "
                  "type:decision (hub policies/access.md)." % manifest_of(slug)[1])


def check_can_post(manifest, slug):
    entry = slack_access(manifest, slug)
    can = [str(c).lower() for c in (entry.get("can") or [])]
    if "post" not in can:
        raise Refused(f"{slug} may {', '.join(can) or 'do nothing'} on Slack, not post.",
                      "Add `post` to the slack entry's `can:` in bot.yaml - which is the owner's "
                      "call: open an Issue with owner:<owner handle> and type:decision.")
    return entry


def check_not_onboarding(slug):
    """An onboarding bot sends nothing to anyone but its manager until a person above it marks onboarding complete
    (backend/lifecycle.py), so it neither posts nor DMs. A run that cannot ask Tico for its stage (no hub credential,
    Tico unreachable) cannot show it is past onboarding, so it sends nothing either."""
    row = HUB_GET(f"bots/{slug}")
    stage = row.get("stage") if isinstance(row, dict) else None
    if stage == "onboarding":
        raise Refused(f"{slug} is still onboarding, so it can't post on Slack yet.",
                      "Answer in chat or ask your manager; a person above you on the org chart marks onboarding "
                      "complete when you are ready.")
    if not stage:
        raise Refused(f"Tico couldn't confirm that {slug} has finished onboarding, so nothing was sent to Slack.",
                      "A bot run reads its stage from Tico with HUB_API_URL and HUB_TOKEN; try again once Tico "
                      "answers.")


def _bot_names(slug):
    """The label and the id of one bot, lowercased. A channel's readers hold bot ids (Tico stores the id the label
    resolves to), so a bot named by its label matches them by its id. Without a Tico row only the label is known."""
    names = {str(slug).strip().lower()}
    row = HUB_GET(f"bots/{slug}") if slug else None
    if isinstance(row, dict) and row.get("id"):
        names.add(str(row["id"]).strip().lower())
    return names


def check_history_scope(manifest, slug, refs, channels=None):
    """Refuse a history read outside an employee's declared channel scope.

    `channels:` is opt-in for backwards compatibility with existing employees. Once present, even
    as an empty list, it is an allow-list. Both channel names and ids are accepted. A channel whose
    entry on the company's list names this bot as a reader is in the scope as well.
    """
    entry = slack_access(manifest, slug)
    can = [str(c).lower() for c in (entry.get("can") or [])]
    if "read" not in can:
        raise Refused(f"{slug} may {', '.join(can) or 'do nothing'} on Slack, not read history.",
                      "Add `read` to the Slack entry's `can:` in bot.yaml; that is the owner's call.")
    declared = entry.get("channels")
    if declared is None:
        return entry
    registry = channels if channels is not None else load_channels()
    allowed = set()
    for ref in declared or []:
        allowed.add(norm_ref(ref))
        try:
            channel = find_registry_channel(registry, ref)
        except Refused:
            continue                                    # declared but not listed: only the words it was declared in
        allowed.update({norm_ref(channel.get("name")), norm_ref(channel.get("id"))} - {""})
    names = _bot_names(slug)
    for channel in registry:
        if names & {str(r).strip().lower() for r in (channel.get("readers") or [])}:
            allowed.update({norm_ref(channel.get("name")), norm_ref(channel.get("id"))} - {""})
    for ref in refs:
        channel = find_registry_channel(registry, ref)
        keys = {norm_ref(channel.get("name")), norm_ref(channel.get("id"))} - {""}
        if not (keys & allowed):
            names = ", ".join(sorted("#" + r for r in allowed if not re.fullmatch(r"[cgd][a-z0-9]{6,}", r))) or "none"
            raise Refused(f"{slug} may not read {ref!r}.",
                          f"Its Slack channels are: {names}. An owner or admin can add it as a reader "
                          f"(Settings > Tools > Slack, or ask BotOps).")
    return entry


def file_conversations(info):
    """The channels and DMs a Slack file was shared in, from files.info."""
    shares = info.get("shares") or {}
    channels = set(info.get("channels") or []) | set(info.get("groups") or [])
    channels |= set((shares.get("public") or {}).keys()) | set((shares.get("private") or {}).keys())
    ims = {c for c in channels | set(info.get("ims") or []) if str(c).startswith("D")}
    return sorted(channels - ims), sorted(ims)


def check_file_scope(manifest, slug, info, channels=None):
    """A file is readable where its message is: a channel the bot may read, or a DM when DMs are not off."""
    entry = slack_access(manifest, slug)
    can = [str(c).lower() for c in (entry.get("can") or [])]
    if "read" not in can:
        raise Refused(f"{slug} may {', '.join(can) or 'do nothing'} on Slack, not read files.",
                      "Add `read` to the Slack entry's `can:` in bot.yaml; that is the owner's call.")
    shared, ims = file_conversations(info)
    if ims and entry.get("dms") is not False:
        return entry
    if shared and entry.get("channels") is None:
        return entry
    registry = None
    for cid in shared:
        try:
            if registry is None:
                registry = channels if channels is not None else load_channels()
            return check_history_scope(manifest, slug, [cid], registry)
        except Refused:
            continue
    raise Refused(f"{slug} may not read file {info.get('id') or '?'}.",
                  "It was not shared in a channel this bot may read"
                  + (" or in a DM it may read." if ims or entry.get("dms") is False else "."))


def check_file_url(url):
    """The bot token is only ever sent to Slack's own file host."""
    parts = urllib.parse.urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == "slack.com" or host.endswith(".slack.com")):
        raise Refused(f"Not a Slack file address: {url or '(none)'}",
                      "Only files hosted by Slack (url_private) are downloaded with the bot token.")
    return url


def one_line(text):
    """One printable line: whitespace runs become one space; control and format characters (NUL, bidi
    overrides) are dropped."""
    text = "".join(c for c in str(text or "") if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf"))
    return " ".join(text.split())


def trim_bytes(text, limit):
    return text.encode()[:max(limit, 0)].decode(errors="ignore")


def file_name(info):
    """`<file id>-<name>`: the sharer picks the name, so it never names an existing file on its own.
    At most MAX_NAME_BYTES, keeping a short extension."""
    name = one_line(Path(str(info.get("name") or "").replace("\\", "/")).name)
    fid = re.sub(r"[^A-Za-z0-9]", "", str(info.get("id") or ""))[:32] or "F"
    if name in ("", ".", ".."):
        return fid
    full = f"{fid}-{name}"
    if len(full.encode()) <= MAX_NAME_BYTES:
        return full
    stem, suffix = os.path.splitext(full)
    if len(suffix.encode()) > 16:
        stem, suffix = full, ""
    return trim_bytes(stem, MAX_NAME_BYTES - len(suffix.encode())).rstrip(" .") + suffix


def files_dir(path):
    """The default download folder, made 0700 if missing; refused unless it is this user's own real folder."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as e:
        raise Failure(f"Could not create {path}: {e.strerror or e}", "Pass --out a folder you own.")
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
        raise Refused(f"{path} is not a folder this user owns; nothing was saved.",
                      "Remove it, or pass --out a folder you own.")
    if hasattr(os, "getuid") and stat.S_IMODE(st.st_mode) & 0o077:
        os.chmod(path, 0o700)
    return path


def save_new(path, raw, folder):
    """Write a new file and never replace or follow one: O_EXCL|O_NOFOLLOW; in a folder, -2, -3 on a clash."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    stem, suffix = path.stem, path.suffix
    for n in range(1, 100 if folder else 2):
        target = path if n == 1 else path.with_name(f"{stem}-{n}{suffix}")
        try:
            fd = os.open(target, flags, 0o600)
        except FileExistsError:
            continue
        except (OSError, ValueError) as e:
            why = getattr(e, "strerror", None) or e
            raise Failure(f"Could not save {one_line(target)}: {why}; nothing was saved.",
                          "Pass --out a shorter or plainer file name.")
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return target
    raise Refused(f"{path} already exists; nothing was saved.", "Pass --out a new file name or a folder.")


def check_inbox_scope(manifest, slug):
    """Refuse DM inbox reads when an employee's Slack entry explicitly disables them."""
    entry = slack_access(manifest, slug)
    can = [str(c).lower() for c in (entry.get("can") or [])]
    if "read" not in can:
        raise Refused(f"{slug} may {', '.join(can) or 'do nothing'} on Slack, not read DMs.",
                      "Add `read` to the Slack entry's `can:` in bot.yaml; that is the owner's call.")
    if entry.get("dms") is False:
        raise Refused(f"{slug} may not read Slack DMs.",
                      "Its bot.yaml limits Slack monitoring to declared channels.")
    return entry


def _default_hub_get(path):
    """One GET to the hub as this run, or None when the run has no hub credential or the hub cannot be reached.
    Tests replace `HUB_GET`."""
    url, token = os.environ.get("HUB_API_URL"), os.environ.get("HUB_TOKEN")
    if not url or not token:
        return None
    try:
        if str(HUB) not in sys.path:
            sys.path.insert(0, str(HUB))
        from clients.tico import Client
        return Client(url, token, timeout=15, retries=1).get(path)
    except Exception:
        return None


HUB_GET = _default_hub_get
CHANNELS_HINT = ("The list is in Tico: Settings > Tools > Slack, or `hub slack channel list`. A bot run reads it from "
                 "the hub with HUB_API_URL and HUB_TOKEN.")


def file_channels(path=CHANNELS_FILE):
    """The old registry/slack-channels.yaml as a list of entries; [] when this machine has none."""
    p = Path(path)
    if not p.exists():
        return []
    data = load_yaml(p, "registry/slack-channels.yaml")
    chans = data.get("channels") if isinstance(data, dict) else data
    out = []
    for c in chans or []:
        if isinstance(c, dict):
            readers = c.get("readers") or []
            out.append({**c, "readers": [readers] if isinstance(readers, str) else list(readers)})
    return out


def load_channels(path=CHANNELS_FILE):
    """The company's Slack channel list: the hub's (Settings > Tools > Slack), else the old registry file.

    The file counts only until an owner or admin imports it, and never when the hub could not be asked and there
    is no file: then there is no list and the refusal says where it lives."""
    if "hub_channels" not in _CACHE:
        _CACHE["hub_channels"] = HUB_GET("slack/channels")
    listed = _CACHE["hub_channels"]
    if isinstance(listed, dict) and isinstance(listed.get("channels"), list):
        rows = [c for c in listed["channels"] if isinstance(c, dict)]
        if (listed.get("registry_file") or {}).get("imported"):
            return rows
        for extra in file_channels(path):
            if not any(norm_ref(extra.get("name")) == norm_ref(r.get("name")) and extra.get("name")
                       or extra.get("id") and extra.get("id") == r.get("id") for r in rows):
                rows.append(extra)
        return rows
    rows = file_channels(path)
    if rows or Path(path).exists():
        return rows
    raise Refused("there is no Slack channel list this run can read.", CHANNELS_HINT)


def norm_ref(ref):
    return str(ref or "").strip().lstrip("#").lower()


def find_registry_channel(channels, ref):
    r = norm_ref(ref)
    for c in channels:
        if r and (r == norm_ref(c.get("name")) or r == str(c.get("id") or "").lower()):
            return c
    raise Refused(f"channel {ref!r} is not on the company's Slack channel list.",
                  "Bots use only the channels listed in Settings > Tools > Slack. Adding one is an owner's or "
                  "admin's call: ask BotOps, or an owner or admin runs "
                  "`hub slack channel add <channel> --reader <bot>`.")


def check_channel_postable(entry, ref):
    if entry.get("post") is False:
        raise Refused(f"the Slack channel list says bots may not post in #{entry.get('name', ref)}.",
                      "Read-only for bots. An owner or admin can turn posting on in Settings > Tools > Slack.")
    if not str(entry.get("id") or "").strip():
        raise Refused(f"the Slack channel list has no id for #{entry.get('name', ref)} and this token cannot see it.",
                      "Run `connectors/slack.py channels`; a private channel needs /invite @Tico first.")
    return str(entry["id"]).strip()


def check_not_external(info, ref):
    """conversations.info payload -> refuse Slack Connect / externally shared channels."""
    c = (info or {}).get("channel") or info or {}
    flags = [k for k in ("is_ext_shared", "is_pending_ext_shared", "is_shared", "is_org_shared")
             if c.get(k)]
    if flags:
        raise Refused(f"#{c.get('name', ref)} is shared outside the workspace ({', '.join(flags)}).",
                      "Posting there is an outbound send: produce the draft on the Issue and stop "
                      "(hub policies/shared-rules.md, 'Outbound sends').")


def check_text(text):
    t = (text or "").strip()
    if not t:
        raise Refused("refusing to post an empty message.")
    if len(t) > MAX_POST_CHARS:
        raise Refused(f"message is {len(t)} characters; the limit is {MAX_POST_CHARS}.",
                      "Post the short version and put the long form on the Issue.")
    return t


def load_hub_access(path=HUB_ACCESS_FILE):
    """registry/hub-access.yaml -> the set of company human emails a bot may DM."""
    data = load_yaml(path, "registry/hub-access.yaml")
    emails = []
    owner = str(data.get("owner") or "").strip().lower()
    if owner:
        emails.append(owner)
    for e in (data.get("allowed") or []):
        e = str(e or "").strip().lower()
        if e:
            emails.append(e)
    if not emails:
        raise Refused("registry/hub-access.yaml lists nobody.",
                      "It is the list of company humans. " + HUB_ACCESS_HINT)
    return set(emails)


def check_recipient(user, allowed_emails, ref):
    """A resolved Slack user -> their email, or a refusal. Nothing outside the company."""
    u = user or {}
    who = u.get("display") or u.get("name") or u.get("id") or ref
    if u.get("is_bot") or u.get("id") == "USLACKBOT":
        raise Refused(f"{ref} is a bot ({who}), not a person.",
                      "Bots talk to each other through hub Issues, not Slack DMs.")
    if u.get("deleted"):
        raise Refused(f"{ref} ({who}) is a deactivated Slack account.",
                      "Nothing can reach them. Drop the recipient or ask the owner who replaced them.")
    email = str(u.get("email") or "").strip().lower()
    if not email:
        raise Refused(f"{ref} ({who}) has no work email this app can see.",
                      "A DM is only allowed to someone on registry/hub-access.yaml, and without "
                      "an email that cannot be checked. The app needs the users:read.email scope; "
                      "if it has it, " + HUB_ACCESS_HINT + ".")
    if email not in allowed_emails:
        raise Refused(f"{email} is not in registry/hub-access.yaml.", HUB_ACCESS_HINT)
    return email


def audit(slug, channel, text, path=AUDIT_FILE, when=None, kind="post", recipients=None):
    rec = {"ts": (when or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
           "kind": kind, "slug": slug, "channel": channel, "text": (text or "")[:200]}
    if recipients is not None:
        rec["recipients"] = list(recipients)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


# ---------------------------------------------------------------- workspace helpers

_CACHE = {}


def workspace(token=None):
    if "auth" not in _CACHE:
        _CACHE["auth"] = call("auth.test", token=token)
    return _CACHE["auth"]


def all_channels(token=None):
    if "channels" not in _CACHE:
        _CACHE["channels"] = paginate(
            "conversations.list",
            {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200},
            "channels", token=token)
    return _CACHE["channels"]


def user_names(token=None, cache_path=USER_CACHE, ttl=USER_CACHE_TTL):
    """id -> display name. Cached on disk for a day; users.list is expensive."""
    if "users" in _CACHE:
        return _CACHE["users"]
    p = Path(cache_path)
    try:
        if p.exists() and time.time() - p.stat().st_mtime < ttl:
            _CACHE["users"] = json.loads(p.read_text())
            return _CACHE["users"]
    except Exception:
        pass
    names = {}
    for u in paginate("users.list", {"limit": 200}, "members", token=token):
        prof = u.get("profile") or {}
        names[u.get("id")] = (prof.get("display_name") or prof.get("real_name")
                              or u.get("real_name") or u.get("name") or u.get("id"))
    _CACHE["users"] = names
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(names))
    except Exception:
        pass
    return names


def trim_user(u):
    """The bits of a users.list record the DM gates need."""
    prof = (u or {}).get("profile") or {}
    return {"id": u.get("id"),
            "name": u.get("name") or "",
            "display": (prof.get("display_name") or prof.get("real_name") or u.get("real_name")
                        or u.get("name") or u.get("id")),
            "real_name": prof.get("real_name") or u.get("real_name") or "",
            "email": str(prof.get("email") or "").strip().lower(),
            "is_bot": bool(u.get("is_bot")) or u.get("id") == "USLACKBOT",
            "deleted": bool(u.get("deleted"))}


def all_users(token=None, cache_path=DIRECTORY_CACHE, ttl=USER_CACHE_TTL):
    """The workspace directory, trimmed. Cached on disk for a day; users.list is expensive."""
    if "directory" in _CACHE:
        return _CACHE["directory"]
    p = Path(cache_path)
    try:
        if p.exists() and time.time() - p.stat().st_mtime < ttl:
            _CACHE["directory"] = json.loads(p.read_text())
            return _CACHE["directory"]
    except Exception:
        pass
    rows = [trim_user(u) for u in paginate("users.list", {"limit": 200}, "members", token=token)]
    _CACHE["directory"] = rows
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows))
    except Exception:
        pass
    return rows


def users_by_id(token=None):
    return {u["id"]: u for u in all_users(token) if u.get("id")}


def resolve_user(ref, token=None):
    """'someone@example.com' | '@handle' | 'U123' -> a trimmed user record."""
    raw = str(ref or "").strip()
    if not raw:
        raise Failure("--to is required")
    if re.fullmatch(r"[UW][A-Z0-9]{2,}", raw):
        found = users_by_id(token).get(raw)
        if found:
            return found
        return trim_user((call("users.info", {"user": raw}, token=token).get("user")) or {})
    if "@" in raw and not raw.startswith("@"):
        data = call("users.lookupByEmail", {"email": raw.lower()}, token=token)
        return trim_user(data.get("user") or {})
    want = raw.lstrip("@").lower()
    hits = [u for u in all_users(token)
            if want in {str(u.get("name") or "").lower(), str(u.get("display") or "").lower(),
                        str(u.get("real_name") or "").lower()}]
    if not hits:
        raise Failure(f"no Slack user matches {ref!r}.",
                      "Use the person's work email (someone@example.com) - it is exact - or their "
                      "Slack id. Handles are matched against display and real names.")
    if len({u["id"] for u in hits}) > 1:
        raise Failure(f"{ref!r} matches {len(hits)} Slack users.", "Use the work email instead.")
    return hits[0]


def load_watermarks(path=INBOX_FILE):
    """conversation id -> last seen ts. Missing or unreadable is an empty inbox state."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text()) or {}
    except Exception:
        return {}
    seen = data.get("seen") if isinstance(data, dict) else None
    return {k: str(v) for k, v in (seen or {}).items()} if isinstance(seen, dict) else {}


def save_watermarks(marks, path=INBOX_FILE, when=None):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"updated": (when or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
         "seen": {k: str(v) for k, v in marks.items()}}, indent=2))
    return marks


def resolve_channel(ref, token=None):
    """'#name' | 'name' | 'C123' -> (id, name). Registry first for names, then the API."""
    raw = str(ref or "").strip()
    if not raw:
        raise Failure("--channel is required")
    if re.fullmatch(r"[CGD][A-Z0-9]{6,}", raw):
        for c in all_channels(token):
            if c.get("id") == raw:
                return raw, c.get("name")
        return raw, None                                # id the bot cannot list; let Slack judge
    want = norm_ref(raw)
    try:
        entry = find_registry_channel(load_channels(), want)
        if str(entry.get("id") or "").strip():
            return str(entry["id"]).strip(), entry.get("name")
    except Failure:
        pass
    for c in all_channels(token):
        if norm_ref(c.get("name")) == want:
            return c.get("id"), c.get("name")
    raise Failure(f"no channel named #{want} is visible to this token.",
                  "Public channels appear once the app is installed; a private channel needs "
                  "/invite @hub. Check `connectors/slack.py channels`.")


def is_shared(c):
    return bool(c.get("is_shared") or c.get("is_ext_shared") or c.get("is_pending_ext_shared")
                or c.get("is_org_shared"))


# ---------------------------------------------------------------- commands

def dm_conversations(token=None):
    """Every DM and group DM the bot is in."""
    return paginate("conversations.list",
                    {"types": "im,mpim", "exclude_archived": "true", "limit": 200},
                    "channels", token=token)


def check_dm_scopes():
    """(count, error) - can this token list DMs at all? A missing scope is the usual answer."""
    try:
        return len(dm_conversations()), None
    except Failure as e:
        return None, e


def cmd_doctor(args):
    auth = workspace()
    mine = paginate("users.conversations",
                    {"types": "public_channel,private_channel", "exclude_archived": "true",
                     "limit": 200}, "channels")
    dms, dm_err = check_dm_scopes()
    if args.json:
        print(json.dumps({"ok": True, "team": auth.get("team"), "team_id": auth.get("team_id"),
                          "bot_user": auth.get("user"), "bot_user_id": auth.get("user_id"),
                          "url": auth.get("url"),
                          "dm_conversations": dms,
                          "dm_scopes_ok": dm_err is None,
                          "dm_error": dm_err.msg if dm_err else None,
                          "dm_hint": dm_err.hint if dm_err else None,
                          "member_of": [{"id": c.get("id"), "name": c.get("name"),
                                         "is_private": bool(c.get("is_private")),
                                         "is_shared": is_shared(c)} for c in mine]}, indent=2))
        return 0
    print(f"team      {auth.get('team')} ({auth.get('team_id')})")
    print(f"bot user  {auth.get('user')} ({auth.get('user_id')})")
    print(f"workspace {auth.get('url')}")
    if dm_err is None:
        print(f"dms       {dms} DM/group-DM conversations "
              f"(im/mpim scopes present, Messages tab on)")
    else:
        print(f"dms       cannot list DMs: {dm_err.msg}")
        print(f"          {dm_err.hint or 'Reinstall the app with the im/mpim scopes.'}")
        print("          An app created before the DM scopes existed must be reinstalled: "
              "api.slack.com/apps shows a reinstall banner.")
    print(f"member of {len(mine)} channels:")
    for c in sorted(mine, key=lambda c: c.get("name") or ""):
        kind = "private" if c.get("is_private") else "public"
        ext = ", EXTERNALLY SHARED" if is_shared(c) else ""
        print(f"  {c.get('id'):<12} #{c.get('name')} ({kind}{ext})")
    if not mine:
        print("  (none - invite @hub to the channels the bots read)")
    return 0


def cmd_channels(args):
    chans = all_channels()
    rows = [{"id": c.get("id"), "name": c.get("name"), "is_private": bool(c.get("is_private")),
             "is_shared": is_shared(c), "is_member": bool(c.get("is_member"))} for c in chans]
    rows.sort(key=lambda r: r["name"] or "")
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{'id':<12} {'name':<32} {'private':<8} {'shared':<7} member")
    for r in rows:
        print(f"{r['id']:<12} #{(r['name'] or ''):<31} {str(r['is_private']).lower():<8} "
              f"{str(r['is_shared']).lower():<7} {str(r['is_member']).lower()}")
    print(f"{len(rows)} channels visible to this token.")
    return 0


def fetch_history(channel_id, start, end, threads, users, team_url, tz):
    msgs = paginate("conversations.history",
                    {"channel": channel_id, "oldest": f"{start.timestamp():.6f}",
                     "latest": f"{end.timestamp():.6f}", "inclusive": "true", "limit": 200},
                    "messages")
    msgs = [m for m in msgs if m.get("subtype") not in NOISE_SUBTYPES]
    msgs.sort(key=lambda m: float(m.get("ts", 0)))
    out = []
    for m in msgs:
        row = shape(m, channel_id, users, team_url, tz)
        if threads and int(m.get("reply_count") or 0) > 0:
            replies = paginate("conversations.replies",
                               {"channel": channel_id, "ts": m.get("ts"), "limit": 200},
                               "messages")
            replies.sort(key=lambda r: float(r.get("ts", 0)))
            row["replies"] = [shape(r, channel_id, users, team_url, tz, m.get("ts"))
                              for r in replies if r.get("ts") != m.get("ts")
                              and r.get("subtype") not in NOISE_SUBTYPES]
        out.append(row)
    return out


def file_lines(m):
    """One `[file F...] name` line per file on a message; `file --id` downloads it."""
    return [f"[file {one_line(f.get('id'))}] {one_line(f.get('name') or f.get('title')) or 'file'}"
            for f in (m.get("files") or []) if isinstance(f, dict) and f.get("id")]


def shape(m, channel_id, users, team_url, tz, thread_ts=None):
    ts = m.get("ts", "0")
    when = datetime.fromtimestamp(float(ts), tz)
    author = (users.get(m.get("user")) or m.get("username") or m.get("bot_id")
              or m.get("user") or "unknown")
    text = humanize(m.get("text"), users)
    for att in (m.get("attachments") or []):
        extra = humanize(att.get("text") or att.get("fallback") or "", users)
        if extra:
            text += ("\n" if text else "") + "[attachment] " + extra
    for line in file_lines(m):
        text += ("\n" if text else "") + line
    return {"ts": ts, "time": when.strftime("%Y-%m-%d %H:%M"), "author": author, "text": text,
            "user": m.get("user"), "reply_count": int(m.get("reply_count") or 0),
            "permalink": permalink(team_url, channel_id, ts, thread_ts), "replies": []}


def cmd_history(args):
    refs = [r for chunk in args.channel for r in str(chunk).split(",") if r.strip()]
    slug = (args.as_slug or bot_from_env()).strip()
    if slug:
        manifest = load_manifest(slug)
        check_history_scope(manifest, slug, refs)
    tz = zone(args.tz)
    start, end = time_range(args.since, args.until, args.tz)
    users = user_names()
    team_url = workspace().get("url", "")
    sections = []
    for ref in refs:
        cid, name = resolve_channel(ref)
        sections.append({
            "id": cid, "name": name,
            "start": start.strftime("%Y-%m-%d %H:%M"), "end": end.strftime("%Y-%m-%d %H:%M"),
            "messages": fetch_history(cid, start, end, args.threads, users, team_url, tz)})
    if args.format == "json":
        print(json.dumps({"ok": True, "tz": args.tz, "channels": sections}, indent=2))
    else:
        print(render_md(sections, args.tz), end="")
    return 0


def cmd_post(args):
    slug = args.as_slug.strip()
    manifest = load_manifest(slug)
    check_can_post(manifest, slug)
    check_not_onboarding(slug)
    entry = find_registry_channel(load_channels(), args.channel)
    if entry.get("post") is not False and not str(entry.get("id") or "").strip():
        cid, _name = resolve_channel(args.channel)      # listed by name: Slack gives the id
        entry = {**entry, "id": cid}
    cid = check_channel_postable(entry, args.channel)
    text = check_text(sys.stdin.read() if args.text == "-" else args.text)
    check_not_external(call("conversations.info", {"channel": cid}), args.channel)
    res = call("chat.postMessage",
               {"channel": cid, "text": text, "thread_ts": args.thread,
                "unfurl_links": False, "unfurl_media": False}, post=True)
    audit(slug, f"{cid} #{entry.get('name')}", text)
    link = permalink(workspace().get("url", ""), cid, res.get("ts", "0"), args.thread)
    if args.json:
        print(json.dumps({"ok": True, "channel": cid, "name": entry.get("name"),
                          "ts": res.get("ts"), "permalink": link}, indent=2))
    else:
        print(f"posted to #{entry.get('name')} ({cid}) as {slug}: {link}")
    return 0


def split_refs(values):
    return [r.strip() for chunk in (values or []) for r in str(chunk).split(",") if r.strip()]


def cmd_dm(args):
    """DM a company human. Every gate below runs before the first network call."""
    slug = args.as_slug.strip()
    manifest = load_manifest(slug)
    check_can_post(manifest, slug)                              # (a) the employee may write
    check_not_onboarding(slug)
    text = check_text(sys.stdin.read() if args.text == "-" else args.text)   # (d) length
    allowed = load_hub_access()                                 # (b) the company list
    refs = split_refs(args.to)
    if not refs:
        raise Failure("--to is required", "A work email is exact: --to someone@example.com")

    ids, emails, names = [], [], []
    for ref in refs:
        user = resolve_user(ref)
        email = check_recipient(user, allowed, ref)             # (b) and (c) bots, deleted
        if user["id"] in ids:
            continue
        ids.append(user["id"])
        emails.append(email)
        names.append(user.get("display") or email)

    conv = call("conversations.open", {"users": ",".join(ids)}, post=True)
    cid = str((conv.get("channel") or {}).get("id") or "")
    if not cid:
        raise Failure("Slack conversations.open returned no conversation.",
                      "Check that the Messages tab is on for the app (connectors/slack.py doctor).")
    res = call("chat.postMessage",
               {"channel": cid, "text": text, "thread_ts": args.thread,
                "unfurl_links": False, "unfurl_media": False}, post=True)
    audit(slug, cid, text, kind="dm", recipients=emails)
    link = permalink(workspace().get("url", ""), cid, res.get("ts", "0"), args.thread)
    kind = "group DM" if len(ids) > 1 else "DM"
    if args.json:
        print(json.dumps({"ok": True, "kind": "mpim" if len(ids) > 1 else "im", "channel": cid,
                          "recipients": emails, "users": ids, "ts": res.get("ts"),
                          "thread_ts": args.thread, "permalink": link}, indent=2))
    else:
        print(f"sent a {kind} to {', '.join(names)} ({cid}) as {slug}: {link}")
    return 0


def conversation_people(conv, directory, me):
    """(who, emails, ids) for a DM or group DM, the bot itself left out."""
    cid, ids = conv.get("id"), []
    if conv.get("is_im") or str(cid or "").startswith("D"):
        ids = [conv.get("user")] if conv.get("user") else []
    else:
        ids = [u for u in paginate("conversations.members", {"channel": cid, "limit": 200},
                                   "members")]
    people = [directory.get(i) or {"id": i, "display": i, "email": ""} for i in ids if i and i != me]
    emails = [p.get("email") for p in people if p.get("email")]
    labels = [(p.get("display") or p.get("id")) +
              (f" <{p['email']}>" if p.get("email") else "") for p in people]
    who = ", ".join(labels) or cid
    if len(people) > 1:
        who = "group DM: " + who
    return who, emails, [p.get("id") for p in people]


def cmd_inbox(args):
    slug = (args.as_slug or bot_from_env()).strip()
    if slug:
        manifest = load_manifest(slug)
        check_inbox_scope(manifest, slug)
    tz = zone(args.tz)
    start, _ = time_range(args.since, None, args.tz)
    auth = workspace()
    me, team_url = auth.get("user_id"), auth.get("url", "")
    directory = users_by_id()
    display = {i: (u.get("display") or i) for i, u in directory.items()}
    marks = load_watermarks(args.state)
    new_marks = dict(marks)
    sections = []
    for conv in dm_conversations():
        cid = conv.get("id")
        if not cid:
            continue
        other = directory.get(conv.get("user")) or {}
        if conv.get("is_im") and (other.get("is_bot") or conv.get("user") == "USLACKBOT"):
            continue                                    # Slackbot and app DMs are not people
        floor = max(float(marks.get(cid) or 0), start.timestamp())
        msgs = paginate("conversations.history",
                        {"channel": cid, "oldest": f"{floor:.6f}", "limit": 200}, "messages")
        msgs = [m for m in msgs if m.get("subtype") not in NOISE_SUBTYPES and m.get("ts")]
        if not msgs:
            continue
        msgs.sort(key=lambda m: float(m.get("ts", 0)))
        new_marks[cid] = msgs[-1]["ts"]                 # our own messages count as seen
        theirs = [m for m in msgs
                  if m.get("user") != me and not m.get("bot_id")
                  and m.get("subtype") != "bot_message"]
        if not theirs:
            continue
        who, emails, _ids = conversation_people(conv, directory, me)
        rows = []
        for m in theirs:
            u = directory.get(m.get("user")) or {}
            rows.append({"ts": m["ts"],
                         "time": datetime.fromtimestamp(float(m["ts"]), tz).strftime(
                             "%Y-%m-%d %H:%M"),
                         "user": m.get("user"),
                         "author": u.get("display") or m.get("user") or "unknown",
                         "email": u.get("email", ""),
                         "text": "\n".join([humanize(m.get("text"), display)] + file_lines(m)).strip(),
                         "thread_ts": m.get("thread_ts") or m["ts"],
                         "permalink": permalink(team_url, cid, m["ts"], m.get("thread_ts"))})
        sections.append({"id": cid, "kind": "im" if conv.get("is_im") else "mpim",
                         "who": who, "emails": emails, "messages": rows})
    if args.mark:
        save_watermarks(new_marks, args.state)
    if args.format == "json":
        print(json.dumps({"ok": True, "tz": args.tz, "since": start.isoformat(timespec="seconds"),
                          "marked": bool(args.mark), "conversations": sections}, indent=2))
    else:
        print(render_inbox_md(sections, args.tz), end="")
    return 0


def cmd_file(args):
    """Download a file a person shared: files.info (files:read), then url_private with the bot token."""
    slug = (args.as_slug or bot_from_env()).strip()
    manifest = load_manifest(slug) if slug else None
    if slug:
        slack_access(manifest, slug)
    info = call("files.info", {"file": args.id.strip()}).get("file") or {}
    if slug:
        check_file_scope(manifest, slug, info)
    url = check_file_url(info.get("url_private_download") or info.get("url_private"))
    size = info.get("size")
    if isinstance(size, (int, float)) and size > MAX_FILE_BYTES:
        raise Refused(f"File {args.id} is {size} bytes, over the {MAX_FILE_BYTES} byte limit.")
    out = Path(args.out).expanduser() if args.out else FILES_DIR
    folder = not args.out or out.is_dir() or str(args.out).endswith(("/", os.sep))
    if folder:
        out = out / file_name(info)
    status, hdrs, raw = _download(url, {"Authorization": "Bearer " + token_or_die(), "User-Agent": "tico-slack/1"},
                                  60, MAX_FILE_BYTES)
    kind = next((str(v) for k, v in hdrs.items() if k.lower() == "content-type"), "")
    if 300 <= status < 400 or (status == 200 and kind.startswith("text/html")
                               and not str(info.get("mimetype") or "").startswith("text/html")):
        raise Failure(f"Slack sent its sign-in page instead of file {args.id}.",
                      "The Slack app needs the files:read bot scope: add it at api.slack.com/apps > OAuth & "
                      "Permissions (connectors/slack-app-manifest.yaml has it), reinstall the app, then update "
                      f"SLACK_BOT_TOKEN in {SECRETS_HINT}.")
    if status != 200:
        raise Failure(f"Slack file download failed: HTTP {status}")
    if len(raw) > MAX_FILE_BYTES:
        raise Refused(f"File {args.id} is over the {MAX_FILE_BYTES} byte limit.")
    if args.out:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as e:
            raise Failure(f"Could not create {one_line(out.parent)}: {getattr(e, 'strerror', None) or e}")
    else:
        files_dir(FILES_DIR)
    out = save_new(out, raw, folder)
    row = {"ok": True, "id": info.get("id"), "name": info.get("name"), "mimetype": info.get("mimetype"),
           "bytes": len(raw), "path": str(out)}
    if args.json:
        print(json.dumps(row, indent=2))
    else:
        print(f"saved {one_line(info.get('name') or args.id)} ({one_line(row['mimetype']) or 'unknown type'}, "
              f"{len(raw)} bytes) to {one_line(out)}")
    return 0


def cmd_join(args):
    cid, name = resolve_channel(args.channel)
    res = call("conversations.join", {"channel": cid}, post=True)
    c = res.get("channel") or {}
    name = c.get("name") or name
    if args.json:
        print(json.dumps({"ok": True, "channel": cid, "name": name}, indent=2))
    else:
        print(f"joined #{name} ({cid})")
    return 0


# ---------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(prog="slack.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output and errors")
        return sp

    d = common(sub.add_parser("doctor", help="check the token and list channels the bot is in"))
    d.set_defaults(func=cmd_doctor)

    c = common(sub.add_parser("channels", help="list channels visible to the bot"))
    c.set_defaults(func=cmd_channels)

    h = common(sub.add_parser("history", help="read a channel over a time range"))
    h.add_argument("--as", dest="as_slug", default=None,
                   help="employee slug; defaults to HUB_BOT in a hosted run")
    h.add_argument("--channel", action="append", required=True,
                   help="id or #name; repeat or comma-separate for several")
    h.add_argument("--since", default="24h", help="ISO time, 24h/7d/90m, today, or yesterday")
    h.add_argument("--until", default=None, help="ISO time; defaults to now")
    h.add_argument("--tz", default=DEFAULT_TZ, help=f"IANA timezone (default {DEFAULT_TZ})")
    h.add_argument("--threads", action="store_true", help="also fetch every thread's replies")
    h.add_argument("--format", choices=["md", "json"], default="md")
    h.set_defaults(func=cmd_history)

    o = common(sub.add_parser("post", help="post a message (policy-gated)"))
    o.add_argument("--as", dest="as_slug", required=True, help="employee slug, e.g. doc-updater")
    o.add_argument("--channel", required=True, help="id or #name, must be in the channel registry")
    o.add_argument("--text", required=True, help="message text, or - to read stdin")
    o.add_argument("--thread", default=None, help="parent ts, to reply in a thread")
    o.set_defaults(func=cmd_post)

    for name in ("dm", "reply"):                        # `reply` is `dm --thread <ts>`
        m = common(sub.add_parser(
            name, help="DM a company human (policy-gated)" if name == "dm"
            else "alias for dm: continue a DM thread with --thread <ts>"))
        m.add_argument("--as", dest="as_slug", required=True, help="employee slug")
        m.add_argument("--to", action="append", required=True,
                       help="work email, @handle or U-id; repeat or comma-separate for a group DM")
        m.add_argument("--text", required=True, help="message text, or - to read stdin")
        m.add_argument("--thread", default=None,
                       help="ts of a message in the DM, to answer in its thread")
        m.set_defaults(func=cmd_dm)

    i = common(sub.add_parser("inbox", help="read DMs people sent the bot"))
    i.add_argument("--as", dest="as_slug", default=None,
                   help="employee slug; defaults to HUB_BOT in a hosted run")
    i.add_argument("--since", default="24h",
                   help="floor for a conversation with no watermark yet (24h, 7d, ISO)")
    i.add_argument("--tz", default=DEFAULT_TZ, help=f"IANA timezone (default {DEFAULT_TZ})")
    i.add_argument("--format", choices=["md", "json"], default="md")
    i.add_argument("--mark", action="store_true",
                   help="advance the watermark so the next run only sees newer messages")
    i.add_argument("--state", default=str(INBOX_FILE), help=argparse.SUPPRESS)
    i.set_defaults(func=cmd_inbox)

    f = common(sub.add_parser("file", help="download a file someone shared (a screenshot, a log)"))
    f.add_argument("--as", dest="as_slug", default=None,
                   help="employee slug; defaults to HUB_BOT in a hosted run")
    f.add_argument("--id", required=True, help="the file id, F..., from a `[file F...]` line")
    f.add_argument("--out", default=None,
                   help=f"a new file path, or a folder to save it in as <id>-<name> (default {FILES_DIR}); "
                        "an existing file is never replaced")
    f.set_defaults(func=cmd_file)

    j = common(sub.add_parser("join", help="join a public channel"))
    j.add_argument("--channel", required=True)
    j.set_defaults(func=cmd_join)
    return p


def main(argv=None):
    global JSON_ERRORS
    args = build_parser().parse_args(argv)
    JSON_ERRORS = bool(getattr(args, "json", False) or getattr(args, "format", "") == "json")
    try:
        return args.func(args)
    except Refused as e:
        report(e, "refused")
        return 2
    except Failure as e:
        report(e, "failed")
        return 1


def report(e, kind):
    if JSON_ERRORS:
        print(json.dumps({"ok": False, "kind": kind, "error": e.msg, "hint": e.hint}, indent=2))
    else:
        print(f"{kind}: {e.msg}", file=sys.stderr)
        if e.hint:
            print(f"  {e.hint}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
