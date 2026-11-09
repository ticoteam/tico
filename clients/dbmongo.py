"""MongoDB (Atlas first) for `hub db`: read-only find, aggregate, count, distinct and collections.

Bots cannot send SQL here, and they cannot send a command either: the verb is one of five, the
filter and pipeline are Extended JSON, and every operator that writes or runs JavaScript on the
server is refused before anything leaves the computer. The Atlas user's built-in `read` role is
still the real boundary (docs/databases.md); this module is the second layer, plus a secondary-
preferred read preference, `maxTimeMS` and a row cap as the third and fourth.

The audit keeps the shape of the call, not its values. `find orders {"email": "<string>"}` says
what was looked at without writing the person's address into the hub, which any reviewer can read.
A named query is different: its catalog entry is already reviewed text, so the audit records the
entry itself (with `{"$param": "name"}` in place of each value) and the parameter names.
"""

import datetime
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit

from clients.dbquery import MAX_ROWS_CEILING, Refusal

VERBS = ("find", "aggregate", "count", "distinct", "collections")
# Anything that writes, or evaluates JavaScript in the server, wherever it is nested (a `$unionWith`
# or `$lookup` sub-pipeline can carry `$out` too, so the walk covers every level).
FORBIDDEN = {
    "$out": "writes a collection",
    "$merge": "writes a collection",
    "$function": "runs JavaScript on the server",
    "$accumulator": "runs JavaScript on the server",
    "$where": "runs JavaScript on the server",
}
# Stages that read the deployment rather than the data.
ADMIN_STAGES = ("$currentOp", "$listSessions", "$listLocalSessions", "$listSampledQueries", "$queryStats")
READ_PREFERENCES = ("primary", "primaryPreferred", "secondary", "secondaryPreferred", "nearest")
DEFAULT_READ_PREFERENCE = "secondaryPreferred"
MAX_COLLECTIONS, SAMPLE_DOCS, MAX_DEPTH = 200, 20, 60
WRITE_ACTIONS = {
    "insert",
    "update",
    "remove",
    "convertToCapped",
    "collMod",
    "anyAction",
    "applyOps",
    "bypassDocumentValidation",
    "compact",
    "planCacheWrite",
    "planCacheIndexFilter",
    "shutdown",
    "setParameter",
    "cleanupOrphaned",
    "unlock",
}
WRITE_PREFIX = re.compile(
    r"^(create|drop|rename|update|insert|remove|grant|revoke|replSet|enableSharding|moveChunk|splitChunk)"
)


# ----------------------------------------------------------------------------- the connection string
@dataclass
class Target:
    srv: bool
    hosts: str
    database: str
    options: dict  # lower-cased option name -> value, from the query string

    @property
    def atlas(self):
        return any(h.strip().lower().endswith(".mongodb.net") for h in self.hosts.split(","))

    @property
    def local(self):
        return all(h.strip().split(":")[0].lower() in ("localhost", "127.0.0.1", "::1") for h in self.hosts.split(","))


def parse_target(url):
    """The parts of `mongodb://` or `mongodb+srv://` that the rules need, without touching the network.

    pymongo resolves the SRV name itself when the client is built; nothing here does DNS."""
    parts = urlsplit(url)
    srv = parts.scheme.lower() == "mongodb+srv"
    hosts = parts.netloc.rpartition("@")[2]
    if not hosts:
        raise Refusal(
            "url",
            "the connection string has no host; Atlas gives `mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/`",
        )
    if srv and ("," in hosts or ":" in hosts):
        raise Refusal(
            "url", "a mongodb+srv:// string names exactly one host and no port (Atlas: `cluster0.xxxxx.mongodb.net`)"
        )
    database = unquote(parts.path.lstrip("/"))
    if not database:
        raise Refusal(
            "url",
            "the connection string must name the database in its path, e.g. "
            "`mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/app` (the Atlas user's `read` role is granted on it)",
        )
    options = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=True)}
    return Target(srv, hosts, database, options)


