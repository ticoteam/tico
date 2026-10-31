"""The bank and card sync worker (spec §4.6, §9): one daemon thread per server, modeled on backend/webhook_delivery.py
and gateway_sync.py. Each step reads or claims in a short transaction, calls Mercury outside any transaction, then
records what came back in a new one. A doorbell wakes it after a commit (a webhook arrived, a payment was queued); a
slow sweep covers a missed ring and Mercury's sandbox, which has no webhooks, so polling alone is enough.

Per connection, in order: balances (with their as-of); the first backfill from /transactions by cursor, paced; the
Events API from its cursor (90 days kept) into fin_inbox, deduped with webhook deliveries by event id; the inbox, each
transaction event read whole and taken in once by its version; the outbox (recipients, cards); payments (send, find
an unknown one by its key, poll Mercury's approval queue); statements once a day. A rehearsal copy calls nothing and
marks what it would send as withheld. The token is a Tico credential read at use time."""
import logging
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import banking, payments
from .context import is_set_up, load
from .core import feeds as F
from .core.errors import FinanceError
from .core.org import SYSTEM
from .gateway_sync import Bell
from .mercury import Answer, Mercury, Provider, error_text, listed, next_page
from .store import banking as SK
from .store import tico

log = logging.getLogger(__name__)

SWEEP = 300.0                     # seconds between sweeps without a ring
POLL_MINUTES = 15                 # how often a connection is read without a webhook
BACKFILL_PAGES = 4                # /transactions pages per step: the first backfill is paced across steps
EVENTS_PAGES = 4
INBOX_MOST, OUTBOX_MOST, PAY_MOST = 200, 50, 50
STATEMENT_HOURS = 24
RETRY = (60, 300, 1800, 3600)
NO_KEY_OPS = ("card_create", "recipient_create")   # Mercury documents no idempotency for these creates
Make = Callable[[str], Provider]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _after(at: str, seconds: float) -> str:
    moment = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _settings(row: Any) -> dict[str, Any]:
    return SK.loads(row["settings_json"])


# ------------------------------------------------------------------------------------------------ connect probe
def probe(provider: Provider) -> tuple[dict[str, object], str]:
    """What connection_create needs, read with the token before the action: accounts (last 4 only, never the full
    number), IO credit accounts and Mercury's users. ({} , why) when the token does not work."""
    accounts = provider.accounts()
    if not accounts.ok:
        return {}, error_text(accounts)
    credit, users = provider.credit(), provider.users()
    rows: list[dict[str, object]] = []
    for a in listed(accounts, "accounts"):
        if str(a.get("type") or "mercury") != "mercury":
            continue
        rows.append({"id": str(a.get("id")), "name": str(a.get("nickname") or a.get("name") or "Mercury")[:120],
                     "kind": str(a.get("kind") or ""), "status": str(a.get("status") or "active"),
                     "last4": F.last4(a.get("accountNumber")), "current": _cents(a.get("currentBalance")),
                     "available": _cents(a.get("availableBalance"))})
    found: dict[str, object] = {"accounts": rows, "credit": [
        {"id": str(a.get("id")), "status": str(a.get("status") or "active"), "current": _cents(a.get("currentBalance")),
         "available": _cents(a.get("availableBalance"))} for a in listed(credit, "accounts")] if credit.ok else []}
    if users.ok:
        found["users"] = [{"email": str(u.get("email") or "").lower()} for u in listed(users, "users")]
    return found, ""


def probe_credential(store: Any, vault: Any, credential_id: str, make: Make | None = None
                     ) -> tuple[dict[str, object], str]:
    """Read the token from its credential (outside any transaction) and probe Mercury with it."""
    with store.read() as c:
        if not tico.credential_exists(c, credential_id) and make is None:
            return {}, "no such credential with a stored token"
    provider = _provider(store, vault, credential_id, make)
    if provider is None:
        return {}, "no such credential with a stored token"
    return probe(provider)


def _cents(value: object) -> int | None:
    try:
        return F.cents(value) if value is not None else None
    except FinanceError:
        return None


# ------------------------------------------------------------------------------------------------ webhooks
def delivery(c: Any, connection_id: str | None, body: Mapping[str, object], at: str | None = None) -> bool:
    """A verified Mercury webhook delivery (backend/triggers.py checked its signature): kept once in fin_inbox by
    event id and the worker rung. False for a replay."""
    eid = str(body.get("id") or "")
    if not eid or not is_set_up(c):
        return False
    kept = SK.inbox_add(c, connection_id=connection_id, provider="mercury", event_id=eid, payload=body,
                        at=at or _now())
    if kept:
        tico.after_commit(c, ring)
    return kept


