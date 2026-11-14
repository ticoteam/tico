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
- Runs. `sync` reads the months the engine's window asks for (the first good run 12 months back, later ones the
  current month and the two before) and hands the rows, or the reason it failed, to growth_sync.run. "Sync now" runs
  it, and so does `refresh`, which the app hangs on Finance's import (ad_import.AFTER_RUN): a restated day reaches
  Growth within one refresh. A channel-month the source filled before that gets no spend now (a campaign remapped
  away, Finance's import off, the channel retired) is set to 0, so Growth's month total keeps equal to Finance's.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
            {
                "platform": a.platform,
                "account": SA.account_label(a.platform, a.external_id),
                "label": _label(a.platform, a.external_id),
                "name": a.name,
            }
            for a in sorted(accounts, key=lambda a: (a.platform, a.external_id))
        ],
        "campaigns": sorted(totals.values(), key=lambda x: (-x["last_month_spend"], x["name"], x["key"])),
    }


def _channel(mapping: Mapping[str, Any], key: str) -> str | None:
    raw: Any = mapping.get("campaigns")
    named: object = cast(dict[str, object], raw).get(key) if isinstance(raw, dict) else None
    slug = str(named).strip() if named else ""
    return None if not slug or slug == UNKNOWN else slug


def _same_currency(platform: str, account: str, spent_in: str, currency: str) -> None:
    if spent_in.upper() != currency:
        raise Failing(f"{_label(platform, account)} spends in {spent_in.upper()}; Finance's books are in {currency}")


def _currency(c: sqlite3.Connection) -> str:
    """The book's currency, once every counting account is in it; else Failing, naming the first that is not."""
    currency = _book_currency(c)
    for a in SA.counting_accounts(c):
        _same_currency(a.platform, a.external_id, a.currency, currency)
    return currency


