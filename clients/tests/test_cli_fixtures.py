"""Frozen behaviour of the `tico` command line, one file per slice in clients/tests/cli_fixtures/: each case is a command
line and what it does, end to end through clients/hubcli.py and clients/remotecli.py: the tools it runs with their
arguments, the HTTP calls they make (method, path, body), what it prints on stdout and stderr, and its exit code.

The slices are the ones the typed command spec (step 4 of the CLI reorganisation) is piloted on, so a parser built
from the spec has to give the same answers. argparse's usage lines are left out of stderr: they list flags in the
parser's own order, which a generated parser need not keep. The error line after them stays, as Python 3.12 words it
(Python 3.14 quotes the choices and adds its own guess at a typo).

Each file lists its cases; a new case is a line `{"argv": [...]}` added to its file, then recorded. A deliberate change
regenerates the files, to be read in the diff before it is committed:
    TICO_CLI_FIXTURES_RECORD=1 python3 -m pytest -q clients/tests/test_cli_fixtures.py
backend/tests/test_bot_instruction_commands.py fails when a command line a bot is taught is not a case in its noun's
file.
"""

import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

from clients import hubcli, hubtools
from clients.tico import APIError, Client

HERE = Path(__file__).parent / "cli_fixtures"
REPO = str(Path(__file__).resolve().parents[2])  # printed as <repo>: a path in an error is the checkout's own
RECORD = os.environ.get("TICO_CLI_FIXTURES_RECORD") == "1"
ROOT_HELP = hubcli.parser().description

