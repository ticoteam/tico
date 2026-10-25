"""The one way Tico sends a request to an address a person typed in: https to a public address, or, when the
endpoint's owner turned it on, plain http to this machine for a receiver being built.

Every send resolves the name again and connects to the address it checked, so a second lookup (DNS rebinding) cannot
send it elsewhere; TLS (SNI and the certificate check) and Host still use the name. No redirect is followed, the
environment's proxy settings are ignored (a proxy would reach what the check kept out), the URL carries no sign-in, and
the answer is read only as far as its first kilobyte. One send has a total budget (DEADLINE) for the name lookup,
connecting and reading: httpx's own timeout restarts on every byte, so a server that drips bytes would otherwise hold
a worker past the point where a crashed send is handed to another.
"""
import ipaddress
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

TIMEOUT = 30.0
DEADLINE = 60.0
HEAD_BYTES = 1024


class Blocked(Exception):
    """The request was not sent: the address is not one Tico sends to. `reason` is plain words for the log."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Answer:
    status: int
    headers: dict = field(default_factory=dict)
    head: str = ""


def resolve(host):
    """Every address `host` resolves to, as ip_address objects (IPv4-mapped IPv6 as the IPv4 it holds). A seam for tests."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError) as exc:
        raise Blocked(f"The name {host} did not resolve") from exc
    found = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        if address not in found:
            found.append(address)
    return found


LOCAL_NAMES = ("localhost",)
LOCAL_SUFFIXES = (".localhost", ".local", ".internal")


def literal_address(host):
    """The ip_address a host spelled as a number is (`10.0.0.1`, `::1`, `2130706433`, `127.1`), else None. No lookup."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[0-9a-fA-FxX.]+", host):
            return None
        try:                                        # the short and whole-number spellings the system's resolver also takes
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return getattr(address, "ipv4_mapped", None) or address


def check_name(parts):
    """Blocked when an https host is, by its spelling alone, not a public address: an IP literal outside public space,
    or a name that is this machine or a local network (localhost, *.localhost, *.local, *.internal). The rule
    `address_for` applies to the addresses a name resolves to; this one needs no lookup, so it can answer when an
    address is saved. A name that merely resolves somewhere private is still stopped at each send."""
    host = parts.hostname.rstrip(".").lower()
    address = literal_address(host)
    if address is not None:
        if not address.is_global:
            raise Blocked(f"{parts.hostname} is not a public address")
    elif host in LOCAL_NAMES or host.endswith(LOCAL_SUFFIXES):
        raise Blocked(f"{parts.hostname} is a name for this machine or a local network, not a public address")


def check(url, allow_http=False):
    """The parts of `url` when Tico may send to it, else Blocked. Needs no network."""
    try:
        parts = urlsplit(url)
        parts.port                                  # raises on a bad port
    except ValueError as exc:
        raise Blocked("The address is not a valid URL") from exc
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise Blocked("The address must be an https URL")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise Blocked("The address must not carry a user name or password")
    if parts.scheme == "http" and not allow_http:
        raise Blocked("Plain http is allowed only to this machine, and only when the endpoint allows it")
    if parts.scheme == "https":
        check_name(parts)
    return parts


def _within(seconds, call, *args):
    """`call(*args)` or Blocked when it takes longer than `seconds`: a name lookup cannot be interrupted, so it runs on a
    thread that is left behind when the budget ends."""
    box = {}

    def run():
        try:
            box["value"] = call(*args)
        except BaseException as exc:                # carried back to the caller
            box["error"] = exc
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(max(seconds, 0.001))
    if worker.is_alive():
        raise Blocked(f"The name lookup took longer than {DEADLINE:g} seconds")
    if "error" in box:
        raise box["error"]
    return box["value"]


def address_for(parts, allow_http=False, budget=None):
    """The address to connect to: the first of the name's addresses, when all of them are acceptable."""
    addresses = resolve(parts.hostname) if budget is None else _within(budget, resolve, parts.hostname)
    if not addresses:
        raise Blocked(f"The name {parts.hostname} did not resolve")
    if parts.scheme == "http":
        if not all(a.is_loopback for a in addresses):
            raise Blocked("Plain http is allowed only to this machine")
    elif not all(a.is_global for a in addresses):
        raise Blocked(f"{parts.hostname} resolves to an address that is not public")
    return str(addresses[0])


def post(url, body, headers, *, allow_http=False, transport=None):
    """POST `body` (bytes) with `headers`; the Answer, or Blocked / httpx.HTTPError. `transport` stands in for the
    network in tests; the address is checked and pinned either way."""
    parts = check(url, allow_http)
    ends = time.monotonic() + DEADLINE
    address = address_for(parts, allow_http, budget=DEADLINE)
    pinned = httpx.URL(url).copy_with(host=address)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    sent = {**headers, "Host": host}
    late = f"The receiver took longer than {DEADLINE:g} seconds to answer"
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False, transport=transport) as client:
        # Closing the client from the timer ends a read that is blocked on a socket; the checks below catch the rest.
        timer = threading.Timer(max(ends - time.monotonic(), 0.001), client.close)
        timer.daemon = True
        timer.start()
        try:
            with client.stream("POST", pinned, content=body, headers=sent,
                               extensions={"sni_hostname": parts.hostname}) as response:
                head = b""
                for chunk in response.iter_bytes():
                    head += chunk
                    if len(head) >= HEAD_BYTES or time.monotonic() > ends:
                        break
                if time.monotonic() > ends:
                    raise Blocked(late)
                return Answer(response.status_code, {k.lower(): v for k, v in response.headers.items()},
                              head[:HEAD_BYTES].decode("utf-8", "replace"))
        except Blocked:
            raise
        except Exception as exc:
            if time.monotonic() > ends:
                raise Blocked(late) from exc
            raise
        finally:
            timer.cancel()
