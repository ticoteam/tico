"""The Tools pass-through proxy: `ANY /api/v2/tool-proxy/{tool_id}/{path}` (docs/credential-vault.md#proxied-tools).

A run whose tool is `delivery = 'proxy'` never receives the tool's key. `credential-runtime` hands it a proxy token in
the key's environment variable and the proxy's address in `TICO_TOOL_<ID>_URL`; the run calls the vendor's API through
that address with the token where the key would go. Here the token is checked, the key chosen by the caller's level
(backend/api_tools.py `key_for`), the vendor's auth rewritten per the tool's `auth_scheme`, and the call forwarded to
`base_url + path` with its query string. The answer streams back with the key scrubbed out of it, and one `tool_calls`
row records the call: its path and query keys, never a query value, a body or the key.

The token is the only credential: it carries no Tico identity, so the request guard (backend/app.py) lets this prefix
through to the route, which answers 401 for anything not signed here. A token is HMAC-SHA256 over its claims with a key
derived from the vault's data key, and names the tool, the grant subject and what it was minted for:
- `a`: a bot run's attempt; valid while that attempt is leased or running on its assigned computer (the same check a
  run token gets, `validate_identity`), never after.
- `r`: a watcher on a computer (`runner-watcher-credentials`); valid while that active bot is assigned there, for TTL.
- `g`: an external agent's standing credential (backend/agents.py); valid while it is not revoked, for TTL.
Every call also re-reads the subject's level, so taking a level away stops the next call, not the next run.

Refusals are the app's error envelope with a `detail` that names the one setting that would change it.
"""
import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import sqlite3
import time
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import outbound_tls
from .auth import Identity, validate_identity
from .store import H, Problem

