"""A saved held model stays editable on the Model field (Dev 693): a save that keeps the bot's own held model, through
the settings transition or the bot model route, succeeds; a fresh pick of a held model, or of a different one, is still
refused. Synthetic store; no provider or billing call."""
from backend import bot_settings as BS
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401  (api is a fixture)


def _terra_bot(api, r):
    """ops saved on the held gpt-6.1-terra row, on a computer that reports codex ready."""
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        config = BS.read(c, "ops")
        config.update(runtime="codex", harness="codex", model="gpt-6.1-terra", reasoning_effort="medium")
        BS.write(c, "ops", config)


def _revision(api):
    return get(api, "bots/ops/access")["revision"]


def test_a_same_model_save_on_the_held_terra_keeps_it_and_a_fresh_pick_is_refused(api):
    r = runner(api)
    _terra_bot(api, r)
    # The Model field and the bot model route both keep the saved held model with a new effort.
    post(api, "bots/ops/transitions", {"kind": "model", "model": "gpt-6.1-terra", "effort": "high",
                                       "expected_revision": _revision(api)}, expected=200)
    with api.app.state.store.read() as c:
        assert BS.read(c, "ops")["reasoning_effort"] == "high"
        assert BS.read(c, "ops")["model"] == "gpt-6.1-terra"
    post(api, "bots/ops/model", {"model": "gpt-6.1-terra", "effort": "low",
                                 "expected_revision": _revision(api)}, expected=200)
    with api.app.state.store.read() as c:
        assert BS.read(c, "ops")["reasoning_effort"] == "low"
    # A bot on another model cannot pick Terra: the fresh pick is refused on both routes.
    with api.app.state.store.transaction() as c:
        config = BS.read(c, "ops")
        config.update(runtime="claude", harness="claude", model="claude-sonnet-5-5", reasoning_effort="high")
        BS.write(c, "ops", config)
    post(api, "bots/ops/transitions", {"kind": "model", "model": "gpt-6.1-terra", "expected_revision": _revision(api)},
         expected=422)
    post(api, "bots/ops/model", {"model": "gpt-6.1-terra", "expected_revision": _revision(api)}, expected=422)
