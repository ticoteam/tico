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



def test_hosted_runner_sizes_use_matching_images_and_boundary_without_ingress():
    for size, arch in (('t4g.medium','arm64'),('m8i-flex.xlarge','amd64'),('m8i-flex.2xlarge','amd64')):
        ec2, iam, ssm, _ = aws_clients()
        p = aws.plan_aws(domain='https://acme.tico.dev', front_door='none', region='us-east-2',
            instance_type=size, name='pv-bot', role='runner', boundary='arn:aws:iam::123456789012:policy/boundary')
        d = deployer(p, ec2, iam, ssm)
        names = []
        original = ssm.get_parameter
        def get_parameter(**kw):
            names.append(kw['Name'])
            return original(**kw)
        ssm.get_parameter = get_parameter
        d.launch_or_reuse(d.prepare(), '#!/bin/bash\n')
        assert arch in names[0]
        assert iam.called('create_role')[0]['PermissionsBoundary'] == p.boundary
        assert ec2.called('run_instances')[0]['InstanceType'] == size
        assert not ec2.called('authorize_security_group_ingress')
