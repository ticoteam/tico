"""The Brex sync worker (spec §9): one Brex connection's cash and card accounts, their balances and their settled
transactions, run by the bank worker (finance/sync.py) for each Brex connection. Brex is read outside any transaction and
what came back is recorded in a short one. Brex has no webhooks, so a connection is read every POLL_MINUTES.

What comes in, and how it maps (read-only; Brex is never written to):
- Each live cash account is a bank money account on a bank asset line (checking). Its balance is Brex's current balance, in
  the book's currency only. Its transactions are read from `posted_at_start` (`history_from`, Jan 1 of this year when the
  connection has none), taken in oldest first by posted day and id whatever order Brex lists them in, and each row is posted
  by banking.ingest with its signed amount.
- Brex documents a cash amount's sign only as "signed", so the sign is checked, not trusted (_sign_check). Until a stable
  balance agrees with the rows taken in since, the cash rows are stored and not posted (connection "sign": "unverified"; the
  history reads Checking amounts). A balance that moved the opposite way flips the sign ("flipped", noted), and the rows post
  with it.
- The one live card account is a card program money account on a credit card liability line, read through the primary
  card transactions. Brex lists no account on a card transaction, so a second live card account stops the sync (its rows
  would be read to the wrong account, or go missing unseen): Finance refuses to connect more than one. A mapped card account
  that Brex no longer lists as live is skipped and noted (its mapping is kept); the other accounts keep syncing. Each Brex
  card a row names gets its fin_cards row on first sight (_ensure_card), so its purchases post to the card liability and to
  their expense line, as a Mercury card's do.
- A card charge (PURCHASE, BNPL_FEE) is money the card owes (negative on the card), a credit back (REFUND, CHARGEBACK,
  REWARDS_CREDIT) positive. A COLLECTION (a payment toward the card) carries no card and is one side of a card payment:
  banking._brex_payment pairs it with the bank's CARD_COLLECTION debit, or posts the bank side alone to the card liability,
  so a payment is never an expense. The direction comes from the type, not from Brex's sign (see core/feeds.brex_card_txn).
  A card type not listed there is posted with Brex's sign under BREX_OTHER (never paired as a payment) and noted in the
  connection's last_error. Card transactions carry no pending state: Brex lists settled ones only.
- Cash rows of BOOK_TRANSFER and INTRA_CUSTOMER_ACCOUNT_BOOK_TRANSFER (money between the company's own Brex accounts) are
  transfers (F.TRANSFER_KINDS), so a pair across two connected accounts is one transfer, not an expense. One with no other
  side yet is noted. Any other cash type posts generic.
- A row Brex sends with no amount is skipped and noted, and its page goes on. A row in another currency than the book's is
  not posted and is noted.
- History is walked once, newest first, a few pages a step. Each cash account's first step brackets its balance with its
  first page (_bracket, as Stripe does): the page's row ids are read before and after the balance is read, so the balance
  holds exactly the rows the page lists. The balance and those ids are kept (`held`, `counted`) and the page is taken in with
  it. When every account has reached its end, each cash account's opening is made from its held balance (banking.open_at),
  not from a later read, so a row posted mid-backfill is left out of the opening and comes in with the catch-up. Each later
  poll re-reads the last CATCH_UP_DAYS (Brex's delayed transactions arrive late); a row already in is updated only when it
  changed (banking.ingest), so a re-read never duplicates, and a re-read row the bracket already counted that predates the
  opening is skipped.
- Known limit: Brex gives dates, not times. A row that is posted after a bracket and reaches Brex's list only later, and
  is older than the catch-up window, is never read again, so the books can miss it. Reconciling the account to a
  statement shows it.

A rehearsal copy calls nothing."""
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
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
from .core.org import SYSTEM
from .handlers import Plan, Result
from .mercury import Answer
from .store import banking as SK
from .store import ledger as SL
from .store import tico

log = logging.getLogger(__name__)

