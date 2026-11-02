"""Finance's side of the QuickBooks and Xero OAuth broker (spec §3.4). HQ holds the app's client secret: it exchanges the
authorization code, refreshes tokens, and keeps a new pair only until the install claims it (single use). This module
calls HQ through the install's Gateway link (its key, as with other HQ calls) and keeps the install's own tokens in
Tico's credential vault. Only ids of those vault rows live in the connection's settings.

HQ contract (the paths sit under the Gateway's API root, so `<root>/finance/oauth/...`):
  POST .../connect   {company_id, user_id, return_to} -> 201 {url, expires_in}
  POST .../claim     {company_id, claim}              -> {access_token, refresh_token, expires_at,
                                                          refresh_expires_at|null, realm_id|null, tenant_id|null};
                                                          409 connected_elsewhere: another install holds that
                                                          realm or tenant. 409 already_connected: this install
                                                          holds another organisation, and this one is refused.
                                                          With `replacement` ({company_id, access_token,
                                                          refresh_token, expires_at, refresh_expires_at, realm_id,
                                                          tenant_id}) the new pair is for the held organisation and
                                                          is saved for its connection; without it nothing changes
  POST .../refresh   {company_id, refresh_token}      -> the same shape; 409 reconnect_required when the grant is gone
  POST .../disconnect {company_id, refresh_token[, access_token]} -> {ok, disconnected}. Xero also takes the install's
                                                          current access_token and deletes only this company's
                                                          connection. 409 access_expired: refresh, then disconnect
                                                          once more. 502 revoke_failed: try later; the connection
                                                          is kept.

A refresh runs outside transactions, one at a time per connection, and the rotated pair is saved before the new access
token is used (QuickBooks rotates refresh tokens). Tokens are never logged or returned by repr."""
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Mapping, cast

from ..hook_relay import Failed, Link, Stop, forget as forget_relay, keep_answer
from ..triggers import digest
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
PRESET = {"quickbooks": "intuit", "xero": "xero"}      # a books connection's provider -> the preset of its trigger (§3.4)
REASON = re.compile(r"[a-z_]{1,40}")  # HQ's reason, kept only when it is a plain code
# HQ answers these by code, and each is a reason the caller acts on or shows (a 409 is not always a reconnect).
REASONS = ("access_expired", "connected_elsewhere", "revoke_failed")
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


class AlreadyConnected(BrokerFailed):
    """HQ refused the claim because this install already holds another organisation for the provider (409
    already_connected). `replacement` is the new pair HQ sent for the held organisation, or None when it sent none."""

    def __init__(self, replacement: dict[str, Any] | None) -> None:
        super().__init__("already_connected")
        self.replacement = replacement


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


class _Superseded(Exception):
    """A newer pair was saved for the connection after the pair being refreshed was read: the refreshed pair is stale and
    is not kept. Internal to this module."""


@dataclass(frozen=True)
class _State:
    access: str
    refresh_token: str
    expires_at: str
    company: str
    realm: str
    generation: int = 0    # saves of the connection's pair when this pair was read; a refresh saves only if unchanged


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
        if e.code == "already_connected" and path.endswith("/claim"):    # HQ answers this to a claim only
            held = _map(e.body).get("replacement")
            if held is not None and not isinstance(held, dict):
                raise BrokerFailed("bad_answer") from None
            raise AlreadyConnected(cast(dict[str, Any], held) if held is not None else None) from None
        if e.code in REASONS:
            raise BrokerFailed(e.code) from None
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


def revoke(link: Link, provider: str, company_id: str, refresh_token: str, access_token: str = "") -> bool:
    """HQ takes the company's connection off the provider. Xero deletes it with the install's current access
    token, which HQ needs; QuickBooks is sent none."""
    body: dict[str, object] = {"company_id": company_id, "refresh_token": refresh_token}
    if access_token:
        body["access_token"] = access_token
    answer = _call(link, f"oauth/{provider}/disconnect", body)
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
        "realm_id": tokens.realm_id or old.get("realm_id"), "tenant_id": tokens.tenant_id or old.get("tenant_id"),
        "generation": _generation(old) + 1})
    for key in ("access", "refresh"):
        if old.get(key) and old.get(key) != SX.books_settings(c, cid).get(key):
            tico.drop_credential(c, str(old[key]))


