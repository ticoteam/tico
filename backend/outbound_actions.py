# pyright: strict
"""The outbound gate: one queue for every send and outside write (docs/outbound-actions.md).

A row is one action on one channel: where it goes, its frozen `payload` (wiped once the row ends) and the caller's
`metadata` (kept, never read by the server). `enqueue` is the one way in, idempotent on `occurrence_key`. A channel
sends only through its registered adapter; none ships yet (Jira is first), so tests register a fake one.

- **Policy.** `decide` runs at enqueue and again before every attempt: a floor that only tightens (the company's Never
  for the channel and audience, a Contacts rule that blocks or escalates), then the default for the channel and audience
  (the company's setting, else On its own inside and Ask first outside). Ask first waits on one OK bound to the frozen
  content (backend/task_asks.py, subject `outbound:<id>`).
- **Lease.** The worker claims a due row through backend/outbox.py (`queued` to `attempting`, with a claim token).
- **Fence.** Before any provider call, ONE durable write under that token, and only while `provider_attempted_at` is
  still empty, stamps it and uses the OK. A worker whose claim lapsed, or a replay of the same claim, calls nothing.
- **Stale claims.** A row still `attempting` past STALE goes back to `queued` only when it was never attempted; one
  that was is `unknown` (first, in the same transaction), never sent again on its own.
- **Answers.** An adapter returns a receipt (done) or a `SafeRejection` (requeued with the marker cleared and
  `next_attempt_at` from Retry-After), raises `Refused` (failed), or raises anything else (unknown). An unknown row is
  reconciled by its adapter where the provider can be asked; otherwise it is logged and waits for a person.
- **Wake.** A commit that queues a row rings the worker, which otherwise sleeps until the next row is due (at most
  SWEEP, the backstop that also recovers and reconciles).

Rows are never pruned: nothing here deletes a row.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Callable, NamedTuple, cast

from fastapi import Request
from pydantic import Field

from . import background
from . import outbox as _O
from . import task_asks
from .api_contract import api_v2
from .models import Contract, Empty
from .status_values import OUTBOUND_ACTION_STATES as STATES
from .store import H as _H
from .store import Problem

H: Any = _H  # hubdb and outbox are not typed: Any at the door
O: Any = _O
LOG = logging.getLogger(__name__)

TABLE = O.Table(
    "outbound_actions",
    due="queued",
    flight="attempting",
    due_at="next_attempt_at",
    lease="claimed_at",
    attempts="attempts",
    error="last_error",
    token="claim_token",
)
STALE = 120  # seconds an `attempting` row may hold its claim before recovery looks at it
SWEEP = 60.0  # the longest the worker sleeps: recovery and reconciling run at most this often
MAX_SAFE_RETRIES = 10  # safe rejections (rate limits) before a row is failed
SUBJECT = "outbound:"
ENDED = ("done", "failed", "canceled")  # terminal: the payload is wiped
OPEN = ("waiting_approval", "queued", "blocked")  # a person may cancel these; an unknown one may have gone
AUDIENCES = ("internal", "external")
# The policy's choices, spelled as the email policy spells them (backend/email_sending.py MODES): strictest last.
CHOICES = ("on_their_own", "ask_first", "never")
DEFAULTS = {"internal": "on_their_own", "external": "ask_first"}
POLICY_KEY = "outbound_policy"
LIST_BYTES = 128 * 1024  # a list page stops before this many bytes of rows
LIST_FIELDS = (
    "id",
    "connection_id",
    "channel",
    "audience",
    "operation",
    "destination",
    "occurrence_key",
    "actor",
    "state",
    "attempts",
    "send_after",
    "next_attempt_at",
    "provider_attempted_at",
    "approval_id",
    "task_id",
    "last_error",
    "created_at",
    "updated_at",
)


# ----------------------------------------------------------------------------------------------- adapter interface
class SafeRejection(NamedTuple):
    """The provider refused without acting (a rate limit): tried again after `retry_after` seconds."""

    retry_after: float
    error: str = "rate limited"


class Settled(NamedTuple):
    """What an adapter's reconcile found in the provider's own record: `state` done or failed (anything else leaves
    the row unknown, for a person)."""

    state: str
    receipt: dict[str, Any] | None = None
    error: str = ""


class Refused(Exception):
    """The provider certainly did not act and will not on a retry: the row is failed."""


@dataclass(frozen=True)
class Adapter:
    """One channel's sender, each called outside any database transaction except `prepare`:

    - `prepare(c, settings, actor, destination, payload, *, operation, connection_id, task_id)` checks an action (at
      enqueue, and again at each attempt) and answers (audience, destination); an audience it cannot tell is external.
      A Problem refuses (blocks) it.
    - `dispatch(store, row)` makes the one provider call: a receipt dict or a SafeRejection, or it raises.
    - `reconcile(store, row)`, where the provider can be asked: a Settled, or None when it cannot tell."""

    channel: str
    prepare: Callable[..., tuple[str, str]]
    dispatch: Callable[..., Any]
    reconcile: Callable[..., "Settled | None"] | None = None


ADAPTERS: dict[str, Adapter] = {}  # no channel ships yet: the first is Jira


def register(adapter: Adapter) -> Adapter:
    ADAPTERS[adapter.channel] = adapter
    return adapter


def _mod(name: str) -> Any:
    """An untyped neighbour module, imported when used and typed as Any at the door. Every name is a literal import, so
    the PR test selection (scripts/pr_checks.py) sees exactly these."""
    if name == "lifecycle":
        from . import lifecycle as found
    elif name == "org_rule_check":
        from . import org_rule_check as found
    elif name == "ask_ingest":
        from . import ask_ingest as found
    elif name == "mail_settings":
        from . import mail_settings as found
    elif name == "api_tools":
        from . import api_tools as found
    else:
        raise KeyError(name)
    return found


# --------------------------------------------------------------------------------------------------------- policy
def company_policy(c: Any) -> dict[str, str]:
    """The company's settings, {"<channel>:<audience>": choice}; what is not set takes DEFAULTS."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (POLICY_KEY,)).fetchone()
    stored: Any = json.loads(row[0]) if row and row[0] else {}
    if not isinstance(stored, dict):
        return {}
    settings = cast(dict[str, Any], stored)
    return {k: str(v) for k, v in settings.items() if v in CHOICES}


