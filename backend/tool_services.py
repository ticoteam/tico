"""The services Tico knows how to reach with a key: where their API lives, how the key goes on a request, and a cheap
GET that proves the key works.

An API tool (backend/api_tools.py) names one of these as its `service`; a new tool fills its empty base URL, auth scheme
and check path from here, and the data migration that made a tool for every stored key matched them the same way. A
check path is an authenticated GET that answers 2xx for a good key and 401 (403 for Vercel: `rejected`) for a bad one:
each was tried with a made-up key. Any other service's 403 answers a key it knows that lacks the path's scope. Services
whose API answers a bad key with something else (400 from Gemini, xAI, Resend and Attio, 410 from Todoist, 200 from
Apollo) or has no cheap GET (Linear's GraphQL; Slack's auth.test answers 200 with ok:false) get no check path, since a
check that cannot tell a bad key from a good one would show Working for both. Their calls still go through the proxy.
A tool with no check path of its own checks with its service's when it is at that service's address
(`check_path_for`), so one made before its service had a path is checked too. Google Ads
is known by name only: its OAuth needs several values on each call, which no proxy key can carry, so it has no base URL
and its tools stay on computers.

`apply_auth` is the one place a key is put on a request, shared by the check here and the proxy (track B), so the two
can never disagree about where a service expects its key. `is_tool_key` and `insert_tool` are the one rule for which
stored key is a tool's and how its tool is made, shared by the data migration (on a raw connection) and
`api_tools.ensure_tool` (every key stored since), so a key stored either way lands on Tools alike.
"""

import base64
import functools
import re
import sqlite3

from .slugs import slugify

AUTH_SCHEME = re.compile(r"^(bearer|basic_user|basic_password|header:[A-Za-z0-9-]{1,60}|query:[A-Za-z0-9_.-]{1,60})$")

# Background adapters use only an administrator's opt-in and company keys without a personal holder.
RECORD_INTEGRATION = (
    "tl.audience IS NULL AND COALESCE(tl.kind, 'api') = 'api' AND tl.record_owner <> '' "
    "AND tl.delivery <> 'computer' AND tl.created_by NOT LIKE 'bot:%'"
)
SHARED_KEY = "k.ciphertext IS NOT NULL AND k.kind <> 'login' AND COALESCE(k.holder, '') = ''"


def _s(name, base_url, check_path="", auth="bearer", env="", aliases=(), username="", headers=None, rejected=(401,)):
    return {
        "name": name,
        "base_url": base_url,
        "check_path": check_path,
        "auth_scheme": auth,
        "env": env,
        "aliases": tuple(aliases),
        "username": username,
        "headers": dict(headers or {}),
        "rejected": tuple(rejected),
    }


