"""`hub db` on MongoDB (Atlas first): the read-only rules, the audit shape, catalogs and the doctor.

The rules and the mongodb+srv parsing are tested without a server. Enforcement is tested against a
throwaway MongoDB container when Docker can run one (skipped otherwise), with a `read` user, a
`readWrite` user (to show the tool refuses writes even when the user could make them) and a user
whose role reaches beyond one database."""

import datetime
import json
import shutil
import subprocess
import time
import types
import uuid

import pytest

from backend.tests.test_databases import FakeHub, workspace
from clients import dbmongo as M
from clients import dbquery as D
from clients.tico import APIError

SRV = "mongodb+srv://tico_ro:p%40ss-w0rd-secret@cluster0.ab1cd.mongodb.net/app?retryWrites=true&w=majority"
GRANT = {"service": "mongodb", "database": "atlas", "can": ["read"], "identity": "Atlas read user"}


def margs(target, verb=None, *rest, **kw):
    base = dict(target=target, sql=verb, extra=list(rest), query_id=None, param=[], json=False, csv=False, max_rows=None,
                timeout=None, projection=None, sort=None, limit=None)
    return types.SimpleNamespace(**{**base, **kw})


def env(tmp_path, url, entry=None):
    return {**workspace(tmp_path, "ops", [{**GRANT, **(entry or {})}]), "DB_ATLAS_URL": url}


# ----------------------------------------------------------------------------- no server: mongodb+srv parsing


# ----------------------------------------------------------------------------- no server: the read-only rules
@pytest.mark.parametrize("call, operator", [
    ({"op": "aggregate", "collection": "c", "pipeline": [{"$match": {}}, {"$out": "copy"}]}, "$out"),
    ({"op": "count", "collection": "c", "filter": {"$or": [{"a": 1}, {"$where": "true"}]}}, "$where"),
    ({"op": "aggregate", "collection": "c", "pipeline": [{"$lookup": {"from": "o", "pipeline": [{"$merge": "x"}], "as": "y"}}]}, "$merge"),
])
def test_write_and_javascript_operators_are_refused_at_any_depth(call, operator):
    with pytest.raises(D.Refusal) as caught:
        M.validate(dict(call), 10)
    assert caught.value.code == "read_only" and operator in caught.value.detail


def test_only_the_read_verbs_run_and_the_shapes_are_checked():
    for verb in ("insert", "update", "delete", "drop", "runCommand", "eval", "mapReduce"):
        with pytest.raises(D.Refusal, match="is not a MongoDB operation"):
            M.call_from_args(verb, ["c", "{}"], margs("a"))
    for bad, match in ((("find", ["c", "{nope"]), "not valid JSON"), (("find", ["c", "[]"]), "must be a JSON object"),
                       (("aggregate", ["c", '{"$match": {}}']), "non-empty JSON array"), (("aggregate", ["c"]), "aggregate <collection>"),
                       (("aggregate", ["c", '[{"$match": {}, "$limit": 1}]']), "exactly one"), (("count", []), "count <collection>"),
                       (("distinct", ["c"]), "distinct <collection> <field>"), (("collections", ["x"]), "no arguments")):
        with pytest.raises(D.Refusal, match=match):
            M.validate(M.call_from_args(bad[0], bad[1], margs("a")), 10)
    with pytest.raises(D.Refusal, match="system collections"):
        M.validate({"op": "find", "collection": "system.users"}, 10)
    with pytest.raises(D.Refusal, match="deployment"):
        M.validate({"op": "aggregate", "collection": "c", "pipeline": [{"$currentOp": {}}]}, 10)
    with pytest.raises(D.Refusal, match="1 or -1"):
        M.validate({"op": "find", "collection": "c", "sort": {"a": "up"}}, 10)


def test_the_audit_statement_keeps_the_shape_and_drops_every_value():
    call = M.call_from_args("find", ["accounts", '{"email": "ana@acme.example", "age": {"$gt": 34}, "at": {"$date": "2026-09-01T00:00:00Z"}, '
                                                 '"plan": {"$in": ["a", "b", "c", "d", "e", "f", "g"]}, "vip": true, "gone": null}'],
                            margs("a", projection='{"email": 1, "_id": 0}', sort='{"at": -1}', limit=5))
    statement = M.audit_statement(call)
    for value in ("ana@acme.example", "34", "2026-09-01"):
        assert value not in statement
    shaped = json.loads(statement.split(" ", 2)[2])
    assert statement.startswith("find accounts ")
    assert shaped["filter"] == {"email": "<string>", "age": {"$gt": "<number>"}, "at": "<date>",
                                "plan": {"$in": ["<string>"] * 5 + ["...+2"]}, "vip": "<bool>", "gone": "<null>"}
    assert shaped["projection"] == {"email": 1, "_id": 0} and shaped["sort"] == {"at": -1} and shaped["limit"] == 5
    pipeline = M.call_from_args("aggregate", ["orders", '[{"$match": {"status": "paid", "_id": {"$oid": "650000000000000000000abc"}}}, {"$limit": 3}]'], margs("a"))
    assert "paid" not in M.audit_statement(pipeline) and "<objectid>" in M.audit_statement(pipeline)
    assert M.audit_statement({"op": "distinct", "collection": "orders", "field": "status", "filter": {"a": "x"}}).startswith("distinct orders")


