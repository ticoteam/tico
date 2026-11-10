"""Strict mode: setup under an --asset-prefix other than tico (the predicate is `strict`).

The default prefix runs aws.py, backup.py and route53.upsert exactly as before --asset-prefix existed. Under any other
prefix, setup shares an AWS account with things it did not make, so everything here acts only on what it can prove is
this install's: a resource carries the install's tags, its id among them, and is the one the install recorded making.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Callable

from . import backup as bk
from . import state
from .aws import AMI_PARAMS, DEFAULT_PREFIX, SSM_CORE, Deployer, Names, fit_user_data
from .ui import MissingInput


def strict(prefix: str) -> bool:
    """The one switch: any --asset-prefix but the default is strict mode."""
    return (prefix or DEFAULT_PREFIX) != DEFAULT_PREFIX


# --- the mode marker: a strict install stays strict ------------------------------------------------------------------
# Saved before the first AWS write, apart from state.json, keyed by domain and by name: a later command for either that
# would take the default path (the flag left out, state.json lost or corrupt) refuses instead. Only strict mode writes
# one, so a default-prefix computer has none and its commands read nothing new that could stop them.


def _marker(kind: str, key: str) -> Path:
    return state.home() / "installs" / f"{kind}-{key}.json"


def mark(domain: str, name: str, prefix: str, install_id: str) -> None:
    for kind, key in (("domain", domain), ("name", name)):
        if not key:
            continue
        p, rows = _marker(kind, key), {}
        if p.exists():
            rows = _read_marker(p)
        rows[install_id] = {"asset_prefix": prefix, "domain": domain, "name": name}
        state.write_private(p, json.dumps(rows, indent=2, sort_keys=True))


def _read_marker(p: Path) -> dict:
    try:
        rows = json.loads(p.read_text())
        if not isinstance(rows, dict) or not all(isinstance(r, dict) and r.get("asset_prefix") for r in rows.values()):
            raise ValueError
        return rows
    except (OSError, ValueError):
        raise MissingInput(
            f"{p} records an install made with an --asset-prefix, but cannot be read; refusing to change anything. "
            "Restore it, or check by hand which AWS resources that install made."
        ) from None


def check_mode(domain: str, name: str, prefix: str, saved: dict) -> None:
    """Refuses a command that would take another mode, or start another install, than the one this computer recorded
    making for the domain (or, by name, for a default-path command)."""
    prefix = prefix or DEFAULT_PREFIX
    had = (saved.get("domain") or "").lower()
    if strict(prefix) and saved and domain and had != domain.lower():
        raise MissingInput(
            f"The state.json under {domain} was saved for {had or 'no domain'} (install {saved.get('install_id')}), "
            "so it describes another install's AWS resources; refusing to change anything. Run setup for that domain, "
            "or move this copy aside."
        )
    rows = {}
    for kind, key in (("domain", domain), ("name", name if not strict(prefix) else "")):
        if key and _marker(kind, key).exists():
            rows.update(
                {
                    k: r
                    for k, r in _read_marker(_marker(kind, key)).items()
                    if kind == "name" or r.get("domain") == domain
                }
            )
    if not rows:
        return
    if not strict(prefix):
        install_id, r = next(iter(rows.items()))
        raise MissingInput(
            f"{r.get('domain') or domain} (--name {r.get('name')}) was made with --asset-prefix {r['asset_prefix']} "
            f"(install {install_id}); refusing to go on without it, which would act on other AWS resources. Run the "
            f"command again with --asset-prefix {r['asset_prefix']}."
        )
    mine = {k: r for k, r in rows.items() if r.get("domain") == domain}
    if mine and saved.get("install_id") not in mine:
        install_id, r = next(iter(mine.items()))
        why = "is missing or unreadable" if not saved else f"belongs to another install ({saved.get('install_id')})"
        raise MissingInput(
            f"This computer recorded install {install_id} for {domain}, made with --asset-prefix {r['asset_prefix']}, "
            f"but its state.json {why}; refusing to change anything or start another install. Check by hand in the AWS "
            "console which of its resources exist, and restore that install's state.json."
        )


def prove_server(ec2, account: str, s) -> None:
    """Strict mode, before any remote shell on the server: the instance, read now, carries this install's id tag, is the
    instance it recorded making, and is in the account it recorded."""
    from .aws import Names

    iid, names = s.aws_instance_id, Names(s.asset_prefix, s.install_id)
    check_account(s.aws_account_id, account, s.domain)
    rows = ec2.describe_instances(InstanceIds=[iid])["Reservations"]
    inst = next((i for r in rows for i in r["Instances"] if i.get("InstanceId") == iid), None)
    ok = inst is not None and names.is_ours(inst.get("Tags"), s.aws_name)
    if not ok or not recorded(s.aws_ids or {}, "instance", iid, (inst or {}).get("ClientToken", ""), row_name(inst)):
        raise MissingInput(
            f"EC2 instance {iid}, named in {s.domain}'s saved state, is not this install's server (its tags, or the "
            "instance this install recorded making, differ); refusing to run anything on it. Check the saved state."
        )


def not_ours(kind: str, resource: str, names: Names, name: str, pick: str = "--name or --asset-prefix") -> MissingInput:
    """The refusal for a name this install would take that something it did not make already holds."""
    install = f", {names.install_tag}={names.install_id}" if names.install_id else ""
    return MissingInput(
        f"{kind} {resource} already exists and was not made by this setup (it lacks the tags "
        f"{names.managed_tag[0]}={names.managed_tag[1]} and {names.name_tag}={name}{install}, or is not the one this "
        f"install recorded); refusing to reuse or change it. Pick another {pick}."
    )


def check_name(given: str | None, had: str, domain: str) -> None:
    """A domain's install keeps its name: another one would find, change or delete another install's resources."""
    given = (given or "").strip().lower()
    if given and had and given != had:
        raise MissingInput(
            f"{domain} is set up with --name {had}, and it cannot change on a later run. Leave --name out or pass "
            f"--name {had}; another name is another install, with its own --domain."
        )


# --- buckets setup makes -------------------------------------------------------------------------------------------
# A bucket is setup's only when it carries this install's tags, which it gets in the CreateBucket call itself: there is
# never an untagged bucket of setup's, so an untagged one is never adopted.


def error_code(e: Exception) -> str:
    return str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))


def bucket_tags(s3, owned: dict) -> list[dict]:
    try:
        return s3.get_bucket_tagging(**owned).get("TagSet") or []
    except Exception as e:  # NoSuchTagSet: a bucket with no tags at all
        if error_code(e) == "NoSuchTagSet":
            return []
        raise


def bucket_proof(s3, owned: dict, names: Names, name: str, pick: str) -> str:
    """Reads only: "absent" or "ours" (this install's tags); else refused."""
    try:
        s3.head_bucket(**owned)
    except Exception as e:
        if error_code(e) not in ("404", "NoSuchBucket", "NotFound"):
            raise
        return "absent"
    tags = bucket_tags(s3, owned)
    if names.is_ours(tags, name):
        return "ours"
    raise not_ours("S3 bucket", owned["Bucket"], names, name, pick)


def ensure_bucket(s3, owned: dict, region: str, names: Names, name: str, proof: str, pick: str) -> None:
    """After bucket_proof found no bucket of the name: creates it with its tags in the same call. Never called for a name
    that exists (us-east-1's CreateBucket answers success for one this account holds, and resets its ACLs)."""
    if proof != "absent":
        return
    bucket, config = owned["Bucket"], {"Tags": names.tags(name)}
    if region != "us-east-1":
        config["LocationConstraint"] = region
    try:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration=config)
    except Exception as e:
        if error_code(e) in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):  # made by someone since the read
            raise not_ours("S3 bucket", bucket, names, name, pick) from None
        if type(e).__name__ == "ParamValidationError":  # a boto3 from before CreateBucket took tags
            raise SystemExit(
                "Setup creates S3 buckets with their tags, which needs a current boto3: pip install -U boto3"
            ) from None
        raise
    # A bucket made in the instant between the read and the create, in this account, under a deterministic name (the
    # default prefix's backup bucket) is told apart only by its tags; a name ending in the install id has no such instant.
    if not names.is_ours(bucket_tags(s3, owned), name):
        raise not_ours("S3 bucket", bucket, names, name, pick)


