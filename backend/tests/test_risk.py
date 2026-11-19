"""Risks and matters (backend/risk.py, Risk plan slice 1): a risk is a tag of kind risk that any task can carry, with
its matter facts; rules and documents link to it. Every teammate lists every risk; its counts and sums follow task
privacy, so a teammate who reads none of its tasks sees it with 0."""

from datetime import timedelta

import pytest

from backend import hubdb as H
from backend import rule_due as D
from backend.entities import store as ES
from backend.tests.helpers.tasks_board import api, bot_token, get, headers, post  # noqa: F401

ANA = "human:ana"
RISK = "risk:customer-theft-claims"


def task(api, title, token="ana-test", **extra):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Acme matter.", **extra}, token=token)


def label(api, task_id, labels, token="ana-test", expected=200):
    version = get(api, "tasks/" + task_id)["task"]["version"]
    return post(api, "tasks/" + task_id, {"version": version, "labels": labels}, token=token, expected=expected)


def matter(api, task_id, body, token="ana-test", expected=200):
    return post(api, f"tasks/{task_id}/matter", body, token=token, expected=expected)


def risk_of(api, token="ana-test", key=RISK):
    return next(r for r in get(api, "risks", token=token)["risks"] if r["key"] == key)


def entity(api, kind, name, match):
    with api.app.state.store.transaction() as c:
        return ES.upsert(c, ANA, kind, match=match, name=name)["id"]


def test_a_risk_is_a_tag_of_kind_risk_and_its_kind_never_changes(api):
    made = post(api, "risks", {"label": "Customer theft claims", "markdown": "Claims that a cleaner took something."})
    risk = made["risk"]
    assert (risk["key"], risk["kind"], risk["label"], risk["level"], risk["open"]) == (
        RISK,
        "risk",
        "Customer theft claims",
        "quiet",
        0,
    )
    # No page owner bot here: the maker owns it and no bot is granted.
    assert risk["owner"] == ANA and risk["bot_writers"] == []
    tag = get(api, "tags/" + risk["id"])["tag"]
    assert tag["kind"] == "risk"
    assert post(api, "risks", {"label": "Customer Theft Claims!"}, expected=409)["error"]["code"] == "duplicate"
    # The tag routes neither make a risk nor change a kind: an unknown field is refused whole.
    post(api, "tags", {"key": "risk:other", "kind": "risk"}, expected=422)
    post(api, "tags/" + risk["id"], {"version": tag["version"], "kind": ""}, expected=422)
    token = bot_token(api, "cpo")
    post(api, "tags/" + risk["id"], {"version": tag["version"], "kind": ""}, token=token, expected=422)
    plain = post(api, "tags", {"key": "area-ops"}, token=token)["tag"]
    assert plain["kind"] == ""
    post(api, "tags/" + plain["id"], {"version": plain["version"], "kind": "risk"}, token=token, expected=422)
    assert get(api, "tags/" + risk["id"])["tag"]["kind"] == "risk"
    assert get(api, "tags/" + plain["id"])["tag"]["kind"] == ""
    # A plain tag of the same key keeps a risk from being made over it.
    post(api, "tags", {"key": "risk:screening"})
    assert "plain tag" in post(api, "risks", {"label": "Screening"}, expected=409)["error"]["detail"]
    # Update takes a version; the owner or a task mover edits it.
    updated = post(api, "risks/" + RISK, {"version": tag["version"], "label": "Theft claims"})["risk"]
    assert updated["label"] == "Theft claims" and updated["version"] == tag["version"] + 1
    post(api, "risks/" + RISK, {"version": tag["version"], "label": "Again"}, expected=409)
    post(api, "risks/" + RISK, {"version": updated["version"], "label": "Priya's"}, token="priya-test", expected=403)


