"""Presence belongs to a window, and is read as a list of people."""
from datetime import timedelta

from fastapi import Request
from pydantic import Field

from . import media, models as M
from .store import H, Problem
from .views import human_only


class Heartbeat(M.Contract):
    place: str = Field(min_length=1, max_length=240)
    session_id: str = Field(min_length=1, max_length=120)
    gone: bool = False


def authorized(c, auth, who, place):
    kind, sep, ident = place.partition(':')
    try:
        if sep and ident and kind == 'meeting':
            return media.authorized(c, who, ident)
        if sep and ident and kind == 'task':
            return auth.task(c, who, ident)
    except Problem:
        pass
    raise Problem('not_found', 'Place not found', 404)


def cutoff():
    return (H.parse_ts(H.now()) - timedelta(seconds=30)).isoformat().replace('+00:00', 'Z')


def listing(c, place):
    return {'actors': [{'actor': row[0], 'name': H.actor_name(c, row[0])} for row in c.execute(
        'SELECT DISTINCT actor FROM presences WHERE place=? AND seen_at>? ORDER BY actor', (place, cutoff()))]}


def install(app, store, mutate, auth):
    @app.get('/api/v2/presence')
    def get(request: Request, place: str):
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            authorized(c, auth, who, place)
            return listing(c, place)

    @app.post('/api/v2/presence')
    def post(request: Request, body: Heartbeat):
        who = request.state.identity
        human_only(who)
        def work(c):
            authorized(c, auth, who, body.place)
            c.execute('DELETE FROM presences WHERE place=? AND seen_at<=?', (body.place, cutoff()))
            existing = c.execute('SELECT actor FROM presences WHERE place=? AND session_id=?',
                                 (body.place, body.session_id)).fetchone()
            if existing and existing[0] != who.actor:
                raise Problem('session', 'This session belongs to another person', 409)
            if body.gone:
                c.execute('DELETE FROM presences WHERE place=? AND session_id=? AND actor=?',
                          (body.place, body.session_id, who.actor))
            else:
                c.execute('INSERT INTO presences(id,place,actor,session_id,seen_at) VALUES(?,?,?,?,?) '
                          'ON CONFLICT(place,session_id) DO UPDATE SET seen_at=excluded.seen_at',
                          (H.new_id(), body.place, who.actor, body.session_id, H.now()))
            return listing(c, body.place)
        return mutate(request, body, work, check=lambda c: authorized(c, auth, who, body.place))
