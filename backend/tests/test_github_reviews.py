"""Reviewer credentials keep the existing bot/repository boundary and pin real verdicts."""
import json

import httpx
import jwt

from backend import github_app as G
from backend.tests.test_github_app import (api, gh, auth, runner_token, turn_token, PEM, KEY)  # noqa: F401
from backend.tests.test_bot_product_repos import ready

HEAD = "a" * 40
REPO = "Acme/product"


def configured(api, gh, tmp_path, monkeypatch):
    ready(api, gh)
    gh.repositories = [{"full_name": "Acme/" + name, "default_branch": "main"}
                       for name in ("product", "docs", "emp-cmo", "emp-cpo")]
    refreshed = api.post("/api/v2/repositories/refresh", headers=auth())
    assert refreshed.status_code == 200, refreshed.text
    for name in ("product", "docs"):
        enabled = api.put("/api/v2/repositories/Acme/" + name, json={"enabled": True}, headers=auth())
        assert enabled.status_code == 200, enabled.text
    settings = api.app_state.store.settings
    settings.github_reviewer_bots = ("cmo",)
    settings.github_reviewer_app_id = "9001"
    settings.github_reviewer_installation_id = "99"
    key = tmp_path / "reviewer.pem"
    key.write_text(PEM)
    settings.github_reviewer_private_key_file = str(key)
    grant = api.put("/api/v2/bots/cmo/repositories", json={"mode": "chosen", "chosen": [
        {"full_name": REPO, "access": "write"}, {"full_name": "Acme/docs", "access": "read"}]}, headers=auth())
    assert grant.status_code == 200, grant.text
    seen = {"head": HEAD, "reviews": [], "issuers": []}

    def fake(request):
        path = request.url.path
        if path.startswith("/app/installations/"):
            claims = jwt.decode(request.headers["authorization"].split(" ")[1], KEY.public_key(), algorithms=["RS256"])
            seen["issuers"].append((path, claims["iss"]))
        if path == "/app/installations/99":
            return httpx.Response(200, json={"id": 99, "account": {"login": seen.get("org", "Acme")},
                                           "permissions": gh.permissions})
        if path == "/repos/Acme/product/pulls/7":
            return httpx.Response(200, json={"state": "open", "head": {"sha": seen["head"]}})
        if path == "/repos/Acme/product/pulls/7/reviews":
            seen["reviews"].append((json.loads(request.content), request.headers["authorization"]))
            if seen.get("lost_response"):
                raise httpx.ReadTimeout("synthetic lost response", request=request)
            payload = json.loads(request.content)
            return httpx.Response(200, json={"id": len(seen["reviews"]), "html_url": "https://github.com/Acme/product/pull/7#review",
                                            "commit_id": payload["commit_id"],
                                            "state": {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}[payload["event"]]})
        response = gh(request)
        if path == "/app/installations/99/access_tokens" and response.status_code == 201:
            return httpx.Response(201, json={**response.json(), "token": "reviewer-" + response.json()["token"]})
        return response

    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(fake))
    return settings, seen


def submit(api, verdict="approve", *, head=HEAD, key=None, repository=REPO):
    return api.post("/api/v2/github/reviews", json={"repository": repository, "number": 7,
                    "head": head, "verdict": verdict, "body": "Reviewed credentials and tests."},
                    headers={**auth("bot-test"), "Idempotency-Key": key or verdict})


