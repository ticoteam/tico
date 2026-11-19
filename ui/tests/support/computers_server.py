"""A real Tico server for the Computers page browser test (ui/tests/computers-shared.cts).

    python3 ui/tests/support/computers_server.py

A loopback install (local owner bearer) with the credential vault on, and three fake computers, "Team box", "Lab box"
and "Spare box", with Claude Code installed and not signed in. Spare box only sends heartbeats, reporting Codex and not
Claude Code: it never takes a job or fetches a key, so it stays a computer a key serves that has reported nothing for
that tool. The keys are as an upgrade left the ones Settings > Computers kept (migration 20261117061243): Team box's
own Claude key, and the team's Claude key given to Lab box and Spare box, each a Settings > Models subscription kept in
the vault. Each fake computer does what runner-rs/src/signin.rs does with a key: it fetches its key from
`runner-model-credentials` every half second and signs Claude Code in with the one it is handed (again when it
changes), signs out when told it is signed out, and finishes a `use_shared_key` job. Every key value a computer
received is appended to the file named `received`, so the test can show the key reached the computer while the page
never showed it again. `POST /test/signed-out/{runner_id}` marks a computer's Claude Code signed out from Tico.
Prints one JSON line when ready: {"url", "token", "received", "computers": {label: id}, "subscriptions": {key: id}}.
Stops on SIGTERM.
"""

import importlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import yaml  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.auth import Identity  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.credentials import CredentialCipher  # noqa: E402
from backend.getting_started import save_state  # noqa: E402
from backend.store import H, encode  # noqa: E402
from backend.tests.helpers.credentials import FakeKMS  # noqa: E402

MOVE = importlib.import_module("backend.migrations.20261117061243_computer_keys_to_subscriptions")
OWN = "sk-ant-api03-own-test-key-0123456789"
SHARED = "sk-ant-api03-shared-test-key-0123456789"
LOCAL_TOKEN = "local-owner-token-for-the-browser-test-0123456789"
OWNER = "ana@acme.example"
CAPABILITIES = ["model_sign_out_v1", "model_keys_v1"]


def free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_computer(base, admin, label, received, ready, works=True):
    def call(method, path, body=None, token=admin, **params):
        r = httpx.request(
            method,
            base + "/api/v2/" + path,
            json=body,
            params=params or None,
            timeout=20,
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": H.new_id()},
        )
        r.raise_for_status()
        return r.json()

    code = call("POST", "enrollments", {"operator": "ana"})["code"]
    made = call("POST", "runners/enroll", {"code": code, "label": label, "platform": "linux"}, token="none")
    token = made["token"]
    claude = {"installed": True, "authenticated": "missing"}
    runtimes = {"claude": claude} if works else {"codex": {"installed": True, "authenticated": "missing"}}

    def beat():
        call(
            "POST",
            "runners/heartbeat",
            {
                "version": "test",
                "platform": "linux",
                "capabilities": CAPABILITIES,
                "readiness": {
                    "schema_version": 1,
                    "runtimes": {k: {f: v for f, v in row.items() if f != "value"} for k, row in runtimes.items()},
                    "bots": {},
                },
            },
            token,
        )

    def use(credential, source):
        with open(received, "a") as f:
            f.write(credential["value"] + "\n")
        claude.clear()
        claude.update(
            {
                "installed": True,
                "authenticated": "ready",
                "key": {"name": credential["env"], "source": source},
                "account": {"kind": "api_key", "checked_at": H.now()},
            }
        )

    beat()
    ready[label] = made["runner_id"]
    while not works:
        beat()
        time.sleep(1)
    while True:
        for job in call("GET", "runner-logins", token=token)["logins"]:
            # The key fetch below signs it in again; any other job is not one this computer does.
            state = "done" if job["kind"] == "use_shared_key" else "failed"
            call("POST", f"runner-logins/{job['id']}/report", {"state": state, "message": ""}, token=token)
        found = call("GET", "runner-model-credentials", token=token, runtime="claude")
        keys = found["credentials"]
        if found.get("signed_out"):
            claude.clear()
            claude.update({"installed": True, "authenticated": "missing"})
        elif keys and (
            claude.get("key", {}).get("source") != keys[0]["source"] or claude.get("value") != keys[0]["value"]
        ):
            use(keys[0], keys[0]["source"])
            claude["value"] = keys[0]["value"]  # remembered here, never reported (the heartbeat drops it below)
        beat()
        time.sleep(0.5)


