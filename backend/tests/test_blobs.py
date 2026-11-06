import base64
import hashlib
import io

import pytest
from botocore.exceptions import ClientError

from backend.blobs import Blobs
from backend.config import Settings
from backend.store import Problem


def test_local_blobs_are_immutable_and_checked_on_every_read(tmp_path):
    storage = Blobs(Settings(db_path=tmp_path / "hub.sqlite"))
    data = b"Original private recording"
    digest = storage.put(data)
    assert storage.put(data) == digest and storage.get(digest) == data
    (storage.directory / storage.key(digest)).write_bytes(b"Corrupted")
    with pytest.raises(Problem, match="integrity"):
        storage.get(digest)
    with pytest.raises(Problem, match="integrity"):
        storage.put(data)
    with pytest.raises(ValueError):
        storage.get("../../outside")


def test_s3_upload_has_checksum_and_never_overwrites_an_existing_key(tmp_path):
    class S3:
        objects = {}
        calls = []

        def head_object(self, **kw):
            if kw['Key'] not in self.objects:
                raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
            data = self.objects[kw['Key']]
            return {
                'ContentLength': len(data),
                'ChecksumSHA256': base64.b64encode(hashlib.sha256(data).digest()).decode(),
            }

        def put_object(self, **kw):
            self.calls.append(kw)
            assert kw["IfNoneMatch"] == "*" and kw["ServerSideEncryption"] == "AES256"
            assert kw["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(kw["Body"]).digest()).decode()
            if kw["Key"] in self.objects:
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
            self.objects[kw["Key"]] = kw["Body"]

        def get_object(self, **kw):
            return {"Body": io.BytesIO(self.objects[kw["Key"]])}

    s3 = S3()
    storage = Blobs(Settings(db_path=tmp_path / "hub.sqlite", blob_bucket="private-test-bucket"), s3)
    digest = storage.put(b"Audio")
    assert storage.put(b"Audio") == digest
    assert len(s3.objects) == 1 and storage.get(digest) == b"Audio"
    s3.objects[storage.key(digest)] = b"Unexpected corruption"
    with pytest.raises(Problem, match="integrity"):
        storage.put(b"Audio")
