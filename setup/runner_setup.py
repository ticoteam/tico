"""`python3 -m setup runner`: put a Linux computer that runs bots onto a Tico server, with a one-time join code."""

from __future__ import annotations

import json
import os
import shlex
import urllib.error
import urllib.parse
import uuid
import urllib.request

from . import aws as awsmod, cloudinit, contract, envfile, owned, remote, settings as st, state, verify
from .remote import Host, Step, StepFailed
from .settings import Asker, Settings
from .ui import IO, MissingInput
from .wizard import Deps, host_of

TARGETS = {k: v for k, v in st.TARGETS.items() if k in ("ssh", "aws", "command")}


def join_env(url: str, code: str, label: str) -> str:
    """Sourced by sh, so values are shell-quoted; removed as soon as the container has been started."""
    q = shlex.quote
    return f"TICO_JOIN_URL={q(url)}\nTICO_JOIN_CODE={q(code)}\nTICO_JOIN_LABEL={q(label)}\n"


def installer_url(tag: str) -> str:
    """The release's own installer, so the runner comes up pinned to it with its updater sidecar."""
    if tag == "latest":
        return "https://github.com/ticoteam/tico/releases/latest/download/install.sh"
    return f"https://github.com/ticoteam/tico/releases/download/{tag}/install.sh"


def join_lines(tag: str, env_file: str, rejoin: bool = False, screen: bool = True) -> list[str]:
    """Starts the runner unless one is already there, through `install.sh --runner` (compose plus the
    updater); a bare `docker run` would never follow the server's releases. The volume keeps logins and repositories.
    The installer offers the screen by default; `screen=False` passes --no-screen. No flag on the default path, so a
    release pinned from before either flag existed still installs."""
    c = contract.RUNNER_CONTAINER
    # A new code needs a fresh .env: the installer keeps the one it finds.
    replace = f"docker rm -f {c} >/dev/null 2>&1 || true; rm -f {contract.RUNNER_DIR}/.env; " if rejoin else ""
    return [
        f"if {'false' if rejoin else f'docker ps -a --format {chr(39)}{{{{.Names}}}}{chr(39)} | grep -qx {c}'}; then echo 'runner already joined; leaving it'; else",
        # The code leaves the disk once it is in the environment, so a failed install does not leave it behind.
        f"  {replace}set -a; . {env_file}; set +a; rm -f {env_file}",
        "  "
        + remote.fetch_and_run(
            installer_url(tag),
            f"--runner --dir {contract.RUNNER_DIR} "
            '--url "$TICO_JOIN_URL" --code "$TICO_JOIN_CODE" --label "$TICO_JOIN_LABEL"'
            + ("" if screen else " --no-screen"),
        ),
        "fi",
        f"rm -f {env_file}",
    ]


def bootstrap_script(
    *,
    os_family: str,
    tag: str,
    env_b64: str = "",
    env_param: tuple[str, str] | None = None,
    rejoin: bool = False,
    screen: bool = True,
) -> str:
    d, q = contract.RUNNER_DIR, shlex.quote
    L = [
        "#!/bin/bash",
        "set -eu",
        "exec > >(tee -a /var/log/tico-setup.log) 2>&1",
        "echo tico-setup runner: start $(date -u)",
    ]
    L += remote.docker_install_lines(os_family, need_aws_cli=bool(env_param))
    L += [f"mkdir -p {d}", "umask 077"]
    if env_param:
        name, region = env_param
        L += [
            f"aws ssm get-parameter --region {q(region)} --name {q(name)} --with-decryption --query Parameter.Value --output text > {d}/join.env"
        ]
    else:
        L += [f"echo {env_b64} | base64 -d > {d}/join.env"]
    L += join_lines(tag, f"{d}/join.env", rejoin, screen) + [f"touch {d}/.bootstrapped", "echo tico-setup runner: done"]
    return "\n".join(L) + "\n"


def ssh_steps(
    sh: remote.Shell, *, url: str, code: str, label: str, tag: str, rejoin: bool, say, scrub, screen: bool = True
) -> list[Step]:
    d = contract.RUNNER_DIR
    h = Host(sh, say, scrub)

    def join() -> None:
        h.ex(f"mkdir -p {d} && chmod 700 {d}", "Creating " + d)
        h.write_private(f"{d}/join.env", join_env(url, code, label).encode(), "Writing the join code")
        script = "\n".join(["set -eu"] + join_lines(tag, f"{d}/join.env", rejoin, screen))
        h.ex(script, "Starting the runner container")

    return [
        Step("Connect over SSH and check sudo", h.connect),
        Step("Install Docker if missing", h.docker),
        Step(
            f"Start the {contract.RUNNER_CONTAINER} container with its updater (install.sh --runner, release {tag})",
            join,
        ),
    ]


