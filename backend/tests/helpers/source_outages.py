"""Helpers that other tests share, moved out of test_source_outages.py so they import no test file."""


def notices(c):
    return [dict(r) for r in c.execute("SELECT * FROM messages WHERE json_extract(refs,'$.source_outage') IS NOT NULL")]


def managers(c):
    c.execute("UPDATE task_types SET manager='coo',role='engineering' WHERE id='general'")
