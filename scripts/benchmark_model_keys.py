#!/usr/bin/env python3
"""Time `GET runner-model-credentials` (keys_for: backend/model_subscriptions.py since D11-5, backend/computer_keys.py
before), the claim's subscriptions (model_subscriptions.for_claim) and the Computers page's keys (key_listing since
D11-5; serving, listing_by_runner and shared_listing per computer before) on a seeded hub, and print each query's plan,
so a checkout before D11-4, D11-5 and one after can be compared on the same data.

    python3 scripts/benchmark_model_keys.py [--repo CHECKOUT] [--computers 50] [--subscriptions 200] [--bots 200]

`--repo` is the checkout whose `backend` is measured (this one by default). The data are fictional and the same for
both: each computer's own Claude key as Settings > Computers kept it (`computer_keys`), one key shared with every
computer, Settings > Models subscriptions given for Codex, and a key granted to each bot. A checkout with the
D11-4 migration (20261117061243) then moves those keys into subscriptions, as an upgrade does, and one with the D11-5
migration (20261117085930) drops the old key tables. `computer_keys` and `model_login_key_off` are made again for the seed where a checkout has
dropped them already. B1 converts the synthetic agents' old grants and bindings into Paid by selections.
"""

import argparse
import importlib
import json
from pathlib import Path
from sqlite3 import Connection
import statistics
import sys
import tempfile
import time
from typing import cast
import uuid

NOW = "2026-11-17T00:00:00.000000Z"
MOVE = "20261117061243_computer_keys_to_subscriptions"
DROP = "20261117085930_drop_computer_keys"
POOLS = "20261122220441_agent_subscriptions"
LEGACY = (
    "CREATE TABLE IF NOT EXISTS computer_keys(credential_id TEXT PRIMARY KEY REFERENCES credentials(id), "
    "runner_id TEXT NOT NULL REFERENCES runners(id), runtime TEXT NOT NULL, state TEXT NOT NULL, login_id TEXT, "
    "created_at TEXT NOT NULL, created_by TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS model_login_key_off(runner_id TEXT NOT NULL REFERENCES runners(id), "
    "runtime TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '', set_at TEXT NOT NULL, set_by TEXT NOT NULL, "
    "PRIMARY KEY(runner_id,runtime,profile))",
)


def insert(c, table, values):
    """A row in `table` with `values`, its other required columns filled."""
    row = dict(values)
    for _, name, kind, notnull, default, pk in c.execute(f"PRAGMA table_info({table})"):
        if name not in row and notnull and default is None and not pk:
            row[name] = 0 if "INT" in (kind or "").upper() else f"{name}-{uuid.uuid4().hex[:12]}"
    c.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", tuple(row.values()))


def seed(c, vault, computers, subscriptions, bots):
    for statement in LEGACY:
        c.execute(statement)
    insert(c, "humans", {"id": "ana", "name": "Ana", "email": "ana@example.test"})
    columns = {r[1] for r in c.execute("PRAGMA table_info(model_subscriptions)")}

    def credential(env, name, source=""):
        cid = uuid.uuid4().hex
        ciphertext, nonce = vault.cipher.encrypt(c, cid, "sk-" + uuid.uuid4().hex)
        insert(
            c,
            "credentials",
            {
                "id": cid,
                "name": name,
                "kind": "api_key",
                "env": env,
                "preview": "sk-…abcd",
                "ciphertext": ciphertext,
                "nonce": nonce,
                "source": source,
                "created_at": NOW,
                "updated_at": NOW,
                "updated_by": "human:ana",
                "created_by": "human:ana",
            },
        )
        return cid

    def grant(cid, subject):
        insert(
            c,
            "credential_grants",
            {
                "id": uuid.uuid4().hex,
                "credential_id": cid,
                "subject": subject,
                "granted_by": "human:ana",
                "created_at": NOW,
            },
        )

    shared = credential("ANTHROPIC_API_KEY", "Team key")
    grant(shared, "computers")
    runners = []
    for i in range(computers):
        rid = f"r{i:03d}"
        runners.append(rid)
        own = i < computers * 4 // 5  # the rest run on the shared key
        key = {"name": "ANTHROPIC_API_KEY", "source": "computer" if own else "shared"}
        readiness = {
            "schema_version": 1,
            "runtimes": {
                "claude": {"installed": True, "authenticated": "ready", "key": key},
                "codex": {"installed": True, "authenticated": "ready"},
            },
            "bots": {},
        }
        insert(
            c,
            "runners",
            {"id": rid, "label": f"Box {i:03d}", "owner_id": "ana", "token_hash": uuid.uuid4().hex, "created_at": NOW},
        )
        c.execute("INSERT OR IGNORE INTO runner_states(runner_id) VALUES(?)", (rid,))  # a trigger may have made it
        c.execute(
            "UPDATE runner_states SET last_seen_at=?, readiness=?, capabilities=? WHERE runner_id=?",
            (NOW, json.dumps(readiness), json.dumps(["model_sign_out_v1", "model_keys_v1"]), rid),
        )
        if own:
            cid = credential("ANTHROPIC_API_KEY", f"ANTHROPIC_API_KEY for {rid}", "computer:" + rid)
            insert(
                c,
                "computer_keys",
                {
                    "credential_id": cid,
                    "runner_id": rid,
                    "runtime": "claude",
                    "state": "active",
                    "created_at": NOW,
                    "created_by": "human:ana",
                },
            )
    plans = []
    for i in range(subscriptions):
        sid = f"s{i:03d}"
        plans.append(sid)
        row = {
            "id": sid,
            "name": f"Plan {i:03d}",
            "kind": "codex_login",
            "sealed": "x",
            "added_by": "human:ana",
            "created_at": NOW,
            "updated_at": NOW,
        }
        insert(c, "model_subscriptions", {k: v for k, v in row.items() if k in columns})
    for i, rid in enumerate(runners):
        insert(
            c,
            "computer_subscriptions",
            {
                "runner_id": rid,
                "runtime": "codex",
                "subscription_id": plans[i % len(plans)],
                "set_by": "human:ana",
                "updated_at": NOW,
            },
        )
    names = []
    for i in range(bots):
        bot = f"bot-{i:03d}"
        names.append(bot)
        insert(
            c, "bots", {"id": bot, "slug": bot, "display_name": f"Bot {i:03d}", "state": "active", "created_at": NOW}
        )
        grant(credential("ANTHROPIC_API_KEY" if i % 2 else "GITHUB_TOKEN", f"Key of {bot}"), "bot:" + bot)
    return runners, names


