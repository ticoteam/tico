"""Helpers that other tests share, moved out of test_storage_delivery.py so they import no test file."""

import base64
import hashlib
import io
from botocore.exceptions import ClientError
from backend.file_delivery import byte_range
from backend.tests.helpers.api import headers, post


class S3:
    def __init__(self):
        self.objects, self.calls, self.parts = {}, [], []
        self.offline, self.corrupt = False, False
        self.head_calls = []

    def head_object(self, **kw):
        self.head_calls.append(kw)
        if self.offline:
            raise RuntimeError('offline')
        if kw['Key'] not in self.objects:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        data = self.objects[kw['Key']]
        return {'ContentLength': len(data), 'ChecksumSHA256': base64.b64encode(hashlib.sha256(data).digest()).decode()}

    def put_object(self, **kw):
        self.calls.append(kw)
        if kw['Key'] in self.objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        body = kw['Body']
        if hasattr(body, 'read'):
            chunks = []
            while chunk := body.read(64 * 1024):
                chunks.append(chunk)
            body = b''.join(chunks)
        self.objects[kw['Key']] = body + (b'bad' if self.corrupt else b'')

    def get_object(self, **kw):
        if self.offline:
            raise RuntimeError('offline')
        if kw['Key'] not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        data = self.objects[kw['Key']]
        if 'Range' in kw:
            start, end = byte_range(kw['Range'], len(data))
            data = data[start : end + 1]
        return {'Body': io.BytesIO(data)}

    def create_multipart_upload(self, **kw):
        self.calls.append(kw)
        self.parts = []
        return {'UploadId': 'upload'}

    def upload_part(self, **kw):
        self.parts.append(kw['Body'])
        return {'ETag': str(kw['PartNumber'])}

    def complete_multipart_upload(self, **kw):
        assert kw['IfNoneMatch'] == '*'
        self.objects[kw['Key']] = b''.join(self.parts)

    def abort_multipart_upload(self, **kw):
        self.aborted = True


def task(api):
    return post(api, 'tasks', {'owner': 'ops', 'title': 'Review media', 'body': 'Open the file.'})['id']


def attach(api, tid, name, data):
    response = api.post(f'/api/v2/tasks/{tid}/files', files={'file': (name, data)}, headers=headers('ana-test'))
    assert response.status_code == 200, response.text
    return response.json()['file']['id']
