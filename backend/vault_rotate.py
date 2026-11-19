# pyright: strict
"""Move every stored secret to a fresh AWS KMS data key and retire the key files: `python -m backend.manage vault-rotate`.

Run with the server STOPPED (setup/cell.py `rewrap` runs it in a one-off container on the company's volume and env). A
hosted company's secrets were sealed with a plain data key in /data/credential.key (and the GitHub App's and directory
sync's with /data/github-app.key), both backed up beside the database, so an old copy of either opened every later
backup. Wrapping the same key with KMS would not change that. This step:

1. needs TICO_CREDENTIAL_KMS_KEY (else exit 2, naming it);
2. opens the current data key (the file's, or the KMS-wrapped one) and github-app.key if it is there;
3. asks KMS for a FRESH data key under the company's encryption context (credentials.kms_context);
4. in ONE transaction re-seals with it every secret in REGISTRY (every `credentials` row, every `CredentialCipher.seal`
   value, everything github-app.key or the vault key seals in backend/github_app.py and backend/directory.py), re-keys
   the persisted HMAC fingerprints it can, scans every text column of every table for a sealed value the registry does
   not know, and replaces the key row with the fresh wrapped key and the marker 'vault:rotated-at'. A secret that does
   not open, or a sealed value nobody registered, rolls it all back: nothing changes, and the report names each kind and
   its count (never a value);
5. from a fresh connection, opens every registered secret with the key KMS unwraps from the database alone; only then
   deletes credential.key and github-app.key beside the database and sets 'vault:key-files-retired'. A secret that does
   not open then deletes nothing (exit 3).

Run again after an interruption: with 'vault:rotated-at' set and the key row wrapped by the configured KMS key, the
rotation is skipped and the step resumes at 5. Both markers set and no key file left: 'already_done'.

Keys derived by HMAC from the data key change with it. Persisted ones are re-keyed here where their input can be read
from sealed data (`_rekey_recipients`); the others are short-lived and simply stop matching, which is safe:
- backend/tool_proxy.py `_signing_key`: proxy tokens of runs in flight (none while the server is stopped; a run's next
  token is minted with the new key).
- backend/credentials.py `Vault.change` idempotency fingerprints: a retry of a request sent before the step, with the
  same Idempotency-Key, is refused as a conflict instead of replayed (idempotency rows expire; `compact` drops them).
- backend/webhooks.py `destination_change`: destination fingerprints shown on a held edit and kept in its audit event; an
  edit held across the step is shown again.
- backend/finance/store/tico.py `fingerprint` in an action still waiting for approval (a recipient's `details_fp`, a
  budget change's `raise_check`): the payload is what was approved, so it is not rewritten; see `_rekey_recipients`.
"""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Protocol, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .credentials import (
    CONTEXT,
    KEY_CHECK,
    KEY_FILE,
    KEY_FILES_RETIRED,
    LOCAL,
    ROTATED_AT,
    SEALED,
    CredentialCipher,
)
from . import credentials as _credentials
from .store import Problem

Row = sqlite3.Row
Conn = sqlite3.Connection
Counts = dict[str, int]
Items = Callable[[Row, Any], Iterable[tuple[Any, str, str]]]


class _Cipher(Protocol):
    """What the step uses of `CredentialCipher` (backend/credentials.py is not typed)."""

    key_id: str
    key_file: Path
    context: dict[str, str]

    def unwrap(self, wrapped: bytes, context: dict[str, str] | None = None) -> bytes: ...

    def generate(self) -> tuple[bytes, bytes]: ...


_legacy_key = cast("Callable[[Path], bytes | None]", _credentials.legacy_key)

GITHUB_KEY = "github-app.key"
SLACK_KEY = "slack.key"  # an older server's Slack row (backend/slack_app.py); read here, never deleted
DONE, ALREADY, STOPPED = "done", "already_done", "stopped"
EXIT = {DONE: 0, ALREADY: 0, STOPPED: 3}
CONFIG_ERROR = 2


