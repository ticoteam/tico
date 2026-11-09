"""Helpers that other tests share, moved out of test_org_turns.py so they import no test file."""

import json


def people(api, rows):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps({'people': rows}),))


def one_person(api):
    people(api, [{'id': 'ana', 'email': 'ana@acme.example'}])
