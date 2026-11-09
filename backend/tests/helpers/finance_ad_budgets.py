"""Helpers that other tests share, moved out of test_finance_ad_budgets.py so they import no test file."""

import sqlite3
from backend import bot_settings as BS
from backend import hubdb as H
from backend.finance import ad_import as AI
from backend.finance import ad_warnings
from backend.finance import reads
from backend.finance.store import ads as SA
from backend.store import Store
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act


ADS = {
    "cmo": ("sales", "human:dan"),
    "paid-rental-ads": ("sales", "bot:cmo"),
    "rental-social-ad-owner": ("sales", "bot:cmo"),
}


def _bots(c: sqlite3.Connection) -> None:
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in ADS}, None)
    for slug, (team, boss) in ADS.items():
        c.execute(
            "INSERT OR REPLACE INTO bot_config(bot,team,owner_id,reports_to) VALUES(?,?,?,?)", (slug, team, OWNER, boss)
        )
        BS.write(c, slug, {"team": team})


def _tool(c: sqlite3.Connection, tid: str, service: str) -> None:
    c.execute(
        "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
        "updated_at) VALUES(?,?,?,'','bearer','','computer',?,?,?)",
        (tid, tid, service, OWNER, AT, AT),
    )


def world(store: Store, at: str = AT) -> dict[str, str]:
    """An Advertising line, Google Ads and Meta imports booking to it, one ad account per bot, the platforms' vendor
    contacts, and budgets: a Marketing one over the whole line and, under it, each bot's (warn only). The ids by name."""
    with store.transaction() as c:
        _bots(c)
        made = act(
            c,
            OWNER,
            "account_create",
            {"code": "6300", "name": "Advertising", "type": "expense", "parent_id": account(c, "6000")},
            at=at,
        )
        adv = str(made.result["account_id"])
        out = {"adv": adv}
        for tid, service, platform, external, name, bot in (
            ("gads", "google-ads", "google_ads", "123", "Paid rental", "paid-rental-ads"),
            ("meta", "meta-ads", "meta_ads", "act_1", "Rental social", "rental-social-ad-owner"),
        ):
            _tool(c, tid, service)
            SA.save_import(
                c,
                SA.AdImport(
                    id="",
                    tool_id=tid,
                    platform=platform,
                    enabled=True,
                    history_from="2026-01-01",
                    account_id=adv,
                    created_by=OWNER,
                ),
                at,
            )
            iid = str(c.execute("SELECT id FROM fin_ad_imports WHERE tool_id=?", (tid,)).fetchone()[0])
            SA.finish(c, iid, "ok", "", at, imported=True)
            out[platform] = SA.upsert_account(c, tid, platform, external, name, "USD", at)
            SA.set_bot(c, out[platform], bot, at)
            out[platform + "_vendor"] = AI.ensure_vendor(c, platform, at)
        for key, body in (
            ("marketing", {"name": "Marketing", "amount": 1_000_000}),
            (
                "google",
                {
                    "name": "Rental Google Ad Owner",
                    "teammate": "bot:paid-rental-ads",
                    "vendor": out["google_ads_vendor"],
                    "amount": 300_000,
                },
            ),
            (
                "meta",
                {
                    "name": "Rental Social Ad Owner",
                    "teammate": "bot:rental-social-ad-owner",
                    "vendor": out["meta_ads_vendor"],
                    "amount": 300_000,
                },
            ),
        ):
            under = {"parent_id": out["marketing"]} if key != "marketing" else {}
            done = act(c, OWNER, "budget_create", {**body, **under, "account_id": adv, "at_limit": "warn"}, at=at)
            assert done.status == "committed", done
            out[key] = str(done.result["budget_id"])
    return out


def platform_spend(store: Store, ad_account: str, rows: list[tuple[str, int]], start: str, end: str) -> None:
    with store.transaction() as c:
        SA.replace_spend(
            c, ad_account, start, end, [SA.SpendRow(d, "c1", "Spring", cents, "USD") for d, cents in rows], AT
        )


def rows_by_name(
    c: sqlite3.Connection, viewer: str, at: str = AT, on: str | None = None
) -> dict[str, dict[str, object]]:
    return {str(r["name"]): r for r in reads.budgets(c, viewer, at=at, on=on)["budgets"]}  # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------------ warnings
WARN_AT = "2026-10-06T12:00:00.000000Z"


def warning_notes(c: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (str(r[0]), str(r[1]))
        for r in c.execute(
            "SELECT to_actor, body FROM notes WHERE from_actor=? AND body LIKE 'Finance · budget warning%' ORDER BY rowid",
            (H.KEEPER,),
        )
    ]


def warn(store: Store, at: str = WARN_AT) -> list[tuple[str, tuple[str, ...]]]:
    with store.transaction() as c:
        return [(s.bot, s.levels) for s in ad_warnings.run(c, at)]