def read_preference(db):
    """The read preference: the bot's `access:` entry, else the URL's, else secondaryPreferred."""
    wanted = str(
        db.options.get("read_preference")
        or parse_target(db.url).options.get("readpreference")
        or DEFAULT_READ_PREFERENCE
    )
    for name in READ_PREFERENCES:
        if name.lower() == wanted.lower():
            return name
    raise Refusal("usage", f"read_preference `{wanted}` is not one of {', '.join(READ_PREFERENCES)}")


def client_options(db, timeout):
    """Keyword arguments for MongoClient; they win over anything the URL says."""
    target = parse_target(db.url)
    options = {
        "appname": "tico-db",
        "serverSelectionTimeoutMS": 10_000,
        "connectTimeoutMS": 10_000,
        "socketTimeoutMS": int((timeout + 5) * 1000),
        "retryWrites": False,
        "maxPoolSize": 2,
        "readPreference": read_preference(db),
    }
    if target.atlas and "authsource" not in target.options:
        options["authSource"] = (
            "admin"  # Atlas users live in `admin`; a database in the path would otherwise be the auth database
        )
    return options


def connect(db, timeout):
    try:
        import pymongo
    except ImportError:
        raise Refusal(
            "driver",
            "the mongodb driver is not installed on this computer; run `pip install 'pymongo>=4.10' dnspython` "
            "in the runner's environment (both are in backend/requirements.txt)",
        ) from None
    return pymongo.MongoClient(db.url, **client_options(db, timeout))


# ----------------------------------------------------------------------------- Extended JSON and the read-only rules
def json_options():
    from bson import json_util

    return json_util.RELAXED_JSON_OPTIONS.with_options(
        tz_aware=True, tzinfo=datetime.timezone.utc
    )  # dates are UTC, never naive


def loads(text, what):
    """Extended JSON (relaxed) text as Python values: `{"$oid": ...}` is an ObjectId, `{"$date": ...}` a datetime."""
    from bson import json_util

    try:
        return json_util.loads(text, json_options=json_options())
    except (ValueError, TypeError, RecursionError) as exc:
        raise Refusal(
            "usage", f"{what} is not valid JSON ({str(exc).splitlines()[0][:120]}); pass Extended JSON in single quotes"
        ) from None


def dumps(value):
    """Relaxed Extended JSON as plain JSON values, so ObjectId and dates survive a round trip."""
    from bson import json_util

    return json.loads(json_util.dumps(value, json_options=json_options()))


def check_operators(node, depth=0):
    """Refuse a write or JavaScript operator at any depth of a filter, projection or pipeline."""
    if depth > MAX_DEPTH:
        raise Refusal("refused", "the JSON is nested too deeply")
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN:
                raise Refusal(
                    "read_only",
                    f"`{key}` {FORBIDDEN[key]} and is never available through `hub db`; read the documents instead",
                )
            check_operators(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            check_operators(value, depth + 1)


def check_pipeline(pipeline):
    if not isinstance(pipeline, list) or not pipeline:
        raise Refusal("usage", "an aggregation pipeline is a non-empty JSON array of stages")
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise Refusal("usage", "each pipeline stage is an object with exactly one `$stage` key")
        name = next(iter(stage))
        if name in ADMIN_STAGES:
            raise Refusal(
                "read_only", f"`{name}` reports on the deployment, not the data, and is not available through `hub db`"
            )
    check_operators(pipeline)
    return pipeline


def document(value, what):
    if not isinstance(value, dict):
        raise Refusal("usage", f"{what} must be a JSON object")
    check_operators(value)
    return value


def sort_spec(value):
    spec = document(value, "--sort")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v in (1, -1) for v in spec.values()):
        raise Refusal("usage", '--sort takes field: 1 or -1, e.g. \'{"placed_at": -1}\'')
    return list(spec.items())


# ----------------------------------------------------------------------------- the audit shape
FLAGS = (0, 1, -1)


