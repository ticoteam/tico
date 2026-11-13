# pyright: strict
"""Growth's Finance ad spend source (spec "Growth fills its own numbers", part 3): the spend Finance already imports
from Google Ads and Meta (backend/finance/ad_import.py, fin_ad_spends), added up per channel and month for Growth's
cohort rows. The sync engine (backend/growth_sync.py) owns the source, its runs, the months window, who wins and
health; this module only answers what a run of kind `finance_ad_spend` writes.

- Money. Growth reads the amount in the book's currency, as Finance does; nothing is converted. A counting ad account
  in another currency (its own, or a day's spend in one) makes the run fail with that account's name, so the source
  shows Failing with that reason and nothing already written changes.
- Mapping. A person maps each campaign once to a channel (`{"campaigns": {"<campaign key>": "<channel slug>"}}`, keys
  from `campaign_key`); a campaign left unmapped, mapped to nothing or to "unknown" counts on Unknown. A day's spend
  belongs to the month of the day the platform reports.
- Totals only. A run's rows hold a channel, a month and the spend: never a campaign's name, id or line. The campaign
  list is for the mapping step alone, and it and connecting are for someone who sees every connected ad account in
  Finance (a finance admin, or a reader with everything in view); anyone else is refused, naming what is missing.
- Refresh. Finance's import calls each of its AFTER_RUN hooks after a good run; the engine hangs the run of every
  source of this kind there, so a restated day reaches Growth within one refresh.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .finance.context import load as finance_load
from .finance.core import rights as finance_rights
from .finance.core.errors import FinanceError
from .finance.store import ads as SA
from .store import Problem

KIND = "finance_ad_spend"
FILLS = ("spend",)
UNKNOWN = "unknown"
MISSING_TEXT = "Connecting Finance ad spend needs someone who sees every ad account in Finance"


class Failing(Exception):
    """The run cannot read Finance's spend; the message is the source's reason, shown as it is."""


@dataclass(frozen=True)
class SpendRow:
    """One channel's spend in one month, in the book's currency (dollars, not cents). channel None is Unknown."""

    month: str
    channel: str | None
    spend: float


def campaign_key(platform: str, account_external_id: str, campaign_external_id: str) -> str:
    """A campaign as the mapping names it: the platform's own ids, so a tool set up again keeps its mapping."""
    return f"{platform}:{account_external_id}:{campaign_external_id}"


def _label(platform: str, account_external_id: str) -> str:
    return f"{SA.LABEL.get(platform, platform)} {SA.account_label(platform, account_external_id)}"


def _book_currency(c: sqlite3.Connection) -> str:
    try:
        return finance_load(c).book.base_currency
    except FinanceError:
        raise Failing("Finance is not set up, so it has no ad spend to read") from None


def may_connect(c: sqlite3.Connection, actor: str) -> None:
    """Refused (403) unless the actor sees every connected ad account in Finance; also refused (409) when Finance has
    no ad account to read. Growth's own owner-or-admin rule is the engine's."""
    try:
        ctx = finance_load(c)
    except FinanceError:
        raise Problem("conflict", "Finance is not set up, so it has no ad spend to read", 409) from None
    accounts = SA.counting_accounts(c)
    if not accounts:
        raise Problem("conflict", "Finance has no Google Ads or Meta ad spend import connected", 409)
    if finance_rights.is_admin(ctx.org, actor, ctx.rights(actor)) or ctx.view(actor).everything:
        return
    platforms = sorted({SA.LABEL.get(a.platform, a.platform) for a in accounts})
    raise Problem(
        "forbidden",
        f"{MISSING_TEXT}: you do not see the {len(accounts)} connected ad account"
        f"{'' if len(accounts) == 1 else 's'} ({', '.join(platforms)}). A finance admin can connect it.",
        403,
    )


