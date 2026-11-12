"""Helpers that other tests share, moved out of test_counsel.py so they import no test file."""

from backend.auth import Identity
from backend.blobs import register
from backend.tests.helpers.api import post
from backend.tests.helpers.org_rules import as_bot
from backend import bot_settings as BS


def counsel_on(api):
    """Counsel is built in and active, as onboarding makes it: a bot row, a config, and its own token."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO bots(id, slug, display_name, state) VALUES('rules-owner', 'rules-owner', 'Counsel', 'active')"
        )
        c.execute("INSERT OR REPLACE INTO bot_config(bot,team,owner_id) VALUES('rules-owner','','ana')")
        BS.write(c, 'rules-owner', {})
    as_bot(api, "rules-owner", "counsel-test")


def store_file(api, text, title="Acme Vendor Terms", bot="ops"):
    """A document in Files, as a person's upload: its current version holds `text`."""
    data = text.encode("utf-8")
    with api.app.state.store.transaction() as c:
        digest = api.app.state.blobs.put(data, "text/plain")
        item = register(c, Identity("human:ana", "human"), digest, len(data), title + ".txt", "text/plain")
        row, _, _ = api.app.state.files.add_version(
            c,
            bot=bot,
            actor="human:ana",
            scope="bot",
            task=None,
            conversation=None,
            attempt="",
            identity="upload:" + title,
            title=title,
            name=title + ".txt",
            mime="text/plain",
            blob_id=item["id"],
            digest=digest,
            size=len(data),
        )
        return row["id"], row["current_version"]


def source(api, name="Acme Vendor", kind="vendor"):
    return post(api, "rule-sources", {"name": name, "kind": kind})["source"]["id"]


def counsel_run(api):
    """Counsel's runner claims its next job: the run on the read task a person just opened. Its token is the run's own,
    so every call it makes goes through the API's private-run guard (backend/task_privacy.py guard_write)."""
    from backend.tests.helpers.api import assign, claim, ready, runner

    r = runner(api, "ana")
    assign(api, r, "rules-owner")
    ready(api, r, ["rules-owner"])
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "local-thread"}, token=r["token"])
    return r, attempt