# Keys are bot_tools.SERVICE_NAMES ids where one exists, so a tool and a bot.yaml declaration name a service alike.
SERVICES = {
    "close-crm": _s("Close CRM", "https://api.close.com", "/api/v1/me/", "basic_user", "CLOSE_API_KEY", ("close",)),
    "stripe": _s("Stripe", "https://api.stripe.com", "/v1/balance", env="STRIPE_API_KEY"),
    "github": _s(
        "GitHub",
        "https://api.github.com",
        "/user",
        env="GITHUB_TOKEN",
        headers={"Accept": "application/vnd.github+json"},
    ),
    "openai": _s("OpenAI", "https://api.openai.com", "/v1/models", env="OPENAI_API_KEY"),
    "anthropic": _s(
        "Anthropic",
        "https://api.anthropic.com",
        "/v1/models",
        "header:x-api-key",
        "ANTHROPIC_API_KEY",
        headers={"anthropic-version": "2023-06-01"},
    ),
    "openrouter": _s("OpenRouter", "https://openrouter.ai", "/api/v1/key", env="OPENROUTER_API_KEY"),
    "posthog": _s("PostHog", "https://us.posthog.com", "/api/users/@me/", env="POSTHOG_API_KEY"),
    "notion": _s(
        "Notion",
        "https://api.notion.com",
        "/v1/users/me",
        env="NOTION_API_KEY",
        headers={"Notion-Version": "2022-06-28"},
    ),
    "linear": _s("Linear", "https://api.linear.app", "", "header:Authorization", "LINEAR_API_KEY"),
    "calendly": _s("Calendly", "https://api.calendly.com", "/users/me", env="CALENDLY_API_KEY"),
    # The host and variable Finance's Brex sync uses (backend/finance/brex.py BASE; TIDY stores BREX_TOKEN), so a tool's
    # check and Finance call the same API. A key stored as BREX_API_KEY still lands here by its name's first word.
    "brex": _s("Brex", "https://api.brex.com", "/v2/users/me", env="BREX_TOKEN"),
    "mercury": _s("Mercury", "https://api.mercury.com/api/v1", "/accounts", env="MERCURY_API_KEY"),
    "cloudflare": _s(
        "Cloudflare", "https://api.cloudflare.com/client/v4", "/user/tokens/verify", env="CLOUDFLARE_API_TOKEN"
    ),
    "hugging-face": _s(
        "Hugging Face", "https://huggingface.co", "/api/whoami-v2", env="HF_TOKEN", aliases=("huggingface", "hf")
    ),
    "elevenlabs": _s(
        "ElevenLabs",
        "https://api.elevenlabs.io",
        "/v1/user",
        "header:xi-api-key",
        "ELEVENLABS_API_KEY",
        ("eleven labs",),
    ),
    "heygen": _s("HeyGen", "https://api.heygen.com", "/v2/user/remaining_quota", "header:X-Api-Key", "HEYGEN_API_KEY"),
    "twilio": _s(
        "Twilio", "https://api.twilio.com", "/2010-04-01/Accounts.json", "basic_password", "TWILIO_AUTH_TOKEN"
    ),
    "airtable": _s("Airtable", "https://api.airtable.com", "/v0/meta/whoami", env="AIRTABLE_API_KEY"),
    "hubspot": _s("HubSpot", "https://api.hubapi.com", "/account-info/v3/details", env="HUBSPOT_ACCESS_TOKEN"),
    "sendgrid": _s("SendGrid", "https://api.sendgrid.com", "/v3/user/profile", env="SENDGRID_API_KEY"),
    "intercom": _s("Intercom", "https://api.intercom.io", "/me", env="INTERCOM_ACCESS_TOKEN"),
    "mailgun": _s(
        "Mailgun", "https://api.mailgun.net", "/v3/domains", "basic_password", "MAILGUN_API_KEY", username="api"
    ),
    "pipedrive": _s("Pipedrive", "https://api.pipedrive.com", "/v1/users/me", "query:api_token", "PIPEDRIVE_API_TOKEN"),
    "digitalocean": _s(
        "DigitalOcean",
        "https://api.digitalocean.com",
        "/v2/account",
        env="DIGITALOCEAN_TOKEN",
        aliases=("digital ocean",),
    ),
    "sentry": _s("Sentry", "https://sentry.io", "/api/0/", env="SENTRY_AUTH_TOKEN"),
    "asana": _s("Asana", "https://app.asana.com", "/api/1.0/users/me", env="ASANA_ACCESS_TOKEN"),
    "clickup": _s(
        "ClickUp", "https://api.clickup.com", "/api/v2/user", "header:Authorization", "CLICKUP_API_TOKEN", ("click up",)
    ),
    "postmark": _s(
        "Postmark",
        "https://api.postmarkapp.com",
        "/server",
        "header:X-Postmark-Server-Token",
        "POSTMARK_SERVER_TOKEN",
        headers={"Accept": "application/json"},
    ),
    "render": _s("Render", "https://api.render.com", "/v1/owners", env="RENDER_API_KEY"),
    # Vercel answers a bad token with 403, so its 403 is a rejection, not a scope the key lacks.
    "vercel": _s("Vercel", "https://api.vercel.com", "/v2/user", env="VERCEL_TOKEN", rejected=(401, 403)),
    "gemini": _s(
        "Gemini", "https://generativelanguage.googleapis.com", "", "query:key", "GEMINI_API_KEY", ("google ai",)
    ),
    "xai": _s("xAI", "https://api.x.ai", "", env="XAI_API_KEY"),
    "resend": _s("Resend", "https://api.resend.com", "", env="RESEND_API_KEY"),
    "attio": _s("Attio", "https://api.attio.com", "", env="ATTIO_API_KEY"),
    "todoist": _s("Todoist", "https://api.todoist.com", "", env="TODOIST_API_TOKEN"),
    "apollo": _s("Apollo", "https://api.apollo.io", "", "header:X-Api-Key", "APOLLO_API_KEY"),
    "deepgram": _s("Deepgram", "https://api.deepgram.com", "", "header:Authorization", "DEEPGRAM_API_KEY"),
    # A bearer call to /me with a bad token answers 401 (code 190); in the query string it would answer 400.
    "meta-ads": _s(
        "Meta Ads",
        "https://graph.facebook.com/v26.0",
        "/me",
        env="META_ACCESS_TOKEN",
        aliases=("facebook ads", "fb ads", "meta", "facebook"),
    ),
    # OAuth (client id and secret, refresh token, developer token, login customer id): no single key goes on a call, so
    # there is no proxy address or check. Bots use it from their computers; Finance's ad spend import
    # (backend/finance/ad_import.py) exchanges the refresh token itself.
    "google-ads": _s("Google Ads", "", env="GOOGLE_ADS_DEVELOPER_TOKEN", aliases=("adwords",)),
}


