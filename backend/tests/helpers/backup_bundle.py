"""Helpers that other tests share, moved out of test_backup_bundle.py so they import no test file."""

import base64
import hashlib
import io
from pathlib import Path
from botocore.exceptions import ClientError
from backend.backup import upload_bundle
from backend.config import Settings


class ObjectStore:
    """Offline S3 semantics, including conditional writes, independent GETs and HEADs."""

    def __init__(self):
        self.objects = {}
        self.gets = []
        self.heads = []
        self.after_put = None

    def put_object(self, **kw):
        slot = (kw["Bucket"], kw["Key"])
        assert kw["IfNoneMatch"] == "*" and kw["ServerSideEncryption"] == "AES256"
        if slot in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        data = kw["Body"].read() if hasattr(kw["Body"], "read") else kw["Body"]
        assert base64.b64encode(hashlib.sha256(data).digest()).decode() == kw["ChecksumSHA256"]
        self.objects[slot] = data
        if self.after_put:
            self.after_put(slot)
        return {"VersionId": "test-version"}

    def get_object(self, **kw):
        slot = (kw["Bucket"], kw["Key"])
        self.gets.append(slot)
        if slot not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[slot])}

    def head_object(self, **kw):
        # S3 reports the checksum it verified on receipt, only when asked for it.
        slot = (kw["Bucket"], kw["Key"])
        self.heads.append(slot)
        if slot not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        data = self.objects[slot]
        head = {"ContentLength": len(data)}
        if kw.get("ChecksumMode") == "ENABLED":
            head["ChecksumSHA256"] = base64.b64encode(hashlib.sha256(data).digest()).decode()
        return head

    def download_file(self, bucket, key, destination):
        with self.get_object(Bucket=bucket, Key=key)["Body"] as stream:
            Path(destination).write_bytes(stream.read())


def target(tmp_path):
    return Settings(db_path=tmp_path / "restore" / "hub.sqlite", blob_dir=tmp_path / "restore" / "files")


def bundle(api, s3, key="backups/test.sqlite"):
    return upload_bundle(api.app.state.store.settings.db_path, "backup-bucket", key, s3, api.app.state.blobs)
