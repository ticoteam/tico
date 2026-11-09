"""Helpers that other tests share, moved out of test_mail_targets.py so they import no test file."""

from backend import bot_settings as BS


def declare(api, identity, **extra):
    """Give the `inbox` bot (Ana's message bot) a gmail tool with this identity, in bot.yaml's shape."""
    tools = [{"service": "gmail", "identity": identity, "can": ["read", "draft"], **extra}]
    with api.app.state.store.transaction() as c:
        BS.write(c, 'inbox', {'name': 'inbox', 'runtime': 'fake', 'status': 'active', 'tools': tools})
