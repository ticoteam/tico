"""Helpers that other tests share, moved out of test_bot_product_repos.py so they import no test file."""

from backend import hubdb as H
from backend.store import encode
from backend.tests.helpers.github_app import connect, runner_token
from backend.enqueue import enqueue_stored


def ready(api, gh, administration="write"):
    """The Owner on the roster, GitHub connected, and bot cmo in a running turn (the `bot-test` credential)."""
    connect(api, administration="true")
    gh.permissions = {"administration": administration, "metadata": "read"}
    runner_token(api, "cmo")
    with api.app_state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata VALUES('owner',?)", (encode({"email": "ana@acme.example"}),)
        )
        c.execute(
            "INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) "
            "VALUES('m-cmo','keeper','bot:cmo','request','Make the services repository',?)",
            (H.now(),),
        )
        enqueue_stored(c, 'm-cmo')
        job = c.execute("SELECT id FROM jobs WHERE bot_id='cmo'").fetchone()["id"]
        c.execute(
            "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
            "VALUES('a1',?,'cmo','r1',1,'x-cmo','running',?,?)",
            (job, H.shift(H.now(), hours=1), H.now()),
        )
