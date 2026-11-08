"""`hub db`: read-only queries against a team database, run on the computer that holds the credential.

  hub db list                                    the databases this bot may use
  hub db doctor [name]                           check the grant, the credential, the connection, read-only
  hub db <name> "<select>" [--param k=v ...]     one read-only statement
  hub db <name> --query <id> [--param k=v ...]   a named query from the team's catalog
  hub db <name> find|aggregate|count|distinct|collections ...   a MongoDB database (clients/dbmongo.py)

Queries execute beside the bot, never on the server. Tico stores the encrypted Credential and
delivers it only to a granted bot for the run; database access also needs network reachability
from that computer. The rules are enforced here, in layers: the bot must declare the database under
`tools:` in `bot.yaml` (older: `access:` in `employee.yaml`); the statement must be one SELECT-like statement; the session is
opened read-only; a row cap and a timeout apply; credentials are scrubbed from every message;
and each query is recorded in Tico (statement, row count, time, no result data) before its
rows are shown. The database role's own permissions are still the first line of defence:
`docs/databases.md` says how to create a read-only one.
"""
import base64
import datetime
import decimal
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from clients.manifest import manifest_path, repo_dir, tools_of
from clients.tico import APIError

DEFAULT_MAX_ROWS, MAX_ROWS_CEILING = 500, 10_000
DEFAULT_TIMEOUT, TIMEOUT_CEILING = 20, 120
AUDIT_STATEMENT_CHARS = 4000
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
RESERVED = ("list", "doctor")
# `service:` values in `access:` that declare a database; the URL scheme decides the driver.
SERVICES = ("postgres", "mysql", "sqlite", "mongodb", "database")
SCHEMES = {"postgres": "postgres", "postgresql": "postgres", "mysql": "mysql", "mariadb": "mysql",
           "mysql+pymysql": "mysql", "sqlite": "sqlite", "mongodb": "mongodb", "mongodb+srv": "mongodb"}
READ_WORDS = {"postgres": ("select", "with", "values", "table", "show", "explain"),
              "mysql": ("select", "with", "values", "table", "show", "explain", "describe", "desc"),
              "sqlite": ("select", "with", "values", "explain")}
CREDENTIAL_IN_TEXT = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]*):[^\s/@]+@")


