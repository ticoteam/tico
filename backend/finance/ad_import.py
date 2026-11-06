"""The ad spend import: Google Ads' and Meta's own daily spend per campaign, read every 4 hours into fin_ad_spends, so
an open month's ad budgets show live spend before the card statement posts (budgeting.py, "Live ad spend").

It reads; it never books. Nothing here posts to the ledger or changes a posting: the card charge still books when the
bank feed brings it, and the budget reads take care that the two are never counted together.

Each import belongs to one Google Ads or Meta Ads tool (backend/tool_services.py) and names which stored credentials
hold each value the platform needs (`SLOTS`; credential ids, never values). Values are read from the vault at use time
and never logged or kept; Google's refresh token is exchanged for an access token on every run and the access token is
dropped with the run. A refused login (a failed token exchange, an API 401, Meta's error 190) sets the import to
"reconnect"; any other failure to "failed" with what went wrong. Either way the spend already imported stays.

A run reads the accounts the login sees, upserts them (a new one is not assigned to a bot until a finance admin picks
one), then each account's spend from `history_from` on the first run (or after `history_from` moves earlier) and the
last 30 days after that. A complete fetch replaces the window's rows per account, so a day the platform restates
overwrites. Rate limits back off and retry a few times. The worker sweeps every 15 minutes, runs what is due (4 hours
since the last run, or asked for), then gives bots their budget warnings (ad_warnings.py). A rehearsal copy calls no
platform. HTTP goes through an injectable opener; tests use fakes and never the network.
"""
import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, cast

from .core import ledger as L
from .core.org import SYSTEM
from .gateway_sync import Bell
from .mercury import Opener
from .store import ads as SA
from .store import ap as SAP
from .store import ledger as SL
from .store import tico

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v26.0"
GRAPH_HOST = "graph.facebook.com"
GOOGLE_ADS = "https://googleads.googleapis.com/v26"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EVERY = timedelta(hours=4)
SWEEP = 900.0
RECENT_DAYS = 30
META_WINDOW = 31                       # days per insights call (a long range per day per campaign is slow to answer)
TIMEOUT = 30
TRIES = 4
MAX_WAIT = 60.0
META_SESSION = {190, 102}              # the token is expired, revoked or wrong
META_THROTTLE = {4, 17, 32, 613, 80000, 80003, 80004, 80014}

# Which stored keys hold each value, by variable name (compared loosely: case and punctuation do not matter).
SLOTS: dict[str, dict[str, tuple[str, ...]]] = {
    "google_ads": {"client_id": ("GOOGLE_ADS_CLIENT_ID",), "client_secret": ("GOOGLE_ADS_CLIENT_SECRET",),
                   "refresh_token": ("GOOGLE_ADS_REFRESH_TOKEN",), "developer_token": ("GOOGLE_ADS_DEVELOPER_TOKEN",),
                   "login_customer_id": ("GOOGLE_ADS_LOGIN_CUSTOMER_ID",),
                   "customer_ids": ("GOOGLE_ADS_CUSTOMER_ID", "GOOGLE_ADS_CUSTOMER_IDS")},
    "meta_ads": {"access_token": ("META_ACCESS_TOKEN", "META_ADS_ACCESS_TOKEN", "FACEBOOK_ACCESS_TOKEN",
                                  "FACEBOOK_ADS_ACCESS_TOKEN", "FB_ACCESS_TOKEN")},
}
REQUIRED = {"google_ads": ("client_id", "client_secret", "refresh_token", "developer_token"),
            "meta_ads": ("access_token",)}
SLOT_LABEL = {"client_id": "Client ID", "client_secret": "Client secret", "refresh_token": "Refresh token",
              "developer_token": "Developer token", "login_customer_id": "Login customer ID",
              "customer_ids": "Customer IDs", "access_token": "Access token"}
# Contacts the platforms' spend books under: a card charge from either is found by these too (budgeting.py).
VENDOR = {"google_ads": "Google Ads", "meta_ads": "Meta"}


class Reconnect(Exception):
    """The platform refused the login: someone has to connect it again."""


class Failed(Exception):
    """Anything else that stopped a run; the message says what (never a secret)."""


# ------------------------------------------------------------------------------------------------ credentials
def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


@dataclass(frozen=True)
class Candidate:
    """A stored credential that can hold a slot's value. `set_key` groups a set (a company's keys, one bot's copies)."""
    id: str
    name: str
    env: str
    slot: str
    set_key: str
    tool_id: str
    created_at: str


