"""The provider choice (backend/providers.py): stored, revisioned, seeded from the environment,
and the only thing that decides a bot's runtime and model when the bot names none."""

import pytest

from backend import providers as P
from backend import bot_settings as BS


def test_resolution_order_bot_then_company_then_first_provider_then_error():
    none = P._record({})
    company = P._record({"enabled": ["anthropic", "openai"], "runtime": "codex", "model": "gpt-6-sol"})
    assert P.resolve(company, {"runtime": "claude", "model": "claude-opus-5"}) == ("claude", "claude-opus-5")
    assert P.resolve(company, {"model": "claude-opus-5-5"}) == ("claude", "claude-opus-5-5")
    assert P.resolve(company, {"runtime": "default", "model": "default"}) == ("codex", "gpt-6-sol")
    assert P.resolve(company, {"runtime": "claude"}) == ("claude", "claude-opus-5-5")
    first = P._record({"enabled": ["google", "openai"]})
    assert P.resolve(first, {}) == ("antigravity", "gemini-4-neon")
    assert P.fill(first, {}) == {"runtime": "antigravity", "model": "gemini-4-neon"}
    # Google serves two runtimes: a bot on the Gemini CLI gets a model the Gemini CLI runs.
    assert P.fill(first, {"runtime": "gemini", "harness": "gemini"}) == {
        "runtime": "gemini",
        "model": "gemini-3.8-flash",
        "harness": "gemini",
    }
    assert P.default_fallbacks(first)[0] == {
        "runtime": "antigravity",
        "harness": "antigravity",
        "model": "gemini-4-neon",
    }
    with pytest.raises(P.NoProvider) as refused:
        P.resolve(none, {})
    assert "Settings > Computers" in refused.value.detail and refused.value.status == 409


def test_a_providers_other_cli_never_becomes_the_computers_first_runtime():
    # Google also serves Antigravity, which sorts first by name but is only Google's second CLI.
    assert P.default_runtime(P._record({"enabled": ["anthropic", "google"]})) == "claude"
    assert P.default_runtime(P._record({"enabled": ["google"]})) == "gemini"
    assert P.default_runtime(P._record({"enabled": ["google"], "runtime": "antigravity"})) == "antigravity"


def test_recommended_is_worked_out_once_and_answers_as_the_catalog_does(monkeypatch):
    # Pages ask resolve (and so recommended) of every bot: the answer is kept per provider, and is the catalog's own row.
    monkeypatch.setattr(P, "_RECOMMENDED", {})
    scans = []
    scan = P.provider_models
    monkeypatch.setattr(P, "provider_models", lambda provider, **k: scans.append(provider) or scan(provider, **k))
    for provider in [*P.PROVIDER_BY_ID, "unknown", ""]:
        rows = scan(provider)
        expected = next((row for row in rows if row.get("recommended")), rows[0] if rows else None)
        assert P.recommended(provider) is expected
        assert P.recommended(provider) is expected
    # One scan per known provider; an id the catalog does not know is scanned each time and never kept.
    assert scans == [*P.PROVIDER_BY_ID, "unknown", "unknown", "", ""]
    assert set(P._RECOMMENDED) == set(P.PROVIDER_BY_ID)


def test_a_claimed_attempt_carries_the_company_default_for_a_bot_that_names_none(api):
    """Found by the Linux rehearsal: onboarding creates bots that follow the company default, and
    the runner got an empty runtime from the claim, so no turn ever started."""
    from backend.store import encode
    from backend.tests.helpers.api import claim, post, put, runner, assign, ready

    put(api, "providers", {"enabled": ["openai"], "runtime": "codex", "model": "gpt-6-luna", "expected_revision": 0})
    with api.app.state.store.transaction() as c:
        BS.write(c, 'ops', {'name': 'ops', 'status': 'active'})
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Say hello."})
    config = claim(api, r)["config"]
    assert (config["runtime"], config["model"]) == ("codex", "gpt-6-luna")


