"""The runner's `hub` lists are made from the CLI's real command tree.

runner-rs/src/hosts/hub_paths.rs holds HUB_PATHS (every path clients/hubcli.py parses, aliases and top-level renames
included) and HUB_READ_PATHS (the leaves the runner trusts to only read). scripts/hub_paths.py writes it from the
parser and the tool table; this fails when the file is out of date, so a command the CLI adds, renames or drops cannot
drift from what a run log shows. It also checks that every phrase key in backend/turns.py is a real path, and that
every read-only path parses to one command, never a group whose subcommand could write.
Light: it only builds the argparse tree, with no network.
"""

import argparse

from backend import turns
from clients import hubcli
from scripts import hub_paths


def subcommands(p):
    return next((action.choices for action in p._actions if isinstance(action, argparse._SubParsersAction)), None)


def test_the_runner_lists_are_generated_from_the_cli():
    assert hub_paths.RUST.read_text() == hub_paths.rust(), "run: python scripts/hub_paths.py"


def test_every_phrase_key_in_the_backend_is_a_real_command_path():
    spelled = {" ".join(word.replace("-", " ") for word in path.split(" ")) for path in hub_paths.paths()}
    assert [key for key in turns.HUB_PHRASES if key not in spelled] == []


def test_hand_kept_read_paths_are_real_and_none_is_built_from_a_spec():
    """A command built from a spec is read-only by its tools' `writes` flags, so moving a toolset to specs drops its
    lines from HAND_READ_PATHS."""
    real, spec = set(hub_paths.paths()), hub_paths.spec_paths()
    assert [path for path in hub_paths.HAND_READ_PATHS if path not in real or path in spec] == []


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
