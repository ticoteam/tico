"""Letters through a fake HQ: the OK bound to one proof is the authority for it, and retries never print twice."""

import json

import pytest

from backend import hubdb as H, letters as L, task_asks
from backend.tests.helpers.api import get, post, put, headers  # noqa: F401
from backend.tests.helpers.agents import hermes_bot, credential, bot_id
from backend.tests.helpers.mcp import call
from backend.tests.helpers.botops_parity import act
from backend.tests.helpers.member_bots import botops, turn  # noqa: F401 (fixture)
from clients import gateway as G
from backend.tests.helpers.letters import HQ, RECIPIENT, fetch, grant, hq, quote, send


def _task(api, row):
    with api.app.state.store.read() as c:
        return task_asks.task_of(c, row["approval_id"])


def approve(api, row, token="ana-test", expected=200, reader=None):
    """The person opens the letter in Tico and clicks OK on it (backend/task_asks.py). `reader` opens it instead, for
    a person who may not read the letter and still tries its OK."""
    shown = get(api, "letters/" + row["id"], token=reader or token)["shown_hash"]
    path = f"tasks/{_task(api, row)}/asks/{row['approval_id']}/ok"
    return post(api, path, {"shown_hash": shown}, token=token, expected=expected)


def asked(api, row):
    """What the letter's OK is for, as it was asked."""
    with api.app.state.store.read() as c:
        return task_asks.grant(c, row["approval_id"])


def bot(api):
    hermes_bot(api)
    return bot_id(api), credential(api)["token"]


def test_botops_policy_and_access_changes_wait_for_the_persons_tap(api, botops):
    ana = turn(api, botops, person="ana-test", text="Let Ops send letters and set a cap")
    path = "letters/outboxes/company/grants/bot/ops"
    card = act(api, ana, "PUT", path, {"level": "send"}).json()
    assert card["needs_confirm"] and get(api, "letters/outboxes/company/grants")["grants"] == []
    confirm = "assistant/actions/" + card["action"]["id"] + "/confirm"
    post(api, confirm, {}, token=ana["token"], expected=403)
    post(api, confirm, {})
    assert get(api, "letters/outboxes/company/grants")["grants"][0]["grantee_id"] == "ops"
    card = act(api, ana, "PUT", "letters/settings", {"monthly_cap_micros": 5000000}).json()
    assert card["needs_confirm"] and get(api, "letters/settings")["monthly_cap_micros"] is None
    post(api, "assistant/actions/" + card["action"]["id"] + "/confirm", {})
    assert get(api, "letters/settings")["monthly_cap_micros"] == 5000000
    card = act(api, ana, "DELETE", path).json()
    assert card["needs_confirm"] and get(api, "letters/outboxes/company/grants")["grants"]
    post(api, "assistant/actions/" + card["action"]["id"] + "/confirm", {})
    assert get(api, "letters/outboxes/company/grants")["grants"] == []


def test_grants_are_paged_without_duplicates_and_refuse_oversized_pages(api):
    grant(api, "ops")
    grant(api, "finance")
    first = get(api, "letters/outboxes/company/grants?limit=1")
    second = get(api, "letters/outboxes/company/grants?limit=1&after=" + first["next"])
    assert first["outbox_id"] == "company" and len(first["grants"]) == len(second["grants"]) == 1
    assert {first["grants"][0]["grantee_id"], second["grants"][0]["grantee_id"]} == {"ops", "finance"}
    assert second["next"] is None
    get(api, "letters/outboxes/company/grants?limit=101", expected=422)
    assert len(json.dumps(get(api, "letters/outboxes/company/grants")).encode()) < 64 * 1024


def test_quote_ask_approve_requotes_same_hash_and_submits_once(api, hq):
    row = quote(api)
    assert row["page_count"] == 1 and row["total_micros"] == 1500000
    row = send(api, row)
    assert row["status"] == "waiting_for_ok" and not hq.jobs
    approval = asked(api, row)
    assert approval["content"]["approval_hash"] == row["approval_hash"]
    assert approval["content"]["recipient"] == RECIPIENT | {"line2": ""}
    approve(api, row)
    after = fetch(api, row)
    assert after["status"] == "submitted" and len(hq.quotes) == 2 and hq.provider_submits == 1
    assert asked(api, row)["used_at"]
    assert send(api, row)["hq_letter_id"] == after["hq_letter_id"] and hq.provider_submits == 1


def test_changed_hash_asks_again_and_keeps_the_older_proof(api, hq):
    row = send(api, quote(api))
    hq.changed = True
    approve(api, row)
    after = fetch(api, row)
    assert after["status"] == "waiting_for_ok" and after["approval_id"] != row["approval_id"]
    assert after["total_micros"] == 1800000 and hq.provider_submits == 0
    assert api.get(row["proof"], headers=headers()).content == hq.proof
    approve(api, after)
    assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1


def test_an_ok_of_a_letter_requoted_after_it_was_shown_is_refused_and_the_new_quote_is_shown(api, hq):
    """The person opened the letter, then it was quoted again (a new total) and asked again before their click: the
    click on what they saw gives no OK, on the old ask or the new one; the letter's preview then shows the new quote,
    whose own OK mails it."""
    row = send(api, quote(api))
    seen = fetch(api, row)
    old_hash, old_ask = seen["shown_hash"], seen["approval_id"]
    hq.changed = True
    service = api.app.state.letters
    service.requote(row["id"])
    with api.app.state.store.transaction() as c:
        service.ask(c, dict(c.execute("SELECT * FROM letters WHERE id=?", (row["id"],)).fetchone()))
    after = fetch(api, row)
    assert after["approval_id"] != old_ask and after["total_micros"] == 1800000
    assert after["shown_hash"] != old_hash
    refused = post(api, f"tasks/{_task(api, seen)}/asks/{old_ask}/ok", {"shown_hash": old_hash}, expected=409)
    assert refused["error"] == "task_requirement"
    changed = post(
        api, f"tasks/{_task(api, after)}/asks/{after['approval_id']}/ok", {"shown_hash": old_hash}, expected=409
    )
    assert changed["error"] == "task_changed"
    assert asked(api, after)["ok_at"] is None and hq.provider_submits == 0
    approve(api, after)
    done = fetch(api, row)
    assert done["status"] == "submitted" and hq.provider_submits == 1


