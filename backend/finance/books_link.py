"""Finance's side of the QuickBooks and Xero OAuth broker (spec §3.4). HQ holds the app's client secret: it exchanges the
authorization code, refreshes tokens, and keeps a new pair only until the install claims it (single use). This module
calls HQ through the install's Gateway link (its key, as with other HQ calls) and keeps the install's own tokens in
Tico's credential vault. Only ids of those vault rows live in the connection's settings.

HQ contract (the paths sit under the Gateway's API root, so `<root>/finance/oauth/...`):
  POST .../connect   {company_id, user_id, return_to} -> 201 {url, expires_in}
  POST .../claim     {company_id, claim}              -> {access_token, refresh_token, expires_at,
                                                          refresh_expires_at|null, realm_id|null, tenant_id|null}
  POST .../refresh   {company_id, refresh_token}      -> the same shape; 409 reconnect_required when it cannot be
  POST .../disconnect {company_id, refresh_token}     -> {ok, disconnected}

A refresh runs outside transactions, one at a time per connection, and the rotated pair is saved before the new access
token is used (QuickBooks rotates refresh tokens). Tokens are never logged or returned by repr."""
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Mapping, cast

from ..hook_relay import Failed, Link, Stop
from .core import mirror as M
from .store import mirror as SX
from .store import tico
from .core.org import SYSTEM

log = logging.getLogger(__name__)
AREA = "finance"                      # HQ's finance routes: <root>/finance/oauth/<provider>/<action>
EARLY = 60                            # seconds before expiry a token is refreshed rather than used
VAULT_TRIES = 2                       # the claimed pair is written to the vault this many times before reconnecting
FLOWS = "finance-books-flows"         # the consents this install started, by flow id, until HQ sends the browser back
FLOW_S = 1800                         # seconds a started consent may wait for its return
LABEL = {"quickbooks": "QuickBooks", "xero": "Xero"}
REASON = re.compile(r"[a-z_]{1,40}")  # HQ's reason, kept only when it is a plain code
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class BrokerFailed(Exception):
    """HQ could not do the step. `code` is stable and holds no HQ text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReconnectRequired(BrokerFailed):
    """The grant no longer works: a person must connect the books again. Nothing retries it."""


class NotConnected(BrokerFailed):
    """HQ holds no grant for the refresh token posted (404 not_connected): the token is not the company's current one,
    or the company is not connected at HQ. The caller reads the vault again before it decides which."""


class ConsentFailed(BrokerFailed):
    """The browser came back without a finished consent (or its claim made no connection). `code` is HQ's reason."""


class NotStarted(Exception):
    """The return names no consent this person started, or one that has already ended."""


class UnknownConnection(Exception):
    """No books connection that is not disconnected has the id."""


@dataclass(frozen=True, repr=False)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: str
    refresh_expires_at: str | None
    realm_id: str | None
    tenant_id: str | None

    def __repr__(self) -> str:
        return "Tokens(<redacted>)"


@dataclass(frozen=True)
class _State:
    access: str
    refresh_token: str
    expires_at: str
    company: str
    realm: str


def root(link: Link) -> Link:
    """The Link at the API root: the Gateway's base is `.../v1/gateway`, and the broker sits beside it at `.../v1`."""
    base = link.base.rstrip("/")
    if base.endswith("/gateway"):
        base = base[: -len("/gateway")]
    return Link(link.key, base, link.fingerprint)


def _call(link: Link, path: str, body: Mapping[str, object]) -> dict[str, Any]:
    try:
        return root(link).call("POST", path, dict(body), area=AREA)
    except Failed as e:
        if e.code == "reconnect_required" or e.status == 409:
            raise ReconnectRequired("reconnect_required") from None
        if e.code == "not_connected" or e.status == 404:
            raise NotConnected("not_connected") from None
        raise BrokerFailed(e.code) from None
    except Stop:
        raise BrokerFailed("gateway_refused") from None


def _text(value: object) -> str | None:
    return str(value) if value else None


def _tokens(answer: Mapping[str, Any]) -> Tokens:
    access, refresh, expires = (str(answer.get(k) or "") for k in ("access_token", "refresh_token", "expires_at"))
    if not (access and refresh and expires):
        raise BrokerFailed("bad_answer")
    return Tokens(access, refresh, expires, _text(answer.get("refresh_expires_at")), _text(answer.get("realm_id")),
                  _text(answer.get("tenant_id")))


