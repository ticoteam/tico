# pyright: strict
"""Rules › Links (docs/dev/rules-plan.md): the ways an agent's writes leave the company, and the org rule check on each,
log-only for now.

A link is one way out: a proxied tool (`tool:<id>`, backend/tool_proxy.py), outgoing webhooks (`webhook`), a report to
Tico support (`support`), a release (`release`) and email (`email`). An agent's write on a link is checked the way a task
write is (`org_rule_check.check_as`: one call at most, outside any transaction) and leaves exactly one outcome event on
`link:<id>`: `rules.passed`, `rules.refused` (a Block rule broken) or `rules.held` (an Escalate rule broken), with the
rules it was checked against in its detail. An agent's write is one whose sender is a bot: a bot's own, or a person's
Assistant or BotOps acting for them (`org_rule_check.checked_as`). A person's own write is never gated, and a read is
never counted.

Nothing here stops a write. `would` in an event's detail says the outcome was not carried out: the write went anyway.
Email's own held path (backend/email_sending.py `rule_gate`) does stop a mail, so an email event says what happened.

Each link's mode, Enforced or Informed only, lives in registry_metadata (`MODES`). A link is Informed only until it is
named Enforced there, except email, whose held path is older than the links (`BUILT_IN`).

What the check reads of a proxied write is scrubbed first (`proxy_output`): no header at all (so no Authorization,
Cookie or a tool's own auth header), the tool's auth query parameters blanked, and every proxy token (`tpx1.*`, plain
or inside base64 such as a Basic pair) taken out. It runs before the proxy puts the key in. Events never carry the text.

Not covered: a tool whose key goes to computers, a website tool's browser sign-in, and any call a computer makes
straight out. Rules › Links lists them, unmeasured.
"""

import base64
import json
import re
from collections.abc import Collection
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Request

from . import org_rule_check as _ORC
from .api_contract import api_v2
from .store import H as _H
from .store import Problem

ORC: Any = _ORC
H: Any = _H
Found = dict[str, Any]  # what `check_as` found and `record` writes

MODES = "rule-link-modes"  # registry_metadata: {link id: "enforced"}; a link it does not name is Informed only
ENFORCED, INFORMED, NOT_COVERED = "enforced", "informed", "not_covered"
BUILT_IN = {"email": ENFORCED}  # mail is held on a broken Block or Escalate rule already (email_sending.rule_gate)
FIXED = {"email": "Email", "webhook": "Outgoing webhooks", "support": "Tico support", "release": "Releases"}
OUTCOMES = ("rules.passed", "rules.refused", "rules.held")
DAYS = 30
TOOLS_LISTED = 500
COMPUTER = "computer"  # the Not covered row for calls a computer makes straight out
TOKEN = re.compile(r"tpx1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")  # backend/tool_proxy.py TOKEN_RE
BASE64 = re.compile(r"[A-Za-z0-9+/_-]{20,}={0,2}")
# What each link's write is, in the words the check reads (org_rule_check.OPERATIONS).
for _op, _words in (
    ("tool_call", "a call to an outside tool's API"),
    ("webhook", "an outgoing webhook"),
    ("support_report", "a report to Tico support"),
    ("release", "a request for a release"),
):
    ORC.OPERATIONS.setdefault(_op, _words)


# ----------------------------------------------------------------------------- what is judged
def scrub(text: str) -> str:
    """`text` without a proxy token: plain, or inside a base64 run (a Basic `user:token` pair, either alphabet)."""

    def hidden(match: re.Match[str]) -> str:
        raw = match.group(0)
        padded = raw + "=" * (-len(raw) % 4)
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                if b"tpx1." in decode(padded):
                    return "[token]"
            except ValueError:
                continue
        return raw

    return BASE64.sub(hidden, TOKEN.sub("[token]", text))


