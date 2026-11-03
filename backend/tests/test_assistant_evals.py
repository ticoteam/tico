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
NEW_COMMANDS = [("needs-you", "show"), ("origin", "show"), ("assistant", "change")]
# Every case the Assistant suite must keep: the real Nov 3 chat split into its asks, undo, injection, decline, approval
# without the setting, and the everyday jobs.
REQUIRED = {"queue-clear-old-tasks", "queue-stop-release-notices", "queue-why-sent", "queue-needs-you-breakdown",
            "queue-undo-close", "queue-morning-summary", "approvals-injection-bot-message",
            "approvals-decline-replaced-release", "approvals-no-setting-no-approve", "approvals-read-back-named",
            "approvals-vendor-text-task", "approvals-clear-stale-direct", "background-daily-sweep", "jobs-launch-status", "jobs-route-paid-trials",
            "jobs-new-bot-botops", "jobs-discount-to-cfo", "jobs-change-bot-routine", "jobs-what-did-bot-do",
            "jobs-find-doc", "jobs-task-for-person"}


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
            out.extend(expand(rx[:m.start()] + alt + rx[m.end():]))
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


def test_ids_are_unique_and_the_required_cases_are_there(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    assert len(ids) >= 18, f"{len(ids)} cases; the suite needs at least 18"
    assert REQUIRED <= set(ids), f"missing: {sorted(REQUIRED - set(ids))}"


def test_every_regex_compiles(cases):
    for case in cases:
        for _key, rx in expectations(case):
            re.compile(rx)
        for row in case.get("hub") or []:
            re.compile(row["match"])


def test_expected_hub_commands_parse_with_the_real_cli(cases, parser):
    """Only `must` names commands the case expects; a must_not naming a command that does not exist cannot fail a run."""
    checked = 0
    for case in cases:
        for rx in case.get("must") or []:
            if rx.startswith("reply:"):
                continue
            key = "must"
            for alt in expand(rx):
                words = command_words(alt)
                if len(words) < 2:
                    continue
                checked += 1
                if any(tuple(words[:len(p)]) == p for p in NEW_COMMANDS):
                    assert not parses(parser, words), (
                        f"{case['id']}: `hub {' '.join(words)}` parses now; remove its entry from NEW_COMMANDS")
                    continue
                assert parses(parser, words), f"{case['id']} ({key}): `hub {' '.join(words)}` is not a command"
    assert checked >= 40, f"only {checked} commands were checked; the heuristic has stopped reading the cases"


def test_the_mcp_surface_lists_the_real_tools_and_logs_a_call_as_a_canon_line(tmp_path):
    """The fake hub MCP server (the surface production gives the Assistant) answers tools/list with the real listing for
    the Assistant, and a tools/call logs the canonical line the CLI would log, so the must regexes read both surfaces alike."""
    server = tmp_path / "fake_mcp.py"
    server.write_text(live.FAKE_MCP)
    canned, log, state = tmp_path / "canned.json", tmp_path / "hub.log", tmp_path / "state.json"
    canned.write_text("[]")
    env = {**os.environ, "EVAL_REPO": str(EVALS.parents[1]), "EVAL_LOG": str(log), "EVAL_CANNED": str(canned),
           "EVAL_STATE": str(state), "EVAL_MCP_KIND": "assistant"}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "hub_task_close", "arguments": {"id": "x", "note": "y"}}},
    ]
    proc = subprocess.run([sys.executable, str(server)], input="".join(json.dumps(r) + "\n" for r in requests),
                          capture_output=True, text=True, env=env, timeout=120, check=False)
    replies = {r["id"]: r for r in map(json.loads, proc.stdout.splitlines())}
    assert set(replies) == {1, 2, 3}, proc.stderr
    real = [t["name"] for t in hubtools.listing(local=True, kind="assistant")]
    assert [t["name"] for t in replies[2]["result"]["tools"]] == real
    assert not replies[3]["result"]["isError"], replies[3]
    logged = [json.loads(l) for l in log.read_text().splitlines()]
    assert logged[-1]["via"] == "mcp" and logged[-1]["tool"] == "hub_task_close", logged
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


@pytest.mark.xfail(strict=False, reason=f"needs {RUNNER_BRANCH} merged: its approval, BotOps and reply lines are not in "
                   "runner-rs/src/prompt.rs on this branch yet")
