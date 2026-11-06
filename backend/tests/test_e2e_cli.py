"""Every `hub` command the templates and skills tell a bot to run, once, through the real CLI against a live server.

The commands are read from templates/ and skills/ (`hub <words>`), matched to the parser's own command tree, and run
with arguments built from each command's arguments and seeded objects. A command must exit 0, or be refused with the
error recorded in e2e/cli/expected-refusals.json. A command the CLI no longer has (a rename the docs still use) fails
unless it is a rename the CLI still answers (`hubcli.rename_argv`) or e2e/cli/stale.json names it. Writes that the CLI can
preview run as dry runs.

Running every command live costs ~0.7 s each, so the live run is a deterministic sample. The static checks cover every
command each run: the scan, stale.json, the expected-refusals list, and an argparse parse of the argv built for each one.
The live sample is (1) every command in a template or skill file changed by the change under test (working tree, the
diff from origin/main, or else the last commit; read from git, ignored where git is missing), plus (2) one command per
top-level group, picked by a fixed rotation: the group's commands in sorted order, index `commit count % group size`.
Consecutive commits walk every command of every group, so all of them run within the largest group's size of commits.
TICO_E2E_CLI_ALL=1 (and recording refusals) runs every command.
"""

import argparse
import contextlib
import functools
import io
import sys
import json
import os
import re
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from backend.tests.live import block_outside_network, live  # noqa: F401
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_automation_api import NOTE_STEP
from clients import hubcli

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = json.loads((ROOT / "e2e" / "cli" / "expected-refusals.json").read_text())
SKIPPED = json.loads((ROOT / "e2e" / "cli" / "skipped.json").read_text())
# `hub <word>` in running text that is not a command: prose, or a stale reference the CLI no longer answers.
STALE = json.loads((ROOT / "e2e" / "cli" / "stale.json").read_text())
TEMPLATES = ROOT / "templates"
SKILLS = ROOT / "skills"
SKIP_FILES = (".png", ".jpg", ".gif", ".pdf", ".woff", ".woff2", ".zip", ".gz", ".sqlite", ".db")
# A `hub` command in running text: the word `hub`, then up to four words that name a command.
COMMAND = re.compile(r"(?:^|[\s`$(\"'|&;])hub ((?:[a-z][a-z0-9-]*)(?: [a-z][a-z0-9-]*){0,3})")
RECORD = os.environ.get("TICO_E2E_RECORD_REFUSALS", "")
SMOKE = "Acme smoke"


@functools.lru_cache(maxsize=None)
def command_tree():
    """The CLI's parser, built once: building it costs about 40 ms, and the scan asks about every mention."""
    return hubcli.parser()