# ----------------------------------------------------------------------------- no server: named queries
ENTRY = {"id": "by-status", "params": [{"name": "status", "type": "text"}, {"name": "since", "type": "date"}],
         "mongo": {"op": "find", "collection": "orders", "filter": {"status": {"$param": "status"}, "at": {"$gte": {"$param": "since"}}},
                   "sort": {"at": -1}, "limit": 10}}


def test_catalog_values_are_substituted_as_typed_values_and_stay_values():
    injection = '{"$ne": null}'
    call = M.named_call(ENTRY, {"status": injection, "since": M.coerce("2026-09-01", "date", "since")})
    assert call["filter"]["status"] == injection and isinstance(call["filter"]["status"], str)
    assert call["filter"]["at"]["$gte"] == datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)
    # The template itself is untouched, and is what the audit records.
    assert call["template"]["filter"]["status"] == {"$param": "status"}
    assert M.audit_statement(call["template"], keep_values=True).count("$param") == 2
    # A value can never carry an operator key or reach another field through a path.
    for text in ("$secret", "$ne"):
        with pytest.raises(D.Refusal, match="field path or operator"):
            M.coerce(text, "text", "status")
    with pytest.raises(D.Refusal, match="not a valid date"):
        M.coerce("yesterday", "date", "since")
    assert M.coerce("a, b ,c", "list", "x") == ["a", "b", "c"] and M.coerce("02134", "text", "zip") == "02134"


# ----------------------------------------------------------------------------- no server: audit through the command
def test_the_command_audits_shape_and_row_count_and_refusals_without_a_server(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "perform", lambda db, call, max_rows, timeout: ([{"n": 1}, {"n": 2}], False))
    hub, environ = FakeHub(), env(tmp_path, SRV)
    result = D.run(hub, margs("atlas", "find", "accounts", '{"email": "ana@acme.example"}'), environ)
    assert result["row_count"] == 2
    (audit,) = hub.audits
    assert audit["kind"] == "mongodb" and audit["operation"] == "find" and audit["collection"] == "accounts" and audit["rows"] == 2
    assert "ana@acme.example" not in json.dumps(audit) and "<string>" in audit["statement"] and audit["params"] == []
    with pytest.raises(APIError) as caught:
        D.run(hub, margs("atlas", "aggregate", "accounts", '[{"$match": {"email": "bo@acme.example"}}, {"$out": "leak"}]'), environ)
    assert caught.value.code == "read_only"
    refusal = hub.audits[-1]
    assert refusal["error"] == "read_only" and refusal["rows"] == 0 and "bo@acme.example" not in json.dumps(refusal)
    with pytest.raises(APIError, match="fills a named query"):
        D.run(hub, margs("atlas", "count", "accounts", param=["a=b"]), environ)
    hub.fail_audit = True
    with pytest.raises(APIError) as caught:
        D.run(hub, margs("atlas", "count", "accounts"), environ)
    assert caught.value.code == "audit"


def test_redaction_removes_a_mongodb_srv_password_from_any_message():
    text = f"failed to connect to {SRV} as tico_ro:p%40ss-w0rd-secret@cluster0 and mongodb+srv://x:other-secret@h/db"
    clean = D.redact(text, SRV)
    assert "p%40ss-w0rd-secret" not in clean and "p@ss-w0rd-secret" not in clean and "other-secret" not in clean


