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

from . import banking, cards, payments, stripe_sync, transfers
from .context import is_set_up, load
from .core import feeds as F
from .core.errors import FinanceError
from .core.org import SYSTEM
from .gateway_sync import Bell
from .mercury import Answer, Mercury, Provider, base_url, error_text, listed, next_page
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
# Never sent again on their own once a call may have landed. Mercury documents no idempotency for recipients and cards;
# a transfer is listed by choice: its key is sent, but an unknown result goes to a person, never to a resend.
NO_KEY_OPS = ("card_create", "recipient_create", "transfer_create")
CLAIM_MINUTES = 10                # a write claimed this long ago with no answer recorded was cut off mid-call
UNOWNED_HOURS = 24               # an event no connection could read (404) is retried this long before it is nobody's
MISSING = "404"                   # in an inbox row's tried list: some connection's read came back 404 this round
GONE_TRIES = 5                    # a transaction Mercury keeps answering 404 for is given up after this many reads
REQUEST_PAGES = 10                # pages of Mercury's approval requests read to find an unknown payment
# The documented filters of Mercury's request list: one list call each (it shows no sent status, so a sent request is
# found by its transaction, read back, not by this list).
REQUEST_STATUSES = ("pendingApproval", "approved", "rejected", "cancelled")
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
         "available": _cents(a.get("availableBalance")), "kind": str(a.get("kind") or "")}
        for a in listed(credit, "accounts")] if credit.ok else []}
    if users.ok:
        found["users"] = user_rows(users)
    return found, ""


def user_rows(users: Answer) -> list[dict[str, object]]:
    """Mercury's users as Finance keeps them: id, name, email (lowercased) and role."""
    return [{"id": str(u.get("id") or ""), "email": str(u.get("email") or "").lower(), "role": str(u.get("role") or ""),
             "name": " ".join(str(u.get(k) or "").strip() for k in ("firstName", "lastName")).strip()[:120]}
            for u in listed(users, "users")]


def provider_users(store: Any, provider: Provider, cid: str, now: str) -> None:
    """Refresh the card holder picker. A failed read keeps the last one and is not a sync failure."""
    users = provider.users()
    if users.ok:
        with store.transaction() as c:
            SK.set_provider_users(c, cid, user_rows(users), now)


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
    if connection_id is None:
        # A trigger is not tied to one connection: with one Mercury connection the event is its; with several, each
        # connection tries it with its own token and only the one that owns the account takes it in.
        live = [str(r["id"]) for r in SK.connections(c, "mercury") if str(r["status"]) in ("active", "error")]
        connection_id = live[0] if len(live) == 1 else None
    kept = SK.inbox_add(c, connection_id=connection_id, provider="mercury", event_id=eid, payload=body,
                        at=at or _now())
    if kept:
        tico.after_commit(c, ring)
    return kept


def stripe_delivery(c: Any, body: Mapping[str, object], at: str | None = None) -> bool:
    """A verified Stripe webhook (backend/triggers.py checked its signature), kept once in fin_inbox by event id and the
    worker rung. The worker reads the balance itself, so the event only says when to read. With one live Stripe
    connection the event is its; with none or several it is not kept."""
    eid = str(body.get("id") or "")
    if not eid or not is_set_up(c):
        return False
    live = [str(r["id"]) for r in SK.connections(c, "stripe") if str(r["status"]) in ("active", "error")]
    if len(live) != 1:
        return False
    kept = SK.inbox_add(c, connection_id=live[0], provider="stripe", event_id=eid, payload=body, at=at or _now())
    if kept:
        tico.after_commit(c, ring)
    return kept


stripe_probe = stripe_sync.probe_credential


# ------------------------------------------------------------------------------------------------ one step
def step(store: Any, vault: Any, *, rehearsal: bool = False, make: Make | None = None, at: str | None = None) -> None:
    """One pass over every connection that is due, or rung."""
    with store.read() as c:
        if not is_set_up(c):
            return
        rows = [r for r in SK.connections(c) if str(r["provider"]) in ("mercury", "stripe")]
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
    return Mercury(token, base=base_url()) if token else None


