"""Encrypted company credentials with explicit human and run-bound bot grants."""
import hashlib
import hmac
import json
import os
import threading
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from pydantic import ConfigDict, Field, SecretStr

from . import providers
from .auth import validate_identity
from .models import Contract, ID
from .bot_access import owner_ids
from .store import H, Problem, encode

CONTEXT = {'application': 'tico-credentials'}
# A grant to every computer, present and future: a model API key or token that signs each computer's model CLI in
# (runner/service.py `team_model_keys`). Never a bot's: a bot still gets only what is granted to it.
COMPUTERS = 'computers'
FILE_MIGRATION = 'credential-file-migration-v1'
# 0.2.30 reserved every HUB_ name, so a team's own HUB_ keys (HUB_BUCKET) were left out of v1. v2 adds only those.
HUB_MIGRATION = 'credential-file-migration-v2-hub'


def model_env(name, kind, env=''):
    name = name.strip()
    return env or (name if name in providers.MODEL_KEY_NAMES and kind in ('api_key', 'token') else '')


def refuse_reserved(env, stored=''):
    """A credential arrives in a bot's turn under its variable; Tico's own and the computer's names are refused, since a
    run given one would lose it (runner/service.py `environment`). One stored under such a name before the rule is
    saved, secret and all, once the same save renames its variable."""
    from clients.access_entry import RESERVED_ENV, RESERVED_PREFIXES
    if env and (env in RESERVED_ENV or env.startswith(RESERVED_PREFIXES)):
        if env == stored:
            raise Problem('env', f"This credential's variable {env} is a name Tico keeps for itself. Rename the variable "
                          f"in the same save (for example {env.removeprefix('TICO_')}) and the change is saved", 422)
        raise Problem('env', f"{env} is a name Tico or the computer uses itself; choose another variable name "
                      "(names starting TICO_, DYLD_ or LD_ are reserved)", 422)


def administrator(c, who, admins):
    """`admins` is the credential administrators: TICO_CREDENTIAL_ADMINS, which defaults to the owner and the Admins
    (`Auth.sync_access`, unless the owner's rule says the owner alone)."""
    if who.role not in ('owner', 'human'):
        return False
    row = c.execute('SELECT email FROM humans WHERE id=?', (H.actor_id(who.actor),)).fetchone()
    return bool(row and (row['email'] or '').lower() in set(admins))


def require_admin(c, who, admins):
    if not administrator(c, who, admins):
        raise Problem('forbidden', ask_admin_detail(c, admins, 'authorize this change'), 403)


def admin_names(c, admins):
    """Who to ask: the credential administrators' names."""
    rows = c.execute('SELECT name,email FROM humans').fetchall()
    return [r['name'] or r['email'] for r in rows if (r['email'] or '').lower() in set(admins)]


def ask_admin_detail(c, admins, what):
    names = admin_names(c, admins)
    return f'Only a credential administrator can {what}' + ('. Ask ' + ', '.join(names[:3]) if names else '')


def effective_grant(c, credential, subject):
    return c.execute('SELECT g.* FROM credential_grants g LEFT JOIN credential_grants p ON p.id=g.parent_id '
                     "LEFT JOIN bot_config bc ON g.subject='bot:'||bc.bot "
                     'WHERE g.credential_id=? AND g.subject=? AND g.revoked IS NULL '
                     "AND (g.parent_id IS NULL OR (p.revoked IS NULL AND p.id IS NOT NULL AND p.subject='human:'||bc.operator))",
                     (credential, subject)).fetchone()


def granted(c, subject):
    """The credential ids `subject` holds an effective grant to, in one query: `effective_grant` for each of them.
    The runner endpoints poll every few seconds for every bot, so one query per credential was most of a server's SQL."""
    return {r[0] for r in c.execute('SELECT g.credential_id FROM credential_grants g '
                                    'LEFT JOIN credential_grants p ON p.id=g.parent_id '
                                    "LEFT JOIN bot_config bc ON g.subject='bot:'||bc.bot "
                                    'WHERE g.subject=? AND g.revoked IS NULL '
                                    "AND (g.parent_id IS NULL OR (p.revoked IS NULL AND p.id IS NOT NULL "
                                    "AND p.subject='human:'||bc.operator))", (subject,))}


def permitted(c, who, credential, admins):
    return administrator(c, who, admins) or (who.role in ('human', 'owner', 'bot')
                                             and effective_grant(c, credential, who.actor) is not None)


