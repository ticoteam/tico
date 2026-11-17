#!/usr/bin/env python3
"""Time `GET runner-model-credentials` (backend/computer_keys.py keys_for) and the claim's subscriptions
(model_subscriptions.for_claim) on a seeded hub, and print each query's plan, so a checkout before D11-4 and one after
can be compared on the same data.

    python3 scripts/benchmark_model_keys.py [--repo CHECKOUT] [--computers 50] [--subscriptions 200] [--bots 200]

`--repo` is the checkout whose `backend` is measured (this one by default). The data are fictional and the same for
both: each computer's own Claude key as Settings > Computers kept it (`computer_keys`), one key shared with every
computer, Settings > Models subscriptions given for Codex, and a key granted to each bot. A checkout with the
D11-4 migration (20261117061243) then moves those keys into subscriptions, as an upgrade does.
"""

import argparse
import importlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import uuid

NOW = "2026-11-17T00:00:00.000000Z"


def insert(c, table, values):
    """A row in `table` with `values`, its other required columns filled."""
    row = dict(values)
    for _, name, kind, notnull, default, pk in c.execute(f"PRAGMA table_info({table})"):
        if name not in row and notnull and default is None and not pk:
            row[name] = 0 if "INT" in (kind or "").upper() else f"{name}-{uuid.uuid4().hex[:12]}"
    c.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", tuple(row.values()))


def seed(c, vault, computers, subscriptions, bots):
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
    from backend import computer_keys, model_subscriptions
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
        migrated = (args.repo / "backend/migrations/20261117061243_computer_keys_to_subscriptions.py").exists()
        if migrated:
            module = importlib.import_module("backend.migrations.20261117061243_computer_keys_to_subscriptions")
            with store.transaction() as c:
                module.up(c, NOW)
        with store.transaction() as c:
            c.execute("ANALYZE")
            counts = {
                t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in (
                    "runners",
                    "model_subscriptions",
                    "computer_subscriptions",
                    "computer_keys",
                    "credentials",
                    "credential_grants",
                    "bots",
                )
            }
        claim_bot = "bot" in model_subscriptions.for_claim.__code__.co_varnames

        def keys(c, i):
            reply, _ = computer_keys.keys_for(c, vault, runners[i % len(runners)], "claude")
            assert reply["credentials"], reply

        def claim(c, i):
            runner = {"id": runners[i % len(runners)]}
            extra = {"bot": bots[i % len(bots)]} if claim_bot else {}
            model_subscriptions.for_claim(c, runner, {"runtime": ("claude", "codex")[i % 2]}, **extra)

        report = {
            "repo": str(args.repo),
            "d11_4": migrated,
            "rows": counts,
            "keys_for": timed(store, args.rounds, keys),
            "for_claim": timed(store, args.rounds, claim),
            "plans": {
                "keys_for": plans(path, lambda c: computer_keys.keys_for(c, vault, runners[0], "claude")),
                "for_claim": plans(path, lambda c: (claim(c, 0), claim(c, 1))),
            },
        }
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
