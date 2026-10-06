#!/usr/bin/env python3
"""Roll an approved release out: push its tag, wait for the GitHub release, update the canary install, then the rest.

Run only after the owner approved this exact version on the release task (playbooks/rollout.md). Reads
knowledge/rollout.json:

    {"repository": "acme/product",
     "installs": [{"name": "Acme", "url": "https://tico.acme.example", "key_env": "TICO_UPDATE_KEY_ACME", "canary": true},
                  {"name": "Acme EU", "url": "https://eu.tico.acme.example", "key_env": "TICO_UPDATE_KEY_EU"}]}

Each `key_env` is a Credential granted to this bot holding that install's update key (`hub service-key create
--scope update`, docs/service-keys.md). GitHub goes through `gh`, with this bot's write grant on the repository.

    python3 software/rollout.py all --version 0.3.23 --sha <commit>     tag, wait, canary, then the rest
    python3 software/rollout.py tag --version 0.3.23 --sha <commit>
    python3 software/rollout.py wait --version 0.3.23
    python3 software/rollout.py update --version 0.3.23 [--only NAME]
    python3 software/rollout.py status

Prints one JSON report (before and after per install) and exits 1 when a step fails; nothing after a failed step runs.
Standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "knowledge" / "rollout.json"
RELEASE_WAIT = 90 * 60      # the desktop builds come before the GitHub release
UPDATE_WAIT = 30 * 60       # the updater pulls, snapshots, restarts and checks health, or rolls back
COMPUTERS_WAIT = 10 * 60    # computers follow their server within a few minutes; reported, never a gate
POLL = 20


class Stop(Exception):
    pass


def say(*parts):
    print(time.strftime("%H:%M:%S"), *parts, file=sys.stderr, flush=True)


def plain(version):
    return str(version or "").strip().lstrip("v")


def gh(*args, check=True):
    done = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and done.returncode:
        raise Stop("gh " + " ".join(args[:3]) + ": " + (done.stderr or done.stdout).strip()[:400])
    return done


def tag(repo, version, sha):
    """Point refs/tags/vX.Y.Z at the approved commit. An existing tag at that commit is fine; elsewhere it stops."""
    name = "v" + plain(version)
    full = gh("api", "repos/%s/commits/%s" % (repo, sha), "--jq", ".sha").stdout.strip()
    existing = gh("api", "repos/%s/git/ref/tags/%s" % (repo, name), "--jq", ".object.sha", check=False)
    if existing.returncode == 0:
        found = existing.stdout.strip()
        if found != full:
            # An annotated tag points at a tag object; follow it once.
            found = gh("api", "repos/%s/git/tags/%s" % (repo, found), "--jq", ".object.sha", check=False).stdout.strip()
        if found != full:
            raise Stop("%s already exists at another commit (%s), not %s" % (name, found[:12], full[:12]))
        say(name, "already at", full[:12])
        return {"tag": name, "sha": full, "pushed": False}
    # Created with this bot's GitHub App token, the push runs the release workflows (GITHUB_TOKEN's would not).
    gh("api", "-X", "POST", "repos/%s/git/refs" % repo, "-f", "ref=refs/tags/" + name, "-f", "sha=" + full)
    say("pushed", name, "at", full[:12])
    return {"tag": name, "sha": full, "pushed": True}


def wait_release(repo, version):
    """Until the GitHub release for the tag exists (installs update from its bundle); stop when a run for it failed."""
    name, deadline = "v" + plain(version), time.time() + RELEASE_WAIT
    while time.time() < deadline:
        found = gh("release", "view", name, "-R", repo, "--json", "tagName,url,isDraft", check=False)
        if found.returncode == 0 and not json.loads(found.stdout).get("isDraft"):
            say("release published:", json.loads(found.stdout).get("url"))
            return {"release": json.loads(found.stdout).get("url")}
        runs = gh("run", "list", "-R", repo, "--branch", name, "--json", "name,status,conclusion,url", check=False)
        for run in json.loads(runs.stdout or "[]") if runs.returncode == 0 else []:
            if run.get("name") in ("Release", "Docker") and run.get("conclusion") in ("failure", "cancelled", "timed_out"):
                raise Stop("the %s workflow for %s ended %s: %s" % (run["name"], name, run["conclusion"], run.get("url")))
        time.sleep(POLL)
    raise Stop("no GitHub release for %s after %d minutes" % (name, RELEASE_WAIT // 60))


def call(install, method, path, body=None, key=True):
    headers = {"Accept": "application/json", "User-Agent": "tico-release-rollout"}
    if key:
        secret = os.environ.get(install["key_env"], "").strip()
        if not secret:
            raise Stop("%s: the Credential %s is not granted to this bot" % (install["name"], install["key_env"]))
        headers["Authorization"] = "Bearer " + secret
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    request = urllib.request.Request(install["url"].rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, {"error": type(exc).__name__}


def running(install):
    """The release the server says it runs: /healthz, else the update status."""
    code, health = call(install, "GET", "/healthz", key=False)
    release = plain(health.get("release")) if code == 200 else ""
    if not release:
        code, status = call(install, "GET", "/api/v2/system/update")
        release = plain(status.get("running")) if code == 200 else ""
    return release


def update(install, version):
    """Start the update and follow it to healthy on the target, or stop on a rollback or failure."""
    target, name = plain(version), install["name"]
    before = running(install)
    out = {"install": name, "before": before, "after": before, "outcome": "", "computers": {}}
    if before == target:
        out["outcome"] = "already"
        return out
    call(install, "POST", "/api/v2/system/update/check")          # 429 within a minute of another check is fine
    code, started = call(install, "POST", "/api/v2/system/update", {"version": target})
    if code != 200:
        out["outcome"] = "refused"
        raise Stop("%s refused the update (%s): %s" % (name, code, json.dumps(started)[:300]), out)
    say(name, "updating", before, "->", target)
    deadline, state = time.time() + UPDATE_WAIT, ""
    while time.time() < deadline:
        time.sleep(POLL)
        code, status = call(install, "GET", "/api/v2/system/update")
        if code != 200:
            continue                                                # the server is restarting
        state = status.get("state") or ""
        if plain(status.get("to")) == target and state in ("rolled_back", "failed"):
            out.update(outcome=state, after=running(install))
            raise Stop("%s: update %s (%s)" % (name, state, status.get("message") or ""), out)
        if state == "healthy" and plain(status.get("to")) == target and running(install) == target:
            out.update(outcome="healthy", after=target)
            break
    else:
        out.update(outcome="timeout", after=running(install))
        raise Stop("%s did not report %s within %d minutes (last state %s)" % (name, target, UPDATE_WAIT // 60, state), out)
    say(name, "healthy on", target)
    out["computers"] = follow(install, install.get("canary", False))
    return out


def follow(install, wait):
    """Online computers by state; the canary waits a while for them to follow. Never fails the rollout."""
    deadline = time.time() + (COMPUTERS_WAIT if wait else 0)
    while True:
        code, status = call(install, "GET", "/api/v2/system/update")
        counts = (status.get("computers") or {}) if code == 200 else {}
        if not (counts.get("needs_update") or counts.get("updating")) or time.time() >= deadline:
            return counts
        time.sleep(POLL)


def load():
    try:
        config = json.loads(CONFIG.read_text())
    except (OSError, ValueError) as exc:
        raise Stop("knowledge/rollout.json is missing or not JSON (%s); see playbooks/rollout.md" % exc)
    if not config.get("repository") or not config.get("installs"):
        raise Stop("knowledge/rollout.json needs a repository and at least one install")
    return config


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("step", choices=["all", "tag", "wait", "update", "status"])
    p.add_argument("--version", default="")
    p.add_argument("--sha", default="")
    p.add_argument("--only", default="", help="update one install by name")
    args = p.parse_args(argv)
    report = {"version": plain(args.version), "steps": [], "installs": [], "ok": False}
    try:
        config = load()
        repo, installs = config["repository"], config["installs"]
        if args.step == "status":
            report["installs"] = [{"install": i["name"], "running": running(i)} for i in installs]
            report["ok"] = True
            return report
        if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", report["version"]):
            raise Stop("--version must be X.Y.Z")
        if args.step in ("all", "tag"):
            if not re.fullmatch(r"[0-9a-f]{7,40}", args.sha):
                raise Stop("--sha must be the approved commit")
            report["steps"].append(tag(repo, args.version, args.sha))
        if args.step in ("all", "wait"):
            report["steps"].append(wait_release(repo, args.version))
        if args.step in ("all", "update"):
            chosen = [i for i in installs if not args.only or i["name"] == args.only]
            canaries = [i for i in chosen if i.get("canary")]
            for install in canaries:                                # one at a time; a failure stops everything after
                report["installs"].append(update(install, args.version))
            rest = [i for i in chosen if not i.get("canary")]
            with ThreadPoolExecutor(max(1, len(rest))) as pool:     # the rest together
                futures = [pool.submit(update, i, args.version) for i in rest]
            failures = []
            for future in futures:
                try:
                    report["installs"].append(future.result())
                except Stop as exc:
                    failures.append(exc)
            if failures:
                for exc in failures:
                    if len(exc.args) > 1:
                        report["installs"].append(exc.args[1])
                raise Stop("; ".join(str(exc.args[0]) for exc in failures))
        report["ok"] = True
    except Stop as exc:
        report["error"] = str(exc.args[0])
        if len(exc.args) > 1 and exc.args[1] not in report["installs"]:
            report["installs"].append(exc.args[1])
    return report


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
