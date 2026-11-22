"""The outbound gate: one queue for every send and outside write (docs/outbound-actions.md).

A row is one action on one channel (`slack`, later `jira`, `email`, `sms`, `webhook`): where it goes, its frozen
`payload` (wiped once the row ends) and the `metadata` kept for later lookups. `enqueue` is the one way in, idempotent on
`occurrence_key`. One policy (`decide`) sets the row's first state and is checked again before every attempt.

- **Policy.** A floor that only tightens (the company's Never for the channel and audience, a bounced email address, a
  Contacts rule that blocks or escalates), then the default for the channel and audience: the company's setting, else
  On its own inside the company and Ask first outside it. Ask first waits on a person's OK bound to the frozen content
  (backend/task_asks.py, subject `outbound:<id>`).
- **Lease.** The worker claims a due row through backend/outbox.py (`queued` to `attempting`, with a claim token).
- **Fence.** Before any provider call, ONE write under that token stamps `provider_attempted_at` and uses the OK. A
  worker whose claim lapsed finds no row to stamp and calls nothing.
- **Stale claims.** A row still `attempting` past STALE goes back to `queued` only when it was never attempted; one
  that was is `unknown` (in the same transaction), never sent again on its own.
- **Answers.** An adapter returns a receipt (done), `HandedOff` (another sender owns it now and settles it), a
  `SafeRejection` (requeued with the marker cleared and `next_attempt_at` set from Retry-After), raises `Refused`
  (failed) or raises anything else (unknown). An unknown row is reconciled by its adapter where it can look; otherwise
  an outside send gets one owner ask, an internal one a log line.
- **Wake.** A commit that queues a row rings the worker; a slow sweep (SWEEP) is the backstop and does the
  reconciling.

Rows are never pruned while unresolved: nothing here deletes a row.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple

from fastapi import Request
from pydantic import Field

from . import background
from . import outbox as O
from . import task_asks
from .api_contract import api_v2
from .models import Contract, Empty
from .status_values import OUTBOUND_ACTION_STATES as STATES
from .store import H, Problem

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
SWEEP = 60.0  # the backstop: recovery, reconciling and a look for due rows without a ring
MAX_SAFE_RETRIES = 10  # safe rejections (rate limits) before a row is failed
SUBJECT = "outbound:"
ENDED = ("done", "failed", "canceled")  # terminal: the payload is wiped
OPEN = ("waiting_approval", "queued", "blocked", "unknown")  # a person may cancel these
AUDIENCES = ("internal", "external")
CHANNELS = ("slack", "jira", "email", "sms", "webhook")
# The policy's choices, spelled as the email policy spells them (backend/email_sending.py MODES): strictest last.
CHOICES = ("on_their_own", "ask_first", "never")
DEFAULTS = {"internal": "on_their_own", "external": "ask_first"}
POLICY_KEY = "outbound_policy"
WORDS = {"slack": "Slack post", "jira": "Jira update", "email": "email", "sms": "text", "webhook": "webhook call"}


# ----------------------------------------------------------------------------------------------- adapter interface
class SafeRejection(NamedTuple):
    """The provider refused without acting (a rate limit): tried again after `retry_after` seconds."""

    retry_after: float
    error: str = "rate limited"


class HandedOff(NamedTuple):
    """The action went to another durable sender (Slack's gateway): it settles the row later (`settle_from`)."""

    receipt: dict[str, Any]


class Settled(NamedTuple):
    """What an adapter's reconcile found: `state` one of done, failed, canceled, blocked, unknown, or `queued` when the
    provider certainly never had it (safe to try again)."""

    state: str
    receipt: dict[str, Any] = {}
    error: str = ""


class Refused(Exception):
    """The provider certainly did not act and will not on a retry: the row is failed."""


class LostClaim(Exception):
    """The worker's claim lapsed before the handoff: nothing was handed off."""


@dataclass
class Adapter:
    """One channel's sender. `prepare(c, settings, actor, destination, payload)` checks an action at enqueue and
    answers (audience, destination) with the destination resolved; an audience it cannot tell is external.
    `dispatch(store, row)` makes the one provider call. `reconcile(c, row)`, where the provider can be asked, answers
    a `Settled` or None (cannot tell yet)."""

    channel: str
    prepare: Callable[..., tuple[str, str]]
    dispatch: Callable[..., Any]
    reconcile: Callable[..., "Settled | None"] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    ADAPTERS[adapter.channel] = adapter
    return adapter


# --------------------------------------------------------------------------------------------------------- policy
def company_policy(c) -> dict[str, str]:
    """The company's settings, {"<channel>:<audience>": choice}; what is not set takes DEFAULTS."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (POLICY_KEY,)).fetchone()
    stored = json.loads(row[0]) if row and row[0] else {}
    return {k: v for k, v in stored.items() if v in CHOICES} if isinstance(stored, dict) else {}


def _strictest(*choices: str) -> str:
    return max(choices, key=CHOICES.index)


def _floor(c, actor, channel, destination, metadata) -> tuple[str, str]:
    """(choice, why) of what may only tighten: a bounced address, a Contacts rule. ("on_their_own", "") when none."""
    address = destination.strip().lower()
    if channel == "email" and "@" in address:
        mailbox = str(metadata.get("mailbox") or "").strip().lower()
        if c.execute(
            "SELECT 1 FROM email_suppressed_recipients WHERE address=? AND (?='' OR mailbox=?) LIMIT 1",
            (address, mailbox, mailbox),
        ).fetchone():
            return "never", "The address bounced: it is on the suppression list"
    from . import org_rule_check

    contacts = [address] if "@" in address or address.startswith("+") else []
    verdict = org_rule_check.structured_check(
        c, actor or H.TICO_ACTOR, channel, targets=list(metadata.get("targets") or []), contacts=contacts
    )
    if verdict and verdict["outcome"] == "held":
        effects = {rule["effect"] for rule in verdict["held"]}
        why = "; ".join(str(rule["text"]) for rule in verdict["held"])[:300]
        return ("never" if "block" in effects else "ask_first"), why
    return "on_their_own", ""


def decide(c, *, actor, channel, audience, destination, metadata=None) -> dict[str, Any]:
    """The policy for one action now: {"choice", "why", "floor"}. The floor only tightens; the default is the company's
    setting for the channel and audience, else DEFAULTS. Enqueue and every attempt read it the same way."""
    company = company_policy(c).get(f"{channel}:{audience}")
    if company == "never":
        return {"choice": "never", "why": f"The company never sends {channel} to {audience} audiences", "floor": True}
    floor, why = _floor(c, actor, channel, destination, metadata or {})
    default = company or DEFAULTS[audience]
    choice = _strictest(default, floor)
    if choice != default:
        return {"choice": choice, "why": why, "floor": True}
    source = "the company's setting" if company else "the default"
    return {"choice": choice, "why": f"{source} for {channel} to {audience} audiences", "floor": False}


# ---------------------------------------------------------------------------------------------------------- rows
def _load(value, default):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _dump(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def get(c, action_id) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM outbound_actions WHERE id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def content(row) -> dict[str, Any]:
    """What an OK of the row is bound to: the frozen action, exactly."""
    return {
        "channel": row["channel"],
        "operation": row["operation"],
        "destination": row["destination"],
        "payload": _load(row["payload"], {}),
    }


def public(c, row, *, payload=False) -> dict[str, Any]:
    out = {
        k: row[k]
        for k in (
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
    }
    out["metadata"] = _load(row["metadata"], {})
    out["policy_decision"] = _load(row["policy_decision"], {})
    out["receipt"] = _load(row["receipt"], {})
    out["approval_task_id"] = task_asks.task_of(c, row["approval_id"]) if row["approval_id"] else None
    if payload:
        out["payload"] = _load(row["payload"], None)
    return out


def _person_for(c, actor) -> str:
    """Who is asked about an action: the person who made it, a bot's manager when that is a person, else the
    company's default person."""
    if H.is_human(actor):
        return actor
    if H.is_bot(actor):
        from . import ask_ingest

        manager = ask_ingest.manager_of(c, actor)[0]
        if manager and H.is_human(manager):
            return manager
    return H.human_actor(H.default_human(c))


def _asker(row) -> str:
    return row["actor"] if H.is_bot(row["actor"]) or H.is_human(row["actor"]) else H.TICO_ACTOR


def request_approval(c, row) -> None:
    """Ask a person for an OK of this exact action (one OK bound to its content), on a task of its own filed under the
    row's task when there is one. The same content asked again is the same ask."""
    asked = task_asks.ask_ok(
        c,
        _asker(row),
        _person_for(c, row["actor"]),
        f"Send this {WORDS.get(row['channel'], row['channel'])}?",
        shows=[SUBJECT + row["id"]],
        subject=SUBJECT + row["id"],
        content=content(row),
        matter=row["task_id"],
    )
    c.execute("UPDATE outbound_actions SET approval_id=? WHERE id=?", (asked["requirement_id"], row["id"]))


def ring() -> None:
    BELL.ring()


def _after_commit(c, fn) -> None:
    hooks = getattr(c, "after_commit", None)
    if hooks is None:
        fn()
    else:
        hooks.append(fn)


def enqueue(
    c,
    settings,
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
    found = c.execute("SELECT * FROM outbound_actions WHERE occurrence_key=?", (key,)).fetchone()
    if found:
        return dict(found), False
    adapter = ADAPTERS.get(channel)
    if adapter is None:
        known = ", ".join(sorted(ADAPTERS)) or "none"
        raise Problem("validation", f"Tico has no sender for {channel!r} yet (it sends: {known})", 422)
    audience, destination = adapter.prepare(c, settings, actor, str(destination or "").strip(), payload)
    audience = audience if audience in AUDIENCES else "external"
    metadata = {**(metadata or {}), "actor": actor}
    policy = decide(c, actor=actor, channel=channel, audience=audience, destination=destination, metadata=metadata)
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
        "metadata": _dump(metadata),
        "policy_decision": _dump(policy),
        "state": state,
        "send_after": send_after or now,
        "next_attempt_at": now,
        "task_id": task_id,
        "last_error": policy["why"] if state == "blocked" else "",
        "created_at": now,
        "updated_at": now,
    }
    c.execute(f"INSERT INTO outbound_actions({','.join(row)}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
    row = get(c, row["id"])
    assert row is not None
    if state == "waiting_approval":
        request_approval(c, row)
        row = get(c, row["id"]) or row
    elif state == "queued":
        _after_commit(c, ring)
    H.event(c, actor or H.TICO_ACTOR, "outbound.queued", row["id"], {"channel": channel, "state": state})
    return row, True


# --------------------------------------------------------------------------------------------------- approvals
def ok_given(c, grant, ctx=None) -> None:
    """In the transaction of an OK of an action (task_asks.ON_OK): the row it waits on is queued, and the worker rung
    once the OK commits. The OK itself is used in the attempt's fenced write."""
    row = get(c, str(grant["subject"]).removeprefix(SUBJECT))
    if not row or row["state"] != "waiting_approval":
        raise Problem("state", "This action is no longer waiting for an OK", 409)
    c.execute(
        "UPDATE outbound_actions SET state='queued',next_attempt_at=?,updated_at=? WHERE id=? AND state='waiting_approval'",
        (H.now(), H.now(), row["id"]),
    )
    _after_commit(c, ring)


def _approved(c, row) -> bool:
    """Whether the row's OK lets this attempt go, using it when it is still unused. An OK this same row already used
    (an attempt the provider safely refused, now tried again) still holds: it was for this action, which never
    happened."""
    rid = row["approval_id"]
    found = task_asks.grant(c, rid) if rid else None
    if not found or found["subject"] != SUBJECT + row["id"]:
        return False
    if found.get("used_at"):
        return found["content_hash"] == task_asks.content_hash(content(row)) and found["used_by_id"] == _asker(row)
    if not task_asks.passes(c, rid, SUBJECT + row["id"], content(row), _asker(row)):
        return False
    task_asks.use(c, rid, SUBJECT + row["id"], content(row), _asker(row))
    return True


# ------------------------------------------------------------------------------------------------------- settling
def _close_unknown_ask(c, row, why) -> None:
    task = _load(row["metadata"], {}).get("unknown_task")
    if task:
        H.attempt(c, H.task_close, H.TICO_ACTOR, task, why)


def settle(c, row, state, *, receipt=None, error="", token=None) -> bool:
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
    if rid and state == "done":
        task_asks.settle(c, rid, "Sent.")
    elif rid and state in ("blocked", "canceled"):
        task_asks.withdraw(c, rid, why)  # an OK not used yet is taken back; one used ends not done
        task_asks.not_done(c, rid, why)
    elif rid:
        task_asks.not_done(c, rid, why)
    if row["state"] == "unknown" and state != "unknown":
        _close_unknown_ask(c, row, f"It resolved as {state}.")
    O.note(TABLE.name, state, row["id"], channel=row["channel"])
    adapter = ADAPTERS.get(row["channel"])
    if state == "unknown" and (adapter is None or adapter.reconcile is None):
        fresh = get(c, row["id"])
        if fresh:
            unresolved(c, fresh)  # nothing can look it up: the owner ask (outside) or a log line (inside) now
    return True


def unresolved(c, row) -> None:
    """An unknown outcome nothing can reconcile: an outside action gets one owner ask, an internal one a log line.
    Either happens once per row."""
    meta = _load(row["metadata"], {})
    if meta.get("unknown_noted"):
        return
    meta["unknown_noted"] = H.now()
    if row["audience"] == "external":
        from . import person_asks

        words = WORDS.get(row["channel"], row["channel"])
        task = person_asks.task_for(
            c,
            H.TICO_ACTOR,
            f"Check whether a {words} went out",
            f"Tico tried a {words} ({row['operation']} to {row['destination']}) and could not confirm it went out. "
            "Check with the other side; cancel the action in Tico if it did not, so nobody thinks it did.",
            _person_for(c, row["actor"]),
            question=f"Did this {words} go out?",
            why="Tico could not confirm it.",
        )
        meta["unknown_task"] = task["id"]
    else:
        LOG.warning("outbound action %s (%s) has an unknown outcome", row["id"], row["channel"])
    c.execute("UPDATE outbound_actions SET metadata=? WHERE id=?", (_dump(meta), row["id"]))
    H.event(c, H.TICO_ACTOR, "outbound.unknown", row["id"], {"channel": row["channel"], "audience": row["audience"]})


def hand_off(c, row, receipt) -> bool:
    """In the adapter's handoff transaction: the row stays `attempting` with its claim released and the receipt saved,
    so the sender it went to settles it and recovery leaves it alone. Only while the claim `row` carries still holds."""
    return (
        c.execute(
            "UPDATE outbound_actions SET claimed_at=NULL,claim_token=NULL,receipt=?,updated_at=? "
            "WHERE id=? AND state='attempting' AND claim_token=? AND provider_attempted_at IS NOT NULL",
            (_dump({**_load(row["receipt"], {}), **receipt}), H.now(), row["id"], row["claim_token"]),
        ).rowcount
        == 1
    )


def apply(c, row, found: "Settled | None") -> None:
    """A reconcile's finding, written: `queued` puts a row the provider never had back in line (marker cleared)."""
    if found is None or found.state == row["state"]:
        if row["state"] == "unknown":
            unresolved(c, row)
            c.execute("UPDATE outbound_actions SET updated_at=? WHERE id=?", (H.now(), row["id"]))  # its turn is over
        return
    if found.state == "queued":
        moved = c.execute(
            "UPDATE outbound_actions SET state='queued',provider_attempted_at=NULL,claimed_at=NULL,claim_token=NULL,"
            "next_attempt_at=?,updated_at=? WHERE id=? AND (state='unknown' OR (state='attempting' AND claimed_at IS NULL))",
            (H.now(), H.now(), row["id"]),
        ).rowcount
        if moved:
            if row["state"] == "unknown":
                _close_unknown_ask(c, row, "Tico found it was never sent and tries again.")
            _after_commit(c, ring)
        return
    settle(c, row, found.state, receipt=found.receipt, error=found.error)
    if found.state == "unknown":
        fresh = get(c, row["id"])
        if fresh:
            unresolved(c, fresh)  # the provider cannot tell either


def settle_from(c, action_id) -> None:
    """A sender the action was handed to has an answer (Slack's gateway, in the transaction that records it): the row
    settles from it now. Rows it does not know are left as they are."""
    row = get(c, action_id) if action_id else None
    adapter = ADAPTERS.get(row["channel"]) if row else None
    if not row or not adapter or not adapter.reconcile or row["state"] not in ("attempting", "unknown"):
        return
    if row["state"] == "attempting" and row["claim_token"]:
        return  # a worker still holds it: its own answer settles it
    apply(c, row, adapter.reconcile(c, row))


# ------------------------------------------------------------------------------------------------------ the worker
BELL = background.Bell()
_workers = background.Loop("outbound-actions", ring)
_state: dict[str, Any] = {"swept": 0.0, "store": None}


class Claimed(NamedTuple):
    id: str
    token: str


def recover(store, now=None) -> int:
    """Rows whose worker never answered (a crash, a restart): one never attempted goes back to `queued`; one attempted
    is `unknown`, first, in the same transaction, so it is never sent again on its own."""
    now = now or H.now()
    before = H.shift(now, seconds=-STALE)
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


def reconcile_pass(store, limit=100) -> int:
    """Rows waiting on an answer whose adapter can look (handed off, or unknown), oldest look first: each is settled
    from what it finds. An unknown row it could not settle moves to the back (`apply` stamps it), so old unknowns take
    turns and never crowd out the rows still in flight."""
    looked = 0
    channels = [name for name, adapter in ADAPTERS.items() if adapter.reconcile]
    if not channels:
        return 0
    marks = ",".join("?" * len(channels))
    with store.read() as c:
        ids = [
            r[0]
            for r in c.execute(
                f"SELECT id FROM outbound_actions WHERE channel IN ({marks}) AND (state='unknown' OR "
                "(state='attempting' AND claimed_at IS NULL)) ORDER BY updated_at LIMIT ?",
                (*channels, limit),
            )
        ]
    for action_id in ids:
        with store.transaction() as c:
            settle_from(c, action_id)
        looked += 1
    return looked


def claim(store, now=None) -> Claimed | None:
    """The next due row (past its retry time and its hold), now `attempting` under a fresh claim token, or None."""
    now = now or H.now()
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


def attempt(store, claimed: Claimed) -> str:
    """Check the policy again, make the fenced write, call the adapter once and record its answer. Returns what
    happened (for logs and tests)."""
    with store.transaction() as c:
        found = c.execute(
            "SELECT * FROM outbound_actions WHERE id=? AND state='attempting' AND claim_token=?",
            (claimed.id, claimed.token),
        ).fetchone()
        if not found:
            return "lost"  # the claim lapsed: nothing is called
        row = dict(found)
        adapter = ADAPTERS.get(row["channel"])
        meta = _load(row["metadata"], {})
        policy = decide(
            c,
            actor=row["actor"],
            channel=row["channel"],
            audience=row["audience"],
            destination=row["destination"],
            metadata=meta,
        )
        if adapter is None or policy["choice"] == "never":
            why = policy["why"] if adapter else f"No sender for {row['channel']}"
            c.execute("UPDATE outbound_actions SET policy_decision=? WHERE id=?", (_dump(policy), row["id"]))
            settle(c, row, "blocked", error=why, token=claimed.token)
            return "blocked"
        approved = _approved(c, row) if row["approval_id"] else False
        if policy["choice"] == "ask_first" and not approved:
            O.finish(
                c,
                TABLE,
                row["id"],
                "waiting_approval",
                attempts=row["attempts"],
                error="",
                due_at=row["next_attempt_at"],
                extra={"policy_decision": _dump(policy), "updated_at": H.now()},
                token=claimed.token,
            )
            request_approval(c, get(c, row["id"]))
            return "waiting_approval"
        now = H.now()
        fenced = c.execute(
            "UPDATE outbound_actions SET provider_attempted_at=?,attempts=attempts+1,policy_decision=?,updated_at=? "
            "WHERE id=? AND state='attempting' AND claim_token=?",
            (now, _dump(policy), now, row["id"], claimed.token),
        ).rowcount
        if not fenced:
            return "lost"
        row = get(c, row["id"])
    assert row is not None
    try:
        answer = adapter.dispatch(store, row)
    except LostClaim:
        return "lost"
    except Refused as exc:
        with store.transaction() as c:
            settle(c, row, "failed", error=str(exc)[:300], token=claimed.token)
        return "failed"
    except Exception as exc:  # noqa: BLE001  (the provider may have acted: unknown, never tried again on its own)
        LOG.warning("outbound action %s: %s", row["id"], type(exc).__name__)
        with store.transaction() as c:
            settle(c, row, "unknown", error=f"{type(exc).__name__}: {exc}"[:300], token=claimed.token)
        return "unknown"
    if isinstance(answer, HandedOff):
        return "handed_off"
    with store.transaction() as c:
        if isinstance(answer, SafeRejection):
            if row["attempts"] >= MAX_SAFE_RETRIES:
                settle(c, row, "failed", error=f"{answer.error} after {row['attempts']} attempts", token=claimed.token)
                return "failed"
            # One write: back in line, the attempt marker cleared, the provider's wait kept.
            O.finish(
                c,
                TABLE,
                row["id"],
                "queued",
                attempts=row["attempts"],
                error=answer.error,
                due_at=H.shift(H.now(), seconds=max(0.0, float(answer.retry_after))),
                extra={"provider_attempted_at": None, "updated_at": H.now()},
                token=claimed.token,
            )
            return "requeued"
        settle(c, row, "done", receipt=dict(answer or {}), token=claimed.token)
    return "done"


def step(store, now=None) -> bool:
    """One worker turn: the backstop (recovery and reconciling) at most every SWEEP seconds, then one due row. True
    when it attempted one (so the caller looks again at once)."""
    if time.monotonic() - _state["swept"] >= SWEEP:
        _state["swept"] = time.monotonic()
        recover(store, now)
        reconcile_pass(store)
    claimed = claim(store, now)
    if not claimed:
        return False
    attempt(store, claimed)
    return True


def _loop(store, halt):
    while not halt.is_set():
        seen = BELL.rung
        try:
            worked = step(store)
        except Exception:
            LOG.exception("outbound actions step failed")
            worked = False
        if not worked:
            BELL.wait(seen, SWEEP)


def start(store) -> None:
    """Start (or restart, when it died) the worker. Safe to call again."""
    _state["store"] = store
    _workers.start(_loop, store)


def ensure() -> None:
    """The scheduler's tick: a worker that died comes back. Nothing before the first `start`."""
    if _state["store"] is not None and not _workers.stopped():
        start(_state["store"])


def stop() -> None:
    _workers.stop()


# --------------------------------------------------------------------------------------------- the Slack adapter
def _slack_prepare(c, settings, actor, destination, payload):
    """An internal post as a bot, to a channel on the Slack channel list with posting on (backend/slack_bots.py). The
    gateway asks Slack again before it posts: a channel shared outside the workspace since is refused there."""
    from . import slack_bots as SB

    bot = (H.resolve_bot(c, str(payload.get("bot") or "")) or {}).get("id")
    if not bot:
        raise Problem("validation", "A Slack post names the bot it posts as (payload.bot)", 422)
    if H.is_human(actor):
        raise Problem("forbidden", "A bot queues its own Slack posts; people post in Slack themselves", 403)
    if H.is_bot(actor) and actor != H.bot_actor(bot):
        raise Problem("forbidden", "A bot posts to Slack as itself", 403)
    payload["bot"] = bot
    payload["text"] = SB.check_text(str(payload.get("text") or ""))
    payload["thread_ts"] = str(payload.get("thread_ts") or "")
    if H.is_bot(actor) and settings is not None:
        SB.need(c, bot, SB.slack_entry(c, settings, bot), "post")
        SB.not_onboarding(c, bot)
    entry = SB.postable(c, settings, destination)
    return "internal", entry["id"]


def _slack_dispatch(store, row):
    """Hand the post to the gateway's outbox (slack_posts, sent by backend/slack_gateway.py) in one transaction with
    the handoff mark, so the post exists exactly when the row says it was handed off. The message carries the row and
    its occurrence key; the gateway settles the row from its sent or uncertain result."""
    from . import slack_bots as SB

    payload = _load(row["payload"], {})
    try:
        with store.transaction() as c:
            entry = SB.postable(c, store.settings, row["destination"])
            post = SB.queue_post(
                c,
                payload["bot"],
                entry,
                payload.get("thread_ts") or "",
                payload["text"],
                refs={"outbound_action": row["id"], "occurrence_key": row["occurrence_key"]},
            )
            if not hand_off(c, row, {"message_id": post["message_id"], "channel": entry["id"]}):
                raise LostClaim(row["id"])
    except Problem as exc:
        raise Refused(exc.detail) from None
    return HandedOff({"message_id": post["message_id"]})


def _slack_reconcile(c, row):
    """The gateway's answer for the post this row handed off. No handoff recorded means none happened (the post and
    the mark commit together): safe to try again."""
    mid = _load(row["receipt"], {}).get("message_id")
    if not mid:
        return Settled("queued")
    post = c.execute("SELECT state,slack_ts,error FROM slack_posts WHERE message_id=?", (mid,)).fetchone()
    if not post:
        return Settled("failed", error="The Slack post is gone")
    state, error = post["state"], str(post["error"] or "")
    if state == "sent":
        return Settled("done", {"slack_ts": post["slack_ts"]})
    if state == "uncertain":
        return Settled("unknown", error=error or "Slack did not confirm the post")
    if state == "failed":
        return Settled("failed", error=error)
    if state == "canceled":
        # Shared outside the workspace since it was queued: held, not dropped.
        return Settled("blocked" if "Slack Connect" in error else "canceled", error=error)
    return None  # ready, sending, rate limited: the gateway still has it


register(Adapter("slack", _slack_prepare, _slack_dispatch, _slack_reconcile))


def from_message(c, message_id) -> None:
    """The gateway recorded a post's outcome (in its own transaction): the outbound row it came from settles now."""
    message = H.message(c, message_id) or {}
    action = (message.get("refs") or {}).get("outbound_action")
    if action:
        settle_from(c, action)


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


def policy_view(c) -> dict[str, Any]:
    stored = company_policy(c)
    effective = {f"{ch}:{aud}": stored.get(f"{ch}:{aud}") or DEFAULTS[aud] for ch in CHANNELS for aud in AUDIENCES}
    return {"settings": stored, "effective": effective, "choices": list(CHOICES), "channels": sorted(ADAPTERS)}


def install(app, store, auth, mutate):
    from .api_answers.outbound_actions import (
        OutboundActionDetail,
        OutboundActionList,
        OutboundActionResult,
        OutboundPolicy,
    )

    def _admin(c, who) -> bool:
        """The owner or an admin, as a person (auth.bot_admin)."""
        return who.role in ("owner", "human") and bool(auth.bot_admin(who))

    def _visible(c, who, row) -> bool:
        return row["actor"] == who.actor or _admin(c, who)

    def one(c, who, oid):
        row = get(c, oid)
        if not row or not _visible(c, who, row):
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
    def get_policy(request: Request):
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
    def set_policy(request: Request, body: OutboundPolicyUpdate):
        from .mail_settings import require_person

        who = request.state.identity
        require_person(who)
        for key, value in body.settings.items():
            channel, _, audience = key.partition(":")
            if channel not in CHANNELS or audience not in AUDIENCES:
                raise Problem("validation", f"{key!r} is not <channel>:<audience> ({', '.join(CHANNELS)})", 422)
            if value is not None and value not in CHOICES:
                raise Problem("validation", f"{value!r} is not one of {', '.join(CHOICES)}", 422)

        def work(c):
            if not _admin(c, who):
                raise Problem("forbidden", "Only the owner or an admin sets the outbound policy", 403)
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
            "Queue an outbound action through the gate; the same occurrence_key answers the existing one",
            OutboundActionResult,
        ),
    )
    def create(request: Request, body: OutboundActionCreate):
        who = request.state.identity

        def work(c):
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
                task_id=body.task_id,
                send_after=body.send_after,
            )
            if not _visible(c, who, row):
                raise Problem("conflict", "This occurrence_key belongs to another action", 409)
            return {"action": public(c, row), "created": created}

        return mutate(request, body, work, durable=True)

    @app.get(
        "/api/v2/outbound-actions",
        **api_v2(
            "Outbound actions",
            "listOutboundActions",
            "Outbound actions you made (all of them for the owner or an admin), newest first",
            OutboundActionList,
        ),
    )
    def listing(request: Request, state: str = "", channel: str = "", cursor: str = "", limit: int = 50):
        who = request.state.identity
        if state and state not in STATES:
            raise Problem("validation", f"state is one of {', '.join(STATES)}", 422)
        cap = min(100, max(1, limit))
        with store.read() as c:
            sql, args = "SELECT * FROM outbound_actions WHERE 1=1", []
            if not _admin(c, who):
                sql += " AND actor=?"
                args.append(who.actor)
            if state:
                sql += " AND state=?"
                args.append(state)
            if channel:
                sql += " AND channel=?"
                args.append(channel)
            if cursor:
                at = c.execute("SELECT created_at FROM outbound_actions WHERE id=?", (cursor,)).fetchone()
                if at:
                    sql += " AND (created_at<? OR (created_at=? AND id<?))"
                    args += [at[0], at[0], cursor]
            rows = c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, cap + 1)).fetchall()
            items = [public(c, r) for r in rows[:cap]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > cap and items else None}

    @app.get(
        "/api/v2/outbound-actions/{oid}",
        **api_v2(
            "Outbound actions",
            "getOutboundAction",
            "One outbound action with its frozen payload, and the shown_hash its OK sends back",
            OutboundActionDetail,
        ),
    )
    def detail(request: Request, oid: str):
        who = request.state.identity
        with store.transaction() as c:
            row = one(c, who, oid)
            shown = None
            if row["state"] == "waiting_approval" and row["approval_id"]:
                shown = task_asks.show(
                    c, who.actor, row["approval_id"], content(row), own_click=task_asks.own_click(who)
                )
            return {"action": public(c, row, payload=True), "shown_hash": shown}

    @app.post(
        "/api/v2/outbound-actions/{oid}/approve",
        **api_v2(
            "Outbound actions",
            "approveOutboundAction",
            "OK exactly the action you were shown, by your own click (the person it is asked of)",
            OutboundActionResult,
        ),
    )
    def approve(request: Request, oid: str, body: OutboundApprove):
        who = request.state.identity
        ctx = task_asks.Context(auth, auth.settings, who, app)

        def work(c):
            row = get(c, oid)
            task_id = task_asks.task_of(c, row["approval_id"]) if row and row["approval_id"] else None
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
            "Cancel an action that has not gone (or whose outcome is unknown): whoever made it, the owner or an admin",
            OutboundActionResult,
        ),
    )
    def cancel(request: Request, oid: str):
        who = request.state.identity

        def work(c):
            row = one(c, who, oid)
            if row["state"] == "canceled":
                return {"action": public(c, row), "created": False}
            if row["state"] not in OPEN:
                raise Problem("state", f"This action is {row['state']}: it can no longer be canceled", 409)
            settle(c, row, "canceled", error=f"Canceled by {H.actor_name(c, who.actor)}")
            H.event(c, who.actor, "outbound.canceled", oid, {"channel": row["channel"]})
            return {"action": public(c, get(c, oid)), "created": False}

        return mutate(request, Empty(), work, durable=True)