POLL_MINUTES = 15                 # how often a connection is read
BACKFILL_PAGES = 4                # pages of one account's history per step: the backfill is paced across steps
CATCH_UP_PAGES = 50               # pages of one account's recent window per poll
CATCH_UP_DAYS = 30                # the window every poll re-reads: delayed transactions arrive late
ACCOUNT_PAGES = 20                # account-list pages read per call
BRACKET_TRIES = 3                 # reads of a cash account's first page and balance before a backfill step waits
PROBE_SECONDS = 20                # the connect probe's whole budget: its calls and any wait Brex asks for, together
ADMIN_ROLES = ("ACCOUNT_ADMIN", "CARD_ADMIN")   # the Brex roles that can see card payments
NOTE_IDS = 50                     # ids kept per note (the first one is named)
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


def _ids(answer: Answer) -> frozenset[str]:
    """The ids of a page's rows: what a bracket compares before and after the balance is read. A set, so the order Brex
    lists rows in does not matter."""
    return frozenset(str(r.get("id") or "") for r in listed(answer))


def _oldest_first(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """A page's rows by posted day, then id: the order they are taken in, whatever order Brex listed them in."""
    return sorted(rows, key=lambda r: (str(r.get("posted_at_date") or ""), str(r.get("id") or "")))


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
    accounts with their balances (last four only, never a full number). ({}, why) when the token does not work, is not an
    account or card admin (card payments need one), or the company has more than one live card account (Finance reads one)."""
    me = provider.me()
    if not me.ok:
        return {}, error_text(me)
    if str(me.body.get("role") or "").upper() not in ADMIN_ROLES:
        return {}, "Brex needs an account or card admin token to see card payments; this token is not one"
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
    """Read the Brex token from its credential (outside any transaction) and probe Brex with it, within PROBE_SECONDS."""
    with store.read() as c:
        if not tico.credential_exists(c, credential_id) and make is None:
            return {}, "no such credential with a stored token"
    provider = _provider(store, vault, credential_id, make, budget=PROBE_SECONDS)
    if provider is None:
        return {}, "no such credential with a stored token"
    return probe(provider)


def _provider(store: Any, vault: Any, ref: str, make: Make | None, budget: float | None = None) -> Provider | None:
    with store.read() as c:
        token = tico.credential_secret(c, vault, ref) if vault is not None else ""
    if make is not None:
        return cast(Provider, make(token))
    return Brex(token, base=base_url(), budget=budget) if token else None


def connection_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Connect a Brex company with its API token (a Tico credential, read at use time and never copied here). Each live
    cash account becomes a bank money account on a bank line, and the one live card account a card program on a credit
    card liability line, as Mercury's accounts are. `probe` is what probe() read with that token just before. History
    starts at `history_from` (Jan 1 of this year when left out). Connecting the same Brex accounts again picks their money
    accounts back up, so their history is never taken in twice. The cash amounts' sign is unverified until a balance agrees."""
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
            "settings": SK.dump({"backfilled": False, "history_from": history, "sign": "unverified"}),
            "next_sync_at": ctx.at, "created_at": ctx.at, "created_by": who})
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
    problems, live, notes = balances(store, provider, cid, now)
    # The sign guard reads the balance before and after the walk: a stable pass (the same both times) is one it can use.
    checking = not problems and _unchecked(_settings(row))
    before = _cash_total(store, provider, cid, now) if checking else None
    if not problems:
        problems = walk(store, provider, cid, now, live)
    after = _cash_total(store, provider, cid, now) if checking and not problems and before is not None else None
    if after is not None and before == after:
        _sign_check(store, cid, now, after)
    with store.transaction() as c:
        conf = _settings(SK.get(c, "fin_connections", cid))
        going = not problems and not conf.get("backfilled")
        fields: dict[str, object] = {"last_sync_at": now, "updated_at": now,
                                     "next_sync_at": now if going else _after(now, POLL_MINUTES * 60)}
        if problems:
            fields.update({"status": "error", "last_error": problems[0][:300]})
            banking.sync_failed(load(c, now), cid, str(row["status"]), problems[0])
        else:
            trusted = conf.get("sign") != "unverified"
            if trusted:     # a card payment pairs only once its bank side is trusted: a waiting one is paired by _brex_payment
                banking.pair_card_payments(load(c, now), cid)
            waiting = SK.unmatched_transfers(c, cid, F.BREX_BOOK_TRANSFERS) if trusted else 0
            fields.update({"status": "active", "last_error": _notes(conf, notes, waiting), "last_ok_at": now})
            banking.refresh_connection_openings(load(c, now), cid)
        SK.update(c, "fin_connections", cid, fields)
    return "failed" if problems else "ok"


