"""`cell add/update --kms-key --kms-credentials-file`, `cell kms-policy` and `cell rewrap` (docs/cell.md, Credentials
and KMS), with a fake System: no Docker, no AWS. Every key here is synthetic."""

import json

import pytest

from setup import cell, envfile
from setup.cell import Result
from setup.tests.helpers.cell import (  # noqa: F401  host and ready are fixtures
    KMS_ARN,
    KMS_KEY_ID,
    KMS_SECRET,
    FakeSystem,
    add_args,
    host,
    kms_args,
    ready,
    record,
    secret_file,
)

BUCKET = "tico-hosting-backups"
BACKUP_KEY, BACKUP_SECRET = "AKIA" + "B" * 16, "bk/" + "x" * 37
OTHER_ARN = "arn:aws:kms:eu-central-1:111122223333:key/abcd1234-12ab-34cd-56ef-1234567890ab"


def env_of(host, slug="boatyard"):
    return envfile.parse((host.root / "companies" / slug / ".env").read_text())


def backup_args(host, slug="boatyard"):
    keys = secret_file(host.tmp, "backup", f"AWS_ACCESS_KEY_ID={BACKUP_KEY}\nAWS_SECRET_ACCESS_KEY={BACKUP_SECRET}")
    return ["--backup-url", f"s3://{BUCKET}/{slug}", "--backup-region", "us-west-2", "--backup-credentials-file", keys]


def drop_kms(host, slug="boatyard"):
    """A company made before KMS: its .env without the KMS lines."""
    path = host.root / "companies" / slug / ".env"
    values = {k: v for k, v in envfile.parse(path.read_text()).items() if k not in cell.KMS_ENV}
    path.write_text(envfile.render(values))


# --- add and update ------------------------------------------------------------------------------------------------


def test_a_new_company_without_kms_is_refused_naming_what_is_missing(ready):
    ready.system.calls.clear()
    answer = ready(*add_args(ready, kms=False))
    assert answer["error"] == "kms_required"
    assert "--kms-key" in answer["detail"] and "--kms-credentials-file" in answer["detail"]
    assert not (ready.root / "companies" / "boatyard").exists()
    answer = ready(*add_args(ready, "boatyard", "--kms-key", KMS_ARN, kms=False))
    assert answer["error"] == "kms_required" and "--kms-credentials-file" in answer["detail"]
    assert "--kms-key " not in answer["detail"] + " "
    assert not (ready.root / "companies" / "boatyard").exists()
    assert ready.system.calls == [], "refused before Docker or the firewall check"


def test_add_writes_the_kms_settings_and_never_prints_the_secret(ready):
    answer = ready(*add_args(ready))
    assert answer["ok"] and answer["kms"] is True and answer["kms_key"] == KMS_ARN and "warning" not in answer
    env = env_of(ready)
    assert env["TICO_CREDENTIAL_KMS_KEY"] == KMS_ARN
    assert env["TICO_CREDENTIAL_KMS_CONTEXT"] == "boatyard"
    assert env["TICO_CREDENTIAL_KMS_ACCESS_KEY_ID"] == KMS_KEY_ID
    assert env["TICO_CREDENTIAL_KMS_SECRET_ACCESS_KEY"] == KMS_SECRET
    assert env["TICO_CREDENTIAL_KMS_REGION"] == "us-west-2"
    assert KMS_SECRET not in json.dumps(answer) and KMS_SECRET not in ready.system.argv_text()
    # The same add again changes nothing; one without the KMS flags keeps them.
    assert ready(*add_args(ready))["changed"] is False
    again = ready(*add_args(ready, kms=False))
    assert again["changed"] is False and again["kms"] is True and env_of(ready) == env


