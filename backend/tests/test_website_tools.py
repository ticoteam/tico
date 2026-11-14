"""Website tools (backend/website_tools.py, backend/totp.py): a tool is a site and its accounts; an account is a login
(`kind='login'`) whose password and 2FA key stay sealed. The pick rule, the people routes, the Activity log, the Tools
row, and the leak gates: a run, a computer, BotOps and the inventory never get a login's value; only a signed-in
person's reveal returns the password, and the 2FA key leaves the server in no route at all.

Ana owns the company and Ben is an admin (both credential administrators); Cara, Eve and Fay are members."""

import json
import logging
from pathlib import Path

import pytest

from backend import totp as T
from backend import website_tools as W
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.helpers.api import get, headers, post, put, setup_attempt
from backend.tests.helpers.botops_parity import act
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.member_bots import botops, finish, turn  # noqa: F401  (fixture)
from backend.tests.helpers.openapi_v2 import conforms

PASSWORD = "canary-website-password-5be1f0"
KEY = "JBSWY3DPEHPK3PXP"  # RFC-style demo key: "Hello!\xde\xad\xbe\xef"
CANARY_KEY = "KRUGS4ZANFZSAYJAMNQW4YLSPEQGWZLZEBTG64RAORSXG5DT"  # base32 of a long canary text
SITE = "bank.acme.example"


# ---- helpers


def start(api):
    """The vault (Ana and Ben administer it), and Eve and Fay as registered members with no grants."""
    setup(api)
    with api.app.state.store.transaction() as c:
        for pid in ("eve", "fay"):
            c.execute(
                "INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (pid, pid.title(), pid + "@acme.example")
            )


def call(api, method, path, body=None, token="ana-test", expected=200):
    kwargs = {"headers": headers(token)}
    if body is not None:
        kwargs["json"] = body
    response = api.request(method, "/api/v2/" + path, **kwargs)
    assert response.status_code == expected, (path, response.text)
    return response.json()


def website(api, token="ana-test", **body):
    return post(api, "website-tools", {"sites": [SITE], **body}, token)["id"]


def account(api, tid, token="ana-test", expected=200, **body):
    found = post(
        api,
        f"website-tools/{tid}/accounts",
        {"username": "ops@acme.example", "password": PASSWORD, "all_bots": False, **body},
        token,
        expected=expected,
    )
    return found.get("id") if expected == 200 else found


def share(api, tid, aid, subject, on=True, token="ana-test", expected=200):
    return put(api, f"website-tools/{tid}/accounts/{aid}/access/{subject}", {"on": on}, token, expected=expected)


def bot(api, slug):
    with api.app.state.store.read() as c:
        return "bot:" + H.resolve_bot(c, slug)["id"]


def tables(api, *names):
    with api.app.state.store.read() as c:
        return "".join(str([tuple(r) for r in c.execute(f"SELECT * FROM {n}")]) for n in names)


def code_now(key):
    return T.code(T.parse_totp(key))


# ---- 2FA codes (RFC 6238, Appendix B)

SEEDS = {
    "SHA1": b"12345678901234567890",
    "SHA256": b"12345678901234567890123456789012",
    "SHA512": b"1234567890123456789012345678901234567890123456789012345678901234",
}
VECTORS = (
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
)


def test_codes_match_the_rfc_vectors_and_a_refusal_never_echoes_the_key():
    import base64

    for at, *expected in VECTORS:
        for algorithm, want in zip(("SHA1", "SHA256", "SHA512"), expected):
            secret = base64.b32encode(SEEDS[algorithm]).decode().rstrip("=")
            uri = f"otpauth://totp/Acme:ops?secret={secret}&algorithm={algorithm}&digits=8&period=30"
            assert T.code(T.parse_totp(uri), at) == want, (algorithm, at)
    plain = T.parse_totp("jbsw y3dp-ehpk 3pxp")
    assert plain == {"secret": KEY, "digits": 6, "period": 30, "algorithm": "SHA1"}
    assert T.parse_totp("  ") is None
    assert len(T.code(plain)) == 6 and 1 <= T.expires_in(plain, 61) == 29
    secret = "SECRETSECRETSECRET"
    for bad in (
        "not base32 at all!" + secret,
        "ABCDEFG",  # under 80 bits
        f"otpauth://hotp/Acme?secret={secret}AB",
        f"otpauth://totp/Acme?secret={KEY}&algorithm=MD5",
        f"otpauth://totp/Acme?secret={KEY}&digits=9",
        f"otpauth://totp/Acme?secret={KEY}&period=0",
        f"otpauth://totp/Acme?secret={KEY}&digits=six",
        "A" * 600,
    ):
        with pytest.raises(Problem) as refused:
            T.parse_totp(bad)
        text = str(refused.value) + json.dumps(getattr(refused.value, "__dict__", {}), default=str)
        assert refused.value.status == 422 and KEY not in text and secret not in text and bad not in text


