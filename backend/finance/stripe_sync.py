"""The Stripe sync worker (spec §9): one Stripe connection's balance, its balance transactions and its webhook doorbells,
run by the bank worker (finance/sync.py) for each Stripe connection. Stripe is called outside any transaction and what
came back is recorded in a short one. The first backfill walks balance transactions newest first, a few pages a step,
and sets the opening balance when the list ends; catch-up reads newest pages until it reaches one already taken in.
Each balance transaction is taken in once by its id, so a repeat read changes nothing. A rehearsal copy calls nothing."""
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import banking
from .context import Ctx, load
from .core import feeds as F
from .core import kinds as K
from .core import validate as V
from .core.errors import CONFLICT, FinanceError, invalid
from .handlers import Plan, Result
from .store import banking as SK
from .store import tico
from .stripe import Provider, Stripe, base_url, error_text, has_more, listed

log = logging.getLogger(__name__)

BALANCE_ACCOUNT = "balance"       # provider_account_id of a Stripe balance money account
BALANCE_CODE, BALANCE_NAME = "1050", "Stripe balance"
BACKFILL_PAGES = 4                # balance-transaction pages per step: the first backfill is paced across steps
BACKFILL_CATCH_UP = 1             # while backfilling, a step reads only the newest page for what came after
CATCH_UP_PAGES = 10               # newest pages read per step once the backfill is done
POLL_MINUTES = 15                 # how often a connection is read without a webhook
INBOX_MOST = 200
Make = Callable[[str], Any]       # a factory from the key to a provider (tests pass a fake)


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
    """What connection_create needs, read with the key before the action: the balances by currency, in cents."""
    got = provider.balance()
    if not got.ok:
        return {}, error_text(got)
    currencies = sorted({str(r.get("currency") or "").upper() for key in ("available", "pending")
                         for r in _rows(got.body, key)} - {""})
    return {"balances": [{"currency": cur, "available": _sum(got.body, "available", cur),
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
    """Connect a Stripe account with a restricted key (a Tico credential, read at use time and never copied here). Its
    balance becomes one money account on a Stripe balance asset line; its activity posts from the balance transactions
    (finance/stripe_post). `probe` is what probe() read with that key. The key must see a balance in the book's
    currency: other currencies are not posted."""
    V.only(p, ("provider", "credential_id", "name", "probe"))
    cred = V.text(p, "credential_id", 120)
    if any(str(r["credential_ref"] or "") == cred and str(r["status"]) != "disconnected" for r in SK.connections(ctx.c)):
        raise FinanceError(CONFLICT, "that credential is already connected")
    probed = V.obj(p.get("probe"), "probe")
    balances = [V.obj(b, "a balance") for b in V.items(probed.get("balances"), "probe.balances")]
    base = ctx.book.base_currency
    if not any(str(b.get("currency") or "").upper() == base.upper() for b in balances):
        raise invalid(f"the Stripe key sees no balance in {base}")
    name = V.opt_text(p, "name", 120) or "Stripe"

    def apply(aid: str) -> Result:
        cid = SK.insert(ctx.c, "fin_connections", {
            "provider": "stripe", "purpose": "payments", "credential_ref": cred, "status": "active", "name": name,
            "settings_json": SK.dump({"backfilled": False}), "next_sync_at": ctx.at, "created_at": ctx.at,
            "created_by": who})
        gl = banking.new_account(ctx, BALANCE_CODE, BALANCE_NAME, "asset", "bank", who)
        mid = SK.insert(ctx.c, "fin_money_accounts", {
            "book_id": ctx.book.id, "kind": "stripe", "subtype": "", "name": name[:120], "provider": "stripe",
            "connection_id": cid, "account_id": gl.id, "provider_account_id": BALANCE_ACCOUNT, "holds_cash": 1,
            "balance_currency": base, "created_at": ctx.at, "created_by": who})
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
        problems += balances(store, provider, cid, now)
    problems += catch_up(store, provider, cid, row, now)
    if not problems:
        with store.transaction() as c:
            for r in waiting:
                SK.inbox_done(c, str(r["id"]), "done", now)
    with store.transaction() as c:
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


def catch_up(store: Any, provider: Provider, cid: str, row: Any, now: str) -> list[str]:
    """Backfill while it runs, then newest pages until a row already taken in: the webhook doorbells and polls both
    read what came after the last read, so nothing waits for the whole list."""
    if not _settings(row).get("backfilled"):
        return _backfill(store, provider, cid, row, now) + _newest(store, provider, cid, now, BACKFILL_CATCH_UP)
    return _newest(store, provider, cid, now, CATCH_UP_PAGES)


def _backfill(store: Any, provider: Provider, cid: str, row: Any, now: str) -> list[str]:
    """Balance transactions newest first from the cursor, a few pages a step. When the list ends, each Stripe money
    account's opening balance is set from the balance read at the start of this step and the rows taken in."""
    conf = _settings(row)
    cursor = str(row["cursor"] or "") or None
    for _ in range(BACKFILL_PAGES):
        page = provider.transactions(cursor)
        if not page.ok:
            return [error_text(page)]
        rows = listed(page)
        last, problems, _ = _take(store, cid, rows, now, stop_known=False)
        if last:
            cursor = last
        done = (not has_more(page) or not rows) and not problems
        with store.transaction() as c:
            ctx = load(c, now)
            mine = [m for m in SK.money_accounts(c, ctx.book.id)
                    if str(m["connection_id"] or "") == cid and str(m["kind"]) == "stripe"]
            # The opening balance is set from a balance read: until one is on the account, the backfill is not done.
            done = done and all(m["balance"] is not None for m in mine)
            fields: dict[str, object] = {"cursor": cursor}
            if done:
                fields["settings_json"] = SK.dump({**conf, "backfilled": True})
            SK.update(c, "fin_connections", cid, fields)
            if done:
                for m in mine:
                    banking.opening_balance(ctx, str(m["id"]))
        if problems or done:
            return problems
    return []


def _newest(store: Any, provider: Provider, cid: str, now: str, pages: int) -> list[str]:
    """Newest balance transactions first until one already taken in: everything that came after the last read."""
    after: str | None = None
    for _ in range(pages):
        page = provider.transactions(after)
        if not page.ok:
            return [error_text(page)]
        rows = listed(page)
        last, problems, known = _take(store, cid, rows, now, stop_known=True)
        if problems or known or not has_more(page) or not rows:
            return problems
        after = last
    return []


def _take(store: Any, cid: str, rows: list[dict[str, Any]], now: str, *, stop_known: bool
          ) -> tuple[str, list[str], bool]:
    """Take rows in order, each in its own savepoint, in one transaction. Returns the last id processed, the problems
    (the page stops at the first failure, so the rest is read again next time) and whether a known row was reached.
    A balance in another currency than the book's is not posted; it still counts as processed."""
    last = ""
    problems: list[str] = []
    known = False
    with store.transaction() as c:
        ctx = load(c, now)
        base = ctx.book.base_currency
        money = SK.money_account_by_provider(c, cid, BALANCE_ACCOUNT)
        for raw in rows:
            tid = str(raw.get("id") or "")
            if stop_known and money is not None and SK.feed_row(c, str(money["id"]), tid) is not None:
                known = True
                break
            if str(raw.get("currency") or "").upper() == base.upper():
                ctx.c.execute("SAVEPOINT fin_stripe")
                try:
                    banking.ingest(ctx, cid, F.stripe_txn(raw))
                except Exception:
                    ctx.c.execute("ROLLBACK TO fin_stripe")
                    ctx.c.execute("RELEASE fin_stripe")
                    log.exception("Finance could not post a Stripe balance transaction")
                    problems.append(f"Stripe balance transaction {tid} could not be posted")
                    break
                ctx.c.execute("RELEASE fin_stripe")
            last = tid
    return last, problems, known
