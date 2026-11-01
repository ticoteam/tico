"""Stripe balance activity in the books (spec §9). The Stripe balance is one money account, and each balance transaction
posts by its own day. A sale is the open invoice it pays when the contact's name matches: one settles it, an invoice
already paid takes a customer credit and a person is asked, and several candidates post nothing and ask. A sale that
matches no invoice takes its posting rule's account, tags and contact (income when no rule covers it) and posts with the
day's summary entry, linked to its row; a rule that asks holds the sale for a person. A row that arrives for a day
already summarized adds an adjusting entry. A refund, dispute or returned refund reverses or restores its sale's own
lines in proportion, and gives back its share of any invoice the sale paid. A payout posts on its day to payouts in
transit and clears when the bank deposit of the same amount arrives: a deposit that looks like Stripe's, and no other
receipt. Called from banking's settle step and from the sync worker, inside the caller's transaction."""
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast

from . import banking, documents, posting, posting_rules
from .context import Ctx
from .core import dates
from .core import feeds as F
from .core import ledger as L
from .core.errors import invalid
from .core.org import SYSTEM
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL

INCOME_CODE = "4000"
FEES_CODE, FEES_NAME = "6200", "Payment processing fees"
TRANSIT_CODE, TRANSIT_NAME = "1060", "Stripe payouts in transit"
STALE_DAYS = 7                   # an item still waiting after this many days is asked about
SUMMARY = "summary"              # the raw copy's mark: the row posts in its day's summary entry
COUNTER = "counter"              # a summarized sale's own counter lines: [account, amount, tags, contact] each
GAVE = "gave"                    # a refund's shares: settlement id -> what it gave that settlement (negative: took back)
SUMMARY_ENTRY = "summary_entry"  # a row that left its summary: the summary entry it was in (reversed; its period holds it)
WAITING = (*F.STRIPE_UNWINDS, *F.STRIPE_PAYOUT_BACK)     # rows that wait for the sale or payout they are about
WAITING_MOST = 200
FAR_PAST = "0000-01-01"

Share = tuple[str, int, dict[str, str], str]     # (account id, amount, tags, contact id) of one line to post