def made(j: Journal, kind: str, s3, owned: dict, region: str, proof: str, pick: str) -> None:
    """A bucket bucket_proof found this install's is adopted only when recorded; an absent one is created, journalled."""
    if proof == "ours":
        j.adopt(kind, owned["Bucket"], "S3 bucket", owned["Bucket"])
        return
    j.missing(kind, "S3 bucket", owned["Bucket"])  # setup never deletes a bucket: a recorded one gone is refused
    j.intend(kind, name=owned["Bucket"])
    ensure_bucket(s3, owned, region, j.n, j.name, proof, pick)
    j.made(kind, owned["Bucket"])


def check_account(had: str, account: str, what: str) -> None:
    """An install's resources are its account's: the same names and tags in another account are someone else's."""
    if had and account != had:
        raise MissingInput(
            f"{what} was set up in AWS account {had}, but these credentials are for account {account}; refusing to "
            "change anything there. Use that account's credentials (--aws-profile)."
        )


def check_region(had: str, given: str | None, what: str) -> None:
    if had and given and given != had:
        raise MissingInput(
            f"{what} was set up in {had}, and its region cannot change on a later run. Leave --aws-region out or pass "
            f"--aws-region {had}; another region is another install."
        )


# --- the journal: which resources are this install's ---------------------------------------------------------------
# A resource is this install's when the install recorded making it: the id from its create call's own answer, saved at
# once. When that answer was lost, the create intent journalled before the call (its kind, name and, for an instance,
# its ClientToken) together with the install-id tag is the proof. Anything else carrying the install's tags (its saved
# state lost, cut short or edited) is never adopted, changed or deleted: setup refuses and says what to check by hand.


def recorded(ids: dict, kind: str, value: str, token: str = "", name: str = "") -> bool:
    """`value` is the `kind` this install recorded making, or the one its journalled create (answer lost) made: the same
    ClientToken where the create took one (an instance), else the same name under the same kind (`name`, the found
    resource's own name; none given proves nothing)."""
    if ids.get(kind):
        return ids[kind] == value
    intent = (ids.get("intents") or {}).get(kind)
    if intent is None or not value:
        return False
    if intent.get("token"):
        return intent["token"] == token
    return bool(name) and intent.get("name") == name and intent.get("type", kind) == kind


def row_name(row: dict | None) -> str:
    """A described resource's own name: a security group's GroupName, else its Name tag (an instance, an address)."""
    row = row or {}
    if row.get("GroupName"):
        return row["GroupName"]
    return next((t.get("Value", "") for t in row.get("Tags") or [] if t.get("Key") == "Name"), "")


def by_hand(what: str) -> str:
    return (
        f"{what} carries this install's tags, but this install never recorded making it (its saved state on this "
        "computer is missing, incomplete or edited); refusing to use, change or delete it. Check it by hand in the AWS "
        "console: if it is a leftover of this install, delete it there; otherwise restore this install's saved state "
        "(state.json) and run setup again."
    )


class Journal:
    """The ids of what an install makes, and its create intents, saved (by `save`) the moment each changes."""

    def __init__(
        self, ids: dict, save: Callable[[], None], names: Names, name: str, record: str = "this install's state.json"
    ):
        self.ids, self.save, self.n, self.name, self.record = ids, save, names, name, record

    def missing(self, kind: str, what: str, resource: str, exists: Callable[[], bool] | None = None) -> None:
        """Before `kind` is made anew because none is found: a recorded one is never replaced. Gone, it is refused with
        the step to take, unless this install's own destroy journalled deleting it (the record is then closed).
        `exists`: a re-read that waits out the service's lag, for a resource only slow to show."""
        if not self.ids.get(kind) or (exists is not None and exists()):
            return
        if close_deleted(self.ids, kind):
            self.save()
            return
        raise MissingInput(
            f"{what} {resource} ({self.ids[kind]}), which this install recorded making, no longer exists, and setup "
            "did not delete it; refusing to make another. Check it in the AWS console. If it is gone for good and "
            f'should be replaced, remove "{kind}" under "aws_ids" in {self.record} and run setup again.'
        )

    def intend(self, kind: str, **what: str) -> None:
        """Before a create call: if its answer is lost, the retry knows the resource it made for this install's (by
        its ClientToken, or by its name and kind)."""
        self.ids.setdefault("intents", {})[kind] = {**what, "type": kind}
        self.save()

    def token(self, kind: str) -> str:
        """The ClientToken of a journalled create, sent again on the retry (EC2 then answers with what it made)."""
        return ((self.ids.get("intents") or {}).get(kind) or {}).get("token", "") or uuid.uuid4().hex

    def made(self, kind: str, value: str) -> None:
        """From the create call's own answer. A recorded id is never overwritten by another (`missing` closes one that
        setup deleted first); the plan object's record is not an id, but what was last written."""
        had = self.ids.get(kind)
        if value and had and had != value and kind != "plan_object":
            raise MissingInput(
                f"This install recorded {kind} {had}, and AWS has now answered a create with {value}; refusing to "
                f'record over it. Check both in the AWS console, and "{kind}" under "aws_ids" in {self.record}.'
            )
        if value:
            self.ids[kind] = value
            (self.ids.get("intents") or {}).pop(kind, None)
            self.save()

    def adopt(self, kind: str, value: str, what: str, resource: str, token: str = "", name: str | None = None) -> None:
        """An existing resource with this install's tags is used only when it is the one recorded, or the one a
        journalled create made. `name`: the resource's own name, when `resource` is its id."""
        if self.ids.get(kind) and self.ids[kind] != value:
            raise not_ours(what, resource, self.n, self.name)
        if not recorded(self.ids, kind, value, token, resource if name is None else name):
            raise MissingInput(by_hand(f"{what} {resource}"))
        self.made(kind, value)


def settled(read: Callable, sleep: Callable[[float], None], missing: type[Exception], tries: int = 6):
    """IAM is eventually consistent: a read right after a create can answer NoSuchEntity for a few seconds. It is tried
    again after 1, 2, 4, 8 and 16 s; then the error stands."""
    for attempt in range(tries):
        try:
            return read()
        except missing:
            if attempt == tries - 1:
                raise
            sleep(2**attempt)
    raise AssertionError


def server_identity(prefix: str, server_url: str, region: str) -> str:
    """A short, fixed digest of what makes a runner's saved record another server's: prefix, full URL and region."""
    return hashlib.sha256(f"{prefix}|{server_url}|{region}".encode()).hexdigest()[:20]