def test_a_bot_set_to_haiku_5_5_at_xhigh_runs_that_model_at_that_effort(api):
    """A Settings model change is what the runner's next claim carries: `--model` and `--effort` come from it."""
    from backend.tests.helpers.api import claim, post, put, runner, assign, ready

    put(
        api,
        "providers",
        {"enabled": ["anthropic"], "runtime": "claude", "model": "claude-opus-5-5", "expected_revision": 0},
    )
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    post(
        api,
        "bots/ops/transitions",
        {"kind": "model", "model": "claude-haiku-5-5", "effort": "xhigh", "expected_revision": revision},
    )
    post(api, "chat/ops", {"text": "Say hello."})
    config = claim(api, r)["config"]
    assert (config["runtime"], config["model"], config["reasoning_effort"]) == ("claude", "claude-haiku-5-5", "xhigh")
    assert P.price_of("claude-haiku-5-5") == (0.10, 0.01, 0.50)


@pytest.mark.parametrize("model,runtime", [("gpt-6-sol", "codex")])
def test_retired_models_remain_readable_but_cannot_be_new_choices(model, runtime):
    company = P._record({"enabled": ["openai", "anthropic"], "runtime": runtime, "model": model})
    assert P.resolve(company, {}) == (runtime, model)
    assert P.resolve(company, {"runtime": runtime, "model": model}) == (runtime, model)
    with pytest.raises(P.ProviderError) as refused:
        P.complete_choice(company["enabled"], runtime, model)
    assert refused.value.status == 422 and "retired" in refused.value.detail


def test_the_bot_list_says_the_effort_a_bot_runs_at_and_nothing_when_it_has_none(api):
    """The runtime mark's hover reads harness, model and effort ("Codex GPT 6.2 Sol High"): the bot's
    reasoning_effort, else an older config `effort`, else its stored row; none, or no runtime, sends none."""
    from backend.store import encode
    from backend.tests.helpers.api import put

    put(
        api,
        "providers",
        {"enabled": ["openai", "anthropic"], "runtime": "claude", "model": "claude-opus-5-5", "expected_revision": 0},
    )
    configs = {
        "ops": ({"runtime": "codex", "model": "gpt-6.2-sol", "reasoning_effort": "high", "effort": "low"}, "max"),
        "cpo": ({}, ""),
        "assistant": ({"effort": "xhigh"}, "low"),
        "inbox": ({}, "medium"),
    }
    with api.app.state.store.transaction() as c:
        for bot, (config, stored) in configs.items():
            BS.write(c, bot, {'name': bot, 'status': 'active', **config})
            c.execute("UPDATE bots SET effort=? WHERE slug=?", (stored, bot))
    rows = {row["name"]: row for row in api.get("/api/employees", headers={"Authorization": "Bearer ana-test"}).json()}
    said = {
        bot: (rows[bot]["resolved_runtime"], rows[bot]["resolved_model"], rows[bot]["resolved_effort"])
        for bot in configs
    }
    assert said == {
        "ops": ("codex", "gpt-6.2-sol", "high"),
        "cpo": ("claude", "claude-opus-5-5", ""),
        "assistant": ("claude", "claude-opus-5-5", "xhigh"),
        "inbox": ("claude", "claude-opus-5-5", "medium"),
    }
    # A team with no provider: nothing resolves, so no effort is said either.
    put(api, "providers", {"enabled": [], "runtime": "", "model": "", "expected_revision": 1})
    rows = {row["name"]: row for row in api.get("/api/employees", headers={"Authorization": "Bearer ana-test"}).json()}
    assert (rows["cpo"]["resolved_runtime"], rows["cpo"]["resolved_effort"], rows["inbox"]["resolved_effort"]) == (
        "",
        "",
        "",
    )


