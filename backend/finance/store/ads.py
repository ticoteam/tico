"""SQL for the ad spend import: each tool's import settings, the ad accounts its login sees (and the bot each counts
for), the platforms' daily spend per campaign, and the budget warnings bots were given from it.

The tables come from migration 20261106071500_finance_ad_spend; `ready` tells a database without them (an old test
copy), and every read answers empty there."""

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .ledger import new_id

PLATFORMS = ("google_ads", "meta_ads")
# The tool service (backend/tool_services.py) each platform imports from.
SERVICE = {"google_ads": "google-ads", "meta_ads": "meta-ads"}
PLATFORM_OF = {v: k for k, v in SERVICE.items()}
LABEL = {"google_ads": "Google Ads", "meta_ads": "Meta Ads"}
# The vendor contact each platform's spend books under (ad_import makes it in native books): a line or hold filed under
# it is the platform's, whatever its descriptor says.
VENDOR = {"google_ads": "Google Ads", "meta_ads": "Meta"}
_CONTACT = {name.casefold(): p for p, name in VENDOR.items()}
# The names a mirrored book's own vendor for a platform goes by, first match wins (QuickBooks and Xero keep their own
# contacts, and Tico never makes one there).
MIRROR_NAMES = {
    "google_ads": ("Google Ads", "Google", "Google LLC"),
    "meta_ads": (
        "Meta",
        "Meta Ads",
        "Facebook",
        "Facebook Ads",
        "Meta Platforms",
        "Meta Platforms Inc",
        "Meta Platforms, Inc.",
    ),
}
# A card or bank descriptor that is a platform's ad charge. Tight on purpose: GOOGLE *ADS, GOOGLE ADS and GOOGLE*ADS but
# not GOOGLE ADSENSE (nothing but digits or punctuation may follow ADS); FACEBK *, FB.ME/ADS, FACEBOOK ADS and META ADS
# but not Meta's other charges (Workplace, Quest, Verified, Blueprint) or a bare FACEBOOK or META PLATFORMS.
DESCRIPTORS = {
    "google_ads": re.compile(r"\bgoogle\s*\*?\s*ads(?![a-z])", re.IGNORECASE),
    "meta_ads": re.compile(
        r"\bfacebk\s*\*|\bfb\.me/ads(?![a-z])|\bfacebook\s*ads(?![a-z])|\bmeta\s*ads(?![a-z])", re.IGNORECASE
    ),
}


@dataclass(frozen=True)
class AdImport:
    id: str
    tool_id: str
    platform: str
    enabled: bool
    history_from: str
    account_id: str = ""  # the books line spend books as
    credentials: dict[str, str] = field(default_factory=dict[str, str])  # slot -> credential id
    status: str = "never"  # never | ok | reconnect | failed
    last_import_at: str | None = None
    last_error: str = ""
    run_requested_at: str | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class AdAccount:
    id: str
    tool_id: str
    platform: str
    external_id: str
    name: str
    currency: str
    bot_slug: str  # "" when not assigned
    active: bool


def ready(c: sqlite3.Connection) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fin_ad_spends'").fetchone() is not None


def _credentials(text: object) -> dict[str, str]:
    try:
        raw: Any = json.loads(str(text or "{}"))
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    pairs: dict[Any, Any] = raw  # type: ignore[assignment]
    return {str(k): str(v) for k, v in pairs.items() if v}