def campaigns(c: sqlite3.Connection, actor: str, start: str, end: str) -> dict[str, Any]:
    """The mapping step: the connected ad accounts and each campaign with spend over the days [start, end], newest month's
    spend first. Only for someone who may connect (may_connect)."""
    may_connect(c, actor)
    accounts = SA.counting_accounts(c)
    totals: dict[str, dict[str, Any]] = {}
    for r in SA.campaign_months(c, start, end):
        key = campaign_key(r.platform, r.account_external_id, r.campaign_external_id)
        row = totals.setdefault(
            key,
            {
                "key": key,
                "name": r.campaign_name,
                "platform": r.platform,
                "account": SA.account_label(r.platform, r.account_external_id),
                "currency": r.currency,
                "last_month": r.month,
                "last_month_spend": 0.0,
            },
        )
        if r.month > row["last_month"]:
            row.update(last_month=r.month, last_month_spend=0.0)
        if r.month == row["last_month"]:
            row["last_month_spend"] = round(row["last_month_spend"] + r.amount / 100, 2)
    return {
        "accounts": [
            {"platform": a.platform, "account": SA.account_label(a.platform, a.external_id), "name": a.name}
            for a in sorted(accounts, key=lambda a: (a.platform, a.external_id))
        ],
        "campaigns": sorted(totals.values(), key=lambda x: (-x["last_month_spend"], x["name"], x["key"])),
    }


def _channel(mapping: Mapping[str, Any], key: str) -> str | None:
    raw: Any = mapping.get("campaigns")
    named: object = cast(dict[str, object], raw).get(key) if isinstance(raw, dict) else None
    slug = str(named).strip() if named else ""
    return None if not slug or slug == UNKNOWN else slug


def rows(c: sqlite3.Connection, mapping: Mapping[str, Any], start: str, end: str) -> list[SpendRow]:
    """What a run writes for the days [start, end]: spend per channel and month in the book's currency, Unknown for an
    unmapped campaign. Raises Failing when Finance is not set up or an account is in another currency."""
    currency = _book_currency(c)
    for a in SA.counting_accounts(c):
        if a.currency.upper() != currency:
            raise Failing(
                f"{_label(a.platform, a.external_id)} spends in {a.currency.upper()}; Finance's books are in {currency}"
            )
    spent = SA.campaign_months(c, start, end)
    totals: dict[tuple[str, str | None], int] = {}
    for r in spent:
        if r.currency.upper() != currency:
            raise Failing(
                f"{_label(r.platform, r.account_external_id)} spends in {r.currency.upper()}; Finance's books are in {currency}"
            )
        key = (r.month, _channel(mapping, campaign_key(r.platform, r.account_external_id, r.campaign_external_id)))
        totals[key] = totals.get(key, 0) + r.amount
    return [
        SpendRow(month, channel, round(cents / 100, 2))
        for (month, channel), cents in sorted(totals.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]


def held(c: sqlite3.Connection) -> tuple[str, str] | None:
    """(first day, last day) of the spend Finance holds on counting accounts; None when it holds none."""
    ids = [a.id for a in SA.counting_accounts(c)]
    if not ids:
        return None
    marks = ",".join("?" * len(ids))
    row = c.execute(f"SELECT MIN(day), MAX(day) FROM fin_ad_spends WHERE ad_account_id IN ({marks})", ids).fetchone()
    return (str(row[0]), str(row[1])) if row and row[0] else None


def window(today: str, months: int) -> tuple[str, str]:
    """The days from the first of the month `months - 1` months before `today`'s, to `today` (YYYY-MM-DD)."""
    year, month = int(today[:4]), int(today[5:7]) - (months - 1)
    while month < 1:
        year, month = year - 1, month + 12
    return f"{year:04d}-{month:02d}-01", today[:10]


MONTHS_MAX = 12


def install(app: Any, store: Any, auth: Any) -> None:
    from fastapi import Request

    from . import brand as _brand_mod
    from . import hubdb as H

    brand: Any = _brand_mod

    @app.get("/api/v2/growth/finance-campaigns")
    def growth_finance_campaigns(request: Request, months: int = MONTHS_MAX) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Connecting Finance ad spend, the mapping step: Finance's connected ad accounts and each campaign with spend in
        the newest `months` months (1 to 12), with its newest month's spend. For the Growth owner or an admin who sees
        every connected ad account in Finance; Growth's own reads show channel-month totals only."""
        who = request.state.identity
        auth.domain(who)
        if not brand.admin(auth, who):
            raise Problem("forbidden", "Only the owner or an admin connects a source", 403)
        if not 1 <= months <= MONTHS_MAX:
            raise Problem("validation", f"months is between 1 and {MONTHS_MAX}", 422)
        start, end = window(str(H.now()), months)
        with store.read() as c:
            return campaigns(c, who.actor, start, end)
