"""GitHub credentials from the team's GitHub App (backend/github_app.py), scoped to the bot's repository.

The hub mints an installation token that lasts an hour, and a turn can run longer, so git does not
keep one: its credential helper is this module, which asks the hub on every credential request and
can reuse a still-valid cached token: from the hub with the runner's own registration, or, where bot code
cannot read that file (runner/isolation.py), from the supervisor's socket with the turn's attempt token. The helper is configured through
environment variables (no file, no askpass script on disk) and the token is only ever printed to
git. `gh` selects a token for its repository through a turn-local command wrapper. With no App
connected, the turn keeps the machine’s git access. App token failures disable that fallback.

Precedence: the App's token wins for every repository in the connected organization. A `GH_TOKEN` credential
granted to the bot is kept aside (`OTHER_TOKEN_KEY`) and used only for repositories outside that organization,
so a stale personal token can never shadow the App. A login shell resets PATH (Debian's /etc/profile, macOS's
path_helper), which can hide the wrapper; the runner image's profile hook and Mac turn startup hooks restore the
turn's PATH from `TURN_PATH_KEY`. A bare `gh` that bypasses the wrapper uses the start-of-turn `GH_TOKEN`, kept
with at least 45 minutes remaining; the credential helper's explicit git-purpose request can reuse a cached token
with at least 10 minutes remaining. The helper first reuses the turn's own start-of-turn token for the repository
while it has that long left, and the supervisor holds each hub answer for a few minutes (`hub_token`), so a busy
turn asks the hub a few times an hour, not once per git command.
"""
import json
import os
import shlex
import shutil
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path

from . import credential_socket, isolation, safe_git
from .outage import log

ROOT = Path(__file__).resolve().parents[1]
# The leading empty value clears helpers inherited from the machine's git config for github.com,
# so a stale keychain entry cannot win over the scoped token.
KEY = "credential.https://github.com.helper"
TOKENS_KEY = "TICO_GITHUB_TOKENS"
# A GitHub token the bot was granted as a credential, for repositories outside the App's organization only.
OTHER_TOKEN_KEY = "TICO_GITHUB_OTHER_TOKEN"
# The turn's PATH, restored by the image's profile hook and the Mac turn's shell startup files.
TURN_PATH_KEY = "TICO_TURN_PATH"
# A fixed token, for when the runner's registration file is not known (tests, embedding).
# The bot's repository (`owner/name`) as the hub resolved it, for the turn's publish step.
REPOSITORY_KEY = "TICO_GITHUB_REPOSITORY"
FAILED_HELPER = '!f() { echo "Tico: GitHub App token unavailable; check Settings > Tools > GitHub" >&2; echo quit=true; }; f'
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
    until = _expiry(granted.get("expires_at"), *(group.get("expires_at") for group in tokens or ())) \
        if isinstance(granted, dict) and granted.get("token") else 0
    # BotOps' answer depends on the runs live when it was asked; the hub says so with hold=false (older hubs: by name).
    holdable = isinstance(granted, dict) and granted.get("hold", body.get("bot") != "botops") is not False
    if holdable and until - margin > time.time():
        with _flights_lock:
            held[key] = (min(until - margin, time.time() + HOLD_S), granted)
    return granted


def held_token(repository, env=None):
    """The turn's start-of-turn token for `repository` while it still has GIT_MARGIN left; '' otherwise."""
    env = os.environ if env is None else env
    try:
        groups = json.loads(env.get(TOKENS_KEY) or "[]")
    except ValueError:
        return ""
    for group in groups if isinstance(groups, list) else ():
        if not isinstance(group, dict):
            continue
        if repository is None or any(str(name).lower() == repository.lower() for name in group.get("repositories", [])):
            fresh = _expiry(group.get("expires_at")) - GIT_MARGIN > time.time()
            return str(group.get("token") or "") if fresh else ""
    return ""


def fresh_helper(config_path, bot, socket_path=None):
    """A git credential helper that fetches a token each time git asks to `get`: from the hub
    with the runner's registration, or from the supervisor's socket when there is one."""
    where = ("--socket", str(socket_path)) if socket_path else (("--config", str(config_path)) if config_path else ())
    run = " ".join(shlex.quote(part) for part in (
        sys.executable, "-m", "runner.git_credentials", *where, "--bot", bot))
    return f"!f() {{ [ \"$1\" = get ] || exit 0; cd {shlex.quote(str(ROOT))} && exec {run}; }}; f"