def _f(id, suffix, role="secret", shape="", aliases=(), optional=False):
    """One field of a vendor's auth template: its variable (`<PREFIX>_<suffix>`, or an alias suffix; an account's label
    may sit between: POSTHOG_CRO_API_KEY), what it is (`secret`: a vault key; `id`: an identifier the bot sends as it is,
    a setting when its value says so; `setting`: a host, region or profile) and the shape a pasted value of it has."""
    return {"id": id, "suffix": suffix, "role": role, "shape": shape, "aliases": tuple(aliases), "optional": optional}


# How a vendor's several values reach it, from each vendor's own documentation. `prefix`: the variables' first word(s).
# `fields`: what the account is made of. `ways`: how a call through the proxy carries them, the first whose fields are
# all present: `basic` [user field, password field], `headers` {name: "text with {field}"}, `query` {param: "{field}"}.
# `delivery`: where a new tool's keys go; a vendor with no `ways` signs on the computer (AWS SigV4, Google Ads' OAuth
# refresh), so its keys go to computers and the proxy carries none of its calls. `sensitive`: grouping that would give
# anyone a value they did not hold asks a person instead (backend/tool_templates.py).
TEMPLATES = {
    # https://www.twilio.com/docs/usage/requests-to-twilio: HTTP Basic, the Account SID and its Auth Token, or an API
    # Key SID (SK...) and its secret. The Account SID is also in every path (/2010-04-01/Accounts/{AccountSid}/...),
    # so it is an id the bot holds as it is, never a proxy token.
    "twilio": {
        "service": "twilio",
        "prefix": "TWILIO",
        "delivery": "proxy",
        "fields": [
            _f("account_sid", "ACCOUNT_SID", "id", r"AC[0-9a-fA-F]{32}"),
            _f("auth_token", "AUTH_TOKEN", "secret", r"[0-9a-fA-F]{32}", optional=True),
            _f("api_key", "API_KEY", "secret", r"SK[0-9a-fA-F]{32}", ("API_KEY_SID", "API_SID"), optional=True),
            _f("api_secret", "API_SECRET", "secret", r"[A-Za-z0-9]{32}", ("API_KEY_SECRET",), optional=True),
        ],
        "ways": [{"basic": ["api_key", "api_secret"]}, {"basic": ["account_sid", "auth_token"]}],
    },
    # https://developers.google.com/google-ads/api/docs/oauth/overview and /concepts/call-structure: a developer token
    # header, an OAuth access token the client mints from the client id, client secret and refresh token, and the
    # manager account in login-customer-id. The refresh stays on the computer for now (the client libraries do it), so
    # the six values go to computers together and the proxy carries none of its calls.
    "google-ads": {
        "service": "google-ads",
        "prefix": "GOOGLE_ADS",
        "delivery": "computer",
        "fields": [
            _f("developer_token", "DEVELOPER_TOKEN", "secret", r"[A-Za-z0-9_-]{22}"),
            _f("client_id", "CLIENT_ID", "secret", r"[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com"),
            _f("client_secret", "CLIENT_SECRET", "secret", r"GOCSPX-[A-Za-z0-9_-]{20,40}"),
            _f("refresh_token", "REFRESH_TOKEN", "secret", r"1//[A-Za-z0-9_-]{20,}"),
            _f("login_customer_id", "LOGIN_CUSTOMER_ID", "id", r"\d{3}-?\d{3}-?\d{4}", optional=True),
            _f("customer_id", "CUSTOMER_ID", "id", r"\d{3}-?\d{3}-?\d{4}", ("CUSTOMER_IDS",), optional=True),
        ],
        "ways": [],
    },
    # https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html: every call is signed (SigV4)
    # with the key pair for a region, so the pair and the region go to the computer and the SDK signs there.
    "aws": {
        "service": "aws",
        "prefix": "AWS",
        "delivery": "computer",
        "sensitive": True,
        "fields": [
            _f("access_key_id", "ACCESS_KEY_ID", "secret", r"(AKIA|ASIA)[A-Z0-9]{16}"),
            _f("secret_access_key", "SECRET_ACCESS_KEY", "secret", r"[A-Za-z0-9/+=]{40}"),
            _f("session_token", "SESSION_TOKEN", "secret", optional=True),
            _f("region", "REGION", "setting", r"[a-z]{2}(-gov)?-[a-z]+-\d", ("DEFAULT_REGION",), optional=True),
            _f("profile", "PROFILE", "setting", optional=True),
        ],
        "ways": [],
    },
    # https://posthog.com/docs/api: a personal API key as a Bearer token, at the project's host (US or EU cloud, or
    # self-hosted), on paths under /api/projects/{project_id}/.
    "posthog": {
        "service": "posthog",
        "prefix": "POSTHOG",
        "delivery": "proxy",
        "fields": [
            _f("api_key", "API_KEY", "secret", r"phx_[A-Za-z0-9]{20,}", ("PERSONAL_API_KEY",)),
            _f("project_api_key", "PROJECT_API_KEY", "secret", r"phc_[A-Za-z0-9]{20,}", optional=True),
            _f("host", "HOST", "setting", r"https://[a-z0-9.-]+", ("API_HOST",), optional=True),
            _f("project_id", "PROJECT_ID", "id", r"\d{1,12}", optional=True),
        ],
        "ways": [{"headers": {"Authorization": "Bearer {api_key}"}}],
    },
    # https://elevenlabs.io/docs/api-reference/authentication: the key in the xi-api-key header.
    "elevenlabs": {
        "service": "elevenlabs",
        "prefix": "ELEVENLABS",
        "delivery": "proxy",
        "fields": [_f("api_key", "API_KEY", "secret", r"sk_[0-9a-f]{48}", ("KEY", "XI_API_KEY"))],
        "ways": [{"headers": {"xi-api-key": "{api_key}"}}],
    },
    # https://developer.calendly.com/how-to-authenticate-with-personal-access-tokens: a Bearer token.
    "calendly": {
        "service": "calendly",
        "prefix": "CALENDLY",
        "delivery": "proxy",
        "fields": [
            _f(
                "token",
                "API_KEY",
                "secret",
                r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                ("TOKEN", "API_TOKEN", "ACCESS_TOKEN", "PERSONAL_ACCESS_TOKEN", "PAT"),
            )
        ],
        "ways": [{"headers": {"Authorization": "Bearer {token}"}}],
    },
}


