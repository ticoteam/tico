#!/usr/bin/env python3
"""Prepare a company publisher role. Dry-run reads GitHub settings; --apply changes IAM only."""
import argparse
import json
import re
import subprocess
import sys

if __package__:
    from .company_apps import key, parse
else:
    from company_apps import key, parse


def oidc_subject(settings):
    """Derive the only supported tag subject shape from GitHub's live repo settings."""
    if not isinstance(settings, dict) or settings.get("use_default") is not True:
        raise ValueError("GitHub OIDC must use the default subject template")
    immutable = settings.get("use_immutable_subject")
    if immutable is True:
        base = settings.get("sub_claim_prefix")
        if not isinstance(base, str) or not re.fullmatch(
                r"repo:ticoteam@[0-9]+/tico@[0-9]+", base):
            raise ValueError("GitHub returned an unsupported immutable repository subject")
    elif immutable is False:
        base = settings.get("sub_claim_prefix", "repo:ticoteam/tico")
        if base != "repo:ticoteam/tico":
            raise ValueError("GitHub returned an unsupported legacy repository subject")
    else:
        raise ValueError("GitHub did not report whether OIDC subjects are immutable")
    return base + ":ref:refs/tags/v*"


def discover_oidc_subject():
    """Read repository OIDC settings; never substitute a guessed legacy subject on failure."""
    try:
        result = subprocess.run(
            ["gh", "api", "repos/ticoteam/tico/actions/oidc/customization/sub"],
            check=True, capture_output=True, text=True)
        settings = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ValueError("could not discover GitHub OIDC settings; authenticate gh and retry") from exc
    return oidc_subject(settings)


def policies(account, bucket, prefix="", subject=None):
    if subject is None:
        subject = discover_oidc_subject()
    if not isinstance(subject, str) or not re.fullmatch(
            r"repo:ticoteam(?:@[0-9]+/tico@[0-9]+|/tico):ref:refs/tags/v\*", subject):
        raise ValueError("refusing an unsupported GitHub OIDC subject")
    provider = f"arn:aws:iam::{account}:oidc-provider/token.actions.githubusercontent.com"
    root = (prefix.strip("/") + "/" if prefix.strip("/") else "") + "releases/app/"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Federated": provider},
             "Action": "sts:AssumeRoleWithWebIdentity", "Condition": {
                 "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
                 "StringLike": {"token.actions.githubusercontent.com:sub": subject}}}]}
    access = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"], "Resource": f"arn:aws:s3:::{bucket}/{root}*"},
        {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}",
         "Condition": {"StringLike": {"s3:prefix": root + "*"}}}]}
    return provider, trust, access


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("slug", "id", "app-name", "url", "icon-url", "bucket", "account-id"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--runner-url")
    p.add_argument("--prefix", default="")
    p.add_argument("--role-name", help="defaults to tico-company-app-<slug hash>")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    a = p.parse_args()
    a.role_name = a.role_name or "tico-company-app-" + key({"slug": a.slug})[:16]
    if not re.fullmatch(r"[0-9]{12}", a.account_id) or not re.fullmatch(r"[A-Za-z0-9_+=,.@-]{1,64}", a.role_name):
        p.error("invalid account id or role name")
    arn = f"arn:aws:iam::{a.account_id}:role/{a.role_name}"
    entry = {"slug": a.slug, "id": a.id, "app_name": a.app_name, "url": a.url, "icon_url": a.icon_url,
             "bucket": a.bucket, "deploy_role_arn": arn}
    if a.runner_url:
        entry["runner_url"] = a.runner_url
    if a.prefix:
        entry["prefix"] = a.prefix
    try:
        parse(json.dumps([entry]))
    except ValueError as exc:
        p.error(str(exc))
    try:
        provider, trust, access = policies(a.account_id, a.bucket, a.prefix)
    except ValueError as exc:
        p.error(str(exc))
    if a.apply:
        import boto3
        from botocore.exceptions import ClientError
        iam = boto3.client("iam")
        # Verify the selected account before making any IAM changes.
        if boto3.client("sts").get_caller_identity()["Account"] != a.account_id:
            p.error("AWS credentials belong to a different account")
        try:
            iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchEntity":
                raise
            iam.create_open_id_connect_provider(Url="https://token.actions.githubusercontent.com",
                                                ClientIDList=["sts.amazonaws.com"])
        try:
            iam.get_role(RoleName=a.role_name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchEntity":
                raise
            iam.create_role(RoleName=a.role_name, AssumeRolePolicyDocument=json.dumps(trust))
        else:
            iam.update_assume_role_policy(RoleName=a.role_name, PolicyDocument=json.dumps(trust))
        iam.put_role_policy(RoleName=a.role_name, PolicyName="company-app-releases", PolicyDocument=json.dumps(access))
    print(json.dumps({"dry_run": not a.apply, "role_arn": arn, "trust": trust, "permissions": access,
                      "TICO_COMPANY_APPS_entry": entry}, indent=2))


if __name__ == "__main__":
    main()
