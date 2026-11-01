"""S3 settings shared by attachments and desktop downloads."""

import os
import threading
import time

CLIENT_BUILD_TIMEOUT = 5
# A failed bucket-region lookup is not repeated for this long, so an S3 outage does not add the
# lookup's timeouts to every download.
REGION_RETRY = 300
# Where an S3 call's own errors come from (`write_error`), besides OSError for the network.
S3_MODULES = ("botocore", "boto3", "s3transfer", "urllib3")


class Sources:
    """One process's selected attachment identity, shared with desktop downloads."""
    def __init__(self, settings):
        self.settings = settings
        self.selected = None
        self._clients = {}
        self._building = {}
        self._regions = {}
        self._region_misses = {}
        self._lock = threading.Lock()

    @property
    def kind(self):
        return self.selected or kinds(self.settings)[0]

    def forget(self):
        """Drop the clients built so far: the next request builds one with the settings' keys as they are now."""
        with self._lock:
            self._clients.clear()
            self._building.clear()

    def client_for(self, kind, reads=False, region_name=None):
        cache_key = (kind, reads) if region_name is None else (kind, reads, region_name)
        with self._lock:
            if cache_key in self._clients:
                return self._clients[cache_key]
            task = self._building.get(cache_key)
            if task is None or (task[0].is_set() and time.monotonic() - task[2] >= 30):
                done, errors = threading.Event(), []
                task = self._building[cache_key] = done, errors, time.monotonic()
                def build():
                    try:
                        if reads:
                            from botocore.config import Config
                            config = Config(connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1})
                            s3 = client(self.settings, kind=kind, config=config, region_name=region_name)
                        else:
                            s3 = client(self.settings, kind=kind, region_name=region_name)
                        with self._lock:
                            self._clients[cache_key] = s3
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        done.set()
                # Credential providers (including IMDS) run before SDK socket timeouts apply.
                # At most one construction per kind/access mode survives a caller timeout.
                threading.Thread(target=build, name="tico-s3-client", daemon=True).start()
        done, errors, _ = task
        if not done.wait(CLIENT_BUILD_TIMEOUT):
            raise TimeoutError("S3 credential lookup timed out")
        if errors:
            raise errors[0]
        with self._lock:
            return self._clients[cache_key]

    @property
    def s3(self):
        return self.client_for(self.kind)

    def read_s3(self, operation, **options):
        return read(lambda kind: self.client_for(kind, reads=True), self.kind,
                    kinds(self.settings, reads=True), operation, **options)

    def presign_get(self, s3, bucket, key, expires):
        """A GET URL signed by `s3`'s identity for the bucket's own region. SigV4 signs one region,
        and a client with none configured signs for us-east-1, which S3 refuses for other buckets."""
        signer = s3
        if not self.settings.blob_endpoint and not region(self.settings):
            with self._lock:
                kind = next((cache_key[0] for cache_key, made in self._clients.items() if made is s3), None)
                known = self._regions.get(bucket)
                missed = self._region_misses.get(bucket)
            if kind is not None and known is None and (missed is None or time.monotonic() - missed >= REGION_RETRY):
                known = bucket_region(s3, bucket)
                with self._lock:
                    if known:
                        self._regions[bucket] = known
                    else:
                        self._region_misses[bucket] = time.monotonic()
            if kind is not None and known and known != s3.meta.region_name:
                signer = self.client_for(kind, reads=True, region_name=known)
        return signer.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)


def region(settings, env=None):
    env = os.environ if env is None else env
    return (settings.blob_region or
            (env.get("TICO_BACKUP_REGION") if not settings.blob_endpoint and not env.get("TICO_BACKUP_ENDPOINT") else None) or
            env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or None)


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
        if ((kind == "keys" or settings.blob_bucket) and env.get(prefix + "_ACCESS_KEY_ID")
                and env.get(prefix + "_SECRET_ACCESS_KEY")):
            candidates.append(kind)
    return candidates + ["role"]


def bucket_region(s3, bucket):
    """The bucket's region from HeadBucket, which S3 names even when it refuses the request."""
    from botocore.exceptions import ClientError
    try:
        response = s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        response = exc.response
    except Exception:
        return None
    return (response.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("x-amz-bucket-region")
            or response.get("BucketRegion") or None)


def client(settings, config=None, kind=None, region_name=None):
    import boto3
    from botocore.config import Config
    # botocore presigns S3 URLs with SigV2 unless SigV4 is chosen explicitly. The desktop app
    # follows a download redirect only to a SigV4 URL (app/src/hub.rs has_s3_signature).
    signing = Config(signature_version="s3v4")
    options = {"region_name": region_name or region(settings), "endpoint_url": settings.blob_endpoint or None,
               "config": signing if config is None else config.merge(signing)}
    kind = kind or kinds(settings)[0]
    if kind == "company":
        # (key ID, secret), or with a session token for Tico file hosting's keys (backend/tico_storage.py).
        key, secret, *token = settings.blob_keys
        options.update(aws_access_key_id=key, aws_secret_access_key=secret,
                       **({"aws_session_token": token[0]} if token and token[0] else {}))
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
    known = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken", "InvalidToken",
             "NoSuchBucket", "AuthorizationHeaderMalformed", "RequestTimeTooSkewed", "SlowDown", "ServiceUnavailable"}
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