def bot_stopped(c: Any, actor: str) -> str:
    """Why a bot may send nothing now ("" when it may): still onboarding, or paused or archived."""
    if not H.is_bot(actor):
        return ""
    slug = H.actor_id(actor)
    if _mod("lifecycle").is_onboarding(c, slug):
        return "The bot is still onboarding: it sends nothing outside yet"
    found: Any = H.bot(c, slug) or {}
    if found.get("state") != "active":
        return "The bot is not active: it sends nothing while paused or archived"
    return ""


def decide(c: Any, *, actor: str, channel: str, audience: str, destination: str) -> dict[str, Any]:
    """The policy for one action now: {"choice", "why", "floor"}. The floor only tightens; the default is the company's
    setting for the channel and audience, else DEFAULTS. Enqueue and every attempt read it the same way."""
    company = company_policy(c).get(f"{channel}:{audience}")
    if company == "never":
        return {"choice": "never", "why": f"The company never sends {channel} to {audience} audiences", "floor": True}
    default = company or DEFAULTS[audience]
    from .entities import rules as DR

    address = str(destination or "").strip().lower()
    contacts = [address] if "@" in address or address.startswith("+") else []
    # Only a channel the Contacts rules name (DR.CHANNEL_OF): any other would read as chat.
    verdict = (
        _mod("org_rule_check").structured_check(c, actor or H.TICO_ACTOR, channel, contacts=contacts)
        if channel in DR.CHANNEL_OF
        else None
    )
    if verdict and verdict["outcome"] == "held":
        floor = "never" if any(rule["effect"] == "block" for rule in verdict["held"]) else "ask_first"
        if CHOICES.index(floor) > CHOICES.index(default):
            why = "; ".join(str(rule["text"]) for rule in verdict["held"])[:300]
            return {"choice": floor, "why": why, "floor": True}
    source = "the company's setting" if company else "the default"
    return {"choice": default, "why": f"{source} for {channel} to {audience} audiences", "floor": False}


# ---------------------------------------------------------------------------------------------------------- rows
def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def get(c: Any, action_id: str) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM outbound_actions WHERE id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def content(row: Any) -> dict[str, Any]:
    """What an OK of the row is bound to: the frozen action, exactly."""
    return {
        "channel": row["channel"],
        "operation": row["operation"],
        "destination": row["destination"],
        "payload": _load(row["payload"], {}),
    }


