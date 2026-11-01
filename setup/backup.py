"""Off-server backup storage for the server's database and attachments.

The container cannot use the instance role (metadata hop limit 1), so on AWS this makes a bucket plus a
narrow IAM user and hands its access key to the server through .env. Everything is created idempotently
and tagged like the rest of setup; destroy never deletes the bucket, because it holds the only other copy.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from . import aws as awsmod
from .cloudflare import Cloudflare, CloudflareError

CHOICES = {"aws": "an S3 bucket in your AWS account, created for you (versioned, encrypted, private)",
           "r2": "a Cloudflare R2 bucket, created with your API token",
           "existing": "a bucket I already have (S3, R2 or any S3-compatible store)",
           "local": "none: keep copies only on this server (not safe if the server is lost)"}
KEY_PREFIX = "tico"
NONCURRENT_DAYS = 90
R2_TOKEN_HELP = "permissions: Account > Workers R2 Storage > Edit"


def bucket_name(domain: str, account_id: str) -> str:
    """Bucket names are global; the account id keeps two companies' names from colliding."""
    return f"tico-backup-{awsmod.slug(domain)[:30]}-{account_id}"[:63].rstrip("-")


def user_name(name: str) -> str:
    return f"tico-backup-{name}"[:64]


def policy(bucket: str) -> dict:
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": f"arn:aws:s3:::{bucket}"},
        # Litestream deletes what its retention expires; versioning keeps those deletes recoverable.
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                                      "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
         "Resource": f"arn:aws:s3:::{bucket}/*"}]}


def aws_plan_lines(domain: str, region: str, account_id: str = "<account>") -> list[str]:
    bucket = bucket_name(domain, account_id)
    return [f"S3 bucket {bucket} in {region}: versioning on, AES-256 encryption, all public access blocked, TLS only, "
            f"older versions expire after {NONCURRENT_DAYS} days",
            f"IAM user {user_name(awsmod.slug(domain))} allowed to read and write that bucket only; its access key goes into .env "
            "(containers cannot use the instance role)",
            f"The server copies its database (Litestream, about every minute) and attachments (every 30 seconds) to s3://{bucket}/{KEY_PREFIX}"]


def create_s3(s3, iam, *, domain: str, region: str, account_id: str, name: str,
              say: Callable[[str], None] = print) -> dict[str, str]:
    """Returns the .env values. Safe to re-run: an existing bucket is kept and a fresh key replaces the oldest."""
    bucket = bucket_name(domain, account_id)
    try:
        s3.head_bucket(Bucket=bucket)
        say(f"  bucket {bucket} exists; keeping it")
    except Exception as e:
        code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise RuntimeError(f"cannot use bucket {bucket}: {code or e}") from e
        args = {"Bucket": bucket}
        if region != "us-east-1":
            args["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**args)
        say(f"  created bucket {bucket}")
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
        "ID": "tico-backups", "Status": "Enabled", "Filter": {"Prefix": ""},
        "NoncurrentVersionExpiration": {"NoncurrentDays": NONCURRENT_DAYS},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}]})
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "TLSOnly", "Effect": "Deny", "Principal": "*", "Action": "s3:*",
        "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
        "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}))
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": awsmod.tags(name)})

    user = user_name(name)
    try:
        iam.get_user(UserName=user)
        if not awsmod.is_ours(iam.list_user_tags(UserName=user).get("Tags"), name):
            raise RuntimeError(f"IAM user {user} exists but was not created by tico setup; refusing to touch it")
    except iam.exceptions.NoSuchEntityException:
        iam.create_user(UserName=user, Tags=awsmod.tags(name))
        say(f"  created IAM user {user}")
    iam.put_user_policy(UserName=user, PolicyName="tico-backup", PolicyDocument=json.dumps(policy(bucket)))
    keys = sorted(iam.list_access_keys(UserName=user)["AccessKeyMetadata"], key=lambda k: k["CreateDate"])
    if len(keys) >= 2:  # IAM allows two; a secret cannot be read back, so a re-run makes a new one
        iam.delete_access_key(UserName=user, AccessKeyId=keys[0]["AccessKeyId"])
    key = iam.create_access_key(UserName=user)["AccessKey"]
    say(f"  created an access key for {user}")
    return {"TICO_BACKUP_URL": f"s3://{bucket}/{KEY_PREFIX}", "TICO_BACKUP_REGION": region,
            "LITESTREAM_ACCESS_KEY_ID": key["AccessKeyId"], "LITESTREAM_SECRET_ACCESS_KEY": key["SecretAccessKey"]}


def r2_endpoint(account_id: str) -> str:
    return f"https://{account_id}.r2.cloudflarestorage.com"


def create_r2(cf: Cloudflare, *, token: str, account_id: str, domain: str, say: Callable[[str], None] = print) -> dict[str, str]:
    """R2 S3 credentials are derived from the API token: key id = the token's id, secret = sha256 of its value."""
    bucket = f"tico-backup-{awsmod.slug(domain)}"[:63].rstrip("-")
    try:
        cf.call("GET", f"/accounts/{account_id}/r2/buckets/{bucket}")
        say(f"  R2 bucket {bucket} exists; keeping it")
    except CloudflareError:
        cf.call("POST", f"/accounts/{account_id}/r2/buckets", {"name": bucket})
        say(f"  created R2 bucket {bucket}")
    try:
        token_id = cf.call("GET", "/user/tokens/verify")["id"]
    except CloudflareError:
        token_id = cf.call("GET", f"/accounts/{account_id}/tokens/verify")["id"]
    return {"TICO_BACKUP_URL": f"s3://{bucket}/{KEY_PREFIX}", "TICO_BACKUP_ENDPOINT": r2_endpoint(account_id),
            "TICO_BACKUP_REGION": "auto", "LITESTREAM_ACCESS_KEY_ID": token_id,
            "LITESTREAM_SECRET_ACCESS_KEY": hashlib.sha256(token.encode()).hexdigest()}


def r2_plan_lines(domain: str) -> list[str]:
    return [f"R2 bucket tico-backup-{awsmod.slug(domain)} (private; the token needs {R2_TOKEN_HELP}); the server copies its "
            "database and attachments there"]


LOCAL_WARNING = ("No off-server backup: copies go to the tico-backups Docker volume on the same server, which survives a "
                 "deleted data volume but not a lost server. Set TICO_BACKUP_URL later (docs/install.md, Backups and restore).")
