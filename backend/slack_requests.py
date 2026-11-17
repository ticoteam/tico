# pyright: strict
"""Requests the API makes of Slack through the gateway: a channel's history and a shared file.

The API process never holds a Slack token (config.slack_credentials). A bot's read goes into `slack_requests`; the
gateway (backend/slack_gateway.py), which holds the token, claims it on its own thread and answers it on one of WORKERS
workers, checking before each Slack call that the API still waits (`alive`). It writes the answer back, and the API
deletes the row once it has read it. A file's bytes go to a folder beside the database, which the API and the gateway
share (the `tico-data` volume), never into the database: the API streams them and deletes them.

What a bot may read is decided by the API before it asks (backend/slack_bots.py). The gateway checks what only Slack
can tell: that a channel is not shared outside the workspace (Slack Connect), and where a file was shared.
"""

import asyncio
import json
import logging
import os
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import hubdb as _H
from . import slack_errors as SE
from .store import Problem

H: Any = _H  # untyped: typed as Any at the door

LOG = logging.getLogger("tico.slack_requests")

POLL_SECONDS = 0.2  # how often the API looks for its answer
SERVE_SECONDS = 0.5  # how often the gateway looks for new requests
CLAIM_SECONDS = 8  # a request no gateway picked up by then: the gateway is not running
KEEP_SECONDS = 600  # rows and files left behind (an API that stopped waiting) are removed after this
MAX_PAGES = 20  # 4,000 top-level messages per read at most
MAX_REPLY_PAGES = 50  # 10,000 replies per thread at most, as the computer's connector read them
WORKERS = 4  # requests the gateway answers at once, so one slow read never holds up the rest
QUEUED = 32  # requests claimed and not yet answered, at most; the rest wait in the table
MAX_FILE_BYTES = 50 * 1024 * 1024
NOISE_SUBTYPES = {"channel_join", "channel_leave", "group_join", "group_leave"}
NOT_RUNNING = (
    "The Slack gateway is not answering, so Tico cannot reach Slack for bots. Start it: TICO_SLACK_GATEWAY_ENABLED=1 "
    "and the slack profile in COMPOSE_PROFILES (docs/slack-gateway.md), with the tokens in Tools > Slack."
)


class Refused(Exception):
    """What the gateway refuses or cannot do, as the API answers it."""

    def __init__(self, code: str, detail: str, status: int = 403):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def files_dir(settings: Any) -> Path:
    """Where the gateway leaves a file for the API: beside the database, private to Tico's user."""
    return Path(settings.db_path).parent / "slack-files"


# ----------------------------------------------------------------------------- the API's side
async def ask(store: Any, kind: str, bot: str, params: dict[str, Any], wait: float = 30.0) -> dict[str, Any]:
    """Write one request and wait for the gateway's answer. 503 when no gateway claims it in CLAIM_SECONDS, 504 when it
    is not done in `wait`; a refusal comes back as the gateway's Problem.

    The wait holds no thread: it sleeps on the event loop and hops to a thread only for each short read, so bots
    waiting on Slack never starve the server's thread pool. A wait the caller abandons leaves its row to `sweep`."""
    # The gateway stops working on it once the API has stopped waiting (`alive`).
    rid = await asyncio.to_thread(_submit, store, kind, bot, {**params, "deadline": time.time() + wait})
    started = time.monotonic()
    while True:
        await asyncio.sleep(POLL_SECONDS)
        answer = await asyncio.to_thread(_step, store, rid, time.monotonic() - started, wait)
        if answer is not None:
            return answer


def _submit(store: Any, kind: str, bot: str, params: dict[str, Any]) -> str:
    rid, now = H.new_id(), H.now()
    with store.transaction() as c:
        c.execute(
            "INSERT INTO slack_requests(id,kind,bot,params,state,created_at,updated_at) VALUES(?,?,?,?,'pending',?,?)",
            (rid, kind, bot, json.dumps(params), now, now),
        )
    return rid


def _step(store: Any, rid: str, waited: float, wait: float) -> dict[str, Any] | None:
    """One look at request `rid`: its answer when the gateway is done, None while it is still to come, or a Problem."""
    with store.read() as c:
        row = c.execute("SELECT state,result,error FROM slack_requests WHERE id=?", (rid,)).fetchone()
    state = row["state"] if row else "failed"
    if state in ("done", "failed"):
        _forget(store, rid)
        if state == "done":
            return {**json.loads(row["result"] or "{}"), "request_id": rid}
        error = json.loads((row["error"] if row else None) or "{}")
        raise Problem(
            str(error.get("code") or "slack_failed"),
            str(error.get("detail") or "Slack did not answer"),
            int(error.get("status") or 502),
        )
    if (state == "pending" and waited >= CLAIM_SECONDS) or waited >= wait:
        with store.transaction() as c:
            gone = c.execute("DELETE FROM slack_requests WHERE id=? AND state=?", (rid, state)).rowcount
        if gone:
            if state == "pending":
                raise Problem("slack_gateway_off", NOT_RUNNING, 503, retryable=True)
            raise Problem("slack_timeout", "Slack took too long to answer; try a shorter time range", 504)
    return None


