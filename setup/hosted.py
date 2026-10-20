"""A server Tico hosts for a company: our operator's flags only, never a question.

`--hosted own|invited` marks the install (TICO_HOSTED) and signs the owner in with a single-use link (TICO_AUTH_PROXY=
owner-link, backend/owner_link.py). With `--auth oidc` (a Tico account issuer, say) people sign in there instead, and
the link stays as the owner's break-glass for when the issuer is down. The link's secret is made here, on our operator's
computer: it goes only into a 0600 file for our team, and the server gets its sha256. `--preset recommended` writes the same choices as
`scripts/install.sh --recommended` and adds the server's own computer, the Docker runner on the server's network.
`python3 -m setup owner-link --domain D [--sign-out]` makes a new link and puts it in place of the old one.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shlex

from . import contract, envfile, remote, settings as st, state
from .settings import Settings
from .ui import IO, MissingInput

SERVER_COMPUTER = "Server computer"
SERVER_NETWORK = "tico_default"   # compose.yaml in /opt/tico: the project is tico
# Inside the server container `docker compose exec` has no entrypoint, so the offline commands name their paths.
_EXEC_ENV = f"-e TICO_REGISTRY_DIR={contract.DATA_DIR}/registry"
# Docker as the shell's user, else through sudo without a password (the SSH user), the same as the checks.
_DOCKER = 'if docker info >/dev/null 2>&1; then dk=docker; else dk="sudo -n docker"; fi'


def gather(args, s: Settings, dry: bool) -> None:
    """Our operator's flags, checked before anything is asked or created. A self-hosted install leaves all of it unset."""
    hosted = (getattr(args, "hosted", None) or s.hosted or "").strip().lower()
    preset = (getattr(args, "preset", None) or s.preset or "").strip().lower()
    auth = getattr(args, "auth", None) or ""
    if not auth and hosted and s.hosted and s.auth == "oidc":
        auth = "oidc"                   # a re-run keeps the OIDC sign-in the first run chose
    if hosted and hosted not in st.HOSTED:
        raise MissingInput("--hosted is own or invited")
    if preset and preset not in st.PRESETS:
        raise MissingInput("--preset is recommended")
    if auth == st.OWNER_LINK and not hosted:
        raise MissingInput("--auth owner-link is for a server Tico hosts: pass --hosted own or --hosted invited")
    if hosted and auth and auth not in (st.OWNER_LINK, "oidc"):
        raise MissingInput("--hosted signs people in with the owner sign-in link, or with --auth oidc (the link then stays "
                           "as break-glass): leave out --auth, or pass --auth owner-link or --auth oidc")
    if preset and not hosted:
        raise MissingInput("--preset recommended is for a server Tico hosts (--hosted); on your own server run "
                           "scripts/install.sh --recommended")
    s.hosted, s.preset = hosted, preset
    if not hosted:
        return
    s.auth = "oidc" if auth == "oidc" else st.OWNER_LINK
    zone = (getattr(args, "hosted_zone", None) or s.hosted_zone or "").strip().lower().strip(".")
    if not zone or not st.DOMAIN_RE.match(zone):
        raise MissingInput("--hosted needs --hosted-zone: the DNS zone of ours that the domain is under")
    if not s.domain.startswith("<") and not s.domain.endswith("." + zone):
        raise MissingInput(f"--domain must be a name under {zone}, the zone given with --hosted-zone")
    s.hosted_zone = zone
    if preset:
        s.support = "off" if getattr(args, "no_support", False) else "on"
        # Only from the environment: setup's saved copy of the .env never holds it (contract.SERVER_ONLY_KEYS).
        invite = os.environ.get("TICO_GATEWAY_INVITE", "")
        if invite and not st.INVITE_RE.fullmatch(invite):
            raise MissingInput("TICO_GATEWAY_INVITE is not an invite code (8 to 200 letters, digits, - and _). It is not shown here.")
        s.gateway_invite = invite


def link_file(domain: str):
    return state.home() / domain / "owner-link.txt"


def new_link(s: Settings) -> str:
    """A new link in its 0600 file; returns the hash the server is given. Nothing here prints it."""
    token = secrets.token_urlsafe(32)
    state.write_private(link_file(s.domain), f"https://{s.domain}{contract.OWNER_LINK_PATH}#{token}\n")
    return hashlib.sha256(token.encode()).hexdigest()


def plan_lines(s: Settings) -> list[str]:
    if s.auth == st.OWNER_LINK:
        out = [f"Sign-in: the owner's single-use sign-in link (a week at most), written to {link_file(s.domain)} (0600) for "
               f"our team to send; never printed. Hosted install: {s.hosted}, under {s.hosted_zone}"]
    else:
        out = [f"Break-glass: the owner's single-use sign-in link (a week at most), for when the issuer is down, written to "
               f"{link_file(s.domain)} (0600) for our team only; never printed, and not for the ready email. Hosted install: "
               f"{s.hosted}, under {s.hosted_zone}"]
    if s.preset:
        out.append("Recommended setup: the sample company, your Claude and ChatGPT subscriptions plus Tico Gateway, "
                   f"Tico Support {s.support or 'on'}, and a computer on the server (the Docker runner). "
                   + ("The Gateway invite is in the environment; it connects on the first start."
                      if s.gateway_invite else "No Gateway invite in the environment (TICO_GATEWAY_INVITE): the Gateway is not set up by this run."))
    return out


