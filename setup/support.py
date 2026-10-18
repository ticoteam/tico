"""When setup fails, offer to send the failure to Tico support (docs/support.md).

Only a person at a terminal is asked, and nothing is sent without their "y". What is sent is a plain summary: the failing
checks or the installer's error, this computer's OS and CPU, and the Tico version, with the same redaction rules as the
app's diagnostics (backend/redaction.py), plus the domain being installed, the team's name and the run's own secrets.
The request goes to HQ's `POST /v1/support` as a `setup` ticket; replies show on HQ's page for it, whose link carries
the ticket's secret in the fragment so it never reaches a server log.

`TICO_SUPPORT=off` (as on the server) or `--no-support` turns the offer off.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import state
from .ui import IO

# Kept the same as backend/census.py DEFAULT_HQ (a test compares them): the installer bundle carries no backend.
DEFAULT_HQ = "https://updates.tico.team"
QUESTION = "Send this to Tico support?"
OFF = ("off", "0", "false", "no", "disabled")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,31})?")    # the version HQ accepts
EMAIL = re.compile(r"[^@\s<>\",;()\[\]\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,24}")
TICKET = re.compile(r"TK-[A-Z2-7]{8}")
SECRET = re.compile(r"[A-Za-z0-9_-]{20,64}")
MAX_MESSAGE = 3900                # HQ takes 4000 characters
STEPS = {3: "this computer does not qualify", 4: "download or checksum failed", 5: "Docker or Python setup failed",
         6: "the wizard or the health check failed"}
ROOT = Path(__file__).resolve().parent.parent


def off(args=None) -> bool:
    return bool(getattr(args, "no_support", False)) or os.environ.get("TICO_SUPPORT", "").strip().lower() in OFF


def hq_url() -> str:
    return os.environ.get("TICO_HQ_URL", "").strip().rstrip("/") or DEFAULT_HQ


def _tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def redaction():
    """backend/redaction.py, loaded by its path: the bundle ships that one file beside setup/ and no backend package."""
    path = ROOT / "backend" / "redaction.py"
    spec = importlib.util.spec_from_file_location("tico_setup_redaction", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise ImportError("backend/redaction.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle_version(given: str = "") -> str:
    """The release this setup came from: the bundle's VERSION file, else --tico-version. "" when neither is a version."""
    try:
        found = (ROOT / "VERSION").read_text().strip()
    except OSError:
        found = ""
    for value in (found, (given or "").strip().lstrip("v")):
        if VERSION.fullmatch(value):
            return value
    return ""


def domains_of(*values: str) -> list[str]:
    """The domains a run names: a domain itself, or the part after @ in an email or user@host."""
    out = []
    for value in values:
        value = str(value or "").strip().lower().rsplit("@", 1)[-1].strip(".")
        if "." in value and not re.fullmatch(r"[\d.]+", value) and value not in out:
            out.append(value)
    return out


# A home folder names the account (/home/ana/tico), and so does an ssh login (ana@203.0.113.7); the address itself goes
# with the diagnostics rules.
HOME = re.compile(r"((?:/home|/Users|[A-Za-z]:\\Users)[/\\])(?!\[user\])[^/\\\s\"'<>]+")
SSH_USER = re.compile(r"(?<![A-Za-z0-9._%+'-])[A-Za-z_][A-Za-z0-9_.-]{0,31}@(?=\d{1,3}(?:\.\d{1,3}){3}(?![\w.-]*[A-Za-z])|\[|[0-9a-fA-F]*:)")


def scrub(text: str, *, domains=(), secrets=(), names=()) -> str:
    """The text as it may leave: the run's own secrets and names first, then the diagnostics rules with its domains."""
    text = str(text or "")
    for secret in sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True):
        text = text.replace(secret, "[secret]")
    for name in sorted({n.strip() for n in names if n and len(n.strip()) >= 3}, key=len, reverse=True):
        text = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), "[team]", text, flags=re.I)
    text = HOME.sub(lambda m: m.group(1) + "[user]", text)
    text = SSH_USER.sub("[user]@", text)
    return redaction().Redactor(company_domains=domains).text(text)


def summary(what: str, checks=(), error: str = "", version: str = "") -> str:
    """The message before redaction: what failed, each failing check with its hint, the system and the version."""
    lines = [f"Setup failed: {what}"]
    if error:
        lines.append(f"error: {error}")
    for c in checks:
        if not c.ok:
            lines.append(f"[FAIL] {c.name}: {c.detail}")
            if c.hint:
                lines.append(f"  fix: {c.hint}")
    lines.append(f"System: {platform.system()} {platform.machine()}, Python {platform.python_version()}")
    lines.append(f"Tico: {version or 'unknown'}")
    return "\n".join(lines)