def _forget(store: Any, rid: str) -> None:
    with store.transaction() as c:
        c.execute("DELETE FROM slack_requests WHERE id=?", (rid,))


def take_file(settings: Any, rid: str) -> bytes:
    """The bytes the gateway left for request `rid`, removed as they are read."""
    path = files_dir(settings) / rid
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


# ----------------------------------------------------------------------------- the gateway's side
class Gone(Exception):
    """The API stopped waiting for this request (its row is gone, or its deadline passed): stop working on it."""


def alive(gateway: Any, rid: str, params: dict[str, Any]) -> None:
    """Raise Gone when nobody waits for request `rid` any more. Called before each Slack call a request makes, so a
    read the API gave up on stops at once instead of fetching every page and thread."""
    if time.time() > float(params.get("deadline") or "inf"):
        raise Gone(rid)
    with gateway.store.read() as c:
        row = c.execute("SELECT state FROM slack_requests WHERE id=?", (rid,)).fetchone()
    if row is None or row["state"] != "running":
        raise Gone(rid)


class Workers:
    """The gateway's request workers: WORKERS at once, at most QUEUED claimed and not yet answered."""

    def __init__(self, gateway: Any, workers: int = WORKERS):
        self.gateway, self.busy, self.lock = gateway, 0, threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="slack-request")

    def room(self) -> int:
        with self.lock:
            return max(0, QUEUED - self.busy)

    def submit(self, row: Any) -> None:
        with self.lock:
            self.busy += 1
        self.pool.submit(self.run, row)

    def run(self, row: Any) -> None:
        try:
            answer(self.gateway, row)
        finally:
            with self.lock:
                self.busy -= 1

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def serve(gateway: Any, limit: int = 4, workers: Workers | None = None) -> int:
    """Claim up to `limit` waiting requests, oldest first, and answer them: on `workers` when given (the request
    thread), else here one after another. Returns how many were claimed."""
    store = gateway.store
    if workers is not None:
        limit = min(limit, workers.room())
    claimed: list[Any] = []
    if limit <= 0:
        return 0
    with store.transaction() as c:
        for row in c.execute(
            "SELECT id,kind,bot,params FROM slack_requests WHERE state='pending' ORDER BY created_at LIMIT ?", (limit,)
        ).fetchall():
            if c.execute(
                "UPDATE slack_requests SET state='running',updated_at=? WHERE id=? AND state='pending'",
                (H.now(), row["id"]),
            ).rowcount:
                claimed.append(row)
    for row in claimed:
        if workers is not None:
            workers.submit(row)
        else:
            answer(gateway, row)
    return len(claimed)


def answer(gateway: Any, row: Any) -> None:
    """Run one request's handler and write its answer, unless the API stopped waiting for it."""
    store, rid = gateway.store, row["id"]
    params = json.loads(row["params"] or "{}")
    try:
        alive(gateway, rid, params)
        result = HANDLERS[row["kind"]](gateway, params, rid)
        state, result_text, error = "done", json.dumps(result), None
    except Gone:
        LOG.info("Slack %s request for %s dropped: the API stopped waiting", row["kind"], row["bot"])
        (files_dir(gateway.settings) / rid).unlink(missing_ok=True)
        return
    except Refused as exc:
        state, result_text = "failed", None
        error = {"code": exc.code, "detail": exc.detail, "status": exc.status}
    except Exception as exc:  # noqa: BLE001 Slack's refusal, Slack unreachable, or a bug: the bot is told, not left waiting
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        state, result_text = "failed", None
        error = {"code": "slack_failed", "detail": f"Slack refused or could not be reached ({code})", "status": 502}
        LOG.warning("Slack %s request for %s failed: %s", row["kind"], row["bot"], code)
    with store.transaction() as c:
        written = c.execute(
            "UPDATE slack_requests SET state=?,result=?,error=?,updated_at=? WHERE id=? AND state='running'",
            (state, result_text, json.dumps(error) if error else None, H.now(), rid),
        ).rowcount
    if not written:  # the API gave up while the answer was being made
        (files_dir(gateway.settings) / rid).unlink(missing_ok=True)


