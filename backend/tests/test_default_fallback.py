"""A bot out of quota with no fallback of its own runs on another provider: the company setting that sends the
runner its choices, and the Overview line (GET /api/v2/health `on_fallback`, `limited`)."""
from backend.store import H
from backend.tests.test_api import api, claim, get, post, setup_attempt  # noqa: F401


def save_providers(api, **extra):
    view = get(api, "providers")
    return api.put("/api/v2/providers", json={"enabled": ["openai", "anthropic"], "model": view["default"]["model"] or "",
                                               "expected_revision": view["revision"], **extra},
                   headers={"Authorization": "Bearer ana-test", "Idempotency-Key": H.new_id()})


def test_the_runner_hears_one_model_per_provider_until_the_owner_turns_it_off(api):
    assert save_providers(api).status_code == 200
    view = get(api, "providers")
    assert view["auto_fallback"] is True
    models = get(api, "config")["fallback_models"]
    assert [row["runtime"] for row in models] == ["codex", "claude"]
    assert all(row["model"] and row["harness"] == row["runtime"] for row in models)

    assert save_providers(api, auto_fallback=False).json()["auto_fallback"] is False
    assert get(api, "config")["fallback_models"] == []
    # A save that does not mention it (the first-run form) keeps the owner's choice.
    assert save_providers(api).json()["auto_fallback"] is False
    with api.app.state.store.read() as c:
        event = c.execute("SELECT detail FROM events WHERE action='providers.updated' ORDER BY ts DESC LIMIT 1").fetchone()
    assert '"auto_fallback": false' in event[0]


def test_overview_counts_bots_on_fallback_and_limited_ones_for_who_may_read_them(api):
    machine, _, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "limited"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "failed", "last_seq": 0, "limited": True, "text": "monthly spend limit"}, machine["token"])
    health = get(api, "health")
    assert [row["bot"] for row in health["limited"]] == ["ops"]
    assert health["on_fallback"] == []

    # A person's message is worth one try: the turn hits the limit again and runs on Codex instead.
    post(api, "chat/ops", {"text": "Try again please"})
    again = claim(api, machine)
    post(api, f"attempts/{again['id']}/started", {"thread_id": "fallback"}, machine["token"])
    post(api, f"attempts/{again['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "fallback": "codex", "primary_limited": True, "text": "Done on Codex."},
         machine["token"])
    health = get(api, "health")
    assert health["limited"] == []
    assert [(row["bot"], row["harness"]) for row in health["on_fallback"]] == [("ops", "codex")]
    with api.app.state.store.read() as c:
        began = c.execute("SELECT coalesce(started_at,created_at) FROM attempts WHERE id=?", (again["id"],)).fetchone()[0]
        detail = c.execute("SELECT detail FROM events WHERE action='attempt.fallback' AND target=?",
                           (again["id"],)).fetchone()[0]
    assert health["on_fallback"][0]["since"] == began
    assert '"primary_limited": true' in detail

    # Someone who cannot read the bot hears nothing about it; a reader does.
    assert [row["bot"] for row in get(api, "health", token="cara-test")["on_fallback"]] == ["ops"]
    access = get(api, "bots/ops/access")
    only_ana = {"people": ["ana"]}
    saved = api.put("/api/v2/bots/ops/access", json={"see": only_ana, "read": only_ana, "write": only_ana,
                                                     "revision": access["revision"]},
                    headers={"Authorization": "Bearer ana-test", "Idempotency-Key": H.new_id()})
    assert saved.status_code == 200, saved.text
    hidden = get(api, "health", token="cara-test")
    assert hidden["on_fallback"] == [] and hidden["limited"] == []


def test_a_run_back_on_the_bots_own_provider_ends_the_fallback(api):
    machine, _, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fallback"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "fallback": "codex", "primary_limited": True, "text": "Done."}, machine["token"])
    assert [row["bot"] for row in get(api, "health")["on_fallback"]] == ["ops"]
    post(api, "chat/ops", {"text": "And one more"})
    later = claim(api, machine)
    post(api, f"attempts/{later['id']}/started", {"thread_id": "own"}, machine["token"])
    post(api, f"attempts/{later['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Back on Claude."},
         machine["token"])
    assert get(api, "health")["on_fallback"] == []
