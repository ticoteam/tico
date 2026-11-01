"""A real Tico server for the Computers page browser test (ui/tests/computers-shared.cjs).

    python3 ui/tests/support/computers_server.py

A loopback install (local owner bearer) with the credential vault on, and three fake computers, "Team box", "Lab box"
and "Spare box", with Claude Code installed and not signed in. Spare box only sends heartbeats, reporting Codex and not
Claude Code: it never takes a job or fetches a key, so it stays a computer a key serves that has reported nothing for
that tool. Each fake computer does what runner-rs/src/signin.rs does with a key:
it takes a `use_key` job, fetches the staged key from `runner-model-credentials`, reports it working and from then on
says Claude Code signs in with that key; it also fetches its keys every second, as a credentials change tells it to,
and signs in with a shared key it is handed. Every key value a computer received is appended to the file named
`received`, so the test can show the key reached the computer while the page never showed it again.
Prints one JSON line when ready: {"url", "token", "received", "computers": {label: id}}. Stops on SIGTERM.
"""

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
from backend.tests.test_credentials import FakeKMS  # noqa: E402

LOCAL_TOKEN = "local-owner-token-for-the-browser-test-0123456789"
OWNER = "ana@acme.example"
CAPABILITIES = ["model_sign_out_v1", "model_keys_v1"]


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_computer(base, admin, label, received, ready, works=True, shared_fetch=None):
    def call(method, path, body=None, token=admin, **params):
        r = httpx.request(method, base + "/api/v2/" + path, json=body, params=params or None, timeout=20,
                          headers={"Authorization": "Bearer " + token, "Idempotency-Key": H.new_id()})
        r.raise_for_status()
        return r.json()
    code = call("POST", "enrollments", {"owner_id": "ana"})["code"]
    made = call("POST", "runners/enroll", {"code": code, "label": label, "platform": "linux"}, token="none")
    token = made["token"]
    claude = {"installed": True, "authenticated": "missing"}
    runtimes = {"claude": claude} if works else {"codex": {"installed": True, "authenticated": "missing"}}

    def beat():
        call("POST", "runners/heartbeat", {"version": "test", "platform": "linux", "capabilities": CAPABILITIES,
                                           "readiness": {"schema_version": 1, "runtimes": runtimes, "bots": {}}},
             token)

    def use(credential, source):
        with open(received, "a") as f:
            f.write(credential["value"] + "\n")
        claude.clear()
        claude.update({"installed": True, "authenticated": "ready", "key": {"name": credential["env"], "source": source},
                       "account": {"kind": "api_key", "checked_at": H.now()}})
    beat()
    ready[label] = made["runner_id"]
    while not works:
        beat()
        time.sleep(1)
    while True:
        for job in call("GET", "runner-logins", token=token)["logins"]:
            state = "failed"
            if job["kind"] == "use_key":
                found = call("GET", "runner-model-credentials", token=token, runtime="claude", login=job["id"])
                use(found["credentials"][0], "computer")
                state = "done"
            elif job["kind"] == "clear_key":
                claude.clear()
                claude.update({"installed": True, "authenticated": "missing"})
                state = "signed_out"
            elif job["kind"] == "use_shared_key":
                state = "done"    # the key fetch below signs it in
            call("POST", f"runner-logins/{job['id']}/report", {"state": state, "message": ""}, token=token)
            if job["kind"] == "use_shared_key" and shared_fetch:
                # A real runner can heartbeat its cleared state before it fetches the newly requested shared key.
                beat()
                paused, release = shared_fetch
                paused.set()
                if not release.wait(30):
                    raise RuntimeError("the test did not release the shared-key fetch")
        found = call("GET", "runner-model-credentials", token=token, runtime="claude")
        keys = found["credentials"]
        if shared_fetch and keys and keys[0]["source"] == "shared" and not shared_fetch[0].is_set():
            # A key poll can race ahead of the request job. Keep this fixture unsigned until that job takes the gate.
            beat()
            time.sleep(0.05)
            continue
        if found.get("signed_out") or found.get("key_off"):
            claude.clear()
            claude.update({"installed": True, "authenticated": "missing"})
        elif keys and keys[0]["source"] == "shared" and claude.get("key", {}).get("source") != "shared":
            use(keys[0], "shared")
        elif keys and keys[0]["source"] == "shared" and keys[0]["value"] not in open(received).read().split("\n"):
            use(keys[0], "shared")    # the shared key was replaced
        beat()
        time.sleep(0.5)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="tico-computers-"))
    registry = tmp / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner_id": OWNER}))
    port = free_port()
    url = "http://127.0.0.1:%d" % port
    settings = Settings(db_path=tmp / "hub.db", registry_dir=registry, public_url=url, company_name="Acme",
                        app_name="Acme HQ", local_owner_email=OWNER, local_owner_token=LOCAL_TOKEN,
                        credential_kms_key="test-key", credential_admins=(OWNER,),
                        test_identities={"admin": Identity("human:ana", "owner", OWNER)})
    app = create_app(settings)
    paused, release = threading.Event(), threading.Event()
    if "--hold-shared-fetch" in sys.argv:
        @app.get("/test/shared-fetch")
        def shared_fetch_state():
            return {"waiting": paused.is_set()}
        app.router.routes.insert(0, app.router.routes.pop())

        @app.post("/test/shared-fetch")
        def shared_fetch_release():
            release.set()
            return {"released": True}
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
        c.execute("INSERT INTO registry_metadata VALUES('onboarding',?)", (encode({"completed": "2026-01-01T00:00:00Z"}),))
    received, ready = tmp / "received.txt", {}
    received.write_text("")
    for label in ("Team box", "Lab box", "Spare box"):
        gate = (paused, release) if label == "Team box" and "--hold-shared-fetch" in sys.argv else None
        threading.Thread(target=fake_computer, args=(url, "admin", label, received, ready, label != "Spare box", gate),
                         daemon=True).start()
    while len(ready) < 3:
        time.sleep(0.05)
    print(json.dumps({"url": url, "token": LOCAL_TOKEN, "received": str(received), "computers": ready}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
