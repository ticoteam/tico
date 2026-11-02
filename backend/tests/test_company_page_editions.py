"""Company page editions (plan §1, §3, §4): what a draft reads, what holds one, who may approve, what publishing spends, and
the ways back. Acme is the fictional company: Ana owns it, Ben is an admin, Ops is a bot. The stats, release and readers
modules belong to other tracks and are stubbed by attribute, so these tests keep working once they land."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import assistant
from backend import batch
from backend import kpis as K
from backend import sleep_idle
from backend import updates
from backend.auth import Identity
from backend.company_page import disclosure as D
from backend.company_page import editions as E
from backend.company_page import readers
from backend.company_page import release
from backend.company_page import schedule as S
from backend.company_page import stats as ST
from backend.store import H, Problem
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.test_assistant import asked, assistant_turn
from backend.tests.test_org_rule_check import Judge
from backend.tests.test_org_rules import as_bot, org, rule

START, END = "2026-10-01", "2026-10-31"
FACTS = {"stats": [{"label": "Revenue", "display": "$120,000", "period": "October 2026"}], "company": "Acme"}
NO_FIGURES = {"title": "A quiet month", "sections": {
    "highlights": ["The team kept shipping."], "lowlights": [], "numbers": [], "asks": [], "next": ["We keep going."]}}
GOOD = {"title": "October in review", "sections": {
    "highlights": ["Revenue reached $120,000."], "lowlights": [], "numbers": ["Revenue: $120,000"],
    "asks": ["Intros to design partners, please."], "next": ["We keep shipping."]}}
SECRET_RULE = "Never name a customer."


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
            c.execute("INSERT INTO audiences(id,name,kind,due_rule,enabled,created_at,updated_at) "
                      "VALUES('board','Board','board','{\"business_day\": 5}',1,?,?)", (at, at))
            c.execute("INSERT INTO audience_sources(id,audience_id,author,created_at) VALUES('s1','board','ops',?)", (at,))
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

    def write(self, system, user):
        self.calls.append((system, user))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def note(self, bot, kind, day, body, meetings=0):
        ident = H.new_id()
        with self.store.transaction() as c:
            c.execute("INSERT INTO updates(id,bot,kind,day,headline,body,created,updated,meetings) VALUES(?,?,?,?,?,?,?,?,?)",
                      (ident, bot, kind, day, "Headline", body, H.now(), H.now(), meetings))
        return ident

    def draft(self):
        return post(self.api, "editions", {"audience_id": "board", "author": "ops", "period_start": START, "period_end": END})

    def ask(self, edition, token="ana-test"):
        return post(self.api, f"editions/{edition['id']}/approval", {}, token=token)

    def decide(self, aid, token="ana-test", expected=200, decision="approved"):
        return post(self.api, f"approvals/{aid}", {"decision": decision}, token=token, expected=expected)

    def approved(self):
        edition = self.ask(self.draft())
        self.decide(edition["approval"]["id"])
        return get(self.api, f"editions/{edition['id']}")

    def publish(self, edition, token="ana-test", expected=200):
        return post(self.api, f"editions/{edition['id']}/publish", {"approval_id": edition["approval"]["id"]},
                    token=token, expected=expected)

    def published(self):
        return self.publish(self.approved())

    def rows(self, query, args=()):
        with self.store.read() as c:
            return [dict(r) for r in c.execute(query, args)]


@pytest.fixture
def page(api, monkeypatch):
    return Page(api, monkeypatch)


# ----------------------------------------------------------------------------- drafting
def test_a_draft_reads_only_the_instructions_the_facts_and_the_chosen_updates(page):
    secret = page.note("ops", "daily", "2026-10-13", "Daily note that a weekly replaces.")
    page.note("ops", "weekly", "2026-10-19", "From the meeting with Globex about their contract.", meetings=1)
    page.note("ops", "weekly", "2026-09-28", "September is another period.")
    page.note("finance", "weekly", "2026-10-12", "Finance is not this audience's source.")
    edition = page.draft()
    assert len(page.calls) == 1
    system, user = page.calls[0]
    assert system == E.INSTRUCTIONS
    sent = json.loads(user)
    assert set(sent) == {"period", "facts", "updates"}
    assert [(x["label"], x["display"]) for x in sent["facts"]["stats"]] == [("Revenue", "$120,000")]
    assert [u["text"] for u in sent["updates"]] == ["We shipped the board page."]
    assert set(sent["updates"][0]) == {"from", "day", "kind", "text"}
    full = get(page.api, f"editions/{edition['id']}")
    assert [s["body"] for s in full["sources"]] == ["We shipped the board page."]
    assert len(full["sources"][0]["body_sha256"]) == 64
    # A meeting-flagged update is out even when it's all the source wrote.
    page.note("finance", "weekly", "2026-11-02", "Meeting only.", meetings=1)
    with page.store.read() as c:
        assert not E.has_material(c, "finance", "2026-11-01", "2026-11-30")
        assert [r["id"] for r in E.source_updates(c, "ops", "2026-10-13", "2026-10-13")] == [secret]


@pytest.mark.parametrize("answer", [
    {"title": "No sections"},
    {"title": "A", "sections": {"highlights": ["Only one key."]}},
    {"title": "A", "sections": {**GOOD["sections"], "extra": ["An unknown section."]}},
    {"title": "A", "sections": {**GOOD["sections"], "highlights": ["<script>alert(1)</script>"]}},
    {"title": "", "sections": GOOD["sections"]},
    {"title": "Nothing", "sections": {k: [] for k in E.SECTIONS}},
    {"title": "A", "sections": {**GOOD["sections"], "highlights": ["x" * (E.ITEM_CHARS + 1)]}},
    "not an object",
    RuntimeError("model down"),
])
def test_a_malformed_answer_or_failed_call_is_held_empty_and_can_be_drafted_again(page, answer):
    page.answer = answer
    edition = page.draft()
    assert edition["status"] == "held" and edition["sections"] == [] and page.rebuilds == []
    post(page.api, f"editions/{edition['id']}/approval", {}, expected=409)
    checks = get(page.api, f"editions/{edition['id']}")["checks"]
    assert next(c for c in checks if c["kind"] == "draft")["outcome"] == "failed"
    page.answer = GOOD
    again = page.draft()
    assert again["id"] == edition["id"] and again["status"] == "ready" and again["title"] == GOOD["title"]


def test_no_engine_holds_the_draft_and_one_period_makes_one_edition(page):
    page.engine = False
    edition = page.draft()
    assert edition["status"] == "held" and page.calls == []
    assert get(page.api, f"editions/{edition['id']}")["checks"][0]["detail"]["reason"] == "no_engine"
    page.engine = True
    assert page.draft()["status"] == "ready"
    assert page.draft()["id"] == edition["id"] and len(page.calls) == 1
    assert page.rows("SELECT count(*) AS n FROM editions")[0]["n"] == 1


def test_an_ask_keeps_its_id_through_an_edit_and_a_write_cannot_name_a_new_one(page):
    edition = page.draft()
    ask = next(s for s in edition["sections"] if s["key"] == "asks")["items"][0]
    assert ask["ask_id"]
    r = page.api.patch(f"/api/v2/editions/{edition['id']}", headers=headers(), json={"sections": [
        {"key": "asks", "items": [{"body": "Reworded ask.", "ask_id": ask["ask_id"]},
                                  {"body": "Another ask.", "ask_id": "chosen-by-caller"}]}]})
    assert r.status_code == 200, r.text
    items = next(s for s in r.json()["sections"] if s["key"] == "asks")["items"]
    assert items[0]["ask_id"] == ask["ask_id"] and items[1]["ask_id"] not in (None, "chosen-by-caller")


# ----------------------------------------------------------------------------- checks
def test_the_leak_check_flags_numbers_names_and_links_not_in_the_facts():
    def found(*texts):
        return E.leak_findings("October in review", plain_sections(*texts), FACTS, START, END, ["Board"])
    assert found("Revenue reached $120,000.", "The team shipped in October 2026.") == {}
    assert found("Revenue reached $130,000.") == {"numbers": ["130,000"], "names": [], "links": []}
    # The dates in the facts don't make their digits allowed: a day's "1" is not a million.
    assert found("Revenue hit $1m.")["numbers"] == ["1"]
    assert found("We signed Globex this month.")["names"] == ["Globex"]
    assert found("Globex renewed.")["names"] == ["Globex"]            # a name at a sentence's start is still a name
    assert found("Dana Reyes joined.")["names"] == ["Dana", "Reyes"]
    assert found("We added thirty seats.")["numbers"] == ["thirty"]
    assert found("See https://evil.example/x for more.")["links"] == ["https://evil.example/x"]
    assert found("Write to dana@evil.example please.")["links"] == ["dana@evil.example"]
    # Commas and trailing zeros are the same number, not a leak.
    facts = {"stats": [{"display": "1,200.50"}]}
    assert not E.leak_findings("Monthly update", plain_sections("We reached 1200.5 now."), facts, START, END)
    # A day of the period is a date after a month's name, and only there.
    assert found("We shipped on October 31.") == {}
    assert found("We added 31 seats.")["numbers"] == ["31"]
    assert found("Revenue hit $10m.")["numbers"] == ["10"]




def test_times_and_ids_in_the_facts_do_not_make_figures_allowed():
    """A clock reading or an id in the facts would allow its digits, so a figure would pass only at some hours."""
    facts = {"stats": [{"as_of": "2026-11-02T01:23:45.000Z", "id": "4b5cb1b2-fe2c-4de8-bdb2-d086f4c6d008",
                        "updated": "00:01:30", "display": "$120,000"}]}
    assert E.leak_findings("t", plain_sections("Revenue hit $1m."), facts, START, END)["numbers"] == ["1"]


def test_a_leak_holds_a_draft_and_a_clean_edit_by_a_person_releases_it(page):
    page.answer = {**GOOD, "sections": {**GOOD["sections"], "highlights": ["We signed Globex for $900k."]}}
    edition = page.draft()
    assert edition["status"] == "held"
    full = get(page.api, f"editions/{edition['id']}")
    leak = next(c for c in full["checks"] if c["kind"] == "leak")
    assert leak["outcome"] == "flagged" and "Globex" in leak["detail"]["names"] and "900" in leak["detail"]["numbers"]
    assert post(page.api, f"editions/{edition['id']}/approval", {}, expected=409)["error"]["code"] == "conflict"
    r = page.api.patch(f"/api/v2/editions/{edition['id']}", headers=headers(),
                       json={"sections": plain_sections("Revenue reached $120,000.")})
    assert r.status_code == 200 and r.json()["status"] == "ready"
    # A person's own write gets the same check: their edit can put it back on hold.
    r = page.api.patch(f"/api/v2/editions/{edition['id']}", headers=headers(),
                       json={"sections": plain_sections("Revenue reached $500k.")})
    assert r.status_code == 200 and r.json()["status"] == "held"


@pytest.mark.parametrize("trouble,reason", [("no_engine", "no_engine"), ("error", "error"), ("checks_off", "checks_off")])
def test_a_rule_nobody_could_read_holds_the_edition(page, monkeypatch, trouble, reason):
    from backend import org_rule_check as K
    org(page.api)
    rule(page.api, {"kind": "internal", "text": SECRET_RULE, "who": {"outside_editions": True}})
    judge = Judge(page.store, {SECRET_RULE: "globex"})
    page.api.app.state.judge = judge
    if trouble == "no_engine":
        page.api.app.state.judge = None
        monkeypatch.setattr(K.B, "server_engine", lambda *a, **k: None)
    elif trouble == "error":
        judge.fail = RuntimeError("judge down")
    else:
        monkeypatch.setattr(K, "on", lambda c: False)
    edition = page.draft()
    assert edition["status"] == "held"
    assert reason in edition["last_check"]["reasons"] or (trouble == "error" and edition["last_check"]["reasons"])


def test_a_broken_outside_rule_holds_and_a_bot_scoped_rule_does_not_read_editions(page):
    org(page.api)
    judge = Judge(page.store, {SECRET_RULE: "shipping"})
    page.api.app.state.judge = judge
    rule(page.api, {"kind": "internal", "text": "A rule about something else.", "who": {"bots": ["ops"]}})
    assert page.draft()["status"] == "ready" and judge.calls == []
    rule(page.api, {"kind": "internal", "text": SECRET_RULE, "who": {"outside_editions": True}})
    edition = post(page.api, f"editions/{page.draft()['id']}/check", {})
    assert edition["status"] == "held" and "broken" in edition["last_check"]["reasons"] and judge.calls
    # It reaches only editions: Ops's own write isn't checked against it, and only an owner or admin can set it.
    rule(page.api, {"kind": "internal", "text": "Another.", "who": {"outside_editions": True}}, token="cara-test", expected=403)
    with page.store.read() as c:
        from backend import org_rules
        assert SECRET_RULE not in [r["text"] for r in org_rules.matching(c, "bot:ops")]


def test_an_override_needs_a_person_and_a_note_and_covers_only_that_text(page):
    page.answer = {**GOOD, "sections": {**GOOD["sections"], "highlights": ["We reached 900 seats."]}}
    edition = page.draft()
    path = f"editions/{edition['id']}/override"
    post(page.api, path, {"note": ""}, expected=422)
    for token in ("ops-test", "ana-token", "ana-assistant"):
        post(page.api, path, {"note": "Fine."}, token=token, expected=403)
    done = post(page.api, path, {"note": "Public figure, already announced."})
    assert done["status"] == "ready" and done["override_note"] == "Public figure, already announced."
    assert page.ask(done)["status"] == "awaiting_approval"
    r = page.api.patch(f"/api/v2/editions/{edition['id']}", headers=headers(),
                       json={"sections": plain_sections("We reached 901 seats.")})
    assert r.json()["status"] == "held"               # the note stays on the record, but it held for the old words
    assert r.json()["override_note"] == "Public figure, already announced."


# ----------------------------------------------------------------------------- approval
def test_the_approval_carries_the_whole_text_and_goes_to_the_named_approver(page):
    with page.store.transaction() as c:
        c.execute("UPDATE audiences SET approver='human:ben' WHERE id='board'")
    edition = page.ask(page.draft())
    assert edition["status"] == "awaiting_approval" and edition["approval"]["to"] == "human:ben"
    found = get(page.api, f"approvals/{edition['approval']['id']}")
    payload = found["payload"]
    assert payload["url"] == f"tico:editions/{edition['id']}/1" and found["kind"] == "publish"
    assert "Revenue reached $120,000." in payload["text"] and "Intros to design partners, please." in payload["text"]
    # Asking twice is one ask. Someone who left is replaced by the owner.
    assert page.ask(edition)["approval"]["id"] == edition["approval"]["id"]
    with page.store.transaction() as c:
        c.execute("UPDATE audiences SET approver='human:gone' WHERE id='board'")
    moved = page.ask(edition)
    assert moved["approval"]["to"] == "human:ana" and moved["approval"]["id"] != edition["approval"]["id"]
    assert get(page.api, f"approvals/{edition['approval']['id']}")["decision"] == "withdrawn"


def test_only_a_person_at_their_own_click_decides_an_edition(page):
    edition = page.ask(page.draft())
    aid = edition["approval"]["id"]
    for token in ("ana-token", "ana-assistant", "ops-test", "ben-test"):      # the last is the wrong person
        page.decide(aid, token, expected=403)
    with page.store.transaction() as c:
        assert H.approval(c, aid)["decision"] is None
    # Publishing with no yes on it, by anyone.
    assert page.publish(edition, expected=409)["error"]["code"] == "approval"
    assert page.decide(aid)["decision"] == "approved"
    # And nobody but Ana's own click may publish on it either.
    for token in ("ana-token", "ana-assistant", "ops-test"):
        page.publish(edition, token, expected=403)
    assert page.rebuilds == []
    assert page.publish(edition)["status"] == "published"


def test_the_batch_decide_path_takes_only_the_persons_own_click(page):
    aid = page.ask(page.draft())["approval"]["id"]
    item, say_yes = {"key": f"approval:{aid}", "title": "Board edition"}, {"decision": "approve"}
    identities, auth = page.store.settings.test_identities, page.api.app.state.auth
    for token in ("ana-token", "ana-assistant"):
        with pytest.raises(Problem):
            with page.store.transaction() as c:
                batch._apply_decide(c, auth, identities[token], item, say_yes, [])
    with page.store.read() as c:
        assert H.approval(c, aid)["decision"] is None
    with page.store.transaction() as c:
        batch._apply_decide(c, auth, identities["ana-test"], item, say_yes, [])
    with page.store.read() as c:
        assert H.approval(c, aid)["decision"] == "approved"


def test_the_assistant_never_decides_one_even_with_the_setting_on_and_the_person_saying_yes(page, api):
    with page.store.transaction() as c:
        c.execute("UPDATE audiences SET approver='human:ben' WHERE id='board'")
    aid = page.ask(page.draft())["approval"]["id"]
    from backend.tests.test_api import as_member
    as_member(api, "ben@acme.example")
    assert post(api, "assistant/settings", {"decides_approvals": True}, token="ben-test")["active"] is True
    _, attempt = assistant_turn(api, "ben-test", "Approve the board edition")
    token = attempt["token"]
    refused = post(api, "assistant/read-back", {"approvals": [aid]}, token=token, expected=403)
    assert refused["error"]["code"] == "approve_own_click"
    asked(api, f"approvals/{aid}", {"decision": "approved"}, token, "turn", expected=403)
    # Nor on a card the person would click: the click on a card is still the Assistant acting.
    steps = [{"path": f"/api/v2/approvals/{aid}", "body": {"decision": "approved"}}]
    card = post(api, "assistant/actions", {"summary": "Approve it", "path": assistant.BATCH_PATH, "body": {"actions": steps}},
                token=token, expected=403)
    assert card["error"]["code"] == "approve_own_click"
    assert get(api, f"approvals/{aid}", "ben-test")["decision"] is None
    # His own click still works, and the setting never got in the way of it.
    assert page.decide(aid, "ben-test")["decision"] == "approved"


def test_a_bot_cannot_ask_for_or_spend_an_edition_approval_on_its_own(page):
    edition = page.draft()
    url = f"tico:editions/{edition['id']}/1"
    for token in ("ops-test", "ana-test"):
        # Case and spaces don't get a bot past it: what an edition approves is worded only here.
        for spelled in (url, url.upper(), " " + url):
            post(page.api, "approvals", {"kind": "publish", "payload": {"url": spelled, "content_sha256": "a" * 64}},
                 token=token, expected=403)
    asked_for = page.ask(edition)
    page.decide(asked_for["approval"]["id"])
    for token in ("ops-test", "ana-test"):
        post(page.api, f"approvals/{asked_for['approval']['id']}/consume", {"payload_hash": "a" * 64}, token=token, expected=403)
    assert page.publish(get(page.api, f"editions/{edition['id']}"))["status"] == "published"


def test_no_route_serves_a_bot_a_token_only_acts_where_it_may(page):
    edition = page.draft()
    path = f"editions/{edition['id']}"
    get(page.api, "editions", "ops-test", expected=403)
    get(page.api, path, "ops-test", expected=403)
    get(page.api, "editions", "cara-test", expected=403)
    for sub, body in (("check", {}), ("approval", {}), ("publish", {}), ("retract", {"note": "x"}),
                      ("correct", {"note": "x", "sections": plain_sections("x")}), ("override", {"note": "x"})):
        post(page.api, f"{path}/{sub}", body, token="ops-test", expected=403)
        post(page.api, f"{path}/{sub}", body, token="cara-test", expected=403)
    assert page.api.patch(f"/api/v2/{path}", headers=headers("ops-test"),
                          json={"sections": plain_sections("x")}).status_code == 403
    post(page.api, "editions", {"audience_id": "board"}, token="ops-test", expected=403)
    post(page.api, "audiences/board/stats/approval", {}, token="ops-test", expected=403)
    # The Assistant may draft and edit for a person; asking or taking down is the person's.
    post(page.api, f"{path}/approval", {}, token="ana-assistant", expected=403)
    post(page.api, f"{path}/retract", {"note": "x"}, token="ana-assistant", expected=403)
    assert page.api.patch(f"/api/v2/{path}", headers=headers("ana-assistant"),
                          json={"sections": plain_sections("Revenue reached $120,000.")}).status_code == 200


def test_an_edit_after_approval_makes_publish_refuse(page):
    edition = page.approved()
    r = page.api.patch(f"/api/v2/editions/{edition['id']}", headers=headers(),
                       json={"sections": plain_sections("Revenue reached $120,000, reworded.")})
    assert r.status_code == 200 and r.json()["status"] == "ready" and r.json()["approval"] is None
    assert page.publish(edition, expected=409)["error"]["code"] == "conflict"
    assert page.rebuilds == [] and page.notified == []
    # A write that slips past the routes meets the same refusal: the payload no longer matches.
    second = page.ask(r.json())
    page.decide(second["approval"]["id"])
    with page.store.transaction() as c:
        c.execute("UPDATE editions SET title='Something else' WHERE id=?", (edition["id"],))
    assert page.publish(second, expected=409)["error"]["code"] == "approval_stale"
    with page.store.read() as c:
        assert H.approval(c, second["approval"]["id"])["consumed_at"] is None


def test_a_rule_or_disclosure_change_after_approval_refuses_publish_until_asked_again(page):
    org(page.api)
    page.api.app.state.judge = Judge(page.store, {SECRET_RULE: "nothing matches this"})
    edition = page.approved()
    rule(page.api, {"kind": "internal", "text": SECRET_RULE, "who": {"outside_editions": True}})
    assert page.publish(edition, expected=409)["error"]["code"] == "approval_stale"
    current = get(page.api, f"editions/{edition['id']}")
    assert current["approval"]["state"] == "stale"
    asked_again = page.ask(current)
    assert asked_again["approval"]["id"] != edition["approval"]["id"]
    page.decide(asked_again["approval"]["id"])
    with page.store.transaction() as c:
        c.execute("INSERT INTO disclosure_revisions(id,audience_id,created_by,created_at) VALUES('r2','board','human:ana',?)",
                  (H.now(),))
    assert page.publish(get(page.api, f"editions/{edition['id']}"), expected=409)["error"]["code"] == "approval_stale"
    assert page.rebuilds == []


def test_an_approval_spends_once_and_a_lapsed_one_shows_and_can_be_asked_again(page):
    from backend import approval_expiry
    edition = page.ask(page.draft())
    with page.store.transaction() as c:
        c.execute("UPDATE approvals SET created=?, ready_since=? WHERE id=?",
                  (H.shift(H.now(), days=-40), H.shift(H.now(), days=-40), edition["approval"]["id"]))
        approval_expiry.sweep(c)
    lapsed = get(page.api, f"editions/{edition['id']}")
    assert lapsed["status"] == "awaiting_approval" and lapsed["approval"]["state"] == "lapsed"
    assert page.publish(lapsed, expected=409)["error"]["code"] == "approval"
    again = page.ask(lapsed)
    assert again["approval"]["state"] == "pending"
    page.decide(again["approval"]["id"])
    published = page.publish(get(page.api, f"editions/{edition['id']}"))
    assert published["status"] == "published" and published["approved_by"] == "human:ana"
    assert page.rebuilds == ["edition published"] and page.notified == [edition["id"]]
    assert page.publish(again, expected=409)["error"]["code"] == "conflict"
    with page.store.read() as c:
        assert H.approval(c, again["approval"]["id"])["consumed_at"]


def test_a_declined_edition_stays_unpublished_and_can_be_asked_again(page):
    edition = page.ask(page.draft())
    page.decide(edition["approval"]["id"], decision="declined")
    assert page.publish(edition, expected=409)["error"]["code"] == "approval"
    assert get(page.api, f"editions/{edition['id']}")["approval"]["state"] == "declined"
    assert page.ask(edition)["approval"]["state"] == "pending"


def test_publishing_while_closed_rebuilds_but_notifies_nobody_and_a_failed_notice_keeps_it_up(page, monkeypatch):
    edition = page.approved()
    with page.store.transaction() as c:
        c.execute("UPDATE company_page_settings SET transparency='closed'")
    assert page.publish(edition)["status"] == "published" and page.notified == []

    def boom(c, e):
        raise RuntimeError("mail down")
    with page.store.transaction() as c:
        c.execute("UPDATE company_page_settings SET transparency='private'")
    page.note("ops", "weekly", "2026-09-14", "September.")
    page.answer = NO_FIGURES         # September's facts have no revenue, so a figure would be a leak
    monkeypatch.setattr(readers, "notify_edition", boom)
    second = post(page.api, "editions", {"audience_id": "board", "author": "ops", "period_start": "2026-09-01",
                                         "period_end": "2026-09-30"})
    second = page.ask(second)
    page.decide(second["approval"]["id"])
    assert page.publish(second)["status"] == "published"


@pytest.mark.parametrize("failure", ["raises", "reports"])
def test_a_failed_rebuild_after_publishing_is_told_and_the_publication_stands(page, monkeypatch, failure):
    edition = page.approved()

    def boom(store_or_c, why):
        if failure == "raises":
            raise RuntimeError("disk full")
        return {"id": "release-2", "empty": True, "failed": True}
    monkeypatch.setattr(release, "rebuild", boom)
    done = page.publish(edition)
    assert done["status"] == "published" and done["release_failed"] is True
    with page.store.read() as c:
        assert H.approval(c, edition["approval"]["id"])["consumed_at"]


def test_publishing_rebuilds_after_the_commit_and_retracting_inside_the_same_step(page):
    edition = page.published()
    assert edition["release_failed"] is False and page.rebuilt_on == ["store"]
    post(page.api, f"editions/{edition['id']}/retract", {"note": "Wrong."})
    assert page.rebuilt_on == ["store", "transaction"]


def test_a_retract_whose_rebuild_cannot_swap_changes_nothing(page, monkeypatch):
    edition = page.published()

    def boom(store_or_c, why):
        raise RuntimeError("swap failed")
    monkeypatch.setattr(release, "rebuild", boom)
    with pytest.raises(RuntimeError):
        page.api.post(f"/api/v2/editions/{edition['id']}/retract", headers=headers(), json={"note": "Wrong."})
    assert page.rows("SELECT status FROM editions WHERE id=?", (edition["id"],)) == [{"status": "published"}]


def test_the_owners_view_names_each_sources_author_and_the_decision_note(page):
    with page.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Riley' WHERE slug='ops'")
    edition = page.ask(page.draft())
    aid = edition["approval"]["id"]
    assert post(page.api, f"approvals/{aid}", {"decision": "declined", "note": "Soften the second line."})["decision"] == "declined"
    full = get(page.api, f"editions/{edition['id']}")
    assert full["approval"]["note"] == "Soften the second line."
    assert [s["author_name"] for s in full["sources"]] == ["Riley"]
    # The update can be gone: the edition's source still names it.
    with page.store.transaction() as c:
        c.execute("DELETE FROM updates")
    assert [s["author_name"] for s in get(page.api, f"editions/{edition['id']}")["sources"]] == ["Riley"]
    board = post(page.api, "audiences/board/stats/approval", {})["approval"]
    post(page.api, f"approvals/{board}", {"decision": "declined", "note": "Not revenue yet."})
    assert get(page.api, "audiences/board/stats/approval")["approval"]["note"] == "Not revenue yet."


# ----------------------------------------------------------------------------- the ways back
def test_retract_takes_it_down_in_the_same_step_and_needs_a_person_and_a_note(page):
    edition = page.published()
    path = f"editions/{edition['id']}/retract"
    post(page.api, path, {"note": " "}, expected=422)
    post(page.api, path, {"note": "Wrong."}, token="ops-test", expected=403)
    post(page.api, path, {"note": "Wrong."}, token="ana-assistant", expected=403)
    done = post(page.api, path, {"note": "Wrong number."})
    assert done["status"] == "retracted" and done["retracted_by"] == "human:ana" and done["retraction_note"] == "Wrong number."
    assert page.rebuilds[-1] == "edition retracted"
    with page.store.read() as c:
        assert E.stream(c, ["board"]) == []
    post(page.api, path, {"note": "Again."}, expected=409)


def test_retract_discards_an_unpublished_edition_and_kills_its_approval(page):
    edition = page.ask(page.draft())
    aid = edition["approval"]["id"]
    done = post(page.api, f"editions/{edition['id']}/retract", {"note": "Not this one."})
    assert done["status"] == "retracted" and page.rebuilds == []
    assert get(page.api, f"approvals/{aid}")["decision"] == "withdrawn"
    assert page.publish(edition, expected=409)["error"]["code"] == "conflict"


def test_a_correction_is_a_new_version_through_the_same_steps_and_replaces_the_old_only_when_published(page):
    old = page.published()
    path = f"editions/{old['id']}/correct"
    body = {"note": "Fixed the figure.", "title": "October, corrected", "sections": plain_sections("Revenue reached $120,000.")}
    post(page.api, path, {**body, "note": ""}, expected=422)
    new = post(page.api, path, body)
    assert new["version"] == 2 and new["supersedes_id"] == old["id"] and new["status"] == "ready"
    assert new["correction_note"] == "Fixed the figure." and [s["update_id"] for s in new["sources"]]
    post(page.api, path, body, expected=409)                  # one open correction at a time
    with page.store.read() as c:
        assert [(u["title"], u["version"]) for u in E.stream(c, ["board"])] == [(old["title"], 1)]
    asked_for = page.ask(new)
    page.decide(asked_for["approval"]["id"])
    done = page.publish(asked_for)
    assert done["status"] == "published" and get(page.api, f"editions/{old['id']}")["status"] == "superseded"
    assert page.rebuilds[-1] == "edition corrected"
    with page.store.read() as c:
        shown = E.stream(c, ["board"])
    assert [(u["version"], u["correction_note"]) for u in shown] == [(2, "Fixed the figure.")]
    # A correction that fails its checks stays held, and the published one stays up.
    held = post(page.api, f"editions/{done['id']}/correct", {**body, "sections": plain_sections("Revenue hit $1m.")})
    assert held["status"] == "held"
    post(page.api, f"editions/{old['id']}/correct", body, expected=409)  # a replaced version can't be corrected
    # Taking the new one down doesn't bring the old one back.
    post(page.api, f"editions/{done['id']}/retract", {"note": "All wrong."})
    with page.store.read() as c:
        assert E.stream(c, ["board"]) == []
    assert get(page.api, f"editions/{old['id']}")["status"] == "superseded"


def test_a_correction_note_with_a_figure_is_held_like_the_text(page):
    """The note is published beside the text, so the leak check reads it too."""
    old = page.published()
    body = {"title": "October, corrected", "sections": plain_sections("Revenue reached $120,000.")}
    flagged = post(page.api, f"editions/{old['id']}/correct", {**body, "note": "Revenue was really $1m."})
    assert flagged["status"] == "held"
    assert next(c for c in flagged["checks"] if c["kind"] == "leak")["outcome"] == "flagged"


def test_a_changed_correction_note_makes_the_approval_stale(page):
    """The approval covers the note as well as the text: a note changed after the yes makes the publish refuse."""
    old = page.published()
    body = {"title": "October, corrected", "sections": plain_sections("Revenue reached $120,000."), "note": "Fixed it."}
    new = post(page.api, f"editions/{old['id']}/correct", body)
    assert new["status"] == "ready"
    asked = page.ask(new)
    page.decide(asked["approval"]["id"])
    with page.store.transaction() as c:
        c.execute("UPDATE editions SET correction_note='Fixed a different figure.' WHERE id=?", (new["id"],))
    assert page.publish(asked, expected=409)["error"]["code"] == "approval_stale"


def test_a_retracted_edition_comes_back_only_through_a_correction_and_a_new_approval(page):
    old = page.published()
    post(page.api, f"editions/{old['id']}/retract", {"note": "Hold on."})
    body = {"note": "Back with the right figure.", "sections": plain_sections("Revenue reached $120,000.")}
    new = page.ask(post(page.api, f"editions/{old['id']}/correct", body))
    page.decide(new["approval"]["id"])
    assert page.publish(new)["status"] == "published"
    assert get(page.api, f"editions/{old['id']}")["status"] == "retracted"
    with page.store.read() as c:
        assert [u["version"] for u in E.stream(c, ["board"])] == [2]


def test_a_published_edition_is_not_edited_in_place(page):
    edition = page.published()
    r = page.api.patch(f"/api/v2/editions/{edition['id']}", headers=headers(), json={"sections": plain_sections("Sneak.")})
    assert r.status_code == 409


# ----------------------------------------------------------------------------- the stream
def test_the_stream_shows_published_only_newest_first_paged_with_a_display_name_never_a_slug(page):
    with page.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Riley' WHERE slug='ops'")
    first = page.published()
    page.note("ops", "weekly", "2026-09-14", "September.")
    page.answer = NO_FIGURES
    draft = post(page.api, "editions", {"audience_id": "board", "author": "ops", "period_start": "2026-09-01",
                                        "period_end": "2026-09-30"})
    with page.store.read() as c:
        shown = E.stream(c, ["board"])
    assert len(shown) == 1
    update = shown[0]
    assert set(update) == {"anchor", "title", "author", "period", "published_at", "version", "correction_note", "sections"}
    assert update["author"] == {"name": "Riley", "kind": "bot"} and update["period"] == "October 2026"
    assert "ops" not in json.dumps(update["author"]).lower() and first["id"] not in json.dumps(update)
    assert {i for s in update["sections"] for i in s} == {"key", "items"} and all(s["items"] for s in update["sections"])
    asked_for = page.ask(draft)
    page.decide(asked_for["approval"]["id"])
    page.publish(asked_for)
    with page.store.read() as c:
        both = E.stream(c, ["board"])
        assert [u["period"] for u in both] == ["2026-09-01 to 2026-09-30", "October 2026"] or \
               [u["period"] for u in both] == ["September 2026", "October 2026"]
        assert E.stream(c, ["board"], limit=1) == both[:1]
        assert E.stream(c, ["board"], before=both[0]["published_at"]) == both[1:]
        assert E.stream(c, ["someone-else"]) == [] and E.stream(c, []) == []


def test_the_owners_view_has_sources_checks_and_seen_by_and_the_list_is_paged(page):
    edition = page.published()
    with page.store.transaction() as c:
        c.execute("INSERT INTO readers(id,email,added_by,created_at) VALUES('r1','reader@acme.example','human:ana',?)", (H.now(),))
        c.execute("INSERT INTO edition_views(id,edition_id,reader_id,viewed_at) VALUES('v1',?,'r1',?)", (edition["id"], H.now()))
    full = get(page.api, f"editions/{edition['id']}")
    assert full["sources"] and {c["kind"] for c in full["checks"]} >= {"draft", "leak", "rules", "summary"}
    assert full["seen_by"][0]["email"] == "reader@acme.example" and full["seen_count"] == 1
    listed = get(page.api, "editions?audience=board&limit=1")
    assert [e["id"] for e in listed["editions"]] == [edition["id"]] and listed["next_before"] is None
    assert get(page.api, "editions?status=held")["editions"] == []


# ----------------------------------------------------------------------------- stats board
def test_a_stats_board_approval_is_recorded_only_once_decided_and_only_for_the_board_it_describes(page):
    path = "audiences/board/stats/approval"
    post(page.api, path, {}, token="ops-test", expected=403)
    post(page.api, path, {}, token="ana-assistant", expected=403)
    asked_for = post(page.api, path, {})
    assert asked_for["state"] == "pending" and post(page.api, path, {})["approval"] == asked_for["approval"]
    aid = asked_for["approval"]
    payload = get(page.api, f"approvals/{aid}")["payload"]
    with page.store.read() as c:
        config = ST.board_config_hash(c, "board")
    assert payload["url"] == f"tico:stat-board/board/{config}" and "Revenue: $120,000" in payload["text"]
    for token in ("ana-token", "ana-assistant", "ops-test", "ben-test"):
        page.decide(aid, token, expected=403)
    assert page.rows("SELECT * FROM stat_board_approvals") == []
    page.decide(aid)
    rows = page.rows("SELECT * FROM stat_board_approvals")
    assert [(r["audience_id"], r["config_hash"], r["approved_by"]) for r in rows] == [("board", config, "human:ana")]
    assert get(page.api, f"approvals/{aid}")["consumed_at"]
    assert get(page.api, path)["approval"]["state"] == "spent"

    # A changed rule changes the board's hash: the yes given to the old board is not recorded for the new one.
    def change_board(mode):
        with page.store.transaction() as c:
            D.set_rule(c, "human:ana", "board", "kpi", mode=mode)
    change_board("rounded")
    second = post(page.api, path, {})["approval"]
    change_board("range")
    page.decide(second)
    # A tightening carries the standing approval forward (carry_board_approval), so rows for the first approval are
    # expected; none may be for the ask made under the old board.
    assert not [r for r in page.rows("SELECT * FROM stat_board_approvals") if r["approval_id"] == second]
    assert get(page.api, path)["approval"]["state"] == "stale"
    # An ask for a board that has since changed leaves the queue when the new one is asked for.
    third = post(page.api, path, {})["approval"]
    change_board("hidden")
    fourth = post(page.api, path, {})["approval"]
    assert get(page.api, f"approvals/{third}")["decision"] == "withdrawn" and fourth != third


def test_a_declined_board_writes_nothing(page):
    aid = post(page.api, "audiences/board/stats/approval", {})["approval"]
    page.decide(aid, decision="declined")
    assert page.rows("SELECT * FROM stat_board_approvals") == []


# ----------------------------------------------------------------------------- schedule
ZONE = "America/Los_Angeles"


def local(*parts):
    from zoneinfo import ZoneInfo
    return datetime(*parts, tzinfo=ZoneInfo(ZONE))


BOARD, INVESTORS = {"due_rule": {"business_day": 5}}, {"due_rule": '{"business_day": 10}'}


@pytest.mark.parametrize("audience,after,expected", [
    # The 5th business day of the following month: Nov 1 2026 is a Sunday.
    (BOARD, local(2026, 11, 1), local(2026, 11, 6)),
    # The 10th, counting the same way.
    (INVESTORS, local(2026, 11, 1), local(2026, 11, 13)),
    # Starting on a Saturday: Aug 1 2026.
    (BOARD, local(2026, 7, 31), local(2026, 8, 7)),
    # Across a year: Jan 2027 starts on a Friday.
    (BOARD, local(2026, 12, 15), local(2027, 1, 7)),
    # After the month's due moment, the next month's. On the moment itself is not after it.
    (BOARD, local(2026, 11, 6), local(2026, 12, 7)),
    # N days after a period ends: Oct 31 + 30.
    ({"due_rule": '{"days_after": 30}'}, local(2026, 11, 1), local(2026, 11, 30)),
    # A long lag crosses a month: Jan 31 2026 + 45, then Feb 28 + 45.
    ({"due_rule": '{"days_after": 45}'}, local(2026, 3, 1), local(2026, 3, 17)),
    ({"due_rule": '{"days_after": 45}'}, local(2026, 3, 17), local(2026, 4, 14)),
    # What audiences.get's view says is the same rule.
    ({"due_rule": {"kind": "business_day", "n": 2}}, local(2026, 11, 1), local(2026, 11, 3)),
    ({"due_rule": {"kind": "days_after_period_end", "n": 3}}, local(2026, 11, 1), local(2026, 11, 3)),
    # A quarter's edition: Q3 ends Sep 30 2026, Q4 ends Dec 31.
    ({"cadence": "quarterly", "due_rule": {"business_day": 5}}, local(2026, 9, 1), local(2026, 10, 7)),
    ({"cadence": "quarterly", "due_rule": {"business_day": 5}}, local(2026, 11, 1), local(2027, 1, 7)),
    ({"cadence": "quarterly", "due_rule": {"days_after": 30}}, local(2026, 9, 1), local(2026, 10, 30)),
])
def test_next_due(audience, after, expected):
    assert S.next_due(audience, after, ZONE) == expected


def test_next_due_is_always_after():
    moment = local(2026, 1, 1)
    for _ in range(40):
        following = S.next_due({"due_rule": {"days_after": 90}}, moment, ZONE)
        assert following > moment
        moment = following


def test_a_quarter_is_the_period_it_drafts():
    found = S.latest_due({"cadence": "quarterly", "due_rule": {"business_day": 5}}, local(2026, 11, 1), ZONE)
    assert found[1:] == ("2026-07-01", "2026-09-30")


@pytest.mark.parametrize("audience,why", [
    ({}, "due_rule"),
    ({"due_rule": "{}"}, "due_rule"),
    ({"due_rule": "not json"}, "due_rule"),
    ({"due_rule": '{"weekday": 5}'}, "due_rule"),
    ({"due_rule": '{"business_day": 0}'}, "due_rule"),
    ({"due_rule": '{"business_day": 24}'}, "due_rule"),
    ({"due_rule": '{"days_after": 366}'}, "due_rule"),
    ({"due_rule": '{"days_after": true}'}, "due_rule"),
    ({"due_rule": '{"business_day": 5, "days_after": 3}'}, "due_rule"),
    ({"due_rule": {"kind": "fortnight", "n": 2}}, "due_rule"),
    ({"due_rule": {"kind": ["business_day"], "n": 2}}, "due_rule"),
    ({"cadence": "weekly", "due_rule": {"business_day": 5}}, "cadence"),
])
def test_a_rule_it_cannot_read_stops_the_schedule_rather_than_guess(audience, why):
    audience = {**audience, "kind": "board", "lag_days": 30}
    assert S.next_due(audience, local(2026, 11, 1), ZONE) is None
    assert S.latest_due(audience, local(2026, 11, 20), ZONE) is None
    assert S.problem(audience) == why


def test_an_unreadable_rule_shows_as_a_problem_and_drafts_nothing(page):
    with page.store.transaction() as c:
        c.execute("""UPDATE audiences SET due_rule='{"kind":"fortnight","n":2}'""")
    at = local(2026, 11, 20)
    assert S.tick(page.store, at) == [] and page.calls == []
    with page.store.read() as c:
        assert S.next_wake(c, at) is None
    assert get(page.api, "editions")["schedule_problems"] == [{"audience_id": "board", "name": "Board", "problem": "due_rule"}]
    # A disabled audience isn't scheduled anyway, so it isn't a problem.
    with page.store.transaction() as c:
        c.execute("UPDATE audiences SET enabled=0")
    assert get(page.api, "editions")["schedule_problems"] == []


def test_what_the_audiences_module_writes_is_what_the_schedule_reads(page):
    """From audiences.py's own output, both the row and its view, through next_due."""
    audiences = pytest.importorskip("backend.company_page.audiences")
    after = local(2026, 11, 1)
    with page.store.transaction() as c:
        audiences.seed(c)
        made = audiences.create(c, "human:ana", "Advisors", due_rule={"kind": "days_after_period_end", "n": 3})
        changed = audiences.create(c, "human:ana", "Lenders")
        changed = audiences.update(c, "human:ana", changed["id"], due_rule={"kind": "business_day", "n": 2})
    with page.store.read() as c:
        expected = {"investors": local(2026, 11, 13), "public": local(2026, 11, 30), made["id"]: local(2026, 11, 3),
                    changed["id"]: local(2026, 11, 3)}
        for ident, due in expected.items():
            row = dict(c.execute("SELECT * FROM audiences WHERE id=?", (ident,)).fetchone())
            assert S.next_due(row, after, ZONE) == due, ident
            assert S.next_due(audiences.get(c, ident), after, ZONE) == due, ident


