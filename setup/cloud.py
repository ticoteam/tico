"""Create the one server on Hetzner Cloud or DigitalOcean through the user's own `hcloud` / `doctl`.

Nothing is created without the CLI being installed and signed in. The user-data (which holds the sign-in secret) goes
to the CLI as a 0600 temp file, never on a command line, and CLI output is scrubbed before it is shown.
"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from . import envfile

MANAGED = "tico-setup"
NAME_LABEL = "tico-setup-name"
IMAGE = {"hetzner": "ubuntu-24.04", "digitalocean": "ubuntu-24-04-x64"}


@dataclass
class Cli:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def run_cli(argv: list[str]) -> Cli:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    return Cli(p.returncode, p.stdout, p.stderr)


class CloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    cli: str
    install_url: str
    login_hint: str
    sizes: dict[str, str]
    default_size: str
    locations: dict[str, str]
    default_location: str
    note: str  # pricing caveats shown with the plan


HETZNER = Provider(
    "hetzner",
    "Hetzner Cloud",
    "hcloud",
    "https://github.com/hetznercloud/cli#installation",
    "hcloud context create tico   (paste a read/write API token from the project's Security > API tokens)",
    {
        "cax11": "2 vCPU Arm, 4 GB, 40 GB: about EUR 5.99/month",
        "cx23": "2 vCPU x86, 4 GB, 40 GB: about EUR 5.49/month",
        "cax21": "4 vCPU Arm, 8 GB, 80 GB: about EUR 10.49/month",
        "cx33": "4 vCPU x86, 8 GB, 80 GB: about EUR 8.49/month",
    },
    "cax11",
    {"nbg1": "Nuremberg", "fsn1": "Falkenstein", "hel1": "Helsinki"},
    "nbg1",
    "List prices for Germany/Finland as of 2026-09, before VAT; the public IPv4 is priced separately. The Arm sizes (cax*) exist only in the EU locations.",
)
DIGITALOCEAN = Provider(
    "digitalocean",
    "DigitalOcean",
    "doctl",
    "https://docs.digitalocean.com/reference/doctl/how-to/install/",
    "doctl auth init   (paste a read/write API token)",
    {"s-1vcpu-2gb": "1 vCPU, 2 GB, 50 GB: $12/month", "s-2vcpu-2gb": "2 vCPU, 2 GB, 60 GB: $18/month"},
    "s-1vcpu-2gb",
    {
        "nyc3": "New York 3",
        "sfo3": "San Francisco 3",
        "ams3": "Amsterdam 3",
        "fra1": "Frankfurt 1",
        "lon1": "London 1",
        "sgp1": "Singapore 1",
    },
    "nyc3",
    "List prices as of 2026-09. The reserved IP costs nothing while it is assigned to the Droplet ($5/month if left unassigned).",
)
PROVIDERS = {"hetzner": HETZNER, "digitalocean": DIGITALOCEAN}


def find_ip(obj) -> str:
    """The first IPv4 address stored under an `ip` key anywhere in a CLI's JSON output."""
    if isinstance(obj, dict):
        v = obj.get("ip")
        if isinstance(v, str) and _is_v4(v):
            return v
        for x in obj.values():
            r = find_ip(x)
            if r:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = find_ip(x)
            if r:
                return r
    return ""


def _is_v4(v: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(v), ipaddress.IPv4Address)
    except ValueError:
        return False


def _json(res: Cli, what: str):
    try:
        return json.loads(res.stdout)
    except ValueError:
        raise CloudError(f"{what}: the CLI's answer was not JSON (a newer or older CLI version?)") from None


@dataclass
class Created:
    name: str
    ip: str
    server_id: str
    reused: bool = False