class Stop(Exception):
    """The step stops with nothing deleted. `code` 2 is a configuration error, 3 anything else."""

    def __init__(self, reason: str, failed: Counts | None = None, code: int = 3) -> None:
        super().__init__(reason)
        self.reason: str = reason
        self.failed: Counts = failed or {}
        self.code: int = code


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def looks_sealed(value: object) -> bool:
    """Whether text is a `CredentialCipher.seal` value: the prefix, then base64 of a nonce and at least a GCM tag. Prose
    that merely starts with the prefix (a comment about this code) is not one."""
    if not isinstance(value, str) or not value.startswith(SEALED):
        return False
    try:
        return len(base64.b64decode(value[len(SEALED) :], validate=True)) >= 28
    except ValueError:
        return False


def _open_sealed(key: bytes, context: str, stored: str) -> bytes:
    blob = base64.b64decode(stored[len(SEALED) :])
    return AESGCM(key).decrypt(blob[:12], blob[12:], b"tico-sealed:v1:" + context.encode())


def _seal(key: bytes, context: str, plain: bytes) -> str:
    nonce = os.urandom(12)
    return (
        SEALED
        + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plain, b"tico-sealed:v1:" + context.encode())).decode()
    )


def _columns(c: Conn, table: str) -> set[Any]:
    return {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}


def _has(c: Conn, table: str, *columns: str) -> bool:
    found = _columns(c, table)
    return bool(found) and set(columns) <= found


# ---------------------------------------------------------------------------------------------------------- registry
# Every place a secret is sealed with the vault's data key or github-app.key. Each entry walks its rows with a `Pass`:
# in the rotation it opens each value with the current key (or a key file) and writes it back sealed with the fresh key;
# in the check after it opens each value with the key KMS unwraps, writing nothing.


class Raw:
    """Rows sealed whole with AESGCM: a `nonce` and a `ciphertext` column, and an AAD per row. `legacy` names the key
    files a row may still be sealed with (from before TICO_CREDENTIAL_KMS_KEY was set)."""

    def __init__(
        self,
        kind: str | Callable[[Row], str],
        table: str,
        key: str,
        aad: Callable[[Row], bytes],
        legacy: tuple[str, ...] = (),
    ) -> None:
        self.kind, self.table, self.key, self.aad, self.legacy = kind, table, key, aad, legacy

    def run(self, c: Conn, step: "Pass") -> None:
        if not _has(c, self.table, self.key, "nonce", "ciphertext"):
            return
        for row in c.execute(f"SELECT * FROM {self.table} WHERE ciphertext IS NOT NULL").fetchall():
            kind = self.kind(row) if callable(self.kind) else self.kind
            aad = self.aad(row)
            plain = step.open_raw(kind, row["nonce"], row["ciphertext"], aad, self.legacy)
            if plain is None or not step.writing:
                continue
            nonce = os.urandom(12)
            c.execute(
                f"UPDATE {self.table} SET ciphertext=?,nonce=? WHERE {self.key}=?",
                (AESGCM(cast("bytes", step.fresh)).encrypt(nonce, plain, aad), nonce, row[self.key]),
            )
            step.resealed(kind)


class Column:
    """A text column holding a `CredentialCipher.seal` value; `context(row)` is what it was sealed for."""

    def __init__(
        self, kind: str, table: str, key: str, column: str, context: Callable[[Row], str], needs: tuple[str, ...] = ()
    ) -> None:
        self.kind, self.table, self.key, self.column, self.context = kind, table, key, column, context
        self.needs = needs

    def run(self, c: Conn, step: "Pass") -> None:
        if not _has(c, self.table, self.key, self.column, *self.needs):
            return
        rows = c.execute(
            f"SELECT * FROM {self.table} WHERE typeof({self.column})='text' AND substr({self.column},1,?)=?",
            (len(SEALED), SEALED),
        ).fetchall()
        for row in rows:
            step.covered(self.table, self.column)
            context = self.context(row)
            plain = step.open_sealed(self.kind, context, row[self.column])
            if plain is None or not step.writing:
                continue
            c.execute(
                f"UPDATE {self.table} SET {self.column}=? WHERE {self.key}=?",
                (_seal(cast("bytes", step.fresh), context, plain), row[self.key]),
            )
            step.resealed(self.kind)


