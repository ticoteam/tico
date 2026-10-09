"""The checks. Each one returns what it saw and, when it fails, what to do next."""
from __future__ import annotations

import json
import shlex
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from . import contract, dns as dnsmod, signin
from .remote import Shell


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""


def check_dns(domain: str, records, resolvers) -> Check:
    bad = [label for label, r in resolvers if not dnsmod.resolves_to(domain, records, r)]
    if not bad:
        return Check("DNS", True, f"{domain} resolves correctly on public resolvers")
    return Check("DNS", False, f"{domain} does not resolve as planned on {', '.join(bad)}",
                 "Add the records at the provider that serves the domain (`tico setup` prints where), then wait a few minutes.")


def check_tls(domain: str, connect: Callable | None = None) -> Check:
    hint_cert = ("The certificate is probably still being issued (Caddy asks Let's Encrypt when it first starts, which takes a minute "
                 "or two): run the check again shortly. If it keeps failing, `docker compose logs caddy` on the server shows the ACME "
                 "error; then check DNS, ports 80 and 443 open, and `docker compose restart caddy`.")
    try:
        ctx = ssl.create_default_context()
        with (connect or socket.create_connection)((domain, 443), timeout=8) as raw, ctx.wrap_socket(raw, server_hostname=domain) as s:
            cert = s.getpeercert()
    except ssl.SSLCertVerificationError as e:
        return Check("HTTPS certificate", False, f"certificate is not valid: {e.verify_message}", hint_cert)
    except ssl.SSLError as e:  # "tlsv1 alert internal error": Caddy is up but has no certificate yet
        return Check("HTTPS certificate", False, f"TLS handshake failed ({getattr(e, "reason", None) or e})", hint_cert)
    except OSError as e:
        return Check("HTTPS certificate", False, f"could not connect to {domain}:443 ({e})",
                     "Nothing answers on 443: DNS not live, security group / firewall closed, the front door is not running, or the server is in "
                     "a private subnet (its route table's 0.0.0.0/0 goes to a NAT gateway instead of an internet gateway).")
    left = (datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc) - datetime.now(timezone.utc)).days
    return Check("HTTPS certificate", True, f"valid, {left} days left")


def _get(url: str, follow: bool = True, opener=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = opener or (urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect))
    try:
        with op.open(urllib.request.Request(url, headers={"User-Agent": "tico-setup"}), timeout=10) as r:
            return r.status, dict(r.headers), r.read(2000).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), ""


def tunnel_route_hint(domain: str) -> str:
    """What to do when the tunnel is connected but sends nothing to Tico: cloudflared has no route for the hostname."""
    d = contract.REMOTE_DIR
    return (f"The tunnel is connected but has no route to Tico. The compose file gives cloudflared a route from TICO_DOMAIN "
            f"({domain} -> http://{contract.SERVICE_SERVER}:{contract.SERVER_PORT}): on the server run `cd {d} && docker compose pull "
            f"&& docker compose up -d` (a Tico older than that needs the update first). If the tunnel is managed in Cloudflare, add the "
            f"route there instead: Zero Trust > Networks > Tunnels > your tunnel > Public Hostname > {domain}, HTTP, "
            f"{contract.SERVICE_SERVER}:{contract.SERVER_PORT}; cloudflared uses the route Cloudflare holds over the local file.")


def check_health(domain: str, get=_get, front_door: str = "") -> Check:
    try:
        code, _, _ = get(f"https://{domain}{contract.HEALTH_PATH}")
    except (OSError, urllib.error.URLError) as e:
        return Check("/healthz", False, f"request failed ({getattr(e, 'reason', e)})",
                     "On the server: `docker compose ps` (server should be healthy) and `docker compose logs server`.")
    if code == 200:
        return Check("/healthz", True, "ok")
    if front_door == "cloudflared" and code in TUNNEL_ERRORS:
        return Check("/healthz", False, f"HTTP {code} from the tunnel", tunnel_route_hint(domain)
                     + " If the route is right, the server is the problem: `docker compose logs server`.")
    return Check("/healthz", False, f"HTTP {code}", "The front door answers but the server does not: `docker compose logs server`.")


NO_INGRESS = "No ingress rules"
# What Cloudflare answers when the tunnel has no route (503, or 502 when the connector cannot reach the origin).
TUNNEL_ERRORS = (502, 503, 530)


