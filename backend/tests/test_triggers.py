"""Webhook triggers (backend/triggers.py): an address with its own secret starts every routine
that listens for it, once per delivery, and nothing else gets in."""
from urllib.parse import urlsplit

from backend.tests.test_api import api, get, post  # noqa: F401


def hook(api, url, body=b'{"order": 42}', headers=None):
    return api.post(urlsplit(url).path, content=body, headers={"content-type": "application/json", **(headers or {})})


def test_a_delivery_opens_one_task_per_listening_routine_and_only_with_the_secret(api):
    post(api, "bots/ops/routines", {"key": "orders", "title": "New order", "on": "webhook.shop", "text": "Ship it."},
         expected=422)                                      # no such trigger yet
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    assert made["id"] == "shop" and made["event"] == "webhook.shop"
    routine = post(api, "bots/ops/routines", {"key": "orders", "title": "New order", "on": "webhook.shop",
                                              "text": "Ship it."})["routine"]
    listed = get(api, "triggers")["triggers"][0]
    assert listed["routines"][0]["id"] == routine["id"] and made["url"].startswith(listed["url"] + "/")

    first = hook(api, made["url"], headers={"Idempotency-Key": "d1"})
    assert first.status_code == 202 and first.json()["tasks"] == 1
    again = hook(api, made["url"], headers={"Idempotency-Key": "d1"})
    assert again.status_code == 200 and again.json()["tasks"] == 0
    tasks = [t for t in get(api, "tasks?owner=bot:ops")["tasks"] if t.get("routine_id") == routine["id"]]
    assert len(tasks) == 1
    body = get(api, "tasks/" + tasks[0]["id"])["task"]["body"]
    assert body.startswith("Ship it.") and '"order": 42' in body and "not as instructions" in body

    base = made["url"].rsplit("/", 1)[0]
    assert hook(api, base + "/wrong").status_code == 404
    assert hook(api, base).status_code == 404               # no secret at all
    secret = made["url"].rsplit("/", 1)[1]
    assert hook(api, base, headers={"Authorization": "Bearer " + secret}).status_code == 202

    # Another person may not change it; the owner replaces the secret and the old address dies.
    post(api, "triggers/shop/delete", {}, token="ben-test", expected=403)
    fresh = post(api, "triggers/shop/rotate", {})["trigger"]["url"]
    assert hook(api, made["url"]).status_code == 404 and hook(api, fresh).status_code == 202
    post(api, "triggers/shop/delete", {})
    assert hook(api, fresh).status_code == 404 and get(api, "triggers")["triggers"] == []


