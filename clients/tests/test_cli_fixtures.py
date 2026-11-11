"""Frozen behaviour of the `tico` command line, one file per slice in clients/tests/cli_fixtures/: each case is a command
line and what it does, end to end through clients/hubcli.py and clients/remotecli.py: the tools it runs with their
arguments, the HTTP calls they make (method, path, body), what it prints on stdout and stderr, and its exit code.

The slices are the ones the typed command spec (step 4 of the CLI reorganisation) is piloted on, so a parser built
from the spec has to give the same answers. argparse's usage lines are left out of stderr: they list flags in the
parser's own order, which a generated parser need not keep. The error line after them stays.

A deliberate change regenerates the files, to be read in the diff before it is committed:
    TICO_CLI_FIXTURES_RECORD=1 python3 -m pytest -q clients/tests/test_cli_fixtures.py
"""

import contextlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

from clients import hubcli, hubtools
from clients.tico import APIError, Client

HERE = Path(__file__).parent / "cli_fixtures"
RECORD = os.environ.get("TICO_CLI_FIXTURES_RECORD") == "1"

# What the fake API answers, by "METHOD path" with the query left off; anything else answers {"ok": true}.
_TRIGGER = {"id": "stripe", "name": "Stripe events", "event": "webhook.stripe", "preset": "stripe", "hourly": 60}
_RELAY = {"trigger": "stripe", "address": "https://gateway.example/r/stripe"}
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
    "GET rules": {"rules": [{"id": "r1", "text": "Reply within a day", "version": 2}], "next_cursor": None},
    "GET rules/missing": APIError("not_found", "No rule missing", 404),
}

TRIGGER = [
    ["trigger", "list"],
    ["trigger", "create", "Stripe events"],
    ["trigger", "create", "Stripe events", "--id", "stripe"],
    ["trigger", "show", "stripe"],
    ["trigger", "update", "stripe"],
    ["trigger", "update", "stripe", "--name", "Stripe", "--preset", "github", "--type-from", "/type", "--hourly", "10"],
    ["trigger", "update", "stripe", "--keep-days", "3", "--unheard", "note", "--type-from", ""],
    ["trigger", "update", "stripe", "--preset", "bogus"],
    ["trigger", "update", "stripe", "--hourly", "ten"],
    ["trigger", "update"],
    ["trigger", "rotate", "stripe"],
    ["trigger", "sign", "stripe"],
    ["trigger", "sign", "stripe", "--preset", "stripe", "--secret", "0123456789abcdef"],
    ["trigger", "sign", "stripe", "--off"],
    ["trigger", "sign", "stripe", "--preset", "none"],
    ["trigger", "delete", "stripe"],
    ["trigger", "deliveries", "stripe"],
    ["trigger", "delivery", "stripe", "d1"],
    ["trigger", "delivery", "stripe", "d1", "--part", "2"],
    ["trigger", "replay", "stripe", "d1"],
    ["trigger", "recover", "stripe"],
    ["trigger", "recover", "stripe", "--since", "48"],
    ["trigger", "relay", "create", "stripe"],
    ["trigger", "relay", "rotate", "stripe"],
    ["trigger", "relay", "delete", "stripe"],
    ["trigger", "relay", "delete"],
    ["trigger", "show", "stripe", "--json"],
    ["trigger", "shwo", "stripe"],
    ["trigger", "list", "--bogus"],
]