def check_tunnel_route(r: Shell, domain: str) -> Check:
    """cloudflared can be up and healthy while every request gets a 503: its log says so when it has no ingress rules."""
    d = contract.REMOTE_DIR
    res = r.run(f"cd {d} && (docker compose logs --no-color --tail 200 cloudflared 2>&1 || "
                f"sudo -n docker compose logs --no-color --tail 200 cloudflared 2>&1)")
    if NO_INGRESS in res.stdout:
        return Check("Tunnel route", False, "cloudflared reports no ingress rules, so it answers 503 to every request", tunnel_route_hint(domain))
    return Check("Tunnel route", True, "cloudflared has a route")


def check_runner_host(host: str, get=_get) -> Check:
    """Computers and outside agents cannot pass a sign-in page, so the runner hostname must answer from the server itself:
    an /api/v2 route that needs no sign-in returns 200 there, not a redirect to a login."""
    name = "Runner hostname"
    try:
        code, headers, _ = get(f"https://{host}{contract.RUNNER_PROBE_PATH}", follow=False)
    except (OSError, urllib.error.URLError) as e:
        return Check(name, False, f"request failed ({getattr(e, 'reason', e)})",
                     f"{host} needs a proxied CNAME to the tunnel (`tico setup` creates it) and a Cloudflare certificate that covers it.")
    if code == 200:
        return Check(name, True, f"{host} answers /api/v2 from the server, no sign-in in front")
    loc = {k.lower(): v for k, v in headers.items()}.get("location", "")
    if code in (301, 302, 303, 307, 308) or code in (401, 403) and "cloudflareaccess" in loc:
        where = urllib.parse.urlparse(loc).hostname or "a sign-in page"
        return Check(name, False, f"HTTP {code}, redirect to {where}",
                     f"Leave {host} out of the Access application (or give it a Bypass policy): Tico checks the computers' and agents' "
                     "tokens itself, and the tunnel routes only /api/v2 and /download there.")
    return Check(name, False, f"HTTP {code} from the tunnel",
                 f"The tunnel has no route for {host}: re-run `tico setup` (it stores the route in Cloudflare), or in Zero Trust > Networks > "
                 f"Tunnels > your tunnel > Public Hostname add {host}, path {contract.RUNNER_ROUTE_PATH}, HTTP "
                 f"{contract.SERVICE_SERVER}:{contract.SERVER_PORT}.")


def check_signin(domain: str, provider: str, client_id: str, get=_get) -> Check:
    want_uri = signin.redirect_uri(domain)
    try:
        code, headers, _ = get(f"https://{domain}{contract.SIGNIN_PATH}", follow=False)
    except (OSError, urllib.error.URLError) as e:
        return Check("Sign-in redirect", False, f"request failed ({getattr(e, 'reason', e)})", "Fix HTTPS first.")
    loc = {k.lower(): v for k, v in headers.items()}.get("location", "")
    u = urllib.parse.urlparse(loc)
    q = urllib.parse.parse_qs(u.query)
    if code not in (301, 302, 303, 307, 308) or u.hostname != contract.OIDC_HOSTS.get(provider):
        return Check("Sign-in redirect", False, f"HTTP {code}, redirect to {u.hostname or 'nowhere'}",
                     f"Expected a redirect to {contract.OIDC_HOSTS.get(provider)}. Check TICO_AUTH_PROXY=oidc and "
                     "TICO_OIDC_ISSUER in /opt/tico/.env, then `docker compose up -d`.")
    if q.get("redirect_uri", [""])[0] != want_uri:
        return Check("Sign-in redirect", False, f"redirect_uri is {q.get('redirect_uri', ['(missing)'])[0]}",
                     f"The provider's OAuth client must list exactly {want_uri}. Fix TICO_DOMAIN or the client.")
    if client_id and q.get("client_id", [""])[0] != client_id:
        return Check("Sign-in redirect", False, "the redirect carries a different client id", "Re-run `tico setup` to update the client id.")
    return Check("Sign-in redirect", True, f"redirects to {u.hostname} with redirect_uri {want_uri}")


def check_service(r: Shell, service: str, label: str) -> Check:
    d = contract.REMOTE_DIR
    svc = shlex.quote(service)
    res = r.run(f"cd {d} && (docker compose ps --format json {svc} 2>/dev/null || sudo -n docker compose ps --format json {svc})")
    rows = []
    for line in res.stdout.splitlines():
        try:
            v = json.loads(line)
            rows += v if isinstance(v, list) else [v]
        except ValueError:
            pass
    if rows and all(x.get("State") == "running" and x.get("Health", "healthy") in ("healthy", "") for x in rows):
        return Check(label, True, "running" + (", healthy" if rows[0].get("Health") == "healthy" else ""))
    state = rows[0].get("State", "not found") if rows else "not found"
    return Check(label, False, f"{service} is {state}",
                 f"On the server: `cd {d} && docker compose logs --tail 50 {service}`. `docker compose up -d` retries.")


