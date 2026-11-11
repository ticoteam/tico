"""Named profiles for the `tico` CLI: one shell talks to several companies (`aws --profile`, `gh auth`).

A profile is a company's URL and the token to use there, kept in `~/.config/tico/profiles.json` (or `TICO_CONFIG`),
mode 600. The token is either stored in the file (`tico auth add NAME --url U --with-token`, read from stdin) or named
by path (`--token-file PATH`) and read at each call, so a token file that is replaced keeps working.

Which credential a command uses, first match wins:

1. `--profile NAME` on the line (anywhere on it);
2. `TICO_PROFILE` in the environment;
3. `HUB_API_URL` + `HUB_TOKEN` in the environment: a bot's turn (the runner sets them) or a person's own script;
4. the default profile of the file (`tico auth use NAME`);
5. for `tico sql` and the tool reads only, this Mac's runner credential (clients/hubcli.py `runner_credential`).

A bot's turn never uses a profile: its own token is what it is, and `--profile` or `TICO_PROFILE` there is refused.
No command prints a token, except `tico token create`, which prints the new one once on stdout.
"""

import json
import os
import stat
import sys
from pathlib import Path

NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")


class ProfileError(Exception):
    """A profile that is missing, malformed or unreadable: the CLI says why and exits with the sign-in code."""


def config_path():
    return Path(os.environ.get("TICO_CONFIG") or Path.home() / ".config" / "tico" / "profiles.json")


