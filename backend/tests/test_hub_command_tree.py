"""The runner's `hub` lists are made from the CLI's real command tree.

runner-rs/src/hosts/hub_paths.rs holds HUB_PATHS (every path clients/hubcli.py parses, aliases and top-level renames
included) and HUB_READ_PATHS (the leaves the runner trusts to only read). scripts/hub_paths.py writes it from the
parser and the tool table; this fails when the file is out of date, so a command the CLI adds, renames or drops cannot
drift from what a run log shows. It also checks that every phrase key in backend/turns.py is a real path, and that
every read-only path parses to one command, never a group whose subcommand could write.
Light: it only builds the argparse tree, with no network.
"""

import argparse

from clients import hubcli
from scripts import hub_paths


def subcommands(p):
    return next((action.choices for action in p._actions if isinstance(action, argparse._SubParsersAction)), None)


def test_every_read_only_path_the_runner_trusts_parses_to_one_command():
    groups = []
    for path in hub_paths.read_paths():
        argv, _notice = hubcli.rename_argv(path.split(" "))
        p = hubcli.parser()
        for word in argv:
            if word.startswith("-"):
                break
            assert word in (subcommands(p) or {}), f"tico {path} does not parse"
            p = subcommands(p)[word]
        if subcommands(p) is not None:
            groups.append(path)
    assert groups == []