def connection(store: Any, vault: Any, cid: str, *, rehearsal: bool = False, make: Make | None = None,
               at: str | None = None) -> str:
    """Sync one connection. `withheld` in rehearsal, `no_token`, else `ok` or `failed`."""
    now = at or _now()
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
        inbox = len(SK.inbox_waiting(c, 1, cid, now)) > 0
        owed = len(SK.outbox_due(c, now, 1, cid)) > 0 or len(SK.payments_due(c, now, 1, cid)) > 0 or \
            len(SK.unknown_transfers(c, cid)) > 0 or \
            len(SK.outbox_stuck(c, _after(now, -CLAIM_MINUTES * 60), cid)) > 0
    if row is None or str(row["status"]) == "disconnected":
        return "gone"
    if str(row["provider"]) == "stripe":
        return stripe_sync.connection(store, vault, cid, rehearsal=rehearsal, make=make, at=now)
    if rehearsal:
        with store.transaction() as c:
            for o in SK.outbox_due(c, now, OUTBOX_MOST, cid):
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
    if due or inbox:       # balances come from webhooks (§9): an event waiting reads them now; the poll stays 15 minutes
        problems += balances(store, provider, cid, now)
    if due:
        provider_users(store, provider, cid, now)
        problems += uncertain_cards(store, provider, cid, now)
        problems += backfill(store, provider, cid, now)
        problems += events(store, provider, cid, now)
    problems += process_inbox(store, provider, cid, now)
    problems += outbox(store, provider, now, cid)
    problems += transfers_unknown(store, provider, now, cid)
    problems += pay(store, provider, now, cid)
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


CARD_PAGES = 20                   # Mercury card-list pages read for one user before giving up (500 a page)
LOOK_HOURS = 24                   # an uncertain card's candidates are looked up again after this long


def _user_cards(provider: Provider, user: str) -> tuple[list[dict[str, Any]], str]:
    """Every card Mercury lists for a user, page after page; a problem when the list could not be read to its end."""
    out: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(CARD_PAGES):
        got = provider.cards(user, after)
        if not got.ok:
            return out, error_text(got)
        out += listed(got, "cards")
        after = next_page(got)
        if not after:
            return out, ""
    return out, "Mercury lists more cards for this user than Finance reads"