def _secret(c: Any, vault: object, credential_id: object) -> str:
    return tico.credential_secret(c, vault, str(credential_id)) if credential_id else ""


def _generation(books: Mapping[str, object]) -> int:
    """How many times the connection's pair has been saved. A refresh saves its pair only while this is unchanged."""
    value = books.get("generation")
    return value if isinstance(value, int) else 0


def _state(c: Any, vault: object, cid: str) -> _State:
    books = SX.books_settings(c, cid)
    return _State(access=_secret(c, vault, books.get("access")), refresh_token=_secret(c, vault, books.get("refresh")),
                  expires_at=str(books.get("expires_at") or ""), company=str(books.get("company_id") or ""),
                  realm=str(books.get("realm_id") or ""), generation=_generation(books))


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


def _refreshed(store: Any, vault: object, link: Link, cid: str, provider: str,
               state: _State) -> tuple[Tokens, int]:
    """HQ's refresh of the connection's pair, with the generation of the pair it was made from. HQ answers not_connected
    when the pair posted is no longer the company's (a refresh saved another meanwhile), so the vault is read again and
    the refresh is tried once with what it holds."""
    try:
        return refresh(link, provider, state.company, state.refresh_token), state.generation
    except NotConnected:
        pass
    current = _current(store, vault, cid)
    if not current.refresh_token or current.refresh_token == state.refresh_token:
        raise ReconnectRequired("not_connected")
    try:
        return refresh(link, provider, current.company, current.refresh_token), current.generation
    except NotConnected:
        raise ReconnectRequired("not_connected") from None


def _save_rotated(store: Any, cid: str, fresh: Tokens, now: str, *, tries: int = VAULT_TRIES,
                  expect: int | None = None) -> bool:
    """Writes a rotated pair to the vault, up to `tries` times. True when one write lands. With `expect`, the pair is
    written only while the connection is still at that generation: a newer save since then raises _Superseded."""
    for attempt in range(1, tries + 1):
        try:
            with store.transaction() as c:
                if expect is not None and _generation(SX.books_settings(c, cid)) != expect:
                    raise _Superseded()
                save(c, cid, fresh, by=SYSTEM, at=now)
            return True
        except _Superseded:
            raise
        except Exception:  # noqa: BLE001 - any failure of the vault write is retried; the rotated pair must not be lost
            log.warning("Books connection %s: vault write try %d of %d failed after a refresh", cid, attempt, tries,
                        exc_info=True)
    return False


def _keep_rotated(store: Any, cid: str, fresh: Tokens, provider: str, now: str, *, expect: int) -> None:
    """The pair a refresh rotated, into the vault. QuickBooks has already retired the old refresh token, so the new pair
    is held here while the write is tried VAULT_TRIES times; when every try fails the connection asks for a reconnect
    and ReconnectRequired is raised, as connect_claim does. A pair a newer save overtook raises _Superseded instead, and
    is not kept."""
    if _save_rotated(store, cid, fresh, now, expect=expect):
        return
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
            fresh, generation = _refreshed(store, vault, link, cid, provider, state)
        except ReconnectRequired:
            with store.transaction() as c:
                SX.set_connection_status(c, cid, "reconnect", "reconnect_required", now)
                tico.needs_you(c, SX.connection_owner(c, cid), f"Connect {provider} again",
                               "The grant for this books connection no longer works. Connect it again in Finance; "
                               "the mirror waits until then.")
            raise
        try:
            _keep_rotated(store, cid, fresh, provider, now, expect=generation)
        except _Superseded:
            return _newer_access(store, vault, cid, now=now)
        return fresh.access_token


def _newer_access(store: Any, vault: object, cid: str, *, now: str) -> str:
    """The access token of the pair a newer save put in the vault while a refresh was at HQ. The refreshed pair was dropped
    as stale: the newer pair's token is used while it is not due; otherwise the pass is retried (BrokerFailed)."""
    current = _current(store, vault, cid)
    if current.access and not _due(current.expires_at, now):
        return current.access
    raise BrokerFailed("superseded")


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