def test_sites_are_hosts_and_a_page_is_a_host_and_path():
    assert W.clean_sites(
        ["https://Bank.Acme.example/login", "bank.acme.example", "http://Intranet.acme.example:8080/x", "  "]
    ) == ["bank.acme.example", "http://intranet.acme.example:8080"]
    assert W.clean_sites(["x.example", "X.example", "https://x.example:443/", "x.example."]) == ["x.example"]
    assert W.clean_sites(["bücher.example", "10.0.0.5"]) == ["xn--bcher-kva.example", "10.0.0.5"]
    for bad in (
        ["not a host"],
        ["under_score.example"],
        ["a..example"],
        [],
        ["  "],
        ["com"],
        ["localhost"],
        ["evil.com\\.good.com"],
        ["user@good.com"],
        ["1.2.3"],
        ["ftp://good.com"],
    ):
        with pytest.raises(Problem) as refused:
            W.clean_sites(bad)
        assert refused.value.status == 422 and "under_score" not in refused.value.detail
    with pytest.raises(Problem):
        W.clean_sites([f"s{i}.example" for i in range(W.MAX_SITES + 1)])
    sites = ["bank.acme.example", "pay.example"]
    assert W.site_matches(sites, "https://bank.acme.example/login?next=/x")
    assert W.site_matches(sites, "bank.acme.example")
    assert W.site_matches(sites, "https://eu.pay.example/") and W.site_matches(sites, "https://pay.example/")
    assert not W.site_matches(sites, "https://bank.acme.example.evil.example/")
    assert not W.site_matches(sites, "https://evilbank.acme.example/")
    assert not W.site_matches(sites, "javascript://bank.acme.example/")
    assert not W.site_matches(sites, "http://bank.acme.example/")  # plain http only when the site says http://
    assert W.site_matches(["http://intranet.acme.example"], "http://intranet.acme.example/a")
    assert W.page_of("https://user:pw@bank.acme.example/statements?account=123#top") == "bank.acme.example/statements"


def test_an_address_a_browser_could_read_differently_matches_no_site():
    good = ["good.com"]
    # urlsplit reads these as good.com; Chrome goes to evil.com.
    for url in (
        "https://evil.com\\@good.com/",
        "https://evil.com\\.good.com/",
        "https://evil.com@good.com/",
        "https://user:pw@good.com/",
        "https://good.com /x",
        "https://good.com\t/x",
        "https://go\nod.com/",
        "https://good.com\x00.evil.com/",
        "https://good.com.evil.com/",
        "https://evil-good.com/",
        "https://evilgood.com/",
        "https://good.com%2f.evil.com/",
    ):
        assert not W.site_matches(good, url), url
    for url in (
        "https://GOOD.com/",
        "https://WWW.Good.Com/a",
        "https://good.com./",
        "good.com",
        "https://good.com:443/",
    ):
        assert W.site_matches(good, url), url
    # Ports: a site without one allows the default port only; one with a port allows that port only.
    assert not W.site_matches(good, "https://good.com:8443/")
    assert W.site_matches(["good.com:8443"], "https://app.good.com:8443/")
    assert not W.site_matches(["good.com:8443"], "https://good.com/")
    assert W.site_matches(["http://intra.good.com:8080"], "http://intra.good.com:8080/x")
    assert not W.site_matches(["http://intra.good.com:8080"], "http://intra.good.com/x")
    # A single word or an IP never stands for more than itself.
    assert not W.site_matches(["com"], "https://good.com/")
    assert W.site_matches(["10.0.0.5"], "https://10.0.0.5/") and not W.site_matches(["0.0.5"], "https://10.0.0.5/")
    assert not W.site_matches(["10.0.0.5"], "https://1.10.0.0.5/") and not W.site_matches(
        good, "https://0x7f.good.com.1/"
    )
    # Punycode: a Unicode address matches its site stored as punycode, and the reverse look-alike does not.
    assert W.site_matches(["xn--bcher-kva.example"], "https://bücher.example/")
    assert W.site_matches(["xn--bcher-kva.example"], "https://shop.xn--bcher-kva.example/")
    assert not W.site_matches(["bucher.example"], "https://bücher.example/")


