"""Finance's ad spend (backend/finance/ad_import.py, store/ads.py): the import reads Google Ads' and Meta's daily spend
per campaign through fake platforms (never the network), a restated day overwrites, a refused login asks to reconnect,
and the default key picker takes one whole set of keys.

Slugs and names echo TIDY's (paid-rental-ads, consumer-google-ad-owner) for realism only; nothing depends on them."""
import io
import json
import sqlite3
import urllib.error
import urllib.parse
from collections.abc import Callable
from typing import Any

import pytest

from backend.credentials import Vault
from backend.finance import ad_import as AI
from backend.finance.store import ads as SA
from backend.store import Store
from backend.tests.test_finance_fixtures import AT, OWNER, books, finance_template, store  # noqa: F401  (fixtures)

Handler = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]


class _Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status, self._raw = status, json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeNet:
    """Answers by (method, path prefix); remembers every call. A handler returns (status, JSON body)."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Handler]] = []
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def on(self, method: str, prefix: str, handler: Handler) -> None:
        self.routes.insert(0, (method, prefix, handler))

    def __call__(self, request: Any, *, timeout: float) -> _Response:
        method, url = request.get_method(), request.full_url
        headers = {k.lower(): v for k, v in request.header_items()}
        self.calls.append((method, url, headers, request.data))
        for m, prefix, handler in self.routes:
            if m == method and url.startswith(prefix):
                status, body = handler(method, url, headers, request.data)
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, "x", {}, io.BytesIO(json.dumps(body).encode()))  # type: ignore[arg-type]
                return _Response(status, body)
        raise urllib.error.HTTPError(url, 404, "x", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]

    def urls(self, part: str) -> list[str]:
        return [u for _, u, _, _ in self.calls if part in u]


def http(net: FakeNet, slept: list[float] | None = None) -> AI.Http:
    return AI.Http(opener=net, sleep=(slept.append if slept is not None else (lambda _: None)))


def key(store: Store, cid: str, name: str, env: str, value: str, *, kind: str = "api_key", tool_id: str | None = None,
        at: str = "2026-10-01T00:00:00Z") -> str:
    vault = Vault(store)
    with store.transaction() as c:
        cipher, nonce = vault.cipher.encrypt(c, cid, value)
        c.execute("INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,"
                  "updated_by,created_by,tool_id) VALUES(?,?,?,?,'',?,?,'',?,?,?,?,?)",
                  (cid, name, kind, env, cipher, nonce, at, at, OWNER, OWNER, tool_id))
    return cid


def tool(store: Store, tid: str, service: str) -> str:
    with store.transaction() as c:
        c.execute("INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
                  "updated_at) VALUES(?,?,?,'','bearer','','computer',?,?,?)", (tid, tid, service, OWNER, AT, AT))
    return tid


def setup_import(store: Store, tool_id: str, platform: str, credentials: dict[str, str], *,
                 history_from: str = "2026-10-01") -> str:
    with store.transaction() as c:
        SA.save_import(c, SA.AdImport(id="", tool_id=tool_id, platform=platform, enabled=True,
                                      history_from=history_from, credentials=credentials, created_by=OWNER), AT)
        return str(c.execute("SELECT id FROM fin_ad_imports WHERE tool_id=?", (tool_id,)).fetchone()[0])


def spends(store: Store) -> dict[tuple[str, str, str], int]:
    with store.read() as c:
        return {(r[0], r[1], r[2]): int(r[3]) for r in c.execute(
            "SELECT a.external_id, s.day, s.campaign_external_id, s.amount_cents FROM fin_ad_spends s "
            "JOIN fin_ad_accounts a ON a.id=s.ad_account_id")}


def imp(store: Store, tool_id: str) -> SA.AdImport:
    with store.read() as c:
        found = SA.import_for(c, tool_id)
    assert found is not None
    return found


# ------------------------------------------------------------------------------------------------ Meta
def meta(store: Store) -> tuple[str, FakeNet, dict[str, list[dict[str, Any]]]]:
    tool(store, "meta-ads", "meta-ads")
    key(store, "k-meta", "Meta · META_ACCESS_TOKEN", "META_ACCESS_TOKEN", "meta-secret-token", tool_id="meta-ads")
    iid = setup_import(store, "meta-ads", "meta_ads", {"access_token": "k-meta"})
    net = FakeNet()
    insights: dict[str, list[dict[str, Any]]] = {"111": [], "222": []}

    def accounts(_m: str, url: str, headers: dict[str, str], _b: bytes | None) -> tuple[int, Any]:
        assert headers["authorization"] == "Bearer meta-secret-token" and "access_token" not in url
        if "after=p2" in url:
            return 200, {"data": [{"account_id": "222", "name": "Rental social", "currency": "USD"}]}
        return 200, {"data": [{"account_id": "111", "name": "Paid rental", "currency": "USD"}],
                     "paging": {"next": AI.GRAPH + "/me/adaccounts?after=p2"}}

    def report(_m: str, url: str, _h: dict[str, str], _b: bytes | None) -> tuple[int, Any]:
        account = url.split("/act_")[1].split("/")[0]
        rows = insights[account]
        if "after=r2" in url:
            return 200, {"data": rows[1:]}
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert query["level"] == ["campaign"] and query["time_increment"] == ["1"]
        more = {"paging": {"next": f"{AI.GRAPH}/act_{account}/insights?after=r2"}} if len(rows) > 1 else {}
        return 200, {"data": rows[:1], **more}

    net.on("GET", AI.GRAPH + "/me/adaccounts", accounts)
    net.on("GET", AI.GRAPH + "/act_", report)
    return iid, net, insights


def test_meta_import_follows_pages_and_stores_cents_per_day_and_campaign(books: Store):
    iid, net, insights = meta(books)
    insights["111"] = [{"campaign_id": "c1", "campaign_name": "Spring", "spend": "12.345", "date_start": "2026-10-02"},
                       {"campaign_id": "c2", "campaign_name": "Fall", "spend": "100", "date_start": "2026-10-02"}]
    insights["222"] = [{"campaign_id": "c9", "campaign_name": "Social", "spend": "7.5", "date_start": "2026-10-14"}]
    done = AI.run(books, Vault(books), iid, now=AT, http_=http(net))
    assert done.status == "ok" and done.accounts == 2
    assert spends(books) == {("111", "2026-10-02", "c1"): 1235, ("111", "2026-10-02", "c2"): 10000,
                             ("222", "2026-10-14", "c9"): 750}
    after = imp(books, "meta-ads")
    assert after.status == "ok" and after.last_import_at == AT and after.last_error == ""
    with books.read() as c:
        accounts = {a.external_id: a for a in SA.accounts(c, "meta-ads")}
        assert accounts["111"].name == "Paid rental" and accounts["111"].bot_slug == ""
        # The platform's vendor is there for the books to find a card charge by.
        assert c.execute("SELECT 1 FROM fin_contacts WHERE name='Meta' AND kind='vendor'").fetchone()
    # The first run reads from history_from; the token never lands in the database.
    assert "2026-10-01" in urllib.parse.unquote(net.urls("act_111")[0])
    with books.read() as c:
        dump = "\n".join(str(tuple(r)) for t in ("fin_ad_imports", "fin_ad_accounts", "fin_ad_spends")
                         for r in c.execute(f"SELECT * FROM {t}"))
    assert "meta-secret-token" not in dump


def test_a_restated_day_overwrites_and_a_gone_campaign_goes(books: Store):
    iid, net, insights = meta(books)
    insights["111"] = [{"campaign_id": "c1", "spend": "10", "date_start": "2026-10-10"},
                       {"campaign_id": "c2", "spend": "5", "date_start": "2026-10-10"}]
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    insights["111"] = [{"campaign_id": "c1", "spend": "12.40", "date_start": "2026-10-10"}]
    later = "2026-10-15T17:00:00.000000Z"
    assert AI.run(books, Vault(books), iid, now=later, http_=http(net)).status == "ok"
    assert spends(books) == {("111", "2026-10-10", "c1"): 1240}


def test_later_runs_read_the_last_30_days_only():
    first = SA.AdImport(id="i", tool_id="t", platform="meta_ads", enabled=True, history_from="2026-01-01")
    assert AI.window(first, "2026-10-15T12:00:00Z") == ("2026-01-01", "2026-10-15")
    again = SA.AdImport(id="i", tool_id="t", platform="meta_ads", enabled=True, history_from="2026-01-01",
                        last_import_at="2026-10-15T08:00:00Z")
    assert AI.window(again, "2026-10-15T12:00:00Z") == ("2026-09-16", "2026-10-15")
    # Moving history_from earlier reads it all again.
    assert AI.settings_changed(again, "2025-12-01").last_import_at is None
    assert AI.settings_changed(again, "2026-02-01").last_import_at == "2026-10-15T08:00:00Z"


def test_due_every_4_hours_or_when_asked():
    ok = SA.AdImport(id="i", tool_id="t", platform="meta_ads", enabled=True, history_from="2026-01-01", status="ok",
                     last_import_at="2026-10-15T08:00:00Z", updated_at="2026-10-15T08:00:00Z")
    assert not AI.due(ok, "2026-10-15T11:59:00Z") and AI.due(ok, "2026-10-15T12:00:00Z")
    assert AI.due(SA.AdImport(**{**ok.__dict__, "run_requested_at": "2026-10-15T09:00:00Z"}), "2026-10-15T09:00:00Z")
    assert not AI.due(SA.AdImport(**{**ok.__dict__, "enabled": False, "status": "never"}), "2026-10-16T00:00:00Z")
    failed = SA.AdImport(**{**ok.__dict__, "status": "reconnect", "updated_at": "2026-10-15T11:00:00Z"})
    assert not AI.due(failed, "2026-10-15T12:00:00Z")          # a refused login is not retried every sweep


def test_meta_error_190_asks_to_reconnect_and_keeps_what_was_imported(books: Store):
    iid, net, insights = meta(books)
    insights["111"] = [{"campaign_id": "c1", "spend": "10", "date_start": "2026-10-10"}]
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    net.on("GET", AI.GRAPH + "/me/adaccounts", lambda *_: (400, {"error": {
        "message": "Error validating access token: Session has expired", "type": "OAuthException", "code": 190}}))
    done = AI.run(books, Vault(books), iid, now="2026-10-15T17:00:00.000000Z", http_=http(net))
    assert done.status == "reconnect"
    after = imp(books, "meta-ads")
    assert after.status == "reconnect" and "Session has expired" in after.last_error and after.last_import_at == AT
    assert spends(books) == {("111", "2026-10-10", "c1"): 1000}


def test_meta_rate_limits_back_off_then_go_on(books: Store):
    iid, net, insights = meta(books)
    insights["111"] = [{"campaign_id": "c1", "spend": "1", "date_start": "2026-10-10"}]
    tries = {"n": 0}
    real = net.routes[0][2]

    def busy(m: str, url: str, h: dict[str, str], b: bytes | None) -> tuple[int, Any]:
        tries["n"] += 1
        if tries["n"] <= 2:
            return 400, {"error": {"message": "User request limit reached", "code": 17}}
        return real(m, url, h, b)

    net.on("GET", AI.GRAPH + "/act_", busy)
    slept: list[float] = []
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net, slept)).status == "ok"
    assert len(slept) == 2 and spends(books)[("111", "2026-10-10", "c1")] == 100


def test_a_next_page_on_another_host_is_not_followed(books: Store):
    iid, net, _ = meta(books)
    net.on("GET", AI.GRAPH + "/me/adaccounts", lambda *_: (200, {
        "data": [{"account_id": "111", "name": "A", "currency": "USD"}],
        "paging": {"next": "https://evil.example/me/adaccounts?after=x"}}))
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
    assert not net.urls("evil.example")


# ------------------------------------------------------------------------------------------------ Google Ads
GOOGLE_KEYS = {"client_id": "GOOGLE_ADS_CLIENT_ID", "client_secret": "GOOGLE_ADS_CLIENT_SECRET",
               "refresh_token": "GOOGLE_ADS_REFRESH_TOKEN", "developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN",
               "login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "customer_ids": "GOOGLE_ADS_CUSTOMER_ID"}


def google_keys(store: Store, *, label: str = "Google Ads · {env}", prefix: str = "g", at: str = "2026-10-01T00:00:00Z",
                values: dict[str, str] | None = None) -> dict[str, str]:
    values = values or {"login_customer_id": "123-456-7890", "customer_ids": "5550001111"}
    out: dict[str, str] = {}
    for slot, env in GOOGLE_KEYS.items():
        kind = "connection" if "customer" in slot else "api_key"
        out[slot] = key(store, f"{prefix}-{slot}", label.format(env=env), env, values.get(slot, f"{prefix}-{slot}-v"),
                        kind=kind, at=at)
    return out


def google(store: Store) -> tuple[str, FakeNet]:
    tool(store, "google-ads", "google-ads")
    iid = setup_import(store, "google-ads", "google_ads", google_keys(store))
    net = FakeNet()

    def token(_m: str, _u: str, _h: dict[str, str], body: bytes | None) -> tuple[int, Any]:
        form = urllib.parse.parse_qs((body or b"").decode())
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["g-refresh_token-v"]
        return 200, {"access_token": "ya29.short-lived", "expires_in": 3599}

    def clients(_m: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
        assert headers["developer-token"] == "g-developer_token-v"
        assert headers["authorization"] == "Bearer ya29.short-lived"
        if "/customers/1234567890/" in url:
            assert headers["login-customer-id"] == "1234567890"
            return 200, {"results": [
                {"customerClient": {"id": "1234567890", "descriptiveName": "Manager", "manager": True}},
                {"customerClient": {"id": "5550001111", "descriptiveName": "Consumer", "currencyCode": "USD",
                                    "manager": False}}]}
        return 403, {"error": {"status": "PERMISSION_DENIED", "message": "denied"}}

    def stream(_m: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
        assert "/customers/5550001111/googleAds:searchStream" in url and headers["login-customer-id"] == "1234567890"
        query = json.loads(body or b"{}")["query"]
        assert "segments.date BETWEEN '2026-10-01' AND '2026-10-15'" in query and "metrics.cost_micros" in query
        return 200, [{"results": [
            {"campaign": {"id": "77", "name": "Search"}, "segments": {"date": "2026-10-03"},
             "metrics": {"costMicros": "12345000"}},
            {"campaign": {"id": "77", "name": "Search"}, "segments": {"date": "2026-10-04"},
             "metrics": {"costMicros": "4995"}}]},
            {"results": [{"campaign": {"id": "78", "name": "Brand"}, "segments": {"date": "2026-10-04"},
                          "metrics": {"costMicros": "1000000"}}]}]

    net.on("POST", AI.TOKEN_URL, token)
    net.on("GET", AI.GOOGLE_ADS + "/customers:listAccessibleCustomers",
           lambda *_: (200, {"resourceNames": ["customers/1234567890", "customers/9998887777"]}))
    net.on("POST", AI.GOOGLE_ADS + "/customers/", clients)
    net.on("POST", AI.GOOGLE_ADS + "/customers/5550001111/googleAds:searchStream", stream)
    return iid, net


def test_google_import_exchanges_the_refresh_token_and_reads_clients_under_the_manager(books: Store):
    iid, net = google(books)
    done = AI.run(books, Vault(books), iid, now=AT, http_=http(net))
    assert done.status == "ok" and done.accounts == 1
    # micros / 10000 = cents, half up: 12_345_000 -> 1235; 4_995 -> 0.
    assert spends(books) == {("5550001111", "2026-10-03", "77"): 1235, ("5550001111", "2026-10-04", "77"): 0,
                             ("5550001111", "2026-10-04", "78"): 100}
    with books.read() as c:
        assert [a.name for a in SA.accounts(c, "google-ads")] == ["Consumer"]
        assert c.execute("SELECT 1 FROM fin_contacts WHERE name='Google Ads'").fetchone()
        dump = "\n".join(str(tuple(r)) for r in c.execute("SELECT * FROM fin_ad_imports"))
    assert "ya29" not in dump and "g-refresh_token-v" not in dump


def test_a_failed_token_exchange_asks_to_reconnect(books: Store):
    iid, net = google(books)
    net.on("POST", AI.TOKEN_URL, lambda *_: (400, {"error": "invalid_grant", "error_description": "Token revoked"}))
    done = AI.run(books, Vault(books), iid, now=AT, http_=http(net))
    assert done.status == "reconnect"
    after = imp(books, "google-ads")
    assert after.status == "reconnect" and "invalid_grant" in after.last_error and after.last_import_at is None
    assert not net.urls("googleads.googleapis.com")


def test_a_google_401_asks_to_reconnect(books: Store):
    iid, net = google(books)
    net.on("GET", AI.GOOGLE_ADS + "/customers:listAccessibleCustomers",
           lambda *_: (401, {"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "bad token"}}))
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "reconnect"


def test_a_missing_key_fails_with_what_is_missing(books: Store):
    tool(books, "google-ads", "google-ads")
    keys = google_keys(books)
    keys.pop("developer_token")
    iid = setup_import(books, "google-ads", "google_ads", keys)
    done = AI.run(books, Vault(books), iid, now=AT, http_=http(FakeNet()))
    assert done.status == "failed" and "Developer token" in done.error


# ------------------------------------------------------------------------------------------------ the key picker
def test_the_default_picker_takes_the_company_set_and_never_mixes(books: Store):
    company = google_keys(books, prefix="co", at="2026-09-01T00:00:00Z")
    google_keys(books, label="{env} (analytics)", prefix="an", at="2026-09-05T00:00:00Z")
    bot = google_keys(books, label="{env} (paid-rental-ads)", prefix="pr", at="2026-09-06T00:00:00Z")
    with books.read() as c:
        assert AI.default_mapping(c, "google_ads") == company
    # A tool made from a bot's copy picks that copy's set.
    tool(books, "google-ads-2", "google-ads")
    with books.transaction() as c:
        c.execute("UPDATE credentials SET tool_id='google-ads-2' WHERE id=?", (bot["client_id"],))
    with books.read() as c:
        assert AI.default_mapping(c, "google_ads", "google-ads-2") == bot


def test_the_picker_prefers_a_whole_set(books: Store):
    whole = google_keys(books, label="{env} (paid-rental-ads)", prefix="pr", at="2026-09-06T00:00:00Z")
    key(books, "lonely", "Google Ads · GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_ID", "x", at="2026-09-01T00:00:00Z")
    with books.read() as c:
        assert AI.default_mapping(c, "google_ads") == whole


@pytest.mark.parametrize("env,name", [("META_ACCESS_TOKEN", "Meta"), ("meta_ads_access_token", ""),
                                      ("", "FACEBOOK_ACCESS_TOKEN (rental-social-ad-owner)")])
def test_meta_tokens_are_found_by_any_of_their_names(books: Store, env: str, name: str):
    key(books, "m1", name or env, env, "v")
    with books.read() as c:
        assert AI.default_mapping(c, "meta_ads") == {"access_token": "m1"}


def test_the_store_answers_empty_without_its_tables(tmp_path):
    conn = sqlite3.connect(tmp_path / "bare.sqlite")
    conn.row_factory = sqlite3.Row
    assert SA.imports(conn) == [] and SA.accounts(conn) == [] and SA.live_spend(conn, "2026-10-01", "2026-10-31") == []
