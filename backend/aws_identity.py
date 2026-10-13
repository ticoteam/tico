"""An optional check, before the server or the Slack gateway starts, that it runs as the AWS identity the install expects.

TICO_EXPECTED_AWS_ACCOUNT (12 digits) and, optionally, TICO_EXPECTED_AWS_ROLE (a role name or role ARN) are compared
with STS GetCallerIdentity, so credentials for another account never reach its buckets, keys or secrets. With neither
set nothing is called. Messages name accounts and ARNs only, never keys or tokens.
"""

import os
import re
import sys

ACCOUNT = re.compile(r"\d{12}")
ROLE_NAME = re.compile(r"[\w+=,.@-]{1,64}")
ROLE_ARN = re.compile(r"arn:aws[\w-]*:iam::(\d{12}):role/(?:[\w+=,.@/-]*/)?([\w+=,.@-]{1,64})")
ASSUMED_ROLE_ARN = re.compile(r"arn:aws[\w-]*:sts::(\d{12}):assumed-role/([\w+=,.@-]{1,64})/.+")


def sts_client(env):
    import boto3
    from botocore.config import Config
    # Short timeouts and few retries: an unreachable STS refuses the start rather than hanging it.
    config = Config(connect_timeout=10, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"})
    region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("sts", region_name=region, config=config)


def caller_role(arn):
    """The role name an assumed-role (or role) ARN carries, else None."""
    match = ASSUMED_ROLE_ARN.fullmatch(arn) or ROLE_ARN.fullmatch(arn)
    return match.group(2) if match else None


def check(env=os.environ, client=sts_client):
    """(True, what was confirmed) or (False, why not); (True, None) when nothing is expected."""
    account = (env.get("TICO_EXPECTED_AWS_ACCOUNT") or "").strip()
    role = (env.get("TICO_EXPECTED_AWS_ROLE") or "").strip()
    if not account and not role:
        return True, None
    if not account:
        return False, "TICO_EXPECTED_AWS_ROLE is set without TICO_EXPECTED_AWS_ACCOUNT; set both"
    if not ACCOUNT.fullmatch(account):
        return False, "TICO_EXPECTED_AWS_ACCOUNT must be a 12-digit AWS account id"
    role_name = None
    if role:
        arn = ROLE_ARN.fullmatch(role)
        if arn and arn.group(1) != account:
            return False, f"TICO_EXPECTED_AWS_ROLE is in account {arn.group(1)}, not TICO_EXPECTED_AWS_ACCOUNT {account}"
        if not arn and not ROLE_NAME.fullmatch(role):
            return False, "TICO_EXPECTED_AWS_ROLE must be a role name or arn:aws:iam::<account>:role/<name>"
        role_name = arn.group(2) if arn else role
    from botocore.exceptions import ClientError, NoCredentialsError
    try:
        identity = client(env).get_caller_identity()
    except NoCredentialsError:
        return False, f"no AWS credentials were found, so AWS account {account} could not be confirmed"
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "error")
        return False, f"could not confirm the AWS identity (expected account {account}): STS answered {code}"
    except Exception as error:  # network, timeout, endpoint: the identity is unknown, so the start is refused
        return False, f"could not confirm the AWS identity (expected account {account}): {type(error).__name__}"
    actual, caller = str(identity.get("Account") or ""), str(identity.get("Arn") or "")
    if actual != account:
        return False, f"AWS account is {actual or 'unknown'}, expected {account} (TICO_EXPECTED_AWS_ACCOUNT)"
    if role_name and caller_role(caller) != role_name:
        return False, f"AWS identity is {caller or 'unknown'}, expected role {role_name} (TICO_EXPECTED_AWS_ROLE)"
    return True, f"AWS account {account}" + (f", role {role_name}" if role_name else "") + " confirmed"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["check"]:
        print("usage: python -m backend.aws_identity check", file=sys.stderr)
        return 2
    ok, message = check()
    if message:
        print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
