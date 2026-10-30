"""The server image's start steps after the data volume is seeded, in one Python process (docker/entrypoint.sh).

`prepare DATA` runs what the entrypoint ran as separate Python processes, in the same order and with the same
messages and exit status: the install's permanent id, the Cloudflare tunnel's route, the Caddy files, and the
environment markers (backend/replication.py). It prints the id, which the entrypoint exports. Each process cost
about 0.12 s of interpreter start before its own imports, on every start.

A write whose result is already on disk is skipped: the id row, the tunnel route and the Caddy supervisor script.
The Caddy configuration (backend/caddy_assets.py) and the markers, which carry the time they were written, are
written on every start as before.

`tunnel-config` and `caddy-config` run one step each, for the entrypoint commands of the same names.
Only the standard library is imported until a step needs more, so the process starts quickly.
"""
import os
import re
import stat
import sys

ORIGIN = "http://server:8765"
RUNNER_PATH = "'^/(?:api/v2|download)(?:/.*)?$'"
VISUAL_PATH = "'^/visuals/'"
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _unchanged(path, content, mode=0o644):
    """Whether `path` is already a regular file with exactly `content` and `mode`."""
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
            return False
        with open(path, "rb") as handle:
            return handle.read() == content
    except OSError:
        return False


# The install's permanent id lives in the database, so a restore brings it back. TICO_ENVIRONMENT_ID sets it.
def environment_identity(env=os.environ):
    import json
    import secrets
    import sqlite3
    db = sqlite3.connect(env["TICO_DB"])
    try:
        row = db.execute("SELECT value_json FROM registry_metadata WHERE key='docker-environment-id'").fetchone()
        value = env.get("TICO_ENVIRONMENT_ID") or (json.loads(row[0]) if row else secrets.token_hex(8))
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", value):
            raise SystemExit("TICO_ENVIRONMENT_ID is lowercase letters, digits and hyphens")
        encoded = json.dumps(value)
        if row is None or row[0] != encoded:      # the same id already stored needs no write
            db.execute("INSERT INTO registry_metadata VALUES('docker-environment-id', ?) "
                       "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (encoded,))
            db.commit()
    finally:
        db.close()
    return value


# The Cloudflare tunnel's route. cloudflared's image has no shell and reads no environment for routes, and a tunnel that
# is run with only its token has none of its own ("No ingress rules ... cloudflared will return 503"), so the one route
# it needs is written here from TICO_DOMAIN into a volume the tunnel container reads: the domain to this server, anything
# else a 404. Written on every start, so a changed TICO_DOMAIN follows, and by the server image itself, so an update that
# replaces compose.yaml never needs a file the old updater does not know about. A tunnel managed in the Cloudflare
# dashboard keeps its own route: cloudflared prefers the configuration Cloudflare holds and uses this file only when the
# tunnel has none. docs/install.md, "Cloudflare Tunnel".

def runner_host(env, domain):
    """The host of TICO_RUNNER_URL when the tunnel should serve it too: a plain hostname other than TICO_DOMAIN.
    A loopback or IP address never reaches the tunnel, so it adds nothing. The same cuts the shell version made."""
    host = env.get("TICO_RUNNER_URL", "")
    scheme = host.find("://")
    if scheme >= 0:
        host = host[scheme + 3:]
    host = host.split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    host = host.translate(_ASCII_LOWER).rstrip("\n")
    if (not host or host == "localhost" or host.startswith(".") or host.endswith(".")
            or re.search(r"[^a-z0-9.-]", host) or not re.search(r"[^0-9.]", host)):
        return ""
    return "" if host == domain.translate(_ASCII_LOWER) else host


def visual_host(env, runner):
    """A visual origin must not turn its cookie-free hostname into another way to reach the whole app.
    Parse it before writing YAML: only a host that passes every check reaches the file. A bad value prints one warning
    naming the reason (never the value) and the config leaves out the visuals route; the server still starts, as the
    backend does without the setting (backend/visuals.py inline_on). Outer whitespace, such as a CR from an edited .env,
    is ignored like backend/config.py does."""
    import ipaddress
    from urllib.parse import urlsplit

    def refuse(reason):
        print("tico: warning: TICO_VISUAL_ORIGIN " + reason + "; the tunnel config leaves out the visuals route",
              file=sys.stderr)
        return ""

    if not env.get("TICO_VISUAL_ORIGIN", ""):
        return ""
    value = env["TICO_VISUAL_ORIGIN"].strip()
    if not value:
        return ""
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return refuse("has spaces or control characters")
    try:
        origin = urlsplit(value)
        host, port = origin.hostname or "", origin.port
    except ValueError:
        return refuse("is not a valid URL")
    if origin.scheme != "https":
        return refuse("is not an https:// address")
    if origin.path not in ("", "/") or origin.query or origin.fragment:
        return refuse("has a path, query or fragment; give only https://<host>")
    if origin.username is not None or origin.password is not None:
        return refuse("has a user name or password")
    if port == 0:
        return refuse("has port 0")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return refuse("is an IP address, not a hostname")
    if not (len(host) <= 253 and "." in host
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))):
        return refuse("is not a plain DNS hostname")
    if host == env["TICO_DOMAIN"].lower():
        return refuse("is the app's own host (TICO_DOMAIN); visuals need a separate site")
    if host == runner:
        return refuse("is the runner's host (TICO_RUNNER_URL); visuals need a separate site")
    return host


class Refused(Exception):
    """A setting that stops the start; its message is printed as the entrypoint's error."""


