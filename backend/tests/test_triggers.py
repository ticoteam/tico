"""Webhook triggers (backend/triggers.py): an address with its own secret starts every automation
that listens for it, once per delivery, and nothing else gets in."""
import json
from urllib.parse import urlsplit

from backend import hubdb as H, tico_notices
from backend.tests.test_api import api, get, post  # noqa: F401


def hook(api, url, body=b'{"order": 42}', headers=None):
    return api.post(urlsplit(url).path, content=body, headers={"content-type": "application/json", **(headers or {})})


def listener(api, trigger_id, bot="ops", text="Ship it.", name="New order"):
    """An automation on the webhook whose Bot turn hands `bot` the call, the way a routine on `webhook.<id>` did."""
    return post(api, "automations", {"name": name, "owner": "person:ana", "trigger": {"type": "webhook", "id": trigger_id},
                                     "steps": [{"id": "work", "type": "bot_turn", "bot": bot,
                                                "prompt": text + " {{webhook.content}}"}]})["id"]


def rule_row(api, params, event="webhook.received", by="human:ana", scope_kind="team", scope_id="", when=None, then="action"):
    """A row in the rules table, as the rules engine reads it (a kept row): what the rules editor wrote."""
    with api.app.state.store.transaction() as c:
        position = (c.execute("SELECT max(position) FROM rules WHERE scope_kind=? AND scope_id=? AND position<?",
                              (scope_kind, scope_id, tico_notices.RULE_POSITION)).fetchone()[0] or 0) + 1
        row = {"id": "rule-" + H.new_id(), "scope_kind": scope_kind, "scope_id": scope_id, "event": event,
               "condition": json.dumps(when or {}, sort_keys=True), "then_outcome": then,
               "params": json.dumps(params, sort_keys=True), "enabled": 1, "position": position, "note": "",
               "created_by": by, "action_by": by, "created_at": H.now(), "updated_at": H.now(), "revision": 1}
        c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return row


def turns(api, name="New order"):
    """The tasks an automation's Bot turns opened: each is titled with its automation's name."""
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE title=? ORDER BY created_at, id", (name,))]


def test_a_delivery_runs_each_listening_automation_once_and_only_with_the_secret(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    assert made["id"] == "shop" and made["event"] == "webhook.shop"
    aid = listener(api, "shop")
    listed = get(api, "triggers")["triggers"][0]
    assert listed["automations"][0]["id"] == aid and made["url"].startswith(listed["url"] + "/")

    first = hook(api, made["url"], headers={"Idempotency-Key": "d1"})
    assert first.status_code == 202
    again = hook(api, made["url"], headers={"Idempotency-Key": "d1"})
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(get(api, f"automations/{aid}/runs")["runs"]) == 1
    tasks = turns(api)
    assert len(tasks) == 1 and tasks[0]["owner"] == "bot:ops"
    body = tasks[0]["body"]
    assert body.startswith("Ship it.") and '"order": 42' in body and "<data n=" in body

    base = made["url"].rsplit("/", 1)[0]
    assert hook(api, base + "/wrong").status_code == 404
    assert hook(api, base).status_code == 404               # no secret at all
    secret = made["url"].rsplit("/", 1)[1]
    assert hook(api, base, headers={"Authorization": "Bearer " + secret, "Idempotency-Key": "d2"}).status_code == 202

    # Another person may not change it; the owner replaces the secret and the old address dies.
    post(api, "triggers/shop/delete", {}, token="cara-test", expected=403)
    fresh = post(api, "triggers/shop/rotate", {})["trigger"]["url"]
    assert hook(api, made["url"]).status_code == 404 and hook(api, fresh, headers={"Idempotency-Key": "d3"}).status_code == 202
    post(api, "triggers/shop/delete", {})
    assert hook(api, fresh).status_code == 404 and get(api, "triggers")["triggers"] == []
    assert len(get(api, f"automations/{aid}/runs")["runs"]) == 3          # d1, d2, d3


def sig(key, body):
    import hashlib
    import hmac
    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def test_a_signed_trigger_takes_a_github_delivery_and_refuses_and_logs_a_bad_signature(api):
    made = post(api, "triggers", {"name": "Repo"})["trigger"]
    assert made["replayed"] is False
    aid = listener(api, "repo", text="Look.", name="Push")
    post(api, "triggers/repo/signing", {}, token="cara-test", expected=403)        # not the maker
    post(api, "triggers/repo/signing", {"secret": "short"}, expected=422)
    key = post(api, "triggers/repo/signing", {}, key="s1")["trigger"]["signing_secret"]
    assert len(key) == 64 and get(api, "triggers")["triggers"][0]["signed"] is True
    replay = post(api, "triggers/repo/signing", {}, key="s1")["trigger"]      # a retried answer has no secret
    assert replay["signed"] is True and replay["replayed"] is True and "signing_secret" not in replay
    with api.app.state.store.read() as c:                                    # sealed, and in no receipt
        assert key not in c.execute("SELECT signing_secret FROM triggers WHERE id='repo'").fetchone()[0]
        receipts = " ".join(r[0] + r[1] for r in c.execute("SELECT request_hash,response FROM idempotency"))
        assert key not in receipts and made["url"].rsplit("/", 1)[1] not in receipts

    body = b'{"ref": "refs/heads/main"}'
    github = {"X-GitHub-Event": "push", "X-GitHub-Delivery": "g1"}
    bad = hook(api, made["url"], body, {**github, "X-Hub-Signature-256": sig("not-the-secret-at-all", body)})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "signature"
    assert hook(api, made["url"], body, {"X-GitHub-Delivery": "g0"}).status_code == 401     # unsigned
    assert hook(api, made["url"], body + b" ", {"X-Signature": sig(key, body)}).status_code == 401  # body changed
    good = hook(api, made["url"], body, {**github, "X-Hub-Signature-256": sig(key, body)})
    assert good.status_code == 202 and len(get(api, f"automations/{aid}/runs")["runs"]) == 1   # the refused g1 did not block it
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
