#!/usr/bin/env python3
"""Drive the BotOps scenarios against a running Tico with a real model, and score them. On demand only: never in CI.

    python3 evals/botops/run.py --url http://127.0.0.1:8765 --owner-token <owner API token> \\
        [--member-token <a member's API token>] [--only build-jira-bot,change-model] [--json out.json]

The server needs BotOps running on a computer with a model (a dev install or a demo whose runner is real; the plain
demo answers bots with one line and cannot be scored). API tokens come from Settings > Computers. The owner token seeds
each scenario and reads the result; `as: member` scenarios ask BotOps as the member. A simulated person answers cards
the way the scenario says (`person:`); each answer is a step. Scenarios that are `live_only` need a real turn to write
a file in a bot's repository and are skipped by the scripted tests (backend/tests/test_botops_evals.py).

What it prints per scenario: done, person steps, jargon words, duplicate messages, times it sent the person to a settings
page. Exit code 0 when every scenario passed its limits.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402


class Live:
    """A running Tico, over HTTP."""

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


def scenarios(only=None, folder=HERE / "scenarios"):
    found = [yaml.safe_load(p.read_text()) for p in sorted(folder.glob("*.yaml"))]
    return [s for s in found if not only or s["id"] in only]


def seed(server, owner, rows):
    """The bots a scenario starts with. A bot made active is placed by the server; planned and paused ones are left as they are."""
    models = server.request("GET", "models", token=owner)[1]
    choice = next((m for m in models.get("models", []) if not m.get("deprecated") and m.get("provider") in (models.get("enabled_providers") or [m.get("provider")])), {})
    for row in rows or []:
        spec = row["bot"]
        status = "planned" if spec["status"] == "planned" else "active"
        code, made = server.request("POST", "bots", {
            "slug": spec["slug"], "display_name": spec["name"], "description": "An evaluation bot", "status": status,
            "model": choice.get("id"), "effort": choice.get("default_effort") or "", "harness": None, "runner_id": None}, owner)
        if code == 200 and spec["status"] == "paused":
            server.request("POST", f"bots/{spec['slug']}/control", {"action": "pause", "expected_revision": made["revision"]}, owner)


def busy(server, token):
    code, body = server.request("GET", "status?bot=botops", token=token)
    state = ((body or {}).get("status") or {}).get("state") if code == 200 else None
    return state in ("running", "queued", "leased")


def run_one(server, scenario, owner, member, quiet=45, timeout=900):
    token = member if scenario.get("as") == "member" else owner
    seed(server, owner, scenario.get("seed"))
    before = score.before_state(server, owner)
    code, sent = server.request("POST", "chat/botops", {"text": scenario["prompt"]}, token)
    if code != 200:
        return {"id": scenario["id"], "done": False, "passed": False, "error": f"could not ask BotOps ({code})"}
    conversation, seen, transcript, steps, answered = sent["conversation"]["id"], set(), [("person", scenario["prompt"])], 0, set()
    rules = {r["when"]: r for r in scenario.get("person", [])}
    last, deadline = time.monotonic(), time.monotonic() + timeout
    while time.monotonic() < deadline and time.monotonic() - last < quiet + (60 if busy(server, owner) else 0):
        time.sleep(3)
        code, page = server.request("GET", f"conversations/{conversation}/messages", token=token)
        for m in (page.get("messages", []) if code == 200 else []):
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            last = time.monotonic()
            refs = m.get("refs") or {}
            if m["sender_id"] == "bot:botops":
                if refs.get("credential_request") and "credential_card" in rules and m["id"] not in answered:
                    answered.add(m["id"])
                    server.request("POST", f"credential-requests/{refs['credential_request']}/save", {"value": rules["credential_card"]["value"]}, owner)
                    steps += 1
                elif refs.get("action") and "confirm_card" in rules and m["id"] not in answered:
                    answered.add(m["id"])
                    server.request("POST", f"assistant/actions/{refs['action']}/confirm", token=token)
                    steps += 1
                if not (refs.get("credential_request") or refs.get("action")):
                    transcript.append(("bot", m["body"]))
            elif m["sender_id"].startswith("human:") and m["body"] != scenario["prompt"] and not refs.get("credential_saved"):
                steps += 1
                transcript.append(("person", m["body"]))
    return score.score(scenario, server, owner, transcript, before, steps) | {"transcript": transcript}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--member-token")
    parser.add_argument("--only", help="comma list of scenario ids")
    parser.add_argument("--json", help="write every result, with transcripts, here")
    parser.add_argument("--quiet", type=int, default=45, help="seconds of silence that end a scenario")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    chosen = scenarios(set(args.only.split(",")) if args.only else None)
    if args.list:
        for s in chosen:
            print(f"{s['id']:22} {s['title']}" + ("  (live only)" if s.get("live_only") else ""))
        return 0
    server, results = Live(args.url), []
    for scenario in chosen:
        if scenario.get("as") == "member" and not args.member_token:
            print(f"{scenario['id']}: skipped (needs --member-token)")
            continue
        result = run_one(server, scenario, args.owner_token, args.member_token, quiet=args.quiet)
        results.append(result)
        print(f"{result['id']:22} {'PASS' if result.get('passed') else 'FAIL'}  done={result.get('done')}  steps={result.get('person_steps')}  "
              f"jargon={result.get('jargon')}  duplicates={result.get('duplicates')}  elsewhere={result.get('sent_elsewhere')}"
              + "".join(f"\n    - {c['check']}: {c.get('why', '')}" for c in result.get("checks", []) if not c["held"]))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=str))
    return 0 if results and all(r.get("passed") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