# ---- the pick rule


def test_new_shared_accounts_cover_current_and_future_bots_without_giving_people_or_unknown_bots_access(api):
    start(api)
    tid = website(api)
    aid = post(api, f"website-tools/{tid}/accounts", {"password": PASSWORD})["id"]
    # An account grant also makes an otherwise private tool discoverable to its bots.
    call(api, "PATCH", f"website-tools/{tid}", {"audience": {"people": ["ana"]}})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,state) VALUES('future-bot','future-bot','active')")
    with api.app.state.store.read() as c:
        for actor in (bot(api, "ops"), "bot:future-bot"):
            assert W.pick(c, Identity(actor, "bot"), tid)["account_id"] == aid
        for actor in ("human:cara", "bot:unregistered"):
            with pytest.raises(Problem):
                W.pick(c, Identity(actor, "human" if actor.startswith("human:") else "bot"), tid)
        from backend import api_tools

        assert "bot:future-bot" in {r["subject"] for r in api_tools.access_rows_all(c, tid)}
    row = get(api, f"website-tools/{tid}")["accounts"][0]
    assert row["all_bots"] is True
    indexed = next(r for r in get(api, 'tool-index')['rows'] if r['id'] == 'website:' + tid)
    assert indexed['who_total'] > 0 and all(p['kind'] == 'bot' for p in indexed['who'])
    assert share(api, tid, aid, "bots", False)["on"] is False
    with api.app.state.store.read() as c:
        with pytest.raises(Problem):
            W.authorize(c, Identity("bot:future-bot", "bot"), tid, aid)


def test_all_bots_is_admin_only_revocable_and_does_not_change_own_or_selected_accounts(api):
    start(api)
    tid = website(api)
    aid = account(api, tid)
    ops = bot(api, "ops")
    share(api, tid, aid, ops)
    assert share(api, tid, aid, "bots", token="cara-test", expected=403)["error"]["code"] == "forbidden"
    share(api, tid, aid, "bots")
    assert share(api, tid, aid, ops, False, expected=409)["error"]["code"] == "all_bots"
    share(api, tid, aid, "bots", False)
    with api.app.state.store.read() as c:
        assert W.pick(c, Identity(ops, "bot"), tid)["account_id"] == aid
        with pytest.raises(Problem) as refused:
            W.pick(c, Identity(bot(api, "finance"), "bot"), tid)
        assert refused.value.code == "no_account"
    own = post(api, f"website-tools/{tid}/accounts", {"holder": ops})["id"]
    assert share(api, tid, own, "bots", expected=409)["error"]["code"] == "own_account"
    assert not next(a for a in get(api, f"website-tools/{tid}")["accounts"] if a["id"] == own)["all_bots"]
    # The broad rule is for website sessions, never an API key's runtime secret.
    from backend.tests.helpers.credentials import create

    key = create(api)["id"]
    post(api, f"credentials/{key}/grants", {"subject": "bots"}, expected=422)


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parents[2] / "contracts/website-sites.json").read_text())["cases"],
    ids=lambda case: case["name"],
)
def test_shared_site_acceptance_vectors(case):
    assert W.site_matches(case["sites"], case["url"]) is case["matches"]