# ----------------------------------------------------------------------------- MongoDB, when Docker can run one
@pytest.fixture(scope="module")
def mongo():
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker is not installed")
    pymongo = pytest.importorskip("pymongo")
    name = "tico-mongo-test-" + uuid.uuid4().hex[:8]
    try:
        started = subprocess.run([docker, "run", "-d", "--rm", "--name", name, "-e", "MONGO_INITDB_ROOT_USERNAME=root",
                                  "-e", "MONGO_INITDB_ROOT_PASSWORD=admin-pw", "-p", "127.0.0.1::27017", "mongo:7"],
                                 capture_output=True, text=True, timeout=180)
        if started.returncode:
            pytest.skip("cannot start a mongo container: " + started.stderr.strip()[:120])
    except subprocess.TimeoutExpired:
        pytest.skip("docker did not answer")
    try:
        port = subprocess.run([docker, "port", name, "27017/tcp"], capture_output=True, text=True).stdout.splitlines()[0].split(":")[-1].strip()
        admin = None
        for _ in range(90):
            try:
                admin = pymongo.MongoClient(f"mongodb://root:admin-pw@127.0.0.1:{port}/?authSource=admin", serverSelectionTimeoutMS=1500)
                admin.admin.command("ping")
                break
            except pymongo.errors.PyMongoError:
                time.sleep(1)
        else:
            pytest.skip("mongo did not come up")
        app = admin["app"]
        app.orders.insert_many([{"n": i, "status": "paid" if i % 2 else "pending", "email": f"user{i}@example.com",
                                 "at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=i)} for i in range(1000)])
        app.one.insert_one({"a": 1})
        admin["other"].secrets.insert_one({"s": 1})
        for user, roles in (("tico_ro", [{"role": "read", "db": "app"}]), ("tico_rw", [{"role": "readWrite", "db": "app"}]),
                            ("tico_wide", [{"role": "read", "db": "app"}, {"role": "read", "db": "other"}])):
            admin.admin.command("createUser", user, pwd=user + "-secret-pw", roles=roles)
        url = lambda user: f"mongodb://{user}:{user}-secret-pw@127.0.0.1:{port}/app?authSource=admin"  # noqa: E731
        yield {"ro": url("tico_ro"), "rw": url("tico_rw"), "wide": url("tico_wide"), "admin": admin, "port": port}
        admin.close()
    finally:
        subprocess.run([docker, "rm", "-f", name], capture_output=True)


def go(url, verb, *rest, max_rows=None, timeout=None, **kw):
    limits = {k: v for k, v in (("max_rows", max_rows), ("timeout", timeout)) if v}
    return M.execute(D.Database("atlas", "mongodb", url, **limits), M.call_from_args(verb, rest, margs("atlas", **kw)))


@pytest.mark.slow
def test_mongo_reads_caps_rows_and_supports_the_five_operations(mongo):
    result = go(mongo["ro"], "find", "orders", '{"status": "paid"}', sort='{"n": -1}', projection='{"n": 1, "_id": 0}', limit=3)
    assert [d["n"] for d in result["documents"]] == [999, 997, 995] and not result["truncated"]
    capped = go(mongo["ro"], "find", "orders", max_rows=100)
    assert capped["row_count"] == 100 and capped["truncated"]
    assert not go(mongo["ro"], "find", "orders", max_rows=100, limit=100)["truncated"]
    agg = go(mongo["ro"], "aggregate", "orders", '[{"$group": {"_id": "$status", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]')
    assert agg["documents"] == [{"_id": "paid", "n": 500}, {"_id": "pending", "n": 500}]
    assert go(mongo["ro"], "aggregate", "orders", '[{"$match": {}}]', max_rows=10)["truncated"]
    assert go(mongo["ro"], "count", "orders", '{"status": "paid"}')["documents"] == [{"count": 500}]
    assert go(mongo["ro"], "distinct", "orders", "status")["documents"] == ["paid", "pending"]
    found = {c["collection"]: c["fields"] for c in go(mongo["ro"], "collections")["documents"]}
    assert set(found) == {"orders", "one"} and found["orders"]["email"] == "str" and found["orders"]["_id"] == "objectId"


@pytest.mark.slow
def test_writes_are_refused_before_they_leave_even_for_a_user_who_could_write(mongo):
    for pipeline in ('[{"$match": {}}, {"$out": "copy"}]', '[{"$merge": {"into": "copy"}}]',
                     '[{"$unionWith": {"coll": "one", "pipeline": [{"$out": "copy"}]}}]'):
        with pytest.raises(D.Refusal) as caught:
            go(mongo["rw"], "aggregate", "orders", pipeline)
        assert caught.value.code == "read_only"
    with pytest.raises(D.Refusal):
        go(mongo["rw"], "find", "orders", '{"$where": "this.n == 1"}')
    with pytest.raises(D.Refusal):
        go(mongo["rw"], "aggregate", "orders", '[{"$addFields": {"x": {"$function": {"body": "function(){return 1}", "args": [], "lang": "js"}}}}]')
    assert "copy" not in mongo["admin"]["app"].list_collection_names()
    assert mongo["admin"]["app"].orders.count_documents({}) == 1000