def proxy_output(method: str, rest: str, query: str, body: bytes, params: Collection[str] = ()) -> str:
    """What the check reads of a proxied write: its method, path and query, and its body, scrubbed. The tool's auth
    query parameters (`params`) are blanked; no header is read."""
    pairs = [(k, "[key]" if k in params else v) for k, v in parse_qsl(query, keep_blank_values=True)]
    head = f"{method} /{rest.lstrip('/')}" + ("?" + urlencode(pairs) if pairs else "")
    text = body.decode("utf-8", "replace") if body else ""
    return scrub(head + ("\n\n" + text if text else ""))


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
    """An agent's write on `link`, checked before it is made and never inside a transaction: what `record` writes. A
    fault of the check itself is recorded as unchecked; the write goes on."""
    try:
        verdict: Any = ORC.check_as(store, primary, principal, sender, via, operation, output)
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


def record(c: Any, found: Found | None, stopped: bool = False) -> Any:
    """The one outcome event of a checked write, in the caller's transaction. `stopped`: the write was not made because
    of it (email's held path); otherwise a refusal or a hold is what would have happened."""
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
    return H.event(c, found["actor"], "rules." + found["outcome"], "link:" + found["link"], detail)


def mailed(found: Any, prep: Any, result: Any) -> Found:
    """The email link's outcome from the mail's own gate (email_sending.rule_gate), which holds a mail for real: `found`
    is (principal, sender, via), `prep` the judged check's preparation (None when it could not be prepared) and
    `result` what `mail_decide` (or its structured fallback) answered."""
    verdict: dict[str, Any] = (prep.get("verdict") if prep else None) or {}
    rules: list[Any] = verdict.get("rules") or []
    broken: list[Any] = verdict.get("broken") or []
    return {
        "link": "email",
        "actor": found[1],
        "for": found[0],
        "operation": "mail",
        "outcome": "held" if result.get("outcome") == "held" else "passed",
        "rules": sorted({str(r["id"]) for r in rules if r.get("id")}),
        "broken": sorted({str(b) for b in broken if b}),
        "unchecked": "unchecked" if result.get("outcome") == "unchecked" else "",
    }


# ----------------------------------------------------------------------------- Rules › Links
def modes(c: Any) -> dict[str, Any]:
    """{link id: mode} as registry_metadata names them."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (MODES,)).fetchone()
    try:
        saved: Any = json.loads(row[0]) if row else {}
    except ValueError:
        saved = {}
    return cast("dict[str, Any]", saved) if isinstance(saved, dict) else {}


def mode(c: Any, link: str, saved: dict[str, Any] | None = None) -> str:
    """Enforced or Informed only: what registry_metadata names, else the link's built-in mode, else Informed only."""
    saved = modes(c) if saved is None else saved
    return ENFORCED if saved.get(link, BUILT_IN.get(link)) == ENFORCED else INFORMED


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


def listing(c: Any, now: str | None = None) -> dict[str, Any]:
    """Every link: Enforced or Informed only with its outcomes over the last DAYS, then the Not covered paths."""
    tools: list[Any] = c.execute(
        "SELECT id, name, kind, delivery FROM tools ORDER BY lower(name), id LIMIT ?", (TOOLS_LISTED,)
    ).fetchall()
    links: list[tuple[str, str]] = [
        *FIXED.items(),
        *(("tool:" + str(t["id"]), str(t["name"] or t["id"])) for t in tools if _proxied(t)),
    ]
    saved = modes(c)
    seen = counts(c, [link for link, _ in links], str(H.shift(now or H.now(), days=-DAYS)))
    rows: list[dict[str, Any]] = [
        {"id": link, "name": name, "mode": mode(c, link, saved), "counts": seen[link]} for link, name in links
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
        """Rules › Links (owner and Admins): every way out, its mode and its last 30 days of checked writes."""
        who: Any = request.state.identity
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin reads Rules › Links", 403)
        with store.read() as c:
            return listing(c)
