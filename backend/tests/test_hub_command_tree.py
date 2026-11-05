"""The runner's `hub` summary reads the CLI's real command paths.

runner-rs/src/hosts/mod.rs keeps HUB_PATHS: every path clients/hubcli.py parses, its aliases and its top-level renames
included. This walks the real parser and fails when the two differ, so a command the CLI adds, renames or drops cannot
drift from what a run log shows. It also checks that every phrase key in backend/turns.py is a real path. Light: it only
builds the argparse tree, with no network.
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


def runner_paths():
    listed = re.search(r"const HUB_PATHS: &\[&str\] = &\[(.*?)\];", RUNNER_SOURCE.read_text(), re.S)
    assert listed, "HUB_PATHS is not in runner-rs/src/hosts/mod.rs"
    return {tuple(path.split(" ")) for path in re.findall(r'"([^"]*)"', listed.group(1))}


def test_the_runner_lists_exactly_the_command_paths_the_hub_cli_parses():
    assert runner_paths() == cli_paths()


def test_every_phrase_key_in_the_backend_is_a_real_command_path():
    spelled = {" ".join(word.replace("-", " ") for word in path) for path in cli_paths()}
    assert [key for key in turns.HUB_PHRASES if key not in spelled] == []