def test_six_tagged_matters_count_and_sum_for_their_readers_and_zero_for_anyone_else(api):
    post(api, "risks", {"label": "Customer theft claims"})
    party = entity(api, "organization", "Acme Homes", {"domain": "homes.acme.example"})
    ids = []
    for n in range(6):
        # Private Legal work: Ana asked for it and ops owns it; Priya reads none of it.
        found = task(api, f"Theft claim {n + 1}", private=True)
        label(api, found["id"], [RISK])
        body = {
            "kind": "dispute",
            "amount": 100_000 * (n + 1),
            "currency": "usd",
            "forum": "AAA",
            "party_entity_id": party,
        }
        set_ = matter(api, found["id"], body)
        assert set_["matter"]["currency"] == "USD" and set_["matter"]["party"] == {"id": party, "name": "Acme Homes"}
        ids.append(found["id"])
    matter(api, ids[5], {"side": "ours"})  # our claim is not the other side's: it leaves the total
    seen = risk_of(api)
    assert (seen["open"], seen["closed"], seen["level"]) == (6, 0, "live")
    assert seen["claimed"] == [{"currency": "USD", "amount": 100_000 * (1 + 2 + 3 + 4 + 5)}]
    assert "markdown" not in seen  # the list is brief
    hidden = risk_of(api, "priya-test")
    assert (hidden["open"], hidden["closed"], hidden["claimed"], hidden["level"]) == (0, 0, [], "quiet")
    assert get(api, "risks/" + RISK, token="priya-test")["matters"] == []
    assert get(api, "matters?kind=dispute&open=true", token="priya-test")["matters"] == []
    # The full task view carries the matter; ?task= lists only that task's risks.
    view = get(api, "tasks/" + ids[0])["task"]
    assert view["matter"]["kind"] == "dispute" and view["matter"]["amount"] == 100_000
    assert [r["key"] for r in get(api, "risks?task=" + ids[0])["risks"]] == [RISK]
    get(api, "risks?task=" + ids[0], token="priya-test", expected=404)
    # Disputes: open ones, newest first, with the risk each carries; paging stops at the last.
    page = get(api, "matters?kind=dispute&open=true&limit=4")
    assert len(page["matters"]) == 4 and page["next_offset"] == 4
    row = page["matters"][0]
    assert row["risks"][0]["key"] == RISK and row["matter"]["forum"] == "AAA" and row["task"]["status"] == "open"
    rest = get(api, "matters?kind=dispute&open=true&limit=4&offset=4")
    assert len(rest["matters"]) == 2 and rest["next_offset"] is None
    assert get(api, "matters?kind=notice")["matters"] == []
    # ?task= is one task's row (the Risk page patches it in from a live event); none for a task the reader cannot see.
    one = get(api, "matters?task=" + ids[2])["matters"]
    assert [m["task"]["id"] for m in one] == [ids[2]] and one[0]["matter"]["amount"] == 300_000
    assert get(api, "matters?task=" + ids[2], token="priya-test")["matters"] == []
    # A fact set alone leaves the others: saving a forum keeps the party.
    matter(api, ids[2], {"version": one[0]["matter"]["version"], "forum": "Court"})
    after = get(api, "matters?task=" + ids[2])["matters"][0]["matter"]
    assert (after["forum"], after["party_entity_id"], after["amount"]) == ("Court", party, 300_000)
    detail = get(api, "risks/" + RISK)
    assert detail["editable"] and len(detail["matters"]) == 6 and detail["risk"]["open"] == 6
    # A finished matter counts as closed and leaves the total.
    post(api, f"tasks/{ids[0]}/done", {"note": "Settled."})
    seen = risk_of(api)
    assert (seen["open"], seen["closed"]) == (5, 1)
    assert seen["claimed"] == [{"currency": "USD", "amount": 100_000 * (2 + 3 + 4 + 5)}]
    # Erasing the party leaves the matter, without the party.
    with api.app.state.store.transaction() as c:
        ES.erase_rows(c, ANA, party, "request")
        assert c.execute("SELECT count(*) FROM matters WHERE party_entity_id IS NOT NULL").fetchone()[0] == 0
    assert get(api, "tasks/" + ids[1])["task"]["matter"]["party"] is None


def test_a_risk_whose_text_names_a_private_task_lists_only_to_its_readers_in_the_api_and_sql(api):
    secret = task(api, "Acme private claim", private=True)
    post(api, "risks", {"label": "Open claims"})
    post(api, "risks", {"label": "Named claims", "markdown": f"See [the claim](#/task/{secret['id']})."})

    def keys(token):
        listed = [r["key"] for r in get(api, "risks", token=token)["risks"]]
        rows = post(api, "sql", {"sql": "SELECT key FROM tags WHERE kind='risk' ORDER BY key"}, token=token)["rows"]
        return listed, [r[0] for r in rows]

    named, open_ = "risk:named-claims", "risk:open-claims"
    assert keys("ana-test") == ([named, open_], [named, open_])
    assert keys("priya-test") == ([open_], [open_])
    get(api, "risks/" + named, token="priya-test", expected=404)


