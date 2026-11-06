"""The Brex sync worker (spec §9): one Brex connection's cash and card accounts, their balances and their settled
transactions, run by the bank worker (finance/sync.py) for each Brex connection. Brex is read outside any transaction and
what came back is recorded in a short one. Brex has no webhooks, so a connection is read every POLL_MINUTES.

What comes in, and how it maps (read-only; Brex is never written to):
- Each live cash account is a bank money account on a bank asset line (checking). Its balance is Brex's current and
  available balance, in the book's currency only. Its transactions are read from `posted_at_start` (`history_from`, Jan 1
  of this year when the connection has none) and each row is posted by banking.ingest with its signed amount.
- The one live card account is a card program money account on a credit card liability line, read through the primary
  card transactions. Brex lists no account on a card transaction, so a second live card account stops the sync (its rows
  would be read to the wrong account): Finance refuses to connect more than one.
- A card charge (PURCHASE, BNPL_FEE) is money the card owes (negative on the card), a credit back (REFUND, CHARGEBACK,
  REWARDS_CREDIT) positive, and a COLLECTION (a payment toward the card) positive and paired with the bank's CARD_COLLECTION
  debit as Mercury's autopay is (banking._card_payment). The direction comes from the type, not from Brex's sign (see
  core/feeds.brex_card_txn). Card transactions carry no pending state: Brex lists settled ones only.
- History is walked once: the backfill keeps each account's cursor between steps, a few pages a step, and completes when
  every account has reached its end; then each cash account's opening balance is made from the balance (banking.opening_balance).
  Each later poll re-reads the last CATCH_UP_DAYS (Brex's delayed transactions arrive late); a row already in is updated
  only when it changed (banking.ingest), so a re-read never duplicates.
- Known limit: Brex gives dates, not times, so a row posted in the seconds between the balance read and the last history
  page of the backfill's completing pass can leave the opening off by that row. Reconciling the account to a statement
  shows it.

A rehearsal copy calls nothing."""
import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from . import banking
from .brex import Brex, Provider, base_url, error_text, listed, next_cursor
from .context import Ctx, load
from .core import feeds as F
from .core import kinds as K
from .core import validate as V
from .core.errors import CONFLICT, FinanceError, invalid
from .handlers import Plan, Result
from .mercury import Answer
from .store import banking as SK
from .store import tico

log = logging.getLogger(__name__)

POLL_MINUTES = 15                 # how often a connection is read
BACKFILL_PAGES = 4                # pages of one account's history per step: the backfill is paced across steps
CATCH_UP_PAGES = 50               # pages of one account's recent window per poll
CATCH_UP_DAYS = 30                # the window every poll re-reads: delayed transactions arrive late
ACCOUNT_PAGES = 20                # account-list pages read per call
Make = Callable[[str], Any]       # a factory from the token to a provider (tests pass a fake)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _after(at: str, seconds: float) -> str:
    moment = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _settings(row: Any) -> dict[str, Any]:
    return SK.loads(row["settings"])


def _start(day: str) -> str:
    """A day as the RFC 3339 date-time Brex's posted_at_start takes."""
    return f"{day}T00:00:00Z"


def _days_before(now: str, days: int) -> str:
    return (date.fromisoformat(now[:10]) - timedelta(days=days)).isoformat()


def _live(account: Mapping[str, Any]) -> bool:
    """A Brex account listed as ACTIVE (its documented status). A missing status is read as active."""
    return str(account.get("status") or "ACTIVE").upper() == "ACTIVE"


def _money_of(value: object) -> tuple[int | None, str]:
    """A Brex Money object as (whole minor units, currency), or (None, "") when Brex sent none."""
    if not isinstance(value, dict):
        return None, ""
    amount = cast(dict[str, Any], value).get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int):
        return None, ""
    return amount, str(cast(dict[str, Any], value).get("currency") or "USD").upper()


def _currency(raw: Mapping[str, Any]) -> str:
    money = raw.get("amount")
    return str(cast(dict[str, Any], money).get("currency") or "USD").upper() if isinstance(money, dict) else "USD"


