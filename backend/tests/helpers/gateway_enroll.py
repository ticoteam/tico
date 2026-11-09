"""Helpers that other tests share, moved out of test_gateway_enroll.py so they import no test file."""

import datetime
import hashlib
import json
import pathlib
import re
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from backend.tests.helpers.decide_gateway import answer_all
from backend.tests.helpers.gateway_connection import setup as connection_setup


CONTRACT = json.loads((pathlib.Path(__file__).parents[1] / "fixtures" / "gateway_enroll_contract.json").read_text())


INVITE = "tgi_" + "s" * 43  # a synthetic invite code


class FakeHQ:
    """HQ's enroll, rotate, key and decisions routes, answering as the contract says. `revoke_install` is staff's."""

    def __init__(self):
        self.invites, self.installs, self.keys, self.calls = {INVITE}, {}, {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def bearer(self):
                return self.headers.get("Authorization", "").removeprefix("Bearer ")

            def known(self):
                """The caller's key record, or the contract's refusal (status, code)."""
                held = owner.keys.get(self.bearer())
                if not held:
                    return None, (401, "unknown_key")
                if held["revoked"] or owner.installs[held["install"]]["revoked"]:
                    return None, (401, "revoked_key")
                return held, None

            def do_GET(self):
                owner.calls.append(("GET", self.path, dict(self.headers)))
                held, refused = self.known()
                if refused:
                    return self.reply(refused[0], {"error": refused[1]})
                self.reply(
                    200,
                    {
                        "status": "active",
                        "month": "2026-10",
                        "cap_usd": 50.0,
                        "spent_usd": 1.0,
                        "used": 0.02,
                        "warning": False,
                        "cap_reached": False,
                        "rpm": 60,
                        "tpm": 100000,
                        "source": "enrolled",
                        "rotate_after": held["rotate_after"],
                    },
                )

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                owner.calls.append(("POST", self.path, dict(self.headers), body))
                path = self.path.removeprefix("/v1/gateway")
                if path == CONTRACT["enroll"]["path"].removeprefix("/v1/gateway"):
                    status, answer = owner.enroll(body)
                elif path == CONTRACT["rotate"]["path"].removeprefix("/v1/gateway"):
                    held, refused = self.known()
                    status, answer = refused if refused else owner.rotate(held, self.bearer())
                elif path == "/decisions":
                    held, refused = self.known()
                    status, answer = refused or (
                        200,
                        {
                            "model": "tico-decide-low",
                            "answers": answer_all(body["questions"]),
                            "usage": {"input_tokens": 120, "output_tokens": 0, "cost_usd": 0.00001},
                        },
                    )
                else:
                    status, answer = 404, "not_found"
                self.reply(status, answer if isinstance(answer, dict) else {"error": answer})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/gateway"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def mint(self, install, days=30):
        key = "tgw_" + secrets.token_urlsafe(32)
        after = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
        self.keys[key] = {
            "install": install,
            "revoked": False,
            "rotated": False,
            "key_id": "gk_" + secrets.token_hex(6),
            "rotate_after": after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return key

    def enroll(self, body):
        install, secret = body.get("install", ""), body.get("secret", "")
        patterns = CONTRACT["patterns"]
        if not re.fullmatch(patterns["install"], install) or not re.fullmatch(patterns["secret"], secret):
            return 422, "invalid"
        known = self.installs.get(install)
        if known and known["revoked"]:
            return 403, "install_revoked"
        if known and known["secret"] != hashlib.sha256(secret.encode()).hexdigest():
            return 409, "already_enrolled"
        if not known and body.get("invite") not in self.invites:
            return 403, "invalid_invite"
        for held in self.keys.values():
            if held["install"] == install:
                held["revoked"] = True  # re-enrolling by the secret revokes the old key at once
        self.installs[install] = {"secret": hashlib.sha256(secret.encode()).hexdigest(), "revoked": False}
        key = self.mint(install)
        return 201, {"key": key, "key_id": self.keys[key]["key_id"], "rotate_after": self.keys[key]["rotate_after"]}

    def rotate(self, held, key):
        if held["rotated"]:
            return 409, "already_rotated"
        held["rotated"] = True
        new = self.mint(held["install"])
        return 201, {
            "key": new,
            "key_id": self.keys[new]["key_id"],
            "rotate_after": self.keys[new]["rotate_after"],
            "old_valid_until": "2026-10-19T18:10:00Z",
        }

    def revoke_install(self, install):
        self.installs[install]["revoked"] = True

    def posts(self, path):
        return [call for call in self.calls if call[0] == "POST" and call[1].endswith(path)]


@contextmanager
def fake_hq():
    fake = FakeHQ()
    try:
        yield fake
    finally:
        fake.server.shutdown()


def setup(api, monkeypatch, fake, invite=INVITE):
    connection_setup(api, monkeypatch)
    from backend import gateway_connection

    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)
    monkeypatch.setenv("TICO_GATEWAY_URL", fake.url)
    if invite:
        monkeypatch.setenv("TICO_GATEWAY_INVITE", invite)
    else:
        monkeypatch.delenv("TICO_GATEWAY_INVITE", raising=False)


def tick(api, now=None):
    from backend import gateway_connection

    return gateway_connection.auto_tick(api.app.state.store, api.app.state.vault, api.app.state.store.settings, now=now)