def check_env_perms(r: Shell) -> Check:
    res = r.run(f"stat -c %a {contract.REMOTE_DIR}/.env 2>/dev/null || sudo -n stat -c %a {contract.REMOTE_DIR}/.env")
    mode = res.stdout.strip()
    if mode == "600":
        return Check(".env permissions", True, "0600")
    return Check(".env permissions", False, f"mode {mode or 'unknown'}", f"chmod 600 {contract.REMOTE_DIR}/.env")


def _https_checks(domain, provider, client_id, front_door="") -> list[Check]:
    out = [check_tls(domain), check_health(domain, front_door=front_door)]
    if provider in contract.OIDC_HOSTS:
        out.append(check_signin(domain, provider, client_id))
    return out


def run_all(*, domain: str, provider: str, client_id: str, front_door: str, records, resolvers,
            shell: Shell | None, runner_host: str = "", wait_https: float = 0, sleep: Callable[[float], None] = time.sleep,
            say: Callable[[str], None] | None = None) -> list[Check]:
    """`wait_https` seconds of retrying the HTTPS checks: right after `docker compose up` Caddy has no certificate yet."""
    dns_check = check_dns(domain, records, resolvers)
    https = _https_checks(domain, provider, client_id, front_door)
    waited, delay = 0.0, 3.0
    while wait_https and not all(c.ok for c in https) and waited < wait_https:
        if not waited and say:
            what = "the tunnel to answer" if front_door == "cloudflared" else "the HTTPS certificate"
            say(f"  waiting for {what} (up to {int(wait_https // 60)} min"
                + ("" if front_door == "cloudflared" else "; Caddy is still asking Let's Encrypt") + ") ...")
        sleep(delay)
        waited += delay
        delay = min(delay + 3, 15)
        https = _https_checks(domain, provider, client_id, front_door)
    out = [dns_check, *https]
    if runner_host:
        out.append(check_runner_host(runner_host))
    if shell is not None:
        out.append(check_service(shell, contract.SERVICE_SERVER, "Server container"))
        if front_door == "cloudflared":
            out.append(check_service(shell, "cloudflared", "Tunnel connector"))
            out.append(check_tunnel_route(shell, domain))
        out.append(check_env_perms(shell))
    return out


def check_runner_container(r: Shell, label: str = "") -> Check:
    """The runner container is up and has not crashed while joining."""
    c = contract.RUNNER_CONTAINER
    res = r.run(f"docker ps --filter name=^{c}$ --format '{{{{.Status}}}}' 2>/dev/null || sudo -n docker ps --filter name=^{c}$ --format '{{{{.Status}}}}'")
    status = res.stdout.strip()
    if not status.startswith("Up"):
        logs = r.run(f"docker logs --tail 15 {c} 2>&1 || sudo -n docker logs --tail 15 {c} 2>&1").stdout.strip()
        return Check("Runner container", False, status or "not running",
                     "Codes are single use and expire after 15 minutes: re-run `python3 -m setup runner` for a fresh one."
                     + (f" Last log lines: {logs[-300:]}" if logs else ""))
    return Check("Runner container", True, status)


def _when(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def check_computer_online(url: str, token: str, label: str, get=None) -> Check | None:
    """Asks the server whether the computer shows up. None when it cannot be told (no token, or the API says no)."""
    if not token:
        return None
    import urllib.request
    try:
        req = urllib.request.Request(url.rstrip("/") + contract.COMPUTERS_PATH, headers={"Authorization": f"Bearer {token}"})
        with (get or urllib.request.urlopen)(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    mine = [x for x in body.get("machines", []) if isinstance(x, dict) and x.get("label") == label and not x.get("revoked_at")]
    if not mine:
        return Check("Computer in Tico", False, f"no computer labelled {label!r} yet",
                     "Give it a minute after the container starts, then run `python3 -m setup doctor --runner`.")
    now = _when(body.get("server_time")) or datetime.now(timezone.utc)
    online = any((seen := _when(x.get("last_seen"))) and (now - seen).total_seconds() <= contract.ONLINE_SECONDS for x in mine)
    return Check("Computer in Tico", online, "online" if online else "registered but not reporting in",
                 "" if online else f"On the box: docker logs {contract.RUNNER_CONTAINER}")
