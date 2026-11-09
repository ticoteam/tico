"""Helpers that other tests share, moved out of test_letters.py so they import no test file."""

import hashlib
import pytest
from backend import hubdb as H, letters as L
from backend.tests.helpers.api import get, post, put
from clients import gateway as G


ADDRESS = {"name": "Acme", "line1": "1 Main St", "city": "Dover", "state": "DE", "postal_code": "19901"}


RECIPIENT = {**ADDRESS, "name": "Recipient", "line1": "2 Main St"}


class HQ:
    def __init__(self):
        self.total = 1500000
        self.proof = L.render("Letter proof\n\nHello from Acme.")
        self.quotes, self.jobs, self.calls = {}, {}, []
        self.unconfigured = self.timeout = self.changed = self.no_cancel = False
        self.provider_submits = 0
        self.refusal = None
        self.timeout_before_claim = False
        self.resolve_supported = True
        self.abandoned = set()

    def __call__(self, key, base, path="", *, body=None, idempotency_key=None, pdf=False):
        assert key == "tgw_letters_test" and base.endswith("/v1/gateway")
        self.calls.append((path, idempotency_key))
        if self.unconfigured:
            raise G.LettersError(503, "unconfigured")
        if path == "/quotes":
            if self.changed and self.quotes:
                self.total = 1800000
            qid = "lq_" + str(len(self.quotes) + 1)
            digest = hashlib.sha256(self.proof).hexdigest()
            approval_hash = hashlib.sha256((L.dump(body) + digest + str(self.total)).encode()).hexdigest()
            self.quotes[qid] = {
                "quote_id": qid,
                "proof_sha256": digest,
                "approval_hash": approval_hash,
                "page_count": 1,
                "total_micros": self.total,
                "currency": "USD",
                "expires_at": L.later(900),
            }
            return self.quotes[qid]
        if path.endswith("/proof"):
            return self.proof
        if path.startswith("/keys/") and path.endswith("/resolve"):
            from urllib.parse import unquote

            if not self.resolve_supported:
                raise G.LettersError(404, "not_found")
            send_key = unquote(path[len("/keys/") : -len("/resolve")])
            if send_key in self.jobs:
                return {"letter": dict(self.jobs[send_key]), "abandoned": False}
            self.abandoned.add(send_key)
            return {"letter": None, "abandoned": True}
        if body is not None and not path:
            if idempotency_key in self.abandoned:
                raise G.LettersError(409, "idempotency_key_abandoned")
            if self.refusal:
                raise G.LettersError(429, self.refusal)
            if self.timeout_before_claim:
                self.timeout_before_claim = False
                raise G.LettersError(502, "gateway_unavailable")
            if idempotency_key not in self.jobs:
                self.provider_submits += 1
                self.jobs[idempotency_key] = {
                    "letter_id": "lt_" + str(len(self.jobs) + 1),
                    "status": "submitted",
                    "tracking": [],
                    "cancel_possible": False,
                    "error": None,
                    "accepted_at": H.now(),
                    "billing": "billed",
                    "charged_micros": self.total,
                }
            else:
                assert self.jobs[idempotency_key]["quote_id"] == body["quote_id"]
            self.jobs[idempotency_key]["quote_id"] = body["quote_id"]
            if self.timeout:
                self.timeout = False
                raise G.LettersError(502, "gateway_unavailable")
            return self.jobs[idempotency_key]
        if path.endswith("/cancel") and self.no_cancel:
            raise G.LettersError(409, "cannot_cancel")
        job = next((row for row in self.jobs.values() if row["letter_id"] == path.split("/")[1]), {})
        result = {
            **job,
            "letter_id": path.split("/")[1],
            "status": "cancelled" if path.endswith("/cancel") else "mailed",
            "tracking": [{"barcode": "1234", "status": "mailed"}],
            "cancel_possible": False,
        }
        if path.endswith("/cancel"):
            result.update(billing="void", charged_micros=0)
        return result


@pytest.fixture
def hq(api, monkeypatch):
    fake = HQ()
    monkeypatch.setenv("TICO_GATEWAY_KEY", "tgw_letters_test")
    monkeypatch.setattr(G, "letters", fake)
    api.app.state.letters.engine = lambda *args: {
        "answers": {"recipient_kind": {"type": "choice", "choice": "other", "confidence": 1.0}}
    }
    put(api, "letters/settings", {"return_address": ADDRESS})
    return fake


def quote(api, token="ana-test", **fields):
    return post(api, "letters/quotes", {"text": "Hello from Acme.", "recipient": RECIPIENT, **fields}, token=token)


def send(api, row, token="ana-test"):
    return post(api, "letters/" + row["id"] + "/send", {}, token=token)


def approve(api, row):
    return post(api, "approvals/" + row["approval_id"], {"decision": "approved"})


def fetch(api, row, token="ana-test"):
    return get(api, "letters/" + row["id"], token=token)


def grant(api, ident):
    return put(api, "letters/outboxes/company/grants/bot/" + ident, {"level": "send"})