def seed_agent_payers(c, runners, bots):
    migration = importlib.import_module("backend.migrations." + POOLS)
    c.execute("UPDATE model_subscriptions SET owner_id='human:ana' WHERE owner_id='' AND kind='codex_login'")
    c.executemany("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", [(bot,) for bot in bots])
    c.executemany(
        "INSERT INTO assignments VALUES(?,?,1,?,'human:ana')",
        [(bot, runners[i % len(runners)], NOW) for i, bot in enumerate(bots)],
    )
    people = migration.active_people(c)
    migration.copy_bindings(c, people)
    migration.copy_key_grants(c, people)
    migration.revoke_key_grants(c)


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def timed(store, rounds, call):
    out = []
    for i in range(rounds):
        start = time.perf_counter()
        with store.read_transaction() as c:
            call(c, i)
        out.append((time.perf_counter() - start) * 1000)
    return {
        "p50_ms": round(percentile(out, 50), 3),
        "p95_ms": round(percentile(out, 95), 3),
        "mean_ms": round(statistics.fmean(out), 3),
    }


def plans(path, call):
    """{sql: [plan lines]} of every query `call` runs on a plain connection."""
    import sqlite3

    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    seen = []
    c.set_trace_callback(seen.append)
    try:
        call(c)
    finally:
        c.set_trace_callback(None)
    out = {}
    for sql in dict.fromkeys(s.strip() for s in seen):
        if not sql.upper().startswith(("SELECT", "WITH")):
            continue
        params = ("x",) * sql.count("?")
        out[" ".join(sql.split())] = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, params)]
    c.close()
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--computers", type=int, default=50)
    parser.add_argument("--subscriptions", type=int, default=200)
    parser.add_argument("--bots", type=int, default=200)
    parser.add_argument("--rounds", type=int, default=1000)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    from backend import model_subscriptions
    from backend.config import Settings
    from backend.credentials import Vault
    from backend.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hub.sqlite"
        store = Store(Settings(db_path=path))
        store.initialize(seed_market=False)
        vault = Vault(store)
        with store.transaction() as c:
            runners, bots = seed(c, vault, args.computers, args.subscriptions, args.bots)
        migrated = (args.repo / f"backend/migrations/{MOVE}.py").exists()
        dropped = (args.repo / f"backend/migrations/{DROP}.py").exists()
        for name, present in ((MOVE, migrated), (DROP, dropped)):
            if present:
                with store.transaction() as c:
                    importlib.import_module("backend.migrations." + name).up(c, NOW)
        pools = (args.repo / f"backend/migrations/{POOLS}.py").exists()
        with store.transaction() as connection:
            c = cast(Connection, connection)
            if pools:
                seed_agent_payers(c, runners, bots)
            c.execute("ANALYZE")
            counts = {
                t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in (
                    "runners",
                    "model_subscriptions",
                    "computer_subscriptions",
                    "credentials",
                    "credential_grants",
                    "bots",
                )
            }
        claim_bot = "bot" in model_subscriptions.for_claim.__code__.co_varnames
        if hasattr(model_subscriptions, "key_listing"):
            keys_for = model_subscriptions.keys_for
            key_listing = model_subscriptions.key_listing
        else:
            computer_keys = importlib.import_module("backend.computer_keys")
            keys_for = computer_keys.keys_for

            def legacy_listing(c):
                # What the Operations listing read for keys before D11-5: two passes, then one read per computer.
                served, own = computer_keys.serving(c), computer_keys.listing_by_runner(c)
                rows = c.execute("SELECT * FROM runners_with_state ORDER BY lower(label), id").fetchall()
                return served, own, [computer_keys.shared_listing(c, row) for row in rows]

            key_listing = legacy_listing

        def keys(c, i):
            index = i % len(runners)
            reply, _ = keys_for(c, vault, runners[index], "claude")
            assert bool(reply["credentials"]) == (not pools or index < len(runners) * 4 // 5), reply

        def listing(c, _):
            key_listing(c)

        def claim(c, i):
            runner = {"id": runners[i % len(runners)]}
            if pools:
                runner["capabilities"] = '["attempt_credentials_v1"]'
            extra = {"bot": bots[i % len(bots)]} if claim_bot else {}
            model_subscriptions.for_claim(c, runner, {"runtime": ("claude", "codex")[i % 2]}, **extra)

        report = {
            "repo": str(args.repo),
            "d11_4": migrated,
            "d11_5": dropped,
            "agent_payers": pools,
            "rows": counts,
            "keys_for": timed(store, args.rounds, keys),
            "for_claim": timed(store, args.rounds, claim),
            "key_listing": timed(store, args.rounds, listing),
            "plans": {
                "keys_for": plans(path, lambda c: keys_for(c, vault, runners[0], "claude")),
                "for_claim": plans(path, lambda c: (claim(c, 0), claim(c, 1))),
                "key_listing": plans(path, key_listing),
            },
        }
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
