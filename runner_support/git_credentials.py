"""GitHub App token helpers the runner's Python side shares: hub_token asks the hub for a token and holds the answer
briefly, environment puts a token into git's config, token_problem says in plain words why there is none, and the
clone, history and placement helpers in this module use them.

The turn's git credential helper and its gh wrapper are the Rust runner's (runner-rs/src/gitcred.rs); nothing here
runs as a credential helper.
"""

import json
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path

from . import isolation, safe_git

# The leading empty value clears helpers inherited from the machine's git config for github.com,
# so a stale keychain entry cannot win over the scoped token.
KEY = "credential.https://github.com.helper"
# A fixed token, for when the runner's registration file is not known (tests, embedding).
# The bot's repository (`owner/name`) as the hub resolved it, for the turn's publish step.
REPOSITORY_KEY = "TICO_GITHUB_REPOSITORY"
STATIC_HELPER = "!f() { echo username=x-access-token; echo \"password=$GH_TOKEN\"; }; f"
# The hub's own margins (backend/github_app.py): a held token is reused while it has this long left.
GIT_MARGIN, TURN_MARGIN = 10 * 60, 45 * 60
# A held hub answer is asked for again after this long, so a changed grant or placement applies within minutes.
HOLD_S = 300
_flights_lock = threading.Lock()


def _expiry(*values):
    """The earliest of `expires_at` values as epoch seconds; 0 when any is missing (an older server)."""
    try:
        return min(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() for value in values)
    except (TypeError, ValueError):
        return 0


def hub_token(client, body):
    """POST github/token, reusing the answer this process already holds while every token in it has the
    purpose's margin left (at most HOLD_S). Concurrent asks for the same body share one request."""
    try:
        held, flights = client.__dict__.setdefault("github_tokens", ({}, {}))
    except AttributeError:
        return client.post("github/token", body)
    key = json.dumps(body, sort_keys=True)
    margin = GIT_MARGIN if body.get("purpose") == "git" else TURN_MARGIN
    with _flights_lock:
        hit = held.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        flight, leader = flights.get(key), False
        if flight is None:
            flight, leader = flights.setdefault(key, Future()), True
    if not leader:
        return flight.result()
    try:
        granted = client.post("github/token", body)
        flight.set_result(granted)
    except BaseException as exc:
        flight.set_exception(exc)
        raise
    finally:
        with _flights_lock:
            flights.pop(key, None)
    tokens = granted.get("tokens") if isinstance(granted, dict) else None
    until = (
        _expiry(granted.get("expires_at"), *(group.get("expires_at") for group in tokens or ()))
        if isinstance(granted, dict) and granted.get("token")
        else 0
    )
    # Agent Ops' answer depends on the runs live when it was asked; the hub says so with hold=false (older hubs: by name).
    holdable = isinstance(granted, dict) and granted.get("hold", body.get("bot") != "botops") is not False
    if holdable and until - margin > time.time():
        with _flights_lock:
            held[key] = (min(until - margin, time.time() + HOLD_S), granted)
    return granted


def environment(token, helper=STATIC_HELPER):
    return {
        "GH_TOKEN": token,
        "GITHUB_TOKEN": token,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": KEY,
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": KEY,
        "GIT_CONFIG_VALUE_1": helper,
        "GIT_CONFIG_KEY_2": "credential.https://github.com.useHttpPath",
        "GIT_CONFIG_VALUE_2": "true",
    }


def token_problem(exc, repository=""):
    """Why Tico could not get a GitHub token, in plain words, from the hub's refusal (`/github/token`,
    `runners/me/repositories/token`): with the App connected, git cannot reach GitHub without one, so this is
    the reason a clone fails, not git's "could not read Username"."""
    what = f"for {repository}" if repository else "for this bot's repositories"
    status, code = getattr(exc, "status", 0), str(getattr(exc, "code", "") or "")
    detail = " ".join(str(getattr(exc, "detail", "") or "").split())[:200]
    if code == "github_repo_not_accessible" or status == 409 and "not reachable" in detail:
        why = (
            "the GitHub App is not installed on it or cannot reach it; add it to the App's repository access "
            "on GitHub, then check Settings > Tools > GitHub"
        )
    elif status == 403 and "organization" in detail:
        why = f"it is outside the connected GitHub organization ({detail})"
    elif status:
        why = detail or f"the server answered {status} ({code or 'no detail'})"
    else:
        why = f"the server did not answer ({type(exc).__name__})"
    from .redact import scrub_credentials

    return scrub_credentials(f"Tico could not get a GitHub token {what}: {why}")