def test_gpt_6_1_terra_is_offered_on_codex_at_the_approved_rate(api):
    """Product opened gpt-6.1-terra on Codex (2026-11-11; held under Dev 693): priced at Tico's rate, listed, pickable,
    and not recommended (gpt-6-luna stays the Codex pick)."""
    from backend import task_models

    row = P.MODEL_BY_ID["gpt-6.1-terra"]
    assert not row.get("held") and not row.get("deprecated") and not row.get("recommended")
    assert P.price_of("gpt-6.1-terra") == (1.25, 0.062, 6.25)
    assert "gpt-6.1-terra" in [r["id"] for r in P.provider_models("openai")]
    assert P.recommended("openai")["id"] == "gpt-6-luna"
    assert P.complete_choice(["openai"], model="gpt-6.1-terra") == ("codex", "gpt-6.1-terra")
    task_models.refuse_retired(row)
    listed = api.get("/api/v2/models", headers={"Authorization": "Bearer ana-test"}).json()["models"]
    assert "gpt-6.1-terra" in [m["id"] for m in listed]


def test_the_newest_fable_and_sol_are_offered_and_their_predecessors_retired():
    """Fable 5.2 (released 2026-11-09) and Sol 6.2 are offered and priced; Fable 5.1 and Sol 6.1 stay readable for
    bots already on them but are retired, so no new pick can land there. Astra 6.2 is offered beside retired Astra 6."""
    for new, old in (("claude-fable-5-2", "claude-fable-5-1"), ("gpt-6.2-sol", "gpt-6.1-sol")):
        assert not P.MODEL_BY_ID[new].get("deprecated") and P.MODEL_BY_ID[old]["deprecated"], new
        assert new in [r["id"] for r in P.provider_models(P.MODEL_BY_ID[new]["provider"])]
        assert P.price_of(new)
    assert not P.MODEL_BY_ID["gpt-6.2-astra"].get("deprecated") and P.MODEL_BY_ID["gpt-6-astra"]["deprecated"]
    assert P.price_of("gpt-6.2-astra") == (8.0, 0.40, 40.0)
    assert P.complete_choice(["openai"], model="gpt-6.2-sol") == ("codex", "gpt-6.2-sol")
    assert P.complete_choice(["anthropic"], model="claude-fable-5-2") == ("claude", "claude-fable-5-2")


def test_the_gateway_still_holds_terra_so_a_live_hq_tier_for_it_stays_hidden(monkeypatch):
    """The Gateway keeps gpt-6.1-terra held for paid use: an HQ tier that runs it is not published, and a tier row for
    the id never replaces the Codex row."""
    live = {
        "id": "gpt-6.1-terra",
        "label": "live",
        "runtime": "pi",
        "provider": "tico",
        "efforts": ("low",),
        "default_effort": "low",
        "tier": {"level": "low", "fast": False, "version": 1, "auto": False},
    }
    monkeypatch.setattr(P.gateway_tiers, "row_for", lambda model: live if model == "gpt-6.1-terra" else None)
    assert P.MODEL_BY_ID["gpt-6.1-terra"]["provider"] == "openai"
    assert P.complete_choice(["tico", "openai"], model="gpt-6.1-terra") == ("codex", "gpt-6.1-terra")
    assert "gpt-6.1-terra" not in [r["id"] for r in P.provider_models("tico")]
    tier = {
        "id": "tico-low",
        "current": 1,
        "versions": [{"version": 1, "id": "tico-low-1.0", "model": "GPT-6.1 Terra"}],
    }
    assert P.gateway_tiers.live_row(tier) is None


def test_minimax_on_openrouter_is_offered_only_as_a_meeting_model_and_priced():
    from clients import openrouter

    assert P.OPENROUTER_MEETING_MODEL == "minimax-m3.1-flash"
    assert "minimax-m3.1-flash" in [row["id"] for row in P.provider_models("openrouter", meeting=True)]
    assert "minimax-m3.1-flash" not in [row["id"] for row in P.provider_models("openrouter")]
    row = P.MODEL_BY_ID["minimax-m3.1-flash"]
    assert (row["runtime"], row["default_effort"], row["efforts"][0]) == ("pi", "low", "low")
    assert P.maker("minimax-m3.1-flash") == "minimax"
    # OpenRouter's list price, as HQ's meeting row reads it (ticoteam/tico-services#377).
    assert P.PRICES["minimax-m3.1-flash"] == (0.15, 0.015, 0.60)
    assert openrouter.MODELS["minimax-m3.1-flash"] == "minimax/minimax-m3.1-flash"
    # OpenRouter withdrew Mercury 3: nothing offers or maps it any more.
    assert "mercury-3" not in [row["id"] for row in P.MODEL_CATALOG]
    assert "mercury-3" not in openrouter.MODELS