def test_own_account_first_then_the_oldest_shared_one_granted_and_a_listed_tool_stays_hidden(api):
    start(api)
    tid = website(api, name="Acme Bank")
    older, newer = account(api, tid, username="first"), account(api, tid, username="second")
    ops, finance, cpo = bot(api, "ops"), bot(api, "finance"), bot(api, "cpo")
    for aid in (newer, older):
        share(api, tid, aid, ops)
    share(api, tid, newer, finance)
    share(api, tid, older, "human:cara")
    store = api.app.state.store

    def pick(actor, role, site):
        with store.read() as c:
            return W.pick(c, Identity(actor, role), site)

    assert pick(ops, "bot", SITE)["account_id"] == older  # the oldest shared one granted
    assert pick(finance, "bot", "https://bank.acme.example/login")["account_id"] == newer
    assert pick("human:cara", "human", "acme bank")["account_id"] == older  # a tool by its name, any case
    own = account(api, tid, username="ops-own", holder=ops)
    picked = pick(ops, "bot", tid)
    assert picked["account_id"] == own and picked["holder"] == ops and picked["sites"] == [SITE]
    for actor, site, code, status in (
        (cpo, SITE, "no_account", 403),
        (ops, "other.example", "no_such_site", 404),
    ):
        with pytest.raises(Problem) as refused:
            pick(actor, "bot", site)
        assert (refused.value.code, refused.value.status) == (code, status)
    with pytest.raises(Problem) as none:
        pick("human:fay", "human", SITE)
    assert none.value.code == "no_account"
    # Only listed: hidden from a member who uses none of its accounts; one who uses an account still sees it.
    call(api, "PATCH", f"website-tools/{tid}", {"audience": {"people": ["ana"]}})
    with pytest.raises(Problem) as hidden:
        pick("human:fay", "human", SITE)
    assert hidden.value.code == "no_such_site"
    get(api, f"website-tools/{tid}", "fay-test", expected=404)
    assert pick("human:cara", "human", SITE)["account_id"] == older


# ---- the routes


def test_accounts_are_added_replaced_shared_and_removed_and_each_answer_is_the_declared_one(api):
    start(api)
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def declared(operation, answer):
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(answer, schema, document) is None, (operation, conforms(answer, schema, document), answer)
        return answer

    created = declared("createWebsiteTool", post(api, "website-tools", {"sites": [SITE, "pay.acme.example"]}))
    tid = created["id"]
    post(api, "website-tools", {"sites": [SITE]}, "cara-test", expected=403)
    shared = declared(
        "addWebsiteAccount",
        post(api, f"website-tools/{tid}/accounts", {"username": "ops@acme.example", "password": PASSWORD, "totp": KEY}),
    )["id"]
    account(api, tid, "cara-test", expected=403)  # a member adds only their own
    mine = account(api, tid, "cara-test", holder="human:cara", username="cara@acme.example")
    assert account(api, tid, "cara-test", expected=409, holder="human:cara")["error"]["code"] == "own_account_exists"
    account(api, tid, "cara-test", expected=403, holder=bot(api, "ops"))  # not her bot
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot=?", (bot(api, "finance")[4:],))
    account(api, tid, "cara-test", holder=bot(api, "finance"))
    bad = account(api, tid, expected=422, totp="1" + CANARY_KEY)
    assert CANARY_KEY not in json.dumps(bad)
    declared("setWebsiteAccountAccess", share(api, tid, shared, "human:eve"))
    assert share(api, tid, mine, "human:eve", expected=409)["error"]["code"] == "own_account"
    detail = declared("getWebsiteTool", get(api, f"website-tools/{tid}"))
    assert detail["tool"] == {
        "id": tid,
        "name": SITE,
        "kind": "website",
        "sites": [SITE, "pay.acme.example"],
        "central_only": False,
    }
    by_aid = {a["id"]: a for a in detail["accounts"]}
    assert by_aid[shared]["has_totp"] and by_aid[shared]["kind"] == "shared"
    assert [p["subject"] for p in by_aid[shared]["access"]] == ["human:eve"]
    assert by_aid[mine]["holder"]["subject"] == "human:cara" and len(by_aid) == 3
    assert PASSWORD not in json.dumps(detail) and KEY not in json.dumps(detail)
    # Eve sees the account she uses, and not the others' own ones.
    assert [a["id"] for a in get(api, f"website-tools/{tid}", "eve-test")["accounts"]] == [shared]
    # Replace login: a new username, the 2FA key removed (null), the password kept.
    declared(
        "updateWebsiteAccount",
        call(
            api, "PATCH", f"website-tools/{tid}/accounts/{shared}", {"username": "billing@acme.example", "totp": None}
        ),
    )
    after = {a["id"]: a for a in get(api, f"website-tools/{tid}")["accounts"]}[shared]
    assert after["username"] == "billing@acme.example" and not after["has_totp"]
    assert post(api, f"credentials/{shared}/reveal", {})["value"] == PASSWORD
    call(api, "PATCH", f"website-tools/{tid}/accounts/{mine}", {"password": "x"}, "eve-test", expected=403)
    call(api, "PATCH", f"website-tools/{tid}/accounts/{mine}", {"password": "cara-new-pass"}, "cara-test")
    declared(
        "updateWebsiteTool", call(api, "PATCH", f"website-tools/{tid}", {"name": "Acme Bank", "central_only": True})
    )
    call(api, "PATCH", f"website-tools/{tid}", {"name": "No"}, "cara-test", expected=403)
    # The old credential routes refuse a login: it is changed and shared only on its tool.
    old = post(api, f"credentials/{shared}", {"name": "x", "secret": "y"}, expected=409)
    assert old["error"]["code"] == "website_account"
    assert (
        post(api, f"credentials/{mine}/grants", {"subject": "human:eve"}, expected=409)["error"]["code"]
        == "own_account"
    )
    declared("setWebsiteAccountAccess", share(api, tid, shared, "human:eve", on=False))
    assert get(api, f"website-tools/{tid}", "eve-test")["accounts"] == []
    call(api, "DELETE", f"website-tools/{tid}/accounts/{mine}", token="eve-test", expected=403)
    declared("deleteWebsiteAccount", call(api, "DELETE", f"website-tools/{tid}/accounts/{mine}", token="cara-test"))
    declared("deleteWebsiteTool", call(api, "DELETE", f"website-tools/{tid}"))
    get(api, f"website-tools/{tid}", expected=404)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM credentials WHERE kind='login'").fetchone()
    assert PASSWORD not in tables(api, "events", "idempotency") and KEY not in tables(api, "events", "idempotency")