def sweep(gateway: Any) -> int:
    """Rows and files an API stopped waiting for: gone after KEEP_SECONDS."""
    cutoff = H.shift(H.now(), seconds=-KEEP_SECONDS)
    with gateway.store.transaction() as c:
        gone = c.execute("DELETE FROM slack_requests WHERE updated_at<?", (cutoff,)).rowcount
    folder = files_dir(gateway.settings)
    if folder.is_dir():
        oldest = time.time() - KEEP_SECONDS
        for path in folder.iterdir():
            try:
                if path.stat().st_mtime < oldest:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    return gone


def serve_forever(gateway: Any) -> None:
    """The gateway's request thread: separate from its tick, which can wait on the decision model. It claims requests
    and hands them to WORKERS workers, so a slow read never holds up another."""
    swept = 0.0
    workers = Workers(gateway)
    try:
        while not gateway.stop.is_set():
            try:
                if time.monotonic() - swept > 60:
                    sweep(gateway)
                    swept = time.monotonic()
                serve(gateway, QUEUED, workers)
            except Exception as exc:  # noqa: BLE001 one bad pass never stops the thread
                LOG.error("Slack requests failed: %s", SE.describe(exc))
            gateway.stop.wait(SERVE_SECONDS)
    finally:
        workers.close()


def _channel(gateway: Any, ref: str) -> dict[str, Any]:
    """The channel a ref names (an id, or a name Slack looks up), refused when shared outside the workspace."""
    from .slack_channels import ID_RE

    ref = str(ref or "").strip().lstrip("#")
    cid = ref if ID_RE.match(ref) else ""
    if not cid:
        found = [ch for ch in gateway.slack.conversations_list() if str(ch.get("name") or "").lower() == ref.lower()]
        if not found:
            raise Refused("not_found", f"Slack has no channel #{ref} the app can see; invite @Tico to it", 404)
        cid = str(found[0]["id"])
    info = gateway.slack.conversations_info(cid)
    from . import slack_gateway

    if slack_gateway.is_external(info):  # pyright: ignore[reportUnknownMemberType]
        raise Refused(
            "slack_connect", "That channel is shared outside the workspace (Slack Connect): bots never use it"
        )
    return {**info, "id": cid}


def _names(gateway: Any, messages: list[dict[str, Any]]) -> dict[str, str]:
    """Each person's Slack name (`users.info`); one Slack cannot answer for keeps its id."""
    from .slack_gateway import SlackError, SlackUnreachable

    names: dict[str, str] = {}
    for uid in {str(m.get("user")) for m in messages if m.get("user") and not m.get("bot_id")}:
        try:
            user: dict[str, Any] = gateway.slack.users_info(uid) or {}
        except (SlackError, SlackUnreachable):
            user = {}
        profile: dict[str, Any] = user.get("profile") or {}
        names[uid] = str(
            profile.get("real_name") or profile.get("display_name") or user.get("real_name") or user.get("name") or uid
        )
    return names


def _shape(m: dict[str, Any], cid: str, names: dict[str, str], team_url: str, thread_ts: str = "") -> dict[str, Any]:
    from . import slack_gateway

    G: Any = slack_gateway
    humanize: Callable[..., str] = G.humanize
    ts = str(m.get("ts") or "0")
    text = humanize(m.get("text"), names)
    attachments: list[dict[str, Any]] = m.get("attachments") or []
    for att in attachments:
        extra = humanize(att.get("text") or att.get("fallback") or "", names)
        if extra:
            text += ("\n" if text else "") + "[attachment] " + extra
    for line in G.file_lines(m):
        text += ("\n" if text else "") + line
    user = str(m.get("user") or "")
    return {
        "ts": ts,
        "user": user,
        "author": names.get(user) or m.get("username") or m.get("bot_id") or user or "unknown",
        "text": text,
        "reply_count": int(m.get("reply_count") or 0),
        "permalink": G.permalink(team_url, cid, ts, thread_ts or None),
        "replies": [],
    }


def _history(gateway: Any, params: dict[str, Any], rid: str) -> dict[str, Any]:
    """A channel's top-level messages between `oldest` and `latest`, with each thread's replies when `threads`."""
    channel = _channel(gateway, str(params.get("channel") or ""))
    cid, cursor = str(channel["id"]), ""
    messages: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        alive(gateway, rid, params)
        page: list[dict[str, Any]]
        page, cursor = gateway.slack.history_range(cid, str(params["oldest"]), str(params["latest"]), cursor)
        messages.extend(m for m in page if m.get("subtype") not in NOISE_SUBTYPES)
        if not cursor:
            break
    messages.sort(key=lambda m: float(m.get("ts") or 0))
    replies: dict[str, list[dict[str, Any]]] = {}
    if params.get("threads"):
        for m in messages:
            if int(m.get("reply_count") or 0) > 0:
                replies[str(m["ts"])] = _thread(gateway, params, rid, cid, str(m["ts"]))
    names = _names(gateway, messages + [r for rows in replies.values() for r in rows])
    out: list[dict[str, Any]] = []
    for m in messages:
        row = _shape(m, cid, names, gateway.team_url)
        row["replies"] = [_shape(r, cid, names, gateway.team_url, str(m["ts"])) for r in replies.get(str(m["ts"]), [])]
        out.append(row)
    return {"id": cid, "name": str(channel.get("name") or ""), "messages": out}


