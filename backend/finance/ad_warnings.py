"""Ad budget warnings for the spending bot. After each ad spend import pass (ad_import.pass_once, every 15 minutes and
after every import), each active budget whose spend the platforms feed (budgeting's live read) and whose teammate is a
bot is checked against three levels: its warn level (80% by default), its amount, and a month-end pace over its amount.
A level reached for the first time in the period (budgeting.event_key: a new amount arms it again) gives that bot one
Finance line at the start of its next turn: a keeper note. Never a person and never the bot's manager; a budget with no
bot gets none. Warn only: nothing is paused, blocked or refused, and the budget's own at-limit action is untouched (the
books-based crossings still run as before, but their owner notice is skipped for these budgets, jobs.drain)."""
import sqlite3
from dataclasses import dataclass

from . import budgeting
from .context import Ctx, is_set_up, load
from .core import budgets as B
from .core import dates
from .store import ads as SA
from .store import budgets as SB
from .store import tico

LEVELS = ("limit", "warn", "pace")        # the strongest first: one line names the strongest new level
PERIOD_WORDS = {"month": "month", "quarter": "quarter"}


@dataclass(frozen=True)
class Sent:
    budget_id: str
    bot: str
    levels: tuple[str, ...]
    text: str


def bot_of(ctx: Ctx, b: B.Budget) -> str:
    """The bot a budget's ad warnings go to ("" when they go to nobody): its teammate, when that is a bot on the org
    chart and the platforms' spend counts in the budget this period."""
    if not b.active or not b.teammate.startswith("bot:") or b.teammate not in ctx.org.bots:
        return ""
    start, end = budgeting.period_window(ctx, b.id)
    return b.teammate if budgeting.live_ads(ctx, b, start, end).covered else ""


def _money(cents: int, currency: str) -> str:
    whole = (abs(cents) + 50) // 100
    sign = "-" if cents < 0 else ""
    return f"{sign}${whole:,}" if currency == "USD" else f"{sign}{whole:,} {currency}"


def _period_label(b: B.Budget, st: B.State) -> str:
    start, end = dates.day(st.period_start), dates.day(st.period_end)
    if b.period == "month":
        return start.strftime("%B")
    if start.year == end.year:
        return f"{start.strftime('%b')}–{end.strftime('%b %Y')}"
    return f"{start.strftime('%b %Y')}–{end.strftime('%b %Y')}"


def levels_reached(b: B.Budget, st: B.State) -> list[str]:
    """The levels a live state stands at: limit (spend reached the amount), warn (its warn level), pace (the period's
    projected end is over the amount, while still under it)."""
    if st.amount is None or st.amount <= 0:
        return []
    out: list[str] = []
    if st.at_limit:
        out.append("limit")
    if st.crossed_warn:
        out.append("warn")
    if not st.at_limit and st.pace_end is not None and st.pace_end > st.amount:
        out.append("pace")
    return out


def words(b: B.Budget, st: B.State, levels: tuple[str, ...], platform: str, currency: str) -> str:
    """The Finance line for the strongest new level."""
    assert st.amount is not None
    period = PERIOD_WORDS.get(b.period, "period")
    spent, amount = _money(st.spent, currency), _money(st.amount, currency)
    pct = (st.spent * 100 + st.amount // 2) // st.amount
    pace = _money(st.pace_end, currency) if st.pace_end is not None else ""
    over = st.pace_end is not None and st.pace_end > st.amount
    if "limit" in levels:
        line = f"Spent {spent} of {amount} ({pct}%): the {period}'s budget is used up."
    elif "warn" in levels:
        line = f"Spent {spent} of {amount} ({pct}%), past the {b.warn_pct}% warning level."
        if over:
            line += f" At today's pace the {period} ends near {pace}, which is over budget."
    else:
        line = f"Spent {spent} of {amount} ({pct}%), but at today's pace the {period} ends near {pace}, which is over budget."
    head = " · ".join(x for x in (b.name, platform, _period_label(b, st)) if x)
    return f"Finance · budget warning\n{head}\n{line}\nThis is a warning only. Nothing has been paused."


def check(ctx: Ctx) -> list[Sent]:
    """Give each ad budget's bot its new warning levels, once per level per period. What was sent."""
    if not budgeting.ad_imports(ctx):
        return []
    today = dates.day(ctx.today)
    sent: list[Sent] = []
    for b in sorted(ctx.budgets().values(), key=lambda x: x.id):
        bot = bot_of(ctx, b)
        if not bot:
            continue
        st = budgeting.state(ctx, b.id, today, live=True)
        reached = levels_reached(b, st)
        if not reached:
            continue
        version = max((v.version for v in SB.versions(ctx.c, [b.id])[b.id]), default=0)
        key = budgeting.event_key(st.period_start, version)
        slug = bot.partition(":")[2]
        new = tuple(level for level in LEVELS if level in reached and
                    SA.warn_once(ctx.c, b.id, key, level, slug, ctx.at))
        if not new:
            continue
        platforms = [budgeting.budget_platform(ctx, b)] if b.vendor else \
            sorted(budgeting.live_ads(ctx, b, st.period_start, st.period_end).platforms)
        label = " + ".join(SA.LABEL[p] for p in platforms if p)
        text = words(b, st, new, label, ctx.book.base_currency)
        tico.bot_note(ctx.c, bot, text)
        sent.append(Sent(b.id, bot, new, text))
    return sent


def run(c: sqlite3.Connection, at: str | None = None) -> list[Sent]:
    """One warnings pass inside the caller's transaction."""
    if not is_set_up(c) or not SA.ready(c):
        return []
    return check(load(c, at))
