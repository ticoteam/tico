#!/usr/bin/env python3
"""Time the reads D11-11 moves into the vault on a seeded hub, and print each query's plan, so a checkout before D11-11
and one after can be compared on the same data:

- a turn's subscription value (model_subscriptions.row + value_of, what `credential-runtime` run_values reads per
  subscription),
- the claim's subscriptions (model_subscriptions.for_claim),
- the Tico AI Gateway key and address (gateway_connection.target, every Gateway send),
- the Tico company server key (company_server._key).

    python3 scripts/benchmark_one_store.py [--repo CHECKOUT] [--bots 200] [--subscriptions 50] [--credentials 500]

`--repo` is the checkout whose `backend` is measured (this one by default). The data are fictional and the same for
both: subscriptions sealed in their own rows (a tenth of them kept keys pointing at a vault credential, as D11-4 left
them), each given to a computer, bots each granted a key, and the Gateway and company server records sealed as an
older server kept them. A checkout with the D11-11 migration (20261117103033) then moves them into the vault, as an
upgrade does.
"""

import argparse
import importlib
import json
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

from benchmark_model_keys import insert, percentile, plans

NOW = "2026-11-17T00:00:00.000000Z"
MOVE = "20261117103033_one_encrypted_store"


def seed(c, vault, bots, subscriptions, credentials):
    insert(c, "humans", {"id": "ana", "name": "Ana", "email": "ana@example.test"})

    def credential(env, name):
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
                "created_at": NOW,
                "updated_at": NOW,
                "updated_by": "human:ana",
                "created_by": "human:ana",
            },
        )
        return cid

    names = []
    for i in range(bots):
        bot = f"bot-{i:03d}"
        names.append(bot)
        insert(
            c, "bots", {"id": bot, "slug": bot, "display_name": f"Bot {i:03d}", "state": "active", "created_at": NOW}
        )
    made = 0
    kept = []
    for i in range(subscriptions // 10):
        kept.append(credential("ANTHROPIC_API_KEY", f"Kept key {i}"))
        made += 1
    while made < credentials:
        cid = credential("ANTHROPIC_API_KEY" if made % 2 else "GITHUB_TOKEN", f"Key {made}")
        insert(
            c,
            "credential_grants",
            {
                "id": uuid.uuid4().hex,
                "credential_id": cid,
                "subject": "bot:" + names[made % len(names)],
                "granted_by": "human:ana",
                "created_at": NOW,
            },
        )
        made += 1
    runners, subs = [], []
    for i in range(subscriptions):
        sid, rid = f"s{i:03d}", f"r{i:03d}"
        subs.append(sid)
        runners.append(rid)
        keep = kept[i] if i < len(kept) else None
        value = "sk-ant-oat01-" + uuid.uuid4().hex
        insert(
            c,
            "model_subscriptions",
            {
                "id": sid,
                "name": f"Plan {i:03d}",
                "kind": "claude_token",
                "sealed": "" if keep else vault.cipher.seal(c, f"model_subscription:{sid}", value),
                "credential_id": keep,
                "added_by": "human:ana",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )
        readiness = {"schema_version": 1, "runtimes": {"claude": {"installed": True, "authenticated": "ready"}}}
        insert(
            c,
            "runners",
            {"id": rid, "label": f"Box {i:03d}", "owner_id": "ana", "token_hash": uuid.uuid4().hex, "created_at": NOW},
        )
        c.execute("INSERT OR IGNORE INTO runner_states(runner_id) VALUES(?)", (rid,))
        c.execute(
            "UPDATE runner_states SET last_seen_at=?, readiness=? WHERE runner_id=?", (NOW, json.dumps(readiness), rid)
        )
        insert(
            c,
            "computer_subscriptions",
            {"runner_id": rid, "runtime": "claude", "subscription_id": sid, "set_by": "human:ana", "updated_at": NOW},
        )
    gateway = {
        "key": vault.cipher.seal(c, "gateway-connection:key", "tgk_" + uuid.uuid4().hex),
        "install": "gi_bench",
        "install_secret": vault.cipher.seal(c, "gateway-connection:install", "gis_" + uuid.uuid4().hex),
        "origin": "automatic",
    }
    server = {"key": vault.cipher.seal(c, "company-server:key", "tsk_" + uuid.uuid4().hex), "company": {"id": "co1"}}
    for key, record in (("gateway-connection", gateway), ("company-server", server)):
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (key, json.dumps(record, sort_keys=True)))
    return runners, subs, names


def stats(out):
    return {
        "p50_ms": round(percentile(out, 50), 4),
        "p95_ms": round(percentile(out, 95), 4),
        "mean_ms": round(statistics.fmean(out), 4),
    }


def timed(store, rounds, call):
    out = []
    for i in range(rounds):
        start = time.perf_counter()
        with store.read_transaction() as c:
            call(c, i)
        out.append((time.perf_counter() - start) * 1000)
    return stats(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bots", type=int, default=200)
    parser.add_argument("--subscriptions", type=int, default=50)
    parser.add_argument("--credentials", type=int, default=500)
    parser.add_argument("--rounds", type=int, default=2000)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    from backend import company_server, gateway_connection, model_subscriptions
    from backend.config import Settings
    from backend.credentials import Vault
    from backend.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hub.sqlite"
        store = Store(Settings(db_path=path))
        store.initialize(seed_market=False)
        vault = Vault(store)
        with store.transaction() as c:
            runners, subs, bots = seed(c, vault, args.bots, args.subscriptions, args.credentials)
        moved = (args.repo / f"backend/migrations/{MOVE}.py").exists()
        if moved:
            with store.transaction() as c:
                importlib.import_module("backend.migrations." + MOVE).move(c, vault.cipher)
        with store.transaction() as c:
            c.execute("ANALYZE")
            counts = {
                t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("bots", "model_subscriptions", "credentials", "credential_grants", "runners")
            }

        def value(c, i):
            sub = model_subscriptions.row(c, subs[i % len(subs)])
            assert model_subscriptions.value_of(c, vault.cipher, sub)

        def claim(c, i):
            # Empty for a kept key and a bot with its own Anthropic key (the grant signs it in), on both sides.
            model_subscriptions.for_claim(
                c, {"id": runners[i % len(runners)]}, {"runtime": "claude"}, bot=bots[i % len(bots)]
            )

        def gateway(c, _):
            assert gateway_connection.target(c, vault)[0]

        server = []
        for _ in range(args.rounds):
            start = time.perf_counter()
            assert company_server._key(store, vault)[0]
            server.append((time.perf_counter() - start) * 1000)

        def plain_value(c):
            for sid in (subs[0], subs[-1]):  # a kept key, then one of its own
                model_subscriptions.value_of(
                    c, vault.cipher, c.execute("SELECT * FROM model_subscriptions WHERE id=?", (sid,)).fetchone()
                )

        report = {
            "repo": str(args.repo),
            "d11_11": moved,
            "rows": counts,
            "subscription_value": timed(store, args.rounds, value),
            "for_claim": timed(store, args.rounds, claim),
            "gateway_target": timed(store, args.rounds, gateway),
            "company_server_key": stats(server),
            "plans": {
                "subscription_value": plans(path, plain_value),
                "for_claim": plans(
                    path,
                    lambda c: model_subscriptions.for_claim(c, {"id": runners[-1]}, {"runtime": "claude"}, bot=bots[0]),
                ),
                "gateway_target": plans(path, lambda c: gateway_connection.target(c, vault)),
            },
        }
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