def test_matter_facts_are_checked_and_a_stale_version_is_refused(api):
    post(api, "risks", {"label": "Customer theft claims"})
    found = task(api, "Theft claim")
    refused = matter(api, found["id"], {"kind": "dispute"}, expected=422)["error"]
    assert "Tag the task with a risk first" in refused["detail"]
    label(api, found["id"], [RISK])
    matter(api, found["id"], {"amount": 5000, "currency": "USD"}, expected=422)  # a new matter needs its kind
    matter(api, found["id"], {"kind": "lawsuit"}, expected=422)
    matter(api, found["id"], {"kind": "dispute", "amount": 5000}, expected=422)  # an amount needs its currency
    matter(api, found["id"], {"kind": "dispute", "amount": -1, "currency": "USD"}, expected=422)
    matter(api, found["id"], {"kind": "dispute", "secret": "x"}, expected=422)
    place = entity(api, "organization", "Acme Storage", {"domain": "storage.acme.example"})
    matter(api, found["id"], {"kind": "dispute", "place_id": place}, expected=422)  # an organization is no place
    matter(api, found["id"], {"kind": "dispute", "party_entity_id": "ent_missing"}, expected=404)
    first = matter(api, found["id"], {"kind": "dispute", "claim": "A missing ring"})["matter"]
    assert (first["version"], first["side"], first["claim"]) == (1, "theirs", "A missing ring")
    second = matter(api, found["id"], {"version": 1, "forum": "Small claims"})["matter"]
    assert (second["version"], second["claim"], second["forum"]) == (2, "A missing ring", "Small claims")
    assert matter(api, found["id"], {"version": 1, "forum": "AAA"}, expected=409)["error"]["code"] == "version_conflict"
    assert matter(api, found["id"], {"version": 2, "claim": None})["matter"]["claim"] == ""
    matter(api, found["id"], {"version": 3, "kind": None}, expected=422)
    matter(api, found["id"], {"clear": True, "kind": "notice"}, expected=422)
    # Whoever may not change the task may not set its facts; one who cannot read it is told it is not there.
    company = task(api, "Company claim")
    label(api, company["id"], [RISK])
    matter(api, company["id"], {"kind": "notice"}, token="priya-test", expected=403)
    hidden = task(api, "Private claim", private=True)
    label(api, hidden["id"], [RISK])
    matter(api, hidden["id"], {"kind": "notice"}, token="priya-test", expected=404)
    assert matter(api, found["id"], {"clear": True})["matter"] is None
    assert "matter" not in get(api, "tasks/" + found["id"])["task"]
    with api.app.state.store.read() as c:
        logged = [r[0] for r in c.execute("SELECT object FROM audit_log WHERE object=?", ("matter:" + found["id"],))]
    assert len(logged) == 4  # set, edit, clear the claim, clear: each write leaves its row (a refusal none)