def _failed(store: Any, cid: str, why: str, now: str, prior: str) -> None:
    with store.transaction() as c:
        SK.update(c, "fin_connections", cid, {"status": "error", "last_error": why, "updated_at": now,
                                              "next_sync_at": _after(now, POLL_MINUTES * 60)})
        banking.sync_failed(load(c, now), cid, prior, why)


def _notes(conf: Mapping[str, Any], notes: list[str], waiting: int = 0) -> str | None:
    """What a pass that read is noted as in last_error (the status stays active): this pass's notes; a flipped sign; each Brex
    card type Finance does not map (`unknown`, from the first row of that type read); the rows in another currency
    (`foreign`) and the rows with no amount (`unamounted`), kept as seen; and the book transfers still waiting for their
    other side (`waiting`)."""
    out = list(notes)
    if conf.get("flipped"):
        out.append("Brex's cash amounts had the opposite sign to its balances: Finance reads them with the sign flipped")
    for kind, first in dict(conf.get("unknown") or {}).items():
        out.append(f"Brex card transactions of type {kind} are not mapped: Finance posts them with Brex's sign (first: {first})")
    for cur, ids in dict(conf.get("foreign") or {}).items():
        named = [str(i) for i in ids]
        if named:
            out.append(f"{len(named)} Brex transactions in {cur} are not posted: the books keep one currency "
                       f"(first: {named[0]})")
    unamounted = [str(i) for i in (conf.get("unamounted") or [])]
    if unamounted:
        out.append(f"{len(unamounted)} Brex transactions have no amount and are skipped (first: {unamounted[0]})")
    if waiting:
        out.append(f"{waiting} Brex book transfers have no counterpart on another account yet and are not posted")
    return "; ".join(out)[:300] or None


def balances(store: Any, provider: Provider, cid: str, now: str) -> tuple[list[str], dict[str, set[str]], list[str]]:
    """Each mapped cash account's balance as Brex reports it, in the book's currency only; and the card accounts check. A
    live card account Brex lists that Finance does not map stops the sync: its rows would be read to the wrong account, or go
    missing unseen. A mapped card account Brex no longer lists as live is skipped and noted, its mapping kept. Returns the
    problems, the live account ids by money kind (`bank`, `card_program`) and the notes."""
    cards, why = _all(provider.card_accounts, ACCOUNT_PAGES)
    if why:
        return [why], {}, []
    cash, why = _all(provider.cash_accounts, ACCOUNT_PAGES)
    if why:
        return [why], {}, []
    live_cash = {str(a.get("id") or ""): a for a in cash if _live(a)}
    listed_cards = {str(a.get("id") or "") for a in cards if _live(a)}
    with store.read() as c:
        book = load(c, now).book.id
        mine = [m for m in SK.money_accounts(c, book) if str(m["connection_id"] or "") == cid]
    mapped_cards = {str(m["provider_account_id"] or "") for m in mine if str(m["kind"]) == "card_program"}
    if listed_cards - mapped_cards:
        return [f"Brex's card accounts are not the one Finance maps ({len(listed_cards)} listed); connect Brex again to "
                "map them"], {}, []
    notes = [f"Brex card account {aid} is no longer live: its transactions are not read, and its mapping is kept"
             for aid in sorted(mapped_cards - listed_cards)]
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
    return [], {"bank": set(live_cash), "card_program": listed_cards}, notes


def _unchecked(conf: Mapping[str, Any]) -> bool:
    """Whether a connection's cash amounts are still to be checked: its backfill has ended and no balance has agreed yet."""
    return bool(conf.get("backfilled")) and conf.get("sign") == "unverified"