def subparsers(parser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def resolve(words):
    """The parser path a `hub <words>` names, after the CLI's own renames: (path, leaf parser) or (None, None)."""
    argv, _ = hubcli.rename_argv(list(words))
    root = command_tree()
    node, path = root, []
    for word in argv:
        group = subparsers(node)
        if group is None or word not in group.choices:
            break
        node = group.choices[word]
        path.append(word)
    return (path, node) if path else (None, None)


def used_commands():
    """Every command path the templates and skills use, as {path: (leaf parser, a file that uses it)}, plus the
    `hub <word>` words that name no command at all (a stale reference the CLI does not answer)."""
    used, stale = {}, {}
    for path in [*sorted(TEMPLATES.rglob("*")), *sorted(SKILLS.rglob("*"))]:
        if not path.is_file() or path.suffix in SKIP_FILES:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for match in COMMAND.finditer(text):
            words = match.group(1).split(" ")
            found, leaf = resolve(words)
            rel = str(path.relative_to(ROOT))
            if found is None:
                stale.setdefault(words[0], rel)
                continue
            if subparsers(leaf) is not None or leaf.get_default("fn") is None:
                continue                      # a group named in prose (`hub task`), not a command to run
            used.setdefault(" ".join(found), (leaf, rel))
    return used, stale


def changed_files():
    """Repo paths changed by the change under test: the working tree, the diff from origin/main, else the last commit."""
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    names = set(git("diff", "--name-only", "HEAD").split()) | set(git("diff", "--name-only", "origin/main...HEAD").split())
    return names or set(git("diff", "--name-only", "HEAD~1", "HEAD").split())


def rotation():
    """The fixed rotation counter: the number of commits, 0 where git cannot say."""
    try:
        return int(subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                  timeout=20).stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def live_sample(used):
    """The command paths to run live: all of them when asked, else the changed-file commands plus one per group."""
    if RECORD or os.environ.get("TICO_E2E_CLI_ALL"):
        return set(used)
    changed = changed_files()
    chosen = {path for path in used if any(f in changed for f in used_in(path))}
    groups = {}
    for path in sorted(used):
        groups.setdefault(path.split(" ")[0], []).append(path)
    count = rotation()
    chosen.update(paths[count % len(paths)] for paths in groups.values())
    return chosen


@functools.lru_cache(maxsize=None)
def files_using():
    """{command path: every template or skill file that uses it}, for the changed-file rule."""
    found = {}
    for path in [*sorted(TEMPLATES.rglob("*")), *sorted(SKILLS.rglob("*"))]:
        if not path.is_file() or path.suffix in SKIP_FILES:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for match in COMMAND.finditer(text):
            resolved, leaf = resolve(match.group(1).split(" "))
            if resolved and subparsers(leaf) is None and leaf.get_default("fn") is not None:
                found.setdefault(" ".join(resolved), set()).add(str(path.relative_to(ROOT)))
    return found


def used_in(path):
    return files_using().get(path, ())


def argument_value(action, command):
    """A valid value for one argparse action, from its name and the kind of object the command is about."""
    dest = action.dest
    if action.choices:
        return str(next(iter(action.choices)))
    if action.type in (int, float):
        return "0" if "wait" in dest else "1"
    if dest in ("id", "task", "task_id", "parent", "ref", "item", "automation", "trigger", "message", "conversation") \
            or dest.endswith("_id"):
        return SEEDS.get(command[0], SEEDS["default"])
    if dest == "owner" and command[0] == "task":
        return "human:ana"                        # an explicit actor: the test registry has no employees.yaml
    if dest in ("bot", "owner", "to", "slug", "from_bot", "bots"):
        return "ops"
    if dest in ("person", "human", "email") or dest.endswith("_email"):
        return "smoke@acme.example"
    if dest == "name":
        return "acme-smoke"                       # a slug in most commands (`hub market page <slug>`)
    if dest in ("text", "title", "body", "note", "subject", "reason", "description", "question"):
        return SMOKE
    if dest in ("url",):
        return "https://acme.example/smoke"
    if dest in ("cron",):
        return "0 9 * * *"
    if dest in ("json", "body", "payload", "value", "input", "data", "flow"):
        return "{}"
    if "file" in dest or "path" in dest or dest in ("source", "files"):
        return "FILE"
    return "acme-smoke"


# Seeded objects: the id each command family is given (see `seeded`).
SEEDS = {"default": "acme-smoke-id"}
# Arguments a command needs that its parser does not mark required (a free-text or optional positional the handler
# refuses without), placed before the flags.
EXTRA = {
    "bot create": ["acme-smoke-bot", "--record-only"],
    "decision ask": ["--list"],
    "task child": ["--owner", "human:ana"],
    "question ask": ["Acme", "smoke", "question?"],
}


def argv_for(path, leaf, file_path):
    """argv for one command: its required positionals and options, one choice of each required exclusive group, and
    `--dry-run` where the command can preview."""
    skip, first_of_required = set(), set()
    for group in leaf._mutually_exclusive_groups:
        if group.required:
            first_of_required.add(id(group._group_actions[0]))
            skip.update(id(action) for action in group._group_actions[1:])
    argv = list(path)
    trailing = []
    for action in leaf._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)) or id(action) in skip:
            continue
        if not action.option_strings:
            # An optional positional is left out, unless it is the one choice of a required group (`approve <id>|--all`).
            if action.nargs not in ("?", "*") or id(action) in first_of_required:
                value = argument_value(action, path)
                argv.append(str(file_path) if value == "FILE" else value)
            continue
        if "--dry-run" in action.option_strings:
            trailing.append(action.option_strings[0])
            continue
        if action.required or id(action) in first_of_required:
            if action.nargs == 0:
                trailing.append(action.option_strings[0])
            else:
                value = argument_value(action, path)
                trailing += [action.option_strings[0], str(file_path) if value == "FILE" else value]
    return argv + EXTRA.get(" ".join(path), []) + trailing


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    """External effects off: a support ticket or a gateway call fails as unreachable instead of sending."""
    block_outside_network(monkeypatch, "the CLI sweep has no network")


def run_as_owner(live_url, workspace, argv):
    """`hub <argv>` as the owner, the way the CLI runs in a turn: (exit code, stdout, stderr)."""
    env = {"HUB_API_URL": live_url, "HUB_TOKEN": "ana-test", "HUB_WORKSPACE": str(workspace)}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err), _no_stdin():
            code = hubcli.main(list(argv))
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def _no_stdin():
    """A command that reads its body from stdin reads nothing, as it would with no pipe."""
    old, sys.stdin = sys.stdin, io.StringIO("")
    try:
        yield
    finally:
        sys.stdin = old


