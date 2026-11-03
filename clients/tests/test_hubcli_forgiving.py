"""The `hub` parser forgives the guesses models make most often: `--version`, a `--json` anywhere, a command that
lacks its id, and a typo that names the closest commands that exist. Parsing only; no API is called."""
import pytest

from clients import hubcli


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
    assert "choose from open, doing, waiting, review, ready, done, closed, declined" in err


def test_an_unknown_flag_suggests_the_closest_flag_of_that_command(capsys):
    code, err = parse_failure(capsys, ["task", "list", "--where-status", "closed"])
    assert code == 2
    assert "Did you mean --status?" in err