def connect(link: Link, provider: str, company_id: str, user_id: str, return_to: str) -> tuple[str, int]:
    """The provider's consent page (the browser goes there) and how long that link lives, in seconds."""
    answer = _call(link, f"oauth/{provider}/connect", {"company_id": company_id, "user_id": user_id,
                                                       "return_to": return_to})
    url = str(answer.get("url") or "")
    expires = answer.get("expires_in")
    if not url.startswith("https://"):
        raise BrokerFailed("bad_answer")
    return url, int(expires) if isinstance(expires, int) else 0


def claim(link: Link, provider: str, company_id: str, claim_id: str) -> Tokens:
    return _tokens(_call(link, f"oauth/{provider}/claim", {"company_id": company_id, "claim": claim_id}))


def refresh(link: Link, provider: str, company_id: str, refresh_token: str) -> Tokens:
    return _tokens(_call(link, f"oauth/{provider}/refresh",
                         {"company_id": company_id, "refresh_token": refresh_token}))


def revoke(link: Link, provider: str, company_id: str, refresh_token: str) -> bool:
    answer = _call(link, f"oauth/{provider}/disconnect", {"company_id": company_id, "refresh_token": refresh_token})
    return answer.get("ok") is True


def save(c: Any, cid: str, tokens: Tokens, *, by: str, at: str) -> None:
    """The new pair into the vault, the connection pointed at it, then the old pair dropped. The caller's transaction."""
    old = SX.books_settings(c, cid)
    provider = str(old.get("provider") or "books")
    access = tico.seal_credential(c, f"{provider} access token", tokens.access_token, "", by, at)
    refreshed = tico.seal_credential(c, f"{provider} refresh token", tokens.refresh_token, "", by, at)
    SX.set_books_settings(c, cid, {
        "access": access, "refresh": refreshed, "expires_at": tokens.expires_at,
        "refresh_expires_at": tokens.refresh_expires_at,
        "realm_id": tokens.realm_id or old.get("realm_id"), "tenant_id": tokens.tenant_id or old.get("tenant_id")})
    for key in ("access", "refresh"):
        if old.get(key) and old.get(key) != SX.books_settings(c, cid).get(key):
            tico.drop_credential(c, str(old[key]))


def _secret(c: Any, vault: object, credential_id: object) -> str:
    return tico.credential_secret(c, vault, str(credential_id)) if credential_id else ""


def _state(c: Any, vault: object, cid: str) -> _State:
    books = SX.books_settings(c, cid)
    return _State(access=_secret(c, vault, books.get("access")), refresh_token=_secret(c, vault, books.get("refresh")),
                  expires_at=str(books.get("expires_at") or ""), company=str(books.get("company_id") or ""),
                  realm=str(books.get("realm_id") or ""))


def _due(expires_at: str, now: str) -> bool:
    return not expires_at or M.parse(expires_at).timestamp() - EARLY <= M.parse(now).timestamp()


def _current(store: Any, vault: object, cid: str) -> _State:
    with store.read() as c:
        return _state(c, vault, cid)


