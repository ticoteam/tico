"""A bot out of quota with no fallback of its own runs the turn once on another provider signed in here."""
import pytest

from runner.hosts.fake import FakeHost
from runner.service import Runner, default_fallback
from runner.tests.test_runner_resilience import FakeClient, attempt

READY = {"installed": True, "authenticated": "ready"}
CHOICES = [{"runtime": "codex", "harness": "codex", "model": "gpt-test"},
           {"runtime": "claude", "harness": "claude", "model": "claude-test"}]


class ConfigClient(FakeClient):
    def get(self, path, **query):
        if path == "config":
            return {"enabled_providers": ["openai", "anthropic"], "default_runtime": "claude", "fallback_models": CHOICES}
        return {}


@pytest.fixture
def execution(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "operator"))
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CURSOR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "emp-coo").mkdir()
    client = ConfigClient()
    runner = Runner({"url": "https://runner.example", "token": "machine", "projects_dir": str(tmp_path),
                     "capacity": 1}, tmp_path / "state", client=client, push=lambda path, env=None: (0, ""))
    runner.renew_interval = 0.05
    monkeypatch.setattr(runner, "publish", lambda *args: None)
    monkeypatch.setattr(runner, "refresh_workspace", lambda *args: None)
    monkeypatch.setattr(runner, "refresh_product_files", lambda *args: None)
    monkeypatch.setattr(runner, "mcp_for_run", lambda *args: [])
    runner.default_runtime()
    runner.runtime_rows = {"claude": dict(READY), "codex": dict(READY)}
    yield runner, client
    runner.pool.shutdown(wait=True)


def claude_bot():
    work = attempt()
    work["config"] = {**work["config"], "runtime": "claude", "harness": "claude", "model": "claude-test"}
    return work


def run(runner, monkeypatch, work, error="Claude AI monthly spend limit reached"):
    primary, second, calls = FakeHost(), FakeHost(replies=["ran on codex"]), []
    primary.fail_next_turn(error)

    def host(row, env):
        calls.append((row["config"]["runtime"], row["config"].get("model"), row.get("fallback")))
        return second if row.get("fallback") else primary

    monkeypatch.setattr(runner, "host_factory", host)
    runner.execute(work)
    return calls


def test_a_limit_with_no_fallback_set_runs_the_turn_once_on_another_ready_provider(execution, monkeypatch):
    runner, client = execution
    calls = run(runner, monkeypatch, claude_bot())
    result = client.completion()
    assert calls == [("claude", "claude-test", None), ("codex", "gpt-test", "codex")]
    assert result["outcome"] == "completed"
    assert result["fallback"] == "codex"
    assert result["primary_limited"] is True
    assert not result.get("limited")
    with runner.state.connect() as connection:
        notes = [row[0] for row in connection.execute("SELECT payload FROM events WHERE kind='diagnostic'")]
    assert any("claude hit its usage limit; running this turn on codex (no fallback set" in note for note in notes), notes


def test_no_ready_provider_leaves_the_turn_limited_as_before(execution, monkeypatch):
    runner, client = execution
    runner.runtime_rows["codex"] = {"installed": True, "authenticated": "missing"}
    calls = run(runner, monkeypatch, claude_bot())
    result = client.completion()
    assert [runtime for runtime, *_ in calls] == ["claude"]
    assert result["limited"] is True
    assert "fallback" not in result and "primary_limited" not in result


def test_a_failure_that_is_not_a_limit_does_not_move(execution, monkeypatch):
    runner, client = execution
    calls = run(runner, monkeypatch, claude_bot(), error="the turn failed")
    assert [runtime for runtime, *_ in calls] == ["claude"]
    assert client.completion()["outcome"] == "failed"


def test_the_company_turning_it_off_sends_no_models(execution, monkeypatch):
    runner, client = execution
    runner._fallback_models = []          # GET /api/v2/config with auto_fallback off
    calls = run(runner, monkeypatch, claude_bot())
    assert [runtime for runtime, *_ in calls] == ["claude"]
    assert client.completion()["limited"] is True


def test_choice_keeps_an_explicit_fallback_honours_off_and_skips_the_primary():
    ready = lambda runtime, profile="": True
    config = {"runtime": "codex", "harness": "codex", "reasoning_effort": "high"}
    assert default_fallback(config, CHOICES, ready) == {
        "harness": "claude", "runtime": "claude", "model": "claude-test", "reasoning_effort": "high", "default": True}
    assert default_fallback({**config, "fallback": {"off": True}}, CHOICES, ready) is None
    assert default_fallback({**config, "fallback": {"harness": "gemini", "model": "g"}}, CHOICES, ready) is None
    assert default_fallback(config, CHOICES, lambda runtime, profile="": runtime == "codex") is None


def test_a_bots_own_fallback_on_a_limit_sends_no_new_field_to_an_older_server(execution, monkeypatch):
    runner, client = execution
    work = claude_bot()
    work["config"]["fallback"] = {"harness": "codex", "model": "gpt-own"}
    calls = run(runner, monkeypatch, work)
    result = client.completion()
    assert calls[1] == ("codex", "gpt-own", "codex")
    assert result["fallback"] == "codex" and "primary_limited" not in result


def test_readiness_is_per_subscription_and_never_a_key_from_the_environment(execution, monkeypatch):
    runner, _ = execution
    assert runner.fallback_ready("claude")
    assert not runner.fallback_ready("claude", "work")      # nobody checked that subscription's sign-in
    runner.runtime_rows["claude"]["profiles"] = {"work": dict(READY)}
    assert runner.fallback_ready("claude", "work")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert not runner.fallback_ready("claude")
    assert runner.fallback_ready("codex")
    runner.reject("codex", "Incorrect API key provided")
    assert not runner.fallback_ready("codex")
