import json

import pytest

from setup import aws
from setup.tests.fakes import FakeEC2, FakeIAM, FakeSSM, aws_clients


def plan(front_door="caddy", **kw):
    return aws.plan_aws(domain="tico.example.com", front_door=front_door, region="us-east-1", **kw)


def deployer(p, ec2, iam, ssm):
    return aws.Deployer(p, ec2, iam, ssm, "123456789012", say=lambda m: None, sleep=lambda s: None)


def test_deploy_caddy_creates_tagged_locked_down_resources():
    ec2, iam, ssm, _ = aws_clients()
    d = deployer(plan("caddy"), ec2, iam, ssm)
    prep = d.prepare()
    iid, created = d.launch_or_reuse(prep, "#!/bin/bash\n")
    assert (iid, created, prep["public_ip"]) == ("i-1", True, "203.0.113.9")
    (run,) = ec2.called("run_instances")
    assert run["MetadataOptions"] == {"HttpTokens": "required", "HttpPutResponseHopLimit": 1, "HttpEndpoint": "enabled"}
    assert run["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] and run["InstanceType"] == "t4g.small"
    assert {"Key": "ManagedBy", "Value": "tico-setup"} in run["TagSpecifications"][0]["Tags"]
    assert {"Key": "tico-setup-name", "Value": "tico-example-com"} in run["TagSpecifications"][0]["Tags"]
    assert "KeyName" not in run  # SSM, not SSH
    (ing,) = ec2.called("authorize_security_group_ingress")
    ports = {p["FromPort"] for p in ing["IpPermissions"]}
    assert ports == {80, 443} and 22 not in ports
    assert ec2.called("associate_address")[0]["InstanceId"] == "i-1"
    assert json.loads(iam.called("put_role_policy")[0]["PolicyDocument"])["Statement"][0]["Resource"].endswith(":parameter/tico/tico-example-com/env")


def test_env_goes_to_a_secure_string_parameter_and_never_into_user_data():
    ec2, iam, ssm, _ = aws_clients()
    d = deployer(plan("caddy"), ec2, iam, ssm)
    d.put_env("TICO_OIDC_CLIENT_SECRET=topsecret\n")
    assert ssm.called("put_parameter")[0]["Type"] == "SecureString"
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\naws ssm get-parameter\n")
    assert "topsecret" not in ec2.called("run_instances")[0]["UserData"]


OURS = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": "tico-example-com"}]


def test_discover_and_destroy_touch_only_tagged_resources():
    stranger = {"InstanceId": "i-other", "Tags": [{"Key": "tico-setup-name", "Value": "tico-example-com"}]}
    mine = {"InstanceId": "i-mine", "Tags": OURS}
    ec2 = FakeEC2(extra_instances=[stranger, mine], extra_sgs=[{"GroupId": "sg-other", "Tags": []}, {"GroupId": "sg-mine", "Tags": OURS}])
    ec2.eips = [{"AllocationId": "eip-mine", "Tags": OURS}, {"AllocationId": "eip-other", "Tags": []}]
    iam, ssm = FakeIAM(role_tags=OURS), FakeSSM(param_tags=OURS)
    found = aws.discover(ec2, iam, ssm, "tico-example-com")
    assert found == {"instance": ["i-mine"], "elastic-ip": ["eip-mine"], "security-group": ["sg-mine"],
                     "iam-role": ["tico-tico-example-com"], "ssm-parameter": ["/tico/tico-example-com/env"]}
    aws.destroy(ec2, iam, ssm, found, say=lambda m: None, sleep=lambda s: None)
    assert ec2.called("terminate_instances") == [{"InstanceIds": ["i-mine"]}]
    assert ec2.called("release_address") == [{"AllocationId": "eip-mine"}]
    assert ec2.called("delete_security_group") == [{"GroupId": "sg-mine"}]
    assert ssm.called("delete_parameter") == [{"Name": "/tico/tico-example-com/env"}]
    assert iam.called("delete_role") == [{"RoleName": "tico-tico-example-com"}]


def test_existing_untagged_role_is_never_reused():
    iam = FakeIAM(role_tags=[])
    iam.create_role = lambda **kw: (_ for _ in ()).throw(iam.exceptions.EntityAlreadyExistsException())
    d = deployer(plan(), FakeEC2(), iam, FakeSSM())
    with pytest.raises(SystemExit, match="refusing to reuse"):
        d.ensure_role()


NAT = {"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "nat-1"}
IGW = {"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-1"}



@pytest.mark.parametrize("instance_type, arch, image", [
    ("t4g.medium", "arm64", "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"),
    ("m8i-flex.xlarge", "x86_64", "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"),
    ("m8i-flex.2xlarge", "x86_64", "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"),
])
def test_each_size_launches_on_an_image_for_its_cpu(instance_type, arch, image):
    """Hosting preview v6 (tico-services Dev 295): Starter t4g.medium, 4 cores m8i-flex.xlarge, 8 cores
    m8i-flex.2xlarge. The Intel sizes need the x86_64 image, never the arm64 one."""
    ec2, iam, ssm, _ = aws_clients()
    p = plan("caddy", instance_type=instance_type)
    d = deployer(p, ec2, iam, ssm)
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\n")
    (run,) = ec2.called("run_instances")
    assert run["InstanceType"] == instance_type and ssm.asked == [image]
    assert f"Ubuntu 24.04 {arch}," in p.lines()[0]


def test_amazon_linux_on_an_intel_size_uses_the_x86_64_image():
    ec2, iam, ssm, _ = aws_clients()
    d = deployer(plan("caddy", instance_type="m8i-flex.xlarge", os_family="al2023"), ec2, iam, ssm)
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\n")
    assert ssm.asked == ["/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"]


def test_the_intel_sizes_are_priced_and_any_other_size_is_refused():
    assert (aws.MEMORY_GIB["m8i-flex.xlarge"], aws.MEMORY_GIB["m8i-flex.2xlarge"]) == (16, 32)
    assert plan("caddy", instance_type="m8i-flex.2xlarge").monthly_usd > plan("caddy", instance_type="m8i-flex.xlarge").monthly_usd
    with pytest.raises(ValueError):
        plan("caddy", instance_type="m8i-flex.4xlarge")


def test_the_cli_takes_the_intel_sizes():
    from setup import cli
    parser = cli.parser()
    for size in ("m8i-flex.xlarge", "m8i-flex.2xlarge"):
        assert parser.parse_args(["--aws-instance-type", size]).aws_instance_type == size
    with pytest.raises(SystemExit):
        parser.parse_args(["--aws-instance-type", "m8i-flex.4xlarge"])