def test_the_code_route_gives_a_person_who_uses_it_the_code_logs_it_without_the_code_and_slows_down(api):
    start(api)
    tid = website(api)
    aid = account(api, tid, totp=KEY)
    plain = account(api, tid, username="no-2fa")
    share(api, tid, aid, "human:cara")
    before = code_now(KEY)
    got = get(api, f"website-tools/{tid}/accounts/{aid}/code", "cara-test")
    assert (
        got["code"] in {before, code_now(KEY)} and 1 <= got["expires_in"] <= 30 and set(got) == {"code", "expires_in"}
    )
    get(api, f"website-tools/{tid}/accounts/{aid}/code", "eve-test", expected=403)
    assert get(api, f"website-tools/{tid}/accounts/{plain}/code", expected=409)["error"]["code"] == "no_2fa"
    rows = get(api, f"website-tools/{tid}/activity")["rows"]
    assert [(r["verb"], r["who"]["subject"], r["page"], r["account"]) for r in rows] == [
        ("code", "human:cara", SITE, "shared")
    ]
    with api.app.state.store.read() as c:
        logged = c.execute("SELECT method,path,detail,result FROM tool_calls WHERE tool_id=?", (tid,)).fetchall()
    assert [tuple(r) for r in logged] == [("code", SITE, "own", "use")]
    # The 20 an hour count the reveal and the code alike.
    post(api, f"credentials/{aid}/reveal", {}, "cara-test")
    for _ in range(W.SECRET_RATE - 2):
        get(api, f"website-tools/{tid}/accounts/{aid}/code", "cara-test")
    slow = get(api, f"website-tools/{tid}/accounts/{aid}/code", "cara-test", expected=429)
    assert slow["error"]["code"] == "slow_down"
    post(api, f"credentials/{aid}/reveal", {}, expected=429)
    # A run of a bot granted the account is never a person.
    r, _, attempt = setup_attempt(api, "finance")
    share(api, tid, plain, bot(api, "finance"))
    get(api, f"website-tools/{tid}/accounts/{plain}/code", attempt["token"], expected=403)
    get(api, f"website-tools/{tid}/accounts/{plain}/code", r["token"], expected=403)


