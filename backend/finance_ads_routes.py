"""Finance's ad spend over HTTP (API v2): the Finance card on a Google Ads or Meta Ads tool's page reads and saves the
import (backend/finance/ads.py) and picks the bot each ad account counts for. Finance admins only; everyone else is
refused. A save that switches the import on, or a run asked for, rings the import worker after the commit."""
from fastapi import Path, Query, Request
from pydantic import Field

from .finance import ad_import
from .finance import ads as finance_ads
from .finance import actions as finance
from .finance_routes import problem
from .models import Contract

DAY = r"^\d{4}-\d{2}-\d{2}$"


class FinanceAdImportRequest(Contract):
    """A tool's ad spend import: on or off, the first day it reads, the books line spend books as, and which stored
    key holds each value the platform needs ({slot: credential id}, null to clear one). Left out: kept as saved."""
    tool_id: str = Field(min_length=1, max_length=120)
    enabled: bool
    history_from: str | None = Field(None, pattern=DAY)
    account_id: str | None = Field(None, max_length=120)
    credentials: dict[str, str | None] | None = None


class FinanceAdRunRequest(Contract):
    """Import a tool's ad spend now instead of at the next 4-hourly run."""
    tool_id: str = Field(min_length=1, max_length=120)


class FinanceAdAccountPatch(Contract):
    """The bot an ad account's spend counts for; null when no bot."""
    bot_slug: str | None = Field(..., max_length=120)


def install(app, store, auth, mutate):
    def viewer(request):
        who = request.state.identity
        auth.domain(who)
        return who

    def guarded(fn):
        try:
            return fn()
        except finance.FinanceError as e:
            raise problem(e) from None

    @app.get("/api/v2/finance/ad-accounts")
    def finance_ad_accounts(request: Request, tool_id: str = Query(..., min_length=1, max_length=120)):
        """A Google Ads or Meta Ads tool's ad spend: the import's settings and last result, the keys it can use, and
        each ad account with the bot it counts for and its spend this month."""
        who = viewer(request)

        def read():
            with store.read() as c:
                return finance_ads.view(c, who.actor, tool_id)
        return guarded(read)

    @app.post("/api/v2/finance/ad-imports")
    def finance_ad_import_save(request: Request, body: FinanceAdImportRequest):
        """Save a tool's ad spend import. Switching it on asks for a run at once."""
        who = viewer(request)

        def work(c):
            return guarded(lambda: finance_ads.configure(
                c, who.actor, body.tool_id, enabled=body.enabled, history_from=body.history_from,
                account_id=body.account_id, credentials=body.credentials))
        saved = mutate(request, body, work)
        if body.enabled:
            ad_import.ring()
        return saved

    @app.post("/api/v2/finance/ad-imports/run")
    def finance_ad_import_run(request: Request, body: FinanceAdRunRequest):
        """Import a tool's ad spend now."""
        who = viewer(request)
        saved = mutate(request, body, lambda c: guarded(lambda: finance_ads.run_now(c, who.actor, body.tool_id)))
        ad_import.ring()
        return saved

    @app.patch("/api/v2/finance/ad-accounts/{ad_account_id}")
    def finance_ad_account_patch(request: Request, body: FinanceAdAccountPatch,
                                 ad_account_id: str = Path(..., min_length=1, max_length=120)):
        """Pick the bot an ad account's spend counts for (null: none)."""
        who = viewer(request)
        return mutate(request, body, lambda c: guarded(
            lambda: finance_ads.assign(c, who.actor, ad_account_id, body.bot_slug)))