def rows(c: sqlite3.Connection, mapping: Mapping[str, Any], start: str, end: str) -> list[SpendRow]:
    """What a run writes for the days [start, end]: spend per channel and month in the book's currency, Unknown for an
    unmapped campaign. Raises Failing when Finance is not set up or an account is in another currency."""
    currency = _currency(c)
    spent = SA.campaign_months(c, start, end)
    totals: dict[tuple[str, str | None], int] = {}
    for r in spent:
        _same_currency(r.platform, r.account_external_id, r.currency, currency)
        key = (r.month, _channel(mapping, campaign_key(r.platform, r.account_external_id, r.campaign_external_id)))
        totals[key] = totals.get(key, 0) + r.amount
    return [
        SpendRow(month, channel, round(cents / 100, 2))
        for (month, channel), cents in sorted(totals.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]


def weeks(
    c: sqlite3.Connection, mapping: Mapping[str, Any], start: str, end: str
) -> dict[tuple[str | None, str], float]:
    """Each Monday-to-Sunday week's spend per channel over the days [start, end], in the book's currency:
    {(channel slug, or None for Unknown, the week's Monday YYYY-MM-DD): spend}. For Growth's weekly CAC, read when a
    weekly cohort is, from Finance's daily spend; the cohort rows hold only months. A mapped channel is read as the sync
    engine reads it, a live channel's slug or name in any case; a campaign mapped to anything else counts nowhere, as a
    run leaves it unwritten. Raises Failing as `rows` does."""
    from . import growth_sync as GS

    currency = _currency(c)
    known = GS._channels(c)  # pyright: ignore[reportPrivateUsage]
    slugs = {str(r[0]): str(r[1]) for r in c.execute("SELECT id, slug FROM growth_channels WHERE retired_at IS NULL")}
    totals: dict[tuple[str | None, str], int] = {}
    for r in SA.campaign_days(c, start, end):
        _same_currency(r.platform, r.account_external_id, r.currency, currency)
        named = _channel(mapping, campaign_key(r.platform, r.account_external_id, r.campaign_external_id))
        if named is not None and named.lower() not in known:
            continue
        day = date.fromisoformat(r.day)
        monday = (day - timedelta(days=day.weekday())).isoformat()
        key = (slugs[known[named.lower()]] if named is not None else None, monday)
        totals[key] = totals.get(key, 0) + r.amount
    return {key: round(cents / 100, 2) for key, cents in totals.items()}


def window(today: str, months: int) -> tuple[str, str]:
    """The days from the first of the month `months - 1` months before `today`'s, to `today` (YYYY-MM-DD)."""
    year, month = int(today[:4]), int(today[5:7]) - (months - 1)
    while month < 1:
        year, month = year - 1, month + 12
    return f"{year:04d}-{month:02d}-01", today[:10]


MONTHS_MAX = 12
CAMPAIGNS_MAX = 2000
SYSTEM = "system"  # who a refresh after Finance's import runs as, in the run log and events


def check_map(given: Mapping[str, Any]) -> dict[str, Any]:
    """A Finance ad spend mapping, before the engine's own check: it fills spend only (the default), and `campaigns`
    maps campaign keys to channel slugs ("unknown" or blank: Unknown)."""
    raw: Any = given.get("fields") or {"spend": "spend"}
    if not isinstance(raw, dict) or set(cast(dict[str, Any], raw)) != set(FILLS):
        raise Problem("validation", "Finance ad spend fills spend only", 422)
    named: Any = given.get("campaigns") or {}
    if not isinstance(named, dict):
        raise Problem("validation", "campaigns maps a campaign key to a channel slug", 422)
    pairs = cast(dict[Any, Any], named)
    if len(pairs) > CAMPAIGNS_MAX:
        raise Problem("validation", f"At most {CAMPAIGNS_MAX} campaigns are mapped", 422)
    campaigns_map = {str(k).strip(): str(v or "").strip().lower() for k, v in pairs.items() if str(k).strip()}
    return {**given, "fields": {"spend": "spend"}, "campaigns": campaigns_map}


def public_map(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """The mapping as every Growth reader sees it: how many campaigns are mapped, never their keys (totals only)."""
    named: Any = mapping.get("campaigns") or {}
    kept = {k: v for k, v in mapping.items() if k != "campaigns"}
    return {**kept, "campaigns_mapped": len(cast(dict[str, Any], named)) if isinstance(named, dict) else 0}


def sync(c: sqlite3.Connection, actor: str, source: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    """One run of a Finance ad spend source: the months the engine's window takes, from Finance, into growth_sync.run.
    A read that fails is a failed run with its reason; nothing already written changes."""
    from . import growth_sync as GS

    at = at or datetime.now(timezone.utc)
    oldest, _ = GS.window(not source.get("last_good_run_at"), at)
    start, end = f"{oldest}-01", at.strftime("%Y-%m-%d")
    mapping = GS.sync_map(source)
    try:
        found = rows(c, mapping, start, end)
    except Failing as failed:
        return GS.run(c, actor, source, error=str(failed), at=at)
    sent = [{"month": r.month, "channel": r.channel or "", "spend": r.spend} for r in found]
    sent += _emptied(c, str(source["id"]), found, oldest, at.strftime("%Y-%m"))
    result = GS.run(c, actor, source, rows=sent, at=at)
    if result.get("ok"):
        result["written"] = int(result.get("written") or 0) + _empty_retired(
            c, actor, str(source["id"]), str(result["run"]), oldest, at.strftime("%Y-%m")
        )
    return result


def _filled(c: sqlite3.Connection, sid: str, oldest: str, newest: str, retired: bool) -> list[sqlite3.Row]:
    """The channel-months in [oldest, newest] whose spend this source filled: Unknown and live channels, or retired
    ones."""
    return c.execute(
        "SELECT k.id, k.month, coalesce(k.channel_id, '') AS channel_id, coalesce(ch.slug, '') AS slug, k.spend "
        "FROM growth_origins o JOIN growth_cohorts k ON k.id=o.cohort_id "
        "LEFT JOIN growth_channels ch ON ch.id=k.channel_id "
        "WHERE o.field='spend' AND o.origin='synced' AND o.source_id=? AND k.month BETWEEN ? AND ? AND "
        + ("ch.retired_at IS NOT NULL " if retired else "(k.channel_id IS NULL OR ch.retired_at IS NULL) ")
        + "ORDER BY k.month, slug",
        (sid, oldest, newest),
    ).fetchall()


def _emptied(c: sqlite3.Connection, sid: str, found: list[SpendRow], oldest: str, newest: str) -> list[dict[str, Any]]:
    """A 0 for each live channel-month (or Unknown) in [oldest, newest] whose spend this source filled before and that
    gets none now (a campaign remapped away, or Finance's import off), so Growth's month total keeps equal to Finance's.
    Sent by the live channel's slug, which the engine resolves before any name, so it never names another channel."""
    from . import growth_sync as GS

    known = GS._channels(c)  # pyright: ignore[reportPrivateUsage]
    now = {(r.month, known.get(r.channel.lower(), r.channel) if r.channel else "") for r in found}
    return [
        {"month": str(r["month"]), "channel": str(r["slug"]), "spend": 0.0}
        for r in _filled(c, sid, oldest, newest, retired=False)
        if (str(r["month"]), str(r["channel_id"])) not in now
    ]


def _empty_retired(c: sqlite3.Connection, actor: str, sid: str, run: str, oldest: str, newest: str) -> int:
    """After a good run: a retired channel's spend this source filled in the window goes to 0. The engine writes only
    live channels, and a retired channel's spend is now counted where its campaigns map (or listed as not written),
    so keeping it would count the month's spend twice. Its origin stays this source, at this run."""
    from . import hubdb as H

    ids = [str(r["id"]) for r in _filled(c, sid, oldest, newest, retired=True) if r["spend"]]
    for cid in ids:
        c.execute("UPDATE growth_cohorts SET spend=0, updated_by=?, updated_at=? WHERE id=?", (actor, H.now(), cid))
        c.execute(
            "UPDATE growth_origins SET run_id=?, at=? WHERE cohort_id=? AND field='spend' AND source_id=?",
            (run, H.now(), cid, sid),
        )
    return len(ids)


def refresh(store: Any, at: str) -> None:
    """After a good Finance ad import: run every live Finance ad spend source, each in its own transaction so one
    failing never holds back another."""
    with store.read() as c:
        ids = [
            str(r[0])
            for r in c.execute(
                "SELECT id FROM gtm_sources WHERE sync_kind=? AND retired_at IS NULL ORDER BY created_at, id", (KIND,)
            )
        ]
    when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    for sid in ids:
        with store.transaction() as c:
            row = c.execute("SELECT * FROM gtm_sources WHERE id=? AND retired_at IS NULL", (sid,)).fetchone()
            if row is not None and row["sync_kind"] == KIND:
                sync(c, SYSTEM, dict(row), at=when)


def install(app: Any, store: Any, auth: Any) -> None:
    from fastapi import Request

    from . import brand as _brand_mod
    from . import hubdb as H

    brand: Any = _brand_mod
    from .finance import ad_import

    if refresh not in ad_import.AFTER_RUN:
        ad_import.AFTER_RUN.append(refresh)

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