def creator(c, who, credential):
    """Whether this person added the credential: they see its metadata and share it with bots they manage, never its
    value unless it is also granted to them."""
    return who.role in ('human', 'owner') and c.execute(
        'SELECT 1 FROM credentials WHERE id=? AND created_by=?', (credential, who.actor)).fetchone() is not None


def can_open(c, who, admins):
    return administrator(c, who, admins) or (who.role in ('human', 'owner') and (any(
        effective_grant(c, row[0], who.actor) for row in c.execute(
            'SELECT credential_id FROM credential_grants WHERE subject=? AND revoked IS NULL', (who.actor,)))
        or c.execute('SELECT 1 FROM credentials WHERE created_by=?', (who.actor,)).fetchone() is not None))


class CredentialWrite(Contract):
    # Password spaces are significant. SecretStr prevents repr/debug output from exposing it.
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    name: str = Field(min_length=1, max_length=150)
    username: str = Field(default='', max_length=250)
    kind: Literal['api_key', 'password', 'token', 'file', 'connection'] = 'api_key'
    env: str = Field(default='', max_length=100, pattern=r'^$|^[A-Z_][A-Z0-9_]*$')
    secret: SecretStr | None = Field(default=None, max_length=100_000)
    source: str = Field(default='', max_length=500)
    expected_revision: int | None = Field(default=None, ge=1)
    # BotOps adding a credential a person asked for in chat, as that person (backend/app.py
    # delegated_identity). Only create and grant take it; reveal, update and revoke never do.
    on_behalf_of: ID | None = None


class CredentialGrant(Contract):
    subject: ID
    on_behalf_of: ID | None = None


class LegacyCredential(Contract):
    env: str = Field(max_length=100, pattern=r'^[A-Z_][A-Z0-9_]*$')
    value: SecretStr = Field(min_length=1, max_length=100_000)
    kind: Literal['api_key', 'file'] = 'api_key'


class CredentialMigration(Contract):
    bot: ID
    credentials: list[LegacyCredential] = Field(default_factory=list, max_length=500)


LOCAL = 'local'                                   # `credential_keys.kms_key` for a data key kept in a file
KEY_CHECK = b'tico-credential-key-check:v1'       # what the row keeps of a local key: proof of it, never the key
KEY_FILE = 'credential.key'
_key_file_lock = threading.Lock()