def uncertain_cards(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    """Cards whose creation was cut off: Mercury's whole card list for each one's user gives a person candidates to
    confirm (card_resolve). Nothing is decided here: Mercury echoes nothing unique Finance set (a nickname is a
    person's to change), so neither a match nor an empty list proves anything; the reservation stays meanwhile."""
    with store.read() as c:
        waiting = [(str(k["id"]), str(SK.card_create_payload(c, str(k["id"])).get("user_id") or ""))
                   for k in SK.cards_uncertain(c, cid, _after(now, -LOOK_HOURS * 3600))]
    problems: list[str] = []
    for card_id, user in waiting:
        if not user:
            continue
        found, problem = _user_cards(provider, user)
        if problem:
            problems.append(problem)
            continue
        with store.transaction() as c:
            card = SK.get(c, "fin_cards", card_id)
            if card is None or str(card["provider_status"] or "") != cards.UNCERTAIN:
                continue
            ctx = load(c, now)
            why = cards.candidates(ctx, card, found)
            tico.needs_you(c, payments.admin_person(ctx), f"Confirm a Mercury card for {card['holder']}",
                           f"{why} Card {card_id}: confirm its Mercury card (card_resolve with provider_card_id), or "
                           "confirm Mercury never issued it (card_resolve with none). Its reservation stays until "
                           "then.")
    return problems


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


def _take(ctx: Any, cid: str, t: F.FeedTxn) -> str | None:
    """Take one transaction in under a savepoint: a failure undoes all of it and raises, so nothing half-posts."""
    ctx.c.execute("SAVEPOINT fin_take")
    try:
        fid = banking.ingest(ctx, cid, t)
    except Exception:
        ctx.c.execute("ROLLBACK TO fin_take")
        ctx.c.execute("RELEASE fin_take")
        raise
    ctx.c.execute("RELEASE fin_take")
    return fid


def _ingest_all(store: Any, cid: str, raws: list[dict[str, Any]], now: str, version: int = 0) -> None:
    """A page of transactions, each taken in on its own: one that fails is rolled back and queued in the inbox to try
    again (a synthetic event naming it), never skipped."""
    with store.transaction() as c:
        ctx = load(c, now)
        for raw in raws:
            try:
                _take(ctx, cid, F.mercury_txn(raw, version))
            except Exception:                       # one bad transaction must not stop the page
                log.exception("Finance will retry a Mercury transaction")
                tid = str(raw.get("id") or "")
                if tid:
                    SK.inbox_add(c, connection_id=cid, provider="mercury", event_id=f"retry:{tid}",
                                 payload={"resourceType": "transaction", "resourceId": tid}, at=now)


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
                # The balances read at the start of this pass and the whole feed: each account's opening balance.
                ctx = load(c, now)
                for m in SK.money_accounts(c, ctx.book.id):
                    if str(m["connection_id"] or "") == cid:
                        banking.opening_balance(ctx, str(m["id"]))
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
    """Each waiting event this connection owns (or that came with no connection and it has not tried): a transaction
    event reads the transaction whole with this connection's token and takes it in by its version, all of it in one
    savepoint. The event is done only when the owning connection took it in. A 404 for its own event backs off (gone
    after GONE_TRIES); a 404 or a foreign account for an unowned event marks it tried by this connection; a failure
    rolls back and stays to retry."""
    with store.read() as c:
        waiting = SK.inbox_waiting(c, INBOX_MOST, cid, now)
        others = [str(r["id"]) for r in SK.connections(c, "mercury") if str(r["status"]) in ("active", "error")]
    for r in waiting:
        body = SK.loads(r["payload_json"])
        kind = str(body.get("resourceType") or "")
        owned = str(r["connection_id"] or "") == cid
        tried = [str(x) for x in SK.loads_list(r["tried_json"])]
        if kind != "transaction":
            with store.transaction() as c:
                SK.inbox_done(c, str(r["id"]), "ignored", now)
            continue
        got = provider.transaction(str(body.get("resourceId") or ""))
        if not got.ok and got.status != 404:
            return [error_text(got)]
        attempts = int(r["attempts"]) + 1
        with store.transaction() as c:
            if not got.ok:                                       # 404: not this token's, or not there yet
                if owned and attempts >= GONE_TRIES:
                    SK.inbox_done(c, str(r["id"]), "gone", now)
                elif owned:
                    SK.inbox_later(c, str(r["id"]), attempts=attempts, tried=tried,
                                   next_try_at=_after(now, RETRY[min(attempts - 1, len(RETRY) - 1)]))
                else:
                    _tried(c, r, tried + [cid], others, now, missing=True)
                continue
            ctx = load(c, now)
            raw = str(body.get("resourceVersion") or "0")
            try:
                fid = _take(ctx, cid, F.mercury_txn(got.body, int(raw) if raw.isdigit() else 0))
            except Exception:                       # rolled back; it stays in the inbox to try again
                log.exception("Finance will retry a Mercury event")
                SK.inbox_later(c, str(r["id"]), attempts=attempts, tried=tried,
                               next_try_at=_after(now, RETRY[min(attempts - 1, len(RETRY) - 1)]))
                continue
            if fid is None and not owned:
                _tried(c, r, tried + [cid], others, now)
            else:
                SK.inbox_done(c, str(r["id"]), "done" if fid is not None else "ignored", now)
    return []


def _tried(c: Any, r: Any, tried: list[str], connections: list[str], now: str, missing: bool = False) -> None:
    """An unowned event this connection could not take in. Once every connection has tried it: when each saw the
    transaction on an account it doesn't map, it is nobody's; when one got a 404 (`missing`: maybe not visible yet), a
    new round starts after a backoff, until UNOWNED_HOURS after it arrived, and only then is it given up."""
    if missing and MISSING not in tried:
        tried = tried + [MISSING]
    if not set(connections) <= set(tried):
        SK.inbox_later(c, str(r["id"]), attempts=int(r["attempts"]), next_try_at=r["next_try_at"], tried=tried)
        return
    if MISSING in tried and str(r["created_at"]) > _after(now, -UNOWNED_HOURS * 3600):
        attempts = int(r["attempts"]) + 1
        SK.inbox_later(c, str(r["id"]), attempts=attempts, tried=[],
                       next_try_at=_after(now, RETRY[min(attempts - 1, len(RETRY) - 1)]))
        return
    SK.inbox_done(c, str(r["id"]), "ignored", now)


# ------------------------------------------------------------------------------------------------ writes
def outbox(store: Any, provider: Provider, now: str, cid: str) -> list[str]:
    """This connection's writes, each claimed in a short transaction before its call (so two workers never send one
    twice), recorded after. A write claimed earlier and never recorded was cut off mid-call: an idempotent one is sent
    again; a create Mercury takes no key for is never sent blindly again but goes to a finance admin to check."""
    with store.transaction() as c:
        for o in SK.outbox_stuck(c, _after(now, -CLAIM_MINUTES * 60), cid):
            _cut_off(c, o, now)
        due = SK.outbox_due(c, now, OUTBOX_MOST, cid)
    problems: list[str] = []
    for o in due:
        with store.transaction() as c:
            if not SK.outbox_claim(c, str(o["id"]), now):
                continue
            if str(o["op"]) == "transfer_create":
                ctx = load(c, now)
                fresh: list[Any] = []
                if transfers.recheck(ctx, o, fresh):
                    continue      # re-checked at use: cancelled or held, so nothing is sent
                transfers.reserve(ctx, o, fresh)         # with the claim: newly applicable limits move once
        body = SK.loads(o["payload_json"])
        answer = _send(store, provider, str(o["op"]), body)
        with store.transaction() as c:
            problem = _record(c, o, body, answer, now)
        if problem:
            problems.append(problem)
    return problems


def _cut_off(c: Any, o: Any, now: str) -> None:
    op = str(o["op"])
    if op not in NO_KEY_OPS:
        SK.update(c, "fin_outbox", str(o["id"]), {"claimed_at": None, "updated_at": now})
        return
    why = "the call was cut off; it may have gone through: check Mercury before trying again"
    SK.update(c, "fin_outbox", str(o["id"]), {"status": "failed", "claimed_at": None, "last_error": why,
                                              "updated_at": now})
    body = SK.loads(o["payload_json"])
    ctx = load(c, now)
    if op == "transfer_create":
        transfers.not_sent(ctx, body, why, unknown=True, started=o["claimed_at"])
        return
    if op == "card_create" and body.get("card_id"):
        cards.uncertain(ctx, str(body["card_id"]), why)
    what = "a card" if op == "card_create" else f"recipient {body.get('name') or ''}".strip()
    tico.needs_you(c, payments.admin_person(ctx), f"Check Mercury for {what}",
                   f"Finance was creating {what} in Mercury when it stopped, so it may or may not exist there. "
                   f"Check Mercury; Finance will not create it again on its own (outbox {o['id']}). For a card, "
                   "confirm its Mercury card or that there is none on the card (card_resolve).")


def _send(store: Any, provider: Provider, op: str, body: Mapping[str, Any]) -> Answer:
    """One outbox write, read against the card's latest desired state at send time."""
    if op == "transfer_create":
        return provider.create_transfer(transfers.request_body(body))
    if op == "recipient_create":
        return _send_recipient(store, provider, body)
    if op == "card_adopt":
        return provider.card(str(body.get("provider_card_id") or ""))
    if op in ("card_create", "card_sync", "card_limit", "card_state"):
        with store.read() as c:
            card = SK.get(c, "fin_cards", str(body.get("card_id")))
        if card is None:
            return Answer(200, {"skipped": "no such card"})
        if op == "card_create":
            if str(card["status"]) == "cancelled":
                return Answer(200, {"skipped": "cancelled before it was issued"})
            with store.read() as c:
                limit = cards.issue_limit(c, card)          # its allocation now, never a stale limit
            if limit <= 0:
                return Answer(200, {"skipped": "held back: the card has no allocation", "waiting": True})
            got = provider.create_card(str(body.get("user_id")), str(body.get("nickname")), limit)
            return Answer(got.status, {**got.body, "sent_limit": limit}) if got.ok else got
        return _sync_card(provider, card)
    return Answer(400, {"message": f"unknown op {op}"})


def _sync_card(provider: Provider, card: Any) -> Answer:
    """Bring Mercury to the card's desired state (status, then limit): what Mercury confirmed comes back in the body.
    Before the card exists at Mercury there is nothing to do (its creation reads the latest state)."""
    pid = str(card["provider_card_id"] or "")
    if not pid:
        return Answer(200, {"skipped": "not issued yet"})
    want, have = str(card["status"]), str(card["provider_status"] or "active")
    done: dict[str, object] = {}
    if want != have:
        op = {"cancelled": "cancel", "frozen": "freeze", "active": "unfreeze"}[want]
        if not (have == "cancelled"):
            got = provider.card_state(pid, op)
            if not got.ok:
                return got
            done["status"] = want
    limit = int(card["limit_amount"] or 0)
    if want != "cancelled" and limit > 0 and limit != int(card["provider_limit"] or 0):
        got = provider.card_limit(pid, limit)
        if not got.ok:
            return Answer(got.status, {**got.body, "confirmed": done})
        done["limit"] = limit
    return Answer(200, {"confirmed": done})


def _send_recipient(store: Any, provider: Provider, body: Mapping[str, Any]) -> Answer:
    """Create the recipient at Mercury with its bank details read from the vault just now (held in memory for the
    one call, never written back)."""
    if body.get("method") == "check":
        return provider.create_recipient(_recipient_body(body))
    with store.read() as c:
        try:
            details = payments.open_details(c, str(body.get("details_ref") or ""))
        except Exception:                    # no vault, or the row will not open: never a crash, never plaintext
            log.exception("Finance could not open a recipient's sealed bank details")
            details = {}
    if not details.get("accountNumber") or not details.get("routingNumber"):
        return Answer(400, {"message": "the recipient's bank details are not in the credential vault; add them again"})
    return provider.create_recipient(_recipient_body({**body, **details}))


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
    """What came back for one outbox write (the claim ends here). Success records the provider's id (a recipient's
    payment details are erased from the outbox then) and what Mercury confirmed for a card; a refused write waits (the
    IP allowlist) or fails."""
    attempts = int(o["attempts"]) + 1
    op = str(o["op"])
    card_id = str(body.get("card_id") or "")
    if answer.ok:
        if op == "recipient_create":
            SK.update(c, "fin_recipients", str(body["recipient_id"]), {
                "provider_recipient_id": str(answer.body.get("id") or ""), "updated_at": now})
            kept = {"recipient_id": body["recipient_id"]}
            SK.update(c, "fin_outbox", str(o["id"]), {"payload_json": SK.dump(kept)})
            # A payment waiting on this recipient goes in this pass (pay runs after outbox); the ring covers one paid
            # from another connection's account, whose pass may already be done.
            tico.after_commit(c, ring)
        elif op == "transfer_create":
            transfers.sent(load(c, now), body, answer.body)
        elif op == "card_create" and answer.body.get("waiting"):
            # Not issued and not cancelled: it is issued once it has an allocation again (cards.sync).
            SK.update(c, "fin_cards", card_id, {"sync_status": cards.WAITING, "updated_at": now,
                                                "sync_error": "Not issued until it has an allocation"})
        elif op == "card_create" and not answer.body.get("skipped"):
            limit = int(answer.body.get("sent_limit") or body["monthly"])     # what was asked for, not a later edit
            SK.update(c, "fin_cards", card_id, {
                "provider_card_id": str(answer.body.get("cardId") or answer.body.get("id") or ""),
                "last4": str(answer.body.get("lastFourDigits") or "")[-4:] or None, "provider_status": "active",
                "provider_limit": limit, "sync_status": "ok", "synced_at": now, "updated_at": now})
            cards.created(load(c, now), card_id, str(o["action_id"] or ""), str(o["id"]))
        elif op == "card_create":
            cards.not_issued(load(c, now), card_id)
        elif op == "card_adopt" and card_id:
            cards.adopt(load(c, now), card_id, answer.body, str(body.get("user_id") or ""), str(o["id"]))
        elif op in ("card_sync", "card_limit", "card_state") and card_id:
            cards.confirmed(load(c, now), card_id, cast(dict[str, Any], answer.body.get("confirmed") or {}),
                            str(o["action_id"] or ""), follow=str(o["id"]))
        SK.update(c, "fin_outbox", str(o["id"]), {"status": "sent", "attempts": attempts, "claimed_at": None,
                                                  "last_error": str(answer.body.get("skipped") or "") or None,
                                                  "updated_at": now})
        return ""
    if op in ("card_sync", "card_limit", "card_state") and card_id and answer.body.get("confirmed"):
        cards.confirmed(load(c, now), card_id, cast(dict[str, Any], answer.body.get("confirmed")))
    why = error_text(answer, write=True)
    final = answer.status is not None and 300 <= answer.status < 500 and not answer.refused_write and \
        answer.status != 429                     # a redirect (never followed) is no better on a retry
    if op in NO_KEY_OPS and (answer.status is None or answer.status >= 500):
        # Mercury takes no idempotency key for these: a call that may have landed is never sent again on its own.
        final, why = True, f"{why}; it may have gone through: check Mercury before trying again"
    SK.update(c, "fin_outbox", str(o["id"]), {
        "status": "failed" if final else "pending", "attempts": attempts, "last_error": why, "claimed_at": None,
        "next_try_at": None if final else _after(now, RETRY[min(attempts - 1, len(RETRY) - 1)]), "updated_at": now})
    if op == "transfer_create" and final:
        transfers.not_sent(load(c, now), body, why, unknown=answer.status is None or answer.status >= 500,
                           started=now)
    if op == "card_create" and card_id and (answer.status is None or answer.status >= 500):
        cards.uncertain(load(c, now), card_id, why)
    elif op.startswith("card_") and card_id:
        SK.update(c, "fin_cards", card_id, {"sync_status": "failed" if final else "pending", "sync_error": why})
    return why


def pay(store: Any, provider: Provider, now: str, cid: str) -> list[str]:
    """Payments from this connection's accounts the worker owes a call. A queued one is claimed (`sending`) in a short
    transaction, rechecked like the action itself, then sent once. One left `sending` by a stopped worker is unknown.
    An unknown one is never sent again: it is looked up (by its request id when a 409 gave it, else in Mercury's
    paged list of requests by its key, recipient and amount), and a finance admin is asked once it has been missing
    for UNKNOWN_ASK_MINUTES. Ones in Mercury's approval queue are polled."""
    with store.read() as c:
        due = SK.payments_due(c, now, PAY_MOST, cid)
    for p in due:
        pid = str(p["id"])
        status = str(p["status"])
        with store.read() as c:
            money = SK.get(c, "fin_money_accounts", str(p["money_account_id"]))
        account = str(money["provider_account_id"]) if money is not None else ""
        if status == "sending":
            with store.transaction() as c:
                payments.cut_off(load(c, now), SK.get(c, "fin_payments", pid))
            continue
        if status in ("awaiting_bank_approval", "submitted", "unknown") and p["provider_request_id"] \
                and str(p["mode"]) != "direct":
            answer = provider.send_request(str(p["provider_request_id"]))
            with store.transaction() as c:
                ctx = load(c, now)
                payments.polled(ctx, SK.get(c, "fin_payments", pid), answer)
                if status == "unknown" and answer.ok:
                    SK.update(c, "fin_payments", pid, {"unknown_since": None})
                banking.payment_linked(ctx, SK.get(c, "fin_payments", pid))
            continue
        if status == "unknown":
            # A cancel a finance admin asked for before this sweep is decided only by a read back of the account's
            # transactions to the payment's creation (what Mercury sent, whether or not it still lists the request).
            asked = payments.cancel_asked_before(p, now)
            requests, complete = _requests(provider, account)
            back, whole = _read_back(provider, account, p, now) if complete and asked else ([], False)
            with store.transaction() as c:
                ctx = load(c, now)
                row = SK.get(c, "fin_payments", pid)
                if payments.found(ctx, row, requests, back):
                    banking.payment_linked(ctx, SK.get(c, "fin_payments", pid))   # a debit taken in before it
                elif complete and (whole or not asked):
                    payments.not_found(ctx, row)
                elif complete:
                    payments.read_incomplete(ctx, row)
            continue
        if status != "queued":
            continue
        with store.transaction() as c:
            ctx = load(c, now)
            row = SK.get(c, "fin_payments", pid)
            fresh: list[Any] = []
            if str(row["status"]) != "queued" or payments.recheck(ctx, row, fresh):
                continue
            if not SK.payment_claim(c, pid, now, _after(now, payments.SENDING_MINUTES * 60)):
                continue
            payments.reserve(ctx, row, fresh)         # with the claim: newly applicable limits move once
            rec = SK.get(c, "fin_recipients", str(p["recipient_id"]))
            claimed = SK.get(c, "fin_payments", pid)
        body = payments.request_body(claimed, rec)
        answer = provider.create_transaction(account, body) if str(claimed["mode"]) == "direct" \
            else provider.request_send_money(account, body)
        with store.transaction() as c:
            ctx = load(c, now)
            payments.sent(ctx, SK.get(c, "fin_payments", pid), answer)
            banking.payment_linked(ctx, SK.get(c, "fin_payments", pid))
    return []


def _requests(provider: Provider, account: str) -> tuple[list[dict[str, Any]], bool]:
    """Mercury's approval requests for one account: every page of each documented status (REQUEST_STATUSES), one list
    call per status. Whole only when all of them were read whole."""
    out: list[dict[str, Any]] = []
    whole = True
    for status in REQUEST_STATUSES:
        rows, done = _every_page(lambda after, s=status: provider.send_requests(account, after, s), "requests")
        out += rows
        whole = whole and done
    return out, whole


def _read_back(provider: Provider, account: str, pay: Any, now: str) -> tuple[list[dict[str, Any]], bool]:
    """The account's transactions from the payment's creation day through the day after now (Mercury's days are UTC),
    every page (up to REQUEST_PAGES): the rows, and whether the whole span was read."""
    if not account:
        return [], False
    start = str(pay["created_at"])[:10]
    end = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=1)).date().isoformat()
    return _every_page(lambda after: provider.account_transactions(account, None, start, end, after), "transactions")