class Refusal(Exception):
    """A rule said no; the message names the rule and the way forward."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass
class Database:
    name: str
    kind: str
    url: str = field(repr=False)
    max_rows: int = DEFAULT_MAX_ROWS
    timeout: int = DEFAULT_TIMEOUT
    options: dict = field(default_factory=dict)      # per-bot settings the driver needs (MongoDB: read_preference)

    def redact(self, text):
        return redact(text, self.url)


# ----------------------------------------------------------------------------- redaction
def redact(text, url=""):
    """`text` without the connection string, its password, or any `scheme://user:password@` pair."""
    text = str(text)
    secrets = {url}
    try:
        parts = urlsplit(url)
        for password in (parts.password or "",):
            secrets |= {password, unquote(password), quote(password, safe="")}
    except ValueError:
        pass
    for secret in sorted((s for s in secrets if s and len(s) >= 3), key=len, reverse=True):
        text = text.replace(secret, "***")
    return CREDENTIAL_IN_TEXT.sub(r"\1:***@", text)


# ----------------------------------------------------------------------------- SQL scanning
def tokens(sql, kind):
    """Split `sql` into (type, text) pieces: code, string, ident, comment.

    Quoting differs by database, and a scanner that disagrees with the server about where a
    string ends could hide a second statement, so each dialect gets its own rules."""
    i, n, out, start = 0, len(sql), [], 0
    mysql, pg = kind == "mysql", kind == "postgres"

    def emit(type_, end):
        nonlocal start
        if start < end:
            out.append((type_, sql[start:end]))
        start = end

    while i < n:
        c = sql[i]
        if c == "'" or (pg and c in "eE" and sql[i + 1:i + 2] == "'" and not (i and (sql[i - 1].isalnum() or sql[i - 1] == "_"))):
            emit("code", i)
            escapes = mysql or c in "eE"
            i += 2 if c in "eE" else 1
            while i < n:
                if escapes and sql[i] == "\\":
                    i += 2
                elif sql[i] == "'":
                    if sql[i + 1:i + 2] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                else:
                    i += 1
            else:
                raise Refusal("refused", "an unterminated string literal")
            emit("string", min(i, n))
        elif c == '"' or (mysql and c == "`"):
            emit("code", i)
            i += 1
            while i < n and not (sql[i] == c and sql[i + 1:i + 2] != c):
                i += 2 if sql[i] == c else 1
            if i >= n:
                raise Refusal("refused", "an unterminated quoted name")
            i += 1
            emit("ident", i)
        elif c == "-" and sql[i + 1:i + 2] == "-" and (not mysql or sql[i + 2:i + 3] in ("", " ", "\t", "\n", "\r")):
            emit("code", i)
            end = sql.find("\n", i)
            i = n if end < 0 else end
            emit("comment", i)
        elif c == "#" and mysql:
            emit("code", i)
            end = sql.find("\n", i)
            i = n if end < 0 else end
            emit("comment", i)
        elif c == "/" and sql[i + 1:i + 2] == "*":
            if mysql and sql[i + 2:i + 3] in ("!", "+"):
                raise Refusal("refused", "MySQL executable or optimizer comments (/*! and /*+) are not allowed")
            emit("code", i)
            depth = 1
            i += 2
            while i < n and depth:
                if sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                elif pg and sql.startswith("/*", i):
                    depth += 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise Refusal("refused", "an unterminated comment")
            emit("comment", i)
        elif c == "$" and pg:
            match = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$").match(sql, i)
            if match and not (i and (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                end = sql.find(match.group(0), match.end())
                if end < 0:
                    raise Refusal("refused", "an unterminated dollar-quoted string")
                emit("code", i)
                i = end + len(match.group(0))
                emit("string", i)
            else:
                i += 1
        else:
            i += 1
    emit("code", n)
    return out


def check_statement(sql, kind):
    """The statement without its trailing `;`, after the shape rules; raises Refusal.

    One statement, starting with a read verb, and no `INTO` (which writes files or tables).
    These rules turn a mistake into a clear message; the read-only session and the database
    role are what actually stop a write."""
    pieces = tokens(sql, kind)
    words, semicolons = [], 0
    second = Refusal("refused", "one statement per call; a second statement follows the `;`")
    for type_, text in pieces:
        if type_ == "code":
            for part in re.split(r"(;)", text):
                if part == ";":
                    semicolons += 1
                elif part.strip():
                    if semicolons:
                        raise second
                    words += re.findall(r"[A-Za-z_]+", part.lower())
        elif type_ in ("string", "ident") and semicolons:
            raise second
    if semicolons > 1:
        raise Refusal("refused", "one statement per call")
    if not words:
        raise Refusal("refused", "the statement is empty")
    allowed = READ_WORDS[kind]
    if words[0] not in allowed:
        raise Refusal("read_only", f"only read statements run here ({', '.join(allowed)}); this starts with `{words[0]}`. "
                                   "Writes are never available through `hub db`.")
    if "into" in words:
        raise Refusal("read_only", "`INTO` writes a table or a file and is not allowed; select the rows instead")
    body = "".join(text for _, text in pieces).rstrip()
    return body[:-1].rstrip() if body.endswith(";") else body


def bind(sql, kind, values, order=()):
    """Translate `:name` and `$N` placeholders to the driver's own style.

    Returns (sql, args). `$N` is the N-th parameter of a named query (its `params` order), so a
    catalog written with `$1`, `$2` runs unchanged; `:name` is the portable spelling."""
    named = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)(?!:)|\$(\d+)")
    marker = "?" if kind == "sqlite" else "%s"
    percent = marker == "%s"
    args, missing, out = [], [], []
    for type_, text in tokens(sql, kind):
        if type_ != "code":
            out.append(text.replace("%", "%%") if percent else text)
            continue
        last = 0
        for m in named.finditer(text):
            out.append(text[last:m.start()].replace("%", "%%") if percent else text[last:m.start()])
            last = m.end()
            if m.group(1) is not None:
                key = m.group(1)
            else:
                index = int(m.group(2)) - 1
                key = order[index] if index < len(order) else None
            if key is None or key not in values:
                missing.append(key or "$" + m.group(2))
                out.append(m.group(0))
                continue
            args.append(values[key])
            out.append(marker)
        out.append(text[last:].replace("%", "%%") if percent else text[last:])
    if missing:
        raise Refusal("params", "no value for " + ", ".join(sorted(set(missing))) + " (pass --param name=value)")
    joined = "".join(out)
    if not args and marker == "%s":
        joined = joined.replace("%%", "%")           # no arguments: the driver does no formatting
    return joined, args


def coerce(text, type_):
    """A `--param` string as the catalog's declared type; text stays text."""
    if not isinstance(text, str):
        return text
    try:
        if type_ in ("int", "integer"):
            return int(text)
        if type_ in ("number", "float", "numeric", "decimal"):
            return decimal.Decimal(text)
        if type_ in ("bool", "boolean"):
            return text.strip().lower() in ("1", "true", "yes", "t", "y")
        if type_ == "date":
            return datetime.date.fromisoformat(text)
        if type_ in ("datetime", "timestamp"):
            return datetime.datetime.fromisoformat(text)
    except ValueError:
        raise Refusal("params", f"`{text}` is not a valid {type_}") from None
    if type_ == "text" or not type_:
        if re.fullmatch(r"-?\d+", text):
            return int(text)
    return text