def shape(node, flags=False):
    """`node` with every value replaced by its type, keys (fields and operators) kept.

    `flags` keeps 0, 1 and -1 (projection and sort directions carry no data)."""
    if isinstance(node, dict):
        if len(node) == 1 and next(iter(node)) == "$param":
            return node  # a named query's placeholder is not a value
        return {k: shape(v, flags) for k, v in node.items()}
    if isinstance(node, list):
        items = [shape(v, flags) for v in node[:5]]
        return items + [f"...+{len(node) - 5}"] if len(node) > 5 else items
    if flags and isinstance(node, (bool, int)) and node in FLAGS:
        return node
    if node is None:
        return "<null>"
    if isinstance(node, bool):
        return "<bool>"
    if isinstance(node, (int, float)):
        return "<number>"
    if isinstance(node, str):
        return "<string>"
    if isinstance(node, datetime.datetime):
        return "<date>"
    return "<" + type(node).__name__.lower() + ">"  # ObjectId -> <objectid>, Regex -> <regex>, ...


def audit_statement(call, keep_values=False):
    """One line for the audit: the operation, collection and shape (or, for a named query, the entry itself)."""
    show = (lambda v, flags=False: v) if keep_values else shape
    parts = {
        "find": ("filter", "projection", "sort"),
        "aggregate": ("pipeline",),
        "count": ("filter",),
        "distinct": ("field", "filter"),
    }
    body = {}
    for key in parts.get(call["op"], ()):
        if call.get(key) is not None and call.get(key) != {}:
            body[key] = call[key] if key == "field" else show(call[key], key in ("projection", "sort"))
    if call.get("limit"):
        body["limit"] = call["limit"]
    return f"{call['op']} {call.get('collection') or ''} {json.dumps(body, default=str, separators=(',', ':'))}".strip()


# ----------------------------------------------------------------------------- named queries
def coerce(text, type_, name):
    """A `--param` string as the catalog's declared type. Strings never turn into operators or field paths."""
    try:
        if type_ in ("int", "integer"):
            return int(text)
        if type_ in ("number", "float", "decimal"):
            return float(text)
        if type_ in ("bool", "boolean"):
            return str(text).strip().lower() in ("1", "true", "yes", "t", "y")
        if type_ in ("date", "datetime", "timestamp"):
            moment = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
            return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)
        if type_ in ("objectid", "oid"):
            from bson import ObjectId

            return ObjectId(str(text))
        if type_ == "list":
            return [coerce(part.strip(), "text", name) for part in str(text).split(",") if part.strip()]
    except Exception as exc:  # bson.errors.InvalidId is not a ValueError
        if isinstance(exc, Refusal):
            raise
        raise Refusal("params", f"`{text}` is not a valid {type_} for {name}") from None
    if isinstance(text, str) and text.startswith("$"):
        # Inside an aggregation expression a string starting with `$` is a field path, so a value could reach another field.
        raise Refusal(
            "params",
            f"the value of {name} starts with `$`, which MongoDB reads as a field path or operator; "
            "named-query values are literals",
        )
    return text


def substitute(node, values):
    """`node` with each `{"$param": "name"}` replaced by that parameter's typed value.

    Only a whole value is replaced, by a Python object; nothing is rendered to text and parsed
    again, so a value such as `{"$ne": null}` stays one string and can never become an operator."""
    if isinstance(node, dict):
        if "$param" in node:
            if len(node) != 1 or not isinstance(node["$param"], str):
                raise Refusal("catalog", '`{"$param": "name"}` takes no other keys')
            if node["$param"] not in values:
                raise Refusal("params", f"no value for {node['$param']} (pass --param {node['$param']}=...)")
            return values[node["$param"]]
        return {k: substitute(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, values) for v in node]
    return node


