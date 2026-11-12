"""The runner's `hub` command lists, made from the CLI: runner-rs/src/hosts/hub_paths.rs.

`python scripts/hub_paths.py` writes the file; backend/tests/test_hub_command_tree.py fails when it is out of date, so
a command added, renamed or dropped in clients/hubcli.py (or a spec in clients/commands/<noun>.py) is one edit plus
running this.

- HUB_PATHS: every path the CLI parses, its aliases and its top-level renames (hubcli.RENAMED) included.
- HUB_READ_PATHS: the leaf paths that only read, which the runner may run without asking. A command built from a spec
  (clients/cli_spec.py) is one when none of the tools it can run writes (`writes` on the tool) or runs on this computer
  (`local`: it saves files), and it is not a paid lookup (PAID). The commands the hand parser still builds are listed in
  HAND_READ_PATHS, which shrinks as they move to specs. Left out on purpose: reads that mark something (`inbox`,
  `message list`, `update read`, `changelog list`), reads with a writing flag (`rules read --from`), paid lookups
  (`gateway search`, `doc fetch`), checks, and every group or alias of a group (`status`, `org`, `docs`).
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clients import cli_spec, hubcli, hubtools

RUST = ROOT / "runner-rs" / "src" / "hosts" / "hub_paths.rs"

HAND_READ_PATHS = (
    "agent pair show",
    "alerts show",
    "assistant change list",
    "automation guide",
    "automation list",
    "automation runs",
    "automation show",
    "automation stats",
    "automation templates",
    "automation versions",
    "automations guide",
    "automations list",
    "automations runs",
    "automations show",
    "automations stats",
    "automations templates",
    "automations versions",
    "board",
    "bot list",
    "bot recent",
    "bot show",
    "bot status history",
    "bot status list",
    "bots list",
    "bots recent",
    "bots show",
    "bots status history",
    "bots status list",
    "calendar free",
    "calendar list",
    "calendar search",
    "calendar status",
    "calendar upcoming",
    "call list",
    "call show",
    "catalog",
    "computers",
    "contacts describe",
    "contacts find",
    "contacts get",
    "contacts history",
    "contacts rules",
    "contacts self",
    "contacts types",
    "context search",
    "context show",
    "conversation show",
    "credential list",
    "doc history",
    "doc list",
    "doc read",
    "doc search",
    "docs links",
    "feedback list",
    "feedback markers",
    "feedback reasons",
    "file list",
    "file read",
    "file search",
    "goal checkin list",
    "goals",
    "group list",
    "gtm show",
    "gtm summary",
    "history",
    "integrations",
    "listening sources",
    "live stats",
    "mcp stats",
    "meeting granola status",
    "meeting read",
    "meeting search",
    "needs-you show",
    "note list",
    "notes",
    "origin show",
    "recent",
    "replay show",
    "repo list",
    "run list",
    "service-key list",
    "settings find",
    "settings show",
    "slack channel list",
    "task caps",
    "task context",
    "task deleted",
    "task followers",
    "task list",
    "task meetings",
    "task readers list",
    "task requesters",
    "task show",
    "task tree",
    "tasks caps",
    "tasks context",
    "tasks deleted",
    "tasks followers",
    "tasks list",
    "tasks meetings",
    "tasks readers list",
    "tasks requesters",
    "tasks show",
    "tasks tree",
    "team show",
    "template list",
    "tool list",
    "tool show",
    "turns",
    "update list",
    "update show",
    "updates",
    "whoami",
)


def _walk(p, path=()):
    """(path, parser) for the root's every node, aliases included."""
    yield path, p
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _walk(sub, (*path, name))


def _is_leaf(p):
    return not any(isinstance(action, argparse._SubParsersAction) for action in p._actions)


def paths():
    """Every command path the CLI parses, as space-joined words."""
    out = {" ".join(path) for path, _p in _walk(hubcli.parser()) if path}
    for old, _new, *_ in hubcli.RENAMED:
        out |= {" ".join(old[:n]) for n in range(1, len(old) + 1)}
    return sorted(out)


def spec_paths():
    """{path: Command} for every leaf path built from a spec, aliases included."""
    out = {}
    for path, p in _walk(hubcli.parser()):
        command = cli_spec.of(argparse.Namespace(**p._defaults))
        if command is not None and _is_leaf(p):
            out[" ".join(path)] = command
    return out


# Spec'd commands whose tools neither write nor run on this computer, but spend: from the company's Gateway cap, or
# (`market ask`) a model call on the server's key.
PAID = ("gateway search", "gateway fetch", "gateway digest", "market ask")


def read_paths():
    reads = set(HAND_READ_PATHS)
    for path, command in spec_paths().items():
        tools = [hubtools.BY_NAME[name] for name in (command.tool, *command.tools)]
        if path not in PAID and not any(tool["writes"] or tool["local"] for tool in tools):
            reads.add(path)
    return sorted(reads)


def _const(name, doc, items):
    lines = [f"/// {line}" if line else "///" for line in doc]
    lines.append(f"pub(super) const {name}: &[&str] = &[")
    lines += [f'    "{item}",' for item in items]
    lines.append("];")
    return "\n".join(lines)


def rust():
    """The text of runner-rs/src/hosts/hub_paths.rs."""
    return (
        "// @generated by scripts/hub_paths.py from clients/hubcli.py and the tool table; do not edit.\n"
        "// backend/tests/test_hub_command_tree.py fails when this file is out of date.\n\n"
        + _const(
            "HUB_PATHS",
            [
                "Every command path the tico CLI parses, its aliases and its top-level renames included, as space-joined",
                "words such as `task close` or `needs-you show`.",
            ],
            paths(),
        )
        + "\n\n"
        + _const(
            "HUB_READ_PATHS",
            [
                "`hub` command paths that only read: each is a leaf in HUB_PATHS (tested in mod.rs). A spec'd command is",
                "one when none of its tools writes; scripts/hub_paths.py says which are left out on purpose.",
            ],
            read_paths(),
        )
        + "\n"
    )


if __name__ == "__main__":
    RUST.write_text(rust())
    print(f"wrote {RUST.relative_to(ROOT)}")