def approval_tasks(c: Any, approval_ids: Any) -> dict[str, str]:
    """{requirement id: the task its OK is on}, in one read (task_asks.task_of for many)."""
    ids = [i for i in dict.fromkeys(approval_ids) if i]
    if not ids:
        return {}
    found = c.execute(
        f"SELECT r.id, v.task_id FROM requirements r JOIN visits v ON v.id = r.visit_id "
        f"WHERE r.id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in found}


def public(c: Any, row: Any, *, full: bool = False, tasks: dict[str, str] | None = None) -> dict[str, Any]:
    """The row as the API shows it. `full` (one action's detail) adds the metadata, receipt and payload."""
    out = {k: row[k] for k in LIST_FIELDS}
    out["policy_decision"] = _load(row["policy_decision"], {})
    if tasks is None:
        tasks = approval_tasks(c, [row["approval_id"]])
    out["approval_task_id"] = tasks.get(row["approval_id"])
    if full:
        out["metadata"] = _load(row["metadata"], {})
        out["receipt"] = _load(row["receipt"], {})
        out["payload"] = _load(row["payload"], None)
    return out


def _people_for(c: Any, actor: str) -> list[str]:
    """Who may be asked to OK an action, in order: a bot's manager, then the company's default person; only people
    who are here."""
    from . import task_requirements as R

    found: list[str | None] = []
    if H.is_bot(actor):
        found.append(_mod("ask_ingest").manager_of(c, actor)[0])
    default = H.default_human(c)
    found.append(H.human_actor(default) if default else None)
    return [p for p in dict.fromkeys(found) if p and H.is_human(p) and R.active_person(c, p)]


def _asker(row: Any) -> str:
    return row["actor"] if H.is_bot(row["actor"]) else H.TICO_ACTOR


NOBODY_TO_ASK = "Nobody here may be asked to OK it: give the bot a manager, or set the company's default person"


def request_approval(c: Any, row: Any, *, renew: bool = False) -> bool:
    """Ask a person for an OK of this exact action, on a task of its own: the first of `_people_for` the asker may
    ask (an ask that is refused is undone and the next person tried). The same content asked again is the same ask,
    unless `renew`: the old OK no longer lets it go (whoever gave it left), so a new one is asked. False when nobody
    may be asked."""
    for person in _people_for(c, row["actor"]):
        c.execute("SAVEPOINT outbound_ask")
        try:
            asked = task_asks.ask_ok(
                c,
                _asker(row),
                person,
                f"Send this {row['channel']} {row['operation']}?",
                shows=[SUBJECT + row["id"]],
                subject=SUBJECT + row["id"],
                content=content(row),
                matter=row["task_id"],
                renew=renew,
            )
        except (H.Refused, Problem):
            c.execute("ROLLBACK TO outbound_ask")
            c.execute("RELEASE outbound_ask")
            continue
        c.execute("RELEASE outbound_ask")
        old = row["approval_id"]
        if old and old != asked["requirement_id"] and (task_asks.grant(c, old) or {}).get("used_at"):
            task_asks.not_done(c, old, "Asked again")  # ask_ok took back an unused one; a used one's task ends here
        c.execute("UPDATE outbound_actions SET approval_id=? WHERE id=?", (asked["requirement_id"], row["id"]))
        return True
    return False


def ring() -> None:
    BELL.ring()


def _after_commit(c: Any, fn: Callable[[], object]) -> None:
    hooks = getattr(c, "after_commit", None)
    if hooks is None:
        fn()
    else:
        hooks.append(fn)


def _when(value: Any) -> str | None:
    """An ISO time in the stored spelling (H.now's, which sorts), None for none; a Problem when it is not a time."""
    if not value:
        return None
    at = H.parse_ts(value)
    if at is None:
        raise Problem("validation", "send_after is an ISO time, e.g. 2026-11-22T09:00:00Z", 422)
    at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def enqueue(
    c: Any,
    settings: Any,
    *,
    actor: str,
    channel: str,
    operation: str,
    destination: str,
    occurrence_key: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    connection_id: str = "",
    task_id: str | None = None,
    send_after: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Queue one action in the caller's transaction: (the row, whether it is new). The same occurrence key answers the
    row it already made, whatever else is sent. The adapter checks the action and resolves its audience (one it cannot
    tell is external); the policy decides the first state: queued, waiting_approval (an OK is asked) or blocked."""
    key = str(occurrence_key or "").strip()
    if not key:
        raise Problem("validation", "Say the action's occurrence_key: the same key never sends twice", 422)
    for name, value, most in (
        ("occurrence_key", key, 300),
        ("channel", channel, 40),
        ("operation", operation, 80),
        ("destination", destination, 500),
        ("connection_id", connection_id, 200),
        ("task_id", task_id, 200),
    ):
        if len(str(value or "")) > most:
            raise Problem("validation", f"{name} is at most {most} characters", 422)
    found = c.execute("SELECT * FROM outbound_actions WHERE occurrence_key=?", (key,)).fetchone()
    if found:
        return dict(found), False
    adapter = ADAPTERS.get(channel)
    if adapter is None:
        known = ", ".join(sorted(ADAPTERS)) or "none yet"
        raise Problem("validation", f"Tico has no sender for {channel!r} (it sends: {known})", 422)
    after = _when(send_after)
    stopped = bot_stopped(c, actor)
    if stopped:
        raise Problem("onboarding" if "onboarding" in stopped else "bot_inactive", stopped, 409)
    audience, destination = adapter.prepare(
        c,
        settings,
        actor,
        str(destination or "").strip(),
        payload,
        operation=operation,
        connection_id=connection_id,
        task_id=task_id,
    )
    if len(str(destination or "")) > 500:
        raise Problem("validation", "destination is at most 500 characters", 422)
    audience = audience if audience in AUDIENCES else "external"
    policy = decide(c, actor=actor, channel=channel, audience=audience, destination=destination)
    state = {"on_their_own": "queued", "ask_first": "waiting_approval", "never": "blocked"}[policy["choice"]]
    now = H.now()
    row = {
        "id": H.new_id(),
        "connection_id": str(connection_id or ""),
        "channel": channel,
        "audience": audience,
        "operation": str(operation or "").strip() or "send",
        "destination": destination,
        "occurrence_key": key,
        "actor": actor,
        "payload": _dump(payload),
        "metadata": _dump(metadata or {}),
        "policy_decision": _dump(policy),
        "state": state,
        "send_after": after or now,
        "next_attempt_at": max(now, after or now),
        "task_id": task_id,
        "last_error": policy["why"] if state == "blocked" else "",
        "created_at": now,
        "updated_at": now,
    }
    c.execute(f"INSERT INTO outbound_actions({','.join(row)}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
    made = get(c, str(row["id"]))
    assert made is not None
    if state == "waiting_approval":
        if not request_approval(c, made):
            settle(c, made, "blocked", error=NOBODY_TO_ASK)
        made = get(c, made["id"]) or made
    elif state == "queued":
        _after_commit(c, ring)
    H.event(c, actor or H.TICO_ACTOR, "outbound.queued", made["id"], {"channel": channel, "state": made["state"]})
    return made, True


# --------------------------------------------------------------------------------------------------- approvals
def ok_given(c: Any, grant: Any, ctx: Any = None) -> None:
    """In the transaction of an OK of an action (task_asks.ON_OK): the row it waits on is queued, and the worker rung
    once the OK commits. The OK itself is used in the attempt's fenced write."""
    row = get(c, str(grant["subject"]).removeprefix(SUBJECT))
    if not row or row["state"] != "waiting_approval":
        raise Problem("state", "This action is no longer waiting for an OK", 409)
    now = H.now()
    c.execute(
        "UPDATE outbound_actions SET state='queued',next_attempt_at=?,updated_at=? WHERE id=? AND state='waiting_approval'",
        (max(now, row["send_after"]), now, row["id"]),
    )
    _after_commit(c, ring)


def may_ok(c: Any, person: str, grant: Any) -> str | None:
    """Who may OK an action now (task_asks.MAY_OK, checked when the OK is given and again before it is used): the
    person it was asked of, while they are still one of those it would be asked of now (`_people_for`)."""
    if str(grant.get("asked_of_id") or "") != person:
        return "Only the person it was asked of may OK this"
    if person not in _people_for(c, str(grant.get("asked_by_id") or "")):
        return "They are no longer the bot's manager or the company's default person"
    return None


def _approval(c: Any, row: Any) -> str | None:
    """What the row's OK allows now, writing nothing: "use" (given, not used yet), "used" (this same row used it on an
    attempt the provider safely refused, and whoever gave it may still give it), or None."""
    rid = row["approval_id"]
    found = task_asks.grant(c, rid) if rid else None
    if not found or found["subject"] != SUBJECT + row["id"]:
        return None
    if found.get("used_at"):
        same = found["content_hash"] == task_asks.content_hash(content(row)) and found["used_by_id"] == _asker(row)
        return "used" if same and task_asks.claimed_problem(c, rid, authority=True) is None else None
    return "use" if task_asks.passes(c, rid, SUBJECT + row["id"], content(row), _asker(row)) else None


# ------------------------------------------------------------------------------------------------------- settling
def settle(
    c: Any, row: Any, state: str, *, receipt: dict[str, Any] | None = None, error: str = "", token: str | None = None
) -> bool:
    """Write an outcome: done, failed, canceled, blocked or unknown. Ended rows lose their payload and keep their
    metadata. With `token`, only while that claim still holds the row. True when written."""
    extra = {
        "receipt": _dump({**_load(row["receipt"], {}), **(receipt or {})}),
        "updated_at": H.now(),
        **({"payload": ""} if state in ENDED else {}),
    }
    if not O.finish(
        c,
        TABLE,
        row["id"],
        state,
        attempts=row["attempts"],
        error=error,
        due_at=row["next_attempt_at"],
        extra=extra,
        token=token,
    ):
        return False
    rid, why = row["approval_id"], error or f"It was not sent ({state})"
    if state == "unknown":
        rid = ""  # its OK's task stays open until the outcome is known
    if rid and state == "done":
        task_asks.settle(c, rid, "Sent.")
    elif rid and state in ("blocked", "canceled"):
        task_asks.withdraw(c, rid, why)  # an OK not used yet is taken back; one used ends not done
        task_asks.not_done(c, rid, why)
    elif rid:
        task_asks.not_done(c, rid, why)
    O.note(TABLE.name, state, row["id"], channel=row["channel"])
    if state == "unknown":
        LOG.warning("outbound action %s (%s) has an unknown outcome", row["id"], row["channel"])
        H.event(c, H.TICO_ACTOR, "outbound.unknown", row["id"], {"channel": row["channel"]})
    return True


STOPS = (task_asks.SENT_BACK, task_asks.WITHDRAWN)  # an OK sent back, taken back, or its task closed: it stops
STOPPED = "Its OK was sent back or taken back, or its task closed"
RESOLVED = ("done", "failed")  # what a reconcile may settle an unknown row as


def apply(c: Any, seen: Any, found: "Settled | None") -> None:
    """A reconcile's finding for the unknown row `seen`, written only while the row is still as it was read: done or
    failed, from the provider's own record. Nothing else moves an unknown row on its own, least of all back in line:
    the worker that made the attempt may still reach the provider. What it cannot settle goes to the back of the
    line."""
    if found is None or found.state not in RESOLVED:
        c.execute(
            "UPDATE outbound_actions SET updated_at=? WHERE id=? AND state='unknown' AND updated_at=?",
            (H.now(), seen["id"], seen["updated_at"]),
        )
        return
    row = get(c, seen["id"])
    if row and row["state"] == "unknown" and row["updated_at"] == seen["updated_at"]:
        settle(c, row, found.state, receipt=found.receipt, error=found.error)


# ------------------------------------------------------------------------------------------------------ the worker
BELL = background.Bell()
_workers = background.Loop("outbound-actions", ring)
_state: dict[str, Any] = {"swept": 0.0, "store": None}


class Claimed(NamedTuple):
    id: str
    token: str


def recover(store: Any, now: str | None = None) -> int:
    """Rows whose worker never answered (a crash, a restart): one never attempted goes back to `queued`; one attempted
    is `unknown`, first, in the same transaction, so it is never sent again on its own."""
    now = now or H.now()
    before = H.shift(now, seconds=-STALE)
    with store.read() as c:  # an idle queue takes no write lock
        if not c.execute(
            "SELECT EXISTS(SELECT 1 FROM outbound_actions WHERE state='attempting' AND claimed_at<?)", (before,)
        ).fetchone()[0]:
            return 0
    with store.transaction() as c:
        stale = c.execute(
            "SELECT * FROM outbound_actions WHERE state='attempting' AND claimed_at<? AND provider_attempted_at IS NOT NULL",
            (before,),
        ).fetchall()
        for row in stale:
            settle(c, dict(row), "unknown", error="The worker stopped after the provider may have had it")
        back = O.reclaim_stale(c, TABLE, before=before, now=now)
    if back:
        ring()
    return back + len(stale)


def reconcile_pass(store: Any, limit: int = 100) -> int:
    """Unknown rows whose adapter can ask the provider, the least recently looked at first. Each adapter call runs
    outside any transaction; what it found is written only while the row is still as it was read (`apply`)."""
    channels = [name for name, adapter in ADAPTERS.items() if adapter.reconcile]
    if not channels:
        return 0
    with store.read() as c:
        rows = [
            dict(r)
            for r in c.execute(
                f"SELECT * FROM outbound_actions WHERE state='unknown' AND channel IN ({','.join('?' * len(channels))}) "
                "ORDER BY updated_at LIMIT ?",
                (*channels, limit),
            )
        ]
    for row in rows:
        reconcile = ADAPTERS[row["channel"]].reconcile
        assert reconcile is not None
        try:
            found = reconcile(store, row)
        except Exception:  # noqa: BLE001  (a lookup that fails tells nothing: the row stays unknown)
            LOG.warning("outbound action %s: reconcile failed", row["id"], exc_info=True)
            found = None
        with store.transaction() as c:
            apply(c, row, found)
    return len(rows)


def claim(store: Any, now: str | None = None) -> Claimed | None:
    """The next due row (past its retry time and its hold), now `attempting` under a fresh claim token, or None."""
    now = now or H.now()
    with store.read() as c:  # an idle queue takes no write lock
        if not c.execute(
            "SELECT EXISTS(SELECT 1 FROM outbound_actions WHERE state='queued' AND next_attempt_at<=? AND send_after<=?)",
            (now, now),
        ).fetchone()[0]:
            return None
    with store.transaction() as c:
        rows = c.execute(
            "SELECT id FROM outbound_actions WHERE state='queued' AND next_attempt_at<=? AND send_after<=? "
            "ORDER BY next_attempt_at LIMIT 20",
            (now, now),
        ).fetchall()
        for r in rows:
            token = O.new_token()
            if O.claim(c, TABLE, r["id"], now, token):
                return Claimed(r["id"], token)
    return None


def next_due(store: Any) -> float | None:
    """Seconds until the next queued row may go (0 when one may now), or None when none is queued."""
    with store.read() as c:
        at = c.execute(
            "SELECT min(max(next_attempt_at, send_after)) FROM outbound_actions WHERE state='queued'"
        ).fetchone()[0]
    when = H.parse_ts(at) if at else None
    return None if when is None else max(0.0, when.timestamp() - time.time())


def _permit(store: Any, claimed: Claimed) -> dict[str, Any] | str:
    """Everything before the provider call, in one durable transaction: the policy and the adapter's checks again
    (the audience derived anew), then the fenced write, which needs this claim and an empty `provider_attempted_at`,
    with the OK used in that same write. The row to dispatch, or what happened instead."""
    with store.transaction(durable=True) as c:
        found = c.execute(
            "SELECT * FROM outbound_actions WHERE id=? AND state='attempting' AND claim_token=? "
            "AND provider_attempted_at IS NULL",
            (claimed.id, claimed.token),
        ).fetchone()
        if not found:
            return "lost"  # the claim lapsed, or this claim already made its attempt: nothing is called
        row = dict(found)
        adapter = ADAPTERS.get(row["channel"])
        try:
            if adapter is None:
                raise Problem("validation", f"Tico has no sender for {row['channel']!r}", 422)
            stopped = bot_stopped(c, row["actor"])
            if stopped:
                raise Problem("bot_inactive", stopped, 409)
            audience, _ = adapter.prepare(
                c,
                store.settings,
                row["actor"],
                row["destination"],
                content(row)["payload"],
                operation=row["operation"],
                connection_id=row["connection_id"],
                task_id=row["task_id"],
            )
        except Problem as exc:
            refused: Any = exc  # Problem is not typed
            settle(c, row, "blocked", error=str(refused.detail), token=claimed.token)
            return "blocked"
        audience = audience if audience in AUDIENCES else "external"
        policy = decide(
            c, actor=row["actor"], channel=row["channel"], audience=audience, destination=row["destination"]
        )
        c.execute(
            "UPDATE outbound_actions SET audience=?,policy_decision=? WHERE id=?", (audience, _dump(policy), row["id"])
        )
        if policy["choice"] == "never":
            settle(c, row, "blocked", error=policy["why"], token=claimed.token)
            return "blocked"
        rid = row["approval_id"]
        if rid and task_asks.standing(c, rid) in STOPS:
            settle(c, row, "blocked", error=STOPPED, token=claimed.token)
            return "blocked"
        approval = _approval(c, row)
        if policy["choice"] == "ask_first" and approval is None:
            if not request_approval(c, row, renew=bool(row["approval_id"])):  # filed while the claim still holds
                settle(c, row, "blocked", error=NOBODY_TO_ASK, token=claimed.token)
                return "blocked"
            O.finish(
                c,
                TABLE,
                row["id"],
                "waiting_approval",
                attempts=row["attempts"],
                error="",
                due_at=row["next_attempt_at"],
                extra={"updated_at": H.now()},
                token=claimed.token,
            )
            return "waiting_approval"
        now = H.now()
        fenced = c.execute(
            "UPDATE outbound_actions SET provider_attempted_at=?,attempts=attempts+1,updated_at=? "
            "WHERE id=? AND state='attempting' AND claim_token=? AND provider_attempted_at IS NULL",
            (now, now, row["id"], claimed.token),
        ).rowcount
        if not fenced:
            return "lost"
        if approval == "use":
            task_asks.use(c, row["approval_id"], SUBJECT + row["id"], content(row), _asker(row))
        return get(c, row["id"]) or row


def attempt(store: Any, claimed: Claimed) -> str:
    """The permit (`_permit`), then the one adapter call, then its answer recorded under the same claim. Returns
    what happened (for logs and tests)."""
    row = _permit(store, claimed)
    if isinstance(row, str):
        return row
    try:
        answer = ADAPTERS[row["channel"]].dispatch(store, row)
    except Refused as exc:
        with store.transaction() as c:
            if not settle(c, row, "failed", error=str(exc)[:300], token=claimed.token):
                return "failed" if late_answer(c, row, "failed", error=str(exc)[:300]) else "lost"
        return "failed"
    except Exception as exc:  # noqa: BLE001  (the provider may have acted: unknown, never tried again on its own)
        with store.transaction() as c:
            settle(c, row, "unknown", error=f"{type(exc).__name__}: {exc}"[:300], token=claimed.token)
        return "unknown"
    with store.transaction() as c:
        if isinstance(answer, SafeRejection):
            if row["attempts"] >= MAX_SAFE_RETRIES:
                error = f"{answer.error} after {row['attempts']} attempts"
                if settle(c, row, "failed", error=error, token=claimed.token):
                    return "failed"
                return "failed" if late_answer(c, row, "failed", error=error) else "lost"
            # One write: back in line, the attempt marker cleared, the provider's wait kept.
            if not O.finish(
                c,
                TABLE,
                row["id"],
                "queued",
                attempts=row["attempts"],
                error=answer.error,
                due_at=H.shift(H.now(), seconds=max(0.0, float(answer.retry_after))),
                extra={"provider_attempted_at": None, "updated_at": H.now()},
                token=claimed.token,
            ):
                # The claim was cleared (the row went unknown): the provider did nothing, and nothing retries an
                # unknown row, so the refusal ends it.
                return "failed" if late_answer(c, row, "failed", error=answer.error) else "lost"
            return "requeued"
        if not settle(c, row, "done", receipt=dict(answer or {}), token=claimed.token):
            return "done" if late_answer(c, row, "done", receipt=dict(answer or {})) else "lost"
    return "done"


def late_answer(c: Any, row: Any, state: str, *, receipt: dict[str, Any] | None = None, error: str = "") -> bool:
    """The provider's definite answer (done, or a certain refusal) for an attempt whose claim stale recovery cleared,
    leaving the row unknown: it settles the row. Matched by the row and its attempt marker, so a later attempt's row
    is never touched. True when written."""
    now = get(c, row["id"])
    if not now or now["state"] != "unknown" or now["provider_attempted_at"] != row["provider_attempted_at"]:
        return False
    return settle(
        c, now, state, receipt={**(receipt or {}), "late": True} if receipt is not None else None, error=error
    )


def retire_pass(store: Any, limit: int = 500) -> int:
    """Actions waiting for an OK that was sent back or taken back, or whose task closed: canceled (payload wiped),
    never asked again."""
    with store.read() as c:
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM outbound_actions WHERE state='waiting_approval' AND approval_id<>'' "
                "ORDER BY updated_at LIMIT ?",
                (limit,),
            )
        ]
        stopped = [r for r in rows if task_asks.standing(c, r["approval_id"]) in STOPS]
    for row in stopped:
        with store.transaction() as c:
            now = get(c, row["id"])
            if now and now["state"] == "waiting_approval" and now["approval_id"] == row["approval_id"]:
                settle(c, now, "canceled", error=STOPPED)
    return len(stopped)