def _import(r: Any) -> AdImport:
    return AdImport(
        id=r["id"],
        tool_id=r["tool_id"],
        platform=r["platform"],
        enabled=bool(r["enabled"]),
        history_from=r["history_from"],
        account_id=r["account_id"] or "",
        credentials=_credentials(r["credentials"]),
        status=r["status"],
        last_import_at=r["last_import_at"],
        last_error=r["last_error"] or "",
        run_requested_at=r["run_requested_at"],
        created_by=r["created_by"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def imports(c: sqlite3.Connection) -> list[AdImport]:
    if not ready(c):
        return []
    return [_import(r) for r in c.execute("SELECT * FROM fin_ad_imports ORDER BY created_at, id")]


def import_for(c: sqlite3.Connection, tool_id: str) -> AdImport | None:
    if not ready(c):
        return None
    row = c.execute("SELECT * FROM fin_ad_imports WHERE tool_id=?", (tool_id,)).fetchone()
    return _import(row) if row else None


def save_import(c: sqlite3.Connection, imp: AdImport, at: str) -> None:
    """Insert or update a tool's import settings (its result fields stay as they are on an update)."""
    c.execute(
        "INSERT INTO fin_ad_imports(id, tool_id, platform, enabled, history_from, account_id, credentials, "
        "status, last_import_at, last_error, run_requested_at, created_by, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tool_id) DO UPDATE SET enabled=excluded.enabled, "
        "history_from=excluded.history_from, account_id=excluded.account_id, "
        "credentials=excluded.credentials, last_import_at=excluded.last_import_at, "
        "run_requested_at=excluded.run_requested_at, updated_at=excluded.updated_at",
        (
            imp.id or new_id(),
            imp.tool_id,
            imp.platform,
            int(imp.enabled),
            imp.history_from,
            imp.account_id,
            json.dumps(imp.credentials, sort_keys=True),
            imp.status,
            imp.last_import_at,
            imp.last_error,
            imp.run_requested_at,
            imp.created_by,
            imp.created_at or at,
            at,
        ),
    )


def finish(c: sqlite3.Connection, import_id: str, status: str, error: str, at: str, *, imported: bool) -> None:
    """Record a run's result (`at`: when its pass started). `imported`: the spend came in, so the next run takes the
    last 30 days only. A run asked for after the pass started stays asked for, so another pass runs it."""
    c.execute(
        "UPDATE fin_ad_imports SET status=?, last_error=?, updated_at=?, "
        "run_requested_at=CASE WHEN run_requested_at>? THEN run_requested_at ELSE NULL END, "
        "last_import_at=CASE WHEN ? THEN ? ELSE last_import_at END WHERE id=?",
        (status, error[:500], at, at, int(imported), at, import_id),
    )


def request_run(c: sqlite3.Connection, import_id: str, at: str) -> None:
    c.execute("UPDATE fin_ad_imports SET run_requested_at=? WHERE id=?", (at, import_id))


def _account(r: Any) -> AdAccount:
    return AdAccount(
        id=r["id"],
        tool_id=r["tool_id"],
        platform=r["platform"],
        external_id=r["external_id"],
        name=r["name"],
        currency=r["currency"],
        bot_slug=r["bot_slug"] or "",
        active=bool(r["active"]),
    )


def accounts(c: sqlite3.Connection, tool_id: str = "") -> list[AdAccount]:
    if not ready(c):
        return []
    if tool_id:
        rows = c.execute("SELECT * FROM fin_ad_accounts WHERE tool_id=? ORDER BY name, external_id", (tool_id,))
    else:
        rows = c.execute("SELECT * FROM fin_ad_accounts ORDER BY tool_id, name, external_id")
    return [_account(r) for r in rows]


def account(c: sqlite3.Connection, account_id: str) -> AdAccount | None:
    if not ready(c):
        return None
    row = c.execute("SELECT * FROM fin_ad_accounts WHERE id=?", (account_id,)).fetchone()
    return _account(row) if row else None


def upsert_account(
    c: sqlite3.Connection, tool_id: str, platform: str, external_id: str, name: str, currency: str, at: str
) -> str:
    """The account's id; a known one takes the platform's current name and currency and is active again. Its bot stays."""
    c.execute(
        "INSERT INTO fin_ad_accounts(id, tool_id, platform, external_id, name, currency, bot_slug, active, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,NULL,1,?,?) ON CONFLICT(tool_id, external_id) DO UPDATE SET "
        "name=excluded.name, currency=excluded.currency, active=1, updated_at=excluded.updated_at",
        (new_id(), tool_id, platform, external_id, name, currency.upper() or "USD", at, at),
    )
    return str(
        c.execute(
            "SELECT id FROM fin_ad_accounts WHERE tool_id=? AND external_id=?", (tool_id, external_id)
        ).fetchone()[0]
    )


def deactivate_missing(c: sqlite3.Connection, tool_id: str, seen: Iterable[str], at: str) -> None:
    """Accounts the login no longer sees stop counting (their spend stays for the record)."""
    keep = sorted(set(seen))
    marks = ",".join("?" * len(keep))
    c.execute(
        "UPDATE fin_ad_accounts SET active=0, updated_at=? WHERE tool_id=? AND active=1"
        + (f" AND external_id NOT IN ({marks})" if keep else ""),
        (at, tool_id, *keep),
    )


def set_bot(c: sqlite3.Connection, account_id: str, bot_slug: str | None, at: str) -> None:
    c.execute("UPDATE fin_ad_accounts SET bot_slug=?, updated_at=? WHERE id=?", (bot_slug or None, at, account_id))


@dataclass(frozen=True)
class SpendRow:
    day: str
    campaign_id: str
    campaign_name: str
    amount_cents: int
    currency: str
    clicks: int = 0
    conversions: float = 0.0  # the platform's count of conversions it attributes to the campaign that day


def replace_spend(
    c: sqlite3.Connection, ad_account_id: str, start: str, end: str, rows: Sequence[SpendRow], at: str
) -> int:
    """The platform's word for [start, end] replaces what was stored for it: a restated day overwrites, a campaign
    gone from a day goes. Rows with the same day and campaign add up. Returns the rows stored."""
    c.execute("DELETE FROM fin_ad_spends WHERE ad_account_id=? AND day BETWEEN ? AND ?", (ad_account_id, start, end))
    merged: dict[tuple[str, str], SpendRow] = {}
    for r in rows:
        if not start <= r.day <= end:
            continue
        key = (r.day, r.campaign_id)
        had = merged.get(key)
        merged[key] = (
            r
            if had is None
            else SpendRow(
                r.day,
                r.campaign_id,
                r.campaign_name or had.campaign_name,
                had.amount_cents + r.amount_cents,
                r.currency,
                had.clicks + r.clicks,
                had.conversions + r.conversions,
            )
        )
    for r in merged.values():
        c.execute(
            "INSERT INTO fin_ad_spends(id, ad_account_id, day, campaign_external_id, campaign_name, amount_cents, "
            "currency, fetched_at, clicks, conversions) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(),
                ad_account_id,
                r.day,
                r.campaign_id,
                r.campaign_name,
                r.amount_cents,
                r.currency.upper(),
                at,
                r.clicks,
                r.conversions,
            ),
        )
    return len(merged)


@dataclass(frozen=True)
class LiveSpend:
    """One counting ad account's spend over a window: an active account of an enabled import (`import_id`). `mate` and
    `team` are whom the spend counts for: the campaign owner's teammate ("bot:…" / "human:…") and/or team. With no
    campaign owner set they are the account's bot and "" (so `bot_slug` alone decides, as before campaigns)."""

    ad_account_id: str
    platform: str
    bot_slug: str
    books_account_id: str
    currency: str
    amount: int
    import_id: str = ""
    mate: str = ""
    team: str = ""


def live_spend(c: sqlite3.Connection, start: str, end: str) -> list[LiveSpend]:
    """Spend per counting account over [start, end] (days), one row per account and currency; accounts with none
    still come back (amount 0), so a reader knows the platform covers them. An ad account two enabled imports both
    hold (the import refuses that, so only older data could) counts once: the earliest account row."""
    if not ready(c):
        return []
    rows = c.execute(
        "SELECT a.id, a.platform, a.external_id, COALESCE(a.bot_slug, '') AS bot_slug, i.id AS import_id, "
        "i.account_id, COALESCE(s.currency, a.currency) AS currency, COALESCE(SUM(s.amount_cents), 0) AS amount "
        "FROM fin_ad_accounts a JOIN fin_ad_imports i ON i.tool_id=a.tool_id AND i.enabled=1 "
        "LEFT JOIN fin_ad_spends s ON s.ad_account_id=a.id AND s.day BETWEEN ? AND ? "
        "WHERE a.active=1 GROUP BY a.id, COALESCE(s.currency, a.currency) ORDER BY a.created_at, a.id",
        (start, end),
    ).fetchall()
    first: dict[tuple[str, str], str] = {}
    for r in rows:
        first.setdefault((str(r["platform"]), str(r["external_id"])), str(r["id"]))
    found = [
        LiveSpend(
            r["id"],
            r["platform"],
            r["bot_slug"],
            r["account_id"] or "",
            r["currency"],
            int(r["amount"]),
            r["import_id"],
            f"bot:{r['bot_slug']}" if r["bot_slug"] else "",
        )
        for r in rows
        if first[(str(r["platform"]), str(r["external_id"]))] == str(r["id"])
    ]
    owned = _owned_campaigns(c)
    if owned:
        found = _by_owner(c, found, owned, start, end)
    return sorted(found, key=lambda x: (x.ad_account_id, x.currency, x.mate, x.team))


def _owned_campaigns(c: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    """{(ad account id, campaign external id): (teammate, team)} for the campaigns whose owner is set (none: the account's
    bot counts them all)."""
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for r in c.execute(
        "SELECT ad_account_id, external_id, COALESCE(teammate, '') AS teammate, COALESCE(team_id, '') AS team_id "
        "FROM fin_ad_campaigns WHERE (teammate IS NOT NULL AND teammate<>'') OR (team_id IS NOT NULL AND team_id<>'')"
    ):
        out[(str(r["ad_account_id"]), str(r["external_id"]))] = (str(r["teammate"]), str(r["team_id"]))
    return out


def _by_owner(
    c: sqlite3.Connection,
    found: list[LiveSpend],
    owned: dict[tuple[str, str], tuple[str, str]],
    start: str,
    end: str,
) -> list[LiveSpend]:
    """The accounts with a campaign owner set, split: each campaign's spend counts for its owner (the campaign's own, else
    the account's bot). An account's rows keep their currency; one owner-row per owner and currency."""
    accounts = {aid for aid, _ in owned}
    ids = sorted({x.ad_account_id for x in found if x.ad_account_id in accounts})
    if not ids:
        return found
    marks = ",".join("?" * len(ids))
    spent: dict[tuple[str, str, str], int] = {}
    for r in c.execute(
        "SELECT ad_account_id, campaign_external_id, currency, COALESCE(SUM(amount_cents), 0) FROM fin_ad_spends "
        f"WHERE day BETWEEN ? AND ? AND ad_account_id IN ({marks}) GROUP BY ad_account_id, campaign_external_id, currency",
        (start, end, *ids),
    ):
        spent[(str(r[0]), str(r[1]), str(r[2]))] = int(r[3])
    out: list[LiveSpend] = []
    for x in found:
        if x.ad_account_id not in accounts:
            out.append(x)
            continue
        parts: dict[tuple[str, str], int] = {}
        for (aid, ext, cur), amount in spent.items():
            if aid != x.ad_account_id or cur != x.currency:
                continue
            key = owned.get((aid, ext), (x.mate, ""))
            parts[key] = parts.get(key, 0) + amount
        if not parts:
            out.append(x)
            continue
        for (mate, team), amount in parts.items():
            out.append(
                replace(x, amount=amount, mate=mate, team=team, bot_slug=mate[4:] if mate.startswith("bot:") else "")
            )
    return out


@dataclass(frozen=True)
class CampaignMonth:
    """One campaign's spend in one calendar month (the month of the day the platform reports), on a counting account."""

    platform: str
    account_external_id: str
    account_name: str
    campaign_external_id: str
    campaign_name: str
    month: str  # YYYY-MM
    currency: str
    amount: int  # cents


def counting_accounts(c: sqlite3.Connection) -> list[AdAccount]:
    """The ad accounts whose spend counts: active, under an enabled import, and once per platform account (the earliest
    row when two tools both hold it, as live_spend counts it)."""
    if not ready(c):
        return []
    rows = c.execute(
        "SELECT a.* FROM fin_ad_accounts a JOIN fin_ad_imports i ON i.tool_id=a.tool_id AND i.enabled=1 "
        "WHERE a.active=1 ORDER BY a.created_at, a.id"
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    out: list[AdAccount] = []
    for r in rows:
        key = (str(r["platform"]), str(r["external_id"]))
        if key not in seen:
            seen.add(key)
            out.append(_account(r))
    return out


def campaign_months(c: sqlite3.Connection, start: str, end: str) -> list[CampaignMonth]:
    """Spend per counting account, campaign, month and currency over the days [start, end]. The campaign's name is its
    record's (the platform's latest), else the newest day's."""
    accounts = {a.id: a for a in counting_accounts(c)}
    if not accounts:
        return []
    marks = ",".join("?" * len(accounts))
    out: list[CampaignMonth] = []
    for r in c.execute(
        "SELECT s.ad_account_id, s.campaign_external_id, substr(s.day, 1, 7) AS month, s.currency, "
        "SUM(s.amount_cents) AS amount, MAX(s.campaign_name) AS spend_name, MAX(k.name) AS campaign_name "
        "FROM fin_ad_spends s LEFT JOIN fin_ad_campaigns k ON k.ad_account_id=s.ad_account_id "
        "AND k.external_id=s.campaign_external_id "
        f"WHERE s.day BETWEEN ? AND ? AND s.ad_account_id IN ({marks}) "
        "GROUP BY s.ad_account_id, s.campaign_external_id, month, s.currency "
        "ORDER BY month, s.ad_account_id, s.campaign_external_id",
        (start, end, *accounts),
    ):
        a = accounts[str(r["ad_account_id"])]
        out.append(
            CampaignMonth(
                a.platform,
                a.external_id,
                a.name,
                str(r["campaign_external_id"]),
                str(r["campaign_name"] or r["spend_name"] or ""),
                str(r["month"]),
                str(r["currency"]),
                int(r["amount"]),
            )
        )
    return out


@dataclass(frozen=True)
class CampaignDay:
    """One campaign's spend on one day the platform reports, on a counting account."""

    platform: str
    account_external_id: str
    campaign_external_id: str
    day: str  # YYYY-MM-DD
    currency: str
    amount: int  # cents


def campaign_days(c: sqlite3.Connection, start: str, end: str) -> list[CampaignDay]:
    """Spend per counting account, campaign, day and currency over the days [start, end]: the daily read behind
    Growth's weekly CAC (backend/growth_finance.py weeks)."""
    accounts = {a.id: a for a in counting_accounts(c)}
    if not accounts:
        return []
    marks = ",".join("?" * len(accounts))
    return [
        CampaignDay(
            accounts[str(r[0])].platform,
            accounts[str(r[0])].external_id,
            str(r[1]),
            str(r[2]),
            str(r[3]),
            int(r[4]),
        )
        for r in c.execute(
            "SELECT ad_account_id, campaign_external_id, day, currency, SUM(amount_cents) FROM fin_ad_spends "
            f"WHERE day BETWEEN ? AND ? AND ad_account_id IN ({marks}) "
            "GROUP BY ad_account_id, campaign_external_id, day, currency ORDER BY day, ad_account_id, campaign_external_id",
            (start, end, *accounts),
        )
    ]


def account_label(platform: str, external_id: str) -> str:
    """An ad account as its platform shows it: 123-456-7890 for Google Ads, act_123 for Meta."""
    if platform == "google_ads" and len(external_id) == 10 and external_id.isdigit():
        return f"{external_id[:3]}-{external_id[3:6]}-{external_id[6:]}"
    return "act_" + external_id if platform == "meta_ads" else external_id


@dataclass(frozen=True)
class Claim:
    """An ad account another enabled import already reads: one import per ad account, so its spend counts once."""

    external_id: str
    label: str
    tool_id: str
    tool_name: str

    @property
    def text(self) -> str:
        return f"Account {self.label} already imports from {self.tool_name}"


def claimed(c: sqlite3.Connection, tool_id: str, platform: str, external_ids: Iterable[str]) -> dict[str, Claim]:
    """{external id: the claim} for those of these ad accounts an active account of another tool's enabled import on the
    platform holds (the earliest such account when several do)."""
    wanted = sorted(set(external_ids))
    if not wanted or not ready(c):
        return {}
    marks = ",".join("?" * len(wanted))
    out: dict[str, Claim] = {}
    for r in c.execute(
        "SELECT a.external_id, a.tool_id, COALESCE(t.name, a.tool_id) AS tool_name FROM fin_ad_accounts a "
        "JOIN fin_ad_imports i ON i.tool_id=a.tool_id AND i.enabled=1 LEFT JOIN tools t ON t.id=a.tool_id "
        f"WHERE a.active=1 AND a.platform=? AND a.tool_id<>? AND a.external_id IN ({marks}) "
        "ORDER BY a.created_at, a.id",
        (platform, tool_id, *wanted),
    ):
        ext = str(r["external_id"])
        if ext not in out:
            out[ext] = Claim(ext, account_label(platform, ext), str(r["tool_id"]), str(r["tool_name"]))
    return out


def account_spend(c: sqlite3.Connection, tool_id: str, start: str, end: str) -> dict[str, int]:
    """{ad account id: spend over [start, end]} for a tool's accounts."""
    if not ready(c):
        return {}
    return {
        str(r[0]): int(r[1])
        for r in c.execute(
            "SELECT a.id, COALESCE(SUM(s.amount_cents), 0) FROM fin_ad_accounts a LEFT JOIN fin_ad_spends s "
            "ON s.ad_account_id=a.id AND s.day BETWEEN ? AND ? WHERE a.tool_id=? GROUP BY a.id",
            (start, end, tool_id),
        )
    }


def last_import(c: sqlite3.Connection) -> str | None:
    """When an enabled import last brought spend in (None: never)."""
    if not ready(c):
        return None
    row = c.execute("SELECT MAX(last_import_at) FROM fin_ad_imports WHERE enabled=1").fetchone()
    return str(row[0]) if row and row[0] else None


def enabled_imports(c: sqlite3.Connection) -> dict[str, AdImport]:
    """{import id: an enabled import}. Several may read one platform, each its own ad accounts."""
    return {i.id: i for i in imports(c) if i.enabled}


def warn_once(c: sqlite3.Connection, budget_id: str, period_key: str, level: str, bot_slug: str, at: str) -> bool:
    """Record that `level` was sent for the budget's period; False when it already was."""
    cur = c.execute(
        "INSERT INTO fin_ad_warnings(budget_id, period_key, level, bot_slug, created_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT DO NOTHING",
        (budget_id, period_key, level, bot_slug, at),
    )
    return cur.rowcount > 0


def warned(c: sqlite3.Connection, budget_id: str, period_key: str) -> set[str]:
    if not ready(c):
        return set()
    return {
        str(r[0])
        for r in c.execute(
            "SELECT level FROM fin_ad_warnings WHERE budget_id=? AND period_key=?", (budget_id, period_key)
        )
    }


# ------------------------------------------------------------------------------------------------ what budgets read
def descriptor_platform(text: str) -> str:
    """The platform a descriptor is an ad charge of ("" when none)."""
    return next((p for p, rx in DESCRIPTORS.items() if rx.search(text or "")), "")


def platform_contact(c: sqlite3.Connection, book_id: str, platform: str, mirror: bool = False) -> str:
    """The platform's vendor contact, found and never made ("" when none): a vendor contact named, or aliased, as
    VENDOR says (native books, where ad_import makes it), or in mirrored books the first of MIRROR_NAMES the
    provider's contacts go by. An active contact before an inactive one, then the oldest."""
    names = MIRROR_NAMES[platform] if mirror else (VENDOR[platform],)
    for name in names:
        nk = name.casefold().strip()  # core.ledger.name_key
        row = c.execute(
            "SELECT k.id FROM fin_contacts k WHERE k.book_id=? AND k.kind IN ('vendor','both') AND (lower(k.name)=? "
            "OR k.id IN (SELECT contact_id FROM fin_vendor_aliases WHERE book_id=? AND name_key=?)) "
            "ORDER BY k.active DESC, k.created_at, k.id LIMIT 1",
            (book_id, nk, book_id, nk),
        ).fetchone()
        if row is not None:
            return str(row[0])
    return ""


def vendor_platform(c: sqlite3.Connection, book_id: str, key: str) -> str:
    """The ad platform a vendor key is ("" when none). A contact is one when it is the platform's contact
    (platform_contact; in mirrored books the provider's own), is named as the import names it ("Google Ads", "Meta"),
    or its name or an alias is a platform descriptor; a posted name is one when its key or the label it was posted
    under is."""
    if not key:
        return ""
    row = c.execute("SELECT name FROM fin_contacts WHERE id=?", (key,)).fetchone()
    if row is not None:
        name = str(row[0] or "")
        if name.strip().casefold() in _CONTACT:
            return _CONTACT[name.strip().casefold()]
        mode = c.execute("SELECT mode FROM fin_books WHERE id=?", (book_id,)).fetchone()
        mirror = mode is not None and str(mode[0]) == "mirror"
        found = next((p for p in PLATFORMS if platform_contact(c, book_id, p, mirror) == key), "")
        if found:
            return found
        aliases = [str(r[0]) for r in c.execute("SELECT name_key FROM fin_vendor_aliases WHERE contact_id=?", (key,))]
        return next((p for t in (name, *aliases) if (p := descriptor_platform(t))), "")
    label = c.execute("SELECT label FROM fin_vendor_labels WHERE book_id=? AND name_key=?", (book_id, key)).fetchone()
    return descriptor_platform(key) or (descriptor_platform(str(label[0] or "")) if label is not None else "")


@dataclass(frozen=True)
class HeldFor:
    """A hold still counting in a window (held, or settled with no entry yet: what store/budgets.held counts) and the
    vendor it is for: a card hold's merchant (`text`), a payment's recipient (its contact and name), a bill's or an
    order's vendor contact. A hold of another source names none."""

    amount: int
    source: str
    contact_id: str
    text: str


def holds_for(c: sqlite3.Connection, budget_ids: Sequence[str], start: str, end: str) -> list[HeldFor]:
    if not budget_ids:
        return []
    marks = ",".join("?" * len(budget_ids))
    rows = c.execute(
        "SELECT CASE WHEN status='held' THEN amount ELSE COALESCE(settled_amount, 0) END AS counted, source, source_id "
        f"FROM fin_holds WHERE budget_id IN ({marks}) AND held_on>=? AND held_on<=? "
        "AND (status='held' OR (status='settled' AND entry_id IS NULL)) ORDER BY id",
        (*budget_ids, start, end),
    )
    out: list[HeldFor] = []
    for r in rows.fetchall():
        source, sid = str(r[1]), str(r[2] or "")
        contact = text = ""
        if source == "card":
            found = c.execute("SELECT counterparty FROM fin_feed_txns WHERE id=?", (sid,)).fetchone()
            text = str(found[0] or "") if found is not None else ""
        elif source == "payment":
            found = c.execute(
                "SELECT r.contact_id, r.name FROM fin_payments p JOIN fin_recipients r "
                "ON r.id=p.recipient_id WHERE p.id=?",
                (sid,),
            ).fetchone()
            if found is not None:
                contact, text = str(found[0] or ""), str(found[1] or "")
        elif source in ("bill", "purchase_order"):
            table = "fin_bills" if source == "bill" else "fin_purchase_orders"
            found = c.execute(f"SELECT vendor_id FROM {table} WHERE id=?", (sid.partition(":")[0],)).fetchone()
            contact = str(found[0] or "") if found is not None else ""
        out.append(HeldFor(int(r[0]), source, contact, text))
    return out


# ------------------------------------------------------------------------------------------------ campaigns
@dataclass(frozen=True)
class Campaign:
    """A campaign an ad account reports (fin_ad_campaigns). Its owner is `teammate` ("bot:…" / "human:…") and/or
    `team_id`; both empty: the account's bot (campaigns.campaign_owner)."""

    id: str
    ad_account_id: str
    external_id: str
    name: str
    team_id: str
    teammate: str
    owner_set_at: str | None
    owner_set_by: str
    prior_team_id: str
    prior_teammate: str
    first_seen_on: str
    last_seen_on: str


def _campaign(r: Any) -> Campaign:
    return Campaign(
        id=r["id"],
        ad_account_id=r["ad_account_id"],
        external_id=r["external_id"],
        name=r["name"] or "",
        team_id=r["team_id"] or "",
        teammate=r["teammate"] or "",
        owner_set_at=r["owner_set_at"],
        owner_set_by=r["owner_set_by"] or "",
        prior_team_id=r["prior_team_id"] or "",
        prior_teammate=r["prior_teammate"] or "",
        first_seen_on=r["first_seen_on"],
        last_seen_on=r["last_seen_on"],
    )


def upsert_campaign(
    c: sqlite3.Connection,
    ad_account_id: str,
    external_id: str,
    name: str,
    first_day: str,
    last_day: str,
    at: str,
) -> None:
    """A campaign the import saw: created with no owner, or renamed to the platform's current name; the days it was seen on
    widen. Its owner stays."""
    c.execute(
        "INSERT INTO fin_ad_campaigns(id, ad_account_id, external_id, name, first_seen_on, last_seen_on, created_at, "
        "updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ad_account_id, external_id) DO UPDATE SET "
        "name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE fin_ad_campaigns.name END, "
        "first_seen_on=MIN(fin_ad_campaigns.first_seen_on, excluded.first_seen_on), "
        "last_seen_on=MAX(fin_ad_campaigns.last_seen_on, excluded.last_seen_on), updated_at=excluded.updated_at",
        (new_id(), ad_account_id, external_id, name, first_day, last_day, at, at),
    )


def campaigns(c: sqlite3.Connection, ad_account_id: str = "") -> list[Campaign]:
    if not ready(c):
        return []
    if ad_account_id:
        rows = c.execute(
            "SELECT * FROM fin_ad_campaigns WHERE ad_account_id=? ORDER BY name, external_id", (ad_account_id,)
        )
    else:
        rows = c.execute("SELECT * FROM fin_ad_campaigns ORDER BY ad_account_id, name, external_id")
    return [_campaign(r) for r in rows]


def campaign(c: sqlite3.Connection, campaign_id: str) -> Campaign | None:
    if not ready(c):
        return None
    row = c.execute("SELECT * FROM fin_ad_campaigns WHERE id=?", (campaign_id,)).fetchone()
    return _campaign(row) if row else None


def set_owner(
    c: sqlite3.Connection, campaign_id: str, team: str, teammate: str, by: str, at: str, *, undo: bool = False
) -> None:
    """Put an owner on a campaign ("" for both: back to the account's bot). The owner it had goes to prior_* (so an undo
    puts it back); an undo swaps the two."""
    cur = campaign(c, campaign_id)
    if cur is None:
        return
    if undo:
        team, teammate = cur.prior_team_id, cur.prior_teammate
    c.execute(
        "UPDATE fin_ad_campaigns SET prior_team_id=?, prior_teammate=?, team_id=?, teammate=?, owner_set_at=?, "
        "owner_set_by=?, updated_at=? WHERE id=?",
        (cur.team_id or None, cur.teammate or None, team or None, teammate or None, at, by, at, campaign_id),
    )


def campaign_month(c: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    """Each campaign's spend, clicks and conversions over [start, end] (days), per currency, with its ad account."""
    if not ready(c):
        return []
    return [
        dict(r)
        for r in c.execute(
            "SELECT g.id AS campaign_id, g.external_id, g.name, g.ad_account_id, g.team_id, g.teammate, "
            "g.last_seen_on, a.name AS account_name, a.platform, a.bot_slug, a.currency AS account_currency, "
            "s.currency AS currency, COALESCE(SUM(s.amount_cents), 0) AS spend, COALESCE(SUM(s.clicks), 0) AS clicks, "
            "COALESCE(SUM(s.conversions), 0) AS conversions, MAX(s.fetched_at) AS fetched_at "
            "FROM fin_ad_campaigns g JOIN fin_ad_accounts a ON a.id=g.ad_account_id "
            "LEFT JOIN fin_ad_spends s ON s.ad_account_id=g.ad_account_id AND s.campaign_external_id=g.external_id "
            "AND s.day BETWEEN ? AND ? WHERE g.first_seen_on <= ? AND g.last_seen_on >= ? GROUP BY g.id, s.currency ORDER BY g.id",
            (start, end, end, start),
        )
    ]