# What the fake API answers, by "METHOD path" with the query left off; anything else answers {"ok": true}.
_TRIGGER = {"id": "stripe", "name": "Stripe events", "event": "webhook.stripe", "preset": "stripe", "hourly": 60}
_RELAY = {"trigger": "stripe", "address": "https://gateway.example/r/stripe"}
_MAIL = {
    "mailbox": "ops@acme.test",
    "msg_id": "m1",
    "thread_id": "t1",
    "subject": "Statement",
    "from_header": "Bank <bank@example.com>",
    "to": ["ops@acme.test"],
    "date": "2026-11-01",
    "labels": ["INBOX"],
    "snippet": "Your statement",
}
_FILE = {"name": "statement.pdf", "type": "application/pdf", "size": 2048}
_CHANNEL = {"slug": "google-search", "name": "Google search", "stage": "testing", "track": "performance"}
_GOAL = {"id": "g1", "title": "Launch", "status": "green", "owner": "bot:ana", "kpis": []}
_GOAL_CALLS = (("GET", ""), ("POST", ""), ("POST", "/status"))
_CHECKIN = {"id": "c1", "goal_id": "g1", "text": "On track", "author": "bot:ana"}
_SIZE = {"id": "s1", "segment": "smb", "method": "top_down", "low": 2.5, "high": 4.0, "status": "proposed"}
ANSWERS = {
    "GET triggers": {"triggers": [_TRIGGER]},
    "POST triggers": {"trigger": _TRIGGER | {"url": "https://tico.example/hooks/stripe"}},
    "GET triggers/stripe": {"trigger": _TRIGGER},
    "POST triggers/stripe": {"trigger": _TRIGGER},
    "POST triggers/stripe/rotate": {"trigger": _TRIGGER | {"url": "https://tico.example/hooks/stripe"}},
    "POST triggers/stripe/signing": {"trigger": _TRIGGER | {"signed": True}},
    "POST triggers/stripe/delete": {"trigger": {"id": "stripe", "deleted": True}},
    "GET triggers/stripe/deliveries": {
        "deliveries": [{"delivery": "d1", "type": "charge.paid", "outcome": "accepted"}]
    },
    "GET triggers/stripe/deliveries/d1": {"delivery": {"delivery": "d1", "part": 1, "parts": 2, "body": "{}"}},
    "POST triggers/stripe/deliveries/d1/replay": {"replay": {"delivery": "d1:replay:1"}},
    "POST triggers/stripe/recover": {"recover": {"found": 2, "recovered": 1, "duplicate": 1, "still_refused": 0}},
    "POST triggers/stripe/relay": {"relay": _RELAY},
    "POST triggers/stripe/relay/rotate": {"relay": _RELAY},
    "POST triggers/stripe/relay/delete": {"relay": {"trigger": "stripe", "deleted": True}},
    "POST triggers/stripe/relay/wake": {"relay": _RELAY | {"wake": True}},
    "GET relay": {"relay": {"key": "connected", "relays": 1, "last_error": "", "pending": 0}},
    "GET sleep": {"sleep": {"hosted": True, "hours": 4, "runners": True}},
    "POST sleep": {"sleep": {"hosted": True, "hours": 2, "runners": False}},
    "GET notices": {"notices": [{"kind": "info", "body": "Tico 0.6 is out", "source": "latest"}]},
    "GET rules": {"rules": [{"id": "r1", "text": "Reply within a day", "version": 2}], "next_cursor": None},
    "GET rules/missing": APIError("not_found", "No rule missing", 404),
    "GET me": {"ok": True, "actor": "bot:ana"},
    "GET mail/mailboxes": {"mailboxes": [{"address": "ops@acme.test"}, {"address": "billing@acme.test"}]},
    "GET mail/messages": {"messages": [_MAIL], "next_cursor": "c2"},
    "GET mail/threads/id-1": {"messages": [_MAIL | {"body": "Your statement is ready."}]},
    "GET mail/messages/id-1": _MAIL | {"body": "Your statement is ready.", "attachments": [_FILE]},
    "POST mail/messages/organize": {
        "count": 2,
        "mailbox": "ops@acme.test",
        "ids": ["m1", "m2"],
        "undo": {"action": "unlabel", "label": "Receipts", "ids": ["m1", "m2"]},
    },
    "GET growth/channels": {"channels": [_CHANNEL]},
    "GET growth/sources": {
        "sources": [
            {"id": "s1", "truth_for": "ad spend", "system": "Google Ads", "reach_kind": "mcp", "reach_ref": "ads"},
            {
                "id": "s4",
                "truth_for": "new customers",
                "system": "Signups sheet",
                "reach_kind": "person",
                "fills": ["new_customers"],
                "health": "failing",
                "last_error": 'column "channel" not found',
            },
        ],
        "conflicts": [{"truth_for": "bookings", "sources": ["s2", "s3"]}],
    },
    "POST growth/sources/s1/sync": {
        "ok": True,
        "written": 4,
        "held": 1,
        "not_written": [{"row": 4, "channel": "podcast", "month": "2026-10", "reason": "no such channel"}],
        "not_written_count": 1,
    },
    "GET growth/cohorts": {"cohorts": [{"month": "2026-10", "channel": None, "spend": 900, "new_customers": 3}]},
    "GET growth/economics": {
        "month": "2026-10",
        "rows": [{"name": "Google search", "spend": 900, "new_customers": 3, "cac": 300, "ltv": 1200, "ltv_cac": 4}],
        "total": {"name": "total", "spend": 900, "new_customers": 3, "cac": 300},
    },
    "GET growth/kpis": {"kpis": [{"id": "k1", "name": "Bookings", "role": "north_star"}]},
    "GET goals": {"goals": [_GOAL]},
    "POST goals": {"goal": _GOAL},
    **{f"{verb} goals/{id}{tail}": {"goal": _GOAL} for id in ("id-1", "1") for verb, tail in _GOAL_CALLS},
    **{f"POST goals/{id}/checkins": {"checkin": _CHECKIN} for id in ("id-1", "1")},
    "POST market/sizes": {"size": _SIZE},
    **{f"POST market/sizes/{id}/{d}": {"size": _SIZE} for id in ("id-1", "1") for d in ("accept", "reject")},
    "GET market/spend": {
        "year": 2026,
        "total": {"spend_usd": 3e9, "low_usd": 2.5e9, "high_usd": 4e9},
        "segments": [
            {"id": "all", "name": "Office software", "depth": 0, "size": None, "estimates": []},
            {
                "id": "smb",
                "name": "Small offices",
                "depth": 1,
                "estimates": [_SIZE],
                "disagree": True,
                "size": _SIZE | {"spend_usd": 3e9, "low_usd": 2.5e9, "high_usd": 4e9, "confidence": "medium"},
            },
        ],
    },
    "POST market/figures": {"figure": {"id": "f1", "entity": "acme", "segment": "smb", "value": 2.5}},
    "PUT strategy/draft": {"draft": {"version": 3, "summary": "Win small offices first"}},
}