class Json:
    """Sealed values inside a JSON column. `items(row, doc)` yields (holder, field, context) for each value that may be
    sealed; a sealed one is replaced in place and the document written back."""

    def __init__(
        self,
        kind: str,
        table: str,
        key: str,
        column: str,
        items: Items,
        where: str = "1=1",
        params: tuple[Any, ...] = (),
    ) -> None:
        self.kind, self.table, self.key, self.column, self.items = kind, table, key, column, items
        self.where, self.params = where, params

    def run(self, c: Conn, step: "Pass") -> None:
        if not _has(c, self.table, self.key, self.column):
            return
        for row in c.execute(f"SELECT * FROM {self.table} WHERE {self.where}", self.params).fetchall():
            try:
                doc = json.loads(row[self.column] or "null")
            except (TypeError, ValueError):
                continue
            changed = False
            for holder, field, context in self.items(row, doc):
                stored = cast("dict[str, Any]", holder).get(field) if isinstance(holder, dict) else None
                if not isinstance(stored, str) or not stored.startswith(SEALED):
                    continue
                step.covered(self.table, self.column)
                plain = step.open_sealed(self.kind, context, stored)
                if plain is not None and step.writing:
                    holder[field] = _seal(cast("bytes", step.fresh), context, plain)
                    step.resealed(self.kind)
                    changed = True
            if changed:
                c.execute(
                    f"UPDATE {self.table} SET {self.column}=? WHERE {self.key}=?",
                    (json.dumps(doc, sort_keys=self.table == "registry_metadata"), row[self.key]),
                )


