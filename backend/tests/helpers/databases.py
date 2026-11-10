"""Helpers that other tests share, moved out of test_databases.py so they import no test file."""

import yaml
from clients.tico import APIError


class FakeHub:
    """The two hub calls `tico db` makes: who am I, and record this query."""

    def __init__(self, actor="bot:ops", catalog=None, fail_audit=False):
        self.actor, self.catalog, self.fail_audit, self.audits = actor, catalog or {}, fail_audit, []

    def get(self, path, **query):
        if path == "me":
            return {"actor": self.actor}
        name = path.split("/")[1]
        query_id = query.get("id")
        if (name, query_id) not in self.catalog:
            raise APIError("not_found", "No query", 404)
        return self.catalog[(name, query_id)]

    def post(self, path, body, key=None):
        assert path == "databases/audit"
        if self.fail_audit:
            raise APIError("unavailable", "hub down", 503, retryable=True)
        self.audits.append(body)
        return {"ok": True}


def workspace(tmp_path, slug="ops", entries=None):
    """A workspace with emp-<slug>/employee.yaml declaring `entries` under access."""
    repo = tmp_path / "ws" / f"emp-{slug}"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "employee.yaml").write_text(yaml.safe_dump({"name": slug, "access": entries or []}))
    return {"HUB_WORKSPACE": str(tmp_path / "ws")}
