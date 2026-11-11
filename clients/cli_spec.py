"""Typed command specs: a `tico` command declared as data beside the tool it runs (clients/commands/<noun>.py), and
the argparse leaf and the tool's arguments made from it.

A command's flags come from its tool's input schema: a required property is a positional, the others are flags
spelled from their names (`type_from` is `--type-from`); an integer takes `type=int`, an enum its choices, a boolean
is a switch (false when left out), an array repeats. `operation_id` is never a flag: it comes from HUB_OPERATION_ID.
A spec says only what differs:

- `Command.path` is the words typed, `Group.aliases` another spelling of a group; `tool` defaults to `tico_` and the
  path's words.
- `Command.help` is the leaf's one-line summary and `Command.example` one command line, shown under its `--help`.
- An argument's help is its property's description in the schema, the same text an MCP client reads; a help that
  reads badly on a command line is fixed there, never overridden here.
- `Arg` overrides one property: a flag where a positional would be, another spelling, arity, a default.
- An `Arg` with `into=` is a flag of the command line alone that `Command.convert` folds into that property (`--who`
  and `--everyone` into the Who scope object); it is not passed on itself. Its help is the description of `part` (its
  name by default) inside that object property.
- A field is omitted when its flag is left out. `default=False` sends false; `NULL` as a flag's const sends null.
- `Command.tool_for(fields)` picks another tool by the arguments (`rules read` without `--from` reads one rule); every
  tool it may pick is in `tools`, whose properties the command takes too.
- `Command.render(args, result)` prints text in place of JSON; such a command also takes `--json`.
- `Group.default` is the leaf a group runs when no word follows (`tico rules due` lists, as `tico rules due list`).

clients/remotecli.py runs any parsed line that carries a spec through `Command.call`. scripts/hub_paths.py makes the
runner's command lists from the parser: a spec'd leaf is read-only when none of its tools writes.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

# The subparsers' dests by depth, as the hand-written parser names them: `cmd`, then `sub`, then `subsub`.
DESTS = ("cmd", "sub", "subsub")


class _Null:
    def __repr__(self):
        return "NULL"


NULL = _Null()  # a flag that sends an explicit null (`action="store_const", const=NULL`)
_SCHEMA = object()  # "as the schema says"


@dataclass(frozen=True)
class Arg:
    name: str  # the tool's property, or with `into` the command line's own name for the value
    flag: str | None = None  # the spelling, such as "--from"; giving one makes it a flag
    positional: bool | None = None  # None: positional when the schema requires it and no flag is spelled
    required: bool | None = None  # None: as the schema says
    nargs: str | None = None
    default: Any = _SCHEMA
    type: Callable[[str], Any] | None = None
    action: str | None = None
    const: Any = None
    metavar: str | None = None
    into: str | None = None  # the property `convert` folds this flag into
    part: str | None = None  # with `into`: the key inside it whose description is the help; the name by default


@dataclass(frozen=True)
class Group:
    path: str
    help: str | None = None
    aliases: tuple[str, ...] = ()
    default: str | None = None  # the path of the leaf run when no word follows


@dataclass(frozen=True)
class Command:
    path: str
    help: str | None = None
    tool: str = ""
    example: str = ""  # one command line, `tico ...`, shown under the leaf's `--help`
    tools: tuple[str, ...] = ()  # the other tools `tool_for` may pick; their properties are the command's too
    args: tuple[Arg, ...] = ()
    exclusive: tuple[tuple[str, ...], ...] = ()  # properties no two of which may be given together
    convert: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    tool_for: Callable[[dict[str, Any]], str] | None = None
    render: Callable[[argparse.Namespace, Any], str] | None = None

    def __post_init__(self):
        if not self.tool:
            object.__setattr__(self, "tool", "tico_" + self.path.replace(" ", "_").replace("-", "_"))

    @property
    def fn(self):
        """The command's name in `args.fn`, as the rest of the CLI reads it: the path, or for a leaf whose tool is
        named otherwise (`rules due add` runs tico_rules_due_set) the tool's own words."""
        own = "tico_" + self.path.replace(" ", "_").replace("-", "_")
        return self.path if own in (self.tool, *self.tools) else self.tool.removeprefix("tico_").replace("_", " ")

    def properties(self):
        """(properties, required): the tool's, then those only the other `tools` have, which are never required."""
        from clients import hubtools

        schema = hubtools.BY_NAME[self.tool]["inputSchema"]
        props = dict(schema["properties"])
        for other in self.tools:
            for name, prop in hubtools.BY_NAME[other]["inputSchema"]["properties"].items():
                props.setdefault(name, prop)
        return props, set(schema.get("required", ()))

    def arguments(self, suppress=False):
        """(option strings or the positional's name, add_argument keywords) for each argument, in schema order."""
        props, required = self.properties()
        given = {a.name: a for a in self.args if not a.into}
        if unknown := set(given) - set(props):
            raise ValueError(f"{self.path}: {sorted(unknown)} are not properties of {self.tool}")
        folded = {}
        for a in self.args:
            if a.into:
                if a.into not in props:
                    raise ValueError(f"{self.path}: {a.name} folds into {a.into}, not a property of {self.tool}")
                folded.setdefault(a.into, []).append(a)
        out = []
        for name, prop in props.items():
            if name in folded:
                parts = prop.get("properties", {})
                out += [_argument(a, parts.get(a.part or a.name, {}), False, suppress) for a in folded[name]]
            elif name != "operation_id" or name in given:
                if prop.get("type") == "object":
                    raise ValueError(f"{self.path}: {name} is an object; give it flags with into={name!r}")
                out.append(_argument(given.get(name, Arg(name)), prop, name in required, suppress))
        if self.render:
            out.append((["--json"], {"action": "store_true", "help": "print the answer as JSON"}))
        return out

    def call(self, client, args):
        """Run the tool with the parsed line's arguments; the answer is the tool's."""
        from clients import hubtools

        tool, fields = self.prepare(args)
        return hubtools.BY_NAME[tool]["fn"](client, fields)

    def prepare(self, args):
        """(the tool to run, its arguments) for a parsed line: the flags given, converted, with the tool `tool_for`
        picks."""
        dests = [kw.get("dest", names[0]) for names, kw in self.arguments() if names != ["--json"]]
        fields = {d: getattr(args, d) for d in dests if getattr(args, d, None) is not None}
        fields = {k: None if v is NULL else v for k, v in fields.items()}
        fields["operation_id"] = fields.get("operation_id") or os.environ.get("HUB_OPERATION_ID")
        if self.convert:
            fields = self.convert(fields)
        for a in self.args:
            if a.into:
                fields.pop(a.name, None)
        tool = self.tool_for(fields) if self.tool_for else self.tool
        if tool not in (self.tool, *self.tools):
            raise ValueError(f"{self.path}: tool_for picked {tool}, which is not in its tools")
        return tool, fields


