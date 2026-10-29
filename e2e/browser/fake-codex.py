#!/usr/bin/env python3
"""The harness for scripts/browser-journey.sh's browser bots: scripts/journey-fake-codex.py for everything it already
answers, and one more prompt. `journey-run <nonce> <phase> [KEY=value ...]` runs e2e/browser/bot-steps.mjs <phase> inside
the turn, so the bot's own HUB_ variables and TICO_BROWSER_URL are the ones a real turn gets. The phase's JSON result
goes to <threads folder>/results/<nonce>.json, and the reply is "journey-reply: <nonce> status=<ok|fail> phase=<phase>".
Only the values in ALLOWED reach the phase, and the phase never prints what it types into a page."""
import importlib.util
import json
import os
import re
import subprocess
import sys

FAKE = os.environ.get("JOURNEY_FAKE_CODEX", "/opt/e2e/journey-fake-codex.py")
STEPS = os.environ.get("JOURNEY_BOT_STEPS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot-steps.mjs"))
RUN = re.compile(r"journey-run (\S+) ([a-z0-9-]+)((?: [A-Za-z_]+=\S+)*)")
ALLOWED = {"PRACTICE", "HOST", "OTHER", "CREDENTIAL", "LABEL", "EXPECT_TZ", "EXPECT_LANG", "PEER_PORT", "PEER_PROFILE"}

spec = importlib.util.spec_from_file_location("journey_fake_codex", FAKE)
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)
answer_journey = fake.answer


def run_phase(nonce, phase, args, env):
    values = dict(part.split("=", 1) for part in args.split())
    turn_env = {**env, **{key: value for key, value in values.items() if key in ALLOWED}}
    try:
        done = subprocess.run(["node", STEPS, phase], env=turn_env, capture_output=True, text=True, timeout=900)
        lines = done.stdout.strip().splitlines()
        result = json.loads(lines[-1]) if lines else {
            "ok": False, "checks": [{"name": "no result", "ok": False, "detail": "exit %d" % done.returncode}]}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "checks": [{"name": "phase did not run", "ok": False, "detail": type(exc).__name__}]}
    result["phase"] = phase
    folder = os.path.join(fake.threads_dir(), "results")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, nonce + ".json"), "w") as out:
        json.dump(result, out)
    return "journey-reply: %s status=%s phase=%s" % (nonce, "ok" if result.get("ok") else "fail", phase)


def answer(text, thread, resumed, env):
    found = RUN.findall(text)
    if found:
        nonce, phase, args = found[-1]
        return run_phase(nonce, phase, args, env)
    return answer_journey(text, thread, resumed, env)


fake.answer = answer

if __name__ == "__main__":
    try:
        fake.main()
    except BaseException:
        import traceback
        with open("/tmp/journey-fake-codex.log", "a") as log:
            log.write("%s %s\n%s" % (os.getuid(), sys.argv[1:], traceback.format_exc()))
        raise
