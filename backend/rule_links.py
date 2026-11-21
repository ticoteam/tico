# pyright: strict
"""Rules › Links (docs/dev/rules-plan.md): the ways an agent's writes leave the company, and the org rule check on each,
log-only for now.

A link is one way out: a proxied tool (`tool:<id>`, backend/tool_proxy.py), outgoing webhooks (`webhook`), a report to
Tico support (`support`), a release (`release`) and email (`email`). An agent's write on a link is checked the way a task
write is (`org_rule_check.check_as`, outside any transaction, on a budget of one call: needing more is unchecked) and
leaves exactly one outcome event on `link:<id>`: `rules.passed`, `rules.refused` (a Block rule broken) or `rules.held`
(an Escalate rule broken), with the rules in its detail. An agent's write is one whose sender is a bot: a bot's own, or
a person's Assistant or BotOps acting for them (`org_rule_check.checked_as`). A person's own write is never gated, and
a read is never counted. A caller checks first, reading only, that the write would be made (its rights, not a replay),
so no call is spent on a write that will not happen; the write's own checks still run inside it.

Nothing here stops a write. `would` in an event's detail says the outcome was not carried out: the write went anyway.
Email's own held path (backend/email_sending.py `rule_gate`) does stop a mail, so email is the one Enforced link and
its event says what happened, with the rules the mail's decision applied. The event is written in a savepoint: a
failure to log never changes the write or its answer.

What the check reads of a proxied write is scrubbed first (`proxy_output`): no header at all (so no Authorization,
Cookie or a tool's own auth header); the path, the query's names and values and a JSON, form or plain text body read
decoded (percent escapes, JSON escapes) with every proxy token (`tpx1.*`, plain or inside base64 such as a Basic pair)
taken out and the tool's auth query parameters blanked, never encoded again. A body of any other kind, or one whose
scrubbing cannot be verified, is not read. It runs before the proxy puts the key in. Events never carry the text.

Not covered: a tool whose key goes to computers, a website tool's browser sign-in, and any call a computer makes
straight out. Rules › Links lists them, unmeasured.
"""

import base64
import json
import logging
import re
from collections.abc import Callable, Collection
from typing import Any, cast
from urllib.parse import parse_qsl, unquote, urlsplit

from fastapi import FastAPI, Request

from . import org_rule_check as _ORC
from .api_contract import api_v2
from .store import H as _H
from .store import Problem

ORC: Any = _ORC
H: Any = _H
Found = dict[str, Any]  # what `check_as` found and `record` writes
LOG = logging.getLogger("tico.rule_links")
ENFORCED, INFORMED, NOT_COVERED = "enforced", "informed", "not_covered"
ENFORCED_LINKS = frozenset({"email"})  # the links whose path really stops a write; every other is Informed only
FIXED = {"email": "Email", "webhook": "Outgoing webhooks", "support": "Tico support", "release": "Releases"}
OUTCOMES = ("rules.passed", "rules.refused", "rules.held")
DAYS = 30
TOOLS_LISTED = 500
CALLS = 1  # the judge calls one link check may make
COMPUTER = "computer"  # the Not covered row for calls a computer makes straight out
TOKEN = re.compile(r"tpx1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")  # backend/tool_proxy.py TOKEN_RE
BASE64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
FORM = "application/x-www-form-urlencoded"
# What each link's write is, in the words the check reads (org_rule_check.OPERATIONS).
for _op, _words in (
    ("tool_call", "a call to an outside tool's API"),
    ("webhook", "an outgoing webhook"),
    ("support_report", "a report to Tico support"),
    ("release", "a request for a release"),
):
    ORC.OPERATIONS.setdefault(_op, _words)