def test_a_matter_through_its_tasks_life_and_the_risk_it_carries(api):
    # A plain tag `claims` does not hide the risk `risk:claims` from a lookup by slug.
    post(api, "tags", {"key": "claims"})
    risk = post(api, "risks", {"label": "Claims"})["risk"]
    assert get(api, "risks/claims")["risk"]["key"] == "risk:claims"
    found = task(api, "Acme deposit claim")
    label(api, found["id"], ["risk:claims"])
    matter(api, found["id"], {"kind": "dispute", "amount": 700, "currency": "USD", "claim": "Deposit"})
    # An edit answers with the risk's standing, as a read would.
    edited = post(api, "risks/risk:claims", {"version": risk["version"], "label": "Deposit claims"})["risk"]
    assert (edited["open"], edited["level"], edited["claimed"]) == (1, "live", [{"currency": "USD", "amount": 700}])
    # A version read before a clear does not make the facts again.
    assert matter(api, found["id"], {"version": 1, "clear": True})["matter"] is None
    assert (
        matter(api, found["id"], {"version": 1, "kind": "notice"}, expected=409)["error"]["code"] == "version_conflict"
    )
    # Facts left after the last risk comes off can still be cleared, and nothing new is set without one.
    matter(api, found["id"], {"kind": "notice"})
    label(api, found["id"], [])
    matter(api, found["id"], {"version": 1, "forum": "AAA"}, expected=422)
    assert matter(api, found["id"], {"version": 1, "clear": True})["matter"] is None
    # A merged party answers as the record it was merged into.
    old = entity(api, "organization", "Acme Homes", {"domain": "homes.acme.example"})
    kept = entity(api, "organization", "Acme Homes LLC", {"domain": "acmehomes.example"})
    label(api, found["id"], ["risk:claims"])
    matter(api, found["id"], {"kind": "dispute", "party_entity_id": old, "claim": "Deposit"})
    with api.app.state.store.transaction() as c:
        ES.merge(c, ANA, kept, old)
    assert get(api, "tasks/" + found["id"])["task"]["matter"]["party"] == {"id": kept, "name": "Acme Homes LLC"}
    # A task restored after its party was erased keeps its facts, without the party.
    post(api, f"tasks/{found['id']}/delete", {})
    with api.app.state.store.transaction() as c:
        ES.erase_rows(c, ANA, kept, "request")
    restored = api.post(f"/api/v2/tasks/{found['id']}/restore", headers=headers(), json={})
    assert restored.status_code == 200, restored.text
    back = get(api, "tasks/" + found["id"])["task"]["matter"]
    assert (back["claim"], back["party"], back["party_entity_id"]) == ("Deposit", None, None)


def test_the_audit_feed_shows_a_matters_facts_only_to_a_reader_of_its_task(api):
    from backend import audit_log

    post(api, "risks", {"label": "Customer theft claims"})
    mine = task(api, "Acme claim")
    label(api, mine["id"], [RISK])
    matter(api, mine["id"], {"kind": "dispute", "claim": "A missing ring"})
    assert get(api, "audit?object=matter:" + mine["id"])["rows"][0]["after"]["claim"] == "A missing ring"
    # Priya's private task the owner does not read: its matter's row says which task, not the facts.
    hidden = post(
        api,
        "tasks",
        {"owner": "human:priya", "title": "Acme warrant", "body": "x", "private": True},
        token="priya-test",
    )
    get(api, "tasks/" + hidden["id"], expected=404)
    with api.app.state.store.transaction() as c:
        audit_log.write(c, "bot:cmo", "matter:" + hidden["id"], None, {"task_id": hidden["id"], "claim": "Secret"})
    row = get(api, "audit?object=matter:" + hidden["id"])["rows"][0]
    assert (row["before"], row["after"]) == (None, {"task_id": hidden["id"]})


def test_the_risk_page_owner_bot_tags_a_task_it_reads_and_sets_its_facts(api):
    r = api.put("/api/v2/page-owners/risk", json={"bot": "cpo"}, headers=headers())
    assert r.status_code == 200, r.text
    risk = post(api, "risks", {"label": "Customer theft claims"}, token="priya-test")["risk"]
    assert risk["owner"] == "bot:cpo" and risk["bot_writers"] == ["bot:cpo"]
    token = bot_token(api, "cpo")
    ticket = post(api, "tasks", {"owner": "cmo", "title": "Refund dispute", "body": "x"})
    assert label(api, ticket["id"], [RISK], token=token)["labels"] == [RISK]
    made = matter(api, ticket["id"], {"kind": "dispute", "amount": 1200, "currency": "USD"}, token=token)["matter"]
    assert made["created_by_id"] == "bot:cpo"
    # A bot sends the version it read once the facts exist.
    matter(api, ticket["id"], {"forum": "Demand"}, token=token, expected=409)
    assert matter(api, ticket["id"], {"version": 1, "forum": "Demand"}, token=token)["matter"]["version"] == 2
    # Another bot is granted nothing, and a task the owner bot cannot read stays hidden.
    other = post(api, "tasks", {"owner": "ops", "title": "Chargeback", "body": "x"})
    label(api, other["id"], [RISK], token=bot_token(api, "cmo"), expected=403)
    hidden = post(api, "tasks", {"owner": "ops", "title": "Warrant", "body": "x", "private": True})
    label(api, hidden["id"], [RISK], token=token, expected=404)
    # The owner bot edits its risk; a person who is neither its owner nor a mover does not.
    post(api, "risks/" + RISK, {"version": risk["version"], "markdown": "Theft claims."}, token=token)
    post(api, "risks/" + RISK, {"version": risk["version"] + 1, "label": "x"}, token="priya-test", expected=403)


