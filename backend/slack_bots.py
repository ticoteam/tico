# pyright: strict
"""Slack for bots: a bot reads and writes in Slack through these routes, never with a Slack token of its own.

The API process holds no Slack token (config.slack_credentials); the Slack gateway does. So Slack for bots needs the
gateway: with it off, every route here answers 503 and says what to start.

    GET  /api/v2/slack/history       a channel's messages: the gateway asks Slack (backend/slack_requests.py)
    POST /api/v2/slack/posts         a post in a listed channel: a message in the bot's room plus a `slack_posts` row,
                                     which the gateway's `deliver` sends
    GET  /api/v2/slack/posts/{id}    where that post, or a DM, is: queued, sent (with its link), canceled or failed
    POST /api/v2/slack/dms           a DM to a person: the bot's message to them in Tico, which the gateway copies into
                                     their Slack DM (`originate`), unless they keep bot messages in Tico
    GET  /api/v2/slack/files/{id}    a file someone shared, downloaded by the gateway

Every gate is here, not on the computer: the bot's `service: slack` entry (`read`, `post`, and its `channels` and `dms`
scope), onboarding, the channel list (Settings > Tools > Slack: readers, posting on, an id), the length limit
(slack_gateway.POST_CHARS), a recipient on the roster who may sign in, the org rule check on what is posted, and
screening on what is read. The gateway refuses a channel shared outside the workspace (Slack Connect) for both, since
only Slack can say. `channels: all` (or `"*"`) lets a bot read any channel the gateway can see; it widens reads only,
and a post still needs its channel on the list with posting on.
`connectors/slack.py` is a thin client of these routes.

History and files wait on the gateway for up to a minute or more, so those two routes are async and hold no thread while
they wait (slack_requests.ask); at most MAX_WAITS wait at once per server, and more answer 503 `slack_busy`.
"""

import asyncio
import hashlib
import json
import re
import threading
import time
import urllib.parse
from typing import Any, Callable

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb as _H
from . import org_rule_check as _OR
from . import slack_channels as _SC
from . import slack_requests as SR
from .store import Problem

# The untyped modules this one calls, typed as Any at the door.
H: Any = _H
OR: Any = _OR
SC: Any = _SC
Row = dict[str, Any]


def _mod(name: str) -> Any:
    """A neighbour module, imported when used and typed as Any at the door. Every name is a literal import, so the PR
    test selection (scripts/pr_checks.py DependencyMap) sees exactly these."""
    if name == "bot_tools":
        from . import bot_tools as found
    elif name == "lifecycle":
        from . import lifecycle as found
    elif name == "models":
        from . import models as found
    elif name == "people":
        from . import people as found
    elif name == "rules":
        from . import rules as found
    elif name == "screening":
        from . import screening as found
    elif name == "slack_app":
        from . import slack_app as found
    elif name == "slack_gateway":
        from . import slack_gateway as found
    elif name == "views":
        from . import views as found
    else:
        raise ValueError(name)
    return found


HISTORY_WAIT, FILE_WAIT = 60.0, 150.0
# Waits on the gateway in flight per server. A wait holds no thread, only a row and a poll, but the gateway answers the
# rows on a few workers (slack_requests.WORKERS), so past this many more would only queue: 503 `slack_busy` instead.
MAX_WAITS, BUSY_RETRY_SECONDS = 16, 5
BUSY = "Tico is already waiting on Slack for many bots; try again in a few seconds."
TS_RE = r"^\d{6,12}\.\d{1,6}$"
FILE_RE = re.compile(r"^F[A-Z0-9]{6,}$")
OFF = {"no", "false", "off", "0"}
# `channels:` words that mean every channel. A channel named all is written `#all`, which is a channel.
ALL_CHANNELS = ("all", "*")


class SlackPost(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    channel: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=50_000)
    thread_ts: str | None = Field(default=None, pattern=TS_RE)