PREFIX = '/api/v2/tool-proxy/'
TOKEN_PREFIX = 'tpx1.'
TOKEN_RE = re.compile(r'tpx1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
SAFE = frozenset(('GET', 'HEAD', 'OPTIONS'))
METHODS = ['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE']
MAX_BODY = 25 * 1024 * 1024
TTL = 12 * 3600                 # a watcher's or an agent's token; a run's lasts exactly as long as its attempt
TIMEOUT = httpx.Timeout(120.0, connect=10.0, pool=10.0)
LEVELS = {'read': 'Read', 'read_write': 'Read and write'}
# Never forwarded either way: hop-by-hop headers, the caller's own sign-in, and what a proxy in front of Tico added.
HOP = frozenset(('connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'trailers',
                 'transfer-encoding', 'upgrade', 'host', 'content-length'))
# The answer is decoded here to scrub it, so only the encodings httpx decodes are asked for.
REQUEST_DROP = frozenset(('authorization', 'cookie', 'forwarded', 'x-real-ip', 'true-client-ip', 'via',
                          'accept-encoding'))
REQUEST_DROP_PREFIXES = ('x-forwarded-', 'cf-', 'x-tico-', 'x-amzn-')
# The answer is served from Tico's own origin: nothing in it may set a cookie there, widen CORS or run as a page.
RESPONSE_DROP = frozenset(('set-cookie', 'content-encoding', 'content-security-policy', 'strict-transport-security',
                           'alt-svc'))
RESPONSE_DROP_PREFIXES = ('access-control-',)


def url_env(tool_id):
    """The variable a run reads the proxy's address from: `TICO_TOOL_CLOSE_CRM_URL` for `close-crm`."""
    return 'TICO_TOOL_' + re.sub(r'[^A-Z0-9]', '_', str(tool_id).upper()) + '_URL'


def proxy_url(settings, tool_id):
    """The proxy's address for one tool, on the address runs reach this server by."""
    return (settings.runner_url or settings.public_url).rstrip('/') + PREFIX + quote(str(tool_id), safe='')


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _signing_key(c, vault):
    return hmac.new(vault.cipher.key(c), b'tico-tool-proxy:v1', hashlib.sha256).digest()


def mint(c, vault, tool_id, subject, *, attempt='', runner='', agent=False, now=None):
    """A proxy token for `subject` on `tool_id`, bound to a run's attempt, else to a computer's watcher or an agent."""
    claims = {'t': tool_id, 's': subject}
    if attempt:
        claims['a'] = attempt
    else:
        claims['r' if runner else 'g'] = runner or '1'
        claims['e'] = int(now if now is not None else time.time()) + TTL
    body = _b64(json.dumps(claims, separators=(',', ':'), sort_keys=True).encode())
    return TOKEN_PREFIX + body + '.' + _b64(hmac.new(_signing_key(c, vault), body.encode(), hashlib.sha256).digest())


def proxied_tools(c):
    """{tool id: tool row} for every tool whose calls go through the proxy."""
    return {r['id']: r for r in c.execute("SELECT * FROM tools WHERE delivery='proxy'")}


def runtime_item(c, vault, settings, row, tool, subject, *, attempt='', runner='', agent=False):
    """What a run is handed for a held credential of a proxied tool: a proxy token where the key would be, and the
    proxy's address in `url_env`. Never the key."""
    token = mint(c, vault, tool['id'], subject, attempt=attempt, runner=runner, agent=agent)
    return {'id': row['id'], 'name': row['name'], 'env': row['env'], 'kind': row['kind'], 'value': token,
            'tool': tool['name'] or tool['id'], 'proxy_url': proxy_url(settings, tool['id']),
            'url_env': url_env(tool['id'])}


def claims_of(c, vault, token):
    """The claims of a token signed here, or None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    body, _, mac = token[len(TOKEN_PREFIX):].partition('.')
    try:
        signing = _signing_key(c, vault)
    except (Problem, sqlite3.Error):
        return None             # no data key yet (nothing was ever stored): nothing was ever signed either
    good = _b64(hmac.new(signing, body.encode(), hashlib.sha256).digest())
    if not body or not hmac.compare_digest(mac.encode(), good.encode()):
        return None
    try:
        claims = json.loads(_unb64(body))
    except ValueError:
        return None
    return claims if isinstance(claims, dict) and claims.get('t') and claims.get('s') else None


def holder_live(c, claims, now=None):
    """Whether what the token was minted for still holds: the attempt live on its computer, the watcher's bot still
    assigned there, the agent not revoked. The subject's level is checked separately, on every call."""
    subject = str(claims['s'])
    if 'a' not in claims and int(claims.get('e') or 0) <= int(now if now is not None else time.time()):
        return False
    try:
        if 'a' in claims:
            row = c.execute('SELECT id,bot_id,runner_id FROM attempts WHERE id=?', (str(claims['a']),)).fetchone()
            if not row or 'bot:' + row['bot_id'] != subject:
                return False
            validate_identity(c, Identity(subject, 'bot', runner_id=row['runner_id'] or '', attempt_id=row['id']))
        elif 'r' in claims:
            if not c.execute("SELECT 1 FROM runners r JOIN assignments a ON a.runner_id=r.id JOIN bots b ON b.id=a.bot "
                             "WHERE r.id=? AND r.revoked_at IS NULL AND 'bot:'||a.bot=? AND b.state='active'",
                             (str(claims['r']), subject)).fetchone():
                return False
        else:
            validate_identity(c, Identity(subject, 'bot', agent='agent'))
    except Problem:
        return False
    return True


def found_token(request, scheme):
    """The proxy token wherever the caller put it: Bearer, either half of Basic, the scheme's header or its query
    parameter. Returns (token, where), where is 'header' when it sat in the scheme's own header."""
    auth = request.headers.get('authorization', '')
    places = []
    kind, _, value = auth.partition(' ')
    if kind.lower() == 'basic':
        try:
            places.append((base64.b64decode(value.strip(), validate=True).decode('utf-8', 'replace'), 'auth'))
        except ValueError:
            pass
    elif auth:
        places.append((auth, 'auth'))
    if scheme.startswith('header:'):
        places.append((request.headers.get(scheme[7:], ''), 'header'))
    if scheme.startswith('query:'):
        places.append((request.query_params.get(scheme[6:], ''), 'query'))
    for text, where in places:
        match = TOKEN_RE.search(text or '')
        if match:
            return match.group(0), where
    return '', ''


def logged_path(rest, query):
    """What a tool_calls row keeps of a call's address: the path, and the query's keys with their values dropped."""
    path = '/' + rest.lstrip('/')
    keys = [k for k, _ in parse_qsl(query, keep_blank_values=True)]
    return (path + ('?' + '&'.join(quote(k, safe='') + '=' for k in keys) if keys else ''))[:500]


def address_problem(base_url, name):
    """Why a tool's address cannot be called through the proxy, or ''."""
    if not base_url:
        return f'{name} has no API address. Set its address on Tools > {name}.'
    parts = urlsplit(base_url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        return f"{name}'s API address must be an https:// address. Change it on Tools > {name}."
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return ''
    if not ip.is_global:
        return f"{name}'s API address is a private address. Change it on Tools > {name}."
    return ''


def secrets_of(key, username):
    """Every spelling of the key an answer could echo: itself and each Basic encoding it was sent in."""
    out = {key}
    for pair in (f'{key}:', f'{username}:{key}'):
        out.add(base64.b64encode(pair.encode()).decode())
    return sorted((s for s in out if s), key=len, reverse=True)


def scrub_text(text, secrets):
    for secret in secrets:
        text = text.replace(secret, '[key]')
    return text


class Scrubber:
    """Replaces the key in a streamed answer, holding back enough of each chunk's tail that a key split across two
    chunks is still caught."""

    def __init__(self, secrets):
        self.secrets = [s.encode() for s in secrets]
        self.keep = max((len(s) for s in self.secrets), default=1) - 1
        self.tail = b''

    def feed(self, chunk):
        data = self.tail + chunk
        for secret in self.secrets:
            data = data.replace(secret, b'[key]')
        if self.keep <= 0 or len(data) <= self.keep:
            self.tail = data if self.keep > 0 else b''
            return b'' if self.keep > 0 else data
        self.tail = data[-self.keep:]
        return data[:-self.keep]

    def end(self):
        data, self.tail = self.tail, b''
        return data


def outgoing_headers(request, tool, credential, key, where):
    """The caller's headers minus its sign-in and what proxies added, with the vendor's auth set from the key."""
    scheme = tool['auth_scheme'] or 'bearer'
    named = scheme[7:].lower() if scheme.startswith('header:') else ''
    out = []
    for name, value in request.headers.items():
        low = name.lower()
        if low in HOP or low in REQUEST_DROP or low.startswith(REQUEST_DROP_PREFIXES) or (named and low == named):
            continue
        out.append((name, value))
    out.append(('Accept-Encoding', 'gzip, deflate'))
    if scheme == 'bearer':
        out.append(('Authorization', 'Bearer ' + key))
    elif scheme == 'basic_user':
        out.append(('Authorization', 'Basic ' + base64.b64encode(f'{key}:'.encode()).decode()))
    elif scheme == 'basic_password':
        user = credential['username'] or ''
        out.append(('Authorization', 'Basic ' + base64.b64encode(f'{user}:{key}'.encode()).decode()))
    elif named:
        # "Token <token>" stays "Token <key>": only the token is replaced, whatever the vendor puts around it.
        sent = request.headers.get(named, '') if where == 'header' else ''
        out.append((scheme[7:], TOKEN_RE.sub(lambda _: key, sent) if TOKEN_RE.search(sent) else key))
    return out


def outgoing_query(query, tool, key):
    """The caller's query string, with the scheme's parameter (when the scheme is a query parameter) set to the key."""
    scheme = tool['auth_scheme'] or ''
    if not scheme.startswith('query:'):
        return query
    param = scheme[6:]
    kept = [part for part in query.split('&') if part and part.split('=', 1)[0] != quote(param, safe='')
            and part.split('=', 1)[0] != param]
    return '&'.join(kept + [quote(param, safe='') + '=' + quote(key, safe='')])


def refused(status, code, detail):
    return JSONResponse({'error': {'code': code, 'detail': detail, 'retryable': False}}, status_code=status,
                        headers={'x-content-type-options': 'nosniff'})


def install(app, store):
    vault = app.state.vault
    loop = asyncio.get_running_loop

    def record(tool_id, credential_id, subject, level, method, path, status, result, detail, began):
        with store.transaction() as c:
            c.execute('INSERT INTO tool_calls(id,tool_id,credential_id,actor,bot,access_level,method,path,status_code,'
                      'result,detail,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (H.new_id(), tool_id, credential_id, subject, subject[4:] if subject.startswith('bot:') else '',
                       level or '', method, path, status, result, detail[:300],
                       int((time.perf_counter() - began) * 1000), H.now()))
            if credential_id and result == 'sent':
                from .credentials import record_use
                record_use(c, subject, 'credential.proxied_call', credential_id, {'tool': tool_id}, windowed=True)

    def plan(request, tool_id, rest, method):
        """Everything the call needs, read once: (refusal, None) or (None, plan)."""
        with store.read() as c:
            tool = c.execute('SELECT * FROM tools WHERE id=?', (tool_id,)).fetchone()
            if not tool:
                return (401, 'identity', 'Invalid tool token', None), None
            token, where = found_token(request, tool['auth_scheme'] or 'bearer')
            claims = claims_of(c, vault, token)
            if not claims or claims['t'] != tool_id:
                return (401, 'identity', 'Invalid tool token', None), None
            subject = str(claims['s'])
            if not holder_live(c, claims):
                return (401, 'token_ended', 'This tool token has ended with the run it was made for. '
                        'A new run gets a new one.', None), None
            from . import api_tools
            name = tool['name'] or tool_id
            who = (H.bot(c, subject) or {}).get('display_name') if subject.startswith('bot:') else None
            who = who or (H.human(c, H.actor_id(subject)) or {}).get('name') or subject
            level = api_tools.subject_level(c, tool_id, subject)
            log = (subject, level)
            if not level:
                return (403, 'forbidden', f"{who} has no level on {name}. Set Read or Read and write for {who} "
                        f"on Tools > {name}.", log), None
            problem = address_problem(tool['base_url'] or '', name)
            if problem:
                return (403, 'tool_address', problem, log), None
            credential = api_tools.key_for(c, tool_id, subject)
            if not credential:
                return (403, 'no_key', f"{name} has no key for {LEVELS[level]}. Add one on Tools > {name}.", log), None
            if level == 'read' and credential['access_level'] != 'read' and method not in SAFE:
                return (403, 'forbidden', f"{who}'s level on {name} is Read: only GET calls. Add a read key or set "
                        "Read and write.", log), None
            key = vault.cipher.decrypt(c, credential)
            return None, {'tool': dict(tool), 'credential': dict(credential), 'key': key, 'where': where,
                          'subject': subject, 'level': level}

    @app.api_route(PREFIX + '{tool_id}/{path:path}', methods=METHODS, include_in_schema=False)
    async def tool_proxy(request: Request, tool_id: str, path: str):
        began = time.perf_counter()
        method = request.method.upper()
        raw = (request.scope.get('raw_path') or request.url.path.encode()).decode('latin-1')
        rest = raw.split('/', 5)[5] if raw.count('/') >= 5 else ''
        query = request.scope.get('query_string', b'').decode('latin-1')
        shown = logged_path(rest, query)
        if any(part.lower() in ('.', '..', '%2e', '%2e%2e', '.%2e', '%2e.') for part in rest.split('/')):
            return refused(404, 'not_found', 'Not found')
        refusal, ready = await loop().run_in_executor(None, plan, request, tool_id, rest, method)
        if refusal:
            status, code, detail, log = refusal
            if log:
                await loop().run_in_executor(None, record, tool_id, None, log[0], log[1], method, shown, status,
                                             'refused', detail, began)
            return refused(status, code, detail)
        tool, credential, key = ready['tool'], ready['credential'], ready['key']
        secrets = secrets_of(key, credential.get('username') or '')
        log = lambda status, result, detail: loop().run_in_executor(  # noqa: E731
            None, record, tool_id, credential['id'], ready['subject'], ready['level'], method, shown, status, result,
            scrub_text(detail, secrets), began)
        size = request.headers.get('content-length', '')
        if size.isdigit() and int(size) > MAX_BODY:
            await log(413, 'refused', 'Request body over 25 MB')
            return refused(413, 'too_large', 'A tool call sends at most 25 MB.')
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BODY:
                await log(413, 'refused', 'Request body over 25 MB')
                return refused(413, 'too_large', 'A tool call sends at most 25 MB.')
            chunks.append(chunk)
        url = tool['base_url'].rstrip('/') + '/' + rest.lstrip('/')
        query = outgoing_query(query, tool, key)
        url += ('?' + query) if query else ''
        if urlsplit(url).hostname != urlsplit(tool['base_url']).hostname:
            await log(None, 'refused', 'Address left the tool host')
            return refused(404, 'not_found', 'Not found')
        client = httpx.AsyncClient(transport=getattr(app.state, 'tool_proxy_transport', None), timeout=TIMEOUT,
                                   follow_redirects=False, trust_env=False,
                                   verify=outbound_tls.context(trust_env=False))
        try:
            upstream = await client.send(client.build_request(
                method, url, headers=outgoing_headers(request, tool, credential, key, ready['where']),
                content=b''.join(chunks)), stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            detail = scrub_text(f'{type(exc).__name__}: {exc}', secrets)[:200]
            await log(None, 'failed', detail)
            return refused(502, 'tool_unreachable', f"{tool['name'] or tool_id} did not answer: {detail}")
        await log(upstream.status_code, 'sent', '')
        headers = [(name, scrub_text(value, secrets)) for name, value in upstream.headers.multi_items()
                   if name.lower() not in HOP and name.lower() not in RESPONSE_DROP
                   and not name.lower().startswith(RESPONSE_DROP_PREFIXES)]
        scrubber = Scrubber(secrets)

        async def body():
            try:
                async for chunk in upstream.aiter_bytes():
                    out = scrubber.feed(chunk)
                    if out:
                        yield out
                tail = scrubber.end()
                if tail:
                    yield tail
            finally:
                await upstream.aclose()
                await client.aclose()

        response = StreamingResponse(body(), status_code=upstream.status_code)
        response.raw_headers = [(k.lower().encode('latin-1'), v.encode('latin-1', 'replace')) for k, v in headers] + [
            (b'x-content-type-options', b'nosniff'), (b'content-security-policy', b"sandbox; default-src 'none'")]
        return response
