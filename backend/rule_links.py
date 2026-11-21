# pyright: strict
"""Rules › Links (docs/dev/rules-plan.md): the ways an agent's writes leave the company, and the org rule check on each,
log-only for now.

A link is one way out: a proxied tool (`tool:<id>`, backend/tool_proxy.py), outgoing webhooks (`webhook`), a release
(`release`), a report to Tico support (`support`, checked at its first delivery to HQ: backend/support.py) and email
(`email`). An agent's write on a link is checked the way a task
write is (`org_rule_check.check_as`, outside any transaction, on a budget of one call: needing more is unchecked) and
leaves exactly one outcome event on `link:<id>`: `rules.passed`, `rules.refused` (a Block rule broken), `rules.held`
(an Escalate rule broken) or `rules.unchecked` (not judged, or the judge out: the reason in its detail), with the rules
in its detail. An agent's write is one whose sender is a bot: a bot's own, or
a person's Assistant or BotOps acting for them (`org_rule_check.checked_as`). A person's own write is never gated, and
a read is never counted. A caller checks first, reading only, that the write would be made (its rights, not a replay),
so no call is spent on a write that will not happen; the write's own checks still run inside it.

Nothing here stops a write. `would` in an event's detail says the outcome was not carried out: the write went anyway.
Email's own held path (backend/email_sending.py `rule_gate`) does stop a mail, so email is the one Enforced link and
its event says what happened, with the rules the mail's decision applied. The event is written in a savepoint: a
failure to log never changes the write or its answer; the proxy writes it with the call's own row, after forwarding.

A link's mode is what its path does (`mode`): Enforced for email, which holds mail, Informed only for the rest. No
setting changes it until a link can switch.

What the check reads is names only, never a value (`proxy_output`): for a proxied write the tool's name, the HTTP
method, an MCP call's method and tool name, and a JSON object body's top-level key names (short plain names: a key that
could be a credential is left out); for a webhook send its event and endpoint names; for Release now nothing more. No
path, query, header or value is read, so no credential can reach the judge. It runs before the proxy puts the key in.
Events never carry the text. A proxied write is judged with or without an Idempotency-Key; with one, a retry under it
within KEY_DAYS logs no second event.

Not covered: a tool whose key goes to computers, a website tool's browser sign-in, and any call a computer makes
straight out. Rules › Links lists them, unmeasured.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

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
FIXED = {"email": "Email", "webhook": "Outgoing webhooks", "release": "Releases", "support": "Tico support"}
UNCOVERED = {"computer": "Calls straight from computers"}  # unmeasured paths, besides tools
CALLS_READ = 50  # the MCP calls of one batch the check reads (each one line), however many the batch holds
MAX_JUDGED = 1024 * 1024  # a proxied body over this is not parsed for its key names (the check reads the rest)
OUTCOMES = ("rules.passed", "rules.refused", "rules.held", "rules.unchecked")
KEY_DAYS = 1  # how long a write's Idempotency-Key keeps a retry under it from logging a second event
DAYS = 30
TOOLS_LISTED = 500
CALLS = 1  # the judge calls one link check may make
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,39}")  # a key or MCP tool name the check may read: short and plain
METHOD = re.compile(r"[a-z][a-z/_]{0,39}")  # an MCP method the check may read
# What each link's write is, in the words the check reads (org_rule_check.OPERATIONS).
for _op, _words in (
    ("tool_call", "a call to an outside tool's API"),
    ("webhook", "an outgoing webhook"),
    ("release", "a request for a release"),
    ("support_report", "a report to Tico support"),
):
    ORC.OPERATIONS.setdefault(_op, _words)


# ----------------------------------------------------------------------------- what is judged
def _name(value: Any, pattern: re.Pattern[str] = NAME) -> str | None:
    """`value` when it is a short plain name the check may read, never anything shaped like a proxy token."""
    text = value if isinstance(value, str) else ""
    return text if pattern.fullmatch(text) and not text.lower().startswith("tpx1") else None


def proxy_output(
    tool_name: str, method: str, body: bytes | None, calls: Any = None, writes: Callable[[Any], bool] | None = None
) -> str:
    """What the check reads of a proxied write, names only: the tool's name and the HTTP method; for MCP calls (`calls`,
    tool_proxy.rpc_calls) each call's method and tool name; for a JSON object body its top-level key names. Never a
    value, the path, the query or a header. `body` is None when it is not read (over MAX_JUDGED). `writes(call)`: whether
    a call may write; those are read first when a batch holds more than CALLS_READ."""
    lines = [f"{method} to {tool_name}"]
    every: list[tuple[Any, Any]] = calls or []
    if writes and len(every) > CALLS_READ:
        # The calls that may write (`writes`) first, in their order, then the rest: a long run of reads never pushes a
        # write out of the calls the check reads.
        every = [call for call in every if writes(call)] + [call for call in every if not writes(call)]
    listed = every[:CALLS_READ]  # a batch of any size is classified in full; the check reads its first CALLS_READ
    if len(every) > CALLS_READ:
        lines.append(f"(the first {CALLS_READ} of {len(every)} MCP calls)")
    for called, params in listed:
        named = _name(called, METHOD)
        if named:
            tool = _name(cast("dict[str, Any]", params).get("name")) if called == "tools/call" else None
            lines.append(f"MCP {named}" + (f" {tool}" if tool else ""))
    if body and not calls:
        try:
            data: Any = json.loads(body)
        except (ValueError, RecursionError):
            data = None
        if isinstance(data, dict):
            keys = sorted({k for k in (_name(k) for k in cast("dict[Any, Any]", data)) if k})[:50]
            if keys:
                lines.append("Fields: " + ", ".join(keys))
    return "\n".join(lines)


def mcp_endpoint(tool: Any, forwarded: str) -> bool:
    """Whether the final address a proxied call is forwarded to (its query, the key's own parameters included) is exactly
    the tool's MCP server, trailing slash included: the one address the gate's MCP read exemption and the read-only
    hints it trusts cover. A tool whose key goes in the query never matches: its writes are all checked."""
    url = str(tool.get("mcp_url") or "")
    return bool(url) and forwarded == url


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
            store, primary, principal, sender, via, operation, output, most_calls=CALLS, parts=False, first_only=True
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


def agent_of(store: Any, who: Any) -> tuple[str, str, str] | None:
    """(principal, sender, via) of a write made by `who` when it is an agent's (its sender a bot), else None. `who` may
    be any object with an `actor` (Maintenance's BotOps is one)."""
    seen = SimpleNamespace(
        actor=str(who.actor), task_actor=str(getattr(who, "task_actor", "") or ""), via=getattr(who, "via", "") or ""
    )
    found: Any = ORC.checked_as(seen, store.settings.assistant_bot)
    if not found or not H.is_bot(found[1]):
        return None
    return str(found[0]), str(found[1]), str(found[2])


def check(store: Any, primary: Any, who: Any, link: str, operation: str, output: str) -> Found | None:
    """`check_as` for a write made by `who`: None when its sender is not a bot (a person's own write is never gated)."""
    found = agent_of(store, who)
    if not found:
        return None
    return check_as(store, primary, found[0], found[1], found[2], link, operation, output)


def would_write(store: Any, who: Any, request: Any, test: Callable[[Any], Any]) -> bool:
    """Read only, before a check: whether the write would be made now, so no call is spent on one that will not. Not
    when its Idempotency-Key is missing or too long, or already has an answer (a replay writes nothing), nor when `test(c)` (the write's own
    checks, read now) is false or refuses. The write runs its checks again inside itself."""
    key = request.headers.get("idempotency-key") or ""
    if not key or len(key) > 200:
        return False  # the write refuses it (Store.mutate)
    try:
        with store.read() as c:
            if store.saved(c, who, request.url.path, key):
                return False
            return bool(test(c))
    except Problem:
        return False


def is_agent(store: Any, who: Any) -> bool:
    """Whether a write by `who` is an agent's, which `check` checks: its sender is a bot."""
    found: Any = ORC.checked_as(who, store.settings.assistant_bot)
    return bool(found) and bool(H.is_bot(found[1]))


def key_digest(key: str) -> str:
    """What an event keeps of a write's Idempotency-Key."""
    return hashlib.sha256(str(key).encode()).hexdigest()[:32]


def logged(c: Any, actor: str, link: str, key: str) -> bool:
    """Whether `actor`'s write on `link` under Idempotency-Key `key` already has its outcome event (within KEY_DAYS): a
    retry, which is neither judged nor logged again. One seek on events_action_target."""
    return bool(
        c.execute(
            "SELECT 1 FROM events WHERE action IN (?,?,?,?) AND target=? AND created_at>=? AND actor=? "
            "AND json_extract(detail, '$.key')=? LIMIT 1",
            (*OUTCOMES, "link:" + link, H.shift(H.now(), days=-KEY_DAYS), actor, key_digest(key)),
        ).fetchone()
    )


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
    target = "link:" + found["link"]
    if found.get("key"):
        # The caller's Idempotency-Key, as a digest: a retry under it within KEY_DAYS logs no second event (checked
        # again here, for two tries racing past `logged`).
        detail["key"] = key_digest(found["key"])
        if logged(c, found["actor"], found["link"], found["key"]):
            return None
    # Not judged (or the judge out) and nothing found: its own outcome, counted apart from a write that passed.
    action = "rules.unchecked" if found["unchecked"] and found["outcome"] == "passed" else "rules." + found["outcome"]
    c.execute("SAVEPOINT rule_link")
    try:
        row = H.event(c, found["actor"], action, target, detail)
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
    answered, with `rules`, the rule ids that decision used (a hold's, a warning's, a consumed approval's)."""
    used: list[Any] = result.get("rules") or []
    applied = sorted({str(r) for r in used if r})
    return {
        "link": "email",
        "actor": found[1],
        "for": found[0],
        "operation": "mail",
        "outcome": "held" if result.get("outcome") == "held" else "passed",
        "rules": applied,
        "broken": applied,
        # Kept when the decision went on unchecked, an approved mail's over-budget recheck included.
        "unchecked": str(result.get("unchecked") or ("unchecked" if result.get("outcome") == "unchecked" else "")),
    }


def unchecked(principal: str, sender: str, link: str, operation: str, reason: str) -> Found:
    """The outcome of an agent's write that is not judged (eligible only after its check): nothing found, recorded as
    `rules.unchecked` with `reason`."""
    return {
        "link": link,
        "actor": sender,
        "for": principal,
        "operation": operation,
        "outcome": "passed",
        "rules": [],
        "broken": [],
        "unchecked": reason,
    }


def unchecked_for(store: Any, who: Any, link: str, operation: str, reason: str) -> Found | None:
    """`unchecked` for a write made by `who`: None when it is not an agent's (`check`)."""
    found: Any = ORC.checked_as(who, store.settings.assistant_bot)
    if not found or not H.is_bot(found[1]):
        return None
    return unchecked(found[0], found[1], link, operation, reason)


# ----------------------------------------------------------------------------- Rules › Links
def mode(link: str) -> str:
    """A link's mode, what its path does: Enforced where it stops a write (ENFORCED_LINKS, email), else Informed only."""
    return ENFORCED if link in ENFORCED_LINKS else INFORMED


def counts(c: Any, links: dict[str, str]) -> dict[str, dict[str, int]]:
    """{link id: {passed, refused, held, unchecked}}, each since its own time (`links`: {link id: since}), in one query:
    the (target, since) pairs as a json_each list, each a seek on events_action_target (action, target, created_at)."""
    found = {link: {"passed": 0, "refused": 0, "held": 0, "unchecked": 0} for link in links}
    rows: Any = c.execute(
        "WITH want(target, since) AS (SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]') FROM json_each(?)) "
        "SELECT e.action, e.target, count(*) FROM want w JOIN events e ON e.action IN (?,?,?,?) AND e.target=w.target "
        "AND e.created_at>=w.since GROUP BY e.action, e.target",
        (json.dumps([["link:" + link, since] for link, since in links.items()]), *OUTCOMES),
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
    since = str(H.shift(now or H.now(), days=-DAYS))
    # A tool's counts start when this tool was made: events of an earlier tool under the same id are not its own.
    made = {"tool:" + str(t["id"]): str(t["created_at"] or "") for t in tools}
    seen = counts(c, {link: max(since, made.get(link, "")) for link, _ in links})
    rows: list[dict[str, Any]] = [
        {"id": link, "name": name, "mode": mode(link), "counts": seen[link]} for link, name in links
    ]
    rows += [
        {"id": "tool:" + str(t["id"]), "name": str(t["name"] or t["id"]), "mode": NOT_COVERED, "counts": None}
        for t in tools
        if not _proxied(t)
    ]
    rows += [{"id": link, "name": name, "mode": NOT_COVERED, "counts": None} for link, name in UNCOVERED.items()]
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