# ----------------------------------------------------------------------------- drivers
def jsonable(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, default=str))
    if isinstance(value, datetime.timedelta):
        return str(value)
    return value


def classify(exc, kind):
    """(code, detail) for a driver error, before redaction."""
    text = str(exc).strip() or type(exc).__name__
    name = type(exc).__name__
    if kind == "postgres":
        if name in ("QueryCanceled", "LockNotAvailable"):
            return "timeout", "the statement ran past the timeout and was canceled"
        if name in ("ReadOnlySqlTransaction", "InsufficientPrivilege") or "data-modifying" in text:
            return "read_only", text.splitlines()[0]
    elif kind == "mysql":
        code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else 0
        if code in (3024, 1969, 2013, 2006) or "timed out" in text.lower():
            return "timeout", "the statement ran past the timeout"
        if code in (1792, 1142, 1044, 1290):
            return "read_only", text
    elif kind == "mongodb":
        from clients import dbmongo
        return dbmongo.classify(exc)
    elif kind == "sqlite":
        if "interrupted" in text:
            return "timeout", "the statement ran past the timeout"
        if "not authorized" in text or "readonly" in text:
            return "read_only", text
    return "db_error", f"{name}: {text.splitlines()[0] if text else ''}"


def driver(kind):
    try:
        if kind == "postgres":
            import psycopg
            return psycopg
        if kind == "mysql":
            import pymysql
            return pymysql
    except ImportError:
        package = {"postgres": "psycopg[binary]", "mysql": "PyMySQL"}[kind]
        raise Refusal("driver", f"the {kind} driver is not installed on this computer; "
                                f"run `pip install '{package}'` in the runner's environment "
                                "(it is in backend/requirements.txt)") from None
    return sqlite3


