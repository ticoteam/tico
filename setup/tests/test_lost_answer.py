"""Strict mode after a create call's answer is lost: a re-run never makes a second server or Elastic IP."""

from __future__ import annotations

import types

import pytest
from botocore.exceptions import ClientError

from setup import aws, owned
from setup.tests import fakes
from setup.ui import MissingInput

ACCOUNT, INSTALL = "123456789012", "0123456789abcdef"
USER_DATA = "#!/bin/bash\n"


def plan(**kw):
    base = dict(domain="hub.example.com", front_door="caddy", region="us-west-2", name="hotmic", prefix="hub")
    return aws.plan_aws(**{**base, "install_id": INSTALL, **kw})


def dep(p, ec2, ids, ssm=None):
    iam = getattr(ec2, "iam", None) or fakes.FakeIAM()  # one IAM per world: a re-run finds the role it made
    return owned.StrictDeployer(p, ec2, iam, ssm or fakes.FakeSSM(), ACCOUNT, sleep=lambda s: None, ids=ids)


def client_error(code, op, status=400, retries=0):
    meta = {"HTTPStatusCode": status, "RetryAttempts": retries}
    return ClientError({"Error": {"Code": code, "Message": code}, "ResponseMetadata": meta}, op)


class ZonedEC2(fakes.FakeEC2):
    """Two public subnets in two zones. As EC2 does, a ClientToken is idempotent only within the zone of the subnet the
    launch names: the same token in another zone launches another server."""

    def __init__(self, order=("subnet-b", "subnet-a")):
        super().__init__()
        self.all = {
            "subnet-a": {"SubnetId": "subnet-a", "AvailabilityZone": "us-west-2a", "DefaultForAz": True},
            "subnet-b": {"SubnetId": "subnet-b", "AvailabilityZone": "us-west-2b", "DefaultForAz": True},
        }
        self.order, self.iam = list(order), fakes.FakeIAM()

    def describe_subnets(self, Filters=(), SubnetIds=()):
        if SubnetIds:
            if not all(s in self.all for s in SubnetIds):
                raise client_error("InvalidSubnetID.NotFound", "DescribeSubnets")
            return {"Subnets": [self.all[s] for s in SubnetIds]}
        return {"Subnets": [self.all[s] for s in self.order if s in self.all]}

    def zone(self, subnet):
        return (self.all.get(subnet) or {}).get("AvailabilityZone", "default-zone")

    def run_instances(self, **kw):
        token, zone, n = kw.get("ClientToken"), self.zone(kw.get("SubnetId", "")), len(self.instances)
        elsewhere = [i for i in self.instances if token and i.get("ClientToken") == token and i["zone"] != zone]
        for i in elsewhere:  # another zone's: not seen by this launch
            i["ClientToken"] = ""
        out = super().run_instances(**kw)
        if len(self.instances) > n:
            self.instances[-1]["zone"] = zone
        for i in elsewhere:
            i["ClientToken"] = token
        return out


def lost_launch(p, ec2, ssm=None):
    """A first run whose RunInstances landed but whose answer never came back: the ids it saved."""
    ids, real = {}, ec2.run_instances

    def lost(**kw):
        real(**kw)
        raise ConnectionError("read timeout")

    ec2.run_instances = lost
    first = dep(p, ec2, ids, ssm)
    with pytest.raises(ConnectionError):
        first.launch_or_reuse(first.prepare(), USER_DATA)
    ec2.run_instances = real
    ec2.describe_instances = lambda **kw: {"Reservations": [{"Instances": []}]}  # the listing lags behind
    return ids


# --- finding 1: the launch request is journalled with its token, and a re-run sends exactly it ------------------------


def test_a_lost_launch_answer_rerun_with_the_subnets_listed_in_another_order_launches_no_second_server():
    p, ec2 = plan(), ZonedEC2(order=("subnet-b", "subnet-a"))
    ids = lost_launch(p, ec2)
    ec2.order = ["subnet-a", "subnet-b"]  # listed in another order on the re-run
    again = dep(p, ec2, ids)
    iid, _ = again.launch_or_reuse(again.prepare(), USER_DATA)
    sent = ec2.called("run_instances")
    assert len(ec2.instances) == 1 and iid == ec2.instances[0]["InstanceId"]
    assert len(sent) == 2 and sent[0]["SubnetId"] == sent[1]["SubnetId"]
    assert sent[0]["ClientToken"] == sent[1]["ClientToken"]


def test_a_rerun_sends_the_journalled_image_type_group_and_profile_not_ones_read_afresh():
    p, ec2, ssm = plan(), ZonedEC2(), fakes.FakeSSM()
    ids = lost_launch(p, ec2, ssm)
    ssm.get_parameter = lambda Name: {"Parameter": {"Value": "ami-newer"}}  # a newer image published meanwhile
    again = dep(plan(instance_type="t4g.large"), ec2, ids, ssm)
    again.launch(USER_DATA, "hub-other", "sg-other", "subnet-b")
    first, second = ec2.called("run_instances")
    for k in ("ImageId", "InstanceType", "SubnetId", "SecurityGroupIds", "IamInstanceProfile", "ClientToken"):
        assert first[k] == second[k], k
    assert len(ec2.instances) == 1


