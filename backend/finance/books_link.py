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
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from ..hook_relay import Failed, Link, Stop
from .core import mirror as M
from .store import mirror as SX
from .store import tico
from .core.org import SYSTEM

AREA = "finance"                      # HQ's finance routes: <root>/finance/oauth/<provider>/<action>
EARLY = 60                            # seconds before expiry a token is refreshed rather than used
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class BrokerFailed(Exception):
    """HQ could not do the step. `code` is stable and holds no HQ text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReconnectRequired(BrokerFailed):
    """The grant no longer works: a person must connect the books again. Nothing retries it."""


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


def _lock(cid: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(cid, threading.Lock())


def realm_of(store: Any, vault: object, cid: str) -> str:
    with store.read() as c:
        return _state(c, vault, cid).realm


def access_token(store: Any, vault: object, link: Link, cid: str, provider: str, *, now: str,
                 force: bool = False) -> str:
    """A usable access token for a books connection: refreshed through HQ when near expiry or when `force` (after a
    401). One refresh at a time per connection; a 409 marks the connection for reconnecting and raises."""
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
            fresh = refresh(link, provider, state.company, state.refresh_token)
        except ReconnectRequired:
            with store.transaction() as c:
                SX.set_connection_status(c, cid, "reconnect", "reconnect_required", now)
                tico.needs_you(c, SX.connection_owner(c, cid), f"Connect {provider} again",
                               "The grant for this books connection no longer works. Connect it again in Finance; "
                               "the mirror waits until then.")
            raise
        with store.transaction() as c:
            save(c, cid, fresh, by=SYSTEM, at=now)
        return fresh.access_token


def connect_claim(store: Any, vault: object, link: Link, *, provider: str, company_id: str, claim_id: str,
                  name: str, by: str, now: str) -> str:
    """Claim the pair HQ holds for a finished consent and make the books connection: its tokens in the vault. The claim
    is single use, so it is spent before anything is written; a failed write after it means connecting again."""
    tokens = claim(link, provider, company_id, claim_id)
    cid = tico.new_id()
    with store.transaction() as c:
        SX.books_connection_insert(c, cid=cid, provider=provider, name=name, by=by, at=now)
        SX.set_books_settings(c, cid, {"provider": provider, "company_id": company_id})
        save(c, cid, tokens, by=by, at=now)
    return cid


def disconnect_books(store: Any, vault: object, link: Link, cid: str, provider: str, *, now: str) -> None:
    """HQ drops the grant, then the vault rows and the connection's ids go. A failed HQ call keeps everything, so it
    can be run again."""
    with store.read() as c:
        state = _state(c, vault, cid)
    if state.refresh_token and not revoke(link, provider, state.company, state.refresh_token):
        raise BrokerFailed("disconnect_refused")
    with store.transaction() as c:
        books = SX.books_settings(c, cid)
        for key in ("access", "refresh"):
            if books.get(key):
                tico.drop_credential(c, str(books[key]))
        SX.set_books_settings(c, cid, {"access": "", "refresh": "", "expires_at": ""})
        SX.disconnect(c, cid, now)

