"""The runner's `hub` summary reads the CLI's real command paths.

runner-rs/src/hosts/mod.rs keeps HUB_PATHS: every path clients/hubcli.py parses, its aliases and its top-level renames
included. This walks the real parser and fails when the two differ, so a command the CLI adds, renames or drops cannot
drift from what a run log shows. It also checks that every phrase key in backend/turns.py is a real path, and that every
read-only path the runner trusts (HUB_READ_PATHS) parses to one command, never a group whose subcommand could write.
Light: it only builds the argparse tree, with no network.
"""

import argparse
import re
from pathlib import Path

from backend import turns
from clients import hubcli

RUNNER_SOURCE = Path(__file__).resolve().parents[2] / "runner-rs" / "src" / "hosts" / "mod.rs"


def parser_paths(p, prefix=()):
    out = set()
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                path = prefix + (name,)
                out.add(path)
                out |= parser_paths(sub, path)
    return out


def cli_paths():
    paths = parser_paths(hubcli.parser())
    for old, _new, *_ in hubcli.RENAMED:
        paths |= {tuple(old[:n]) for n in range(1, len(old) + 1)}
    return paths


def runner_list(name):
    listed = re.search(rf"const {name}: &\[&str\] = &\[(.*?)\];", RUNNER_SOURCE.read_text(), re.S)
    assert listed, f"{name} is not in runner-rs/src/hosts/mod.rs"
    return re.findall(r'"([^"]*)"', listed.group(1))


def runner_paths():
    return {tuple(path.split(" ")) for path in runner_list("HUB_PATHS")}


def subcommands(p):
    return next((action.choices for action in p._actions if isinstance(action, argparse._SubParsersAction)), None)


def test_the_runner_lists_exactly_the_command_paths_the_hub_cli_parses():
    assert runner_paths() == cli_paths()


def test_every_phrase_key_in_the_backend_is_a_real_command_path():
    spelled = {" ".join(word.replace("-", " ") for word in path) for path in cli_paths()}
    assert [key for key in turns.HUB_PHRASES if key not in spelled] == []


def test_every_read_only_path_the_runner_trusts_parses_to_one_command():
    groups = []
    for path in runner_list("HUB_READ_PATHS"):
        argv, _notice = hubcli.rename_argv(path.split(" "))
        p = hubcli.parser()
        for word in argv:
            if word.startswith("-"):
                break
            assert word in (subcommands(p) or {}), f"hub {path} does not parse"
            p = subcommands(p)[word]
        if subcommands(p) is not None:
            groups.append(path)
    assert groups == []
