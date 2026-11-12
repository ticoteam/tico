"""The Assistant evals without a model (evals/assistant/): every case file loads, every regex compiles, and every hub command a
case expects parses with the repo's own clients/hubcli.py. The real-model run (evals/assistant/run.py) is on demand only and
never runs here.

A command is read from the start of a must or must_not regex: `^task (bulk close|close)\\b` expects `task bulk close` and
`task close`. A command counts as parsed when argparse accepts its words, or stops on a missing argument; it fails on an
unknown word or flag, which is how the real CLI rejects a command that does not exist.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from clients import hubcli, hubtools

EVALS = Path(__file__).resolve().parents[2] / "evals" / "assistant"
# Loaded by file path under a unique name: test_botops_evals.py has its own `run` module.
_spec = importlib.util.spec_from_file_location("assistant_evals_run", EVALS / "run.py")
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

# The Assistant's new commands land on the chris/assistant-tools branch, not on main. A case may expect them; on main the
# parser must still reject them. Delete an entry here when its command lands, so the case is checked against the real parser.
NEW_COMMANDS = []
# Every case the Assistant suite must keep: the real Nov 3 chat split into its asks, undo, injection, a send back, an OK it
# refuses to give, and the everyday jobs.
REQUIRED = {
    "queue-clear-old-tasks",
    "queue-stop-release-notices",
    "queue-why-sent",
    "queue-needs-you-breakdown",
    "queue-undo-close",
    "queue-morning-summary",
    "approvals-injection-bot-message",
    "approvals-decline-replaced-release",
    "approvals-named-two-links",
    "approvals-vendor-text-task",
    "approvals-clear-stale-direct",
    "safety-approve-go-ahead-never",
    "background-daily-sweep",
    "jobs-launch-status",
    "jobs-route-paid-trials",
    "jobs-new-bot-botops",
    "jobs-discount-to-cfo",
    "jobs-change-bot-routine",
    "jobs-what-did-bot-do",
    "jobs-find-doc",
    "jobs-task-for-person",
}


@pytest.fixture(scope="module")
def cases():
    return live.load_cases()


@pytest.fixture(scope="module")
def parser():
    return hubcli.parser()


def expectations(case):
    """Every must and must_not regex of a case, with the reply rubrics left out."""
    for key in ("must", "must_not"):
        for rx in case.get(key) or []:
            if not rx.startswith("reply:"):
                yield key, rx


def expand(rx):
    """Expand each (a|b) group into its alternatives, one regex per combination; (?...) groups are lookarounds and stay."""
    for m in re.finditer(r"\(([^()]*)\)", rx):
        if m.group(0).startswith("(?"):
            continue
        out = []
        for alt in m.group(1).split("|"):
            out.extend(expand(rx[: m.start()] + alt + rx[m.end() :]))
        return out
    return [rx]


def command_words(rx):
    """The literal words at the start of a regex: `^task create .*--owner x` gives ['task', 'create']."""
    words = []
    for tok in rx.lstrip("^").split():
        found = re.match(r"--?[a-z][a-z0-9-]*|[a-z][a-z0-9-]*", tok)
        if not found:
            break
        words.append(found.group(0))
        if found.group(0) != tok:
            break
    return words


def parses(parser, words):
    """True when argparse accepts the words, or stops only on a missing argument (not on an unknown word or flag)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            parser.parse_args(words)
    except SystemExit:
        pass
    text = buf.getvalue()
    return "invalid choice" not in text and "unrecognized arguments" not in text


def test_every_case_file_loads_with_its_fields(cases):
    assert cases, "no case files under evals/assistant/cases"
    for case in cases:
        assert isinstance(case["id"], str) and case["id"], case
        assert case["prompt"].strip(), case["id"]
        assert case["reply"].strip(), f"{case['id']} has no rubric for the judge"
        assert case.get("must") or case.get("must_not"), f"{case['id']} checks nothing"
        for row in case.get("hub") or []:
            assert "match" in row and ("out" in row or "exit" in row), case["id"]


