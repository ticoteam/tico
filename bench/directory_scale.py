#!/usr/bin/env python3
"""Directory scale gate (spec §9.1): a deterministic fixture, and the §9.1 operations timed against their targets.

    python3 bench/directory_scale.py --people 20000 --orgs 2000 [--runs 40] [--json report.json]

The fixture is fictional (Acme, example.test) and deterministic: the same counts give the same rows and the same reads.
People are written through the store's upsert and link, in transactions of --chunk rows, so each transaction holds the
writer gate for one chunk. The one raw write is the external CRM refs (entity_external_refs has no writer in this branch;
sync is a later track). Reads run through the store as the owner, so the read rule is inside the timing.

Gated (exit 1 on a miss, p95 over --runs calls): get by email, domain and external id; entity page; directory list page;
name search. Also gated: the longest chunk transaction, which bounds how long any other write waits (§9.1 "no other write
waits > 1 s"), and the lookup plans: each lookup must SEARCH an index, not SCAN, because a scan is O(rows) at any N.
Skipped with the reason: resolving rules cold (spec §3 Rules) and the linker on a 2k-word transcript (§7.1), neither is in
this branch. Informational, not gated: the upsert rate, and the extrapolated minutes for a 1M-person import.
"""

import argparse
import json
import math
import random
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import hubdb as H
from backend.config import Settings
from backend.entities import store as ES
from backend.store import Store

OWNER_EMAIL = "ana@acme.example"
OWNER = "human:ana"
SEED = 20261107
CHUNK = 2000
WRITE_WAIT_MS = 1000
# §9.1 targets, in ms, at p95.
TARGETS_MS = {
    "get_by_email": 10,
    "get_by_domain": 10,
    "get_by_external_id": 10,
    "entity_page": 200,
    "directory_list": 300,
    "name_search": 300,
}
SKIPPED = {
    "resolve_rules_cold": "rule resolution (spec §3 Rules) is not in this branch",
    "linker_2k_words": "the linker (spec §7.1) is not in this branch",
}
FIRST = ("Jane", "Ana", "Ben", "Cara", "Eve", "Fay", "Omar", "Lin")
LAST = ("Ortiz", "Marsh", "Lee", "Patel", "Stone", "Rivera", "Kim", "Okafor")
PLAN_QUERIES = {
    "identifier_lookup": "SELECT DISTINCT entity_id FROM entity_identifiers WHERE normalized_value = ?",
    "external_ref_lookup": "SELECT DISTINCT entity_id FROM entity_external_refs WHERE external_id = ?",
}


def person_email(i: int) -> str:
    return f"person{i:07d}@example.test"


def person_name(i: int) -> str:
    return f"{FIRST[i % 8]} {LAST[(i // 8) % 8]} {i:07d}"


def org_domain(j: int) -> str:
    return f"org{j:06d}.example.test"


def crm_id(i: int) -> str:
    return f"crm{i:08d}"


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def build(store: Store, *, people: int, orgs: int, chunk: int) -> tuple[list[float], list[float], dict[str, int]]:
    """Organizations first, then people (each linked to an org; every fourth has an external CRM id), in transactions of
    `chunk` rows. Returns the wall time of each transaction, the time of each person upsert, and the row counts."""
    chunk_ms: list[float] = []
    upsert_ms: list[float] = []
    org_ids: list[str] = []
    external = 0
    items = [("org", j) for j in range(orgs)] + [("person", i) for i in range(people)]
    for start in range(0, len(items), chunk):
        started = time.perf_counter()
        with store.transaction() as c:
            for kind, n in items[start : start + chunk]:
                if kind == "org":
                    org_ids.append(
                        ES.upsert(c, OWNER, "organization", match={"domain": org_domain(n)}, name=f"Company {n:06d}")[
                            "id"
                        ]
                    )
                    continue
                began = time.perf_counter()
                entity = ES.upsert(c, OWNER, "person", match={"email": person_email(n)}, name=person_name(n))
                upsert_ms.append((time.perf_counter() - began) * 1000)
                if org_ids:
                    ES.link(c, OWNER, entity["id"], "works_at", org_ids[n % len(org_ids)])
                if n % 4 == 0:
                    c.execute(
                        "INSERT INTO entity_external_refs (id, entity_id, connection_id, system, external_type, external_id, "
                        "url, revision, synced_at, status) VALUES (?, ?, 'conn_bench', 'crm', 'contact', ?, '', '', ?, 'active')",
                        (f"ext{n:09d}", entity["id"], crm_id(n), H.now()),
                    )
                    external += 1
        chunk_ms.append((time.perf_counter() - started) * 1000)
    return chunk_ms, upsert_ms, {"people": people, "orgs": len(org_ids), "external_refs": external}


def plan_gate(plans: dict[str, list[str]]) -> dict:
    """The lookups must seek an index. A scan is O(rows) and misses §9.1 at scale whatever the timing says at a small N,
    so it is gated on the plan itself: a lookup whose plan has a SCAN step fails."""
    scans = sorted(name for name, steps in plans.items() if any(step.startswith("SCAN") for step in steps))
    return {
        "name": "lookup_plans",
        "target_ms": None,
        "p95_ms": None,
        "max_ms": None,
        "runs": 0,
        "status": "miss" if scans else "pass",
        "scans": scans,
    }


