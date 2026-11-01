"""Everything the wizard assumes about the Docker install (compose.yaml, .env.example, docs/install.md).

Kept in one place so a change on the Docker side is a change here and nowhere else.
"""
from __future__ import annotations

import os

# scripts/install.sh --dir sets this so the wizard and the installer agree on where the stack lives.
REMOTE_DIR = os.environ.get("TICO_INSTALL_DIR") or "/opt/tico"
# A starter plan too big for the env (hosted, --plan-bucket): the server copies it here at first boot, and compose.yaml
# mounts this folder read-only at COMPANY_FILE_MOUNT, where TICO_COMPANY_FILE names the file.
COMPANY_FILE_DIR = "company-file"
COMPANY_FILE_MOUNT = "/run/tico/company-file"
COMPANY_FILE_NAME = "plan.b64"
COMPOSE_RAW_URL = "https://raw.githubusercontent.com/ticoteam/tico/{ref}/compose.yaml"
SERVER_PORT = 8765
SERVICE_SERVER = "server"
# The tunnel's origin: compose.yaml's hold, in front of the server so a request waits out a restart. docker/entrypoint.sh
# writes the same.
TUNNEL_ORIGIN = "hold:8080"
UPDATER_URL = "http://updater:8080"

# Bots run on separate computers that join with a one-time code (the server runs none).
RUNNER_IMAGE = "ghcr.io/ticoteam/tico-runner"
RUNNER_CONTAINER = "tico-runner"
RUNNER_VOLUME = "tico-runner"
RUNNER_DIR = "/opt/tico-runner"
ENROLLMENTS_PATH = "/api/v2/enrollments"  # POST, owner auth: {"code", "expires_at", "operator"}; codes last 15 minutes, single use
COMPUTERS_PATH = "/api/v2/operations"  # GET, owner auth: {"machines": [{label, last_seen_at, revoked_at}], "server_time"}
ONLINE_SECONDS = 60  # a computer is online when it reported in this recently (backend/onboarding.py)

HEALTH_PATH = "/healthz"
REDIRECT_PATH = "/auth/callback"
# Sign-in starts here and must answer with a redirect to the identity provider.
SIGNIN_PATH = "/auth/login"
# A separate runner hostname serves only what runners and outside agents call; docker/entrypoint.sh writes the same path.
RUNNER_ROUTE_PATH = r"^/(?:api/v2|download)(?:/.*)?$"
# An /api/v2 route the server answers with no sign-in (backend/app.py), so a check can tell it from a login page.
RUNNER_PROBE_PATH = "/api/v2/agents/setup-script"

OIDC_ISSUERS = {
    "google": "https://accounts.google.com",
    "microsoft": "https://login.microsoftonline.com/{tenant}/v2.0",
}
OIDC_HOSTS = {"google": "accounts.google.com", "microsoft": "login.microsoftonline.com"}

KEY_ALLOWED_DOMAIN = "TICO_OIDC_ALLOWED_DOMAINS"  # comma-separated
DECISIONS_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}

SECRET_KEYS = frozenset({
    "TICO_OIDC_CLIENT_SECRET", "CLOUDFLARE_TUNNEL_TOKEN", "LITESTREAM_SECRET_ACCESS_KEY", "TICO_GATEWAY_INVITE",
    "TICO_INBOUND_KEY", "TICO_INBOUND_URL", "TICO_VISUAL_SECRET", "TICO_COMPANY_FILE_B64",
    *DECISIONS_KEYS.values(),
})
# Sent to the server only: the private copy of the .env setup keeps on this computer leaves these out.
SERVER_ONLY_KEYS = frozenset({"TICO_GATEWAY_INVITE"})

# Order they appear in the written .env.
ENV_ORDER = (
    "TICO_DOMAIN_ALIASES", "TICO_PUBLIC_URL", "TICO_CORS_ORIGINS", "TICO_HQ_URL", "TICO_GATEWAY_URL",
    "TICO_VISUAL_ORIGIN", "TICO_VISUAL_SAME_ORIGIN", "TICO_CADDY_VISUALS", "TICO_VISUAL_SECRET",
    "TICO_TRUSTED_PROXIES", "TICO_CLIENT_IP_HEADER", "TICO_EDGE_SUBNET", "TICO_EDGE_DYNAMIC_RANGE",
    "TICO_CADDY_ADDRESS", "TICO_CLOUDFLARED_ADDRESS",
    "TICO_TEAM_NAME", "TICO_COMPANY_NAME", "TICO_OWNER_EMAIL", "COMPOSE_PROFILES", "TICO_DOMAIN", "TICO_RUNNER_URL", "CLOUDFLARE_TUNNEL_TOKEN",
    "TICO_AUTH_PROXY", "TICO_OIDC_ISSUER", "TICO_OIDC_CLIENT_ID", "TICO_OIDC_CLIENT_SECRET", KEY_ALLOWED_DOMAIN, "TICO_OIDC_LABEL",
    "TICO_ACCESS_ISSUER", "TICO_ACCESS_AUDIENCE", "TICO_HOSTED", "TICO_OWNER_LINK_HASH", "TICO_INSTALL_PRESET",
    "TICO_SAMPLE_COMPANY", "TICO_MODELS_PRESET", "TICO_SUPPORT", "TICO_GATEWAY_INVITE", "TICO_INBOUND_KEY", "TICO_INBOUND_URL",
    *DECISIONS_KEYS.values(), "TICO_UPDATER_URL", "TICO_TAG",
)
# The hosted owner's sign-in link: the page on the server, and the command that replaces the link (backend/owner_link.py).
OWNER_LINK_PATH = "/auth/owner-link"
DATA_DIR = "/data"
DATABASE = DATA_DIR + "/hub.sqlite"


def profiles(front_door: str, updater: bool) -> str:
    return ",".join([front_door] + (["updater"] if updater else []))


def compose_url(ref: str = "main") -> str:
    return COMPOSE_RAW_URL.format(ref=ref)


def issuer(provider: str, tenant: str = "") -> str:
    return OIDC_ISSUERS[provider].format(tenant=tenant)