class CredentialCipher:
    """One AES-256 data key encrypts every credential. With `key_id` (TICO_CREDENTIAL_KMS_KEY) it is an AWS KMS data key and
    only its wrapped form is in the database. Without one it is 32 random bytes in `key_file` (mode 0600, beside the database
    on the server's data volume), generated on first use: the database alone then cannot decrypt a credential."""

    def __init__(self, key_id, kms=None, key_file=None):
        self.key_id, self.kms, self.key_file = key_id, kms, Path(key_file) if key_file else None
        self.cached = None

    @property
    def configured(self):
        return bool(self.key_id or self.key_file)

    @property
    def storage(self):
        return 'kms' if self.key_id else 'local' if self.key_file else ''

    def file_key(self, create):
        """The local data key; made (once, never overwritten) only when `create` says nothing was ever encrypted with one."""
        path = self.key_file
        with _key_file_lock:
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                if not create:
                    raise Problem('vault_unavailable', f'The credential key file ({path.name}) is missing from the data volume: '
                                  'restore it from the backup that was made with this database', 503) from None
                path.parent.mkdir(parents=True, exist_ok=True)
                data = os.urandom(32)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as out:
                    out.write(data)
            if len(data) != 32:
                raise Problem('vault_unavailable', f'The credential key file ({path.name}) is not a valid key', 503)
            if path.stat().st_mode & 0o077:
                path.chmod(0o600)
        return data

    def local_key(self, c, row):
        if row and row['kms_key'] != LOCAL:
            raise Problem('vault_unavailable', 'Credentials here were stored under an AWS KMS key: set TICO_CREDENTIAL_KMS_KEY again', 503)
        key = self.file_key(create=not row)
        check = hmac.new(key, KEY_CHECK, hashlib.sha256).digest()
        if row and not hmac.compare_digest(bytes(row['wrapped_key']), check):
            raise Problem('vault_unavailable', 'The credential key file does not belong to this database', 503)
        if not row:
            c.execute('INSERT INTO credential_keys VALUES(?,?,?)', ('v1', LOCAL, check))
        return key

    def kms_key(self, c, row):
        if self.kms is None:
            import boto3
            self.kms = boto3.client('kms')
        if row and row['kms_key'] == LOCAL:
            # Credentials saved before a KMS key was set: wrap the same key with KMS, so nothing is re-encrypted.
            key = self.local_key(c, row)
            wrapped = self.kms.encrypt(KeyId=self.key_id, Plaintext=key, EncryptionContext=CONTEXT)['CiphertextBlob']
            c.execute("UPDATE credential_keys SET kms_key=?,wrapped_key=? WHERE id='v1'", (self.key_id, wrapped))
            return key
        if row:
            if row['kms_key'] != self.key_id:
                raise ValueError('Unexpected wrapping key')
            return self.kms.decrypt(KeyId=self.key_id, CiphertextBlob=row['wrapped_key'], EncryptionContext=CONTEXT)['Plaintext']
        generated = self.kms.generate_data_key(KeyId=self.key_id, KeySpec='AES_256', EncryptionContext=CONTEXT)
        c.execute('INSERT INTO credential_keys VALUES(?,?,?)', ('v1', self.key_id, generated['CiphertextBlob']))
        return generated['Plaintext']

    def key(self, c):
        if not self.configured:
            raise Problem('vault_unavailable', 'Credential encryption is not configured', 503)
        if self.cached is not None:
            return self.cached
        try:
            row = c.execute("SELECT * FROM credential_keys WHERE id='v1'").fetchone()
            key = self.kms_key(c, row) if self.key_id else self.local_key(c, row)
            if len(key) != 32:
                raise ValueError('Invalid data key')
            # Cache only keys already persisted by a previous transaction. A new key must
            # not survive in memory if the surrounding creation transaction rolls back.
            if row:
                self.cached = key
            return key
        except Problem:
            raise
        except Exception:
            raise Problem('vault_unavailable', 'Credential encryption is temporarily unavailable', 503) from None

    def encrypt(self, c, cid, secret):
        nonce = os.urandom(12)
        return AESGCM(self.key(c)).encrypt(nonce, secret.encode(), ('tico-credential:v1:' + cid).encode()), nonce

    def decrypt(self, c, row):
        try:
            return AESGCM(self.key(c)).decrypt(row['nonce'], row['ciphertext'],
                                             ('tico-credential:v1:' + row['id']).encode()).decode()
        except Problem:
            raise
        except Exception:
            raise Problem('vault_unavailable', 'This credential could not be decrypted', 503) from None


