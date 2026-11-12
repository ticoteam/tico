"""Helpers that other tests share, moved out of test_bookkeeper.py so they import no test file."""

import shutil
from pathlib import Path
from backend import bookkeeper as BK
from backend.finance import actions as FA
from backend.store import H
from backend.tests.helpers.goal_manager import BASE
from backend.tests.helpers.onboarding import draft, machine, signed_in


TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "catalog" / "finance-owner"


DEFAULT_RIGHTS = {"finance.book", "finance.ap", "finance.ar", "finance.pay", "finance.cards", "finance.rules"}


def built(environment):
    api = environment(seed={}, cards=BASE)
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "finance-owner")
    machine(api)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    return api


def ensure(api):
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        return BK.ensure(c, api.app.state.onboarding, api.app.state.auth)


def check(api):
    with api.app.state.store.transaction() as c:
        return BK.check(c, api.app.state.auth, H.now())


def checks(api):
    with api.app.state.store.read() as c:
        return [
            dict(r) for r in c.execute("SELECT id,title,body,status,private FROM tasks WHERE owner_id=?", (BK.ACTOR,))
        ]