def test_the_kms_credentials_file_takes_the_backup_key_formats_and_must_be_private(ready):
    as_json = json.dumps({"AccessKey": {"AccessKeyId": KMS_KEY_ID, "SecretAccessKey": KMS_SECRET}})
    args = add_args(
        ready,
        "boatyard",
        "--kms-key",
        KMS_ARN,
        "--kms-credentials-file",
        secret_file(ready.tmp, "j", as_json),
        kms=False,
    )
    assert ready(*args)["ok"] and env_of(ready)["TICO_CREDENTIAL_KMS_SECRET_ACCESS_KEY"] == KMS_SECRET
    loose = secret_file(
        ready.tmp, "loose", f"AWS_ACCESS_KEY_ID={KMS_KEY_ID}\nAWS_SECRET_ACCESS_KEY={KMS_SECRET}", 0o644
    )
    answer = ready(*add_args(ready, "nets", "--kms-key", KMS_ARN, "--kms-credentials-file", loose, kms=False))
    assert answer["error"] == "secret_file" and "--kms-credentials-file" in answer["detail"]
    empty = secret_file(ready.tmp, "empty", "AWS_ACCESS_KEY_ID=nope")
    answer = ready(*add_args(ready, "nets", "--kms-key", KMS_ARN, "--kms-credentials-file", empty, kms=False))
    assert answer["error"] == "secret_file" and "--kms-credentials-file" in answer["detail"]


@pytest.mark.parametrize(
    "arn",
    [
        "alias/tico",
        "1234abcd-12ab-34cd-56ef-1234567890ab",
        "arn:aws:kms:us-west-2:111122223333:alias/tico",
        "arn:aws:kms:us-west-2:1111:key/abc",
        "arn:aws:s3:::bucket",
    ],
)
def test_kms_key_is_a_key_arn(ready, arn):
    answer = ready(*add_args(ready, "boatyard", *kms_args(ready, arn), kms=False))
    assert answer["error"] == "bad_args" and "--kms-key" in answer["detail"]
    assert not (ready.root / "companies" / "boatyard").exists()


def test_init_saves_the_cells_kms_key_for_add(host):
    assert host("init", "--kms-key", KMS_ARN, "--json")["kms_key"] == KMS_ARN
    assert host("init", "--json")["kms_key"] == KMS_ARN  # kept when left out
    creds = kms_args(host)[3]
    assert host(*add_args(host, "boatyard", "--kms-credentials-file", creds, kms=False))["kms_key"] == KMS_ARN
    assert env_of(host)["TICO_CREDENTIAL_KMS_KEY"] == KMS_ARN
    assert "kms_key" not in host("init", "--kms-key", "", "--json")  # "" clears it
    assert host("init", "--kms-key", "alias/x", "--json")["error"] == "bad_args"


def test_an_existing_company_without_kms_converges_with_a_warning_then_update_sets_it(ready):
    assert ready(*add_args(ready))["ok"]
    drop_kms(ready)
    answer = ready(*add_args(ready, kms=False))
    assert answer["ok"] and answer["kms"] is False and "KMS is not set" in answer["warning"]
    assert not any(k in env_of(ready) for k in cell.KMS_ENV)
    answer = ready("update", "--company", "boatyard", "--version", "v1.2.3")
    assert answer["ok"] and answer["kms"] is False and answer["changed"] is False
    # Half of it is refused: a key needs the access key, and none is saved.
    answer = ready("update", "--company", "boatyard", "--version", "v1.2.3", "--kms-key", OTHER_ARN)
    assert answer["error"] == "kms_required" and "--kms-credentials-file" in answer["detail"]
    ready.system.calls.clear()
    answer = ready("update", "--company", "boatyard", "--version", "v1.2.3", *kms_args(ready, OTHER_ARN))
    assert answer["ok"] and answer["kms"] is True and answer["kms_key"] == OTHER_ARN and answer["changed"] is True
    assert KMS_SECRET not in json.dumps(answer)
    assert any("up" in c for c in ready.system.calls), "the running server picks up its new .env"
    env = env_of(ready)
    assert env["TICO_CREDENTIAL_KMS_REGION"] == "eu-central-1" and env["TICO_CREDENTIAL_KMS_CONTEXT"] == "boatyard"
    # Left out, the saved values stay, across a new release too; a new key alone keeps the saved access key.
    assert ready("update", "--company", "boatyard", "--version", "v1.2.3")["changed"] is False
    assert ready("update", "--company", "boatyard", "--version", "v1.2.4")["kms"] is True
    assert env_of(ready)["TICO_CREDENTIAL_KMS_SECRET_ACCESS_KEY"] == KMS_SECRET
    answer = ready("update", "--company", "boatyard", "--version", "v1.2.4", "--kms-key", KMS_ARN)
    assert answer["ok"] and env_of(ready)["TICO_CREDENTIAL_KMS_KEY"] == KMS_ARN
    assert env_of(ready)["TICO_CREDENTIAL_KMS_ACCESS_KEY_ID"] == KMS_KEY_ID


