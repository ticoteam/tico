"""Helpers that other tests share, moved out of test_finance_ad_spend.py so they import no test file."""

import io
import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from typing import Any
from backend.credentials import Vault
from backend.finance import ad_import as AI
from backend.finance.store import ads as SA
from backend.store import Store
from backend.tests.helpers.finance_fixtures import AT, OWNER


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


def key(
    store: Store,
    cid: str,
    name: str,
    env: str,
    value: str,
    *,
    kind: str = "api_key",
    tool_id: str | None = None,
    at: str = "2026-10-01T00:00:00Z",
) -> str:
    vault = Vault(store)
    with store.transaction() as c:
        cipher, nonce = vault.cipher.encrypt(c, cid, value)
        c.execute(
            "INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,"
            "updated_by,created_by,tool_id) VALUES(?,?,?,?,'',?,?,'',?,?,?,?,?)",
            (cid, name, kind, env, cipher, nonce, at, at, OWNER, OWNER, tool_id),
        )
    return cid


def tool(store: Store, tid: str, service: str) -> str:
    with store.transaction() as c:
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
            "updated_at) VALUES(?,?,?,'','bearer','','computer',?,?,?)",
            (tid, tid, service, OWNER, AT, AT),
        )
    return tid


def setup_import(
    store: Store, tool_id: str, platform: str, credentials: dict[str, str], *, history_from: str = "2026-10-01"
) -> str:
    with store.transaction() as c:
        SA.save_import(
            c,
            SA.AdImport(
                id="",
                tool_id=tool_id,
                platform=platform,
                enabled=True,
                history_from=history_from,
                credentials=credentials,
                created_by=OWNER,
            ),
            AT,
        )
        return str(c.execute("SELECT id FROM fin_ad_imports WHERE tool_id=?", (tool_id,)).fetchone()[0])


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
        return 200, {
            "data": [{"account_id": "111", "name": "Paid rental", "currency": "USD"}],
            "paging": {"next": AI.GRAPH + "/me/adaccounts?after=p2"},
        }

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
