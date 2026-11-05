"""The services Tico knows how to reach with a key: where their API lives, how the key goes on a request, and a cheap
GET that proves the key works.

An API tool (backend/api_tools.py) names one of these as its `service`; a new tool fills its empty base URL, auth scheme
and check path from here, and the data migration that made a tool for every stored key matched them the same way. A
check path is an authenticated GET that answers 2xx for a good key and 401 (403 for Vercel) for a bad one: each was
tried with a made-up key. Services whose API answers a bad key with something else (400 from Gemini, xAI, Resend and
Attio, 410 from Todoist, 200 from Apollo) or has no cheap GET (Linear's GraphQL) get no check path, since a check that
cannot tell a bad key from a good one would show Working for both. Their calls still go through the proxy.

`apply_auth` is the one place a key is put on a request, shared by the check here and the proxy (track B), so the two
can never disagree about where a service expects its key.
"""

import base64
import re

AUTH_SCHEME = re.compile(r"^(bearer|basic_user|basic_password|header:[A-Za-z0-9-]{1,60}|query:[A-Za-z0-9_.-]{1,60})$")


def _s(name, base_url, check_path="", auth="bearer", env="", aliases=(), username="", headers=None):
    return {"name": name, "base_url": base_url, "check_path": check_path, "auth_scheme": auth, "env": env,
            "aliases": tuple(aliases), "username": username, "headers": dict(headers or {})}


# Keys are bot_tools.SERVICE_NAMES ids where one exists, so a tool and a bot.yaml declaration name a service alike.
SERVICES = {
    "close-crm": _s("Close CRM", "https://api.close.com", "/api/v1/me/", "basic_user", "CLOSE_API_KEY", ("close",)),
    "stripe": _s("Stripe", "https://api.stripe.com", "/v1/balance", env="STRIPE_API_KEY"),
    "github": _s("GitHub", "https://api.github.com", "/user", env="GITHUB_TOKEN", headers={"Accept": "application/vnd.github+json"}),
    "openai": _s("OpenAI", "https://api.openai.com", "/v1/models", env="OPENAI_API_KEY"),
    "anthropic": _s("Anthropic", "https://api.anthropic.com", "/v1/models", "header:x-api-key", "ANTHROPIC_API_KEY",
                    headers={"anthropic-version": "2023-06-01"}),
    "openrouter": _s("OpenRouter", "https://openrouter.ai", "/api/v1/key", env="OPENROUTER_API_KEY"),
    "posthog": _s("PostHog", "https://us.posthog.com", "/api/users/@me/", env="POSTHOG_API_KEY"),
    "notion": _s("Notion", "https://api.notion.com", "/v1/users/me", env="NOTION_API_KEY", headers={"Notion-Version": "2022-06-28"}),
    "linear": _s("Linear", "https://api.linear.app", "", "header:Authorization", "LINEAR_API_KEY"),
    "calendly": _s("Calendly", "https://api.calendly.com", "/users/me", env="CALENDLY_API_KEY"),
    "brex": _s("Brex", "https://platform.brexapis.com", "/v2/users/me", env="BREX_API_KEY"),
    "mercury": _s("Mercury", "https://api.mercury.com/api/v1", "/accounts", env="MERCURY_API_KEY"),
    "cloudflare": _s("Cloudflare", "https://api.cloudflare.com/client/v4", "/user/tokens/verify", env="CLOUDFLARE_API_TOKEN"),
    "hugging-face": _s("Hugging Face", "https://huggingface.co", "/api/whoami-v2", env="HF_TOKEN", aliases=("huggingface", "hf")),
    "elevenlabs": _s("ElevenLabs", "https://api.elevenlabs.io", "/v1/user", "header:xi-api-key", "ELEVENLABS_API_KEY",
                     ("eleven labs",)),
    "heygen": _s("HeyGen", "https://api.heygen.com", "/v2/user/remaining_quota", "header:X-Api-Key", "HEYGEN_API_KEY"),
    "twilio": _s("Twilio", "https://api.twilio.com", "/2010-04-01/Accounts.json", "basic_password", "TWILIO_AUTH_TOKEN"),
    "airtable": _s("Airtable", "https://api.airtable.com", "/v0/meta/whoami", env="AIRTABLE_API_KEY"),
    "hubspot": _s("HubSpot", "https://api.hubapi.com", "/account-info/v3/details", env="HUBSPOT_ACCESS_TOKEN"),
    "sendgrid": _s("SendGrid", "https://api.sendgrid.com", "/v3/user/profile", env="SENDGRID_API_KEY"),
    "intercom": _s("Intercom", "https://api.intercom.io", "/me", env="INTERCOM_ACCESS_TOKEN"),
    "mailgun": _s("Mailgun", "https://api.mailgun.net", "/v3/domains", "basic_password", "MAILGUN_API_KEY", username="api"),
    "pipedrive": _s("Pipedrive", "https://api.pipedrive.com", "/v1/users/me", "query:api_token", "PIPEDRIVE_API_TOKEN"),
    "digitalocean": _s("DigitalOcean", "https://api.digitalocean.com", "/v2/account", env="DIGITALOCEAN_TOKEN",
                       aliases=("digital ocean",)),
    "sentry": _s("Sentry", "https://sentry.io", "/api/0/", env="SENTRY_AUTH_TOKEN"),
    "asana": _s("Asana", "https://app.asana.com", "/api/1.0/users/me", env="ASANA_ACCESS_TOKEN"),
    "clickup": _s("ClickUp", "https://api.clickup.com", "/api/v2/user", "header:Authorization", "CLICKUP_API_TOKEN",
                  ("click up",)),
    "postmark": _s("Postmark", "https://api.postmarkapp.com", "/server", "header:X-Postmark-Server-Token",
                   "POSTMARK_SERVER_TOKEN", headers={"Accept": "application/json"}),
    "render": _s("Render", "https://api.render.com", "/v1/owners", env="RENDER_API_KEY"),
    "vercel": _s("Vercel", "https://api.vercel.com", "/v2/user", env="VERCEL_TOKEN"),
    "gemini": _s("Gemini", "https://generativelanguage.googleapis.com", "", "query:key", "GEMINI_API_KEY", ("google ai",)),
    "xai": _s("xAI", "https://api.x.ai", "", env="XAI_API_KEY", aliases=("grok",)),
    "resend": _s("Resend", "https://api.resend.com", "", env="RESEND_API_KEY"),
    "attio": _s("Attio", "https://api.attio.com", "", env="ATTIO_API_KEY"),
    "todoist": _s("Todoist", "https://api.todoist.com", "", env="TODOIST_API_TOKEN"),
    "apollo": _s("Apollo", "https://api.apollo.io", "", "header:X-Api-Key", "APOLLO_API_KEY"),
    "deepgram": _s("Deepgram", "https://api.deepgram.com", "", "header:Authorization", "DEEPGRAM_API_KEY"),
}


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
            if words[:len(prefix)] == prefix:
                return key
    return None


def defaults(service):
    """The fields a tool for `service` starts with ({} for an unknown one)."""
    row = SERVICES.get(service or "")
    if not row:
        return {}
    return {"name": row["name"], "base_url": row["base_url"], "auth_scheme": row["auth_scheme"],
            "check_path": row["check_path"], "env": row["env"], "username": row["username"]}


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


def check_headers(service):
    """Fixed headers some services require on every call (an API version), sent with checks too."""
    return dict((SERVICES.get(service or "") or {}).get("headers") or {})
