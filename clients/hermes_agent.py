#!/usr/bin/env python3
"""Register a Hermes or OpenClaw profile with Tico as an external agent, and keep its heartbeat going.

One file, standard library only, for the box that runs the profile; Tico serves it at
`GET /api/v2/agents/setup-script` so the box needs no checkout. Add `--harness openclaw` to any
command for an OpenClaw profile (`--profile` is then optional: the default profile is ~/.openclaw).
What it does:

    python3 hermes_agent.py install --profile scout --url https://tico.example.com --bot scout --token tico-agent-...
        1. checks the token is that bot's agent credential (`GET /api/v2/me`);
        2. puts Tico in the profile's config.yaml as an MCP server (`mcp_servers.tico`),
           with the token in the profile's `.env` as TICO_AGENT_TOKEN, so the profile's
           agent has the Tico tools allowed by its bot credential;
        3. saves the credential in ~/.config/tico/agents/<profile>.json (mode 600);
        4. posts one heartbeat and installs a timer (launchd on macOS, a systemd user timer on
           Linux) that posts one every minute. The bot shows offline after three misses.
        5. installs the `tico-sync` skill in the profile and one scheduled job, named `tico-sync`,
           that makes the agent look at what is waiting (`--sync 15m|1h|daily|'<cron>'|off`, default 1h).
           For OpenClaw no MCP entry is added: the skill calls Tico through this file (`call`).
    python3 hermes_agent.py pair --profile scout --url https://tico.example.com
        the same steps with no token to copy: it prints a code and what to tell BotOps,
        waits for a person (or BotOps) to approve it for a bot, and receives the credential itself.
    python3 hermes_agent.py update --profile scout       # newest connector from Tico, then install again
    python3 hermes_agent.py doctor --profile scout       # what works, what does not, old tool names
    python3 hermes_agent.py heartbeat --profile scout    # what the timer runs
    python3 hermes_agent.py status --profile scout       # the last reply, and what is waiting
    python3 hermes_agent.py check --profile scout        # what the sync job asks first: what is waiting
    python3 hermes_agent.py call --profile scout hub_whoami '{}'   # one Tico tool, for agents with no MCP client
    python3 hermes_agent.py reinstall --profile scout --sync 30m   # change how often the agent looks
    python3 hermes_agent.py uninstall --profile scout    # timer, sync job, skill, config entry and env line

The heartbeat is a plain HTTP call, never an agent turn: it proves the box and the profile are
there, not that the model works. What the agent does with Tico is visible on its bot page
like any bot's. The sync job is the agent's own schedule (Hermes cron, OpenClaw cron); Tico never
starts a run. See docs/hermes-agents.md and docs/openclaw-agents.md.
"""

import argparse
import ast
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.3.0"
CONFIG_DIR = Path(os.environ.get("TICO_AGENT_CONFIG_DIR") or Path.home() / ".config" / "tico" / "agents")
EVERY_SECONDS = 60
MCP_SERVER_NAME = "tico"
ENV_TOKEN = "TICO_AGENT_TOKEN"
ENV_URL = "TICO_URL"
BACKOFF_SECONDS = 3600      # one heartbeat an hour for a bot that is archived or whose credential is revoked
LABEL = "tico-agent"
HARNESSES = ("hermes", "openclaw")
SKILL_NAME = "tico-sync"            # the skill (skills/tico-sync/SKILL.md) and the scheduled job share it
JOB_NAME = "tico-sync"
CHECK_SCRIPT = "tico-sync-check.py"  # Hermes's pre-check script, in the profile's scripts/
SYNC_DEFAULT = "1h"
SYNC_MIN_MINUTES = 5
UPDATE_EVERY_SECONDS = 7 * 86400    # the skill updates the connector once a week
FRESH_SECONDS = 300                 # a heartbeat reply this young is what `check` reports
ENV_FILE = "tico.env"               # OpenClaw: the credential, next to its config (mode 600)


class Failure(Exception):
    """Something to tell the person in one line. A failed call also carries what Tico answered."""

    def __init__(self, message, status=None, code="", detail=""):
        super().__init__(message)
        self.status, self.code, self.detail = status, code, detail


# ----------------------------------------------------------------------------- Tico
def _open(url, token, method, path, body=None, timeout=15, headers=None):
    """One call to Tico, returning the response bytes. Always sends this connector's own
    User-Agent: Cloudflare in front of Tico blocks Python's default one (error 1010)."""
    data = json.dumps(body).encode() if body is not None else None
    head = {"Accept": "application/json", "User-Agent": "tico-hermes-agent/" + VERSION}
    if token:
        head["Authorization"] = "Bearer " + token
    if data is not None:
        head["Content-Type"] = "application/json"
        head["Idempotency-Key"] = os.urandom(16).hex()
    head.update(headers or {})
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers=head)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        code = detail = ""
        try:
            error = json.loads(exc.read().decode()).get("error", {})
            code, detail = str(error.get("code", "")), str(error.get("detail", ""))
        except (ValueError, AttributeError):
            pass
        raise Failure(f"{method} {path}: HTTP {exc.code}" + (f": {detail}" if detail else ""),
                      status=exc.code, code=code, detail=detail)
    except urllib.error.URLError as exc:
        raise Failure(f"{method} {path}: {exc.reason}")
    except OSError as exc:
        raise Failure(f"{method} {path}: {exc}")


def request(url, token, method, path, body=None, timeout=15, headers=None):
    return json.loads(_open(url, token, method, path, body, timeout, headers).decode() or "{}")


# ----------------------------------------------------------------------------- the profile
def hermes_home():
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def profile_dir(profile):
    """A profile is a separate Hermes home; `default` is the home itself."""
    if profile in ("", "default"):
        return hermes_home()
    return hermes_home() / "profiles" / profile


def harness_name(harness):
    return "OpenClaw" if harness == "openclaw" else "Hermes"


def ident(harness, profile):
    """The name a profile's saved credential and heartbeat timer go by. Hermes profiles keep their own name;
    an OpenClaw profile is `openclaw-<name>`, so a Hermes and an OpenClaw profile can share a name."""
    return profile if harness == "hermes" else "openclaw-" + profile


def flags(harness, profile):
    """The command-line words that say which profile a command is about."""
    return ["--profile", profile] if harness == "hermes" else ["--harness", "openclaw", "--profile", profile]


def openclaw_dir(profile):
    """An explicit state directory wins over profile defaults, which use OpenClaw's home."""
    state = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if state:
        return Path(state).expanduser().resolve()
    home = os.environ.get("OPENCLAW_HOME", "").strip()
    root = Path(home).expanduser() if home and home not in ("undefined", "null") else Path.home()
    suffix = "" if profile in ("", "default") else "-" + profile
    return (root / (".openclaw" + suffix)).resolve()


def agent_dir(harness, profile):
    return profile_dir(profile) if harness == "hermes" else openclaw_dir(profile)


def harness_version(harness="hermes"):
    exe = shutil.which(harness)
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"v?(\d+\.\d+\.\d+\S*)", (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else "")
    return match.group(1)[:100] if match else ""


def read_model(directory):
    """`model.default` and `model.provider` from the profile's config.yaml, without PyYAML."""
    path = directory / "config.yaml"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return "", ""
    model = provider = ""
    inside = False
    for line in lines:
        if re.match(r"^model:\s*$", line):
            inside = True
            continue
        if inside and line and not line.startswith(" "):
            inside = False
        if inside:
            m = re.match(r"^\s+(default|provider):\s*(.+?)\s*$", line)
            if m:
                value = m.group(2).strip().strip("'\"")
                if m.group(1) == "default":
                    model = value
                else:
                    provider = value
    return model[:200], provider[:100]


