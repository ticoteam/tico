"""The client `tico contacts` calls, answered by the app's own test client: shared by the Directory route tests."""

from backend.tests.helpers.api import headers
from clients.tico import APIError


class Forward:
    """The client `tico contacts` calls, answered by the app's own test client as `token`: each request is the real one."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def _answer(self, r):
        if r.status_code >= 400:
            error = r.json()["error"]
            raise APIError(error["code"], error["detail"], r.status_code)
        return r.json()

    def get(self, path, /, **query):
        params = {k: v for k, v in query.items() if v is not None}
        return self._answer(self.api.get("/api/v2/" + path, params=params, headers=headers(self.token)))

    def post(self, path, body=None, key=None):
        return self._answer(self.api.post("/api/v2/" + path, json=body or {}, headers=headers(self.token, key)))
