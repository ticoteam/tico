import fnmatch

import unittest

from scripts import company_app_publisher as publisher


LEGACY = {"use_default": True, "use_immutable_subject": False}
IMMUTABLE = {"use_default": True, "use_immutable_subject": True,
             "sub_claim_prefix": "repo:ticoteam@335213986/tico@1395824547"}


class CompanyPublisherSubject(unittest.TestCase):
    def test_subject_uses_live_legacy_or_immutable_repository_prefix(self):
        self.assertEqual(publisher.oidc_subject(LEGACY), "repo:ticoteam/tico:ref:refs/tags/v*")
        self.assertEqual(publisher.oidc_subject({"use_default": True}),
                         "repo:ticoteam/tico:ref:refs/tags/v*")
        self.assertEqual(publisher.oidc_subject(IMMUTABLE),
                         "repo:ticoteam@335213986/tico@1395824547:ref:refs/tags/v*")


    def test_subject_rejects_custom_incomplete_or_unrelated_settings(self):
        cases = [
            {"use_default": False, "include_claim_keys": ["repo", "ref"]},
            {"use_default": True, "include_claim_keys": ["repo", "ref"]},
            {"use_default": True, "use_immutable_subject": True,
             "sub_claim_prefix": "repo:attacker@1/other@2"},
            {"use_default": True, "use_immutable_subject": False,
             "sub_claim_prefix": "repo:ticoteam/tico:environment:prod"},
        ]
        for settings in cases:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                publisher.oidc_subject(settings)


    def test_discovery_failure_does_not_guess_legacy(self):
        from unittest.mock import patch
        def failed(*args, **kwargs):
            raise OSError("gh is unavailable")
        with patch.object(publisher.subprocess, "run", failed):
            with self.assertRaisesRegex(ValueError, "could not discover"):
                publisher.discover_oidc_subject()


    def test_tag_trust_does_not_match_other_repo_branch_or_pr_event(self):
        expected = publisher.oidc_subject(IMMUTABLE)
        subjects = [
            "repo:other/tico:ref:refs/tags/v1.2.3",                 # another owner
            "repo:ticoteam/other:ref:refs/tags/v1.2.3",             # another repository
            "repo:ticoteam/tico:ref:refs/heads/main",                # branch event
            "repo:ticoteam/tico:pull_request",                       # pull request event
            "repo:ticoteam/tico:environment:production",             # custom environment subject
        ]
        for subject in subjects:
            with self.subTest(subject=subject):
                self.assertFalse(fnmatch.fnmatchcase(subject, expected))


    def test_immutable_trust_keeps_audience_and_s3_scope(self):
        _, trust, access = publisher.policies(
            "123456789012", "files", "team", subject=publisher.oidc_subject(IMMUTABLE))
        condition = trust["Statement"][0]["Condition"]
        self.assertEqual(condition["StringEquals"]["token.actions.githubusercontent.com:aud"], "sts.amazonaws.com")
        self.assertEqual(condition["StringLike"]["token.actions.githubusercontent.com:sub"],
                         "repo:ticoteam@335213986/tico@1395824547:ref:refs/tags/v*")
        self.assertEqual(access["Statement"][0]["Resource"], "arn:aws:s3:::files/team/releases/app/*")
        self.assertEqual(access["Statement"][1]["Condition"]["StringLike"]["s3:prefix"],
                         "team/releases/app/*")
