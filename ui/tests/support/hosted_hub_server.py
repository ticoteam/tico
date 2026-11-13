"""A real hosted Tico company for the phone browser tests (ui/tests/phone-dead-ends.cts): the app in-process on a fresh
temporary database, hosted (TICO_HOSTED=invited) on Tico Cloud, with the shipped catalog and the default company the
setup page makes when nothing is picked (the built-ins and the default chart's bots). Only HQ is made up: its machines
routes (the tests' FakeHQ) and the cloud access it reports.

    python3 ui/tests/support/hosted_hub_server.py --state no-computer

States, each a first day a person really starts from:
    no-computer   setup finished, the bots are on the shared cloud computer, and HQ has made none yet
    no-credit     the same company out of credit (cloud access frozen)
    setup-halfway setup not finished: the owner closed it halfway
    sign-off      a task in the Product pipeline waits on the owner's sign-off
    bot-waiting   a bot asked the owner a question and waits for the answer

GitHub is never connected and no model plan is signed in, in every state. Prints one JSON line when ready:
{"url", "state", "token", "facts": {...ids the test opens}}. Stops on SIGTERM and removes its temporary folder.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
# Nothing leaves for Tico HQ, and no update check.
os.environ.update(
    {
        "TICO_SUPPORT": "off",
        "TICO_TELEMETRY": "off",
        "TICO_HQ_URL": "http://127.0.0.1:9",
        "TICO_UPDATE_CHECK": "off",
        "TICO_RUNNER_CONTAINER_PROBE": "off",
        "TICO_SQLITE_SYNC": "off",
    }
)

import uvicorn  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import bot_settings, cloud_access  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.auth import Identity  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.store import H, encode  # noqa: E402
from backend.tests.helpers.cloud_access import signals  # noqa: E402
from backend.tests.helpers.cloud_machines import FakeHQ  # noqa: E402

STATES = ("no-computer", "no-credit", "setup-halfway", "sign-off", "bot-waiting")
TOKEN = "owner-test"
BOT_TOKEN = "botops-test"
OWNER_EMAIL = "owner@first-day.example"
PEOPLE = {
    "default_user": "owner",
    "people": [{"id": "owner", "name": "Owner Person", "email": OWNER_EMAIL, "primary_for": ["*"]}],
}
# What a fresh environment seeds (templates/environment-registry/employees.yaml): the assistant and BotOps, planned.
SEED = {"assistant": {"name": "assistant", "status": "planned"}, "botops": {"name": "botops", "status": "planned"}}


def bound_socket():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


class Hub:
    def __init__(self, tmp, port):
        registry = tmp / "registry"
        registry.mkdir()
        (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": OWNER_EMAIL}))
        self.settings = Settings(
            db_path=tmp / "hub.db",
            registry_dir=registry,
            owner_email=OWNER_EMAIL,
            environment_id="first-day-1",
            company_name="First Day",
            app_name="Tico",
            assistant_name="Assistant",
            assistant_bot="assistant",
            enabled_providers=(),
            hosted="invited",
            public_url=f"http://127.0.0.1:{port}",
            test_identities={
                TOKEN: Identity("human:owner", "owner", OWNER_EMAIL),
                BOT_TOKEN: Identity(
                    "bot:botops", "bot", agent="hermes"
                ),  # BotOps signed in as an agent, to ask as a bot does
            },
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.store = self.app.state.store
        with self.store.transaction() as c:
            H.sync_registry(c, SEED, PEOPLE)
            c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO NOTHING", (encode(PEOPLE),))
            for slug, config in SEED.items():
                c.execute("INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)", (slug, "owner", "bot-" + slug))
                bot_settings.write(c, slug, config)
        self.hq = FakeHQ()
        self.machines = self.app.state.cloud_machines
        self.machines.client = self.hq

    def call(self, method, path, body=None, token=TOKEN):
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}
        reply = self.client.request(method, "/api/v2/" + path, json=body, headers=headers)
        if reply.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {reply.status_code} {reply.text[:400]}")
        return reply.json() if reply.content else None

    def cloud(self, access="open", reason=None):
        cloud_access.apply(self.store, cloud_access.parse(signals(access, reason)))

    def finish_setup(self):
        self.call(
            "put",
            "onboarding",
            {
                "names": {"company_name": "First Day", "app_name": "Tico", "assistant_name": "Assistant"},
                "answers": {"what_we_do": "We sell a booking app to salons.", "customers": "businesses"},
                "selected": {},
            },
        )
        return self.call("post", "onboarding/complete", {})


def build(hub, state):
    facts = {}
    hub.cloud()
    hub.machines.tick()  # HQ lists its sizes: the company's bots may run on cloud machines
    if state == "setup-halfway":
        return facts
    done = hub.finish_setup()
    facts["bots"] = sorted(row["slug"] for row in done["bots"])
    hub.machines.tick()
    if state == "no-credit":
        hub.cloud("frozen", "out_of_credit")
    if state in ("sign-off", "bot-waiting"):
        # BotOps, on the first bot's setup task, asks the owner: for an OK, or a question only the owner can answer.
        task = hub.call("get", "tasks")["tasks"][0]
        with hub.store.transaction() as c:
            c.execute(
                "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('botops','hermes','x-botops',?,?)",
                (H.now(), "human:owner"),
            )
        facts["agent"] = "botops"  # the walk waits for its row on Settings > Computers before auditing
        ask = (
            {"to": "owner", "kind": "ok", "line": "OK to give the Builder write access to the app repository?"}
            if state == "sign-off"
            else {
                "to": "owner",
                "kind": "answer",
                "line": "Which GitHub organization holds your app's code?",
                # A bot's question to a person says why it matters (backend/ask_shape.py).
                "why": "The Builder can only open pull requests in that organization.",
            }
        )
        hub.call("post", f"tasks/{task['id']}/asks", {**ask, "version": task.get("version", 1)}, token=BOT_TOKEN)
        facts["task"] = task["id"]
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", default="no-computer", choices=STATES)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    tmp = Path(tempfile.mkdtemp(prefix="hosted-hub-"))
    server = thread = hub = None
    try:
        sock = bound_socket()
        hub = Hub(tmp, sock.getsockname()[1])
        facts = build(hub, args.state)
        server = uvicorn.Server(
            uvicorn.Config(
                hub.app,
                host="127.0.0.1",
                port=sock.getsockname()[1],
                log_level="warning",
                lifespan="off",
                timeout_graceful_shutdown=1,
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        for _ in range(600):
            if server.started or not thread.is_alive():
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("the hub did not start")
        print(
            json.dumps({"url": hub.settings.public_url, "state": args.state, "token": TOKEN, "facts": facts}),
            flush=True,
        )
        while True:
            time.sleep(1)
    finally:
        if server is not None:
            server.should_exit = True
            thread.join(10)
        if hub is not None:
            hub.client.__exit__(None, None, None)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
