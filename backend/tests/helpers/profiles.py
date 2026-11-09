"""Helpers that other tests share, moved out of test_profiles.py so they import no test file."""

from backend import hubdb as H
from backend import profiles as P
from backend import bot_settings as BS


def add_bot(c, slug, **config):
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"}}, {"people": []})
    c.execute('INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)', (slug, None, 'ana'))
    BS.write(c, slug, {'name': slug, 'runtime': 'fake', 'status': 'active', **config})


def profile_of(c, slug):
    return BS.read(c, slug)


def put(c, slug, name, **options):
    """Give an existing bot a profile the way the API does (without the Strategy Bot gate or audit)."""
    declared = profile_of(c, slug)
    BS.write(c, slug, P.apply(declared, name, options))