# Ads, payments and CRM (plan P3/W4): a write key of one of these reaches a run only through the proxy, where the
# Rules gate (backend/rule_links.py) sees its writes (api_tools.proxy_policy). With each, the POSTs its API answers
# as a read (a search, a query), which are no write: any other call but GET, HEAD and OPTIONS is one. A tool is one of
# these by its service or its API's known host (a "Billing" tool at api.stripe.com is Stripe's), never by its name.
GATED = {
    "meta-ads": ("ads", ()),
    "google-ads": ("ads", (r"/v\d+/customers/[^/]+/googleAds:search(Stream)?",)),
    "stripe": ("payments", ()),
    "brex": ("payments", ()),
    "mercury": ("payments", ()),
    "close-crm": ("crm", (r"/api/v1/data/search/?",)),
    # v3 and the date-versioned CRM (/crm/objects/2026-03/...): object search and batch read; list search
    "hubspot": (
        "crm",
        (
            r"/crm/(v3/objects|objects/\d{4}-\d{2})/[^/]+/(search|batch/read)",
            r"/crm/(v3/lists|lists/\d{4}-\d{2})/search",
        ),
    ),
    "pipedrive": ("crm", ()),
    "attio": ("crm", (r"/v2/(objects/[^/]+/records|lists/[^/]+/entries)/query",)),
}