class StrictDeployer(Deployer):
    """aws.Deployer, for an install with an id: every resource it reuses is proved first, and the ids of what it makes are
    kept (in `ids`, saved by `save`) as each is made."""

    def __init__(
        self,
        plan,
        ec2,
        iam,
        ssm,
        account_id,
        say=print,
        sleep=time.sleep,
        ids=None,
        save=lambda: None,
        record="this install's saved state (state.json)",
    ):
        if not plan.install_id:
            raise MissingInput("strict mode needs the install's id; refusing to touch AWS without it")
        super().__init__(plan, ec2, iam, ssm, account_id, say=say, sleep=sleep)
        self.ids = ids if ids is not None else {}
        self.save = save
        self.record = record  # the file `ids` is saved in, named in a by-hand step
        self.j = Journal(self.ids, save, self.n, plan.name, record)

    def find_instance(self) -> dict | None:
        """This install's server. Once its launch is recorded, that instance is read by its id and nothing else: a tag
        listing is eventually consistent, and missing a server there must never lead to launching a second one."""
        if self.ids.get("instance"):
            inst = self.recorded_row("instance")
            if inst is not None or self.ids.get("instance"):
                return inst
        r = self.ec2.describe_instances(
            Filters=self.n.filters(self.p.name)
            + [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
        )
        found = [i for res in r["Reservations"] for i in res["Instances"] if self.n.is_ours(i.get("Tags"), self.p.name)]
        if found:
            i = found[0]
            self.j.adopt(
                "instance", i["InstanceId"], "EC2 instance", i["InstanceId"], i.get("ClientToken", ""), row_name(i)
            )
        return found[0] if found else None

    def recorded_row(self, kind: str) -> dict | None:
        """The resource this install recorded making (`kind`: instance, eip or sg), read by its id and nothing else: any
        state short of terminated is used. Gone (terminated, or unknown after a bounded wait) it is refused, never
        replaced, unless this install's own destroy journalled deleting it; then the record is closed and None returned,
        so the next create is a new resource (an instance under a new ClientToken)."""
        rid = self.ids[kind]
        what, verb, refusal, extra = RECORDED[kind]
        row = described(self.ec2, kind, rid, self.sleep)
        state_now = ((row or {}).get("State") or {}).get("Name", "")
        if row is not None and state_now not in ("shutting-down", "terminated"):
            if not self.n.is_ours(row.get("Tags"), self.p.name):
                raise not_ours(what, rid, self.n, self.p.name)
            if undelete(self.ids, kind, rid):  # a delete journalled but never made: still this install's
                self.save()
            return row
        if close_deleted(self.ids, kind, extra):
            self.save()
            return None
        why = f"is {state_now}" if row is not None else "is not known to EC2 (after waiting about 30 seconds)"
        keys = " and ".join(f'"{k}"' for k in (kind, *extra[:1]))
        raise MissingInput(
            f"{what} {rid}, which this install recorded {verb}, {why}, and setup did not delete it; refusing to "
            f"{refusal}. Check it in the EC2 console (tag {self.n.install_tag}={self.n.install_id}). If it is gone for "
            f'good and should be replaced, remove {keys} under "aws_ids" in {self.record} and run setup again.'
        )

    def put_env(self, env_text: str, *, company_file=False) -> None:
        # The normalized team stays with the encrypted env, under the existing
        # instance role. Explicit Advanced avoids an attempted downgrade on retry.
        tier = {"Tier": "Advanced"} if company_file else {}
        if company_file and len(env_text.encode()) > 8192:
            from backend.company_file import starter

            raise SystemExit(
                "The starter plan and install settings exceed AWS Parameter Store's 8 KB limit. "
                + starter.IMPORT_IN_APP
            )
        tags = self.param_tags()
        if tags is not None and not self.n.is_ours(tags, self.p.name):
            raise not_ours("SSM parameter", self.param_name, self.n, self.p.name)
        if tags is not None:
            self.j.adopt("param", self.param_name, "SSM parameter", self.param_name)
        if tags is None:  # created with its tags, never overwritten: one made meanwhile by someone else fails here
            self.j.missing("param", "SSM parameter", self.param_name, lambda: self.param_tags(wait=True) is not None)
            self.j.intend("param", name=self.param_name)
            self.ssm.put_parameter(
                Name=self.param_name,
                Value=env_text,
                Type="SecureString",
                Overwrite=False,
                Tags=self.n.tags(self.p.name),
                **tier,
            )
            self.j.made("param", self.param_name)
        else:
            # Tags were read just above. SSM has no conditional put, so a parameter replaced in that instant is the
            # one residual window; it is never re-tagged here, so a replacement keeps its own tags.
            self.ssm.put_parameter(Name=self.param_name, Value=env_text, Type="SecureString", Overwrite=True, **tier)

    def param_tags(self, wait: bool = False) -> list[dict] | None:
        """The settings parameter's tags; None when there is no such parameter (`wait`: only after 1..16 s more)."""
        read = lambda: self.ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=self.param_name)["TagList"]
        try:
            return settled(read, self.sleep, self.ssm.exceptions.InvalidResourceId, 6 if wait else 1)
        except self.ssm.exceptions.InvalidResourceId:
            return None

    def role_exists(self, n: str) -> bool:
        """Read again while IAM may still be catching up (1..16 s)."""
        try:
            settled(lambda: self.iam.get_role(RoleName=n), self.sleep, self.iam.exceptions.NoSuchEntityException)
            return True
        except self.iam.exceptions.NoSuchEntityException:
            return False

    def profile_exists(self, n: str) -> bool:
        read = lambda: self.iam.get_instance_profile(InstanceProfileName=n)
        try:
            settled(read, self.sleep, self.iam.exceptions.NoSuchEntityException)
            return True
        except self.iam.exceptions.NoSuchEntityException:
            return False

    def role_tags(self) -> list[dict] | None:
        try:
            return self.iam.list_role_tags(RoleName=self.n.resource(self.p.name))["Tags"]
        except self.iam.exceptions.NoSuchEntityException:
            return None

    def preflight(self, s3=None) -> None:
        """Reads only, before setup's first write: every fixed name this install takes is free or already its own.
        Something else of that name is refused here, so nothing is created, changed or tagged first."""
        # Each recorded resource is read by its id (EC2) or its name (IAM, SSM, S3) and, gone, refused here, before any
        # write, unless this install's own destroy deleted it.
        tags = self.param_tags()
        if tags is not None and not self.n.is_ours(tags, self.p.name):
            raise not_ours("SSM parameter", self.param_name, self.n, self.p.name)
        if tags is not None:
            self.j.adopt("param", self.param_name, "SSM parameter", self.param_name)
        else:
            self.j.missing("param", "SSM parameter", self.param_name, lambda: self.param_tags(wait=True) is not None)
        tags = self.role_tags()
        if tags is not None and not self.n.is_ours(tags, self.p.name):
            raise not_ours("IAM role", self.n.resource(self.p.name), self.n, self.p.name)
        n = self.n.resource(self.p.name)
        if tags is not None:
            self.prove_role(n)
        else:
            self.j.missing("role", "IAM role", n, lambda: self.role_exists(n))
        prof = instance_profile(self.iam, n)
        self.prove_profile(prof)
        if prof is None:
            self.j.missing("profile", "IAM instance profile", n, lambda: self.profile_exists(n))
        self.find_instance()  # also refuses an instance that is not the recorded one
        flt = self.n.filters(self.p.name)
        if self.ids.get("sg"):  # recorded: read by its id, never looked for in a listing
            self.recorded_row("sg")
        else:
            for g in self.ec2.describe_security_groups(Filters=flt)["SecurityGroups"]:
                if self.n.is_ours(g.get("Tags"), self.p.name):
                    self.j.adopt("sg", g["GroupId"], "Security group", g["GroupId"], name=row_name(g))
        if self.ids.get("eip"):  # recorded: read by its id, never looked for in a listing
            self.recorded_row("eip")
        else:
            for a in self.ec2.describe_addresses(Filters=flt)["Addresses"]:
                if self.n.is_ours(a.get("Tags"), self.p.name):
                    self.j.adopt("eip", a["AllocationId"], "Elastic IP", a["AllocationId"], name=row_name(a))
        if s3 is not None and self.p.plan_bucket:  # setup's own plan bucket
            owned = {"Bucket": self.p.plan_bucket, "ExpectedBucketOwner": self.account_id}
            if bucket_proof(s3, owned, self.n, self.p.name, "--name") == "ours":
                self.j.adopt("plan_bucket", self.p.plan_bucket, "S3 bucket", self.p.plan_bucket)

    def prove_profile(self, prof: dict | None) -> None:
        n = self.n.resource(self.p.name)
        if prof is None:
            return
        if not profile_is_ours(prof, n, self.n):
            raise not_ours("IAM instance profile", n, self.n, self.p.name)
        self.j.adopt("profile", prof.get("InstanceProfileId", ""), "IAM instance profile", n)

    def ensure_plan_bucket(self, s3, region: str) -> None:
        """Configure only setup's dedicated bucket, in the installer's account. Never adopt an unrelated bucket.
        Every safety setting precedes the first chart upload. Re-running repairs an interrupted configuration.
        """
        owned = {"Bucket": self.p.plan_bucket, "ExpectedBucketOwner": self.account_id}
        try:
            # A collision is an error, never permission to reuse someone else's name.
            proof = bucket_proof(s3, owned, self.n, self.p.name, "--name")
        except MissingInput:
            raise SystemExit("The starter plan bucket was not created by this setup; refusing to reuse it.") from None
        made(self.j, "plan_bucket", s3, owned, region, proof, "--name")
        # S3 bucket settings have no conditional writes: the bucket is read again (its tags, and the name this install
        # recorded making) right before each one.
        self.prove_plan_bucket(s3)
        s3.put_public_access_block(
            **owned,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        self.prove_plan_bucket(s3)
        s3.put_bucket_ownership_controls(
            **owned, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
        )
        self.prove_plan_bucket(s3)
        s3.put_bucket_encryption(
            **owned,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
        self.prove_plan_bucket(s3)
        s3.put_bucket_lifecycle_configuration(
            **owned,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "expire-starter-plans",
                        "Status": "Enabled",
                        "Filter": {"Prefix": f"plans/{self.p.name}/"},
                        "Expiration": {"Days": 1},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                    }
                ]
            },
        )

    def verify_plan_bucket(self, s3) -> None:
        """A --plan-bucket: never written in strict mode. An install tag on a bucket proves only that someone tagged it,
        not that setup made it; the plan goes only into a bucket this install recorded making."""
        raise MissingInput(
            f"--plan-bucket {self.p.plan_bucket}: with --asset-prefix, setup uploads the starter plan only to a bucket it "
            "made and recorded itself; refusing to write to one it did not make. Leave --plan-bucket out."
        )

    def prove_plan_bucket(self, s3) -> None:
        """Read now, right before a write: the plan bucket carries this install's tags and is the one it recorded
        making."""
        n, owned = self.n, {"Bucket": self.p.plan_bucket, "ExpectedBucketOwner": self.account_id}
        bucket = self.p.plan_bucket
        if n.is_ours(bucket_tags(s3, owned), self.p.name) and recorded(self.ids, "plan_bucket", bucket, name=bucket):
            return
        raise not_ours("S3 bucket", self.p.plan_bucket, n, self.p.name, "--name")

    def plan_record(self) -> dict:
        """What this install recorded writing to the plan bucket: {"key", "etag"} (and "version_id" when versioned)."""
        rec = self.ids.get("plan_object") or {}
        return rec if isinstance(rec, dict) else {"key": rec}

    def put_plan(self, s3, key: str, data: str) -> None:
        """The packed starter plan, for the server to copy once at first boot (remote.bootstrap_script). Plan only:
        the env names the key and its sha256, never the plan. Written only where no object is (IfNoneMatch), journalled
        first (key, sha256 and a fresh nonce, also written in the object's metadata). On S3's 412 the object there is
        this install's only when it carries that nonce, from an intent an earlier call journalled (its answer lost) or
        this call's own retried attempt: one that was already there is never adopted, whatever its bytes or metadata.
        A recorded write is kept as it was: the object at its key must still be that version (or, unversioned, that
        ETag), else the re-run refuses; it is never re-recorded as another version."""
        if not key.startswith(f"plans/{self.p.name}/"):
            raise ValueError("a starter plan key is plans/<this server's name>/<sha256>")
        raw = data.encode("ascii")
        sha = hashlib.sha256(raw).hexdigest()
        where = {"Bucket": self.p.plan_bucket, "Key": key, "ExpectedBucketOwner": self.account_id}
        self.prove_plan_bucket(s3)
        rec = self.plan_record()
        intent = (self.ids.get("intents") or {}).get("plan_object") or {}
        if rec.get("key") == key:
            nonce = rec.get("nonce", "")
        elif intent.get("key") == key and intent.get("sha256") == sha and intent.get("nonce"):
            nonce = intent["nonce"]  # an earlier call's put, its answer lost: the same nonce again
        else:
            nonce = uuid.uuid4().hex
            self.j.intend("plan_object", key=key, sha256=sha, nonce=nonce)
        meta = {"install": self.n.install_id, "sha256": sha, "nonce": nonce}
        refused = MissingInput(
            f"s3://{self.p.plan_bucket}/{key} already exists and this install did not record writing it (or it changed "
            "since); refusing to overwrite it or take it for this install's."
        )
        try:  # only where no object is: one already at the key is never overwritten
            got = s3.put_object(**where, IfNoneMatch="*", Body=raw, ContentType="text/plain", Metadata=meta)
        except Exception as e:
            if error_code(e) != "PreconditionFailed":
                raise
            got = s3.head_object(**where)
            m = got.get("Metadata") or {}
            if rec.get("key") == key:
                same = (
                    got.get("VersionId") == rec["version_id"]
                    if rec.get("version_id")
                    else got.get("ETag") == rec.get("etag")
                )
                if not same or m.get("sha256") != sha:
                    raise refused from None
                return  # this install's recorded write, still there: the record stays exactly as it was
            if not (nonce and m.get("nonce") == nonce and m.get("sha256") == sha):
                if (self.ids.get("intents") or {}).get("plan_object", {}).get("nonce") == nonce:
                    self.ids["intents"].pop("plan_object")  # this put wrote nothing: its intent proves nothing
                    self.save()
                raise refused from None
        rec = {"key": key, "etag": (got or {}).get("ETag", ""), "nonce": nonce}
        if (got or {}).get("VersionId"):
            rec["version_id"] = got["VersionId"]
        self.j.made("plan_object", rec)

    def delete_plan(self, s3, key: str) -> str:
        """Once the server is up and its app reads its copy. Returns "" when deleted, else the error code: the server is
        already working, and the plan stays until someone deletes it or the bucket's lifecycle rule, if it has one,
        expires it. Only the object this install recorded writing is ever deleted, and never by a read followed by a
        plain delete: in a versioned bucket exactly the version it wrote (VersionId); otherwise a delete S3 carries out
        only while the object's ETag is still the recorded one (IfMatch)."""
        rec = self.plan_record()
        if rec.get("key") != key or not rec.get("etag"):
            return "NotWrittenByThisInstall"
        try:
            self.prove_plan_bucket(s3)
        except MissingInput:
            return "NotThisInstallsBucket"
        cond = {"VersionId": rec["version_id"]} if rec.get("version_id") else {"IfMatch": rec["etag"]}
        code = self._s3_call(s3.delete_object, Bucket=self.p.plan_bucket, Key=key, **cond)
        return "ChangedSinceWritten" if code == "PreconditionFailed" and not rec.get("version_id") else code

    def _s3_call(self, call, **kw) -> str:
        """ "" when the call, made with this account as the expected bucket owner, worked; else its error code."""
        try:
            call(**kw, ExpectedBucketOwner=self.account_id)
        except Exception as e:  # noqa: BLE001  botocore ClientError or a network error, without importing botocore
            return str(getattr(e, "response", {}).get("Error", {}).get("Code") or type(e).__name__)
        return ""

    def ensure_role(self) -> str:
        n = self.n.resource(self.p.name)
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}
            ],
        }
        # The tags and the boundary go in the CreateRole call itself: an account that only lets setup create roles under a
        # boundary, tagged ManagedBy=<prefix>-setup, refuses a role that would get them afterwards.
        boundary = {"PermissionsBoundary": self.p.boundary} if self.p.boundary else {}
        if self.role_tags() is None:
            self.j.missing("role", "IAM role", n, lambda: self.role_exists(n))
            self.j.intend("role", name=n)
        try:
            made = self.iam.create_role(
                RoleName=n, AssumeRolePolicyDocument=json.dumps(trust), Tags=self.n.tags(self.p.name), **boundary
            )
            self.j.made("role", ((made or {}).get("Role") or {}).get("RoleId", ""))
        except self.iam.exceptions.EntityAlreadyExistsException:
            if not self.n.is_ours(self.iam.list_role_tags(RoleName=n)["Tags"], self.p.name):
                raise SystemExit(
                    f"IAM role {n} exists but was not created by tico setup; refusing to reuse it."
                ) from None
        # IAM and SSM have no conditional writes: the role is read again, by its install id tag and recorded RoleId,
        # right before each write. That re-read is the bar; a replacement made in the instant after it is not caught.
        self.prove_role(n)
        self.iam.attach_role_policy(RoleName=n, PolicyArn=SSM_CORE)
        arn = self.ssm_param_arn()
        pol = {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": arn}],
        }
        if self.p.plan_bucket:
            # Its own plan only; a boundary, when there is one, only limits this and grants nothing.
            pol["Statement"].append(
                {
                    "Effect": "Allow",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{self.p.plan_bucket}/plans/{self.p.name}/*",
                }
            )
        self.prove_role(n)
        self.iam.put_role_policy(RoleName=n, PolicyName=self.n.role_policy, PolicyDocument=json.dumps(pol))
        if instance_profile(self.iam, n) is None:
            self.j.missing("profile", "IAM instance profile", n, lambda: self.profile_exists(n))
            self.j.intend("profile", name=n)
        try:
            made = self.iam.create_instance_profile(InstanceProfileName=n, Tags=self.n.tags(self.p.name))
            self.j.made("profile", ((made or {}).get("InstanceProfile") or {}).get("InstanceProfileId", ""))
            self.add_role(n)
        except self.iam.exceptions.EntityAlreadyExistsException:
            prof = instance_profile(self.iam, n)
            if not prof:
                raise not_ours("IAM instance profile", n, self.n, self.p.name) from None
            self.prove_profile(prof)
            if not prof.get("Roles"):  # an earlier run stopped between creating it and adding the role
                self.add_role(n)
        return n

    def add_role(self, n: str) -> None:
        """Adds the role to the profile, both read again right before it (tags and recorded ids)."""
        try:
            read = lambda: self.iam.get_instance_profile(InstanceProfileName=n)["InstanceProfile"]
            prof = settled(read, self.sleep, self.iam.exceptions.NoSuchEntityException)
        except self.iam.exceptions.NoSuchEntityException:
            raise not_ours("IAM instance profile", n, self.n, self.p.name) from None
        self.prove_profile(prof)
        self.prove_role(n)
        self.iam.add_role_to_instance_profile(InstanceProfileName=n, RoleName=n)

    def prove_role(self, n: str) -> str:
        """The role's RoleId, read now: it must carry this install's tags and be the role this install recorded (or the
        one its journalled create made)."""
        role = settled(
            lambda: self.iam.get_role(RoleName=n)["Role"], self.sleep, self.iam.exceptions.NoSuchEntityException
        )
        tags = role.get("Tags")
        if tags is None:  # GetRole returns tags on real IAM; older fakes and SDKs may not
            tags = self.iam.list_role_tags(RoleName=n)["Tags"]
        if not self.n.is_ours(tags, self.p.name):
            raise not_ours("IAM role", n, self.n, self.p.name)
        self.j.adopt("role", role.get("RoleId", ""), "IAM role", n)
        return role.get("RoleId", "")

    def ensure_sg(self) -> str:
        n = self.n.resource(self.p.name)
        if self.ids.get("sg"):  # recorded: read by its id, never looked for in a listing or made again
            g = self.recorded_row("sg")
            if g is not None:
                self.authorize(g["GroupId"], g.get("IpPermissions") or [])
                return g["GroupId"]
        r = self.ec2.describe_security_groups(Filters=self.n.filters(self.p.name))["SecurityGroups"]
        r = [g for g in r if self.n.is_ours(g.get("Tags"), self.p.name)]
        if r:
            self.j.adopt("sg", r[0]["GroupId"], "Security group", r[0]["GroupId"], name=row_name(r[0]))
            # Proved this install's: a run stopped before its ingress gets it now.
            self.authorize(r[0]["GroupId"], r[0].get("IpPermissions") or [])
            return r[0]["GroupId"]
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise SystemExit(
                "This region has no default VPC. Pick another region (--aws-region) or create a default VPC."
            )
        self.j.intend("sg", name=n)
        gid = self.ec2.create_security_group(
            GroupName=n,
            Description=self.n.sg_description,
            VpcId=vpcs[0]["VpcId"],
            TagSpecifications=[{"ResourceType": "security-group", "Tags": self.n.tags(self.p.name, n)}],
        )["GroupId"]
        self.j.made("sg", gid)
        self.authorize(gid, [])
        return gid

    def ingress(self) -> list[dict]:
        perms = [
            {
                "IpProtocol": "tcp",
                "FromPort": p,
                "ToPort": p,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
            }
            for p in self.p.ports
        ]
        if perms:
            perms.append({"IpProtocol": "udp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]})
        return perms

    def authorize(self, gid: str, have: list[dict]) -> None:
        """Adds the wanted ingress the group lacks (all of it for a new group)."""

        def rules(perms: list[dict]) -> set[tuple]:
            out = set()
            for p in perms:
                port = (p.get("IpProtocol"), p.get("FromPort"), p.get("ToPort"))
                out |= {(*port, r["CidrIp"]) for r in p.get("IpRanges", [])}
                out |= {(*port, r["CidrIpv6"]) for r in p.get("Ipv6Ranges", [])}
            return out

        missing = rules(self.ingress()) - rules(have)
        if not missing:
            return
        perms = []
        for p in self.ingress():
            v4 = [
                r
                for r in p.get("IpRanges", [])
                if (p["IpProtocol"], p["FromPort"], p["ToPort"], r["CidrIp"]) in missing
            ]
            v6 = [
                r
                for r in p.get("Ipv6Ranges", [])
                if (p["IpProtocol"], p["FromPort"], p["ToPort"], r["CidrIpv6"]) in missing
            ]
            if v4 or v6:
                perms.append({**p, "IpRanges": v4, **({"Ipv6Ranges": v6} if "Ipv6Ranges" in p else {})})
        self.ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=perms)

    def ensure_eip(self) -> tuple[str, str]:
        """AllocateAddress takes no ClientToken: a retry cannot ask EC2 for the address an earlier call made. A recorded
        allocation is read by its id (recorded_row), never looked for in a listing or allocated again. An open intent
        (an answer that was lost) is waited for while EC2's listing catches up, and never allocated again; an intent whose
        call provably made nothing is closed."""
        if self.ids.get("eip"):
            row = self.recorded_row("eip")
            if row is not None:
                return row["AllocationId"], row["PublicIp"]
        name = self.n.resource(self.p.name)
        waits = (1, 2, 4, 8, 16) if (self.ids.get("intents") or {}).get("eip") is not None else ()
        for wait in (*waits, None):
            r = self.ec2.describe_addresses(Filters=self.n.filters(self.p.name))["Addresses"]
            r = [a for a in r if self.n.is_ours(a.get("Tags"), self.p.name)]
            if r:
                self.j.adopt("eip", r[0]["AllocationId"], "Elastic IP", r[0]["AllocationId"], name=row_name(r[0]))
                return r[0]["AllocationId"], r[0]["PublicIp"]
            if wait is not None:
                self.sleep(wait)
        if waits:
            tag = f"{self.n.install_tag}={self.n.install_id}"
            raise MissingInput(
                f"Setup's last run asked EC2 for an Elastic IP ({name}) but never heard back, and none with this "
                "install's tags is listed yet; refusing to allocate a second one. Check, after a few minutes: "
                f"aws ec2 describe-addresses --region {self.p.region} --filters "
                f"Name=tag:{self.n.install_tag},Values={self.n.install_id} (or the EC2 console, Elastic IPs, tag {tag}). "
                "If it lists an address, run setup again: it is found and kept. If it lists none, remove \"eip\" "
                f'under "intents" under "aws_ids" in {self.record}, then run setup again.'
            )
        self.j.intend("eip", name=name)
        try:
            a = self.ec2.allocate_address(
                Domain="vpc",
                TagSpecifications=[{"ResourceType": "elastic-ip", "Tags": self.n.tags(self.p.name, name)}],
            )
        except Exception as e:
            if made_nothing(e):  # provably no address: the intent is closed, and a re-run simply allocates
                self.ids["intents"].pop("eip", None)
                self.save()
            raise
        self.j.made("eip", a["AllocationId"])
        return a["AllocationId"], a["PublicIp"]

    def launch(self, user_data: str, profile: str, sg: str, subnet: str = "") -> str:
        parameter = AMI_PARAMS[self.p.os_family]
        if self.p.instance_type.startswith('m8i-'):
            parameter = parameter.replace('arm64', 'amd64' if self.p.os_family == 'ubuntu' else 'x86_64')
        ami = self.ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
        n = self.n.resource(self.p.name)
        args = dict(
            ImageId=ami,
            InstanceType=self.p.instance_type,
            MinCount=1,
            MaxCount=1,
            UserData=fit_user_data(user_data),
            IamInstanceProfile={"Name": profile},
            SecurityGroupIds=[sg],
            MetadataOptions={"HttpTokens": "required", "HttpPutResponseHopLimit": 1, "HttpEndpoint": "enabled"},
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1" if self.p.os_family == "ubuntu" else "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": self.p.volume_gib,
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            TagSpecifications=[
                {"ResourceType": rt, "Tags": self.n.tags(self.p.name, n)}
                for rt in ("instance", "volume", "network-interface")
            ],
        )
        if subnet:
            args["SubnetId"] = subnet
        # Journalled before the call, and sent again on a retry: EC2 answers a repeated ClientToken with the instance it
        # already launched, so a lost answer never makes a second server.
        # The token stays with the install for its life (instance_token): a launch call repeated for any reason is
        # answered with the same server. Only this install's own destroy closes it (recorded_row).
        token = self.ids.get("instance_token") or self.j.token("instance")
        self.ids["instance_token"] = token
        self.j.intend("instance", name=n, token=token)
        args["ClientToken"] = token
        for attempt in range(8):  # a new instance profile takes a few seconds to be usable
            try:
                iid = self.ec2.run_instances(**args)["Instances"][0]["InstanceId"]
                self.j.made("instance", iid)
                return iid
            except Exception as e:  # botocore ClientError, without importing botocore here
                if not ("Instance Profile" in str(e) or "iamInstanceProfile" in str(e)) or attempt == 7:
                    raise
                self.sleep(5)
        raise AssertionError

    def launch_or_reuse(self, prep: dict, user_data: str) -> tuple[str, bool]:
        """Returns (instance id, created). Running it again reuses what exists."""
        inst = self.find_instance()
        if inst:
            self.say(f"  instance {inst['InstanceId']} already exists; reusing it.")
            iid, created = inst["InstanceId"], False
            if self.p.plan_bucket:
                self.ensure_role()  # a server set up before --plan-bucket gets the read of its own plan
        else:
            iid, created = self.launch(user_data, self.ensure_role(), prep["sg"], prep.get("subnet", "")), True
            self.say(f"  launched {iid}")
        self.ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
        self.journal_attached(iid)
        if prep["alloc"]:
            self.associate(prep["alloc"], iid)
        return iid, created

    def journal_attached(self, iid: str) -> None:
        """The root volume and primary network interface the launch made with the server, journalled once it runs (the
        RunInstances answer names no volume yet). Only those two: anything attached since is never this install's, and
        an instance that shows neither leaves them unrecorded, so destroy refuses to terminate it."""
        if self.ids.get("volumes") and self.ids.get("enis"):
            return
        rows = self.ec2.describe_instances(InstanceIds=[iid])["Reservations"]
        inst = next((i for r in rows for i in r["Instances"] if i.get("InstanceId") == iid), {})
        root = inst.get("RootDeviceName")
        self.ids["volumes"] = [
            m["Ebs"]["VolumeId"]
            for m in inst.get("BlockDeviceMappings") or []
            if root and m.get("DeviceName") == root and (m.get("Ebs") or {}).get("VolumeId")
        ]
        self.ids["enis"] = [
            n["NetworkInterfaceId"]
            for n in inst.get("NetworkInterfaces") or []
            if (n.get("Attachment") or {}).get("DeviceIndex") == 0 and n.get("NetworkInterfaceId")
        ]
        self.save()

    def associate(self, alloc: str, iid: str) -> None:
        """Read fresh by its id: setup's own address (its tags, the recorded allocation), free or already on this server.
        The call never moves it: an address attached elsewhere since this read makes EC2 refuse it."""
        try:
            rows = self.ec2.describe_addresses(AllocationIds=[alloc])["Addresses"]
        except Exception as e:
            if error_code(e) != "InvalidAllocationID.NotFound":
                raise
            rows = []
        row = next((a for a in rows if a.get("AllocationId") == alloc), None)
        mine = recorded(self.ids, "eip", alloc, name=row_name(row))
        if row is None or not self.n.is_ours(row.get("Tags"), self.p.name) or not mine:
            raise MissingInput(
                f"Elastic IP {alloc} is no longer this install's (released, retagged, or not the allocation it recorded); "
                "refusing to attach it. Check it by hand in the EC2 console, then run setup again."
            )
        at = row.get("InstanceId") or ""
        if at and at != iid:
            raise MissingInput(
                f"Elastic IP {alloc} is attached to {at}, which is not this install's server; refusing to move it."
            )
        if not at:
            self.ec2.associate_address(AllocationId=alloc, InstanceId=iid, AllowReassociation=False)


# kind: (what, how it was made, what a gone one does not lead to, the record's other keys a delete closes with it)
RECORDED = {
    "instance": ("EC2 instance", "launching", "launch another server", ("instance_token", "volumes", "enis")),
    "eip": ("Elastic IP", "allocating", "allocate another", ()),
    "sg": ("Security group", "creating", "create another", ()),
}


def deleting(ids: dict, kind: str, rid: str, save: Callable[[], None]) -> None:
    """Journalled right before setup's own delete call: a later run that finds the recorded resource gone knows setup
    deleted it, and may make another."""
    gone = ids.setdefault("deleted", {})
    gone[kind] = sorted({*gone.get(kind, []), rid})
    save()


def undelete(ids: dict, kind: str, rid: str) -> bool:
    gone = (ids.get("deleted") or {}).get(kind) or []
    if rid in gone:
        gone.remove(rid)
        return True
    return False


def close_deleted(ids: dict, kind: str, extra: tuple[str, ...] = ()) -> bool:
    """When setup itself deleted the recorded `kind`: its record (and `extra` keys) closed, True."""
    if not undelete(ids, kind, ids.get(kind, "")):
        return False
    for k in (kind, *extra):
        ids.pop(k, None)
    return True


def made_nothing(e: Exception) -> bool:
    """A create call that provably made nothing: botocore stopped it before any request left, or AWS answered its one
    and only attempt (no retries) with a 4xx. Anything else (no answer, a 5xx, an answer after retries, an interrupted
    process) may have made the resource."""
    if type(e).__name__ in ("ParamValidationError", "NoCredentialsError", "PartialCredentialsError", "NoRegionError"):
        return True
    meta = getattr(e, "response", None)
    info = meta.get("ResponseMetadata") if isinstance(meta, dict) else None
    if not isinstance(info, dict) or info.get("RetryAttempts") != 0 or not info.get("HTTPStatusCode"):
        return False
    return 400 <= int(info["HTTPStatusCode"]) < 500 and bool(error_code(e))


NOT_FOUND = {
    "instance": ("InvalidInstanceID.NotFound", "InvalidInstanceID.Malformed"),
    "eip": ("InvalidAllocationID.NotFound",),
    "sg": ("InvalidGroup.NotFound", "InvalidGroupId.Malformed"),
}


def described(ec2, kind: str, rid: str, sleep: Callable[[float], None] | None) -> dict | None:
    """A recorded resource, read by its id (never by a listing). None when EC2 does not know it; with `sleep`, an unknown
    id is read again after 1, 2, 4, 8 and 16 s first, as EC2 may not show a resource it just made."""
    call, key, rows_key, arg = {
        "instance": (ec2.describe_instances, "InstanceId", None, "InstanceIds"),
        "eip": (ec2.describe_addresses, "AllocationId", "Addresses", "AllocationIds"),
        "sg": (ec2.describe_security_groups, "GroupId", "SecurityGroups", "GroupIds"),
    }[kind]
    for wait in ((1, 2, 4, 8, 16) if sleep else ()) + (None,):
        try:
            out = call(**{arg: [rid]})
            rows = [i for r in out["Reservations"] for i in r["Instances"]] if rows_key is None else out[rows_key]
        except Exception as e:
            if error_code(e) not in NOT_FOUND[kind]:
                raise
            rows = []
        row = next((r for r in rows if r.get(key) == rid), None)
        if row is not None or wait is None:
            return row
        sleep(wait)
    return None


def instance_profile(iam, name: str) -> dict | None:
    try:
        return iam.get_instance_profile(InstanceProfileName=name)["InstanceProfile"]
    except iam.exceptions.NoSuchEntityException:
        return None


def profile_is_ours(prof: dict, name: str, names: Names) -> bool:
    """Its own tags make it setup's <prefix>-<tagged name>, and it holds no role but setup's of that name. A profile is
    checked apart from its role: a role being setup's says nothing about a profile of the same name."""
    tags = {t["Key"]: t["Value"] for t in prof.get("Tags") or []}
    tagged = tags.get(names.name_tag)
    return (
        bool(tagged)
        and names.is_ours(prof.get("Tags"), tagged)
        and names.resource(tagged) == name
        and all(r.get("RoleName") == name for r in prof.get("Roles") or [])
    )


def foreign_attached(ec2, instance_ids: list[str], name: str, names: Names, ids: dict) -> list[str]:
    """The volumes and network interfaces that terminating these instances would delete (DeleteOnTermination) but that
    are not the ones this install journalled its launch making (with its tags): someone attached them, so terminating
    would delete what setup did not make."""
    if not instance_ids:
        return []
    flt = [{"Name": "attachment.instance-id", "Values": list(instance_ids)}]
    vols = [
        v["VolumeId"]
        for v in ec2.describe_volumes(Filters=flt)["Volumes"]
        if any(a.get("InstanceId") in instance_ids and a.get("DeleteOnTermination") for a in v.get("Attachments", []))
        and not (names.is_ours(v.get("Tags"), name) and v["VolumeId"] in (ids.get("volumes") or []))
    ]
    enis = [
        n["NetworkInterfaceId"]
        for n in ec2.describe_network_interfaces(Filters=flt)["NetworkInterfaces"]
        if (n.get("Attachment") or {}).get("InstanceId") in instance_ids
        and n["Attachment"].get("DeleteOnTermination")
        and not (names.is_ours(n.get("TagSet"), name) and n["NetworkInterfaceId"] in (ids.get("enis") or []))
    ]
    return vols + enis


def refuse_foreign_attached(ec2, instance_ids: list[str], name: str, names: Names, ids: dict) -> None:
    found = foreign_attached(ec2, instance_ids, name, names, ids)
    if found:
        raise MissingInput(
            f"Terminating {', '.join(instance_ids)} would also delete {', '.join(found)}, which this install did not "
            "record making (attached with DeleteOnTermination); refusing, and nothing was deleted. Check it by hand in the EC2 console: detach "
            "it, or turn off its DeleteOnTermination, then run destroy again."
        )


def association(ec2, alloc: str, sleep: Callable[[float], None] | None) -> str | None:
    """What the address is associated with, read fresh by its allocation id ("" for nothing at all: no association, no
    instance, no network interface; None when it is gone). With `sleep` (it was on the server just terminated), EC2 is
    given a little while to show it freed."""
    for wait in (1, 2, 4, 8, 16, None) if sleep else (None,):
        try:
            rows = ec2.describe_addresses(AllocationIds=[alloc])["Addresses"]
        except Exception as e:
            if error_code(e) != "InvalidAllocationID.NotFound":
                raise
            return None
        row = next((r for r in rows if r.get("AllocationId") == alloc), None)
        if row is None:
            return None
        at = [row[k] for k in ("AssociationId", "InstanceId", "NetworkInterfaceId") if row.get(k)]
        if not at or wait is None:
            return ", ".join(at)
        sleep(wait)
    return ""


def _delete_sg(ec2, g: str, sleep) -> None:
    for attempt in range(6):  # the network interface lingers briefly after termination
        try:
            ec2.delete_security_group(GroupId=g)
            return
        except Exception:
            if attempt == 5:
                raise
            sleep(5)


def refuse_unrecorded(ec2, iam, found: dict, names: Names, name: str, ids: dict) -> None:
    """Before any delete: everything found with the install's tags must be what it recorded making. Otherwise its saved
    state is missing, cut short or edited, and nothing is deleted."""
    bad = []
    if found.get("instance"):
        r = ec2.describe_instances(Filters=names.filters(name))
        token = {i["InstanceId"]: i.get("ClientToken", "") for res in r["Reservations"] for i in res["Instances"]}
        bad += [f"EC2 instance {i}" for i in found["instance"] if not recorded(ids, "instance", i, token.get(i, ""))]

    def named(kind: str, rows: Callable[[], list[dict]], key: str) -> dict[str, str]:
        """Each found resource's own name, read only when an intent (no recorded id) has to be matched by it."""
        if ids.get(kind) or not (ids.get("intents") or {}).get(kind):
            return {}
        return {r.get(key, ""): row_name(r) for r in rows()}

    eips = named("eip", lambda: ec2.describe_addresses(Filters=names.filters(name))["Addresses"], "AllocationId")
    sgs = named("sg", lambda: ec2.describe_security_groups(Filters=names.filters(name))["SecurityGroups"], "GroupId")
    bad += [f"Elastic IP {a}" for a in found.get("elastic-ip", []) if not recorded(ids, "eip", a, name=eips.get(a, ""))]
    bad += [
        f"security group {g}"
        for g in found.get("security-group", [])
        if not recorded(ids, "sg", g, name=sgs.get(g, ""))
    ]
    for n in found.get("iam-role", []):
        try:
            rid = iam.get_role(RoleName=n)["Role"].get("RoleId", "")
        except iam.exceptions.NoSuchEntityException:
            continue
        if not recorded(ids, "role", rid, name=n):
            bad.append(f"IAM role {n}")
        prof = instance_profile(iam, n)
        if prof is not None and profile_is_ours(prof, n, names):
            if not recorded(ids, "profile", prof.get("InstanceProfileId", ""), name=n):
                bad.append(f"IAM instance profile {n}")
    bad += [f"SSM parameter {p}" for p in found.get("ssm-parameter", []) if not recorded(ids, "param", p, name=p)]
    if bad:
        raise MissingInput(
            by_hand(", ".join(bad)).replace("refusing to use, change or delete it", "nothing was deleted")
        )


def destroy(
    ec2,
    iam,
    ssm,
    found,
    say,
    sleep,
    names: Names,
    name: str,
    ids: dict,
    dns_done: bool = False,
    save: Callable[[], None] = lambda: None,
) -> None:
    """Deletes what aws.discover found, each resource read again right before its delete, in order, and deleted only
    while it carries the install's tags and is the one the install recorded (ids); anything else is left alone and
    named."""

    def left(what: str, why: str = "is no longer this install's (its tags or id changed since it was listed)") -> None:
        say(f"  left alone: {what} {why}.")

    def still(kind: str, rid: str, rows: list[dict], key: str) -> dict | None:
        """The row for rid, read just now, while it carries the install's tags and is the one recorded."""
        row = next((r for r in rows if r.get(key) == rid), None)
        ok = (
            row is not None
            and names.is_ours(row.get("Tags"), name)
            and recorded(ids, kind, rid, row.get("ClientToken", ""), row_name(row))
        )
        return row if ok else None

    live = ["pending", "running", "stopping", "stopped"]
    flt = names.filters(name)
    refuse_unrecorded(ec2, iam, found, names, name, ids)
    insts = []
    if found.get("instance"):
        r = ec2.describe_instances(Filters=flt + [{"Name": "instance-state-name", "Values": live}])
        rows = [i for res in r["Reservations"] for i in res["Instances"]]
        for i in found["instance"]:
            (insts.append(i) if still("instance", i, rows, "InstanceId") else left(f"instance {i}"))
    if insts:
        refuse_foreign_attached(ec2, insts, name, names, ids)
        for i in insts:
            deleting(ids, "instance", i, save)
        ec2.terminate_instances(InstanceIds=insts)
        say("  waiting for the instance to terminate ...")
        ec2.get_waiter("instance_terminated").wait(InstanceIds=insts)
    for a in found.get("elastic-ip", []):
        row = still("eip", a, ec2.describe_addresses(Filters=flt)["Addresses"], "AllocationId")
        if not row:
            left(f"Elastic IP {a}")
        elif row.get("InstanceId") and row["InstanceId"] not in insts:
            left(f"Elastic IP {a}", f"is attached to {row['InstanceId']}, which is not this install's server")
        else:
            at = association(ec2, a, sleep if row.get("InstanceId") else None)
            if at is None:
                left(f"Elastic IP {a}", "is gone already")
            elif at:
                left(
                    f"Elastic IP {a}",
                    f"is still associated ({at}), and releasing it would detach it from that; release it by hand in "
                    "the EC2 console (Elastic IPs) once you have checked nothing there needs it",
                )
            else:
                # EC2 has no conditional release: an association made in the instant after this read is not caught.
                deleting(ids, "eip", a, save)
                ec2.release_address(AllocationId=a)
    for g in found.get("security-group", []):
        if still("sg", g, ec2.describe_security_groups(Filters=flt)["SecurityGroups"], "GroupId"):
            deleting(ids, "sg", g, save)
            _delete_sg(ec2, g, sleep)
        else:
            left(f"security group {g}")

    def profile_now(n: str) -> tuple[dict | None, bool]:
        """(the profile read just now, whether it is this install's: its tags and recorded id)."""
        prof = instance_profile(iam, n)
        pid = (prof or {}).get("InstanceProfileId", "")
        return prof, prof is not None and profile_is_ours(prof, n, names) and recorded(ids, "profile", pid, name=n)

    for n in found.get("iam-role", []):
        prof, ours = profile_now(n)
        if prof is not None and not ours:
            left(f"IAM instance profile {n}", f"was not made by this install; the role {n} stays with it")
            continue
        try:
            if prof and any(r.get("RoleName") == n for r in prof.get("Roles") or []):
                iam.remove_role_from_instance_profile(InstanceProfileName=n, RoleName=n)
                prof, ours = profile_now(n)  # IAM has no conditional delete: read again right before it
                if prof is not None and not ours:
                    left(f"IAM instance profile {n}", f"was replaced while its role was removed; the role {n} stays")
                    continue
            if prof:
                deleting(ids, "profile", prof.get("InstanceProfileId", ""), save)
                iam.delete_instance_profile(InstanceProfileName=n)
        except iam.exceptions.NoSuchEntityException:
            pass
        steps = (
            (iam.detach_role_policy, {"RoleName": n, "PolicyArn": SSM_CORE}),
            (iam.delete_role_policy, {"RoleName": n, "PolicyName": names.role_policy}),
            (iam.delete_role, {"RoleName": n}),
        )
        for call, kw in steps:
            # IAM has no conditional delete: the role is read again, by its tags and recorded RoleId, right before each.
            try:
                role = iam.get_role(RoleName=n)["Role"]
            except iam.exceptions.NoSuchEntityException:
                break
            if not (names.is_ours(role.get("Tags"), name) and recorded(ids, "role", role.get("RoleId", ""), name=n)):
                left(f"IAM role {n}")
                break
            if kw.keys() == {"RoleName"}:  # the role itself
                deleting(ids, "role", role.get("RoleId", ""), save)
            try:
                call(**kw)
            except iam.exceptions.NoSuchEntityException:  # an interrupted install: the policy was never put or attached
                pass
    for p in found.get("ssm-parameter", []):
        try:  # read again right before the delete: a parameter replaced since it was listed is left alone
            tags = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=p)["TagList"]
        except ssm.exceptions.InvalidResourceId:
            continue
        if names.is_ours(tags, name) and recorded(ids, "param", p, name=p):
            deleting(ids, "param", p, save)
            ssm.delete_parameter(Name=p)
        else:
            left(f"SSM parameter {p}")
    say("  done." if dns_done else "  done. DNS records were not touched; remove them at your DNS provider.")


def create_s3(
    s3,
    iam,
    *,
    domain: str,
    region: str,
    account_id: str,
    name: str,
    say: Callable[[str], None] = print,
    names: Names,
    ids: dict | None = None,
    save: Callable[[], None] = lambda: None,
    sleep: Callable[[float], None] = time.sleep,
    record: str = "this install's state.json",
) -> dict[str, str]:
    """Returns the .env values. `ids`/`save`: the journal of what it makes (bucket, the backup user's UserId), saved as each
    is made. Safe to re-run: an existing bucket is kept and a fresh key replaces the oldest.
    Only a bucket (in this account) and a user that carry both of this install's tags are reused; anything else of the
    same name is refused before the first write."""
    bucket = names.backup_bucket(domain, account_id)
    owned = {"Bucket": bucket, "ExpectedBucketOwner": account_id}
    pick = "--domain or another --asset-prefix"
    try:
        proof = bucket_proof(s3, owned, names, name, pick)
    except MissingInput:
        raise
    except Exception as e:
        raise RuntimeError(f"cannot use bucket {bucket}: {error_code(e) or e}") from e
    user = names.backup_user(name)
    try:
        user_id = ((iam.get_user(UserName=user) or {}).get("User") or {}).get("UserId", "")
        user_exists = True
    except iam.exceptions.NoSuchEntityException:
        user_id, user_exists = "", False
    j = Journal(ids if ids is not None else {}, save, names, name, record)
    if not user_exists and j.ids.get("backup_user"):  # recorded: only slow to show (IAM lag), or gone and refused
        try:
            read = lambda: ((iam.get_user(UserName=user) or {}).get("User") or {}).get("UserId", "")
            user_id, user_exists = settled(read, sleep, iam.exceptions.NoSuchEntityException), True
        except iam.exceptions.NoSuchEntityException:
            j.missing("backup_user", "IAM user", user)
    if user_exists and not names.is_ours(iam.list_user_tags(UserName=user).get("Tags"), name):
        raise not_ours("IAM user", user, names, name)
    if user_exists:
        j.adopt("backup_user", user_id, "IAM user", user)
    made(j, "backup_bucket", s3, owned, region, proof, pick)
    say(f"  created bucket {bucket}" if proof == "absent" else f"  bucket {bucket} exists; keeping it")

    def prove_bucket() -> None:
        # S3 bucket settings have no conditional writes: the bucket is read again (its tags, in this account, and the
        # name this install recorded making) right before each one, so one replaced after an earlier write gets no more.
        try:
            tags = bucket_tags(s3, owned)
        except Exception as e:
            if error_code(e) not in ("404", "NoSuchBucket", "NotFound"):
                raise
            tags = []
        if not (names.is_ours(tags, name) and recorded(j.ids, "backup_bucket", bucket, name=bucket)):
            raise not_ours("S3 bucket", bucket, names, name, pick)

    prove_bucket()
    s3.put_public_access_block(
        **owned,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    prove_bucket()
    s3.put_bucket_versioning(**owned, VersioningConfiguration={"Status": "Enabled"})
    prove_bucket()
    s3.put_bucket_encryption(
        **owned,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    prove_bucket()
    s3.put_bucket_lifecycle_configuration(
        **owned,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": names.backup_rule,
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": bk.NONCURRENT_DAYS},
                    "Expiration": {"ExpiredObjectDeleteMarker": True},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                },
                {
                    # Litestream keeps one folder per compaction level (0000-0009) at the top of the replica.
                    "ID": "litestream",
                    "Status": "Enabled",
                    "Filter": {"Prefix": f"{names.backup_key_prefix}/000"},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": bk.LITESTREAM_NONCURRENT_DAYS},
                },
            ]
        },
    )
    prove_bucket()
    s3.put_bucket_policy(
        **owned,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "TLSOnly",
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                    }
                ],
            }
        ),
    )

    made_here = not user_exists
    if not user_exists:
        j.intend("backup_user", name=user)
        try:
            new = iam.create_user(UserName=user, Tags=names.tags(name))
        except iam.exceptions.EntityAlreadyExistsException:  # made by someone else since the read above
            raise not_ours("IAM user", user, names, name) from None
        user_id = ((new or {}).get("User") or {}).get("UserId", "")
        j.made("backup_user", user_id)
        say(f"  created IAM user {user}")

    def prove_user() -> None:
        # IAM has no compare-and-swap: the user is re-read by UserId right before each write; one replaced in the
        # instant between that read and the write is the residual window.
        def read() -> tuple[dict, list | None]:
            return (iam.get_user(UserName=user) or {}).get("User") or {}, iam.list_user_tags(UserName=user).get("Tags")

        try:  # a user this run just made may not be readable yet (IAM lag)
            now, tags = settled(read, sleep, iam.exceptions.NoSuchEntityException) if made_here else read()
        except iam.exceptions.NoSuchEntityException:
            raise not_ours("IAM user", user, names, name) from None
        if not names.is_ours(tags, name) or (user_id and now.get("UserId") != user_id):
            raise not_ours("IAM user", user, names, name)

    prove_user()
    iam.put_user_policy(UserName=user, PolicyName=names.backup_policy, PolicyDocument=json.dumps(bk.policy(bucket)))
    prove_user()
    keys = sorted(iam.list_access_keys(UserName=user)["AccessKeyMetadata"], key=lambda k: k["CreateDate"])
    if len(keys) >= 2:  # IAM allows two; a secret cannot be read back, so a re-run makes a new one
        prove_user()
        # Only a key this install recorded making (its id, from the create call's answer) is ever deleted.
        mine = [k["AccessKeyId"] for k in keys if k["AccessKeyId"] in (j.ids.get("backup_keys") or [])]
        if not mine:
            raise MissingInput(
                f"IAM user {user} has two access keys ({', '.join(k['AccessKeyId'] for k in keys)}) and this install "
                "recorded making neither; refusing to delete one. Delete the one no longer in use by hand in the IAM "
                "console, then run setup again."
            )
        iam.delete_access_key(UserName=user, AccessKeyId=mine[0])
        j.ids["backup_keys"] = [k for k in j.ids["backup_keys"] if k != mine[0]]
        save()
    prove_user()
    key = iam.create_access_key(UserName=user)["AccessKey"]
    j.ids.setdefault("backup_keys", []).append(key["AccessKeyId"])
    save()
    say(f"  created an access key for {user}")
    return {
        "TICO_BACKUP_URL": f"s3://{bucket}/{names.backup_key_prefix}",
        "TICO_BACKUP_REGION": region,
        "LITESTREAM_ACCESS_KEY_ID": key["AccessKeyId"],
        "LITESTREAM_SECRET_ACCESS_KEY": key["SecretAccessKey"],
    }
