"""RFC 8058 one-click requests: signed headers, public HTTPS, no redirects/cookies.

Only an explicit CLI unsubscribe invocation submits a request. Unsupported methods never
fall back to following a body link or sending an email. URLs contain recipient tokens: keep
only hashes and hostnames in output/audit/state.
"""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit


class Unsupported(ValueError):
    pass


def endpoint(raw):
    import dkim

    msg = BytesParser(policy=policy.default).parsebytes(raw)
    for name in ('List-Unsubscribe', 'List-Unsubscribe-Post', 'From'):
        if len(msg.get_all(name, [])) != 1:
            raise Unsupported('Missing or duplicate one-click headers; manual unsubscribe needed.')
    if str(msg['List-Unsubscribe-Post']).strip().lower() != 'list-unsubscribe=one-click':
        raise Unsupported('Sender does not advertise standard one-click unsubscribe.')
    urls = re.findall(r'<([^<>]+)>', str(msg['List-Unsubscribe']))
    urls = [u.strip() for u in urls if u.strip().lower().startswith('https:')]
    if len(urls) != 1:
        raise Unsupported('Expected exactly one HTTPS unsubscribe endpoint.')
    parsed_url(urls[0])
    # Verify the actual raw message, never trust an Authentication-Results string from mail.
    verifier = dkim.DKIM(raw, timeout=5)
    required = {b'from', b'list-unsubscribe', b'list-unsubscribe-post'}
    for idx, sig in enumerate(msg.get_all('DKIM-Signature', [])[:5]):
        fields = dict(re.findall(r'(\w+)\s*=\s*([^;]+)', str(sig)))
        signed = {x.strip().lower().encode() for x in fields.get('h', '').split(':')}
        if not required <= signed:
            continue
        try:
            if verifier.verify(idx=idx):
                return urls[0]
        except Exception:
            continue
    raise Unsupported('Could not verify a DKIM signature covering the unsubscribe headers.')


def parsed_url(url):
    try:
        p = urlsplit(url)
        valid = (
            p.scheme == 'https'
            and p.hostname
            and p.port in (None, 443)
            and not p.username
            and not p.password
            and not p.fragment
            and not any(ord(c) <= 32 or ord(c) >= 127 for c in url)
        )
    except ValueError:
        valid = False
    if not valid:
        raise Unsupported('Unsubscribe endpoint must be a plain HTTPS URL on port 443.')
    return p


def public_address(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise Unsupported('Unsubscribe endpoint does not resolve exclusively to public addresses.')
    return addresses[0][4][0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        # Pin the connection to the validated IP; TLS still verifies the original hostname.
        address = public_address(self.host)
        sock = socket.create_connection((address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def post(url):
    p = parsed_url(url)
    conn = PinnedHTTPS(p.hostname, timeout=15, context=ssl.create_default_context())
    try:
        target = (p.path or '/') + ('?' + p.query if p.query else '')
        conn.request(
            'POST',
            target,
            body=b'List-Unsubscribe=One-Click',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        return conn.getresponse().status
    finally:
        conn.close()


def submit(conn, mailbox, url, dry_run=False):
    host = parsed_url(url).hostname
    key = hashlib.sha256((mailbox + '\n' + url).encode()).hexdigest()
    result = {'endpoint_host': host, 'request_key': key, 'accepted': False}
    if dry_run:
        return dict(result, status='ready', would_unsubscribe=True)
    conn.execute(
        'CREATE TABLE IF NOT EXISTS unsubscribe_requests '
        '(key TEXT PRIMARY KEY, status TEXT NOT NULL, http_status INTEGER)'
    )
    conn.commit()
    # Claim before network I/O: an uncertain timeout must not cause automatic resubmission.
    with conn:
        claimed = conn.execute(
            "INSERT OR IGNORE INTO unsubscribe_requests VALUES (?, 'pending', NULL)", (key,)
        ).rowcount
    if not claimed:
        row = conn.execute('SELECT status, http_status FROM unsubscribe_requests WHERE key=?', (key,)).fetchone()
        return dict(result, status=row[0], http_status=row[1], accepted=row[0] == 'accepted', already_attempted=True)
    try:
        code = post(url)
        status = 'accepted' if 200 <= code < 300 else 'manual_required'
    except Exception:
        code, status = None, 'unknown'
    with conn:
        conn.execute('UPDATE unsubscribe_requests SET status=?, http_status=? WHERE key=?', (status, code, key))
    return dict(result, status=status, http_status=code, accepted=status == 'accepted')
