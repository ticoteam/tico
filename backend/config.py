"""Explicit service configuration; importing the backend never opens the live Hub DB."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .replication import rehearsal_on

ROOT = Path(__file__).resolve().parents[1]

# The upstream project's own name. Every environment renames itself through TICO_APP_NAME
# and TICO_ASSISTANT_NAME; nothing below carries a company's name, domain or people.
PRODUCT_NAME = "Tico"
ASSISTANT_NAME = "Assistant"        # what the assistant is called until a company names it


def _release_origin():
    """The commit and repository in the release manifest beside the code, when this is a release."""
    try:
        manifest = json.loads((ROOT / "release-manifest.json").read_text())
    except (OSError, ValueError):
        return {}
    return {"release_commit": str(manifest.get("commit") or ""), "release_repo": str(manifest.get("repository") or "")}
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _typesafe_key():
    """The optional TypeSafe (Jev) decisions key behind `POST /api/v2/decisions` and the Slack gateway."""
    value = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if value:
        return value
    secret = os.environ.get("TICO_TYPESAFE_SECRET_ARN", "").strip()
    if secret:
        try:
            import boto3
            return boto3.client("secretsmanager", region_name=os.environ.get("AWS_DEFAULT_REGION") or None) \
                .get_secret_value(SecretId=secret).get("SecretString", "").strip()
        except Exception:
            return ""
    try:
        return Path("/etc/tico/typesafe-api-key").read_text().strip()
    except OSError:
        return ""


def _emails(value):
    return tuple(dict.fromkeys(part.strip().lower() for part in str(value or "").split(",") if part.strip()))


def slack_credentials(settings=None):
    """The Slack gateway's tokens, read the way `_typesafe_key` reads the decisions key and nowhere else.

    Only `python -m backend.slack_gateway` calls this: the API process never holds a Slack token.
    Precedence: `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN` in the environment, then the JSON secret at
    `TICO_SLACK_SECRET_ARN` (`{"bot_token","app_token","team_id","app_id"}`), then
    `/etc/tico/slack` in the same shape for a self-hosted instance. `team_id` and `app_id` from
    `SLACK_TEAM_ID`/`SLACK_APP_ID` win over the secret's, so the workspace the gateway will
    accept events from is fixed in the reviewed server configuration.
    """
    data = {}
    bot, app = os.environ.get("SLACK_BOT_TOKEN", "").strip(), os.environ.get("SLACK_APP_TOKEN", "").strip()
    if not (bot and app):
        secret = (settings.slack_secret_arn if settings else os.environ.get("TICO_SLACK_SECRET_ARN", "")).strip()
        if secret:
            try:
                import boto3
                value = boto3.client("secretsmanager", region_name=os.environ.get("AWS_DEFAULT_REGION") or None) \
                    .get_secret_value(SecretId=secret).get("SecretString", "")
                data = json.loads(value or "{}")
            except Exception as exc:
                # The gateway refuses to start without tokens; say why in its log, never the value.
                logging.getLogger("tico.slack").error("Slack secret could not be read: %s", type(exc).__name__)
                data = {}
        else:
            try:
                data = json.loads(Path("/etc/tico/slack").read_text() or "{}")
            except (OSError, ValueError):
                data = {}
    return {"bot_token": bot or str(data.get("bot_token") or "").strip(),
            "app_token": app or str(data.get("app_token") or "").strip(),
            "team_id": ((settings.slack_team_id if settings else os.environ.get("SLACK_TEAM_ID", ""))
                        or str(data.get("team_id") or "")).strip(),
            "app_id": ((settings.slack_app_id if settings else os.environ.get("SLACK_APP_ID", ""))
                       or str(data.get("app_id") or "")).strip()}


@dataclass
class Settings:
    db_path: Path
    registry_dir: Path = ROOT / "registry"
    # The bot templates onboarding offers. A deployment may ship its own set of cards.
    catalog_dir: Path = ROOT / "templates" / "catalog"
    ui_dir: Path = ROOT / "ui"
    integrations_dir: Path = ROOT / "integrations"
    # A company's own pages and query catalogs, layered over integrations_dir; None means
    # <registry_dir>/integrations.
    company_integrations_dir: Path | None = None
    environment_id: str = ""
    company_name: str = "Company"
    app_name: str = PRODUCT_NAME
    assistant_name: str = ASSISTANT_NAME
    assistant_bot: str = "coo"
    owner_email: str = ""
    github_owner: str = ""
    credential_admins: tuple[str, ...] = ()
    local_owner_token_file: Path | None = None
    # Local-only bearer auth when Access is unset (see Auth.identity_from_local_owner_token).
    local_owner_email: str = ""
    local_owner_token: str = field(default="", repr=False)
    public_url: str = "http://127.0.0.1:8765"
    runner_url: str = ""
    owner_name: str = ""
    compose_project: str = "tico"
    server_network: str = "tico_default"
    access_issuer: str = ""
    access_audience: str = ""
    # Empty means Cloudflare Access's default JWKS path under the issuer.
    access_jwks_url: str = ""
    # cloudflare | aws-alb | oidc. Empty means cloudflare when an Access issuer is set, else no proxy.
    auth_proxy: str = ""
    alb_arn: str = ""
    alb_region: str = ""
    # Base URL of the ALB public keys, overridable so tests need no network.
    alb_keys_url: str = ""
    # Where the identity provider ends its own session (a Cognito /logout URL with its
    # client_id and logout_uri); without it, logout returns to the app's root.
    cognito_logout_url: str = ""
    # Built-in sign-in (TICO_AUTH_PROXY=oidc, backend/oidc.py). The secret is read from its file
    # when one is named; the session secret defaults to a generated file beside the database.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = field(default="", repr=False)
    oidc_client_secret_file: Path | None = None
    oidc_allowed_domains: tuple[str, ...] = ()
    session_secret: str = field(default="", repr=False)
    # Off: a bot may invite anyone to a calendar event. On: only people on the roster (403 external_attendee).
    block_external_invites: bool = False
    # Health warns about a bot whose uncached input tokens pass this in a day (TICO_TOKEN_ALERT_INPUT); 0 is off.
    token_alert_input: int = 100_000_000
    session_idle_seconds: int = 30 * 86400
    session_absolute_seconds: int = 90 * 86400
    # Origins of browser apps allowed to call the API from another address (TICO_CORS_ORIGINS,
    # backend/cors.py): exact origins only, checked in __post_init__.
    cors_origins: tuple[str, ...] = ()
    lease_seconds: int = 90
    scheduler_enabled: bool = False
    # The flight recorder (backend/flight.py): request, process and database history in this database. On unless
    # TICO_FLIGHT_RECORDER=0; off by default here so tests opt in.
    flight_recorder: bool = False
    blob_dir: Path | None = None
    blob_bucket: str = ""
    blob_region: str = ""
    blob_endpoint: str = ""
    blob_credentials: str = "auto"
    blob_prefix: str = field(default="", init=False)
    upload_max_bytes: int = 2 * 1024 ** 3
    # {bucket: https base} from TICO_S3_VIEW_URLS (clients/s3links.py): where people open a bucket's objects.
    s3_view_urls: dict = field(default_factory=dict)
    processing_operators: tuple[str, ...] = ()
    mail_retention_days: int = 180
    release_id: str = ""
    # The commit and repository the running release was built from (a release-manifest.json
    # at the repository root, when one is present); what marks a merged product task shipped.
    release_commit: str = ""
    release_repo: str = ""
    # Signs the repository webhook that moves product tasks (backend/github.py). Unset: no webhook.
    github_webhook_secret: str = field(default="", repr=False)
    # The task role a GitHub review request puts its reviewer in (backend/github.py), matched by the
    # person's `github` login on the roster. Unset: review requests change no roles.
    github_review_role: str = ""
    observability_environment: str = ""
    observability_id_secret: str = field(default="", repr=False)
    posthog_key: str = field(default="", repr=False)
    posthog_host: str = field(default="", repr=False)
    sentry_dsn: str = field(default="", repr=False)
    sentry_server_dsn: str = field(default="", repr=False)
    typesafe_api_key: str = field(default="", repr=False)
    # First-boot seed for the owner's AI provider choice (backend/providers.py). Once the
    # database holds a choice, these are never read again.
    enabled_providers: tuple[str, ...] = ()
    default_runtime: str = ""
    default_model: str = ""
    credential_kms_key: str = ""
    # The Slack gateway (backend/slack_gateway.py): off unless the server configuration says so.
    # The thresholds are the routing rules in docs/slack-gateway.md; the tokens are read by
    # `slack_credentials()` in the gateway process only.
    slack_gateway_enabled: bool = False
    slack_team_id: str = ""
    slack_app_id: str = ""
    slack_secret_arn: str = ""
    slack_route_threshold: float = 0.6
    slack_ask_threshold: float = 0.5
    slack_max_recipients: int = 3
    # Channels read like an employee reads them: every message in a channel Tico is in is stored;
    # the readers a channel names in the list in Settings > Tools > Slack get what is unread every
    # `slack_digest_minutes` (0 pauses delivery, storage goes on), at most `slack_digest_cap`
    # messages per channel per pass.
    slack_digest_minutes: int = 60
    slack_digest_cap: int = 200
    # Test-only identities are injected in memory, never enabled through HTTP or an env flag.
    test_identities: dict = field(default_factory=dict, repr=False)
    # `python -m backend.demo`: sample data, no bots, no outbound calls (backend/demo.py). A public
    # demo is one someone put on a non-loopback address on purpose; it is read-only.
    demo: bool = False
    demo_public: bool = False
    # TICO_REHEARSAL=1: a copy of real data run to see whether a migration works. Nothing runs on a timer and nothing
    # leaves the server: no scheduler, directory sync, backups, release check, usage count, support or HQ calls,
    # GitHub or Slack, telemetry or updater (docs/install.md, "Rehearse a migration"). Migrations run as usual.
    rehearsal: bool = False

    def __post_init__(self):
        self.blob_credentials = self.blob_credentials.strip().lower() or "auto"
        if self.blob_credentials not in ("auto", "role", "backup", "keys"):
            raise ValueError("TICO_BLOB_CREDENTIALS must be auto, role, backup or keys")
        bucket = self.blob_bucket.removeprefix("s3://").strip("/")
        self.blob_bucket, _, self.blob_prefix = bucket.partition("/")
        self.blob_prefix = self.blob_prefix.rstrip("/")
        from clients import s3links
        self.s3_view_urls = s3links.parse(self.s3_view_urls)
        self.public_url = self.public_url.rstrip("/")
        self.auth_proxy = self.auth_proxy.strip().lower()
        from . import cors
        self.cors_origins = cors.parse(self.cors_origins)
        from . import identity_proxy        # imports the store, which imports this module
        identity_proxy.check(self)
        self.runner_url = (self.runner_url or self.public_url).rstrip("/")
        self.owner_email = (self.owner_email or self._roster_owner()).strip().lower()
        # Unless the server names its credential administrators, they are whoever owns the environment now and its
        # Admins (backend/auth.py sync_access follows a transfer and the Admins list).
        self.credential_admins_follow_owner = not _emails(",".join(self.credential_admins))
        self.credential_admins = _emails(",".join(self.credential_admins)) or tuple(
            filter(None, [self.owner_email]))
        if self.local_owner_token_file and not self.loopback:
            raise RuntimeError(
                "Local owner sign-in mints its own session and is only safe on loopback; "
                + self.public_url + " is reachable by others. Unset TICO_LOCAL_OWNER_TOKEN_FILE "
                "or point TICO_PUBLIC_URL at 127.0.0.1.")

    def _roster_owner(self):
        """Deployments made before TICO_OWNER_EMAIL name their owner in registry/hub-access.yaml."""
        try:
            document = yaml.safe_load((self.registry_dir / "hub-access.yaml").read_text())
        except OSError:
            return ""
        return str((document or {}).get("owner") or "")

    @property
    def proxy_kind(self):
        """The selected identity proxy, "" when browsers sign in some other way (loopback)."""
        return self.auth_proxy or ("cloudflare" if self.access_issuer else "")

    @property
    def loopback(self):
        return (urlparse(self.public_url).hostname or "") in LOOPBACK

    @property
    def local_signin(self):
        """Whether a loopback owner session may be minted without an identity proxy."""
        return bool(self.local_owner_token_file)

    def environment(self):
        """What a client needs to render this environment: names, links and sign-in mode."""
        return {"environment_id": self.environment_id, "company_name": self.company_name,
                "app_name": self.app_name, "assistant_name": self.assistant_name,
                "assistant_bot": self.assistant_bot, "public_url": self.public_url,
                "runner_url": self.runner_url, "github_owner": self.github_owner,
                "compose_project": self.compose_project, "server_network": self.server_network,
                "local": self.local_signin, "release": self.release_id,
                "owner_email": self.owner_email, "owner_name": self.owner_name, "demo": self.demo, "rehearsal": self.rehearsal}

    def allows_origin(self, origin):
        """Whether a browser write comes from this server's own page. A demo is opened by whatever
        loopback name and published port the person used, which the server cannot know."""
        if origin == self.public_url or origin in self.cors_origins:
            return True
        if self.demo and not self.demo_public and (urlparse(origin).hostname or "") in LOOPBACK:
            return True
        if self.loopback and self.local_signin:
            # A local install answers to 127.0.0.1 and to localhost on its own port; people type either.
            seen, own = urlparse(origin), urlparse(self.public_url)
            return (seen.scheme == own.scheme and (seen.hostname or "") in LOOPBACK
                    and seen.port == own.port)
        return False

    @classmethod
    def from_env(cls):
        path = os.environ.get("TICO_DB")
        if not path:
            raise RuntimeError("TICO_DB must explicitly name the cloud service database")
        environment_id = os.environ.get("TICO_ENVIRONMENT_ID", "").strip()
        company = os.environ.get("TICO_TEAM_NAME", "").strip() or os.environ.get("TICO_COMPANY_NAME", "").strip()
        if company and not environment_id:
            raise RuntimeError("TICO_ENVIRONMENT_ID must give this team a permanent, opaque "
                               "identity whenever TICO_TEAM_NAME (or TICO_COMPANY_NAME) is set")
        token_file = os.environ.get("TICO_LOCAL_OWNER_TOKEN_FILE", "").strip()
        rehearsal = rehearsal_on()
        # Whatever else the environment holds, a rehearsal cannot reach the services it would otherwise talk to.
        quiet = (lambda value: "") if rehearsal else (lambda value: value)
        settings = cls(
            db_path=Path(path),
            registry_dir=Path(os.environ["TICO_REGISTRY_DIR"]) if os.environ.get("TICO_REGISTRY_DIR") else ROOT / "registry",
            company_integrations_dir=Path(os.environ["TICO_INTEGRATIONS_DIR"]) if os.environ.get("TICO_INTEGRATIONS_DIR") else None,
            catalog_dir=Path(os.environ["TICO_CATALOG_DIR"]) if os.environ.get("TICO_CATALOG_DIR") else ROOT / "templates" / "catalog",
            environment_id=environment_id,
            company_name=company or "Company",
            app_name=os.environ.get("TICO_APP_NAME", "").strip() or PRODUCT_NAME,
            assistant_name=os.environ.get("TICO_ASSISTANT_NAME", "").strip() or ASSISTANT_NAME,
            assistant_bot=os.environ.get("TICO_ASSISTANT_BOT", "").strip() or "coo",
            owner_email=os.environ.get("TICO_OWNER_EMAIL", ""),
            # TICO_ROUTINES_GITHUB_OWNER is the older name for the same organization.
            github_owner=(os.environ.get("TICO_GITHUB_OWNER", "").strip()
                          or os.environ.get("TICO_ROUTINES_GITHUB_OWNER", "").strip()),
            credential_admins=_emails(os.environ.get("TICO_CREDENTIAL_ADMINS", "")),
            local_owner_token_file=Path(token_file) if token_file else None,
            local_owner_email=os.environ.get("TICO_LOCAL_OWNER_EMAIL", "").strip().lower(),
            local_owner_token=os.environ.get("TICO_LOCAL_OWNER_TOKEN", "").strip(),
            public_url=(os.environ.get("TICO_PUBLIC_URL") or
                        "http://127.0.0.1:" + os.environ.get("TICO_PORT", "8765")).rstrip("/"),
            owner_name=os.environ.get("TICO_OWNER_NAME", "").strip(),
            compose_project=os.environ.get("TICO_COMPOSE_PROJECT", "tico"),
            server_network=os.environ.get("TICO_SERVER_NETWORK", "tico_default"),
            runner_url=os.environ.get("TICO_RUNNER_URL", "").rstrip("/"),
            access_issuer=os.environ.get("TICO_ACCESS_ISSUER", "").rstrip("/"),
            access_audience=os.environ.get("TICO_ACCESS_AUDIENCE", ""),
            access_jwks_url=os.environ.get("TICO_ACCESS_JWKS_URL", "").strip(),
            auth_proxy=os.environ.get("TICO_AUTH_PROXY", ""),
            alb_arn=os.environ.get("TICO_ALB_ARN", "").strip(),
            alb_region=os.environ.get("TICO_ALB_REGION", "").strip(),
            alb_keys_url=os.environ.get("TICO_ALB_KEYS_URL", "").strip(),
            cognito_logout_url=os.environ.get("TICO_COGNITO_LOGOUT_URL", "").strip(),
            oidc_issuer=os.environ.get("TICO_OIDC_ISSUER", "").strip(),
            oidc_client_id=os.environ.get("TICO_OIDC_CLIENT_ID", "").strip(),
            oidc_client_secret=os.environ.get("TICO_OIDC_CLIENT_SECRET", "").strip(),
            oidc_client_secret_file=(Path(os.environ["TICO_OIDC_CLIENT_SECRET_FILE"])
                                     if os.environ.get("TICO_OIDC_CLIENT_SECRET_FILE") else None),
            oidc_allowed_domains=_emails(os.environ.get("TICO_OIDC_ALLOWED_DOMAINS", "")),
            session_secret=os.environ.get("TICO_SESSION_SECRET", "").strip(),
            block_external_invites=os.environ.get("TICO_BLOCK_EXTERNAL_INVITES", "0") == "1",
            token_alert_input=int(os.environ.get("TICO_TOKEN_ALERT_INPUT", "").strip() or 100_000_000),
            session_idle_seconds=max(60, int(os.environ.get("TICO_SESSION_IDLE_SECONDS", "") or 30 * 86400)),
            session_absolute_seconds=max(60, int(os.environ.get("TICO_SESSION_ABSOLUTE_SECONDS", "") or 90 * 86400)),
            cors_origins=os.environ.get("TICO_CORS_ORIGINS", ""),
            scheduler_enabled=not rehearsal and os.environ.get("TICO_SCHEDULER", "1") == "1",
            flight_recorder=os.environ.get("TICO_FLIGHT_RECORDER", "1") != "0",
            rehearsal=rehearsal,
            blob_dir=Path(os.environ["TICO_BLOB_DIR"]) if os.environ.get("TICO_BLOB_DIR") else None,
            blob_bucket=os.environ.get("TICO_BLOB_BUCKET", ""),
            blob_region=os.environ.get("TICO_BLOB_REGION", ""),
            blob_endpoint=os.environ.get("TICO_BLOB_ENDPOINT", ""),
            blob_credentials=os.environ.get("TICO_BLOB_CREDENTIALS", "auto"),
            upload_max_bytes=int(os.environ.get("TICO_UPLOAD_MAX_BYTES") or 2 * 1024 ** 3),
            s3_view_urls=os.environ.get("TICO_S3_VIEW_URLS", ""),
            processing_operators=tuple(filter(None, os.environ.get("TICO_PROCESSING_OPERATORS", "").split(","))),
            mail_retention_days=max(1, int(os.environ.get("TICO_MAIL_RETENTION_DAYS", "180") or "180")),
            release_id=os.environ.get("TICO_RELEASE", ""),
            **_release_origin(),
            github_webhook_secret=os.environ.get("TICO_GITHUB_WEBHOOK_SECRET", "").strip(),
            github_review_role=os.environ.get("TICO_GITHUB_REVIEW_ROLE", "").strip().lower(),
            observability_environment=os.environ.get("TICO_OBSERVABILITY_ENVIRONMENT", ""),
            observability_id_secret=os.environ.get("TICO_OBSERVABILITY_ID_SECRET", ""),
            posthog_key=quiet(os.environ.get("TICO_POSTHOG_KEY", "")),
            posthog_host=quiet(os.environ.get("TICO_POSTHOG_HOST", "")),
            sentry_dsn=quiet(os.environ.get("TICO_SENTRY_DSN", "")),
            sentry_server_dsn=quiet(os.environ.get("TICO_SENTRY_SERVER_DSN", "")),
            typesafe_api_key="" if rehearsal else _typesafe_key(),
            enabled_providers=tuple(part.strip().lower() for part in
                                    os.environ.get("TICO_ENABLED_PROVIDERS", "").split(",") if part.strip()),
            default_runtime=os.environ.get("TICO_DEFAULT_RUNTIME", "").strip().lower(),
            default_model=os.environ.get("TICO_DEFAULT_MODEL", "").strip(),
            credential_kms_key=os.environ.get("TICO_CREDENTIAL_KMS_KEY", ""),
            slack_gateway_enabled=not rehearsal and os.environ.get("TICO_SLACK_GATEWAY_ENABLED", "0") == "1",
            slack_team_id=os.environ.get("SLACK_TEAM_ID", "").strip(),
            slack_app_id=os.environ.get("SLACK_APP_ID", "").strip(),
            slack_secret_arn=os.environ.get("TICO_SLACK_SECRET_ARN", "").strip(),
            slack_route_threshold=float(os.environ.get("TICO_SLACK_ROUTE_THRESHOLD", "0.6") or "0.6"),
            slack_ask_threshold=float(os.environ.get("TICO_SLACK_ASK_THRESHOLD", "0.5") or "0.5"),
            slack_max_recipients=max(1, int(os.environ.get("TICO_SLACK_MAX_RECIPIENTS", "3") or "3")),
            slack_digest_minutes=max(0, int(os.environ.get("TICO_SLACK_DIGEST_MINUTES", "60") or "60")),
            slack_digest_cap=max(1, int(os.environ.get("TICO_SLACK_DIGEST_CAP", "200") or "200")),
        )
        # Local by default, public only with sign-in: no domain and no sign-in setup runs on loopback, but a server
        # that others can reach never starts without a way to sign in.
        if not settings.loopback and not settings.proxy_kind and not settings.demo and not (
                settings.local_owner_email and settings.local_owner_token):
            raise RuntimeError(
                settings.public_url + " is reachable by others, so it needs sign-in. Set TICO_AUTH_PROXY "
                "(oidc, cloudflare or aws-alb), or point TICO_PUBLIC_URL at 127.0.0.1 to run on this computer only.")
        return settings