def _cash_total(store: Any, provider: Provider, cid: str, now: str) -> int | None:
    """The balances of this connection's live, mapped cash accounts summed in the book's currency, as Brex's account list
    reads them now (None when the list cannot be read, or one of those balances is not in the book's currency)."""
    with store.read() as c:
        ctx = load(c, now)
        ids = {str(m["provider_account_id"] or "") for m in SK.money_accounts(c, ctx.book.id)
               if str(m["connection_id"] or "") == cid and str(m["kind"]) == "bank"}
        base = ctx.book.base_currency.upper()
    cash, why = _all(provider.cash_accounts, ACCOUNT_PAGES)
    if why:
        return None
    total = 0
    for a in cash:
        if str(a.get("id") or "") in ids and _live(a):
            amount, currency = _money_of(a.get("current_balance"))
            if amount is None or currency != base:
                return None
            total += amount
    return total


def _put(c: Any, cid: str, now: str, changes: Mapping[str, object], drop: Sequence[str] = ()) -> None:
    """Merge changes into a connection's settings, and drop the keys named, as one write."""
    fresh = {k: v for k, v in _settings(SK.get(c, "fin_connections", cid)).items() if k not in drop}
    SK.update(c, "fin_connections", cid, {"settings": SK.dump({**fresh, **changes}), "updated_at": now})


def _sign_check(store: Any, cid: str, now: str, balance: int) -> None:
    """The sign guard, run on a stable read (the balance was the same before and after the pass's walk). `sign_anchor` is the
    balance at the last stable read and `sign_pending` the Brex amounts of the rows taken in since (_take keeps it, a
    restated row counting as its change). When the balance moved as those rows say, Brex's sign is right and the rows post.
    When it moved the opposite way, Brex sent the sign inverted: the unposted rows flip and each opening is made again from
    the balance (banking.flip_unposted). Anything else (a row not listed yet, a balance that moved with no row) moves the
    anchor to this balance and the rows wait, unposted, for the next read. With no bank account to check, there is nothing to
    check, and the connection is verified."""
    with store.transaction() as c:
        ctx = load(c, now)
        conf = _settings(SK.get(c, "fin_connections", cid))
        if not _unchecked(conf):
            return
        bank = [str(m["id"]) for m in SK.money_accounts(c, ctx.book.id)
                if str(m["connection_id"] or "") == cid and str(m["kind"]) == "bank"]
        if not bank:
            _put(c, cid, now, {"sign": "verified", "flipped": False})
            return
        anchor = conf.get("sign_anchor")
        if anchor is None:
            _put(c, cid, now, {"sign_anchor": balance, "sign_pending": 0})
            return
        pending = int(conf.get("sign_pending") or 0)
        delta = balance - int(anchor)
        if delta == 0 and pending == 0:
            return
        if pending and delta == pending:
            flipped = False
        elif pending and delta == -pending:
            flipped = True
        else:
            _put(c, cid, now, {"sign_anchor": balance, "sign_pending": 0})
            return
        _put(c, cid, now, {"sign": "verified", "flipped": flipped}, drop=("sign_anchor", "sign_pending"))
        if flipped:
            banking.flip_unposted(ctx, bank)
        banking.settle_waiting(ctx, [str(m["id"]) for m in SK.money_accounts(c, ctx.book.id)
                                     if str(m["connection_id"] or "") == cid])


def walk(store: Any, provider: Provider, cid: str, now: str, live: Mapping[str, set[str]]) -> list[str]:
    """This pass's history: the backfill while it runs (a few pages an account a step, cursors kept), else the recent
    window of every account. Only the live accounts are read (`live`, by money kind): a card account Brex no longer
    lists is not."""
    with store.read() as c:
        row = SK.get(c, "fin_connections", cid)
        ctx = load(c, now)
        mine = [m for m in SK.money_accounts(c, ctx.book.id)
                if str(m["connection_id"] or "") == cid and str(m["status"] or "active") == "active"
                and str(m["provider_account_id"] or "") in live.get(str(m["kind"]), set())]
    conf = _settings(row)
    since = str(conf.get("history_from") or f"{now[:4]}-01-01")
    if conf.get("backfilled"):
        counted = {str(k): frozenset(str(x) for x in v) for k, v in dict(conf.get("counted") or {}).items()}
        window = max(_days_before(now, CATCH_UP_DAYS), since)      # never before the day history starts from
        return _catch_up(store, provider, cid, now, mine, _start(window), counted)
    return _backfill(store, provider, cid, now, mine, _start(since), conf, ctx.book.base_currency)