def _every_page(read: Callable[[str | None], Answer], rows_key: str) -> tuple[list[dict[str, Any]], bool]:
    """One of Mercury's lists read to the end, every page (up to REQUEST_PAGES). The flag says the whole list was
    read (a failed or cut-short read never counts as "not there")."""
    out: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(REQUEST_PAGES):
        page = read(after)
        if not page.ok:
            return out, False
        out += listed(page, rows_key)
        after = next_page(page)
        if not after:
            return out, True
    return out, False


def transfers_unknown(store: Any, provider: Provider, now: str, cid: str) -> list[str]:
    """Transfers left unknown (a call that may have landed) from this connection's accounts: each is found by its key
    in Mercury's transactions, as payments are found in its requests. Not found, it stays unknown; a finance admin was
    asked when it went unknown, and nothing sends it again. Mercury's full list is read a poll at a time, from the place
    the transfer's last poll stopped (its find_after), so a leg far down the list is found by a later poll."""
    with store.read() as c:
        due = [(t, transfers.source_account(c, t)) for t in SK.unknown_transfers(c, cid)]
    for t, account in due:
        tid = str(t["id"])
        narrow = _searched(provider, t, account)
        if any(transfers.matches(t, account, row) for row in narrow):
            rows, after = narrow, None
        else:
            rows, after = _read_from(provider, str(t["find_after"] or "") or None)
        with store.transaction() as c:
            if not transfers.found(load(c, now), SK.get(c, "fin_transfers", tid), rows) and after:
                SK.update(c, "fin_transfers", tid, {"find_after": after})
    return []


