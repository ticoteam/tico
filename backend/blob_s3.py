"""S3 settings for attachments."""

import os
import threading
import time

CLIENT_BUILD_TIMEOUT = 5
# Where an S3 call's own errors come from (`write_error`), besides OSError for the network.
S3_MODULES = ("botocore", "boto3", "s3transfer", "urllib3")


class Sources:
    """One process's selected attachment identity."""

    def __init__(self, settings):
        self.settings = settings
        self.selected = None
        self._clients = {}
        self._building = {}
        self._generation = 0  # forget() moves it on: a build started before then caches nothing
        self._lock = threading.Lock()

    @property
    def kind(self):
        return self.selected or kinds(self.settings)[0]

    def forget(self):
        """Drop the clients built so far: the next request builds one with the settings' keys as they are now."""
        with self._lock:
            self._clients.clear()
            self._building.clear()
            self._generation += 1

    def client_for(self, kind, reads=False, region_name=None):
        cache_key = (kind, reads) if region_name is None else (kind, reads, region_name)
        with self._lock:
            if cache_key in self._clients:
                return self._clients[cache_key]
            task = self._building.get(cache_key)
            if task is None or (task[0].is_set() and time.monotonic() - task[2] >= 30):
                done, errors, made, generation = threading.Event(), [], [], self._generation
                task = self._building[cache_key] = done, errors, time.monotonic(), made

                def build():
                    try:
                        if reads:
                            from botocore.config import Config

                            config = Config(connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1})
                            s3 = client(self.settings, kind=kind, config=config, region_name=region_name)
                        else:
                            s3 = client(self.settings, kind=kind, region_name=region_name)
                        made.append(s3)
                        with self._lock:
                            if self._generation == generation:
                                self._clients[cache_key] = s3
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        done.set()

                # Credential providers (including IMDS) run before SDK socket timeouts apply.
                # At most one construction per kind/access mode survives a caller timeout.
                threading.Thread(target=build, name="tico-s3-client", daemon=True).start()
        done, errors, _, made = task
        if not done.wait(CLIENT_BUILD_TIMEOUT):
            raise TimeoutError("S3 credential lookup timed out")
        if errors:
            raise errors[0]
        with self._lock:
            return self._clients.get(cache_key, made[0])

    @property
    def s3(self):
        return self.client_for(self.kind)

    def read_s3(self, operation, **options):
        return read(
            lambda kind: self.client_for(kind, reads=True),
            self.kind,
            kinds(self.settings, reads=True),
            operation,
            **options,
        )


def region(settings, env=None):
    env = os.environ if env is None else env
    return (
        settings.blob_region
        or (
            env.get("TICO_BACKUP_REGION")
            if not settings.blob_endpoint and not env.get("TICO_BACKUP_ENDPOINT")
            else None
        )
        or env.get("AWS_REGION")
        or env.get("AWS_DEFAULT_REGION")
        or None
    )


def kinds(settings, env=None, reads=False):
    env = os.environ if env is None else env
    # A company bucket set in Settings (backend/company_storage.py) carries its own keys and uses nothing else.
    if getattr(settings, "blob_keys", None):
        return ["company"]
    mode = getattr(settings, "blob_credentials", "auto") or "auto"
    if mode != "auto" and not reads:
        return [mode]
    candidates = []
    for kind, prefix in (("keys", "TICO_BLOB"), ("backup", "LITESTREAM")):
        if (
            (kind == "keys" or settings.blob_bucket)
            and env.get(prefix + "_ACCESS_KEY_ID")
            and env.get(prefix + "_SECRET_ACCESS_KEY")
        ):
            candidates.append(kind)
    return candidates + ["role"]


def client(settings, config=None, kind=None, region_name=None):
    import boto3
    from botocore.config import Config

    # botocore signs with SigV2 unless SigV4 is chosen explicitly.
    signing = Config(signature_version="s3v4")
    options = {
        "region_name": region_name or region(settings),
        "endpoint_url": settings.blob_endpoint or None,
        "config": signing if config is None else config.merge(signing),
    }
    kind = kind or kinds(settings)[0]
    if kind == "company":
        # (key ID, secret), or with a session token for Tico file hosting's keys (backend/tico_storage.py).
        key, secret, *token = settings.blob_keys
        options.update(
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            **({"aws_session_token": token[0]} if token and token[0] else {}),
        )
    elif kind != "role":
        prefix = "TICO_BLOB" if kind == "keys" else "LITESTREAM"
        key, secret = os.environ.get(prefix + "_ACCESS_KEY_ID"), os.environ.get(prefix + "_SECRET_ACCESS_KEY")
        if not key or not secret:
            from botocore.exceptions import NoCredentialsError

            raise NoCredentialsError()
        options.update(aws_access_key_id=key, aws_secret_access_key=secret)
    return boto3.client("s3", **options)


def read(client_for, selected, candidates, operation, **options):
    """Retry other identities only after access denial; return the signing client too."""
    try:
        s3 = client_for(selected)
        return getattr(s3, operation)(**options), s3
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") not in ("AccessDenied", "403"):
            raise
        denied = exc
    for kind in candidates:
        if kind == selected:
            continue
        try:
            s3 = client_for(kind)
            return getattr(s3, operation)(**options), s3
        except Exception:
            continue
    raise denied


def write_error(exc, bucket, cleanup=False):
    # Only known error codes or the exception type: SDK messages can contain credential material.
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    known = {
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "InvalidToken",
        "NoSuchBucket",
        "AuthorizationHeaderMalformed",
        "RequestTimeTooSkewed",
        "SlowDown",
        "ServiceUnavailable",
    }
    reason = code if code in known else type(exc).__name__
    if code is None and not isinstance(exc, OSError) and type(exc).__module__.split(".")[0] not in S3_MODULES:
        # Not from S3 or the network to it (a busy database, a bug here): saying "S3" sends people to the wrong place.
        return f"File storage failed on this server, not in S3: {reason} while using {bucket}"
    if cleanup and code == "AccessDenied":
        return f"S3 storage cleanup permission missing: s3:AbortMultipartUpload on {bucket}"
    if cleanup:
        return f"S3 storage can't clean up its write check: {reason} on {bucket}"
    action = " (s3:PutObject)" if code == "AccessDenied" else ""
    return f"S3 storage can't write: {reason} on {bucket}{action}"
