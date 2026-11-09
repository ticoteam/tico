"""Helpers that other tests share, moved out of test_finance_platform_share.py so they import no test file."""

import sqlite3
from backend import bot_settings as BS
from backend import hubdb as H
from backend.finance import ad_import as AI
from backend.finance.store import ads as SA
from backend.store import Store
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act


BOTS = {
    "cmo": ("sales", "human:dan"),
    "paid-rental-ads": ("sales", "bot:cmo"),
    "consumer-google-ad-owner": ("sales", "bot:cmo"),
    "rental-social-ad-owner": ("sales", "bot:cmo"),
    **{f"ten-{n}": ("sales", "bot:cmo") for n in range(10)},
}


def _bots(c: sqlite3.Connection) -> None:
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in BOTS}, None)
    for slug, (team, boss) in BOTS.items():
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


def google(store: Store, *, history_from: str = "2026-01-01") -> dict[str, str]:
    """An Advertising line (6300), a Google Ads import booking to it (read from history_from, last run ok), three ad
    accounts on its login (a, b owned by two bots; u owned by none) and the Google Ads vendor contact (vendor)."""
    with store.transaction() as c:
        _bots(c)
        made = act(
            c,
            OWNER,
            "account_create",
            {"code": "6300", "name": "Advertising", "type": "expense", "parent_id": account(c, "6000")},
            at=AT,
        )
        adv = str(made.result["account_id"])
        _tool(c, "gads", "google-ads")
        SA.save_import(
            c,
            SA.AdImport(
                id="",
                tool_id="gads",
                platform="google_ads",
                enabled=True,
                history_from=history_from,
                account_id=adv,
                created_by=OWNER,
            ),
            AT,
        )
        iid = str(c.execute("SELECT id FROM fin_ad_imports WHERE tool_id='gads'").fetchone()[0])
        SA.finish(c, iid, "ok", "", AT, imported=True)
        out = {"adv": adv, "import": iid}
        for key, ext, name, bot in (
            ("a", "111", "Paid rental", "paid-rental-ads"),
            ("b", "222", "Consumer", "consumer-google-ad-owner"),
            ("u", "333", "Unowned", ""),
        ):
            aid = SA.upsert_account(c, "gads", "google_ads", ext, name, "USD", AT)
            if bot:
                SA.set_bot(c, aid, bot, AT)
            out[key] = aid
        out["vendor"] = AI.ensure_vendor(c, "google_ads", AT)
    return out


def by_mate(c: sqlite3.Connection, code: str, table: str = "fin_rollups") -> dict[str, int]:
    rows = c.execute(
        f"SELECT teammate, sum(amount) FROM {table} WHERE account_id=? GROUP BY teammate", (account(c, code),)
    ).fetchall()
    return {str(t): int(a) for t, a in rows if a}


A, B, SOCIAL = "bot:paid-rental-ads", "bot:consumer-google-ad-owner", "bot:rental-social-ad-owner"