def mint_code(url: str, token: str, opener=urllib.request.urlopen) -> str:
    """Asks the server for a single-use code (valid 15 minutes) with the owner's personal token."""
    req = urllib.request.Request(
        url.rstrip("/") + contract.ENROLLMENTS_PATH,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": uuid.uuid4().hex,
        },
    )
    try:
        with opener(req, timeout=15) as r:
            return json.loads(r.read())["code"]
    except urllib.error.HTTPError as e:
        with e:
            raise MissingInput(
                f"The server refused the token (HTTP {e.code}). It must be the owner's personal token."
            ) from None
    except (OSError, ValueError, KeyError) as e:
        raise MissingInput(f"Could not get a join code from {url} ({type(e).__name__})") from None


def server_tag(url: str, deps: Deps) -> str:
    """The release the server runs (its /healthz), so the runner matches it rather than GitHub's latest stable."""
    release = (deps.server_release(url) or "").strip()
    release = release if not release or release.startswith("v") else "v" + release
    if not cloudinit.VERSION_RE.match(release):
        raise MissingInput(
            f"Could not read which release {url} runs (its /healthz names none); pass --runner-tag vX.Y.Z, "
            "the server's release, or --runner-tag latest"
        )
    return release


def plan_lines(s: Settings, label: str) -> list[str]:
    L = [
        f"Add a Linux computer named {label!r} to {s.server_url}. The runner container is the only thing installed; "
        "it makes outbound connections only.",
        "",
    ]
    if s.target == "aws":
        p = _aws_plan(s, label)
        L.append("  1. Create the AWS box:")
        L += [f"       - {x}" for x in p.lines()] + [f"       ! {w}" for w in p.warnings]
    elif s.target == "ssh":
        L.append(
            f"  1. Over SSH to {s.ssh_host}: install Docker if missing, run the {s.runner_tag} release's install.sh --runner (runner plus updater)"
        )
    else:
        L.append("  1. Write one paste-able command (0600 file) that installs Docker and starts the runner")
    L.append(
        "  2. A one-time join code (15 minutes, single use) is minted or asked for right before it is used; it is never printed"
    )
    L.append("  3. Verify: container up, and the computer shows online in Tico when an owner token is available")
    return L


def _aws_plan(s: Settings, label: str) -> awsmod.AwsPlan:
    return awsmod.plan_aws(
        domain=s.server_url,
        front_door="none",
        region=s.aws_region,
        instance_type=s.aws_instance_type,
        os_family=s.aws_os,
        name=s.aws_name or f"runner-{awsmod.slug(label)}",
        role="runner",
        boundary=s.aws_permissions_boundary,
        prefix=s.asset_prefix,
        install_id=s.install_id if owned.strict(s.asset_prefix) else "",
    )


def runner_key(s: Settings, name: str) -> str:
    """The file its record is saved in: one per server, region and name, so another server's runner is never overwritten.
    The digest of the full identity (prefix, URL with port and path, region) keeps names that slug alike apart."""
    host = urllib.parse.urlsplit(s.server_url).hostname or ""
    ident = owned.server_identity(s.asset_prefix, s.server_url, s.aws_region)[:10]
    return f"{name}--{awsmod.slug(host)[:20]}--{s.aws_region}--{ident}"


def runner_prefix(s: Settings, label: str, given: str | None) -> str:
    """The runner's own saved one, else the server's it joins (set up from this computer), else --asset-prefix or tico.
    Only this server's runner counts as its own: a runner of the same name kept for another server, in any region (its
    IAM role and profile are global), is refused when the names would be the same AWS resources."""
    name = s.aws_name or f"runner-{awsmod.slug(label)}"
    runners = [state.record(r) for r in state.load_runners()]
    same = [r for r in runners if r.get("target") == "aws" and r.get("name") == name]
    mine = next(
        (r for r in same if r.get("server_url") == s.server_url and r.get("aws_region") == s.aws_region),
        next((r for r in same if r.get("server_url") == s.server_url), None),
    )
    host = urllib.parse.urlsplit(s.server_url).hostname or ""
    prefix = awsmod.asset_prefix(given, mine or (state.record(state.load(host)[0]) if host else {}))
    other = next(
        (
            r
            for r in same
            if r.get("server_url") != s.server_url and (r.get("asset_prefix") or awsmod.DEFAULT_PREFIX) == prefix
        ),
        None,
    )
    if other and owned.strict(prefix):
        raise MissingInput(
            f"{awsmod.Names(prefix).resource(name)} is already the runner of {other['server_url']} "
            f"({other.get('aws_region', '')}), "
            "set up from this computer; refusing to reuse or change it. Pick another --name or --label."
        )
    return prefix