def _argument(a, prop, schema_required, suppress):
    kind = prop.get("type")
    keywords = {}
    if a.metavar:
        keywords["metavar"] = a.metavar
    if a.action or kind in ("boolean", "array"):
        keywords["action"] = a.action or ("store_true" if kind == "boolean" else "append")
    help = prop.get("description") or _range(prop)
    if help and keywords.get("action") == "append":
        help += " (repeatable)"
    if help and help != a.metavar:
        keywords["help"] = help.replace("%", "%%")
    if a.const is not None:
        keywords["const"] = a.const
    takes_value = keywords.get("action") in (None, "store", "append")
    if takes_value and (a.type or kind in ("integer", "number")):
        keywords["type"] = a.type or (int if kind == "integer" else float)
    if takes_value and "enum" in prop:
        keywords["choices"] = list(prop["enum"])
    if a.nargs:
        keywords["nargs"] = a.nargs
    default = a.default
    if default is _SCHEMA and keywords.get("action") == "store_true":
        default = False
    if suppress:
        default = argparse.SUPPRESS  # a group's default leaf: a flag on the group must survive the leaf's parse
    if default is not _SCHEMA:
        keywords["default"] = default
    positional = a.positional if a.positional is not None else (schema_required and a.flag is None)
    if positional:
        return [a.name], keywords
    keywords["dest"] = a.name
    if a.required if a.required is not None else schema_required:
        keywords["required"] = True
    return [a.flag or "--" + a.name.replace("_", "-")], keywords


def _range(prop):
    low, high = prop.get("minimum"), prop.get("maximum")
    return f"{low}-{high}" if low is not None and high is not None else None


def _fill(parser, command, suppress=False):
    exclusive = {}
    for names in command.exclusive:
        exclusive.update(dict.fromkeys(names, parser.add_mutually_exclusive_group()))
    for names, keywords in command.arguments(suppress):
        (exclusive.get(keywords.get("dest", names[0])) or parser).add_argument(*names, **keywords)
    parser.set_defaults(fn=command.fn, _spec=command)


def of(args):
    """The Command a parsed line was built from, or None for a command the hand parser still builds. Kept under a
    private dest, so a command's own `--spec` flag (`tico page publish --spec`) cannot shadow it."""
    command = getattr(args, "_spec", None)
    return command if isinstance(command, Command) else None


def add(sub, specs: Iterable[Group | Command]):
    """Add the groups and commands of `specs`, in their order, under the CLI's top-level subcommands `sub`."""
    subs, groups, commands = {(): sub}, {}, {}
    for spec in specs:
        words = tuple(spec.path.split())
        parent = subs[words[:-1]]
        if isinstance(spec, Group):
            node = parent.add_parser(words[-1], aliases=list(spec.aliases), help=spec.help)
            subs[words] = node.add_subparsers(dest=DESTS[len(words)])
            groups[spec.path] = (spec, node)
        else:
            leaf = parent.add_parser(
                words[-1],
                help=spec.help,
                description=spec.help,
                epilog=f"Example: {spec.example}" if spec.example else None,
                formatter_class=argparse.RawDescriptionHelpFormatter,
            )
            _fill(leaf, spec, suppress=_is_default(spec, specs))
            commands[spec.path] = spec
    for group, node in groups.values():
        if group.default:
            _fill(node, commands[group.default], suppress=True)


def _is_default(command, specs):
    return any(isinstance(s, Group) and s.default == command.path for s in specs)
