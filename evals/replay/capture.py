#!/usr/bin/env python3
"""Write a draft replay case from a finished task, for a person to read, redact and sign.

    python3 evals/replay/capture.py --url https://tico.example --token <API token> --task <task id> --out <private folder>

The draft holds the task's real words. Write it outside this public repository. It is not runnable until a person has
read it, cut anything private or unneeded (names, customers, links, figures), adjusted its `done` checks and `limits`,
and set `reviewed_by` to their name. What is cut from the original transcript is cut from the baseline score too.

It prints any secret-looking values (keys, tokens, email addresses) it found; run.py refuses a case that still has one.
Only text files are copied; a binary file is listed with empty text for the person to replace or drop.
"""
import argparse
import importlib.util
import sys
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import replay  # noqa: E402

_spec = importlib.util.spec_from_file_location("botops_run", HERE.parent / "botops" / "run.py")
botops_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(botops_run)

TEXT_LIMIT = 200_000


def fetch_text(url, base, token):
    request = urllib.request.Request(url if "://" in url else base.rstrip("/") + url,
                                     headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(TEXT_LIMIT + 1)
    return body[:TEXT_LIMIT].decode("utf-8", "replace")


def capture(server, base, token, task_id):
    code, detail = server.request("GET", f"tasks/{task_id}", token=token)
    if code != 200:
        raise SystemExit(f"cannot read task {task_id}: {code}")
    if detail["task"]["status"] not in replay.SETTLED:
        raise SystemExit(f"task {task_id} is {detail['task']['status']}: capture a finished task")
    files = server.request("GET", f"tasks/{task_id}/files", token=token)[1] or {}
    for f in files.get("files") or []:
        for v in f.get("versions") or []:
            text_like = str(v.get("mime") or "").startswith("text/") or v.get("mime") in ("application/json", "application/yaml")
            v["text"] = fetch_text(v["url"], base, token) if text_like and v.get("url") else ""
    bot = str(detail["task"]["owner_id"]).removeprefix("bot:")
    instructions = server.request("GET", f"bots/{bot}/instructions", token=token)[1] or {}
    children = [server.request("GET", f"tasks/{c['id']}", token=token)[1] or {} for c in detail.get("children") or []]
    return replay.split(detail, files, instructions, bot, [c for c in children if c.get("task")])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--task", required=True, action="append", help="a finished task's id; repeat for more")
    parser.add_argument("--out", required=True, help="a private folder outside this repository")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if (Path(__file__).resolve().parents[2] / ".git").exists() and out.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise SystemExit("write cases outside this repository: it is public")
    out.mkdir(parents=True, exist_ok=True)
    server = botops_run.Live(args.url)
    for task_id in args.task:
        try:
            case = capture(server, args.url, args.token, task_id)
        except ValueError as exc:
            print(f"{task_id}: skipped: {exc}")
            continue
        path = out / f"{case['id']}.yaml"
        path.write_text(yaml.safe_dump(case, sort_keys=False, allow_unicode=True, width=120))
        hits = replay.secrets_in(case)
        print(f"{path}: draft, {len(case['conversation'])} opening messages, {len(case['files'])} files, "
              f"{len(case['original']['transcript'])} messages after; prompt {'exact' if case['prompt']['exact'] else 'NEWER than the task'}"
              + (f"; {len(hits)} secret-looking values to cut" if hits else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