def _post(url: str, body: dict, timeout: float = 20):
    """(status, decoded JSON or {}). Redirects are not followed: the request goes to HQ or nowhere."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "tico-setup"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as r:
            return r.status, json.loads(r.read(65536).decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(65536).decode() or "{}")
        except ValueError:
            return e.code, {}


def send(message: str, email: str, version: str, hq: str, post=None) -> tuple[str, str]:
    """File the ticket. Returns (ticket_id, secret); raises OSError or ValueError with a plain reason."""
    body = {"message": message, "kind": "setup", **({"email": email} if email else {}), **({"version": version} if version else {})}
    post = post or _post
    status, answer = post(hq + "/v1/support", body)
    if status == 422 and isinstance(answer, dict) and answer.get("field") == "fields":
        # An HQ from before the kind field: the kind goes in the text instead.
        body = {**{k: v for k, v in body.items() if k != "kind"}, "message": "Kind: setup\n" + message}
        status, answer = post(hq + "/v1/support", body)
    if status == 429:
        raise ValueError("Tico support is taking no more requests from this address for now")
    if status != 201 or not isinstance(answer, dict):
        raise ValueError(f"Tico support answered {status}")
    ticket, secret = str(answer.get("ticket_id") or ""), str(answer.get("secret") or "")
    if not TICKET.fullmatch(ticket) or not SECRET.fullmatch(secret):
        raise ValueError("Tico support's answer was not a ticket")
    return ticket, secret


def offer(io: IO, what: str, checks=(), error: str = "", *, domains=(), secrets=(), names=(), version: str = "",
          args=None, post=None) -> bool:
    """Ask, and send on "y". Never raises and never changes how the run ends; returns whether a ticket was filed."""
    if off(args) or not io.interactive or not _tty():
        return False
    marker = os.environ.get("TICO_SUPPORT_ASKED", "")
    if marker:
        # The installer asks after a failed wizard only when the wizard did not.
        try:
            Path(marker).touch()
        except OSError:
            pass
    try:
        hq = hq_url()
        if not hq.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            return False
        message = scrub(summary(what, checks, error, version), domains=domains, secrets=secrets, names=names)[:MAX_MESSAGE]
        io.say("\nTico support can look at this for you. This is exactly what would be sent (keys, emails, addresses, "
               "account names and your domain removed):\n")
        io.say("\n".join("    " + line for line in message.splitlines()))
        if not io.confirm(QUESTION, False):
            return False
        email = io.ask("Email for the reply (optional; Enter to skip)").strip()
        if email and (len(email) > 254 or not EMAIL.fullmatch(email)):
            io.say("That is not an email address; sending without one.")
            email = ""
        ticket, secret = send(message, email, version, hq, post)
    except (EOFError, KeyboardInterrupt):
        io.say("")
        return False
    except ImportError:
        io.say("Not sent: this setup has no redaction rules (backend/redaction.py), so nothing leaves the computer.")
        return False
    except (OSError, ValueError) as e:
        reason = str(e) if isinstance(e, ValueError) else f"could not reach {hq_url()} ({type(e).__name__})"
        io.say(f"Not sent: {reason}.")
        return False
    io.say(f"Sent as {ticket}. Replies show up at {hq}/support/{ticket}#s={secret}")
    return True


def from_installer(args, io: IO) -> int:
    """`python3 -m setup support --step N --error TEXT`: scripts/install.sh's offer after it failed with exit code N.
    Always returns 0; the installer keeps its own exit code."""
    step = args.step if args.step in STEPS else 0
    what = f"install.sh (exit {step}: {STEPS[step]})" if step else "install.sh"
    hosts, names = [args.domain or ""], []
    # The wizard's saved answers name the domain being installed; they are readable when this runs as the same user.
    for domain in state.known_domains():
        saved, _ = state.load(domain)
        hosts += [domain, saved.get("owner_email", ""), saved.get("allowed_domain", ""), saved.get("runner_host", "")]
        names.append(saved.get("company", ""))
    offer(io, what, error=args.error or "", domains=domains_of(*hosts), names=names,
          version=bundle_version(args.tico_version or ""), args=args)
    return 0
