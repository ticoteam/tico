"""DNS: find who is authoritative, plan the records, and wait until public resolvers see them."""
from __future__ import annotations

import ipaddress
import random
import re
import socket
import struct
import time
from dataclasses import dataclass
from typing import Callable, Protocol

A, NS, CNAME, AAAA = 1, 2, 5, 28
PUBLIC_RESOLVERS = (("Google", "8.8.8.8"), ("Cloudflare", "1.1.1.1"), ("Quad9", "9.9.9.9"))


class Resolver(Protocol):
    def query(self, name: str, rtype: int) -> list[str]: ...


class WireResolver:
    """A minimal stub client, so the wizard needs no dig or dnspython."""

    def __init__(self, server: str, timeout: float = 3.0):
        self.server, self.timeout = server, timeout

    def query(self, name: str, rtype: int) -> list[str]:
        qid = random.randrange(65536)
        q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
        q += b"".join(bytes([len(p)]) + p.encode("idna") for p in name.rstrip(".").split(".")) + b"\0"
        q += struct.pack(">HH", rtype, 1)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            s.sendto(q, (self.server, 53))
            data, _ = s.recvfrom(4096)
        if struct.unpack(">H", data[2:4])[0] & 0x0200:
            with socket.create_connection((self.server, 53), timeout=self.timeout) as t:
                t.sendall(struct.pack(">H", len(q)) + q)
                n = struct.unpack(">H", _read(t, 2))[0]
                data = _read(t, n)
        return _parse_answers(data, rtype)


