"""Ringing the people a new call rings, by every way other modules register.

`HOOKS` is a list of functions `hook(app, actors, payload)`; backend/web_push.py adds one that sends the ring to each
rung person's browsers. With no hook, ringing does nothing beyond what the call view already shows. A hook that fails
is logged and the rest still run: a ring never stops a call from starting.
"""

import logging

HOOKS = []


def ring(app, call_id, actors, payload):
    """Hand `payload` ({title, body, url, tag}) for call `call_id` to every hook, for the actors it rings."""
    for hook in list(HOOKS):
        try:
            hook(app, actors, payload)
        except Exception:
            logging.getLogger(__name__).exception("call ring hook failed for call %s", call_id)
