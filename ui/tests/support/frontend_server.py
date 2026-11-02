"""A real Tico server for the custom-frontend browser test (ui/tests/custom-frontend.cjs).

    python3 ui/tests/support/frontend_server.py --auth none|oidc --origin http://localhost:5173

--auth none is a loopback install with a local owner bearer (the TICO_AUTH_PROXY=none of the
Docker install); --auth oidc is built-in sign-in against a small OpenID provider started here that
approves whoever asks as ana@acme.example. Either way TICO_CORS_ORIGINS is the --origin. A fake
runner answers chats to the bot `ops` word by word, so a stream has something to carry.
Prints one JSON line when ready: {"url", "token", "issuer"}. Stops on SIGTERM.
"""

import argparse
import base64
import hashlib
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx  # noqa: E402
import jwt  # noqa: E402
import uvicorn  # noqa: E402
import yaml  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.auth import Identity  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.store import H, encode  # noqa: E402

LOCAL_TOKEN = "local-owner-token-for-the-browser-test-0123456789"
REPLY = "Hello from ops. The reply is streaming to your own frontend, one word at a time."
CLIENT, SECRET = "example-client", "example-secret"


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def provider(email):
    """Discovery, JWKS, an authorize page that approves at once, and the token endpoint."""
    key = rsa.generate_private_key(65537, 2048)
    numbers = key.public_key().public_numbers()
    size = lambda n: n.to_bytes((n.bit_length() + 7) // 8, "big")  # noqa: E731
    codes = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, status=200, location=None):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            url = urlparse(self.path)
            issuer = "http://127.0.0.1:%d" % server.server_port
            if url.path.endswith("/openid-configuration"):
                self.send({"issuer": issuer, "authorization_endpoint": issuer + "/authorize", "token_endpoint": issuer + "/token",
                           "jwks_uri": issuer + "/keys", "id_token_signing_alg_values_supported": ["RS256"]})
            elif url.path == "/keys":
                self.send({"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
                                     "n": b64(size(numbers.n)), "e": b64(size(numbers.e))}]})
            elif url.path == "/authorize":
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                code = "code-%d" % len(codes)
                codes[code] = query
                self.send({}, 302, query["redirect_uri"] + "?" + urlencode({"code": code, "state": query["state"]}))
            else:
                self.send({}, 404)

        def do_POST(self):
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
            query = codes.pop(form.get("code"), None)
            if not query or form.get("client_secret") != SECRET or b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest()) != query["code_challenge"]:
                return self.send({"error": "invalid_grant"}, 400)
            now = int(time.time())
            claims = {"iss": "http://127.0.0.1:%d" % server.server_port, "aud": CLIENT, "sub": "1", "iat": now, "exp": now + 300,
                      "nonce": query["nonce"], "email": email, "email_verified": True}
            self.send({"id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % server.server_port


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_runner(base, admin):
    """Enroll a computer, give it the bot `ops`, and answer whatever is queued for it."""
    def call(path, body, token=admin):
        r = httpx.post(base + "/api/v2/" + path, json=body, timeout=20,
                       headers={"Authorization": "Bearer " + token, "Idempotency-Key": H.new_id()})
        r.raise_for_status()
        return r.json()
    code = call("enrollments", {"owner_id": "ana"})["code"]
    runner = call("runners/enroll", {"code": code, "label": "Test computer", "platform": "test"}, token="none")
    call("bots/ops/assignment", {"runner_id": runner["runner_id"], "expected_generation": 0})
    token, last_beat = runner["token"], 0
    while True:
        if time.time() - last_beat > 15:
            call("runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"ops": True}}, token)
            last_beat = time.time()
        attempt = call("jobs/claim", {"bot": None}, token)["attempt"]
        if not attempt:
            time.sleep(0.2)
            continue
        aid = attempt["id"]
        call("attempts/%s/started" % aid, {"thread_id": "t"}, token)
        # Seeded task work shares the chat room but is a different input from the browser greeting.
        reply = "The weekly update is drafted for the team." if attempt["task"] else REPLY
        words = reply.split(" ")
        for seq, word in enumerate(words, 1):
            time.sleep(0.1)
            call("attempts/%s/events" % aid, {"events": [{"seq": seq, "kind": "delta", "payload": {"text": word + " "}}]}, token)
        call("attempts/%s/complete" % aid, {"outcome": "completed", "text": reply, "last_seq": len(words)}, token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", choices=("none", "oidc"), default="none")
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="tico-frontend-"))
    registry = tmp / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner_id": "ana@acme.example"}))
    port = free_port()
    url = "http://127.0.0.1:%d" % port
    common = dict(db_path=tmp / "hub.db", registry_dir=registry, public_url=url, cors_origins=args.origin,
                  company_name="Acme", app_name="Acme HQ", test_identities={"admin": Identity("human:ana", "owner", "ana@acme.example")})
    if args.auth == "oidc":
        issuer = provider("ana@acme.example")
        settings = Settings(**common, auth_proxy="oidc", oidc_issuer=issuer, oidc_client_id=CLIENT, oidc_client_secret=SECRET,
                            session_secret="s" * 40)
    else:
        issuer = ""
        settings = Settings(**common, local_owner_email="ana@acme.example", local_owner_token=LOCAL_TOKEN)
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    people = [{"id": "ana", "name": "Ana Rivera", "email": "ana@acme.example", "title": "CEO", "primary_for": ["*"]},
              {"id": "ben", "name": "Ben Okafor", "email": "ben@acme.example", "title": "Engineer", "reports_to": "ana"}]
    bots = {"coo": {"name": "coo", "display_name": "Chief of Staff", "runtime": "fake", "status": "active"},
            "ops": {"name": "ops", "display_name": "Ops", "runtime": "fake", "status": "active", "description": "Runs operations"}}
    with app.state.store.transaction() as c:
        H.sync_registry(c, bots, {"people": people})
        c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": people}),))
        for slug, config in bots.items():
            c.execute("INSERT INTO bot_config(bot,config,owner_id,reports_to,description) VALUES(?,?,?,?,?)",
                      (slug, encode(config), "ana", "ana" if slug == "coo" else "coo", config.get("description", "")))
        c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
    call = lambda path, body: httpx.post(url + "/api/v2/" + path, json=body, timeout=20, headers={  # noqa: E731
        "Authorization": "Bearer admin", "Idempotency-Key": H.new_id()}).raise_for_status()
    call("tasks", {"title": "Review the launch plan", "body": "Please review the plan and reply.", "owner_id": "human:ana"})
    call("tasks", {"title": "Draft the weekly update", "body": "Summarize the week for the team.", "owner_id": "bot:ops"})
    call("files/links", {"bot": "ops", "url": "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit", "title": "Launch plan"})
    threading.Thread(target=fake_runner, args=(url, "admin"), daemon=True).start()
    print(json.dumps({"url": url, "token": LOCAL_TOKEN, "issuer": issuer}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
