"""Helpers that other tests share, moved out of test_source_outages.py so they import no test file."""


def notices(c):
    return [dict(r) for r in c.execute("SELECT * FROM messages WHERE json_extract(refs,'$.source_outage') IS NOT NULL")]


def managers(c):
    """An Engineering pipeline the COO manages: a source outage tells its manager."""
    from backend import hubdb as H

    now = H.now()
    c.execute(
        "INSERT INTO pipelines(id,name,manager_id,preset,created_at,updated_at) "
        "VALUES('outage-engineering','Outage engineering','bot:assistant','engineering',?,?)",
        (now, now),
    )