def registry() -> list[Raw | Column | Json]:
    """The entries, built from each owner module's own context function, so a context changed there is followed here.
    Imported on use: the step runs with the server stopped, never inside it."""
    from . import (
        company_server,
        company_storage,
        directory,
        gateway_connection,
        github_app,
        google_tokens,
        google_workspace,
        growth_sync,
        mcp_connect,
        model_subscriptions,
        support,
        tico_storage,
        triggers,
        web_push,
        webhook_delivery,
    )

    # The owner modules are not all typed: each context function is used with the type it has.
    webhook_sealed_as = cast("Callable[..., str]", webhook_delivery.sealed_as)
    trigger_sealed_as = cast("Callable[[str], str]", triggers.sealed_as)
    support_context = cast("Callable[[str], str]", support._context)  # pyright: ignore[reportPrivateUsage]
    tool_context = cast("Callable[[str], str]", mcp_connect._secret_context)  # pyright: ignore[reportPrivateUsage]
    storage_context = cast("Callable[[str], str]", company_storage._context)  # pyright: ignore[reportPrivateUsage]
    subscription_context = cast("Callable[[str], str]", model_subscriptions._context)  # pyright: ignore[reportPrivateUsage]
    workspace_context = cast("Callable[[str], str]", google_workspace._context)  # pyright: ignore[reportPrivateUsage]

    def headers(row: Row, doc: Any) -> Iterator[tuple[Any, str, str]]:
        for h in cast("list[Any]", doc) if isinstance(doc, list) else []:
            if not isinstance(h, dict):
                continue
            header = cast("dict[str, Any]", h)
            if header.get("secret"):
                yield header, "value", f"{webhook_sealed_as(row['id'])}:header:{header.get('name')}"

    def record(fields: list[tuple[str, str]]) -> Items:
        # registry_metadata records: a field sealed in the record itself (from before D11-11, migration
        # 20261117103033 moves it to a service row) or web push's key.
        return lambda row, doc: (
            ((cast("dict[str, Any]", doc), f, ctx) for f, ctx in fields) if isinstance(doc, dict) else ()
        )

    gateway = [(field, ctx) for field, (_, _, ctx) in gateway_connection.SECRETS.items()]
    return [
        Raw(
            lambda r: "credentials:" + str(r["kind"] or "credential"),
            "credentials",
            "id",
            lambda r: ("tico-credential:v1:" + r["id"]).encode(),
        ),
        Raw("github_app", "github_app", "id", lambda r: b"tico-github-app:v1", (GITHUB_KEY,)),
        Raw("github_user_token", "github_user_token", "id", lambda r: github_app.USER_AAD, (GITHUB_KEY,)),
        Raw(
            "directory_credentials",
            "directory_credentials",
            "source",
            lambda r: directory.AAD + str(r["source"]).encode(),
            (GITHUB_KEY,),
        ),
        Raw("slack_credentials", "slack_credentials", "id", lambda r: b"tico-slack-app:v1", (SLACK_KEY,)),
        Column("support_tickets", "support_tickets", "id", "hq_secret", lambda r: support_context(r["id"])),
        Column(
            "google_connects",
            "google_connects",
            "person_id",
            "grant_sealed",
            lambda r: google_tokens.context(str(r["person_id"]), str(r["grant_operation_id"]), str(r["mailbox"])),
            ("grant_operation_id", "mailbox"),
        ),
        Column("webhooks", "webhooks", "id", "secret", lambda r: webhook_sealed_as(r["id"])),
        Column("webhooks", "webhooks", "id", "secret_prev", lambda r: webhook_sealed_as(r["id"], True)),
        Json("webhooks", "webhooks", "id", "headers", headers),
        Column("triggers", "triggers", "id", "signing_secret", lambda r: trigger_sealed_as(r["id"])),
        Column("tico_storage", "tico_storage", "id", "secret", lambda r: tico_storage.CONTEXT),
        Column(
            "growth_source_secrets",
            "growth_source_secrets",
            "source_id",
            "signing_secret",
            lambda r: growth_sync.sealed_as(r["source_id"]),
        ),
        Column("tools", "tools", "id", "mcp_client_secret", lambda r: tool_context(r["id"])),
        Column("tool_oauth_states", "tool_oauth_states", "id", "verifier", lambda r: "tool_oauth_states:" + r["id"]),
        Column("company_storage", "company_storage", "id", "secret", lambda r: storage_context(r["id"])),
        Column(
            "model_subscriptions",
            "model_subscriptions",
            "id",
            "sealed",
            lambda r: subscription_context(r["id"]),
        ),
        Column("google_workspaces", "google_workspaces", "id", "key_sealed", lambda r: workspace_context(r["id"])),
        Column(
            "google_oauth_clients",
            "google_oauth_clients",
            "singleton",
            "secret_sealed",
            lambda r: google_workspace.OAUTH_CONTEXT,
        ),
        Json(
            "web_push",
            "registry_metadata",
            "key",
            "value",
            record([("private", web_push.CONTEXT)]),
            "key=?",
            (web_push.KEY_ROW,),
        ),
        Json(
            "gateway-connection",
            "registry_metadata",
            "key",
            "value",
            record(gateway),
            "key=?",
            (gateway_connection.KEY,),
        ),
        Json(
            "company-server",
            "registry_metadata",
            "key",
            "value",
            record([("key", company_server.CONTEXT)]),
            "key=?",
            (company_server.KEY,),
        ),
    ]