def environment(token, helper=STATIC_HELPER):
    return {
        "GH_TOKEN": token, "GITHUB_TOKEN": token, "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": KEY, "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": KEY, "GIT_CONFIG_VALUE_1": helper,
        "GIT_CONFIG_KEY_2": "credential.https://github.com.useHttpPath", "GIT_CONFIG_VALUE_2": "true",
    }


def token_problem(exc, repository=""):
    """Why Tico could not get a GitHub token, in plain words, from the hub's refusal (`/github/token`,
    `runners/me/repositories/token`): with the App connected, git cannot reach GitHub without one, so this is
    the reason a clone fails, not git's "could not read Username"."""
    what = f"for {repository}" if repository else "for this bot's repositories"
    status, code = getattr(exc, "status", 0), str(getattr(exc, "code", "") or "")
    detail = " ".join(str(getattr(exc, "detail", "") or "").split())[:200]
    if code == "github_repo_not_accessible" or status == 409 and "not reachable" in detail:
        why = ("the GitHub App is not installed on it or cannot reach it; add it to the App's repository access "
               "on GitHub, then check Settings > Tools > GitHub")
    elif status == 403 and "organization" in detail:
        why = f"it is outside the connected GitHub organization ({detail})"
    elif status:
        why = detail or f"the server answered {status} ({code or 'no detail'})"
    else:
        why = f"the server did not answer ({type(exc).__name__})"
    from .redact import scrub_credentials
    return scrub_credentials(f"Tico could not get a GitHub token {what}: {why}")


def failed_helper(reason=""):
    """A credential helper that tells git, and so the person reading its error, why there is no token."""
    reason = " ".join(str(reason or "").split())[:300]
    if not reason:
        return FAILED_HELPER
    return f'!f() {{ echo {shlex.quote(reason)} >&2; echo quit=true; }}; f'


def apply(env, client, bot, config_path=None, socket_path=None):
    """Add the bot's repository-scoped GitHub credentials to `env`. True when applied.
    Never raises: a turn must not fail because GitHub access could not be arranged."""
    reason = ""
    try:
        granted = hub_token(client, {"bot": bot})
    except Exception as exc:
        reason = token_problem(exc)
        log(f"Tico runner: {bot}: GitHub App token unavailable ({type(exc).__name__}): {reason}")
        granted = {"configured": True}
    if not granted.get("configured"):
        return False
    applied = bool(granted.get('token'))
    stored = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or ""
    if not applied:
        granted = {**granted, 'token': '', 'tokens': []}
    elif stored and stored != granted["token"] and granted.get("repository"):
        env[OTHER_TOKEN_KEY] = stored
    helper = fresh_helper(config_path, bot, socket_path) if (config_path or socket_path or "tokens" in granted) else STATIC_HELPER
    env.update(environment(granted["token"], helper if applied else failed_helper(reason)))
    if "tokens" in granted:
        env[TOKENS_KEY] = json.dumps(granted["tokens"])
        executable = shutil.which("gh", path=env.get("PATH", os.environ.get("PATH")))
        if executable:
            env["TICO_GITHUB_GH"] = executable
            env["TICO_GITHUB_PYTHON"] = sys.executable
            env["TICO_GITHUB_BOT"] = bot
            if config_path and not socket_path:
                env["TICO_GITHUB_CONFIG"] = str(config_path)
            env["PATH"] = str(ROOT / "runner" / "credential_bin") + os.pathsep + env.get("PATH", os.environ.get("PATH", os.defpath))
            env[TURN_PATH_KEY] = env["PATH"]
            if sys.platform == "darwin":
                startup = str(ROOT / "runner" / "shell_init")
                env["TICO_TURN_BASH_ENV"] = env.get("BASH_ENV", "")
                env["TICO_TURN_ZDOTDIR"] = env.get("ZDOTDIR") or env.get("HOME") or str(Path.home())
                env["BASH_ENV"] = str(Path(startup) / "bash-env")
                env["ZDOTDIR"] = startup
    if socket_path:
        env[credential_socket.SOCKET_ENV] = str(socket_path)
    if granted.get("repository"):
        env[REPOSITORY_KEY] = str(granted["repository"])
    return applied


