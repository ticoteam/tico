"""The books worker and the accounting webhooks (spec §3.4, §10). A relayed QuickBooks or Xero event is kept once in
fin_inbox by its event id, and the worker reads the provider's changes at once instead of at the next scheduled pass.
The worker runs the mirror's pass for this install's books connection, outside any transaction. In rehearsal it calls
no provider: the writes it would send are withheld and stay visible in the outbox (a rehearsal copy never pays)."""
import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .gateway_sync import Bell
from .core import mirror as M
from .store import banking as SK
from .store import ledger as SL
from .store import mirror as SX
from .store import tico
from . import books_link, mirror, qbo, xero

log = logging.getLogger(__name__)
PROVIDERS = {"intuit": "quickbooks", "xero": "xero"}      # a trigger's preset -> the provider's name
BOOKS = ("quickbooks", "xero")                                    # the providers a books connection can be
ENVELOPE = "tico.finance.webhook"                          # the body HQ relays for one provider event
REFUSED, KEPT, SEEN = "refused", "kept", "seen"
SWEEP = 300.0                                              # seconds between passes without a ring
REHEARSAL_WHY = "rehearsal copy: the write is held here and never sent to the provider"
BELL = Bell()
_worker: dict[str, Any] = {"thread": None, "stop": threading.Event(), "lock": threading.Lock(), "args": None}


def ring() -> None:
    BELL.ring()


def _maps(values: list[Any]) -> list[dict[str, Any]]:
    return [v for v in values if isinstance(v, dict)]


def _dict(value: Any) -> dict[str, Any]:
    """A JSON object, as a dict of Any (empty when the value is not one)."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    """A JSON array, as a list of Any (empty when the value is not one)."""
    return cast(list[Any], value) if isinstance(value, list) else []


def _cloud(event: Mapping[str, Any]) -> bool:
    """A CloudEvents event: the spec makes its specversion a string."""
    return isinstance(event.get("specversion"), str)


def _events(provider: str, payload: object) -> list[dict[str, Any]] | None:
    """The events a relayed payload holds, in a shape HQ relays for the provider; None when it is in none this reads (the
    delivery is refused and nothing is woken). QuickBooks sends a legacy notification object (eventNotifications), one
    CloudEvents event (an object with a specversion), a CloudEvents batch (a JSON array of them), or a cloudEvents list.
    Xero sends an events list."""
    if provider != "quickbooks":
        found = _dict(payload)
        return _maps(found["events"]) if isinstance(found.get("events"), list) else None
    if isinstance(payload, list):
        raw = _list(payload)
        events = _maps(raw)
        return events if events and len(events) == len(raw) and all(_cloud(e) for e in events) else None
    if not isinstance(payload, dict):
        return None
    found = _dict(payload)
    if isinstance(found.get("eventNotifications"), list):
        return _maps(found["eventNotifications"])
    if isinstance(found.get("cloudEvents"), list):
        return _maps(found["cloudEvents"])
    return [found] if _cloud(found) else None


def _realms(events: Sequence[Mapping[str, Any]]) -> set[str]:
    """The QuickBooks companies (realms) the events name: a legacy notification's realmId, a CloudEvent's intuitaccountid."""
    out: set[str] = set()
    for event in events:
        realm = event.get("realmId") or event.get("intuitaccountid")
        if realm:
            out.add(str(realm))
    return out


def _shape_of(payload: object) -> str:
    """A payload's shape for a log line: its keys or length, never its contents."""
    if isinstance(payload, dict):
        return "an object with keys " + ", ".join(sorted(str(k) for k in _dict(payload))[:8])
    if isinstance(payload, list):
        return f"a list of {len(_list(payload))}"
    return type(payload).__name__


def delivery(c: Any, preset: str, body: Mapping[str, object]) -> str:
    """A relayed accounting webhook (backend/triggers.py routes it here): kept once by event id, and the worker rung.
    Refused when the envelope is not one HQ sends for this provider, the payload is in no shape this reads (logged, and
    nothing is woken), or it names a company no books connection holds (or, for QuickBooks, a realm other than the
    connection's). The worker reads the change feed, not the events, so the payload's detail is not kept here."""
    provider = PROVIDERS.get(preset, "")
    eid = str(body.get("external_id") or "")
    company = str(body.get("company_id") or "")
    payload = body.get("payload")
    if not provider or body.get("type") != ENVELOPE or body.get("provider") != provider or not eid or not company:
        return REFUSED
    events = _events(provider, payload)
    if events is None:
        log.warning("A relayed %s delivery (%s) has a payload in no shape this reads (%s): refused, nothing woken",
                    provider, eid[:80], _shape_of(payload))
        return REFUSED
    held = [cid for cid, _, co in SX.books_connections(c, provider) if co == company]
    if len(held) != 1:
        return REFUSED
    realm = str(SX.books_settings(c, held[0]).get("realm_id") or "")
    if provider == "quickbooks" and realm and _realms(events) - {realm}:
        return REFUSED
    kept = SK.inbox_add(c, connection_id=held[0], provider=provider, event_id=eid, payload=body, at=tico.now())
    if kept:
        tico.after_commit(c, ring)
    return KEPT if kept else SEEN


def pass_once(store: Any, vault: object, settings: Any, *, now: str) -> list[str]:
    """One pass: the books connections' triggers that are missing are made first (books_link.ensure_triggers), then the
    grants of stopped books are taken off, then the books pass runs. Returns what the pass recorded."""
    books_link.ensure_triggers(store, now=now)
    return _revoke_stopped(store, vault, settings, now=now) + _sync_pass(store, vault, settings, now=now)