RECONCILE_CAP = 20  # provider lookups per sweep, so a due row never waits behind many


def step(store: Any, now: str | None = None) -> bool:
    """One worker turn: one due row, then the backstop (recovery, retiring stopped OKs, a few reconciles) at most
    every SWEEP seconds. True when it attempted one (so the caller looks again at once)."""
    claimed = claim(store, now)
    if claimed:
        attempt(store, claimed)
    if time.monotonic() - _state["swept"] >= SWEEP:
        _state["swept"] = time.monotonic()
        recover(store, now)
        retire_pass(store)
        reconcile_pass(store, RECONCILE_CAP)
    return claimed is not None


def _loop(store: Any, halt: Any) -> None:
    while not halt.is_set():
        seen = BELL.rung
        try:
            worked = step(store)
            due = None if worked else next_due(store)
        except Exception:
            LOG.exception("outbound actions step failed")
            worked, due = False, None
        if not worked:
            BELL.wait(seen, SWEEP if due is None else min(SWEEP, max(0.05, due)))


def start(store: Any) -> None:
    """Start (or restart, when it died) the worker. Safe to call again."""
    _state["store"] = store
    _workers.start(_loop, store)


def ensure() -> None:
    """The scheduler's tick: a worker that died comes back. Nothing before the first `start`."""
    if _state["store"] is not None and not _workers.stopped():
        start(_state["store"])