class Pass:
    """One walk of the registry. `old` opens what the vault sealed, `legacy` ({file name: key}) what a key file sealed;
    with `fresh` set every opened value is written back sealed with it."""

    def __init__(self, old: bytes | None, legacy: dict[str, bytes], fresh: bytes | None = None) -> None:
        self.old, self.legacy, self.fresh = old, legacy, fresh
        self.failed: Counts = {}
        self.done: Counts = {}
        self.cover: dict[tuple[str, str], int] = {}
        self.plain: dict[str, bytes] = {}

    @property
    def writing(self) -> bool:
        return self.fresh is not None

    def fail(self, kind: str) -> None:
        self.failed[kind] = self.failed.get(kind, 0) + 1

    def resealed(self, kind: str) -> None:
        self.done[kind] = self.done.get(kind, 0) + 1

    def covered(self, table: str, column: str) -> None:
        self.cover[(table, column)] = self.cover.get((table, column), 0) + 1

    def open_raw(self, kind: str, nonce: Any, ciphertext: Any, aad: bytes, legacy: tuple[str, ...]) -> bytes | None:
        keys = [self.old] + [self.legacy.get(name) for name in legacy]
        for key in keys:
            if key is None:
                continue
            try:
                plain = AESGCM(key).decrypt(bytes(nonce), bytes(ciphertext), aad)
            except Exception:  # noqa: BLE001 - a key that does not open: the next one is tried
                continue
            if aad.startswith(b"tico-credential:v1:"):
                self.plain[aad[len(b"tico-credential:v1:") :].decode()] = plain  # for `_rekey_recipients`
            return plain
        self.fail(kind)
        return None

    def open_sealed(self, kind: str, context: str, stored: str) -> bytes | None:
        try:
            return _open_sealed(
                cast("bytes", self.old), context, stored
            )  # no key (None) fails here, as one that does not open
        except Exception:  # noqa: BLE001 - no key, or one that does not open it: a failure, named by kind
            self.fail(kind)
            return None

    def walk(self, c: Conn) -> "Pass":
        for entry in registry():
            entry.run(c, self)
        return self


# ------------------------------------------------------------------------------------------------------------- guard
def uncovered(c: Conn, cover: dict[tuple[str, str], int]) -> Counts:
    """{"table.column": count} of sealed values in columns the registry did not walk, or more of them than it walked.
    Every text value of every table is looked at: a value that is a sealed value, or JSON holding one."""
    found: Counts = {}
    tables = [
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND upper(COALESCE(sql,'')) NOT LIKE 'CREATE VIRTUAL%'"
        )
    ]
    for table in tables:
        for column in _columns(c, table):
            q = f'"{column}"'
            rows = c.execute(
                f'SELECT {q} FROM "{table}" WHERE typeof({q})=\'text\' AND (substr({q},1,?)=? OR instr({q},?)>0)',
                (len(SEALED), SEALED, '"' + SEALED),
            ).fetchall()
            n = 0
            for (value,) in rows:
                if looks_sealed(value):
                    n += 1
                elif value[:1] in "[{":
                    n += _sealed_in_json(value)
            if n > cover.get((table, column), 0):
                found[f"{table}.{column}"] = n - cover.get((table, column), 0)
    return found


def _sealed_in_json(text: str) -> int:
    try:
        doc = json.loads(text)
    except ValueError:
        return 0
    stack: list[Any] = [doc]
    n = 0
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(cast("dict[Any, Any]", item).values())
        elif isinstance(item, list):
            stack.extend(cast("list[Any]", item))
        elif looks_sealed(item):
            n += 1
    return n


# ----------------------------------------------------------------------------------------------- HMAC fingerprints
def _finance_fingerprint(key: bytes, context: str, value: str) -> str:
    """backend/finance/store/tico.py `fingerprint`, with a given key."""
    return hmac.new(key, f"tico-finance:{context}:".encode() + value.encode(), hashlib.sha256).hexdigest()


