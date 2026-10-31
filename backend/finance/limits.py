"""Budget cutoffs as usage limits (spec §9): a `decline` budget at its limit is one more limit a bot can be over, for
every bot it covers, so the bot takes no new job and reads "Paused: AI services budget reached". One `Cutoffs` serves a
whole pass over many bots: the budgets' states are read once."""
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
    its last day). No amounts: who reads a paused bot may not see the budget's numbers."""
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
        if not self.cut or self.ctx is None:
            return None
        if bot not in self._seen:
            self._seen[bot] = self._find(self.ctx, bot)
        return self._seen[bot]

    def _find(self, ctx: Ctx, bot: str) -> Cutoff | None:
        for account in self.lines:
            found = budgeting.leaf(ctx, account, teammate="bot:" + bot)
            for b in B.chain(ctx.budgets(), found.id) if found else []:
                st = self.cut.get(b.id)
                if st is not None:
                    end = date.fromisoformat(st.period_end) + timedelta(days=1)
                    return Cutoff(budget_id=b.id, name=b.name, period=PERIOD_WORDS.get(b.period, b.period),
                                  until=end.isoformat() + "T00:00:00Z")
        return None


def cutoffs(c: sqlite3.Connection, at: str | None = None) -> Cutoffs:
    """The `decline` budgets over AI services and Video production that are at their limit now. Cheap when Finance is
    not set up or no such budget has an amount."""
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
    lines = tuple(a.id for a in ctx.accounts.values() if a.code in (AI, VIDEO))
    today = dates.day(ctx.today)
    cut: dict[str, B.State] = {}
    for bid in budgets:
        if not versions.get(bid):
            continue
        st = budgeting.state(ctx, bid, today)
        if st.at_limit and st.amount is not None:
            cut[bid] = st
    return Cutoffs(ctx=ctx, cut=cut, lines=lines)