def test_a_mercury_3_run_that_completes_after_the_upgrade_is_still_priced():
    # 1M uncached input and 1M output at Mercury 3's list price, as the run's completion prices it (execution.py).
    assert P.estimate_cost("mercury-3", 1_000_000, 0, 1_000_000) == pytest.approx(0.90)


OLD, MINIMAX = "mercury-3", "minimax-m3.1-flash"


def _saved_company(**saved):
    return P._record({"enabled": ["openrouter"], **saved})


def _entry_points():
    """Every place a saved or requested `mercury-3` is read: (what, what it resolves to)."""
    from types import SimpleNamespace

    from backend import check_models, harnesses, task_models, task_run_models

    seed = SimpleNamespace(enabled_providers=["openrouter"], default_runtime="", default_model=OLD, models_preset="")
    company = _saved_company(runtime="pi", model=OLD)
    return {
        "company default": lambda: company["model"],
        "company default from the environment": lambda: P.env_seed(seed)["model"],
        "company default fallback list": lambda: [row["model"] for row in P.default_fallbacks(company)][0],
        "company meeting model": lambda: _saved_company(meeting_model=OLD)["meeting_model"],
        "company check order": lambda: _saved_company(check_models=["pi:" + OLD, OLD])["check_models"],
        "check order as saved (PUT providers)": lambda: P.normalize_check_models([OLD], company),
        "meeting model requested": lambda: P.meeting_choice({"enabled": ["openrouter"]}, OLD),
        "catalog lookup (bot model, fallback and task model setters)": lambda: P.MODEL_BY_ID[OLD]["id"],
        "bot model": lambda: P.resolve(company, {"model": OLD})[1],
        "bot fallback at the claim": lambda: P.fill(company, {"fallback": {"harness": "pi", "model": OLD}})["fallback"][
            "model"
        ],
        "bot fallback as shown": lambda: harnesses.normalize_fallback({"harness": "pi", "model": OLD})["model"],
        "bot task model": lambda: task_models.stored({"task_model": {"model": OLD}})["model"],
        "task model requested": lambda: task_models.choice_of(OLD, "low")["model"],
        "routine step stamp": lambda: task_run_models.stamped(OLD)["model"],
        "check model requested": lambda: P.complete_choice(["openrouter"], *check_models.parse(OLD))[1],
    }


@pytest.mark.parametrize(
    "where",
    [
        "company default",
        "company default from the environment",
        "company default fallback list",
        "company meeting model",
        "company check order",
        "check order as saved (PUT providers)",
        "meeting model requested",
        "catalog lookup (bot model, fallback and task model setters)",
        "bot model",
        "bot fallback at the claim",
        "bot fallback as shown",
        "bot task model",
        "task model requested",
        "routine step stamp",
        "check model requested",
    ],
)
def test_a_saved_or_requested_mercury_3_resolves_to_minimax(where):
    got = _entry_points()[where]()
    if where == "company check order":
        assert got == ["pi:" + MINIMAX, MINIMAX]
    elif where == "check order as saved (PUT providers)":
        assert got == [MINIMAX]
    else:
        assert got == MINIMAX