def own_record(s: Settings, name: str) -> dict:
    """This server's saved runner of that name and region, if this computer set it up."""
    return next(
        (
            r
            for r in map(state.record, state.load_runners(s.server_url))
            if r.get("target") == "aws" and r.get("name") == name and r.get("aws_region") == s.aws_region
        ),
        {},
    )


def save_aws_runner(s: Settings, plan: awsmod.AwsPlan, label: str, iid: str) -> None:
    data = {
        "server_url": s.server_url,
        "label": label,
        "target": "aws",
        "aws_region": s.aws_region,
        "aws_profile": s.aws_profile,
        "instance_id": iid,
        "name": plan.name,
        "asset_prefix": s.asset_prefix,
        "aws_account_id": s.aws_account_id,
        "install_id": s.install_id,
        "aws_ids": s.aws_ids,
    }
    state.save_runner(runner_key(s, plan.name), data)


def _save_aws_runner(s: Settings, plan: awsmod.AwsPlan, label: str, iid: str) -> None:
    if owned.strict(s.asset_prefix):
        save_aws_runner(s, plan, label, iid)
        return
    state.save_runner(
        plan.name,
        {
            "server_url": s.server_url,
            "label": label,
            "target": "aws",
            "aws_region": s.aws_region,
            "aws_profile": s.aws_profile,
            "instance_id": iid,
            "name": plan.name,
        },
    )


