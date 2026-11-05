"""Letters through a fake HQ: the approval is the authority for one proof, and retries never print twice."""
import hashlib
import json

import pytest

from backend import hubdb as H, letters as L
from backend.tests.test_api import api, get, post, put, headers  # noqa: F401
from backend.tests.test_agents import hermes_bot, credential, bot_id
from backend.tests.test_mcp import call
from backend.tests.test_botops_parity import act
from backend.tests.test_member_bots import botops, turn  # noqa: F401 (fixture)
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
            self.quotes[qid] = {"quote_id": qid, "proof_sha256": digest, "approval_hash": approval_hash,
                "page_count": 1, "total_micros": self.total, "currency": "USD", "expires_at": L.later(900)}
            return self.quotes[qid]
        if path.endswith("/proof"):
            return self.proof
        if body is not None and not path:
            if idempotency_key not in self.jobs:
                self.provider_submits += 1
                self.jobs[idempotency_key] = {"letter_id": "lt_" + str(len(self.jobs) + 1), "status": "submitted",
                    "tracking": [], "cancel_possible": False, "error": None}
            else:
                assert self.jobs[idempotency_key]["quote_id"] == body["quote_id"]
            self.jobs[idempotency_key]["quote_id"] = body["quote_id"]
            if self.timeout:
                self.timeout = False
                raise G.LettersError(502, "gateway_unavailable")
            return self.jobs[idempotency_key]
        if path.endswith("/cancel") and self.no_cancel:
            raise G.LettersError(409, "cannot_cancel")
        return {"letter_id": path.split("/")[1], "status": "cancelled" if path.endswith("/cancel") else "mailed",
                "tracking": [{"barcode": "1234", "status": "mailed"}], "cancel_possible": False}


@pytest.fixture
def hq(api, monkeypatch):
    fake = HQ()
    monkeypatch.setenv("TICO_GATEWAY_KEY", "tgw_letters_test")
    monkeypatch.setattr(G, "letters", fake)
    api.app.state.letters.engine = lambda *args: {"answers": {
        "recipient_kind": {"type": "choice", "choice": "other", "confidence": 1.0}}}
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


def bot(api):
    hermes_bot(api)
    return bot_id(api), credential(api)["token"]


def grant(api, ident):
    return put(api, "letters/outboxes/company/grants/bot/" + ident, {"level": "send"})


def test_botops_policy_and_access_changes_wait_for_the_persons_tap(api, botops):
    ana = turn(api, botops, person="ana-test", text="Let Ops send letters and set a cap")
    path = "letters/outboxes/company/grants/bot/ops"
    card = act(api, ana, "PUT", path, {"level": "send"}).json()
    assert card["needs_confirm"] and get(api, "letters/outboxes/company/grants")["grants"] == []
    confirm = "assistant/actions/" + card["action"]["id"] + "/confirm"
    post(api, confirm, {}, token=ana["token"], expected=403)
    post(api, confirm, {})
    assert get(api, "letters/outboxes/company/grants")["grants"][0]["grantee_id"] == "ops"
    card = act(api, ana, "PUT", "letters/settings", {"monthly_cap_micros": 5000000}).json()
    assert card["needs_confirm"] and get(api, "letters/settings")["monthly_cap_micros"] is None
    post(api, "assistant/actions/" + card["action"]["id"] + "/confirm", {})
    assert get(api, "letters/settings")["monthly_cap_micros"] == 5000000
    card = act(api, ana, "DELETE", path).json()
    assert card["needs_confirm"] and get(api, "letters/outboxes/company/grants")["grants"]
    post(api, "assistant/actions/" + card["action"]["id"] + "/confirm", {})
    assert get(api, "letters/outboxes/company/grants")["grants"] == []


def test_quote_ask_approve_requotes_same_hash_and_submits_once(api, hq):
    row = quote(api)
    assert row["page_count"] == 1 and row["total_micros"] == 1500000
    row = send(api, row)
    assert row["status"] == "waiting_for_ok" and not hq.jobs
    approval = get(api, "approvals/" + row["approval_id"])
    assert approval["payload"]["proof"] == row["proof"]
    assert approval["payload"]["recipient"] == RECIPIENT | {"line2": ""}
    approve(api, row)
    after = fetch(api, row)
    assert after["status"] == "submitted" and len(hq.quotes) == 2 and hq.provider_submits == 1
    assert get(api, "approvals/" + row["approval_id"])["consumed_at"]
    assert send(api, row)["hq_letter_id"] == after["hq_letter_id"] and hq.provider_submits == 1


def test_changed_hash_asks_again_and_keeps_the_older_proof(api, hq):
    row = send(api, quote(api))
    hq.changed = True
    approve(api, row)
    after = fetch(api, row)
    assert after["status"] == "waiting_for_ok" and after["approval_id"] != row["approval_id"]
    assert after["total_micros"] == 1800000 and hq.provider_submits == 0
    assert api.get(row["proof"], headers=headers()).content == hq.proof
    approve(api, after)
    assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1