class SlackDM(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    to: str = Field(min_length=1, max_length=200)
    text: str = Field(max_length=50_000)


# ----------------------------------------------------------------------------- gates
def bot_name(c: Any, bot: str) -> str:
    row: Row = H.bot(c, bot) or {}
    return str(row.get("display_name") or row.get("slug") or bot)


def slack_entry(c: Any, settings: Any, bot: str) -> dict[str, Any]:
    """The bot's `service: slack` entry, as its computer reports it, or a refusal saying what to add."""
    tools: list[Row] = _mod("bot_tools").reported_tools(c, settings, bot)
    for entry in tools:
        if str(entry.get("service") or "").lower() == "slack":
            return entry
    raise Problem(
        "slack_access",
        f"{bot_name(c, bot)} does not declare Slack access. Add a `tools:` entry with service: slack to its bot.yaml; "
        "that is the owner's call.",
        403,
    )


def need(c: Any, bot: str, entry: dict[str, Any], verb: str) -> None:
    # No `can` (missing, or the empty list a runner reports for it) means read and post; a listed `can:` is exact, so
    # `can: [read]` stays read-only.
    can: list[Any] = entry.get("can") or ["read", "post"]
    verbs = [str(v).lower() for v in can]
    if verb not in verbs:
        raise Problem(
            "slack_access",
            f"{bot_name(c, bot)} may {', '.join(verbs) or 'do nothing'} on Slack, not {verb}. Add `{verb}` to the Slack "
            "entry's `can:` in bot.yaml; that is the owner's call.",
            403,
        )


def scope_values(entry: dict[str, Any], key: str) -> list[str]:
    """A scope key's values as written (stripped, `#` and case kept): a YAML list, or text split on commas or spaces."""
    scope: Row = entry.get("scope") or {}
    value: Any = scope.get(key)
    items: list[Any] = list(value) if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value or ""))  # pyright: ignore[reportUnknownArgumentType]
    return [str(v).strip() for v in items if str(v).strip()]


def scope_items(entry: dict[str, Any], key: str) -> list[str]:
    """A scope key's items, lowercased with no `#`. `all` and `*` are not items: under `channels` they mean every
    channel (reads_all_channels)."""
    return [v.lstrip("#").lower() for v in scope_values(entry, key) if v.lower() not in ALL_CHANNELS]


def reads_all_channels(entry: dict[str, Any]) -> bool:
    """`channels: all` (or `"*"`, or either in a list): the bot may read any channel the gateway can see, Slack Connect
    aside. It widens reads only: posting still needs the channel on the list with posting on."""
    return any(v.lower() in ALL_CHANNELS for v in scope_values(entry, "channels"))


def dms_on(entry: dict[str, Any]) -> bool:
    scope: Row = entry.get("scope") or {}
    value = scope.get("dms")
    return str(value).strip().lower() not in OFF if value is not None else True


def readable(c: Any, settings: Any, bot: str, entry: dict[str, Any]) -> set[str]:
    """The channels a bot may read, by lowercased id and name: those whose list entry names it as a reader, and those
    its entry declares under `channels:` (with the listed entry's other name). `channels: all` is not a name here:
    may_read takes it as every channel."""
    declared = set(scope_items(entry, "channels"))
    row: Row = H.bot(c, bot) or {}
    me = {bot.lower(), str(row.get("slug") or "").lower()} - {""}
    allowed = set(declared)
    for row in SC.listing(c, settings):
        keys = {str(row["id"]).lower(), str(row["name"]).lower()} - {""}
        if keys & declared or me & {str(r).lower() for r in row["readers"]}:
            allowed |= keys
    return allowed


def may_read(c: Any, settings: Any, bot: str, allowed: set[str], ref: str, everywhere: bool = False) -> str:
    """The channel the gateway reads: its id when Tico knows it, else a name for the gateway to look up. Refused outside
    the bot's channels, unless `everywhere` (`channels: all`): then any channel the gateway can see, which refuses
    Slack Connect itself. A ref with an id is authorized on that id alone: the name beside it in `<#C...|name>` is the
    caller's word, never Slack's, so it grants nothing."""
    cid, name = SC.parse_ref(ref)
    if cid:
        target = cid if everywhere or cid.lower() in allowed else ""
    else:
        listed = next((r for r in SC.listing(c, settings) if r["name"] == name and r["id"]), None)
        target = (str(listed["id"]) if listed else name) if everywhere or name in allowed else ""
    if not target:
        names = ", ".join(sorted("#" + a for a in allowed if not SC.ID_RE.match(a.upper()))) or "none"
        raise Problem(
            "slack_scope",
            f"{bot_name(c, bot)} may not read {ref}. Its Slack channels are: {names}. An owner or admin can add it as a "
            "reader (Settings > Tools > Slack, or ask Agent Ops).",
            403,
        )
    return target