def run_runner(args, io: IO, deps: Deps) -> int:
    dry = args.dry_run
    s = Settings(aws_instance_type="t4g.medium")
    s.aws_name = getattr(args, 'name', None) or ''
    s.aws_permissions_boundary = getattr(args, 'aws_permissions_boundary', None) or ''
    if s.aws_name and not awsmod.NAME_RE.fullmatch(s.aws_name):
        raise MissingInput('Invalid --name')
    if s.aws_permissions_boundary and not awsmod.POLICY_ARN_RE.fullmatch(s.aws_permissions_boundary):
        raise MissingInput('Invalid --aws-permissions-boundary')
    a = Asker(io, args, s, dry)
    io.say("tico setup runner" + (" (dry run: nothing will be created or changed)" if dry else ""))
    domain = (args.domain or "").lower()
    if not args.server_url and not domain:
        known = state.known_domains()
        domain = known[0] if len(known) == 1 else ""
    a.get("server_url", "Tico server URL", default=f"https://{domain}" if domain else "")
    s.server_url = s.server_url.rstrip("/")
    a.get("target", "Where should the runner live?", choices=TARGETS, default="aws")
    a.get(
        "runner_label",
        "Name for this computer in Tico",
        flag="--label",
        default=(host_of(args.ssh_host or "linux").replace(".", "-")),
    )
    label = s.runner_label
    s.runner_tag = args.runner_tag or server_tag(s.server_url, deps)
    screen = bool(getattr(args, "screen", True))
    if s.target == "ssh":
        a.get("ssh_host", "SSH destination (user@host)", flag="--ssh")
        s.ssh_port = int(args.ssh_port or 22)
        a.get(
            "ssh_identity", "SSH key file (blank uses your agent / default keys)", required=False, flag="--ssh-identity"
        )
    if s.target == "aws":
        a.get(
            "aws_region",
            "AWS region",
            flag="--aws-region",
            default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
        )
        a.get(
            "aws_instance_type",
            "Instance size (about 0.5-1 GiB RAM per bot working at once)",
            flag="--aws-instance-type",
            choices={k: k for k in awsmod.HOURLY},
            default="t4g.medium",
        )
        a.get(
            "aws_os",
            "Operating system",
            flag="--aws-os",
            choices={"ubuntu": "Ubuntu 24.04", "al2023": "Amazon Linux 2023"},
            default="ubuntu",
        )
        a.get("aws_profile", "AWS profile (blank for the default credentials)", flag="--aws-profile", required=False)
        s.asset_prefix = runner_prefix(s, label, getattr(args, "asset_prefix", None))
        if owned.strict(s.asset_prefix):
            # Strict mode: this runner's own install id, kept from its record, or new for a runner never set up here.
            rec = own_record(s, s.aws_name or f"runner-{awsmod.slug(label)}")
            if rec and not rec.get("install_id"):
                raise MissingInput(
                    "This runner's saved record has no install id, so setup cannot prove which AWS resources are its "
                    "own; refusing to change anything. Check by hand what exists before setting it up again."
                )
            s.install_id = rec.get("install_id", "") if rec else awsmod.new_install_id()
            s.aws_ids = dict(rec.get("aws_ids") or {})
    io.say("\nPlan")
    for line in plan_lines(s, label):
        io.say(line)
    if dry:
        io.say("\nDry run: nothing was created, changed or saved.")
        return 0
    if not args.yes and not io.confirm("\nGo ahead?", True):
        io.say("Stopped; nothing was changed.")
        return 1

    token = os.environ.get("TICO_OWNER_TOKEN", "")
    code = os.environ.get("TICO_ENROLL_CODE", "")
    scrub = lambda t: envfile.scrub(t, [code, token])

    def get_code() -> str:
        nonlocal code
        if token and not code:
            code = mint_code(s.server_url, token)
            io.say("  minted a one-time join code (valid 15 minutes)")
        elif not code:
            io.say(
                f"\nGet a one-time code: in Tico open Settings > Computers > Add computer (or set TICO_OWNER_TOKEN and I will mint one)."
            )
            code = io.ask("One-time code (hidden)", secret=True) if io.interactive else ""
            if not code:
                raise MissingInput("Missing the join code: set TICO_ENROLL_CODE or TICO_OWNER_TOKEN")
        return code

    shell: remote.Shell
    if s.target == "ssh":
        shell = deps.ssh_shell(s.ssh_host, s.ssh_port, s.ssh_identity)
        steps = ssh_steps(
            shell,
            url=s.server_url,
            code="",
            label=label,
            tag=s.runner_tag,
            rejoin=args.rejoin,
            say=io.say,
            scrub=scrub,
            screen=screen,
        )
        io.say("\nComputer")
        for i, step in enumerate(steps):
            io.say(f"  - {step.description}")
            try:
                if i == 2:  # the code is minted only now: it expires in 15 minutes
                    steps = ssh_steps(
                        shell,
                        url=s.server_url,
                        code=get_code(),
                        label=label,
                        tag=s.runner_tag,
                        rejoin=args.rejoin,
                        say=io.say,
                        scrub=scrub,
                        screen=screen,
                    )
                    step = steps[2]
                step.run()
            except StepFailed as e:
                io.say(f"\nFailed: {e}")
                return 1
    elif s.target == "aws":
        plan = _aws_plan(s, label)
        ec2, iam, ssm, account = deps.aws_clients(s.aws_region, s.aws_profile)
        strict = owned.strict(s.asset_prefix)
        if strict:
            owned.check_account(own_record(s, plan.name).get("aws_account_id", ""), account, f"Runner {plan.name}")
            s.aws_account_id = account

            def keep_ids() -> None:
                save_aws_runner(s, plan, label, s.aws_ids.get("instance", ""))

            d = owned.StrictDeployer(
                plan,
                ec2,
                iam,
                ssm,
                account,
                say=io.say,
                sleep=deps.sleep,
                ids=s.aws_ids,
                save=keep_ids,
                record=str(state.home() / "runners" / f"{runner_key(s, plan.name)}.json"),
            )
            d.preflight()
        else:
            d = awsmod.Deployer(plan, ec2, iam, ssm, account, say=io.say, sleep=deps.sleep)
        existing = d.find_instance()
        if existing:
            io.say(f"  {existing['InstanceId']} already exists for this name; not creating a second one.")
        if strict:  # before the first AWS write: a retry after a failure from here keeps this prefix without the flag
            save_aws_runner(s, plan, label, existing["InstanceId"] if existing else "")
        d.put_env(join_env(s.server_url, get_code(), label))
        prep = d.prepare()
        script = bootstrap_script(
            os_family=s.aws_os,
            tag=s.runner_tag,
            env_param=(d.param_name, s.aws_region),
            rejoin=args.rejoin,
            screen=screen,
        )
        iid, _ = d.launch_or_reuse(prep, script)
        _save_aws_runner(s, plan, label, iid)
        if existing:
            # SSM may still be registering after EC2 reports running. Wait before replay,
            # then let cloud-init finish before recovering an incomplete boot.
            # join_lines leaves an existing runner and its durable identity alone.
            shell = remote.SSMShell(ssm, iid, deps.sleep)
            result = shell.run(
                'cloud-init status --wait >/dev/null 2>&1 || true; bash -c ' + shlex.quote(script), ready_timeout=300
            )
            if result.returncode:
                reason = (
                    'Runner recovery timed out waiting for Systems Manager'
                    if result.returncode == 124
                    else 'Runner setup did not finish'
                )
                io.say(f'{reason}; the computer is kept for recovery.')
                return 1
        io.say("\nWaiting for the box to install Docker and start the runner (a few minutes) ...")
        if not d.wait_ready():
            io.say("The box did not report ready in time. Log: /var/log/tico-setup.log (via SSM Session Manager).")
            return 1
        shell = remote.SSMShell(ssm, iid, deps.sleep)
        _save_aws_runner(s, plan, label, iid)
    else:
        script = bootstrap_script(
            os_family="ubuntu",
            tag=s.runner_tag,
            env_b64=remote.env_b64(join_env(s.server_url, get_code(), label)),
            rejoin=args.rejoin,
            screen=screen,
        )
        out = state.home() / "runners" / f"{awsmod.slug(label)}-install-command.txt"
        state.write_private(out, remote.paste_command(script) + "\n")
        io.say(
            f"\nThe command holds the one-time join code, so it is not printed. It is one line in {out} (mode 0600)."
        )
        io.say(f"  Copy it:  pbcopy < {out}   (macOS)   or   xclip -selection clipboard < {out}   (Linux)")
        io.say("  Paste it in a shell on the Debian/Ubuntu box (sudo). Do it within 15 minutes: the code expires.")
        io.say(f"  Delete it afterwards:  rm {out}")
        state.save_runner(
            f"cmd-{awsmod.slug(label)}", {"server_url": s.server_url, "label": label, "target": "command"}
        )
        return 0
    if s.target == "ssh":
        state.save_runner(
            f"ssh-{awsmod.slug(label)}",
            {
                "server_url": s.server_url,
                "label": label,
                "target": "ssh",
                "ssh_host": s.ssh_host,
                "ssh_port": s.ssh_port,
                "ssh_identity": s.ssh_identity,
            },
        )
    return _verify(io, shell, s.server_url, token, label)