def test_an_env_file_cannot_set_the_kms_settings(ready):
    carried = secret_file(
        ready.tmp,
        "carried",
        "TICO_CREDENTIAL_KMS_KEY=arn:aws:kms:us-east-1:999999999999:key/evil\n"
        "TICO_CREDENTIAL_KMS_CONTEXT=someone-else\nTICO_CREDENTIAL_KMS_REGION=us-east-1\nTICO_RUNNER_URL=https://r.example",
    )
    answer = ready(*add_args(ready, "boatyard", "--env-file", carried))
    assert {"TICO_CREDENTIAL_KMS_KEY", "TICO_CREDENTIAL_KMS_CONTEXT", "TICO_CREDENTIAL_KMS_REGION"} <= set(
        answer["ignored"]
    )
    env = env_of(ready)
    assert env["TICO_CREDENTIAL_KMS_KEY"] == KMS_ARN and env["TICO_CREDENTIAL_KMS_CONTEXT"] == "boatyard"
    assert env["TICO_RUNNER_URL"] == "https://r.example"


# --- kms-policy ----------------------------------------------------------------------------------------------------


def allows(policy, action, resource, context):
    """IAM's answer for one KMS call, for the policy shapes kms-policy writes: an Allow statement whose action and
    resource match, and whose StringEquals conditions on kms:EncryptionContext:<k> all equal the call's context."""
    for st in policy["Statement"]:
        actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        if st["Effect"] != "Allow" or action not in actions or st["Resource"] != resource:
            continue
        conditions = st.get("Condition", {})
        assert set(conditions) <= {"StringEquals"}, "only the condition this evaluator reads"
        wanted = conditions.get("StringEquals", {})
        prefix = "kms:EncryptionContext:"
        assert all(k.startswith(prefix) for k in wanted)
        if all(context.get(k[len(prefix) :]) == v for k, v in wanted.items()):
            return True
    return False


def test_kms_policy_is_the_company_own_context_on_the_cells_key(host):
    answer = host("kms-policy", "--company", "boatyard", "--kms-key", KMS_ARN, "--json")
    assert answer["ok"] and answer["company"] == "boatyard" and answer["kms_key"] == KMS_ARN
    (statement,) = answer["policy"]["Statement"]
    assert statement["Effect"] == "Allow" and statement["Resource"] == KMS_ARN
    assert sorted(statement["Action"]) == ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    assert statement["Condition"] == {
        "StringEquals": {
            "kms:EncryptionContext:company": "boatyard",
            "kms:EncryptionContext:application": "tico-credentials",
        }
    }


def test_company_a_policy_cannot_use_company_b_context(host):
    policy = host("kms-policy", "--company", "alpha", "--kms-key", KMS_ARN)["policy"]
    own = {"application": "tico-credentials", "company": "alpha"}
    for action in ("kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"):
        assert allows(policy, action, KMS_ARN, own)
        assert not allows(policy, action, KMS_ARN, {**own, "company": "bravo"})
        assert not allows(policy, action, KMS_ARN, {"application": "tico-credentials"})  # self-hosted's context
        assert not allows(policy, action, OTHER_ARN, own)
    assert not allows(policy, "kms:ReEncryptFrom", KMS_ARN, own)