def _searched(provider: Provider, t: Any, account: str) -> list[dict[str, Any]]:
    """The source account's transactions that Mercury's search finds by the transfer's note, in the days around its
    claim (one page). Mercury's search reads a transaction's note and description, so a key held only in a memo is
    found by the paged read instead."""
    if not account:
        return []
    start, end = transfers.search_days(t)
    page = provider.account_transactions(account, transfers.note(t), start, end)
    return listed(page, "transactions") if page.ok else []


def _read_from(provider: Provider, after: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Mercury's transactions from `after` on, up to REQUEST_PAGES pages: the rows read, and the cursor the next read
    continues from (the last page's next page, or the last row once the list has ended). A failed read keeps its
    place."""
    rows: list[dict[str, Any]] = []
    cursor = after
    for _ in range(REQUEST_PAGES):
        page = provider.transactions(cursor)
        if not page.ok:
            break
        got = listed(page, "transactions")
        rows += got
        nxt = next_page(page)
        if nxt:
            cursor = nxt
            continue
        if got:
            cursor = str(got[-1].get("id") or "") or cursor
        break
    return rows, cursor

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
    """Start the thread (or restart it if it died). Safe to call again on every scheduler tick. Registers the
    credential vault for sealing and reading payees' bank details, even when rehearsal withholds the worker."""
    tico.use_vault(vault)
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