def _same_repository(url, repository):
    """Whether a remote URL names `owner/name` on github.com, however it is spelled."""
    text = str(url or "").strip().lower().removesuffix("/").removesuffix(".git")
    return text.endswith("github.com/" + repository.lower()) or text.endswith("github.com:" + repository.lower())


def publish_history(path, repository, env=None, url=None, timeout=60):
    """Publish a bot's local history to its GitHub repository when the checkout has none yet.

    `hub bot repo-create <slug> --empty` makes an empty repository for a bot built on a
    computer; nothing pushes into it, because a turn's token is scoped to the bot's own repository
    and BotOps cannot push another bot's. So the runner does it, with that bot's own token from
    `apply`: a checkout with commits and no upstream gets `origin` set to the resolved https URL and
    `git push -u origin <branch>`. Never forces. A remote that already has history this checkout
    does not contain is left alone and reported. Returns (state, detail), state one of `published`,
    `current` (nothing to do), `skipped` (not a checkout with commits) or `failed`; detail says why
    for `failed` and `skipped`."""
    path = Path(path)
    if not repository or not (path / ".git").exists():
        return "skipped", "no repository link" if not repository else "not a git checkout"
    env = safe_git.environment(env)

    def git(*args, timeout=15):
        return isolation.run([*safe_git.prefix(path), "-C", str(path), *args], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, env=env, timeout=timeout)

    def why(result):
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        return (lines[-1] if lines else f"exit {result.returncode}")[:200]

    try:
        branch = git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
        if not branch:
            return "skipped", "no branch is checked out"
        if git("rev-parse", "--verify", "-q", "HEAD").returncode != 0:
            return "skipped", "no commits yet"
        if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode == 0:
            return "current", ""
        wanted = url or f"https://github.com/{repository}.git"
        origin = git("config", "--get", "remote.origin.url")      # as configured, before any insteadOf rewrite
        current = origin.stdout.strip() if origin.returncode == 0 else ""
        if current and current != wanted and not _same_repository(current, repository):
            return "failed", f"origin is {current}, not {repository}; left as it is"
        if origin.returncode != 0:
            added = git("remote", "add", "origin", wanted)
            if added.returncode != 0:
                return "failed", "could not add origin: " + why(added)
        elif not current:
            git("remote", "set-url", "origin", wanted)
        listed = git("ls-remote", "--heads", "origin", timeout=timeout)
        if listed.returncode != 0:
            text = why(listed)
            return "failed", ("GitHub refused the bot's token" if any(w in text for w in ("uthentication", "403", "Permission", "denied"))
                              else "could not reach " + repository + ": " + text)
        remote = {line.split("\t")[1].removeprefix("refs/heads/"): line.split("\t")[0]
                  for line in listed.stdout.splitlines() if "\t" in line}
        if remote:
            # Anything already there must be an ancestor of what is pushed, or this would rewrite it.
            names = [branch] if branch in remote else sorted(remote)[:20]
            fetched = git("fetch", "--quiet", "--no-tags", "origin", *names, timeout=timeout).returncode == 0
            if fetched and branch in remote and git("merge-base", "--is-ancestor", remote[branch], "HEAD").returncode == 0:
                pass
            elif fetched and _unrelated(git, [remote[name] for name in names]):
                # No commit in common (a template copy beside the bot's real history): the readiness check keys
                # on "unrelated history" and holds turns, unlike a checkout that merely diverged from GitHub.
                return "failed", (f"{repository} holds unrelated history on {', '.join(names)}"
                                  + ("" if branch in remote else f", not {branch}") + "; nothing pushed")
            elif branch not in remote:
                return "failed", f"{repository} already has history on {', '.join(sorted(remote))}, not {branch}; nothing pushed"
            else:
                return "failed", f"{repository} already has different history on {branch}; nothing pushed"
        pushed = git("push", "-q", "-u", "origin", branch, timeout=timeout)
        if pushed.returncode != 0:
            return "failed", "push failed: " + why(pushed)
        return "published", ""
    except subprocess.TimeoutExpired:
        return "failed", "timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed", type(exc).__name__