def _page(provider: Provider, m: Any, start: str, cursor: str | None) -> Answer:
    if str(m["kind"]) == "card_program":
        return provider.card_transactions(start, cursor)
    return provider.cash_transactions(str(m["provider_account_id"] or ""), start, cursor)


def _catch_up(store: Any, provider: Provider, cid: str, now: str, mine: list[Any], start: str,
              counted: Mapping[str, Collection[str]]) -> list[str]:
    """Every page of each account's recent window, taken in once by id. Past CATCH_UP_PAGES the window is not whole, and
    the pass says so rather than stop short silently. `counted` is each account's bracket ids (see _take)."""
    for m in mine:
        cursor: str | None = None
        for _ in range(CATCH_UP_PAGES):
            page = _page(provider, m, start, cursor)
            if not page.ok:
                return [error_text(page)]
            problems = _take(store, cid, m, listed(page), now, counted.get(str(m["id"]), ()))
            if problems:
                return problems
            cursor = next_cursor(page)
            if not cursor:
                break
        else:
            return ["Brex lists more transactions in the window than Finance reads"]
    return []


def _backfill(store: Any, provider: Provider, cid: str, now: str, mine: list[Any], start: str,
              conf: Mapping[str, Any], base: str) -> list[str]:
    """The first history: each account from `start` on, newest first, BACKFILL_PAGES pages a step, its cursor saved after
    every page so a failed page is read again and nothing is skipped. A cash account's first step brackets its balance
    with its first page (_bracket), and the balance it holds and the bracket's ids are saved with that page. When every
    account has reached its end, the backfill completes."""
    cursors = {str(k): str(v) for k, v in dict(conf.get("cursors") or {}).items()}
    walked = {str(x) for x in list(conf.get("walked") or [])}
    held: dict[str, int | None] = {str(k): (None if v is None else int(v))
                                   for k, v in dict(conf.get("held") or {}).items()}
    counted: dict[str, list[str]] = {str(k): [str(x) for x in v] for k, v in dict(conf.get("counted") or {}).items()}
    for m in mine:
        mid = str(m["id"])
        if mid in walked:
            continue
        cursor: str | None = cursors.get(mid)
        first: Answer | None = None
        if str(m["kind"]) == "bank" and mid not in held:
            first, held[mid], why = _bracket(provider, m, start, base)
            if first is None:
                return [why]
            counted[mid] = sorted(_ids(first))
        done = False
        for _ in range(BACKFILL_PAGES):
            page = first if first is not None else _page(provider, m, start, cursor)
            first = None
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
            _save(store, cid, now, {"cursors": cursors, "walked": sorted(walked), "held": held, "counted": counted})
            if done:
                break
        if not done:
            return []           # this account goes on from its cursor next step; the others wait their turn
    _complete(store, cid, now)
    return []


def _bracket(provider: Provider, m: Any, start: str, base: str) -> tuple[Answer | None, int | None, str]:
    """A cash account's first page and its balance in the book's currency, read so that they agree (Stripe's bracket,
    stripe_sync._bracket): the page lists the same row ids before and after the balance is read, so no row came in between
    and the balance holds exactly the rows the page starts from. After BRACKET_TRIES disagreements the step gives up and the
    next one reads again. Returns the page, the balance (None when it is not in the book's currency) and the problem."""
    for _ in range(BRACKET_TRIES):
        page = _page(provider, m, start, None)
        if not page.ok:
            return None, None, error_text(page)
        held, why = _balance(provider, str(m["provider_account_id"] or ""), base)
        if why:
            return None, None, why
        again = _page(provider, m, start, None)
        if not again.ok:
            return None, None, error_text(again)
        if _ids(again) == _ids(page):
            return page, held, ""
    return None, None, "Brex's balance changed while it was read: it is read again next time"


