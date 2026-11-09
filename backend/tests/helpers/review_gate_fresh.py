"""Helpers that other tests share, moved out of test_review_gate_fresh.py so they import no test file."""

from types import SimpleNamespace
from backend.tests.helpers.review_gate import HEAD


def github(api, monkeypatch, checks, head=HEAD):
    """A configured GitHub App whose PR #412 has this head and check-run conclusions; returns the paths read."""
    service = api.app.state.github_app
    monkeypatch.setattr(service, 'row', lambda c: {'org': 'ticoteam'})
    monkeypatch.setattr(service, 'mint', lambda *a: ('synthetic', 'later'))
    reads = []

    def fetch(method, path, **kw):
        reads.append(path)
        if '/pulls/' in path:
            data = {'head': {'sha': head}}
        elif path.endswith('check-runs'):
            data = {
                'check_runs': [
                    {'id': i, 'name': name, 'conclusion': result} for i, (name, result) in enumerate(checks.items(), 1)
                ]
            }
        else:
            data = []
        return SimpleNamespace(status_code=200, json=lambda: data)

    monkeypatch.setattr(service, '_call', fetch)
    return reads
