"""Helpers that other tests share, moved out of test_shared_bots.py so they import no test file."""

from backend.tests.helpers.api import get, post


def revision(api, bot):
    return get(api, 'bots/' + bot)['revision']


def share(api, enabled=True):
    post(api, 'bots/cpo/definition', {'shared': enabled, 'expected_revision': revision(api, 'cpo')})


def branch(api, computer=None, token='cara-test', bot='cpo', expected=200):
    return post(
        api, f'bots/{bot}/copies', {'runner_id': computer['runner_id']} if computer else {}, token, expected=expected
    )