def _read(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("connection closed")
        buf += chunk
    return buf


def _name(data: bytes, pos: int) -> tuple[str, int]:
    labels, end, jumped = [], pos, False
    for _ in range(128):
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = ((n & 0x3F) << 8) | data[pos + 1]
            if not jumped:
                end = pos + 2
            pos, jumped = ptr, True
            continue
        labels.append(data[pos + 1:pos + 1 + n].decode("ascii", "replace"))
        pos += 1 + n
    else:
        raise ValueError("name loop")
    return ".".join(labels).lower(), (end if jumped else pos)


def _parse_answers(data: bytes, want: int) -> list[str]:
    _, _, qd, an, _, _ = struct.unpack(">HHHHHH", data[:12])
    pos = 12
    for _ in range(qd):
        _, pos = _name(data, pos)
        pos += 4
    out = []
    for _ in range(an):
        _, pos = _name(data, pos)
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        rd = data[pos:pos + rdlen]
        if rtype == want:
            if rtype in (NS, CNAME):
                out.append(_name(data, pos)[0])
            elif rtype == A:
                out.append(socket.inet_ntoa(rd))
            elif rtype == AAAA:
                out.append(str(ipaddress.IPv6Address(rd)))
        pos += rdlen
    return out


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    where: str


# (regex on the nameserver host, provider). First match wins.
_SUFFIXES = [
    (r"awsdns-", Provider("route53", "Amazon Route 53", "Route 53 > Hosted zones")),
    (r"ns\.cloudflare\.com$", Provider("cloudflare", "Cloudflare", "Cloudflare dashboard > your domain > DNS > Records")),
    (r"nsone\.net$", Provider("netlify", "Netlify DNS (NS1 nameservers)",
                           "Netlify > Domains > your domain > DNS records (or NS1, if you use it directly)")),
    (r"vercel-dns\.com$", Provider("vercel", "Vercel", "Vercel > Domains > your domain > DNS Records")),
    (r"googledomains\.com$", Provider("google", "Google Cloud DNS / Google Domains",
                                   "Google Cloud console > Cloud DNS > your zone (or Squarespace Domains)")),
    (r"domaincontrol\.com$", Provider("godaddy", "GoDaddy", "GoDaddy > My Products > Domains > DNS")),
    (r"registrar-servers\.com$", Provider("namecheap", "Namecheap", "Namecheap > Domain List > Manage > Advanced DNS")),
    (r"azure-dns\.", Provider("azure", "Azure DNS", "Azure portal > DNS zones")),
    (r"digitalocean\.com$", Provider("digitalocean", "DigitalOcean", "DigitalOcean > Networking > Domains")),
    (r"squarespacedns\.com$", Provider("squarespace", "Squarespace Domains", "Squarespace > Domains > DNS settings")),
    (r"wixdns\.net$", Provider("wix", "Wix", "Wix > Domains > Manage DNS records")),
    (r"dnsimple\.com$", Provider("dnsimple", "DNSimple", "DNSimple > your domain > DNS")),
    (r"hover\.com$", Provider("hover", "Hover", "Hover > your domain > DNS")),
    (r"gandi\.net$", Provider("gandi", "Gandi", "Gandi > Domain > DNS Records")),
    (r"ovh\.net$", Provider("ovh", "OVHcloud", "OVHcloud > Domains > DNS zone")),
]


def detect_provider(nameservers: list[str]) -> Provider:
    for ns in nameservers:
        host = ns.lower().rstrip(".")
        for suffix, prov in _SUFFIXES:
            if re.search(suffix, host):
                return prov
    shown = nameservers[0] if nameservers else "no nameservers found"
    return Provider("unknown", f"an unrecognized provider ({shown})", "your DNS provider's control panel")


@dataclass(frozen=True)
class Zone:
    name: str
    nameservers: tuple[str, ...]
    provider: Provider


def detect_zone(domain: str, resolver: Resolver) -> Zone | None:
    """Walk up from the domain to the first name that has its own NS records: that is where records live."""
    labels = domain.lower().rstrip(".").split(".")
    for i in range(len(labels) - 1):
        cand = ".".join(labels[i:])
        try:
            ns = resolver.query(cand, NS)
        except OSError:
            continue
        if ns:
            return Zone(cand, tuple(sorted(ns)), detect_provider(ns))
    return None


@dataclass(frozen=True)
class Record:
    type: str
    name: str  # fully qualified, no trailing dot
    value: str
    proxied: bool = False
    ttl: int = 300


def plan_records(domain: str, front_door: str, ipv4: str = "", ipv6: str = "", tunnel_id: str = "", runner_host: str = "") -> list[Record]:
    """The domain's record comes first: the DNS wait and check look at it."""
    if front_door == "cloudflared":
        target = f"{tunnel_id or '<tunnel-id>'}.cfargotunnel.com"
        return [Record("CNAME", name, target, proxied=True) for name in (domain, runner_host) if name]
    recs = []
    if ipv4:
        recs.append(Record("A", domain, ipv4))
    if ipv6:
        recs.append(Record("AAAA", domain, ipv6))
    if not recs:
        recs.append(Record("A", domain, "<server-ip>"))
    return recs


def relative_name(fqdn: str, zone: str) -> str:
    return "@" if fqdn == zone else fqdn[: -len(zone) - 1]


def manual_instructions(records: list[Record], zone: Zone) -> list[str]:
    lines = [f"Your domain's DNS is served by {zone.provider.name} (nameservers: {', '.join(zone.nameservers)}).",
             f"Add these records there ({zone.provider.where}), in the zone {zone.name}:"]
    for r in records:
        note = "  (proxied / orange cloud)" if r.proxied else ""
        lines.append(f"  {r.type:<6} name {relative_name(r.name, zone.name):<12} value {r.value}  ttl {r.ttl}{note}")
    lines.append("Add them at that provider only: records in any other DNS service you may also have are ignored.")
    return lines


def _addrs(res: Resolver, name: str) -> tuple[list[str], list[str]]:
    try:
        return res.query(name, A), res.query(name, AAAA)
    except OSError:
        return [], []


def resolves_to(domain: str, records: list[Record], res: Resolver) -> bool:
    rec = records[0]
    if rec.type == "CNAME":
        v4, v6 = _addrs(res, domain)  # a proxied tunnel name resolves to Cloudflare's addresses
        try:
            target = res.query(domain, CNAME)
        except OSError:
            target = []
        return any("cfargotunnel.com" in t for t in target) or bool(v4 or v6)
    v4, v6 = _addrs(res, domain)
    want4 = {r.value for r in records if r.type == "A"}
    want6 = {r.value for r in records if r.type == "AAAA"}
    return (not want4 or want4 <= set(v4)) and (not want6 or want6 <= set(v6))


def wait_until_live(domain: str, records: list[Record], resolvers: list[tuple[str, Resolver]], *, timeout: float,
                    interval: float = 10.0, say: Callable[[str], None] = print,
                    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> bool:
    """True once every public resolver agrees. Certificates fail if we start before this."""
    start = clock()
    while True:
        seen = {label: resolves_to(domain, records, r) for label, r in resolvers}
        if all(seen.values()):
            say(f"  {domain} resolves correctly on {', '.join(seen)}.")
            return True
        if clock() - start >= timeout:
            waiting = ", ".join(k for k, v in seen.items() if not v)
            say(f"  Still not resolving on {waiting} after {int(timeout // 60)} min. Records can take longer at some "
                "providers; check they are in the provider named above, then re-run `tico setup` (it resumes).")
            return False
        say(f"  waiting for DNS ({int(clock() - start)}s): not yet on "
            f"{', '.join(k for k, v in seen.items() if not v)}")
        sleep(interval)


def default_resolvers() -> list[tuple[str, Resolver]]:
    return [(label, WireResolver(ip)) for label, ip in PUBLIC_RESOLVERS]