def test_rules_and_documents_link_once_each(api):
    post(api, "risks", {"label": "Customer theft claims"})
    policy = entity(api, "document", "Acme crime policy", {"name": "Acme crime policy"})
    org = entity(api, "organization", "Acme Insurance", {"domain": "insurance.acme.example"})
    with api.app.state.store.transaction() as c:
        ts = H.now()
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,status,suggested_by,created_at,updated_at) "
            "VALUES('rule_vet','internal','Vet every cleaner before a first job','{}','active',?,?,?)",
            (ANA, ts, ts),
        )
    rule = {"linkable_type": "org_rule", "linkable_id": "rule_vet"}
    source = {"linkable_type": "entity", "linkable_id": policy}
    covers = {**source, "role": "covers"}
    assert post(api, f"risks/{RISK}/links", rule)["link"]["role"] == "rule"
    assert post(api, f"risks/{RISK}/links", source)["link"]["role"] == "source"
    post(api, f"risks/{RISK}/links", covers)
    assert post(api, f"risks/{RISK}/links", rule, expected=409)["error"]["code"] == "duplicate"
    post(api, f"risks/{RISK}/links", source, expected=409)
    post(api, f"risks/{RISK}/links", {**rule, "role": "source"}, expected=422)
    post(api, f"risks/{RISK}/links", {"linkable_type": "entity", "linkable_id": org}, expected=422)
    post(api, f"risks/{RISK}/links", {"linkable_type": "org_rule", "linkable_id": "rule_none"}, expected=404)
    post(api, f"risks/{RISK}/links", {"linkable_type": "entity", "linkable_id": policy, "role": "x"}, expected=422)
    post(api, f"risks/{RISK}/links", {"linkable_type": "entity", "linkable_id": org}, token="priya-test", expected=403)
    detail = get(api, "risks/" + RISK, token="priya-test")
    assert [r["text"] for r in detail["rules"]] == ["Vet every cleaner before a first job"]
    assert [(s["name"], s["type"]) for s in detail["sources"]] == [("Acme crime policy", "document")]
    assert [s["id"] for s in detail["coverage"]] == [policy]
    assert not detail["editable"]
    assert post(api, f"risks/{RISK}/unlink", covers)["link"] is None
    post(api, f"risks/{RISK}/unlink", covers, expected=404)
    assert get(api, "risks/" + RISK)["coverage"] == []
    # Erasing a document drops its links.
    with api.app.state.store.transaction() as c:
        ES.erase_rows(c, ANA, policy, "request")
    assert get(api, "risks/" + RISK)["sources"] == []


def test_a_matter_that_waits_on_a_person_makes_its_risk_live_for_them_alone(api):
    """Risk page (slice 2): live when an open matter waits on the viewer, as their Needs you has it; a linked active rule
    shows as `has_rules`, never a count."""
    from backend import question_marks as Q

    post(api, "risks", {"label": "Customer theft claims"})
    found = task(api, "Theft claim from Acme Homes")
    label(api, found["id"], [RISK])
    matter(api, found["id"], {"kind": "dispute"})
    month_ago = H.shift(H.now(), days=-30)
    with api.app.state.store.transaction() as c:
        # Arrived a month ago: no longer new, so the risk is open, not live.
        c.execute("UPDATE matters SET created_at=? WHERE task_id=?", (month_ago, found["id"]))
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (month_ago, found["id"]))
    seen = risk_of(api)
    assert (seen["level"], seen["waits"], seen["has_rules"]) == ("open", False, False)
    with api.app.state.store.transaction() as c:
        Q.ask(c, "bot:ops", found["id"], ANA, "Which date did the cleaner come?")
    seen = risk_of(api)
    assert (seen["level"], seen["waits"]) == ("live", True)
    # Ben reads the task too, but nothing waits on him: open for him.
    ben = risk_of(api, "ben-test")
    assert (ben["level"], ben["waits"]) == ("open", False)
    assert get(api, "risks/" + RISK)["risk"]["waits"] is True
    # A linked rule: shown to every teammate while active, and also on a risk with no matter at all.
    post(api, "risks", {"label": "Data breach"})
    with api.app.state.store.transaction() as c:
        ts = H.now()
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,status,suggested_by,created_at,updated_at) "
            "VALUES('rule_vet','internal','Vet every cleaner before a first job','{}','active',?,?,?)",
            (ANA, ts, ts),
        )
    for key in (RISK, "risk:data-breach"):
        post(api, f"risks/{key}/links", {"linkable_type": "org_rule", "linkable_id": "rule_vet"})
    assert risk_of(api)["has_rules"] is True and risk_of(api, "priya-test")["has_rules"] is True
    breach = risk_of(api, key="risk:data-breach")
    assert (breach["has_rules"], breach["level"], breach["open"], breach["closed"]) == (True, "quiet", 0, 0)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rules SET status='retired' WHERE id='rule_vet'")
    assert risk_of(api)["has_rules"] is False
    # The ask answered or the task done: nothing waits, and the risk is closed out to quiet.
    post(api, f"tasks/{found['id']}/done", {"note": "Settled."})
    assert (risk_of(api)["level"], risk_of(api)["waits"]) == ("quiet", False)


