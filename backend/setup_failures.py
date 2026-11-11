# pyright: strict
"""What a failed setup step leaves on the server (docs/support.md, "A failed setup step").

A person who starts a setup action and is refused gets a sentence. Without a row, that sentence is all anyone can read
afterwards. `record` writes one `setup.failed` event for a refusal on a setup route: a category from the code's fixed
list (the `Problem` code, never its text), the step (the route's template, never the address with its values), the
HTTP status, the ids the route names and a short reference the answer carries back to the person.

Never kept: the request's body, query string or headers, the refusal's sentence, an exception's message or anything a
provider answered. `failures` reads these rows and the other failure events back for the support bundle
(backend/diagnostics.py), by a fixed list of detail keys.
"""

import json
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, cast

from . import hubdb as H

ACTION = "setup.failed"
CODE = re.compile(r"[a-z][a-z0-9_]{0,59}")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
# A write under one of these is a setup action a person (or a computer joining) started.
WRITES = (
    "/api/v2/github/app",
    "/api/v2/github/repos",
    "/api/v2/github/product-repos",
    "/api/v2/bots/{bot}/github-repos",
    "/api/v2/bots/{bot}/go-live",
    "/api/v2/bots/{bot}/cloud-machine",
    "/api/v2/cloud-machines/shared",
    "/api/v2/model-subscriptions",
    "/api/v2/computer-subscriptions",
    "/api/v2/enrollments",
    "/api/v2/runners/enroll",
    "/api/v2/runners/{rid}/logins",
    "/api/v2/runners/{rid}/shared-key",
    "/api/v2/runners/{rid}/keys",
    "/api/v2/runners/{rid}/harness-actions",
)
# The reads that are setup steps: GitHub Connect's form and GitHub's returns to it.
READS = (
    "/api/v2/github/app/manifest",
    "/api/v2/github/app/callback",
    "/api/v2/github/app/authorized",
    "/api/v2/github/app/authorize-link",
    "/api/v2/github/app/install",
    "/api/v2/github/app/installed",
)
PARAMS = {"bot": "bot", "rid": "computer", "lid": "login", "sid": "subscription"}  # a route's ids, by what they name
EXTRA = ("github_status", "rate_limited", "timed_out")  # numbers and flags a Problem carries; never text
SESSION = ("identity", "revoked", "unauthenticated")  # the 401s of a missing or ended session
REPEAT_SECONDS = 60
ANONYMOUS_PER_MINUTE = 20  # GitHub's return and a computer's enroll take callers with no session
# The failure events a support bundle lists, and the detail keys read from them. An event of one of these kinds shows
# there with whichever of these keys it carries, so a new failure event needs no change here beyond its kind.
KINDS = (
    ACTION,
    "runner.login.failed",
    "runner.harness.failed",
    "cloud_machine.refused",
    "cloud_machine.list_refused",
    "cloud_machine.restart_failed",
    "bot.go_live_failed",
    "subscription.rejected",
    "attempt.auth_rejected",
)
NUMBERS = ("status", "github_status", "exit_code", "elapsed_ms", "count")
WORDS = ("step", "ref", "kind", "action", "runtime", "harness", "error", "login", "id")
LINES, LINE = 20, 300
KEEP_DAYS, KEEP_ROWS = 7, 30

_lock = threading.Lock()
_anonymous: list[float] = []
_shift = cast("Callable[..., str]", H.shift)
_event = cast("Callable[[sqlite3.Connection, str, str, str, dict[str, Any]], object]", H.event)


def step(method: str, template: object) -> str | None:
    """The step a route is, as `METHOD /template`, or None when the route is not a setup step."""
    path = str(template or "")
    if method == "GET":
        return "GET " + path if path in READS else None
    if any(path == p or path.startswith(p + "/") for p in WRITES):
        return method + " " + path
    return None


def _allow_anonymous() -> bool:
    now = time.monotonic()
    with _lock:
        _anonymous[:] = [t for t in _anonymous if now - t < 60]
        if len(_anonymous) >= ANONYMOUS_PER_MINUTE:
            return False
        _anonymous.append(now)
        return True


def _document(text: object) -> dict[str, Any]:
    try:
        loaded: Any = json.loads(str(text or "{}"))
    except ValueError:
        return {}
    return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}