def test_the_opening_lines_appear_in_the_runner_prompt():
    """Every line of the harness's opening (the chat path, with the owner and the chat id filled in) is a string the runner
    builds in runner-rs/src/prompt.rs, so the harness cannot drift from production silently."""
    patterns = runner_patterns(PROMPT_RS.read_text())
    missing = []
    for line in live.opening().split("\n"):
        norm = " ".join(line.split())
        if not any(re.fullmatch(p, norm) for p in patterns):
            missing.append(norm[:120])
    assert not missing, f"not in prompt.rs: {missing}"


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("surface", ["both", "mcp", "cli"])
def test_a_sample_runs_its_claude_command_on_every_surface(tmp_path, monkeypatch, surface):
    """run_sample builds the claude command, the MCP config and the PATH for each surface. A stand-in `claude` on PATH
    records the argv it gets, so the real code path runs with no model; a crash there (a missing import) fails CI."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    record = tmp_path / "argv.json"
    claude = bin_dir / "claude"
    claude.write_text("#!/usr/bin/env python3\n"
                      "import json, os, sys\n"
                      "open(os.environ['FAKE_CLAUDE_OUT'], 'w').write(json.dumps(sys.argv[1:]))\n"
                      "print(json.dumps({'result': 'done', 'num_turns': 1, 'total_cost_usd': 0}))\n")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(record))
    args = argparse.Namespace(repo=str(REPO), model="claude-haiku-5-5", judge="claude-opus-5-5", effort="",
                              surface=surface, max_turns=3, timeout=120)
    case = {"id": "smoke-close", "group": "smoke", "prompt": "close x", "must": ["^task close"]}
    (tmp_path / "out").mkdir()
    res = live.run_sample(case, 1, args, tmp_path / "out")
    assert res["surface"] == surface and res["id"] == "smoke-close"
    argv = json.loads(record.read_text())
    servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    if surface == "cli":
        assert servers == {}
    else:
        assert servers["hub"]["command"] and servers["hub"]["args"][0].endswith("fake_mcp.py"), servers


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


def test_a_read_after_a_successful_write_shows_the_new_value(tmp_path, monkeypatch):
    answer = _hub_answer(tmp_path, monkeypatch, [{"match": r"^bot status list", "out": PAUSED,
                         "after": [{"when": r"^bot resume bot_a", "out": ACTIVE}]}])
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == PAUSED
    answer(["bot", "resume", "bot_a"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == ACTIVE


def test_a_failed_or_dry_run_write_leaves_the_old_value(tmp_path, monkeypatch):
    canned = [{"match": r"^bot status list", "out": PAUSED,
               "after": [{"when": r"^bot resume bot_a", "out": ACTIVE}]},
              {"match": r"^bot resume bot_a --fail", "out": "not allowed", "exit": 1}]
    answer = _hub_answer(tmp_path, monkeypatch, canned)
    answer(["bot", "resume", "bot_a", "--fail"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == PAUSED
    answer(["bot", "resume", "bot_a", "--dry-run"], "cli")
    assert json.loads(answer(["bot", "status", "list"], "cli")[0]) == PAUSED


def test_the_latest_matching_write_wins(tmp_path, monkeypatch):
    answer = _hub_answer(tmp_path, monkeypatch, [{"match": r"^bot status list", "out": ACTIVE, "after": [
        {"when": r"^bot pause bot_a", "out": PAUSED}, {"when": r"^bot resume bot_a", "out": ACTIVE}]}])
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


def test_approval_list_is_built_from_the_message_list_in_the_real_shape(tmp_path, monkeypatch):
    old, new = "2a7f0c9e-4000-4000-8000-000000000001", "5f3e21c0-4000-4000-8000-000000000002"
    long_text = "Approve this spend: " + "x" * 300
    messages = [
        {"id": "m1", "from_actor": "bot:release", "text": "Approve this publish:\n{\"release\": \"v0.5.17\"}",
         "refs": {"approval": old, "task": "b1c2d3e4"}, "created": "2026-10-30T10:00:00Z"},
        {"id": "m2", "from_actor": "bot:growth", "text": long_text, "refs": {"approval": new},
         "created": "2026-11-01T09:00:00Z"}]
    answer = _hub_answer(tmp_path, monkeypatch, [{"match": r"^message list", "out": {"actor": "human:dana",
                         "messages": messages}}])
    env = json.loads(answer(["approval", "list"], "cli")[0])
    assert set(env) == {"actor", "status", "total", "approvals", "next"}
    assert (env["actor"], env["status"], env["total"], env["next"]) == ("human:dana", "pending", 2, None)
    rows = env["approvals"]
    assert [r["id"] for r in rows] == [new, old]          # newest first
    assert set(rows[0]) == {"id", "status", "from_actor", "summary", "message_id", "task_id", "created", "link"}
    assert rows[0]["task_id"] is None and rows[0]["link"] is None
    assert len(rows[0]["summary"]) <= 200 and rows[0]["summary"].endswith("...")
    assert rows[1]["summary"] == 'Approve this publish: {"release": "v0.5.17"}'   # one line, lead-in kept
    assert rows[1]["task_id"] == "b1c2d3e4" and rows[1]["link"] == "#/task/b1c2d3e4"
    answer(["approval", "decline", "5f3e21c0"], "cli")
    env = json.loads(answer(["approval", "list"], "cli")[0])
    assert [r["id"] for r in env["approvals"]] == [old] and env["total"] == 1
    env = json.loads(answer(["approval", "list", "--status", "declined"], "cli")[0])
    assert env["status"] == "declined" and [(r["id"], r["status"]) for r in env["approvals"]] == [(new, "declined")]