def test_an_account_with_no_password_is_signed_in_by_a_person_and_can_get_a_password_later(api):
    """A site a person signs in to by hand (Google or SSO, an emailed code, a passkey): no username, no password. It is
    a normal account with grants; fill, copy and reveal of what it lacks are refused, and Replace login adds it."""
    start(api)
    tid = website(api)
    bare = post(api, f"website-tools/{tid}/accounts", {})["id"]
    blank = account(api, tid, "cara-test", holder="human:cara", username="", password="")
    share(api, tid, bare, "human:cara")
    by_aid = {a["id"]: a for a in get(api, f"website-tools/{tid}")["accounts"]}
    assert (by_aid[bare]["has_password"], by_aid[bare]["username"], by_aid[blank]["has_password"]) == (False, "", False)
    assert by_aid[bare]["access"][0]["subject"] == "human:cara"
    assert post(api, f"credentials/{bare}/reveal", {}, "cara-test", expected=409)["error"]["code"] == "no_password"
    vault = api.app.state.vault
    with api.app.state.store.transaction() as c:
        for field in ("password", "username"):
            with pytest.raises(Problem) as refused:
                W.secret(c, vault, bare, field)
            assert refused.value.code == "no_" + field and refused.value.status == 409
            assert f'tico browser needs-you {SITE} "Sign in to {SITE}"' in refused.value.detail
        assert vault.open_login(c, c.execute("SELECT * FROM credentials WHERE id=?", (bare,)).fetchone()) == {
            "password": "",
            "totp": "",
        }
    # Refusals spend nothing of the hourly limit.
    assert bare not in W._spent
    # A 2FA key alone keeps it password-less; then a password is added.
    call(api, "PATCH", f"website-tools/{tid}/accounts/{bare}", {"totp": KEY})
    assert {a["id"]: a for a in get(api, f"website-tools/{tid}")["accounts"]}[bare]["has_password"] is False
    call(api, "PATCH", f"website-tools/{tid}/accounts/{bare}", {"password": PASSWORD, "username": "ops@acme.example"})
    after = {a["id"]: a for a in get(api, f"website-tools/{tid}")["accounts"]}[bare]
    assert (after["has_password"], after["has_totp"], after["username"]) == (True, True, "ops@acme.example")
    assert post(api, f"credentials/{bare}/reveal", {}, "cara-test")["value"] == PASSWORD
    with api.app.state.store.transaction() as c:
        assert W.secret(c, vault, bare, "password") == PASSWORD
    call(api, "DELETE", f"website-tools/{tid}/accounts/{blank}", token="cara-test")


def test_activity_pages_newest_first_and_a_member_sees_only_their_own_rows(api):
    start(api)
    tid = website(api)
    aid = account(api, tid)
    for subject in ("human:cara", "human:eve"):
        share(api, tid, aid, subject)
    with api.app.state.store.transaction() as c:
        picked = W.picked_account(c, Identity("human:ana", "owner"), tid, aid)
        for i, actor in enumerate(("human:cara", "human:eve", "human:cara", "human:cara", "human:eve")):
            W.log(c, picked, Identity(actor, "human"), "fill", f"https://{SITE}/p{i}?q=secret", "central", "sent")
        with pytest.raises(ValueError):
            W.log(c, picked, Identity("human:cara", "human"), "type_password", SITE, "central")
    seen, cursor = [], ""
    while True:
        page = get(api, f"website-tools/{tid}/activity?limit=2&cursor={cursor}")
        seen += page["rows"]
        if not page["next"]:
            break
        cursor = page["next"]
    assert [r["page"] for r in seen] == [f"{SITE}/p{i}" for i in (4, 3, 2, 1, 0)]
    assert {(r["where"], r["result"], r["verb"]) for r in seen} == {("central", "sent", "fill")}
    cara = get(api, f"website-tools/{tid}/activity", "cara-test")["rows"]
    assert [r["page"] for r in cara] == [f"{SITE}/p{i}" for i in (3, 2, 0)]
    assert len(get(api, f"website-tools/{tid}")["recent"]) == W.RECENT
    get(api, f"website-tools/{tid}/activity?limit=0", expected=422)


