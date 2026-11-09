"""Helpers that other tests share, moved out of test_automation_cli.py so they import no test file."""

from urllib.parse import urlencode
from backend.tests.helpers.api import headers
from clients import hubcli, remotecli


class Client:
    """The hub client over the test app, as one caller."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def _call(self, method, path, body=None):
        r = self.api.request(method, "/api/v2/" + path, json=body, headers=headers(self.token))
        if r.status_code != 200:
            raise remotecli.APIError(str(r.status_code), r.text, r.status_code)
        return r.json()

    def call(self, method, path, body=None, key=None, query=None):
        query = urlencode({k: v for k, v in (query or {}).items() if v is not None})
        return self._call(method, path + ("?" + query if query else ""), body)

    def get(self, path, **params):
        return self.call("GET", path, query=params)

    def post(self, path, body, key=None):
        return self._call("POST", path, body)

    def patch(self, path, body, key=None):
        return self._call("PATCH", path, body)


def cli(api, token, *argv):
    return remotecli.automation(Client(api, token), hubcli.parser().parse_args(["automation", *argv]))
