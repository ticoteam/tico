"""Helpers that other tests share, moved out of test_self_install_plan.py so they import no test file."""

from setup.tests.helpers.plan_bucket import ClientError
from setup.tests.helpers.preview_flags import CREATE


def self_args():
    args = list(CREATE)
    for flag in ("--hosted", "--hosted-zone", "--preset", "--aws-permissions-boundary"):
        i = args.index(flag)
        del args[i : i + 2]
    return args


class PlanS3:
    def __init__(self):
        self.calls, self.exists, self.tagset = [], False, []

    def head_bucket(self, **kw):
        self.calls.append(("head_bucket", kw))
        if not self.exists:
            raise ClientError("404")

    def create_bucket(self, **kw):
        self.calls.append(("create_bucket", kw))
        assert not self.exists
        self.exists = True

    def put_bucket_tagging(self, **kw):
        self.calls.append(("put_bucket_tagging", kw))
        self.tagset = kw["Tagging"]["TagSet"]

    def get_bucket_tagging(self, **kw):
        self.calls.append(("get_bucket_tagging", kw))
        return {"TagSet": self.tagset}

    def __getattr__(self, name):
        def record(**kw):
            self.calls.append((name, kw))

        return record

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]
