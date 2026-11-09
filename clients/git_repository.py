"""Repository addresses and stable folder names shared by the server and computer."""

import hashlib
import re
from urllib.parse import urlsplit


def address(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ValueError('Use owner/repo or a Git HTTPS or SSH URL')
    if re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', value) and value.split('/')[1] not in ('.', '..'):
        return 'https://github.com/' + value + '.git'
    parsed = urlsplit(value)
    if (
        parsed.scheme in ('https', 'ssh', 'file')
        and parsed.path
        and not parsed.query
        and not parsed.fragment
        and not parsed.password
        and not (parsed.scheme == 'https' and parsed.username)
    ):
        if parsed.scheme == 'file' and parsed.netloc not in ('', 'localhost'):
            raise ValueError('Use a local file remote')
        if parsed.scheme == 'file' or parsed.hostname:
            return value
    if re.fullmatch(r'(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', value):
        return value
    raise ValueError('Use owner/repo or a Git HTTPS or SSH URL')


def folder(value):
    url = address(value)
    if url == 'https://github.com/' + value + '.git':
        return value.replace('/', '__')
    path = urlsplit(url).path if '://' in url else url.split(':', 1)[1]
    parts = path.strip('/').removesuffix('.git').split('/')[-2:]
    name = '__'.join(re.sub(r'[^A-Za-z0-9_.-]', '-', part) for part in parts)
    return (name[:100] or 'repo') + '-' + hashlib.sha256(url.encode()).hexdigest()[:12]


def matches(url, value):
    return url.rstrip('/') == address(value).rstrip('/')
