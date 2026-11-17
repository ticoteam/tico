"""Company is one page now, a person's own (`person:<id>`, backend/cards.py), and the Floor is its first card. A page
nobody saved shows it among its defaults; a page saved before the Floor was a card gets it here, once: a new Floor card
made by its person, pinned first and full width, the other pins moved down one. A person who removes it later keeps it
removed, since this runs once. A page that already holds a Floor is left as it is. It reads no other migration's schema,
so it runs in any order. A page already at the pin limit is skipped: the Floor would take it over.
"""

import uuid
from datetime import datetime, timezone

PIN_LIMIT = 60  # backend/cards.py PLACE_MAX, copied: a migration reads no app code


def up(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"cards", "pins", "pin_places"} <= tables:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    places = [
        r[0]
        for r in conn.execute(
            "SELECT place FROM pin_places WHERE place LIKE 'person:%' AND place NOT IN ("
            "SELECT p.place FROM pins p JOIN cards k ON p.item='card:'||k.id WHERE k.template='floor') "
            "AND (SELECT count(*) FROM pins q WHERE q.place=pin_places.place) < ?",
            (PIN_LIMIT,),
        )
    ]
    for place in places:
        actor = "human:" + place.partition(":")[2]
        cid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO cards(id,template,settings,title,made_by_id,created_at,updated_at,updated_by_id) "
            "VALUES(?,'floor','{}','',?,?,?,?)",
            (cid, actor, now, now, actor),
        )
        conn.execute("UPDATE pins SET position=position+1 WHERE place=?", (place,))
        conn.execute(
            "INSERT INTO pins(id,place,item,position,size,made_by,created_at) VALUES(?,?,?,0,'large',?,?)",
            (str(uuid.uuid4()), place, "card:" + cid, actor, now),
        )
