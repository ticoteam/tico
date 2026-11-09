"""Helpers that other tests share, moved out of test_cloudflare.py so they import no test file."""

import json


class Api:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body):
        path = url.split("/client/v4")[1]
        self.calls.append((method, path, json.loads(body) if body else None))
        for (m, p), resp in self.routes.items():
            if m == method and path.startswith(p):
                return resp if isinstance(resp, tuple) else (200, {"success": True, "result": resp})
        return 404, {"success": False, "errors": [{"message": "no route"}]}