def _all(read: Callable[[str | None], Answer], pages: int) -> tuple[list[dict[str, Any]], str]:
    """One of Brex's account lists read to its end, cursor by cursor: its rows, and why it could not be read whole."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(pages):
        got = read(cursor)
        if not got.ok:
            return rows, error_text(got)
        rows += listed(got)
        cursor = next_cursor(got)
        if not cursor:
            return rows, ""
    return rows, "Brex lists more accounts than Finance reads"


def _cash_row(a: Mapping[str, Any]) -> dict[str, object]:
    """A cash account for the probe: its name, the last four digits of its account number (the full number is never
    kept), and its balances."""
    current, currency = _money_of(a.get("current_balance"))
    available, _ = _money_of(a.get("available_balance"))
    return {"id": str(a.get("id") or ""), "name": str(a.get("name") or "Brex")[:120],
            "last4": F.last4(a.get("account_number") or ""), "current": current, "available": available,
            "currency": currency}


def _card_row(a: Mapping[str, Any]) -> dict[str, object]:
    """A card account for the probe. Brex's CardAccount lists no name and no number (only its id, status, balances and
    statement period), so Finance names it Brex card and keeps no digits."""
    return {"id": str(a.get("id") or ""), "name": str(a.get("name") or "Brex card")[:120], "last4": ""}


# ------------------------------------------------------------------------------------------------ connect probe
def probe(provider: Provider) -> tuple[dict[str, object], str]:
    """What connection_create needs, read with the token before the action: the live card accounts and the live cash
    accounts with their balances (last four only, never a full number). ({}, why) when the token does not work or the
    company has more than one live card account (Finance reads one)."""
    cards, why = _all(provider.card_accounts, ACCOUNT_PAGES)
    if why:
        return {}, why
    cash, why = _all(provider.cash_accounts, ACCOUNT_PAGES)
    if why:
        return {}, why
    live_cards = [a for a in cards if _live(a)]
    if len(live_cards) > 1:
        return {}, f"Brex lists {len(live_cards)} card accounts; Finance reads one card account per connection"
    return {"card": [_card_row(a) for a in live_cards], "cash": [_cash_row(a) for a in cash if _live(a)]}, ""


def probe_credential(store: Any, vault: Any, credential_id: str, make: Make | None = None
                     ) -> tuple[dict[str, object], str]:
    """Read the Brex token from its credential (outside any transaction) and probe Brex with it."""
    with store.read() as c:
        if not tico.credential_exists(c, credential_id) and make is None:
            return {}, "no such credential with a stored token"
    provider = _provider(store, vault, credential_id, make)
    if provider is None:
        return {}, "no such credential with a stored token"
    return probe(provider)


def _provider(store: Any, vault: Any, ref: str, make: Make | None) -> Provider | None:
    with store.read() as c:
        token = tico.credential_secret(c, vault, ref) if vault is not None else ""
    if make is not None:
        return cast(Provider, make(token))
    return Brex(token, base=base_url()) if token else None


def connection_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Connect a Brex company with its API token (a Tico credential, read at use time and never copied here). Each live
    cash account becomes a bank money account on a bank line, and the one live card account a card program on a credit
    card liability line, as Mercury's accounts are. `probe` is what probe() read with that token just before. History
    starts at `history_from` (Jan 1 of this year when left out). Connecting the same Brex accounts again picks their money
    accounts back up, so their history is never taken in twice."""
    V.only(p, ("provider", "credential_id", "name", "probe", "history_from"))
    cred = V.text(p, "credential_id", 120)
    if any(str(r["credential_ref"] or "") == cred and str(r["status"]) != "disconnected" for r in SK.connections(ctx.c)):
        raise FinanceError(CONFLICT, "that credential is already connected")
    probed = V.obj(p.get("probe"), "probe")
    cards = [V.obj(a, "a card account") for a in V.items(probed.get("card"), "probe.card")]
    cash = [V.obj(a, "a cash account") for a in V.items(probed.get("cash"), "probe.cash")]
    if not cards and not cash:
        raise invalid("the token sees no Brex accounts")
    if len(cards) > 1:
        raise invalid("Finance reads one Brex card account per connection")
    history = V.opt_text(p, "history_from", 10) or f"{ctx.today[:4]}-01-01"
    try:
        date.fromisoformat(history)
    except ValueError:
        raise invalid("history_from is a day, YYYY-MM-DD") from None
    name = V.opt_text(p, "name", 120) or "Brex"
    base = ctx.book.base_currency

    def apply(aid: str) -> Result:
        cid = SK.insert(ctx.c, "fin_connections", {
            "provider": "brex", "purpose": "bank", "credential_ref": cred, "status": "active", "name": name,
            "settings": SK.dump({"backfilled": False, "history_from": history}), "next_sync_at": ctx.at,
            "created_at": ctx.at, "created_by": who})
        mapped: list[Result] = []
        for a in cash:
            row = _in_base(a, base)
            if (kept := banking.reuse_money(ctx, cid, row, "brex")) is not None:
                mapped.append(kept)
                continue
            bank = banking.by_code(ctx, banking.BANK)
            used = bank is not None and any(str(r["account_id"]) == bank.id for r in SK.money_accounts(ctx.c, ctx.book.id))
            four = str(row.get("last4") or "")
            named = F.bare_name(row.get("name") or "Brex", four)
            gl = bank if bank is not None and not used else banking.new_account(
                ctx, banking.BANK, f"{named} ••{four}" if four else named, "asset", "bank", who)
            mapped.append(banking.new_money(ctx, cid, row, "bank", "checking", gl, who, provider="brex"))
        for a in cards:
            if (kept := banking.reuse_money(ctx, cid, a, "brex")) is not None:
                mapped.append(kept)
                continue
            line = banking.by_code(ctx, banking.CARDS)
            gl = line if line is not None and not any(
                str(r["account_id"]) == line.id for r in SK.money_accounts(ctx.c, ctx.book.id)) else None
            if gl is None:
                gl = banking.new_account(ctx, banking.CARDS, "Credit cards" if line is None else "Brex card",
                                         "liability", "credit_card", who)
            mapped.append(banking.new_money(ctx, cid, a, "card_program", "credit", gl, who, provider="brex"))
        return {"connection_id": cid, "money_accounts": mapped, "payments_enabled": False, "approval_note": None}
    return Plan(K.ALLOW, f"Connect Brex ({len(cash)} cash, {len(cards)} card)", apply)