def postable(c: Any, settings: Any, ref: str) -> dict[str, Any]:
    """The channel list's entry for a post: listed, posting on, and its id known."""
    cid, name = SC.parse_ref(ref)
    entry, _ = SC.lookup(c, settings, cid, name)
    if entry is None and not cid:
        entry = next((r for r in SC.listing(c, settings) if r["name"] == name), None)
    if entry is None:
        raise Problem(
            "slack_channel",
            f"{ref} is not on the Slack channel list. An owner or admin adds it in Settings > Tools > Slack.",
            403,
        )
    if not entry["post"]:
        raise Problem("slack_channel", f"Posting in {entry['channel']} is off on the Slack channel list.", 403)
    if not entry["id"]:
        raise Problem(
            "slack_channel",
            f"{entry['channel']} is listed by name only and the gateway has not found its id yet; try again shortly, "
            "or list it by id.",
            409,
            retryable=True,
        )
    return entry


def not_onboarding(c: Any, bot: str) -> None:
    if _mod("lifecycle").is_onboarding(c, bot):
        raise Problem(
            "onboarding",
            f"{bot_name(c, bot)} is still onboarding, so it sends nothing to Slack yet. A person above it on the org "
            "chart marks onboarding complete.",
            403,
        )


def check_text(text: str) -> str:
    """The text of a post or a DM: not empty, and no longer than the gateway sends whole (POST_CHARS), so a post never
    loses text on the way to Slack."""
    text = str(text or "").strip()
    limit = int(_mod("slack_gateway").POST_CHARS)
    if not text:
        raise Problem("slack_text", "Nothing to send: the text is empty.", 422)
    if len(text) > limit:
        raise Problem(
            "slack_text",
            f"The text is {len(text)} characters; Slack posts from bots stop at {limit}. Shorten it, or put "
            "the detail on the task and link it.",
            422,
        )
    return text


def gateway_up(c: Any) -> None:
    state = str(_mod("slack_app").status(c)["state"])
    if state != "connected":
        raise Problem("slack_gateway_off", f"{SR.NOT_RUNNING} (It is {state}.)", 503, retryable=True)


def recipient(c: Any, to: str) -> dict[str, Any]:
    """The person a DM goes to, by work email, Slack @handle or id, or Tico id: on the roster, allowed to sign in, and
    with a Slack account Tico knows."""
    want = str(to or "").strip().lower().lstrip("@").removeprefix("human:")
    # A Slack id the gateway learned (humans.slack_id) as well as one on the roster.
    learned = {str(r[0]): str(r[1]) for r in c.execute("SELECT id,slack_id FROM humans WHERE slack_id<>''")}
    people: list[Row] = _mod("views").roster(c).get("people") or []
    found: Row | None = next(
        (
            p
            for p in people
            if want
            and want
            in {str(p.get(k) or "").lower() for k in ("id", "email", "slack", "slack_id")}
            | {learned.get(str(p.get("id")), "").lower()}
        ),
        None,
    )
    if not found or not _mod("people").may_sign_in(found):
        raise Problem(
            "slack_recipient",
            f"{to} is not a person at the company who may sign in to Tico; bots DM only them. An owner or admin adds "
            "people in Settings > People.",
            403,
        )
    slack_id = str(found.get("slack_id") or learned.get(str(found["id"])) or "")
    if not slack_id:
        raise Problem(
            "slack_recipient",
            f"Tico knows no Slack account for {found['name']}: add their Slack id to their profile, or message them in "
            "Tico.",
            409,
        )
    return found


# ----------------------------------------------------------------------------- the post
def queue_post(c: Any, bot: str, entry: dict[str, Any], thread_ts: str, text: str) -> dict[str, Any]:
    """A bot's post: a message in its room, so people see what it said, and the outbox row the gateway sends."""
    room: Row = _mod("rules")._bot_room(c, bot)
    refs = {"slack_post": {"channel": entry["id"], "name": entry["name"], "thread_ts": thread_ts}}
    message: dict[str, Any] = H._write_message(
        c, H.bot_actor(bot), room.get("owner_id") or H.TICO_ACTOR, text, room, "say", refs, None, None
    )
    now = H.now()
    c.execute(
        "INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created_at,updated_at,recipient_actor) "
        "VALUES(?,?,?,?,?,'ready',?,?,?)",
        (message["id"], entry["id"], thread_ts, bot, text, now, now, message.get("recipient_id")),
    )
    return {"message_id": message["id"], "state": "ready", "channel": entry["id"], "name": entry["name"]}