def _thread(gateway: Any, params: dict[str, Any], rid: str, cid: str, ts: str) -> list[dict[str, Any]]:
    """Every reply in one thread, page by page (MAX_REPLY_PAGES at most), oldest first, the root and joins left out."""
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(MAX_REPLY_PAGES):
        alive(gateway, rid, params)
        page: list[dict[str, Any]]
        page, cursor = gateway.slack.replies_page(cid, ts, cursor)
        out.extend(r for r in page if str(r.get("ts")) != ts and r.get("subtype") not in NOISE_SUBTYPES)
        if not cursor:
            break
    return sorted(out, key=lambda r: float(r.get("ts") or 0))


def file_conversations(info: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The channels and the DMs a Slack file was shared in, from files.info."""
    shares: dict[str, dict[str, Any]] = info.get("shares") or {}
    listed: list[Any] = [*(info.get("channels") or []), *(info.get("groups") or [])]
    listed += [*(shares.get("public") or {}), *(shares.get("private") or {})]
    channels = {str(c) for c in listed}
    direct: list[Any] = info.get("ims") or []
    ims = {str(c) for c in [*channels, *direct] if str(c).startswith("D")}
    return sorted(str(c) for c in channels - ims), sorted(ims)


def _file_allowed(gateway: Any, info: dict[str, Any], params: dict[str, Any]) -> bool:
    """A file is readable where its message is: a channel the bot may read (by id or name, not Slack Connect), or a DM
    unless the bot's DMs are off."""
    shared, ims = file_conversations(info)
    if ims and params.get("dms"):
        return True
    wanted: list[Any] = params.get("channels") or []
    allowed = {str(a).lower().lstrip("#") for a in wanted}
    for cid in shared:
        try:
            channel = _channel(gateway, cid)
        except Refused:
            continue
        if {cid.lower(), str(channel.get("name") or "").lower()} & allowed:
            return True
    return False


def check_file_url(url: str) -> str:
    """The bot token is only ever sent to Slack's own file host."""
    parts = urllib.parse.urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == "slack.com" or host.endswith(".slack.com")):
        raise Refused("not_slack_file", "That file is not hosted by Slack, so it is not downloaded with the token")
    return url


def _file(gateway: Any, params: dict[str, Any], rid: str) -> dict[str, Any]:
    """A shared file, downloaded with the token and left beside the database for the API."""
    info = gateway.slack.files_info(str(params.get("file") or ""))
    if not _file_allowed(gateway, info, params):
        raise Refused("slack_scope", "That file was not shared in a channel this bot may read, or in a DM it may read")
    url = check_file_url(str(info.get("url_private_download") or info.get("url_private") or ""))
    size = info.get("size")
    if isinstance(size, (int, float)) and size > MAX_FILE_BYTES:
        raise Refused("too_large", f"That file is over the {MAX_FILE_BYTES} byte limit", 413)
    alive(gateway, rid, params)
    status, kind, raw = gateway.slack.download(url, MAX_FILE_BYTES)
    if 300 <= status < 400 or (
        status == 200 and kind.startswith("text/html") and not str(info.get("mimetype") or "").startswith("text/html")
    ):
        raise Refused(
            "files_scope",
            "Slack sent its sign-in page instead of the file: the Slack app needs the files:read scope "
            "(connectors/slack-app-manifest.yaml); reinstall it and paste the new token in Tools > Slack",
            502,
        )
    if status != 200:
        raise Refused("slack_failed", f"Slack file download failed: HTTP {status}", 502)
    alive(gateway, rid, params)
    folder = files_dir(gateway.settings)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(folder / rid, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(raw)
    return {
        "id": str(info.get("id") or params.get("file") or ""),
        "name": str(info.get("name") or info.get("title") or ""),
        "mimetype": str(info.get("mimetype") or ""),
        "bytes": len(raw),
    }


HANDLERS: dict[str, Callable[[Any, dict[str, Any], str], dict[str, Any]]] = {"history": _history, "file": _file}
