"""Generate the optional Caddy front door without changing signed-file authorization.

The app keeps its ordinary proxy. A separately opted-in visual site accepts only
unambiguous signed-token reads; the backend still verifies every token and reader.
No credentials or signing keys are written into this configuration.
"""
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


READY_SERVER = "tico_ready"
READY_ADDRESS = "127.0.0.1:20203"
READY_PATH = "/__tico_caddy_ready"
MARKER_PREFIX = "tico-caddy-generation:"
TOKEN_PATH = r"^/visuals/[A-Za-z0-9_-]+[.][A-Za-z0-9_-]{43}$"
RAW_TOKEN_PATH = r"^/visuals/[A-Za-z0-9_-]+[.][A-Za-z0-9_-]+$"
AUTH_HEADERS = ["Cookie", "Authorization", "Proxy-Authorization", "Cf-Access-*", "X-Amzn-Oidc-*", "X-Tico-*"]
LOG = logging.getLogger("tico.caddy")


def _hostname(value, *, allow_ip=False):
    if (not isinstance(value, str) or not 0 < len(value) <= 253
            or not value.isascii() or (not allow_ip and not re.search(r"[A-Za-z]", value))
            or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                       for part in value.split("."))):
        raise ValueError("hostname_invalid")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value.lower()
    if allow_ip:
        return value.lower()
    raise ValueError("hostname_is_ip")


def _visual_host(origin, app, runner):
    """Optional input fails closed, without reflecting a URL into diagnostics."""
    if not isinstance(origin, str) or not origin.strip():
        return "", "visual_origin_missing"
    value = origin.strip()  # Match existing settings' outer-whitespace normalization.
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return "", "visual_origin_control"
    try:
        parsed = urlsplit(value)
        host = _hostname(parsed.hostname)
        if "." not in host:
            return "", "visual_origin_not_dns_host"
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or "?" in value or "#" in value
                or parsed.netloc.lower() not in (host, host + ":443")):
            return "", "visual_origin_not_https_origin"
    except (ValueError, TypeError):
        return "", "visual_origin_invalid"
    if host in (app, runner):
        return "", "visual_origin_host_collision"
    return host, None


def _encoded(config):
    return json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _configuration(app_hostname, visual_origin, runner_hostname, serve_visuals):
    if type(serve_visuals) is not bool:
        raise ValueError("serve_visuals_invalid")
    app = _hostname(app_hostname, allow_ip=True) if app_hostname else ""
    runner = _hostname(runner_hostname) if runner_hostname else ""
    if runner and runner == app:
        raise ValueError("runner_hostname_collision")
    if serve_visuals and not app:
        raise ValueError("app_hostname_required")
    visual, reason = _visual_host(visual_origin, app, runner) if serve_visuals else ("", None)
    # Caddy's HTTP error logger includes request.uri even without access logs.
    # Remove the whole request object: headers can contain token-bearing Referer
    # values as well. Keep structured fault diagnostics at INFO and above.
    config = {"admin": {"disabled": True}, "logging": {"logs": {"default": {
        "level": "INFO", "encoder": {"format": "filter", "wrap": {"format": "json"},
                                      "fields": {"request": {"filter": "delete"}}}}}},
        "apps": {"http": {"servers": {}}}}
    if app:
        proxy = {"handler": "reverse_proxy", "upstreams": [{"dial": "server:8765"}]}
        routes = [{"match": [{"host": [app]}], "handle": [proxy], "terminal": True}]
        if visual:
            asset_proxy = {"handler": "reverse_proxy", "upstreams": [{"dial": "server:8765"}],
                           "headers": {"request": {"delete": list(AUTH_HEADERS)},
                                       "response": {"delete": ["Set-Cookie"]}}}
            routes.append({"match": [{"host": [visual], "method": ["GET", "HEAD"],
                "path_regexp": {"name": "signed_visual", "pattern": TOKEN_PATH},
                # PathRegexp cleans decoded paths. The original target must
                # independently match and equal the unchanged decoded path.
                "expression": "{http.request.orig_uri}.matches('" + RAW_TOKEN_PATH
                              + "') && {http.request.orig_uri} == {http.request.uri.path}"}],
                "handle": [asset_proxy], "terminal": True})
        routes.append({"handle": [{"handler": "static_response", "status_code": 404}], "terminal": True})
        config["apps"]["http"]["servers"]["tico"] = {
            "listen": [":443"], "tls_connection_policies": [{}], "routes": routes}
    # Hash the canonical base before inserting the entire readiness server, so
    # the marker is deterministic and occurs exactly once without self-reference.
    generation = hashlib.sha256(_encoded(config)).hexdigest()
    config["apps"]["http"]["servers"][READY_SERVER] = {
        "listen": [READY_ADDRESS], "automatic_https": {"disable": True}, "routes": [
            {"match": [{"method": ["GET"], "path": [READY_PATH],
                        "expression": "{http.request.orig_uri} == '" + READY_PATH + "'"}],
             "handle": [{"handler": "static_response", "status_code": 200,
                         "body": MARKER_PREFIX + generation}], "terminal": True},
            {"handle": [{"handler": "static_response", "status_code": 404}], "terminal": True}]}
    return config, generation, bool(visual), reason