@OR.replayer("slack_post")
def replay_post(c: Any, bot: Any, hold: Any, replay: dict[str, Any], ingest: Any, gate: Any = None) -> dict[str, Any]:
    """Send anyway for a held post: queued again as the bot, if its channel is still listed with posting on."""
    slug = str(hold["author"]).removeprefix("bot:")
    entry = postable(c, None, str(replay.get("channel") or ""))  # the list is in the database: no settings needed
    return queue_post(c, slug, entry, str(replay.get("thread_ts") or ""), OR.warned_text(gate, replay["text"]))


def post_state(c: Any, who: Any, mid: str) -> dict[str, Any]:
    message = H.message(c, mid)
    if not message or message.get("sender_id") != who.actor:
        raise Problem("not_found", "No such post or DM from this bot", 404)
    row = c.execute("SELECT * FROM slack_posts WHERE message_id=?", (mid,)).fetchone()
    if not row:
        return {"message_id": mid, "state": "queued"}
    url = c.execute("SELECT value FROM registry_metadata WHERE key='slack_workspace_url'").fetchone()
    permalink = _mod("slack_gateway").permalink

    return {
        "message_id": mid,
        "state": row["state"],
        "channel": row["channel"],
        "thread_ts": row["thread_ts"],
        "slack_ts": row["slack_ts"],
        "error": row["error"],
        "permalink": permalink(json.loads(url[0]) if url else "", row["channel"], row["slack_ts"], row["thread_ts"])
        if row["slack_ts"]
        else None,
    }


# ----------------------------------------------------------------------------- reading
def screened(store: Any, bot: str, text: str) -> str:
    """Screening on what a bot reads from Slack: a hold refuses the read, a warning comes back as its line."""
    screening = _mod("screening")

    if not text.strip():
        return ""
    verdict = screening.screen(store, "slack", text, bot=bot, key="read:" + hashlib.sha256(text.encode()).hexdigest())
    if verdict.get("action") == "hold":
        raise Problem(
            "screening_held",
            "Screening held what Slack returned: it looks like a prompt injection. A person can read it in Slack.",
            403,
        )
    return screening.warning_line(verdict) if verdict.get("action") == "warn" else ""


