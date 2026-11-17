"""Bot creation, onboarding and seat routes."""

from .store import H
from . import models as M
from fastapi import Request
from . import gateway_connection
from . import lifecycle
from . import recruiting
from . import views
from .api_contract import api_v2


def register(app, auth, mutate, onboarding, report_mutate, settings, settings_admin, store):
    @app.post("/api/v2/bots")
    def create_bot(request: Request, body: M.BotDefinitionCreate):
        who = request.state.identity

        def work(c):
            if who.role == "bot" and who.via == "botops":
                return settings_admin.create_report(c, who, body)  # a manager bot's report, asked of BotOps
            created = settings_admin.create_bot(c, who, body)
            if body.template:
                # Adding from the catalog after onboarding is the same handoff to BotOps.
                created.update(
                    onboarding.attach_template(
                        c, who, H.resolve_bot(c, created["slug"])["id"], body.template, body.instructions
                    )
                )
            return created

        return report_mutate(request, body, work)

    @app.post(
        "/api/v2/bots/{bot}/onboarded",
        **api_v2(
            "Bots",
            "markBotOnboarded",
            "Mark onboarding complete, by a person above the bot on the org chart (its manager, their managers or the owner; 403 for anyone else): its `stage` goes from onboarding to active (`onboarding_state` `needs_setup` to `onboarded`) and its automations start. The bot's own call is its \"I'm ready\" with a `summary` and leaves it onboarding. Repeating it changes nothing; 409 bot_limit for a member's bot over their limit",
            "BotOnboarded",
        ),
    )
    def bot_onboarded(request: Request, bot: str, body: M.OnboardingComplete):
        """Mark onboarding complete: a person above the bot on the org chart makes it Active (backend/lifecycle.py).
        The bot's own call (`tico bot ready`) is its "I'm ready", with `summary` saying what it learned, and leaves it
        Onboarding. Repeating either changes nothing more."""
        who = request.state.identity
        return mutate(
            request,
            body,
            lambda c: onboarding.onboarded(c, who, (H.resolve_bot(c, bot) or {}).get("id", bot), summary=body.summary),
        )

    @app.get(
        "/api/v2/seats",
        **api_v2(
            "Team chart",
            "listSeats",
            "The Recruiting seats: roles on the chart with a job description and reporting line, and no bot. A seat holds no work: a task for it is refused and its role's work stays with its manager",
            "SeatList",
        ),
    )
    def seat_list(request: Request):
        """The Recruiting seats: roles on the org chart with a job description and reporting line, and no bot yet."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            parents = views.seat_parents(c, settings, auth, who)
            return {"seats": [{**seat, "org_parent": parents.get(seat["id"], "")} for seat in lifecycle.seats(c)]}

    @app.get(
        "/api/v2/org-models",
        **api_v2(
            "Team chart",
            "listOrgModels",
            "Published Tico starter org charts used for re-org recommendations; contains no customer org data",
            "OrgModels",
        ),
    )
    def org_models(request: Request):
        """Published Tico starter org charts, the only chart models the Strategy Owner uses for re-org recommendations."""
        from . import org_models as published_models

        auth.domain(request.state.identity)
        return {"models": published_models.published()}

    @app.post(
        "/api/v2/seats",
        **api_v2(
            "Team chart",
            "createSeat",
            "Add a Recruiting seat: the owner anywhere, a person under themselves or someone below them",
            "Seat",
        ),
    )
    def seat_create(request: Request, body: M.SeatCreate):
        """Add a Recruiting seat: the owner anywhere, a person under themselves or someone below them."""
        who = request.state.identity
        auth.domain(who)
        return mutate(
            request,
            body,
            lambda c: lifecycle.create_seat(auth, c, who, body.title, body.description, body.reports_to, body.team),
        )

    @app.post(
        "/api/v2/seats/batch",
        **api_v2(
            "Team chart",
            "createSeats",
            "Add a group of Recruiting seats in one transaction; the owner anywhere, others only under themselves or below",
            "SeatBatch",
        ),
    )
    def seat_batch_create(request: Request, body: M.SeatBatchCreate):
        """Add one confirmed group of Recruiting seats; the owner may place them anywhere, others only in their chain."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: lifecycle.create_seats(auth, c, who, body.seats))

    def _recover_seat(seat_id):
        """A cloud agent request a dead process left in flight on this seat is recovered in its own transaction first, so
        the action below goes ahead and a refusal of it cannot roll the recovery back (backend/cloud_agents.py)."""
        from . import cloud_agents

        cloud_agents.recover(store, seat_id)

    @app.patch(
        "/api/v2/seats/{seat_id}",
        **api_v2(
            "Team chart",
            "updateSeat",
            "Change a seat's title, job description, reporting line or group (a person it reports up to, or the owner)",
            "Seat",
        ),
    )
    def seat_update(request: Request, seat_id: str, body: M.SeatUpdate):
        who = request.state.identity
        auth.domain(who)
        _recover_seat(seat_id)
        return mutate(
            request, body, lambda c: lifecycle.update_seat(auth, c, who, seat_id, body.model_dump(exclude_unset=True))
        )

    @app.post(
        "/api/v2/seats/draft",
        **api_v2(
            "Team chart",
            "draftSeat",
            "A job description for a job not posted yet, from its title: composed from the template catalog at once, or with `redraft` rewritten once by the model when the server has a key. Stores nothing",
            "SeatDrafted",
        ),
    )
    def seat_draft(request: Request, body: M.SeatDraft):
        """A job description for a job not posted yet, from its title: composed from the template catalog at once, or
        written by the model when no catalog card is the job, or with `redraft` rewritten once by the model. The model
        is Gemini when the server has a key, else the Tico AI Gateway with the company's gateway key
        (backend/recruiting.py). Stores nothing."""
        who = request.state.identity
        auth.domain(who)
        recruiting.may_draft(who)
        boss = ""
        if body.redraft and body.reports_to:
            with store.read() as c:
                boss = recruiting.boss_name(auth, c, who, body.reports_to)

        def gateway():
            # Read only when the model is asked, inside its time bound: a catalog draft never waits on the vault.
            with store.read() as c:
                return gateway_connection.target(c, app.state.vault, settings)[:2]

        return recruiting.draft(onboarding, body.title, body.team, boss, body.redraft, gateway=gateway)

    @app.get(
        "/api/v2/seats/{seat_id}",
        **api_v2(
            "Team chart",
            "getSeat",
            "One seat, with whose words its job description is and whether the caller may edit it",
            "SeatDetail",
        ),
    )
    def seat_show(request: Request, seat_id: str):
        """One Recruiting seat, with whether the caller may edit it (backend/recruiting.py)."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return recruiting.show(auth, c, who, seat_id)

    @app.post(
        "/api/v2/seats/{seat_id}/recruit",
        **api_v2(
            "Team chart",
            "recruitSeat",
            "Up to eight Tico templates for the seat at once, best fit first, each with one line, what it needs, how well it matches and a suggested model, and the models one may choose instead. Local: nothing leaves the server",
            "SeatCandidates",
        ),
    )
    def seat_recruit(request: Request, seat_id: str, body: M.Empty):
        """Recruit: up to eight Tico templates for the seat at once, best fit first, each with one line, what it needs,
        how well it matches and a suggested model. Local: nothing leaves this server."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return recruiting.candidates(auth, onboarding, c, who, seat_id)

    @app.post(
        "/api/v2/seats/{seat_id}/hire",
        **api_v2(
            "Team chart",
            "hireSeat",
            "Pick a candidate: the bot is created in Onboarding in the seat's place, then its repository",
            "SeatHired",
        ),
    )
    def seat_hire(request: Request, seat_id: str, body: M.SeatHire):
        """Pick a candidate: the bot is created in Onboarding in the seat's place with its reporting line, and only
        then its repository (by its computer for a starter, else BotOps)."""
        who = request.state.identity
        auth.domain(who)
        _recover_seat(seat_id)
        return mutate(request, body, lambda c: recruiting.hire(auth, onboarding, c, who, seat_id, body))

    @app.post(
        "/api/v2/seats/{seat_id}/close",
        **api_v2("Team chart", "closeSeat", "Take a seat off the chart; its row is kept", "SeatClosed"),
    )
    def seat_close(request: Request, seat_id: str, body: M.Empty):
        """Take a seat off the chart; its row stays for the record."""
        who = request.state.identity
        auth.domain(who)
        _recover_seat(seat_id)
        return mutate(request, body, lambda c: lifecycle.close_seat(auth, c, who, seat_id))
