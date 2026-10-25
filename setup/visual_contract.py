"""Versioned, nonsecret operator contract for same-installation asset configuration."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid

PROTOCOL = "tico-hosted-assets-v1"
PURPOSE = "same-instance-applied-assets"
MANAGED = ("TICO_VISUAL_ORIGIN", "TICO_CADDY_VISUALS", "TICO_VISUAL_SAME_ORIGIN")
RUNTIME_FILES = ("setup/visual_contract.py", "backend/visual_config.py", "backend/caddy_assets.py",
                 "backend/visuals.py", "docker/caddy-supervisor.sh", "docker/entrypoint.sh")
FIELDS = frozenset("operation_id revision account_id company_id workspace_id preview_id attempt_id address "
                   "public_hostname enabled lease_id hostname receipt instance_id install_id challenge".split())
HEX = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"[0-9a-f]{40}")
TOKEN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}")


class Pending(ValueError):
    """Only fixed reason codes cross the operator transport."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise Pending("duplicate_field")
        out[key] = value
    return out


def decode(raw, limit=4096):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise Pending("payload_invalid")
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise Pending("payload_invalid") from None


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def uuid4(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4
    except ValueError:
        return False


def hostname(value):
    return (isinstance(value, str) and 1 < len(value) <= 253 and "." in value
            and all(matches(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", s) for s in value.split(".")))


def validate(request):
    if not isinstance(request, dict) or set(request) != FIELDS:
        raise Pending("request_invalid")
    if (not all(uuid4(request[k]) for k in ("account_id", "company_id", "workspace_id", "install_id"))
            or not all(matches(r"[A-Za-z0-9_-]{8,100}", request[k]) for k in ("operation_id", "preview_id", "attempt_id"))
            or type(request["revision"]) is not int or not 1 <= request["revision"] <= 2**63 - 1
            or type(request["enabled"]) is not bool or not matches(r"i-[0-9a-f]{8,17}", request["instance_id"])
            or not matches(HEX, request["challenge"])
            or (request["lease_id"] is not None and not uuid4(request["lease_id"]))
            or (request["enabled"] and (request["lease_id"] is None or not matches(r"[0-9a-f]{32}", request["receipt"])))
            or not matches(r"(?:[0-9a-f]{32})?", request["receipt"])):
        raise Pending("request_invalid")
    public = request["public_hostname"]
    slug = public.removesuffix(".tico.dev") if isinstance(public, str) else ""
    if (not matches(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", slug) or slug == "hub"
            or public != slug + ".tico.dev" or request["hostname"] != slug + ".ticofiles.com"
            or not hostname(request["address"]) or not matches(r"pv-[a-z0-9]{8,32}\..+", request["address"])):
        raise Pending("request_invalid")
    if len(encoded(request)) > 4096:
        raise Pending("request_invalid")
    return request


def from_argument(value):
    if not matches(r"[A-Za-z0-9_-]{1,5462}", value):
        raise Pending("request_invalid")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError:
        raise Pending("request_invalid") from None
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise Pending("request_invalid")
    return validate(decode(raw))


def configuration(request):
    return dict(zip(MANAGED, ("https://" + request["hostname"] if request["enabled"] else "",
                              "1" if request["enabled"] else "0", "0")))


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def checks(enabled):
    common = dict.fromkeys(("saved_configuration", "running_configuration", "applied_generation", "app_auth_routes_refused"), True)
    return common | dict.fromkeys(("signed_read", "unsigned_refused", "credentials_stripped", "response_cookies_absent")
                                   if enabled else ("listener_retired", "signed_read_refused"), True)


def receipt(request, generation):
    value = {"protocol": PROTOCOL, "purpose": PURPOSE, "request": request,
             "config_digest": digest(configuration(request)), "generation": generation,
             "checks": checks(request["enabled"])}
    if not matches(HEX, generation) or len(encoded(value)) > 2048:
        raise Pending("receipt_invalid")
    return value
