"""Helpers that other tests share, moved out of test_bot_contact.py so they import no test file."""

from backend.tests.helpers.api import post


def bot(api, slug, name, reports_to=None):
    return post(
        api,
        "bots",
        {
            "slug": slug,
            "display_name": name,
            "description": "A test bot.",
            "reports_to": reports_to,
            "status": "active",
            "repo": "emp-" + slug,
            "thread_mode": "personal",
            "model": "hermes-profile",
            "effort": "as-configured",
            "harness": "hermes",
            "operator": "ana",
            "owners": ["ana"],
            "runner_id": None,
        },
    )


def token(api, slug):
    return post(api, "bots/" + slug + "/agent-credential", {})["token"]
