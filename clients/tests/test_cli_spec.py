"""clients/cli_spec.py: what a spec may say that the trigger and rules fixtures (test_cli_fixtures.py) do not reach."""

import argparse
import json
import shlex

import pytest

from clients import cli_spec, hubcli, hubtools
from clients.cli_spec import NULL, Arg, Command, Group

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "days": {"type": ["integer", "null"]},
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


def test_a_command_may_run_its_own_function_with_the_tools_arguments(calls):
    show = Command("fake list", run=lambda client, fields: {"ran": fields["id"]})
    args = parse((Group("fake"), show), ["fake", "list", "x", "--limit", "2"])
    assert show.call(None, args) == {"ran": "x"}
    assert calls == []  # the tool itself was not called


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


def test_several_flags_may_set_one_property_and_an_object_is_given_whole_as_json(calls, capsys):
    show = Command(
        "fake show",
        args=(
            Arg("limit", "--few", action="store_const", const=5),
            Arg("limit", "--none", action="store_const", const=NULL),
            Arg("scope", type=json.loads, metavar="JSON"),
        ),
    )
    specs = (Group("fake"), show)
    args = parse(specs, ["fake", "show", "x", "--few", "--scope", '{"names": ["Ada"]}'])
    assert show.prepare(args)[1] == {"id": "x", "limit": 5, "scope": {"names": ["Ada"]}, "operation_id": None}
    assert show.prepare(parse(specs, ["fake", "show", "x", "--few", "--none"]))[1]["limit"] is None
    assert show.prepare(parse(specs, ["fake", "show", "x", "--days", "14"]))[1]["days"] == 14  # ["integer", "null"]
    with pytest.raises(SystemExit):
        parse(specs, ["fake", "show", "-h"])
    assert "(sends null)" in capsys.readouterr().out


def test_a_fixed_property_is_sent_and_an_omitted_one_is_not_and_neither_takes_an_argument(calls):
    few = Command("fake few", tool="tico_fake_list", fixed=(("limit", 5),), omit=("days",))
    specs = (Group("fake"), few)
    args = parse(specs, ["fake", "few", "x"])
    assert args.fn == "fake list"  # the tool's words, as for any leaf named otherwise
    assert few.prepare(args)[1] == {"id": "x", "limit": 5, "operation_id": None}
    for flag in ("--limit", "--days"):
        with pytest.raises(SystemExit):
            parse(specs, ["fake", "few", "x", flag, "9"])


def test_exactly_one_of_an_id_or_a_local_flag_and_the_local_flag_is_not_sent(calls, capsys):
    every = Arg("all", local=True, action="store_true", help="every one of them")
    show = Command("fake list", args=(Arg("id", nargs="?"), every), one_of=(("id", "all"),))
    specs = (Group("fake"), show)
    args = parse(specs, ["fake", "list", "--all"])
    assert args.all is True and show.prepare(args)[1] == {"operation_id": None}
    assert show.prepare(parse(specs, ["fake", "list", "x"]))[1] == {"id": "x", "operation_id": None}
    for argv, says in (
        (["fake", "list"], "one of the arguments id --all is required"),
        (["fake", "list", "x", "--all"], "not allowed with"),
    ):
        with pytest.raises(SystemExit):
            parse(specs, argv)
        assert says in capsys.readouterr().err
    with pytest.raises(SystemExit):
        parse(specs, ["fake", "list", "-h"])
    assert "every one of them" in capsys.readouterr().out


def test_exactly_one_of_the_flags_that_set_a_required_property(calls, capsys):
    few = Command(
        "fake few",
        tool="tico_fake_list",
        args=(Arg("id", "--id"), Arg("id", "--first", action="store_const", const="1")),
        one_of=(("id",),),
    )
    specs = (Group("fake"), few)
    assert few.prepare(parse(specs, ["fake", "few", "--first"]))[1]["id"] == "1"
    assert few.prepare(parse(specs, ["fake", "few", "--id", "x"]))[1]["id"] == "x"
    for argv in (["fake", "few"], ["fake", "few", "--first", "--id", "x"]):
        with pytest.raises(SystemExit):
            parse(specs, argv)
    capsys.readouterr()


@pytest.mark.parametrize(
    ("command", "says"),
    [
        (Command("fake show", args=(Arg("nope"),)), "not properties"),
        (Command("fake list", args=(Arg("limit", help="how many"),)), "its help is its description there"),
        (Command("fake list", args=(Arg("limit", local=True),)), "is local"),
        (Command("fake list", one_of=(("nope",),)), "are not its arguments"),
        (Command("fake list", one_of=(("id", "limit"),)), "takes nargs"),
        (Command("fake show", fixed=(("nope", 1),)), "not properties"),
        (Command("fake list", args=(Arg("limit"),), fixed=(("limit", 1),)), "are fixed or omitted"),
        (Command("fake list", omit=("nope",)), "not properties"),
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


def test_every_spec_command_has_an_example_that_runs_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a file an example names is read as it parses (`pipeline save --file x.json`)
    wrong = []
    for path, _p, command in spec_leaves():
        if " ".join(path) != command.path:
            continue  # a group run bare (`tico rules due`): its default leaf has its own example
        words = shlex.split(command.example)
        for word in words:
            if word.endswith((".json", ".md")):
                (tmp_path / word).write_text("{}")
        if words[:1] != ["tico"] or cli_spec.of(hubcli.parser().parse_args(words[1:])) is not command:
            wrong.append(f"{command.path}: {command.example!r}")
    assert wrong == []