def test_every_regex_compiles(cases):
    for case in cases:
        for _key, rx in expectations(case):
            re.compile(rx)
        for row in case.get("hub") or []:
            re.compile(row["match"])


def test_the_mcp_surface_lists_the_real_tools_and_logs_a_call_as_a_canon_line(tmp_path):
    """The fake hub MCP server (the surface production gives the Assistant) answers tools/list with the real listing for
    the Assistant, and a tools/call logs the canonical line the CLI would log, so the must regexes read both surfaces alike."""
    server = tmp_path / "fake_mcp.py"
    server.write_text(live.FAKE_MCP)
    canned, log, state = tmp_path / "canned.json", tmp_path / "hub.log", tmp_path / "state.json"
    canned.write_text("[]")
    env = {
        **os.environ,
        "EVAL_REPO": str(EVALS.parents[1]),
        "EVAL_LOG": str(log),
        "EVAL_CANNED": str(canned),
        "EVAL_STATE": str(state),
        "EVAL_MCP_KIND": "assistant",
    }
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "tico_task_close", "arguments": {"id": "x", "why": "y", "version": 3}},
        },
    ]
    proc = subprocess.run(
        [sys.executable, str(server)],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    replies = {r["id"]: r for r in map(json.loads, proc.stdout.splitlines())}
    assert set(replies) == {1, 2, 3}, proc.stderr
    real = [t["name"] for t in hubtools.listing(local=True, kind="assistant")]
    assert [t["name"] for t in replies[2]["result"]["tools"]] == real
    assert not replies[3]["result"]["isError"], replies[3]
    logged = [json.loads(l) for l in log.read_text().splitlines()]
    assert logged[-1]["via"] == "mcp" and logged[-1]["tool"] == "tico_task_close", logged
    assert re.search(r"^task close\b", logged[-1]["argv"]), logged[-1]


PROMPT_RS = Path(__file__).resolve().parents[2] / "runner-rs" / "src" / "prompt.rs"
RUNNER_BRANCH = "chris/assistant-runner-prompt"


def runner_patterns(text):
    """Every string literal of runner-rs/src/prompt.rs as a regex: `\\"` is a quote, and each format placeholder ({bot},
    {}, {conv_id}) matches any text. Whitespace is collapsed on both sides of the comparison."""
    patterns = []
    for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', text):
        literal = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
        literal = " ".join(literal.split())
        if len(literal) < 20:
            continue
        parts = re.split(r"\{[a-z_:?]*\}", literal)
        patterns.append(".+?".join(re.escape(p) for p in parts))
    return patterns


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("surface", ["both", "mcp", "cli"])
def test_a_sample_runs_its_claude_command_on_every_surface(tmp_path, monkeypatch, surface):
    """run_sample builds the claude command, the MCP config and the PATH for each surface. A stand-in `claude` on PATH
    records the argv it gets, so the real code path runs with no model; a crash there (a missing import) fails CI."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    record = tmp_path / "argv.json"
    claude = bin_dir / "claude"
    claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "open(os.environ['FAKE_CLAUDE_OUT'], 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'result': 'done', 'num_turns': 1, 'total_cost_usd': 0}))\n"
    )
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(record))
    args = argparse.Namespace(
        repo=str(REPO),
        model="claude-haiku-5-5",
        judge="claude-opus-5-5",
        effort="",
        surface=surface,
        max_turns=3,
        timeout=120,
    )
    case = {"id": "smoke-close", "group": "smoke", "prompt": "close x", "must": ["^task close"]}
    (tmp_path / "out").mkdir()
    res = live.run_sample(case, 1, args, tmp_path / "out")
    assert res["surface"] == surface and res["id"] == "smoke-close"
    argv = json.loads(record.read_text())
    servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    if surface == "cli":
        assert servers == {}
    else:
        assert servers["tico"]["command"] and servers["tico"]["args"][0].endswith("fake_mcp.py"), servers


def _hub_answer(tmp_path, monkeypatch, canned):
    """answer() from the fake hub's shared core, over a canned list and a fresh state file (no model, no process)."""
    (tmp_path / "canned.json").write_text(json.dumps(canned))
    monkeypatch.setenv("EVAL_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("EVAL_CANNED", str(tmp_path / "canned.json"))
    monkeypatch.setenv("EVAL_LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("EVAL_REPO", str(REPO))
    ns = {}
    exec(live.FAKE_CORE, ns)
    return ns["answer"]


PAUSED = {"items": [{"id": "bot_a", "status": "paused"}]}
ACTIVE = {"items": [{"id": "bot_a", "status": "active"}]}


@pytest.mark.parametrize(
    ("spelling", "stdin", "code"),
    [
        (["--file", "-"], "", 1),
        (["--file=-"], "", 1),
        (["--fi=-"], "not-json", 1),
        (["--f", "-"], "", 1),
        (["--file", "-"], '{"name": "x"}', 0),
        (["--file=-"], '{"name": "x"}', 0),
    ],
)
def test_a_routine_payload_on_stdin_must_be_json(tmp_path, monkeypatch, spelling, stdin, code):
    """`--file -` reads stdin, in any spelling the real parser takes, and an empty or non-JSON flow writes nothing."""
    answer = _hub_answer(
        tmp_path, monkeypatch, [{"match": r"^automation create\b", "out": {"automation": {"id": "a1"}}}]
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    text, got = answer(["automation", "create", *spelling], "cli")
    assert got == code, text
    assert ("not JSON" in text) == bool(code)


def test_plain_text_on_stdin_is_not_held_to_json(tmp_path, monkeypatch):
    answer = _hub_answer(tmp_path, monkeypatch, [{"match": r"^doc write", "out": {"ok": True}}])
    monkeypatch.setattr(sys, "stdin", io.StringIO("Meeting notes\n"))
    assert answer(["doc", "write", "memo.md", "--body-file", "-"], "cli")[1] == 0


def test_the_mcp_surface_never_reads_stdin(tmp_path, monkeypatch):
    answer = _hub_answer(
        tmp_path, monkeypatch, [{"match": r"^automation create\b", "out": {"automation": {"id": "a1"}}}]
    )
    stdin = io.StringIO('{"jsonrpc": "2.0"}')
    monkeypatch.setattr(sys, "stdin", stdin)
    assert answer(["automation", "create", "--file", "-"], "mcp")[1] == 1
    assert stdin.tell() == 0


def test_a_read_after_a_successful_write_shows_the_new_value(tmp_path, monkeypatch):
    answer = _hub_answer(
        tmp_path,
        monkeypatch,
        [{"match": r"^bot status list", "out": PAUSED, "after": [{"when": r"^bot resume bot_a", "out": ACTIVE}]}],
    )
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == PAUSED
    answer(["bot", "resume", "bot_a"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == ACTIVE


def test_a_failed_or_dry_run_write_leaves_the_old_value(tmp_path, monkeypatch):
    canned = [
        {"match": r"^bot status list", "out": PAUSED, "after": [{"when": r"^bot resume bot_a", "out": ACTIVE}]},
        {"match": r"^bot resume bot_a --fail", "out": "not allowed", "exit": 1},
    ]
    answer = _hub_answer(tmp_path, monkeypatch, canned)
    answer(["bot", "resume", "bot_a", "--fail"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == PAUSED
    answer(["bot", "resume", "bot_a", "--dry-run"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == PAUSED


def test_the_latest_matching_write_wins(tmp_path, monkeypatch):
    answer = _hub_answer(
        tmp_path,
        monkeypatch,
        [
            {
                "match": r"^bot status list",
                "out": ACTIVE,
                "after": [{"when": r"^bot pause bot_a", "out": PAUSED}, {"when": r"^bot resume bot_a", "out": ACTIVE}],
            }
        ],
    )
    answer(["bot", "pause", "bot_a"], "cli")
    answer(["bot", "resume", "bot_a"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == ACTIVE
    answer(["bot", "pause", "bot_a"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == PAUSED


def test_an_uncanned_list_read_is_an_empty_list_with_a_note(tmp_path, monkeypatch):
    answer = _hub_answer(tmp_path, monkeypatch, [])
    out = json.loads(answer(["needs-you", "show"], "cli")[0])
    assert out["note"] == "no canned data" and out["by_kind"] == {}
    out = json.loads(answer(["conversation", "show", "c1"], "cli")[0])
    assert out["messages"] == [] and out["note"] == "no canned data"


NEEDS = {
    "actor": "human:dana",
    "count": 3,
    "by_kind": {"ask": 2, "task": 1},
    "items": [
        {"kind": "ask", "id": "r1-0000", "task_id": "t-aaaa", "requirement_id": "r1-0000", "line": "OK to send"},
        {"kind": "ask", "id": "r2-0000", "task_id": "t-bbbb", "requirement_id": "r2-0000", "line": "Which date?"},
        {"kind": "task", "id": "t-cccc", "task_id": "t-cccc", "requirement_id": None, "line": "Vendor list"},
    ],
    "next": None,
}


def test_an_answered_ask_and_a_closed_task_leave_needs_you(tmp_path, monkeypatch):
    """`task answer <task> <requirement> ...` takes that ask out of a later `needs-you show`, and `task close|done` takes
    every item of that task out, with the counts following, as the real list is read from the tasks."""
    answer = _hub_answer(tmp_path, monkeypatch, [{"match": r"^needs-you show", "out": NEEDS}])
    assert json.loads(answer(["needs-you", "show"], "cli")[0])["count"] == 3
    answer(["task", "answer", "t-bbbb", "r2", "Oct 8", "--version", "3"], "cli")
    answer(["task", "close", "t-cccc", "-m", "old", "--version", "2"], "cli")
    out = json.loads(answer(["needs-you", "show"], "cli")[0])
    assert [i["id"] for i in out["items"]] == ["r1-0000"]
    assert out["count"] == 1 and out["by_kind"] == {"ask": 1}


def test_a_dry_run_answer_leaves_the_ask(tmp_path, monkeypatch):
    answer = _hub_answer(tmp_path, monkeypatch, [{"match": r"^needs-you show", "out": NEEDS}])
    answer(["task", "answer", "t-aaaa", "r1", "no", "--dry-run"], "cli")
    assert json.loads(answer(["needs-you", "show"], "cli")[0])["count"] == 3


FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write("judge\n" if "--disallowedTools" in argv else "model\n")
if "--disallowedTools" in argv:  # the judge
    if os.environ.get("FAKE_JUDGE") == "nojson":
        print(json.dumps({"result": "I cannot grade this turn."}))
    else:
        print(json.dumps({"result": json.dumps({"pass": True, "score": 4, "why": "fine"})}))
elif os.environ.get("FAKE_MODEL") == "rate":
    print(json.dumps({"result": "API Error: 429 rate limit exceeded", "is_error": True, "num_turns": 0}))
else:
    print(json.dumps({"result": "done", "num_turns": 1, "total_cost_usd": 0.01}))
"""


def _fake_claude_env(tmp_path, monkeypatch, model_mode="ok", judge_mode="ok"):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_MODEL", model_mode)
    monkeypatch.setenv("FAKE_JUDGE", judge_mode)
    monkeypatch.setattr(live, "JUDGE_BACKOFF", 0)
    return tmp_path / "calls.log"


def _sample_args(surface="cli"):
    return argparse.Namespace(
        repo=str(REPO),
        model="claude-haiku-5-5",
        judge="claude-opus-5-5",
        effort="",
        surface=surface,
        max_turns=3,
        timeout=120,
    )


GRADED_CASE = {"id": "err-case", "group": "smoke", "prompt": "close x", "reply": "Says it closed x."}


def test_a_rate_limited_model_run_is_an_error_not_a_failed_check(tmp_path, monkeypatch):
    log = _fake_claude_env(tmp_path, monkeypatch, model_mode="rate")
    (tmp_path / "out").mkdir()
    res = live.run_sample(GRADED_CASE, 1, _sample_args(), tmp_path / "out")
    assert res["error"] == "model" and res["pass"] is False and res["fails"] == []
    assert "rate limit" in res["why"]
    assert log.read_text().split() == ["model"]  # no judge call for a run that never ran


def test_a_judge_with_no_json_is_retried_then_marked_an_error(tmp_path, monkeypatch):
    log = _fake_claude_env(tmp_path, monkeypatch, judge_mode="nojson")
    (tmp_path / "out").mkdir()
    res = live.run_sample(GRADED_CASE, 1, _sample_args(), tmp_path / "out")
    assert res["error"] == "judge" and res["fails"] == []
    assert log.read_text().split() == ["model"] + ["judge"] * live.JUDGE_TRIES


def test_a_judge_that_answers_after_a_retry_scores_the_sample(tmp_path, monkeypatch):
    _fake_claude_env(tmp_path, monkeypatch)
    calls = {"n": 0}
    real_run = subprocess.run

    def flaky_judge(cmd, **kw):
        if "--disallowedTools" in cmd:
            calls["n"] += 1
            if calls["n"] == 1:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": "no json"}), stderr="")
        return real_run(cmd, **kw)

    monkeypatch.setattr(live.subprocess, "run", flaky_judge)
    assert live.judge(GRADED_CASE, "Closed x.", [], {}, _sample_args()) == {"pass": True, "score": 4, "why": "fine"}
    assert calls["n"] == 2


def test_the_summary_line_counts_errors_apart_from_the_pass_rate(tmp_path):
    rows = [
        {
            "id": "a",
            "sample": 1,
            "pass": True,
            "score": 5,
            "turns": 2,
            "cost_usd": 0.1,
            "fails": [],
            "surfaces": ["mcp"],
            "invalid": 0,
            "invalid_by_surface": {},
        },
        {
            "id": "a",
            "sample": 2,
            "pass": False,
            "score": 2,
            "turns": 2,
            "cost_usd": 0.1,
            "fails": ["no call /x/ that worked"],
            "surfaces": ["cli"],
            "invalid": 0,
            "invalid_by_surface": {},
        },
        {
            "id": "b",
            "sample": 1,
            "pass": False,
            "score": None,
            "turns": None,
            "cost_usd": 0,
            "fails": [],
            "surfaces": [],
            "invalid": 0,
            "invalid_by_surface": {},
            "error": "model",
        },
        {
            "id": "b",
            "sample": 2,
            "pass": False,
            "score": None,
            "turns": None,
            "cost_usd": 0,
            "fails": [],
            "surfaces": [],
            "invalid": 0,
            "invalid_by_surface": {},
            "error": "judge",
        },
    ]
    for r in rows:
        (tmp_path / f"{r['id']}.s{r['sample']}.json").write_text(json.dumps(r))
    head = live.summarise(tmp_path, "t1", "claude-haiku-5-5", "claude-opus-5-5", 2)
    lines = head.splitlines()
    assert lines[0] == "# t1: 1/2 pass (50%), 2 errored, model claude-haiku-5-5, judge claude-opus-5-5"
    assert (
        lines[3] == "2 samples errored: judge 1, model 1 (b s1, b s2). Left out of the pass rate; "
        "rerun with --rerun-errors t1."
    )
    md = (tmp_path / "summary.md").read_text()
    assert "| b | errored 2 | - | - | - | model error |" in md
    assert "| a | 1/2 |" in md


def test_rerun_errors_selects_only_the_errored_samples(tmp_path):
    rows = [
        {"id": "a", "sample": 1, "pass": True},
        {"id": "a", "sample": 2, "pass": False, "error": "model"},
        {"id": "b", "sample": 1, "pass": False, "error": "judge"},
    ]
    for r in rows:
        (tmp_path / f"{r['id']}.s{r['sample']}.json").write_text(json.dumps(r))
    assert live.errored_samples(tmp_path) == {("a", 2), ("b", 1)}