# Of those, the ones whose keys stay with the run: Google Ads signs each call on the computer (an OAuth refresh and a
# developer token on every call), which no proxy key can carry. Listed as Not covered, never refused.
DIRECT = frozenset({"google-ads"})


@functools.lru_cache(maxsize=1)
def _gated_names():
    """{a GATED vendor's id or one of its explicit aliases (SERVICES `aliases`, VENDOR_ALIASES): its id}."""
    names = {key: key for key in GATED}
    for key in GATED:
        for alias in (SERVICES.get(key) or {}).get("aliases", ()):
            names.setdefault(str(alias).lower(), key)
    for alias, key in VENDOR_ALIASES.items():
        if key in GATED:
            names.setdefault(alias, key)
    return names


@functools.lru_cache(maxsize=1)
def _gated_hosts():
    """{the exact hostname of a GATED vendor's API (api.stripe.com, graph.facebook.com): its id}. Google Ads has no
    catalog address (its calls are signed on the computer), so its API's host is named here."""
    from urllib.parse import urlsplit

    hosts = {
        urlsplit((SERVICES.get(key) or {}).get("base_url") or "").hostname: key
        for key in GATED
        if (SERVICES.get(key) or {}).get("base_url")
    }
    return {**hosts, "googleads.googleapis.com": "google-ads"}


def gated_vendor(service="", host=""):
    """The GATED vendor of a tool, else "": its service when that is a vendor's id or one of its explicit aliases
    (`close` is Close CRM's), else its address's exact hostname when that is the vendor's known API host
    (api.stripe.com yes; dashboard.stripe.com, a "stripe-adapter" service no). Never its display name."""
    from urllib.parse import urlsplit

    found = _gated_names().get(str(service or "").strip().lower())
    if found:
        return found
    hostname = urlsplit(host if "//" in str(host) else "//" + str(host or "")).hostname if host else None
    return _gated_hosts().get(hostname, "") if hostname else ""


def canonical(service=""):
    """The catalog's service for a service as given: itself when known, else the one it is an explicit alias of
    (SERVICES `aliases`, VENDOR_ALIASES: `close` is Close CRM's), else itself."""
    service = str(service or "").strip().lower()
    if service in SERVICES:
        return service
    for key, row in SERVICES.items():
        if service and service in row["aliases"]:
            return key
    return VENDOR_ALIASES.get(service, service) if VENDOR_ALIASES.get(service) in SERVICES else service


def protected(service=""):
    """Whether a key of this service is a write key that lives in the proxy (GATED, but not DIRECT): what the intake of
    a stored or imported key reads before any id or setting guess, so such a key is never made a setting nor put on a
    tool with no address."""
    vendor = gated_vendor(service)
    return bool(vendor) and vendor not in DIRECT


def gated(service="", host=""):
    """`ads`, `payments` or `crm` for a tool of one of those vendors, else ""."""
    return GATED.get(gated_vendor(service, host), ("", ()))[0]


def vendor_read(service, method, path, host=""):
    """A POST the vendor's API answers as a read (GATED), at exactly this URL path (the whole path) and only at the
    vendor's own known API host: not a write. Anywhere else the same path is a write."""
    from urllib.parse import urlsplit

    vendor = gated_vendor(service, host)
    hostname = urlsplit(host if "//" in str(host) else "//" + str(host or "")).hostname if host else None
    if not vendor or _gated_hosts().get(hostname) != vendor:
        return False
    return method == "POST" and any(re.fullmatch(pattern, path) for pattern in GATED[vendor][1])


# Vendors' own MCP servers (docs/connect-tools.md): Find or connect offers a sign-in with the vendor on these.
MCP_URLS = {
    "linear": "https://mcp.linear.app/mcp",
    "posthog": "https://mcp.posthog.com/mcp",
    "sentry": "https://mcp.sentry.dev/mcp",
}
# The site a person signs in to where it is not the API address's domain.
SITES = {"gemini": "aistudio.google.com", "google-ads": "ads.google.com", "hubspot": "app.hubspot.com"}


