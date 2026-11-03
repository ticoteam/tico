"""Screen permission policy and durable audit records.

Rust owns every ticket, session and WebSocket. The backend authorizes people and runners,
publishes grants only after their opening audit transaction commits, and records close reports.
"""
from fastapi import Request

from .store import H, Problem

MODES = ("off", "view", "control")
CAPABILITY = "screen_v1"
WIDTH, HEIGHT = 1440, 900   # the runner's default display, when its report names none

UNAVAILABLE = "This computer has no screen. Turn it on on the computer first."


def available(row):
    """Whether the computer's last heartbeat offered a screen."""
    return CAPABILITY in set(H._json(row["capabilities"], []) or []) if row else False


def size(row):
    screen = (H._json(row["readiness"], {}) or {}).get("screen") or {}
    return int(screen.get("width") or WIDTH), int(screen.get("height") or HEIGHT)


def first_offer(c, rid):
    """A new computer's first screen offer sets its Screen to Control, unless a person already chose one. Control
    widens no one's access: only the computer's operator and admins open a screen (may_change)."""
    if c.execute("UPDATE runners SET screen='control',screen_default_pending=0 WHERE id=? AND screen_default_pending=1",
                 (rid,)).rowcount:
        H.event(c, "system", "runner.screen", rid, {"mode": "control", "reason": "first offer"})


def listing(row):
    """The fields a computer's row carries in the Operations and Computers listings."""
    return {"screen": row["screen"] if row["screen"] in MODES else "off", "screen_available": available(row)}


def may_change(auth, who, runner):
    """The same rule as restart and inbox sharing: an admin, or the person whose computer it is."""
    return bool(runner) and (auth.bot_admin(who) or who.role == "human" and who.actor == "human:" + runner["owner_id"])


def _allows(mode, control):
    return mode == "control" or mode == "view" and not control


def registry(store):
    if getattr(store, "screen_sessions", None) is None:
        from .screen_relay import RemoteRegistry
        store.screen_sessions = RemoteRegistry(store)
    return store.screen_sessions


def install(app, store, auth, mutate, execution, settings):
    from . import models as M
    from .views import human_only
    sessions = registry(store)
    from .screen_relay import install_policy
    install_policy(app, store, auth, execution, settings)

    def computer(c, rid):
        return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()

    @app.post("/api/v2/runners/{rid}/screen")
    def screen_mode(request: Request, rid: str, body: M.ScreenMode):
        """Who may open this computer's screen from the app: off, view, or control. The computer's operator or an
        admin; a mode other than off needs the screen turned on on the computer. Off closes open sessions, view
        closes the ones in control."""
        who = request.state.identity

        def work(c):
            runner = computer(c, rid)
            if not may_change(auth, who, runner):
                raise Problem("forbidden", "You cannot change this computer", 403)
            if body.mode != "off" and not available(runner):
                raise Problem("screen_unavailable", UNAVAILABLE, 409)
            c.execute("UPDATE runners SET screen=?,screen_default_pending=0 WHERE id=?", (body.mode, rid))
            H.event(c, who.actor, "runner.screen", rid, {"mode": body.mode})
            return {"screen": body.mode, "screen_available": available(runner)}
        result = mutate(request, body, work)
        # The mode now stored, not this request's (a replayed older request must not reopen anything).
        with store.read() as c:
            row = c.execute("SELECT screen,revoked_at FROM runners WHERE id=?", (rid,)).fetchone()
        mode = row["screen"] if row and not row["revoked_at"] and row["screen"] in MODES else "off"
        sessions.set_mode(rid, mode, "screen turned off" if mode == "off" else "view only now")
        return result

    @app.post("/api/v2/runners/{rid}/screen/sessions")
    def open_screen(request: Request, rid: str, body: M.ScreenSessionStart):
        """Open the computer's screen: a single-use ticket for the browser's websocket at `ws_path`, good for a
        minute. The computer's operator or an admin, in person: not the Assistant, BotOps or a personal token."""
        who = request.state.identity
        human_only(who)
        if who.via or who.via_token:
            raise Problem("forbidden", "Open the screen yourself in the app", 403)
        admitted = []

        def work(c):
            # Inside the write lock a mode change and a revocation take, so neither can pass this check unseen.
            runner = computer(c, rid)
            if not may_change(auth, who, runner):
                raise Problem("forbidden", "You cannot open this computer's screen", 403)
            mode = runner["screen"] if runner["screen"] in MODES else "off"
            if mode == "off":
                raise Problem("screen_off", "This computer's screen is off. Turn it on in Settings > Computers.", 403)
            if not available(runner):
                raise Problem("screen_unavailable", UNAVAILABLE, 409)
            if body.control and mode != "control":
                raise Problem("screen_view_only", "This computer's screen is view only", 403)
            width, height = size(runner)
            session, ticket = sessions.admit(rid, who.actor, body.control, width, height, mode)
            admitted.append(session)
            H.event(c, who.actor, "runner.screen_open", rid,
                    {"session_id": session.id, "control": body.control,
                     "relay_lease": session.relay_lease})
            # A `runner` change row is the runner's `screen_open` event (backend/events.py runner_read).
            c.execute("INSERT INTO changes(topic,kind,subject_id,ref,created_at) VALUES('runner','screen_open',?,?,?)",
                      (rid, session.id, H.now()))
            return {"session_id": session.id, "ticket": ticket, "control": body.control,
                    "ws_path": f"/api/v2/runners/{rid}/screen/ws?ticket={ticket}",
                    "width": width, "height": height}
        try:
            result = store.write(who, work)     # never `mutate`: its replay record would keep the ticket
            sessions.publish(admitted[0])  # prepared grants become visible only after the audit commit
            return result
        except Exception:
            for session in admitted:            # the write failed after admission: close it, waking any socket
                sessions.end(session, 1011, "could not open the screen")
            raise

    @app.get("/api/v2/runners/me/screen/sessions")
    def pending_screens(request: Request):
        """The screen sessions waiting for this computer to answer: the backup for a missed `screen_open` event."""
        who = request.state.identity
        with store.read() as c:
            runner = execution.runner(c, who)
            if not runner:
                raise Problem("forbidden", "This computer is no longer registered", 403)
        return {"sessions": sessions.pending(who.runner_id)}

    return sessions
