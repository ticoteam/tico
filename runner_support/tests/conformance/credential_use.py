"""What a bot does with the credentials its turn was given, for the fakes (fake_claude.py, fake_codex.py) in the
credential delivery suite (runner_support/tests/test_credential_delivery.py). A prompt line starting with a marker
makes the turn use a credential the way a real bot would, from the environment and MCP configuration the runner
launched the harness with:

- `CALL <auth> <ENV> <url> [+wrong|+bare]`: one GET to `url` with the value of $ENV as `auth` says: `bearer`,
  `header:<Name>`, `query:<param>`, `basic:<username>` or `digest:<username>`. A `url` starting with `$NAME` starts
  from that variable (a proxied tool's `$TICO_TOOL_<ID>_URL`), so the same line calls the vendor directly or through the
  Tools proxy; `${NAME}` elsewhere in it is that variable's value. `+wrong` sends the request again with the value
  changed and `+bare` without it, its status kept as `refused`: proof that the service checks what it was sent.
- `SIGV4 <ID_ENV> <SECRET_ENV> <region>/<service> <url>`: one GET signed with AWS Signature Version 4.
- `OAUTH1 <KEY_ENV> <SECRET_ENV> [<TOKEN_ENV> <TOKEN_SECRET_ENV>] <url>`: one GET signed with OAuth 1.0a HMAC-SHA1.
- `HAWK <ID_ENV> <KEY_ENV> <url>`: one GET with a Hawk (sha256) Authorization header.
- `MTLS <CERT_ENV> <PASSPHRASE_ENV> <url>`: one GET presenting the client certificate in the file $CERT_ENV names, its
  private key opened with $PASSPHRASE_ENV (`-` for none).
- `HMAC <ENV> <hex> <payload>`: whether `hex` is the HMAC-SHA256 of `payload` (the rest of the line) under $ENV, as a
  webhook receiver checks a delivery's signature.
- `IMAP <host> <user> <ENV>`: an IMAP login over TLS with $ENV as the password, then LIST.
- `SESSION <ENV> <user> <login url> <me url>`: a JSON login with $ENV as the password, then a GET to `me url` with
  the session cookie it set.

Each of SIGV4, OAUTH1, HAWK, MTLS, HMAC, IMAP and SESSION also tries once with the secret changed (MTLS: with no
client certificate) and keeps that answer as `refused`. They use the standard library only: what a bot would write.
- `FILE <ENV> <url>`: reads the file $ENV names (a `file` credential) and POSTs its bytes to `url`.
- `MCPCALL <server>`: connects to the named server of the harness's MCP configuration with the headers it gives
  (`servers`, resolved by the fake as its harness does) and sends initialize, tools/list and one tools/call.
  `MCPCALL <server> <tool> <json arguments>` calls that tool with those arguments instead of the first with an order.
- `LOGIN <site>`: signs in to a website tool with the bot-side commands: `tico browser open`, `tico login fill` for
  the username, password and code, then `tico browser click` to submit.
- `ECHO <ENV>`: says the value of $ENV in the reply, as a careless bot would, so the transcript's redaction is tested.

Each use appends one record to $FAKE_LOG (`{"use": ..., ...}`: status codes, the answer's body, the turn's whole
environment) and adds one line to the reply. A prompt with no marker changes nothing."""

import base64
import hashlib
import hmac
import http.cookiejar
import imaplib
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

MARKER = re.compile(r"^(CALL|FILE|MCPCALL|LOGIN|ECHO|SIGV4|OAUTH1|HAWK|MTLS|HMAC|IMAP|SESSION) +(.+)$")
AGENT = "acme-bot-fake/1"