def test_runner_gets_separate_reviewer_tokens_without_changing_builder_or_grants(api, gh, tmp_path, monkeypatch):
    settings, seen = configured(api, gh, tmp_path, monkeypatch)
    runner_token(api, "cpo")
    builder = turn_token(api, "cpo").json()
    reviewer = turn_token(api, "cmo").json()
    assert not builder["token"].startswith("reviewer-")
    assert reviewer["token"].startswith("reviewer-")
    assert all(group["token"].startswith("reviewer-") for group in reviewer["tokens"])
    assert any("Acme/docs" in g["repositories"] and g["access"] == "read" for g in reviewer["tokens"])
    assert not any("Acme/docs" in g["repositories"] and g["access"] == "write" for g in reviewer["tokens"])
    assert turn_token(api, "cmo", purpose="git").json()["token"].startswith("reviewer-")
    assert ("/app/installations/99/access_tokens", "9001") in seen["issuers"]
    assert ("/app/installations/77/access_tokens", "4242") in seen["issuers"]
    # Bot identity, role and repository scope cannot be supplied by the run itself.
    assert api.post("/api/v2/github/token", json={"bot": "cpo"}, headers=auth("bot-test")).status_code == 403
    assert api.post("/api/v2/github/token", json={"bot": "cmo", "identity": "primary"}, headers=auth("runner-test")).status_code == 422
    assert api.post("/api/v2/github/token", json={"bot": "cmo", "repository": "Acme/private"}, headers=auth("runner-test")).status_code == 403
    # A bad reviewer configuration never downgrades to the author's credentials or harms Builder.
    settings.github_reviewer_app_id = "4242"
    assert turn_token(api, "cmo").status_code == 409
    assert turn_token(api, "cpo").status_code == 200
    settings.github_reviewer_app_id = "9001"
    seen["org"] = "Elsewhere"
    api.app_state.github_reviewer_app.live.clear()
    api.app_state.github_reviewer_app.cache.clear()
    assert turn_token(api, "cmo").status_code == 409
    seen["org"] = "Acme"
    api.app_state.github_reviewer_app.live.clear()
    (tmp_path / "reviewer.pem").unlink()
    assert turn_token(api, "cmo").status_code == 503
    # Explicit rollback restores the shared App, not an automatic fallback on a broken key.
    settings.github_reviewer_bots = ()
    assert not turn_token(api, "cmo").json()["token"].startswith("reviewer-")


def test_real_reviews_pin_head_verdict_and_identity_and_refuse_stale_or_unauthorized_writes(api, gh, tmp_path, monkeypatch):
    settings, seen = configured(api, gh, tmp_path, monkeypatch)
    approved = submit(api)
    assert approved.status_code == 200, approved.text
    assert approved.json()["event"] == "APPROVE"
    assert submit(api).json() == approved.json() and len(seen["reviews"]) == 1
    changes = submit(api, "changes")
    assert changes.status_code == 200 and changes.json()["event"] == "REQUEST_CHANGES", changes.text
    for verdict, event, (payload, authz) in zip(("approve", "changes"), ("APPROVE", "REQUEST_CHANGES"), seen["reviews"]):
        assert payload == {"event": event, "commit_id": HEAD,
                           "body": f"Reviewed credentials and tests.\n\nVERDICT: {verdict} PR #7 head {HEAD}"}
        assert authz.startswith("Bearer reviewer-")
    seen["head"] = "b" * 40
    assert submit(api, key="moved").status_code == 409
    assert submit(api, repository="Acme/docs", key="read-only").status_code == 403
    assert submit(api, repository="elsewhere/product", key="foreign").status_code == 403
    assert len(seen["reviews"]) == 2
    seen["head"] = HEAD
    seen["lost_response"] = True
    assert submit(api, key="lost").status_code == 502
    assert submit(api, key="lost").status_code == 409
    assert len(seen["reviews"]) == 3  # The lost response is never blindly posted a second time.
    seen["lost_response"] = False
    settings.github_reviewer_bots = ()
    fallback = submit(api, key="rollback")
    assert fallback.status_code == 200 and fallback.json()["event"] == "COMMENT", fallback.text
    assert seen["reviews"][-1][0]["commit_id"] == HEAD
    assert not seen["reviews"][-1][1].startswith("Bearer reviewer-")

    # A cached receipt does not bypass a subsequently revoked grant.
    revoked = api.put("/api/v2/bots/cmo/repositories", json={"mode": "own"}, headers=auth())
    assert revoked.status_code == 200, revoked.text
    assert submit(api).status_code == 403
    assert len(seen["reviews"]) == 4