# ----------------------------------------------------------------------------- what is judged
def _decoded(text: str) -> str:
    """`text` with percent escapes (up to three layers) and JSON `\\u` escapes undone."""
    for _ in range(3):
        once = unquote(text)
        if once == text:
            break
        text = once
    return ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def _hidden(match: re.Match[str]) -> str:
    """A base64 run that holds a proxy token, read from any of its four offsets in either alphabet, as `[token]`."""
    raw = match.group(0)
    for offset in range(4):
        part = raw[offset:].rstrip("=")
        part = part[: len(part) // 4 * 4]  # whole quanta only: a run's tail never makes it fail to decode
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                if b"tpx1." in decode(part).lower():
                    return "[token]"
            except ValueError:
                continue
    return raw


def scrub(text: str) -> str:
    """`text`, decoded, without a proxy token: plain, or inside a base64 run (a Basic `user:token` pair)."""
    return BASE64.sub(_hidden, TOKEN.sub("[token]", _decoded(text)))


def clean(text: str) -> bool:
    """Whether `text` holds no proxy token in any spelling `scrub` reads."""
    plain = _decoded(text)
    return "tpx1" not in plain.lower() and BASE64.sub(_hidden, plain) == plain


def _scrubbed(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        return [_scrubbed(v) for v in cast("list[Any]", value)]
    if isinstance(value, dict):
        return {scrub(str(k)): _scrubbed(v) for k, v in cast("dict[Any, Any]", value).items()}
    return value


def _body(body: bytes, content_type: str) -> str | None:
    """A JSON, form or plain text body as the check reads it, scrubbed; None for any other kind or one that fails."""
    kind = content_type.split(";")[0].strip().lower()
    try:
        text = body.decode("utf-8")
        if not kind or kind.endswith("/json") or kind.endswith("+json"):
            out = json.dumps(_scrubbed(json.loads(text)), ensure_ascii=False)
        elif kind == FORM:
            out = "&".join(f"{scrub(k)}={scrub(v)}" for k, v in parse_qsl(text, keep_blank_values=True))
        elif kind.startswith("text/"):
            out = scrub(text)
        else:
            return None
    except (ValueError, RecursionError):
        return None
    return out if clean(out) else None


def proxy_output(
    method: str, rest: str, query: str, body: bytes, params: Collection[str] = (), content_type: str = ""
) -> str:
    """What the check reads of a proxied write: its method, path and query, and its body, each decoded and scrubbed and
    never encoded again. The tool's auth query parameters (`params`) are blanked; no header is read. A body (or an
    address) that cannot be read safely is named, not read."""
    pairs = parse_qsl(query, keep_blank_values=True)
    path = "/".join(scrub(part) for part in rest.lstrip("/").split("/"))
    asked = "&".join(f"{scrub(k)}={'[key]' if k in params else scrub(v)}" for k, v in pairs)
    head = f"{method} /{path}" + ("?" + asked if asked else "")
    if not clean(head):
        head = f"{method} [an address not read]"
    if not body:
        return head
    text = _body(body, content_type)
    return head + "\n\n" + (text if text is not None else f"[a body of {len(body)} bytes, not read]")


def mcp_endpoint(tool: Any, rest: str) -> bool:
    """Whether a proxied call goes to the tool's own MCP server, the one address the MCP read exemption covers."""
    url = str(tool.get("mcp_url") or "")
    return bool(url) and rest.strip("/") == (urlsplit(url).path or "/").strip("/")


# ----------------------------------------------------------------------------- the check and its one event
def outcome(verdict: Any) -> tuple[str, list[str], list[str], str]:
    """(outcome, rules checked, rules broken, why unchecked) of an `org_rule_check.check_as` verdict. No verdict (no
    rule met the write, or checks are off) passes. A Block rule broken refuses, an Escalate rule holds, a Warn passes;
    a write too long to check is decided by the strictest rule it meets."""
    if not verdict:
        return "passed", [], [], ""
    rules: list[Any] = verdict.get("rules") or []
    checked = sorted({str(r["id"]) for r in rules})
    broken = [r for r in rules if r["id"] in (verdict.get("broken") or ())]
    unchecked = str(
        verdict.get("error") or verdict["outcome"]
        if verdict["outcome"] in ("unchecked", "too_large") or verdict.get("unchecked_rules")
        else ""
    )
    found = "passed"
    if verdict["outcome"] in ("broken", "too_large") and broken:
        strictest = max(broken, key=lambda r: ORC.STRICT[r["if_broken"]])["if_broken"]
        found = {"block": "refused", "escalate": "held"}.get(strictest, "passed")
    return found, checked, sorted(str(r["id"]) for r in broken), unchecked


def check_as(
    store: Any, primary: Any, principal: str, sender: str, via: str, link: str, operation: str, output: str
) -> Found:
    """An agent's write on `link`, checked before it is made and never inside a transaction: what `record` writes. One
    call at most and no part call (`CALLS`). A fault of the check itself is recorded as unchecked; the write goes on."""
    try:
        verdict: Any = ORC.check_as(
            store, primary, principal, sender, via, operation, output, most_calls=CALLS, parts=False
        )
    except Problem:
        verdict = {"outcome": "unchecked", "error": "check_failed", "rules": []}
    found, checked, broken, unchecked = outcome(verdict)
    return {
        "link": link,
        "actor": sender,
        "for": principal,
        "operation": operation,
        "outcome": found,
        "rules": checked,
        "broken": broken,
        "unchecked": unchecked,
    }


def check(store: Any, primary: Any, who: Any, link: str, operation: str, output: str) -> Found | None:
    """`check_as` for a write made by `who`: None when its sender is not a bot (a person's own write is never gated)."""
    found: Any = ORC.checked_as(who, store.settings.assistant_bot)
    if not found or not H.is_bot(found[1]):
        return None
    return check_as(store, primary, found[0], found[1], found[2], link, operation, output)


def would_write(store: Any, who: Any, request: Any, test: Callable[[Any], Any]) -> bool:
    """Read only, before a check: whether the write would be made now, so no call is spent on one that will not. Not
    when its Idempotency-Key already has an answer (a replay writes nothing), nor when `test(c)` (the write's own
    checks, read now) is false or refuses. The write runs its checks again inside itself."""
    try:
        with store.read() as c:
            if store.saved(c, who, request.url.path, request.headers.get("idempotency-key")):
                return False
            return bool(test(c))
    except Problem:
        return False


def record(c: Any, found: Found | None, stopped: bool = False) -> Any:
    """The one outcome event of a checked write, in the caller's transaction, inside its own savepoint: a failure is
    logged and leaves the write as it was. `stopped`: the write was not made because of it (email's held path);
    otherwise a refusal or a hold is what would have happened."""
    if not found:
        return None
    detail: dict[str, Any] = {"operation": found["operation"], "rules": found["rules"]}
    if found["broken"]:
        detail["broken"] = found["broken"]
    if found["unchecked"]:
        detail["unchecked"] = found["unchecked"]
    if found["outcome"] != "passed" and not stopped:
        detail["would"] = True
    if found["for"] != found["actor"]:
        detail["for"] = found["for"]
    c.execute("SAVEPOINT rule_link")
    try:
        row = H.event(c, found["actor"], "rules." + found["outcome"], "link:" + found["link"], detail)
    except Exception:
        c.execute("ROLLBACK TO rule_link")
        c.execute("RELEASE rule_link")
        LOG.exception("A link's rule check outcome could not be logged")
        return None
    c.execute("RELEASE rule_link")
    return row


def mailed(found: Any, result: Any) -> Found:
    """The email link's outcome from the decision the mail's own gate applied (email_sending.rule_gate), which holds a
    mail for real: `found` is (principal, sender, via) and `result` what `mail_decide` (or its structured fallback)
    answered. Its rules are the ones that decision held or warned on."""

    def ids(part: Any) -> list[str]:
        rules: list[Any] = (cast("dict[str, Any]", part).get("rules") or []) if isinstance(part, dict) else []
        return sorted(
            {str(r["id"]) for r in (cast("dict[str, Any]", r) for r in rules if isinstance(r, dict)) if r.get("id")}
        )

    applied = sorted({*ids(result.get("held")), *ids(result.get("rule_warning"))})
    return {
        "link": "email",
        "actor": found[1],
        "for": found[0],
        "operation": "mail",
        "outcome": "held" if result.get("outcome") == "held" else "passed",
        "rules": applied,
        "broken": applied,
        "unchecked": "unchecked" if result.get("outcome") == "unchecked" else "",
    }


# ----------------------------------------------------------------------------- Rules › Links
def mode(link: str) -> str:
    """Enforced only where the path really stops a write (email, in this phase); every other link is Informed only."""
    return ENFORCED if link in ENFORCED_LINKS else INFORMED


def counts(c: Any, links: list[str], since: str) -> dict[str, dict[str, int]]:
    """{link id: {passed, refused, held}} since `since`: one seek per (outcome, link) on events_action_target."""
    found = {link: {"passed": 0, "refused": 0, "held": 0} for link in links}
    targets = ["link:" + link for link in links]
    for start in range(0, len(targets), 300):
        part = targets[start : start + 300]
        rows: Any = c.execute(
            "SELECT action, target, count(*) FROM events WHERE action IN (?,?,?) "
            f"AND target IN ({','.join('?' * len(part))}) AND created_at>=? GROUP BY action, target",
            (*OUTCOMES, *part, since),
        )
        for action, target, n in rows:
            found[str(target)[5:]][str(action)[6:]] = int(n)
    return found


def listing(c: Any, who: Any, admins: Any, auth: Any, now: str | None = None) -> dict[str, Any]:
    """Every link: Enforced or Informed only with its outcomes over the last DAYS, then the Not covered paths. A tool is
    listed only to a reader who may know it exists (api_tools.visible)."""
    from . import api_tools as _api_tools

    api_tools: Any = _api_tools
    rows_read: list[Any] = c.execute("SELECT * FROM tools ORDER BY lower(name), id LIMIT ?", (TOOLS_LISTED,)).fetchall()
    tools = [t for t in rows_read if api_tools.visible(c, who, t, admins, auth)]
    links: list[tuple[str, str]] = [
        *FIXED.items(),
        *(("tool:" + str(t["id"]), str(t["name"] or t["id"])) for t in tools if _proxied(t)),
    ]
    seen = counts(c, [link for link, _ in links], str(H.shift(now or H.now(), days=-DAYS)))
    rows: list[dict[str, Any]] = [
        {"id": link, "name": name, "mode": mode(link), "counts": seen[link]} for link, name in links
    ]
    rows += [
        {"id": "tool:" + str(t["id"]), "name": str(t["name"] or t["id"]), "mode": NOT_COVERED, "counts": None}
        for t in tools
        if not _proxied(t)
    ]
    rows.append({"id": COMPUTER, "name": "Calls straight from computers", "mode": NOT_COVERED, "counts": None})
    return {"links": rows, "days": DAYS}


def _proxied(tool: Any) -> bool:
    return tool["kind"] == "api" and tool["delivery"] == "proxy"


def install(app: FastAPI, store: Any, auth: Any) -> None:
    @app.get(
        "/api/v2/rule-links",
        **api_v2(
            "Rules",
            "listRuleLinks",
            "Rules › Links (owner, Admins): each way out, Enforced or Informed only with 30 days of checked writes, or "
            "Not covered",
        ),
    )
    def rule_links(request: Request) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Rules › Links: every way out, its mode and its last 30 days of checked writes. The owner and Admins, in
        person: never a support session."""
        who: Any = request.state.identity
        auth.domain(who)
        if who.support or who.role not in ("owner", "human") or not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin reads Rules › Links", 403)
        with store.read() as c:
            return listing(c, who, request.app.state.vault.admins, auth)