def _send(url, data=None, headers=None, opener=None, context=None):
    """(status, body text, headers) of one request; an HTTP error is an answer too, and no answer is status 0."""
    request = urllib.request.Request(url, data=data, headers={"User-Agent": AGENT, **(headers or {})})
    try:
        opened = (
            opener.open(request, timeout=20) if opener else urllib.request.urlopen(request, timeout=20, context=context)
        )
        with opened as answer:
            return answer.status, answer.read().decode("utf-8", "replace"), dict(answer.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace"), dict(error.headers)
    except OSError as error:
        return 0, f"{type(error).__name__}: {error}", {}


def _url(text):
    found = re.match(r"^\$([A-Z_][A-Z0-9_]*)(.*)$", text)
    text = (os.environ.get(found.group(1), "") + found.group(2)) if found else text
    return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), text)


def _wrong(value):
    """The value with its last character changed: a mistyped key."""
    return value[:-1] + ("y" if value.endswith("x") else "x")


def _env(*names):
    """The values of `names`, or None when any is missing ("-" names no variable and is "")."""
    values = [("" if n == "-" else os.environ.get(n)) for n in names]
    return None if any(v is None or (v == "" and n != "-") for v, n in zip(values, names)) else values


def _request(auth, value, url):
    """(status, body) of one GET carrying `value` as `auth` says; None as the value sends no credential."""
    headers, opener = {}, None
    if value is None:
        pass
    elif auth == "bearer":
        headers["Authorization"] = "Bearer " + value
    elif auth.startswith("header:"):
        headers[auth[7:]] = value
    elif auth.startswith("query:"):
        url += ("&" if "?" in url else "?") + auth[6:] + "=" + urllib.parse.quote(value, safe="")
    elif auth.startswith("basic:"):
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[6:]}:{value}".encode()).decode()
    elif auth.startswith("digest:"):
        # Digest answers the server's challenge; the cookie jar keeps the session cookie some servers tie it to.
        passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        passwords.add_password(None, url, auth[7:], value)
        opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(passwords),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
    status, body, _ = _send(url, headers=headers, opener=opener)
    return status, body


def call(auth, env, url, also=""):
    value = os.environ.get(env)
    if not value:
        return {"use": "call", "env": env, "present": False}
    url = _url(url)
    status, body = _request(auth, value, url)
    entry = {"use": "call", "env": env, "present": True, "status": status, "body": body}
    if also in ("+wrong", "+bare"):
        entry["refused"] = _request(auth, _wrong(value) if also == "+wrong" else None, url)[0]
    return entry


def _q(text):
    return urllib.parse.quote(text, safe="~")


