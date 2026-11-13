"""Access routes: limits, rules, allow list, owner transfer, owner email."""

from . import access as Access
from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from . import account_routes
from . import org_routes
from . import key_grants
from . import page_owners
from . import secret_drop
from . import team_rules
from . import views


def register(app, admin_only, auth, mutate, onboarding, owner_only, recruiter, settings, settings_admin, store):
    @app.put("/api/v2/access/limits")
    def access_limits(request: Request, body: M.AccessLimits):
        """How many active bots one member may have. Owners and admins."""
        who = request.state.identity
        admin_only(who, "sets the bot limit")

        def work(c):
            stored = Access._load_json(c, Access.ACCESS) or {}
            before = Access.load_access(c, settings)
            Access._store(c, Access.ACCESS, {**before, **stored, "member_bot_limit": body.member_bot_limit})
            H.event(
                c,
                who.actor,
                "access.limits_updated",
                "",
                {"before": before["member_bot_limit"], "after": body.member_bot_limit},
            )
            return {"member_bot_limit": body.member_bot_limit}

        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.put("/api/v2/access/rules")
    def access_rules(request: Request, body: M.AccessRules):
        """Turn the team's fast defaults off, or back on (backend/team_rules.py). The owner; an admin may change only
        the rules in `team_rules.ADMIN_RULES` (Stale ticket review, Start fresh when the topic changes)."""
        who = request.state.identity
        sent = {name for name, value in body.model_dump().items() if value is not None}
        if sent and sent <= set(team_rules.ADMIN_RULES):
            admin_only(who, "changes " + ", ".join(sorted(sent)))
        else:
            owner_only(who, "changes the team's rules")
        result = mutate(request, body, lambda c: team_rules.save(c, who.actor, body.model_dump()))
        with store.read() as c:
            auth.sync_access(c)  # who is a credential administrator follows the rule
        return result

    @app.get("/api/v2/page-owners")
    def page_owners_read(request: Request):
        """Each page's owner, the bot its Chat opens (backend/page_owners.py). Anyone on the team reads it."""
        with store.read() as c:
            return {"pages": page_owners.read(c)}

    @app.get("/api/v2/secret-rules")
    def secret_rules_read(request: Request):
        """The Tools chat's key detector rules (backend/secret_rules.json), which the page runs on a paste before it
        reaches the text box; the server runs the same ones (backend/secret_drop.py). Anyone on the team reads it."""
        return secret_drop.rules()

    @app.post("/api/v2/tools-chat/proposals")
    def key_grant_propose(request: Request, body: M.KeyGrantProposal):
        """The Tools owner's proposal for keys a person dropped in its chat: the server checks each row (a key saved
        there, a tool, a level, named bots: never the owner, every bot or every computer) and posts the card
        (backend/key_grants.py). Only the Tools page's owner bot."""
        who = request.state.identity
        rows = [r.model_dump() for r in body.rows]
        return mutate(request, body, lambda c: key_grants.propose(c, who, body.thread_id, rows, body.note))

    @app.post("/api/v2/tools-chat/proposals/{message_id}/grant")
    def key_grant_grant(request: Request, message_id: str, body: M.KeyGrantGrant):
        """Grant on the card: exactly the rows kept, then each tool's check; the card with a result per row. A
        credential administrator in that chat."""
        return key_grants.grant(store, request.app.state.vault, request.state.identity, message_id, body.keep)

    @app.put("/api/v2/page-owners/{page}")
    def page_owner_set(request: Request, page: str, body: M.PageOwnerSet):
        """Set or clear a page's owner. The owner or an admin; the page follows at its next load."""
        who = request.state.identity
        admin_only(who, "changes a page's owner")
        return mutate(request, body, lambda c: page_owners.save(c, who.actor, page, body.bot))

    @app.put("/api/v2/access/allow")
    def access_allow_save(request: Request, body: M.AccessAllowUpdate):
        who = request.state.identity
        owner_only(who, "changes who may sign in")

        def work(c):
            before, after = Access.save_access(c, who.actor, body.model_dump(), H.now(), settings=settings)
            H.event(
                c,
                who.actor,
                "access.allow_updated",
                "",
                {
                    "before": {k: before[k] for k in ("allowed", "allowed_domains", "revision")},
                    "after": {k: after[k] for k in ("allowed", "allowed_domains", "revision")},
                },
            )
            return {
                "revision": after["revision"],
                "allowed": after["allowed"],
                "allowed_domains": after["allowed_domains"],
            }

        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.post("/api/v2/access/owner")
    def access_owner_transfer(request: Request, body: M.OwnerTransfer):
        """Hand ownership to another active person. It takes effect with this commit."""
        who = request.state.identity
        owner_only(who, "transfers ownership")

        def work(c):
            owner = Access.load_owner(c, settings)
            record = Access.transfer(
                c,
                who.actor,
                views.roster(c),
                owner,
                body.person,
                expected_revision=body.expected_revision,
                previous_bot_admin=body.previous_owner_bot_admin,
                now=H.now(),
                settings=settings,
            )
            return {"owner": record["email"], "revision": record["revision"]}

        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.post("/api/v2/access/owner/email")
    def access_owner_email(request: Request, body: M.OwnerEmailChange):
        """The owner changes their own sign-in address in place: the same person, every copy of the address at once.
        Signed in as themselves only: not through an API token, BotOps or the Assistant. From this commit on, the old
        address no longer signs in (docs/people.md, Change the owner's sign-in email)."""
        who = request.state.identity
        owner_only(who, "changes the owner's sign-in email")
        if who.via_token or who.via:
            raise Problem("forbidden", "Change the owner's sign-in email from a signed-in browser, as yourself", 403)
        if body.email.strip().lower() != body.confirm_email.strip().lower():
            raise Problem("email", "The two addresses do not match", 422)

        def work(c):
            record = Access.change_owner_email(
                c,
                who.actor,
                views.roster(c),
                Access.load_owner(c, settings),
                body.email,
                expected_revision=body.expected_revision,
                now=H.now(),
                settings=settings,
            )
            return {"owner": record["email"], "revision": record["revision"], "person": H.actor_id(who.actor)}

        result = mutate(request, body, work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    account_routes.register(app, auth, mutate, onboarding, owner_only, recruiter, settings, store)

    org_routes.register(app, auth, settings, settings_admin, store)