def test_a_website_tool_is_a_tools_row_and_never_an_api_tool(api):
    start(api)
    tid = website(api, name="Acme Bank")
    aid = account(api, tid)
    share(api, tid, aid, "human:cara")
    index = get(api, "tool-index")
    row = next(r for r in index["rows"] if r["id"] == "website:" + tid)
    assert row["kind"] == "website" and row["name"] == "Acme Bank" and index["counts"]["websites"] == 1
    assert row["href"] == f"#/tools/website/{tid}" and row["detail"] == SITE
    assert "website:" + tid in {r["id"] for r in get(api, "tool-index", "eve-test")["rows"]}  # Everyone
    for method in ("GET", "DELETE"):
        call(api, method, f"api-tools/{tid}", expected=404)
    call(api, "POST", f"api-tools/{tid}/check", {}, expected=404)
    api_tool = post(api, "api-tools", {"name": "Close", "service": "close", "base_url": "https://api.close.com"})
    get(api, f"website-tools/{api_tool['id']}", expected=404)
    call(api, "PATCH", f"website-tools/{tid}", {"audience": {"people": ["ana"]}})
    assert "website:" + tid not in {r["id"] for r in get(api, "tool-index", "fay-test")["rows"]}
    assert "website:" + tid in {r["id"] for r in get(api, "tool-index", "cara-test")["rows"]}  # she uses it
    assert PASSWORD not in json.dumps(index)


# ---- the leak gates


def test_a_bot_granted_a_login_gets_nothing_from_a_run_a_computer_or_the_inventory(api):
    start(api)
    tid = website(api)
    aid = account(api, tid, totp=CANARY_KEY)
    finance = bot(api, "finance")
    share(api, tid, aid, finance)
    machine, _, attempt = setup_attempt(api, "finance")
    assert attempt["credential_vault"] is True
    assert get(api, "credential-runtime", attempt["token"])["credentials"] == []
    assert get(api, "runner-credential-grants", machine["token"])["bots"] == {finance[4:]: []}
    assert get(api, f"runner-watcher-credentials?bot={finance[4:]}", machine["token"])["credentials"] == []
    for token in ("ana-test", attempt["token"]):
        listed = api.get("/api/v2/credentials", headers=headers(token))
        assert aid not in listed.text and PASSWORD not in listed.text, token
    # The reveal: a signed-in person who uses it, the password alone; never a run or a computer.
    for token, expected in ((attempt["token"], 403), (machine["token"], 403), ("eve-test", 403)):
        refused = post(api, f"credentials/{aid}/reveal", {}, token, expected=expected)
        assert PASSWORD not in json.dumps(refused)
    shown = post(api, f"credentials/{aid}/reveal", {})
    assert shown == {"id": aid, "value": PASSWORD}
    with api.app.state.store.read() as c:
        detail = c.execute(
            "SELECT detail FROM events WHERE action='credential.revealed' AND target=?", (aid,)
        ).fetchone()
        assert json.loads(detail[0]) == {"kind": "login"}


def test_botops_acting_for_an_admin_never_gets_a_password_or_a_code(api, botops):
    start(api)
    tid = website(api)
    aid = account(api, tid, totp=KEY)
    for person in ("ana-test", "ben-test"):
        attempt = turn(api, botops, person=person, text="What is the bank's 2FA code?")
        for method, path in (
            ("GET", f"website-tools/{tid}/accounts/{aid}/code"),
            ("POST", f"credentials/{aid}/reveal"),
        ):
            refused = act(api, attempt, method, path, {} if method == "POST" else None)
            assert refused.status_code == 403 and refused.json()["error"]["code"] == "person_only", (path, refused.text)
            assert PASSWORD not in refused.text
        # BotOps reads no website tool's code even for itself.
        assert act(api, attempt, "GET", f"website-tools/{tid}/accounts/{aid}/code", ref=None).status_code == 403
        finish(api, botops, attempt)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tool_calls WHERE tool_id=?", (tid,)).fetchone()  # nothing was read


def _skipped(path):
    """Streams and long polls: the walk reads one answer and moves on."""
    return (
        path.endswith("/events")
        or "frame" in path
        or path.endswith("runner-browser-commands")
        or not path.startswith("/api/")
    )


