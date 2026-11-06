"""SQL for the ad spend import: each tool's import settings, the ad accounts its login sees (and the bot each counts
for), the platforms' daily spend per campaign, and the budget warnings bots were given from it.

The tables come from migration 20261106071500_finance_ad_spend; `ready` tells a database without them (an old test
copy), and every read answers empty there."""
import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .ledger import new_id

PLATFORMS = ("google_ads", "meta_ads")
# The tool service (backend/tool_services.py) each platform imports from.
SERVICE = {"google_ads": "google-ads", "meta_ads": "meta-ads"}
PLATFORM_OF = {v: k for k, v in SERVICE.items()}
LABEL = {"google_ads": "Google Ads", "meta_ads": "Meta Ads"}


@dataclass(frozen=True)
class AdImport:
    id: str
    tool_id: str
    platform: str
    enabled: bool
    history_from: str
    account_id: str = ""                         # the books line spend books as
    credentials: dict[str, str] = field(default_factory=dict[str, str])       # slot -> credential id
    status: str = "never"                        # never | ok | reconnect | failed
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
    bot_slug: str                                # "" when not assigned
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
    pairs: dict[Any, Any] = raw          # type: ignore[assignment]
    return {str(k): str(v) for k, v in pairs.items() if v}


def _import(r: Any) -> AdImport:
    return AdImport(id=r["id"], tool_id=r["tool_id"], platform=r["platform"], enabled=bool(r["enabled"]),
                    history_from=r["history_from"], account_id=r["account_id"] or "",
                    credentials=_credentials(r["credentials"]), status=r["status"], last_import_at=r["last_import_at"],
                    last_error=r["last_error"] or "", run_requested_at=r["run_requested_at"],
                    created_by=r["created_by"], created_at=r["created_at"], updated_at=r["updated_at"])


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
    c.execute("INSERT INTO fin_ad_imports(id, tool_id, platform, enabled, history_from, account_id, credentials, "
              "status, last_import_at, last_error, run_requested_at, created_by, created_at, updated_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tool_id) DO UPDATE SET enabled=excluded.enabled, "
              "history_from=excluded.history_from, account_id=excluded.account_id, "
              "credentials=excluded.credentials, last_import_at=excluded.last_import_at, "
              "run_requested_at=excluded.run_requested_at, updated_at=excluded.updated_at",
              (imp.id or new_id(), imp.tool_id, imp.platform, int(imp.enabled), imp.history_from, imp.account_id,
               json.dumps(imp.credentials, sort_keys=True), imp.status, imp.last_import_at, imp.last_error,
               imp.run_requested_at, imp.created_by, imp.created_at or at, at))


def finish(c: sqlite3.Connection, import_id: str, status: str, error: str, at: str, *, imported: bool) -> None:
    """Record a run's result. `imported`: the spend came in, so the next run takes the last 30 days only."""
    c.execute("UPDATE fin_ad_imports SET status=?, last_error=?, run_requested_at=NULL, updated_at=?, "
              "last_import_at=CASE WHEN ? THEN ? ELSE last_import_at END WHERE id=?",
              (status, error[:500], at, int(imported), at, import_id))


def request_run(c: sqlite3.Connection, import_id: str, at: str) -> None:
    c.execute("UPDATE fin_ad_imports SET run_requested_at=? WHERE id=?", (at, import_id))


def _account(r: Any) -> AdAccount:
    return AdAccount(id=r["id"], tool_id=r["tool_id"], platform=r["platform"], external_id=r["external_id"],
                     name=r["name"], currency=r["currency"], bot_slug=r["bot_slug"] or "", active=bool(r["active"]))


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


def upsert_account(c: sqlite3.Connection, tool_id: str, platform: str, external_id: str, name: str, currency: str,
                   at: str) -> str:
    """The account's id; a known one takes the platform's current name and currency and is active again. Its bot stays."""
    c.execute("INSERT INTO fin_ad_accounts(id, tool_id, platform, external_id, name, currency, bot_slug, active, "
              "created_at, updated_at) VALUES(?,?,?,?,?,?,NULL,1,?,?) ON CONFLICT(tool_id, external_id) DO UPDATE SET "
              "name=excluded.name, currency=excluded.currency, active=1, updated_at=excluded.updated_at",
              (new_id(), tool_id, platform, external_id, name, currency.upper() or "USD", at, at))
    return str(c.execute("SELECT id FROM fin_ad_accounts WHERE tool_id=? AND external_id=?",
                         (tool_id, external_id)).fetchone()[0])


def deactivate_missing(c: sqlite3.Connection, tool_id: str, seen: Iterable[str], at: str) -> None:
    """Accounts the login no longer sees stop counting (their spend stays for the record)."""
    keep = sorted(set(seen))
    marks = ",".join("?" * len(keep))
    c.execute("UPDATE fin_ad_accounts SET active=0, updated_at=? WHERE tool_id=? AND active=1"
              + (f" AND external_id NOT IN ({marks})" if keep else ""), (at, tool_id, *keep))


def set_bot(c: sqlite3.Connection, account_id: str, bot_slug: str | None, at: str) -> None:
    c.execute("UPDATE fin_ad_accounts SET bot_slug=?, updated_at=? WHERE id=?", (bot_slug or None, at, account_id))


@dataclass(frozen=True)
class SpendRow:
    day: str
    campaign_id: str
    campaign_name: str
    amount_cents: int
    currency: str