def _verify(io: IO, shell: remote.Shell, url: str, token: str, label: str) -> int:
    io.say("\nChecking it works")
    checks = [verify.check_runner_container(shell, label)]
    online = verify.check_computer_online(url, token, label)
    if online:
        checks.append(online)
    from .wizard import show

    show(io, checks)
    if all(c.ok for c in checks):
        io.say(f"\nDone. {label} is joined to {url}. Sign the models in for its bots from Tico's Settings > Computers.")
        return 0
    return 1


def prove_runner(ec2, account: str, r: dict) -> None:
    """Strict mode, before any remote shell on a runner: its record has an install id and account, and the instance,
    read now, carries that install's tags, is the one the record says it made, in that account (owned.prove_server)."""
    what = f"Runner {r.get('name') or r.get('label', '')}"
    if not r.get("install_id") or not r.get("aws_account_id"):
        raise MissingInput(
            f"{what}'s saved record has no install id or AWS account, so setup cannot prove EC2 instance "
            f"{r['instance_id']} is its box; refusing to run anything on it. Check it by hand in the EC2 console."
        )
    owned.prove_server(
        ec2,
        account,
        st.Settings(
            domain=what,
            aws_instance_id=r["instance_id"],
            asset_prefix=r["asset_prefix"],
            install_id=r["install_id"],
            aws_account_id=r["aws_account_id"],
            aws_name=r.get("name", ""),
            aws_ids=dict(r.get("aws_ids") or {}),
        ),
    )


def doctor_runners(io: IO, deps: Deps, server_url: str) -> bool:
    saved = state.load_runners(server_url)
    if not saved:
        io.say("  No runners were set up from this machine for that server.")
        return True
    ok = True
    for r in saved:
        io.say(f"\nRunner {r['label']} ({r['target']})")
        if r["target"] == "ssh":
            sh = deps.ssh_shell(r["ssh_host"], r.get("ssh_port", 22), r.get("ssh_identity", ""))
        elif r["target"] == "aws":
            if not r.get("instance_id"):
                io.say(
                    "  not launched yet: its setup stopped before the box started; run `python3 -m setup runner` again"
                )
                ok = False
                continue
            ec2, _, ssm, account = deps.aws_clients(r["aws_region"], r.get("aws_profile", ""))
            if owned.strict(r.get("asset_prefix", "")):  # its box, read now, before any shell on it
                try:
                    prove_runner(ec2, account, r)
                except MissingInput as e:
                    io.say(f"  {e}")
                    ok = False
                    continue
            sh = remote.SSMShell(ssm, r["instance_id"], deps.sleep)
        else:
            io.say("  Set up with the paste command; check it on the box: docker ps --filter name=tico-runner")
            continue
        c = verify.check_runner_container(sh, r["label"])
        from .wizard import show

        show(io, [c])
        ok = ok and c.ok
    return ok
