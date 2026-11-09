"""Setup check: the checks `tico-setup doctor` runs (setup/verify.py), run by the server against its own address, plus
the failing lines of Health. Help shows the result with plain fixes and can attach it to a support request
(docs/support.md). Checks that need a shell on the server's machine (containers, the tunnel's log, .env permissions)
stay with doctor; the result says so instead of passing them."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from . import health
from .store import Problem

LOOPBACK = {"127.0.0.1", "localhost", "::1", ""}
EVERY = 30  # seconds between one person's runs: each run makes up to four outbound HTTPS checks
_last: dict[str, float] = {}
DOCTOR_ONLY = (
    "Server container, tunnel connector and .env permissions are checked on the server itself: "
    "run `sudo ./scripts/tico-setup doctor` in the Tico folder there."
)


def _provider(settings, contract):
    """The doctor's provider name for the built-in sign-in, or "" when sign-in is not Tico's own OIDC."""
    if (settings.auth_proxy or "").lower() != "oidc":
        return ""
    issuer = urlparse(settings.oidc_issuer or "").hostname or ""
    return next((name for name, host in contract.OIDC_HOSTS.items() if issuer == host), "")


def _row(check, source="doctor"):
    return {
        "name": check.name,
        "status": "ok" if check.ok else "bad",
        "detail": check.detail,
        "fix": check.hint,
        "source": source,
    }


def doctor_checks(settings, checks=None):
    """The doctor's HTTPS checks on this server's public and runner addresses, run side by side (each waits at most
    ~10 s). A Tico that serves only this computer has no HTTPS to check."""
    try:
        from setup import contract, verify  # the installer's own checks, shipped beside the server
    except ImportError:
        return [
            {
                "name": "HTTPS",
                "status": "info",
                "detail": "The setup checks are not part of this build.",
                "fix": DOCTOR_ONLY,
                "source": "doctor",
            }
        ]
    checks = checks or verify
    domain = urlparse(settings.public_url).hostname or ""
    if urlparse(settings.public_url).scheme != "https" or domain in LOOPBACK:
        return [
            {
                "name": "HTTPS",
                "status": "info",
                "detail": "This Tico serves this computer only, so there is no public address to check.",
                "fix": "",
                "source": "doctor",
            }
        ]
    runner = urlparse(settings.runner_url or "").hostname or ""
    provider = _provider(settings, contract)
    jobs = [lambda: checks.check_tls(domain), lambda: checks.check_health(domain)]
    if provider:
        jobs.append(lambda: checks.check_signin(domain, provider, settings.oidc_client_id))
    if runner and runner != domain:
        jobs.append(lambda: checks.check_runner_host(runner))
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(lambda job: job(), jobs))
    return [_row(check) for check in results]


def health_checks(view):
    """Health's lines that are not fine, worded as Setup check rows: the summary is the detail, its fixes the fix."""
    rows = []
    for check in view.get("checks") or []:
        if check.get("status") not in ("bad", "warn"):
            continue
        fixes = "; ".join(f.get("label", "") for f in check.get("fixes") or [] if f.get("label"))
        rows.append(
            {
                "name": check.get("label") or check.get("id") or "Health",
                "status": check["status"],
                "detail": check.get("summary") or "",
                "fix": fixes,
                "source": "health",
            }
        )
    return rows


def run(store, who, settings, auth, github, config_view, checks=None):
    key, at = who.actor, time.monotonic()
    if at - _last.get(key, -EVERY) < EVERY:
        raise Problem("setup_check_busy", "Setup check just ran. Try again in half a minute.", 429, retryable=True)
    _last[key] = at
    with store.read() as c:
        view = health.view(c, who, settings, auth, github, config_view(c))
    rows = doctor_checks(settings, checks) + health_checks(view)
    failing = sum(row["status"] == "bad" for row in rows)
    return {
        "checks": rows,
        "failing": failing,
        "doctor_only": DOCTOR_ONLY,
        "ran": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