def _in_base(row: Mapping[str, Any], base: str) -> dict[str, Any]:
    """A probe's cash row with its balances only when they are in the book's currency (a money account's balance is)."""
    out = dict(row)
    if str(row.get("currency") or base).upper() != base.upper():
        out["current"] = out["available"] = None
    return out


# ------------------------------------------------------------------------------------------------ one step
def connection(store: Any, vault: Any, cid: str, *, rehearsal: bool = False, make: Make | None = None,
               at: str | None = None) -> str:
    """Sync one Brex connection. `withheld` in rehearsal, `gone`, `idle`, `no_token`, else `ok` or `failed`."""
    now = at or _now()
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
    if row is None or str(row["status"]) == "disconnected":
        return "gone"
    if rehearsal:
        return "withheld"
    due = not row["next_sync_at"] or str(row["next_sync_at"]) <= now
    if not due:
        return "idle"
    provider = _provider(store, vault, str(row["credential_ref"] or ""), make)
    if provider is None:
        _failed(store, cid, "the Brex token is missing; store it again in Settings", now, str(row["status"]))
        return "no_token"
    problems, live = balances(store, provider, cid, now)
    if not problems:
        problems = walk(store, provider, cid, now, live)
    with store.transaction() as c:
        going = not problems and not _settings(SK.get(c, "fin_connections", cid)).get("backfilled")
        fields: dict[str, object] = {"last_sync_at": now, "updated_at": now,
                                     "next_sync_at": now if going else _after(now, POLL_MINUTES * 60)}
        if problems:
            fields.update({"status": "error", "last_error": problems[0][:300]})
            banking.sync_failed(load(c, now), cid, str(row["status"]), problems[0])
        else:
            fields.update({"status": "active", "last_error": None, "last_ok_at": now})
            banking.refresh_connection_openings(load(c, now), cid)
        SK.update(c, "fin_connections", cid, fields)
    return "failed" if problems else "ok"


def _failed(store: Any, cid: str, why: str, now: str, prior: str) -> None:
    with store.transaction() as c:
        SK.update(c, "fin_connections", cid, {"status": "error", "last_error": why, "updated_at": now,
                                              "next_sync_at": _after(now, POLL_MINUTES * 60)})
        banking.sync_failed(load(c, now), cid, prior, why)