def test_a_saved_mercury_3_never_reaches_the_runner_and_setters_save_minimax(api):
    import json

    from backend import check_models, task_run_models
    from backend.store import H, encode
    from backend.tests.helpers.api import claim, get, post
    from backend.tests.helpers.task_models import ops_on_sonnet, revision, set_task_model

    machine = ops_on_sonnet(api)
    settings = api.app.state.store.settings
    # The company and ops as a release before OpenRouter withdrew Mercury 3 saved them.
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE registry_metadata SET value=? WHERE key='providers'",
            (encode({"revision": 1, "enabled": ["anthropic", "xai", "openrouter"], "runtime": "pi", "model": OLD}),),
        )
        config = BS.read(c, "ops")
        config["fallback"] = {"harness": "pi", "model": OLD}
        BS.write(c, "ops", config)
    task = post(api, "tasks", {"owner": "ops", "title": "Tidy the changelog", "body": "Sort the entries."})
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO task_run_models(task_id,runtime,model,harness,effort,compact_at,set_by,set_at) "
            "VALUES(?,'pi',?,'pi','low',NULL,'human:ana',?)",
            (task["id"], OLD, H.now()),
        )
    with api.app.state.store.read() as c:
        assert task_run_models.of(c, task["id"])["model"] == MINIMAX
    turn = claim(api, machine)
    assert turn["task"]["id"] == task["id"]
    assert turn["config"]["fallback"]["model"] == MINIMAX
    assert OLD not in json.dumps(turn["config"])
    fallbacks = get(api, "config", machine["token"])["fallback_models"]
    assert MINIMAX in [row["model"] for row in fallbacks] and OLD not in json.dumps(fallbacks)

    # A check subtask filed on it: the turn, its claim and its notes all name MiniMax.
    check = post(api, "tasks", {"owner": "ops", "title": "Check the changelog", "body": "Read it."})
    with api.app.state.store.transaction() as c:
        owner = c.execute("SELECT owner_id FROM tasks WHERE id=?", (check["id"],)).fetchone()[0]
        c.execute(
            "INSERT INTO task_check_models(task_id,runtime,model,harness,effort,role,created_by,created_at) "
            "VALUES(?,'pi',?,'pi','low','check',?,?)",
            (check["id"], OLD, owner, H.now()),
        )
        assert check_models.of(c, check["id"], owner.removeprefix("bot:"))["model"] == MINIMAX

    # Setters take the old id and save the new one.
    post(api, "bots/ops/fallback", {"fallback": {"harness": "pi", "model": OLD}, "expected_revision": revision(api)})
    set_task_model(api, {"model": OLD, "harness": "pi"})
    view = get(api, "providers")
    saved = api.put(
        "/api/v2/providers",
        json={
            "enabled": view["enabled"],
            "model": view["default"]["model"],
            "check_models": [OLD],
            "expected_revision": view["revision"],
        },
        headers={"Authorization": "Bearer ana-test", "Idempotency-Key": H.new_id()},
    )
    assert saved.status_code == 200, saved.text
    with api.app.state.store.read() as c:
        config = BS.read(c, "ops")
        assert (config["fallback"]["model"], config["task_model"]["model"]) == (MINIMAX, MINIMAX)
        assert P.load(c, settings)["check_models"] == [MINIMAX]
        stored = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='providers'").fetchone()[0])
    assert stored["check_models"] == [MINIMAX] and stored["model"] == MINIMAX


def test_antigravity_runs_neon_and_hydrogen_on_its_own_runtime_and_claims_with_xhigh(api):
    from backend import updates
    from backend.tests.helpers.api import assign, claim, post, put, ready, runner

    put(
        api,
        "providers",
        {"enabled": ["google"], "runtime": "antigravity", "model": "gemini-4-neon", "expected_revision": 0},
    )
    listed = {
        m["id"]: m for m in api.get("/api/v2/models", headers={"Authorization": "Bearer ana-test"}).json()["models"]
    }
    neon, hydrogen, flash = listed["gemini-4-neon"], listed["gemini-4-hydrogen"], listed["gemini-3.8-flash"]
    for row in (neon, hydrogen):
        assert row["runtime"] == "antigravity" and row["harnesses"] == ["antigravity"]
        assert row["efforts"] == ["low", "medium", "high", "xhigh"] and row["default_effort"] == "high"
    assert neon.get("recommended") is True and not flash.get("recommended")
    assert flash["runtime"] == "gemini" and flash["harnesses"] == ["gemini"]
    # Google serves both runtimes; a runtime alone gets a model it runs.
    assert P.runtime_provider("antigravity") == P.runtime_provider("gemini") == "google"
    assert P.runtime_enabled("antigravity", ["google"]) and not P.runtime_enabled("antigravity", ["openai"])
    assert P.complete_choice(["google"], "gemini") == ("gemini", "gemini-3.8-flash")
    assert P.complete_choice(["google"], "antigravity") == ("antigravity", "gemini-4-neon")
    # Direct Gemini API drafts skip Antigravity-only Neon for a Gemini-harness model.
    assert updates.own_model(
        P._record({"enabled": ["google"], "runtime": "antigravity", "model": "gemini-4-neon"}), {"GEMINI_API_KEY": "k"}
    ) == (
        "google",
        "k",
        "gemini-3.8-flash",
    )
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    refused = api.post(
        "/api/v2/bots/ops/model",
        json={"model": "gemini-4-neon", "harness": "gemini", "expected_revision": revision},
        headers={"Authorization": "Bearer ana-test"},
    )
    assert refused.status_code == 422
    saved = post(
        api,
        "bots/ops/model",
        {"model": "gemini-4-neon", "effort": "xhigh", "expected_revision": revision},
    )
    assert (saved["runtime"], saved["harness"], saved["model"], saved["effort"]) == (
        "antigravity",
        "antigravity",
        "gemini-4-neon",
        "xhigh",
    )
    post(api, "chat/ops", {"text": "Say hello."})
    config = claim(api, r)["config"]
    assert (config["runtime"], config["harness"], config["model"], config["reasoning_effort"]) == (
        "antigravity",
        "antigravity",
        "gemini-4-neon",
        "xhigh",
    )