def fast_forward(path, env=None, timeout=60):
    """Bring a checkout that tracks GitHub up to it, only when that loses nothing: (state, detail), state `updated`,
    `current` (already there, or ahead), `skipped` (no upstream to follow) or `kept` (local changes, diverged, or
    GitHub unreachable; the copy is left exactly as it was)."""
    path = Path(path)
    env = safe_git.environment(env)

    def git(*args, timeout=15):
        return isolation.run([*safe_git.prefix(path), "-C", str(path), *args], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, env=env, timeout=timeout)

    try:
        if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode != 0:
            return "skipped", "no upstream"
        if git("fetch", "--quiet", "--no-tags", timeout=timeout).returncode != 0:
            return "kept", "could not reach GitHub"
        if git("merge-base", "--is-ancestor", "@{u}", "HEAD").returncode == 0:
            return "current", ""
        if git("merge-base", "--is-ancestor", "HEAD", "@{u}").returncode != 0:
            return "kept", "it has commits GitHub does not, and GitHub has commits it does not"
        if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return "kept", "it has uncommitted changes"
        merged = git("merge", "--ff-only", "--quiet", "@{u}", timeout=timeout)
        return ("updated", "") if merged.returncode == 0 else ("kept", "fast-forward failed")
    except subprocess.TimeoutExpired:
        return "kept", "timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        return "kept", type(exc).__name__


def _unrelated(git, commits):
    """Whether HEAD shares no commit with any of `commits` (already fetched). Only a definite answer counts:
    a shallow checkout or a git error is not proof, so it is never called unrelated."""
    if git("rev-parse", "--is-shallow-repository").stdout.strip() != "false":
        return False
    codes = [git("merge-base", commit, "HEAD").returncode for commit in commits]
    return bool(codes) and all(code == 1 for code in codes)


