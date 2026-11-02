"""A Mercury trigger is Finance's (spec §3.4): the ordinary trigger routes refuse anyone but a finance admin, never show
its bodies to anyone, and only a finance admin makes one; its bodies are read in Finance by a finance admin who sees
sources, and moving it off Mercury takes its bank payloads with it."""
import json
import time

from backend.finance import actions as FA
from backend.finance.core import feeds as F
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_triggers import hook
from backend.tests.test_finance_fixtures import AT, MERCURY, OWNER, act

SECRET = "whsec_acme_0123456789abcdef"


def _get(api, path, token="ana-test"):
    return api.get("/api/v2/" + path, headers=headers(token))


def _post(api, path, body, token="ana-test"):
    return api.post("/api/v2/" + path, json=body, headers=headers(token))


def _mercury(api):
    """Finance set up for Ana, a Mercury trigger she made, and one signed call (evt-0002) on it."""
    FA.banking_stop()
    with api.app.state.store.transaction() as c:
        FA.setup(c, OWNER, AT)
    made = post(api, "triggers", {"name": "Mercury"})["trigger"]
    post(api, "triggers/mercury/signing", {"preset": "mercury", "secret": SECRET})
    body = json.dumps(MERCURY["event_txn_updated"]).encode()
    assert hook(api, made["url"], body, {"Mercury-Signature": F.sign(body, SECRET, int(time.time()))}).status_code == 202


def test_only_a_finance_admin_manages_a_mercury_trigger_and_no_trigger_route_shows_its_bodies(api):
    _mercury(api)
    # Ben is an admin but not a finance admin: he cannot make a Mercury trigger of his own either.
    post(api, "triggers", {"name": "Acme bank"}, token="ben-test")
    assert _post(api, "triggers/acme-bank/signing", {"preset": "mercury", "secret": SECRET}, "ben-test").status_code == 403
    assert _post(api, "triggers/acme-bank", {"preset": "mercury"}, "ben-test").status_code == 403
    refused = [_get(api, "triggers/mercury/deliveries", "ben-test"),
               _get(api, "triggers/mercury/deliveries/evt-0002", "ben-test"),
               _post(api, "triggers/mercury/deliveries/evt-0002/replay", {}, "ben-test"),
               _post(api, "triggers/mercury/recover", {}, "ben-test"),
               _post(api, "triggers/mercury/rotate", {}, "ben-test"),
               _post(api, "triggers/mercury/signing", {"secret": SECRET}, "ben-test"),
               _post(api, "triggers/mercury", {"name": "Not Finance"}, "ben-test"),
               _post(api, "triggers/mercury/delete", {}, "ben-test"),
               _get(api, "finance/webhook-deliveries", "ben-test")]
    assert [r.status_code for r in refused] == [403] * len(refused), [r.text for r in refused]
    assert "Finance" in refused[0].text
    # A finance trigger is listed only to finance admins (consumer "finance"); its bodies are never listed.
    listed = _get(api, "triggers", "ben-test").json()["triggers"]
    assert [t for t in listed if t["id"] == "mercury"] == [] and "evt-0002" not in json.dumps(listed)
    owners = _get(api, "triggers").json()["triggers"]
    assert [t["preset"] for t in owners if t["id"] == "mercury"] == ["mercury"]
    # The owner (a finance admin who sees everything) manages it, but reads its bodies only in Finance.
    assert _get(api, "triggers/mercury/deliveries/evt-0002").status_code == 403
    assert [d["delivery"] for d in _get(api, "triggers/mercury/deliveries").json()["deliveries"]] == ["evt-0002"]
    assert _post(api, "triggers/mercury/deliveries/evt-0002/replay", {}).status_code == 200
    found = _get(api, "finance/webhook-deliveries").json()["deliveries"]
    assert [(d["trigger"], d["delivery"], d["outcome"]) for d in found] == [("mercury", "evt-0002", "accepted")]
    assert json.loads(found[0]["body"])["id"] == "evt-0002" and found[0]["received_at"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM fin_inbox").fetchone()[0] == 1          # the replay was kept once

    # finance.admin lets Ben manage it; reading bodies also needs the sources view, which he lacks.
    with api.app.state.store.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "human:ben", "right": "finance.admin"}).status == "committed"
    assert _post(api, "triggers/mercury", {"name": "Mercury (Acme)"}, "ben-test").status_code == 200
    assert [t["preset"] for t in _get(api, "triggers", "ben-test").json()["triggers"] if t["id"] == "mercury"] == [
        "mercury"]
    assert _get(api, "finance/webhook-deliveries", "ben-test").status_code == 403
    assert _get(api, "triggers/mercury/deliveries/evt-0002", "ben-test").status_code == 403

    # Moved off Mercury, the trigger is ordinary again and keeps no bank payload behind.
    assert _post(api, "triggers/mercury/signing", {"off": True}, "ben-test").status_code == 200
    shown = _get(api, "triggers/mercury/deliveries/evt-0002", "ben-test").json()["delivery"]
    assert shown["expired"] and "body" not in shown
    assert _get(api, "finance/webhook-deliveries").json()["deliveries"] == []