class Vault:
    def __init__(self, store, cipher=None, auth=None):
        self.store, self.auth = store, auth
        self.cipher = cipher or CredentialCipher(store.settings.credential_kms_key,
                                                 key_file=Path(store.settings.db_path).parent / KEY_FILE)

    @property
    def configured(self):
        return self.cipher.configured

    @property
    def admins(self):
        return self.store.settings.credential_admins

    def change(self, who, operation, key, body, authorize, fn):
        if not key or len(key) > 200:
            raise Problem('idempotency_key', 'Provide an Idempotency-Key', 422)
        with self.store.transaction() as c:
            validate_identity(c, who)
            authorize(c, who)
            # Keyed fingerprint: neither a plaintext secret nor an offline password-guessing
            # hash enters the mutation receipt. Responses here contain only metadata.
            payload = body.model_dump()
            if isinstance(payload.get('secret'), SecretStr):
                payload['secret'] = payload['secret'].get_secret_value()
            fingerprint = hmac.new(self.cipher.key(c), b'tico-request:v1:' + encode(payload).encode(), hashlib.sha256).hexdigest()
            old = c.execute('SELECT request_hash,response_json FROM idempotency WHERE actor=? AND operation=? AND key=?',
                            (who.actor, operation, key)).fetchone()
            if old:
                if not hmac.compare_digest(old['request_hash'], fingerprint):
                    raise Problem('idempotency_conflict', 'This request key was already used for different content', 409)
                return json.loads(old['response_json'])
            result = fn(c)
            c.execute('INSERT INTO idempotency VALUES(?,?,?,?,?,?)',
                      (who.actor, operation, key, fingerprint, encode(result), H.now()))
            return result

    @staticmethod
    def row(c, cid):
        row = c.execute('SELECT * FROM credentials WHERE id=?', (cid,)).fetchone()
        if not row:
            raise Problem('not_found', 'Credential not found', 404)
        return row

    @staticmethod
    def brief(row):
        return {k: row[k] for k in ('id', 'name', 'username', 'kind', 'env', 'preview', 'source', 'revision', 'created', 'updated')} | {
            'stored': row['ciphertext'] is not None, 'env': model_env(row['name'], row['kind'], row['env'])}

    def write(self, c, who, body, cid=None):
        require_admin(c, who, self.admins)
        old = self.row(c, cid) if cid else None
        if old and body.expected_revision != old['revision']:
            raise Problem('version_conflict', 'Credential changed; refresh before saving', 409)
        if not old and body.expected_revision is not None:
            raise Problem('version_conflict', 'New credentials have no previous revision', 409)
        cid, now = cid or H.new_id(), H.now()
        ciphertext, nonce, preview = (old['ciphertext'], old['nonce'], old['preview']) if old else (None, None, '')
        secret = body.secret.get_secret_value() if body.secret is not None else None
        if secret is not None:
            if not secret:
                raise Problem('credential', 'Enter a nonempty password or key', 422)
            ciphertext, nonce = self.cipher.encrypt(c, cid, secret)
            preview = secret[:3] + '…' + secret[-3:] if body.kind in ('api_key', 'token') and len(secret) >= 12 else '••••••'
        if old and body.kind != old['kind'] and secret is None:
            # A password must never retain a previous API-key preview after a kind change.
            preview = '••••••' if ciphertext is not None else ''
        name = body.name.strip()
        if not name:
            raise Problem('credential', 'Enter a credential name', 422)
        refuse_reserved(model_env(name, body.kind, body.env), old['env'] if old else '')
        c.execute('INSERT INTO credentials(id,name,username,kind,env,preview,ciphertext,nonce,source,created,updated,updated_by,created_by) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,username=excluded.username,'
                  'kind=excluded.kind,env=excluded.env,preview=excluded.preview,ciphertext=excluded.ciphertext,nonce=excluded.nonce,'
                  'source=excluded.source,revision=credentials.revision+1,updated=excluded.updated,updated_by=excluded.updated_by',
                  (cid,name,body.username,body.kind,model_env(name,body.kind,body.env),preview,ciphertext,nonce,body.source,now,now,who.actor,who.actor))
        H.event(c,who.actor,'credential.updated' if old else 'credential.created',cid,{'secret_changed':secret is not None})
        return self.brief(self.row(c,cid))

    def manages_bot(self, c, who, subject):
        """Whether `subject` is a bot this person owns or manages (`Auth.bot_manager`)."""
        return (subject.startswith('bot:') and self.auth is not None
                and self.auth.bot_manager(c, who, H.actor_id(subject)))

    def grant_authority(self, c, who, cid, subject):
        if subject == COMPUTERS:
            require_admin(c, who, self.admins)
            return None
        if administrator(c, who, self.admins):
            return None
        if creator(c, who, cid):
            # The person who added a credential shares it with their own bots, as a direct grant; anyone else's bot, a
            # person, or every computer stays an administrator's call.
            if self.manages_bot(c, who, subject):
                return None
            raise Problem('forbidden', 'You can share a credential you added only with bots you own or manage. '
                          + ask_admin_detail(c, self.admins, f'give it to {H.actor_id(subject)}'), 403)
        parent = effective_grant(c,cid,who.actor) if who.role in ('human','owner') else None
        bot = c.execute('SELECT operator,bot_owners_json FROM bot_config WHERE bot=?', (H.actor_id(subject),)).fetchone() if subject.startswith('bot:') else None
        # A bot's owners (its creator and co-owners, and its operator) attach credentials they hold themselves.
        if not parent or not bot or (bot['operator'] != H.actor_id(who.actor)
                                     and H.actor_id(who.actor) not in owner_ids(bot['bot_owners_json'])):
            raise Problem('forbidden', ask_admin_detail(c, self.admins, 'give a bot a credential')
                          + (', or give a bot you manage a credential you added or were granted' if not parent else ''), 403)
        return parent['id']

    def revoke_authority(self, c, who, cid, grant):
        """An administrator takes any grant away; the person who added the credential, or who made this grant, takes it
        away from a bot they own or manage."""
        if administrator(c, who, self.admins):
            return
        if (creator(c, who, cid) or grant['granted_by'] == who.actor) and self.manages_bot(c, who, grant['subject']):
            return
        raise Problem('forbidden', ask_admin_detail(c, self.admins, 'take this away')
                      + ', or take a credential you added away from a bot you manage', 403)

    def grant(self, c, who, cid, subject):
        row=self.row(c,cid)
        parent = self.grant_authority(c,who,cid,subject)
        inferred = model_env(row['name'], row['kind'], row['env'])
        if inferred and not row['env']:
            # Older name-only model keys gain a variable only when someone grants them.
            c.execute('UPDATE credentials SET env=?,revision=revision+1,updated=?,updated_by=? WHERE id=?',
                      (inferred,H.now(),who.actor,cid))
            row=self.row(c,cid)
        if subject == COMPUTERS:
            if row['env'] not in providers.MODEL_KEY_NAMES or row['kind'] not in ('api_key','token') or row['ciphertext'] is None:
                raise Problem('subject','Only a stored model API key or token, named '+', '.join(sorted(providers.MODEL_KEY_NAMES))
                              +', can be given to every computer',422)
            exists=True
        elif subject.startswith('human:'):
            exists=c.execute('SELECT 1 FROM humans WHERE id=?',(H.actor_id(subject),)).fetchone()
        elif subject.startswith('bot:'):
            exists=c.execute('SELECT 1 FROM bots WHERE slug=?',(H.actor_id(subject),)).fetchone()
        else:
            exists=None
        if not exists:
            raise Problem('subject','Choose a registered person or bot',422)
        old=effective_grant(c,cid,subject)
        if old and (parent is not None or old['parent_id'] is None):
            return {'id':old['id'],'subject':subject,'credential':row['name'],'env':row['env']}
        if subject.startswith('bot:'):
            refuse_reserved(row['env'])
        if subject.startswith('bot:') and row['env']:
            # A run gets one value per variable: a bot with another credential under the same name must give it up first.
            for other in c.execute('SELECT id,name FROM credentials WHERE env=? AND id!=? AND ciphertext IS NOT NULL',(row['env'],cid)):
                if effective_grant(c,other['id'],subject):
                    raise Problem('env_in_use',f"{H.actor_id(subject)} already has {other['name']} for {row['env']}; take that away from it first "
                                  'so a run has only one',409)
        # Replace expired delegated rows, or promote a delegated grant to direct admin authorization.
        c.execute('UPDATE credential_grants SET revoked=?,revoked_by=? WHERE credential_id=? AND subject=? AND revoked IS NULL',
                  (H.now(),who.actor,cid,subject))
        gid=H.new_id()
        c.execute('INSERT INTO credential_grants(id,credential_id,subject,granted_by,parent_id,created) VALUES(?,?,?,?,?,?)',
                  (gid,cid,subject,who.actor,parent,H.now()))
        H.event(c,who.actor,'credential.granted',cid,{'grant':gid,'subject':subject,'parent':parent})
        return {'id':gid,'subject':subject,'credential':row['name'],'env':row['env']}

    def reveal(self,c,who,cid):
        validate_identity(c,who)
        if not permitted(c,who,cid,self.admins):
            raise Problem('forbidden','You do not have access to this credential',403)
        row=self.row(c,cid)
        if row['ciphertext'] is None:
            raise Problem('not_connected','This connection has no stored password or key',409)
        value=self.cipher.decrypt(c,row)
        H.event(c,who.actor,'credential.revealed',cid,{'attempt':who.attempt_id or None})
        return {'id':cid,'value':value}


