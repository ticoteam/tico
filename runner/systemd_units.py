"""systemd user units for a Linux runner that runs from a git checkout (docs/install.md, docs/updates.md).

`scripts/tico install` gives a Mac launchd jobs with KeepAlive; on Linux it writes these units instead, under
`~/.config/systemd/user`, each with `Restart=always`. Nothing here needs root. The unit carries two variables the
runner reads: `TICO_SUPERVISED=1` (something starts it again, so it may exit to update) and `TICO_SYSTEMD_UNIT`
(its own unit name), which `runner/release_update.py` uses to restart the runner and the helper jobs through
`systemctl --user restart`.

Every function is pure or takes the command runner as an argument, so the tests never start systemd.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

KINDS = ("bot", "connectors", "close-calls", "importers")
COMMANDS = {"bot": "run", "connectors": "connectors", "close-calls": "close-calls", "importers": "importers"}
DESCRIPTIONS = {"bot": "Tico runner", "connectors": "Tico connectors", "close-calls": "Tico close-calls",
                "importers": "Tico importers", "api": "Tico server"}


def unit_name(kind, env_slug=""):
    """`tico-bot.service`, or `tico-<environment>-bot.service` for a company environment (scripts/tico `label`)."""
    return f"tico-{env_slug}-{kind}.service" if env_slug else f"tico-{kind}.service"


def helper_units(unit, kinds=KINDS):
    """The units beside the runner's own, from its name: `tico-bot.service` -> `tico-connectors.service`, ..."""
    if not (unit.startswith("tico-") and unit.endswith("bot.service")):
        return {}
    stem = unit[:-len("bot.service")]
    return {kind: f"{stem}{kind}.service" for kind in kinds if kind != "bot"}


def user_unit_dir(env=os.environ):
    return Path(env.get("TICO_SYSTEMD_USER_DIR") or Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user")


def quote(value):
    """One word of an ExecStart= or Environment= line: systemd splits on spaces, and treats `%` and `$` itself."""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return '"' + text + '"'


def render(kind, *, python, config, root, log, path, env_slug="", home="", user="", env_file="", port="",
           throttle=10):
    """The text of one unit. `kind` is bot, connectors, close-calls, importers, or api (an environment's local server,
    which reads its settings from `env_file`)."""
    if kind == "api":
        exec_line = " ".join(quote(x) for x in (python, "-m", "backend.server", "--host",
                                               "127.0.0.1", "--port", port))
    elif kind in COMMANDS:
        exec_line = " ".join(quote(x) for x in (python, "-m", "runner", "--config", config, COMMANDS[kind]))
    else:
        raise ValueError(f"unknown job {kind!r}")
    lines = ["[Unit]", f"Description={DESCRIPTIONS[kind]}" + (f" ({env_slug})" if env_slug else ""),
             "StartLimitIntervalSec=0", "",
             "[Service]", "Type=simple", f"WorkingDirectory={root}"]
    if env_file:
        lines.append(f"EnvironmentFile={env_file}")
    lines += [f"ExecStart={exec_line}", "Restart=always", f"RestartSec={throttle}",
              "Environment=" + quote("TICO_SUPERVISED=1"),
              "Environment=" + quote("TICO_SYSTEMD_UNIT=" + unit_name(kind, env_slug)),
              "Environment=" + quote("PATH=" + path)]
    if home:
        lines.append("Environment=" + quote("HOME=" + home))
    if user:
        lines += ["Environment=" + quote("USER=" + user), "Environment=" + quote("LOGNAME=" + user)]
    lines += [f"StandardOutput=append:{log}", f"StandardError=append:{log}", "",
              "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def write(unit_dir, kind, text, env_slug=""):
    """Write the unit (mode 0600: it names the runner's config and the PATH) and return its path."""
    directory = Path(unit_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / unit_name(kind, env_slug)
    path.write_text(text)
    path.chmod(0o600)
    return path


def linger_guidance(user, run=subprocess.run):
    """What to tell the person when their user manager stops at logout, or "" when lingering is on (or cannot be told)."""
    try:
        done = run(["loginctl", "show-user", user, "--property=Linger", "--value"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    answer = done.stdout.strip().lower()
    if done.returncode or answer == "yes":
        return ""
    return (f"Linger is off for {user}: these jobs stop when the last session logs out and do not start at boot. "
            f"Run once: loginctl enable-linger {user}   (add sudo if it asks)")


def restart(unit, run=subprocess.run, timeout=180):
    """`systemctl --user restart <unit>`. Returns an error text, or "" when it restarted."""
    try:
        done = run(["systemctl", "--user", "restart", unit], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"systemctl could not run: {type(exc).__name__}"
    if done.returncode:
        lines = (done.stderr or done.stdout or "").strip().splitlines()
        return lines[-1] if lines else f"systemctl exited {done.returncode}"
    return ""


def is_installed(unit, unit_dir=None):
    return (Path(unit_dir) if unit_dir else user_unit_dir()).joinpath(unit).is_file()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m runner.systemd_units")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="write one unit file and print its path")
    w.add_argument("kind", choices=KINDS + ("api",))
    w.add_argument("--python", required=True)
    w.add_argument("--config", default="")
    w.add_argument("--root", required=True)
    w.add_argument("--log", required=True)
    w.add_argument("--path", default=os.environ.get("PATH", ""))
    w.add_argument("--env", default="")
    w.add_argument("--env-file", default="")
    w.add_argument("--port", default="")
    w.add_argument("--dir", default="")
    n = sub.add_parser("name", help="print a unit's name")
    n.add_argument("kind", choices=KINDS + ("api",))
    n.add_argument("--env", default="")
    sub.add_parser("linger", help="print the linger guidance, or nothing when lingering is on")
    sub.add_parser("dir", help="print the user unit directory")
    args = parser.parse_args(argv)
    if args.cmd == "name":
        print(unit_name(args.kind, args.env))
    elif args.cmd == "dir":
        print(user_unit_dir())
    elif args.cmd == "linger":
        note = linger_guidance(os.environ.get("USER") or os.environ.get("LOGNAME") or "")
        if note:
            print(note)
    else:
        text = render(args.kind, python=args.python, config=args.config, root=args.root, log=args.log, path=args.path,
                      env_slug=args.env, home=os.environ.get("HOME", ""), user=os.environ.get("USER", ""),
                      env_file=args.env_file, port=args.port)
        print(write(args.dir or user_unit_dir(), args.kind, text, args.env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