def _same_repository(url, repository):
    """Whether a remote URL names `owner/name` on github.com, however it is spelled."""
    text = str(url or "").strip().lower().removesuffix("/").removesuffix(".git")
    return text.endswith("github.com/" + repository.lower()) or text.endswith("github.com:" + repository.lower())


def remote_history(repository, env=None, url=None, timeout=30):
    """Whether GitHub already holds history for a bot's repository: (`present` | `empty` | `unknown`, detail).

    A bot set up from a catalog template is materialized only when its repository has nothing in it yet; one that
    does is cloned instead, so a moved bot never runs on a fresh template copy. `unknown` (unreachable, no access)
    says why in `detail`."""
    env = safe_git.environment(env)
    wanted = url or f"https://github.com/{repository}.git"
    try:
        done = isolation.run(
            [*safe_git.PREFIX, "ls-remote", "--heads", wanted],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "unknown", "timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", type(exc).__name__
    if done.returncode != 0:
        lines = (done.stderr or done.stdout or "").strip().splitlines()
        return "unknown", (lines[-1] if lines else f"exit {done.returncode}")[:200]
    return ("present" if done.stdout.strip() else "empty"), ""


def clone_repository(path, repository, env=None, url=None, timeout=180):
    """Bring a bot's GitHub repository onto this computer: the other half of `publish_history`.

    A bot placed on a computer that has never held it (added, or moved from another computer) has no
    checkout there, and nothing else puts one there. `env` carries the bot's scoped token from `apply`
    (or the machine's own git access). Only a missing or empty folder is cloned into; anything already
    there is left as it is. Returns (state, detail), state `cloned` or `failed`; detail says why."""
    path = Path(path)
    from clients.repo_safety import outside_installation

    try:
        outside_installation(path)
    except ValueError as exc:
        return "failed", str(exc)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        return "failed", f"{path.name} already exists here and is not an empty folder; left as it is"
    fresh = not path.exists() or not any(path.iterdir())  # nothing in it is anyone's yet
    env = safe_git.environment(env)
    wanted = url or f"https://github.com/{repository}.git"
    try:
        done = isolation.run(
            [*safe_git.PREFIX, "clone", "--quiet", wanted, str(path)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        discard_partial(path, fresh)
        return (
            "failed",
            f"git clone timed out after {timeout} seconds; a large repository or a slow network needs longer",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        discard_partial(path, fresh)
        return "failed", type(exc).__name__
    if done.returncode == 0:
        if empty_clone(path):
            discard_partial(path, fresh)
            return "failed", (
                f"GitHub's {repository} is empty: the computer that built the bot has not published its "
                "history yet; it is cloned once it has"
            )
        return "cloned", ""
    discard_partial(path, fresh)
    from .redact import env_secrets, git_failure

    text = git_failure(done.stderr or done.stdout, env_secrets(env), limit=240) or f"exit {done.returncode}"
    if any(word in text for word in ("uthentication", "403", "Permission to", "denied to")):
        return "failed", f"GitHub refused the bot's token: {text}"
    if "not found" in text.lower():
        return "failed", f"GitHub has no repository {repository} that this token can see: {text}"
    return "failed", text


def empty_clone(path):
    """Whether `path` holds only a clone of a repository with no commits: what `git clone` leaves (and calls a
    success) when GitHub's repository is still empty. Nothing in it is anyone's work, so it is not a checkout."""
    path = Path(path)
    try:
        if not (path / ".git").is_dir() or any(entry.name != ".git" for entry in path.iterdir()):
            return False
        env = safe_git.clean_environment()
        head = isolation.run(
            [*safe_git.prefix(path), "-C", str(path), "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=10,
        )
        refs = isolation.run(
            [*safe_git.prefix(path), "-C", str(path), "for-each-ref", "--count=1"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return head.returncode != 0 and refs.returncode == 0 and not refs.stdout.strip()


def discard_partial(path, fresh):
    """Remove what a failed clone left in a folder that did not exist before it: otherwise every later try finds
    a folder that "already exists and is not empty" and the checkout never arrives."""
    path = Path(path)
    if fresh and path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)


def repository_name(value):
    """Normalize a Git credential path, GitHub URL or owner/repo argument."""
    value = str(value or "").strip()
    value = re.sub(r"^(?:https?://github\.com/|git@github\.com:)", "", value, flags=re.I)
    parts = value.strip("/").split("/")
    if len(parts) < 2:
        return None
    name = parts[1].removesuffix(".git")
    return parts[0] + "/" + name if re.fullmatch(r"[\w.-]+", parts[0]) and re.fullmatch(r"[\w.-]+", name) else None