RULES = [
    ["rules", "list"],
    ["rule", "list"],
    ["rules", "list", "--kind", "internal", "--status", "active", "--who", "Ops", "--with", "Ada", "--source", "Acme"],
    ["rules", "list", "--bot", "writer", "--mine", "--cursor", "c1", "--limit", "5"],
    ["rules", "list", "--kind", "both"],
    ["rules", "read", "r1"],
    ["rules", "read", "missing"],
    ["rules", "read", "f1", "--from", "Acme", "--kind", "vendor", "--note", "the MSA"],
    ["rules", "read", "https://acme.example/terms", "--from", "Acme"],
    ["rules", "ask", "Can I share the deck with Acme?"],
    ["rules", "suggested"],
    ["rules", "suggested", "--cursor", "c1", "--limit", "2"],
    ["rules", "add", "--kind", "internal", "--text", "Reply within a day"],
    [
        "rules",
        "add",
        "--kind",
        "internal",
        "--text",
        "Reply within a day",
        "--description",
        "why",
        "--who",
        "Ops",
        "--who",
        "writer",
        "--everyone",
        "--all-bots",
        "--outside-editions",
        "--with",
        "Ada",
        "--where",
        "thread:t1",
        "--where",
        "kind:dm",
        "--if-broken",
        "warn",
        "--reviewer",
        "Ada",
    ],  # fmt: skip
    [
        "rules",
        "add",
        "--kind",
        "external",
        "--text",
        "Pay in 30 days",
        "--from",
        "Acme",
        "--file",
        "f1",
        "--section",
        "4.2",
        "--quote",
        "within thirty days",
        "--version",
        "3",
    ],  # fmt: skip
    ["rules", "add", "--kind", "external", "--text", "Pay", "--from", "Acme", "--file", "f1", "--quote", "thirty"],
    ["rules", "add", "--kind", "external", "--text", "Pay", "--quote", "thirty"],
    ["rules", "add", "--kind", "external", "--text", "Pay", "--file", "f1", "--version", "3"],
    ["rules", "add", "--text", "Pay"],
    ["rules", "add", "--kind", "internal", "--text", "Pay", "--if-broken", "explode"],
    ["rules", "edit", "r1", "--version", "2"],
    [
        "rules",
        "edit",
        "r1",
        "--version",
        "2",
        "--text",
        "Reply in a day",
        "--description",
        "d",
        "--who",
        "Ops",
        "--everyone",
        "--all-bots",
        "--outside-editions",
        "--with",
        "Ada",
        "--where",
        "team:Ops",
        "--from",
        "Acme",
        "--if-broken",
        "block",
        "--reviewer",
        "Ada",
    ],  # fmt: skip
    ["rules", "edit", "r1"],
    ["rules", "edit", "r1", "--version", "two"],
    ["rules", "accept", "r1"],
    ["rules", "accept", "r1", "--version", "2", "--who", "Ops", "--who", "writer", "--everyone"],
    ["rules", "accept", "r1", "--all-bots", "--outside-editions"],
    ["rules", "skip", "r1"],
    ["rules", "skip", "r1", "--version", "2"],
    ["rules", "retire", "r1", "--version", "2"],
    ["rules", "due"],
    ["rules", "due", "--next", "30d", "--status", "overdue", "--cursor", "c1", "--limit", "3"],
    ["rules", "due", "list"],
    ["rules", "due", "list", "--next", "30d", "--status", "due_soon"],
    ["rules", "due", "--next", "10d", "list", "--status", "upcoming"],
    ["rules", "due", "--status", "late"],
    ["rules", "due", "add", "r1", "--first", "2026-12-01", "--repeats", "monthly", "--owner", "Ada"],
    [
        "rules",
        "due",
        "add",
        "r1",
        "--first",
        "2026-12-01",
        "--repeats",
        "yearly",
        "--owner",
        "writer",
        "--timezone",
        "Europe/Lisbon",
        "--task-days",
        "3",
    ],  # fmt: skip
    ["rules", "due", "add", "r1", "--first", "2026-12-01", "--owner", "Ada"],
    ["rules", "due", "edit", "d1"],
    ["rules", "due", "edit", "d1", "--first", "2027-01-01", "--repeats", "once", "--owner", "Ada", "--timezone", "UTC"],
    ["rules", "due", "edit", "d1", "--task-days", "2"],
    ["rules", "due", "edit", "d1", "--no-routine"],
    ["rules", "due", "edit", "d1", "--task-days", "2", "--no-routine"],
    ["rules", "due", "satisfied", "d1"],
    ["rules", "due", "satisfied", "d1", "--on", "2026-12-01"],
    ["rules", "check", "Sending the deck now", "--to", "Ada"],
    ["rules", "check", "Done", "--to", "Ada", "--as", "comment"],
    ["rules", "check", "Done"],
    ["rules", "appeal", "h1"],
    ["rules", "appeal", "h1", "the rule is about invoices"],
    ["rules", "withdraw", "h1"],
    ["rules", "withdraw"],
    ["rules", "red", "r1"],
]

SLICES = {"trigger": TRIGGER, "rules": RULES}


def usage_free(text):
    """`text` without argparse's usage block (the `usage:` line and the lines indented under it)."""
    kept, in_usage = [], False
    for line in text.splitlines():
        if line.startswith("usage: "):
            in_usage = True
            continue
        if in_usage and line.startswith(" "):
            continue
        in_usage = False
        kept.append(line)
    return "\n".join(kept)


def run_line(argv, monkeypatch):
    """One command line against the fake API: what it did and printed."""
    tools, http = [], []

    def request(self, method, path, body=None, key=None, **_):
        http.append([method, path.removeprefix("/api/v2/"), body])
        answer = ANSWERS.get(f"{method} {path.removeprefix('/api/v2/').split('?')[0]}", {"ok": True})
        if isinstance(answer, Exception):
            raise answer
        return json.loads(json.dumps(answer))

    def recording(name, fn):
        def call(api, args):
            tools.append([name, json.loads(json.dumps(args, default=str))])
            return fn(api, args)

        return call

    monkeypatch.setattr(Client, "request", request)
    for name, entry in hubtools.BY_NAME.items():
        monkeypatch.setitem(entry, "fn", recording(name, entry["fn"]))
    for key in ("HUB_BOT", "HUB_EMPLOYEE", "HUB_OPERATION_ID", "HUB_WORKSPACE", "TICO_PROFILE", "HUB_HUMAN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HUB_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("HUB_TOKEN", "fixture-token")
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
        "stdout": out.getvalue(),
        "stderr": usage_free(err.getvalue()),
        "exit": code,
    }


@pytest.mark.parametrize("slice_name", sorted(SLICES))
def test_the_command_line_does_what_its_fixtures_froze(slice_name, monkeypatch):
    path = HERE / f"{slice_name}.json"
    observed = []
    for argv in SLICES[slice_name]:
        with monkeypatch.context() as m:
            observed.append(run_line(argv, m))
    if RECORD:
        HERE.mkdir(exist_ok=True)
        path.write_text(json.dumps(observed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    frozen = {tuple(case["argv"]): case for case in json.loads(path.read_text(encoding="utf-8"))}
    assert set(frozen) == {tuple(case["argv"]) for case in observed}, f"{path.name}: the case list changed; record it"
    for case in observed:
        assert case == frozen[tuple(case["argv"])], " ".join(case["argv"])