def _balance(provider: Provider, account_id: str, base: str) -> tuple[int | None, str]:
    """One cash account's current balance as Brex's account list reports it, in the book's currency (None otherwise)."""
    cash, why = _all(provider.cash_accounts, ACCOUNT_PAGES)
    if why:
        return None, why
    for a in cash:
        if str(a.get("id") or "") == account_id:
            amount, currency = _money_of(a.get("current_balance"))
            return (amount if amount is not None and currency == base.upper() else None), ""
    return None, f"Brex lists no cash account {account_id}: it is read again next time"


def _save(store: Any, cid: str, now: str, changes: Mapping[str, object]) -> None:
    with store.transaction() as c:
        fresh = _settings(SK.get(c, "fin_connections", cid))
        SK.update(c, "fin_connections", cid, {"settings": SK.dump({**fresh, **changes}), "updated_at": now})


def _complete(store: Any, cid: str, now: str) -> None:
    """The backfill's end: the connection is backfilled, and each cash account whose bracket held a balance gets its opening
    from that balance, less every row now taken in (banking.open_at), recorded as made (banking.mark_opened). The held
    balances and the cursors are dropped; the bracket's ids (`counted`) are kept for the catch-up."""
    with store.transaction() as c:
        conf = _settings(SK.get(c, "fin_connections", cid))
        held = dict(conf.get("held") or {})
        done = {k: v for k, v in conf.items() if k not in ("cursors", "walked", "held")}
        done["backfilled"] = True
        SK.update(c, "fin_connections", cid, {"settings": SK.dump(done), "updated_at": now})
        ctx = load(c, now)
        opened: list[str] = []
        for m in SK.money_accounts(c, ctx.book.id):
            mid = str(m["id"])
            if str(m["connection_id"] or "") != cid or str(m["kind"]) != "bank" or held.get(mid) is None \
                    or not m["account_id"]:
                continue
            banking.open_at(ctx, m, int(held[mid]))
            opened.append(mid)
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


def _ensure_card(ctx: Ctx, money: Any, card_id: str) -> None:
    """The fin_cards row a Brex card's rows post to (banking.ingest finds it by provider and id), made on the card's first row
    as Mercury's cards are: on the card program's money account, with no holder (Brex's transaction names none), no budget and
    no limit. A person sets a holder or a budget later; until then the card's spend posts to Other expenses."""
    if SK.card_by_provider(ctx.c, "brex", card_id) is not None:
        return
    SK.insert(ctx.c, "fin_cards", {
        "id": SL.new_id(), "provider": "brex", "provider_card_id": card_id, "holder": "", "budget_id": None,
        "money_account_id": str(money["id"]), "kind": "standing", "limit_amount": None, "limit_interval": None,
        "currency": ctx.book.base_currency, "status": "active", "nickname": "Brex card", "created_at": ctx.at,
        "created_by": SYSTEM, "updated_at": ctx.at})


