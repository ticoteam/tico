"""Measures GET /api/v2/finance/brief and GET /api/v2/finance/transactions?limit=200 on fictional books at about TIDY's
size (Nov 2026): a payments account with ENTRIES payouts of about eleven feed rows each (FEED rows), a bank account with
a few hundred more, expense lines spread over teams and teammates, budgets, and a private payroll line.

    nice -n 19 .venv/bin/python bench/perf-finance.py

For each read it prints the statements one call runs, its median wall time over RUNS calls, the bytes of its JSON, the
statement shapes that repeat most, and a digest of the answer (an equal digest before and after a change means the same
answer; SEED_DB=path keeps the seeded books there and reuses them on the next run, so a before and after run read
the same books, ids included; ANSWERS=dir writes each answer there to diff). PLANS=1 adds the query plan of every distinct statement the reads run.

Only fictional data, in a temporary folder that is removed afterwards. Never point it at a real hub.
"""

import collections
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend.credentials import Vault
from backend.finance import actions as FA
from backend.finance import brief as BR
from backend.finance import reads as R
from backend.finance.core import ledger as L
from backend.store import Store
from backend.tests.helpers import finance_fixtures as F
from backend.tests.helpers.api import api_settings

ENTRIES = int(os.environ.get("ENTRIES", 7700))
PER_ENTRY = int(os.environ.get("PER_ENTRY", 11))
BANK = int(os.environ.get("BANK", 1000))
RUNS = int(os.environ.get("RUNS", 5))
AT = "2026-11-19T12:00:00.000000Z"
SEED_AT = "2026-11-19T11:00:00.000000Z"
TEAMS = [("web", "bot:coder"), ("web", "human:cara"), ("sales", "bot:seller"), ("sales", "human:dan"), ("", "")]
VENDORS = [f"Vendor {n:03d}" for n in range(300)]


def _day(rnd: random.Random) -> str:
    """A day in the year before AT, weighted to the recent months the way a growing company's books are."""
    back = int(rnd.triangular(0, 365, 0))
    return time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime("2026-11-19", "%Y-%m-%d")) - back * 86400))


