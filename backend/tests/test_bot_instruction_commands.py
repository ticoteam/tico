"""Every `hub` command a bot is taught must parse with clients/hubcli.py, and every task command must run as taught.

Bots copy the commands they read in policies/, the templates their repositories are made from (AGENT.md, playbooks,
skills), the shared skills, the integration pages, the runner's prompt and the BotOps routine's text. When the task
engine changed (docs/pipelines-v3.md), those texts kept teaching commands that no longer existed, so a bot in a new
company failed on its first task. Parsing was not enough either: `hub task label <id> --add x` parsed, but the CLI
refused it before sending anything ("Run `hub task show` first", even right after a show), so a bot that followed the
text looped.

So: every `hub` command quoted in those texts is parsed with the real parser (a placeholder the bot fills in, such as
`--kind <kind>` or `--content JSON`, may be refused for its sample value; a line that leaves out a required argument
passes, since argparse names what is missing), a command the CLI took away or renamed fails, and every whole task, OK
and Needs-you line is run through the real CLI (hubcli.main, remotecli, hubtools) against a stand-in server, so a
refusal the CLI makes on its own (its "usage" answers) fails here. Nothing is sent to a server."""

import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from backend import routines
from backend.tests.helpers.assistant_template_commands import argv_of, commands_in, parse_error, variants
from clients import hubcli, remotecli

ROOT = Path(__file__).resolve().parents[2]
# Where a bot reads the commands it runs: policies, the templates (AGENT.md, playbooks, skills, knowledge), the shared
# skills, the integration pages and the runner's prompt as it renders (its goldens).
FOLDERS = ("policies", "templates", "skills", "integrations", "runner-rs/src/testdata")
SUFFIXES = (".md", ".yaml", ".txt")
# The commands run as taught, not only parsed: the task engine's, whose CLI checks a line before it sends it.
RUN_GROUPS = ("task", "tasks", "ok", "needs-you")
SPAN = re.compile(r"`(hub [^`]+)`")
PATH = re.compile(r"[\w./-]+")
# A value the text leaves for the bot to fill in (`--kind <kind>`, `--content JSON`, `NUMBER`, `--period-start D`)
# gets a sample that a choice, a number or a JSON flag may refuse; the bot puts in a real one.
FILLED = re.compile(
    r"invalid choice: '(1|\.\.\.)'|invalid int value: '[A-Z_]+'|must be a JSON object|is JSON, for example"
)


def cases():
    out = []
    for folder in FOLDERS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            for number, text in commands_in(path):
                argv = argv_of(text)
                if argv:
                    out.append((str(path.relative_to(ROOT)), number, text, argv))
    for match in SPAN.finditer(routines.SWEEP_TEXT):
        argv = argv_of(match.group(1))
        if argv:
            out.append(("backend/routines.py SWEEP_TEXT", 0, match.group(1), argv))
    return out


def mention(argv):
    """A command named in prose (`hub task create`, `hub meeting read <id> --offset`): it names the command and maybe a
    flag, not a whole line to run."""
    return len([a for a in argv if not a.startswith("-")]) <= 2 or argv[-1].startswith("-")


def test_the_texts_name_commands_to_check():
    # Guard the guard: if this finds nothing, the checks below would pass for the wrong reason.
    found = cases()
    assert len(found) > 3000
    assert {where.split("/")[0] for where, *_ in found} >= {"policies", "templates", "skills", "integrations"}
    assert any(where.startswith("runner-rs/") for where, *_ in found)
    assert len([1 for *_, argv in found if argv[0] in RUN_GROUPS]) > 1000