def test_the_stats_refresh_once_a_day_and_never_while_closed(page, monkeypatch):
    at = local(2026, 11, 20, 9)
    S.tick(page.store, at)
    S.tick(page.store, at + timedelta(hours=2))
    assert page.refreshes == [at]
    next_day = local(2026, 11, 21, 0, 1)
    S.tick(page.store, next_day)
    assert page.refreshes == [at, next_day]
    assert S.start(page.store, next_day) is False
    # A failing refresh waits for tomorrow, and doesn't stop the drafts.
    page.note("ops", "weekly", "2026-11-03", "November.")
    page.answer = NO_FIGURES

    def boom(store, when):
        raise RuntimeError("render failed")
    monkeypatch.setattr(release, "refresh", boom)
    assert [d["status"] for d in S.tick(page.store, local(2026, 12, 7))] == ["ready"]
    assert S.start(page.store, local(2026, 12, 7)) is False
    with page.store.transaction() as c:
        c.execute("UPDATE company_page_settings SET transparency='closed'")
    monkeypatch.setattr(release, "refresh", lambda store, when: page.refreshes.append(when))
    S.tick(page.store, local(2026, 12, 8))
    assert len(page.refreshes) == 2 and S.start(page.store, local(2026, 12, 8)) is False


def test_tick_catches_up_once_with_no_backlog_and_a_repeat_does_nothing(page):
    # Down since September: only October's edition opens, however many months passed.
    page.note("ops", "weekly", "2026-07-14", "July.")
    page.note("ops", "weekly", "2026-08-14", "August.")
    at = local(2026, 11, 20)
    assert len(S.tick(page.store, at)) == 1
    rows = page.rows("SELECT period_start, period_end, status FROM editions")
    assert rows == [{"period_start": START, "period_end": END, "status": "ready"}]
    assert S.tick(page.store, at) == [] and S.tick(page.store, at + timedelta(days=2)) == []
    assert len(page.calls) == 1
    # A retraction counts as decided: the schedule doesn't write it again.
    edition = page.rows("SELECT id FROM editions")[0]["id"]
    post(page.api, f"editions/{edition}/retract", {"note": "Skip this month."})
    assert S.tick(page.store, at) == []