def seed(store: Store) -> None:
    rnd = random.Random(7)
    with store.transaction() as c:
        F._seed(c)
        FA.setup(c, F.OWNER, SEED_AT)
        F.open_views(c)
    fake = F.FakeMercury()
    for n in range(BANK):
        day = _day(rnd)
        fake.txns[f"t{n}"] = {
            **F.MERCURY["txn_bank_fee"],
            "id": f"t{n}",
            "amount": -round(rnd.uniform(5, 900), 2),
            "kind": "externalTransfer",
            "createdAt": f"{day}T10:00:00Z",
            "postedAt": f"{day}T10:00:00Z",
            "counterpartyName": rnd.choice(VENDORS),
            "bankDescription": "PAYMENT",
        }
    F.connect(store, fake)
    F.sync_once(store, fake, SEED_AT)
    with store.transaction() as c:
        soft = F.account(c, "6100")
        top = F.act(c, F.OWNER, "budget_create", {"name": "Software", "account_id": soft, "amount": 5_000_000})
        parent = str(top.result["budget_id"])
        for team, amount in (("web", 2_000_000), ("sales", 1_000_000)):
            F.act(
                c,
                F.OWNER,
                "budget_create",
                {
                    "name": team,
                    "account_id": soft,
                    "team": team,
                    "parent_id": parent,
                    "amount": amount,
                    "at_limit": "warn",
                },
            )
        for mate in ("bot:coder", "bot:seller"):
            F.act(
                c,
                F.OWNER,
                "budget_create",
                {
                    "name": mate,
                    "account_id": soft,
                    "teammate": mate,
                    "parent_id": parent,
                    "amount": 300_000,
                    "at_limit": "warn",
                },
            )
        pay = F.account(c, "6900")
        F.act(c, F.OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}, at=SEED_AT)
        money = c.execute(
            "SELECT id, account_id FROM fin_money_accounts WHERE provider_account_id='acct-chk'"
        ).fetchone()
        money_id, gl = str(money[0]), str(money[1])
        codes = [F.account(c, x) for x in ("5100", "5200", "6100", "6150")]
    # Payouts: one entry each, its feed rows inserted beside it the way a payments provider's sync links them.
    for start in range(0, ENTRIES, 500):
        with store.transaction() as c:
            for n in range(start, min(start + 500, ENTRIES)):
                day = _day(rnd)
                lines = []
                for _ in range(rnd.randint(1, 3)):
                    team, mate = rnd.choice(TEAMS)
                    tags = {k: v for k, v in (("team", team), ("teammate", mate)) if v}
                    lines.append(
                        {
                            "account_id": rnd.choice(codes),
                            "amount": rnd.randint(100, 90_000),
                            "tags": tags,
                            "contact_id": rnd.choice(VENDORS),
                        }
                    )
                total = sum(int(x["amount"]) for x in lines)
                lines.append({"account_id": gl, "amount": -total})
                posted = FA.post_entry(
                    c,
                    {"date": day, "source": "bank", "source_id": f"payout-{n}", "lines": lines},
                    at=SEED_AT,
                    touch=False,
                    roles={len(lines) - 1: L.MONEY_ROLE},
                )
                vendor = rnd.choice(VENDORS)
                rows = []
                for k in range(PER_ENTRY):
                    amount = -rnd.randint(100, 50_000) if k % 2 else rnd.randint(100, 50_000)
                    rows.append(
                        (
                            f"p{n}-{k}",
                            money_id,
                            f"pp{n}-{k}",
                            day,
                            amount,
                            "USD",
                            vendor,
                            f"Charge {n}-{k}",
                            "posted",
                            posted["entry_id"],
                            SEED_AT,
                            "system",
                            SEED_AT,
                            0,
                            "charge",
                            json.dumps({"id": f"pp{n}-{k}", "object": "charge", "pad": "x" * 300}),
                            vendor.lower(),
                        )
                    )
                c.executemany(
                    "INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, "
                    "counterparty, description, status, entry_id, created_at, created_by, updated_at, pending, kind, "
                    "raw, vendor_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
    with store.transaction() as c:
        c.execute("ANALYZE")


def _shape(sql: str) -> str:
    text = re.sub(r"'[^']*'", "?", re.sub(r"\s+", " ", sql))
    return re.sub(r"(\?,)+\?", "?..", re.sub(r"-?\b\d+(\.\d+)?\b", "N", text))[:160]


def measure(store: Store, label: str, fn) -> None:
    seen: list[str] = []
    spent: collections.Counter[str] = collections.Counter()  # each statement's shape: time to the next statement
    marks: list[float] = []

    def record(sql: str) -> None:
        marks.append(time.perf_counter())
        seen.append(sql)

    with store.read() as c:
        c.set_trace_callback(record)
        out = fn(c)
        marks.append(time.perf_counter())
        c.set_trace_callback(None)
    for n, sql in enumerate(seen):
        spent[_shape(sql)] += marks[n + 1] - marks[n]
    times = []
    for _ in range(RUNS):
        with store.read() as c:
            t = time.perf_counter()
            fn(c)
            times.append((time.perf_counter() - t) * 1000)
    body = json.dumps(out, sort_keys=True, default=str)
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"], re.sub(r"\W+", "-", label) + ".json").write_text(
            json.dumps(out, indent=1, default=str)
        )
    print(
        f"== {label}: {len(seen)} statements, {statistics.median(times):.0f} ms median, {len(body):,} bytes, "
        f"answer {digest}"
    )
    counts = collections.Counter(_shape(q) for q in seen)
    for shape, took in spent.most_common(6):
        print(f"   {counts[shape]:6d} {took * 1000:6.0f} ms  {shape}")
    if os.environ.get("PLANS"):
        with store.read() as c:
            for q in dict.fromkeys(seen):
                if q.lstrip().upper().startswith("SELECT"):
                    plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + q)]
                    print("   PLAN", _shape(q)[:100], "|", "; ".join(plan))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="perf-finance-"))
    try:
        settings = api_settings(tmp)
        kept = Path(os.environ.get("SEED_DB", ""))
        reuse = kept.name and kept.exists()
        if reuse:
            shutil.copy(kept, settings.db_path)  # the same books for a before and after run (ids are random)
        store = Store(settings)
        store.initialize(seed_market=False)
        FA.use_vault(Vault(store))
        t = time.perf_counter()
        if not reuse:
            seed(store)
            if kept.name:
                with store.read() as c:
                    c.execute("VACUUM INTO ?", (str(kept),))
        with store.read() as c:
            sizes = {
                t_: c.execute(f"SELECT count(*) FROM {t_}").fetchone()[0]
                for t_ in ("fin_feed_txns", "fin_entries", "fin_lines", "fin_rollups")
            }
        print(f"seeded in {time.perf_counter() - t:.0f} s: {sizes}")
        for who in (F.OWNER, "human:cara", "bot:coder"):
            measure(store, f"brief compute {who}", lambda c, w=who: BR.compute(c, w, AT))
        measure(store, "watch findings owner", lambda c: R.watch_findings(c, F.OWNER, AT))
        for who in (F.OWNER, "human:cara"):
            measure(store, f"transactions limit=200 {who}", lambda c, w=who: R.transactions(c, w, limit=200, at=AT))
        t = time.perf_counter()
        BR.on_demand(store, F.OWNER, AT)
        print(f"== brief on_demand owner (first, stores): {(time.perf_counter() - t) * 1000:.0f} ms")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