def _rekey_recipients(c: Conn, step: Pass, old: bytes | None) -> Counts:
    """Finance keeps a keyed fingerprint of each payee's bank details (`fin_recipients.details_hash`, HMAC with the
    vault's data key) to refuse a duplicate recipient. For a bank recipient the details are sealed in its `details_ref`
    credential, so the fingerprint is computed again with the fresh key. A check recipient's fingerprint is of its
    mailing address, which the row does not keep: it cannot be recomputed and is returned in `stale` (it only stops a
    duplicate check recipient from being refused as one)."""
    stale: Counts = {}
    if old is None or not _has(c, "fin_recipients", "id", "method", "details_hash", "details_ref"):
        return stale
    from .finance.core.feeds import details_hash

    for row in c.execute("SELECT id,method,details_hash,details_ref FROM fin_recipients").fetchall():
        plain = step.plain.get(row["details_ref"] or "")
        if row["method"] == "check" or plain is None:
            stale["fin_recipients.details_hash"] = stale.get("fin_recipients.details_hash", 0) + 1
            continue
        try:
            digest = details_hash(row["method"], json.loads(plain))
        except Exception:  # noqa: BLE001 - details that do not read as bank details: counted as stale
            stale["fin_recipients.details_hash"] = stale.get("fin_recipients.details_hash", 0) + 1
            continue
        if not hmac.compare_digest(str(row["details_hash"]), _finance_fingerprint(old, "payee", digest)):
            stale["fin_recipients.details_hash"] = stale.get("fin_recipients.details_hash", 0) + 1
            continue
        c.execute(
            "UPDATE fin_recipients SET details_hash=? WHERE id=?",
            (_finance_fingerprint(cast("bytes", step.fresh), "payee", digest), row["id"]),
        )
        step.resealed("fin_recipients.details_hash")
    return stale