def _slot_of(platform: str, env: str, name: str) -> tuple[str, str] | None:
    """(slot, the variable it matched) for a credential's variable, else for a variable written in its name."""
    wanted = SLOTS[platform]
    norm_env, norm_name = _norm(env), _norm(name)
    for text, exact in ((norm_env, True), (norm_name, False)):
        if not text:
            continue
        # Longest names first, so GOOGLE_ADS_CUSTOMER_IDS is not read as GOOGLE_ADS_CUSTOMER_ID.
        for slot, names in wanted.items():
            for var in sorted(names, key=len, reverse=True):
                if text == var or (not exact and re.search(rf"(^|_){var}(_|$)", text)):
                    return slot, var
    return None


def _set_key(name: str, var: str) -> str:
    """A credential's name without its variable: "Google Ads · GOOGLE_ADS_CLIENT_ID" -> "google ads",
    "GOOGLE_ADS_CLIENT_ID (analytics)" -> "(analytics)"."""
    rest = re.sub(re.escape(var).replace("_", "[^A-Za-z0-9]?"), " ", name, flags=re.IGNORECASE)
    return re.sub(r"[\s·:\-–—|]+", " ", rest).strip().casefold()


def candidates(c: Any, platform: str) -> list[Candidate]:
    """Every stored credential with a value whose variable (or name) is one the platform needs, oldest first. Names
    and ids only; no value is read."""
    out: list[Candidate] = []
    for r in c.execute("SELECT id, name, env, tool_id, created_at FROM credentials WHERE ciphertext IS NOT NULL "
                       "AND source<>'finance' ORDER BY created_at, id"):
        hit = _slot_of(platform, str(r["env"] or ""), str(r["name"] or ""))
        if hit is None:
            continue
        slot, var = hit
        out.append(Candidate(str(r["id"]), str(r["name"] or ""), str(r["env"] or ""), slot,
                             _set_key(str(r["name"] or ""), var), str(r["tool_id"] or ""), str(r["created_at"] or "")))
    return out


def default_mapping(c: Any, platform: str, tool_id: str = "") -> dict[str, str]:
    """{slot: credential id} from one set, never a mix: the set holding the tool's own key first, then the set with the
    most required slots, then one with no "(…)" suffix (the company's, not a bot's copy), then the oldest."""
    found = candidates(c, platform)
    sets: dict[str, list[Candidate]] = {}
    for cand in found:
        sets.setdefault(cand.set_key, []).append(cand)
    if not sets:
        return {}

    def rank(key: str) -> tuple[int, int, int, int, str]:
        rows = sets[key]
        have = {r.slot for r in rows}
        return (-int(any(r.tool_id == tool_id for r in rows) and bool(tool_id)),
                -sum(s in have for s in REQUIRED[platform]), -len(have),
                int(bool(re.fullmatch(r"\(.*\)", key))), min(r.created_at for r in rows))

    best = sorted(sets, key=rank)[0]
    out: dict[str, str] = {}
    for cand in sets[best]:                                 # oldest first: the first per slot wins
        out.setdefault(cand.slot, cand.id)
    return out


def missing(platform: str, mapping: Mapping[str, str]) -> list[str]:
    return [s for s in REQUIRED[platform] if not mapping.get(s)]


def _secrets(c: Any, vault: object, imp: SA.AdImport) -> dict[str, str]:
    values = {slot: tico.credential_secret(c, vault, cid).strip() for slot, cid in imp.credentials.items() if cid}
    gaps = [SLOT_LABEL[s] for s in REQUIRED[imp.platform] if not values.get(s)]
    if gaps:
        raise Failed("No stored value for " + ", ".join(gaps))
    return values


# ------------------------------------------------------------------------------------------------ HTTP
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: a token goes only to the host it was meant for."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


@dataclass(frozen=True)
class Reply:
    status: int | None                    # None: unreachable
    body: Any                             # parsed JSON ({} when none)
    retry_after: float | None = None


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {}


