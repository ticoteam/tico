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
            if seen.get("refusal"):
                return httpx.Response(seen["refusal"], json={"message": seen.get("refusal_message", "synthetic refusal")})
            seen["reviews"].append((json.loads(request.content), request.headers["authorization"]))
            if seen.get("lost_response"):
                raise httpx.ReadTimeout("synthetic lost response", request=request)
            payload = json.loads(request.content)
            return httpx.Response(200, json={"id": len(seen["reviews"]), "html_url": "https://github.com/Acme/product/pull/7#review",
                                            "commit_id": seen.get("receipt_head", payload["commit_id"]),
                                            "state": seen.get("receipt_state") or {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}[payload["event"]]})
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
    # Selected reviewers can use existing read grants, but a foreign repository is still refused.
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


def test_reviewer_turn_can_write_only_its_own_repo_and_reads_a_product_write_grant(api, gh, tmp_path, monkeypatch):
    configured(api, gh, tmp_path, monkeypatch)
    minted = []
    gh.on_token = lambda body: minted.append(body)
    reply = turn_token(api, "cmo").json()
    assert any("Acme/emp-cmo" in g["repositories"] and g["access"] == "write" for g in reply["tokens"])
    assert any(REPO in g["repositories"] and g["access"] == "read" for g in reply["tokens"])
    assert all(REPO not in g["repositories"] for g in reply["tokens"] if g["access"] == "write")
    assert all("product" not in b.get("repositories", []) for b in minted if b.get("permissions", {}).get("contents") == "write")
    narrowed = api.post("/api/v2/github/token", json={"bot": "cmo", "repository": REPO}, headers=auth("bot-test"))
    assert narrowed.status_code == 200, narrowed.text
    assert all(g["access"] == "read" for g in narrowed.json()["tokens"])
    assert submit(api).status_code == 200  # Only the server-side path mints product review write.
    changed = api.put("/api/v2/bots/cmo/repositories", json={"mode": "chosen", "chosen": [
        {"full_name": REPO, "access": "read"}]}, headers=auth())
    assert changed.status_code == 200
    assert submit(api, key="read-grant").status_code == 200
    api.app_state.store.settings.github_reviewer_bots = ()
    assert submit(api, key="primary-read-only").status_code == 403


def test_review_mismatch_stays_fenced_and_definite_refusal_can_retry(api, gh, tmp_path, monkeypatch):
    settings, seen = configured(api, gh, tmp_path, monkeypatch)
    for field, value in (("receipt_head", "b" * 40), ("receipt_state", "COMMENTED")):
        seen[field] = value
        assert submit(api, key=field).status_code == 502
        assert submit(api, key=field).status_code == 409
        seen.pop(field)
    seen["refusal"] = 422
    assert submit(api, key="refused").status_code == 409
    seen.pop("refusal")
    assert submit(api, key="refused").status_code == 200


def test_reviewer_failure_does_not_mutate_primary_health_or_reachability(api, gh, tmp_path, monkeypatch):
    configured(api, gh, tmp_path, monkeypatch)
    from backend import repositories as R
    def unexpected(*args, **kw):
        raise AssertionError("reviewer failure must not write primary health")
    monkeypatch.setattr(G, "note_github_token", unexpected)
    monkeypatch.setattr(R, "repository_health", unexpected)
    monkeypatch.setattr(R, "workflow_health", unexpected)
    gh.refuse = (403, "synthetic installation refusal")
    assert turn_token(api, "cmo").status_code == 409


def test_reviewer_ignores_known_missing_repo_without_changing_primary_cache(api, gh, tmp_path, monkeypatch):
    configured(api, gh, tmp_path, monkeypatch)
    reviewer = api.app_state.github_reviewer_app
    token, _, repos = reviewer.mint_reachable([REPO, "Acme/gone"], G.READ_PERMISSIONS, missing={"acme/gone"})
    assert token.startswith("reviewer-") and repos == [REPO]


def test_review_missing_from_reviewer_installation_refuses_before_claim_or_post(api, gh, tmp_path, monkeypatch):
    _, seen = configured(api, gh, tmp_path, monkeypatch)
    def unexpected(*args, **kw):
        raise AssertionError("reviewer refusal must not diagnose through primary Administration")
    monkeypatch.setattr(G.GitHubApp, "can_create_repos", unexpected)
    gh.missing.add("product")  # Scoped mint refuses; a wide token still succeeds for diagnosis.
    reply = submit(api, key="not-installed")
    assert reply.status_code == 409, reply.text
    assert reply.json()["error"]["code"] == "github_repo_not_accessible"
    assert seen["reviews"] == []
    with api.app_state.store.read_transaction() as c:
        assert c.execute("SELECT count(*) FROM github_review_operations").fetchone()[0] == 0


def test_github_refusing_a_review_403_files_one_devops_task_not_the_type_manager(api, gh, tmp_path, monkeypatch):
    from backend import hubdb as H
    settings, seen = configured(api, gh, tmp_path, monkeypatch)
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, runtime, state) VALUES('devops-engineer','devops-engineer','DevOps','fake','active')")
    seen["refusal"] = 403
    for key in ("first", "again"):
        assert submit(api, key=key).status_code == 409
    with api.app_state.store.read() as c:
        filed = [dict(r) for r in c.execute("SELECT owner_id, title FROM tasks WHERE requester_id=? AND title LIKE 'GitHub refused%'",
                                             (H.KEEPER,))]
    assert filed == [{"owner_id": "bot:devops-engineer", "title": f"GitHub refused cmo's review on {REPO} (403)"}]


def test_github_rate_limiting_a_review_403_files_no_task(api, gh, tmp_path, monkeypatch):
    from backend import hubdb as H
    settings, seen = configured(api, gh, tmp_path, monkeypatch)
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO bots(id, slug, display_name, runtime, state) VALUES('devops-engineer','devops-engineer','DevOps','fake','active')")
    seen["refusal"] = 403
    seen["refusal_message"] = "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."
    assert submit(api, key="throttled").status_code == 409
    seen["refusal_message"] = "Resource not accessible by integration"      # the permission twin still files
    assert submit(api, key="denied").status_code == 409
    with api.app_state.store.read() as c:
        filed = [r[0] for r in c.execute("SELECT title FROM tasks WHERE requester_id=? AND title LIKE 'GitHub refused%'",
                                         (H.KEEPER,))]
    assert filed == [f"GitHub refused cmo's review on {REPO} (403)"]


def test_github_refusing_a_review_403_goes_to_the_tickets_type_manager_without_devops(api, gh, tmp_path, monkeypatch):
    from backend import hubdb as H
    settings, seen = configured(api, gh, tmp_path, monkeypatch)
    with api.app_state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Dev tickets", [{"name": "In Progress", "status": "doing"}])
        c.execute("UPDATE task_types SET manager='cpo' WHERE id=?", (typ["id"],))
        ticket = H.task_create(c, H.KEEPER, "Build the thing", "x", "bot:cfo", type=typ["id"], lint=False)
        c.execute("INSERT INTO task_links(id, task_id, kind, url, created_at) VALUES('l1', ?, 'pr', ?, ?)",
                  (ticket["id"], f"https://github.com/{REPO}/pull/7", H.now()))
    seen["refusal"] = 403
    assert submit(api, key="only").status_code == 409
    with api.app_state.store.read() as c:
        owners = [r[0] for r in c.execute("SELECT owner_id FROM tasks WHERE requester_id=? AND title LIKE 'GitHub refused%'",
                                          (H.KEEPER,))]
    assert owners == ["bot:cpo"]