def record(
    store: Any,
    request: Any,
    code: object,
    status: int,
    extra: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> str | None:
    """Keep one refusal of a setup step and return its reference, or None when nothing was kept. Never raises: a
    failure to record must not change what the person is told."""
    try:
        found = step(str(request.method), getattr(request.scope.get("route"), "path", ""))
        # A session that ended is not a setup step's failure; a computer's refused enrollment code (also 401) is.
        if not found or status < 400 or (status == 401 and code in SESSION):
            return None
        who = getattr(request.state, "identity", None)
        actor = str(getattr(who, "actor", "") or "")
        if not actor:
            if not _allow_anonymous():
                return None
            actor = "anonymous"
        category = code if isinstance(code, str) and CODE.fullmatch(code) else "other"
        detail: dict[str, Any] = {"category": category, "step": found, "status": int(status)}
        params = cast("dict[str, Any]", request.path_params or {})
        for name, label in PARAMS.items():
            value = str(params.get(name) or "")
            if ID.fullmatch(value):
                detail[label] = value
        for key in EXTRA:
            number = (extra or {}).get(key)
            if isinstance(number, (bool, int)):
                detail[key] = number
        if error is not None:
            detail["error"] = type(error).__name__[:60]
        with store.transaction() as c:
            since = _shift(H.now(), seconds=-REPEAT_SECONDS)
            for row in c.execute(
                "SELECT id, detail FROM events WHERE actor=? AND action=? AND target=? AND created_at>=? "
                "ORDER BY created_at DESC LIMIT 5",
                (actor, ACTION, found, since),
            ).fetchall():
                kept = _document(row["detail"])
                if kept.get("category") == category:
                    # The same refusal again within a minute (a retry, a page asking twice) is one row with a count.
                    kept["count"] = int(kept.get("count") or 1) + 1
                    c.execute("UPDATE events SET detail=? WHERE id=?", (json.dumps(kept), row["id"]))
                    return str(kept.get("ref") or "") or None
            ref = "sf-" + secrets.token_hex(4)
            detail["ref"] = ref
            _event(c, actor, ACTION, found, detail)
            return ref
    except Exception:  # noqa: BLE001 - the person's answer matters more than its record
        return None


def lines(values: object, keep: int = LINES) -> list[str]:
    """A computer's log lines as a failure keeps them: the last few, each scrubbed again here and cut short."""
    from . import model_login

    scrub = cast("Callable[[object], str]", model_login.redact)
    out: list[str] = []
    for value in cast("list[object]", values)[-keep:] if isinstance(values, list) else []:
        text = scrub(value)[:LINE]
        if text:
            out.append(text)
    return out


def failures(
    c: sqlite3.Connection,
    runner_label: Callable[[str], str | None] = lambda runner_id: None,
    bot_label: Callable[[str], str | None] = lambda bot: None,
) -> list[dict[str, Any]]:
    """The last failure events, newest first, for the support bundle: only the keys named above, so a field nobody
    listed (a provider's sentence, a name) cannot appear. `runner_label` and `bot_label` give a computer's and a
    bot's label in the bundle (`runner-1`, `bot-3`); one with no label is named only as "computer" or "bot", never
    by its id or slug, which is its name."""
    marks = ",".join("?" * len(KINDS))
    out: list[dict[str, Any]] = []
    for row in c.execute(
        f"SELECT created_at, actor, action, target, detail FROM events INDEXED BY events_ts "
        f"WHERE created_at>=? AND action IN ({marks}) ORDER BY created_at DESC LIMIT ?",
        (_shift(H.now(), days=-KEEP_DAYS), *KINDS, KEEP_ROWS),
    ):
        detail = _document(row["detail"])
        who, action, target = str(row["actor"]), str(row["action"]), str(row["target"] or "")
        if who.startswith("runner:"):  # a computer's own report: its label, as the bundle's `runners` rows have it
            who = runner_label(who[7:]) or "computer"
        elif who.startswith("bot:"):
            who = bot_label(who[4:]) or "bot"
        item: dict[str, Any] = {"at": row["created_at"], "event": action, "who": who}
        category = detail.get("category") or detail.get("code")
        item["category"] = category if isinstance(category, str) and CODE.fullmatch(category) else ""
        for key in NUMBERS:
            number = detail.get(key)
            if isinstance(number, (bool, int)):
                item[key] = int(number)
        for key in WORDS:
            word = detail.get(key)
            if isinstance(word, str) and word:
                item[key] = word[:120]
        bot = detail.get("bot") or (target if action.startswith("bot.") else "")
        if isinstance(bot, str) and bot:
            item["bot"] = bot_label(bot) or "bot"
        computer = detail.get("computer") or detail.get("runner") or target
        label = runner_label(computer) if isinstance(computer, str) and computer else None
        if label:
            item["computer"] = label
        kept = detail.get("lines")
        if isinstance(kept, list):
            item["lines"] = [str(x)[:LINE] for x in cast("list[object]", kept)[-LINES:]]
        out.append(item)
    return out