def test_every_command_a_bot_is_taught_parses():
    parser = hubcli.parser()
    failures = []
    for where, number, text, argv in cases():
        if argv == ["\x00unbalanced quotes"]:
            failures.append(f"{where}:{number}: unbalanced quotes in `{text}`")
            continue
        gone = hubcli.removed_notice(argv)
        if gone:
            failures.append(f"{where}:{number}: `{text}` is taught, but {gone}")
            continue
        argv, renamed = hubcli.rename_argv(argv)
        if renamed:
            failures.append(f"{where}:{number}: `{text}` is taught by its old name: {renamed}")
        for option in variants(argv):
            error = parse_error(option, parser)
            if error is None or FILLED.search(error):
                continue
            # A line that leaves out a required argument is refused by argparse with what is missing and an example
            # (`hub task create --owner <bot>` in prose): the bot is told what to add, it does not loop.
            if "the following arguments are required" in error or mention(option) and "expected one argument" in error:
                continue
            failures.append(f"{where}:{number}: `{' '.join(option)}` does not parse: {error}")
    assert not failures, "\n".join(failures)


class Any(dict):
    """A stand-in server's answer: whatever a command reads from it is there, empty."""

    def __missing__(self, key):
        return Any()


class Server:
    """The API a command calls: it answers every request with a task the bot may change (version 1, one ask of its
    own to withdraw), and refuses nothing. Only the CLI's own refusals are left to see."""

    def __init__(self, *args, **kwargs):
        self.calls = []

    def _answer(self, *args, **kwargs):
        self.calls.append(args)
        task = Any(id="T1", version=1, labels=[], title="Acme task", to_leave=[{"requirement_id": "r1", "may": ["withdraw"]}])
        return Any(task=task, tasks=[task], items=[])

    def __getattr__(self, name):
        return self._answer

    def features(self):
        return {}


def runs(argv, tmp_path, monkeypatch):
    """`hub <argv>` through the CLI against the stand-in: (exit code, what it printed)."""
    monkeypatch.setattr(remotecli, "Client", Server)
    for token in argv:  # a file the line names exists, as it would in the bot's checkout
        if PATH.fullmatch(token) and not token.startswith(("-", "/", "..")) and ("/" in token or "." in token):
            (tmp_path / token).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / token).write_text("Acme\n")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = hubcli.main(list(argv))
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue()


def test_every_task_command_a_bot_is_taught_runs_as_taught(tmp_path, monkeypatch):
    """The CLI refuses no taught task, OK or Needs-you line on its own: a line that parses is sent."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "1").write_text("Acme\n")  # `hub task attach <id> <file>`: the file the bot attaches
    (tmp_path / "f").write_text("Acme\n")
    for name, value in {
        "HUB_API_URL": "https://tico.acme.example/api/v2",
        "HUB_TOKEN": "acme-token",
        "HUB_BOT": "builder",
        "HUB_WORKSPACE": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("HUB_OPERATION_ID", raising=False)
    parser = hubcli.parser()
    failures, ran = [], set()
    for where, number, text, argv in cases():
        # `hub task worktree ...` runs git in the bot's own checkout (runner_support/worktrees.py): parsed only.
        if argv[0] not in RUN_GROUPS or mention(argv) or argv[1:2] == ["worktree"]:
            continue
        for option in variants(argv):
            line = " ".join(option)
            if line in ran or parse_error(option, parser) is not None:
                continue
            ran.add(line)
            code, printed = runs(option, tmp_path, monkeypatch)
            if code in (0, None):
                continue
            try:
                answer = json.loads(printed)
            except ValueError:
                answer = {"error": printed.strip()[:300]}
            failures.append(f"{where}:{number}: `hub {line}` is refused by the CLI: {answer}")
    assert len(ran) > 100
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize(
    "line",
    [
        ["task", "label", "T1", "--add", "pricing-page"],  # policies/handoffs.md, with no version
        ["task", "label", "T1", "--remove", "growth:running", "--add", "growth:measuring"],
    ],
)
def test_the_taught_label_line_is_sent(line, tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    sent = []

    class Recorder(Server):
        def post(self, path, body=None, key=None):
            sent.append((path, body))
            return Any(task=Any(id="T1"))

    monkeypatch.setattr(remotecli, "Client", Recorder)
    with contextlib.redirect_stdout(io.StringIO()):
        assert hubcli.main(line) == 0
    assert sent and sent[0][0] == "tasks/T1" and sent[0][1]["version"] == 1
