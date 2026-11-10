"""Helpers that other tests share, moved out of test_settings_assistant.py so they import no test file."""

from backend.tests.helpers.api import headers
from clients import hubtools
from clients.tico import APIError


def mcp(api, name, args, token):
    """A `hub` tool in the Assistant's run, answered by this server: `(error, payload)` as the MCP helper gives."""

    class Turn:
        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            extra = {"X-Tico-On-Behalf-Of": delegate if isinstance(delegate, str) else "turn"} if delegate else {}
            r = api.request(method, "/api/v2/" + path, json=body, params=query, headers={**headers(token), **extra})
            if r.status_code >= 400:
                raise APIError(r.json()["error"]["code"], r.json()["error"]["detail"], status=r.status_code)
            return r.json()

        def get(self, path, **query):
            return self.call("GET", path, query=query)

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body or {}, key)

    try:
        return False, hubtools.BY_NAME[name]["fn"](Turn(), args)
    except APIError as refused:
        return True, {"error": refused.code, "detail": str(refused)}