def timed(fn, runs: int, expect) -> tuple[list[float], bool]:
    """Runs fn once to warm, then `runs` times; each call's result must satisfy `expect`."""
    fn()
    samples: list[float] = []
    correct = True
    for _ in range(runs):
        began = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - began) * 1000)
        correct = correct and bool(expect(result))
    return samples, correct


def gated(name: str, samples: list[float], correct: bool) -> dict:
    target = TARGETS_MS[name]
    value = p95(samples)
    status = "pass" if correct and value <= target else ("wrong" if not correct else "miss")
    return {
        "name": name,
        "target_ms": target,
        "p95_ms": round(value, 3),
        "max_ms": round(max(samples), 3),
        "runs": len(samples),
        "status": status,
    }


def run(root: Path, *, people: int, orgs: int, runs: int, chunk: int = CHUNK) -> dict:
    registry = root / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": OWNER_EMAIL, "bot_admins": []}))
    store = Store(Settings(db_path=root / "hub.db", registry_dir=registry))
    store.initialize()
    with store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO humans(id, name, email) VALUES('ana', 'Ana Marsh', ?)", (OWNER_EMAIL,))
        ES.ensure_self(c, company_name="Acme Lights", domain="acme.example")

    chunk_ms, upsert_ms, counts = build(store, people=people, orgs=orgs, chunk=chunk)
    rng = random.Random(SEED)
    picks = [rng.randrange(people) for _ in range(runs)]
    org_picks = [rng.randrange(orgs) for _ in range(runs)] if orgs else []
    external_picks = [4 * rng.randrange(people // 4) for _ in range(runs)] if people >= 4 else []

    ops: list[dict] = []
    with store.read() as c:
        person_id = {i: ES.get(c, OWNER, person_email(i))[0]["id"] for i in set(picks)}
        cycle = iter(picks * (runs + 1))
        samples, correct = timed(lambda: ES.get(c, OWNER, person_email(next(cycle))), runs, lambda rows: len(rows) == 1)
        ops.append(gated("get_by_email", samples, correct))

        if org_picks:
            cycle = iter(org_picks * (runs + 1))
            samples, correct = timed(
                lambda: ES.get(c, OWNER, org_domain(next(cycle))), runs, lambda rows: len(rows) == 1
            )
            ops.append(gated("get_by_domain", samples, correct))
        if external_picks:
            cycle = iter(external_picks * (runs + 1))
            samples, correct = timed(lambda: ES.get(c, OWNER, crm_id(next(cycle))), runs, lambda rows: len(rows) == 1)
            ops.append(gated("get_by_external_id", samples, correct))

        cycle = iter(picks * (runs + 1))
        samples, correct = timed(lambda: ES.card(c, OWNER, person_id[next(cycle)]), runs, lambda card: card is not None)
        ops.append(gated("entity_page", samples, correct))

        samples, correct = timed(
            lambda: ES.find(c, OWNER, "person", limit=50), runs, lambda page: page.get("count", 0) >= 1
        )
        ops.append(gated("directory_list", samples, correct))

        cycle = iter(picks * (runs + 1))

        def prefix_search():
            i = next(cycle)
            return ES.find(c, OWNER, "person", name=f"{FIRST[i % 8]} {LAST[(i // 8) % 8]}", limit=50)

        samples, correct = timed(prefix_search, runs, lambda page: page.get("count", 0) >= 1)
        ops.append(gated("name_search", samples, correct))

        plans = {
            name: [row[3] for row in c.execute(f"EXPLAIN QUERY PLAN {sql}", ("x",))]
            for name, sql in PLAN_QUERIES.items()
        }
        ops.append(plan_gate(plans))
        counts["entities"] = c.execute("SELECT count(*) FROM entities").fetchone()[0]
        counts["links"] = c.execute("SELECT count(*) FROM entity_links").fetchone()[0]

    longest = max(chunk_ms)
    write = {
        "name": "longest_chunk_transaction",
        "target_ms": WRITE_WAIT_MS,
        "p95_ms": round(longest, 3),
        "max_ms": round(longest, 3),
        "runs": len(chunk_ms),
        "status": "pass" if longest < WRITE_WAIT_MS else "miss",
    }
    ops.append(write)
    total_s = sum(chunk_ms) / 1000
    return {
        "fixture": {**counts, "chunk": chunk, "seed": SEED},
        "ops": ops,
        "skipped": [{"name": name, "reason": reason} for name, reason in SKIPPED.items()],
        "informational": {
            "upsert_p95_ms": round(p95(upsert_ms), 3) if upsert_ms else None,
            "import_seconds": round(total_s, 2),
            "extrapolated_minutes_1m_people": round(total_s / max(people, 1) * 1_000_000 / 60, 1),
            "query_plans": plans,
        },
        "passed": all(op["status"] == "pass" for op in ops),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--people", type=int, default=1_000_000)
    parser.add_argument("--orgs", type=int, default=100_000)
    parser.add_argument("--runs", type=int, default=40)
    parser.add_argument("--chunk", type=int, default=CHUNK)
    parser.add_argument("--root", type=Path, help="where the database goes (default: a temporary directory)")
    parser.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        root = args.root or Path(tmp)
        root.mkdir(parents=True, exist_ok=True)
        report = run(root, people=args.people, orgs=args.orgs, runs=args.runs, chunk=args.chunk)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json:
        args.json.write_text(text + "\n")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
