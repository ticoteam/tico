"""Helpers that other tests share, moved out of test_company_page_editions.py so they import no test file."""

import pytest
from backend import kpis as K
from backend import updates
from backend.auth import Identity
from backend.company_page import disclosure as D
from backend.company_page import readers
from backend.company_page import release
from backend.company_page import stats as ST
from backend.store import H
from backend.tests.helpers.api import get, post
from backend.tests.helpers.org_rules import as_bot


START, END = "2026-10-01", "2026-10-31"


GOOD = {
    "title": "October in review",
    "sections": {
        "highlights": ["Revenue reached $120,000."],
        "lowlights": [],
        "numbers": ["Revenue: $120,000"],
        "asks": ["Intros to design partners, please."],
        "next": ["We keep shipping."],
    },
}


def plain_sections(*texts):
    return [{"key": "highlights", "items": [{"body": t} for t in texts]}]


class Page:
    """One enabled audience with Ops as its source, a writer that answers `answer`, and the other tracks stubbed."""

    def __init__(self, api, monkeypatch):
        self.api, self.store = api, api.app.state.store
        self.answer, self.engine = GOOD, True
        self.calls, self.rebuilds, self.notified = [], [], []
        self.refreshes, self.rebuilt_on = [], []
        monkeypatch.setattr(updates, "writer_for", lambda c, s, st: self.write if self.engine else None)
        at = H.now()
        with self.store.transaction() as c:
            c.execute("INSERT OR REPLACE INTO company_page_settings(id, transparency) VALUES('company','private')")
            c.execute(
                "INSERT INTO audiences(id,name,kind,due_rule,enabled,created_at,updated_at) "
                "VALUES('board','Board','board','{\"business_day\": 5}',1,?,?)",
                (at, at),
            )
            c.execute(
                "INSERT INTO audience_sources(id,audience_id,author,created_at) VALUES('s1','board','ops',?)", (at,)
            )
            # Revenue is a KPI the board may show: the real projection and facts read it.
            revenue = K.create(c, "human:ana", "human:ana", {"name": "Revenue", "unit": "$", "cadence": "monthly"})
            K.add_reading(c, "human:ana", revenue, 120000, period_start=START, period_end=END)
            D.set_rule(c, "human:ana", "board", "kpi", mode="show")
            ST.create_stat(c, "human:ana", "kpi", revenue["id"], data_class="kpi", label="Revenue", chart="number")
        real_rebuild = release.rebuild
        real_notify = readers.notify_edition

        def rebuild(store_or_c, why, at=None):
            self.rebuilds.append(why)
            self.rebuilt_on.append("store" if hasattr(store_or_c, "transaction") else "transaction")
            return real_rebuild(store_or_c, why, at)

        def notify(c, edition_id):
            self.notified.append(edition_id)
            return real_notify(c, edition_id)

        real_refresh = release.refresh

        def refresh(store, at):
            self.refreshes.append(at)
            return real_refresh(store, at)

        monkeypatch.setattr(release, "rebuild", rebuild)
        monkeypatch.setattr(release, "refresh", refresh)
        monkeypatch.setattr(readers, "notify_edition", notify)
        self.note("ops", "weekly", "2026-10-12", "We shipped the board page.")
        settings = self.store.settings.test_identities
        settings["ana-token"] = Identity("human:ana", "owner", "ana@acme.example", via_token=True)
        settings["ana-assistant"] = Identity("human:ana", "owner", "ana@acme.example", via="assistant", confirmed=True)
        as_bot(api, "ops", "ops-test")

    def write_sql(self, statement):
        with self.store.transaction() as c:
            c.execute(statement)

    def write(self, system, user):
        self.calls.append((system, user))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def note(self, bot, kind, day, body, meetings=0):
        ident = H.new_id()
        with self.store.transaction() as c:
            c.execute(
                "INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at,meetings) VALUES(?,?,?,?,?,?,?,?,?)",
                (ident, bot, kind, day, "Headline", body, H.now(), H.now(), meetings),
            )
        return ident

    def draft(self):
        return post(
            self.api, "editions", {"audience_id": "board", "author": "ops", "period_start": START, "period_end": END}
        )

    def ask(self, edition, token="ana-test"):
        return post(self.api, f"editions/{edition['id']}/approval", {}, token=token)

    def decide(self, aid, token="ana-test", expected=200, decision="approved"):
        return post(self.api, f"approvals/{aid}", {"decision": decision}, token=token, expected=expected)

    def approved(self):
        edition = self.ask(self.draft())
        self.decide(edition["approval"]["id"])
        return get(self.api, f"editions/{edition['id']}")

    def publish(self, edition, token="ana-test", expected=200):
        return post(
            self.api,
            f"editions/{edition['id']}/publish",
            {"approval_id": edition["approval"]["id"]},
            token=token,
            expected=expected,
        )

    def published(self):
        return self.publish(self.approved())

    def rows(self, query, args=()):
        with self.store.read() as c:
            return [dict(r) for r in c.execute(query, args)]


@pytest.fixture
def page(api, monkeypatch):
    return Page(api, monkeypatch)