def _lock(cid: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(cid, threading.Lock())


def realm_of(store: Any, vault: object, cid: str) -> str:
    with store.read() as c:
        return _state(c, vault, cid).realm


def _refreshed(store: Any, vault: object, link: Link, cid: str, provider: str, state: _State) -> Tokens:
    """HQ's refresh of the connection's pair. HQ answers not_connected when the pair posted is no longer the company's
    (a refresh saved another meanwhile), so the vault is read again and the refresh is tried once with what it holds."""
    try:
        return refresh(link, provider, state.company, state.refresh_token)
    except NotConnected:
        pass
    current = _current(store, vault, cid)
    if not current.refresh_token or current.refresh_token == state.refresh_token:
        raise ReconnectRequired("not_connected")
    try:
        return refresh(link, provider, current.company, current.refresh_token)
    except NotConnected:
        raise ReconnectRequired("not_connected") from None


def _keep_rotated(store: Any, cid: str, fresh: Tokens, provider: str, now: str) -> None:
    """The pair a refresh rotated, into the vault. QuickBooks has already retired the old refresh token, so the new pair
    is held here while the write is tried VAULT_TRIES times; when every try fails the connection asks for a reconnect
    and ReconnectRequired is raised, as connect_claim does."""
    for attempt in range(1, VAULT_TRIES + 1):
        try:
            with store.transaction() as c:
                save(c, cid, fresh, by=SYSTEM, at=now)
            return
        except Exception:  # noqa: BLE001 - any failure of the vault write is retried; the rotated pair must not be lost
            log.warning("Books connection %s: vault write try %d of %d failed after a refresh", cid, attempt, VAULT_TRIES,
                        exc_info=True)
    with store.transaction() as c:
        SX.set_connection_status(c, cid, "reconnect", "vault_write_failed", now)
        tico.needs_you(c, SX.connection_owner(c, cid), f"Connect {provider} again",
                       "Tico could not keep the grant for this books connection. Connect it again in Finance.")
    raise ReconnectRequired("vault_write_failed")


def access_token(store: Any, vault: object, link: Link, cid: str, provider: str, *, now: str,
                 force: bool = False) -> str:
    """A usable access token for a books connection: refreshed through HQ when near expiry or when `force` (after a
    401). One refresh at a time per connection; a 409 marks the connection for reconnecting and raises, and so does a
    rotated pair the vault will not keep (see _keep_rotated)."""
    with store.read() as c:
        state = _state(c, vault, cid)
    if not force and state.access and not _due(state.expires_at, now):
        return state.access
    with _lock(cid):
        with store.read() as c:
            state = _state(c, vault, cid)                  # a refresh that finished while this one waited
        if not force and state.access and not _due(state.expires_at, now):
            return state.access
        if not state.refresh_token:
            raise ReconnectRequired("no_refresh_token")
        try:
            fresh = _refreshed(store, vault, link, cid, provider, state)
        except ReconnectRequired:
            with store.transaction() as c:
                SX.set_connection_status(c, cid, "reconnect", "reconnect_required", now)
                tico.needs_you(c, SX.connection_owner(c, cid), f"Connect {provider} again",
                               "The grant for this books connection no longer works. Connect it again in Finance; "
                               "the mirror waits until then.")
            raise
        _keep_rotated(store, cid, fresh, provider, now)
        return fresh.access_token


def _same_account(c: Any, provider: str, tokens: Tokens) -> str | None:
    """The books connection, not disconnected, that already holds this QuickBooks company or Xero organisation."""
    key = tokens.realm_id or tokens.tenant_id
    if not key:
        return None
    for cid, _, _ in SX.books_connections(c, provider):
        books = SX.books_settings(c, cid)
        if key in (str(books.get("realm_id") or ""), str(books.get("tenant_id") or "")):
            return cid
    return None


def connect_claim(store: Any, vault: object, link: Link, *, provider: str, company_id: str, claim_id: str,
                  name: str, by: str, now: str) -> str:
    """Claim the pair HQ holds for a finished consent and keep its tokens in the vault for the books connection.

    The claim is single use and is spent first. A consent for a company or organisation that already has a books
    connection repairs that connection (same row, same book); any other makes a new one. The row is in status
    `reconnect` before the vault write, so a failure is visible. The pair stays in this call's memory while the vault
    write is tried VAULT_TRIES times; when every try fails the connection stays in `reconnect`, a Needs-you item asks
    for a new connect, and ReconnectRequired is raised. A pair cut off by a crash before the write is lost the same
    way."""
    tokens = claim(link, provider, company_id, claim_id)
    with store.transaction() as c:
        cid = _same_account(c, provider, tokens)
        if cid is None:
            cid = tico.new_id()
            SX.books_connection_insert(c, cid=cid, provider=provider, name=name, by=by, at=now)
            SX.set_books_settings(c, cid, {"provider": provider, "company_id": company_id,
                                          "realm_id": tokens.realm_id, "tenant_id": tokens.tenant_id})
        SX.set_connection_status(c, cid, "reconnect", "vault_pending", now)
    for attempt in range(1, VAULT_TRIES + 1):
        try:
            with store.transaction() as c:
                save(c, cid, tokens, by=by, at=now)
                SX.set_connection_status(c, cid, "active", "", now)
            return cid
        except Exception:  # noqa: BLE001 - any failure of the vault write is retried; the claimed pair must not be lost
            log.warning("Books connection %s: vault write try %d of %d failed", cid, attempt, VAULT_TRIES,
                        exc_info=True)
    with store.transaction() as c:
        SX.set_connection_status(c, cid, "reconnect", "vault_write_failed", now)
        tico.needs_you(c, by, f"Connect {provider} again",
                       "Tico could not keep the grant for this books connection. Connect it again in Finance.")
    raise ReconnectRequired("vault_write_failed")


def _revoked(store: Any, vault: object, link: Link, cid: str, provider: str, state: _State) -> None:
    """HQ drops the company's grant. When HQ holds no grant for the pair posted (not_connected), a newer pair the vault
    holds is tried once; a grant HQ no longer holds has nothing to drop, so the caller may clean up."""
    try:
        ok = revoke(link, provider, state.company, state.refresh_token)
    except NotConnected:
        current = _current(store, vault, cid)
        if not current.refresh_token or current.refresh_token == state.refresh_token:
            return
        try:
            ok = revoke(link, provider, current.company, current.refresh_token)
        except NotConnected:
            return
    if not ok:
        raise BrokerFailed("disconnect_refused")


def disconnect_books(store: Any, vault: object, link: Link, cid: str, provider: str, *, now: str) -> None:
    """HQ drops the grant, then the vault rows and the connection's ids go. A failed HQ call keeps everything, so it
    can be run again. HQ answering reconnect_required means its grant is already gone, so only the local rows go."""
    state = _current(store, vault, cid)
    if state.refresh_token:
        try:
            _revoked(store, vault, link, cid, provider, state)
        except ReconnectRequired:
            pass
    with store.transaction() as c:
        books = SX.books_settings(c, cid)
        for key in ("access", "refresh"):
            if books.get(key):
                tico.drop_credential(c, str(books[key]))
        SX.set_books_settings(c, cid, {"access": "", "refresh": "", "expires_at": ""})
        SX.disconnect(c, cid, now)


# ------------------------------------------------------------------------------------------------ the routes' calls
def _map(value: object) -> dict[str, Any]:
    """A stored value as a dict, or an empty one when it is not (a damaged row)."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _flows(c: Any) -> dict[str, Any]:
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (FLOWS,)).fetchone()
    try:
        return _map(json.loads(row[0]) if row else {})
    except ValueError:
        return {}


def _keep_flows(c: Any, flows: Mapping[str, Any]) -> None:
    c.execute("INSERT INTO registry_metadata(key, value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
              "value_json=excluded.value_json", (FLOWS, json.dumps(flows, sort_keys=True)))


def _fresh(started: object, now: str) -> bool:
    at = _map(started).get("at")
    return isinstance(at, str) and M.parse(now).timestamp() - M.parse(at).timestamp() <= FLOW_S


def _open_flow(c: Any, *, user: str, provider: str, company_id: str, now: str) -> str:
    """A flow id for one consent, kept with who started it. Consents that have run out are dropped here."""
    flows = {k: v for k, v in _flows(c).items() if _fresh(v, now)}
    flow = secrets.token_urlsafe(24)
    flows[flow] = {"user": user, "provider": provider, "company": company_id, "at": now}
    _keep_flows(c, flows)
    return flow


def _spend_flow(c: Any, flow: str, *, user: str, now: str) -> dict[str, str] | None:
    """The consent a flow id names, spent. None unless `user` started it and it has not run out; someone else's flow
    is left as it is."""
    flows = _flows(c)
    started = _map(flows.get(flow))
    if not started or started.get("user") != user or not _fresh(started, now):
        return None
    del flows[flow]
    _keep_flows(c, flows)
    return {str(k): str(v) for k, v in started.items()}


def start(store: Any, link: Link, *, provider: str, company_id: str, user: str, return_base: str) -> tuple[str, int]:
    """HQ's consent page for the provider, and how long that link lives. The return route carries a flow id this install
    keeps for `user`, so only the person who started the consent can claim what HQ sends back."""
    with store.transaction() as c:
        flow = _open_flow(c, user=user, provider=provider, company_id=company_id, now=tico.now())
    return connect(link, provider, company_id, user, f"{return_base.rstrip('/')}/{flow}")


def finish(store: Any, vault: object, link: Link, *, flow: str, user: str, outcome: str, reason: str,
           claim_id: str) -> str:
    """The browser came back from HQ for a consent: the flow is spent, then the pair is claimed once and kept, and the
    books connection made or repaired. Returns its id. Raises NotStarted, ConsentFailed (HQ did not finish the consent)
    or the BrokerFailed of the claim or the vault."""
    now = tico.now()
    with store.transaction() as c:
        started = _spend_flow(c, flow, user=user, now=now)
    if started is None:
        raise NotStarted()
    if outcome != "connected" or not claim_id:
        raise ConsentFailed(reason if REASON.fullmatch(reason) else "unknown")
    provider = started["provider"]
    cid = connect_claim(store, vault, link, provider=provider, company_id=started["company"], claim_id=claim_id,
                        name=LABEL.get(provider, provider), by=user, now=now)
    with store.transaction() as c:
        tico.audit(c, user, "finance.books.connected", cid, {"provider": provider})
    return cid


def drop(store: Any, vault: object, link: Link, cid: str, *, by: str) -> None:
    """Disconnect a books connection: HQ's grant, then its vault rows (disconnect_books). Raises UnknownConnection when
    no books connection that is not disconnected has the id."""
    with store.read() as c:
        held = [row for row in SX.books_connections(c) if row[0] == cid]
    if not held:
        raise UnknownConnection()
    provider = held[0][1]
    disconnect_books(store, vault, link, cid, provider, now=tico.now())
    with store.transaction() as c:
        tico.audit(c, by, "finance.books.disconnected", cid, {"provider": provider})