def all_text(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for top in messages:
        replies: list[Row] = top.get("replies") or []
        lines += [str(m.get("text") or "") for m in [top, *replies]]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- routes
def install(
    app: Any, settings: Any, store: Any, auth: Any, checked_write: Callable[..., Any], send_message: Callable[..., Any]
) -> None:
    def bot_of(request: Request) -> tuple[Any, str]:
        who = request.state.identity
        auth.domain(who)
        if who.role != "bot" or not str(who.actor).startswith("bot:"):
            raise Problem("forbidden", "These routes are for bots; a person uses Slack itself.", 403)
        return who, str(who.actor)[4:]

    app.state.slack_waits = threading.BoundedSemaphore(MAX_WAITS)

    async def wait_for_slack(kind: str, bot: str, params: dict[str, Any], wait: float) -> dict[str, Any] | Response:
        """The gateway's answer, waited for without a thread (slack_requests.ask). At most MAX_WAITS at once per
        server: past that, 503 `slack_busy` with Retry-After, before anything is asked of Slack."""
        slots: Any = app.state.slack_waits
        if not slots.acquire(blocking=False):
            return JSONResponse(
                {"error": {"code": "slack_busy", "detail": BUSY, "retryable": True}},
                status_code=503,
                headers={"Retry-After": str(BUSY_RETRY_SECONDS)},
            )
        try:
            return await SR.ask(store, kind, bot, params, wait=wait)
        finally:
            slots.release()

    @app.get("/api/v2/slack/history")
    async def slack_history(
        request: Request,
        channel: str = Query(min_length=1, max_length=200),
        oldest: float = Query(ge=0),
        latest: float | None = Query(default=None, ge=0),
        threads: bool = False,
    ) -> Any:
        """A channel's messages from `oldest` to `latest` (Unix seconds; now by default), with threads on request."""
        end = time.time() if latest is None else latest

        def gates() -> tuple[str, dict[str, Any]]:
            _, bot = bot_of(request)
            if end < oldest:
                raise Problem("slack_range", "`latest` is before `oldest`.", 422)
            with store.read() as c:
                entry = slack_entry(c, settings, bot)
                need(c, bot, entry, "read")
                ref = may_read(
                    c, settings, bot, readable(c, settings, bot, entry), channel, everywhere=reads_all_channels(entry)
                )
                gateway_up(c)
            return bot, {"channel": ref, "oldest": f"{oldest:.6f}", "latest": f"{end:.6f}", "threads": threads}

        bot, params = await asyncio.to_thread(gates)
        answer = await wait_for_slack("history", bot, params, HISTORY_WAIT)
        if isinstance(answer, Response):
            return answer
        answer.pop("request_id", None)
        warning = await asyncio.to_thread(screened, store, bot, all_text(answer.get("messages") or []))
        return {"ok": True, **answer, **({"warning": warning} if warning else {})}

    @app.post("/api/v2/slack/posts")
    def slack_post(request: Request, body: SlackPost) -> Any:
        """A post in a channel on the list, queued for the gateway and checked against the org's rules first."""
        who, bot = bot_of(request)
        with store.read() as c:
            need(c, bot, slack_entry(c, settings, bot), "post")
            not_onboarding(c, bot)
            entry = postable(c, settings, body.channel)
            gateway_up(c)
        text, thread_ts = check_text(body.text), body.thread_ts or ""
        return checked_write(
            request,
            body,
            who,
            "slack_post",
            text,
            OR.company,
            lambda c, gate: queue_post(c, bot, entry, thread_ts, OR.warned_text(gate, text)),  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
            {"channel": entry["id"], "thread_ts": thread_ts, "text": text},
            context={"slack_channel": entry["channel"]},
        )

    @app.get("/api/v2/slack/posts/{mid}")
    def slack_post_state(request: Request, mid: str) -> dict[str, Any]:
        who, _ = bot_of(request)
        with store.read() as c:
            return post_state(c, who, mid)

    @app.post("/api/v2/slack/dms")
    def slack_dm(request: Request, body: SlackDM) -> Any:
        """A DM to a person: the bot's message to them, which the gateway copies into their Slack DM."""
        _, bot = bot_of(request)
        with store.read() as c:
            need(c, bot, slack_entry(c, settings, bot), "post")
            not_onboarding(c, bot)
            person = recipient(c, body.to)
            gateway_up(c)
        message = _mod("models").MessageCreate(to="human:" + person["id"], text=check_text(body.text))
        answer = send_message(request, message)
        return {**answer, "message_id": answer.get("id"), "to": person["email"] or person["id"]}

    @app.get("/api/v2/slack/files/{fid}")
    async def slack_file(request: Request, fid: str) -> Response:
        """A file someone shared, by its `F...` id: where the bot may read its message."""

        def gates() -> tuple[str, dict[str, Any]]:
            _, bot = bot_of(request)
            if not FILE_RE.match(fid):
                raise Problem("slack_file", "A Slack file id looks like F0123ABCD, from a `[file F...]` line.", 422)
            with store.read() as c:
                entry = slack_entry(c, settings, bot)
                need(c, bot, entry, "read")
                params = {
                    "file": fid,
                    "channels": sorted(readable(c, settings, bot, entry)),
                    "all_channels": reads_all_channels(entry),
                    "dms": dms_on(entry),
                }
                gateway_up(c)
            return bot, params

        bot, params = await asyncio.to_thread(gates)
        answer = await wait_for_slack("file", bot, params, FILE_WAIT)
        if isinstance(answer, Response):
            return answer
        raw = await asyncio.to_thread(SR.take_file, settings, str(answer["request_id"]))
        mimetype = str(answer.get("mimetype") or "")
        text = raw[:200_000].decode("utf-8", "replace") if mimetype.startswith("text/") else ""
        warning = await asyncio.to_thread(screened, store, bot, text) if text else ""
        headers = {
            "X-Slack-File-Id": str(answer.get("id") or fid),
            "X-Slack-File-Name": urllib.parse.quote(str(answer.get("name") or fid)),
            "X-Slack-File-Type": urllib.parse.quote(mimetype),
            **({"X-Tico-Screening": urllib.parse.quote(warning)} if warning else {}),
        }
        return Response(raw, media_type="application/octet-stream", headers=headers)
