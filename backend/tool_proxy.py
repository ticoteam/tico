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

At Read with only a Read and write key, the proxy lets through GET, HEAD and OPTIONS, and a POST only as MCP calls that
read (`MCP_READS`, and `tools/call` of a tool the server's own tools/list marked `readOnlyHint`). A bot held at Read on a
tool whose keys go to computers is sent here too, when the tool has an API address (backend/api_tools.py
`computer_way`).

Refusals are the app's error envelope with a `detail` that names the one setting that would change it.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import sqlite3
import time
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import egress, outbound_tls, rule_links
from .auth import Identity, validate_identity
from .store import H, Problem

LOG = logging.getLogger('tico.tool_proxy')
PREFIX = '/api/v2/tool-proxy/'
TOKEN_PREFIX = 'tpx1.'
TOKEN_RE = re.compile(r'tpx1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
SAFE = frozenset(('GET', 'HEAD', 'OPTIONS'))
METHODS = ['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE']
MAX_BODY = 25 * 1024 * 1024
# MCP over HTTP is JSON-RPC in POST bodies. At Read with only a Read and write key these calls pass, as reads: the
# session's own set-up and listings, and `tools/call` of a tool the server's own tools/list marked readOnlyHint.
MCP_READS = frozenset(
    (
        'initialize',
        'ping',
        'tools/list',
        'resources/list',
        'resources/templates/list',
        'resources/read',
        'prompts/list',
        'prompts/get',
        'completion/complete',
        'logging/setLevel',
    )
)
READ_ONLY_TOOLS = {}  # {tool id: {MCP tool name}}: what the vendor's latest tools/list marked read-only
# The same, learned only from a tools/list sent to exactly the tool's MCP address (backend/rule_links.mcp_endpoint): the
# hints the outside-write gate trusts. Read authorization keeps READ_ONLY_TOOLS, as before.
GATE_READ_ONLY = {}
MAX_LISTING = 4 * 1024 * 1024
MAX_NAMES = 5000
TTL = 12 * 3600  # a watcher's or an agent's token; a run's lasts exactly as long as its attempt
TIMEOUT = httpx.Timeout(120.0, connect=10.0, pool=10.0)
RESOLVE_BUDGET = 10.0
# Never forwarded either way: hop-by-hop headers, the caller's own sign-in, and what a proxy in front of Tico added.
HOP = frozenset(
    (
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailer',
        'trailers',
        'transfer-encoding',
        'upgrade',
        'host',
        'content-length',
    )
)
# The answer is decoded here to scrub it, so only the encodings httpx decodes are asked for.
REQUEST_DROP = frozenset(
    ('authorization', 'cookie', 'forwarded', 'x-real-ip', 'true-client-ip', 'via', 'accept-encoding')
)
REQUEST_DROP_PREFIXES = ('x-forwarded-', 'cf-', 'x-tico-', 'x-amzn-')
# The answer is served from Tico's own origin: nothing in it may set a cookie there, widen CORS or run as a page.
RESPONSE_DROP = frozenset(
    ('set-cookie', 'content-encoding', 'content-security-policy', 'strict-transport-security', 'alt-svc')
)
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


def home_of(row, tool):
    """The tool id a key's proxy address names: the tool it was on before it was grouped onto its vendor account's tool
    (`credentials.former_tool_id`, backend/tool_templates.py), so a run's `TICO_TOOL_<ID>_URL` never changes, else its
    own tool's."""
    try:
        former = row['former_tool_id']
    except (IndexError, KeyError):
        former = None
    return former or tool['id']


def tool_for(c, tool_id):
    """The tools row a proxy address names: the tool, or the tool a grouped tool's key was moved onto from it."""
    tool = c.execute('SELECT * FROM tools WHERE id=?', (tool_id,)).fetchone()
    if tool is not None:
        return tool
    try:
        moved = c.execute(
            'SELECT tool_id FROM credentials WHERE former_tool_id=? AND tool_id IS NOT NULL LIMIT 1', (tool_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # a file from before grouping
    return c.execute('SELECT * FROM tools WHERE id=?', (moved[0],)).fetchone() if moved else None


def proxied_tools(c):
    """{tool id: tool row} for every tool whose calls go through the proxy."""
    return {r['id']: r for r in c.execute("SELECT * FROM tools WHERE delivery='proxy'")}


def runtime_item(c, vault, settings, row, tool, subject, *, attempt='', runner='', agent=False):
    """What a run is handed for a held credential of a proxied tool: a proxy token where the key would be, and the
    proxy's address in `url_env`. Never the key."""
    token = mint(c, vault, tool['id'], subject, attempt=attempt, runner=runner, agent=agent)
    home = home_of(row, tool)
    return {
        'id': row['id'],
        'name': row['name'],
        'env': row['env'],
        'kind': row['kind'],
        'value': token,
        'tool': tool['name'] or tool['id'],
        'proxy_url': proxy_url(settings, home),
        'url_env': url_env(home),
        # The vendor address the proxy forwards to: the runner sends a declared MCP server under it through the proxy.
        'base_url': tool['base_url'] or '',
    }


def claims_of(c, vault, token):
    """The claims of a token signed here, or None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    body, _, mac = token[len(TOKEN_PREFIX) :].partition('.')
    try:
        signing = _signing_key(c, vault)
    except (Problem, sqlite3.Error):
        return None  # no data key yet (nothing was ever stored): nothing was ever signed either
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
            if not c.execute(
                "SELECT 1 FROM runners r JOIN assignments a ON a.runner_id=r.id JOIN bots b ON b.id=a.bot "
                "WHERE r.id=? AND r.revoked_at IS NULL AND 'bot:'||a.bot=? AND b.state='active'",
                (str(claims['r']), subject),
            ).fetchone():
                return False
        else:
            validate_identity(c, Identity(subject, 'bot', agent='agent'))
    except Problem:
        return False
    return True


def found_token(request, scheme, names=(), params=()):
    """The proxy token wherever the caller put it: Bearer, either half of Basic, the scheme's header or its query
    parameter, or any header or parameter a template's ways send (`names`, `params`). Returns (token, where), where is
    'header' when it sat in the scheme's own header."""
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
    places += [(request.headers.get(n, ''), 'template') for n in names if n != 'authorization']
    places += [(request.query_params.get(p, ''), 'query') for p in params]
    for text, where in places:
        match = TOKEN_RE.search(text or '')
        if match:
            return match.group(0), where
    return '', ''


def rpc_calls(body):
    """[(method or None, params)] for a JSON-RPC body, one message or a batch; None when the body is not JSON-RPC. A
    message without a method is the client's answer to the server (None)."""
    try:
        data = json.loads(body or b'')
    except ValueError:
        return None
    items = data if isinstance(data, list) else [data]
    if not items or not all(isinstance(m, dict) and m.get('jsonrpc') == '2.0' for m in items):
        return None
    out = []
    for m in items:
        method = m.get('method')
        if method is None and ('result' in m or 'error' in m):
            out.append((None, {}))
        elif isinstance(method, str):
            out.append((method, m.get('params') if isinstance(m.get('params'), dict) else {}))
        else:
            return None
    return out


def mcp_write(tool_id, calls, known=READ_ONLY_TOOLS):
    """'' when every call only reads, else the first call that may write, named for the refusal."""
    for method, params in calls:
        if method is None or method in MCP_READS or method.startswith('notifications/'):
            continue
        if method == 'tools/call':
            name = str(params.get('name') or '')
            if name and name in known.get(tool_id, ()):
                continue
            return f'the MCP tool {name or "(unnamed)"}, which its server does not mark read-only'
        return f'the MCP call {method}'
    return ''


def learn_read_only(tool_id, data, into=READ_ONLY_TOOLS):
    """Note which tools a tools/list answer (JSON, or server-sent `data:` lines) marks `readOnlyHint`; a tool listed
    without it is taken off."""
    messages = []
    try:
        messages.append(json.loads(data))
    except ValueError:
        for line in data.splitlines():
            if line.startswith(b'data:'):
                try:
                    messages.append(json.loads(line[5:]))
                except ValueError:
                    continue
    known = into.setdefault(tool_id, set())
    for m in messages:
        tools = ((m or {}).get('result') or {}).get('tools') if isinstance(m, dict) else None
        for t in tools if isinstance(tools, list) else []:
            if not isinstance(t, dict) or not isinstance(t.get('name'), str):
                continue
            if (t.get('annotations') or {}).get('readOnlyHint') is True and len(known) < MAX_NAMES:
                known.add(t['name'])
            else:
                known.discard(t['name'])


def logged_path(rest, query):
    """What a tool_calls row keeps of a call's address: the path, and the query's keys with their values dropped."""
    path = '/' + rest.lstrip('/')
    keys = [k for k, _ in parse_qsl(query, keep_blank_values=True)]
    return (path + ('?' + '&'.join(quote(k, safe='') + '=' for k in keys) if keys else ''))[:500]


def address_problem(base_url, name):
    """Why a tool's address cannot be called through the proxy, by its spelling alone (no lookup), or ''."""
    if not base_url:
        return f'{name} has no API address. Set its address on Tools > {name}.'
    try:
        egress.check(base_url)
    except egress.Blocked as exc:
        return f'{name}\'s API address is refused: {exc.reason}. Change it on Tools > {name}.'
    return ''


def pinned(url, name):
    """(url to connect to, Host, SNI name) for `url`: the name resolved now, refused (egress.Blocked, its reason naming
    the setting) unless every address it resolves to is public, and pinned to the address checked so a second lookup
    (DNS rebinding) cannot send the call elsewhere. Blocking: run it off the event loop."""
    parts = egress.check(url)
    try:
        address = egress.address_for(parts, budget=RESOLVE_BUDGET)
    except egress.Blocked as exc:
        raise egress.Blocked(f'{name}\'s API address is refused: {exc.reason}. Change it on Tools > {name}.') from None
    host = parts.hostname + (f':{parts.port}' if parts.port else '')
    return httpx.URL(url).copy_with(host=address), host, parts.hostname


def secrets_of(key, username, more=()):
    """Every spelling of the key an answer could echo: itself and each Basic encoding it was sent in."""
    out = {key, *more}
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
        self.tail = data[-self.keep :]
        return data[: -self.keep]

    def end(self):
        data, self.tail = self.tail, b''
        return data


def outgoing_headers(request, tool, credential, key, where, auth=None):
    """The caller's headers minus its sign-in and what proxies added, with the vendor's auth set from the key, or, for
    a tool with a template, the headers its template built from every field (`auth`)."""
    scheme = tool['auth_scheme'] or 'bearer'
    named = scheme[7:].lower() if scheme.startswith('header:') and auth is None else ''
    dropped = {k.lower() for k in auth} if auth is not None else set()
    out = []
    for name, value in request.headers.items():
        low = name.lower()
        if low in HOP or low in REQUEST_DROP or low.startswith(REQUEST_DROP_PREFIXES) or (named and low == named):
            continue
        if low in dropped:
            continue
        out.append((name, value))
    out.append(('Accept-Encoding', 'gzip, deflate'))
    if auth is not None:
        out += list(auth.items())
    elif scheme == 'bearer':
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


def outgoing_query(query, tool, key, params=None):
    """The caller's query string, with the scheme's parameter (when the scheme is a query parameter) set to the key, or,
    for a tool with a template, each parameter its template built (`params`) set."""
    if params is None:
        scheme = tool['auth_scheme'] or ''
        if not scheme.startswith('query:'):
            return query
        params = {scheme[6:]: key}
    if not params:
        return query
    names = set(params) | {quote(p, safe='') for p in params}
    kept = [part for part in query.split('&') if part and part.split('=', 1)[0] not in names]
    return '&'.join(kept + [quote(p, safe='') + '=' + quote(v, safe='') for p, v in params.items()])


def read_only_detail(who, name, mcp=''):
    if mcp:
        return (
            f"{who}'s level on {name} is Read: GET calls and MCP calls that only read, and {mcp} may write. "
            'Add a read key or set Read and write.'
        )
    return f"{who}'s level on {name} is Read: only GET calls. Add a read key or set Read and write."


def refused(status, code, detail):
    return JSONResponse(
        {'error': {'code': code, 'detail': detail, 'retryable': False}},
        status_code=status,
        headers={'x-content-type-options': 'nosniff'},
    )


def install(app, store):
    vault = app.state.vault
    loop = asyncio.get_running_loop

    def record(tool_id, credential_id, subject, level, method, path, status, result, detail, began, gate=None):
        with store.transaction() as c:
            c.execute(
                'INSERT INTO tool_calls(id,tool_id,credential_id,actor,bot,access_level,method,path,status_code,'
                'result,detail,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    H.new_id(),
                    tool_id,
                    credential_id,
                    subject,
                    subject[4:] if subject.startswith('bot:') else '',
                    level or '',
                    method,
                    path,
                    status,
                    result,
                    detail[:300],
                    int((time.perf_counter() - began) * 1000),
                    H.now(),
                ),
            )
            if credential_id and result == 'sent':
                from .credentials import record_use
                from . import reconnect_tasks

                record_use(c, subject, 'credential.proxied_call', credential_id, {'tool': tool_id}, windowed=True)
                reconnect_tasks.called(c, tool_id, status)  # Key rejected opens its Reconnect task, Working ends it
            rule_links.record(c, gate)  # the write's rule check, in the same transaction as its row (in a savepoint)

    def gated(subject, tool_id, method, rest, query, body, params, content_type):
        try:
            if body is None:  # over rule_links.MAX_JUDGED: never copied or read, recorded unchecked
                return rule_links.unchecked(subject, subject, 'tool:' + tool_id, 'tool_call', 'too_large')
            output = rule_links.proxy_output(method, rest, query, body, params, content_type)
            return rule_links.check_as(
                store, getattr(app.state, 'judge', None), subject, subject, '', 'tool:' + tool_id, 'tool_call', output
            )
        except Exception:  # log-only: a failure to check never stops the write
            LOG.exception('The rule check of a proxied write failed')
            return None

    def plan(request, tool_id, rest, method):
        """Everything the call needs, read once: (refusal, None) or (None, plan)."""
        with store.read() as c:
            tool = tool_for(c, tool_id)
            if not tool:
                return (401, 'identity', 'Invalid tool token', None), None
            from . import tool_templates

            spec = tool_templates.spec_of(tool)
            names, params = tool_templates.auth_names(spec) if spec else ((), ())
            token, where = found_token(request, tool['auth_scheme'] or 'bearer', names, params)
            claims = claims_of(c, vault, token)
            if not claims or claims['t'] != tool['id']:
                return (401, 'identity', 'Invalid tool token', None), None
            subject = str(claims['s'])
            if not holder_live(c, claims):
                return (
                    401,
                    'token_ended',
                    'This tool token has ended with the run it was made for. A new run gets a new one.',
                    None,
                ), None
            from . import api_tools

            name = tool['name'] or tool_id
            tool_id = tool['id']
            auth = None
            try:
                # No level, or no stored key for it: refused with the setting that would allow it (api_tools).
                if spec and spec.get('ways'):
                    auth = tool_templates.proxy_auth(c, vault, tool, spec, subject)
                    credential, level = auth['credential'], auth['level']
                else:
                    credential, level = api_tools.key_for(c, tool_id, subject)
            except Problem as exc:
                return (exc.status, exc.code, exc.detail, (subject, '', tool_id)), None
            log = (subject, level, tool_id)
            problem = address_problem(tool['base_url'] or '', name)
            if problem:
                return (403, 'tool_address', problem, log), None
            # A sign-in (backend/mcp_connect.py) is a Read and write key too: a Read caller is held to MCP reads.
            reads_only = auth['reads_only'] if auth else level == 'read' and credential['access_level'] != 'read'
            who = api_tools._label(c, subject) if reads_only else ''
            if reads_only and method not in SAFE and method != 'POST':
                return (403, 'forbidden', read_only_detail(who, name), log), None
            # An MCP tool's sign-in has no stored key: its access token is fetched below.
            key = None if credential['kind'] == 'oauth' else vault.cipher.decrypt(c, credential)
            scheme = tool['auth_scheme'] or ''
            return None, {
                'auth': auth,
                # Where a caller may put the key in the query: blanked from what the org rule check reads.
                'auth_params': {*params, *([scheme[6:]] if scheme.startswith('query:') else [])},
                'tool': dict(tool),
                'credential': dict(credential),
                'key': key,
                'where': where,
                'subject': subject,
                'level': level,
                'reads_only': reads_only,
                'who': who,
                'name': name,
            }

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
                await loop().run_in_executor(
                    None, record, log[2], None, log[0], log[1], method, shown, status, 'refused', detail, began
                )
            return refused(status, code, detail)
        assert ready is not None  # plan answers a refusal or a plan
        tool, credential, key = ready['tool'], ready['credential'], ready['key']
        tool_id, auth = tool['id'], ready['auth']
        got = ready or {}  # the plan, typed as never None past the refusal above
        if key is None:
            from . import mcp_connect

            try:
                key = await loop().run_in_executor(None, mcp_connect.access_token, store, vault, credential['id'])
            except mcp_connect.Failed as exc:
                name = tool['name'] or tool_id
                again = isinstance(exc, mcp_connect.SignInNeeded)
                detail = (
                    f'{name} needs a new sign-in: Tools > {name} > Sign in.'
                    if again
                    else f'{name}\'s sign-in server did not answer ({exc.reason}).'
                )
                await loop().run_in_executor(
                    None,
                    record,
                    tool_id,
                    credential['id'],
                    got['subject'],
                    got['level'],
                    method,
                    shown,
                    401 if again else 502,
                    'refused' if again else 'failed',
                    detail,
                    began,
                )
                return refused(401 if again else 502, 'tool_sign_in', detail)
        secrets = secrets_of(key, credential.get('username') or '', auth['secrets'] if auth else ())
        checked = {}  # the write's rule check, written once with the row its call leaves (`record`)
        log = lambda status, result, detail: loop().run_in_executor(
            None,
            record,
            tool_id,
            credential['id'],
            ready['subject'],
            ready['level'],
            method,
            shown,
            status,
            result,
            scrub_text(detail, secrets),
            began,
            checked.pop('found', None),
        )
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
        # Read authorization parses a Read caller's POST whatever its size, as before; the gate reads it only up to its
        # 1 MB limit (`gate_calls`), and a larger one is not parsed for it at all (its check is too_large).
        small = total <= rule_links.MAX_JUDGED
        calls = rpc_calls(b''.join(chunks)) if method == 'POST' and (small or ready['reads_only']) else None
        gate_calls = calls if small else None
        if ready['reads_only'] and method == 'POST':
            # A POST passes at Read only as MCP calls that only read.
            write = mcp_write(tool_id, calls) if calls is not None else 'not MCP'
            if write:
                detail = read_only_detail(ready['who'], ready['name'], '' if calls is None else write)
                await log(403, 'refused', detail)
                return refused(403, 'forbidden', detail)
        listing = bool(calls) and any(m == 'tools/list' for m, _ in calls)
        asked = query
        url = tool['base_url'].rstrip('/') + '/' + rest.lstrip('/')
        query = outgoing_query(query, tool, key, auth['query'] if auth else None)
        url += ('?' + query) if query else ''
        # The gate's MCP read exemption, and the read-only hints it trusts, need the final address (the key's own query
        # parameters included) to be exactly the tool's MCP address.
        mcp = rule_links.mcp_endpoint(tool, url)
        if urlsplit(url).hostname != urlsplit(tool['base_url']).hostname:
            await log(None, 'refused', 'Address left the tool host')
            return refused(404, 'not_found', 'Not found')
        try:
            target, host, sni = await loop().run_in_executor(None, pinned, url, tool['name'] or tool_id)
        except egress.Blocked as exc:
            await log(None, 'refused', exc.reason)
            return refused(403, 'tool_address', exc.reason)
        if method not in SAFE and not (
            mcp and gate_calls is not None and not mcp_write(tool_id, gate_calls, GATE_READ_ONLY)
        ):
            # The destination checked and pinned, a write meets the org rules on what the caller sent, scrubbed, before
            # it is forwarded with the key (backend/rule_links.py). Log-only: its one outcome event on the tool's link
            # goes with the call's own row, after it is forwarded.
            idem = request.headers.get('idempotency-key', '')
            if not idem or len(idem) > 200:
                # No usable Idempotency-Key: not judged (a retry could not be told from a new write), recorded
                # unchecked, and forwarded as before.
                subject = got['subject']
                checked['found'] = rule_links.unchecked(subject, subject, 'tool:' + tool_id, 'tool_call', 'no_key')
            else:
                sent = b''.join(chunks) if total <= rule_links.MAX_JUDGED else None
                kind = request.headers.get('content-type', '')
                checked['found'] = await loop().run_in_executor(
                    None, gated, got['subject'], tool_id, method, rest, asked, sent, got['auth_params'], kind
                )
        client = httpx.AsyncClient(
            transport=getattr(app.state, 'tool_proxy_transport', None),
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            verify=outbound_tls.context(trust_env=False),
        )
        forward = lambda key: client.send(
            client.build_request(
                method,
                target,
                headers=[
                    ('Host', host),
                    *outgoing_headers(request, tool, credential, key, got['where'], auth['headers'] if auth else None),
                ],
                content=b''.join(chunks),
                extensions={'sni_hostname': sni},
            ),
            stream=True,
        )
        from . import api_tools

        try:
            upstream = await forward(key)
            if upstream.status_code == 401 and credential['kind'] == 'oauth':
                # The server ended the access token before its time: refreshed once, and the call sent again.
                from . import mcp_connect

                await upstream.aclose()
                try:
                    key = await loop().run_in_executor(
                        None, lambda: mcp_connect.access_token(store, vault, credential['id'], force=True)
                    )
                except mcp_connect.Failed:
                    key = None
                if key is None:
                    name = tool['name'] or tool_id
                    await client.aclose()
                    await log(401, 'refused', f'{name} needs a new sign-in')
                    return refused(401, 'tool_sign_in', f'{name} needs a new sign-in: Tools > {name} > Sign in.')
                secrets = secrets_of(key, '')
                upstream = await forward(key)
            # A 401's first bytes are read before its row is written, so the row can say whether the reply names a missing
            # scope. They are relayed first, exactly as they came; nothing else is read ahead.
            stream = upstream.aiter_bytes()
            ahead, seen = [], 0
            if upstream.status_code == 401:
                async for chunk in stream:
                    ahead.append(chunk)
                    seen += len(chunk)
                    if seen >= api_tools.SCOPE_READ:
                        break
        except httpx.HTTPError as exc:
            await client.aclose()
            detail = scrub_text(f'{type(exc).__name__}: {exc}', secrets)[:200]
            await log(None, 'failed', detail)
            return refused(502, 'tool_unreachable', f"{tool['name'] or tool_id} did not answer: {detail}")
        scoped = upstream.status_code == 401 and api_tools.said_missing_scope(
            upstream.headers.get('www-authenticate', ''), b''.join(ahead)
        )
        await log(upstream.status_code, 'sent', api_tools.MISSING_SCOPE if scoped else '')
        headers = [
            (name, scrub_text(value, secrets))
            for name, value in upstream.headers.multi_items()
            if name.lower() not in HOP
            and name.lower() not in RESPONSE_DROP
            and not name.lower().startswith(RESPONSE_DROP_PREFIXES)
        ]
        scrubber = Scrubber(secrets)
        heard = [] if listing else None

        async def relayed():
            for chunk in ahead:
                yield chunk
            async for chunk in stream:
                yield chunk

        async def body():
            try:
                async for chunk in relayed():
                    if heard is not None and sum(map(len, heard)) < MAX_LISTING:
                        heard.append(chunk)
                    out = scrubber.feed(chunk)
                    if out:
                        yield out
                tail = scrubber.end()
                if tail:
                    yield tail
                if heard:
                    learn_read_only(tool_id, b''.join(heard))
                    if mcp:
                        learn_read_only(tool_id, b''.join(heard), GATE_READ_ONLY)
            finally:
                await upstream.aclose()
                await client.aclose()

        response = StreamingResponse(body(), status_code=upstream.status_code)
        response.raw_headers = [(k.lower().encode('latin-1'), v.encode('latin-1', 'replace')) for k, v in headers] + [
            (b'x-content-type-options', b'nosniff'),
            (b'content-security-policy', b"sandbox; default-src 'none'"),
        ]
        return response
