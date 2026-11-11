"""Helpers that other tests share, moved out of test_task_rules.py so they import no test file."""

import json


def department(api):
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster['org_groups'] = {
            'engineering': {'name': 'Engineering'},
            'web': {'name': 'Web', 'parent': 'engineering'},
            'marketing': {'name': 'Marketing'},
        }
        next(p for p in roster['people'] if p['id'] == 'cara')['team'] = 'web'
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
