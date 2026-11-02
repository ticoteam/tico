"""Sign-in: which console to open, what to paste in it, and what comes back."""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from . import contract


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    console_url: str
    steps: tuple[str, ...]


PROVIDERS = {
    "google": Provider("google", "Google", "https://console.cloud.google.com/auth/clients/create", (
        "Pick (or create) a Google Cloud project, and finish the consent screen if it asks (Internal for a Workspace "
        "company, External otherwise).",
        "Application type: Web application. Name it Tico.",
        "Under Authorized redirect URIs add the URI below, exactly, with no trailing slash.",
        "Create, then copy the client ID and client secret.")),
    "microsoft": Provider("microsoft", "Microsoft",
                          "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/CreateApplicationBlade",
                          ("Register an application named Tico; supported account types: this organization only.",
                           "Redirect URI: platform Web, and the URI below, exactly.",
                           "Copy the Application (client) ID and the Directory (tenant) ID from Overview.",
                           "Certificates & secrets > New client secret; copy the secret Value (not the ID).")),
}


# `--auth oidc`: any OpenID Connect issuer, given by its address. Whoever runs it registers the client.
GENERIC = Provider("oidc", "OpenID Connect", "", (
    "At your OpenID Connect provider, register a web client (authorization code flow) for Tico.",
    "Its redirect URI is the URI below, exactly, with no trailing slash.",
    "Copy the issuer URL, the client ID and the client secret."))


def redirect_uri(domain: str) -> str:
    return f"https://{domain}{contract.REDIRECT_PATH}"


# A reader signs in on the Company page's own origin (docs/company-page.md), so the provider needs this second redirect
# URI as well. The same path as the app's, on the reader's host: backend/company_page/reader_app.py's CALLBACK_PATH.
READER_REDIRECT_PATH = "/auth/callback"


def reader_redirect_uri(host: str) -> str:
    return f"https://{host}{READER_REDIRECT_PATH}"


def provider(auth: str) -> Provider:
    return GENERIC if auth == "oidc" else PROVIDERS[auth]


def issuer_host(auth: str, issuer: str = "") -> str:
    """The host sign-in redirects to: Google's or Microsoft's, or the given issuer's for `--auth oidc`; "" for neither."""
    if auth == "oidc":
        return urllib.parse.urlsplit(issuer).hostname or ""
    return contract.OIDC_HOSTS.get(auth, "")


def valid_issuer(url: str) -> bool:
    """An https address with a host and no query, fragment or credentials: what TICO_OIDC_ISSUER takes."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname) and not (u.query or u.fragment or u.username or u.password)