def test_the_answers_match_the_contract_and_the_migration_runs_again(api):
    from importlib import import_module

    from backend.tests.helpers.openapi_v2 import conforms

    made = post(api, "risks", {"label": "Customer theft claims"})
    found = task(api, "Theft claim")
    label(api, found["id"], [RISK])
    set_ = matter(api, found["id"], {"kind": "dispute", "amount": 900, "currency": "USD"})
    # A date of the matter, marked with proof, and one still unconfirmed.
    with api.app.state.store.transaction() as c:
        ts = H.now()
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,status,suggested_by,created_at,updated_at) "
            "VALUES('rule_court','internal','Meet every date a court sets','{}','active',?,?,?)",
            (ANA, ts, ts),
        )
    post(api, f"risks/{RISK}/links", {"linkable_type": "org_rule", "linkable_id": "rule_court"})
    for title, first in (
        ("Murphy discovery", D.local_today("UTC") - timedelta(days=20)),
        ("Murphy hearing", D.local_today("UTC") - timedelta(days=10)),
    ):
        body = {"first_due": first, "repeats": "once", "owner": "ana", "timezone": "UTC", "title": title}
        body["first_due"] = first.isoformat()
        dated = post(api, "rules/rule_court/due", {**body, "basis": "order", "task_id": found["id"]})
    post(api, f"rule-due/{dated['id']}/satisfied", {"proof": {"kind": "link", "ref": "https://aaa.example/r"}})
    obligations = get(api, "risk/obligations?group=unconfirmed")
    assert [(o["title"], o["mark"]) for o in obligations["obligations"]] == [("Murphy discovery", None)]
    assert get(api, "risks/" + RISK)["risk"]["unconfirmed"] == 1
    document = get(api, "openapi.json")
    shape = {
        op["operationId"]: op["responses"]["200"]["content"]["application/json"]["schema"]
        for ops in document["paths"].values()
        for op in ops.values()
        if op.get("tags") == ["Risk"]
    }
    assert set(shape) == {
        "listRisks",
        "createRisk",
        "updateRisk",
        "getRisk",
        "linkRisk",
        "unlinkRisk",
        "setTaskMatter",
        "listMatters",
        "listRiskObligations",
    }
    for operation, answer in (
        ("createRisk", made),
        ("setTaskMatter", set_),
        ("listRisks", get(api, "risks")),
        ("getRisk", get(api, "risks/" + RISK)),
        ("listMatters", get(api, "matters")),
        ("listRiskObligations", obligations),
    ):
        assert conforms(answer, shape[operation], document) is None, (
            operation,
            conforms(answer, shape[operation], document),
        )
    task_schema = document["components"]["schemas"]["Task"]
    assert conforms(get(api, "tasks/" + found["id"])["task"], task_schema, document) is None
    # Pending files run in any order and may run on a database that has the change: running it again changes nothing.
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused):
            H.tag_create(c, ANA, "acme-other", kind="contract")  # a kind is '' or risk
    for name in ("20261119025516_risks_and_matters", "20261119034134_rule_due_proof"):
        with api.app.state.store.transaction() as c:
            import_module("backend.migrations." + name).up(c)
            assert c.execute("SELECT kind FROM tags WHERE key=?", (RISK,)).fetchone()[0] == "risk"
            assert c.execute("SELECT count(*) FROM matters").fetchone()[0] == 1
