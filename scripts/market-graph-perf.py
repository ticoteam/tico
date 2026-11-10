#!/usr/bin/env python3
"""Opt-in synthetic API/browser benchmark; no production data or model calls.

python scripts/market-graph-perf.py --baseline-ref <commit> [--entities 20000 --edges 40000]
Outputs fixtures and measurements under .market-perf/ in this checkout.
"""

import argparse
from contextlib import nullcontext
import importlib
import json
from pathlib import Path
import random
import sqlite3
import statistics
import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import hubdb as H, market as M


def fixture(nodes, edges):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(H.MARKET_SCHEMA)
    # The Directory names: the market tables become entities, entity_links, entity_identifiers.
    importlib.import_module('backend.migrations.20261108011017_directory_core').up(conn)
    ids = ['company/self'] + [f'company/node-{i:05}' for i in range(1, nodes)]
    conn.executemany(
        'INSERT INTO entities(id,type,name,summary,created_at,created_by,updated_at) VALUES(?,?,?,?,?,?,?)',
        [
            (
                eid,
                'company' if i == 0 or i % 3 else 'person',
                f'Entity {i:05}',
                'Synthetic market summary. ' * 8,
                '2026-10-12',
                'seed',
                '2026-10-12',
            )
            for i, eid in enumerate(ids)
        ],
    )
    rng = random.Random(400)
    pairs = set()
    for i in range(min(180, nodes - 1)):
        pairs.add((0, i + 1))
    while len(pairs) < edges:
        a, b = rng.randrange(nodes), rng.randrange(nodes)
        if a != b:
            pairs.add((a, b))
    conn.executemany(
        'INSERT INTO entity_links(id,from_entity_id,link_key,to_entity_id,confidence,created_by,created_by_type,created_by_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
        [
            (f'edge-{i:06}', ids[a], 'works_at', ids[b], 0.9, 'seed', 'system', 'seed', '2026-10-12', '2026-10-12')
            for i, (a, b) in enumerate(sorted(pairs))
        ],
    )
    evidence_count = nodes * 4
    conn.executemany(
        'INSERT INTO market_evidence(id,captured_by,quote,our_read) VALUES(?,?,?,?)',
        [
            (f'ev-{i:06}', 'seed', 'Synthetic evidence quote. ' * 8, 'Synthetic reading. ' * 4)
            for i in range(evidence_count)
        ],
    )
    conn.executemany(
        'INSERT INTO market_citations VALUES(?,?,?)',
        [
            ('entity' if i < nodes else 'edge', ids[i] if i < nodes else f'edge-{(i - nodes) % edges:06}', f'ev-{i:06}')
            for i in range(evidence_count)
        ],
    )
    return conn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entities', type=int, default=4000)
    parser.add_argument('--edges', type=int, default=6000)
    parser.add_argument('--baseline-ref', required=True)
    parser.add_argument('--api-only', action='store_true')
    parser.add_argument('--after-only', action='store_true', help='skip the expensive old browser layout')
    args = parser.parse_args()
    if args.entities < 2 or not 0 < args.edges <= args.entities * (args.entities - 1):
        parser.error('need at least two entities and a feasible positive edge count')
    conn = fixture(args.entities, args.edges)
    app = FastAPI()

    @app.middleware('http')
    async def identity(request, call_next):
        request.state.identity = SimpleNamespace(role='owner', actor='human:owner')
        return await call_next(request)

    M.install(app, SimpleNamespace(read=lambda: nullcontext(conn)), SimpleNamespace(domain=lambda who: None), None)
    out = ROOT / '.market-perf'
    out.mkdir(exist_ok=True)
    data, api = {}, {}
    with TestClient(app) as client:
        for name, path in (
            ('entities', '/api/v2/market/entities'),
            ('edges', '/api/v2/market/edges?as_of=2026-10-12'),
            ('graph', '/api/v2/market/graph?focus=company/self&fill=true&limit=250&as_of=2026-10-12'),
            ('list', '/api/v2/market/entities?status=active&limit=50'),
            ('next_list', '/api/v2/market/entities?status=active&limit=50&offset=50'),
            ('search', f'/api/v2/market/entities?status=active&limit=50&q=Entity%20{args.entities - 1:05}'),
            ('focus', f'/api/v2/market/graph?focus=company/node-{args.entities - 1:05}&limit=250'),
            ('show', f'/api/v2/market/entities/company/node-{args.entities - 1:05}?names=true'),
            ('find', '/api/v2/market/entities?q=Synthetic'),
        ):
            times = []
            for _ in range(4):
                start = perf_counter()
                response = client.get(path)
                response.raise_for_status()
                times.append((perf_counter() - start) * 1000)
            data[name] = response.json()
            api[name] = {'ms': round(statistics.median(times[1:]), 2), 'bytes': len(response.content)}
    data['api'] = api
    (out / 'fixtures.json').write_text(json.dumps(data))
    # The page's TypeScript at the baseline, compiled the way `npm run build:ui` compiles it (the same esbuild and flags).
    # The graph moved to ui/market-page-graph.ts; a baseline from before that has it in market-page.ts.
    source = b'\n'.join(
        subprocess.run(
            ['git', 'show', f'{args.baseline_ref}:{path}'],
            cwd=ROOT,
            capture_output=True,
            check=path == 'ui/market-page.ts',
        ).stdout
        for path in ('ui/market-page.ts', 'ui/market-page-graph.ts')
    )
    before = subprocess.check_output(
        [str(ROOT / 'node_modules' / '.bin' / 'esbuild'), '--loader=ts', '--target=esnext', '--charset=utf8'],
        cwd=ROOT,
        input=source,
    )
    (out / 'before.js').write_bytes(before)
    result = {
        'entities': args.entities,
        'edges': args.edges,
        'evidence': args.entities * 4,
        'baseline_ref': subprocess.check_output(['git', 'rev-parse', args.baseline_ref], cwd=ROOT, text=True).strip(),
        'api': api,
        'drawn_nodes': len(data['graph']['entities']),
        'drawn_edges': len(data['graph']['edges']),
    }
    if not args.api_only:
        browser = subprocess.check_output(
            ['node', str(ROOT / 'scripts/market-graph-perf.cts'), *(['--after-only'] if args.after_only else [])],
            cwd=ROOT,
            text=True,
        )
        result['browser'] = json.loads(browser)
    (out / 'results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    conn.close()


if __name__ == '__main__':
    main()