def catalog():
    """The known services as Find or connect offers them, by name: how a pasted key goes on a call, the site a person
    signs in to and the vendor's MCP server, where it has one."""
    return [
        {
            "id": key,
            "name": row["name"],
            "auth_scheme": row["auth_scheme"],
            "site": SITES.get(key) or (_domain(row["base_url"]) if row["base_url"] else ""),
            "mcp_url": MCP_URLS.get(key, ""),
        }
        for key, row in sorted(SERVICES.items(), key=lambda kv: kv[1]["name"].lower())
    ]


def _words(text):
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _prefixes():
    out = []
    for key, row in SERVICES.items():
        for alias in (key.replace("-", " "), row["name"], *row["aliases"]):
            words = _words(alias)
            if words:
                out.append((words, key))
    # Longest first, so "hugging face" wins over a one-word alias that happens to start the same name.
    return sorted(out, key=lambda pair: -len(pair[0]))


PREFIXES = _prefixes()
BY_ENV = {row["env"]: key for key, row in SERVICES.items() if row["env"]}


def match(env="", name="", service=""):
    """The known service a key or tool is for: its stated service, else its variable, else the first words of its
    name ("Close API key", "STRIPE_SECRET_KEY"). None when nothing matches, so an unknown tool is never given another
    service's address."""
    service = str(service or "").strip().lower()
    if service in SERVICES:
        return service
    if env in BY_ENV:
        return BY_ENV[env]
    for text in (env, name):
        words = _words(text)
        for prefix, key in PREFIXES:
            if words[: len(prefix)] == prefix:
                return key
    return None


# Words that start many tools' names without naming who makes them ("API key", "Prod Raygun"): never a vendor.
NOT_VENDORS = frozenset(
    "api key keys token tokens secret the my our new old test tests prod production dev staging stage main default "
    "internal tico hub read write readonly admin bot bots company team service account".split()
)
# One vendor under several names: a service, a variable's prefix or a word that is another's (GH_TOKEN is GitHub's,
# GOOGLE_ADS_* and GSC_* are Google's, Meta Ads is Meta's).
VENDOR_ALIASES = {
    "gh": "github",
    "github-app": "github",
    "gsc": "google",
    "gcp": "google",
    "gmail": "google",
    "google-ads": "google",
    "google-calendar": "google",
    "google-drive": "google",
    "google-workspace": "google",
    "google-workspace-admin": "google",
    "adwords": "google",
    "meta-ads": "meta",
    "facebook": "meta",
    "fb": "meta",
    "s3": "aws",
    "amazon": "aws",
    "close": "close-crm",
    "hf": "hugging-face",
    "huggingface": "hugging-face",
    "postgresql": "postgres",
}
VENDOR_NAMES = {"google": "Google", "meta": "Meta", "aws": "AWS", "github": "GitHub", "slack": "Slack"}
# Second-level labels that are part of a country's public suffix (acme.co.uk): the vendor is the label before them.
SHARED_SUFFIXES = frozenset(("co", "com", "org", "net", "ac", "gov", "edu"))


def vendor_word(text: str) -> str:
    """The first word of a tool's name or variable as a vendor id ("Raygun production" -> raygun), or ""."""
    words = _words(text)
    first: str = words[0] if words else ""
    word: str = VENDOR_ALIASES.get(first) or first
    return word if len(word) >= 3 and not word.isdigit() and word not in NOT_VENDORS else ""


def _known():
    from backend.bot_tools import SERVICE_NAMES

    return {VENDOR_ALIASES.get(k, k) for k in (*SERVICES, *SERVICE_NAMES, *VENDOR_ALIASES)}


def _domain(host):
    """A host's registrable domain, near enough: its last two labels, three under a shared suffix (acme.co.uk)."""
    host = str(host or "").strip().lower().split("//")[-1].split("/")[0].split(":")[0].strip(".")
    labels = [x for x in host.split(".") if x]
    if len(labels) >= 3 and labels[-2] in SHARED_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def service_for_host(host):
    """The vendor a site or an API address belongs to: the known service whose address shares its domain
    (app.close.com -> close-crm), else the domain's own name (app.raygun.com -> raygun). "" for an IP or a bare host."""
    domain = _domain(host)
    if "." not in domain or domain.replace(".", "").isdigit():
        return ""
    for key, row in SERVICES.items():
        if row["base_url"] and _domain(row["base_url"]) == domain:
            return VENDOR_ALIASES.get(key, key)
    return vendor_word(domain.split(".")[0])


