"""Turn levels, billing, cloud access, hosted servers, and the access view."""

from . import access as Access
from . import models as M
from .store import Problem
from fastapi import Request
from fastapi.responses import Response
from . import cloud_access
from . import company_billing
from . import demo_billing
from . import gateway_connection
from . import invites
from . import recorder_installs
from . import team_rules
from .usage import own_bots as usage_bots
from . import views


def register(app, admin_only, auth, mutate, settings, store):
    @app.get('/api/v2/settings/turn-levels')
    def turn_levels(request: Request):
        admin_only(request.state.identity, 'sees company turn settings')
        from . import org_turns

        with store.read_transaction() as c:
            # Settings only shows levels and in_use; a fleet snapshot would classify every
            # queued bot on every computer for fields this page never renders.
            return org_turns.admission(c)

    @app.post('/api/v2/settings/turn-levels')
    def turn_levels_set(request: Request, body: M.TurnLevels):
        from . import org_turns

        def permitted(c):
            auth.sync_access(c)
            who = request.state.identity
            # Rebuild human roles under the writer lock, including on an idempotent replay.
            current = (
                auth.identity_for_actor(c, who.actor) if who.role in ('owner', 'human') and not who.support else who
            )
            admin_only(current, 'changes company turn settings')

        return mutate(
            request,
            body,
            lambda c: org_turns.configure(c, request.state.identity.actor, body, settings),
            check=permitted,
        )

    # ------------------------------------------------------------------ billing (Settings > Billing)
    def billing_key(who, does="sees billing"):
        """The gateway key and Gateway URL Settings > Billing reads HQ with. Owners and admins only."""
        admin_only(who, does)
        with store.read() as c:
            return gateway_connection.target(c, app.state.vault, settings)[:2]

    @app.get("/api/v2/billing")
    def billing_view(request: Request, month: str = ""):
        """The company's balance and status, credits, usage by day and the month's statement, from Tico HQ; a Tico
        Cloud company's access (`cloud`) too, kept at once so the app and the page agree (backend/cloud_access.py)."""
        key_base = billing_key(request.state.identity)
        if settings.demo:  # a sample from the demo's own usage (backend/demo_billing.py); HQ is not reached
            with store.read() as c:
                return demo_billing.read(c, usage_bots(c, auth, request.state.identity), month)
        answer = company_billing.read(*key_base, month)
        cloud_access.from_billing(store, answer)
        return answer

    @app.get("/api/v2/cloud-access")
    def cloud_access_view(request: Request):
        """A Tico Cloud company's access for every page: open, frozen or locked, why, and where to pay. Null off
        cloud. Open to every signed-in person, also while locked."""
        views.human_only(request.state.identity)
        return {"cloud": cloud_access.view(cloud_access.current(store))}

    @app.get("/api/v2/billing/usage")
    def billing_day(request: Request, day: str = ""):
        """One day's usage by hour, from Tico HQ."""
        key_base = billing_key(request.state.identity)
        if settings.demo:  # the same sample as the page's, hour by hour (backend/demo_billing.py)
            with store.read() as c:
                return demo_billing.day(c, usage_bots(c, auth, request.state.identity), day)
        return company_billing.day(*key_base, day)

    @app.get("/api/v2/billing/statements/{month}.csv")
    def billing_statement_csv(request: Request, month: str):
        """The month's statement as CSV, the same lines and numbers as on screen."""
        key_base = billing_key(request.state.identity)
        if settings.demo:  # the same lines as the page's statement (backend/demo_billing.py)
            with store.read() as c:
                body = demo_billing.statement_csv(c, usage_bots(c, auth, request.state.identity), month)
        else:
            body = company_billing.statement_csv(*key_base, month)
        return Response(
            body,
            media_type="text/csv; charset=utf-8",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'attachment; filename="tico-statement-%s.csv"' % month,
            },
        )

    # Settings > Computers: the company's hosted servers, and Start for a stopped one (HQ refuses while out of credit).
    @app.get("/api/v2/hosted-servers")
    def hosted_servers(request: Request):
        """The company's Tico server and bot computers HQ hosts, each on or stopped; `none` when HQ hosts nothing."""
        key_base = billing_key(request.state.identity, "sees hosted servers")
        if settings.demo:  # the sample key goes nowhere: HQ hosts nothing the demo can show
            return {"state": "none"}
        return company_billing.servers(*key_base)

    @app.post("/api/v2/hosted-servers/{server_id}/start")
    def hosted_server_start(request: Request, server_id: str):
        """Start one stopped hosted server; `paused` with the add-credit words while the company is out of credit."""
        if settings.rehearsal:  # a copy of real data sends nothing: never a real server's power
            raise Problem("rehearsal", "Hosted servers can't be started from a rehearsal", 409)
        key_base = billing_key(request.state.identity, "starts hosted servers")
        if settings.demo:  # the sample key goes nowhere: a demo never starts a real server
            raise Problem("demo", "Hosted servers can't be started from the demo", 409)
        return company_billing.start(*key_base, server_id)

    @app.get("/api/v2/access")
    def access_view(request: Request):
        """Who can sign in, the roles, what members may do and the company domain. Owners and admins."""
        who = request.state.identity
        admin_only(who, "sees who can sign in")
        with store.read() as c:
            return {
                **Access.view(c, settings, views.roster(c), settings.proxy_kind, auth.owner_id(c)),
                "rules": team_rules.load(c),
                "you": auth.company_role(who),
                # When each person was last sent an invite (backend/invites.py), and whether this install's
                # sign-in could send one at all: only the built-in sign-in, through a Tico account issuer.
                "invites": {pid: sent["at"] for pid, sent in invites.sent(c).items()},
                "invite_email": invites.can_email(settings),
                # Whether the issuer also makes accounts with a generated password (Create password).
                "account_password": bool(invites.can_make(auth, settings)),
                # Each person's most recently used Tico Recorder (backend/recorder_installs.py).
                "recorders": recorder_installs.everyone(c),
            }