def test_kms_policy_needs_a_key(host):
    assert host("kms-policy", "--company", "boatyard")["error"] == "not_initialized"
    host("init")
    assert host("kms-policy", "--company", "boatyard")["error"] == "bad_args"
    assert host("kms-policy", "--company", "Bad_Slug", "--kms-key", KMS_ARN)["error"] == "bad_slug"
    host("init", "--kms-key", KMS_ARN)
    assert host("kms-policy", "--company", "boatyard")["kms_key"] == KMS_ARN


# --- rewrap ----------------------------------------------------------------------------------------------------------


class S3System(FakeSystem):
    """FakeSystem with the host role's S3 calls and the backend step: `step` is (exit code, its JSON answer)."""

    def setup_s3(self):
        self.blocked = (True, "")
        self.s3_calls = []
        self.step = (0, {"status": "done", "resealed": {"api_key": 2}, "failed": {}, "deleted": ["credential.key"]})
        self.copies = 3
        self.s3_error = None

    def run(self, argv, *, input=None, timeout=900):
        if "vault-rotate" in argv:
            self.calls.append(list(argv))
            code, answer = self.step
            return Result(code, "tico: starting\n" + json.dumps(answer) + "\n")
        return super().run(argv, input=input, timeout=timeout)

    def public_access_blocked(self, bucket, region):
        self.s3_calls.append(("public_access_blocked", bucket, region))
        if self.s3_error:
            raise self.s3_error
        return self.blocked

    def delete_key_copies(self, bucket, prefix, region):
        self.s3_calls.append(("delete_key_copies", bucket, prefix, region))
        n, self.copies = self.copies, 0
        return n


def use_s3(host):
    host.system.__class__ = S3System  # the fixture's FakeSystem, with S3 and the step on top
    host.system.setup_s3()


@pytest.fixture
def company(ready):
    """A running company with a backup bucket and KMS settings, on the S3 fake."""
    use_s3(ready)
    assert ready(*add_args(ready, "boatyard", *backup_args(ready)))["ok"]
    ready.system.calls.clear()
    return ready


def step_index(calls):
    return next(i for i, c in enumerate(calls) if "vault-rotate" in c)


def test_rewrap_stops_before_anything_when_the_bucket_is_not_private(company):
    company.system.blocked = (False, f"bucket {BUCKET} does not block public access: BlockPublicPolicy off")
    answer = company("rewrap", "--company", "boatyard", "--json")
    assert answer["error"] == "public_access" and BUCKET in answer["detail"]
    assert company.system.s3_calls == [("public_access_blocked", BUCKET, "us-west-2")]
    assert not any("vault-rotate" in c or "stop" in c for c in company.system.calls)
    assert company.system.ps["boatyard"][0] == "running"


def test_rewrap_refuses_a_company_without_kms(company):
    drop_kms(company)
    answer = company("rewrap", "--company", "boatyard")
    assert answer["error"] == "kms_required" and "--kms-key" in answer["detail"]
    assert not any("vault-rotate" in c or "stop" in c for c in company.system.calls)