def vendor_of(service: str = "", name: str = "", envs: "tuple[str, ...] | list[str]" = (), host: str = "") -> str:
    """The vendor Tools lists a tool under: one row a vendor, its tools on the vendor's page. Its `service` first (what
    a person set, or a known service; "aws-production" is AWS's and "google-ads" Google's), else a known service or
    vendor its name or a key's variable names, by any word (TIDY_AWS_READONLY is AWS's, GH_TOKEN GitHub's), else its
    address's domain (an MCP server at api.raygun.com is Raygun's), else its name's or variable's first word ("Raygun
    production" -> raygun).
    "" when nothing says: the tool is then its own row."""
    known = _known()
    service = str(service or "").strip().lower()
    if service:
        own = VENDOR_ALIASES.get(service, service)
        first = vendor_word(own.split("-")[0])
        return own if own in known or not first or first not in known else first
    texts = [t for t in (name, *envs) if t]
    for text in texts:
        found = match(env=text) or match(name=text)
        if found:
            return VENDOR_ALIASES.get(found, found)
    for text in texts:
        for word in _words(text):
            word = VENDOR_ALIASES.get(word, word)
            if word in known:
                return word
    by_host = service_for_host(host) if host else ""
    if by_host:
        return by_host
    for text in texts:
        if vendor_word(text):
            return vendor_word(text)
    return ""


def vendor_name(vendor):
    """How Tools names a vendor: Google, AWS, a known service's own name, else its id in words (raygun -> Raygun)."""
    from backend.bot_tools import service_name

    vendor = str(vendor or "").strip().lower()
    return VENDOR_NAMES.get(vendor) or (SERVICES.get(vendor) or {}).get("name") or service_name(vendor)


def is_tool_key(kind, env, name, source, stored, on_computers):
    """Whether a stored credential is a tool's key: an `api_key` or `token` with a value. Not a tool's: a model key (its
    variable, or a name with none set, is a providers.RUNTIME_KEY_NAMES name: a runtime's CLI reads it directly, so it
    belongs to the model setup and never goes through the proxy), a computer's key or shared model login
    (`on_computers`: a model subscription or a grant to the computers; or a `computers:` source),
    Finance's bank logins (`finance`), a bot's own `HUB_` key back into Tico, a webhook or signing secret
    (`STRIPE_WEBHOOK_SECRET`, "Slack signing secret": the bot checks signatures with the value itself, it is never sent
    to the vendor, so a proxy token in its place would break it), passwords and files."""
    from backend.providers import RUNTIME_KEY_NAMES

    env, source = env or "", source or ""
    return (
        kind in ("api_key", "token")
        and bool(stored)
        and not on_computers
        and (env or str(name or "").strip()) not in RUNTIME_KEY_NAMES
        and not env.startswith("HUB_")
        and not source.startswith("computers:")
        and source != "finance"
        and not {"webhook", "signing"} & set(_words(env) + _words(name))
    )


def slug(text):
    return slugify(str(text or ""), limit=48, fallback="tool")


# The tables whose rows name a tool as the connection an outside item or record came through (backend/item_sources).
CONNECTION_TABLES = ("task_links", "item_metadata", "entity_external_refs")


# An outbound action that may still go (or whose outcome is not known yet): its destination (Jira's `HM-12`) is the
# connection's endpoint's, so the endpoint stays while one is pending (backend/outbound_actions.py).
PENDING_OUTBOUND = "state IN ('waiting_approval','queued','attempting','unknown')"