def test_bot_needs_send_access_and_grants_are_audited(api, hq):
    ident, token = bot(api)
    refused = post(api, "letters/quotes", {"text": "Hello", "recipient": RECIPIENT}, token=token, expected=403)
    assert refused["error"]["code"] == "forbidden" and not hq.calls
    grant(api, ident)
    row = quote(api, token=token)
    assert row["acting_bot_id"] == ident
    assert not call(api, "tico_letter_send", {"letter_id": row["id"]}, token=token)[0]
    approved = fetch(api, row)
    approve(api, approved)
    assert fetch(api, row)["status"] == "submitted"
    result = api.delete("/api/v2/letters/outboxes/company/grants/bot/" + ident, headers=headers())
    assert result.status_code == 200
    assert not get(api, "letters/access", token=token)["send"]
    with api.app.state.store.read() as c:
        actions = [r[0] for r in c.execute("SELECT action FROM events WHERE action LIKE 'letter.access.%'")]
    assert actions == ["letter.access.granted", "letter.access.revoked"]


def test_revoke_or_never_before_approval_prevents_submission(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    row = send(api, quote(api, token=token), token=token)
    api.delete("/api/v2/letters/outboxes/company/grants/bot/" + ident, headers=headers())
    approve(api, row)
    assert fetch(api, row)["error"] == "forbidden" and not hq.jobs
    grant(api, ident)
    put(api, "letters/settings", {"mode": "never"})
    assert (
        post(api, "letters/" + row["id"] + "/send", {}, token=token, expected=403)["error"]["code"]
        == "letters_disabled"
    )


def test_an_admin_can_approve_but_an_unrelated_person_cannot(api, hq):
    """An OK's task is private to its parties, and an ask goes only to someone who can already read it (the engine
    never adds a reader): cara, a plain member, cannot be given it, cannot open the letter and cannot OK it; ben, an
    Admin, is given it once Ana shares the task with him, and OKs it."""
    ident, token = bot(api)
    grant(api, ident)
    theirs, bens = (send(api, quote(api, token=token), token=token) for _ in range(2))
    with pytest.raises(H.Refused, match="Cara can't see this private task"):
        with api.app.state.store.transaction() as c:
            H.task_give(
                c, "human:ana", task_asks.task_of(c, theirs["approval_id"]), theirs["approval_id"], "human:cara"
            )
    with api.app.state.store.transaction() as c:  # Ana, who can see it, shares it with Ben first
        H.set_task_readers(c, "human:ana", task_asks.task_of(c, bens["approval_id"]), add=["human:ben"])
        H.task_give(c, "human:ana", task_asks.task_of(c, bens["approval_id"]), bens["approval_id"], "human:ben")
    get(api, "letters/" + theirs["id"], token="cara-test", expected=404)
    refused = approve(api, theirs, token="cara-test", expected=404, reader="ana-test")  # not hers to read or OK
    assert refused["error"]["code"] == "not_found" and fetch(api, theirs)["status"] != "submitted"
    approve(api, bens, token="ben-test")
    assert fetch(api, bens)["status"] == "submitted"


def test_company_monthly_cap_counts_accepted_spend_and_approval_cannot_bypass_it(api, hq):
    put(api, "letters/settings", {"monthly_cap_micros": 2000000})
    first = send(api, quote(api))
    second = send(api, quote(api))
    approve(api, first)
    approve(api, second)
    assert fetch(api, second)["error"] == "letters_cap" and hq.provider_submits == 1


def test_per_bot_cap_and_bot_override_cannot_loosen_company_policy(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    put(api, "letters/bots/" + ident + "/settings", {"mode": "on_their_own"}, expected=422)
    put(api, "letters/bots/" + ident + "/settings", {"monthly_cap_micros": 1000000})
    row = quote(api, token=token)
    assert post(api, "letters/" + row["id"] + "/send", {}, token=token, expected=409)["error"]["code"] == "letters_cap"
    assert not hq.jobs


def test_a_later_provider_failure_does_not_erase_accepted_spend(api, hq):
    put(api, "letters/settings", {"monthly_cap_micros": 2000000})
    first = send(api, quote(api))
    approve(api, first)
    accepted = fetch(api, first)
    api.app.state.letters.update(first["id"], {"letter_id": accepted["hq_letter_id"], "status": "failed"})
    second = quote(api)
    assert post(api, "letters/" + second["id"] + "/send", {}, expected=409)["error"]["code"] == "letters_cap"


def test_on_their_own_still_asks_for_new_address_and_certified(api, hq):
    put(api, "letters/settings", {"mode": "on_their_own"})
    first = send(api, quote(api))
    assert first["status"] == "waiting_for_ok"
    approve(api, first)
    assert send(api, quote(api))["status"] == "submitted"
    certified = send(api, quote(api, mail_class="certified"))
    assert certified["status"] == "waiting_for_ok" and hq.provider_submits == 2


def test_courts_and_unknown_triage_always_ask(api, hq):
    put(api, "letters/settings", {"mode": "on_their_own"})
    first = send(api, quote(api))
    approve(api, first)
    api.app.state.letters.engine = lambda *args: {
        "answers": {"recipient_kind": {"type": "choice", "choice": "court_agency", "confidence": 0.9}}
    }
    assert send(api, quote(api))["status"] == "waiting_for_ok"
    api.app.state.letters.engine = lambda *args: {"answers": {}}
    assert send(api, quote(api))["status"] == "waiting_for_ok"


def test_timeout_retry_keeps_the_claimed_quote_and_never_resubmits_provider(api, hq):
    row = send(api, quote(api))
    hq.timeout = True
    approve(api, row)
    pending = fetch(api, row)
    assert pending["status"] == "submitting" and pending["error"] == "gateway_unavailable"
    count = len(hq.quotes)
    assert send(api, row)["status"] == "submitted"
    assert hq.provider_submits == 1 and len(hq.quotes) == count
    submits = [(path, key) for path, key in hq.calls if key]
    assert len(submits) == 2 and submits[0] == submits[1]


def test_restart_recovers_the_saved_submission_claim_without_a_new_quote(api, hq):
    row = send(api, quote(api))
    hq.timeout = True
    approve(api, row)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE letters SET next_sync_at=? WHERE id=?", (H.now(), row["id"]))
    count = len(hq.quotes)
    restarted = L.Letters(api.app, api.app.state.store, api.app.state.auth, api.app.state.store.settings)
    restarted.drain()
    assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1 and len(hq.quotes) == count


def test_unstable_proof_stops_instead_of_repeatedly_requesting_approval(api, hq):
    row = send(api, quote(api))
    hq.proof = L.render("Different provider proof bytes for the same letter")
    approve(api, row)
    after = fetch(api, row)
    assert after["error"] == "unstable_proof" and after["approval_id"] == row["approval_id"] and not hq.jobs
    count = len(hq.calls)
    api.app.state.letters.drain()
    assert len(hq.calls) == count


def test_bot_quotes_an_uploaded_pdf_through_the_existing_library_path(api, hq):
    import base64

    ident, token = bot(api)
    grant(api, ident)
    failed, uploaded = call(
        api,
        "tico_file_publish",
        {
            "name": "letter.pdf",
            "content_base64": base64.b64encode(L.render("A letter uploaded by a bot")).decode(),
            "scope": "bot",
        },
        token=token,
    )
    assert not failed, uploaded
    row = post(api, "letters/quotes", {"file_id": uploaded["file"]["id"], "recipient": RECIPIENT}, token=token)
    assert row["page_count"] == 1 and row["proof"]


def test_unconfigured_is_coming_soon_and_does_not_poll_in_a_loop(api, hq):
    hq.unconfigured = True
    result = post(api, "letters/quotes", {"text": "Hello", "recipient": RECIPIENT}, expected=503)
    assert result["error"]["code"] == "unconfigured" and "Coming soon" in result["error"]["detail"]
    count = len(hq.calls)
    api.app.state.letters.drain()
    api.app.state.letters.drain()
    assert len(hq.calls) == count
    assert get(api, "letters/settings")["availability"] == "unconfigured"


def test_gateway_billing_and_cap_refusals_wait_for_an_explicit_retry(api, hq, monkeypatch):
    real = hq.__call__
    for code in ("billing_required", "cap_reached", "out_of_credit"):
        monkeypatch.setattr(G, "letters", real)
        row = send(api, quote(api))
        attempts = []

        def refused(key, base, path="", **kwargs):
            attempts.append(path)
            if path == "/quotes":
                raise G.LettersError(403, code)
            return real(key, base, path, **kwargs)

        monkeypatch.setattr(G, "letters", refused)
        approve(api, row)
        after = fetch(api, row)
        assert after["error"] == code and not hq.jobs
        calls = len(attempts)
        api.app.state.letters.drain()
        assert calls == 1 and len(attempts) == calls


def test_gateway_client_preserves_billing_required(monkeypatch):
    import io
    import urllib.error
    from types import SimpleNamespace

    def refused(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://gateway.acme.example", 403, "Forbidden", {}, io.BytesIO(b'{"error":"billing_required"}')
        )

    monkeypatch.setattr(G, "branded_opener", lambda *args: SimpleNamespace(open=refused))
    with pytest.raises(G.LettersError) as error:
        G.letters("tgw_test", "https://gateway.acme.example/v1/gateway", "/quotes", body={})
    assert (error.value.status, error.value.code) == (403, "billing_required")


def due(api, row):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE letters SET next_sync_at=? WHERE id=?", (H.now(), row["id"]))


def test_definite_refusal_releases_hold_and_never_blocks_the_drain(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    put(api, "letters/settings", {"monthly_cap_micros": 2000000})
    row = send(api, quote(api, token=token), token=token)
    hq.refusal = "out_of_credit"
    approve(api, row)
    refused = fetch(api, row)
    assert refused["status"] == "quoted" and refused["can_cancel"] and not hq.jobs
    with api.app.state.store.read() as c:
        saved = c.execute("SELECT * FROM letters WHERE id=?", (row["id"],)).fetchone()
        assert saved["submission_quote_id"] is None and saved["submitted_at"] is None
    put(api, "letters/settings", {"mode": "never"})
    hq.refusal = None
    count = len([path for path, _ in hq.calls if path == ""])
    due(api, row)
    api.app.state.letters.drain()
    assert len([path for path, _ in hq.calls if path == ""]) == count and hq.provider_submits == 0
    assert post(api, "letters/" + row["id"] + "/cancel", {})["status"] == "cancelled"


def test_refusal_then_revoke_does_not_post_and_retry_gets_new_quote_and_ok(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    row = send(api, quote(api, token=token), token=token)
    hq.refusal = "rate_limited"
    approve(api, row)
    api.delete("/api/v2/letters/outboxes/company/grants/bot/" + ident, headers=headers())
    hq.refusal = None
    count = len(hq.calls)
    due(api, row)
    api.app.state.letters.drain()
    assert len(hq.calls) == count and not hq.jobs
    grant(api, ident)
    again = send(api, row, token=token)
    assert again["quote_id"] != row["quote_id"] and again["approval_id"] != row["approval_id"]
    assert again["status"] == "waiting_for_ok" and not hq.jobs
    approve(api, again)
    assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1


def test_uncertain_claim_reauthorization_uses_only_resolve_after_never(api, hq):
    row = send(api, quote(api))
    hq.timeout = True
    approve(api, row)
    put(api, "letters/settings", {"mode": "never"})
    count = len([path for path, _ in hq.calls if path == ""])
    due(api, row)
    api.app.state.letters.drain()
    after = fetch(api, row)
    assert after["status"] == "submitted" and after["billing"] == "billed"
    assert len([path for path, _ in hq.calls if path == ""]) == count
    assert any(path.startswith("/keys/") for path, _ in hq.calls)


def test_old_hq_keeps_unauthorized_uncertain_claim_for_review(api, hq):
    ident, token = bot(api)
    grant(api, ident)
    row = send(api, quote(api, token=token), token=token)
    hq.timeout_before_claim = True
    approve(api, row)
    hq.resolve_supported = False
    api.delete("/api/v2/letters/outboxes/company/grants/bot/" + ident, headers=headers())
    count = len([path for path, _ in hq.calls if path == ""])
    due(api, row)
    api.app.state.letters.drain()
    assert fetch(api, row)["status"] == "needs_review"
    assert len([path for path, _ in hq.calls if path == ""]) == count and not hq.jobs
    with api.app.state.store.read() as c:
        saved = c.execute("SELECT * FROM letters WHERE id=?", (row["id"],)).fetchone()
        assert saved["submission_quote_id"] and saved["submitted_at"] and not saved["spend_released"]
    post(api, "letters/" + row["id"] + "/cancel", {}, expected=409)
    grant(api, ident)
    assert send(api, row, token=token)["status"] == "needs_review"
    assert len([path for path, _ in hq.calls if path == ""]) == count
    hq.resolve_supported = True
    assert post(api, "letters/" + row["id"] + "/cancel", {})["status"] == "cancelled"
    assert "tico-letter:" + row["id"] in hq.abandoned and hq.provider_submits == 0


def test_lowered_cap_reconciles_an_uncertain_claim_without_posting_again(api, hq):
    row = send(api, quote(api))
    hq.timeout = True
    approve(api, row)
    put(api, "letters/settings", {"monthly_cap_micros": 1000000})
    count = len([path for path, _ in hq.calls if path == ""])
    due(api, row)
    api.app.state.letters.drain()
    assert fetch(api, row)["status"] == "submitted"
    assert len([path for path, _ in hq.calls if path == ""]) == count


def test_refusal_on_a_retry_cannot_release_an_earlier_uncertain_claim(api, hq):
    row = send(api, quote(api))
    hq.timeout = True
    approve(api, row)
    quoted = len(hq.quotes)
    hq.refusal = "quote_changed"
    due(api, row)
    api.app.state.letters.drain()
    after = fetch(api, row)
    assert after["status"] == "submitted" and after["billing"] == "billed"
    assert len(hq.quotes) == quoted and hq.provider_submits == 1
    assert any(path.startswith("/keys/") for path, _ in hq.calls)


def test_abandoned_key_is_definitively_not_sent(api, hq):
    row = send(api, quote(api))
    hq.abandoned.add("tico-letter:" + row["id"])
    approve(api, row)
    after = fetch(api, row)
    assert after["status"] == "cancelled" and after["billing"] == "none" and after["charged_micros"] == 0
    assert hq.provider_submits == 0


@pytest.mark.parametrize("left", ["approved", "submitting"])
def test_older_letters_still_waiting_for_an_ok_never_starve_the_drain(api, hq, monkeypatch, left):
    """A hundred older letters waiting for their OK, and a newer one the drain must reach: approved while the send
    after the OK was lost (a restart), or claimed with HQ's answer lost."""
    older = send(api, quote(api))
    row = send(api, quote(api))
    with api.app.state.store.transaction() as c:
        names = [r[1] for r in c.execute("PRAGMA table_info(letters)") if r[1] not in ("id", "updated_at")]
        for n in range(120):
            c.execute(
                f"INSERT INTO letters (id, updated_at, {','.join(names)}) "
                f"SELECT ?, '2000-01-01T00:00:{n % 60:02d}Z', {','.join(names)} FROM letters WHERE id=?",
                (f"waiting-{n}", older["id"]),
            )
    if left == "approved":
        monkeypatch.setattr(api.app.state.letters, "decided", lambda rid: None)
    else:
        hq.timeout = True
    approve(api, row)
    assert fetch(api, row)["status"] == ("waiting_for_ok" if left == "approved" else "submitting")
    due(api, row)
    api.app.state.letters.drain()
    assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1


def _ended(api, task_id):
    """The status of the task an OK filed, and the words it ended with (its last history line), if it ended."""
    with api.app.state.store.read() as c:
        status = H.task(c, task_id)["status"]
        note = c.execute(
            "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND note IS NOT NULL AND note<>'' "
            "ORDER BY rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    return status, note[0] if note else ""


def test_the_oks_task_ends_done_only_once_hq_has_the_letter(api, hq):
    """Accepted: done, "Mailed the letter." Unknown (a timeout): the task stays open while the claim is reconciled,
    and ends done when HQ says it has the letter."""
    row = send(api, quote(api))
    task_id = _task(api, row)
    hq.timeout = True
    approve(api, row)
    assert fetch(api, row)["status"] == "submitting" and _ended(api, task_id)[0] == "open"
    due(api, row)
    api.app.state.letters.drain()
    assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1
    assert _ended(api, task_id) == ("done", "Mailed the letter.")


@pytest.mark.parametrize("refusal", ["out_of_credit", "quote_changed"])
def test_a_letter_hq_refused_is_never_recorded_as_mailed(api, hq, refusal):
    """A definite refusal before HQ claimed the send: the OK's task ends closed, saying the letter was not mailed, and
    the OK it used lets nothing else go. A new quote needs a new OK (quote_changed asks it at once)."""
    row = send(api, quote(api))
    task_id = _task(api, row)
    hq.refusal = refusal
    approve(api, row)
    after = fetch(api, row)
    assert hq.provider_submits == 0 and after["status"] in ("quoted", "waiting_for_ok")
    status, note = _ended(api, task_id)
    assert status == "closed" and note.startswith("Not mailed: "), (status, note)
    assert asked(api, row)["used_at"]
    if refusal == "quote_changed":
        assert after["status"] == "waiting_for_ok" and after["approval_id"] != row["approval_id"]
        hq.refusal = None
        approve(api, after)
        assert fetch(api, row)["status"] == "submitted" and hq.provider_submits == 1
        assert _ended(api, _task(api, after)) == ("done", "Mailed the letter.")


@pytest.mark.parametrize(("later", "ends"), [("mailed", "done"), ("failed", "closed")])
def test_a_letter_hq_takes_as_submitting_ends_its_task_on_hqs_first_decided_word(api, hq, monkeypatch, later, ends):
    """HQ names the letter but says only `submitting`: undecided, the task stays open. Its next word decides it, though
    the letter's HQ id is already recorded: done once mailed, closed "Not mailed" once failed."""

    def answer(key, base, path="", **kwargs):
        result = hq(key, base, path, **kwargs)
        if isinstance(result, dict) and result.get("letter_id"):
            result = {**result, "status": "submitting" if not path else later}
        return result

    monkeypatch.setattr(G, "letters", answer)
    row = send(api, quote(api))
    task_id = _task(api, row)
    approve(api, row)
    after = fetch(api, row)
    assert after["status"] == "submitting" and after["hq_letter_id"] and _ended(api, task_id)[0] == "open"
    due(api, row)
    api.app.state.letters.drain()
    assert fetch(api, row)["status"] == later and hq.provider_submits == 1
    status, note = _ended(api, task_id)
    if ends == "done":
        assert (status, note) == ("done", "Mailed the letter.")
    else:
        assert (status, note) == (
            "closed",
            "Not mailed: Letters out could not mail it. Nothing else goes out under this OK.",
        )
    due(api, row)
    api.app.state.letters.drain()
    assert _ended(api, task_id) == (status, note)


def test_an_abandoned_send_ends_the_oks_task_as_not_mailed(api, hq):
    row = send(api, quote(api))
    task_id = _task(api, row)
    hq.abandoned.add("tico-letter:" + row["id"])
    approve(api, row)
    assert fetch(api, row)["status"] == "cancelled"
    status, note = _ended(api, task_id)
    assert status == "closed" and note.startswith("Not mailed: "), (status, note)


@pytest.mark.parametrize("legacy", [False, True])
def test_lost_acceptance_then_failed_keeps_company_and_bot_spend(api, hq, legacy):
    ident, token = bot(api)
    grant(api, ident)
    put(api, "letters/settings", {"monthly_cap_micros": 2000000})
    put(api, "letters/bots/" + ident + "/settings", {"monthly_cap_micros": 2000000})
    first = send(api, quote(api, token=token), token=token)
    hq.timeout = True
    approve(api, first)
    job = hq.jobs["tico-letter:" + first["id"]]
    job["status"] = "failed"
    if legacy:
        for key in ("accepted_at", "billing", "charged_micros"):
            job.pop(key)
    due(api, first)
    api.app.state.letters.drain()
    assert fetch(api, first)["status"] == "failed"
    if not legacy:
        with api.app.state.store.read() as c:
            saved = c.execute("SELECT * FROM letters WHERE id=?", (first["id"],)).fetchone()
            assert saved["accepted_at"] == job["accepted_at"] and saved["charged_micros"] == 1500000
            assert saved["billing"] == "billed"
    second = quote(api, token=token)
    post(api, "letters/" + second["id"] + "/send", {}, token=token, expected=409)
    put(api, "letters/settings", {"monthly_cap_micros": None})
    post(api, "letters/" + second["id"] + "/send", {}, token=token, expected=409)
    assert hq.provider_submits == 1


def test_only_authoritative_void_or_reversal_releases_a_terminal_charge(api, hq):
    put(api, "letters/settings", {"monthly_cap_micros": 2000000})
    first = send(api, quote(api))
    approve(api, first)
    job = hq.jobs["tico-letter:" + first["id"]]
    service = api.app.state.letters
    service.update(first["id"], {**job, "status": "cancelled", "billing": "hold", "charged_micros": 0})
    second = quote(api)
    post(api, "letters/" + second["id"] + "/send", {}, expected=409)
    service.update(first["id"], {**job, "status": "cancelled", "billing": "reversed", "charged_micros": 0})
    assert send(api, second)["status"] == "waiting_for_ok"


def test_claim_and_approval_are_durable_before_approved_and_autonomous_post(api, hq, monkeypatch):
    from contextlib import contextmanager

    store = api.app.state.store
    transaction, real_hq = store.transaction, hq.__call__
    committed, sent = [], []

    @contextmanager
    def traced(durable=False):
        with transaction(durable=durable) as c:
            yield c
            claims = c.execute(
                "SELECT id,submission_quote_id,approval_id,approved_hash FROM letters WHERE status='submitting'"
            ).fetchall()
            found = (bool(durable), c.execute("PRAGMA synchronous").fetchone()[0]) if claims else None
        if found:
            committed.append(found)

    def boundary(key, base, path="", **kwargs):
        if path == "" and kwargs.get("body"):
            assert committed[-1] == (True, 2)
            with store.read() as c:
                row = c.execute(
                    "SELECT * FROM letters WHERE submission_quote_id=?", (kwargs["body"]["quote_id"],)
                ).fetchone()
                assert kwargs["idempotency_key"] == "tico-letter:" + row["id"]
                if row["approved_hash"]:
                    assert task_asks.standing(c, row["approval_id"]) == task_asks.USED
                sent.append(bool(row["approved_hash"]))
        return real_hq(key, base, path, **kwargs)

    monkeypatch.setattr(store, "transaction", traced)
    monkeypatch.setattr(G, "letters", boundary)
    put(api, "letters/settings", {"mode": "on_their_own"})
    first = send(api, quote(api))
    approve(api, first)
    assert send(api, quote(api))["status"] == "submitted"
    assert sent == [True, False]


def test_bot_policy_partial_updates_preserve_omissions_and_clear_explicit_null(api, hq):
    ident, _ = bot(api)
    path = "letters/bots/" + ident + "/settings"
    put(api, path, {"mode": "never", "monthly_cap_micros": 1000000})
    assert put(api, path, {"monthly_cap_micros": 2000000})["mode"] == "never"
    assert put(api, path, {"mode": "ask_first"})["monthly_cap_micros"] == 2000000
    assert put(api, path, {"mode": None})["monthly_cap_micros"] == 2000000
    assert get(api, path)["mode"] is None
    put(api, path, {"mode": "never"})
    assert put(api, path, {"monthly_cap_micros": None})["mode"] == "never"
    assert get(api, path)["monthly_cap_micros"] is None


def test_truncated_four_page_pdf_always_asks_even_for_a_known_address(api, hq):
    import base64
    import io
    from reportlab.pdfgen import canvas

    ident, token = bot(api)
    grant(api, ident)
    put(api, "letters/settings", {"mode": "on_their_own"})
    first = send(api, quote(api, token=token), token=token)
    approve(api, first)
    output = io.BytesIO()
    pdf = canvas.Canvas(output, invariant=1)
    for _ in range(3):
        text = pdf.beginText(50, 740)
        for _ in range(80):
            text.textLine("Benign marketing copy for Acme. " * 5)
        pdf.drawText(text)
        pdf.showPage()
    pdf.drawString(50, 740, "Court filing: a letter to the court concerning a pending agency proceeding.")
    pdf.save()
    data = output.getvalue()
    sampled, complete = L.pdf_text(data)
    assert not complete and "Court filing" not in sampled
    failed, uploaded = call(
        api,
        "tico_file_publish",
        {"name": "letter.pdf", "content_base64": base64.b64encode(data).decode(), "scope": "bot"},
        token=token,
    )
    assert not failed, uploaded
    row = post(api, "letters/quotes", {"file_id": uploaded["file"]["id"], "recipient": RECIPIENT}, token=token)
    requested = send(api, row, token=token)
    assert requested["status"] == "waiting_for_ok" and requested["approval_reason"]
    assert asked(api, requested)["content"]["reason"] == requested["approval_reason"]
    assert hq.provider_submits == 1


def test_sparse_or_failed_pdf_extraction_is_incomplete(monkeypatch):
    from types import SimpleNamespace
    import pypdf

    def broken():
        raise ValueError("unreadable text stream")

    for extract in (lambda: "", lambda: "Short", broken):
        monkeypatch.setattr(
            pypdf,
            "PdfReader",
            lambda data: SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=extract)]),
        )
        assert L.pdf_text(b"%PDF-1.7\nfixture")[1] is False


def test_delegated_reader_needs_its_own_letter_permission_on_every_surface(api, hq):
    from backend import events, task_privacy
    from backend.tests.helpers.api import setup_attempt
    from backend.tests.helpers.assistant import assistant_turn

    token = setup_attempt(api, "finance")[2]["token"]
    grant(api, "finance")
    row = send(api, quote(api, token=token), token=token)
    assert get(api, "letters/" + row["id"])["recipient"]["name"] == RECIPIENT["name"]
    _, attempt = assistant_turn(api, "ana-test")
    delegated = attempt["token"]
    assert get(api, "me", delegated)["actor"] == "human:ana"
    assert get(api, "letters", delegated)["items"] == []
    get(api, "letters/" + row["id"], delegated, expected=404)
    get(api, row["proof"].removeprefix("/api/v2/"), delegated, expected=403)
    assert call(api, "tico_letter_list", {}, token=delegated)[1]["items"] == []
    with api.app.state.store.read() as c:
        source = c.execute("SELECT source_blob_id FROM letters WHERE id=?", (row["id"],)).fetchone()[0]
        who = api.app.state.auth.identity_for_actor(c, "human:ana")
        from dataclasses import replace

        who = replace(who, task_actor="bot:assistant")
        assert not task_privacy.blob_readable(c, task_privacy.actor(who), source)
    with api.app.state.store.read() as c:
        assert events.read(c, api.app.state.auth, who, 0, topics=("letters",))[0] == []
    get(api, "files/" + source, delegated, expected=403)


def test_a_delegated_reader_reads_the_letters_ok_task_but_never_the_letter(api, hq):
    """The Assistant reads a task that holds an OK asked of its person (task_asks.assistant_reads_own_ok), so it can
    send it back on her word. With no letter permission of its own it reads only what the OK shows (the addresses,
    the price, the settings and the hash it binds): never the letter's text, its proof or its source."""
    from backend.tests.helpers.api import setup_attempt
    from backend.tests.helpers.assistant import assistant_turn

    token = setup_attempt(api, "finance")[2]["token"]
    grant(api, "finance")
    row = send(api, quote(api, token=token), token=token)
    with api.app.state.store.read() as c:
        letter = dict(c.execute("SELECT * FROM letters WHERE id=?", (row["id"],)).fetchone())
        blobs = [letter[k] for k in ("source_blob_id", "proof_blob_id")]
        digests = [c.execute("SELECT digest FROM blobs WHERE id=?", (b,)).fetchone()[0] for b in blobs]
    _, attempt = assistant_turn(api, "ana-test")
    delegated = attempt["token"]
    task = json.dumps(get(api, "tasks/" + _task(api, row), delegated))
    for secret in ["Hello from Acme", row["id"], RECIPIENT["name"], RECIPIENT["line1"], *blobs, *digests]:
        assert secret not in task, secret
    shown = get(api, "oks/" + row["approval_id"], delegated)
    assert shown["content"] == L.content(letter)
    assert "Hello from Acme" not in json.dumps(shown)
    assert not any(b in json.dumps(shown) for b in blobs + digests)
    get(api, "letters/" + row["id"], delegated, expected=404)
    get(api, row["proof"].removeprefix("/api/v2/"), delegated, expected=403)
    get(api, "files/" + letter["source_blob_id"], delegated, expected=403)


def test_private_source_keeps_only_admin_metadata_and_fences_both_copies(api, hq):
    from types import SimpleNamespace
    from backend import blobs, events, task_privacy

    data = L.render("A complete source letter with enough searchable text for the recipient review.")
    digest = api.app.state.blobs.put(data, "application/pdf")
    with api.app.state.store.transaction() as c:
        task = H.task_create(
            c, "human:cara", "Review the source letter", "Read the packet.", "human:cara", private=False, lint=False
        )
        original = blobs.register(
            c, SimpleNamespace(actor="human:ana"), digest, len(data), "packet.pdf", "application/pdf"
        )
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (task["id"], original["id"]))
    row = post(api, "letters/quotes", {"file_id": original["id"], "recipient": RECIPIENT})
    row = send(api, row)
    approve(api, row)
    receipt = fetch(api, row)
    assert api.get(row["proof"], headers=headers()).status_code == 200
    task = get(api, "tasks/" + task["id"], token="cara-test")["task"]
    post(api, "tasks/" + task["id"], {"version": task["version"], "private": True}, token="cara-test")
    with api.app.state.store.read() as c:
        before = events.latest(c)
        stored = c.execute("SELECT * FROM letters WHERE id=?", (row["id"],)).fetchone()
        source = stored["source_blob_id"]
        assert stored["original_blob_id"] == original["id"] and stored["task_id"] == task["id"]
        assert task["id"] in json.loads(stored["source_provenance"])["tasks"]
    for token in ("ana-test", "ben-test"):
        listed = next(item for item in get(api, "letters", token)["items"] if item["id"] == row["id"])
        assert listed["redacted"] and listed["total_micros"] == 1500000
        assert not {"recipient", "return_address", "proof", "task_id", "thread_id", "requested_by"}.intersection(listed)
        assert get(api, "letters/" + row["id"], token)["redacted"]
        get(api, "files/" + source, token, expected=403)
        get(api, row["proof"].removeprefix("/api/v2/"), token, expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_assets WHERE blob_id=?", (original["id"],))
    get(api, "files/" + source, expected=403)
    api.app.state.letters.update(
        row["id"],
        {"letter_id": receipt["hq_letter_id"], "status": "mailed", "billing": "billed", "charged_micros": 1500000},
    )
    with api.app.state.store.read() as c:
        who = api.app.state.auth.identity_for_actor(c, "human:ana")
        changed = events.read(c, api.app.state.auth, who, before, topics=("letters",))[0]
        assert changed and all(event[2]["letter"]["redacted"] for event in changed)
        assert not task_privacy.blob_readable(c, "human:ana", source)
    cancelled = post(api, "letters/" + row["id"] + "/cancel", {}, token="ben-test")
    assert cancelled["status"] == "cancelled" and cancelled["redacted"]


def external_thread_letter(api, audience, *, explicit=False):
    import base64
    from backend.tests.threads_e2e import create_thread

    ident, token = bot(api)
    grant(api, ident)
    group = post(api, "groups", {"name": "Marketing", "add": {"bots": [ident]}}) if audience == "team" else None
    thread = create_thread(
        api, title="Letter source", audience=audience, group=group["id"] if group else None, members=["bot:" + ident]
    )
    failed, uploaded = call(
        api,
        "tico_file_publish",
        {
            "name": "letter.pdf",
            "scope": "bot",
            "content_base64": base64.b64encode(
                L.render("A complete source letter with searchable text for the recipient review.")
            ).decode(),
        },
        token=token,
    )
    assert not failed, uploaded
    message = post(api, "threads/" + thread["id"] + "/messages", {"text": "The letter PDF."}, token=token)
    with api.app.state.store.transaction() as c:
        original = c.execute(
            "SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=1", (uploaded["file"]["id"],)
        ).fetchone()[0]
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES(?,?)", (message["id"], original))
    fields = {"thread_id": thread["id"]} if explicit else {}
    row = post(
        api, "letters/quotes", {"file_id": uploaded["file"]["id"], "recipient": RECIPIENT, **fields}, token=token
    )
    return ident, token, thread, row


@pytest.mark.parametrize("audience", ["company", "team"])
def test_external_sender_losing_an_inferred_source_thread_gets_only_metadata(api, hq, audience):
    ident, token, thread, row = external_thread_letter(api, audience)
    assert not row["redacted"] and row["thread_id"] is None
    with api.app.state.store.read() as c:
        saved = c.execute("SELECT source_blob_id,source_provenance FROM letters WHERE id=?", (row["id"],)).fetchone()
        source = saved["source_blob_id"]
        assert thread["id"] in json.loads(saved["source_provenance"])["threads"]
    assert api.get(row["proof"], headers=headers(token)).status_code == 200
    if audience == "company":
        from backend import bot_settings
        from starlette.datastructures import Headers

        with api.app.state.store.transaction() as c:
            bot_settings.update(c, ident, {"harness": "codex", "runtime": "codex"})
        # A profile edit does not turn the still-active standing credential into a runner lease.
        assert api.app.state.auth.authenticate(Headers(headers(token)), "/api/v2/letters", "GET").agent == "hermes"
    removed = api.delete("/api/v2/threads/" + thread["id"] + "/members/bot:" + ident, headers=headers())
    assert removed.status_code == 200 and removed.json()["removed"]
    get(api, "threads/" + thread["id"], token, expected=404)
    assert fetch(api, row, token)["redacted"]
    listed = next(item for item in get(api, "letters", token)["items"] if item["id"] == row["id"])
    assert listed["redacted"] and not {"recipient", "return_address", "proof"}.intersection(listed)
    get(api, "files/" + source, token, expected=403)
    get(api, row["proof"].removeprefix("/api/v2/"), token, expected=403)


@pytest.mark.parametrize("phase,audience", [("queued", "company"), ("uncertain", "team")])
def test_external_deferred_send_cannot_gain_internal_thread_standing(api, hq, phase, audience):
    ident, token, thread, row = external_thread_letter(api, audience, explicit=True)
    row = send(api, row, token)
    if phase == "uncertain":
        hq.timeout_before_claim, hq.resolve_supported = True, False
        approve(api, row)
        assert fetch(api, row)["status"] == "submitting"
    removed = api.delete("/api/v2/threads/" + thread["id"] + "/members/bot:" + ident, headers=headers())
    assert removed.status_code == 200
    count = len([path for path, _ in hq.calls if path == ""])
    if phase == "queued":
        approve(api, row)
        assert fetch(api, row)["error"] == "forbidden"
    else:
        due(api, row)
        api.app.state.letters.drain()
        assert fetch(api, row)["status"] == "needs_review"
        assert any(path.startswith("/keys/") for path, _ in hq.calls)
    assert len([path for path, _ in hq.calls if path == ""]) == count and hq.provider_submits == 0


def test_revoked_external_identity_stops_deferred_send_instead_of_becoming_internal(api, hq):
    ident, token, _, row = external_thread_letter(api, "company", explicit=True)
    row = send(api, row, token)
    post(api, "bots/" + ident + "/agent-credential/revoke", {})
    approve(api, row)
    assert fetch(api, row)["error"] == "identity" and hq.provider_submits == 0
    with api.app.state.store.read() as c:
        assert c.execute("SELECT next_sync_at FROM letters WHERE id=?", (row["id"],)).fetchone()[0] is None
        from backend.auth import bot_identity
        from backend.store import Problem

        with pytest.raises(Problem):
            bot_identity(c, "bot:no-longer-registered")


def test_real_source_task_privacy_and_party_changes_patch_finished_letter_rows(api, hq):
    from backend import events

    task = post(
        api,
        "tasks",
        {"owner": "human:cara", "title": "Review source", "body": "Source packet.", "private": False},
        token="cara-test",
    )
    variants = [
        ({"task_id": task["id"]}, {}),
        ({}, {"tasks": [task["id"]]}),
        ({"thread_id": task["conversation_id"]}, {}),
        ({}, {"threads": [task["conversation_id"]]}),
    ]
    rows = [quote(api) for _ in variants]
    unrelated = quote(api)
    for index, (row, (columns, frozen)) in enumerate(zip(rows, variants)):
        api.app.state.letters.update(
            row["id"],
            {
                "letter_id": "lt_finished_" + str(index),
                "status": "delivered",
                "billing": "billed",
                "charged_micros": 1500000,
            },
        )
        with api.app.state.store.transaction() as c:
            c.execute(
                "UPDATE letters SET task_id=?,thread_id=?,source_provenance=? WHERE id=?",
                (columns.get("task_id"), columns.get("thread_id"), json.dumps(frozen), row["id"]),
            )
            L.freeze_sources(c, row["id"])
    assert all(not fetch(api, row)["redacted"] for row in rows)
    with api.app.state.store.read() as c:
        before = events.latest(c)
        originals = {r["id"]: r["updated_at"] for r in c.execute("SELECT id,updated_at FROM letters")}
    post(api, "tasks/" + task["id"], {"version": task["version"], "private": True}, token="cara-test")
    owner = api.app.state.auth.settings.test_identities["ana-test"]
    with api.app.state.store.read() as c:
        changes = events.read(c, api.app.state.auth, owner, before, topics=("letters",))[0]
        assert all(r["updated_at"] == originals[r["id"]] for r in c.execute("SELECT id,updated_at FROM letters"))
        assert (
            c.execute("SELECT count(*) FROM letters WHERE status='delivered' AND next_sync_at IS NULL").fetchone()[0]
            == 4
        )
    patched = {event[2]["id"]: event[2]["letter"] for event in changes}
    assert set(patched) == {row["id"] for row in rows} and unrelated["id"] not in patched
    assert all(
        row["redacted"] and not {"recipient", "return_address", "proof"}.intersection(row) for row in patched.values()
    )
    # Changing a private task's parties can also restore access, without touching the letter or polling a list.
    task = get(api, "tasks/" + task["id"], "cara-test")["task"]
    with api.app.state.store.read() as c:
        before = events.latest(c)
    post(api, "tasks/" + task["id"] + "/owner", {"version": task["version"], "owner": "human:ben"}, token="cara-test")
    with api.app.state.store.read() as c:
        ben = api.app.state.auth.settings.test_identities["ben-test"]
        restored = events.read(c, api.app.state.auth, ben, before, topics=("letters",))[0]
    assert {event[2]["id"] for event in restored} == set(patched)
    assert all(not event[2]["letter"]["redacted"] for event in restored)


def test_status_sync_patches_one_letter_and_emits_authorized_live_change(api, hq):
    from backend import events

    row = send(api, quote(api))
    approve(api, row)
    with api.app.state.store.transaction() as c:
        before = events.latest(c)
        c.execute("UPDATE letters SET next_sync_at=? WHERE id=?", (H.now(), row["id"]))
    api.app.state.letters.drain()
    after = fetch(api, row)
    assert after["status"] == "mailed" and after["tracking"] == [{"barcode": "1234", "status": "mailed"}]
    with api.app.state.store.read() as c:
        owner = api.app.state.auth.identity_for_actor(c, "human:ana")
        stranger = api.app.state.auth.identity_for_actor(c, "human:cara")
        changes = events.read(c, api.app.state.auth, owner, before, topics=("letters",))[0]
        hidden = events.read(c, api.app.state.auth, stranger, before, topics=("letters",))[0]
    assert changes[0][2]["letter"]["status"] == "mailed" and hidden == []


def test_approved_proof_is_local_and_follows_the_record_permissions(api, hq):
    row = send(api, quote(api))
    approve(api, row)
    assert api.get(row["proof"], headers=headers()).content == hq.proof
    assert api.get(row["proof"], headers=headers("cara-test")).status_code in (403, 404)
    get(api, "letters/" + row["id"], token="cara-test", expected=404)
    assert get(api, "letters", token="cara-test")["items"] == []
    with api.app.state.store.read() as c:
        source = c.execute("SELECT source_blob_id FROM letters WHERE id=?", (row["id"],)).fetchone()[0]
        assert c.execute("SELECT count(*) FROM letter_proofs WHERE letter_id=?", (row["id"],)).fetchone()[0] == 2
    assert api.get("/api/v2/files/" + source, headers=headers("cara-test")).status_code in (403, 404)


def test_decline_and_cancel_close_unsent_letters_and_provider_refusal_passes_through(api, hq):
    row = send(api, quote(api))
    path = f"tasks/{_task(api, row)}/asks/{row['approval_id']}/answer"
    post(api, path, {"value": "no", "note": "Wrong address."})
    api.app.state.letters.drain()
    assert fetch(api, row)["status"] == "declined" and not hq.jobs
    pending = send(api, quote(api))
    assert post(api, "letters/" + pending["id"] + "/cancel", {})["status"] == "cancelled"
    row = send(api, quote(api))
    approve(api, row)
    hq.no_cancel = True
    error = post(api, "letters/" + row["id"] + "/cancel", {}, expected=409)
    assert error["error"]["code"] == "cannot_cancel" and fetch(api, row)["status"] == "submitted"


def test_sql_provenance_sources_do_not_expose_letter_or_agent_tables(api, hq):
    quote(api)
    for table in ("letters", "letter_proofs", "letter_outbox_grants", "agents"):
        result = post(api, "sql", {"sql": "SELECT * FROM " + table}, expected=422)
        assert result["error"]["code"] == "sql"
        result = post(api, "sql", {"sql": "SELECT * FROM main." + table}, expected=422)
        assert result["error"]["code"] == "sql"


def test_renderer_is_deterministic_us_letter_and_does_not_execute_markup():
    from pypdf import PdfReader

    data = L.render("# Heading\n\nHello <script>world</script>.\n\n" + "word " * 2000)
    assert data == L.render("# Heading\n\nHello <script>world</script>.\n\n" + "word " * 2000)
    reader = PdfReader(__import__("io").BytesIO(data))
    assert len(reader.pages) > 1 and tuple(reader.pages[0].mediabox[2:]) == (612, 792)
    assert "<script>world</script>" in reader.pages[0].extract_text()