def test_a_canary_login_is_in_no_get_answer_and_no_table_and_the_password_only_in_the_reveal(api, caplog):
    from fastapi.routing import APIRoute

    caplog.set_level(logging.DEBUG)
    start(api)
    tid = website(api)
    aid = account(api, tid, totp=CANARY_KEY)
    finance = bot(api, "finance")
    share(api, tid, aid, finance)
    share(api, tid, aid, "human:cara")
    machine, _, attempt = setup_attempt(api, "finance")
    get(api, f"website-tools/{tid}/accounts/{aid}/code", "cara-test")
    assert post(api, f"credentials/{aid}/reveal", {}, "cara-test")["value"] == PASSWORD
    values = {"tool_id": tid, "account_id": aid, "cid": aid, "credential_id": aid, "key_id": aid, "bot": finance[4:]}
    paths = sorted(
        {
            route.path
            for route in api.app.routes
            if isinstance(route, APIRoute) and "GET" in route.methods and not _skipped(route.path)
        }
    )
    assert any("website-tools" in p for p in paths) and len(paths) > 100
    secrets = (PASSWORD, CANARY_KEY, CANARY_KEY.lower())
    walked, answered = 0, set()
    for path in paths:
        url = path
        for name in [part[1:-1].split(":")[0] for part in path.split("/") if part.startswith("{")]:
            url = url.replace("{" + name + "}", values.get(name, "x")).replace("{" + name + ":path}", "x")
        for token in ("ana-test", attempt["token"], machine["token"]):
            response = api.get(url, headers=headers(token), params={"bot": finance[4:]})
            walked += 1
            if response.status_code == 200:
                answered.add((path, token))
            assert not any(s in response.text for s in secrets), (url, token, response.status_code)
    assert walked == 3 * len(paths)
    # The walk reached the routes that know the login: its tool, its Activity, the runs' and computers' own reads.
    for path, token in (
        ("/api/v2/website-tools/{tool_id}", "ana-test"),
        ("/api/v2/website-tools/{tool_id}/activity", "ana-test"),
        ("/api/v2/credentials", "ana-test"),
        ("/api/v2/tool-index", attempt["token"]),
        ("/api/v2/credential-runtime", attempt["token"]),
        ("/api/v2/runner-credential-grants", machine["token"]),
    ):
        assert (path, token) in answered, path
    dump = tables(api, "credentials", "events", "idempotency", "tool_calls", "messages")
    assert not any(s in dump for s in secrets)
    assert not any(s in caplog.text for s in secrets)


def test_one_who_only_manages_a_granted_bot_reaches_the_browser_only_while_an_ask_waits_on_them(api):
    start(api)
    tid = website(api)
    aid, other = account(api, tid), account(api, tid, username="second")
    finance = bot(api, "finance")
    for each in (aid, other):
        share(api, tid, each, finance)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot=?", (finance[4:],))
    cara = Identity("human:cara", "human")

    def view(account_id=aid):
        with store.read() as c:
            return W.picked_account(c, cara, tid, account_id)

    with pytest.raises(Problem) as hidden:
        view()
    assert hidden.value.status == 404
    task = post(api, "tasks", {"owner": "finance", "title": "Pull the statements", "body": "From the bank."})
    with store.transaction() as c:
        made = H.task_ask(
            c,
            finance,
            task["id"],
            "human:cara",
            "Please sign in to the bank for me.",
            kind="answer",
            choices=["Done"],
            shows=[f"browser:{tid}/{aid}"],
            seen_version=task.get("version"),
        )
    assert view()["account_id"] == aid
    with pytest.raises(Problem):
        view(other)  # the ask is for one account's browser
    with pytest.raises(Problem):
        with store.read() as c:
            W.picked_account(c, Identity("human:eve", "human"), tid, aid)  # it waits on Cara, not on Eve
    post(api, f"tasks/{task['id']}/asks/{made['requirement_id']}/answer", {"value": "Done"}, "cara-test")
    with pytest.raises(Problem):
        view()


def test_a_bot_run_or_a_computer_never_reads_an_accounts_username(api):
    start(api)
    tid = website(api)
    aid = account(api, tid)
    finance = bot(api, "finance")
    share(api, tid, aid, finance)
    machine, _, attempt = setup_attempt(api, "finance")
    assert [a["username"] for a in get(api, f"website-tools/{tid}")["accounts"]] == ["ops@acme.example"]
    seen = get(api, f"website-tools/{tid}", attempt["token"])
    assert [a["id"] for a in seen["accounts"]] == [aid] and "username" not in seen["accounts"][0]
    assert "ops@acme.example" not in json.dumps(seen)
    for token in (attempt["token"], machine["token"]):
        listed = api.get("/api/v2/tool-index", headers=headers(token))
        assert "ops@acme.example" not in listed.text, token
        answer = api.get(f"/api/v2/website-tools/{tid}", headers=headers(token))
        assert "ops@acme.example" not in answer.text, token