# ------------------------------------------------------------------------------------------------ one step
def step(store: Any, vault: Any, *, rehearsal: bool = False, make: Make | None = None, at: str | None = None) -> None:
    """One pass over every connection that is due, or rung."""
    with store.read() as c:
        if not is_set_up(c):
            return
        rows = SK.connections(c, "mercury")
    for row in rows:
        if str(row["status"]) not in ("active", "error"):
            continue
        try:
            connection(store, vault, str(row["id"]), rehearsal=rehearsal, make=make, at=at)
        except Exception:
            log.exception("Finance sync step failed")


def _provider(store: Any, vault: Any, ref: str, make: Make | None) -> Provider | None:
    with store.read() as c:
        token = tico.credential_secret(c, vault, ref) if vault is not None else ""
    if make is not None:
        return make(token)
    return Mercury(token) if token else None


def connection(store: Any, vault: Any, cid: str, *, rehearsal: bool = False, make: Make | None = None,
               at: str | None = None) -> str:
    """Sync one connection. `withheld` in rehearsal, `no_token`, else `ok` or `failed`."""
    now = at or _now()
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
        inbox = len(SK.inbox_waiting(c, 1)) > 0
        owed = len(SK.outbox_due(c, now, 1)) > 0 or len(SK.payments_due(c, now, 1)) > 0
    if row is None:
        return "gone"
    if rehearsal:
        with store.transaction() as c:
            for o in SK.outbox_due(c, now, OUTBOX_MOST):
                SK.update(c, "fin_outbox", str(o["id"]), {"status": "withheld", "updated_at": now})
        return "withheld"
    due = not row["next_sync_at"] or str(row["next_sync_at"]) <= now
    if not (due or inbox or owed):
        return "idle"
    provider = _provider(store, vault, str(row["credential_ref"] or ""), make)
    if provider is None:
        _failed(store, cid, "the Mercury token is missing; store it again in Settings", now)
        return "no_token"
    problems: list[str] = []
    if due:
        problems += balances(store, provider, cid, now)
        problems += backfill(store, provider, cid, now)
        problems += events(store, provider, cid, now)
    problems += process_inbox(store, provider, cid, now)
    problems += outbox(store, provider, now)
    problems += pay(store, provider, now)
    if due:
        problems += statements(store, provider, cid, now)
    with store.transaction() as c:
        fields: dict[str, object] = {"last_sync_at": now, "updated_at": now}
        if due:
            fields["next_sync_at"] = _after(now, POLL_MINUTES * 60)
        if problems:
            fields.update({"status": "error", "last_error": problems[0][:300]})
            ctx = load(c, now)
            banking.event(ctx, "finance.sync.failed", cid, {"connection_id": cid}, now[:13])
        else:
            fields.update({"status": "active", "last_error": None, "last_ok_at": now})
        SK.update(c, "fin_connections", cid, fields)
    return "failed" if problems else "ok"


def _failed(store: Any, cid: str, why: str, now: str) -> None:
    with store.transaction() as c:
        SK.update(c, "fin_connections", cid, {"status": "error", "last_error": why, "updated_at": now,
                                              "next_sync_at": _after(now, POLL_MINUTES * 60)})


