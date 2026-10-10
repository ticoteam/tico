"""OAuth sign-in for the MCP server: an agent that cannot paste a token ends up with the person's own token, once."""

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from backend.tests.test_api import api, get, headers, post  # noqa: F401

REDIRECT = "https://agent.example/callback"
VERIFIER = "v" * 50
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


def register(api):
    r = api.post("/api/v2/oauth/register", json={"client_name": "Dots", "redirect_uris": [REDIRECT]})
    assert r.status_code == 201
    return r.json()["client_id"]


def approve(api, client_id, token="ana-test", decision="allow"):
    page = api.get("/oauth/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
                                               "code_challenge": CHALLENGE, "code_challenge_method": "S256",
                                               "state": "s1"}, headers=headers(token))
    assert page.status_code == 200 and "Connect Dots?" in page.text and "agent.example" in page.text
    grant = re.search(r'name="grant" value="([^"]+)"', page.text).group(1)
    r = api.post("/oauth/authorize", data={"grant": grant, "decision": decision}, headers=headers(token),
                 follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(REDIRECT + "?")
    return {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}


def token(api, **form):
    return api.post("/api/v2/oauth/token", data=form)


def whoami(api, access):
    r = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "hub_whoami", "arguments": {}}},
                 headers={"Authorization": "Bearer " + access})
    return r.status_code, r.text


def test_an_agent_discovers_signs_in_and_works_as_the_person(api):
    r = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    meta_url = re.search(r'resource_metadata="([^"]+)"', r.headers["www-authenticate"]).group(1)
    meta = api.get(urlsplit(meta_url).path).json()
    assert meta["resource"].endswith("/api/v2/mcp")
    server = api.get(urlsplit(meta["authorization_servers"][0]).path + "/.well-known/openid-configuration").json()
    assert server["code_challenge_methods_supported"] == ["S256"] and server["registration_endpoint"].endswith("/register")

    client_id = register(api)
    answer = approve(api, client_id)
    assert answer["state"] == "s1" and answer["iss"] == server["issuer"]
    assert token(api, grant_type="authorization_code", code=answer["code"], client_id=client_id, redirect_uri=REDIRECT,
                 code_verifier="w" * 50).json()["error"] == "invalid_grant"          # wrong PKCE verifier
    issued = token(api, grant_type="authorization_code", code=answer["code"], client_id=client_id,
                   redirect_uri=REDIRECT, code_verifier=VERIFIER).json()
    status, text = whoami(api, issued["access_token"])
    assert status == 200 and "ana" in text

    # A refresh rotates the one token row: the old access token stops, the list keeps one line for Dots.
    fresh = token(api, grant_type="refresh_token", refresh_token=issued["refresh_token"], client_id=client_id).json()
    assert whoami(api, issued["access_token"])[0] == 401 and whoami(api, fresh["access_token"])[0] == 200
    rows = [t for t in get(api, "me/tokens")["tokens"] if t["label"].startswith("Dots")]
    assert len(rows) == 1

    # Revoke on that line ends the refresh too.
    post(api, "me/tokens/" + rows[0]["id"] + "/revoke", {})
    assert whoami(api, fresh["access_token"])[0] == 401
    assert token(api, grant_type="refresh_token", refresh_token=fresh["refresh_token"],
                 client_id=client_id).json()["error"] == "invalid_grant"


def test_a_code_works_once_and_a_second_use_ends_the_first(api):
    client_id = register(api)
    code = approve(api, client_id)["code"]
    first = token(api, grant_type="authorization_code", code=code, client_id=client_id, redirect_uri=REDIRECT,
                  code_verifier=VERIFIER).json()
    again = token(api, grant_type="authorization_code", code=code, client_id=client_id, redirect_uri=REDIRECT,
                  code_verifier=VERIFIER)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert whoami(api, first["access_token"])[0] == 401


def test_deny_and_unknown_redirects_never_hand_out_a_code(api):
    client_id = register(api)
    assert approve(api, client_id, decision="deny") == {"error": "access_denied", "state": "s1",
                                                        "iss": api.get("/api/v2/oauth/.well-known/openid-configuration").json()["issuer"]}
    r = api.get("/oauth/authorize", params={"response_type": "code", "client_id": client_id,
                                            "redirect_uri": "https://evil.example/cb", "code_challenge": CHALLENGE,
                                            "code_challenge_method": "S256"}, headers=headers(), follow_redirects=False)
    assert r.status_code == 400
    assert api.post("/api/v2/oauth/register", json={"redirect_uris": ["http://agent.example/cb"]}).status_code == 400


def test_bodies_are_capped_and_bad_requests_are_shown_not_redirected(api):
    assert api.post("/api/v2/oauth/token", content=b"a=" + b"x" * 30_000,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
    client_id = register(api)
    r = api.get("/oauth/authorize", params={"response_type": "token", "client_id": client_id, "redirect_uri": REDIRECT},
                headers=headers(), follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers
