"""The Stripe sync worker (spec §9): one Stripe connection's balance, its balance transactions and its webhook doorbells,
run by the bank worker (finance/sync.py) for each Stripe connection. Stripe is called outside any transaction and what
came back is recorded in a short one. The first backfill step reads the first page and the balance together, between
two reads of the newest row that agree, so the balance holds exactly the rows the walk starts from; the walk then runs
newest first, a few pages a step, and sets the opening balance when the list ends. Catch-up walks newest first down to
the newest row of the last completed walk (the boundary). A walk that did not finish resumes from its cursor, so a page
that failed is read again and nothing above or below it is skipped. Each balance transaction is taken in once by its id,
so a repeat read changes nothing. The product catalog (see catalog) is read on each due pass, before the balance
transactions, and written in one transaction: each Stripe product is one item and each of its prices one ref on it.
A rehearsal copy calls nothing."""
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import banking, stripe_post
from ..records import store as RS
from .context import Ctx, load
from .core import dates
from .core import feeds as F
from .core import kinds as K
from .core import validate as V
from .core.errors import CONFLICT, FinanceError, invalid
from .core.org import SYSTEM
from .handlers import Plan, Result
from .store import banking as SK
from .store import mirror as SX
from .store import tico
from .mercury import Answer
from .stripe import Catalog, Provider, Stripe, base_url, error_text, has_more, listed

log = logging.getLogger(__name__)

BALANCE_CODE, BALANCE_NAME = "1050", "Stripe balance"
BACKFILL_PAGES = 4               # balance-transaction pages per step: the first backfill is paced across steps
CATCH_UP_PAGES = 10              # newest pages read per step until the boundary
CATALOG_PAGES = 50               # product or price pages read per pass: 5,000 of each; more is read in part, never "gone"
BRACKET_TRIES = 3                # reads of the first page and balance before a backfill step waits for the next
POLL_MINUTES = 15                # how often a connection is read without a webhook
INBOX_MOST = 200
Make = Callable[[str], Any]      # a factory from the key to a provider (tests pass a fake)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _after(at: str, seconds: float) -> str:
    moment = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _settings(row: Any) -> dict[str, Any]:
    return SK.loads(row["settings_json"])


def _rows(body: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = body.get(key)
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], r) for r in cast(list[Any], value) if isinstance(r, dict)]


