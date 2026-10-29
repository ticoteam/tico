"""Website logins (docs/credential-vault.md "Website logins"): a username, password and 2FA key that only the granted
bot's runner reads, to type into a page; never an environment variable, a revealed value, an event or a receipt. 2FA
codes follow RFC 6238. A login card and a website card make the task wait and wake it once."""
import base64
import json

import pytest

from backend import totp as T
from backend.store import H, Problem
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401  (fixture)
from backend.tests.test_reconnects import cards, shown, task, wakes

PASSWORD = "acme-Portal pa55word!"
KEY = "JBSWY3DPEHPK3PXP"


# ------------------------------------------------------------------ RFC 6238 and setup keys
SEEDS = {"SHA1": b"12345678901234567890", "SHA256": b"12345678901234567890123456789012",
         "SHA512": b"1234567890123456789012345678901234567890123456789012345678901234"}
VECTORS = [  # RFC 6238 appendix B: time, then the SHA1, SHA256 and SHA512 codes
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize("at,sha1,sha256,sha512", VECTORS)
def test_codes_match_the_rfc_6238_vectors(at, sha1, sha256, sha512):
    for algorithm, expected in zip(("SHA1", "SHA256", "SHA512"), (sha1, sha256, sha512)):
        secret = base64.b32encode(SEEDS[algorithm]).decode().rstrip("=")
        assert T.code({"secret": secret, "digits": 8, "period": 30, "algorithm": algorithm}, at) == expected


def test_a_setup_key_or_otpauth_link_parses_and_a_bad_one_is_refused_without_echoing_it():
    assert T.parse_totp("jbsw y3dp-ehpk 3pxp") == {"secret": KEY, "digits": 6, "period": 30, "algorithm": "SHA1"}
    assert T.parse_totp("  ") is None
    link = f"otpauth://totp/Acme:ana%40acme.example?secret={KEY.lower()}&issuer=Acme&digits=8&period=60&algorithm=SHA256"
    assert T.parse_totp(link) == {"secret": KEY, "digits": 8, "period": 60, "algorithm": "SHA256"}
    assert len(T.code(T.parse_totp(link))) == 8
    for bad in (f"otpauth://hotp/Acme?secret={KEY}&counter=1", "not base32 at all!", "MZXW6",
                f"otpauth://totp/Acme?secret={KEY}&algorithm=MD5", f"otpauth://totp/Acme?secret={KEY}&digits=4",
                f"otpauth://totp/Acme?secret={KEY}&period=0", "otpauth://totp/Acme?issuer=Acme"):
        with pytest.raises(Problem) as refused:
            T.parse_totp(bad)
        assert refused.value.status == 422 and KEY not in refused.value.detail and bad not in refused.value.detail


# ------------------------------------------------------------------ the vault
def add_login(api, token="ana-test", expected=200, **fields):
    return post(api, "credentials", {"name": "Acme portal", "kind": "login", "username": "ana@acme.example",
                                     "password": PASSWORD, "totp": KEY, "sites": ["https://Portal.Acme.example:8443/login"],
                                     **fields}, token, expected=expected)


def leaks(api, *values):
    """Every stored row that holds one of `values` in plain text: none may."""
    found = []
    with api.app.state.store.read() as c:
        for table in ("credentials", "events", "idempotency", "messages", "credential_requests", "task_reconnects"):
            dump = str([tuple(r) for r in c.execute(f"SELECT * FROM {table}")])
            found += [(table, v) for v in values if v in dump]
    return found


def two_bots(api):
    machine = runner(api)
    for bot in ("ops", "finance"):
        assign(api, machine, bot)
    ready(api, machine, ["ops", "finance"])
    return machine


def test_a_login_reaches_only_its_granted_bots_runner_as_logins_never_as_a_variable_or_a_response(api):
    machine = two_bots(api)
    row = add_login(api)
    assert row["kind"] == "login" and row["env"] == "" and row["sites"] == ["portal.acme.example"] and row["has_totp"]
    add_login(api, env="ACME_PASSWORD", name="Other", expected=422)          # never a variable
    post(api, f"credentials/{row['id']}/grants", {"subject": "bot:ops"})
    post(api, "chat/finance", {"text": "Check the Acme invoices"})
    task(api)
    ops = claim(api, machine, "ops")
    runtime = get(api, "credential-runtime", ops["token"])
    assert runtime["credentials"] == []
    assert runtime["logins"] == [{"id": row["id"], "name": "Acme portal", "username": "ana@acme.example",
                                  "sites": ["portal.acme.example"], "password": PASSWORD,
                                  "totp": {"secret": KEY, "digits": 6, "period": 30, "algorithm": "SHA1"}}]
    finance = claim(api, machine, "finance")
    assert get(api, "credential-runtime", finance["token"])["logins"] == []
    get(api, "credential-runtime", "ana-test", expected=403)
    # Never revealed, to a person or to the bot that holds it; never in a listing.
    post(api, f"credentials/{row['id']}/reveal", {}, expected=409)
    post(api, f"credentials/{row['id']}/reveal", {}, ops["token"], expected=409)
    [listed] = [c for c in get(api, "credentials")["credentials"] if c["id"] == row["id"]]
    assert listed["can_reveal"] is False and listed["can_code"] is True and listed["has_totp"]
    # A change keeps the password and 2FA key it leaves out; an empty key takes 2FA off.
    renamed = post(api, f"credentials/{row['id']}", {"name": "Acme", "kind": "login", "username": "ana@acme.example",
                                                     "sites": ["portal.acme.example", "acme.example"],
                                                     "expected_revision": row["revision"]})
    assert renamed["sites"] == ["portal.acme.example", "acme.example"] and renamed["has_totp"]
    assert get(api, "credential-runtime", ops["token"])["logins"][0]["password"] == PASSWORD
    off = post(api, f"credentials/{row['id']}", {"name": "Acme", "kind": "login", "totp": "",
                                                 "expected_revision": renamed["revision"]})
    assert not off["has_totp"] and get(api, "credential-runtime", ops["token"])["logins"][0]["totp"] is None
    post(api, f"credentials/{row['id']}", {"name": "Acme", "kind": "api_key", "expected_revision": off["revision"]},
         expected=422)                                                    # a login stays a login
    assert leaks(api, PASSWORD, KEY) == []


def test_a_code_is_for_a_person_who_may_open_the_login_signed_in_and_each_view_is_recorded_without_it(api):
    machine = two_bots(api)
    row = add_login(api)
    post(api, f"credentials/{row['id']}/grants", {"subject": "bot:ops"})
    task(api)
    ops = claim(api, machine, "ops")
    shown_code = get(api, f"credentials/{row['id']}/code")
    assert shown_code["code"].isdigit() and len(shown_code["code"]) == 6 and 1 <= shown_code["expires_in"] <= 30
    get(api, f"credentials/{row['id']}/code", ops["token"], expected=403)             # the bot never reads a code
    get(api, f"credentials/{row['id']}/code", "cara-test", expected=403)              # nor anyone it is not granted to
    token = post(api, "me/tokens", {"label": "script"})["token"]
    get(api, f"credentials/{row['id']}/code", token, expected=403)                    # nor a script with a person's token
    plain = add_login(api, name="No 2FA", totp=None)
    get(api, f"credentials/{plain['id']}/code", expected=409)
    with api.app.state.store.read() as c:
        views = c.execute("SELECT actor,detail_json FROM events WHERE action='credential.code' AND target=?",
                          (row["id"],)).fetchall()
    assert [v["actor"] for v in views] == ["human:ana"] and shown_code["code"] not in str([tuple(v) for v in views])
    assert leaks(api, PASSWORD, KEY) == []


# ------------------------------------------------------------------ cards
def ops_on_a_task(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    t = task(api)
    return machine, t, claim(api, machine, "ops")


def ask(api, attempt, path, body, expected=200):
    r = api.post("/api/v2/" + path, json=body, headers=headers(attempt["token"]))
    assert r.status_code == expected, r.text
    return r.json()


def test_a_login_card_on_a_task_saves_a_login_granted_to_the_bot_and_wakes_the_task_once(api):
    machine, t, attempt = ops_on_a_task(api)
    ask(api, attempt, "credential-requests", {"kind": "login"}, expected=422)                  # sites are required
    opened = ask(api, attempt, "credential-requests", {"kind": "login", "sites": ["Portal.Acme.example"],
                                                       "username": "ops@acme.example", "label": "the Acme portal"})
    [card] = cards(api, t)
    assert card["key"] == "site:ops:portal.acme.example" and card["status"] == "open" and card["id"]
    assert shown(api, t)["task"]["status"] == "waiting"
    seen = get(api, f"credential-requests/{opened['id']}")
    assert seen["kind"] == "login" and seen["sites"] == ["portal.acme.example"] and seen["username"] == "ops@acme.example"
    post(api, f"credential-requests/{opened['id']}/save", {"username": "ops@acme.example"}, expected=422)   # no password
    post(api, f"credential-requests/{opened['id']}/save", {"password": PASSWORD, "totp": KEY}, "cara-test", expected=404)
    saved = post(api, f"credential-requests/{opened['id']}/save", {"username": "ops@acme.example", "password": PASSWORD,
                                                                  "totp": KEY})
    assert saved["status"] == "saved" and PASSWORD not in json.dumps(saved)
    assert [c["status"] for c in cards(api, t)] == ["fixed"]
    assert len(wakes(api, t)) == 1
    [login] = get(api, "credential-runtime", attempt["token"])["logins"]
    assert login["username"] == "ops@acme.example" and login["password"] == PASSWORD and login["totp"]["secret"] == KEY
    assert login["sites"] == ["portal.acme.example"]
    assert leaks(api, PASSWORD, KEY) == []


def test_a_website_card_waits_the_task_until_done_and_a_saved_login_for_the_host_closes_another(api):
    machine, t, attempt = ops_on_a_task(api)
    ask(api, attempt, "browser/needs-you", {"host": "app.acme.example"}, expected=409)          # no task named
    post(api, "browser/needs-you", {"host": "app.acme.example", "task": t["id"]}, expected=403)  # a person never asks
    opened = ask(api, attempt, "browser/needs-you", {"host": "https://App.Acme.example/login", "reason": "solve the captcha",
                                                     "task": t["id"][:8]})
    assert opened["host"] == "app.acme.example" and opened["computer"] == machine["runner_id"]
    [card] = cards(api, t)
    assert card["id"] == opened["id"] and card["service"] == "site" and card["status"] == "open"
    assert card["fix"]["computer"] == machine["runner_id"] and card["fix"]["reason"] == "solve the captcha"
    assert shown(api, t)["task"]["status"] == "waiting"
    [item] = [i for i in get(api, "needs-you")["items"] if i["id"] == t["id"]]
    assert item["need"] == "connect" and item["reconnect"]["id"] == opened["id"]
    assert "solve the captcha" in item["person_ask"]
    # Asked again while it stands: the same card.
    assert ask(api, attempt, "browser/needs-you", {"host": "app.acme.example", "task": t["id"]})["id"] == opened["id"]
    post(api, f"reconnects/{opened['id']}/done", {}, attempt["token"], expected=403)            # the bot cannot close it
    post(api, f"reconnects/{opened['id']}/done", {}, "cara-test", expected=403)                  # nor a member who reads the task
    assert post(api, f"reconnects/{opened['id']}/done", {})["status"] == "fixed"
    assert [c["status"] for c in cards(api, t)] == ["fixed"] and len(wakes(api, t)) == 1
    assert post(api, f"reconnects/{opened['id']}/done", {})["status"] == "fixed"                 # once
    assert len(wakes(api, t)) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT status FROM credential_requests WHERE id=?", (card["fix"]["request"],)).fetchone()[0] == "cancelled"

    # A second task waits on a subdomain; a login saved for the parent domain on the card closes it, and its login
    # card is the one that saves.
    second = task(api, "Download the Acme statements")
    again = ask(api, attempt, "browser/needs-you", {"host": "billing.acme.example", "task": second["id"]})
    [card] = cards(api, second)
    rid = card["fix"]["request"]
    assert get(api, f"credential-requests/{rid}")["sites"] == ["billing.acme.example"]
    post(api, f"credential-requests/{rid}/save", {"username": "ops@acme.example", "password": PASSWORD})
    assert [c["status"] for c in cards(api, second)] == ["fixed"] and len(wakes(api, second)) == 1
    post(api, f"reconnects/{again['id']}/done", {})                                            # already closed: no second wake
    assert len(wakes(api, second)) == 1
    # A login granted for a parent domain closes a waiting card on a subdomain too.
    third = task(api, "Pay the Acme invoice")
    ask(api, attempt, "browser/needs-you", {"host": "pay.acme.example", "task": third["id"]})
    other = add_login(api, name="Acme parent", sites=["acme.example"], totp=None)
    post(api, f"credentials/{other['id']}/grants", {"subject": "bot:ops"})
    assert [c["status"] for c in cards(api, third)] == ["fixed"] and len(wakes(api, third)) == 1
    with api.app.state.store.read() as c:
        assert H.task(c, third["id"])["status"] != "waiting"
    assert leaks(api, PASSWORD, KEY) == []