def named_call(entry, values):
    """The call a catalog entry's `mongo:` block describes, with `values` in place of its placeholders."""
    spec = entry.get("mongo")
    if not isinstance(spec, dict) or spec.get("op") not in VERBS[:4] or not spec.get("collection"):
        raise Refusal(
            "catalog",
            f"query `{entry.get('id')}` has no valid `mongo:` block (op: find|aggregate|count|distinct, collection)",
        )
    template = {
        k: spec[k]
        for k in ("op", "collection", "filter", "projection", "sort", "limit", "pipeline", "field")
        if k in spec
    }
    call = substitute(template, values)
    call["template"] = template
    return call


# ----------------------------------------------------------------------------- running
def max_time(timeout):
    return max(1, int(timeout * 1000))


def rows_of(cursor, max_rows):
    docs = list(cursor)
    return [dumps(d) for d in docs[:max_rows]], len(docs) > max_rows


def perform(db, call, max_rows, timeout):
    """Run one validated call; returns (documents, truncated)."""
    ms = max_time(timeout)
    op, collection = call["op"], call.get("collection")
    client = connect(db, timeout)
    try:
        database = client[parse_target(db.url).database]
        if op == "collections":
            return collections(database, ms, max_rows), False
        coll = database[collection]
        if op == "find":
            want = call.get("limit")
            fetch = min(want, max_rows + 1) if want else max_rows + 1
            cursor = coll.find(
                call.get("filter") or {},
                call.get("projection") or None,
                sort=call.get("sort") or None,
                limit=fetch,
                max_time_ms=ms,
            )
            return rows_of(cursor, max_rows)
        if op == "aggregate":
            pipeline = list(call["pipeline"]) + [{"$limit": max_rows + 1}]  # the cap is applied on the server
            return rows_of(coll.aggregate(pipeline, maxTimeMS=ms, allowDiskUse=False), max_rows)
        if op == "count":
            return [{"count": coll.count_documents(call.get("filter") or {}, maxTimeMS=ms)}], False
        if op == "distinct":
            values = coll.distinct(call["field"], call.get("filter") or {}, maxTimeMS=ms)
            return [dumps({"value": v})["value"] for v in values[:max_rows]], len(values) > max_rows
        raise Refusal("usage", f"unknown operation `{op}` (one of {', '.join(VERBS)})")
    finally:
        client.close()


def collections(database, ms, max_rows):
    """Collection names with the field names (and types) seen in a small sample of each: enough to write a filter."""
    names = sorted(n for n in database.list_collection_names() if not n.startswith("system."))[
        : min(MAX_COLLECTIONS, max_rows)
    ]
    out = []
    for name in names:
        fields = {}
        for doc in database[name].find({}, limit=SAMPLE_DOCS, max_time_ms=ms):
            for key, value in doc.items():
                fields.setdefault(key, type(value).__name__.lower().replace("objectid", "objectId"))
        out.append({"collection": name, "fields": fields})
    return out


def validate(call, max_rows):
    """The rules, in one place, applied to a call from the command line or from a catalog entry."""
    op = call["op"]
    if op not in VERBS:
        raise Refusal("usage", f"`{op}` is not a MongoDB operation here; use one of {', '.join(VERBS)}")
    if op != "collections" and not re.fullmatch(r"[A-Za-z0-9_.\-]{1,120}", str(call.get("collection") or "")):
        raise Refusal(
            "usage", f"`hub db <name> {op} <collection> ...` needs a collection name (`collections` lists them)"
        )
    if str(call.get("collection") or "").startswith("system."):
        raise Refusal("read_only", "system collections are not available through `hub db`")
    if op in ("find", "count", "distinct"):
        call["filter"] = document(call.get("filter") if call.get("filter") is not None else {}, "the filter")
    if op == "find":
        if call.get("projection"):
            call["projection"] = document(call["projection"], "--projection")
        if call.get("sort"):
            call["sort"] = sort_spec(call["sort"]) if isinstance(call["sort"], dict) else call["sort"]
        if call.get("limit") is not None:
            call["limit"] = max(1, int(call["limit"]))
    if op == "aggregate":
        call["pipeline"] = check_pipeline(call.get("pipeline"))
    if op == "distinct" and not re.fullmatch(r"[A-Za-z0-9_.\-]{1,120}", str(call.get("field") or "")):
        raise Refusal("usage", "`distinct <collection> <field> ['<filter-json>']` needs a field name")
    return call