@dataclass
class Http:
    """One run's HTTP: no redirects, rate limits waited out a few times (`throttled` says which answers are one)."""
    opener: Opener | None = None
    sleep: Callable[[float], None] = time.sleep

    def call(self, method: str, url: str, headers: Mapping[str, str], *, body: object = None,
             form: Mapping[str, str] | None = None, throttled: Callable[[Reply], bool] | None = None) -> Reply:
        data: bytes | None = None
        sent = {"Accept": "application/json", **headers}
        if form is not None:
            data = urllib.parse.urlencode(dict(form)).encode()
            sent["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            sent["Content-Type"] = "application/json"
        reply = Reply(None, {})
        for attempt in range(TRIES):
            reply = self._once(method, url, sent, data)
            limited = reply.status == 429 or (throttled is not None and throttled(reply))
            if not limited or attempt == TRIES - 1:
                return reply
            wait = reply.retry_after if reply.retry_after is not None else 2.0 * 2 ** attempt
            self.sleep(max(0.0, min(wait, MAX_WAIT)))
        return reply

    def _once(self, method: str, url: str, headers: Mapping[str, str], data: bytes | None) -> Reply:
        request = urllib.request.Request(url, data=data, method=method, headers=dict(headers))
        send = self.opener or cast(Opener, _OPENER.open)
        try:
            with send(request, timeout=TIMEOUT) as response:
                return Reply(int(getattr(response, "status", 200) or 200), _parse(cast(bytes, response.read())))
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
            except (OSError, http.client.HTTPException):
                raw = b""
            after: float | None = None
            try:
                after = float(str(exc.headers.get("Retry-After"))) if exc.headers and exc.headers.get("Retry-After") \
                    else None
            except ValueError:
                after = None
            return Reply(exc.code, _parse(raw), after)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            return Reply(None, {})


def _dict(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def _cents(units: object) -> int:
    """A platform's decimal amount ("12.345") as cents, half up."""
    try:
        return int((Decimal(str(units)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return 0


def _micros_cents(micros: object) -> int:
    """Google's cost in micros (millionths of the currency) as cents, half up."""
    try:
        value = int(str(micros))
    except ValueError:
        return 0
    return (value + 5000) // 10000 if value >= 0 else -((-value + 5000) // 10000)


@dataclass(frozen=True)
class Account:
    external_id: str
    name: str
    currency: str
    via: str = ""                         # Google: the manager to send as login-customer-id


@dataclass
class Fetched:
    accounts: list[Account] = field(default_factory=list[Account])
    spend: dict[str, list[SA.SpendRow]] = field(default_factory=dict[str, list[SA.SpendRow]])


# ------------------------------------------------------------------------------------------------ Google Ads
def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _google_error(reply: Reply) -> str:
    body = reply.body[0] if isinstance(reply.body, list) and reply.body else reply.body
    err = _dict(_dict(body).get("error"))
    message = str(err.get("message") or "")
    for detail in _list(err.get("details")):
        for e in _list(_dict(detail).get("errors")):
            message = str(_dict(e).get("message") or message)
            break
    return (message or f"HTTP {reply.status}")[:300]


def _google_throttled(reply: Reply) -> bool:
    body = reply.body[0] if isinstance(reply.body, list) and reply.body else reply.body
    return str(_dict(_dict(body).get("error")).get("status") or "") == "RESOURCE_EXHAUSTED"


def google_token(http_: Http, secrets: Mapping[str, str]) -> str:
    """An access token for this run, from the stored refresh token. Never stored."""
    reply = http_.call("POST", TOKEN_URL, {}, form={"client_id": secrets["client_id"],
                                                    "client_secret": secrets["client_secret"],
                                                    "refresh_token": secrets["refresh_token"],
                                                    "grant_type": "refresh_token"})
    token = str(_dict(reply.body).get("access_token") or "")
    if reply.status == 200 and token:
        return token
    if reply.status is None:
        raise Failed("Google's sign-in could not be reached")
    reason = str(_dict(reply.body).get("error") or f"HTTP {reply.status}")
    raise Reconnect(f"Google refused the refresh token ({reason})")


class _Google:
    def __init__(self, http_: Http, secrets: Mapping[str, str], token: str) -> None:
        self.http, self.secrets, self.token = http_, secrets, token

    def _headers(self, via: str) -> dict[str, str]:
        out = {"Authorization": "Bearer " + self.token, "developer-token": self.secrets["developer_token"]}
        if via:
            out["login-customer-id"] = via
        return out

    def _check(self, reply: Reply) -> None:
        if reply.status == 401:
            raise Reconnect("Google Ads refused the login (" + _google_error(reply) + ")")
        if reply.status is None:
            raise Failed("Google Ads could not be reached")

    def accessible(self) -> list[str]:
        reply = self.http.call("GET", GOOGLE_ADS + "/customers:listAccessibleCustomers", self._headers(""),
                               throttled=_google_throttled)
        self._check(reply)
        if reply.status != 200:
            raise Failed("Google Ads: " + _google_error(reply))
        return [_digits(str(n)) for n in _list(_dict(reply.body).get("resourceNames")) if _digits(str(n))]

    def search(self, customer: str, via: str, query: str) -> list[dict[str, Any]] | None:
        """Every row of a GAQL search (None when this customer cannot be read through `via`)."""
        rows: list[dict[str, Any]] = []
        token = ""
        while True:
            body: dict[str, object] = {"query": query}
            if token:
                body["pageToken"] = token
            reply = self.http.call("POST", f"{GOOGLE_ADS}/customers/{customer}/googleAds:search", self._headers(via),
                                   body=body, throttled=_google_throttled)
            self._check(reply)
            if reply.status != 200:
                if reply.status in (400, 403, 404):
                    return None
                raise Failed("Google Ads: " + _google_error(reply))
            found = _dict(reply.body)
            rows += [_dict(r) for r in _list(found.get("results"))]
            token = str(found.get("nextPageToken") or "")
            if not token:
                return rows

    def stream(self, customer: str, via: str, query: str) -> list[dict[str, Any]]:
        reply = self.http.call("POST", f"{GOOGLE_ADS}/customers/{customer}/googleAds:searchStream", self._headers(via),
                               body={"query": query}, throttled=_google_throttled)
        self._check(reply)
        if reply.status != 200:
            raise Failed(f"Google Ads account {customer}: " + _google_error(reply))
        batches = reply.body if isinstance(reply.body, list) else [reply.body]
        return [_dict(r) for b in cast(list[Any], batches) for r in _list(_dict(b).get("results"))]


CLIENTS = ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, "
           "customer_client.manager FROM customer_client")


def fetch_google(http_: Http, secrets: Mapping[str, str], start: str, end: str) -> Fetched:
    api = _Google(http_, secrets, google_token(http_, secrets))
    login = _digits(secrets.get("login_customer_id", ""))
    named = [_digits(x) for x in re.split(r"[,\s;]+", secrets.get("customer_ids", "")) if _digits(x)]
    roots = list(dict.fromkeys([*([login] if login else []), *named, *api.accessible()]))
    found: dict[str, Account] = {}
    for root in roots:
        via = login or root
        rows = api.search(root, via, CLIENTS)
        if rows is None and via != root:
            via = root
            rows = api.search(root, root, CLIENTS)
        for row in rows or []:
            cc = _dict(row.get("customerClient"))
            cid = _digits(str(cc.get("id") or ""))
            if not cid or cc.get("manager") is True or cid in found:
                continue
            found[cid] = Account(cid, str(cc.get("descriptiveName") or cid), str(cc.get("currencyCode") or "USD"), via)
    if not found and roots:
        raise Failed("Google Ads: this login sees no ad accounts")
    query = ("SELECT campaign.id, campaign.name, segments.date, metrics.cost_micros FROM campaign "
             f"WHERE segments.date BETWEEN '{start}' AND '{end}' AND metrics.cost_micros > 0")
    out = Fetched(accounts=list(found.values()))
    for acct in out.accounts:
        rows: list[SA.SpendRow] = []
        for row in api.stream(acct.external_id, acct.via, query):
            campaign, seg, metrics = _dict(row.get("campaign")), _dict(row.get("segments")), _dict(row.get("metrics"))
            day = str(seg.get("date") or "")
            if not day:
                continue
            rows.append(SA.SpendRow(day, str(campaign.get("id") or ""), str(campaign.get("name") or ""),
                                    _micros_cents(metrics.get("costMicros") or 0), acct.currency))
        out.spend[acct.external_id] = rows
    return out


# ------------------------------------------------------------------------------------------------ Meta
def _meta_error(reply: Reply) -> tuple[int, str]:
    err = _dict(_dict(reply.body).get("error"))
    try:
        code = int(err.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    return code, str(err.get("message") or f"HTTP {reply.status}")[:300]


def _meta_throttled(reply: Reply) -> bool:
    return _meta_error(reply)[0] in META_THROTTLE


class _Meta:
    def __init__(self, http_: Http, token: str) -> None:
        self.http, self.token = http_, token

    def pages(self, path: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        """Every row of a Graph list, following paging.next only on Graph's own host."""
        url = GRAPH + path + "?" + urllib.parse.urlencode(dict(params))
        rows: list[dict[str, Any]] = []
        while url:
            reply = self.http.call("GET", url, {"Authorization": "Bearer " + self.token}, throttled=_meta_throttled)
            code, message = _meta_error(reply)
            if reply.status is None:
                raise Failed("Meta could not be reached")
            if code in META_SESSION or reply.status == 401:
                raise Reconnect(f"Meta refused the access token ({message})")
            if reply.status != 200:
                raise Failed(f"Meta: {message}")
            found = _dict(reply.body)
            rows += [_dict(r) for r in _list(found.get("data"))]
            nxt = str(_dict(found.get("paging")).get("next") or "")
            parts = urllib.parse.urlsplit(nxt) if nxt else None
            url = nxt if parts is not None and parts.scheme == "https" and parts.hostname == GRAPH_HOST else ""
        return rows


def _windows(start: str, end: str, size: int) -> list[tuple[str, str]]:
    a, z = date.fromisoformat(start), date.fromisoformat(end)
    out: list[tuple[str, str]] = []
    while a <= z:
        b = min(z, a + timedelta(days=size - 1))
        out.append((a.isoformat(), b.isoformat()))
        a = b + timedelta(days=1)
    return out


def fetch_meta(http_: Http, secrets: Mapping[str, str], start: str, end: str) -> Fetched:
    api = _Meta(http_, secrets["access_token"])
    out = Fetched()
    for row in api.pages("/me/adaccounts", {"fields": "name,account_id,currency", "limit": "100"}):
        aid = _digits(str(row.get("account_id") or row.get("id") or ""))
        if aid and all(a.external_id != aid for a in out.accounts):
            out.accounts.append(Account(aid, str(row.get("name") or aid), str(row.get("currency") or "USD")))
    for acct in out.accounts:
        rows: list[SA.SpendRow] = []
        for a, z in _windows(start, end, META_WINDOW):
            for r in api.pages(f"/act_{acct.external_id}/insights",
                               {"level": "campaign", "time_increment": "1", "limit": "500",
                                "fields": "campaign_id,campaign_name,spend,date_start",
                                "time_range": json.dumps({"since": a, "until": z}, separators=(",", ":"))}):
                day = str(r.get("date_start") or "")
                if day:
                    rows.append(SA.SpendRow(day, str(r.get("campaign_id") or ""), str(r.get("campaign_name") or ""),
                                            _cents(r.get("spend") or "0"), acct.currency))
        out.spend[acct.external_id] = rows
    return out


FETCH: dict[str, Callable[[Http, Mapping[str, str], str, str], Fetched]] = {
    "google_ads": fetch_google, "meta_ads": fetch_meta}


# ------------------------------------------------------------------------------------------------ runs
def _today(now: str) -> date:
    return datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(timezone.utc).date()


def window(imp: SA.AdImport, now: str) -> tuple[str, str]:
    """The days a run reads: from history_from on a first run, else the last 30 days (never before history_from)."""
    today = _today(now)
    start = date.fromisoformat(imp.history_from)
    if imp.last_import_at:
        start = max(start, today - timedelta(days=RECENT_DAYS - 1))
    return min(start, today).isoformat(), today.isoformat()


def due(imp: SA.AdImport, now: str) -> bool:
    """Enabled and asked for, never run, or 4 hours past its last try."""
    if not imp.enabled:
        return False
    if imp.run_requested_at or imp.status == "never":
        return True
    last = imp.last_import_at if imp.status == "ok" else (imp.updated_at or imp.last_import_at)
    if not last:
        return True
    then = datetime.fromisoformat(last.replace("Z", "+00:00"))
    return datetime.fromisoformat(now.replace("Z", "+00:00")) - then >= EVERY


def ensure_vendor(c: Any, platform: str, at: str) -> str:
    """The platform's vendor contact ("Google Ads", "Meta"), made if no contact goes by the name. "" in mirrored books
    (their contacts come from the accounting provider) or with no book."""
    book = SL.book(c)
    if book is None or book.mode == "mirror":
        return ""
    name = VENDOR[platform]
    nk = L.name_key(name)
    owner = SAP.alias_of(c, book.id, nk)
    if owner:
        return owner
    row = c.execute("SELECT id FROM fin_contacts WHERE book_id=? AND kind IN ('vendor','both') AND lower(name)=? "
                    "ORDER BY created_at LIMIT 1", (book.id, nk)).fetchone()
    if row is not None:
        return str(row[0])
    cid = SAP.insert(c, "fin_contacts", {"book_id": book.id, "name": name, "kind": "vendor", "created_at": at,
                                         "created_by": SYSTEM, "updated_at": at})
    SAP.alias_add(c, book.id, nk, cid, at)
    return cid


@dataclass(frozen=True)
class Outcome:
    status: str
    accounts: int = 0
    rows: int = 0
    error: str = ""


def run(store: Any, vault: object, import_id: str, *, now: str | None = None, http_: Http | None = None) -> Outcome:
    """One import, outside any transaction while the platform answers."""
    at = now or tico.now()
    with store.read() as c:
        imp = next((i for i in SA.imports(c) if i.id == import_id), None)
        if imp is None:
            return Outcome("failed", error="no such import")
        try:
            secrets = _secrets(c, vault, imp)
        except Failed as exc:
            secrets, early = {}, str(exc)
        else:
            early = ""
    if early:
        with store.transaction() as c:
            SA.finish(c, imp.id, "failed", early, at, imported=False)
        return Outcome("failed", error=early)
    start, end = window(imp, at)
    try:
        fetched = FETCH[imp.platform](http_ or Http(), secrets, start, end)
    except Reconnect as exc:
        with store.transaction() as c:
            SA.finish(c, imp.id, "reconnect", str(exc), at, imported=False)
        return Outcome("reconnect", error=str(exc))
    except Failed as exc:
        with store.transaction() as c:
            SA.finish(c, imp.id, "failed", str(exc), at, imported=False)
        return Outcome("failed", error=str(exc))
    finally:
        secrets = {}
    stored = 0
    with store.transaction() as c:
        current = SA.import_for(c, imp.tool_id)
        if current is None or current.id != imp.id:
            return Outcome("failed", error="the import was removed")
        for acct in fetched.accounts:
            aid = SA.upsert_account(c, imp.tool_id, imp.platform, acct.external_id, acct.name, acct.currency, at)
            stored += SA.replace_spend(c, aid, start, end, fetched.spend.get(acct.external_id, []), at)
        SA.deactivate_missing(c, imp.tool_id, [a.external_id for a in fetched.accounts], at)
        ensure_vendor(c, imp.platform, at)
        # A history_from moved earlier while this ran wants a full read again: keep last_import_at empty then.
        full_again = current.history_from < imp.history_from
        SA.finish(c, imp.id, "ok", "", at, imported=not full_again)
    return Outcome("ok", accounts=len(fetched.accounts), rows=stored)


def pass_once(store: Any, vault: object, *, now: str | None = None, http_: Http | None = None) -> dict[str, Outcome]:
    """Run every due import, then give bots their budget warnings. {tool id: outcome} for the imports run."""
    at = now or tico.now()
    with store.read() as c:
        todo = [i for i in SA.imports(c) if due(i, at)]
    done: dict[str, Outcome] = {}
    for imp in todo:
        try:
            done[imp.tool_id] = run(store, vault, imp.id, now=at, http_=http_)
        except Exception:                                   # one platform's bug never stops the other's import
            log.exception("Finance ad spend import failed")
            done[imp.tool_id] = Outcome("failed", error="the import stopped unexpectedly")
            with store.transaction() as c:
                SA.finish(c, imp.id, "failed", "the import stopped unexpectedly", at, imported=False)
    return done


def settings_changed(imp: SA.AdImport, history_from: str) -> SA.AdImport:
    """An import whose history_from moved earlier reads its full history again on the next run."""
    if history_from < imp.history_from:
        return replace(imp, history_from=history_from, last_import_at=None)
    return replace(imp, history_from=history_from)


# ------------------------------------------------------------------------------------------------ the worker
BELL = Bell()
_worker: dict[str, Any] = {"thread": None, "stop": threading.Event(), "lock": threading.Lock()}


def ring() -> None:
    BELL.ring()


def start(store: Any, vault: object, *, rehearsal: bool = False) -> None:
    """Start the worker (or restart it if it died). A rehearsal copy calls no platform, so it starts none."""
    if rehearsal:
        return
    with _worker["lock"]:
        thread = _worker["thread"]
        if thread is not None and thread.is_alive():
            return
        if _worker["stop"].is_set():
            _worker["stop"] = threading.Event()
        made = threading.Thread(target=_loop, args=(store, vault, _worker["stop"]), name="finance-ads", daemon=True)
        made.start()
        _worker["thread"] = made


def stop() -> None:
    _worker["stop"].set()
    BELL.ring()
    thread = _worker["thread"]
    if thread is not None:
        thread.join(5)
    _worker["thread"] = None


def _loop(store: Any, vault: object, halt: threading.Event) -> None:
    while not halt.is_set():
        seen = BELL.rung
        try:
            pass_once(store, vault)
        except Exception:
            log.exception("Finance ad spend pass failed")
        BELL.wait(seen, SWEEP)
