"""A push to a bot's repository is told to the computer that runs it (backend/github.py `tell_pushed`), as a `pushed`
hint on its event stream with no value, so its next chat turn does not start on a fetch from before the push
(runner-rs/src/fetch_fresh.rs). Only a computer that says it takes the hint is told: an older runner reads every
endpoint again for a kind it does not know. Another computer, and another company's, hear nothing."""

import hashlib
import hmac
import json

from backend import events as E
from backend import github as G
from backend import hubdb as H
from backend.tests.helpers.api import assign, ready, runner
from backend.tests.helpers.api_app import api_client

SECRET = "push-hints-secret"


def hook(api, payload):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = api.post(
        G.PATH,
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push", "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def push(repo, ref="refs/heads/main", **more):
    return {
        "ref": ref,
        "after": "b" * 40,
        "repository": {"full_name": repo, "default_branch": "main"},
        "head_commit": {"id": "b" * 40},
        **more,
    }


def pushed(api, computer, after):
    """The `pushed` hints computer `computer` is sent after change `after`."""
    with api.app.state.store.read() as c:
        sent = E.runner_read(c, computer["runner_id"], "runner:" + computer["runner_id"], after)[0]
    return [data for _, data in sent if data["kind"] == "pushed"]


def newest(api):
    with api.app.state.store.read() as c:
        return E.latest(c)


def company(api, bots):
    """`bots`: slug -> (its repository, its computer, whether that computer takes the hint). The bots' ids."""
    api.app.state.store.settings.github_webhook_secret = SECRET
    ids = {}
    for slug, (repo, computer, takes) in bots.items():
        with api.app.state.store.transaction() as c:
            ids[slug] = H.resolve_bot(c, slug)["id"]
            c.execute("UPDATE bot_config SET repo=? WHERE bot=?", (repo, ids[slug]))
        assign(api, computer, slug)
        ready(api, computer, [slug], capabilities=[G.PUSH_HINTS] if takes else ())
    return ids


def test_a_push_tells_only_the_computers_of_that_repositorys_bots_that_take_the_hint(api, api_database, tmp_path):
    mine, theirs, old = runner(api, label="Mine"), runner(api, label="Theirs"), runner(api, label="Old")
    ids = company(
        api,
        {
            "ops": ("acme/ops", mine, True),
            "cpo": ("acme/cpo", theirs, True),
            # The same repository, on a computer that does not say it takes the hint.
            "finance": ("acme/ops", old, False),
        },
    )
    # Another company with a bot of the same name and repository, on a computer that takes the hint.
    other_company = api_client(tmp_path / "other", api_database)
    other = next(other_company)
    try:
        elsewhere = runner(other, label="Elsewhere")
        company(other, {"ops": ("acme/ops", elsewhere, True)})
        start, other_start = newest(api), newest(other)

        answer = hook(api, push("Acme/Ops"))
        assert answer["told"] == [ids["ops"]]
        hints = pushed(api, mine, start)
        assert hints == [{"seq": hints[0]["seq"], "kind": "pushed", "bots": [ids["ops"]]}]
        assert pushed(api, theirs, start) == [] and pushed(api, old, start) == []
        assert pushed(other, elsewhere, other_start) == []

        # Another branch (a task's), a deleted default branch, a tag: nobody's checkout tracks it.
        after = newest(api)
        for payload in (
            push("acme/ops", "refs/heads/tico/task-1"),
            push("acme/ops", deleted=True),
            push("acme/ops", "refs/tags/v1"),
        ):
            assert hook(api, payload)["told"] == []
        assert pushed(api, mine, after) == []
    finally:
        other_company.close()