def read_openclaw_config(directory):
    """openclaw.json is JSON5; a file with comments or trailing commas reads as empty here."""
    try:
        value = json.loads((directory / "openclaw.json").read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def read_openclaw_model(directory):
    """`agents.defaults.model` of openclaw.json, as (model, provider): `provider/model-name` is provider `provider`."""
    defaults = (read_openclaw_config(directory).get("agents") or {}).get("defaults") or {}
    model = defaults.get("model") if isinstance(defaults, dict) else ""
    if isinstance(model, dict):
        model = model.get("primary")
    model = str(model or "")
    return model[:200], (model.split("/", 1)[0] if "/" in model else "")[:100]


def openclaw_gateway(directory):
    """Whether the profile's Gateway answers: OpenClaw's cron runs inside it."""
    gateway = read_openclaw_config(directory).get("gateway")
    gateway = gateway if isinstance(gateway, dict) else {}
    if gateway.get("mode") == "remote":
        return "gateway remote"
    try:
        port = int(gateway.get("port") or 18789)
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return "gateway running"
    except (OSError, ValueError):
        return "gateway not running"


def gateway_running(directory):
    try:
        pid = int((directory / "gateway.pid").read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def write_private(path, text):
    """Write a file that only its owner can read, all at once: nobody ever sees a half file or a wide mode."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def upsert_env(directory, values, name=".env"):
    path = directory / name
    lines = path.read_text().splitlines() if path.exists() else []
    keys = set(values)
    kept = [line for line in lines if not any(line.startswith(k + "=") for k in keys)]
    kept += [f"{k}={v}" for k, v in values.items()]
    write_private(path, "\n".join(kept) + "\n")


def remove_env(directory, keys, name=".env"):
    path = directory / name
    if not path.exists():
        return
    lines = [line for line in path.read_text().splitlines() if not any(line.startswith(k + "=") for k in keys)]
    if name != ".env" and not lines:
        path.unlink()
        return
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def upsert_mcp(directory, url):
    """Put Tico in `mcp_servers.<tico>` of the profile's config.yaml, keeping the rest.
    With PyYAML the document is rewritten; without it a block is appended when no
    `mcp_servers:` exists yet, and otherwise the person is told what to paste."""
    path = directory / "config.yaml"
    entry = {"url": url.rstrip("/") + "/api/v2/mcp",
             "headers": {"Authorization": "Bearer ${" + ENV_TOKEN + "}"}}
    try:
        import yaml  # noqa: F401
    except ImportError:
        yaml = None
    if yaml:
        config = yaml.safe_load(path.read_text()) if path.exists() else {}
        config = config if isinstance(config, dict) else {}
        servers = config.get("mcp_servers")
        if not isinstance(servers, dict):
            servers = {}
        servers[MCP_SERVER_NAME] = entry
        config["mcp_servers"] = servers
        backup = path.with_suffix(".yaml.tico-bak")
        if path.exists():
            shutil.copy2(path, backup)
        path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
        return "written"
    text = path.read_text() if path.exists() else ""
    block = ("\nmcp_servers:\n  " + MCP_SERVER_NAME + ":\n    url: \"" + entry["url"] + "\"\n"
             "    headers:\n      Authorization: \"Bearer ${" + ENV_TOKEN + "}\"\n")
    if re.search(r"^mcp_servers:", text, re.M):
        print("config.yaml already has mcp_servers and PyYAML is not installed here; add this under it:\n"
              + block.replace("\nmcp_servers:\n", ""), file=sys.stderr)
        return "manual"
    path.write_text(text.rstrip("\n") + "\n" + block)
    return "appended"


def remove_mcp(directory):
    path = directory / "config.yaml"
    try:
        import yaml
    except ImportError:
        print("PyYAML is not installed here; remove mcp_servers." + MCP_SERVER_NAME + " from "
              + str(path) + " by hand", file=sys.stderr)
        return
    if not path.exists():
        return
    config = yaml.safe_load(path.read_text()) or {}
    servers = config.get("mcp_servers") if isinstance(config, dict) else None
    if isinstance(servers, dict) and MCP_SERVER_NAME in servers:
        del servers[MCP_SERVER_NAME]
        if not servers:
            del config["mcp_servers"]
        path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))


# ----------------------------------------------------------------------------- our config
def config_path(profile):
    return CONFIG_DIR / (profile + ".json")


def load_config(profile):
    path = config_path(profile)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise Failure(f"{path} must be readable only by its owner (chmod 600)")
        return json.loads(path.read_text())
    except OSError:
        raise Failure(f"No agent configuration for profile {profile!r}; run pair (or install) first")


def save_config(profile, value):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = config_path(profile)
    write_private(path, json.dumps(value, indent=2))
    return path


def installed_copy():
    """The timer runs a copy under our config dir, so a downloaded file in Downloads can go."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    target = CONFIG_DIR / "hermes_agent.py"
    source = Path(__file__).resolve()
    if source != target.resolve():
        write_private(target, source.read_text())
    return target


# ----------------------------------------------------------------------------- the timer
def timer_label(profile):
    return "team.tico-agent." + profile


def install_timer(profile, python, cli=None):
    """`profile` is the saved name (`ident`); `cli` the words the timer passes to `heartbeat`."""
    cli = cli or ["--profile", profile]
    script = installed_copy()
    log_dir = CONFIG_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / (profile + ".log")
    if sys.platform == "darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / (timer_label(profile) + ".plist")
        plistlib.dump({"Label": timer_label(profile),
                       "ProgramArguments": [python, str(script), "heartbeat", *cli],
                       "StartInterval": EVERY_SECONDS, "RunAtLoad": True,
                       "StandardOutPath": str(log), "StandardErrorPath": str(log),
                       "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                "HOME": str(Path.home())}},
                      plist.open("wb"))
        os.chmod(plist, 0o600)
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        done = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
        if done.returncode != 0:
            raise Failure("launchctl bootstrap failed: " + (done.stderr or done.stdout).strip())
        return f"launchd job {timer_label(profile)} every {EVERY_SECONDS} s, log {log}"
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        units = Path.home() / ".config" / "systemd" / "user"
        units.mkdir(parents=True, exist_ok=True)
        name = "tico-agent-" + profile
        (units / (name + ".service")).write_text(
            "[Unit]\nDescription=Tico heartbeat for agent profile " + profile + "\n\n"
            "[Service]\nType=oneshot\nExecStart=" + python + " " + str(script) + " heartbeat " + shlex.join(cli) + "\n")
        (units / (name + ".timer")).write_text(
            "[Unit]\nDescription=Tico heartbeat timer for agent profile " + profile + "\n\n"
            "[Timer]\nOnBootSec=30\nOnUnitActiveSec=" + str(EVERY_SECONDS) + "s\nAccuracySec=5s\n\n"
            "[Install]\nWantedBy=timers.target\n")
        for command in (["systemctl", "--user", "daemon-reload"],
                        ["systemctl", "--user", "enable", "--now", name + ".timer"]):
            done = subprocess.run(command, capture_output=True, text=True)
            if done.returncode != 0:
                raise Failure(" ".join(command) + " failed: " + (done.stderr or done.stdout).strip())
        return f"systemd user timer {name}.timer every {EVERY_SECONDS} s"
    raise Failure("No timer installed: this is neither macOS nor a Linux with systemd. Run "
                  f"`{python} {script} heartbeat {shlex.join(cli)}` from cron every minute instead")


def remove_timer(profile):
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / (timer_label(profile) + ".plist")
        if plist.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
            plist.unlink()
        return
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        name = "tico-agent-" + profile
        subprocess.run(["systemctl", "--user", "disable", "--now", name + ".timer"], capture_output=True)
        units = Path.home() / ".config" / "systemd" / "user"
        for suffix in (".service", ".timer"):
            (units / (name + suffix)).unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def _suffixed(name, profile):
    """True for `team.tico-agent.scout`, `com.acme.tico-agent.scout`, `tico-agent-scout`: any name
    that ends in `tico-agent.<profile>` or `tico-agent-<profile>` on a word boundary."""
    for sep in (".", "-"):
        tail = LABEL + sep + profile
        if name.endswith(tail) and (name == tail or name[-len(tail) - 1] in ".-_"):
            return True
    return False


def remove_old_timers(profile):
    """Earlier versions labelled the launchd job `com.acme.tico-agent.<profile>` (an environment's name
    on a public connector); a reinstall under the current label would leave two heartbeats. Remove any
    other job or unit for this profile that runs a hermes_agent.py. Returns what was removed."""
    removed = []
    current = timer_label(profile)
    if sys.platform == "darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        for plist in sorted(agents.glob("*.plist")) if agents.is_dir() else []:
            try:
                job = plistlib.loads(plist.read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
            label = str(job.get("Label") or plist.stem)
            arguments = [str(a) for a in job.get("ProgramArguments") or []]
            if label == current or plist.stem == current or not _suffixed(label, profile):
                continue
            if not any(a.endswith("hermes_agent.py") for a in arguments):
                continue
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
            removed.append("launchd job " + label)
    elif sys.platform.startswith("linux") and shutil.which("systemctl"):
        units = Path.home() / ".config" / "systemd" / "user"
        current_name = "tico-agent-" + profile
        stems = sorted({u.stem for u in units.glob("*.service")} | {u.stem for u in units.glob("*.timer")}) if units.is_dir() else []
        changed = False
        for stem in stems:
            if stem == current_name or not _suffixed(stem, profile):
                continue
            try:
                text = (units / (stem + ".service")).read_text()
            except OSError:
                continue
            if "hermes_agent.py" not in text:
                continue
            subprocess.run(["systemctl", "--user", "disable", "--now", stem + ".timer"], capture_output=True)
            for suffix in (".service", ".timer"):
                (units / (stem + suffix)).unlink(missing_ok=True)
            removed.append("systemd unit " + stem)
            changed = True
        if changed:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return removed


def timer_loaded(profile):
    """(loaded, note): is the heartbeat timer known to launchd or systemd right now."""
    if sys.platform == "darwin":
        done = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{timer_label(profile)}"],
                              capture_output=True, text=True)
        return done.returncode == 0, "launchd job " + timer_label(profile)
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        name = "tico-agent-" + profile + ".timer"
        done = subprocess.run(["systemctl", "--user", "is-active", name], capture_output=True, text=True)
        return done.returncode == 0 and done.stdout.strip() == "active", "systemd user timer " + name
    return False, "no launchd or systemd here"


# ----------------------------------------------------------------------------- the sync job
# The agent's own schedule: a Hermes cron job or an OpenClaw cron job that runs the `tico-sync` skill
# (skills/tico-sync/SKILL.md). Tico never starts it; it is a job on the agent's box like any other.
def parse_sync(text):
    """('off', ''), ('every', '15m') or ('cron', '0 9 * * *') from what a person typed: 15m, 1h, every 2h,
    daily, a five-field cron expression, or off."""
    raw = " ".join(str(text if text is not None else SYNC_DEFAULT).strip().lower().split())
    if raw in ("off", "none", "no", "never"):
        return "off", ""
    if raw == "daily":
        return "cron", "0 9 * * *"
    if raw == "hourly":
        return "every", "1h"
    match = re.fullmatch(r"(?:every )?(\d+) ?(m|h|d)", raw)
    if match:
        minutes = int(match.group(1)) * {"m": 1, "h": 60, "d": 1440}[match.group(2)]
        if minutes < SYNC_MIN_MINUTES:
            raise Failure(f"A sync every {minutes} minute(s) is too often; {SYNC_MIN_MINUTES}m is the shortest")
        return "every", (f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m")
    if re.fullmatch(r"[0-9*/,\-a-z?]+( [0-9*/,\-a-z?]+){4}", raw):
        return "cron", raw
    raise Failure(f"Cannot read the sync interval {text!r}: use 15m, 1h, daily, a cron expression such as "
                  "'0 9 * * *', or off")


def sync_words(parsed):
    kind, value = parsed
    return "off" if kind == "off" else ("every " + value if kind == "every" else "cron " + value)


def fetch_skill(url):
    """The tico-sync skill: from Tico (so `update` brings the newest), else from a checkout this file sits in."""
    problem = ""
    try:
        text = _open(url, None, "GET", "/api/v2/agents/sync-skill", timeout=30, headers={"Accept": "text/plain"}).decode()
    except Failure as exc:
        text, problem = "", str(exc)
    if not text.startswith("---") or ("name: " + SKILL_NAME) not in text.split("---")[1]:
        local = Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME / "SKILL.md"
        try:
            text = local.read_text()
        except OSError:
            raise Failure("Tico has no tico-sync skill to install" + (f" ({problem})" if problem else "")
                          + "; update Tico, then run reinstall")
    return text


def render_skill(text, harness, profile, python):
    """Put this profile's connector command and profile words where the skill has {{connector}} and {{profile}}."""
    connector = shlex.quote(python) + " " + shlex.quote(str(installed_copy()))
    return text.replace("{{connector}}", connector).replace("{{profile}}", shlex.join(flags(harness, profile)))


def skill_path(directory):
    return directory / "skills" / SKILL_NAME / "SKILL.md"


def tool_command(harness, profile):
    """The harness's own command for this profile, with its name in any error."""
    exe = shutil.which(harness)
    if not exe:
        raise Failure(f"`{harness}` is not on PATH, so its scheduled job cannot be changed")
    if profile in ("", "default"):
        return [exe]
    return [exe, "-p" if harness == "hermes" else "--profile", profile]


def redact(text):
    """A harness's message with any credential in it blanked out, so it can be printed and saved."""
    text = re.sub(r'(?i)(bearer\s+|(?:token|api[_-]?key|password)["\']?\s*[=:]\s*["\']?)[^\s&"\']+', r"\1[redacted]", text)
    text = re.sub(r"((?:https?|wss?)://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)\b(basic\s+)[A-Za-z0-9+/=]{8,}", r"\1[redacted]", text)
    text = re.sub(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{8,}", "[redacted]", text)
    text = re.sub(r"(?<![\w/.-])[A-Za-z0-9_-]{32,}(?![\w/.-])", "[redacted]", text)   # long opaque tokens, Tico's included
    return re.sub(r"([?&](?:token|key|secret|password)=)[^&\s]+", r"\1[redacted]", text, flags=re.I)


def run_tool(command, what, directory=None):
    try:
        env = None
        if directory is not None:
            env = dict(os.environ, OPENCLAW_STATE_DIR=str(directory))
        done = subprocess.run(command, capture_output=True, text=True, timeout=90, **({"env": env} if env else {}))
    except (OSError, subprocess.SubprocessError) as exc:
        raise Failure(f"{what}: {exc}")
    if done.returncode != 0:
        lines = (done.stderr or done.stdout).strip().splitlines() or ["failed"]
        selected = []
        for pattern in (r"error|gateway closed", r"gateway target", r"source:"):
            line = next((line for line in lines if re.search(pattern, line, re.I)), None)
            if line and line not in selected:
                selected.append(line)
        selected = selected or lines[-1:]
        detail = redact(" · ".join(selected))
        hint = "; start this profile's Gateway, then run reinstall" if "gateway" in detail.lower() else ""
        raise Failure(f"{what}: " + detail[:700] + hint)
    return done.stdout or ""


def json_in(text):
    """The first JSON object in a command's output (OpenClaw prints warnings around it)."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"^[{\[]", text, re.M):
        try:
            return decoder.raw_decode(text[match.start():])[0]
        except ValueError:
            continue
    return None


def sync_jobs(harness, profile, directory):
    """Jobs named `tico-sync` on this profile: [{id, schedule, last_run, status, error, enabled}]. Hermes keeps them in
    <profile>/cron/jobs.json; OpenClaw's live in its Gateway and come from `openclaw cron list`."""
    if harness == "hermes":
        try:
            data = json.loads((directory / "cron" / "jobs.json").read_text())
        except (OSError, ValueError):
            return []
        jobs = data.get("jobs") if isinstance(data, dict) else data
        return [{"id": str(j.get("id")), "schedule": str(j.get("schedule_display") or ""), "enabled": j.get("enabled", True),
                 "last_run": str(j.get("last_run_at") or ""), "status": str(j.get("last_status") or ""),
                 "error": str(j.get("last_error") or "")}
                for j in jobs or [] if isinstance(j, dict) and j.get("name") == JOB_NAME]
    data = json_in(run_tool(tool_command(harness, profile) + ["cron", "list", "--all", "--json"], "openclaw cron list", directory))
    out = []
    for job in (data or {}).get("jobs", []) if isinstance(data, dict) else []:
        if isinstance(job, dict) and job.get("name") == JOB_NAME:
            state = job.get("state") or {}
            schedule = job.get("schedule") or {}
            last = state.get("lastRunAtMs")
            out.append({"id": str(job.get("id")), "enabled": job.get("enabled", True),
                        "schedule": (f"every {int(schedule.get('everyMs', 0)) // 60000}m" if schedule.get("kind") == "every"
                                     else str(schedule.get("expr") or "")),
                        "last_run": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last / 1000)) if last else "",
                        "status": str(state.get("lastStatus") or state.get("lastRunStatus") or ""),
                        "error": str(state.get("lastError") or "")})
    return out


def sync_prompt(harness, directory):
    if harness == "hermes":
        return ("Run the tico-sync skill now. The Tico check above says what is waiting. If it shows nothing waiting "
                "and no connector update due, reply exactly [SILENT] and stop. Otherwise follow the skill.")
    return ("Run the tico-sync skill: read " + str(skill_path(directory)) + " and follow it. Its first step is a "
            "command that says what is waiting; if nothing is waiting and no connector update is due, reply "
            "'idle' and stop.")


CHECK_SCRIPT_TEXT = '''#!/usr/bin/env python3
"""Written by the Tico connector (hermes_agent.py); rewritten by `reinstall`. Hermes runs it before each
tico-sync job and gives the agent what it prints. A last line of {"wakeAgent": false} keeps the agent asleep,
so a run with nothing waiting costs no model call."""
import os
import subprocess
import sys

env = dict(os.environ)
env["TICO_AGENT_CONFIG_DIR"] = %(config_dir)r
env.setdefault("PATH", "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin")
try:
    done = subprocess.run([%(python)r, %(connector)r, "check", "--gate", "--profile", %(profile)r],
                          capture_output=True, text=True, timeout=120, env=env)
    sys.stdout.write(done.stdout)
    if done.returncode != 0:
        print("Tico check failed: " + ((done.stderr or "").strip().splitlines() or ["no message"])[-1][:300])
        print('{"wakeAgent": false}')
except Exception as exc:
    print("Tico check failed: " + str(exc)[:300])
    print('{"wakeAgent": false}')
'''


def write_check_script(directory, profile, python):
    path = directory / "scripts" / CHECK_SCRIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CHECK_SCRIPT_TEXT % {"config_dir": str(CONFIG_DIR), "python": python,
                                         "connector": str(installed_copy()), "profile": profile})
    os.chmod(path, 0o700)
    return path


def create_job(harness, profile, directory, parsed, prompt):
    kind, value = parsed
    command = tool_command(harness, profile)
    if harness == "hermes":
        command += ["cron", "create", "every " + value if kind == "every" else value, prompt, "--name", JOB_NAME,
                    "--skill", SKILL_NAME, "--script", CHECK_SCRIPT, "--deliver", "local"]
    else:
        command += ["cron", "add", "--name", JOB_NAME, "--every" if kind == "every" else "--cron", value,
                    "--session", "isolated", "--message", prompt, "--no-deliver", "--timeout-seconds", "600", "--json"]
    run_tool(command, f"{harness} cron create", directory if harness == "openclaw" else None)


def remove_jobs(harness, profile, directory):
    """Remove every job named tico-sync; returns how many there were."""
    jobs = sync_jobs(harness, profile, directory)
    for job in jobs:
        run_tool(tool_command(harness, profile) + ["cron", "remove" if harness == "hermes" else "rm", job["id"]],
                 f"{harness} cron remove", directory if harness == "openclaw" else None)
    return len(jobs)


def apply_sync(harness, profile, directory, config, raw):
    """Install the skill and make (or replace, or remove) the one sync job. Fills `config`; returns the lines to print.
    A problem here never undoes the credential: it is reported, saved, and `doctor` shows it."""
    parsed = parse_sync(raw)
    python = config.get("python") or sys.executable
    lines = []
    try:
        text = render_skill(fetch_skill(config["url"]), harness, profile, python)
        path = skill_path(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        lines.append(f"skill {SKILL_NAME} in {path}")
        if harness == "hermes":
            write_check_script(directory, profile, python)
        prompt = sync_prompt(harness, directory)
        fingerprint = json.dumps([harness, parsed, prompt, SKILL_NAME, CHECK_SCRIPT])
        existing = sync_jobs(harness, profile, directory)
        if parsed[0] == "off":
            removed = remove_jobs(harness, profile, directory) if existing else 0
            lines.append("sync is off" + (": removed the scheduled job" if removed else "; no scheduled job"))
            config.pop("sync_job", None)
        elif len(existing) == 1 and config.get("sync_job") == fingerprint:
            lines.append(f"sync job {JOB_NAME} ({sync_words(parsed)}) is already in place")
        else:
            if existing:
                remove_jobs(harness, profile, directory)
            create_job(harness, profile, directory, parsed, prompt)
            config["sync_job"] = fingerprint
            lines.append(f"{harness_name(harness)} cron job {JOB_NAME}, {sync_words(parsed)}, skill attached"
                         + (", pre-check " + CHECK_SCRIPT if harness == "hermes" else ""))
        config["sync"] = raw if parsed[0] != "off" else "off"
        config.pop("sync_error", None)
    except Failure as exc:
        config["sync"] = raw if parsed[0] != "off" else "off"
        config["sync_error"] = str(exc)
        config.pop("sync_job", None)
        lines.append("WARNING: the sync job is not in place: " + str(exc)
                     + f"; fix that, then run `reinstall {' '.join(flags(harness, profile))} --sync {raw}`")
    return lines


def remove_sync(harness, profile, directory):
    """What `uninstall` does for the sync: the job, the skill, Hermes's pre-check script. Returns warnings."""
    problems = []
    try:
        remove_jobs(harness, profile, directory)
    except Failure as exc:
        problems.append(f"the {JOB_NAME} job was not removed ({exc}); remove it with `{harness} cron`")
    try:
        skill = skill_path(directory)
        if skill.is_file() and ("name: " + SKILL_NAME) in skill.read_text():
            shutil.rmtree(skill.parent, ignore_errors=True)
        (directory / "scripts" / CHECK_SCRIPT).unlink(missing_ok=True)
    except OSError as exc:
        problems.append(f"could not remove the skill: {exc}")
    return problems


# ----------------------------------------------------------------------------- install, shared
def perform_install(harness, profile, directory, url, bot, token, no_timer=False, sync=None, previous=None):
    """The steps `install`, `pair` and `update` all end in. Never prints the token. `sync` is the interval the
    person asked for (None keeps what `previous`, the saved configuration, says)."""
    url = url.rstrip("/")
    key = ident(harness, profile)
    previous = previous or {}
    me = request(url, token, "GET", "/api/v2/me")
    # `bot` may be the bot's id or its slug; the saved configuration keeps the id (stable across renames) and the
    # slug only to show.
    if me.get("role") != "bot" or bot not in (str(me.get("actor") or "")[4:], me.get("slug")) or not me.get("agent"):
        raise Failure(f"This token is not the agent credential for bot {bot!r}: Tico says "
                      f"{me.get('actor')!r} ({me.get('role')!r})")
    bot, slug = str(me["actor"])[4:], me.get("slug") or bot
    if harness == "hermes":
        upsert_env(directory, {ENV_TOKEN: token, ENV_URL: url})
        mcp = upsert_mcp(directory, url)
    else:
        # OpenClaw gets no MCP entry (2026.3 had no MCP client; later releases do, docs/openclaw-agents.md): the skill
        # calls Tico through this file, which reads the credential from here (and the saved configuration) only.
        upsert_env(directory, {ENV_TOKEN: token, ENV_URL: url}, ENV_FILE)
        mcp = "none"
    python = sys.executable
    config = {"url": url, "bot": bot, "slug": slug, "token": token, "profile": profile,
              "profile_dir": str(directory), "python": python, "harness": me.get("agent")}
    for kept in ("sync", "sync_job", "updated_at"):
        if kept in previous:
            config[kept] = previous[kept]
    config["heartbeat_mode"] = "manual" if no_timer else "timer"
    config.setdefault("updated_at", time.time())
    path = save_config(key, config)
    reply = request(url, token, "POST", "/api/v2/agents/heartbeat", heartbeat_body(harness, profile, directory))
    config["last_reply"] = reply
    config["last_ok"] = time.time()
    if no_timer and previous.get("timer"):
        remove_timer(key)                 # `reinstall --no-timer`: the person runs the heartbeat, so one source
    wanted = sync if sync is not None else previous.get("sync")
    synced = apply_sync(harness, profile, directory, config, wanted) if wanted else []
    save_config(key, config)
    # The timer is the last step: launchd or systemd refusing it (launchd refuses a plist on an external volume)
    # must not cost the pairing, the skill or the sync job, which are all in place by now.
    timer = timer_problem = ""
    if not no_timer:
        try:
            timer = install_timer(key, python, flags(harness, profile))
            config["timer"] = timer
        except Failure as exc:
            timer_problem = str(exc)
            remove_timer(key)             # no half-installed plist or unit left behind
            config["heartbeat_mode"] = "manual"
            config["timer_error"] = timer_problem
        save_config(key, config)
    removed = remove_old_timers(key) if not timer_problem else []
    label = "Paired, finish connecting tools:" if mcp == "manual" else "Registered"
    print(f"{label} {harness_name(harness)} profile {profile!r} as bot {slug!r} at {url}")
    if mcp == "manual":
        command = shlex.join([python, "-m", "pip", "install", "PyYAML"]) + " && " + shlex.join(
            [python, str(installed_copy()), "reinstall", *flags(harness, profile)])
        print("  finish connecting tools: " + command)
    if harness == "hermes":
        print(f"  MCP server `{MCP_SERVER_NAME}` in {directory / 'config.yaml'}: {mcp}; token in {directory / '.env'}")
    else:
        print(f"  no MCP entry in OpenClaw: the skill calls Tico through this connector; token in {directory / ENV_FILE}")
    print(f"  credential saved in {path}")
    print(f"  heartbeat: {timer or 'none (run `heartbeat` yourself every minute)'}")
    for name in removed:
        print(f"  removed the older {name} for this profile, so there is one heartbeat")
    for line in synced:
        print("  " + line)
    waiting = reply.get("waiting", {})
    print(f"  Tico sees it: {waiting.get('messages', 0)} message(s) and {waiting.get('tasks', 0)} task(s) waiting")
    if mcp in ("written", "appended"):
        print("  restart the profile's gateway or chat so it loads the new MCP server (`/reload-mcp` in a chat)")
    if harness == "openclaw" and synced and not any(line.startswith("WARNING") for line in synced):
        print("  new skills load when the next OpenClaw session starts")
    if timer_problem:
        for line in timer_failed_lines(harness, profile, python, timer_problem,
                                       None if config.get("sync_error") else wanted):
            print(line)
    return 0


def timer_failed_lines(harness, profile, python, problem, sync):
    """What `pair`, `install` and `reinstall` print when launchd or systemd refused the heartbeat timer."""
    command = shlex.join([python, str(installed_copy())])
    words = shlex.join(flags(harness, profile))
    lines = ["WARNING: the heartbeat timer was not installed: " + problem,
             "  Everything else is in place: the pairing, the credential"
             + (", the tico-sync skill and its sync job." if sync and sync != "off" else "."),
             "  Without a heartbeat the bot shows offline in Tico. Heartbeat mode is now manual: run",
             f"    {command} heartbeat {words}",
             "  every minute yourself, for example from cron (`crontab -e`):",
             f"    * * * * * {command} heartbeat {words} >/dev/null 2>&1"]
    if sync and sync != "off":
        run = f"openclaw{'' if profile in ('', 'default') else ' --profile ' + profile} cron run <job id>"
        lines.append(f"  The sync job does not need the timer: it runs on {harness_name(harness)}'s own schedule, and its "
                     "first step asks Tico itself when the last heartbeat is over 5 minutes old"
                     + (f". To run it now: `{run}` (the id is in `cron list`)." if harness == "openclaw" else "."))
    lines.append(f"  To retry the timer once the cause is fixed: {command} reinstall {words} --timer")
    return lines


def profile_directory(harness, profile, given=None):
    directory = Path(given).expanduser() if given else agent_dir(harness, profile)
    if harness == "openclaw":
        directory = directory.resolve()
    if not directory.is_dir():
        if harness == "openclaw":
            raise Failure(f"No OpenClaw state directory at {directory}; set the profile up first "
                          f"(`openclaw{'' if profile == 'default' else ' --profile ' + profile} setup`) or pass --profile-dir")
        raise Failure(f"No Hermes profile directory at {directory}; create the profile first "
                      f"(`hermes profile create {profile}`) or pass --profile-dir")
    return directory


# ----------------------------------------------------------------------------- commands
def heartbeat_body(harness, profile, directory):
    if harness == "hermes":
        model, provider = read_model(directory)
        state = "gateway running" if gateway_running(directory) else "gateway not running"
    else:
        model, provider = read_openclaw_model(directory)
        state = openclaw_gateway(directory)
    return {"version": harness_version(harness), "platform": sys.platform + " " + platform.machine(),
            "model": model, "provider": provider, "profile": profile,
            "detail": state + "; heartbeat " + VERSION}


def blocked_message(failure, bot):
    """The one line for a heartbeat Tico refuses for good reasons, or ''. The timer keeps
    trying, but once an hour, so a forgotten profile does not hammer Tico every minute."""
    if failure.status == 409 and (failure.code == "bot_archived" or "archived" in failure.detail.lower()):
        return f"Bot {bot} is archived in Tico: restore it (ask BotOps) or run uninstall"
    if failure.status == 401:
        return f"Bot {bot}: credential revoked: run pair again"
    return ""


def cmd_heartbeat(args):
    config = load_config(args.key)
    hold = config.get("backoff") or {}
    if hold.get("until", 0) > time.time():
        return 0
    directory = Path(config.get("profile_dir") or agent_dir(args.harness, args.profile))
    try:
        reply = request(config["url"], config["token"], "POST", "/api/v2/agents/heartbeat",
                        heartbeat_body(args.harness, args.profile, directory))
    except Failure as exc:
        message = blocked_message(exc, config.get("slug") or config.get("bot", args.profile))
        if not message:
            raise
        config["backoff"] = {"until": time.time() + BACKOFF_SECONDS, "since": hold.get("since") or time.time(),
                             "reason": exc.code or str(exc.status), "message": message}
        save_config(args.key, config)
        print(message, file=sys.stderr)
        return 1
    config["last_reply"] = reply
    config["last_ok"] = time.time()
    if config.pop("backoff", None):
        print(f"Bot {config.get('slug') or config.get('bot', '')} answers again: heartbeat back to every {EVERY_SECONDS} s")
    save_config(args.key, config)
    waiting = reply.get("waiting", {})
    print(f"{reply.get('server_time', '')} {config.get('slug') or config.get('bot', '')}: {waiting.get('messages', 0)} message(s), "
          f"{waiting.get('tasks', 0)} task(s) waiting")
    return 0


def cmd_status(args):
    config = load_config(args.key)
    print(json.dumps({k: config.get(k) for k in ("url", "bot", "profile", "profile_dir", "python", "timer", "heartbeat_mode",
                                                 "sync", "sync_error", "updated_at", "last_reply", "backoff")}, indent=2))
    return 0


def cmd_check(args):
    """What the sync job asks first: how many messages and tasks wait, and whether the weekly connector update
    is due. The answer comes from the last heartbeat reply (the timer posts one a minute), or from a fresh heartbeat
    when that is older than five minutes, so a stopped timer cannot hide work. With `--gate` a last line of
    {"wakeAgent": false} tells Hermes not to start the agent when there is nothing to do."""
    config = load_config(args.key)
    bot = config.get("slug") or config.get("bot", args.profile)
    reply, last_ok = config.get("last_reply"), config.get("last_ok") or 0
    hold = config.get("backoff") or {}
    problem = ""
    if hold.get("until", 0) > time.time():
        problem = hold.get("message") or "heartbeats are paused"
    elif not reply or time.time() - last_ok > FRESH_SECONDS:
        directory = Path(config.get("profile_dir") or agent_dir(args.harness, args.profile))
        try:
            reply = request(config["url"], config["token"], "POST", "/api/v2/agents/heartbeat",
                            heartbeat_body(args.harness, args.profile, directory))
            config["last_reply"], config["last_ok"] = reply, time.time()
            save_config(args.key, config)
        except Failure as exc:
            problem = blocked_message(exc, bot) or str(exc)
    waiting = (reply or {}).get("waiting", {})
    messages, tasks = int(waiting.get("messages", 0)), int(waiting.get("tasks", 0))
    age = time.time() - float(config.get("updated_at") or time.time())
    due = age > UPDATE_EVERY_SECONDS
    if problem:
        print(f"Tico check failed: {problem}")
    elif messages or tasks:
        print(f"Tico: {messages} message(s) and {tasks} task(s) waiting for {bot}.")
    else:
        print(f"Tico: nothing waiting for {bot}.")
    if due:
        print(f"Connector update due (last updated {int(age // 86400)} days ago).")
    # With Tico out of reach the agent could not read its messages either, so it stays asleep too.
    if args.gate and (problem or not (messages or tasks) and not due):
        print(json.dumps({"wakeAgent": False}))
    return 0


def cmd_call(args):
    """One Tico tool over Tico's MCP endpoint, for an agent with no MCP client. Prints the tool's JSON."""
    config = load_config(args.key)
    url, token = config["url"], config["token"]
    if args.harness == "openclaw":
        url = env_value(Path(config.get("profile_dir") or openclaw_dir(args.profile)), ENV_URL, ENV_FILE) or url
        token = env_value(Path(config.get("profile_dir") or openclaw_dir(args.profile)), ENV_TOKEN, ENV_FILE) or token
    try:
        arguments = json.loads(args.arguments or "{}")
    except ValueError:
        raise Failure("The tool's arguments must be one JSON object, for example '{\"to\":\"ana\",\"text\":\"Done\"}'")
    if not isinstance(arguments, dict):
        raise Failure("The tool's arguments must be one JSON object")
    reply = request(url, token, "POST", "/api/v2/mcp",
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": args.tool, "arguments": arguments}}, timeout=90)
    if reply.get("error"):
        raise Failure(f"{args.tool}: {reply['error'].get('message', 'refused')}")
    result = reply.get("result") or {}
    text = "\n".join(str(part.get("text", "")) for part in result.get("content") or [] if isinstance(part, dict))
    if result.get("isError"):
        print(text or "the tool refused", file=sys.stderr)
        return 1
    print(text)
    return 0


def cmd_install(args):
    directory = profile_directory(args.harness, args.profile, args.profile_dir)
    parse_sync(args.sync)
    return perform_install(args.harness, args.profile, directory, args.url, args.bot, args.token, args.no_timer, args.sync)


def pair_code_sentence(harness, profile, code):
    return f"connect my {harness_name(harness)} profile {profile}, code {code}"


def cmd_pair(args):
    directory = profile_directory(args.harness, args.profile, args.profile_dir)
    parse_sync(args.sync)
    url = args.url.rstrip("/")
    created = request(url, None, "POST", "/api/v2/agents/pairings",
                      {"profile": args.profile, "harness": args.harness,
                       "host": socket.gethostname().split(".")[0], "version": VERSION})
    pairing, code, secret = created.get("pairing_id"), created.get("code"), created.get("secret")
    if not (pairing and code and secret):
        raise Failure("Tico did not start a pairing; is this the address Tico shows?")
    wait = max(1, int(created.get("expires_in") or 600))
    every = min(30, max(1, int(created.get("poll_every") or 3)))
    print(f"Tell BotOps (or press Pair on the bot in Settings → Bots): {pair_code_sentence(args.harness, args.profile, code)}")
    print(f"Waiting for approval (the code works for {wait // 60 or 1} minute(s); Ctrl-C to stop)...", flush=True)
    deadline = time.monotonic() + wait
    trouble = 0
    try:
        while True:
            try:
                answer = request(url, None, "GET", "/api/v2/agents/pairings/" + str(pairing),
                                 headers={"X-Pairing-Secret": secret})
                trouble = 0
            except Failure as exc:
                if exc.status is not None and exc.status < 500 and exc.status != 429:
                    raise Failure("Tico no longer knows this pairing; run pair again") from exc
                trouble += 1
                if trouble >= 10:
                    raise
                answer = {"state": "pending"}
            state = answer.get("state")
            if state == "approved":
                break
            if state == "expired":
                raise Failure(f"The code {code} expired before anyone approved it; run pair again")
            if state == "declined":
                raise Failure(f"The code {code} was declined in Tico; nothing was changed on this computer")
            if state not in ("pending", None):
                raise Failure(f"The pairing is {state}; run pair again")
            if time.monotonic() >= deadline:
                raise Failure(f"The code {code} expired before anyone approved it; run pair again")
            time.sleep(every)
    except KeyboardInterrupt:
        print("\nStopped. The code stays valid until it expires but nothing was changed here.", file=sys.stderr)
        return 130
    bot, token = answer.get("bot"), answer.get("token")
    if not bot or not token:
        raise Failure("Tico approved the pairing without a bot or credential; run pair again")
    print(f"Approved for bot {answer.get('slug') or bot!r}.")
    return perform_install(args.harness, args.profile, directory, answer.get("url") or url, bot, token, args.no_timer, args.sync)


def fetch_script(url, token):
    text = _open(url, token, "GET", "/api/v2/agents/setup-script", timeout=30, headers={"Accept": "text/plain"}).decode()
    try:
        ast.parse(text)
    except SyntaxError:
        raise Failure("Tico did not send a Python file (a sign-in page, perhaps); nothing was replaced")
    if "def cmd_heartbeat" not in text or "tico-hermes-agent" not in text:
        raise Failure("The download is not the Hermes connector; nothing was replaced")
    return text


def script_version(text):
    match = re.search(r'^VERSION = "([^"]+)"', text, re.M)
    return match.group(1) if match else "?"


def cmd_update(args):
    config = load_config(args.key)
    text = fetch_script(config["url"], config["token"])
    target = CONFIG_DIR / "hermes_agent.py"
    before = script_version(target.read_text()) if target.exists() else VERSION
    write_private(target, text)
    after = script_version(text)
    print(f"Connector {before} -> {after} in {target}" if before != after else f"Connector is current ({after}); reinstalled it in {target}")
    # The new file does the install, so a newer connector's steps apply now, not on the next update.
    done = subprocess.run([config.get("python") or sys.executable, str(target), "reinstall", *flags(args.harness, args.profile)])
    if done.returncode != 0:
        raise Failure("The new connector could not finish installing; see above")
    config = load_config(args.key)           # the new file saved it; the weekly clock restarts now
    config["updated_at"] = time.time()
    save_config(args.key, config)
    return 0


def cmd_reinstall(args):
    """Run the install steps again with what `install` or `pair` saved. `--sync` changes how often the agent looks;
    `--timer` and `--no-timer` change the heartbeat mode, otherwise the saved one is kept."""
    config = load_config(args.key)
    if args.sync is not None:
        parse_sync(args.sync)
    directory = profile_directory(args.harness, args.profile, config.get("profile_dir"))
    if args.timer or args.no_timer:
        no_timer = args.no_timer
    else:
        no_timer = config.get("heartbeat_mode") == "manual" or ("heartbeat_mode" not in config and not config.get("timer"))
    return perform_install(args.harness, args.profile, directory, config["url"], config["bot"], config["token"],
                           no_timer=no_timer, sync=args.sync, previous=config)


# Tools renamed in Tico 0.2.21 (CHANGELOG, Breaking changes), old -> new. Names ending in `_` are prefixes.
RENAMED_TOOLS = {
    "hub_say": "hub_message_send", "hub_notice": "hub_message_send (fyi)", "hub_inbox": "hub_message_list",
    "hub_ack": "hub_message_mark_read", "hub_history": "hub_conversation_show",
    "hub_ask": "hub_question_ask", "hub_answer": "hub_question_answer",
    "hub_board": "hub_task_list (all)", "hub_task_stuck": "hub_task_list (stuck)",
    "hub_goals": "hub_goal_list", "hub_goal_auto": "hub_goal_status (auto)", "hub_kpi_add": "hub_kpi_create",
    "hub_context_search": "hub_doc_search", "hub_context_show": "hub_doc_read",
    "hub_meetings_transcript": "hub_meeting_read", "hub_bot_register": "hub_bot_create",
    "hub_bot_set": "hub_bot_update", "hub_bot_onboarded": "hub_bot_setup_done", "hub_turns": "hub_run_list",
    "hub_fleet": "hub_health_check", "hub_fleet-check": "hub_health_check", "hub_fleet_check": "hub_health_check",
    "hub_computers": "hub_computer_list", "hub_catalog": "hub_template_list",
    "hub_routine_on": "hub_automation_update (enabled)", "hub_routine_off": "hub_automation_update (enabled)",
    "hub_routine_": "hub_automation_", "hub_rule_": "hub_automation_",
    "hub_org": "hub_team_show", "hub_updates": "hub_update_list",
    "hub_integrations": "hub_tool_*", "hub_integration": "hub_tool_*", "hub_queries": "hub_tool_*",
    "hub_learn": "hub_tool_*", "hub_decisions": "hub_decision_ask",
    "hub_judge": "hub_decision_ask", "hub_listen_judge": "hub_listening_*",
    "hub_docs_": "hub_doc_", "hub_files_": "hub_file_", "hub_status_": "hub_bot_status_",
    "hub_people_": "hub_human_", "hub_person_": "hub_human_", "hub_tools_": "hub_tool_",
    "hub_batch_": "hub_needs_you_", "hub_listen_": "hub_listening_", "hub_intake_": "hub_listening_",
}
TOOL_WORD = re.compile(r"\bhub_[A-Za-z0-9_]+(?:-check)?")
SCAN_LIMIT = 2_000_000
MAX_SHOWN = 100


def new_tool_name(word):
    if word in RENAMED_TOOLS:
        return RENAMED_TOOLS[word]
    for old, new in RENAMED_TOOLS.items():
        if old.endswith("_") and word.startswith(old):
            return new + (word[len(old):] or "*")
    return ""


def scan_old_tool_names(directory):
    """(file, line number, old name, new name) for each renamed tool a profile's own text still uses:
    SOUL.md, skills/, cron definitions and memories/. Reads only; never edits."""
    roots = [directory / "SOUL.md", directory / "skills", directory / "cron", directory / "memories"]
    roots += sorted(directory.glob("cron*.json")) + sorted(directory.glob("cron*.yaml")) + sorted(directory.glob("cron*.yml"))
    found = []
    for root in roots:
        files = [root] if root.is_file() else sorted(f for f in root.rglob("*") if f.is_file()) if root.is_dir() else []
        for path in files:
            if any(part.startswith(".") for part in path.relative_to(directory).parts):
                continue
            try:
                if path.stat().st_size > SCAN_LIMIT:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, 1):
                for word in TOOL_WORD.findall(line):
                    new = new_tool_name(word)
                    if new:
                        found.append((path, number, word, new))
    return found


def env_value(directory, key, name=".env"):
    try:
        for line in (directory / name).read_text().splitlines():
            if line.startswith(key + "="):
                return line[len(key) + 1:].strip().strip("'\"")
    except OSError:
        pass
    return ""


def mcp_url(directory):
    """The url of `mcp_servers.tico` in config.yaml, '' when there is none."""
    try:
        text = (directory / "config.yaml").read_text()
    except OSError:
        return ""
    try:
        import yaml
        config = yaml.safe_load(text)
        entry = ((config or {}).get("mcp_servers") or {}).get(MCP_SERVER_NAME) if isinstance(config, dict) else None
        return str(entry.get("url") or "(no url)") if isinstance(entry, dict) else ""
    except ImportError:
        pass
    except Exception:
        return ""
    inside = seen = False
    for line in text.splitlines():
        if re.match(r"^mcp_servers:\s*$", line):
            inside = True
        elif inside and line and not line.startswith((" ", "\t", "#")):
            inside = False
        elif inside and re.match(r"^\s+" + MCP_SERVER_NAME + r":\s*$", line):
            seen = True
        elif inside and seen:
            m = re.match(r"^\s+url:\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip("'\"")
    return "(no url)" if seen else ""


# Environment variables in a Hermes profile's .env that connect its gateway to a chat service.
GATEWAY_CHANNELS = (("SLACK_BOT_TOKEN", "Slack"), ("TELEGRAM_BOT_TOKEN", "Telegram"), ("DISCORD_BOT_TOKEN", "Discord"),
                    ("WHATSAPP_ENABLED", "WhatsApp"), ("MATTERMOST_TOKEN", "Mattermost"), ("SIGNAL_HTTP_URL", "Signal"))


def gateway_channels(directory):
    """The chat services the profile's gateway is set up for, read from its .env ([] when none)."""
    return [name for key, name in GATEWAY_CHANNELS if env_value(directory, key)]


def doctor_gateway(say, profile, directory, config):
    """Hermes fires cron jobs, tico-sync included, only while the profile's gateway runs; and a gateway on a chat
    service answers people there without Tico's message limits and checks."""
    command = "hermes" + ("" if profile in ("", "default") else f" -p {profile}")
    running = gateway_running(directory)
    if running:
        say("ok", "the profile's gateway is running")
    elif config.get("sync") != "off":
        say("warn", "the profile's gateway is not running, and Hermes runs its cron jobs, the tico-sync job included, only "
                    f"while it is: run `{command} gateway install` ({command} gateway status shows it)")
    channels = gateway_channels(directory)
    if channels:
        say("warn", f"the gateway is set up for {', '.join(channels)}; chats there bypass Tico's rules "
                    "(message limits and checks). Remove those tokens from the profile's .env for a bot that should speak only through Tico")


def doctor_sync(say, harness, profile, directory, config):
    """The sync job: is the skill there, is the job scheduled, when did it last run."""
    wanted = config.get("sync")
    if not wanted:
        say("warn", f"no sync job was set up; run reinstall {' '.join(flags(harness, profile))} --sync 1h")
        return
    if wanted == "off":
        say("ok", "sync is off: the agent looks at Tico only when something makes it")
        return
    skill = skill_path(directory)
    say("ok" if skill.is_file() else "problem",
        f"skill {SKILL_NAME} is in {skill}" if skill.is_file() else f"skill {SKILL_NAME} is missing from {skill}; run update")
    if config.get("sync_error"):
        say("problem", "the sync job was not created: " + str(config["sync_error"]))
    # Listing OpenClaw's jobs needs its Gateway, so a stopped Gateway is said first, as what to fix: with it down
    # no sync runs, and messages wait however healthy the heartbeat looks.
    if harness == "openclaw" and openclaw_gateway(directory) == "gateway not running":
        command = "openclaw" + ("" if profile in ("", "default") else f" --profile {profile}")
        say("problem", f"the OpenClaw Gateway is not running, so the {JOB_NAME} job cannot run: start it "
                       f"(`{command} gateway start`, or `{command} gateway run` in a terminal); the job is kept")
        return
    try:
        jobs = sync_jobs(harness, profile, directory)
    except Failure as exc:
        say("warn", f"cannot read {harness_name(harness)}'s scheduled jobs: {exc}")
        return
    if not jobs:
        if not config.get("sync_error"):
            say("problem", f"no scheduled job named {JOB_NAME}; run reinstall {' '.join(flags(harness, profile))} --sync {wanted}")
        return
    for job in jobs:
        ran = f"last ran {job['last_run']}" + (f" ({job['status']})" if job["status"] else "") if job["last_run"] else "has not run yet"
        say("ok" if job["enabled"] else "warn",
            f"sync job {JOB_NAME} ({job['schedule'] or wanted}) is " + ("scheduled" if job["enabled"] else "paused") + f"; {ran}")
        if job["status"] and job["status"].lower() in ("error", "failed", "fail", "timeout", "timed-out"):
            error = job.get("error") or ""
            if config.get("token"):
                error = error.replace(config["token"], "[redacted]")
            error = " ".join(redact(error).split())[:300]
            say("problem", f"the last sync run ended in {job['status']}" + (f": {error}" if error else "")
                           + f"; see the job's output in {harness_name(harness)}")
    age = time.time() - float(config.get("updated_at") or time.time())
    if age > UPDATE_EVERY_SECONDS:
        say("warn", f"the connector was last updated {int(age // 86400)} days ago; the sync skill updates it weekly, "
                    "or run update")


def openclaw_model_check(profile, directory):
    """(level, text) from `openclaw models status --json --check`, OpenClaw's own readiness check of the profile's
    model sign-in. It reads the profile's config and auth store and calls no model (no `--probe`), so it spends no
    tokens. ('', why) when this OpenClaw gives no readable answer (a release without `--check`, say)."""
    command = "openclaw" + ("" if profile in ("", "default") else f" --profile {profile}")
    try:
        done = subprocess.run(tool_command("openclaw", profile) + ["models", "status", "--json", "--check"],
                              capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, OPENCLAW_STATE_DIR=str(directory)))
    except (Failure, OSError, subprocess.SubprocessError) as exc:
        return "", str(exc)
    data = json_in(done.stdout or "")
    auth = data.get("auth") if isinstance(data, dict) else None
    if done.returncode not in (0, 1, 2) or not isinstance(auth, dict):
        return "", f"`{command} models status --json --check` gave no answer doctor can read"
    model = str(data.get("resolvedDefault") or data.get("defaultModel") or "the default model")[:200]
    missing = [str(p)[:100] for p in auth.get("missingProvidersInUse") or [] if p]
    if missing:
        return "problem", (f"the model {model} has no sign-in (\"Missing auth\" for {', '.join(missing)}), so every sync "
                           f"run fails at its first model call: run `{command} models auth login --provider {missing[0]}` "
                           f"or `{command} configure`, then `{command} models status` (if the key is only in the Gateway "
                           "service's environment, this check can't see it)")
    if done.returncode == 1:
        return "problem", (f"the model {model} is not ready (OpenClaw's check says missing, expired or unavailable "
                           f"sign-in), so sync runs fail: see `{command} models status`")
    if done.returncode == 2:
        return "warn", f"the model {model}'s sign-in expires soon: see `{command} models status`"
    return "ok", f"the model {model} has a usable sign-in (`models status --check`; no model was called)"


def cmd_doctor(args):
    problems = []

    def say(level, text):
        if level == "problem":
            problems.append(text)
        print(f"  {level.upper() if level != 'ok' else 'ok':<7} {text}")

    harness = args.harness
    print(f"{harness_name(harness)} profile {args.profile!r}, connector {VERSION}")
    path = config_path(args.key)
    config = {}
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        config = json.loads(path.read_text())
        if mode & 0o077:
            say("problem", f"credential file {path} is mode {mode:03o}; run chmod 600 on it")
        else:
            say("ok", f"credential file {path} exists, mode 600")
    except (OSError, ValueError) as exc:
        say("problem", f"no usable credential file at {path} ({exc.__class__.__name__}); run pair")
    directory = Path(config.get("profile_dir") or agent_dir(harness, args.profile))
    if not directory.is_dir():
        say("problem", f"profile directory {directory} does not exist")
        return 1

    env_name = ".env" if harness == "hermes" else ENV_FILE
    if harness == "hermes":
        found = mcp_url(directory)
        expected = config.get("url", "").rstrip("/") + "/api/v2/mcp" if config.get("url") else ""
        if not found:
            say("problem", f"config.yaml has no mcp_servers.{MCP_SERVER_NAME} entry; run update")
        elif expected and found != expected:
            say("problem", f"mcp_servers.{MCP_SERVER_NAME} points at {found}, the credential file says {expected}; run update")
        else:
            say("ok", f"mcp_servers.{MCP_SERVER_NAME} is in config.yaml ({found})")
    else:
        say("ok", "no MCP entry in OpenClaw: the tico-sync skill calls Tico through this connector")

    token = env_value(directory, ENV_TOKEN, env_name)
    if not token:
        say("problem", f"{ENV_TOKEN} is missing from {directory / env_name}; run update")
    elif config.get("token") and token != config["token"]:
        say("problem", f"{ENV_TOKEN} in {env_name} is not the credential file's token; run update")
    else:
        say("ok", f"{ENV_TOKEN} is present in {env_name}")

    manual = config.get("heartbeat_mode") == "manual" or ("heartbeat_mode" not in config and not config.get("timer"))
    if manual:
        command = shlex.join([config.get("python") or sys.executable, str(installed_copy())])
        profile_flags = shlex.join(flags(harness, args.profile))
        refused = f" (the timer was refused: {config['timer_error']})" if config.get("timer_error") else ""
        say("warn", f"manual heartbeat mode{refused}: run `{command} heartbeat {profile_flags}` every minute; "
                    f"to install a timer, run `{command} reinstall {profile_flags} --timer`")
    else:
        loaded, note = timer_loaded(args.key)
        say("ok" if loaded else "problem", f"heartbeat timer ({note}) is " + ("loaded" if loaded else "not loaded; run update"))

    reply = config.get("last_reply")
    last_ok = config.get("last_ok")
    if not reply:
        say("problem", "no heartbeat has been answered yet")
    else:
        when = reply.get("server_time", "?")
        age = f", {int(time.time() - last_ok)} s ago" if last_ok else ""
        waiting = reply.get("waiting", {})
        say("ok", f"last heartbeat reply {when}{age}: {waiting.get('messages', 0)} message(s), "
                  f"{waiting.get('tasks', 0)} task(s) waiting")
        if last_ok and time.time() - last_ok > 180:
            say("warn", "the last good heartbeat is more than three minutes old; the bot shows offline")
    hold = config.get("backoff")
    if hold:
        say("problem", hold.get("message", "heartbeats are paused") + " (retrying once an hour)")

    if config.get("url") and config.get("token"):
        try:
            me = request(config["url"], config["token"], "GET", "/api/v2/me")
            if me.get("actor") == "bot:" + str(config.get("bot")):
                say("ok", f"GET /api/v2/me works: {me.get('actor')} ({me.get('agent') or me.get('role')})")
            else:
                say("problem", f"GET /api/v2/me answers {me.get('actor')!r}, not bot:{config.get('bot')}")
        except Failure as exc:
            message = blocked_message(exc, config.get("slug") or config.get("bot", args.profile)) or str(exc)
            say("problem", "GET /api/v2/me failed: " + message)

    doctor_sync(say, harness, args.profile, directory, config)
    if harness == "hermes":
        doctor_gateway(say, args.profile, directory, config)
    # The model: checked only through the harness's own offline status call, never by calling the model.
    level, text = openclaw_model_check(args.profile, directory) if harness == "openclaw" else ("", "")
    model_checked = bool(level)
    if model_checked:
        say(level, text)
    elif harness == "openclaw":
        say("note", f"doctor did not check the model ({text}); "
                    f"`openclaw{'' if args.profile in ('', 'default') else ' --profile ' + args.profile} models status` "
                    "must show no \"Missing auth\"")
    else:
        say("note", "doctor does not check the model: Hermes has no offline check for it. The sync job's last run "
                    "(above) shows whether it worked")

    old = scan_old_tool_names(directory)
    if old:
        say("warn", f"{len(old)} use(s) of tool names renamed in Tico 0.2.21 (not edited; change them by hand):")
        for file, number, word, new in old[:MAX_SHOWN]:
            print(f"            {file}:{number}: {word} -> {new}")
        if len(old) > MAX_SHOWN:
            print(f"            ... and {len(old) - MAX_SHOWN} more")
    else:
        say("ok", "SOUL.md, skills, cron and memories use no old tool names")
    if problems:
        print(f"{len(problems)} problem(s) found.")
    elif model_checked:
        print("Everything needed is working.")
    else:
        print("Everything doctor checks is working; it did not check the model (see NOTE above).")
    return 1 if problems else 0


def cmd_uninstall(args):
    try:
        config = load_config(args.key)
    except Failure:
        config = {}
    directory = Path(config.get("profile_dir") or agent_dir(args.harness, args.profile))
    remove_timer(args.key)
    remove_old_timers(args.key)
    problems = []
    if directory.is_dir():
        problems = remove_sync(args.harness, args.profile, directory)
        if args.harness == "hermes":
            remove_env(directory, [ENV_TOKEN, ENV_URL])
            remove_mcp(directory)
        else:
            remove_env(directory, [ENV_TOKEN, ENV_URL], ENV_FILE)
    config_path(args.key).unlink(missing_ok=True)
    for problem in problems:
        print("warning: " + problem, file=sys.stderr)
    print(f"Removed Tico from {harness_name(args.harness)} profile {args.profile!r}"
          + (": the timer, the sync job and skill, the credential." if directory.is_dir() else ".")
          + " Revoke its credential in Settings → Bots too.")
    return 0


def common(s, required_profile=False):
    s.add_argument("--harness", choices=HARNESSES, default="hermes",
                   help="the agent this profile belongs to (default hermes)")
    s.add_argument("--profile", help="the profile name: Hermes `default` is ~/.hermes itself; for OpenClaw it is optional "
                                     "and maps to `openclaw --profile` (default: ~/.openclaw)")


def sync_option(s, default):
    s.add_argument("--sync", default=default, metavar="INTERVAL",
                   help="how often the agent looks at Tico: 15m, 1h, daily, a cron expression in quotes, or off"
                        + (f" (default {default})" if default else " (default: keep the saved one)"))


def parser():
    p = argparse.ArgumentParser(description="Register a Hermes or OpenClaw profile with Tico, keep its heartbeat "
                                            "and its scheduled tico-sync job.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("install", help="wire the profile to Tico, start the heartbeat timer and the sync job")
    common(s)
    s.add_argument("--url", required=True, help="Tico's origin, for example https://tico.example.com")
    s.add_argument("--bot", required=True, help="the bot (its slug or id) this profile is")
    s.add_argument("--token", required=True, help="the agent credential from Settings → Bots")
    s.add_argument("--profile-dir", help="the profile directory when it is not under ~/.hermes (or ~/.openclaw)")
    s.add_argument("--no-timer", action="store_true", help="do not install a launchd/systemd timer")
    sync_option(s, SYNC_DEFAULT)
    s.set_defaults(fn=cmd_install)
    s = sub.add_parser("pair", help="connect the profile to a bot with a code, no token to copy")
    common(s)
    s.add_argument("--url", required=True, help="the address Tico shows, for example https://tico.example.com")
    s.add_argument("--profile-dir", help="the profile directory when it is not under ~/.hermes (or ~/.openclaw)")
    s.add_argument("--no-timer", action="store_true", help="do not install a launchd/systemd timer")
    sync_option(s, SYNC_DEFAULT)
    s.set_defaults(fn=cmd_pair)
    s = sub.add_parser("reinstall", help="run the install steps again with the saved credential; --sync changes the interval")
    common(s)
    sync_option(s, None)
    timer = s.add_mutually_exclusive_group()
    timer.add_argument("--timer", action="store_true", help="install the heartbeat timer")
    timer.add_argument("--no-timer", action="store_true",
                       help="no launchd/systemd timer (removes one installed earlier): run `heartbeat` yourself")
    s.set_defaults(fn=cmd_reinstall)
    s = sub.add_parser("check", help="what the sync job asks first: what is waiting, and whether the weekly update is due")
    common(s)
    s.add_argument("--gate", action="store_true", help="end with {\"wakeAgent\": false} when nothing is waiting (Hermes cron)")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("call", help="call one Tico tool over Tico's MCP endpoint (for an agent with no MCP client)")
    common(s)
    s.add_argument("tool", help="the tool's name, for example hub_message_list")
    s.add_argument("arguments", nargs="?", default="{}", help="the tool's arguments as one JSON object")
    s.set_defaults(fn=cmd_call)
    for name, fn, help_text in (("heartbeat", cmd_heartbeat, "post one heartbeat (what the timer runs)"),
                                ("status", cmd_status, "the saved configuration and the last reply"),
                                ("update", cmd_update, "download the newest connector from Tico and install again"),
                                ("doctor", cmd_doctor, "check the wiring, the sync job and old tool names (changes nothing)"),
                                ("uninstall", cmd_uninstall, "remove the timer, the sync job and skill, the env line and the MCP entry")):
        s = sub.add_parser(name, help=help_text)
        common(s)
        s.set_defaults(fn=fn)
    return p


def settle(args):
    """Which agent and profile a command is about, and the name its saved files go by."""
    args.harness = getattr(args, "harness", None) or "hermes"
    if not args.profile:
        if args.harness == "hermes":
            raise Failure("--profile is required for a Hermes profile (`default` is ~/.hermes itself)")
        args.profile = "default"
    args.key = ident(args.harness, args.profile)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        settle(args)
        return args.fn(args)
    except Failure as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