def test_bot_needs_send_access_and_grants_are_audited(api, hq):
    ident, token = bot(api)
    refused = post(api, "letters/quotes", {"text": "Hello", "recipient": RECIPIENT}, token=token, expected=403)
    assert refused["error"]["code"] == "forbidden" and not hq.calls
    grant(api, ident)
    row = quote(api, token=token)
    assert row["acting_bot_id"] == ident
    assert not call(api, "hub_letter_send", {"letter_id": row["id"]}, token=token)[0]
    approved = fetch(api, row)
    approve(api, approved)
    assert fetch(api, row)["status"] == "submitted"
    result = api.delete("/api/v2/letters/outboxes/company/grants/bot/" + ident, headers=headers())
    assert result.status_code == 200
    assert not get(api, "letters/access", token=token)["send"]
    with api.app.state.store.read() as c:
        actions = [r[0] for r in c.execute("SELECT action FROM events WHERE action LIKE 'letter.access.%'")]
    assert actions == ["letter.access.granted", "letter.access.revoked"]


def test_revoke_or_never_before_approval_prevents_submission(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    row = send(api, quote(api, token=token), token=token)
    api.delete("/api/v2/letters/outboxes/company/grants/bot/" + ident, headers=headers())
    approve(api, row)
    assert fetch(api, row)["error"] == "forbidden" and not hq.jobs
    grant(api, ident)
    put(api, "letters/settings", {"mode": "never"})
    assert post(api, "letters/" + row["id"] + "/send", {}, token=token, expected=403)["error"]["code"] == "letters_disabled"


def test_an_admin_can_approve_but_an_unrelated_person_cannot(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    row = send(api, quote(api, token=token), token=token)
    post(api, "approvals/" + row["approval_id"], {"decision": "approved"}, token="cara-test", expected=403)
    post(api, "approvals/" + row["approval_id"], {"decision": "approved"}, token="ben-test")
    assert fetch(api, row)["status"] == "submitted"


def test_company_monthly_cap_counts_accepted_spend_and_approval_cannot_bypass_it(api, hq):
    put(api, "letters/settings", {"monthly_cap_micros": 2000000})
    first = send(api, quote(api))
    second = send(api, quote(api))
    approve(api, first)
    approve(api, second)
    assert fetch(api, second)["error"] == "letters_cap" and hq.provider_submits == 1


def test_per_bot_cap_and_bot_override_cannot_loosen_company_policy(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    put(api, "letters/bots/" + ident + "/settings", {"mode": "on_their_own"}, expected=422)
    put(api, "letters/bots/" + ident + "/settings", {"monthly_cap_micros": 1000000})
    row = quote(api, token=token)
    assert post(api, "letters/" + row["id"] + "/send", {}, token=token, expected=409)["error"]["code"] == "letters_cap"
    assert not hq.jobs


def test_a_later_provider_failure_does_not_erase_accepted_spend(api, hq):
    put(api, "letters/settings", {"monthly_cap_micros": 2000000})
    first = send(api, quote(api))
    approve(api, first)
    accepted = fetch(api, first)
    api.app.state.letters.update(first["id"], {"letter_id": accepted["hq_letter_id"], "status": "failed"})
    second = quote(api)
    assert post(api, "letters/" + second["id"] + "/send", {}, expected=409)["error"]["code"] == "letters_cap"


def test_on_their_own_still_asks_for_new_address_and_certified(api, hq):
    put(api, "letters/settings", {"mode": "on_their_own"})
    first = send(api, quote(api))
    assert first["status"] == "waiting_for_ok"
    approve(api, first)
    assert send(api, quote(api))["status"] == "submitted"
    certified = send(api, quote(api, mail_class="certified"))
    assert certified["status"] == "waiting_for_ok" and hq.provider_submits == 2


def test_courts_and_unknown_triage_always_ask(api, hq):
    put(api, "letters/settings", {"mode": "on_their_own"})
    first = send(api, quote(api))
    approve(api, first)
    api.app.state.letters.engine = lambda *args: {"answers": {
        "recipient_kind": {"type": "choice", "choice": "court_agency", "confidence": 0.9}}}
    assert send(api, quote(api))["status"] == "waiting_for_ok"
    api.app.state.letters.engine = lambda *args: {"answers": {}}
    assert send(api, quote(api))["status"] == "waiting_for_ok"


def test_timeout_retry_keeps_the_claimed_quote_and_never_resubmits_provider(api, hq):
    row = send(api, quote(api))
    hq.timeout = True
    approve(api, row)
    pending = fetch(api, row)
    assert pending["status"] == "submitting" and pending["error"] == "gateway_unavailable"
    count = len(hq.quotes)
    assert send(api, row)["status"] == "submitted"
    assert hq.provider_submits == 1 and len(hq.quotes) == count
    submits = [(path, key) for path, key in hq.calls if key]
    assert len(submits) == 2 and submits[0] == submits[1]


def test_restart_recovers_the_saved_submission_claim_without_a_new_quote(api, hq):
    row = send(api, quote(api))
    hq.timeout = True
    approve(api, row)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE letters SET next_sync_at=? WHERE id=?", (H.now(), row["id"]))
    count = len(hq.quotes)
    restarted = L.Letters(api.app, api.app.state.store, api.app.state.auth, api.app.state.store.settings)
    restarted.drain()
    assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1 and len(hq.quotes) == count


def test_unstable_proof_stops_instead_of_repeatedly_requesting_approval(api, hq):
    row = send(api, quote(api))
    hq.proof = L.render("Different provider proof bytes for the same letter")
    approve(api, row)
    after = fetch(api, row)
    assert after["error"] == "unstable_proof" and after["approval_id"] == row["approval_id"] and not hq.jobs
    count = len(hq.calls)
    api.app.state.letters.drain()
    assert len(hq.calls) == count


def test_bot_quotes_an_uploaded_pdf_through_the_existing_library_path(api, hq):
    import base64
    ident, token = bot(api)
    grant(api, ident)
    failed, uploaded = call(api, "hub_file_publish", {"name": "letter.pdf", "content_base64":
        base64.b64encode(L.render("A letter uploaded by a bot")).decode(), "scope": "bot"}, token=token)
    assert not failed, uploaded
    row = post(api, "letters/quotes", {"file_id": uploaded["file"]["id"], "recipient": RECIPIENT}, token=token)
    assert row["page_count"] == 1 and row["proof"]


def test_unconfigured_is_coming_soon_and_does_not_poll_in_a_loop(api, hq):
    hq.unconfigured = True
    result = post(api, "letters/quotes", {"text": "Hello", "recipient": RECIPIENT}, expected=503)
    assert result["error"]["code"] == "unconfigured" and "Coming soon" in result["error"]["detail"]
    count = len(hq.calls)
    api.app.state.letters.drain()
    api.app.state.letters.drain()
    assert len(hq.calls) == count
    assert get(api, "letters/settings")["availability"] == "unconfigured"


def test_status_sync_patches_one_letter_and_emits_authorized_live_change(api, hq):
    from backend import events
    row = send(api, quote(api))
    approve(api, row)
    with api.app.state.store.transaction() as c:
        before = events.latest(c)
        c.execute("UPDATE letters SET next_sync_at=? WHERE id=?", (H.now(), row["id"]))
    api.app.state.letters.drain()
    after = fetch(api, row)
    assert after["status"] == "mailed" and after["tracking"] == [{"barcode": "1234", "status": "mailed"}]
    with api.app.state.store.read() as c:
        owner = api.app.state.auth.identity_for_actor(c, "human:ana")
        stranger = api.app.state.auth.identity_for_actor(c, "human:cara")
        changes = events.read(c, api.app.state.auth, owner, before, topics=("letters",))[0]
        hidden = events.read(c, api.app.state.auth, stranger, before, topics=("letters",))[0]
    assert changes[0][2]["letter"]["status"] == "mailed" and hidden == []


def test_approved_proof_is_local_and_follows_the_record_permissions(api, hq):
    row = send(api, quote(api))
    approve(api, row)
    assert api.get(row["proof"], headers=headers()).content == hq.proof
    assert api.get(row["proof"], headers=headers("cara-test")).status_code in (403, 404)
    get(api, "letters/" + row["id"], token="cara-test", expected=404)
    assert get(api, "letters", token="cara-test")["items"] == []
    with api.app.state.store.read() as c:
        source = c.execute("SELECT source_blob_id FROM letters WHERE id=?", (row["id"],)).fetchone()[0]
        assert c.execute("SELECT count(*) FROM letter_proofs WHERE letter_id=?", (row["id"],)).fetchone()[0] == 2
    assert api.get("/api/v2/files/" + source, headers=headers("cara-test")).status_code in (403, 404)


def test_decline_and_cancel_close_unsent_letters_and_provider_refusal_passes_through(api, hq):
    row = send(api, quote(api))
    post(api, "approvals/" + row["approval_id"], {"decision": "declined"})
    assert fetch(api, row)["status"] == "declined" and not hq.jobs
    pending = send(api, quote(api))
    assert post(api, "letters/" + pending["id"] + "/cancel", {})["status"] == "cancelled"
    row = send(api, quote(api))
    approve(api, row)
    hq.no_cancel = True
    error = post(api, "letters/" + row["id"] + "/cancel", {}, expected=409)
    assert error["error"]["code"] == "cannot_cancel" and fetch(api, row)["status"] == "submitted"


def test_renderer_is_deterministic_us_letter_and_does_not_execute_markup():
    from pypdf import PdfReader
    data = L.render("# Heading\n\nHello <script>world</script>.\n\n" + "word " * 2000)
    assert data == L.render("# Heading\n\nHello <script>world</script>.\n\n" + "word " * 2000)
    reader = PdfReader(__import__("io").BytesIO(data))
    assert len(reader.pages) > 1 and tuple(reader.pages[0].mediabox[2:]) == (612, 792)
    assert "<script>world</script>" in reader.pages[0].extract_text()