def _other_book(c: Any, provider: str, *, company_id: str, tokens: Tokens) -> bool:
    """Whether the company already has a books connection of this provider for another QuickBooks company or Xero
    organisation. A company connects one (spec 3.4), so a consent for a second one makes no connection."""
    quickbooks = provider == "quickbooks"
    key = tokens.realm_id if quickbooks else tokens.tenant_id
    if not key:
        return False
    for cid, _, _ in SX.books_connections(c, provider):
        books = SX.books_settings(c, cid)
        held = str(books.get("realm_id" if quickbooks else "tenant_id") or "")
        if str(books.get("company_id") or "") == company_id and held and held != key:
            return True
    return False


def connect_claim(store: Any, vault: object, link: Link, *, provider: str, company_id: str, claim_id: str,
                  name: str, by: str, now: str) -> str:
    """Claim the pair HQ holds for a finished consent and keep its tokens in the vault for the books connection.

    The claim is single use and is spent first. A consent for a company or organisation that already has a books
    connection repairs that connection (same row, same book); any other makes a new one. The row is in status
    `reconnect` before the vault write, so a failure is visible. The pair stays in this call's memory while the vault
    write is tried VAULT_TRIES times; when every try fails the connection stays in `reconnect`, a Needs-you item asks
    for a new connect, and ReconnectRequired is raised. A pair cut off by a crash before the write is lost the same
    way.

    HQ may refuse the claim `already_connected`, because this install holds another organisation for the provider. Then
    nothing is made here, and a `replacement` for the held organisation is first kept for its connection
    (_replace_held). The connect ends as AlreadyConnected either way. So does a pair for a second QuickBooks company or
    Xero organisation of a company that already has one (_other_book): nothing is made."""
    try:
        tokens = claim(link, provider, company_id, claim_id)
    except AlreadyConnected as refused:
        if refused.replacement is not None:
            _replace_held(store, provider=provider, replacement=refused.replacement, by=by, now=now)
        raise
    with store.transaction() as c:
        cid = _same_account(c, provider, tokens)
        if cid is None and _other_book(c, provider, company_id=company_id, tokens=tokens):
            raise AlreadyConnected(None)
        if cid is None:
            cid = tico.new_id()
            SX.books_connection_insert(c, cid=cid, provider=provider, name=name, by=by, at=now)
            SX.set_books_settings(c, cid, {"provider": provider, "company_id": company_id,
                                          "realm_id": tokens.realm_id, "tenant_id": tokens.tenant_id})
        _keep_trigger(c, cid, provider=provider, by=by, now=now)
        SX.set_connection_status(c, cid, "reconnect", "vault_pending", now)
    with _lock(cid):
        _keep_claimed(store, cid, tokens, provider=provider, by=by, now=now)
    return cid


def _keep_trigger(c: Any, cid: str, *, provider: str, by: str, now: str) -> None:
    """The books connection's Tico trigger (spec §3.4): a Finance trigger, `consumer` finance, whose Intuit or Xero calls
    HQ relays to this install over the Gateway. It has the connection's id. Made with the connection, and made again for
    a connection that predates it, or whose trigger was removed."""
    c.execute("INSERT INTO triggers(id,name,kind,secret_hash,created_by,created_at,updated_at,preset,consumer) "
              "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,preset=excluded.preset,"
              "consumer=excluded.consumer,signing_secret=NULL,deleted_at=NULL,updated_at=excluded.updated_at",
              (cid, LABEL.get(provider, provider), "webhook", digest(secrets.token_urlsafe(24)), by, now, now,
               PRESET[provider], "finance"))


def ensure_relays(store: Any, vault: object, settings: Any, *, now: str) -> list[str]:
    """Each books connection's Gateway relay, which HQ delivers its Intuit or Xero calls to (spec §3.4). It is asked of HQ
    outside any transaction, and tried again on each books pass until HQ takes it, so a connection made while HQ could not
    be reached, or from before this, gets its relay then. Nothing is asked without a Gateway key. Returns what failed."""
    from ..hook_relay import link_of
    with store.read() as c:
        missing = [(cid, provider) for cid, provider, _ in SX.books_connections(c)
                   if c.execute("SELECT 1 FROM trigger_relays WHERE trigger_id=? AND deleted_at IS NULL",
                                (cid,)).fetchone() is None]
        link = link_of(c, vault, settings) if missing else None
        owners = {cid: SX.connection_owner(c, cid) for cid, _ in missing}
    if not missing or link is None:
        return []
    problems: list[str] = []
    for cid, provider in missing:
        label = LABEL.get(provider, provider)
        with store.transaction() as c:
            _keep_trigger(c, cid, provider=provider, by=owners[cid], now=now)
        try:
            answer = link.call("POST", "relays", {"trigger_id": cid, "name": label}, "books-relay:" + cid)
        except Stop:
            problems.append(f"The Tico Gateway does not accept this server's key, so the {label} relay was not made")
            continue
        except Failed as failed:
            problems.append(f"The Tico Gateway did not take the {label} relay ({failed.code})")
            continue
        with store.transaction() as c:
            if not keep_answer(c, answer, trigger_id=cid, by=owners[cid]):
                problems.append(f"The Tico Gateway gave an answer for the {label} relay this server cannot use")
    return problems


