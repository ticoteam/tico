"""GET /api/v2/runner-credential-grants reads a computer's bots in one snapshot (the company-wide tool, key and grant reads
once, not once per bot) and answers exactly what each bot read on its own answered, names in the same order; and the
batch `tool_settings.names_each` and the snapshot's `held_tools` answer what `names` and `held_tools` answer alone."""

import random

from backend import tool_settings
from backend.auth import Identity
from backend.credentials import granted
from backend.tests.helpers.api import headers
from backend.tests.helpers.runner_grants import reference, seed

BOTS = ["tools-owner"] + [f"bot{n:02d}" for n in range(47)]


def answer(api, runner_id):
    token = "runner-" + runner_id
    api.app.state.store.settings.test_identities[token] = Identity("runner:" + runner_id, "runner", runner_id=runner_id)
    r = api.get("/api/v2/runner-credential-grants", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_one_pass_answers_byte_for_byte_what_each_bot_read_alone_answered(api):
    store = api.app.state.store
    for seed_value in (3, 11):
        rng = random.Random(seed_value)
        with store.transaction() as c:
            c.execute("DELETE FROM credential_grants")
            c.execute("DELETE FROM tool_settings")
            c.execute("DELETE FROM credentials")
            c.execute("DELETE FROM tools")
            seed(c, BOTS, rng, tools=40, runners=4)
        held_any = settings_any = 0
        for runner_id in ("r0", "r1", "r2", "r3"):
            body = answer(api, runner_id)
            expected = reference(store, runner_id)
            assert body == expected and list(body["bots"]) == list(expected["bots"])
            for bot, names in body["bots"].items():
                assert names == expected["bots"][bot], bot  # the same names in the same order
            held_any += sum(bool(n) for n in body["bots"].values())
            settings_any += sum(any(n.endswith(("_HOST", "_REGION", "_ID")) for n in v) for v in body["bots"].values())
        assert held_any > 20 and settings_any > 5  # the fixture exercises keys and settings, not empty answers

        # The batch and the snapshot cache answer what the per-subject helpers answer outside a snapshot.
        with store.read() as c:
            subjects = ["bot:" + b for b in BOTS] + ["human:ana", "human:ben", "computers"]
            helds = [granted(c, s) for s in subjects] + [frozenset(), frozenset({"nothing"})]
            alone = [tool_settings.names(c, h) for h in helds]
            tools_alone = [tool_settings.held_tools(c, h) for h in helds]
        with store.read_transaction() as c:
            assert tool_settings.names_each(c, helds) == alone
            assert [tool_settings.held_tools(c, h) for h in helds] == tools_alone
            assert [granted(c, s) for s in subjects] == helds[: len(subjects)]


def test_a_write_in_the_snapshot_is_seen_by_the_next_held_tools(api):
    """The key-to-tool map kept in a snapshot is dropped when the connection writes, as every read_cache value is."""
    store = api.app.state.store
    with store.transaction() as c:
        seed(c, BOTS[:6], random.Random(5), tools=6, runners=1)
        held = frozenset(r[0] for r in c.execute("SELECT id FROM credentials WHERE tool_id='tool01'"))
        assert tool_settings.held_tools(c, held) == ["tool01"]
        c.execute("UPDATE credentials SET tool_id='tool02' WHERE tool_id='tool01'")
        assert tool_settings.held_tools(c, held) == ["tool02"]
        c.execute("UPDATE credentials SET ciphertext=NULL WHERE tool_id='tool02'")
        assert tool_settings.held_tools(c, held) == []