def settle(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> None:
    """Post one Stripe balance transaction the first time it is seen: a row never changes after. What needs another row
    waits as new (see batch_done), and a kind nothing posts is asked about."""
    if row["entry_id"]:
        return
    fid = str(row["id"])
    gross, fee = F.stripe_parts(t.raw)
    if t.kind == F.STRIPE_PAYOUT:
        _payout(ctx, row, money, t)
        return
    if t.kind in F.STRIPE_PAYOUT_BACK:
        _payout_back(ctx, row, money, t)
        return
    if t.kind in (*F.STRIPE_CHARGES, *F.STRIPE_UNWINDS, *F.STRIPE_COSTS):
        if gross == 0 and fee == 0:
            SK.update(ctx.c, "fin_feed_txns", fid, {"status": "ignored", "updated_at": ctx.at})
        elif t.kind in F.STRIPE_CHARGES:
            _sale(ctx, row, money, t, gross, fee)
        elif t.kind in F.STRIPE_UNWINDS:
            _unwind(ctx, row, money, t, gross, fee)
        else:
            _to_summary(ctx, row)
        return
    _ask(ctx, fid, f"Stripe does not post a {t.kind} balance transaction")


def bank_deposit(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> bool:
    """A bank deposit that looks like a Stripe payout clears the one payout of its amount in transit, once. True when it
    is one (it waits as new until its payout is there, or a person picks between several). False leaves any other
    receipt to the posting rules."""
    if str(money["kind"]) != "bank" or t.amount <= 0 or "STRIPE" not in f"{t.counterparty} {t.description}".upper():
        return False
    start, end = _window(t.txn_date)
    found = SK.stripe_transit(ctx.c, ctx.book.id, start=start, end=end, amount=-t.amount, most=WAITING_MOST)
    if len(found) > 1:
        _ask(ctx, str(row["id"]), "several Stripe payouts could be this deposit", "ambiguous")
    elif found:
        _clear(ctx, found[0], row, money)
    return True


def batch_done(ctx: Ctx, money: Any) -> None:
    """At the end of a batch: the waiting rows it may have completed post first, then the day's summary rows."""
    for row in SK.stripe_waiting(ctx.c, str(money["id"]), WAITING, WAITING_MOST):
        settle(ctx, row, money, _txn(row))
    _summarize(ctx, money)


def stale(ctx: Ctx) -> None:
    """Asks about Stripe items that waited past STALE_DAYS: a payout with no deposit, a Stripe-looking deposit with no
    payout, and a refund, dispute or returned payout with nothing to reverse. Each is asked once."""
    cutoff = (dates.day(ctx.today) - timedelta(days=STALE_DAYS)).isoformat()
    for r in SK.stripe_transit(ctx.c, ctx.book.id, start=FAR_PAST, end=cutoff, amount=None, most=WAITING_MOST):
        _ask(ctx, str(r["id"]), "its payout has no bank deposit", "stale")
    for r in SK.stripe_deposits(ctx.c, ctx.book.id, start=FAR_PAST, end=cutoff, amount=None, most=WAITING_MOST):
        _ask(ctx, str(r["id"]), "a Stripe deposit has no payout", "stale")
    for m in SK.money_accounts(ctx.c, ctx.book.id):
        if str(m["kind"]) != "stripe":
            continue
        for r in SK.stripe_waiting(ctx.c, str(m["id"]), WAITING, WAITING_MOST):
            if str(r["txn_date"]) <= cutoff:
                _ask(ctx, str(r["id"]), "it has no sale or payout to post against", "stale")


def fee_of(row: Any) -> int:
    """What a Stripe sale's fee took out of its net, in cents; 0 for any other row (a refund, a payout, a cost)."""
    if str(row["kind"]) not in F.STRIPE_CHARGES:
        return 0
    return F.stripe_parts(SK.loads(row["raw"]))[1]


def fee_line(ctx: Ctx, row: Any) -> dict[str, object] | None:
    """The line a Stripe sale's fee posts to fees, or None when it took no fee. What the sale settles is its gross, so
    the line it settles grosses up by this same fee."""
    fee = fee_of(row)
    return banking.feed_line(_fees(ctx).id, fee) if fee else None


# ------------------------------------------------------------------------------------------------ sales
def _sale(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gross: int, fee: int) -> None:
    """A sale the way a bank receipt is matched: a payment marked from this account first (the sale's fee posts with
    it), then the one open invoice of its gross amount from a contact the billing name matches, then the customer's
    credit when that invoice is paid already. Anything unclear asks and posts nothing; a sale that matches none is
    ordinary (see _ordinary)."""
    fid = str(row["id"])
    if gross <= 0:
        _to_summary(ctx, row, [(_income(ctx).id, -gross, {}, "")])
        return
    fees = [banking.feed_line(_fees(ctx).id, fee)] if fee else []
    match = documents.expected_match(ctx, row, money, replace(t, amount=gross), posted=t, fees=fees)
    if match == documents.ASK:
        _ask(ctx, fid, "several marked payments could be this sale", "ambiguous")
        return
    if match:
        return
    owing = SAP.open_owing(ctx.c, "invoice", ctx.book.id, gross, documents.OWING_MOST + 1)
    if len(owing) > documents.OWING_MOST:
        _ask(ctx, fid, "too many open invoices owe this amount to tell apart", "ambiguous")
        return
    named = documents.named(t.counterparty, owing)
    if len(named) == 1:
        _settle_invoice(ctx, row, money, t, gross, fee, named[0])
        return
    if named or len(owing) > 1:
        _ask(ctx, fid, "several open invoices could be this sale", "ambiguous")
        return
    paid = SAP.paid_owing(ctx.c, "invoice", ctx.book.id, gross, documents.OWING_MOST + 1)
    named_paid = documents.named(t.counterparty, paid)
    if len(paid) > documents.OWING_MOST or len(named_paid) > 1 or (not named_paid and len(paid) > 1):
        _ask(ctx, fid, "several invoices this amount are paid or could be this sale", "ambiguous")
        return
    if named_paid:
        _credit(ctx, row, money, t, gross, fee, named_paid[0])
        return
    _ordinary(ctx, row, money, t, gross)


def _ordinary(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gross: int) -> None:
    """A sale no invoice takes: the first posting rule that covers it says where its gross goes (an account, its tags and
    a contact, as on a bank row), and it posts with the day's summary. A rule that asks holds it for a person; with no
    rule it is income."""
    fid = str(row["id"])
    facts = {"amount": gross, "counterparty": t.counterparty, "description": t.description, "kind": t.kind}
    rule = posting_rules.first_for(ctx, posting_rules.feed_facts(facts, money))
    if rule is None:
        counter: list[Share] = [(_income(ctx).id, -gross, {}, "")]
    elif rule.then.outcome == "post" and posting_rules.usable(ctx, rule.then):
        counter = [(str(line["account_id"]), cast(int, line["amount"]), cast(dict[str, str], line["tags"]),
                    str(line.get("contact_id") or "")) for line in posting_rules.lines_for(ctx, facts, rule)]
    else:
        _ask(ctx, fid, "a posting rule asks a person about this sale")
        return
    _to_summary(ctx, row, counter)


def _settle_invoice(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gross: int, fee: int, doc: Any) -> None:
    """The sale pays the invoice: the receivable settles on the gross, the fee posts as an expense."""
    entry = _post_sale(ctx, row, money, t, gross, fee, documents.control(ctx, "invoice").id, doc)
    documents.settle(ctx, "invoice", str(doc["id"]), amount=gross, day=str(row["txn_date"]), from_kind="feed",
                     from_id=str(row["id"]), entry_id=entry)


def _credit(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gross: int, fee: int, doc: Any) -> None:
    """The invoice it names is paid already: the money is a customer credit on the receivable, never income again, and
    a person decides what it is."""
    _post_sale(ctx, row, money, t, gross, fee, documents.control(ctx, "invoice").id, doc)
    _ask(ctx, str(row["id"]), "the invoice it names is paid already: the money is a customer credit", "paid")


def _post_sale(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gross: int, fee: int, account_id: str, doc: Any) -> str:
    """One entry for a sale against a document: the document's account for the gross, the fee, and the balance's net."""
    contact = str(doc[f"{documents.CONTACT_OF['invoice']}_id"])
    lines = [banking.feed_line(account_id, -gross, {}, contact)]
    if fee:
        lines.append(banking.feed_line(_fees(ctx).id, fee))
    entry = banking.post_row(ctx, str(row["id"]), t, lines, _gl_of(ctx, money).id, [])
    banking.mark_posted(ctx, [str(row["id"])], entry)
    return entry


def _to_summary(ctx: Ctx, row: Any, counter: Sequence[Share] | None = None) -> None:
    """Marks the row for its day's summary entry (see _summarize_day), which posts it with the day's other ordinary rows.
    A sale carries the counter lines it posts to; a Stripe charge of its own (a cost) carries none."""
    raw = {**SK.loads(row["raw"]), SUMMARY: True}
    if counter is not None:
        raw[COUNTER] = [[account, amount, dict(tags), contact] for account, amount, tags, contact in counter]
    SK.update(ctx.c, "fin_feed_txns", str(row["id"]), {"raw": SK.dump(raw), "updated_at": ctx.at})


def _summarize(ctx: Ctx, money: Any) -> None:
    days: dict[str, list[Any]] = {}
    for r in SK.stripe_summary_rows(ctx.c, str(money["id"])):
        days.setdefault(str(r["txn_date"]), []).append(r)
    for day in sorted(days):
        _summarize_day(ctx, money, day, days[day])


def _summarize_day(ctx: Ctx, money: Any, day: str, rows: list[Any]) -> None:
    """A day's ordinary sales and Stripe charges, as summary entries: each sale's counter lines are summed per account,
    tags and contact; the fees and Stripe's own charges are expenses, and the balance takes the nets. A day whose lines
    would pass the journal cap posts as several journals, each balanced."""
    for chunk in _journals(ctx, rows):
        _post_summary(ctx, money, day, chunk)


def _journals(ctx: Ctx, rows: list[Any]) -> list[list[Any]]:
    """The day's rows in consecutive journals that each stay under the line cap: a line per account, tags and contact,
    plus one for the fees and one for the balance."""
    out: list[list[Any]] = []
    current: list[Any] = []
    keys: set[SummaryKey] = set()
    for r in rows:
        grown = keys | _row_keys(ctx, r)
        if current and len(grown) + 2 > L.MAX_LINES:
            out.append(current)
            current, grown = [], _row_keys(ctx, r)
        current.append(r)
        keys = grown
    if current:
        out.append(current)
    return out


SummaryKey = tuple[str, tuple[tuple[str, str], ...], str]     # (account id, tags, contact id) of one summary line


def _row_keys(ctx: Ctx, r: Any) -> set[SummaryKey]:
    """The summary lines a row adds: none for a cost, its counter lines for a charge."""
    if str(r["kind"]) not in F.STRIPE_CHARGES:
        return set()
    return {(account, tuple(sorted(tags.items())), contact) for account, _, tags, contact
            in _carried(ctx, SK.loads(r["raw"]))}


def _post_summary(ctx: Ctx, money: Any, day: str, rows: list[Any]) -> None:
    """One balanced journal for these rows of a day. The first journal of a day takes the day's id; a later batch's rows
    add an adjusting entry for their own rows only."""
    counter: dict[SummaryKey, int] = {}
    fees = net = 0
    for r in rows:
        raw = SK.loads(r["raw"])
        gross, fee = F.stripe_parts(raw)
        net += int(r["amount"])
        fees += fee
        if str(r["kind"]) in F.STRIPE_CHARGES:
            for account, amount, tags, contact in _carried(ctx, raw):
                key = (account, tuple(sorted(tags.items())), contact)
                counter[key] = counter.get(key, 0) + amount
        else:
            fees -= gross
    lines: list[dict[str, object]] = [banking.feed_line(account, amount, dict(tags), contact)
                                      for (account, tags, contact), amount in sorted(counter.items()) if amount]
    if fees:
        lines.append(banking.feed_line(_fees(ctx).id, fees))
    roles: dict[int, str] = {}
    if net:
        roles[len(lines)] = L.MONEY_ROLE
        lines.append(banking.feed_line(_gl_of(ctx, money).id, net))
    base = f"stripe:summary:{money['id']}:{day}"
    if SL.entry_by_source(ctx.c, ctx.book.id, "sync", base) is None:
        source_id = base
    else:
        source_id = banking.fresh_source(ctx, "sync", f"stripe:adjust:{money['id']}:{day}:{rows[0]['id']}")
    done = posting.post_entry(ctx.c, {"date": banking.open_day(ctx, day), "source": "sync", "source_id": source_id,
                                      "memo": f"Stripe sales and fees, {day}", "lines": banking.vendor_lines(ctx, lines)},
                              at=ctx.at, roles=roles)
    banking.mark_posted(ctx, [str(r["id"]) for r in rows], str(done["entry_id"]))


def _carried(ctx: Ctx, raw: Mapping[str, Any]) -> list[Share]:
    """The counter lines a summarized sale carries: the ones its posting rule set, else its gross to income."""
    lines = raw.get(COUNTER)
    if not lines:
        gross, _ = F.stripe_parts(raw)
        return [(_income(ctx).id, -gross, {}, "")]
    return [(str(account), int(amount), {str(k): str(v) for k, v in dict(tags).items()}, str(contact))
            for account, amount, tags, contact in lines]


# ------------------------------------------------------------------------------------------------ refunds and disputes
def _unwind(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, gross: int, fee: int) -> None:
    """A refund or dispute reverses its sale, and a refund failure restores the refund it names. What the sale's settlements
    take back reopens each document they paid, through that document's control account. The rest reverses the sale's own
    counter lines in proportion, its fee is an expense, and the balance takes the net. A sale or refund that has not
    arrived yet waits as new."""
    fid = str(row["id"])
    link = str(t.raw.get("link") or "")
    if not link:
        _ask(ctx, fid, "it names no sale to reverse")
        return
    original = SK.stripe_source(ctx.c, str(money["id"]), F.STRIPE_CHARGES, link)
    if original is None:
        return
    counter = _counter(ctx, original)
    if counter is None:
        return
    if not counter:
        _ask(ctx, fid, "the sale it names has no lines to reverse")
        return
    given = SK.stripe_given(ctx.c, str(money["id"]), F.STRIPE_UNWINDS, link)
    if gross < 0:
        shares = _shares(ctx, original, gross, given)
    else:
        # A restore undoes exactly the refund it names, so it gives back what that refund gave, and nothing more.
        source = str(t.raw.get("source") or "")
        if not source:
            _ask(ctx, fid, "it names no refund to restore")
            return
        # A refund fails once. A repeat of a failure already undone changes nothing, so the row is recorded and left.
        if SK.stripe_restored(ctx.c, str(money["id"]), F.STRIPE_UNWINDS, source):
            SK.update(ctx.c, "fin_feed_txns", fid, {"status": "ignored", "updated_at": ctx.at})
            return
        failed = SK.stripe_reversal(ctx.c, str(money["id"]), F.STRIPE_UNWINDS, source)
        if failed is None:
            return
        shares = _restores(ctx, original, failed)
    # The plain part is what this refund takes from the sale itself: the refunds so far, less what settlements gave back.
    before = SK.stripe_refunded(ctx.c, str(money["id"]), F.STRIPE_UNWINDS, link) - sum(given.values())
    plain = -gross - sum(move for _, move in shares)
    lines = [banking.feed_line(account, amount, tags, contact) for account, amount, tags, contact
             in _reversal(counter, before, before + plain)]
    lines += [_reopened(ctx, s, move) for s, move in shares if move]
    if fee:
        lines.append(banking.feed_line(_fees(ctx).id, fee))
    entry = banking.post_row(ctx, fid, t, lines, _gl_of(ctx, money).id, [])
    banking.mark_posted(ctx, [fid], entry)
    gave = _give_back(ctx, row, shares, entry)
    if gave:
        raw = {**SK.loads(row["raw"]), GAVE: gave}
        SK.update(ctx.c, "fin_feed_txns", fid, {"raw": SK.dump(raw), "updated_at": ctx.at})


def _shares(ctx: Ctx, original: Any, gross: int, given: Mapping[str, int]) -> list[tuple[Any, int]]:
    """What each settlement of the refunded sale gives back for this refund. A refund shares out over what each
    settlement still holds after the refunds before it, so a settlement made later takes none of an earlier refund, and
    nothing goes past what a settlement received."""
    live = SAP.settlements_from(ctx.c, "feed", str(original["id"]))
    remaining = [max(int(s["amount"]) - max(given.get(str(s["id"]), 0), 0), 0) for s in live]
    return list(zip(live, _allocate(min(-gross, sum(remaining)), remaining), strict=True))


def _restores(ctx: Ctx, original: Any, failed: Any) -> list[tuple[Any, int]]:
    """What a restore takes back from each settlement: exactly what the refund it undoes gave that settlement (kept on
    that refund as `gave`). A refund that gave nothing back restores nothing, whatever the other refunds gave."""
    gave = cast(Mapping[str, int], cast(Mapping[str, Any], SK.loads(failed["raw"])).get(GAVE) or {})
    live = SAP.settlements_from(ctx.c, "feed", str(original["id"]))
    return [(s, -int(gave.get(str(s["id"]), 0))) for s in live]


def _reopened(ctx: Ctx, settlement: Any, move: int) -> dict[str, object]:
    """The line that owes a settled document again: its control account takes what the settlement gave back, the way
    the settlement moved it (a receivable is a debit, a payable a credit), so the books match the document."""
    kind = str(settlement["to_kind"])
    doc = SAP.get(ctx.c, SAP.DOCS[kind][0], str(settlement["to_id"]))
    return banking.feed_line(documents.control(ctx, kind).id, -documents.SIGN[kind] * move, {},
                             str(doc[f"{documents.CONTACT_OF[kind]}_id"]))


def _give_back(ctx: Ctx, row: Any, shares: Sequence[tuple[Any, int]], entry: str) -> dict[str, int]:
    """Each document a settlement gave back owes that much again: a negative settlement from the refund's row, on its
    entry. Returns what this refund gave each settlement, kept on the refund as `gave`."""
    gave: dict[str, int] = {}
    for s, move in shares:
        if move:
            gave[str(s["id"])] = move
            documents.settle(ctx, str(s["to_kind"]), str(s["to_id"]), amount=-move, day=str(row["txn_date"]),
                             from_kind="feed", from_id=str(row["id"]), entry_id=entry)
    return gave


def _counter(ctx: Ctx, original: Any) -> list[Share] | None:
    """The counter lines a sale posted (negative), to be reversed. A summarized sale has no lines of its own (the day's
    entry is shared), so its carried lines are what it posted to. None while the sale is not posted yet."""
    raw = SK.loads(original["raw"])
    if raw.get(SUMMARY):
        return _carried(ctx, raw)
    if not original["entry_id"]:
        return None
    return [(line.account_id, line.amount, dict(line.tags), line.contact_id)
            for _, line in SL.entry_lines(ctx.c, str(original["entry_id"]))
            if line.role != L.MONEY_ROLE and line.amount < 0]


def _reversal(counter: Sequence[Share], before: int, now: int) -> list[Share]:
    """The counter lines a refund reverses: each line's share of what the sale has refunded in all, so far, less its
    share before this refund. The shares are cumulative, so separate refunds add up to the same as one, to the cent.
    Counter lines are credits, so a refund debits them."""
    weights = [abs(amount) for _, amount, _, _ in counter]
    out: list[Share] = []
    for (account, amount, tags, contact), new, old in zip(counter, _allocate(now, weights), _allocate(before, weights),
                                                           strict=True):
        part = (new - old) if amount < 0 else (old - new)
        if part:
            out.append((account, part, tags, contact))
    return out


def _allocate(total: int, weights: Sequence[int]) -> list[int]:
    """Each weight's share of `total` in whole cents, by largest remainder: the floors first, then the cents left over go
    to the largest fractions, earlier weights first. The shares add up to `total` exactly."""
    base = sum(weights)
    if not base:
        return [0] * len(weights)
    shares = [total * w // base for w in weights]
    left = total - sum(shares)
    for i in sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % base), i))[:left]:
        shares[i] += 1
    return shares


