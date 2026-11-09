"""Helpers that other tests share, moved out of test_subscriptions.py so they import no test file."""

from backend import bot_settings as BS


def set_runtime(api, runtime, harness=None, bot="ops"):
    with api.app.state.store.transaction() as c:
        config = BS.read(c, bot)
        config.update(runtime=runtime, harness=harness or runtime)
        BS.write(c, bot, config)