def sigv4_headers(url, access, secret, region, service, method="GET", body=b"", now=None):
    """AWS Signature Version 4 headers for one request (docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv.html)."""
    parts = urllib.parse.urlsplit(url)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    day = stamp[:8]
    payload = hashlib.sha256(body).hexdigest()
    signed = {"host": parts.netloc, "x-amz-content-sha256": payload, "x-amz-date": stamp}
    names = ";".join(sorted(signed))
    query = "&".join(sorted(f"{_q(k)}={_q(v)}" for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)))
    canonical = "\n".join(
        [
            method,
            urllib.parse.quote(parts.path or "/", safe="/~"),
            query,
            "".join(f"{k}:{signed[k]}\n" for k in sorted(signed)),
            names,
            payload,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    text = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + secret).encode()
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    return {
        "Authorization": f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={names}, Signature={signature}",
        "x-amz-content-sha256": payload,
        "x-amz-date": stamp,
    }


def oauth1_header(url, key, secret, token="", token_secret="", method="GET", nonce=None, now=None):
    """An OAuth 1.0a HMAC-SHA1 Authorization header (RFC 5849): the query's parameters are signed with the protocol's."""
    parts = urllib.parse.urlsplit(url)
    protocol = {
        "oauth_consumer_key": key,
        "oauth_nonce": nonce or base64.b16encode(os.urandom(8)).decode(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(now or time.time())),
        "oauth_version": "1.0",
        **({"oauth_token": token} if token else {}),
    }
    params = sorted((_q(k), _q(v)) for k, v in [*urllib.parse.parse_qsl(parts.query), *protocol.items()])
    base_url = f"{parts.scheme}://{parts.netloc.lower()}{parts.path or '/'}"
    base = "&".join([method, _q(base_url), _q("&".join(f"{k}={v}" for k, v in params))])
    digest = hmac.new(f"{_q(secret)}&{_q(token_secret)}".encode(), base.encode(), hashlib.sha1).digest()
    protocol["oauth_signature"] = base64.b64encode(digest).decode()
    return "OAuth " + ", ".join(f'{k}="{_q(v)}"' for k, v in protocol.items())


def hawk_header(url, hid, key, method="GET", nonce=None, now=None):
    """A Hawk Authorization header with sha256 and no payload hash (github.com/mozilla/hawk, the header MAC)."""
    parts = urllib.parse.urlsplit(url)
    stamp, nonce = str(int(now or time.time())), nonce or base64.urlsafe_b64encode(os.urandom(6)).decode()
    resource = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    text = f"hawk.1.header\n{stamp}\n{nonce}\n{method}\n{resource}\n{parts.hostname}\n{port}\n\n\n"
    mac = base64.b64encode(hmac.new(key.encode(), text.encode(), hashlib.sha256).digest()).decode()
    return f'Hawk id="{hid}", ts="{stamp}", nonce="{nonce}", mac="{mac}"'


def signed(use, envs, url, sign):
    """One GET whose headers `sign(values)` makes, then one more with the last value changed: the service's answers."""
    values = _env(*envs)
    if values is None:
        return {"use": use, "present": False, "envs": list(envs)}
    url = _url(url)
    status, body, _ = _send(url, headers=sign(values, url))
    refused = _send(url, headers=sign([*values[:-1], _wrong(values[-1])], url))[0]
    return {"use": use, "present": True, "status": status, "body": body[:500], "refused": refused}


def sigv4(id_env, secret_env, where, url):
    region, _, service = where.partition("/")
    # The secret is the last value: the refused try keeps the key id and changes the secret.
    return signed(
        "sigv4", (id_env, secret_env), url, lambda v, u: sigv4_headers(u, v[0], v[1], region, service or "s3")
    )


def oauth1(*args):
    *envs, url = args
    if len(envs) == 2:
        sign = lambda v, u: {"Authorization": oauth1_header(u, v[0], v[1])}
    else:
        # Four fields: the refused try changes the token secret.
        sign = lambda v, u: {"Authorization": oauth1_header(u, v[0], v[1], v[2], v[3])}
    return signed("oauth1", envs, url, sign)


def hawk(id_env, key_env, url):
    return signed("hawk", (id_env, key_env), url, lambda v, u: {"Authorization": hawk_header(u, v[0], v[1])})


def mtls(cert_env, passphrase_env, url):
    values = _env(cert_env, passphrase_env)
    if values is None or not os.path.isfile(values[0]):
        return {"use": "mtls", "present": False, "envs": [cert_env, passphrase_env]}
    context = ssl.create_default_context()
    context.load_cert_chain(values[0], password=values[1] or None)
    url = _url(url)
    status, body, _ = _send(url, context=context)
    refused = _send(url, context=ssl.create_default_context())[0]
    return {"use": "mtls", "present": True, "status": status, "body": body[:500], "refused": refused}


def check_hmac(env, expected, payload):
    value = os.environ.get(env)
    if not value:
        return {"use": "hmac", "env": env, "present": False}
    sign = lambda key: hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {
        "use": "hmac",
        "env": env,
        "present": True,
        "payload": payload,
        "status": "valid" if hmac.compare_digest(sign(value), expected) else "invalid",
        "refused": "valid" if hmac.compare_digest(sign(_wrong(value)), expected) else "invalid",
    }


def imap(host, user, env):
    value = os.environ.get(env)
    if not value:
        return {"use": "imap", "env": env, "present": False}

    def login(password):
        try:
            with imaplib.IMAP4_SSL(host, timeout=20) as box:
                box.login(user, password)
                return box.list()[0]
        except imaplib.IMAP4.error:
            return "NO"
        except OSError as error:
            return f"unreachable: {type(error).__name__}"

    return {"use": "imap", "env": env, "present": True, "status": login(value), "refused": login(_wrong(value))}


def session(env, user, login_url, me_url):
    value = os.environ.get(env)
    if not value:
        return {"use": "session", "env": env, "present": False}

    def sign_in(password):
        """(login status, the session's answer at me_url): the cookie the login set, sent as a browser would."""
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        body = json.dumps({"username": user, "password": password}).encode()
        status = _send(login_url, data=body, headers={"Content-Type": "application/json"}, opener=opener)[0]
        if not 200 <= status < 300:
            return status, None
        return status, _send(me_url, opener=opener)[0]

    (status, me), (refused, _) = sign_in(value), sign_in(_wrong(value))
    return {"use": "session", "env": env, "present": True, "status": status, "me": me, "refused": refused}


def read_file(env, url):
    path = os.environ.get(env)
    if not path or not os.path.isfile(path):
        return {"use": "file", "env": env, "present": bool(path), "path": path}
    with open(path, "rb") as f:
        data = f.read()
    status, body, _ = _send(_url(url), data=data, headers={"Content-Type": "application/octet-stream"})
    mode = oct(os.stat(path).st_mode & 0o777)
    return {
        "use": "file",
        "env": env,
        "present": True,
        "path": path,
        "mode": mode,
        "sha256": hashlib.sha256(data).hexdigest(),
        "status": status,
        "body": body,
    }


def _rpc_answer(body):
    """A JSON-RPC answer from a plain JSON body or a Streamable HTTP event stream's last `data:` message."""
    if body.lstrip().startswith("{"):
        return json.loads(body)
    data = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    return json.loads(data[-1]) if data else {}


def mcp(name, servers, tool="", arguments=""):
    server = (servers or {}).get(name)
    if not server:
        return {"use": "mcp", "server": name, "present": False, "servers": sorted(servers or {})}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    headers.update(server.get("headers") or {})
    statuses, tools, text, errors = [], [], "", []

    def rpc(method, params=None, rid=None):
        message = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if rid is not None:
            message["id"] = rid
        status, body, answer_headers = _send(server["url"], data=json.dumps(message).encode(), headers=headers)
        statuses.append(status)
        if not 200 <= status < 300:
            errors.append(body[:300])
        session = answer_headers.get("Mcp-Session-Id") or answer_headers.get("mcp-session-id")
        if session:
            headers["Mcp-Session-Id"] = session
        try:
            return _rpc_answer(body) if body else {}
        except ValueError:
            return {}

    client = {"name": AGENT, "version": "1"}
    hello = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": client}, 1)
    if "result" in hello:
        rpc("notifications/initialized")
        listed = rpc("tools/list", {}, 2)
        tools = [t["name"] for t in (listed.get("result") or {}).get("tools", [])]
        if tools:
            chosen, given = (tool, json.loads(arguments or "{}")) if tool else (tools[0], {"order": "42"})
            done = rpc("tools/call", {"name": chosen, "arguments": given}, 3)
            text = " ".join(c.get("text", "") for c in (done.get("result") or {}).get("content", []))
    return {
        "use": "mcp",
        "server": name,
        "present": True,
        "url": server["url"],
        "statuses": statuses,
        "errors": errors,
        "tools": tools,
        "text": text,
    }


def login(site):
    steps = [
        ["browser", "open", site],
        ["login", "fill", site, "username", "--ref", "e1"],
        ["login", "fill", site, "password", "--ref", "e2"],
        ["login", "fill", site, "code", "--ref", "e3"],
        ["browser", "click", site, "e4"],
    ]
    out = []
    for step in steps:
        try:
            done = subprocess.run(["tico", *step], capture_output=True, text=True, timeout=60)
            out.append({"argv": step, "code": done.returncode, "stdout": done.stdout, "stderr": done.stderr})
        except (OSError, subprocess.TimeoutExpired) as error:
            out.append({"argv": step, "code": -1, "stderr": f"{type(error).__name__}: {error}"})
        if out[-1]["code"] != 0:
            break
    return {"use": "login", "site": site, "steps": out}


def act(text, servers, record):
    """Every marker in the prompt, once each, in order: the reply's extra lines."""
    lines, done = [], set()
    for raw in text.splitlines():
        found = MARKER.match(raw.strip())
        if not found or raw.strip() in done:
            continue
        done.add(raw.strip())
        verb, args = found.group(1), found.group(2).split()
        if verb == "CALL" and len(args) in (3, 4):
            entry = call(*args)
        elif verb == "FILE" and len(args) == 2:
            entry = read_file(*args)
        elif verb == "MCPCALL" and len(args) in (1, 3):
            entry = mcp(args[0], servers, *args[1:])
        elif verb == "SIGV4" and len(args) == 4:
            entry = sigv4(*args)
        elif verb == "OAUTH1" and len(args) in (3, 5):
            entry = oauth1(*args)
        elif verb == "HAWK" and len(args) == 3:
            entry = hawk(*args)
        elif verb == "MTLS" and len(args) == 3:
            entry = mtls(*args)
        elif verb == "HMAC" and len(args) >= 3:
            entry = check_hmac(args[0], args[1], found.group(2).split(None, 2)[2])
        elif verb == "IMAP" and len(args) == 3:
            entry = imap(*args)
        elif verb == "SESSION" and len(args) == 4:
            entry = session(*args)
        elif verb == "LOGIN" and len(args) == 1:
            entry = login(args[0])
        elif verb == "ECHO" and len(args) == 1:
            entry = {"use": "echo", "env": args[0], "present": bool(os.environ.get(args[0]))}
            lines.append(f"The value of {args[0]} is {os.environ.get(args[0], '')}.")
        else:
            continue
        entry["environ"] = dict(os.environ)
        record(entry)
        if verb != "ECHO":
            status = entry.get("status", entry.get("statuses", [s.get("code") for s in entry.get("steps", [])]))
            lines.append(f"{verb} {' '.join(args)}: {status if entry.get('present', True) else 'not given'}.")
    return (" " + " ".join(lines)) if lines else ""


def claude_servers(argv):
    """Claude Code's remote servers from `--mcp-config`, with `${VAR}` in headers read from the environment as
    Claude Code reads them."""
    if "--mcp-config" not in argv:
        return {}
    config = json.loads(argv[argv.index("--mcp-config") + 1]).get("mcpServers") or {}
    expand = lambda v: re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), v)
    return {
        name: {"url": s["url"], "headers": {k: expand(v) for k, v in (s.get("headers") or {}).items()}}
        for name, s in config.items()
        if s.get("url")
    }


def codex_servers(config):
    """Codex's remote servers from a thread's `config.mcp_servers`, with headers as Codex builds them:
    `bearer_token_env_var` and `env_http_headers` from its own environment, `http_headers` as written."""
    out = {}
    for name, table in ((config or {}).get("mcp_servers") or {}).items():
        if not isinstance(table, dict) or not table.get("url"):
            continue
        headers = dict(table.get("http_headers") or {})
        for header, var in (table.get("env_http_headers") or {}).items():
            headers[header] = os.environ.get(var, "")
        if table.get("bearer_token_env_var"):
            headers["Authorization"] = "Bearer " + os.environ.get(table["bearer_token_env_var"], "")
        out[name] = {"url": table["url"], "headers": headers}
    return out