def balances(store: Any, provider: Provider, cid: str, now: str) -> tuple[list[str], set[str]]:
    """Each mapped cash account's balance as Brex reports it, in the book's currency only; and the card accounts check: the
    live card accounts Brex lists must be exactly the card account Finance maps, or the sync stops (its rows would be read
    to the wrong account, or not at all). Returns the problems and the live cash account ids."""
    cards, why = _all(provider.card_accounts, ACCOUNT_PAGES)
    if why:
        return [why], set()
    cash, why = _all(provider.cash_accounts, ACCOUNT_PAGES)
    if why:
        return [why], set()
    live_cash = {str(a.get("id") or ""): a for a in cash if _live(a)}
    listed_cards = {str(a.get("id") or "") for a in cards if _live(a)}
    with store.read() as c:
        book = load(c, now).book.id
        mine = [m for m in SK.money_accounts(c, book) if str(m["connection_id"] or "") == cid]
    mapped_cards = {str(m["provider_account_id"] or "") for m in mine if str(m["kind"]) == "card_program"}
    if listed_cards != mapped_cards:
        return [f"Brex's card accounts are not the one Finance maps ({len(listed_cards)} listed); connect Brex again to "
                "map them"], set(live_cash)
    with store.transaction() as c:
        ctx = load(c, now)
        base = ctx.book.base_currency.upper()
        for m in mine:
            a = live_cash.get(str(m["provider_account_id"] or "")) if str(m["kind"]) == "bank" else None
            if a is None:
                continue
            current, currency = _money_of(a.get("current_balance"))
            available, _ = _money_of(a.get("available_balance"))
            in_base = currency == base
            banking.set_balance(ctx, str(m["id"]), current if in_base else None,
                                available if in_base else None, now)
    return [], set(live_cash)


def walk(store: Any, provider: Provider, cid: str, now: str, live: set[str]) -> list[str]:
    """This pass's history: the backfill while it runs (a few pages an account a step, cursors kept), else the recent
    window of every account. Only the card account and the live cash accounts are read."""
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
        ctx = load(c, now)
        mine = [m for m in SK.money_accounts(c, ctx.book.id)
                if str(m["connection_id"] or "") == cid and str(m["status"] or "active") == "active"
                and (str(m["kind"]) == "card_program"
                     or (str(m["kind"]) == "bank" and str(m["provider_account_id"] or "") in live))]
    conf = _settings(row)
    if conf.get("backfilled"):
        return _catch_up(store, provider, cid, now, mine, _start(_days_before(now, CATCH_UP_DAYS)))
    return _backfill(store, provider, cid, now, mine, _start(str(conf.get("history_from") or f"{now[:4]}-01-01")), conf)


def _page(provider: Provider, m: Any, start: str, cursor: str | None) -> Answer:
    if str(m["kind"]) == "card_program":
        return provider.card_transactions(start, cursor)
    return provider.cash_transactions(str(m["provider_account_id"] or ""), start, cursor)


def _catch_up(store: Any, provider: Provider, cid: str, now: str, mine: list[Any], start: str) -> list[str]:
    """Every page of each account's recent window, taken in once by id. Past CATCH_UP_PAGES the window is not whole, and
    the pass says so rather than stop short silently."""
    for m in mine:
        cursor: str | None = None
        for _ in range(CATCH_UP_PAGES):
            page = _page(provider, m, start, cursor)
            if not page.ok:
                return [error_text(page)]
            problems = _take(store, cid, m, listed(page), now)
            if problems:
                return problems
            cursor = next_cursor(page)
            if not cursor:
                break
        else:
            return ["Brex lists more transactions in the window than Finance reads"]
    return []


