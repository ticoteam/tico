"""clients/cli_spec.py: what a spec may say that the trigger and rules fixtures (test_cli_fixtures.py) do not reach."""

import argparse

import pytest

from clients import cli_spec, hubtools
from clients.cli_spec import NULL, Arg, Command, Group

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "scope": {"type": "object"},
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
    listing = Command("fake list", render=lambda args, result: "text", tool_for=lambda f: f["id"])
    args = parse((Group("fake"), listing), ["fake", "list", "tico_fake_show", "--json"])
    assert args.json is True and args.fn == "fake list"
    assert listing.call(None, args) == {"id": "tico_fake_show", "operation_id": None}
    assert listing.render and listing.render(args, {}) == "text"


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