def moved_keys(url, app, ready):
    """Team box's own key and the team key given to Lab box and Spare box, as the upgrade moved them."""

    def stored(name, value):
        r = httpx.post(
            url + "/api/v2/credentials",
            json={"name": name, "env": "ANTHROPIC_API_KEY", "secret": value},
            headers={"Authorization": "Bearer " + LOCAL_TOKEN, "Idempotency-Key": H.new_id()},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["id"]

    own, team = stored("ANTHROPIC_API_KEY for Team box", OWN), stored("Team Claude key", SHARED)
    now = H.now()
    with app.state.store.transaction() as c:
        c.execute("UPDATE credentials SET source=? WHERE id=?", ("computer:" + ready["Team box"], own))
        out = {}
        for key, cid, name, given in (
            ("own", own, "Anthropic API · Team box", ("Team box",)),
            ("team", team, "Team Claude key", ("Lab box", "Spare box")),
        ):
            preview = c.execute("SELECT preview FROM credentials WHERE id=?", (cid,)).fetchone()[0]
            out[key] = MOVE._subscription(c, cid, "ANTHROPIC_API_KEY", name, preview, "human:ana", now, now)
            for label in given:
                MOVE._give(c, ready[label], "claude", out[key], "human:ana", now)
    return out


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tico-computers-"))
    registry = tmp / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": OWNER}))
    port = free_port()
    url = "http://127.0.0.1:%d" % port
    settings = Settings(
        db_path=tmp / "hub.db",
        registry_dir=registry,
        public_url=url,
        company_name="Acme",
        app_name="Acme HQ",
        local_owner_email=OWNER,
        local_owner_token=LOCAL_TOKEN,
        credential_kms_key="test-key",
        credential_admins=(OWNER,),
        test_identities={"admin": Identity("human:ana", "owner", OWNER)},
    )
    app = create_app(settings)

    @app.post("/test/signed-out/{runner_id}")
    def signed_out(runner_id: str):
        with app.state.store.transaction() as c:
            c.execute(
                "INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
                "VALUES(?,'claude','',?,'human:ana',0)",
                (runner_id, H.now()),
            )
        return {"signed_out": True}

    # Test controls precede the application's static-file mount.
    app.router.routes.insert(0, app.router.routes.pop())
    app.state.vault.cipher = CredentialCipher("test-key", FakeKMS())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    people = [{"id": "ana", "name": "Ana Rivera", "email": OWNER, "title": "CEO", "primary_for": ["*"]}]
    with app.state.store.transaction() as c:
        H.sync_registry(c, {}, {"people": people})
        # Computers tests start with an existing owner, whose first-visit tour is already complete.
        save_state(c, Identity("human:ana", "owner", OWNER), {"tour": True})
        c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode({"people": people}),))
        c.execute(
            "INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),)
        )
    received, ready = tmp / "received.txt", {}
    received.write_text("")
    for label in ("Team box", "Lab box", "Spare box"):
        threading.Thread(
            target=fake_computer, args=(url, "admin", label, received, ready, label != "Spare box"), daemon=True
        ).start()
    while len(ready) < 3:
        time.sleep(0.05)
    subscriptions = moved_keys(url, app, ready)
    print(
        json.dumps(
            {
                "url": url,
                "token": LOCAL_TOKEN,
                "received": str(received),
                "computers": ready,
                "subscriptions": subscriptions,
            }
        ),
        flush=True,
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
