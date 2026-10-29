"""Import logins from a password manager's export (docs/credential-vault.md, Import from a password manager).

Bitwarden and Vaultwarden write unencrypted JSON; KeePass and KeePassXC write CSV. `parse` is a pure function: it
reads the export text and returns the logins it can read as `rows`, and a reason for each entry it cannot as `errors`.
Nothing it returns for an error carries a value, so a refusal can say which entry failed without echoing a password.
"""
import csv
import io
import json
from urllib.parse import urlsplit

FORMATS = ('bitwarden', 'keepass')
BITWARDEN_LOGIN = 1         # Bitwarden's item type for a login: 2 is a note, 3 a card, 4 an identity
MAX_ENTRIES = 5000          # a larger export is split: one request is one transaction
MAX_CONTENT = 5_000_000     # characters in the export text the route accepts


class FileError(Exception):
    """The export as a whole cannot be read; the message says why and is safe to show."""


def parse(format, content):
    """(rows, errors) for a password manager's export. Each row is {row, name, username, password, totp, sites}, where
    `row` is the entry's position in the export (1-based: the item in a Bitwarden export, the record in a KeePass CSV,
    the header not counted). `sites` lists the hostnames of its web addresses; `totp` is the authenticator text as
    exported (a base32 key or an otpauth URI), or None."""
    if format == 'bitwarden':
        return bitwarden(content)
    if format == 'keepass':
        return keepass(content)
    raise FileError('Choose the export format: bitwarden or keepass')


def bitwarden(content):
    try:
        data = json.loads(_unbom(content))
    except ValueError:
        raise FileError('This is not a Bitwarden JSON export') from None
    if not isinstance(data, dict):
        raise FileError('This is not a Bitwarden JSON export')
    if data.get('encrypted'):
        raise FileError('This Bitwarden export is encrypted, so it cannot be read here. Export again with the '
                        'unencrypted JSON format, then import that file.')
    items = data.get('items')
    if not isinstance(items, list):
        raise FileError('This Bitwarden export has no items')
    if len(items) > MAX_ENTRIES:
        raise FileError(f'This export has more than {MAX_ENTRIES} entries; split it and import each part')
    rows, errors = [], []
    for number, item in enumerate(items, 1):
        if not isinstance(item, dict) or item.get('type') != BITWARDEN_LOGIN:
            continue
        login = item.get('login') if isinstance(item.get('login'), dict) else {}
        uris = login.get('uris') if isinstance(login.get('uris'), list) else []
        _add(rows, errors, number, item.get('name'), login.get('username'), login.get('password'),
             login.get('totp'), _hosts(u.get('uri') for u in uris if isinstance(u, dict)))
    return rows, errors


def keepass(content):
    try:
        records = list(csv.reader(io.StringIO(_unbom(content), newline='')))
    except csv.Error:
        raise FileError('This CSV could not be read') from None
    records = [record for record in records if any(cell.strip() for cell in record)]
    if not records:
        raise FileError('This CSV is empty')
    header = {name.strip().lower(): index for index, name in enumerate(records[0])}
    missing = [name for name in ('title', 'username', 'password', 'url') if name not in header]
    if missing:
        raise FileError('This is not a KeePass or KeePassXC CSV export: no ' + ', '.join(missing) + ' column')
    totp = header.get('totp', header.get('otp'))
    entries = records[1:]
    if len(entries) > MAX_ENTRIES:
        raise FileError(f'This export has more than {MAX_ENTRIES} entries; split it and import each part')
    rows, errors = [], []
    for number, record in enumerate(entries, 1):
        def cell(index):
            return record[index] if index is not None and index < len(record) else ''
        _add(rows, errors, number, cell(header['title']), cell(header['username']), cell(header['password']),
             cell(totp), _hosts([cell(header['url'])]))
    return rows, errors