def _backfill(store: Any, provider: Provider, cid: str, now: str, mine: list[Any], start: str,
              conf: Mapping[str, Any]) -> list[str]:
    """The first history: each account from `start` on, BACKFILL_PAGES pages a step, its cursor saved after every page so a
    failed page is read again and nothing is skipped. When every account has reached its end, the backfill completes."""
    cursors = {str(k): str(v) for k, v in dict(conf.get("cursors") or {}).items()}
    walked = {str(x) for x in list(conf.get("walked") or [])}
    for m in mine:
        mid = str(m["id"])
        if mid in walked:
            continue
        cursor: str | None = cursors.get(mid)
        done = False
        for _ in range(BACKFILL_PAGES):
            page = _page(provider, m, start, cursor)
            if not page.ok:
                return [error_text(page)]
            problems = _take(store, cid, m, listed(page), now)
            if problems:
                return problems
            cursor = next_cursor(page)
            if cursor:
                cursors[mid] = cursor
            else:
                cursors.pop(mid, None)
                walked.add(mid)
                done = True
            _save(store, cid, now, {"cursors": cursors, "walked": sorted(walked)})
            if done:
                break
        if not done:
            return []           # this account goes on from its cursor next step; the others wait their turn
    _complete(store, cid, now)
    return []


def _save(store: Any, cid: str, now: str, changes: Mapping[str, object]) -> None:
    with store.transaction() as c:
        fresh = _settings(SK.get(c, "fin_connections", cid))
        SK.update(c, "fin_connections", cid, {"settings": SK.dump({**fresh, **changes}), "updated_at": now})


def _complete(store: Any, cid: str, now: str) -> None:
    """The backfill's end: the connection is backfilled, and each cash account with a balance gets its opening (the balance
    Brex reported less the rows now taken in, banking.opening_balance), recorded as made (banking.mark_opened)."""
    with store.transaction() as c:
        conf = _settings(SK.get(c, "fin_connections", cid))
        done = {k: v for k, v in conf.items() if k not in ("cursors", "walked")}
        done["backfilled"] = True
        SK.update(c, "fin_connections", cid, {"settings": SK.dump(done), "updated_at": now})
        ctx = load(c, now)
        opened: list[str] = []
        for m in SK.money_accounts(c, ctx.book.id):
            if str(m["connection_id"] or "") != cid or str(m["kind"]) != "bank":
                continue
            if m["balance"] is not None and m["account_id"]:
                banking.opening_balance(ctx, str(m["id"]))
                opened.append(str(m["id"]))
        banking.mark_opened(ctx, cid, opened)


def _changed(ctx: Ctx, money: Any, t: F.FeedTxn) -> F.FeedTxn | None:
    """What banking.ingest is given for one Brex row: the row itself when it is new; None when it is in and unchanged (a
    re-read changes nothing); the row with the next version when Brex has changed it since it was taken in, so ingest
    restates it. Brex sends no version, so the stored raw body is what is compared."""
    row = SK.feed_row(ctx.c, str(money["id"]), t.provider_id)
    if row is None:
        return t
    if SK.loads(row["raw"]) == t.raw:
        return None
    return replace(t, version=int(row["version"]) + 1)


def _take(store: Any, cid: str, money: Any, rows: list[dict[str, Any]], now: str) -> list[str]:
    """A page of one account's transactions, oldest first, each taken in under its own savepoint, in one transaction. A
    row in another currency than the book's is not posted. A row that cannot be read stops the page: the rows before it
    stay in, and the next pass reads the page again (an unchanged row is then skipped, see _changed)."""
    problems: list[str] = []
    is_card = str(money["kind"]) == "card_program"
    account = str(money["provider_account_id"] or "")
    with store.transaction() as c:
        ctx = load(c, now)
        base = ctx.book.base_currency.upper()
        for raw in reversed(rows):
            if _currency(raw) != base:
                continue
            ctx.c.execute("SAVEPOINT fin_brex")
            try:
                t = _changed(ctx, money, F.brex_card_txn(raw, account) if is_card else F.brex_cash_txn(raw, account))
                if t is not None:
                    banking.ingest(ctx, cid, t)
            except Exception as exc:
                ctx.c.execute("ROLLBACK TO fin_brex")
                ctx.c.execute("RELEASE fin_brex")
                why = str(exc) if isinstance(exc, FinanceError) else "it could not be posted"
                if not isinstance(exc, FinanceError):
                    log.exception("Finance could not post a Brex transaction")
                problems.append(f"Brex transaction {raw.get('id')}: {why}")
                break
            ctx.c.execute("RELEASE fin_brex")
    return problems