def call_from_args(verb, rest, args):
    """The call `hub db <name> <verb> <collection> ...` asks for; raises Refusal for a bad shape."""
    if verb not in VERBS:
        raise Refusal(
            "usage",
            f"`{verb}` is not a MongoDB operation; use `find`, `aggregate`, `count`, `distinct` or `collections`, "
            "or --query ID for a named query",
        )
    rest = list(rest or [])
    call = {"op": verb}
    if verb == "collections":
        if rest:
            raise Refusal("usage", "`collections` takes no arguments")
        return call
    if not rest:
        raise Refusal("usage", f"hub db <name> {verb} <collection> ...")
    call["collection"], rest = rest[0], rest[1:]
    limit = getattr(args, "limit", None)
    if verb == "aggregate":
        if len(rest) != 1:
            raise Refusal("usage", "hub db <name> aggregate <collection> '<pipeline-json>'")
        call["pipeline"] = loads(rest[0], "the pipeline")
    elif verb == "distinct":
        if not rest or len(rest) > 2:
            raise Refusal("usage", "hub db <name> distinct <collection> <field> ['<filter-json>']")
        call["field"] = rest[0]
        call["filter"] = loads(rest[1], "the filter") if len(rest) > 1 else {}
    else:
        if len(rest) > 1:
            raise Refusal("usage", f"hub db <name> {verb} <collection> ['<filter-json>']")
        call["filter"] = loads(rest[0], "the filter") if rest else {}
    if verb == "find":
        if getattr(args, "projection", None):
            call["projection"] = loads(args.projection, "--projection")
        if getattr(args, "sort", None):
            call["sort"] = loads(args.sort, "--sort")
        if limit:
            call["limit"] = limit
    return call


def execute(db, call, max_rows=None, timeout=None):
    """Validate and run `call`; returns the result dict `hub db` returns, with `operation` and `collection`."""
    max_rows = min(int(max_rows or db.max_rows), db.max_rows, MAX_ROWS_CEILING)
    timeout = min(float(timeout or db.timeout), db.timeout, 120)
    call = validate(dict(call), max_rows)
    started = time.monotonic()
    try:
        documents, truncated = perform(db, call, max_rows, timeout)
    except Refusal:
        raise
    except Exception as exc:  # noqa: BLE001 - pymongo has many error types
        code, detail = classify(exc)
        raise Refusal(code, db.redact(detail)) from None
    return {
        "documents": documents,
        "row_count": len(documents),
        "truncated": truncated,
        "ms": int((time.monotonic() - started) * 1000),
        "operation": call["op"],
        "collection": call.get("collection"),
    }