def domain_aliases(env, runner, visual):
    """An app alias has the whole app, unlike the runner and cookie-free visuals. Validate the complete list before
    replacing the config, so a typo cannot promote either restricted hostname or inject another YAML rule."""
    import ipaddress
    from urllib.parse import urlsplit

    def refuse(reason):
        raise Refused("tico: error: TICO_DOMAIN_ALIASES " + reason)

    def plain_hostname(host):
        return bool(host and len(host) <= 253 and "." in host
                    and any(ch in "abcdefghijklmnopqrstuvwxyz" for ch in host)
                    and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")))

    value = env.get("TICO_DOMAIN_ALIASES", "")
    if not value:
        return []
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        refuse("has control characters")
    if not value.isascii():
        refuse("must use plain ASCII DNS hostnames")
    if env.get("TICO_VISUAL_ORIGIN", "").strip() and not visual:
        refuse("needs a valid separate visual origin before app aliases can be routed")
    canonical = env["TICO_DOMAIN"].lower()
    runner_value = env.get("TICO_RUNNER_URL", "")
    if runner_value.strip():
        try:
            parsed = urlsplit(runner_value)
            runner_name, port = parsed.hostname or "", parsed.port
        except ValueError:
            refuse("needs a valid runner URL before app aliases can be routed")
        if (not runner_value.isascii() or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in runner_value)
                or parsed.scheme not in ("http", "https") or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None
                or port == 0 or not plain_hostname(runner_name)
                or runner_name != canonical and runner_name != runner):
            refuse("needs a valid runner URL before app aliases can be routed")
    seen = {canonical}
    aliases = []
    for part in value.split(","):
        host = part.strip(" ").lower()
        if not plain_hostname(host):
            refuse("must contain comma-separated plain DNS hostnames with no empty entries")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            refuse("must not contain an IP address")
        if host == runner or host == visual:
            refuse("collides with the restricted runner or visual hostname")
        if host not in seen:
            seen.add(host)
            aliases.append(host)
    return aliases


def tunnel_config(env=os.environ):
    """Writes the tunnel's route when there is a domain and a writable tunnel volume. Returns the exit status."""
    domain = env.get("TICO_DOMAIN", "")
    directory = env.get("TICO_TUNNEL_DIR") or "/tunnel"
    if not domain or not os.path.isdir(directory) or not os.access(directory, os.W_OK):
        return 0
    if re.search(r"[^A-Za-z0-9.-]", domain) or domain.startswith(".") or domain.endswith("."):
        print("tico: error: TICO_DOMAIN is not a plain hostname: " + domain, file=sys.stderr)
        return 1
    runner = runner_host(env, domain)
    visual = visual_host(env, runner)    # a bad value printed its warning and is left out
    try:
        aliases = domain_aliases(env, runner, visual)
    except Refused as refused:
        print(str(refused), file=sys.stderr)
        return 1
    lines = ["ingress:", "  - hostname: " + domain, "    service: " + ORIGIN]
    for host in aliases:
        lines += ["  - hostname: " + host, "    service: " + ORIGIN]
    if runner:
        # A separate runner hostname has no sign-in page in front of it, so it serves only what runners and
        # external agents call: the API (which checks their tokens) and the runner download.
        lines += ["  - hostname: " + runner, "    path: " + RUNNER_PATH, "    service: " + ORIGIN]
    if visual:
        lines += ["  - hostname: " + visual, "    path: " + VISUAL_PATH, "    service: " + ORIGIN]
    lines.append("  - service: http_status:404")
    content = ("\n".join(lines) + "\n").encode()
    path, temporary = os.path.join(directory, "cloudflared.yml"), os.path.join(directory, "cloudflared.yml.new")
    if _unchanged(path, content) and not os.path.lexists(temporary):
        return 0
    with open(temporary, "wb") as handle:
        handle.write(content)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    return 0


def caddy_config(env=os.environ):
    import tempfile
    from pathlib import Path
    from backend import caddy_assets

    directory = Path(env.get("TICO_CADDY_DIR", "/caddy-generated"))
    if directory.is_dir() and os.access(directory, os.W_OK):
        # Directory mounts expose atomic replacements; a single-file bind could retain an older inode.
        source = (Path(caddy_assets.__file__).resolve().parent.parent / "docker" / "caddy-supervisor.sh").read_bytes()
        if not _unchanged(directory / "supervisor.sh", source):
            descriptor, temporary = tempfile.mkstemp(prefix=".supervisor-", dir=directory)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(source)
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.fchmod(handle.fileno(), 0o644)
                os.replace(temporary, directory / "supervisor.sh")
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    caddy_assets.prepare()


def mark_environment(data_dir, environment_id, env=os.environ):
    from backend import replication
    replication.write_markers(data_dir, environment_id,
                              None if replication.no_mirror(env) else replication.mirror_from_env(env))


def prepare(data_dir):
    environment_id = environment_identity()
    os.environ["TICO_ENVIRONMENT_ID"] = environment_id    # what the entrypoint exported before the next steps
    status = tunnel_config()
    if status:
        return status
    caddy_config()
    mark_environment(data_dir, environment_id)
    print(environment_id)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "prepare":
        return prepare(argv[1])
    if argv == ["tunnel-config"]:
        return tunnel_config()
    if argv == ["caddy-config"]:
        caddy_config()
        return 0
    print("usage: python -m backend.boot prepare DATA|tunnel-config|caddy-config", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
