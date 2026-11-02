"""The books worker and the accounting webhooks (spec §3.4, §10). A relayed QuickBooks or Xero event is kept once in
fin_inbox by its event id, and the worker reads the provider's changes at once instead of at the next scheduled pass.
The worker runs the mirror's pass for this install's books connection, outside any transaction. In rehearsal it calls
no provider: the writes it would send are withheld and stay visible in the outbox (a rehearsal copy never pays)."""
import logging
import threading
from collections.abc import Mapping
from typing import Any

from .gateway_sync import Bell
from .core import mirror as M
from .core import qbo as Q
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


def _shaped(provider: str, payload: object) -> bool:
    """Whether a relayed payload is one HQ sends for the provider: QuickBooks sends eventNotifications (legacy) or
    cloudEvents, Xero sends events."""
    found = Q.as_map(payload)
    if found is None:
        return False
    if provider == "quickbooks":
        return isinstance(found.get("eventNotifications"), list) or isinstance(found.get("cloudEvents"), list)
    return isinstance(found.get("events"), list)


def _realms(payload: Mapping[str, Any]) -> set[str]:
    """The QuickBooks companies (realms) a payload's events name: a legacy notification's realmId, a CloudEvent's
    intuitaccountid."""
    out: set[str] = set()
    for note in Q.as_list(payload.get("eventNotifications")):
        found = Q.as_map(note)
        if found is not None and found.get("realmId"):
            out.add(str(found["realmId"]))
    for event in Q.as_list(payload.get("cloudEvents")):
        found = Q.as_map(event)
        if found is not None and found.get("intuitaccountid"):
            out.add(str(found["intuitaccountid"]))
    return out


def delivery(c: Any, preset: str, body: Mapping[str, object]) -> str:
    """A relayed accounting webhook (backend/triggers.py routes it here): kept once by event id, and the worker rung.
    Refused when the envelope is not one HQ sends for this provider, the payload has no event list it knows, or it names
    a company no books connection holds (or, for QuickBooks, a realm other than the connection's)."""
    provider = PROVIDERS.get(preset, "")
    eid = str(body.get("external_id") or "")
    company = str(body.get("company_id") or "")
    payload = body.get("payload")
    if (not provider or body.get("type") != ENVELOPE or body.get("provider") != provider or not eid or not company
            or not _shaped(provider, payload)):
        return REFUSED
    held = [cid for cid, _, co in SX.books_connections(c, provider) if co == company]
    if len(held) != 1:
        return REFUSED
    realm = str(SX.books_settings(c, held[0]).get("realm_id") or "")
    if provider == "quickbooks" and realm and _realms(Q.as_map(payload) or {}) - {realm}:
        return REFUSED
    kept = SK.inbox_add(c, connection_id=held[0], provider=provider, event_id=eid, payload=body, at=tico.now())
    if kept:
        tico.after_commit(c, ring)
    return KEPT if kept else SEEN


def pass_once(store: Any, vault: object, settings: Any, *, now: str) -> list[str]:
    """One pass: the Gateway relays of the books connections that lack one are made first (books_link.ensure_relays), then
    the books pass runs. Returns what the pass recorded."""
    return books_link.ensure_relays(store, vault, settings, now=now) + _sync_pass(store, vault, settings, now=now)


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