def portable(text):
    """`text` without argparse's usage block (the `usage:` line and the lines indented under it), and its error line as
    Python 3.12 words it on every version. The top-level help (a group typed with no command, `tico email`) is kept to
    its first line: the rest lists every command, which a slice's fixtures must not freeze."""
    text = re.sub(r", maybe you meant '[^']*'\?", "", text)
    text = re.sub(r"\(choose from ([^)]*)\)", lambda m: "(choose from " + m.group(1).replace("'", "") + ")", text)
    kept, in_usage = [], False
    for line in text.splitlines():
        if line.startswith("usage: "):
            in_usage = True
            continue
        if in_usage and line.startswith(" "):
            continue
        in_usage = False
        kept.append(line)
    text = "\n".join(kept)
    return ROOT_HELP if text.lstrip().startswith(ROOT_HELP) else text


def run_line(argv, monkeypatch):
    """One command line against the fake API: what it did and printed."""
    tools, http = [], []

    def request(self, method, path, body=None, key=None, **_):
        http.append([method, path.removeprefix("/api/v2/"), body])
        answer = ANSWERS.get(f"{method} {path.removeprefix('/api/v2/').split('?')[0]}", {"ok": True})
        if isinstance(answer, Exception):
            raise answer
        return json.loads(json.dumps(answer))

    def recording(name, fn, properties):
        def call(api, args):
            # Only the arguments the tool's schema names: what else a command line passes (the hand parser's `json`)
            # the tool never reads, and a read of one would show in `http`.
            given = {k: v for k, v in args.items() if k in properties or k == "operation_id"}
            tools.append([name, json.loads(json.dumps(given, default=str, sort_keys=True))])  # order is noise
            return fn(api, args)

        return call

    monkeypatch.setattr(Client, "request", request)
    for name, entry in hubtools.BY_NAME.items():
        monkeypatch.setitem(entry, "fn", recording(name, entry["fn"], entry["inputSchema"]["properties"]))
    for key in ("HUB_BOT", "HUB_EMPLOYEE", "HUB_OPERATION_ID", "HUB_WORKSPACE", "TICO_PROFILE", "HUB_HUMAN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HUB_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("HUB_TOKEN", "fixture-token")
    monkeypatch.setattr(sys, "stdin", io.StringIO("Plain words, no hype.\n"))  # what `-` reads (`brand check -`)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = hubcli.main(list(argv))
        except SystemExit as exc:  # a check in remotecli that ends the process, as `python -m clients.hubcli` would
            code = exc.code if isinstance(exc.code, int) else 1
            if isinstance(exc.code, str):
                print(exc.code, file=sys.stderr)
    return {
        "argv": list(argv),
        "tools": tools,
        "http": http,
        "stdout": out.getvalue().replace(REPO, "<repo>"),
        "stderr": portable(err.getvalue()).replace(REPO, "<repo>"),
        "exit": code,
    }


def slices():
    """{noun: [case, ...]} from the files in cli_fixtures/."""
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(HERE.glob("*.json"))}


@pytest.mark.parametrize("slice_name", sorted(slices()))
def test_the_command_line_does_what_its_fixtures_froze(slice_name, monkeypatch):
    path = HERE / f"{slice_name}.json"
    frozen = slices()[slice_name]
    argvs = [case["argv"] for case in frozen]
    assert len({tuple(argv) for argv in argvs}) == len(argvs), f"{path.name}: a case is listed twice"
    observed = []
    for argv in argvs:
        with monkeypatch.context() as m:
            observed.append(run_line(argv, m))
    if RECORD:
        path.write_text(json.dumps(observed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    for case, seen in zip(frozen, observed, strict=True):
        assert case == seen, f"{path.name}: tico {' '.join(case['argv'])}" + (
            "" if "exit" in case else " is not recorded"
        )
