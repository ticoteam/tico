"""clients/cli_spec.py: what a spec may say that the trigger and rules fixtures (test_cli_fixtures.py) do not reach."""

import argparse
import shlex

import pytest

from clients import cli_spec, hubcli, hubtools
from clients.cli_spec import NULL, Arg, Command, Group

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "scope": {"type": "object", "properties": {"names": {"type": "array", "description": "Who it is for"}}},
        "operation_id": {"type": "string"},
    },
    "required": ["id"],
}


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fn(api, args):
        seen.append(args)
        return args

    monkeypatch.setitem(hubtools.BY_NAME, "tico_fake_show", {"inputSchema": SCHEMA, "fn": fn})
    plain = {**SCHEMA, "properties": {k: v for k, v in SCHEMA["properties"].items() if k != "scope"}}
    monkeypatch.setitem(hubtools.BY_NAME, "tico_fake_list", {"inputSchema": plain, "fn": fn})
    monkeypatch.setitem(hubtools.BY_NAME, "tico_fake_find", {"inputSchema": plain, "fn": fn})
    return seen


def parse(specs, argv):
    root = argparse.ArgumentParser()
    cli_spec.add(root.add_subparsers(dest="cmd"), specs)
    return root.parse_args(argv)


def test_a_flag_can_send_an_explicit_null_and_a_folded_flag_is_not_sent_itself(calls, monkeypatch):
    monkeypatch.setenv("HUB_OPERATION_ID", "op-1")
    show = Command(
        "fake show",
        args=(
            Arg("limit", action="store_const", const=NULL),
            Arg("names", "--name", action="append", into="scope"),
        ),
        convert=lambda f: f | {"scope": {"names": f["names"]}} if f.get("names") else f,
    )
    args = parse((Group("fake"), show), ["fake", "show", "x", "--limit", "--name", "Ada"])
    assert (args.fn, cli_spec.of(args)) == ("fake show", show)
    show.call(None, args)
    assert calls == [{"id": "x", "limit": None, "scope": {"names": ["Ada"]}, "operation_id": "op-1"}]


def test_a_command_with_a_renderer_takes_json_and_another_tool_by_its_arguments(calls):
    listing = Command(
        "fake list", render=lambda args, result: "text", tools=("tico_fake_find",), tool_for=lambda f: f["id"]
    )
    args = parse((Group("fake"), listing), ["fake", "list", "tico_fake_find", "--json"])
    assert args.json is True and args.fn == "fake list"
    assert listing.call(None, args) == {"id": "tico_fake_find", "operation_id": None}
    assert listing.render and listing.render(args, {}) == "text"
    elsewhere = parse((Group("fake"), listing), ["fake", "list", "tico_fake_other"])
    with pytest.raises(ValueError, match="not in its tools"):
        listing.prepare(elsewhere)


def test_help_is_the_schema_description_and_the_example_follows_the_flags(calls, capsys):
    show = Command(
        "fake show",
        "one fake",
        args=(Arg("names", "--name", action="append", into="scope"),),
        example="tico fake show x --name Ada",
    )
    with pytest.raises(SystemExit):
        parse((Group("fake"), show), ["fake", "show", "-h"])
    out = capsys.readouterr().out
    assert "Who it is for (repeatable)" in out  # the folded flag's own key inside `scope`
    assert "1-50" in out  # no description: the range
    assert out.rstrip().endswith("Example: tico fake show x --name Ada")


@pytest.mark.parametrize(
    ("command", "says"),
    [
        (Command("fake show", args=(Arg("nope"),)), "not properties"),
        (Command("fake show", args=(Arg("x", into="nope"),)), "folds into nope"),
        (Command("fake show", args=(Arg("limit"),)), "scope is an object"),
    ],
)
def test_a_spec_that_does_not_fit_its_tool_stops_the_parser_being_built(calls, command, says):
    with pytest.raises(ValueError, match=says):
        parse((Group("fake"), command), ["fake", "show", "x"])


def spec_leaves(p=None, path=()):
    """(path, parser, Command) for every leaf of the real CLI built from a spec."""
    p = p or hubcli.parser()
    out = []
    if (command := p._defaults.get("_spec")) is not None:
        out.append((path, p, command))
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen = set()
            for name, sub in action.choices.items():
                if id(sub) not in seen:  # an alias is the same parser
                    seen.add(id(sub))
                    out += spec_leaves(sub, (*path, name))
    return out


def test_every_spec_command_has_an_example_that_runs_it():
    wrong = []
    for path, _p, command in spec_leaves():
        if " ".join(path) != command.path:
            continue  # a group run bare (`tico rules due`): its default leaf has its own example
        words = shlex.split(command.example)
        if words[:1] != ["tico"] or cli_spec.of(hubcli.parser().parse_args(words[1:])) is not command:
            wrong.append(f"{command.path}: {command.example!r}")
    assert wrong == []


def test_no_argument_help_names_a_tool():
    """The help is the schema's description, which an MCP client reads too: it names a command (`trigger list`), not
    the tool behind it, so it reads well in both places."""
    named = [
        f"{' '.join(path)} {action.option_strings or action.dest}: {action.help}"
        for path, p, _command in spec_leaves()
        for action in p._actions
        if action.help and "tico_" in action.help
    ]
    assert named == []