def test_tick_before_the_due_day_or_with_nothing_written_opens_nothing(page):
    assert S.tick(page.store, local(2026, 11, 5, 23, 59)) == []
    assert page.rows("SELECT id FROM editions") == []
    assert len(S.tick(page.store, local(2026, 11, 6))) == 1
    # December's edition has nothing to read in November yet.
    assert S.tick(page.store, local(2026, 12, 7)) == []


@pytest.mark.parametrize("change,args", [
    ("UPDATE company_page_settings SET transparency='closed'", ()),
    ("DELETE FROM company_page_settings", ()),
    ("UPDATE audiences SET enabled=0", ()),
])
def test_closed_or_disabled_does_nothing_and_sets_no_wake(page, change, args):
    at = local(2026, 11, 20)
    with page.store.transaction() as c:
        c.execute(change, args)
    assert S.tick(page.store, at) == [] and page.calls == []
    assert S.start(page.store, at) is False       # the tick above already had the day's refresh
    with page.store.read() as c:
        assert S.next_wake(c, at) is None
        assert sleep_idle.edition_times(c, at) == []


def test_public_waits_for_open_and_the_wake_time_reaches_the_sleep_timer(page):
    at = local(2026, 11, 1)
    with page.store.transaction() as c:
        c.execute("""UPDATE audiences SET kind='public', due_rule='{"days_after": 30}'""")
    with page.store.read() as c:
        assert S.next_wake(c, at) is None
    assert S.tick(page.store, local(2026, 12, 1)) == []
    with page.store.transaction() as c:
        c.execute("UPDATE company_page_settings SET transparency='open'")
    with page.store.read() as c:
        assert S.next_wake(c, at) == local(2026, 11, 30)
        assert sleep_idle.edition_times(c, at) == [local(2026, 11, 30).timestamp()]
    assert len(S.tick(page.store, local(2026, 12, 1))) == 1


def test_a_failed_scheduled_draft_is_held_and_doesnt_stop_the_others(page):
    page.answer = RuntimeError("model down")
    at = local(2026, 11, 20)
    assert [d["status"] for d in S.tick(page.store, at)] == ["held"]
    assert S.tick(page.store, at) == []     # a person picks it up from there; the schedule doesn't call again and again
    assert len(page.calls) == 1


def test_a_draft_left_by_a_restart_is_picked_up_again(page):
    with page.store.transaction() as c:
        c.execute("INSERT INTO editions(id,audience_id,author,period_start,period_end,status,created_at,updated_at) "
                  "VALUES('left','board','ops',?,?,'drafting',?,?)",
                  (START, END, H.shift(H.now(), days=-1), H.shift(H.now(), days=-1)))
    assert [d["id"] for d in S.tick(page.store, local(2026, 11, 20))] == ["left"]
    assert page.rows("SELECT status FROM editions") == [{"status": "ready"}]


def test_a_naive_time_reads_as_utc():
    assert S.next_due(BOARD, datetime(2026, 11, 1), ZONE).tzinfo == timezone.utc