def stop() -> None:
    _workers.stop()


# ---------------------------------------------------------------------------------------------------------- the API
class OutboundActionCreate(Contract):
    channel: str = Field(min_length=1, max_length=40)
    operation: str = Field(default="send", max_length=80)
    destination: str = Field(min_length=1, max_length=500)
    occurrence_key: str = Field(min_length=1, max_length=300)
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    connection_id: str = Field(default="", max_length=200)
    task_id: str | None = Field(default=None, max_length=200)
    send_after: str | None = Field(default=None, max_length=40)


class OutboundApprove(Contract):
    shown_hash: str = Field(min_length=1, max_length=200)


class OutboundPolicyUpdate(Contract):
    settings: dict[str, str | None]
    """{"<channel>:<audience>": "on_their_own" | "ask_first" | "never" | null (back to the default)}"""


def policy_view(c: Any) -> dict[str, Any]:
    stored = company_policy(c)
    channels = sorted(ADAPTERS)
    effective = {f"{ch}:{aud}": stored.get(f"{ch}:{aud}") or DEFAULTS[aud] for ch in channels for aud in AUDIENCES}
    return {"settings": stored, "effective": effective, "choices": list(CHOICES), "channels": channels}


def install(app: Any, store: Any, auth: Any, mutate: Any) -> None:
    from .api_answers.outbound_actions import (
        OutboundActionDetail,
        OutboundActionList,
        OutboundActionResult,
        OutboundPolicy,
    )

    def admin(who: Any) -> bool:
        """The owner or an admin, as a person (auth.bot_admin)."""
        return who.role in ("owner", "human") and bool(auth.bot_admin(who))

    def visible(c: Any, who: Any, row: Any) -> bool:
        """Whoever queued it, the owner or an admin, and the parties to its OK (task_asks.party: the person asked)."""
        if row["actor"] == who.actor or admin(who):
            return True
        found = task_asks.grant(c, row["approval_id"]) if row["approval_id"] else None
        return bool(found) and task_asks.party(c, who.actor, found)

    def one(c: Any, who: Any, oid: str) -> dict[str, Any]:
        row = get(c, oid)
        if not row or not visible(c, who, row):
            raise Problem("not_found", "No such outbound action", 404)
        return row

    @app.get(
        "/api/v2/outbound-actions/policy",
        **api_v2(
            "Outbound actions",
            "getOutboundPolicy",
            "The company's outbound policy per channel and audience",
            OutboundPolicy,
        ),
    )
    def get_policy(request: Request) -> Any:
        auth.domain(request.state.identity)
        with store.read() as c:
            return policy_view(c)

    @app.put(
        "/api/v2/outbound-actions/policy",
        **api_v2(
            "Outbound actions",
            "setOutboundPolicy",
            "Set the company's outbound policy (owner or admin, as themselves)",
            OutboundPolicy,
        ),
    )
    def set_policy(request: Request, body: OutboundPolicyUpdate) -> Any:
        who = request.state.identity
        _mod("mail_settings").require_person(who)
        if getattr(who, "support", None):
            raise Problem("forbidden", "Tico support does not set the company's outbound policy", 403)
        if not admin(who):
            raise Problem("forbidden", "Only the owner or an admin sets the outbound policy", 403)
        for key, value in body.settings.items():
            channel, _, audience = key.partition(":")
            if channel not in ADAPTERS or audience not in AUDIENCES:
                known = ", ".join(sorted(ADAPTERS)) or "none yet"
                raise Problem("validation", f"{key!r} is not <channel>:<audience> (channels: {known})", 422)
            if value is not None and value not in CHOICES:
                raise Problem("validation", f"{value!r} is not one of {', '.join(CHOICES)}", 422)

        def work(c: Any) -> Any:
            stored = company_policy(c)
            for key, value in body.settings.items():
                if value is None:
                    stored.pop(key, None)
                else:
                    stored[key] = value
            c.execute(
                "INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (POLICY_KEY, _dump(stored)),
            )
            H.event(c, who.actor, "outbound.policy", POLICY_KEY, {"settings": body.settings})
            return policy_view(c)

        return mutate(request, body, work, durable=True)

    @app.post(
        "/api/v2/outbound-actions",
        **api_v2(
            "Outbound actions",
            "createOutboundAction",
            "Queue an outbound action through the gate (a bot); the same occurrence_key answers the existing one",
            OutboundActionResult,
        ),
    )
    def create(request: Request, body: OutboundActionCreate) -> Any:
        who = request.state.identity
        bot = who.role == "bot" and H.is_bot(who.actor)
        if not (bot or who.actor == H.TICO_ACTOR):
            raise Problem("forbidden", "A bot queues outbound actions; a person sends or approves them in Tico", 403)

        def work(c: Any) -> Any:
            task_id = None
            if body.task_id:
                task_id = auth.resolve_task(c, who, body.task_id)
                auth.task(c, who, task_id)
            if body.connection_id:
                vault = getattr(app.state, "vault", None)
                if not _mod("api_tools").visible(c, who, body.connection_id, vault.admins if vault else (), auth):
                    raise Problem("not_found", "No such connection", 404)
            row, created = enqueue(
                c,
                auth.settings,
                actor=who.actor,
                channel=body.channel,
                operation=body.operation,
                destination=body.destination,
                occurrence_key=body.occurrence_key,
                payload=dict(body.payload),
                metadata=body.metadata,
                connection_id=body.connection_id,
                task_id=task_id,
                send_after=body.send_after,
            )
            if row["actor"] != who.actor:
                raise Problem("conflict", "This occurrence_key belongs to another action", 409)
            return {"action": public(c, row), "created": created}

        return mutate(request, body, work, durable=True)

    @app.get(
        "/api/v2/outbound-actions",
        **api_v2(
            "Outbound actions",
            "listOutboundActions",
            "Outbound actions you queued (all of them for the owner or an admin), newest first, byte capped",
            OutboundActionList,
        ),
    )
    def listing(request: Request, state: str = "", channel: str = "", cursor: str = "", limit: int = 50) -> Any:
        who = request.state.identity
        auth.domain(who)
        if state and state not in STATES:
            raise Problem("validation", f"state is one of {', '.join(STATES)}", 422)
        cap = min(100, max(1, limit))
        fields = ",".join(
            (
                *(f for f in LIST_FIELDS if f != "last_error"),
                "substr(last_error,1,300) AS last_error",
                "policy_decision",
            )
        )
        with store.read() as c:
            sql, args = f"SELECT {fields} FROM outbound_actions WHERE 1=1", list[Any]()
            if not admin(who):
                sql += " AND actor=?"
                args.append(who.actor)
            if state:
                sql += " AND state=?"
                args.append(state)
            if channel:
                sql += " AND channel=?"
                args.append(channel)
            if cursor:
                at = c.execute(
                    "SELECT created_at FROM outbound_actions WHERE id=? AND (?=1 OR actor=?)",
                    (cursor, int(admin(who)), who.actor),
                ).fetchone()
                if at:
                    sql += " AND (created_at<? OR (created_at=? AND id<?))"
                    args += [at[0], at[0], cursor]
            rows = c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, cap + 1)).fetchall()
            tasks = approval_tasks(c, [r["approval_id"] for r in rows[:cap]])
            items: list[dict[str, Any]] = []
            spent = 0
            for r in rows[:cap]:
                item = public(c, r, tasks=tasks)
                size = len(_dump(item))
                if spent + size > LIST_BYTES:
                    break
                items.append(item)
                spent += size
            more = len(items) < len(rows)
            return {"items": items, "next_cursor": items[-1]["id"] if more and items else None}

    @app.get(
        "/api/v2/outbound-actions/{oid}",
        **api_v2(
            "Outbound actions",
            "getOutboundAction",
            "One outbound action with its frozen payload, and the shown_hash its OK sends back",
            OutboundActionDetail,
        ),
    )
    def detail(request: Request, oid: str) -> Any:
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = one(c, who, oid)
            waiting = row["state"] == "waiting_approval" and row["approval_id"]
            if not waiting:
                return {"action": public(c, row, full=True), "shown_hash": None}
            if not task_asks.own_click(who):  # nothing to record: the hash alone
                shown = task_asks.show(c, who.actor, row["approval_id"], content(row), own_click=False)
                return {"action": public(c, row, full=True), "shown_hash": shown}
        with store.transaction() as c:  # the person is shown what an OK binds: recorded (task_asks.show)
            row = one(c, who, oid)
            shown = None
            if row["state"] == "waiting_approval" and row["approval_id"]:
                shown = task_asks.show(
                    c, who.actor, row["approval_id"], content(row), own_click=task_asks.own_click(who)
                )
            return {"action": public(c, row, full=True), "shown_hash": shown}

    @app.post(
        "/api/v2/outbound-actions/{oid}/approve",
        **api_v2(
            "Outbound actions",
            "approveOutboundAction",
            "OK exactly the action you were shown, by your own click (the person it is asked of)",
            OutboundActionResult,
        ),
    )
    def approve(request: Request, oid: str, body: OutboundApprove) -> Any:
        who = request.state.identity
        ctx = task_asks.Context(auth, auth.settings, who, app)

        def work(c: Any) -> Any:
            row = get(c, oid)
            task_id = approval_tasks(c, [row["approval_id"]]).get(row["approval_id"]) if row else None
            if not row or not task_id:
                raise Problem("not_found", "No such outbound action waiting for an OK", 404)
            auth.task(c, who, task_id)
            task_asks.give_ok(
                c, who.actor, row["approval_id"], body.shown_hash, own_click=task_asks.own_click(who), ctx=ctx
            )
            return {"action": public(c, get(c, oid)), "created": False}

        return mutate(request, body, work, durable=True)

    @app.post(
        "/api/v2/outbound-actions/{oid}/cancel",
        **api_v2(
            "Outbound actions",
            "cancelOutboundAction",
            "Cancel an action that has not gone: whoever queued it, the owner or an admin",
            OutboundActionResult,
        ),
    )
    def cancel(request: Request, oid: str) -> Any:
        who = request.state.identity

        def work(c: Any) -> Any:
            row = get(c, oid)
            if not row or not (row["actor"] == who.actor or admin(who)):
                raise Problem("not_found", "No such outbound action", 404)
            if row["state"] == "canceled":
                return {"action": public(c, row), "created": False}
            if row["state"] == "unknown":
                raise Problem(
                    "state",
                    "It may have gone: confirm it went. Queue a new action (a new occurrence_key) only once the original "
                    "attempt provably can no longer act; finding no record of it at the provider is not proof",
                    409,
                )
            if row["state"] not in OPEN:
                raise Problem("state", f"This action is {row['state']}: it can no longer be canceled", 409)
            settle(c, row, "canceled", error=f"Canceled by {H.actor_name(c, who.actor)}")
            H.event(c, who.actor, "outbound.canceled", oid, {"channel": row["channel"]})
            return {"action": public(c, get(c, oid)), "created": False}

        return mutate(request, Empty(), work, durable=True)

    @app.post(
        "/api/v2/outbound-actions/{oid}/confirm-sent",
        **api_v2(
            "Outbound actions",
            "confirmOutboundActionSent",
            "A person confirms an action whose outcome is unknown went out: it is done",
            OutboundActionResult,
        ),
    )
    def confirm_sent(request: Request, oid: str) -> Any:
        who = request.state.identity
        # A person as themselves (their own token too), never Agent Ops, the Assistant or a run acting for them.
        if who.role not in ("owner", "human") or who.via or who.task_actor or getattr(who, "support", None):
            raise Problem("forbidden", "Only a person, as themselves, confirms it went", 403)

        def work(c: Any) -> Any:
            row = one(c, who, oid)
            if row["state"] != "unknown":
                raise Problem("state", f"This action is {row['state']}, not unknown", 409)
            settle(c, row, "done", receipt={"confirmed_by": who.actor})
            H.event(c, who.actor, "outbound.confirmed_sent", oid, {})
            return {"action": public(c, get(c, oid)), "created": False}

        return mutate(request, Empty(), work, durable=True)