@dataclass
class Cloud:
    """One provider run. `cli` is the subprocess runner, replaced in tests."""

    provider: Provider
    name: str
    location: str
    size: str
    front_door: str
    ssh_key: str = ""
    cli: Callable[[list[str]], Cli] = run_cli
    say: Callable[[str], None] = print
    secrets: list[str] = field(default_factory=list)

    def _run(self, argv: list[str], what: str, ok_codes: tuple = (0,)) -> Cli:
        res = self.cli(argv)
        if res.returncode not in ok_codes:
            tail = envfile.scrub((res.stderr or res.stdout).strip()[-400:], self.secrets)
            raise CloudError(f"{what} failed ({self.provider.cli} exit {res.returncode}): {tail}")
        return res

    def _probe(self, argv: list[str]) -> Cli:
        return self.cli(argv)

    # -- shared -------------------------------------------------------------------------------------

    def check_auth(self) -> None:
        argv = (
            ["hcloud", "location", "list", "-o", "noheader"]
            if self.provider.key == "hetzner"
            else ["doctl", "account", "get", "--format", "Status", "--no-header"]
        )
        res = self.cli(argv)
        if res.returncode:
            raise CloudError(f"{self.provider.cli} is installed but not signed in. Run: {self.provider.login_hint}")

    def ssh_keys(self) -> list[str]:
        """The key to inject: the one asked for, else the account's only key, else none (console access only)."""
        if self.ssh_key:
            return [self.ssh_key]
        if self.provider.key == "hetzner":
            res = self.cli(["hcloud", "ssh-key", "list", "-o", "noheader", "-o", "columns=name"])
        else:
            res = self.cli(["doctl", "compute", "ssh-key", "list", "--format", "ID", "--no-header"])
        names = [l.strip() for l in res.stdout.splitlines() if l.strip()] if res.returncode == 0 else []
        return names if len(names) == 1 else []

    def inbound_ports(self, ssh: bool) -> list[tuple[str, str]]:
        ports = [("tcp", "22")] if ssh else []
        if self.front_door == "caddy":
            ports += [("tcp", "80"), ("tcp", "443"), ("udp", "443")]
        return ports

    def create(self, render: Callable[[str], str], ssh: bool, keys: list[str]) -> Created:
        """`render(ip)` returns the user-data once the server's address is known (the server waits for DNS to match it)."""
        with tempfile.TemporaryDirectory(prefix="tico-cloud-") as d:
            os.chmod(d, 0o700)
            if self.provider.key == "hetzner":
                return self._hetzner(d, render, ssh, keys)
            return self._digitalocean(d, render, ssh, keys)

    @staticmethod
    def _write(tmp: str, text: str) -> str:
        path = os.path.join(tmp, "user-data.yaml")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        return path

    # -- Hetzner ------------------------------------------------------------------------------------

    def _hetzner(self, tmp: str, render: Callable[[str], str], ssh: bool, keys: list[str]) -> Created:
        n, say = self.name, self.say
        labels = ["--label", f"ManagedBy={MANAGED}", "--label", f"{NAME_LABEL}={n}"]
        existing = self._probe(["hcloud", "server", "describe", n, "-o", "json"])
        if existing.returncode == 0:
            say(f"  Server {n} already exists; not creating a second one.")
            data = _json(existing, "server describe")
            return Created(n, find_ip(data.get("public_net", data)), str(data.get("id", "")), reused=True)
        ip = ""
        if self.front_door == "caddy":
            pip = self._probe(["hcloud", "primary-ip", "describe", n, "-o", "json"])
            if pip.returncode != 0:
                say(f"  Creating the public IPv4 {n} (kept if the server is deleted)")
                pip = self._run(
                    [
                        "hcloud",
                        "primary-ip",
                        "create",
                        "--name",
                        n,
                        "--type",
                        "ipv4",
                        "--location",
                        self.location,
                        "--auto-delete=false",
                        *labels,
                        "-o",
                        "json",
                    ],
                    "Creating the IPv4 address",
                )
            ip = find_ip(_json(pip, "primary-ip"))
            if not ip:
                raise CloudError("Could not read the new IPv4 address from hcloud's answer.")
        if self._probe(["hcloud", "firewall", "describe", n]).returncode != 0:
            rules = [
                {"direction": "in", "protocol": p, "port": port, "source_ips": ["0.0.0.0/0", "::/0"]}
                for p, port in self.inbound_ports(ssh)
            ]
            rules_path = os.path.join(tmp, "rules.json")
            with open(rules_path, "w") as f:
                json.dump(rules, f)
            say(
                f"  Creating the firewall {n}: "
                + (", ".join(f"{p}/{x}" for p, x in self.inbound_ports(ssh)) or "no inbound rules")
            )
            self._run(
                ["hcloud", "firewall", "create", "--name", n, "--rules-file", rules_path, *labels],
                "Creating the firewall",
            )
        path = self._write(tmp, render(ip))
        argv = [
            "hcloud",
            "server",
            "create",
            "--name",
            n,
            "--type",
            self.size,
            "--image",
            IMAGE["hetzner"],
            "--location",
            self.location,
            "--firewall",
            n,
            "--user-data-from-file",
            path,
            *labels,
            "-o",
            "json",
        ]
        if self.front_door == "caddy":
            argv += ["--primary-ipv4", n]
        for k in keys:
            argv += ["--ssh-key", k]
        say(f"  Creating the server {n} ({self.size}, {self.location})")
        res = self._run(argv, "Creating the server")
        data = _json(res, "server create")
        server = data.get("server", data)
        return Created(n, ip or find_ip(server.get("public_net", {})), str(server.get("id", "")))

    # -- DigitalOcean -------------------------------------------------------------------------------

    def _digitalocean(self, tmp: str, render: Callable[[str], str], ssh: bool, keys: list[str]) -> Created:
        n, say = self.name, self.say
        found = self._probe(
            ["doctl", "compute", "droplet", "list", "--tag-name", n, "--format", "ID,PublicIPv4", "--no-header"]
        )
        rows = [l.split() for l in found.stdout.splitlines() if l.strip()] if found.returncode == 0 else []
        if rows:
            say(f"  Droplet {n} already exists; not creating a second one.")
            did, ip = rows[0][0], (rows[0][1] if len(rows[0]) > 1 else "")
            return Created(n, self._reserved(did) or ip, did, reused=True)
        ip = ""
        if self.front_door == "caddy":
            say("  Reserving a stable IP (free while it is assigned to the Droplet)")
            r = self._run(
                [
                    "doctl",
                    "compute",
                    "reserved-ip",
                    "create",
                    "--region",
                    self.location,
                    "--format",
                    "IP",
                    "--no-header",
                ],
                "Reserving the IP address",
            )
            ip = (r.stdout.split() or [""])[0]
            if not _is_v4(ip):
                raise CloudError("Could not read the reserved IP from doctl's answer.")
        try:
            return self._do_create(tmp, render, ssh, keys, ip)
        except CloudError as e:
            if ip:
                raise CloudError(
                    f"{e}\n  The reserved IP {ip} is not assigned to anything and bills $5/month until it is used: "
                    f"release it with `doctl compute reserved-ip delete {ip}`."
                ) from None
            raise

    def _do_create(self, tmp: str, render: Callable[[str], str], ssh: bool, keys: list[str], ip: str) -> Created:
        n, say = self.name, self.say
        listed = self._probe(["doctl", "compute", "firewall", "list", "--format", "Name", "--no-header"])
        if n not in listed.stdout.split():
            everyone = "address:0.0.0.0/0,address:::/0"
            inbound = " ".join(f"protocol:{p},ports:{port},{everyone}" for p, port in self.inbound_ports(ssh))
            out = " ".join(
                [
                    f"protocol:tcp,ports:all,{everyone}",
                    f"protocol:udp,ports:all,{everyone}",
                    f"protocol:icmp,{everyone}",
                ]
            )
            say(
                f"  Creating the firewall {n}: "
                + (", ".join(f"{p}/{x}" for p, x in self.inbound_ports(ssh)) or "no inbound rules")
            )
            argv = ["doctl", "compute", "firewall", "create", "--name", n, "--tag-names", n, "--outbound-rules", out]
            if inbound:
                argv += ["--inbound-rules", inbound]
            self._run(argv, "Creating the firewall")
        path = self._write(tmp, render(ip))
        argv = [
            "doctl",
            "compute",
            "droplet",
            "create",
            n,
            "--region",
            self.location,
            "--size",
            self.size,
            "--image",
            IMAGE["digitalocean"],
            "--user-data-file",
            path,
            "--tag-names",
            f"{MANAGED},{n}",
            "--wait",
            "--format",
            "ID,PublicIPv4",
            "--no-header",
        ]
        if keys:
            argv += ["--ssh-keys", ",".join(keys)]
        say(f"  Creating the Droplet {n} ({self.size}, {self.location})")
        res = self._run(argv, "Creating the Droplet")
        parts = res.stdout.split()
        if len(parts) < 2 or not _is_v4(parts[1]):
            raise CloudError("Could not read the Droplet's id and address from doctl's answer.")
        did, droplet_ip = parts[0], parts[1]
        if self.front_door == "caddy":
            self._run(["doctl", "compute", "reserved-ip-action", "assign", ip, did], "Assigning the reserved IP")
        return Created(n, ip or droplet_ip, did)

    def _reserved(self, droplet_id: str) -> str:
        res = self._probe(["doctl", "compute", "reserved-ip", "list", "--format", "IP,DropletID", "--no-header"])
        for l in res.stdout.splitlines() if res.returncode == 0 else []:
            p = l.split()
            if len(p) == 2 and p[1] == droplet_id:
                return p[0]
        return ""


