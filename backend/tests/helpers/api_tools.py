"""Helpers that other tests share, moved out of test_api_tools.py so they import no test file."""

from backend.tests.helpers.api import post, put


def tool(api, name="Close CRM", **body):
    return post(api, "api-tools", {"name": name, **body})


def key(api, tid, value, level="read_write", token="ana-test", expected=200):
    return post(
        api,
        f"api-tools/{tid}/keys",
        {"name": f"Close {level}", "value": value, "access_level": level},
        token,
        expected=expected,
    )


def level(api, tid, subject, lv, token="ana-test", expected=200):
    return put(api, f"api-tools/{tid}/access/{subject}", {"level": lv}, token, expected=expected)