def replace_spend(c: sqlite3.Connection, ad_account_id: str, start: str, end: str, rows: Sequence[SpendRow],
                  at: str) -> int:
    """The platform's word for [start, end] replaces what was stored for it: a restated day overwrites, a campaign
    gone from a day goes. Rows with the same day and campaign add up. Returns the rows stored."""
    c.execute("DELETE FROM fin_ad_spends WHERE ad_account_id=? AND day BETWEEN ? AND ?", (ad_account_id, start, end))
    merged: dict[tuple[str, str], SpendRow] = {}
    for r in rows:
        if not start <= r.day <= end:
            continue
        key = (r.day, r.campaign_id)
        had = merged.get(key)
        merged[key] = r if had is None else SpendRow(r.day, r.campaign_id, r.campaign_name or had.campaign_name,
                                                     had.amount_cents + r.amount_cents, r.currency)
    for r in merged.values():
        c.execute("INSERT INTO fin_ad_spends(id, ad_account_id, day, campaign_external_id, campaign_name, amount_cents, "
                  "currency, fetched_at) VALUES(?,?,?,?,?,?,?,?)",
                  (new_id(), ad_account_id, r.day, r.campaign_id, r.campaign_name, r.amount_cents, r.currency.upper(),
                   at))
    return len(merged)


@dataclass(frozen=True)
class LiveSpend:
    """One counting ad account's spend over a window: an active account of an enabled import."""
    ad_account_id: str
    platform: str
    bot_slug: str
    books_account_id: str
    currency: str
    amount: int


def live_spend(c: sqlite3.Connection, start: str, end: str) -> list[LiveSpend]:
    """Spend per counting account over [start, end] (days), one row per account and currency; accounts with none
    still come back (amount 0), so a reader knows the platform covers them."""
    if not ready(c):
        return []
    rows = c.execute(
        "SELECT a.id, a.platform, COALESCE(a.bot_slug, '') AS bot_slug, i.account_id, "
        "COALESCE(s.currency, a.currency) AS currency, COALESCE(SUM(s.amount_cents), 0) AS amount "
        "FROM fin_ad_accounts a JOIN fin_ad_imports i ON i.tool_id=a.tool_id AND i.enabled=1 "
        "LEFT JOIN fin_ad_spends s ON s.ad_account_id=a.id AND s.day BETWEEN ? AND ? "
        "WHERE a.active=1 GROUP BY a.id, COALESCE(s.currency, a.currency) ORDER BY a.id", (start, end))
    return [LiveSpend(r["id"], r["platform"], r["bot_slug"], r["account_id"] or "", r["currency"], int(r["amount"]))
            for r in rows]


def account_spend(c: sqlite3.Connection, tool_id: str, start: str, end: str) -> dict[str, int]:
    """{ad account id: spend over [start, end]} for a tool's accounts."""
    if not ready(c):
        return {}
    return {str(r[0]): int(r[1]) for r in c.execute(
        "SELECT a.id, COALESCE(SUM(s.amount_cents), 0) FROM fin_ad_accounts a LEFT JOIN fin_ad_spends s "
        "ON s.ad_account_id=a.id AND s.day BETWEEN ? AND ? WHERE a.tool_id=? GROUP BY a.id", (start, end, tool_id))}


def last_import(c: sqlite3.Connection) -> str | None:
    """When an enabled import last brought spend in (None: never)."""
    if not ready(c):
        return None
    row = c.execute("SELECT MAX(last_import_at) FROM fin_ad_imports WHERE enabled=1").fetchone()
    return str(row[0]) if row and row[0] else None


def enabled_platforms(c: sqlite3.Connection) -> dict[str, AdImport]:
    """{platform: its enabled import}. At most one per platform (the API refuses a second)."""
    return {i.platform: i for i in imports(c) if i.enabled}


def warn_once(c: sqlite3.Connection, budget_id: str, period_key: str, level: str, bot_slug: str, at: str) -> bool:
    """Record that `level` was sent for the budget's period; False when it already was."""
    cur = c.execute("INSERT INTO fin_ad_warnings(budget_id, period_key, level, bot_slug, created_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT DO NOTHING", (budget_id, period_key, level, bot_slug, at))
    return cur.rowcount > 0


def warned(c: sqlite3.Connection, budget_id: str, period_key: str) -> set[str]:
    if not ready(c):
        return set()
    return {str(r[0]) for r in c.execute("SELECT level FROM fin_ad_warnings WHERE budget_id=? AND period_key=?",
                                         (budget_id, period_key))}


# ------------------------------------------------------------------------------------------------ what budgets read
def rollup_vendors(c: sqlite3.Connection, book_id: str, start: str, end: str) -> list[str]:
    """Every vendor key a posted line carries in [start, end]: the keys a live budget read sorts into platform vendors
    (budgeting.ad_platform) and the rest."""
    return [str(r[0]) for r in c.execute("SELECT DISTINCT vendor FROM fin_rollups WHERE book_id=? AND day>=? AND day<=? "
                                         "AND vendor<>''", (book_id, start, end))]


def card_holds(c: sqlite3.Connection, budget_ids: Sequence[str], start: str, end: str) -> list[tuple[int, str]]:
    """(amount, merchant) of each card authorization hold in the window still counting on these budgets (held, or
    settled with no entry yet: what store/budgets.held counts), for a live read to take the ad platforms' out."""
    if not budget_ids:
        return []
    marks = ",".join("?" * len(budget_ids))
    rows = c.execute(
        "SELECT CASE WHEN h.status='held' THEN h.amount ELSE COALESCE(h.settled_amount, 0) END, "
        "COALESCE(f.counterparty, '') FROM fin_holds h LEFT JOIN fin_feed_txns f ON f.id=h.source_id "
        f"WHERE h.budget_id IN ({marks}) AND h.source='card' AND h.held_on>=? AND h.held_on<=? "
        "AND (h.status='held' OR (h.status='settled' AND h.entry_id IS NULL))", (*budget_ids, start, end))
    return [(int(r[0]), str(r[1])) for r in rows]