def run_postgres(db, sql, args, max_rows, timeout):
    psycopg = driver("postgres")
    ms = int(timeout * 1000)
    conn = psycopg.connect(db.url, connect_timeout=10, application_name="tico-db")
    try:
        conn.read_only = True                         # BEGIN READ ONLY, so a write fails in the server
        with conn.cursor() as setup:
            # SET LOCAL survives a transaction pooler; a startup option would not.
            setup.execute(f"SET LOCAL statement_timeout = {ms}")
            setup.execute(f"SET LOCAL lock_timeout = {ms}")
        first = re.match(r"\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*([A-Za-z]+)", sql, re.S)
        streamed = (first.group(1).lower() if first else "") in ("select", "with", "values", "table")
        # A server-side cursor keeps a huge result on the server; only max_rows + 1 rows travel.
        with conn.cursor(name="tico_db") if streamed else conn.cursor() as cur:
            cur.execute(sql, args or None)
            columns = [d.name for d in (cur.description or [])]
            rows = cur.fetchmany(max_rows + 1)
        return columns, rows
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def mysql_connect(db, timeout):
    pymysql = driver("mysql")
    parts = urlsplit(db.url)
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    options = {"host": parts.hostname or "localhost", "port": parts.port or 3306, "user": unquote(parts.username or ""),
               "password": unquote(parts.password or ""), "database": unquote(parts.path.lstrip("/")) or None,
               "connect_timeout": 10, "read_timeout": timeout + 5, "write_timeout": timeout + 5, "autocommit": False,
               "charset": query.get("charset", "utf8mb4"), "cursorclass": pymysql.cursors.SSCursor}
    if query.get("ssl_ca"):
        options["ssl"] = {"ca": query["ssl_ca"]}
    elif query.get("ssl", query.get("tls", "")).lower() in ("1", "true", "required", "yes"):
        import ssl
        options["ssl"] = ssl.create_default_context()
    return pymysql.connect(**options)


def run_mysql(db, sql, args, max_rows, timeout):
    conn = mysql_connect(db, timeout)
    try:
        cur = conn.cursor()
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        for setting in (f"SET SESSION max_execution_time = {int(timeout * 1000)}",       # MySQL, milliseconds
                        f"SET SESSION max_statement_time = {int(timeout)}"):             # MariaDB, seconds
            try:
                cur.execute(setting)
                break
            except Exception:
                continue
        cur.execute("START TRANSACTION READ ONLY")
        cur.execute(sql, args or None)
        columns = [d[0] for d in (cur.description or [])]
        rows = list(cur.fetchmany(max_rows + 1))
        if len(rows) > max_rows:
            # Closing a streaming cursor would read the rest of the result; drop the socket instead.
            getattr(conn, "_force_close", conn.close)()
            # Neither the cursor nor the result may try to drain a socket that is gone.
            cur.connection = None
            if getattr(conn, "_result", None) is not None:
                conn._result.unbuffered_active = False
        else:
            conn.rollback()
        return columns, rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sqlite_path(url):
    """The file behind `sqlite:///relative.db`, `sqlite:////absolute.db` or a bare path."""
    for prefix in ("sqlite:///", "sqlite://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    return unquote(url.split("?", 1)[0])


def sqlite_connect(db, timeout):
    path = sqlite_path(db.url)
    conn = sqlite3.connect("file:" + quote(path) + "?mode=ro", uri=True, timeout=min(timeout, 10), isolation_level=None)
    conn.execute("PRAGMA query_only = ON")
    # Reads only: the authorizer refuses ATTACH, PRAGMA and every write before the statement runs.
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    deadline = time.monotonic() + timeout
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    return conn


def run_sqlite(db, sql, args, max_rows, timeout):
    conn = sqlite_connect(db, timeout)
    try:
        cur = conn.execute(sql, args)
        columns = [d[0] for d in (cur.description or [])]
        return columns, cur.fetchmany(max_rows + 1)
    finally:
        conn.close()


RUNNERS = {"postgres": run_postgres, "mysql": run_mysql, "sqlite": run_sqlite}


def execute(db, sql, values=None, order=(), max_rows=None, timeout=None):
    """Run one read-only statement on `db`; returns the result dict `hub sql` returns.

    Raises Refusal for a rule and for a database error (its message scrubbed of credentials)."""
    max_rows = min(int(max_rows or db.max_rows), db.max_rows, MAX_ROWS_CEILING)
    timeout = min(float(timeout or db.timeout), db.timeout, TIMEOUT_CEILING)
    statement = check_statement(sql, db.kind)
    bound, args = bind(statement, db.kind, values or {}, order)
    started = time.monotonic()
    try:
        columns, rows = RUNNERS[db.kind](db, bound, args, max_rows, timeout)
    except Refusal:
        raise
    except Exception as exc:                                          # noqa: BLE001 - every driver has its own
        code, detail = classify(exc, db.kind)
        raise Refusal(code, db.redact(detail)) from None
    truncated = len(rows) > max_rows
    rows = [[jsonable(v) for v in row] for row in rows[:max_rows]]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
            "ms": int((time.monotonic() - started) * 1000)}