def _cents(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _sum(body: Mapping[str, Any], key: str, currency: str) -> int:
    """A Stripe balance list (`available` or `pending`) summed for one currency, in cents."""
    return sum(_cents(r.get("amount")) for r in _rows(body, key)
               if str(r.get("currency") or "").upper() == currency.upper())


# ------------------------------------------------------------------------------------------------ connect probe
def probe(provider: Provider) -> tuple[dict[str, object], str]:
    """What connection_create needs, read with the key before the action: the Stripe account the key belongs to, and
    the balances by currency, in cents."""
    account = provider.account()
    if not account.ok:
        return {}, error_text(account)
    acct = str(account.body.get("id") or "")
    if not acct.startswith("acct_"):
        return {}, "Stripe did not name its account"
    got = provider.balance()
    if not got.ok:
        return {}, error_text(got)
    currencies = sorted({str(r.get("currency") or "").upper() for key in ("available", "pending")
                         for r in _rows(got.body, key)} - {""})
    return {"account": acct, "balances": [{"currency": cur, "available": _sum(got.body, "available", cur),
                                           "pending": _sum(got.body, "pending", cur)} for cur in currencies]}, ""


def probe_credential(store: Any, vault: Any, credential_id: str, make: Make | None = None
                     ) -> tuple[dict[str, object], str]:
    """Read the Stripe key from its credential (outside any transaction) and probe Stripe with it."""
    with store.read() as c:
        if not tico.credential_exists(c, credential_id) and make is None:
            return {}, "no such credential with a stored key"
    provider = _provider(store, vault, credential_id, make)
    if provider is None:
        return {}, "no such credential with a stored key"
    return probe(provider)


def _provider(store: Any, vault: Any, ref: str, make: Make | None) -> Provider | None:
    with store.read() as c:
        token = tico.credential_secret(c, vault, ref) if vault is not None else ""
    if make is not None:
        return cast(Provider, make(token))
    return Stripe(token, base=base_url()) if token else None


def connection_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Connect a Stripe account with a restricted key (a Tico credential, read at use time and never copied here). The
    account's balance is one money account on a Stripe balance asset line, keyed by the Stripe account id: connecting the
    same account again picks its money account back up, so its history is never taken in twice. `probe` is what probe()
    read with that key. The key must see a balance in the book's currency: other currencies are not posted."""
    V.only(p, ("provider", "credential_id", "name", "probe"))
    cred = V.text(p, "credential_id", 120)
    if any(str(r["credential_ref"] or "") == cred and str(r["status"]) != "disconnected" for r in SK.connections(ctx.c)):
        raise FinanceError(CONFLICT, "that credential is already connected")
    probed = V.obj(p.get("probe"), "probe")
    acct = str(probed.get("account") or "")
    if not acct.startswith("acct_"):
        raise invalid("the probe names no Stripe account: probe the key again")
    balances = [V.obj(b, "a balance") for b in V.items(probed.get("balances"), "probe.balances")]
    base = ctx.book.base_currency
    if not any(str(b.get("currency") or "").upper() == base.upper() for b in balances):
        raise invalid(f"the Stripe key sees no balance in {base}")
    name = V.opt_text(p, "name", 120) or "Stripe"
    live = SK.money_account_by_provider_id(ctx.c, ctx.book.id, "stripe", acct)
    held = SK.get(ctx.c, "fin_connections", str(live["connection_id"] or "")) if live is not None else None
    if held is not None and str(held["status"]) != "disconnected":
        raise FinanceError(CONFLICT, "that Stripe account is already connected")

    def apply(aid: str) -> Result:
        cid = SK.insert(ctx.c, "fin_connections", {
            "provider": "stripe", "purpose": "payments", "credential_ref": cred, "status": "active", "name": name,
            "settings_json": SK.dump({"backfilled": False, "account": acct}), "next_sync_at": ctx.at,
            "created_at": ctx.at, "created_by": who})
        if live is None:
            gl = banking.new_account(ctx, BALANCE_CODE, BALANCE_NAME, "asset", "bank", who)
            mid = SK.insert(ctx.c, "fin_money_accounts", {
                "book_id": ctx.book.id, "kind": "stripe", "subtype": "", "name": name[:120], "provider": "stripe",
                "connection_id": cid, "account_id": gl.id, "provider_account_id": acct, "holds_cash": 1,
                "balance_currency": base, "created_at": ctx.at, "created_by": who})
        else:
            mid = str(live["id"])
            gl = ctx.accounts.get(str(live["account_id"] or "")) or banking.new_account(
                ctx, BALANCE_CODE, BALANCE_NAME, "asset", "bank", who)
            SK.update(ctx.c, "fin_money_accounts", mid, {"connection_id": cid, "status": "active", "name": name[:120],
                                                         "account_id": gl.id, "updated_at": ctx.at})
        return {"connection_id": cid, "money_accounts": [{"money_account_id": mid, "kind": "stripe",
                                                          "account_id": gl.id, "code": gl.code}],
                "payments_enabled": False, "approval_note": None}
    return Plan(K.ALLOW, f"Connect Stripe ({len(balances)} balance(s))", apply)


# ------------------------------------------------------------------------------------------------ one step
def connection(store: Any, vault: Any, cid: str, *, rehearsal: bool = False, make: Make | None = None,
               at: str | None = None) -> str:
    """Sync one Stripe connection. `withheld` in rehearsal, `gone`, `idle`, `no_token`, else `ok` or `failed`."""
    now = at or _now()
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
        waiting = [r for r in SK.inbox_waiting(c, INBOX_MOST, cid, now)
                   if str(r["provider"]) == "stripe" and str(r["connection_id"] or "") == cid]
    if row is None or str(row["status"]) == "disconnected":
        return "gone"
    if rehearsal:
        return "withheld"
    due = not row["next_sync_at"] or str(row["next_sync_at"]) <= now
    if not (due or waiting):
        return "idle"
    provider = _provider(store, vault, str(row["credential_ref"] or ""), make)
    if provider is None:
        _failed(store, cid, "the Stripe key is missing; store it again in Settings", now)
        return "no_token"
    problems: list[str] = []
    if due:
        if isinstance(provider, Catalog):
            problems += catalog(store, provider, cid, now)
        problems += balances(store, provider, cid, now)
    problems += catch_up(store, provider, cid, now)
    if not problems:
        with store.transaction() as c:
            for r in waiting:
                SK.inbox_done(c, str(r["id"]), "done", now)
    with store.transaction() as c:
        if due:
            stripe_post.stale(load(c, now))
        fields: dict[str, object] = {"last_sync_at": now, "updated_at": now}
        if due:
            fields["next_sync_at"] = _after(now, POLL_MINUTES * 60)
        if problems:
            fields.update({"status": "error", "last_error": problems[0][:300]})
            banking.event(load(c, now), "finance.sync.failed", cid, {"connection_id": cid}, now[:13])
        else:
            fields.update({"status": "active", "last_error": None, "last_ok_at": now})
        SK.update(c, "fin_connections", cid, fields)
    return "failed" if problems else "ok"


def _failed(store: Any, cid: str, why: str, now: str) -> None:
    with store.transaction() as c:
        SK.update(c, "fin_connections", cid, {"status": "error", "last_error": why, "updated_at": now,
                                              "next_sync_at": _after(now, POLL_MINUTES * 60)})


# ------------------------------------------------------------------------------------------------ catalog
def catalog(store: Any, provider: Catalog, cid: str, now: str) -> list[str]:
    """Stripe's products as items, and each product's prices as refs on its item (plan §7.2, case P2). Both lists are read
    outside any transaction, then written in one. A product is found by its Stripe id, so a later read renames or
    archives the same item rather than adding one; each price is its own ref, so a monthly and a yearly price both stay.
    An item keeps the books' income account only when it is created, and Tico never writes back to Stripe. A product or
    price a complete list no longer holds is marked deleted (its item archived), never removed."""
    products, products_whole, why = _every(provider.products)
    if why:
        return [why]
    prices, prices_whole, why = _every(provider.prices)
    if why:
        return [why]
    with store.transaction() as c:
        ctx = load(c, now)
        items = _write_products(ctx, cid, products, now)
        _write_prices(ctx, cid, prices, items, now)
        if products_whole:
            for r in _gone(c, cid, "Product", _keys("Product", products), now):
                RS.update(c, "items", str(r["local_id"]), {"archived_at": now, "updated_at": now, "updated_by_id": SYSTEM})
        if prices_whole:
            _gone(c, cid, "Price", _keys("Price", prices), now)
    return []


def _every(read: Callable[[str | None], Answer]) -> tuple[list[dict[str, Any]], bool, str]:
    """One Stripe list read to its end, a page at a time (up to CATALOG_PAGES). The flag says the whole list was read,
    which is what lets a row missing from it count as gone; the text says why a read failed."""
    rows: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(CATALOG_PAGES):
        page = read(after)
        if not page.ok:
            return rows, False, error_text(page)
        batch = listed(page)
        rows += batch
        last = str(batch[-1].get("id") or "") if batch else ""
        if not has_more(page) or not last:
            return rows, True, ""
        after = last
    return rows, False, ""


def _keys(kind: str, rows: list[dict[str, Any]]) -> set[str]:
    """The external ids (kind:id) of the rows a list read."""
    return {f"{kind}:{r.get('id')}" for r in rows if r.get("id")}


def _digest(value: object) -> str:
    """A content digest: a ref's revision moves when what Stripe says about the record moves."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _write_products(ctx: Ctx, cid: str, products: list[dict[str, Any]], now: str) -> dict[str, str]:
    """Each listed product's item, made, renamed or archived to match Stripe. Returns the item id by Stripe product id. A
    new item sells on the books' income account (the one a Stripe sale with no invoice posts to) and is a service."""
    income = banking.by_code(ctx, stripe_post.INCOME_CODE)
    items: dict[str, str] = {}
    for p in products:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        name = str(p.get("name") or pid)
        active = p.get("active") is True
        digest = _digest([name, active])
        key = f"Product:{pid}"
        row = SX.ref(ctx.c, cid, "items", key)
        if row is None:
            local = RS.insert(ctx.c, "items", {
                "name": name, "kind": "service", "income_account_id": income.id if income is not None else None,
                "archived_at": None if active else now, "created_at": now, "created_by_id": SYSTEM,
                "updated_at": now, "updated_by_id": SYSTEM})
            SX.ref_insert(ctx.c, cid=cid, local_table="items", local_id=local, kind="Product", external_id=key,
                          revision=digest, status="active", at=now, by=SYSTEM)
        else:
            local = str(row["local_id"])
            if str(row["revision"]) != digest or str(row["status"]) != "active":
                RS.update(ctx.c, "items", local, {"name": name, "archived_at": None if active else now,
                                                  "updated_at": now, "updated_by_id": SYSTEM})
                SX.ref_set(ctx.c, str(row["id"]), local_id=local, revision=digest, status="active", at=now)
        items[pid] = local
    return items


def _write_prices(ctx: Ctx, cid: str, prices: list[dict[str, Any]], items: Mapping[str, str], now: str) -> None:
    """Each listed price as its own ref on its product's item. A price whose product no item came from is not kept."""
    for p in prices:
        prid = str(p.get("id") or "")
        item = items.get(str(p.get("product") or ""))
        if not prid or item is None:
            continue
        detail = json.dumps(_price_detail(p), sort_keys=True)
        digest = _digest(detail)
        key = f"Price:{prid}"
        row = SX.ref(ctx.c, cid, "items", key)
        if row is None:
            SX.ref_insert(ctx.c, cid=cid, local_table="items", local_id=item, kind="Price", external_id=key,
                          revision=digest, status="active", at=now, by=SYSTEM, detail=detail)
        elif str(row["revision"]) != digest or str(row["status"]) != "active" or str(row["local_id"]) != item:
            SX.ref_set(ctx.c, str(row["id"]), local_id=item, revision=digest, status="active", at=now, detail=detail)


def _price_detail(p: Mapping[str, Any]) -> dict[str, object]:
    """What a Stripe price is for the books: its currency, its amount in minor units (null when the customer names it),
    its type, its recurring interval (month or year; "" for a one-time price) and whether Stripe has it active."""
    raw = p.get("recurring")
    recurring: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
    amount = p.get("unit_amount")
    return {"active": p.get("active") is True, "currency": str(p.get("currency") or "").upper(),
            "amount": amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
            "type": str(p.get("type") or ""), "interval": str(recurring.get("interval") or ""),
            "interval_count": _cents(recurring.get("interval_count")) or 1}


def _gone(c: Any, cid: str, kind: str, listed_keys: set[str], now: str) -> list[Any]:
    """The refs of one kind a complete list no longer holds: marked deleted. Returned so the caller can archive their items."""
    gone = [r for r in SX.refs_of_kind(c, cid, "items", kind)
            if str(r["external_id"]) not in listed_keys and str(r["status"]) != "deleted"]
    for r in gone:
        SX.ref_set(c, str(r["id"]), local_id=str(r["local_id"]), revision=str(r["revision"]), status="deleted", at=now)
    return gone


def balances(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    """The balance as Stripe reports it in the book's currency: available plus pending, and available alone."""
    got = provider.balance()
    if not got.ok:
        return [error_text(got)]
    with store.transaction() as c:
        ctx = load(c, now)
        base = ctx.book.base_currency
        available = _sum(got.body, "available", base)
        current = available + _sum(got.body, "pending", base)
        for m in SK.money_accounts(c, ctx.book.id):
            if str(m["connection_id"] or "") == cid and str(m["kind"]) == "stripe":
                banking.set_balance(ctx, str(m["id"]), current, available, now)
    return []


def catch_up(store: Any, provider: Provider, cid: str, now: str) -> list[str]:
    """Backfill while it runs; once it is done, the newest balance transactions down to the boundary, a few pages a
    step. The webhook doorbells and polls both read what came after the last completed walk."""
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
    if row is None:
        return []
    conf = _settings(row)
    if not conf.get("backfilled"):
        return _backfill(store, provider, cid, row, now)
    cursor, head, conf = _resume(row)
    return _walk(store, provider, cid, conf, cursor, head, CATCH_UP_PAGES, now,
                 stop=str(conf.get("boundary") or "") or None, backfill=False)


def _resume(row: Any) -> tuple[str | None, str | None, dict[str, Any]]:
    """Where a walk stands: its cursor (the row to read older rows from; None at the top), its head (the newest row it
    started from; None before its first page) and the settings it carries."""
    conf = _settings(row)
    head = str(conf.pop("head", "") or "") or None
    return str(row["cursor"] or "") or None, head, conf


def _backfill(store: Any, provider: Provider, cid: str, row: Any, now: str) -> list[str]:
    """The first step reads the first page and the balance together (see _bracket) and walks from there. Each later
    step continues from its cursor, BACKFILL_PAGES pages at a time, until the list ends."""
    cursor, head, conf = _resume(row)
    if cursor is not None or head is not None:
        return _walk(store, provider, cid, conf, cursor, head, BACKFILL_PAGES, now, stop=None, backfill=True)
    with store.read() as c:
        base = load(c, now).book.base_currency
    page, held, why = _bracket(provider, base)
    if page is None:
        return [why]
    return _walk(store, provider, cid, {**conf, "held": held}, None, None, BACKFILL_PAGES, now, stop=None,
                 backfill=True, first=page)


def _bracket(provider: Provider, base: str) -> tuple[Answer | None, int, str]:
    """The first page and the balance in the book's currency, read so that they agree: the newest row is the same
    before and after the balance is read, so no row came in between and the balance holds exactly the rows the page
    starts from. After BRACKET_TRIES disagreements the step gives up and the next one reads again."""
    for _ in range(BRACKET_TRIES):
        page = provider.transactions(None)
        if not page.ok:
            return None, 0, error_text(page)
        got = provider.balance()
        if not got.ok:
            return None, 0, error_text(got)
        again = provider.transactions(None, limit=1)
        if not again.ok:
            return None, 0, error_text(again)
        if _newest(again) == _newest(page):
            return page, _sum(got.body, "available", base) + _sum(got.body, "pending", base), ""
    return None, 0, "the Stripe balance changed while it was read: it is read again next time"


def _newest(answer: Answer) -> str:
    rows = listed(answer)
    return str(rows[0].get("id") or "") if rows else ""


def _walk(store: Any, provider: Provider, cid: str, conf: dict[str, Any], cursor: str | None, head: str | None,
          pages: int, now: str, *, stop: str | None, backfill: bool, first: Answer | None = None) -> list[str]:
    """Up to `pages` pages newest first from `cursor` (None: the top), each taken in and then the walk's place saved.
    The walk ends at `stop` (the boundary: rows from it on are already in), at the list's end, or as complete. A problem
    returns at once and keeps the place the last page saved, so the next step reads the failed page again. A complete
    walk's head becomes the boundary; a backfill also sets the opening balance. `first` is a page already read."""
    page = first
    for _ in range(pages):
        if page is None:
            page = provider.transactions(cursor)
        if not page.ok:
            return [error_text(page)]
        rows = listed(page)
        ids = [str(r.get("id") or "") for r in rows]
        if head is None and ids:
            head = ids[0]
        problems = _take(store, cid, str(conf.get("account") or ""), rows, now, stop=stop)
        if problems:
            return problems
        if (stop is not None and stop in ids) or not has_more(page) or not rows:
            _complete(store, cid, now, conf, head, backfill)
            return []
        cursor = ids[-1]
        _save(store, cid, now, conf, cursor, head)
        page = None
    return []


def _save(store: Any, cid: str, now: str, conf: Mapping[str, Any], cursor: str, head: str | None) -> None:
    state = {**conf, "head": head} if head else dict(conf)
    with store.transaction() as c:
        SK.update(c, "fin_connections", cid, {"cursor": cursor, "settings_json": SK.dump(state), "updated_at": now})


def _complete(store: Any, cid: str, now: str, conf: Mapping[str, Any], head: str | None, backfill: bool) -> None:
    """A walk that reached its end: its head is the boundary the next walk stops at. A backfill sets each Stripe money
    account's opening balance too: the balance it read, less every row now taken in (all of them at or before its
    head, since the walk started there)."""
    done = {k: v for k, v in conf.items() if k != "held"}
    done["boundary"] = head or conf.get("boundary") or ""
    with store.transaction() as c:
        if backfill:
            ctx = load(c, now)
            for m in SK.money_accounts(c, ctx.book.id):
                if str(m["connection_id"] or "") != cid or str(m["kind"]) != "stripe":
                    continue
                mid = str(m["id"])
                first = SK.first_feed_day(c, mid)
                day = (dates.day(first) - timedelta(days=1)).isoformat() if first else ctx.today
                banking.set_opening(ctx, m, int(conf["held"]) - SK.feed_total(c, mid), day)
            done["backfilled"] = True
        SK.update(c, "fin_connections", cid, {"settings_json": SK.dump(done), "cursor": None, "updated_at": now})


def _take(store: Any, cid: str, acct: str, rows: list[dict[str, Any]], now: str, *, stop: str | None) -> list[str]:
    """Take a page's rows in, oldest first, each in its own savepoint, in one transaction; then the batch is done (see
    stripe_post.batch_done). Rows from `stop` on are not read. A row in another currency than the book's is read and not
    posted. Returns the problems: the page stops at the first failure, and the rows before it stay in."""
    problems: list[str] = []
    with store.transaction() as c:
        ctx = load(c, now)
        base = ctx.book.base_currency
        money = SK.money_account_by_provider(c, cid, acct)
        if money is None:
            return ["the Stripe account has no money account on the books"]
        cut = len(rows)
        if stop is not None:
            cut = next((i for i, r in enumerate(rows) if str(r.get("id") or "") == stop), len(rows))
        for raw in reversed(rows[:cut]):
            if str(raw.get("currency") or "").upper() != base.upper():
                continue
            ctx.c.execute("SAVEPOINT fin_stripe")
            try:
                banking.ingest(ctx, cid, F.stripe_txn(raw, acct))
            except Exception:
                ctx.c.execute("ROLLBACK TO fin_stripe")
                ctx.c.execute("RELEASE fin_stripe")
                log.exception("Finance could not post a Stripe balance transaction")
                problems.append(f"Stripe balance transaction {raw.get('id')} could not be posted")
                break
            ctx.c.execute("RELEASE fin_stripe")
        stripe_post.batch_done(ctx, money)
    return problems