def split(rows, known):
    """(new, duplicates): the rows that are not in `known` (a set of keys from `identity`), and how many were. A row
    repeated later in the same export counts as a duplicate of the first, and `known` is updated as rows are taken."""
    new, duplicates = [], 0
    for row in rows:
        key = identity(row['name'], row['username'], row['sites'])
        if key in known:
            duplicates += 1
            continue
        known.add(key)
        new.append(row)
    return new, duplicates


def identity(name, username, sites):
    """What makes two logins the same one: name, username and the hostnames they are for."""
    return (name.casefold(), username, tuple(sorted(sites)))


def _add(rows, errors, number, name, username, password, totp, sites):
    name, username = _text(name).strip(), _text(username).strip()
    password = _text(password)                  # spaces in a password are significant: never stripped
    totp = _text(totp).strip() or None
    name = name or (sites[0] if sites else '')
    if not sites:
        errors.append({'row': number, 'reason': 'no web address to log in to'})
    elif not password:
        errors.append({'row': number, 'reason': 'no password'})
    elif not name:
        errors.append({'row': number, 'reason': 'no name'})
    else:
        rows.append({'row': number, 'name': name, 'username': username, 'password': password, 'totp': totp,
                     'sites': sites})


def _hosts(uris):
    """The hostnames of web addresses, in the order given. An address without a scheme is read as https; an app's
    own scheme (androidapp://, for one) names no website and is left out."""
    found = []
    for uri in uris:
        text = _text(uri).strip()
        if not text:
            continue
        if '://' not in text:
            text = 'https://' + text
        try:
            parts = urlsplit(text)
        except ValueError:
            continue
        host = (parts.hostname or '').lower() if parts.scheme in ('http', 'https') else ''
        if host and host not in found:
            found.append(host)
    return found


def _text(value):
    return value if isinstance(value, str) else ''


def _unbom(content):
    return content[1:] if content.startswith('﻿') else content


def install_credential_imports(app, store):
    """`POST /api/v2/credentials/import`: a credential administrator's export, kept as logins with no grants. Registered
    before the `/api/v2/credentials/{cid}` routes, which would otherwise take `import` for a credential id."""
    from typing import Literal
    from fastapi import Request
    from pydantic import Field
    from .auth import validate_identity
    from . import credentials as creds
    from .models import Contract
    from .store import H, Problem

    class CredentialImport(Contract):
        format: Literal['bitwarden', 'keepass']
        content: str = Field(min_length=1, max_length=MAX_CONTENT)

    @app.post('/api/v2/credentials/import')
    def import_logins(request: Request, body: CredentialImport):
        who = request.state.identity
        with store.transaction() as c:
            validate_identity(c, who)
            creds.require_admin(c, who, store.settings.credential_admins)
            if not app.state.vault.configured:
                raise Problem('vault_unavailable', 'Credential encryption is not configured', 503)
            try:
                rows, errors = parse(body.format, body.content)
            except FileError as e:
                raise Problem('import', str(e), 422) from None
            known = existing_identities(c)
            new, skipped = split(rows, known)
            imported = 0
            for row in new:
                try:
                    creds.create_login(c, who, name=row['name'], username=row['username'], password=row['password'],
                                       totp=row['totp'], sites=row['sites'], source='import')
                except Problem as e:
                    errors.append({'row': row['row'], 'reason': e.detail})
                    continue
                imported += 1
            H.event(c, who.actor, 'credential.imported', '', {'format': body.format, 'imported': imported,
                                                              'skipped': skipped, 'errors': len(errors)})
            return {'imported': imported, 'skipped': skipped, 'errors': sorted(errors, key=lambda e: e['row'])}


def existing_identities(c):
    """The identities of the logins already stored, so an import never adds a second copy of one."""
    found = set()
    for name, username, sites in c.execute("SELECT name,username,sites FROM credentials WHERE kind='login'"):
        found.add(identity(name or '', username or '', json.loads(sites or '[]')))
    return found
