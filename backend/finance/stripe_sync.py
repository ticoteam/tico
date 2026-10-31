"""The Stripe sync worker (spec §9): one Stripe connection's balance, its balance transactions and its webhook doorbells,
run by the bank worker (finance/sync.py) for each Stripe connection. Stripe is called outside any transaction and what
came back is recorded in a short one. The first backfill walks balance transactions newest first, a few pages a step,
and sets the opening balance when the list ends: the balance read at the start, less the rows created by then, so every
transaction counts once. Catch-up walks newest first from where it last stopped until it reaches a row already taken in,
so a long run of new rows is read across passes and none is skipped. Each balance transaction is taken in once by its
id, so a repeat read changes nothing. A rehearsal copy calls nothing."""
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import banking, stripe_post
from .context import Ctx, load
from .core import dates
from .core import feeds as F
from .core import kinds as K
from .core import validate as V
from .core.errors import CONFLICT, FinanceError, invalid
from .handlers import Plan, Result
from .store import banking as SK
from .store import tico
from .stripe import Provider, Stripe, base_url, error_text, has_more, listed

log = logging.getLogger(__name__)

BALANCE_CODE, BALANCE_NAME = "1050", "Stripe balance"
BACKFILL_PAGES = 4               # balance-transaction pages per step: the first backfill is paced across steps
CATCH_UP_PAGES = 10              # newest pages read per step until a row already taken in
POLL_MINUTES = 15                # how often a connection is read without a webhook
INBOX_MOST = 200
Make = Callable[[str], Any]      # a factory from the key to a provider (tests pass a fake)
_clock: Callable[[], float] = time.time    # the moment the backfill cuts its balance (tests set it)
Walk = tuple[str | None, list[str], bool]  # (cursor to resume from, problems, finished)


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
    """Backfill while it runs; once it is done, newest balance transactions from the saved cursor until a row already
    taken in. The webhook doorbells and polls both read what came after the last read, so nothing waits for the list."""
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
    if row is None:
        return []
    if not _settings(row).get("backfilled"):
        return _backfill(store, provider, cid, row, now)
    _, problems, _ = _walk(store, provider, cid, str(_settings(row).get("account") or ""),
                           now, str(row["cursor"] or "") or None, CATCH_UP_PAGES, stop_known=True)
    return problems


def _backfill(store: Any, provider: Provider, cid: str, row: Any, now: str) -> list[str]:
    """Balance transactions newest first from the cursor, a few pages a step. The cut (the balance and the clock at the
    start) is taken once and kept; when the list ends, the opening balance is set from it."""
    conf = _settings(row)
    if "cutoff" not in conf:
        cut, why = _cut(store, provider, now)
        if why:
            return [why]
        conf = {**conf, **cut}
        with store.transaction() as c:
            SK.update(c, "fin_connections", cid, {"settings_json": SK.dump(conf)})
    _, problems, finished = _walk(store, provider, cid, str(conf.get("account") or ""), now,
                                  str(row["cursor"] or "") or None, BACKFILL_PAGES, stop_known=False)
    if problems or not finished:
        return problems
    _finish(store, cid, conf, now)
    return []


def _cut(store: Any, provider: Provider, now: str) -> tuple[dict[str, object], str]:
    """The balance as the backfill starts. It is read first and the clock after, so a transaction created before the cut
    is in the balance and one created after it is not (to the second: Stripe's ids carry no snapshot)."""
    got = provider.balance()
    if not got.ok:
        return {}, error_text(got)
    with store.read() as c:
        base = load(c, now).book.base_currency
    held = _sum(got.body, "available", base) + _sum(got.body, "pending", base)
    return {"cutoff": int(_clock()), "cutoff_balance": held}, ""


def _finish(store: Any, cid: str, conf: Mapping[str, Any], now: str) -> None:
    """Each Stripe money account's opening balance: the cut's balance less the rows the cut had created, dated the day
    before the first row (today with none). Later rows are never counted in it."""
    cut, held = int(conf["cutoff"]), int(conf["cutoff_balance"])
    with store.transaction() as c:
        ctx = load(c, now)
        for m in SK.money_accounts(c, ctx.book.id):
            if str(m["connection_id"] or "") != cid or str(m["kind"]) != "stripe":
                continue
            mid = str(m["id"])
            first = SK.first_feed_day(c, mid)
            day = (dates.day(first) - timedelta(days=1)).isoformat() if first else ctx.today
            banking.set_opening(ctx, m, held - SK.stripe_settled_through(c, mid, cut), day)
        SK.update(c, "fin_connections", cid, {"settings_json": SK.dump({**conf, "backfilled": True}), "cursor": None,
                                              "updated_at": now})


def _walk(store: Any, provider: Provider, cid: str, acct: str, now: str, start: str | None, pages: int, *,
          stop_known: bool) -> Walk:
    """Up to `pages` pages newest first from `start`, each taken in. The walk ends at a page that reaches a row already
    taken in (with stop_known), at the list's end, or at a problem (its cursor is kept, so the page is read again).
    Otherwise the cursor saved is the oldest row read, and the next step continues from there."""
    cursor = start
    for _ in range(pages):
        page = provider.transactions(cursor)
        if not page.ok:
            return cursor, [error_text(page)], False
        rows = listed(page)
        problems, known = _take(store, cid, acct, rows, now, stop_known=stop_known)
        if problems:
            return cursor, problems, False
        ended = known or not has_more(page) or not rows
        cursor = None if ended else str(rows[-1]["id"])
        with store.transaction() as c:
            SK.update(c, "fin_connections", cid, {"cursor": cursor})
        if ended:
            return None, [], True
    return cursor, [], False


def _take(store: Any, cid: str, acct: str, rows: list[dict[str, Any]], now: str, *, stop_known: bool
          ) -> tuple[list[str], bool]:
    """Take a page's rows in, oldest first, each in its own savepoint, in one transaction; then the batch is done (see
    stripe_post.batch_done). With stop_known, rows from the first already taken in onward are not read. A row in another
    currency than the book's is read and not posted. Returns the problems (the page stops at the first failure) and
    whether a known row was reached."""
    problems: list[str] = []
    known = False
    ids = [str(r.get("id") or "") for r in rows]
    with store.transaction() as c:
        ctx = load(c, now)
        base = ctx.book.base_currency
        money = SK.money_account_by_provider(c, cid, acct)
        if money is None:
            return ["the Stripe account has no money account on the books"], False
        cut = len(rows)
        if stop_known:
            taken = SK.provider_rows(c, str(money["id"]), ids)
            for i, tid in enumerate(ids):
                if tid in taken:
                    cut, known = i, True
                    break
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
    return problems, known