def balances(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    accounts, credit = provider.accounts(), provider.credit()
    if not accounts.ok:
        return [error_text(accounts)]
    found = {str(a.get("id")): a for a in listed(accounts, "accounts")}
    if credit.ok:
        found.update({str(a.get("id")): a for a in listed(credit, "accounts")})
    with store.transaction() as c:
        ctx = load(c, now)
        for m in SK.money_accounts(c, ctx.book.id):
            if str(m["connection_id"] or "") != cid:
                continue
            a = found.get(str(m["provider_account_id"] or ""))
            if a is not None:
                banking.set_balance(ctx, str(m["id"]), _cents(a.get("currentBalance")),
                                    _cents(a.get("availableBalance")), now)
    return []


def _ingest_all(store: Any, cid: str, raws: list[dict[str, Any]], now: str, version: int = 0) -> None:
    with store.transaction() as c:
        ctx = load(c, now)
        for raw in raws:
            try:
                banking.ingest(ctx, cid, F.mercury_txn(raw, version))
            except FinanceError as e:
                log.warning("Finance skipped a Mercury transaction: %s", e.message)


def backfill(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    """The first backfill: /transactions oldest first from its cursor, a few pages per step. When it ends, the Events
    cursor starts at Mercury's newest event, so catch-up reads only what came after."""
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
    conf = _settings(row)
    if conf.get("backfilled"):
        return []
    if not row["events_cursor"]:
        newest = provider.events(None, 1, newest=True)
        first = listed(newest, "events")
        with store.transaction() as c:
            SK.update(c, "fin_connections", cid, {"events_cursor": str(first[0].get("id")) if first else "start"})
    cursor = str(row["cursor"] or "") or None
    for _ in range(BACKFILL_PAGES):
        page = provider.transactions(cursor)
        if not page.ok:
            return [error_text(page)]
        raws = listed(page, "transactions")
        _ingest_all(store, cid, raws, now)
        cursor = next_page(page) or (str(raws[-1].get("id")) if raws else cursor)
        done = not raws or not next_page(page) and len(raws) < 500
        with store.transaction() as c:
            SK.update(c, "fin_connections", cid, {"cursor": cursor, "settings_json": SK.dump(
                {**conf, "backfilled": True}) if done else row["settings_json"]})
        if done:
            break
    return []


def events(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    """Catch-up from the Events API: every event after the cursor goes to the inbox (a webhook already kept is a
    replay and ignored there)."""
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
    if not _settings(row).get("backfilled"):
        return []
    cursor = str(row["events_cursor"] or "")
    for _ in range(EVENTS_PAGES):
        page = provider.events(None if cursor in ("", "start") else cursor)
        if not page.ok:
            return [error_text(page)]
        found = listed(page, "events")
        with store.transaction() as c:
            for e in found:
                if e.get("id"):
                    SK.inbox_add(c, connection_id=cid, provider="mercury", event_id=str(e["id"]), payload=e, at=now)
            if found:
                cursor = str(found[-1].get("id"))
                SK.update(c, "fin_connections", cid, {"events_cursor": cursor})
        if not found or not next_page(page):
            break
    return []


def process_inbox(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    """Each waiting event: a transaction event reads the transaction whole and takes it in by its version (a
    replayed or older event changes nothing); other events refresh nothing more than balances already did."""
    with store.read() as c:
        waiting = [r for r in SK.inbox_waiting(c, INBOX_MOST) if str(r["connection_id"] or cid) == cid]
    for r in waiting:
        body = SK.loads(r["payload_json"])
        kind = str(body.get("resourceType") or "")
        if kind != "transaction":
            with store.transaction() as c:
                SK.inbox_done(c, str(r["id"]), "ignored", now)
            continue
        got = provider.transaction(str(body.get("resourceId") or ""))
        if not got.ok and got.status != 404:
            return [error_text(got)]
        with store.transaction() as c:
            if got.ok:
                ctx = load(c, now)
                version = int(str(body.get("resourceVersion") or 0)) if str(body.get("resourceVersion") or "0"
                                                                           ).isdigit() else 0
                try:
                    banking.ingest(ctx, cid, F.mercury_txn(got.body, version))
                except FinanceError as e:
                    log.warning("Finance skipped a Mercury transaction: %s", e.message)
            SK.inbox_done(c, str(r["id"]), "done" if got.ok else "gone", now)
    return []


# ------------------------------------------------------------------------------------------------ writes
def outbox(store: Any, provider: Provider, now: str) -> list[str]:
    with store.read() as c:
        due = SK.outbox_due(c, now, OUTBOX_MOST)
    problems: list[str] = []
    for o in due:
        body = SK.loads(o["payload_json"])
        op = str(o["op"])
        if op == "recipient_create":
            answer = provider.create_recipient(_recipient_body(body))
        elif op == "card_create":
            answer = provider.create_card(str(body.get("user_id")), str(body.get("nickname")), int(body["monthly"]))
        elif op == "card_limit":
            with store.read() as c:
                card = SK.get(c, "fin_cards", str(body.get("card_id")))
            if card is None or not card["provider_card_id"]:
                answer = Answer(None, {})
            else:
                answer = provider.card_limit(str(card["provider_card_id"]), int(body["monthly"]))
        elif op == "card_state":
            with store.read() as c:
                card = SK.get(c, "fin_cards", str(body.get("card_id")))
            answer = provider.card_state(str(card["provider_card_id"]), str(body["op"])) if card is not None and \
                card["provider_card_id"] else Answer(None, {})
        else:
            answer = Answer(400, {"message": f"unknown op {op}"})
        with store.transaction() as c:
            problem = _record(c, o, body, answer, now)
        if problem:
            problems.append(problem)
    return problems


def _recipient_body(body: Mapping[str, Any]) -> dict[str, object]:
    out: dict[str, object] = {"name": body.get("name"), "emails": [body["email"]] if body.get("email") else []}
    address = cast(dict[str, Any], body.get("address") or {})
    if body.get("method") == "check":
        out["checkInfo"] = {"address": address}
    elif body.get("method") == "wire":
        out["domesticWireRoutingInfo"] = {"accountNumber": body.get("accountNumber"),
                                          "routingNumber": body.get("routingNumber"), "address": address}
    else:
        out["electronicRoutingInfo"] = {"accountNumber": body.get("accountNumber"),
                                        "routingNumber": body.get("routingNumber"),
                                        "electronicAccountType": body.get("account_type") or "businessChecking",
                                        "address": address}
    return out


def _record(c: Any, o: Any, body: Mapping[str, Any], answer: Answer, now: str) -> str:
    """What came back for one outbox write. Success records the provider's id (a recipient's payment details are
    erased from the outbox then); a refused write waits (the IP allowlist) or fails."""
    attempts = int(o["attempts"]) + 1
    op = str(o["op"])
    if answer.ok:
        if op == "recipient_create":
            SK.update(c, "fin_recipients", str(body["recipient_id"]), {
                "provider_recipient_id": str(answer.body.get("id") or ""), "updated_at": now})
            kept = {"recipient_id": body["recipient_id"]}
            SK.update(c, "fin_outbox", str(o["id"]), {"payload_json": SK.dump(kept)})
        elif op == "card_create":
            SK.update(c, "fin_cards", str(body["card_id"]), {
                "provider_card_id": str(answer.body.get("cardId") or answer.body.get("id") or ""),
                "last4": str(answer.body.get("lastFourDigits") or "")[-4:] or None,
                "provider_limit": int(body["monthly"]), "sync_status": "ok", "synced_at": now, "updated_at": now})
        elif op == "card_limit":
            SK.update(c, "fin_cards", str(body["card_id"]), {"provider_limit": int(body["monthly"]),
                                                             "sync_status": "ok", "synced_at": now})
        elif op == "card_state":
            SK.update(c, "fin_cards", str(body["card_id"]), {"sync_status": "ok", "synced_at": now})
        SK.update(c, "fin_outbox", str(o["id"]), {"status": "sent", "attempts": attempts, "last_error": None,
                                                  "updated_at": now})
        return ""
    why = error_text(answer, write=True)
    final = answer.status is not None and 400 <= answer.status < 500 and not answer.refused_write and \
        answer.status != 429
    if op in NO_KEY_OPS and (answer.status is None or answer.status >= 500):
        # Mercury takes no idempotency key for these: a call that may have landed is never sent again on its own.
        final, why = True, f"{why}; it may have gone through: check Mercury before trying again"
    SK.update(c, "fin_outbox", str(o["id"]), {
        "status": "failed" if final else "pending", "attempts": attempts, "last_error": why,
        "next_try_at": None if final else _after(now, RETRY[min(attempts - 1, len(RETRY) - 1)]), "updated_at": now})
    if op.startswith("card_") and body.get("card_id"):
        SK.update(c, "fin_cards", str(body["card_id"]), {"sync_status": "failed" if final else "pending",
                                                         "sync_error": why})
    return why


def pay(store: Any, provider: Provider, now: str) -> list[str]:
    """Payments the worker owes a call: send queued ones (after the recheck), find unknown ones by their key, poll the
    ones in Mercury's approval queue."""
    with store.read() as c:
        due = SK.payments_due(c, now, PAY_MOST)
    for p in due:
        status = str(p["status"])
        with store.read() as c:
            money = SK.get(c, "fin_money_accounts", str(p["money_account_id"]))
            rec = SK.get(c, "fin_recipients", str(p["recipient_id"]))
        account = str(money["provider_account_id"]) if money is not None else ""
        if status in ("awaiting_bank_approval", "submitted") and p["provider_request_id"]:
            answer = provider.send_request(str(p["provider_request_id"]))
            with store.transaction() as c:
                payments.polled(load(c, now), SK.get(c, "fin_payments", str(p["id"])), answer)
            continue
        if status == "unknown":
            listing = provider.send_requests(account)
            if listing.ok:
                with store.transaction() as c:
                    ctx = load(c, now)
                    if payments.found(ctx, SK.get(c, "fin_payments", str(p["id"])), list(listed(listing,
                                                                                                "requests"))):
                        continue
            elif listing.status is None:
                continue
        if status in ("queued", "unknown"):
            with store.transaction() as c:
                ctx = load(c, now)
                row = SK.get(c, "fin_payments", str(p["id"]))
                why = payments.precheck(ctx, row) if str(row["status"]) == "queued" else ""
            if why or rec is None or not rec["provider_recipient_id"]:
                continue
            answer = provider.request_send_money(account, payments.request_body(p, rec))
            with store.transaction() as c:
                payments.sent(load(c, now), SK.get(c, "fin_payments", str(p["id"])), answer)
    return []


def statements(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    """Statements for reconciliation, once a day: IO credit statements (what is owed, sign normalized once) and the
    last three months of depository statements (an ending balance only)."""
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
        ctx = load(c, now)
        mine = [m for m in SK.money_accounts(c, ctx.book.id) if str(m["connection_id"] or "") == cid]
    conf = _settings(row)
    if str(conf.get("statements_at") or "") > _after(now, -STATEMENT_HOURS * 3600):
        return []
    start = (datetime.fromisoformat(now[:10]) - timedelta(days=89)).date().isoformat()
    for m in mine:
        pid = str(m["provider_account_id"] or "")
        program = str(m["kind"]) == "card_program"
        got = provider.credit_statements(pid) if program else provider.statements(pid, start, now[:10])
        if not got.ok:
            continue
        with store.transaction() as c:
            for s in listed(got, "statements"):
                try:
                    starting, ending = F.credit_owed(s) if program else (None, F.bank_ending(s))
                    SK.upsert_statement(c, money_account_id=str(m["id"]), provider_id=str(s.get("id") or ""),
                                        start=F.day_of(s.get("startDate") or s.get("endDate")),
                                        end=F.day_of(s.get("endDate")), starting=starting, ending=ending,
                                        currency=str(m["balance_currency"] or "USD"), source="api", by=SYSTEM, at=now)
                except FinanceError:
                    continue
    with store.transaction() as c:
        fresh = SK.get(c, "fin_connections", cid)
        SK.update(c, "fin_connections", cid, {"settings_json": SK.dump({**_settings(fresh), "statements_at": now})})
    return []


# ------------------------------------------------------------------------------------------------ the thread
BELL = Bell()
_worker: dict[str, Any] = {"thread": None, "stop": threading.Event(), "lock": threading.Lock(), "args": None}


def ring() -> None:
    BELL.ring()


def start(store: Any, vault: Any, *, rehearsal: bool = False, make: Make | None = None) -> None:
    """Start the thread (or restart it if it died). Safe to call again on every scheduler tick."""
    with _worker["lock"]:
        _worker["args"] = (store, vault, rehearsal, make)
        thread = cast(threading.Thread | None, _worker["thread"])
        if thread is not None and thread.is_alive():
            return
        if cast(threading.Event, _worker["stop"]).is_set():
            _worker["stop"] = threading.Event()
        made = threading.Thread(target=_loop, args=(store, vault, rehearsal, make, _worker["stop"]),
                                name="finance-banking", daemon=True)
        made.start()
        _worker["thread"] = made


def ensure() -> None:
    args = cast(tuple[Any, Any, bool, Make | None] | None, _worker["args"])
    if args and not cast(threading.Event, _worker["stop"]).is_set():
        store, vault, rehearsal, make = args
        start(store, vault, rehearsal=rehearsal, make=make)


def stop() -> None:
    cast(threading.Event, _worker["stop"]).set()
    BELL.ring()
    thread = cast(threading.Thread | None, _worker["thread"])
    if thread is not None:
        thread.join(5)
    _worker["thread"] = None


def _loop(store: Any, vault: Any, rehearsal: bool, make: Make | None, halt: threading.Event) -> None:
    while not halt.is_set():
        seen = BELL.rung
        try:
            step(store, vault, rehearsal=rehearsal, make=make)
        except Exception:
            log.exception("Finance sync failed")
        BELL.wait(seen, SWEEP)