def _keep_claimed(store: Any, cid: str, tokens: Tokens, *, provider: str, by: str, now: str) -> None:
    """A claimed pair into the vault for a books connection, which is active once it lands. The write is tried
    VAULT_TRIES times; when every try fails the connection stays in `reconnect`, a Needs-you item asks for a new connect,
    and ReconnectRequired is raised. The caller holds the connection's lock."""
    for attempt in range(1, VAULT_TRIES + 1):
        try:
            with store.transaction() as c:
                save(c, cid, tokens, by=by, at=now)
                SX.set_connection_status(c, cid, "active", "", now)
            return
        except Exception:  # noqa: BLE001 - any failure of the vault write is retried; the claimed pair must not be lost
            log.warning("Books connection %s: vault write try %d of %d failed", cid, attempt, VAULT_TRIES,
                        exc_info=True)
    with store.transaction() as c:
        SX.set_connection_status(c, cid, "reconnect", "vault_write_failed", now)
        tico.needs_you(c, by, f"Connect {provider} again",
                       "Tico could not keep the grant for this books connection. Connect it again in Finance.")
    raise ReconnectRequired("vault_write_failed")


def _held(c: Any, provider: str, *, company_id: str, tenant_id: str | None) -> str | None:
    """The books connection, not disconnected, that holds this organisation (tenant) for this company."""
    if not (company_id and tenant_id):
        return None
    for cid, _, _ in SX.books_connections(c, provider):
        books = SX.books_settings(c, cid)
        if str(books.get("tenant_id") or "") == tenant_id and str(books.get("company_id") or "") == company_id:
            return cid
    return None


def _replace_held(store: Any, *, provider: str, replacement: dict[str, Any], by: str, now: str) -> None:
    """HQ's pair for the organisation this install already holds, sent with a refused claim. Its company and tenant must
    be that connection's, or nothing is saved (bad_answer). The pair takes the connection's place: the provider has
    already superseded its old pair, since one Xero login authorised the second organisation. It is kept as a claimed
    pair is."""
    fresh = _tokens(replacement)
    company = str(replacement.get("company_id") or "")
    with store.read() as c:
        cid = _held(c, provider, company_id=company, tenant_id=fresh.tenant_id)
    if cid is None:
        raise BrokerFailed("bad_answer")
    # The connection's lock, as a refresh takes it: a refresh at HQ finishes first, and no older pair is saved after this.
    with _lock(cid):
        with store.transaction() as c:
            SX.set_connection_status(c, cid, "reconnect", "vault_pending", now)
        _keep_claimed(store, cid, fresh, provider=provider, by=by, now=now)


def _drop_grant(store: Any, vault: object, link: Link, cid: str, provider: str, state: _State) -> None:
    """HQ drops the company's grant for the pair posted. When HQ holds no grant for it (not_connected), a newer pair the
    vault holds is tried once; a grant HQ no longer holds has nothing to drop, so the caller may clean up."""
    try:
        ok = revoke(link, provider, state.company, state.refresh_token, state.access if provider == "xero" else "")
    except NotConnected:
        current = _current(store, vault, cid)
        if not current.refresh_token or current.refresh_token == state.refresh_token:
            return
        try:
            ok = revoke(link, provider, current.company, current.refresh_token,
                        current.access if provider == "xero" else "")
        except NotConnected:
            return
    if not ok:
        raise BrokerFailed("disconnect_refused")