def replace_command(token_hash: str, sign_out: bool) -> str:
    return (f"cd {contract.REMOTE_DIR} && {_DOCKER}; $dk compose exec -T {_EXEC_ENV} {contract.SERVICE_SERVER} "
            f"python -m backend.owner_link replace {contract.DATABASE} {token_hash}" + (" --sign-out" if sign_out else ""))


def server_computer_script(s: Settings) -> str:
    """The server's own computer: a code from the server (written to a private file in its volume and read back, never
    printed), then install.sh --runner on the server's network, as scripts/install.sh --recommended does."""
    from .runner_setup import installer_url
    q = shlex.quote
    tag = s.tag if s.tag not in ("", "latest") else "latest"
    server = f"$dk compose exec -T {_EXEC_ENV} {contract.SERVICE_SERVER}"
    return "\n".join([
        "set -eu",
        f"cd {contract.REMOTE_DIR}",
        _DOCKER,
        f"if $dk ps -a --format '{{{{.Names}}}}' | grep -qx {contract.RUNNER_CONTAINER}; then echo 'server computer already there'; exit 0; fi",
        f"code_file={contract.DATA_DIR}/enrollment-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \\n')",
        f'trap \'{server} rm -f "$code_file" >/dev/null 2>&1 || true\' EXIT',
        f'{server} python -m backend.manage enrollment {contract.DATABASE} --owner {q(s.owner_email)} --out "$code_file" >/dev/null',
        f"TICO_JOIN_CODE=$({server} python -c 'import json, sys; print(json.load(open(sys.argv[1]))[\"code\"])' \"$code_file\")",
        "export TICO_JOIN_CODE",
        remote.fetch_and_run(installer_url(tag), f"--runner --dir {contract.RUNNER_DIR} --url http://{contract.SERVICE_SERVER}:"
                             f"{contract.SERVER_PORT} --code \"$TICO_JOIN_CODE\" --label {q(SERVER_COMPUTER)} "
                             f"--server-network {SERVER_NETWORK}"),
    ]) + "\n"


def add_server_computer(io: IO, s: Settings, shell: remote.Shell | None) -> bool:
    """The preset's computer. A failure leaves the server running and says how to add it by hand."""
    later = ("Add it later: Settings > Computers > Add computer, then the --runner command it shows, run on the server "
             "with --server-network tico_default.")
    io.say("\nServer computer")
    if shell is None:
        io.say("  This target gives setup no shell on the server, so the computer is not added. " + later)
        return False
    res = shell.run(server_computer_script(s))
    if res.returncode:
        io.say("  The server computer did not start: " + envfile.scrub((res.stderr or res.stdout)[-300:], s.secrets()).strip())
        io.say("  " + later)
        return False
    io.say(f"  {SERVER_COMPUTER} added: the Docker runner on the server, shown in Settings > Computers in a minute.")
    return True


def shell_for(s: Settings, deps) -> remote.Shell | None:
    if s.target == "local":
        return deps.local_shell()
    if s.target == "ssh":
        return deps.ssh_shell(s.ssh_host, s.ssh_port, s.ssh_identity)
    if s.target == "aws" and s.aws_instance_id:
        _, _, ssm, _ = deps.aws_clients(s.aws_region, s.aws_profile)
        return remote.SSMShell(ssm, s.aws_instance_id, deps.sleep)
    return None


def rotate(args, io: IO, deps) -> int:
    """`python3 -m setup owner-link`: a new link in place of the old one, and with --sign-out every owner session ends."""
    domain = (args.domain or "").lower()
    if not domain:
        known = state.known_domains()
        if len(known) != 1:
            raise MissingInput("Pass --domain" + (f" (known: {', '.join(known)})" if known else ""))
        domain = known[0]
    saved, env_text = state.load(domain)
    if not saved:
        raise MissingInput(f"No saved setup for {domain}; run `tico setup` first.")
    s = st.from_saved(saved, env_text)
    if s.auth != st.OWNER_LINK and not (s.hosted and s.auth == "oidc"):
        raise MissingInput(f"{domain} does not use the owner sign-in link (it was set up with --auth {s.auth}).")
    what = "a new owner sign-in link" + (" and sign the owner out everywhere" if args.sign_out else "")
    if args.dry_run:
        io.say(f"Dry run: would make {what} for {domain}, write it to {link_file(domain)} (0600) and give the server its hash.")
        return 0
    shell = shell_for(s, deps)
    if shell is None:
        raise MissingInput(f"Setup has no shell on the {s.target} server for {domain}; run the command there by hand "
                           "(`python -m backend.owner_link replace` in the server container).")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    res = shell.run(replace_command(token_hash, args.sign_out))
    if res.returncode:
        io.say("The server did not take the new link; the old one still works. " + envfile.scrub((res.stderr or res.stdout)[-300:], s.secrets()).strip())
        return 1
    state.write_private(link_file(domain), f"https://{domain}{contract.OWNER_LINK_PATH}#{token}\n")
    s.owner_link_hash = token_hash
    state.save(domain, s.public(), envfile.render(s.to_env()))
    io.say(f"A new owner sign-in link is in {link_file(domain)} (0600): send it to {s.owner_email}. It works once, for a week. "
           "The old unused link no longer works" + (", and the owner is signed out everywhere." if args.sign_out else "."))
    return 0
