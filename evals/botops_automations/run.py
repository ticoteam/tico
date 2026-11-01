#!/usr/bin/env python3
"""Ask BotOps each automation request on a running Tico with a real model, and check what it saves.
On demand only: never in CI.

    python3 evals/botops_automations/run.py --url http://127.0.0.1:8765 --owner-token <owner API token> \\
        [--only investor-drafts,priya-inbox-refuse] [--quiet 60] [--json out.json] [--record evals/botops_automations/saved]

The server needs BotOps running on a computer with a model (a dev install whose runner is real; the
plain demo answers bots with one line and cannot be checked). The install needs Acme's people (Dana, who owns the
requests, with dana@acme.example; Omar; Priya) as evals/redesign sets them up. API tokens come from Settings > Computers.

Each request is asked in its own chat with BotOps. An `edit` request names its seed automation (refs.automation); a
`new` request names none. Once BotOps is quiet, what it saved is read back and scored by checker.py: the automations
that appeared (new), or the live flow and any pending versions (edit). `--record DIR` writes what was observed as one
file per request, the saved-answer shape the offline tests replay.

What it prints per request: PASS or FAIL, and each failed check with what was wanted and what was saved. Exit code 0
when every request passed.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checker  # noqa: E402

BOT = "botops"
BUSY = ("running", "queued", "leased")


class Live:
    """A running Tico, over HTTP (the same shape as evals/botops/run.py)."""

    def __init__(self, url):
        self.url = url.rstrip("/")

    def request(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + "/api/v2/" + path.lstrip("/"), data=data, method=method, headers={
            "Authorization": "Bearer " + str(token), "Accept": "application/json", "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4())})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            except ValueError:
                return exc.code, {}


def busy(server, token):
    code, body = server.request("GET", f"status?bot={BOT}", token=token)
    return code == 200 and ((body or {}).get("status") or {}).get("state") in BUSY


def observe(server, aid, token):
    """An edit: the live flow (the newest active version) and the pending versions, oldest first."""
    code, body = server.request("GET", f"automations/{aid}/versions", token=token)
    rows = body.get("versions", []) if code == 200 else []
    active = [row for row in rows if row.get("status") == "active"]
    live = max(active, key=lambda row: row["n"])["flow"] if active else None
    pending = [row["flow"] for row in sorted((r for r in rows if r.get("status") == "pending"), key=lambda r: r["n"])]
    return {"live": live, "pending": pending}


def ids(server, token):
    code, body = server.request("GET", "automations?limit=200", token=token)
    return {row["id"] for row in body.get("automations", [])} if code == 200 else set()


def made(server, before, token):
    """A new request: the automations that appeared, each as GET shows it (name, owner, enabled, trigger, steps)."""
    found = []
    for aid in sorted(ids(server, token) - before):
        code, row = server.request("GET", f"automations/{aid}", token=token)
        if code == 200:
            found.append({key: row[key] for key in ("name", "owner", "enabled", "trigger", "steps", "batch") if key in row})
    return {"created": found}


def run_case(server, case, owner, quiet=60, timeout=900):
    seed, aid, refs = checker.before_of(case), None, {}
    if seed:
        body = {key: seed[key] for key in ("name", "owner", "enabled", "trigger", "steps", "batch") if key in seed}
        code, saved = server.request("POST", "automations", body, owner)
        if code != 200:
            return {"id": case["id"], "passed": False, "checks": [{"name": "seeded", "held": False,
                    "why": f"could not save the automation ({code})"}]}
        aid = saved["id"]
        refs = {"automation": aid}
    before = ids(server, owner)
    code, sent = server.request("POST", f"chat/{BOT}", {"text": case["prompt"], "refs": refs}, owner)
    if code != 200:
        return {"id": case["id"], "passed": False, "checks": [{"name": "asked", "held": False,
                "why": f"could not ask BotOps ({code})"}]}
    conversation, seen, replies = sent["conversation"]["id"], set(), []
    last, deadline = time.monotonic(), time.monotonic() + timeout
    while time.monotonic() < deadline and time.monotonic() - last < quiet + (60 if busy(server, owner) else 0):
        time.sleep(3)
        code, page = server.request("GET", f"conversations/{conversation}/messages", token=owner)
        for message in (page.get("messages", []) if code == 200 else []):
            if message["id"] in seen:
                continue
            seen.add(message["id"])
            last = time.monotonic()
            if message["sender_id"] == f"bot:{BOT}":
                replies.append(message["body"])
    observed = observe(server, aid, owner) if aid else made(server, before, owner)
    return {**checker.check_case(case, observed), "replies": replies, "observed": observed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--only", help="comma list of request ids")
    parser.add_argument("--quiet", type=int, default=60, help="seconds of silence that end a request")
    parser.add_argument("--json", help="write every result, with what was observed, here")
    parser.add_argument("--record", help="write each observed result as a saved answer into this folder")
    parser.add_argument("--list", action="store_true", help="list the requests and exit")
    args = parser.parse_args(argv)
    chosen = checker.load_cases(set(args.only.split(",")) if args.only else None)
    if args.list:
        for case in chosen:
            print(f"{case['id']:24} {case.get('mode', 'new'):5} {case['title']}")
        return 0
    server, results = Live(args.url), []
    for case in chosen:
        result = run_case(server, case, args.owner_token, quiet=args.quiet)
        results.append(result)
        print(f"{result['id']:24} {'PASS' if result.get('passed') else 'FAIL'}"
              + "".join(f"\n    - {check['name']}: {check.get('why', '')}" for check in result["checks"] if not check["held"]))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=str))
    if args.record:
        folder = Path(args.record)
        folder.mkdir(parents=True, exist_ok=True)
        for result in results:
            (folder / f"{result['id']}.json").write_text(json.dumps(result["observed"], indent=2, ensure_ascii=False) + "\n")
    return 0 if results and all(result.get("passed") for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
