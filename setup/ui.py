from __future__ import annotations

import getpass
import sys
import webbrowser


# Kept the same as backend/census.py NOTICE (a test compares them): the installer bundle carries no backend.
USAGE_NOTICE = ("Tico counts active installs anonymously (a random ID, version, and three yes/no activity flags). "
                "Turn off: Settings > Privacy or TICO_TELEMETRY=off.")


class MissingInput(SystemExit):
    pass


class IO:
    """Terminal I/O. `interactive=False` never prompts: a missing value is an error naming the flag."""

    def __init__(self, interactive: bool = True):
        self.interactive = interactive

    def say(self, msg: str = "") -> None:
        print(msg, flush=True)

    def ask(self, question: str, default: str = "", secret: bool = False) -> str:
        suffix = f" [{default}]" if default and not secret else ""
        prompt = f"{question}{suffix}: "
        v = getpass.getpass(prompt) if secret else input(prompt)
        return v.strip() or default

    def confirm(self, question: str, default: bool = True) -> bool:
        if not self.interactive:
            return default
        v = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not v else v.startswith("y")

    def pause(self, message: str) -> None:
        if self.interactive:
            input(message)

    def open_url(self, url: str) -> None:
        if self.interactive:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        self.say(f"  {url}")


def stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