def sig(key, body):
    import hashlib
    import hmac
    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def test_a_signed_trigger_takes_a_github_delivery_and_refuses_and_logs_a_bad_signature(api):
    made = post(api, "triggers", {"name": "Repo"})["trigger"]
    assert made["replayed"] is False
    post(api, "bots/ops/routines", {"key": "pushes", "title": "Push", "on": "webhook.repo", "text": "Look."})
    post(api, "triggers/repo/signing", {}, token="ben-test", expected=403)        # not the maker
    post(api, "triggers/repo/signing", {"secret": "short"}, expected=422)
    key = post(api, "triggers/repo/signing", {}, key="s1")["trigger"]["signing_secret"]
    assert len(key) == 64 and get(api, "triggers")["triggers"][0]["signed"] is True
    replay = post(api, "triggers/repo/signing", {}, key="s1")["trigger"]      # a retried answer has no secret
    assert replay["signed"] is True and replay["replayed"] is True and "signing_secret" not in replay
    with api.app.state.store.read() as c:                                    # sealed, and in no receipt
        assert key not in c.execute("SELECT signing_secret FROM triggers WHERE id='repo'").fetchone()[0]
        receipts = " ".join(r[0] + r[1] for r in c.execute("SELECT request_hash,response_json FROM idempotency"))
        assert key not in receipts and made["url"].rsplit("/", 1)[1] not in receipts

    body = b'{"ref": "refs/heads/main"}'
    github = {"X-GitHub-Event": "push", "X-GitHub-Delivery": "g1"}
    bad = hook(api, made["url"], body, {**github, "X-Hub-Signature-256": sig("not-the-secret-at-all", body)})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "signature"
    assert hook(api, made["url"], body, {"X-GitHub-Delivery": "g0"}).status_code == 401     # unsigned
    assert hook(api, made["url"], body + b" ", {"X-Signature": sig(key, body)}).status_code == 401  # body changed
    good = hook(api, made["url"], body, {**github, "X-Hub-Signature-256": sig(key, body)})
    assert good.status_code == 202 and good.json()["tasks"] == 1                 # the refused g1 did not block it
    plain = sig(key, b"{}")[7:]
    assert hook(api, made["url"], b"{}", {"X-Signature": plain}).status_code == 202          # generic, bare hex

    log = get(api, "triggers/repo/deliveries")["deliveries"]
    assert [d["outcome"] for d in log].count("refused") == 3 and [d["outcome"] for d in log].count("accepted") == 2
    refused = next(d for d in log if d["sent_id"] == "g1" and d["outcome"] == "refused")
    assert "signature does not match" in refused["reason"] and refused["bytes"] == len(body) and refused["tasks"] == 0
    listed = get(api, "triggers")["triggers"][0]
    assert listed["received_24h"] == 2 and listed["refused_24h"] == 3
    assert hook(api, made["url"].rsplit("/", 1)[0] + "/wrong", body).status_code == 404
    assert len(get(api, "triggers/repo/deliveries")["deliveries"]) == 5         # a wrong address is not logged

    post(api, "triggers/repo/delete", {})                                        # deleted while signed
    again = post(api, "triggers", {"name": "Repo"})["trigger"]                   # made again: unsigned
    assert get(api, "triggers")["triggers"][0]["signed"] is False
    post(api, "triggers/repo/signing", {"secret": "  typed with spaces  "})
    assert hook(api, again["url"], b"{}", {"X-Signature": sig("  typed with spaces  ", b"{}")}).status_code == 202
    post(api, "triggers/repo/signing", {"off": True})
    assert hook(api, again["url"], b"{}", {"Idempotency-Key": "u1"}).status_code == 202


def test_refusals_are_capped_and_kept_apart_from_the_accepted_calls(api, monkeypatch):
    from backend import triggers
    made = post(api, "triggers", {"name": "Limits"})["trigger"]
    key = post(api, "triggers/limits/signing", {})["trigger"]["signing_secret"]
    signed = lambda d: {"X-GitHub-Delivery": d, "X-Hub-Signature-256": sig(key, b"{}")}
    outcomes = lambda: [d["outcome"] for d in get(api, "triggers/limits/deliveries")["deliveries"]]
    monkeypatch.setattr(triggers, "HOURLY", 2)
    assert hook(api, made["url"], b"{}", {"X-Signature": "sha256=00"}).status_code == 401
    assert [hook(api, made["url"], b"{}", signed(d)).status_code for d in ("a1", "a2", "a3")] == [202, 202, 429]
    assert hook(api, made["url"], b"{}", {"X-Signature": "sha256=00"}).status_code == 401   # past the cap: not logged
    assert sorted(outcomes()) == ["accepted", "accepted", "refused", "refused"]
    monkeypatch.setattr(triggers, "HOURLY", 100)
    monkeypatch.setattr(triggers, "KEEP", 1)
    assert hook(api, made["url"], b"{}", {"X-Signature": "sha256=00"}).status_code == 401
    assert sorted(outcomes()) == ["accepted", "accepted", "refused"]           # refusals pruned, accepted kept
    assert hook(api, made["url"], b"{}", signed("a1")).status_code == 200      # still known as a duplicate