# ------------------------------------------------------------------------------------------------ payouts
def _payout(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> None:
    """The balance paid out, on the payout's day: the amount is in transit until a Stripe-looking deposit of it arrives
    within MATCH_DAYS (or has already)."""
    fid = str(row["id"])
    entry = banking.post_row(ctx, fid, t, [banking.feed_line(_transit(ctx).id, -t.amount)], _gl_of(ctx, money).id, [])
    banking.mark_posted(ctx, [fid], entry)
    start, end = _window(t.txn_date)
    found = SK.stripe_deposits(ctx.c, ctx.book.id, start=start, end=end, amount=-t.amount, most=WAITING_MOST)
    if len(found) > 1:
        _ask(ctx, fid, "several bank deposits could be this payout", "ambiguous")
    elif found:
        _clear(ctx, row, found[0], SK.get(ctx.c, "fin_money_accounts", str(found[0]["money_account_id"])))


def _payout_back(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> None:
    """A payout the bank or Stripe sent back to the balance: its transit amount returns. A payout already cleared at the
    bank cannot be returned by this path, so a person is asked. A payout not yet here waits as new."""
    fid = str(row["id"])
    link = str(t.raw.get("link") or "")
    payout = SK.stripe_source(ctx.c, str(money["id"]), ("payout",), link) if link else None
    if payout is None:
        return
    if SK.stripe_cleared(ctx.c, str(payout["id"])):
        _ask(ctx, fid, "its payout already reached the bank", "cleared")
        return
    entry = banking.post_row(ctx, fid, t, [banking.feed_line(_transit(ctx).id, -t.amount)], _gl_of(ctx, money).id, [])
    banking.mark_posted(ctx, [fid], entry)


def _clear(ctx: Ctx, payout: Any, deposit: Any, bank: Any) -> None:
    """The bank deposit of a payout: Dr the bank account, Cr payouts in transit, one entry for both sides, and the
    deposit is posted by it (the payout posted on its own day already)."""
    amount = int(deposit["amount"])
    day = max(str(payout["txn_date"]), str(deposit["txn_date"]))
    lines = [banking.feed_line(_gl_of(ctx, bank).id, amount), banking.feed_line(_transit(ctx).id, -amount)]
    done = posting.post_entry(ctx.c, {
        "date": banking.open_day(ctx, day), "source": "bank",
        "source_id": banking.fresh_source(ctx, "bank", f"stripe:clear:{payout['id']}"),
        "memo": f"Stripe payout to {bank['name']}"[:200], "lines": lines}, at=ctx.at, roles={0: L.MONEY_ROLE})
    banking.mark_posted(ctx, [str(deposit["id"])], str(done["entry_id"]))


# ------------------------------------------------------------------------------------------------ shared
def _txn(row: Any) -> F.FeedTxn:
    """A stored Stripe row as the balance transaction it was taken in from (what settle reads)."""
    return F.FeedTxn(provider_id=str(row["provider_id"]), account_id="", amount=int(row["amount"]),
                     txn_date=str(row["txn_date"]), status=F.POSTED, kind=str(row["kind"]),
                     counterparty=str(row["counterparty"] or ""), description=str(row["description"] or ""),
                     version=int(row["version"]), raw=SK.loads(row["raw"]))


def _ask(ctx: Ctx, fid: str, why: str, key: str = "") -> None:
    banking.event(ctx, banking.CATEGORY_EVENT, fid, {"feed_id": fid, "why": why}, key)


def _window(day: str) -> tuple[str, str]:
    d = dates.day(day)
    return (d - timedelta(days=banking.MATCH_DAYS)).isoformat(), (d + timedelta(days=banking.MATCH_DAYS)).isoformat()


def _gl_of(ctx: Ctx, money: Any) -> L.Account:
    account = ctx.accounts.get(str(money["account_id"] or ""))
    if account is None:
        raise invalid("that money account has no account in the books")
    return account


def _income(ctx: Ctx) -> L.Account:
    found = banking.by_code(ctx, INCOME_CODE)
    if found is None:
        raise invalid("the books have no Revenue account")
    return found


def _fees(ctx: Ctx) -> L.Account:
    return banking.by_code(ctx, FEES_CODE) or banking.new_account(ctx, FEES_CODE, FEES_NAME, "expense", "", SYSTEM)


def _transit(ctx: Ctx) -> L.Account:
    found = next((a for a in ctx.accounts.values() if a.name == TRANSIT_NAME and a.type == "asset"), None)
    return found or banking.new_account(ctx, TRANSIT_CODE, TRANSIT_NAME, "asset", "", SYSTEM)