# ----------------------------------------------------------------------------- grants
def env_name(name):
    return "DB_" + name.upper().replace("-", "_") + "_URL"


def kind_of(url):
    if url.startswith("/"):
        return "sqlite"
    scheme = urlsplit(url).scheme.lower()
    if scheme not in SCHEMES:
        raise Refusal("url", "the connection string must start with postgresql://, mysql://, mongodb+srv://, mongodb:// or sqlite:///")
    return SCHEMES[scheme]


def employee_manifest(slug, environ=None):
    import yaml
    environ = environ if environ is not None else os.environ
    candidates = []
    if environ.get("HUB_WORKSPACE"):
        candidates.append(manifest_path(repo_dir(environ["HUB_WORKSPACE"], slug)))
    candidates.append(manifest_path(Path.cwd()))
    for path in candidates:
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    raise Refusal("grant", f"no bot.yaml found for {slug}; a bot declares its databases under `tools:` there")


def declared(manifest):
    """{name: tool entry} for every database a bot.yaml declares (under `tools:`, older `access:`)."""
    found = {}
    for entry in (tools_of(manifest) or []):
        if isinstance(entry, dict) and str(entry.get("service", "")).lower() in SERVICES and entry.get("database"):
            found[str(entry["database"])] = entry
    return found


def limit(entry, key, default, ceiling):
    try:
        return max(1, min(int(entry.get(key) or default), ceiling))
    except (TypeError, ValueError):
        return default


def open_database(name, actor, environ=None):
    """The Database `actor` may use under `name`, with its credential from the environment.

    A bot needs a declared `tools:` entry; a person running `hub db` on their own computer
    needs the credential in their own environment, which is their grant."""
    environ = environ if environ is not None else os.environ
    if not NAME.match(name) or name in RESERVED:
        raise Refusal("usage", f"`{name}` is not a database name (lower-case letters, digits, - and _)")
    entry = {}
    if actor.startswith("bot:"):
        entries = declared(employee_manifest(actor[4:], environ))
        if name not in entries:
            have = ", ".join(sorted(entries)) or "none"
            raise Refusal("grant", f"{actor} does not declare database `{name}` (declared: {have}). Add a `tools:` entry with "
                                   f"`service: postgres|mysql|mongodb|sqlite`, `database: {name}` and `can: [read]` to its bot.yaml; "
                                   "that is the owner's call.")
        entry = entries[name]
        if "read" not in [str(v) for v in (entry.get("can") or [])]:
            raise Refusal("grant", f"{actor}'s entry for `{name}` does not carry `read` in `can:`")
    variable = str(entry.get("env") or env_name(name))
    url = str(environ.get(variable) or "").strip()
    if not url:
        setup = (f"Store the read-only connection string in Tools > Credentials with Bot variable name {variable} "
                 "and grant it to this bot" if actor.startswith("bot:") else
                 f"Set {variable} to the read-only connection string in your own shell environment")
        raise Refusal("credential", f"{variable} is not set for this run. {setup} "
                                    "(docs/databases.md, step 2). Do not ask for a writable URL.")
    return Database(name=name, kind=kind_of(url), url=url, max_rows=limit(entry, "max_rows", DEFAULT_MAX_ROWS, MAX_ROWS_CEILING),
                    timeout=limit(entry, "timeout_seconds", DEFAULT_TIMEOUT, TIMEOUT_CEILING),
                    options={"read_preference": entry["read_preference"]} if entry.get("read_preference") else {})


