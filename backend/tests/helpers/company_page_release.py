"""Helpers that other tests share, moved out of test_company_page_release.py so they import no test file."""


def view_model(**over):
    stat = {
        "id": "s1",
        "label": "Revenue",
        "definition": "Money in.",
        "chart": "bars",
        "period": "2026-10",
        "as_of": "2026-11-01",
        "display": "$118k",
        "stale": False,
        "series": [
            {"period": "2026-09", "value": 104, "display": "$104k"},
            {"period": "2026-10", "value": 118, "display": "$118k"},
        ],
    }
    update = {
        "anchor": "2026-10",
        "title": "October",
        "author": {"name": "Maya R.", "kind": "person"},
        "period": "2026-10",
        "published_at": "2026-11-01T12:00:00Z",
        "version": 1,
        "correction_note": None,
        "sections": [
            {"key": "highlights", "items": [{"body": "Revenue grew.", "ask_id": None}]},
            {"key": "asks", "items": [{"body": "Intros please.", "ask_id": "ask-1"}]},
        ],
    }
    view = {
        "company": {
            "name": "Acme",
            "logo_url": "",
            "mission": "Quotes in seconds.",
            "badge": "public",
            "accent": "#1E4FD8",
            "updated_at": "2026-11-01T12:00:00Z",
        },
        "audience": {"kind": "public", "name": "Public"},
        "stats": [stat],
        "updates": [update],
        "next_before": None,
    }
    view.update(over)
    return view