def _refresh_for_disconnect(store: Any, vault: object, link: Link, cid: str, provider: str, *,
                            now: str) -> tuple[_State, Tokens | None]:
    """A fresh pair for a disconnect HQ is asked with, kept in the vault. When the write does not land, the pair still
    goes to HQ, since it can take the grant off with it: the second value is that pair, for the caller to keep if HQ
    refuses. Unlike a sync's refresh, a grant HQ no longer holds is not marked for a reconnect here."""
    with _lock(cid):
        state = _current(store, vault, cid)
        if not state.refresh_token:
            raise ReconnectRequired("no_refresh_token")
        fresh, generation = _refreshed(store, vault, link, cid, provider, state)
        unkept = None if _save_rotated(store, cid, fresh, now) else fresh
    held = _State(access=fresh.access_token, refresh_token=fresh.refresh_token, expires_at=fresh.expires_at,
                  company=state.company, realm=state.realm, generation=generation)
    return held, unkept


def _keep_unkept(store: Any, cid: str, fresh: Tokens, provider: str, *, now: str, expect: int) -> None:
    """HQ refused a disconnect while the rotated pair is still only in memory: the write is tried once more. When it
    lands the connection stays with its pair. When it does not, the connection asks for a reconnect and the caller
    gets a retryable error saying so. A pair saved since the refresh is newer, so this one is dropped."""
    try:
        if _save_rotated(store, cid, fresh, now, tries=1, expect=expect):
            return
    except _Superseded:
        return
    with store.transaction() as c:
        SX.set_connection_status(c, cid, "reconnect", "vault_write_failed", now)
        tico.needs_you(c, SX.connection_owner(c, cid), f"Connect {provider} again",
                       "Tico could not keep the grant for this books connection. Connect it again in Finance.")
    raise BrokerFailed("vault_write_failed")


def _revoked(store: Any, vault: object, link: Link, cid: str, provider: str, *, now: str) -> None:
    """HQ drops the company's grant. Xero deletes only this company's connection with the install's current access
    token, so a usable one is refreshed first when there is none or it is due. HQ's access_expired refreshes the pair
    once and asks again. A rotated pair the vault does not keep is still what HQ is asked with; if HQ refuses, the
    vault write is tried once more before the refusal goes on."""
    state = _current(store, vault, cid)
    unkept: Tokens | None = None
    if provider == "xero" and (not state.access or _due(state.expires_at, now)):
        state, unkept = _refresh_for_disconnect(store, vault, link, cid, provider, now=now)
    retried = False
    while True:
        try:
            return _drop_grant(store, vault, link, cid, provider, state)
        except BrokerFailed as e:
            if e.code == "access_expired" and not retried:
                retried = True
                state, unkept = _refresh_for_disconnect(store, vault, link, cid, provider, now=now)
                continue
            if unkept is not None:
                _keep_unkept(store, cid, unkept, provider, now=now, expect=state.generation)
            raise


def disconnect_books(store: Any, vault: object, link: Link, cid: str, provider: str, *, now: str) -> None:
    """HQ drops the grant, then the vault rows and the connection's ids go. A failed HQ call keeps everything, so it can
    be run again. HQ answering reconnect_required means its grant is already gone, so only the local rows go."""
    try:
        if _current(store, vault, cid).refresh_token:
            _revoked(store, vault, link, cid, provider, now=now)
    except ReconnectRequired:
        pass    # HQ holds no grant to take off (reconnect_required, not_connected, no_refresh_token): the local rows go
    with store.transaction() as c:
        books = SX.books_settings(c, cid)
        for key in ("access", "refresh"):
            if books.get(key):
                tico.drop_credential(c, str(books[key]))
        SX.set_books_settings(c, cid, {"access": "", "refresh": "", "expires_at": ""})
        SX.disconnect(c, cid, now)
        _drop_trigger(c, cid, now=now)


def _drop_trigger(c: Any, cid: str, *, now: str) -> None:
    """The connection's trigger goes with it: what HQ relayed to it, its seen ids, and its Gateway relay (HQ is told of that
    by the relay loop, so no network call waits in this write)."""
    c.execute("UPDATE triggers SET deleted_at=?,updated_at=? WHERE id=? AND deleted_at IS NULL", (now, now, cid))
    c.execute("DELETE FROM trigger_bodies WHERE trigger_id=?", (cid,))
    c.execute("DELETE FROM trigger_seen WHERE trigger_id=?", (cid,))
    forget_relay(c, cid)


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
    or the BrokerFailed of the claim or the vault. AlreadyConnected is HQ refusing the organisation: no connection is
    made, and its code is the reason the return carries."""
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