def in_bot_turn():
    return bool(os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE"))


def valid_name(name):
    return bool(name) and len(name) <= 64 and set(name.lower()) <= NAME_CHARS and name[0] not in "-."


def load():
    """The config as {"default": name or None, "profiles": {name: {url, token | token_file}}}."""
    path = config_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {"default": None, "profiles": {}}
    except OSError as exc:
        raise ProfileError(f"cannot read {path}: {exc.strerror}") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise ProfileError(f"{path} is not valid JSON") from None
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        raise ProfileError(f"{path} has no \"profiles\" object")
    if any("token" in p for p in profiles.values() if isinstance(p, dict)) and os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ProfileError(f"{path} holds tokens and others can read it (mode {mode:o}); run chmod 600 {path}")
    return {"default": data.get("default"), "profiles": profiles}


def save(config):
    """Write the config atomically, mode 600, its folder 700."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump({"default": config.get("default"), "profiles": config["profiles"]}, out, indent=2, sort_keys=True)
        out.write("\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def read_token_file(path):
    try:
        token = Path(path).expanduser().read_text().strip()
    except OSError as exc:
        raise ProfileError(f"cannot read the token file {path}: {exc.strerror}") from None
    if not token or len(token.split()) != 1:
        raise ProfileError(f"the token file {path} holds no single token")
    return token


def credential(name):
    """(url, token) of a profile."""
    config = load()
    profile = config["profiles"].get(name)
    if not isinstance(profile, dict) or not profile.get("url"):
        known = ", ".join(sorted(config["profiles"])) or "none yet"
        raise ProfileError(f'no profile "{name}" in {config_path()} (profiles: {known}); add one with tico auth add')
    if profile.get("token_file"):
        return profile["url"], read_token_file(profile["token_file"])
    if profile.get("token"):
        return profile["url"], profile["token"]
    raise ProfileError(f'profile "{name}" has no token; add it again with tico auth add')


def split_profile(argv):
    """Pull `--profile NAME` / `--profile=NAME` out of anywhere on the line before a bare `--`, as `--human` is.
    Returns (argv without it, name or None)."""
    argv = list(argv)
    for i, a in enumerate(argv):
        if a == "--":
            break
        if a == "--profile" and i + 1 < len(argv):
            return argv[:i] + argv[i + 2 :], argv[i + 1]
        if a.startswith("--profile="):
            return argv[:i] + argv[i + 1 :], a.split("=", 1)[1]
    return argv, None


# Which profile the command runs as, for `tico whoami`: (name, source) once resolve() chose one.
ACTIVE = {"profile": None, "source": None}


def resolve(flag):
    """Point HUB_API_URL / HUB_TOKEN at the chosen profile, when one applies (the order in the module docstring).
    Returns True when a profile was applied; False leaves the environment as it was."""
    name, source = (flag, "flag") if flag else (os.environ.get("TICO_PROFILE") or None, "TICO_PROFILE")
    if name:
        if in_bot_turn():
            raise ProfileError(
                "a bot's turn runs with its own token; --profile and TICO_PROFILE are for people's shells"
            )
    elif os.environ.get("HUB_API_URL"):
        ACTIVE.update(profile=None, source="HUB_API_URL")
        return False
    else:
        try:
            config = load()
        except ProfileError:
            return False  # a broken file only matters when a profile is asked for
        name, source = config.get("default"), "default"
        if not name or name not in config["profiles"]:
            return False
    url, token = credential(name)
    os.environ["HUB_API_URL"], os.environ["HUB_TOKEN"] = url, token
    ACTIVE.update(profile=name, source=source)
    return True


# ----------------------------------------------------------------------------- tico auth ...
def add(name, url, token_file=None, with_token=False, make_default=False, check=None, stdin=None):
    """Save a profile. `check(url, token)` (the caller's GET /api/v2/me) runs first, so a wrong URL or token is not
    saved; it returns what `tico auth add` prints."""
    from clients.tico import Client

    if not valid_name(name):
        raise ProfileError("a profile name is letters, digits, '-', '_' or '.', at most 64")
    if bool(token_file) == bool(with_token):
        raise ProfileError("give the token one way: --token-file PATH, or --with-token to read it from stdin")
    Client(url, "check")  # the same URL rules as every call: https, origin only
    url = url.rstrip("/")
    if token_file:
        token_file = str(Path(token_file).expanduser().resolve())
        token = read_token_file(token_file)
        entry = {"url": url, "token_file": token_file}
    else:
        token = (stdin or sys.stdin).read().strip()
        if not token or len(token.split()) != 1:
            raise ProfileError("--with-token reads one token from stdin; none was given")
        entry = {"url": url, "token": token}
    shown = check(url, token) if check else {}
    config = load()
    config["profiles"][name] = entry
    if make_default or not config.get("default"):
        config["default"] = name
    save(config)
    return {"profile": name, "url": url, "default": config["default"] == name, **shown}


def listing():
    config = load()
    rows = []
    for name, p in sorted(config["profiles"].items()):
        p = p if isinstance(p, dict) else {}
        rows.append(
            {
                "profile": name,
                "url": p.get("url"),
                "token": ("file " + p["token_file"]) if p.get("token_file") else "stored" if p.get("token") else None,
                "default": name == config.get("default"),
            }
        )
    return {"config": str(config_path()), "profiles": rows}


def remove(name):
    config = load()
    if name not in config["profiles"]:
        raise ProfileError(f'no profile "{name}"')
    del config["profiles"][name]
    if config.get("default") == name:
        config["default"] = None
    save(config)
    return {"profile": name, "removed": True}


def use(name):
    config = load()
    if name not in config["profiles"]:
        raise ProfileError(f'no profile "{name}"')
    config["default"] = name
    save(config)
    return {"profile": name, "default": True}


# ----------------------------------------------------------------------------- --fields
PAGING_KEYS = ("next_cursor", "cursor", "next_offset", "next", "has_more", "next_before", "truncated")


def split_fields(argv):
    """Pull `--fields a,b.c` out of the line (before a bare `--`). Returns (argv, [paths] or None)."""
    argv = list(argv)
    for i, a in enumerate(argv):
        if a == "--":
            break
        if a == "--fields" and i + 1 < len(argv):
            value, rest = argv[i + 1], argv[:i] + argv[i + 2 :]
        elif a.startswith("--fields="):
            value, rest = a.split("=", 1)[1], argv[:i] + argv[i + 1 :]
        else:
            continue
        return rest, [f.strip() for f in value.split(",") if f.strip()]
    return argv, None


def _get(value, path):
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _pick_one(item, fields):
    return {f: _get(item, f) for f in fields} if isinstance(item, dict) else item


def pick(result, fields):
    """`--fields id,title,task.stage_id`: only those fields of the answer, a dotted path reaching in (the key is the
    path as typed; a missing field is null). A list picks from each item. An object that has none of the fields but
    holds one list of objects (`{"tasks": [...], "next_cursor": ...}`) picks from each item of that list and keeps
    its paging keys."""
    if isinstance(result, list):
        return [_pick_one(item, fields) for item in result]
    if not isinstance(result, dict):
        return result
    heads = {f.split(".", 1)[0] for f in fields}
    if heads & set(result):
        return _pick_one(result, fields)
    lists = [k for k, v in result.items() if isinstance(v, list) and v and all(isinstance(i, dict) for i in v)]
    lists = lists or [k for k, v in result.items() if isinstance(v, list) and not v]
    if len(lists) == 1:
        key = lists[0]
        return {
            key: [_pick_one(item, fields) for item in result[key]],
            **{k: result[k] for k in PAGING_KEYS if k in result},
        }
    return _pick_one(result, fields)