def _take(store: Any, cid: str, money: Any, rows: list[dict[str, Any]], now: str,
          counted: Collection[str] = ()) -> list[str]:
    """A page of one account's transactions, taken in oldest first (by posted day, then id, whatever order Brex listed them
    in), each under its own savepoint, in one transaction. A row in another currency than the book's is not posted and is
    noted; a row with no amount is skipped and noted, and the page goes on; a row the bracket counted (`counted`) that
    predates the opening is skipped, the opening holding it. A row that cannot be read stops the page: the rows before it stay
    in, and the next pass reads the page again (an unchanged row is then skipped, see _changed). While a cash account's sign is
    unchecked, the Brex amounts of the rows taken in are summed into `sign_pending` (_sign_check)."""
    problems: list[str] = []
    is_card = str(money["kind"]) == "card_program"
    account = str(money["provider_account_id"] or "")
    unknown: dict[str, str] = {}
    unamounted: list[str] = []
    foreign: dict[str, list[str]] = {}
    moved = 0
    with store.transaction() as c:
        ctx = load(c, now)
        conf = _settings(SK.get(c, "fin_connections", cid))
        base = ctx.book.base_currency.upper()
        checking = _unchecked(conf) and not is_card
        flipped = bool(conf.get("flipped")) and not is_card
        opening = SK.opening_entry(ctx.c, ctx.book.id, str(money["id"]))
        opened_day = str(opening["entry_date"]) if opening is not None else ""
        for raw in _oldest_first(rows):
            rid = str(raw.get("id") or "")
            if _money_of(raw.get("amount"))[0] is None:
                unamounted.append(rid)
                continue
            if _currency(raw) != base:
                foreign.setdefault(_currency(raw), []).append(rid)
                continue
            if rid in counted and opened_day and str(raw.get("posted_at_date") or "") < opened_day:
                continue            # the bracket's balance already holds it, and the opening (dated after it) with it
            ctx.c.execute("SAVEPOINT fin_brex")
            try:
                t = F.brex_card_txn(raw, account) if is_card else F.brex_cash_txn(raw, account)
                if flipped:
                    t = replace(t, amount=-t.amount)
                if t.card_id:
                    _ensure_card(ctx, money, t.card_id)
                prior = SK.feed_row(ctx.c, str(money["id"]), t.provider_id)
                taken = _changed(ctx, money, t)
                if taken is not None:
                    banking.ingest(ctx, cid, taken)
                    if checking:
                        moved += t.amount - (int(prior["amount"]) if prior is not None else 0)
            except Exception as exc:
                ctx.c.execute("ROLLBACK TO fin_brex")
                ctx.c.execute("RELEASE fin_brex")
                why = str(exc) if isinstance(exc, FinanceError) else "it could not be posted"
                if not isinstance(exc, FinanceError):
                    log.exception("Finance could not post a Brex transaction")
                problems.append(f"Brex transaction {raw.get('id')}: {why}")
                break
            ctx.c.execute("RELEASE fin_brex")
            kind = str(raw.get("type") or "")
            if is_card and kind not in F.BREX_KNOWN:
                unknown.setdefault(kind or "none", rid)
        if unknown:
            _note_unknown(c, cid, unknown, now)
        _note_rows(c, cid, now, foreign, unamounted, moved if checking else 0)
    return problems


def _note_rows(c: Any, cid: str, now: str, foreign: Mapping[str, Sequence[str]], unamounted: Sequence[str],
               moved: int) -> None:
    """What a page could not take in, kept in the connection's settings: the rows in another currency (`foreign`, by currency)
    and the rows with no amount (`unamounted`), each up to NOTE_IDS ids; and the Brex amounts taken in while the sign is
    unchecked (`sign_pending`)."""
    fresh = _settings(SK.get(c, "fin_connections", cid))
    changes: dict[str, object] = {}
    if foreign:
        seen = {str(k): [str(i) for i in v] for k, v in dict(fresh.get("foreign") or {}).items()}
        for cur, ids in foreign.items():
            seen[cur] = sorted({*seen.get(cur, []), *ids})[:NOTE_IDS]
        changes["foreign"] = seen
    if unamounted:
        changes["unamounted"] = sorted({*[str(i) for i in fresh.get("unamounted") or []], *unamounted})[:NOTE_IDS]
    if moved:
        changes["sign_pending"] = int(fresh.get("sign_pending") or 0) + moved
    if changes:
        SK.update(c, "fin_connections", cid, {"settings": SK.dump({**fresh, **changes}), "updated_at": now})


def _note_unknown(c: Any, cid: str, seen: Mapping[str, str], now: str) -> None:
    """Each Brex card type Finance does not map, with the first row of that type read, kept in the connection's settings
    (`unknown`) so the connection keeps saying so after the pass that read it."""
    fresh = _settings(SK.get(c, "fin_connections", cid))
    known = dict(fresh.get("unknown") or {})
    new = {kind: first for kind, first in seen.items() if kind not in known}
    if new:
        SK.update(c, "fin_connections", cid, {"settings": SK.dump({**fresh, "unknown": {**known, **new}}),
                                              "updated_at": now})