def test_rewrap_runs_the_step_on_the_stopped_server_then_deletes_the_key_copies(company):
    answer = company("rewrap", "--company", "boatyard", "--json")
    assert answer == {
        "ok": True,
        "company": "boatyard",
        "public_access_blocked": True,
        "step": "done",
        "resealed": {"api_key": 2},
        "deleted": ["credential.key"],
        "deleted_key_copies": 3,
        "state": "running",
        "ready_ms": answer["ready_ms"],
    }
    calls = company.system.calls
    i = step_index(calls)
    step = calls[i]
    assert step[:2] == ["docker", "compose"] and "tico-boatyard" in step
    tail = step[step.index("run") :]
    assert tail == [
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "-e",
        "TICO_DB=/data/hub.sqlite",
        "--entrypoint",
        "python",
        "server",
        "-m",
        "backend.manage",
        "vault-rotate",
        "--json",
    ]
    assert any("stop" in c for c in calls[:i]), "the server is stopped before the step"
    assert any("up" in c for c in calls[i:]), "and started again after it"
    assert company.system.ps["boatyard"][0] == "running"
    assert company.system.s3_calls == [
        ("public_access_blocked", BUCKET, "us-west-2"),
        ("delete_key_copies", BUCKET, "boatyard", "us-west-2"),
    ]
    printed = json.dumps(answer) + company.system.argv_text()
    assert KMS_SECRET not in printed and BACKUP_SECRET not in printed
    # Again: the step finds its work done and nothing is left to delete.
    company.system.step = (0, {"status": "already_done", "resealed": {}, "failed": {}, "deleted": []})
    again = company("rewrap", "--company", "boatyard")
    assert again["ok"] and again["step"] == "already_done" and again["deleted_key_copies"] == 0


def test_a_stopped_step_deletes_nothing_and_the_company_runs_again(company):
    company.system.step = (
        3,
        {"status": "stopped", "reason": "2 secrets do not open under KMS", "failed": {"api_key": 1, "slack": 1}},
    )
    answer = company("rewrap", "--company", "boatyard")
    assert answer["ok"] is False and answer["error"] == "rewrap_failed"
    assert answer["failed"] == {"api_key": 1, "slack": 1} and answer["step"] == "stopped" and answer["exit"] == 3
    assert "api_key: 1" in answer["detail"] and "nothing was deleted" in answer["detail"]
    assert [c[0] for c in company.system.s3_calls] == ["public_access_blocked"]
    calls = company.system.calls
    assert any("up" in c for c in calls[step_index(calls) :]), "started again although the step failed"
    assert company.system.ps["boatyard"][0] == "running"


def test_a_step_with_no_answer_or_a_config_error_deletes_nothing(company):
    company.system.step = (0, {})
    assert company("rewrap", "--company", "boatyard")["error"] == "rewrap_failed"
    company.system.step = (2, {"status": "error", "reason": "TICO_CREDENTIAL_KMS_KEY is not set"})
    answer = company("rewrap", "--company", "boatyard")
    assert answer["error"] == "rewrap_failed" and "not set" in answer["detail"]
    assert not any(c[0] == "delete_key_copies" for c in company.system.s3_calls)


def test_an_asleep_company_is_not_started_after_the_step(company):
    assert company("sleep", "--company", "boatyard")["ok"]
    company.system.calls.clear()
    answer = company("rewrap", "--company", "boatyard")
    assert answer["ok"] and answer["state"] == "sleeping" and answer["ready_ms"] == 0
    calls = company.system.calls
    assert not any("up" in c for c in calls[step_index(calls) :])
    assert company.system.ps["boatyard"][0] != "running" and record(company)["state"] == "sleeping"
    assert company.system.s3_calls[-1][0] == "delete_key_copies"


def test_an_s3_error_is_s3_failed_and_stops_the_run(company):
    company.system.s3_error = RuntimeError("AccessDenied: GetBucketPublicAccessBlock")
    answer = company("rewrap", "--company", "boatyard")
    assert answer["error"] == "s3_failed" and "AccessDenied" in answer["detail"]
    assert not any("vault-rotate" in c for c in company.system.calls)


def test_rewrap_without_a_backup_url_touches_no_bucket(ready):
    use_s3(ready)
    assert ready(*add_args(ready))["ok"]
    answer = ready("rewrap", "--company", "boatyard")
    assert answer["ok"] and answer["public_access_blocked"] is None and answer["deleted_key_copies"] == 0
    assert ready.system.s3_calls == []