def listing(actor, environ=None):
    environ = environ if environ is not None else os.environ
    if not actor.startswith("bot:"):
        return [{"database": re.sub(r"^DB_(.*)_URL$", lambda m: m.group(1).lower(), key), "env": key, "credential": True}
                for key in sorted(environ) if re.fullmatch(r"DB_[A-Z0-9_]+_URL", key) and environ[key]]
    rows = []
    for name, entry in sorted(declared(employee_manifest(actor[4:], environ)).items()):
        variable = str(entry.get("env") or env_name(name))
        rows.append({"database": name, "service": entry.get("service"), "can": entry.get("can") or [], "env": variable,
                     "credential": bool(environ.get(variable)), "max_rows": limit(entry, "max_rows", DEFAULT_MAX_ROWS, MAX_ROWS_CEILING),
                     "timeout_seconds": limit(entry, "timeout_seconds", DEFAULT_TIMEOUT, TIMEOUT_CEILING),
                     "note": entry.get("note", "")})
    return rows


# ----------------------------------------------------------------------------- doctor
WRITE_GRANT = re.compile(r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|GRANT OPTION|FILE|SUPER|TRUNCATE)\b", re.I)


def probe(db):
    """Checks that need a live connection: version, read-only session, the role's write grants."""
    checks = []

    def add(level, check, detail):
        checks.append({"level": level, "check": check, "detail": db.redact(detail)})

    if db.kind == "postgres":
        version = execute(db, "SELECT version()")["rows"][0][0]
        add("ok", "connects", version.split(",")[0])
        state = execute(db, "SHOW transaction_read_only")["rows"][0][0]
        add("ok" if state == "on" else "fail", "session is read-only", f"transaction_read_only = {state}")
        add("ok", "statement timeout", f"{db.timeout} s, set per query")
        row = execute(db, "SELECT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user), "
                          "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                          "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
                          "AND has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE'))")["rows"][0]
        if row[0]:
            add("warn", "role privileges", "the role is a superuser; use a role that can only SELECT (docs/databases.md, step 1)")
        elif row[1]:
            add("warn", "role privileges", f"the role can write to {row[1]} table(s); revoke INSERT/UPDATE/DELETE (docs/databases.md, step 1)")
        else:
            add("ok", "role privileges", "no write privilege on any table")
    elif db.kind == "mysql":
        version = execute(db, "SELECT version()")["rows"][0][0]
        add("ok", "connects", "MySQL/MariaDB " + str(version))
        conn = mysql_connect(db, db.timeout)
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute("START TRANSACTION READ ONLY")
                cur.execute("SELECT @@transaction_read_only" if _has_var(cur, "transaction_read_only") else "SELECT @@tx_read_only")
                state = cur.fetchone()[0]
                add("ok" if state in (1, "1") else "fail", "session is read-only", f"transaction_read_only = {state}")
                cur.execute("SHOW GRANTS FOR CURRENT_USER()")
                grants = " ".join(str(r[0]) for r in cur.fetchall())
            conn.rollback()
        finally:
            conn.close()
        hit = sorted({m.upper() for m in WRITE_GRANT.findall(grants)})
        if hit:
            add("warn", "role privileges", f"the account holds {', '.join(hit)}; grant only SELECT (docs/databases.md, step 1)")
        else:
            add("ok", "role privileges", "SELECT only")
    elif db.kind == "mongodb":
        from clients import dbmongo
        checks += dbmongo.probe(db)
    else:
        version = execute(db, "SELECT sqlite_version()")["rows"][0][0]
        add("ok", "opens", f"SQLite {version}, opened read-only")
        try:
            conn = sqlite_connect(db, 5)
            try:
                conn.execute("CREATE TABLE tico_doctor_probe (x)")
                add("fail", "session is read-only", "a write was accepted")
            except sqlite3.Error:
                add("ok", "session is read-only", "a write was refused")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            add("fail", "session is read-only", str(exc))
    return checks