def test_the_antigravity_migration_moves_every_antigravity_pick_and_flash_to_hydrogen_once():
    import importlib
    import json
    import sqlite3

    migration = importlib.import_module("backend.migrations.20261123184253_antigravity_runtime")
    c = sqlite3.connect(":memory:")
    c.executescript(
        "CREATE TABLE bot_settings(bot_id TEXT PRIMARY KEY, harness TEXT, runtime TEXT, model TEXT, fallback TEXT,"
        " extra TEXT NOT NULL DEFAULT '{}');"
        "CREATE TABLE task_run_models(task_id TEXT PRIMARY KEY, runtime TEXT, model TEXT, harness TEXT);"
        "CREATE TABLE task_check_models(task_id TEXT PRIMARY KEY, runtime TEXT, model TEXT, harness TEXT);"
        "CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    agy_flash = {"harness": "antigravity", "runtime": "gemini", "model": "gemini-3.8-flash"}
    cli_flash = {"harness": "gemini", "runtime": "gemini", "model": "gemini-3.8-flash"}
    # Neon on the Gemini runtime with no harness saved: the server took Antigravity for it before the split.
    bare_neon = {"runtime": "gemini", "model": "gemini-4-neon"}
    c.executemany(
        "INSERT INTO bot_settings VALUES (?,?,?,?,?,?)",
        [
            ("scout", "antigravity", "gemini", "gemini-3.8-flash", json.dumps(cli_flash), "{}"),
            ("ops", "antigravity", "gemini", "gemini-4-neon", None, json.dumps({"task_model": agy_flash, "x": 1})),
            (
                "cli",
                "gemini",
                "gemini",
                "gemini-3.8-flash",
                json.dumps(agy_flash),
                json.dumps({"task_model": cli_flash}),
            ),
            ("bare", None, "gemini", "gemini-4-neon", json.dumps(bare_neon), json.dumps({"task_model": bare_neon})),
            ("empty", "", "gemini", "gemini-4-neon", None, "{}"),
        ],
    )
    c.execute("INSERT INTO task_run_models VALUES ('t1', 'gemini', 'gemini-3.8-flash', 'antigravity')")
    c.execute("INSERT INTO task_run_models VALUES ('t2', 'gemini', 'gemini-4-neon', '')")
    c.executemany(
        "INSERT INTO task_check_models VALUES (?,?,?,?)",
        [
            ("t1", "gemini", "gemini-3.8-flash", "antigravity"),
            ("t2", "gemini", "gemini-4-neon", ""),
            ("t3", "gemini", "gemini-3.8-flash", "gemini"),
        ],
    )
    c.execute(
        "INSERT INTO registry_metadata VALUES ('providers', ?)",
        (
            json.dumps(
                {"runtime": "gemini", "model": "gemini-4-neon", "check_models": ["gemini:gemini-4-neon", "grok"]}
            ),
        ),
    )
    for _ in range(2):
        migration.up(c)
    rows = {r[0]: r[1:] for r in c.execute("SELECT bot_id, runtime, model, fallback, extra FROM bot_settings")}
    hydrogen = {"harness": "antigravity", "runtime": "antigravity", "model": "gemini-4-hydrogen"}
    assert rows["scout"][:2] == ("antigravity", "gemini-4-hydrogen") and json.loads(rows["scout"][2]) == cli_flash
    assert rows["ops"][:2] == ("antigravity", "gemini-4-neon")
    assert json.loads(rows["ops"][3]) == {"task_model": hydrogen, "x": 1}
    # A Gemini CLI bot keeps its own pick; only its pick on Antigravity moves.
    assert rows["cli"][:2] == ("gemini", "gemini-3.8-flash") and json.loads(rows["cli"][2]) == hydrogen
    assert json.loads(rows["cli"][3]) == {"task_model": cli_flash}
    neon = {"harness": "antigravity", "runtime": "antigravity", "model": "gemini-4-neon"}
    rows = {r[0]: r[1:] for r in c.execute("SELECT bot_id, harness, runtime, model, fallback, extra FROM bot_settings")}
    assert rows["bare"][:3] == ("antigravity", "antigravity", "gemini-4-neon")
    assert json.loads(rows["bare"][3]) == neon and json.loads(rows["bare"][4]) == {"task_model": neon}
    assert rows["empty"][:3] == ("antigravity", "antigravity", "gemini-4-neon")
    picks = "SELECT task_id, runtime, model, harness FROM {} ORDER BY task_id"
    assert c.execute(picks.format("task_run_models")).fetchall() == [
        ("t1", "antigravity", "gemini-4-hydrogen", "antigravity"),
        ("t2", "antigravity", "gemini-4-neon", "antigravity"),
    ]
    assert c.execute(picks.format("task_check_models")).fetchall() == [
        ("t1", "antigravity", "gemini-4-hydrogen", "antigravity"),
        ("t2", "antigravity", "gemini-4-neon", "antigravity"),
        ("t3", "gemini", "gemini-3.8-flash", "gemini"),
    ]
    record = json.loads(c.execute("SELECT value FROM registry_metadata").fetchone()[0])
    assert record == {
        "runtime": "antigravity",
        "model": "gemini-4-neon",
        "check_models": ["antigravity:gemini-4-neon", "grok"],
    }


def test_a_runner_without_the_antigravity_runtime_is_never_handed_a_named_antigravity_fallback():
    # A runner from before the split takes harness antigravity for the Gemini CLI: it would run Neon there.
    from backend.execution import without_unknown_fallback

    config = {"runtime": "claude", "fallback": {"harness": "antigravity", "model": "gemini-4-neon"}}
    old = {"runtimes": {"claude": {"installed": True}, "gemini": {"installed": True}}}
    assert without_unknown_fallback(config, old) == {"runtime": "claude"}
    new = {"runtimes": {**old["runtimes"], "antigravity": {"installed": True}}}
    assert without_unknown_fallback(config, new) == config
    # Any other named fallback stays where it is.
    codex = {"runtime": "claude", "fallback": {"harness": "codex", "model": "gpt-6-sol"}}
    assert without_unknown_fallback(codex, old) == codex


def test_a_paid_by_bot_on_an_older_runner_keeps_its_fallbacks_off_once_its_antigravity_one_is_dropped():
    # The claim drops the named Antigravity fallback first, so restrict_fallbacks sees a bot with none of its own on a
    # runner that cannot filter hops per turn: automatic hops stay off, never onto the paid-by tool.
    from backend.execution import restrict_fallbacks, without_unknown_fallback

    config = {"runtime": "claude", "fallback": {"harness": "antigravity", "model": "gemini-4-neon"}}
    older = {"runtimes": {"claude": {"installed": True}, "codex": {"installed": True}}}
    out = restrict_fallbacks(without_unknown_fallback(config, older), {"codex"}, [])
    assert out["fallback"] == {"off": True} and out["blocked_fallback_runtimes"] == ["codex"]
