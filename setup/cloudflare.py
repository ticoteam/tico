"""Cloudflare API: a named tunnel, its hostname route, and the DNS record. Needs a token scoped to
Account > Cloudflare Tunnel: Edit and Zone > DNS: Edit on the one zone."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from . import contract, settings as st

API = "https://api.cloudflare.com/client/v4"
TOKEN_URL = "https://dash.cloudflare.com/profile/api-tokens"

Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict]"]


class CloudflareError(RuntimeError):
    pass


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def ingress_config(hostname: str, runner_host: str = "", *, aliases=(), visual_origin: str = "") -> dict:
    """The route a Tico tunnel needs: the hostname to the server, anything else a 404. With a runner hostname (served without
    Access, for computers and outside agents) that host goes to the server too, for /api/v2 and /download only. It is what
    setup stores in a tunnel it creates through the API, and what the server writes for the cloudflared container from
    TICO_DOMAIN and TICO_RUNNER_URL at every start (docker/entrypoint.sh `tunnel-config`, read by compose.yaml's cloudflared
    service), so a tunnel run with only its token still has a route. A tunnel managed in Cloudflare (the dashboard, or setup
    through the API) keeps its own: cloudflared prefers it to the local file."""
    if not st.plain_hostname(hostname.lower()) or runner_host and not st.plain_hostname(runner_host.lower()):
        raise st.MissingInput("Tunnel hosts must be plain DNS hostnames")
    canonical, runner_host = hostname.lower(), runner_host.lower()
    runner_host = "" if runner_host == canonical else runner_host
    try:
        visual = st.visual_host(visual_origin, canonical, runner_host)
    except st.MissingInput:
        if aliases:
            raise st.MissingInput("TICO_DOMAIN_ALIASES needs a valid separate visual origin") from None
        visual = ""  # The local runtime also leaves invalid optional visual routes out.
    value = aliases if isinstance(aliases, str) else ",".join(aliases)
    app_hosts = st.aliases(value, canonical, runner_host, visual)
    service = f"http://{contract.SERVICE_SERVER}:{contract.SERVER_PORT}"
    rules = [{"hostname": host, "service": service} for host in (canonical, *app_hosts)]
    if runner_host:
        rules.append({"hostname": runner_host, "path": contract.RUNNER_ROUTE_PATH, "service": service})
    if visual:
        rules.append({"hostname": visual, "path": "^/visuals/", "service": service})
    return {"ingress": [*rules, {"service": "http_status:404"}]}


class Cloudflare:
    def __init__(self, token: str, transport: Transport = _urllib_transport):
        self._token, self._t = token, transport

    def call(self, method: str, path: str, body: dict | None = None):
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        status, data = self._t(method, API + path, headers, json.dumps(body).encode() if body is not None else None)
        if status >= 300 or not data.get("success", True):
            msgs = "; ".join(e.get("message", "") for e in data.get("errors", [])) or f"HTTP {status}"
            raise CloudflareError(f"Cloudflare {method} {path}: {msgs}")
        return data.get("result")

    def find_zone(self, domain: str) -> dict | None:
        labels = domain.split(".")
        for i in range(len(labels) - 1):
            zones = self.call("GET", f"/zones?name={'.'.join(labels[i:])}")
            if zones:
                z = zones[0]
                return {"id": z["id"], "name": z["name"], "account_id": z["account"]["id"]}
        return None

    def find_tunnel(self, account_id: str, name: str) -> str:
        found = self.call("GET", f"/accounts/{account_id}/cfd_tunnel?name={name}&is_deleted=false")
        return found[0]["id"] if found else ""

    def ensure_tunnel(self, account_id: str, name: str) -> tuple[str, str]:
        """Returns (tunnel id, tunnel token); an existing tunnel of that name is reused, not duplicated."""
        tid = self.find_tunnel(account_id, name)
        if tid:
            return tid, self.call("GET", f"/accounts/{account_id}/cfd_tunnel/{tid}/token")
        made = self.call("POST", f"/accounts/{account_id}/cfd_tunnel", {"name": name, "config_src": "cloudflare"})
        return made["id"], made["token"]

    def configure_tunnel(self, account_id: str, tunnel_id: str, hostname: str, runner_host: str = "", *, aliases=(), visual_origin: str = "") -> None:
        self.call("PUT", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations", {"config": ingress_config(hostname, runner_host, aliases=aliases, visual_origin=visual_origin)})

    def find_record(self, zone_id: str, name: str) -> dict | None:
        existing = self.call("GET", f"/zones/{zone_id}/dns_records?name={name}")
        return existing[0] if existing else None

    def upsert_cname(self, zone_id: str, name: str, target: str, repoint: bool = True) -> str:
        """`repoint=False` leaves a CNAME that points somewhere else alone: it may serve something that is not Tico's."""
        body = {"type": "CNAME", "name": name, "content": target, "proxied": True, "ttl": 1}
        rec = self.find_record(zone_id, name)
        if rec:
            if rec["type"] != "CNAME":
                raise CloudflareError(f"{name} already has a {rec['type']} record in Cloudflare; remove it first")
            if rec["content"] == target and rec.get("proxied"):
                return "unchanged"
            if rec["content"] != target and not repoint:
                raise CloudflareError(f"{name} already points at {rec['content']}; not repointing it to {target}")
            self.call("PUT", f"/zones/{zone_id}/dns_records/{rec['id']}", body)
            return "updated" if rec["content"] == target else f"repointed from {rec['content']}"
        self.call("POST", f"/zones/{zone_id}/dns_records", body)
        return "created"

    def upsert_address(self, zone_id: str, rec_type: str, name: str, ip: str) -> str:
        body = {"type": rec_type, "name": name, "content": ip, "proxied": False, "ttl": 300}
        existing = [r for r in self.call("GET", f"/zones/{zone_id}/dns_records?name={name}") or []]
        same = [r for r in existing if r["type"] == rec_type]
        if same and same[0]["content"] == ip:
            return "unchanged"
        if same:
            self.call("PUT", f"/zones/{zone_id}/dns_records/{same[0]['id']}", body)
            return "updated"
        self.call("POST", f"/zones/{zone_id}/dns_records", body)
        return "created"


DASHBOARD_STEPS = [
    "In Cloudflare, open Zero Trust > Networks > Tunnels > Create a tunnel > Cloudflared.",
    "Name it, then copy the token from the install command (the long string after `--token`).",
    "In the tunnel's Public Hostname tab add: hostname {domain}, service type HTTP, URL server:8765.",
]
RUNNER_DASHBOARD_STEP = ("Add a second Public Hostname: hostname {runner}, path {path}, service type HTTP, URL server:8765. "
                         "Leave it out of the Access application: Tico checks the computers' and agents' tokens itself.")
