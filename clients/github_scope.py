"""GitHub documentation URL scopes, shared by indexing and on-demand reads."""
import re
from urllib.parse import quote, unquote, urlsplit


def parse_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or parts.hostname not in ('github.com', 'www.github.com'):
        return None
    path = unquote(parts.path).strip('/')
    bits = path.split('/')
    if (parts.username or parts.password or parts.port not in (None, 443, 80) or len(bits) < 2
            or not all(re.fullmatch(r'[\w.-]+', bit) for bit in bits[:2])
            or any(bit in ('', '.', '..') or any(ord(ch) < 32 for ch in bit) for bit in bits)):
        return None
    owner, name = (bit.lower() for bit in bits[:2])
    name = name.removesuffix('.git')
    if owner.lower() in ('orgs', 'settings', 'marketplace', 'topics', 'search') or not name:
        return None
    kind, tail = '', ''
    if len(bits) > 2:
        if bits[2] not in ('tree', 'blob') or len(bits) < 4:
            return None
        kind, tail = bits[2], '/'.join(bits[3:])
    return {'repo': owner + '/' + name, 'kind': kind, 'tail': tail,
            'url': 'https://github.com/' + owner + '/' + name +
                   ('/' + kind + '/' + quote(tail, safe='/') if kind else '')}


def split_reference(tail, refs=(), override=''):
    """Never guess where a slash-containing reference ends."""
    if override:
        if tail != override and not tail.startswith(override + '/'):
            raise ValueError('The reference must match the start of the GitHub URL.')
        return override, tail[len(override):].lstrip('/')
    first, _, rest = tail.partition('/')
    if re.fullmatch(r'[a-fA-F0-9]{40}', first) or first == 'HEAD':
        return first, rest
    matches = sorted({ref for ref in refs if tail == ref or tail.startswith(ref + '/')})
    if len(matches) != 1:
        raise ValueError('Choose the branch, tag or commit in Reference.')
    ref = matches[0]
    return ref, tail[len(ref):].lstrip('/')


def resolve(scope, info, get, override=''):
    """get takes a repository-relative API path and returns decoded GitHub JSON."""
    if not scope['kind']:
        return info['default_branch'], ''
    tail = scope['tail']
    first = tail.split('/', 1)[0]
    if override or re.fullmatch(r'[a-fA-F0-9]{40}', first) or first == 'HEAD':
        return split_reference(tail, override=override)
    refs = []
    for kind in ('heads', 'tags'):
        for row in get('/git/matching-refs/' + kind + '/' + quote(first, safe='')):
            ref = row.get('ref', '')
            if ref.startswith('refs/' + kind + '/'):
                refs.append(ref[len('refs/' + kind + '/'):])
    return split_reference(tail, refs)