# -------------------------------------------------------------------------------------------------------------- step
def _connect(db: Path) -> Conn:
    c = sqlite3.connect(str(db), isolation_level=None, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _marker(c: Conn, key: str) -> str | None:
    try:
        row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _set_marker(c: Conn, key: str, value: str) -> None:
    c.execute(
        "INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _key_row(c: Conn) -> Row | None:
    try:
        return c.execute("SELECT * FROM credential_keys WHERE id='v1'").fetchone()
    except sqlite3.Error:
        return None


def _current_key(c: Conn, cipher: _Cipher, row: Row | None) -> bytes | None:
    """The data key every vault secret is sealed with now, or None when none was ever made."""
    if row is None:
        return None
    if row["kms_key"] == LOCAL:
        try:
            key = cipher.key_file.read_bytes()
        except OSError:
            raise Stop(f"The credential key file ({KEY_FILE}) is missing beside the database") from None
        if len(key) != 32 or not hmac.compare_digest(
            hmac.new(key, KEY_CHECK, hashlib.sha256).digest(), bytes(row["wrapped_key"])
        ):
            raise Stop(f"The credential key file ({KEY_FILE}) does not belong to this database")
        return key
    if row["kms_key"] != cipher.key_id:
        raise Stop(
            "Credentials here are wrapped by another KMS key than TICO_CREDENTIAL_KMS_KEY: set the one they use",
            code=CONFIG_ERROR,
        )
    # Wrapped under this company's context; a key wrapped before TICO_CREDENTIAL_KMS_CONTEXT was set has the
    # application's alone.
    for context in [cipher.context] + ([CONTEXT] if cipher.context != CONTEXT else []):
        try:
            return cipher.unwrap(row["wrapped_key"], context)
        except Exception:  # noqa: BLE001 - another context is tried, then the step stops
            continue
    raise Stop("KMS did not unwrap the current data key (vault_unavailable): check the company's AWS access")


def _rotate(db: Path, cipher: _Cipher, folder: Path) -> tuple[Counts, Counts]:
    """Steps 2 to 4: every secret re-sealed with a fresh KMS data key in one transaction. Returns (resealed, stale)."""
    with closing(_connect(db)) as c:
        old = _current_key(c, cipher, _key_row(c))
    legacy = {name: k for name in (GITHUB_KEY, SLACK_KEY) if (k := _legacy_key(folder / name)) is not None}
    try:
        fresh, wrapped = cipher.generate()
    except Exception:  # noqa: BLE001 - KMS unreachable or denied: the step stops
        raise Stop("KMS did not make a data key (vault_unavailable): check the company's AWS access") from None
    if len(fresh) != 32:
        raise Stop("KMS returned a data key that is not 256 bits")
    c = _connect(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        step = Pass(old, legacy, fresh).walk(c)
        stale = _rekey_recipients(c, step, old)
        missed = uncovered(c, step.cover)
        if step.failed or missed:
            c.execute("ROLLBACK")
            failed = {**step.failed, **missed}
            raise Stop(
                ("Some secrets do not open with the current key" if step.failed else "")
                + (" and " if step.failed and missed else "")
                + ("Some sealed values are not in the step's registry" if missed else "")
                + ": nothing was changed",
                failed,
            )
        if c.execute("SELECT 1 FROM credential_keys WHERE id='v1'").fetchone():
            c.execute("UPDATE credential_keys SET kms_key=?,wrapped_key=? WHERE id='v1'", (cipher.key_id, wrapped))
        else:
            c.execute("INSERT INTO credential_keys VALUES(?,?,?)", ("v1", cipher.key_id, wrapped))
        _set_marker(c, ROTATED_AT, _now())
        c.execute("COMMIT")
        return step.done, stale
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def _verify(db: Path, cipher: _Cipher) -> dict[tuple[str, str], int]:
    """Step 5's check: every registered secret opens with the key KMS unwraps from the database, and nothing else."""
    c = _connect(db)
    try:
        row = _key_row(c)
        if row is None or row["kms_key"] != cipher.key_id:
            raise Stop("The data key is not wrapped by TICO_CREDENTIAL_KMS_KEY after the rotation")
        try:
            key = cipher.unwrap(row["wrapped_key"])
        except Exception:  # noqa: BLE001 - KMS unreachable or denied: the step stops
            raise Stop("KMS did not unwrap the new data key (vault_unavailable): nothing was deleted") from None
        step = Pass(key, {}).walk(c)
        missed = uncovered(c, step.cover)
        if step.failed or missed:
            raise Stop(
                "Some secrets do not open with the KMS-wrapped key: nothing was deleted", {**step.failed, **missed}
            )
        return step.cover
    finally:
        c.close()


def run(settings: Any, kms: Any = None) -> tuple[dict[str, Any], int]:
    """The step. Returns (report, exit code); the report never holds a secret."""
    report: dict[str, Any] = {"status": STOPPED, "reason": "", "failed": {}, "resealed": {}, "deleted": []}
    if not str(settings.credential_kms_key or "").strip():
        report["reason"] = "TICO_CREDENTIAL_KMS_KEY is not set: the step needs the company's KMS key"
        return report, CONFIG_ERROR
    db = Path(settings.db_path)
    if not db.is_file():
        report["reason"] = f"No database at {db}"
        return report, CONFIG_ERROR
    folder = db.parent
    cipher = cast("Callable[[Any, Any], _Cipher]", CredentialCipher.for_settings)(settings, kms)
    files = [folder / KEY_FILE, folder / GITHUB_KEY]
    try:
        with closing(_connect(db)) as c:
            rotated, retired, row = _marker(c, ROTATED_AT), _marker(c, KEY_FILES_RETIRED), _key_row(c)
        resumed = bool(rotated and row is not None and row["kms_key"] == cipher.key_id)
        if resumed and retired and not any(f.exists() for f in files):
            report.update(status=ALREADY, reason="Every secret is under a fresh KMS data key and no key file is left")
            return report, EXIT[ALREADY]
        if not resumed:
            report["resealed"], stale = _rotate(db, cipher, folder)
            if stale:
                report["stale"] = stale
        _verify(db, cipher)
        for f in files:
            try:
                f.unlink()
                report["deleted"].append(f.name)
            except FileNotFoundError:
                pass
        with closing(_connect(db)) as c:
            _set_marker(c, KEY_FILES_RETIRED, _now())
    except Stop as stop:
        report.update(reason=stop.reason, failed=stop.failed)
        return report, stop.code
    except Problem as problem:
        refused = cast("Any", problem)
        report.update(reason=f"{refused.code}: {refused.detail}")
        return report, EXIT[STOPPED]
    report.update(
        status=DONE,
        reason="Every secret is under a fresh KMS data key"
        + (" (resumed after an earlier run)" if resumed else "")
        + "; the key files are deleted",
    )
    return report, EXIT[DONE]