def remote_history(repository, env=None, url=None, timeout=30):
    """Whether GitHub already holds history for a bot's repository: (`present` | `empty` | `unknown`, detail).

    A bot set up from a catalog template is materialized only when its repository has nothing in it yet; one that
    does is cloned instead, so a moved bot never runs on a fresh template copy. `unknown` (unreachable, no access)
    says why in `detail`."""
    env = safe_git.environment(env)
    wanted = url or f"https://github.com/{repository}.git"
    try:
        done = isolation.run([*safe_git.PREFIX, "ls-remote", "--heads", wanted], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, env=env, timeout=timeout)
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
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        return "failed", f"{path.name} already exists here and is not an empty folder; left as it is"
    env = safe_git.environment(env)
    wanted = url or f"https://github.com/{repository}.git"
    try:
        done = isolation.run([*safe_git.PREFIX, "clone", "--quiet", wanted, str(path)], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "failed", "timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed", type(exc).__name__
    if done.returncode == 0:
        return "cloned", ""
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    text = (lines[-1] if lines else f"exit {done.returncode}")[:200]
    if any(word in text for word in ("uthentication", "403", "Permission", "denied")):
        return "failed", "GitHub refused the bot's token"
    if "not found" in text.lower():
        return "failed", f"GitHub has no repository {repository} that this token can see"
    return "failed", text


def repository_name(value):
    """Normalize a Git credential path, GitHub URL or owner/repo argument."""
    value = str(value or "").strip()
    value = re.sub(r"^(?:https?://github\.com/|git@github\.com:)", "", value, flags=re.I)
    parts = value.strip("/").split("/")
    if len(parts) < 2:
        return None
    name = parts[1].removesuffix(".git")
    return parts[0] + "/" + name if re.fullmatch(r"[\w.-]+", parts[0]) and re.fullmatch(r"[\w.-]+", name) else None


def select_token(granted, repository=None):
    """Old servers have one token; new ones have authoritative repository groups."""
    if not granted.get("configured"):
        return ""
    if repository and "tokens" in granted:
        for group in granted["tokens"]:
            if any(str(name).lower() == repository.lower() for name in group.get("repositories", [])):
                return group.get("token", "")
        return ""
    return granted.get("token", "")


def outside_token(repository, env=None):
    """The bot's own GitHub credential, for a repository outside the App's organization; '' otherwise."""
    env = os.environ if env is None else env
    org = str(env.get(REPOSITORY_KEY) or "").partition("/")[0].lower()
    owner = str(repository or "").partition("/")[0].lower()
    return env.get(OTHER_TOKEN_KEY, "") if org and owner and owner != org else ""


def credential(config_path, bot, socket_path=None, repository=None):
    """The turn's matching token while it has GIT_MARGIN left, else fresh repository credentials, falling back to
    the turn's matching token in memory. A repository outside the App's organization gets the bot's own GitHub
    credential, if it was granted one."""
    other = outside_token(repository) or held_token(repository)
    if other:
        return other
    from clients.tico import Client
    try:
        if socket_path:
            return credential_socket.request(socket_path, os.environ.get("HUB_TOKEN", ""), timeout=15,
                                             repository=repository, purpose="git")
        if config_path:
            config = json.loads(Path(config_path).read_text())
            granted = Client(config["url"], config["token"], timeout=10, retries=1).post(
                "github/token", {"bot": bot, "purpose": "git"})
            return select_token(granted, repository)
    except Exception as exc:
        reason = str(exc) if socket_path else token_problem(exc, repository)
        print(f"Tico runner: no fresh GitHub App token ({type(exc).__name__}): {reason[:300]}", file=sys.stderr)
    granted = {"configured": True, "token": os.environ.get("GH_TOKEN", "")}
    if TOKENS_KEY in os.environ:
        granted["tokens"] = json.loads(os.environ[TOKENS_KEY])
    return select_token(granted, repository)


def gh_repository(argv, env):
    skip, positional = False, []
    for index, arg in enumerate(argv):
        if skip:
            skip = False
            continue
        if arg in ('--body', '-b', '--title', '-t', '--body-file', '-F', '--field', '-f', '--raw-field', '--header', '-H',
                   '--comment', '--notes', '-m', '--message', '--jq', '-q', '--search', '--template'):
            skip = True
            continue
        if arg in ("-R", "--repo") and index + 1 < len(argv):
            return repository_name(argv[index + 1])
        if arg.startswith("--repo=") or (arg.startswith("-R") and len(arg) > 2):
            return repository_name(arg.split("=", 1)[1] if arg.startswith("--repo=") else arg[2:])
        if not arg.startswith('-'):
            positional.append(arg)
    for arg in positional:
        if arg.startswith('https://github.com/'):
            return repository_name(arg)
        if arg.lstrip('/').startswith('repos/'):
            name = repository_name(arg.lstrip('/')[6:])
            if name:
                return name
    if len(argv) > 2 and argv[0] == "repo" and argv[1] in ("view", "clone", "fork"):
        if not argv[2].startswith("-"):
            return repository_name(argv[2])
    if env.get("GH_REPO"):
        return repository_name(env["GH_REPO"])
    remote = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True,
                            text=True, timeout=10, env=env)
    return repository_name(remote.stdout) if remote.returncode == 0 else None


def gh_main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    env = dict(os.environ)
    repository = None
    try:
        repository = gh_repository(argv, env)
        token = credential(env.get("TICO_GITHUB_CONFIG"), env.get("TICO_GITHUB_BOT"),
                           env.get(credential_socket.SOCKET_ENV), repository)
    except (OSError, subprocess.SubprocessError):
        token = ""
    if token:
        env.update(GH_TOKEN=token, GITHUB_TOKEN=token)
    else:
        print("Tico runner: no GitHub App token for this repository; check Settings > Tools > GitHub", file=sys.stderr)
        return 1
    os.execve(env["TICO_GITHUB_GH"], [env["TICO_GITHUB_GH"], *argv], env)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="git credential helper backed by the Tico hub")
    parser.add_argument("--config")
    parser.add_argument("--socket")
    parser.add_argument("--bot", required=True)
    args = parser.parse_args(argv)
    wanted = dict(line.split("=", 1) for line in sys.stdin.read().splitlines() if "=" in line)
    if wanted.get("host") != (os.environ.get("TICO_GITHUB_HOST") or "github.com"):   # the override is for tests
        return
    token = credential(args.config, args.bot, args.socket, repository_name(wanted.get("path")))
    if token:
        sys.stdout.write(f"username=x-access-token\npassword={token}\n")
    else:
        print("Tico runner: no GitHub App token for this repository; check Settings > Tools > GitHub", file=sys.stderr)
        sys.stdout.write("quit=true\n")


if __name__ == "__main__":
    main()