def test_a_journalled_subnet_that_no_longer_exists_stops_the_rerun_rather_than_choosing_another():
    p, ec2 = plan(), ZonedEC2()
    ids = lost_launch(p, ec2)
    gone = ec2.called("run_instances")[0]["SubnetId"]
    del ec2.all[gone]
    again = dep(p, ec2, ids)
    with pytest.raises(MissingInput, match=f"{gone}.*no longer exists"):
        again.launch_or_reuse(again.prepare(), USER_DATA)
    assert len(ec2.called("run_instances")) == 1 and len(ec2.instances) == 1


def test_a_launch_intent_saved_without_its_request_is_refused_not_relaunched_elsewhere():
    p, ec2 = plan(), ZonedEC2()
    ids = {"instance_token": "t" * 32, "intents": {"instance": {"name": "hub-hotmic", "token": "t" * 32}}}
    with pytest.raises(MissingInput, match="never heard back"):
        dep(p, ec2, ids).launch(USER_DATA, "hub-hotmic", "sg-1", "subnet-a")
    assert not ec2.called("run_instances")


def test_strict_mode_picks_its_subnet_by_zone_then_id_whatever_order_ec2_lists_them():
    for order in (("subnet-b", "subnet-a"), ("subnet-a", "subnet-b")):
        assert dep(plan(), ZonedEC2(order=order), {}).public_subnet() == "subnet-a"


def test_a_server_with_no_inbound_ports_is_launched_into_a_named_subnet_too():
    p, ec2 = plan(front_door="cloudflared"), ZonedEC2()
    assert not p.ports
    d = dep(p, ec2, {})
    d.launch_or_reuse(d.prepare(), USER_DATA)
    assert ec2.called("run_instances")[0]["SubnetId"] == "subnet-a"


def test_the_default_prefix_still_takes_the_first_listed_default_subnet():
    p = aws.plan_aws(domain="hub.example.com", front_door="caddy", region="us-west-2", name="hotmic")
    d = aws.Deployer(p, ZonedEC2(order=("subnet-b", "subnet-a")), fakes.FakeIAM(), fakes.FakeSSM(), ACCOUNT)
    assert d.public_subnet() == "subnet-b"


# --- finding 2: only an error that proves the allocation made nothing closes its intent --------------------------------


def allocate_failing(err):
    ec2, ids = fakes.FakeEC2(), {}

    def fail(**kw):
        raise err

    ec2.allocate_address = fail
    with pytest.raises(type(err)):
        dep(plan(), ec2, ids).ensure_eip()
    return ids


@pytest.mark.parametrize(
    "err",
    [
        # A credential refresh while signing a retry: STS's own error, not the allocation's answer.
        client_error("ExpiredToken", "AssumeRole", status=403),
        # EC2's answer to a retried attempt: the first may have landed.
        client_error("AddressLimitExceeded", "AllocateAddress", retries=1),
        # EC2's answer, but not one that proves nothing was made.
        client_error("RequestExpired", "AllocateAddress"),
        client_error("InternalError", "AllocateAddress", status=500),
        ConnectionError("read timeout"),
    ],
)
def test_an_elastic_ip_intent_stays_open_unless_the_error_proves_nothing_was_allocated(err):
    assert (allocate_failing(err).get("intents") or {}).get("eip"), err


@pytest.mark.parametrize("code", ["AddressLimitExceeded", "UnauthorizedOperation", "InvalidParameterValue"])
def test_the_allocations_own_single_attempt_refusal_closes_its_intent(code):
    assert not (allocate_failing(client_error(code, "AllocateAddress")).get("intents") or {}).get("eip")


def test_strict_mode_allocates_an_elastic_ip_through_a_single_attempt_ec2_client():
    built = []

    class Once(fakes.FakeEC2):
        pass

    once = Once()

    def client(name, config=None):
        built.append((name, config))
        return once

    ec2 = fakes.FakeEC2()
    ec2.setup_session = types.SimpleNamespace(client=client)
    alloc, _ = dep(plan(), ec2, {}).ensure_eip()
    assert [n for n, _ in built] == ["ec2"] and built[0][1].retries["max_attempts"] == 1
    assert once.called("allocate_address") and not ec2.called("allocate_address")
    assert alloc == "eipalloc-1"


def test_make_clients_keeps_its_session_for_strict_mode_and_makes_no_other_call(monkeypatch):
    made = []

    class Session:
        def __init__(self, region_name=None, profile_name=None):
            pass

        def client(self, name, config=None):
            made.append((name, config))
            return types.SimpleNamespace(get_caller_identity=lambda: {"Account": ACCOUNT})

    import sys

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(Session=Session))
    ec2, *_ = aws.make_clients("us-west-2")
    assert isinstance(ec2.setup_session, Session) and made == [
        ("ec2", None),
        ("iam", None),
        ("ssm", None),
        ("sts", None),
    ]