def classify(code, out, err):
    """`ok`, or the refusal a command printed: the error code of its JSON, or else the first line it wrote. A usage
    error (argparse) is a bad call from this test, not a refusal, and is named as one."""
    if code == 0:
        return "ok"
    if "usage:" in err:
        return "usage-error"
    try:
        payload = json.loads(out or "null")
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "error" in payload:
        return str(payload["error"])[:80]
    lines = (out or err).strip().splitlines()
    return ("text: " + lines[0])[:80] if lines else "silent"


@pytest.fixture
def workspace(tmp_path):
    """HUB_WORKSPACE: where bot repositories are written. Empty and private to this test."""
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def seeded_ids(api, tmp_path):
    """Objects the commands name: a task, a bot chat, an automation and a trigger, made through the API."""
    task = post(api, "tasks", {"owner": "ops", "title": "Acme smoke task", "body": "Seeded for the CLI sweep."})
    message = post(api, "chat/ops", {"text": "Acme smoke message"})
    trigger = post(api, "triggers", {"name": "Acme smoke hook"})["trigger"]
    automation = post(api, "automations", {"name": "Acme smoke automation", "owner": "person:ana", "steps": [NOTE_STEP],
                                           "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}})
    SEEDS.update({"default": "acme-smoke-id", "task": task["id"], "automation": automation["id"],
                  "trigger": trigger["id"], "chat": message["conversation_id"], "conversation": message["conversation_id"],
                  "message": message["id"]})
    file_path = tmp_path / "acme-smoke.txt"
    file_path.write_text("Acme smoke file\n")
    yield file_path
    SEEDS.clear()
    SEEDS["default"] = "acme-smoke-id"


@pytest.mark.slow
def test_every_hub_command_in_templates_and_skills_runs_or_is_its_recorded_refusal(live, seeded_ids, workspace):
    used, stale = used_commands()
    assert len(used) > 150, f"only {len(used)} commands found: the scan is not reading templates and skills"
    problems, observed = [], {}
    sample = live_sample(used)
    for path, (leaf, where) in sorted(used.items()):
        if path in SKIPPED:
            continue
        argv = argv_for(path.split(" "), leaf, seeded_ids)
        try:                                      # every command's argv parses, run live or not
            command_tree().parse_args(argv)
        except SystemExit:
            problems.append(f"hub {path} ({where}): the argv built for it does not parse: {argv}")
            continue
        if path not in sample:
            continue
        try:
            code, out, err = run_as_owner(live, workspace, argv)
        except Exception as exc:       # a command that raises instead of exiting is the failure this test is for
            problems.append(f"hub {path}: raised {type(exc).__name__}: {exc}")
            continue
        result = classify(code, out, err)
        if result != "ok":
            observed[path] = result
        if RECORD:
            continue
        if result == "ok":
            if path in EXPECTED:
                problems.append(f"hub {path}: was refused as {EXPECTED[path]!r} and now runs; update expected-refusals.json")
        elif result in ("usage-error", "silent"):
            problems.append(f"hub {path} ({where}): {result}: {(out + err)[:200]!r}")
        elif EXPECTED.get(path) != result:
            problems.append(f"hub {path} ({where}): refused as {result!r} (recorded: {EXPECTED.get(path)!r})")
    unlisted = sorted(f"hub {word} (in {where})" for word, where in stale.items() if word not in STALE)
    if unlisted:
        problems.append("`hub` words that name no CLI command and are not in stale.json: " + ", ".join(unlisted))
    if RECORD:
        Path(RECORD).write_text(json.dumps(dict(sorted(observed.items())), indent=1, ensure_ascii=False) + "\n")
        assert not [p for p in problems if "raised" in p], "\n".join(problems)
        return
    stale_entries = sorted(set(EXPECTED) - set(used))
    if stale_entries:
        problems.append("expected-refusals.json names commands no template or skill uses: " + ", ".join(stale_entries))
    gone = sorted(word for word in STALE if word not in stale)
    if gone:
        problems.append("stale.json lists words no template or skill uses any more: " + ", ".join(gone))
    assert not problems, "\n".join(problems) + (
        "\nFix unexpected command failures first. For intended refusal changes, edit e2e/cli/expected-refusals.json; "
        "remove entries no template or skill uses. Fix stale command references or update e2e/cli/stale.json "
        "(remove unused entries). To regenerate refusals for review: "
        "TICO_E2E_RECORD_REFUSALS=/tmp/cli-refusals.json python3 -m pytest -q -m slow backend/tests/test_e2e_cli.py. "
        "Review the diff before copying it over e2e/cli/expected-refusals.json.")