def referenced(conn, tid):
    """Whether an outside reference still names `tid` as its connection, a restorable deleted task's link or a pending
    outbound action included. A deleted tool's id is then never given to a new tool, so a new connection never inherits
    the old one's items and records (`PROJ-7` of another site), and its endpoint never moves under an action waiting to
    go there (api_tools.keep_sites). A file from before the columns has none."""
    for table in CONNECTION_TABLES:
        try:
            if conn.execute(f"SELECT 1 FROM {table} WHERE connection_id=? LIMIT 1", (tid,)).fetchone():
                return True
        except sqlite3.OperationalError:
            continue
    try:
        if conn.execute(
            f"SELECT 1 FROM outbound_actions WHERE connection_id=? AND {PENDING_OUTBOUND} LIMIT 1", (tid,)
        ).fetchone():
            return True
    except sqlite3.OperationalError:
        pass
    try:  # a deleted task's links come back with it (backend/task_delete.py keeps them in task_trash.rows)
        return bool(
            conn.execute(
                "SELECT 1 FROM task_trash t, json_each(t.rows, '$.rows.task_links') l WHERE t.purged_at IS NULL "
                "AND json_valid(t.rows) AND json_extract(l.value, '$.connection_id')=? LIMIT 1",
                (tid,),
            ).fetchone()
        )
    except sqlite3.OperationalError:
        return False


def insert_tool(conn, service, name, delivery, created_by, created_at, updated_at=None, tid=None, first=None):
    """A new tools row for a key's service (or its own name), its fields from SERVICES; the id is free in `tools`. The
    first tool of a known service takes the service's name, a second keeps its key's name so the two can be told
    apart. Plain positional SQL, so it runs on a migration's raw connection too. `tid` and `first` are a caller's own
    free id and whether it counts as the first (backend/api_tools.py `ensure_tool`, around tools its caller may not
    see)."""
    fields = defaults(service)
    base = slug(service or name)
    if tid is None:
        tid, n = base, 1
        while conn.execute("SELECT 1 FROM tools WHERE id=?", (tid,)).fetchone() or referenced(conn, tid):
            n += 1
            tid = f"{base}-{n}"
    first = tid == base if first is None else first
    conn.execute(
        "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            tid,
            fields["name"] if service and first else str(name or "").strip() or tid,
            service or "",
            fields.get("base_url", ""),
            fields.get("auth_scheme", "bearer"),
            fields.get("check_path", ""),
            delivery,
            created_by or "",
            created_at,
            updated_at or created_at,
        ),
    )
    return tid


def defaults(service):
    """The fields a tool for `service` starts with ({} for an unknown one)."""
    row = SERVICES.get(service or "")
    if not row:
        return {}
    return {
        "name": row["name"],
        "base_url": row["base_url"],
        "auth_scheme": row["auth_scheme"],
        "check_path": row["check_path"],
        "env": row["env"],
        "username": row["username"],
    }


def check_path_for(service, base_url, check_path=""):
    """The path a tool's check GETs: its own `check_path`, else its known service's when the tool is at that service's
    own address (a tool made before its service had a check path, or whose path was left empty). A service's path
    belongs to its address, so a tool at another address gets none. "" when there is none."""
    if check_path:
        return check_path
    row = SERVICES.get(service or "") or SERVICES.get(service_for_address(base_url))
    if not row or not row["check_path"] or not row["base_url"]:
        return ""
    return row["check_path"] if str(base_url or "").rstrip("/") == row["base_url"] else ""


def service_for_address(base_url):
    """The known service whose own address `base_url` is ("" for none)."""
    url = str(base_url or "").rstrip("/")
    return next((key for key, row in SERVICES.items() if row["base_url"] and row["base_url"] == url), "")


def apply_auth(scheme, secret, username=""):
    """(headers, query params) that carry `secret` the way `scheme` says. `basic_password` sends the stored username
    (Twilio's Account SID, Mailgun's "api") as the user and the key as the password; `basic_user` the key as the user."""
    scheme = scheme or "bearer"
    if scheme == "bearer":
        return {"Authorization": "Bearer " + secret}, {}
    if scheme in ("basic_user", "basic_password"):
        pair = f"{secret}:" if scheme == "basic_user" else f"{username}:{secret}"
        return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}, {}
    kind, _, name = scheme.partition(":")
    if kind == "header" and name:
        return {name: secret}, {}
    if kind == "query" and name:
        return {}, {name: secret}
    raise ValueError("unknown auth scheme")


def rejects(service, code):
    """Whether `service`'s check answering `code` says the key is bad: a 401, or what the service names (Vercel's
    403)."""
    return code in ((SERVICES.get(service or "") or {}).get("rejected") or (401,))


def check_headers(service):
    """Fixed headers some services require on every call (an API version), sent with checks too."""
    return dict((SERVICES.get(service or "") or {}).get("headers") or {})
