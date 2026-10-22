"""A selected team survives the same private installer path on AWS and self-hosted servers."""
import argparse

import pytest

from backend import company_file as C
from backend.company_file import starter
from setup import aws, envfile, settings, wizard
from setup.tests.fakes import aws_clients
from setup.ui import MissingInput


def chart(seats=20):
    """A first chart cut to `seats` bots. Every company model is over the 8 KB parameter the installer packs a plan
    into today; carrying a whole model chart waits on the plan-bucket transport (hosted signup, Org chart models)."""
    draft = C.Turn(C.blank()).finish()
    draft["bots"] = draft["bots"][:seats]
    return draft


def test_company_file_reaches_private_env_and_secure_parameter_without_public_state_or_sample(tmp_path):
    draft = chart()
    draft["company"]["name"] = "Acme Books"
    path = tmp_path / "tico.company.yaml"
    path.write_text(C.to_yaml(draft))
    args = argparse.Namespace(company_file=str(path), company=None)
    s = settings.Settings(preset="recommended")
    wizard.gather_company_file(args, s)
    assert s.company == "Acme Books"
    public = s.public()
    assert "company_file_data" not in public
    rendered = envfile.render(s.to_env())
    packed = envfile.parse(rendered)["TICO_COMPANY_FILE_B64"]
    assert packed not in repr(s) and packed not in envfile.redact_env(rendered)
    assert "TICO_SAMPLE_COMPANY=off" in rendered
    resumed = settings.from_saved(public, rendered)
    wizard.gather_company_file(argparse.Namespace(company_file=None, company=None), resumed)
    assert resumed.company_file_data == packed
    assert starter.unpack(packed)["bots"] == starter.load(path.read_text())["bots"]
    assert starter.load(starter.file_text(starter.unpack(packed))) == starter.unpack(packed)
    ec2, iam, ssm, account = aws_clients()
    deployer = aws.Deployer(aws.plan_aws(domain="tico.example.com", front_door="caddy", region="us-east-1"),
                            ec2, iam, ssm, account, say=lambda _: None, sleep=lambda _: None)
    deployer.put_env(rendered, company_file=True)
    written = ssm.called("put_parameter")[0]
    assert written["Type"] == "SecureString" and written["Tier"] == "Advanced"
    assert written["Value"] == rendered
    args.company = "Different Company"
    with pytest.raises(MissingInput, match="must match"):
        wizard.gather_company_file(args, s)


def test_invalid_or_oversize_transport_fails_before_cloud_changes():
    import base64
    import gzip
    for data in ("invalid", base64.b64encode(gzip.compress(b"x" * (starter.MAX_BYTES + 1))).decode()):
        with pytest.raises(C.DraftError):
            starter.unpack(data)
    ec2, iam, ssm, account = aws_clients()
    deployer = aws.Deployer(aws.plan_aws(domain="tico.example.com", front_door="caddy", region="us-east-1"),
                            ec2, iam, ssm, account, say=lambda _: None, sleep=lambda _: None)
    with pytest.raises(SystemExit, match="8 KB") as refused:
        deployer.put_env("x" * 8193, company_file=True)
    assert str(refused.value).endswith(starter.IMPORT_IN_APP)
    assert not ssm.called("put_parameter")


def test_a_whole_model_chart_is_refused_plainly_before_cloud_changes_until_the_plan_bucket_lands(tmp_path):
    draft = C.Turn(C.blank()).finish()
    draft["company"]["name"] = "Acme Books"
    path = tmp_path / "tico.company.yaml"
    path.write_text(C.to_yaml(draft))
    with pytest.raises(MissingInput, match="without --company-file.*Already chose your team.*Use this team"):
        wizard.gather_company_file(argparse.Namespace(company_file=str(path), company=None),
                                   settings.Settings(preset="recommended"))


def test_hosted_cli_carries_selected_file_through_machine_and_dns_setup(tmp_path, monkeypatch):
    from setup import verify
    from setup.tests.test_preview_flags import CREATE, go, world
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "test-client-private-value")
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])
    draft = chart()
    draft["company"]["name"] = "Acme Finance"
    path = tmp_path / "tico.company.yaml"
    path.write_text(C.to_yaml(draft))
    deps, ec2, iam, ssm, r53 = world()
    code, out = go([*CREATE, "--company-file", str(path)], deps)
    assert code == 0, out
    param = ssm.called("put_parameter")[0]
    env = envfile.parse(param["Value"])
    assert env["TICO_TEAM_NAME"] == "Acme Finance" and env["TICO_SAMPLE_COMPANY"] == "off"
    assert starter.unpack(env["TICO_COMPANY_FILE_B64"])["company"]["name"] == "Acme Finance"
    assert env["TICO_COMPANY_FILE_B64"] not in out + ec2.called("run_instances")[0]["UserData"]
    assert r53.called("change_resource_record_sets")
