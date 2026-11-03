"""The `hub` parser forgives the guesses models make most often: `--version`, a `--json` anywhere, a command that
lacks its id, and a typo that names the closest commands that exist. Parsing only; no API is called."""
import pytest

from clients import gateway, hubcli


def parse_failure(capsys, argv):
    """The exit code and stderr of a line the parser rejects."""
    with pytest.raises(SystemExit) as failed:
        hubcli.parse(hubcli.parser(), argv)
    return failed.value.code, capsys.readouterr().err


def test_version_prints_the_tico_version_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setenv("TICO_VERSION", "1.2.3")
    with pytest.raises(SystemExit) as done:
        hubcli.parse(hubcli.parser(), ["--version"])
    assert done.value.code == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


class _Reply:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _unstamped(monkeypatch, opened):
    """A CLI with no TICO_VERSION (a released runner's turn) whose opener is `opened(url, timeout)`."""
    monkeypatch.setenv("TICO_VERSION", "dev")
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")

    class Opener:
        def open(self, url, timeout=None):
            return opened(url, timeout)
    monkeypatch.setattr(gateway, "branded_opener", lambda *handlers: Opener())


def test_version_asks_the_server_when_the_cli_is_unstamped(monkeypatch, capsys):
    seen = []

    def healthz(url, timeout):
        seen.append((url, timeout))
        return _Reply('{"ok": true, "release": "1.4.0"}')
    _unstamped(monkeypatch, healthz)
    with pytest.raises(SystemExit) as done:
        hubcli.parse(hubcli.parser(), ["--version"])
    assert done.value.code == 0
    assert capsys.readouterr().out.strip() == "1.4.0"
    assert seen == [("https://tico.acme.example/healthz", 3)]


def test_version_says_unstamped_when_the_server_does_not_answer(monkeypatch, capsys):
    def unreachable(url, timeout):
        raise OSError("timed out")
    _unstamped(monkeypatch, unreachable)
    with pytest.raises(SystemExit):
        hubcli.parse(hubcli.parser(), ["--version"])
    assert capsys.readouterr().out.strip() == "unknown (this CLI build is not stamped)"


def test_json_is_accepted_anywhere_on_the_line(capsys):
    for argv in (["task", "show", "x", "--json"], ["--json", "task", "show", "x"]):
        args = hubcli.parse(hubcli.parser(), argv)
        assert args.fn == "task show"
        assert args.id == ["x"]
    assert capsys.readouterr().err == ""


def test_a_command_without_its_id_prints_its_usage_and_an_example(capsys):
    code, err = parse_failure(capsys, ["task", "show"])
    assert code == 2
    assert "usage: hub task show" in err
    assert "Example: hub task show <id>" in err


def test_a_mistyped_command_names_the_closest_commands_that_exist(capsys):
    code, err = parse_failure(capsys, ["task", "lst"])
    assert code == 2
    assert "Did you mean: hub task list" in err.splitlines()[-1]


def test_a_bad_flag_value_lists_its_choices(capsys):
    code, err = parse_failure(capsys, ["task", "list", "--status", "todo"])
    assert code == 2
    listed = err.split("choose from", 1)[1]    # quoted on newer Pythons, bare on older ones: match the names only
    for status in ("open", "doing", "waiting", "review", "ready", "done", "closed", "declined"):
        assert status in listed


def test_an_unknown_flag_suggests_the_closest_flag_of_that_command(capsys):
    code, err = parse_failure(capsys, ["task", "list", "--where-status", "closed"])
    assert code == 2
    assert "Did you mean --status?" in err


def _leaves(p, skip_aliases):
    """Every command parser under `p`. With `skip_aliases`, an alias (the same parser under another name) is left out."""
    import argparse
    found = []
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen = set()
            for name, sub in action.choices.items():
                if skip_aliases and id(sub) in seen:
                    continue
                seen.add(id(sub))
                found.extend(_leaves(sub, skip_aliases) or [sub])
    return found


@pytest.mark.parametrize("words, command", [
    (["tasks", "list"], "task list"),
    (["approvals", "show", "x"], "approval show"),
    (["automations", "list"], "automation list"),
    (["bots", "status", "list"], "bot status list"),
    (["rule", "list"], "rules list"),
])
def test_plural_nouns_parse_to_the_same_command(words, command, capsys):
    assert hubcli.parse(hubcli.parser(), words).fn == command
    assert capsys.readouterr().err == ""


def test_aliases_add_no_mcp_tool():
    from clients import remotecli
    root = hubcli.parser()
    with_aliases = {remotecli.tool_name(sub._defaults["fn"]) for sub in _leaves(root, skip_aliases=False)
                    if "fn" in sub._defaults}
    without = {remotecli.tool_name(sub._defaults["fn"]) for sub in _leaves(root, skip_aliases=True)
               if "fn" in sub._defaults}
    assert with_aliases == without


def test_a_flag_the_command_does_not_take_lists_the_ones_it_does(capsys):
    code, err = parse_failure(capsys, ["automation", "list", "--all"])
    assert code == 2
    assert "It takes: --cursor, --limit, --owner" in err


def test_bot_show_takes_a_slug_and_is_the_bot_detail_read():
    args = hubcli.parse(hubcli.parser(), ["bot", "show", "ops-bot"])
    assert (args.fn, args.slug) == ("bot show", "ops-bot")


def test_bot_show_reads_the_bot_detail_through_its_tool(monkeypatch):
    from clients import remotecli
    calls = []

    class Api:
        def __init__(self, *args, **kw):
            self.__dict__["_tico_is_botops"] = False      # a person's own rights: no BotOps lookup

        def get(self, path, **query):
            calls.append(("GET", path))
            return {"slug": "ops-bot", "name": "Ops"}
    monkeypatch.setattr(remotecli, "Client", Api)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    monkeypatch.setenv("HUB_TOKEN", "t")
    result = remotecli.run(hubcli.parse(hubcli.parser(), ["bot", "show", "ops-bot"]))
    assert calls == [("GET", "bots/ops-bot")]
    assert result == {"slug": "ops-bot", "name": "Ops"}