def install_credentials(app,store,delegate=None,propose=None):
    from . import runner_versions
    vault=app.state.vault=Vault(store,auth=getattr(app.state,'auth',None))
    def migration_runner(c,who):
        if who.role!='runner' or not c.execute('SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL',(who.runner_id,)).fetchone():
            raise Problem('forbidden','Only a registered Computer may migrate its bots\' credentials',403)

    @app.get('/api/v2/runner-credential-migration')
    def pending_migration(request:Request):
        who=request.state.identity
        with store.read() as c:
            migration_runner(c,who)
            pending=set(json.loads(c.execute('SELECT value_json FROM registry_metadata WHERE key=?',(FILE_MIGRATION,)).fetchone()[0]))
            row=c.execute('SELECT value_json FROM registry_metadata WHERE key=?',(HUB_MIGRATION,)).fetchone()
            if runner_versions.at_least(c,who.runner_id,'0.2.31'):
                pending|=set(json.loads(row[0])) if row else set()
            return {'bots':[r[0] for r in c.execute('SELECT bot FROM assignments WHERE runner_id=?',(who.runner_id,)) if r[0] in pending]}

    @app.get('/api/v2/runner-credential-grants')
    def runner_grants(request:Request):
        who=request.state.identity
        with store.read() as c:
            migration_runner(c,who)
            bots={r[0]:[] for r in c.execute('SELECT bot FROM assignments WHERE runner_id=?',(who.runner_id,))}
            stored=c.execute('SELECT id,env FROM credentials WHERE ciphertext IS NOT NULL AND env!=\'\'').fetchall()
            for bot,names in bots.items():
                held=granted(c,'bot:'+bot)
                names.extend(r['env'] for r in stored if r['id'] in held)
            return {'bots':bots}

    @app.post('/api/v2/runner-credential-migration')
    def migrate_credentials(request:Request,body:CredentialMigration):
        from clients.access_entry import RESERVED_ENV, RESERVED_PREFIXES
        who=request.state.identity
        with store.transaction() as c:
            migration_runner(c,who)
            if not c.execute('SELECT 1 FROM assignments WHERE runner_id=? AND bot=?',(who.runner_id,body.bot)).fetchone():
                raise Problem('forbidden','That bot is not on this Computer',403)
            pending=json.loads(c.execute('SELECT value_json FROM registry_metadata WHERE key=?',(FILE_MIGRATION,)).fetchone()[0])
            row=c.execute('SELECT value_json FROM registry_metadata WHERE key=?',(HUB_MIGRATION,)).fetchone()
            hub_pending=json.loads(row[0]) if row else []
            if body.bot not in pending and body.bot not in hub_pending:
                return {'migrated':True}
            first=body.bot in pending
            supports_hub=runner_versions.at_least(c,who.runner_id,'0.2.31')
            if not first and not supports_hub:
                return {'migrated':False,'min_runner':'0.2.31'}
            if first and not supports_hub and body.bot not in hub_pending:
                hub_pending.append(body.bot)
                c.execute('UPDATE registry_metadata SET value_json=? WHERE key=?',(encode(hub_pending),HUB_MIGRATION))
            subject='bot:'+body.bot
            for item in body.credentials:
                if item.env.startswith('HUB_') and not supports_hub:
                    continue
                if not first and not item.env.startswith('HUB_'):
                    continue                # the bot migrated before: only the HUB_ keys 0.2.30 could not take
                if item.env in RESERVED_ENV or item.env.startswith(RESERVED_PREFIXES):
                    raise Problem('env','A Computer setting cannot be granted as a Credential',422)
                # Even a revoked grant wins over an old file: migration never resurrects access.
                if c.execute('SELECT 1 FROM credentials v JOIN credential_grants g ON g.credential_id=v.id '
                             'WHERE v.env=? AND g.subject=?',(item.env,subject)).fetchone():
                    continue
                cid,gid,now=H.new_id(),H.new_id(),H.now()
                ciphertext,nonce=vault.cipher.encrypt(c,cid,item.value.get_secret_value())
                c.execute('INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created,updated,updated_by) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (cid,f'{item.env} ({body.bot})',item.kind,item.env,'••••••',ciphertext,nonce,'Computer migration',now,now,who.actor))
                c.execute('INSERT INTO credential_grants(id,credential_id,subject,granted_by,created) VALUES(?,?,?,?,?)',
                          (gid,cid,subject,who.actor,now))
                H.event(c,who.actor,'credential.migrated',cid,{'env':item.env,'bot':body.bot,'grant':gid})
            if first:
                pending.remove(body.bot)
                c.execute('UPDATE registry_metadata SET value_json=? WHERE key=?',(encode(pending),FILE_MIGRATION))
            if body.bot in hub_pending and supports_hub:
                hub_pending.remove(body.bot)
                c.execute('UPDATE registry_metadata SET value_json=? WHERE key=?',(encode(hub_pending),HUB_MIGRATION))
            return {'migrated':True}

    def acting(request, body, message_id=None):
        """For "Add to credentials" / "Do this for me": the caller, or the person
        whose message to BotOps asked for this, checked with that person's own rights."""
        who = request.state.identity
        message_id = message_id or getattr(body, 'on_behalf_of', None)
        if not message_id:
            return who
        if delegate is None:
            raise Problem('forbidden', 'Delegated credential changes are not enabled', 403)
        with store.transaction() as c:
            person = delegate(c, who, message_id)
            H.event(c, who.actor, 'credential.change_delegated', request.url.path,
                    {'on_behalf_of': person.actor, 'message_id': message_id})
        return person

    @app.get('/api/v2/credentials')
    def inventory(request:Request,on_behalf_of:str=''):
        # Metadata only (names, bot key names, grants; never a secret), so BotOps can find the
        # credential a person asked it to grant (no manual steps).
        who=acting(request,None,on_behalf_of) if on_behalf_of else request.state.identity
        with store.read() as c:
            if who.role not in ('human','owner') or not can_open(c,who,vault.admins):
                raise Problem('forbidden',ask_admin_detail(c,vault.admins,'share credentials, or grant you access to one'),403)
            admin=administrator(c,who,vault.admins)
            rows,added=[],False
            for row in c.execute('SELECT * FROM credentials ORDER BY lower(name),id'):
                # A credential this person added is listed for sharing (its grants too), never revealed unless granted.
                mine=row['created_by']==who.actor
                revealable=permitted(c,who,row['id'],vault.admins)
                if not (revealable or mine):continue
                added|=mine
                item=vault.brief(row)|{'can_reveal':revealable}
                item['grants']=[dict(g)|{'can_revoke':admin or ((mine or g['granted_by']==who.actor) and bool(vault.manages_bot(c,who,g['subject'])))}
                                for g in c.execute('SELECT id,subject,granted_by,parent_id,created FROM credential_grants WHERE credential_id=? AND revoked IS NULL',(row['id'],))
                                if effective_grant(c,row['id'],g['subject']) and (admin or mine or g['subject']==who.actor or g['granted_by']==who.actor)]
                rows.append(item)
            people=[dict(r) for r in c.execute('SELECT id,name,email FROM humans ORDER BY name')] if admin else []
            bots=[dict(r) for r in c.execute('SELECT b.slug AS id,b.display_name AS name,bc.operator FROM bots b JOIN bot_config bc ON bc.bot=b.slug ORDER BY b.display_name')
                  if admin or r['operator']==H.actor_id(who.actor) or (added and vault.manages_bot(c,who,'bot:'+r['id']))]
            return {'credentials':rows,'can_manage':admin,'people':people,'bots':bots,'configured':vault.configured,'key_storage':vault.cipher.storage}

    @app.post('/api/v2/credentials')
    def create(request:Request,body:CredentialWrite):
        who=acting(request,body)
        return vault.change(who,request.url.path,request.headers.get('idempotency-key'),body,
                            lambda c,w:require_admin(c,w,vault.admins),
                            lambda c:vault.write(c,who,body))

    @app.post('/api/v2/credentials/{cid}')
    def update(request:Request,cid:str,body:CredentialWrite):
        # On a person's behalf only the name and bot key name may change: never the secret.
        if body.on_behalf_of and body.secret is not None:
            raise Problem('forbidden', 'A stored secret is changed only by a credential administrator', 403)
        who=acting(request,body)
        return vault.change(who,request.url.path,request.headers.get('idempotency-key'),body,
                            lambda c,w:require_admin(c,w,vault.admins),
                            lambda c:vault.write(c,who,body,cid))

    @app.post('/api/v2/credentials/{cid}/grants')
    def grant(request:Request,cid:str,body:CredentialGrant):
        who=acting(request,body)
        if who.via=='botops' and not who.confirmed and propose:
            # BotOps giving a bot a stored credential for a credential administrator who asked in chat runs at once, as
            # them: their own rights allow it, and a bot only ever gets what is explicitly granted to it. Anyone else is
            # refused here in plain words (`grant_authority`); a click stays for a person's access, every computer's, and
            # a holder passing on what they were granted.
            with store.transaction() as c:
                validate_identity(c,who)
                vault.grant_authority(c,who,cid,body.subject)
                if not (body.subject.startswith('bot:') and (administrator(c,who,vault.admins) or creator(c,who,cid))):
                    name=vault.row(c,cid)['name']
                    return propose(c,who,'POST',request.url.path,{'subject':body.subject},
                                   f"Give every computer the stored credential {name}, to sign its model in" if body.subject==COMPUTERS
                                   else f"Give {body.subject} the stored credential {name}")
        return vault.change(who,request.url.path,request.headers.get('idempotency-key'),body,
                            lambda c,w:vault.grant_authority(c,w,cid,body.subject),lambda c:vault.grant(c,who,cid,body.subject))

    @app.post('/api/v2/credentials/{cid}/grants/{gid}/revoke')
    def revoke(request:Request,cid:str,gid:str):
        who=request.state.identity
        with store.transaction() as c:
            validate_identity(c,who)
            row=c.execute('SELECT subject,granted_by FROM credential_grants WHERE id=? AND credential_id=?',(gid,cid)).fetchone()
            if not row:
                require_admin(c,who,vault.admins)
                raise Problem('not_found','Grant not found',404)
            vault.revoke_authority(c,who,cid,row)
            c.execute('UPDATE credential_grants SET revoked=coalesce(revoked,?),revoked_by=? WHERE id=?',(H.now(),who.actor,gid))
            H.event(c,who.actor,'credential.revoked',cid,{'grant':gid,'subject':row['subject']})
            return {'ok':True}

    @app.delete('/api/v2/credentials/{cid}')
    def delete(request:Request,cid:str):
        who=request.state.identity
        with store.transaction() as c:
            validate_identity(c,who)
            require_admin(c,who,vault.admins)
            row=vault.row(c,cid)
            grants=c.execute('SELECT count(*) FROM credential_grants WHERE credential_id=?',(cid,)).fetchone()[0]
            c.execute('DELETE FROM credential_grants WHERE credential_id=?',(cid,))
            c.execute('DELETE FROM credentials WHERE id=?',(cid,))
            H.event(c,who.actor,'credential.deleted',cid,{'name':row['name'],'env':row['env'],'grants':grants})
            return {'deleted':True,'id':cid}

    @app.post('/api/v2/credentials/{cid}/reveal')
    def reveal(request:Request,cid:str):
        with store.transaction() as c:
            return vault.reveal(c,request.state.identity,cid)

    @app.get('/api/v2/credential-runtime')
    def runtime(request:Request):
        who=request.state.identity
        if who.role!='bot':raise Problem('forbidden','An active bot run is required',403)
        with store.transaction() as c:
            validate_identity(c,who)
            values=[]
            held=granted(c,who.actor)
            for row in c.execute('SELECT * FROM credentials ORDER BY id'):
                if row['ciphertext'] is not None and row['id'] in held:
                    values.append({'id':row['id'],'name':row['name'],'env':row['env'],'kind':row['kind'],**vault.reveal(c,who,row['id'])})
            return {'credentials':values}

    @app.get('/api/v2/runner-watcher-credentials')
    def watcher_credentials(request:Request,bot:str):
        who=request.state.identity
        with store.transaction() as c:
            migration_runner(c,who)
            if not c.execute('SELECT 1 FROM assignments a JOIN bots b ON b.slug=a.bot '
                             'WHERE a.runner_id=? AND a.bot=? AND b.state=\'active\'',(who.runner_id,bot)).fetchone():
                raise Problem('forbidden','That active bot is not on this Computer',403)
            values=[]
            held=granted(c,'bot:'+bot)
            for row in c.execute('SELECT * FROM credentials ORDER BY id'):
                if row['ciphertext'] is not None and row['env'] and row['id'] in held:
                    values.append({'id':row['id'],'env':row['env'],'kind':row['kind'],'value':vault.cipher.decrypt(c,row)})
                    H.event(c,who.actor,'credential.sent_to_computer',row['id'],{'bot':bot,'runner':who.runner_id,'use':'watcher'})
            return {'credentials':values}

    @app.get('/api/v2/runner-model-credentials')
    def model_credentials(request:Request,runtime:str):
        """The model key or token the company gave every computer, for a runner whose model CLI is not signed in.
        Only credentials granted to `computers` and named for this runtime: never one granted to a bot."""
        who=request.state.identity
        names=providers.MODEL_KEYS.get(runtime)
        if who.role!='runner':raise Problem('forbidden','Only a registered computer may ask for this',403)
        if not names:raise Problem('runtime','That runtime has no key or token to sign in with',422)
        with store.transaction() as c:
            if not c.execute('SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL',(who.runner_id,)).fetchone():
                raise Problem('forbidden','Only a registered computer may ask for this',403)
            found={}
            held=granted(c,COMPUTERS)
            for row in c.execute('SELECT * FROM credentials WHERE ciphertext IS NOT NULL ORDER BY id'):
                if row['env'] in names and row['env'] not in found and row['id'] in held:
                    found[row['env']]=(row['id'],vault.cipher.decrypt(c,row))
            for env,(cid,_) in found.items():
                H.event(c,who.actor,'credential.sent_to_computer',cid,{'env':env,'runner':who.runner_id})
            return {'credentials':[{'env':env,'value':found[env][1]} for env in names if env in found]}