def render(app_hostname, visual_origin="", runner_hostname="", *, serve_visuals=False):
    """Return deterministic JSON data; an empty app while off removes public sites.

    Invalid required app/runner names raise reason-only ValueError. An invalid
    optional visual origin produces app-only configuration, never an old site.
    External visual origins have no local effect unless serve_visuals is True.
    """
    return _configuration(app_hostname, visual_origin, runner_hostname, serve_visuals)[0]


def _runner_host(value, app):
    if not value:
        return ""
    # Only the hostname participates in collision checks; no runner route or
    # URL is changed. Canonical app runner URLs have no separate hostname.
    if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("runner_url_invalid")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        try:
            ipaddress.ip_address(host)
            is_ip = True
        except (ValueError, TypeError):
            host = _hostname(host)
            is_ip = False
        authority = "[" + host + "]" if ":" in host else host
        if (parsed.scheme not in ("https", "http") or parsed.username is not None
                or parsed.password is not None or parsed.port == 0
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or "?" in value or "#" in value
                or parsed.netloc.lower() != (authority if parsed.port is None else authority + ":" + str(parsed.port))):
            raise ValueError("runner_url_invalid")
    except (ValueError, TypeError):
        raise ValueError("runner_url_invalid") from None
    # Visual origins are DNS-only, so a valid IP runner endpoint cannot collide.
    # Its endpoint is only inspected here; no runner URL or route is changed.
    return "" if is_ip or host == app else host


def _present(path):
    """Only real absence is False: a path that cannot be inspected may hold an old generation."""
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        pass
    return True


def prepare(environ=os.environ):
    """Atomically replace desired config before health; return nonsecret metadata.

    A missing optional volume is inert while off, and so is an unwritable one that
    holds no generation: the image directory under a read-only root when an older
    compose mounts no volume. An unwritable directory with an old generation, or
    one that cannot be inspected, refuses, because that generation may still serve
    visuals. Writable mounted volumes always receive the new generation, including
    app-only/disabled states. Explicit opt-in with missing required app/volume or
    write failure refuses startup. Optional visual mistakes leave the app working
    with a reason-only warning.
    """
    directory = Path(environ.get("TICO_CADDY_DIR", "/caddy-generated"))
    enabled = environ.get("TICO_CADDY_VISUALS") == "1"
    inert = {"written": False, "path": None, "generation": None, "serve_visuals": False, "reason": None}
    if not _present(directory):
        if enabled:
            raise ValueError("caddy_config_directory_missing")
        return inert
    if not directory.is_dir():
        raise ValueError("caddy_config_directory_invalid")
    path = directory / "caddy.json"
    if not enabled and not os.access(directory, os.W_OK) and not _present(path):
        return inert
    app_value = environ.get("TICO_DOMAIN", "")
    app = _hostname(app_value, allow_ip=True) if app_value else ""
    if enabled and not app:
        raise ValueError("app_hostname_required")
    runner, runner_reason = "", None
    if enabled:
        try:
            runner = _runner_host(environ.get("TICO_RUNNER_URL", ""), app)
        except ValueError:
            runner_reason = "runner_url_invalid"
    config, generation, serving, reason = _configuration(
        app, environ.get("TICO_VISUAL_ORIGIN", ""), runner, enabled and runner_reason is None)
    reason = runner_reason or reason
    if reason:
        LOG.warning("Caddy visual serving disabled: %s", reason)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".caddy-", suffix=".json", dir=directory)
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(_encoded(config) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        raise RuntimeError("caddy_config_write_failed") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return {"written": True, "path": str(path), "generation": generation,
            "serve_visuals": serving, "reason": reason}