def _has_var(cur, name):
    cur.execute("SHOW VARIABLES LIKE %s", (name,))
    return bool(cur.fetchall())


def doctor(actor, names=(), environ=None):
    environ = environ if environ is not None else os.environ
    if not names:
        names = [row["database"] for row in listing(actor, environ)]
    if not names:
        return {"ok": False, "databases": [], "text": "no databases: declare one under `tools:` in bot.yaml (docs/databases.md)"}
    report, ok = [], True
    for name in names:
        checks = []
        try:
            db = open_database(name, actor, environ)
            checks.append({"level": "ok", "check": "grant and credential", "detail": f"{actor} may use {name}; the credential is set"})
            checks += probe(db)
        except Refusal as exc:
            checks.append({"level": "fail", "check": exc.code, "detail": exc.detail})
        except Exception as exc:                                         # noqa: BLE001
            module = type(exc).__module__
            code, detail = classify(exc, "postgres" if "psycopg" in module else "mysql" if "pymysql" in module else
                                    "mongodb" if "pymongo" in module else "sqlite")
            checks.append({"level": "fail", "check": code, "detail": redact(detail, environ.get(env_name(name), ""))})
        ok = ok and all(c["level"] != "fail" for c in checks)
        report.append({"database": name, "checks": checks})
    return {"ok": ok, "databases": report}


# ----------------------------------------------------------------------------- audit and command
def audit(client, db, statement, result, query=None, param_names=(), error=None, extra=None):
    """Record the query on the hub: the statement and its size, never a row. `extra` adds fields (MongoDB: operation, collection)."""
    body = {"database": db.name, "kind": db.kind, "statement": db.redact(statement)[:AUDIT_STATEMENT_CHARS],
            "rows": result["row_count"] if result else 0, "truncated": bool(result and result["truncated"]),
            "ms": result["ms"] if result else 0, "query": query, "params": sorted(param_names)[:20],
            "error": error, **(extra or {})}
    try:
        client.post("databases/audit", body)
    except APIError as exc:
        raise APIError("audit", "the query could not be recorded on the hub, so its result is withheld "
                                f"({exc.code}: {exc.detail})", 0, retryable=False) from None


def named_query(client, name, query_id):
    try:
        return client.get(f"integrations/{name}/queries", id=query_id)
    except APIError as exc:
        if exc.status == 404:
            raise Refusal("catalog", f"no query `{query_id}` in the `{name}` catalog. Named queries live in the company's private "
                                     f"config as integrations/queries/{name}.yaml (docs/databases.md, step 5); "
                                     f"`hub tool query-search {name} <term>` searches them") from None
        raise


def parse_params(pairs):
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_]\w*", key):
            raise Refusal("usage", "--param takes name=value")
        out[key] = value
    return out


def run(client, args, environ=None):
    """The `hub db` command; `client` is the hub client that records the audit."""
    environ = environ if environ is not None else os.environ
    actor = client.get("me")["actor"]
    target = args.target
    try:
        if target == "list":
            rows = listing(actor, environ)
            return {"databases": rows, "text": "\n".join(
                f"{r['database']:<20} {r.get('service') or '':<10} env {r['env']} ({'set' if r['credential'] else 'MISSING'})"
                + (f"  max {r['max_rows']} rows, {r['timeout_seconds']} s" if 'max_rows' in r else "") for r in rows)
                or "no databases declared; see docs/databases.md"}
        if target == "doctor":
            return doctor(actor, [args.sql] if args.sql else [], environ)
        db = open_database(target, actor, environ)
        if db.kind == "mongodb":
            from clients import dbmongo
            return mongo_run(client, db, args, dbmongo)
        if getattr(args, "extra", None):
            raise Refusal("usage", "give one statement, quoted: hub db NAME \"SELECT ...\"")
        values = parse_params(args.param)
        order = ()
        query_id = args.query_id
        if query_id:
            if args.sql:
                raise Refusal("usage", "give either a statement or --query, not both")
            page = named_query(client, target, query_id)
            sql, defs = page["sql"], page.get("params") or []
            order = [p["name"] for p in defs]
            for p in defs:
                if p["name"] in values:
                    values[p["name"]] = coerce(values[p["name"]], p.get("type", ""))
                elif "default" in p:
                    values[p["name"]] = coerce(p["default"], p.get("type", ""))
                elif p.get("required"):
                    raise Refusal("params", f"`{p['name']}` is required ({p.get('label', p['name'])}); pass --param {p['name']}=...")
        else:
            if not args.sql:
                raise Refusal("usage", 'give a statement (hub db NAME "SELECT ...") or --query ID')
            sql = args.sql
            values = {k: coerce(v, "") for k, v in values.items()}
        try:
            result = execute(db, sql, values, order, args.max_rows, args.timeout)
        except Refusal as exc:
            audit(client, db, sql, None, query_id, values, error=exc.code)
            raise
        audit(client, db, sql, result, query_id, values)
        return result
    except Refusal as exc:
        raise APIError(exc.code, exc.detail, 0, retryable=False) from None


