"""1Password references in a bot's secrets, resolved on this Mac before a turn starts.

A secrets-file value like `op://vault/Upfluence/password` is read from 1Password with the
service-account token in `secrets/_shared.env` (OP_SERVICE_ACCOUNT_TOKEN). The token is
read-only on the company's vault and is stripped from the turn's environment afterwards.
"""
import os
import subprocess
import sys
from datetime import datetime

OP_REF = "op://"                # a secrets-file value like op://vault/Upfluence/password is read
                                # from 1Password when the run starts
OP_TIMEOUT_S = 20
# This machine's own keys: consumed here, never handed to a run.
SECRET_KEYS = ("OPENROUTER_API_KEY", "HUB_INGEST_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN")


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", file=sys.stderr, flush=True)


def resolve_op_refs(env, run=None):
    """Replace every `op://vault/item/field` value with the secret 1Password holds for it.

    A reference that does not resolve becomes an empty value, so preflight and the turn's
    credential report show it as missing instead of a run failing halfway with a broken secret.
    Returns {key: "ok" | reason} for the references it saw.
    """
    refs = {k: v for k, v in env.items() if isinstance(v, str) and v.startswith(OP_REF)}
    if not refs:
        return {}
    run = run or subprocess.run
    token = env.get("OP_SERVICE_ACCOUNT_TOKEN", "").strip()
    out = {}
    if not token:
        for k in refs:
            env[k] = ""; out[k] = "OP_SERVICE_ACCOUNT_TOKEN is not set in secrets/_shared.env"
        log("op: " + next(iter(out.values())))
        return out
    cache = {}
    for k, ref in refs.items():
        if ref not in cache:
            try:
                r = run(["op", "read", "--no-newline", ref], capture_output=True, text=True,
                        timeout=OP_TIMEOUT_S, env={**os.environ, "OP_SERVICE_ACCOUNT_TOKEN": token})
                if r.returncode == 0 and r.stdout != "":
                    cache[ref] = (True, r.stdout)
                else:
                    lines = (r.stderr or "").strip().splitlines()
                    cache[ref] = (False, lines[-1] if lines else f"op exited {r.returncode}")
            except FileNotFoundError:
                cache[ref] = (False, "the 1Password CLI (`op`) is not installed on PATH")
            except subprocess.TimeoutExpired:
                cache[ref] = (False, f"op read timed out after {OP_TIMEOUT_S}s")
        ok, val = cache[ref]
        if ok:
            env[k] = val; out[k] = "ok"
        else:
            env[k] = ""; out[k] = val
            log(f"op: {k} did not resolve from 1Password: {val}")
    return out