def _revoke_stopped(store: Any, vault: object, settings: Any, *, now: str) -> list[str]:
    """The grant of a book switched back to native is taken off at HQ here: the switch makes no HQ call, so the tokens
    stay until HQ has them off. Tried again on each pass, whatever mode the book is in. Returns what failed."""
    from ..hook_relay import link_of
    with store.read() as c:
        stopped = SX.stopped_books_grants(c)
        link = link_of(c, vault, settings) if stopped else None
    if not stopped:
        return []
    if link is None:
        return ["the Gateway key is not set, so the grants of stopped books cannot be taken off"]
    problems: list[str] = []
    for cid, provider in stopped:
        try:
            books_link.disconnect_books(store, vault, link, cid, provider, now=now)
        except books_link.BrokerFailed as failed:
            problems.append(f"The Tico Gateway did not take off the {books_link.LABEL.get(provider, provider)} grant of a "
                            f"stopped book ({failed.code})")
    return problems


def _sync_pass(store: Any, vault: object, settings: Any, *, now: str) -> list[str]:
    """The books pass for this install's books connection, when its book is a provider's. A webhook waiting means the
    change feed is read now; the events it waited on are done once that read lands."""
    from ..hook_relay import link_of
    with store.read() as c:
        book = SL.book(c)
        if book is None or book.mode != "mirror" or not book.mirror_connection_id:
            return []
        cid = book.mirror_connection_id
        held = [row for row in SX.books_connections(c) if row[0] == cid]
        if not held or held[0][1] not in BOOKS or SX.books_status(c, cid) not in ("active", "error"):
            return []
        provider = held[0][1]
        link = link_of(c, vault, settings)
        found = SX.books_settings(c, cid)
        realm, tenant = found.get("realm_id"), found.get("tenant_id")
        location = str(SL.setting(c, mirror.XERO_LOCATION_SETTING) or "")
        waiting = [str(row["id"]) for row in SK.inbox_waiting(c, 50, cid, now)]
        held_until = str(SX.mirror_state(c, cid).get("held_until") or "") if provider == "xero" else ""
    if held_until and M.parse(held_until) > M.parse(now):
        return [f"Xero is rate limiting; the books wait until {held_until}"]
    if link is None:
        return ["the Gateway key is not set, so the books cannot be reached"]

    def token(force: bool) -> str:
        return books_link.access_token(store, vault, link, cid, provider, now=now, force=force)
    if provider == "xero":
        if not tenant:
            return ["the Xero organisation is not known yet; connect Xero again"]
        adapter: mirror.Books = xero.Xero(str(tenant), token, location=location)
    else:
        adapter = qbo.QuickBooks(str(realm or ""), token)
    every = xero.CHANGE_EVERY if provider == "xero" else M.CDC_EVERY
    problems = mirror.sync_once(store, adapter, cid, now, waiting=bool(waiting), every=every)
    if provider == "xero":
        hold = float(getattr(adapter, "hold", 0.0) or 0.0)
        if hold or held_until:
            with store.transaction() as c:
                SX.set_mirror_state(c, cid, {"held_until": M.stamp(now, int(hold * 1_000_000)) if hold else ""})
    if waiting and not any(p.startswith("changes") for p in problems):
        with store.transaction() as c:
            for inbox_id in waiting:
                SK.inbox_done(c, inbox_id, "done", now)
    return problems


def start(store: Any, vault: object, settings: Any, *, rehearsal: bool = False) -> None:
    """Start the worker (or restart it if it died). Safe to call on every scheduler tick."""
    tico.use_vault(vault)
    with _worker["lock"]:
        _worker["args"] = (store, vault, settings, rehearsal)
        thread = _worker["thread"]
        if thread is not None and thread.is_alive():
            return
        if _worker["stop"].is_set():
            _worker["stop"] = threading.Event()
        made = threading.Thread(target=_loop, args=(store, vault, settings, rehearsal, _worker["stop"]),
                                name="finance-books", daemon=True)
        made.start()
        _worker["thread"] = made


def stop() -> None:
    _worker["stop"].set()
    BELL.ring()
    thread = _worker["thread"]
    if thread is not None:
        thread.join(5)
    _worker["thread"] = None


def withhold(store: Any, *, now: str) -> int:
    """Rehearsal: the writes a mirrored book would send to its provider are withheld, not sent, and stay visible in the
    outbox with that reason. Returns how many were withheld."""
    with store.transaction() as c:
        book = SL.book(c)
        if book is None or book.mode != "mirror" or not book.mirror_connection_id:
            return 0
        return SX.withhold_writes(c, book.mirror_connection_id, REHEARSAL_WHY, now)


def _reading(store: Any) -> bool:
    """Whether this install's mirrored books are still being read in full: then the next page is read at once."""
    with store.read() as c:
        book = SL.book(c)
        if book is None or book.mode != "mirror" or not book.mirror_connection_id:
            return False
        return mirror.reading(c, book.mirror_connection_id)


def _loop(store: Any, vault: object, settings: Any, rehearsal: bool, halt: threading.Event) -> None:
    while not halt.is_set():
        seen = BELL.rung
        more = False
        try:
            if rehearsal:
                withhold(store, now=tico.now())
            else:
                problems = pass_once(store, vault, settings, now=tico.now())
                more = not problems and _reading(store)
        except Exception:
            log.exception("Finance books sync failed")
        if not more:
            BELL.wait(seen, SWEEP)