def test_a_trigger_is_finance_s_by_its_consumer_not_its_preset(api):
    """`consumer` says who gets a trigger's calls (spec §3.4): a Mercury trigger is `finance`, any other is `automations`,
    and taking a trigger off Mercury gives it back to automations."""
    _mercury(api)
    post(api, "triggers", {"name": "Plain"})
    consumers = {t["id"]: t["consumer"] for t in _get(api, "triggers").json()["triggers"]}
    assert consumers == {"mercury": "finance", "plain": "automations"}
    assert _post(api, "triggers/mercury", {"preset": "none"}).status_code == 200
    assert {t["id"]: t["consumer"] for t in _get(api, "triggers").json()["triggers"]}["mercury"] == "automations"


# ------------------------------------------------------------------------------------------------ Intuit and Xero calls
# Tico HQ holds Intuit's and Xero's signing keys and checks their calls (spec §3.4). The install takes one only over its
# own Gateway relay (via="gateway"), and its public address refuses the same preset, whatever the headers say.
BOOKS_KEY = "intuit-verifier-token-0123456789"
BOOKS_ADDRESS = "books-addr-secret-0123456789"


def _books_trigger(api):
    """A QuickBooks books connection and the Finance trigger for it: an `intuit` trigger with an address secret and a
    signing secret the install may hold, which must not make a call count here."""
    from backend.finance.store import mirror as SX
    from backend.triggers import digest
    store = api.app.state.store
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
        SX.books_connection_insert(c, cid="conn-qb", provider="quickbooks", name="Acme books", by=OWNER, at=AT)
        SX.set_books_settings(c, "conn-qb", {"provider": "quickbooks", "company_id": "co_acme", "realm_id": "123"})
        sealed = api.app.state.vault.cipher.seal(c, "triggers:books-qb", BOOKS_KEY)
        c.execute("INSERT INTO triggers(id,name,kind,secret_hash,created_by,created_at,updated_at,signing_secret,preset,"
                  "consumer) VALUES('books-qb','QuickBooks','webhook',?,?,?,?,?,'intuit','finance')",
                  (digest(BOOKS_ADDRESS), OWNER, AT, AT, sealed))


def _envelope(external_id="evt-1"):
    return json.dumps({"type": "tico.finance.webhook", "provider": "quickbooks", "company_id": "co_acme",
                       "external_id": external_id, "payload": {"eventNotifications": [{"realmId": "123"}]}}).encode()


def _intuit_signed(body, key=BOOKS_KEY):
    import base64
    import hashlib
    import hmac
    return base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()


def _kept(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM fin_inbox").fetchone()[0]


def test_a_quickbooks_call_relayed_over_the_gateway_is_kept_without_a_signature_here(api):
    from backend import triggers
    _books_trigger(api)
    store = api.app.state.store
    with store.transaction() as c:
        # vault=None: the relayed call needs no signing secret here, so none is read.
        got = triggers.receive(c, "books-qb", _envelope(), {"content-type": "application/json"}, secret=None,
                               via="gateway", delivery_id="relay-1", vault=None)
    assert got.outcome == "accepted"
    assert _kept(api) == 1


def test_the_public_address_refuses_intuit_even_with_a_signature_the_install_could_check(api):
    _books_trigger(api)
    body = _envelope()
    url = "/api/v2/hooks/books-qb/" + BOOKS_ADDRESS
    headers = {"content-type": "application/json", "intuit-signature": _intuit_signed(body)}
    refused = api.post(url, content=body, headers=headers)
    assert refused.status_code == 401 and "Tico Gateway" in refused.text
    unsigned = api.post(url, content=body, headers={"content-type": "application/json"})
    assert unsigned.status_code == 401
    # A header that says the call came over the relay is only a header: `via` is never read from the request.
    spoofed = api.post(url, content=body, headers={**headers, "via": "gateway", "x-tico-via": "gateway"})
    assert spoofed.status_code == 401
    assert _kept(api) == 0