def manual_steps(
    provider: Provider, *, name: str, size: str, location: str, front_door: str, user_data_path: str, ssh: bool = True
) -> list[str]:
    """Copy-paste steps for someone without the CLI (or not signed in): dashboard first, CLI as the alternative."""
    ports = "80, 443" + (", 22" if ssh else "") if front_door == "caddy" else ("22" if ssh else "none")
    if provider.key == "hetzner":
        return [
            f"In the Hetzner Cloud Console (console.hetzner.cloud), open your project > Servers > Add Server:",
            f"  Location {location}; Image Ubuntu 24.04; Type {size} ({provider.sizes.get(size, size)})",
            f"  Networking: keep IPv4 (assign a Primary IP you keep if you want the address to survive a rebuild)",
            f"  SSH keys: add yours. Firewalls: create one allowing inbound {ports} only and attach it.",
            f"  Cloud config: paste the whole contents of {user_data_path}",
            f"  Name it {name}, then Create & Buy Now.",
            f"Or with the CLI (https://github.com/hetznercloud/cli):",
            f"  hcloud server create --name {name} --type {size} --image ubuntu-24.04 --location {location} \\",
            f"    --ssh-key <your-key> --user-data-from-file {user_data_path}",
        ]
    return [
        f"DigitalOcean has no link that pre-fills a Droplet with user data, so use the control panel or doctl:",
        f"  Control panel: Create > Droplets. Region {location}; Ubuntu 24.04 (LTS) x64; Basic, Regular, {size} ({provider.sizes.get(size, size)}).",
        f"  Authentication: your SSH key. Advanced options: tick Add Initialization scripts (free) and paste the whole",
        f"  contents of {user_data_path}. Name it {name}. Then Networking > Firewalls: allow inbound {ports} only.",
        f"  Networking > Reserved IPs: reserve one and assign it to the Droplet (free while assigned).",
        f"Or with the CLI (https://docs.digitalocean.com/reference/doctl/how-to/install/):",
        f"  doctl compute droplet create {name} --region {location} --size {size} --image ubuntu-24-04-x64 \\",
        f"    --ssh-keys <key-id-or-fingerprint> --user-data-file {user_data_path} --wait",
    ]