def mongo_run(client, db, args, dbmongo):
    """`hub db <mongo> find|aggregate|count|distinct|collections ...` or `--query ID`: the same audit-first flow as SQL."""
    values, query_id, entry = parse_params(args.param), args.query_id, None
    if query_id:
        if args.sql or getattr(args, "extra", None):
            raise Refusal("usage", "give either an operation or --query, not both")
        entry = named_query(client, args.target, query_id)
        defs = entry.get("params") or []
        typed = {}
        for p in defs:
            if p["name"] in values:
                typed[p["name"]] = dbmongo.coerce(values[p["name"]], p.get("type", ""), p["name"])
            elif "default" in p:
                typed[p["name"]] = dbmongo.coerce(p["default"], p.get("type", ""), p["name"])
            elif p.get("required"):
                raise Refusal("params", f"`{p['name']}` is required ({p.get('label', p['name'])}); pass --param {p['name']}=...")
        call = dbmongo.named_call(entry, typed)
        if getattr(args, "limit", None):
            call["limit"] = min(args.limit, call.get("limit") or args.limit)
        statement, names = dbmongo.audit_statement(call["template"], keep_values=True), list(typed)
    else:
        if values:
            raise Refusal("usage", "--param fills a named query's placeholders; for a direct call put the value in the JSON")
        if not args.sql:
            raise Refusal("usage", "give an operation (find, aggregate, count, distinct, collections) or --query ID; see docs/databases.md")
        call = dbmongo.call_from_args(args.sql, getattr(args, "extra", None), args)
        statement, names = None, []
    extra = {"operation": call["op"], "collection": call.get("collection")}
    statement = statement or dbmongo.audit_statement(call)
    try:
        result = dbmongo.execute(db, call, args.max_rows, args.timeout)
    except Refusal as exc:
        audit(client, db, statement, None, query_id, names, error=exc.code, extra=extra)   # the attempt is the interesting part
        raise
    audit(client, db, statement, result, query_id, names, extra=extra)
    return result


def render(result, args):
    from clients import remotecli
    if "documents" in result:
        lines = [json.dumps(d, separators=(",", ":"), ensure_ascii=False) for d in result["documents"]]
        count = f"{result['row_count']} {'document' if result['row_count'] == 1 else 'documents'}"
        if result.get("truncated"):
            count += " shown (more available; add a filter or --limit, or aggregate)"
        return "\n".join(lines + [f"{count} ({result['ms']} ms)"])
    if "text" in result and "columns" not in result:
        return result["text"]
    if "databases" in result and result["databases"] and "checks" in result["databases"][0]:
        lines = []
        for entry in result["databases"]:
            lines.append(entry["database"])
            lines += [f"  {c['level'].upper():<5} {c['check']}: {c['detail']}" for c in entry["checks"]]
        return "\n".join(lines)
    if args.csv:
        return remotecli.sql_csv(result)
    return remotecli.sql_table(result)
