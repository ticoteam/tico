"""Budget cutoffs (spec §4.4, §9). A `decline` budget at its limit on the bot's AI services path is one more usage
limit a bot can be over, so the bot takes no new job and reads "Paused: AI services budget reached". Video production is
not a run limit (a run need not make images): at its cutoff it refuses new image making for whoever it covers, people
included, and pauses no bot. One `Cutoffs` serves a whole pass over many bots: the budgets' states are read once."""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import budgeting
from .context import Ctx, load
from .core import budgets as B
from .core import dates
from .setup import AI, VIDEO
from .store import budgets as SB
from .store import ledger as SL

PERIOD_WORDS = {"month": "monthly", "quarter": "quarterly", "year": "yearly", "once": "budget"}


@dataclass(frozen=True)
class Cutoff:
    """The budget a bot is held by: its id and name, its period's word, and when that period ends (UTC midnight after
    its last day; "" for a one-time budget, which never turns over). No amounts: who reads a paused bot may not see
    the budget's numbers."""
    budget_id: str
    name: str
    period: str
    until: str


@dataclass
class Cutoffs:
    ctx: Ctx | None
    cut: dict[str, B.State]
    lines: tuple[str, ...]
    _seen: dict[str, Cutoff | None] = field(default_factory=lambda: dict[str, Cutoff | None]())

    def of(self, bot: str) -> Cutoff | None:
        """The cutoff holding `bot`, or None."""
        return self.holding("bot:" + bot)

    def holding(self, mate: str) -> Cutoff | None:
        """The cutoff holding teammate `mate` (`bot:<slug>` or `human:<id>`), or None."""
        if not self.cut or self.ctx is None:
            return None
        if mate not in self._seen:
            self._seen[mate] = self._find(self.ctx, mate)
        return self._seen[mate]

    def _find(self, ctx: Ctx, mate: str) -> Cutoff | None:
        """The first budget on the teammate's path whose spend (actuals and holds) has reached its amount. Money set aside
        in an allocation, the teammate's own or a Gateway key's or another teammate's, is not spend, so it pauses no bot
        on its own. A new request that no reserve leaves room for is refused where it is placed (runs.hold), by the
        budget's remaining, which counts reserves (§4.4)."""
        today = dates.day(ctx.today)
        for account in self.lines:
            found = budgeting.leaf(ctx, account, teammate=mate)
            path = budgeting.chain_of(ctx, found.id) if found else []
            for b in B.chain(ctx.budgets(), found.id) if found else []:
                if b.id not in self.cut:
                    continue
                st = budgeting.state(ctx, b.id, today, "teammate:" + mate, path)
                if st.at_limit:
                    end = date.fromisoformat(st.period_end)
                    until = "" if end >= B.ONCE_END else (end + timedelta(days=1)).isoformat() + "T00:00:00Z"
                    return Cutoff(budget_id=b.id, name=b.name, period=PERIOD_WORDS.get(b.period, b.period),
                                  until=until)
        return None


def cutoffs(c: sqlite3.Connection, at: str | None = None, line: str = AI) -> Cutoffs:
    """The `decline` budgets whose spend has reached their amount now, read against each teammate's path on `line` (an
    account code; AI services for usage limits). Spend is actuals and holds (§4.4): an allocation sets money aside but is
    not spend, so a Gateway key's allocation reserving the whole budget pauses no bot. Cheap when Finance is not set up or
    no such budget has an amount."""
    book = SL.book(c)
    if book is None:
        return Cutoffs(ctx=None, cut={}, lines=())
    budgets = {b.id: b for b in SB.budgets(c, book.id).values() if b.active and b.at_limit == "decline"}
    if not budgets:
        return Cutoffs(ctx=None, cut={}, lines=())
    versions = SB.versions(c, list(budgets))
    if not any(versions.get(bid) for bid in budgets):
        return Cutoffs(ctx=None, cut={}, lines=())
    ctx = load(c, at)
    lines = tuple(a.id for a in ctx.accounts.values() if a.code == line)
    today = dates.day(ctx.today)
    cut: dict[str, B.State] = {}
    for bid in budgets:
        if not versions.get(bid):
            continue
        st = budgeting.state(ctx, bid, today)
        if st.at_limit and st.amount is not None:
            cut[bid] = st
    return Cutoffs(ctx=ctx, cut=cut, lines=lines)


def video_cutoff(c: sqlite3.Connection, actor: str, at: str | None = None) -> Cutoff | None:
    """The Video production `decline` budget at its cutoff that refuses `actor`'s new image making, or None: at its
    limit a budget refuses new requests (spec §4.4). A person follows the budget their own spend counts against."""
    return cutoffs(c, at, VIDEO).holding(actor)