def classify(exc):
    """(code, detail) for a pymongo error, before redaction."""
    name = type(exc).__name__
    text = str(exc).strip().splitlines()[0][:400] if str(exc).strip() else name
    lowered = text.lower()
    code = getattr(exc, "code", None)
    if name in ("ExecutionTimeout", "NetworkTimeout") or code == 50:
        return "timeout", "the operation ran past the timeout (maxTimeMS) and was stopped"
    if name == "AutoReconnect" and "timed out" in lowered:
        return "timeout", "the operation ran past the timeout and was stopped"
    if name == "ConfigurationError" and (
        "dns" in lowered or "srv" in lowered or "resolution" in lowered or "query name" in lowered
    ):
        return "srv_dns", (
            f"the SRV record for the cluster could not be resolved ({text}). Check the host in the connection string, "
            "that the runner's DNS allows TXT and SRV lookups, or use Atlas's standard (non-SRV) connection string"
        )
    if name == "OperationFailure" and (code in (18, 8000) or "authentication failed" in lowered):
        return (
            "auth",
            "authentication failed: check the Atlas database user, its password (percent-encoded) and `authSource=admin`",
        )
    if name == "OperationFailure" and (code == 13 or "not authorized" in lowered or "unauthorized" in lowered):
        return (
            "read_only",
            f"the database user is not authorized for this ({text}); the role is read-only or does not cover this collection",
        )
    if name == "ServerSelectionTimeoutError":
        if "ssl" in lowered or "tls" in lowered or "certificate" in lowered:
            return (
                "tls",
                f"the TLS handshake failed ({text[:200]}). Atlas needs TLS 1.2+; check the runner's clock and CA bundle",
            )
        return "unreachable", (
            "no server answered within 10 s. Usually the runner's public IP is not on the Atlas IP access list, "
            "the cluster is paused, or a private endpoint is not reachable from this computer"
        )
    if name in ("OperationFailure", "BulkWriteError", "WriteError") and (
        code in (20, 66, 67) or "read only" in lowered
    ):
        return "read_only", text
    return "db_error", f"{name}: {text}"


# ----------------------------------------------------------------------------- doctor
def write_actions(privileges):
    found = set()
    for privilege in privileges:
        for action in privilege.get("actions", []):
            if action in WRITE_ACTIONS or WRITE_PREFIX.match(action):
                found.add(action)
    return sorted(found)


def probe(db):
    """Checks that need a live connection: version, read preference, and what the user's roles allow."""
    checks = []

    def add(level, check, detail):
        checks.append({"level": level, "check": check, "detail": db.redact(detail)})

    target = parse_target(db.url)
    try:
        client = connect(db, db.timeout)
    except Refusal:
        raise
    except Exception as exc:  # noqa: BLE001 - e.g. SRV lookup fails while building the client
        code, detail = classify(exc)
        raise Refusal(code, detail) from None
    try:
        info = client.admin.command("buildInfo")
        add("ok", "connects", f"MongoDB {info.get('version', '?')}" + (" (Atlas, mongodb+srv)" if target.srv else ""))
        add("ok", "read preference", read_preference(db))
        add("ok", "timeout and row cap", f"maxTimeMS {int(db.timeout * 1000)}, {db.max_rows} documents, set per query")
        if not (
            target.srv or target.local or target.options.get("tls", target.options.get("ssl", "")).lower() == "true"
        ):
            add("warn", "transport", "the connection is not TLS-encrypted; add `?tls=true` (Atlas always uses TLS)")
        status = client.admin.command({"connectionStatus": 1, "showPrivileges": True})["authInfo"]
        users = status.get("authenticatedUsers", [])
        if not users:
            add(
                "warn",
                "role privileges",
                "the connection is not authenticated; use an Atlas database user with the `read` role",
            )
        else:
            privileges = status.get("authenticatedUserPrivileges", [])
            roles = [f"{r['role']}@{r['db']}" for r in status.get("authenticatedUserRoles", [])]
            bad = write_actions(privileges)
            wide = sorted(
                {
                    p["resource"].get("db")
                    for p in privileges
                    if p.get("resource", {}).get("db") not in (None, "", target.database, "local")
                }
                | ({"(cluster)"} if any(p.get("resource", {}).get("cluster") for p in privileges) else set())
            )
            if bad:
                add(
                    "warn",
                    "role privileges",
                    f"roles {', '.join(roles)} allow writes ({', '.join(bad[:8])}"
                    f"{'...' if len(bad) > 8 else ''}); use the built-in `read` role on `{target.database}` "
                    "only (docs/databases.md, step 1)",
                )
            else:
                add("ok", "role privileges", f"roles {', '.join(roles)}: no write action")
            if wide:
                add(
                    "warn",
                    "role scope",
                    f"the user can also read {', '.join(wide[:5])}; scope the role to `{target.database}` only",
                )
    finally:
        client.close()
    return checks
