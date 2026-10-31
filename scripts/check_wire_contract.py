"""The runner wire contract (contracts/runner, docs/runner-wire-contract.md): validate it, regenerate its requests,
snapshot it at a release, and refuse a change that breaks a runner or server inside the supported window.

    python scripts/check_wire_contract.py                    # the check: current contract against each kept release
    python scripts/check_wire_contract.py --write-requests   # regenerate requests.json from backend/models.py
    python scripts/check_wire_contract.py --snapshot 0.5.0   # keep the current contract as that release's copy

The window is the current and the previous minor release, both ways: a runner of either works with a server of
either. For every release copy inside the window (contracts/runner/releases/<version>/) the check asks four things:

1. old runner, new server, requests: does the current server take everything that release's runner may send?
2. old runner, new server, answers: does that release's runner read everything the current server sends?
3. new runner, old server, requests: does that release's server take what the current runner may send, other than
   what the contract lists under `gated` (sent only once the server says it takes it)?
4. new runner, old server, answers: does the current runner read everything that release's server sends?

A runner reads an answer leniently (unknown fields are ignored; an `x-open-enum` string takes values it does not
know); a server reads a request strictly (backend/models.py Contract forbids unknown fields). Exit status: 0 when
compatible, 1 with the breaks listed, 2 when the contract cannot be read.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "runner"
RELEASES = CONTRACT / "releases"
FIXTURES = "fixtures"
# The request models behind each endpoint's `request` (backend/models.py).
REQUEST_MODELS = ("Heartbeat", "Claim", "StartPlace", "Started", "EventBatch", "Empty", "Completion")
# Keywords that describe rather than constrain: left out of comparisons.
ANNOTATIONS = {"title", "description", "default", "examples", "x-note"}
CHILD_MAPS = ("properties", "$defs", "patternProperties")


# --- reading the contract ------------------------------------------------------------------------------------------

def load(directory=CONTRACT):
    """{"wire": ..., "requests": ...} for the contract in `directory`."""
    directory = Path(directory)
    return {"wire": json.loads((directory / "wire.json").read_text()),
            "requests": json.loads((directory / "requests.json").read_text())}


def generate_requests():
    """The request schemas as the server's own models define them."""
    sys.path.insert(0, str(ROOT))
    from pydantic.json_schema import models_json_schema
    from backend import models as M
    _, document = models_json_schema([(getattr(M, name), "validation") for name in REQUEST_MODELS],
                                     ref_template="#/$defs/{model}")
    return {"title": "Tico runner wire contract: requests",
            "x-note": "Generated from backend/models.py by scripts/check_wire_contract.py --write-requests.",
            "$defs": document["$defs"]}


def render(document):
    return json.dumps(document, indent=1, sort_keys=True) + "\n"


def stripped(node):
    """`node` without annotations, for comparing two schemas by what they accept."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in ANNOTATIONS or key.startswith("x-") and key != "x-open-enum":
                continue
            out[key] = ({k: stripped(v) for k, v in value.items()} if key in CHILD_MAPS and isinstance(value, dict)
                        else stripped(value))
        return out
    if isinstance(node, list):
        return [stripped(item) for item in node]
    return node


def releases(directory=RELEASES):
    """[(version tuple, path)] of the kept release copies, oldest first."""
    found = []
    for path in Path(directory).glob("*/wire.json"):
        parsed = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", path.parent.name)
        if parsed:
            found.append((tuple(int(p) for p in parsed.groups()), path.parent))
    return sorted(found)


def window(kept):
    """The copies inside the window: the newest two minor releases kept (the code here ships as the next release of
    one of them, and runs with the other)."""
    minors = sorted({version[:2] for version, _ in kept})[-2:]
    return [(version, path) for version, path in kept if version[:2] in minors]


# --- validating a value --------------------------------------------------------------------------------------------

VALIDATING = {"$ref", "anyOf", "oneOf", "allOf", "const", "enum", "type", "properties", "required",
              "additionalProperties", "patternProperties", "propertyNames", "maxProperties", "items", "prefixItems",
              "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum",
              "exclusiveMaximum", "$defs"}
JSON_TYPES = {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer", float: "number",
              type(None): "null"}


def json_type(value):
    return JSON_TYPES[type(value)]


def resolve(schema, root):
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[1]
        schema = root["$defs"][name]
        seen += 1
        if seen > 50:
            raise ValueError("a $ref loop at " + name)
    return schema


def errors(value, schema, root, where="$"):
    """Every way `value` fails `schema` (the JSON Schema keywords the contract uses); [] when it conforms. A keyword
    the validator does not know is an error in the contract, never silently passed."""
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [where + ": nothing is allowed here"]
    unknown = {k for k in schema if k not in VALIDATING and k not in ANNOTATIONS and not k.startswith("x-")}
    if unknown:
        raise ValueError("%s: the contract uses keywords the validator does not know: %s" % (where, sorted(unknown)))
    if "$ref" in schema:
        return errors(value, resolve(schema, root), root, where)
    found = []
    for key in ("anyOf", "oneOf"):
        if key in schema:
            results = [errors(value, option, root, where) for option in schema[key]]
            passing = sum(1 for r in results if not r)
            if not passing:
                found.append("%s: matches no option (%s)" % (where, "; ".join(r[0] for r in results if r)))
            elif key == "oneOf" and passing > 1:
                found.append(where + ": matches more than one option")
    for option in schema.get("allOf", []):
        found += errors(value, option, root, where)
    if "const" in schema and not same(value, schema["const"]):
        found.append("%s: %r is not %r" % (where, value, schema["const"]))
    if "enum" in schema and not any(same(value, option) for option in schema["enum"]):
        found.append("%s: %r is not one of %r" % (where, value, schema["enum"]))
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        actual = json_type(value)
        if actual not in kinds and not (actual == "integer" and "number" in kinds):
            return found + ["%s: %s is not %s" % (where, actual, "/".join(kinds))]
    if isinstance(value, dict):
        found += object_errors(value, schema, root, where)
    elif isinstance(value, list):
        found += array_errors(value, schema, root, where)
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            found.append("%s: shorter than %d" % (where, schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            found.append("%s: longer than %d" % (where, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            found.append("%s: %r does not match %s" % (where, value, schema["pattern"]))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        for key, fails in (("minimum", lambda v, b: v < b), ("maximum", lambda v, b: v > b),
                           ("exclusiveMinimum", lambda v, b: v <= b), ("exclusiveMaximum", lambda v, b: v >= b)):
            if key in schema and fails(value, schema[key]):
                found.append("%s: %r is outside %s %r" % (where, value, key, schema[key]))
    return found


def same(a, b):
    """JSON equality: true is not 1."""
    return type(a) is type(b) and a == b if isinstance(a, bool) or isinstance(b, bool) else a == b


def object_errors(value, schema, root, where):
    found = []
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in value:
            found.append("%s: missing %s" % (where, key))
    if "maxProperties" in schema and len(value) > schema["maxProperties"]:
        found.append("%s: more than %d fields" % (where, schema["maxProperties"]))
    for key, item in value.items():
        if "propertyNames" in schema:
            found += errors(key, schema["propertyNames"], root, where + "<key>")
        if key in properties:
            found += errors(item, properties[key], root, where + "." + key)
            continue
        patterned = [s for p, s in schema.get("patternProperties", {}).items() if re.search(p, key)]
        if patterned:
            for sub in patterned:
                found += errors(item, sub, root, where + "." + key)
            continue
        extra = schema.get("additionalProperties", True)
        if extra is False:
            found.append("%s: unexpected field %s" % (where, key))
        elif isinstance(extra, dict):
            found += errors(item, extra, root, where + "." + key)
    return found


def array_errors(value, schema, root, where):
    found = []
    if "minItems" in schema and len(value) < schema["minItems"]:
        found.append("%s: fewer than %d items" % (where, schema["minItems"]))
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        found.append("%s: more than %d items" % (where, schema["maxItems"]))
    prefix = schema.get("prefixItems", [])
    for i, item in enumerate(value):
        sub = prefix[i] if i < len(prefix) else schema.get("items", True)
        found += errors(item, sub, root, "%s[%d]" % (where, i))
    return found


# --- comparing two schemas -----------------------------------------------------------------------------------------

class Compare:
    """Whether a reader schema takes everything a writer schema allows, conservatively: a problem reported may be
    one a real value never hits, never the other way round. `lenient`: the reader ignores fields it does not know
    (a runner reading an answer), rather than refusing them (a server reading a request). `gated`: paths whose
    problems do not count, inside an optional value's `anyOf` too (the wire contract's `gated`)."""

    def __init__(self, reader_root, writer_root, lenient, gated=()):
        self.reader_root, self.writer_root, self.lenient = reader_root, writer_root, lenient
        self.gated = tuple(gated)
        self.problems, self.seen = [], set()

    def check(self, reader, writer, where):
        reader_ref = reader.get("$ref") if isinstance(reader, dict) else None
        writer_ref = writer.get("$ref") if isinstance(writer, dict) else None
        if reader_ref or writer_ref:
            pair = (reader_ref, writer_ref, where if not (reader_ref and writer_ref) else "")
            if pair in self.seen:
                return
            self.seen.add(pair)
        reader, writer = resolve(reader, self.reader_root), resolve(writer, self.writer_root)
        if reader is True or reader == {} or stripped(reader) == {}:
            return
        if writer is False:
            return
        if writer is True or stripped(writer) == {}:
            self.problems.append(where + ": the writer may send anything; the reader is narrower")
            return
        for key in ("anyOf", "oneOf"):
            if key in writer:
                for option in writer[key]:
                    self.check(reader, {**{k: v for k, v in writer.items() if k != key}, **option}
                               if len(writer) > 1 else option, where)
                return
        for key in ("anyOf", "oneOf"):
            if key in reader:
                tries = []
                for option in reader[key]:
                    sub = Compare(self.reader_root, self.writer_root, self.lenient, self.gated)
                    sub.seen = set(self.seen)
                    sub.check(option, writer, where)
                    problems = [p for p in sub.problems if not p.startswith(self.gated)]
                    if not problems:
                        return
                    tries.append(problems)
                best = min(tries, key=len)
                self.problems.append("%s: no reader option takes what the writer sends (%s)" % (where, best[0]))
                return
        self.values(reader, writer, where)
        writer_types = self.types(writer)
        if "object" in (writer_types or {"object"}) and ("properties" in writer or "object" in (writer_types or ())):
            self.objects(reader, writer, where)
        if "array" in (writer_types or ()):
            self.arrays(reader, writer, where)
        self.bounds(reader, writer, where)

    @staticmethod
    def types(schema):
        if "const" in schema:
            return {json_type(schema["const"])}
        if "enum" in schema and "type" not in schema:
            return {json_type(v) for v in schema["enum"]}
        kinds = schema.get("type")
        return None if kinds is None else {kinds} if isinstance(kinds, str) else set(kinds)

    def values(self, reader, writer, where):
        reader_types, writer_types = self.types(reader), self.types(writer)
        if reader_types is not None:
            if writer_types is None:
                self.problems.append("%s: the writer's type is open; the reader takes only %s" % (where, sorted(reader_types)))
            else:
                for kind in sorted(writer_types):
                    if kind not in reader_types and not (kind == "integer" and "number" in reader_types):
                        self.problems.append("%s: the writer may send %s; the reader takes %s"
                                             % (where, kind, "/".join(sorted(reader_types))))
        reader_values = [reader["const"]] if "const" in reader else reader.get("enum")
        writer_values = [writer["const"]] if "const" in writer else writer.get("enum")
        if reader_values is None or reader.get("x-open-enum"):
            return
        if writer_values is None:
            self.problems.append("%s: the reader takes only %r; the writer is not limited to them" % (where, reader_values))
            return
        for value in writer_values:
            if not any(same(value, r) for r in reader_values):
                self.problems.append("%s=%s: the reader does not know this value" % (where, json.dumps(value)))

    def objects(self, reader, writer, where):
        reader_props, writer_props = reader.get("properties", {}), writer.get("properties", {})
        writer_required = set(writer.get("required", []))
        for key in reader.get("required", []):
            if key not in writer_required:
                self.problems.append("%s: the reader requires %s; the writer may leave it out" % (where, key))
        reader_extra = True if self.lenient else reader.get("additionalProperties", True)
        for key, sub in writer_props.items():
            if key in reader_props:
                self.check(reader_props[key], sub, where + "." + key)
                continue
            patterned = [s for p, s in reader.get("patternProperties", {}).items() if re.search(p, key)]
            if patterned:
                for option in patterned:
                    self.check(option, sub, where + "." + key)
            elif reader_extra is False:
                self.problems.append("%s.%s: the reader refuses this field" % (where, key))
            elif isinstance(reader_extra, dict):
                self.check(reader_extra, sub, where + "." + key)
        writer_extra = [writer.get("additionalProperties", True), *writer.get("patternProperties", {}).values()]
        open_writer = [e for e in writer_extra if e is not False]
        if open_writer and not self.lenient:
            if reader_extra is False and not reader.get("patternProperties"):
                self.problems.append(where + ": the writer may send fields of any name; the reader refuses unknown ones")
            elif isinstance(reader_extra, dict):
                for extra in open_writer:
                    self.check(reader_extra, extra if isinstance(extra, dict) else True, where + ".*")
        if "maxProperties" in reader and reader["maxProperties"] < writer.get("maxProperties", float("inf")):
            self.problems.append("%s: the reader takes at most %d fields" % (where, reader["maxProperties"]))
        if "propertyNames" in reader:
            self.check(reader["propertyNames"], writer.get("propertyNames", {"type": "string"}), where + "<key>")

    def arrays(self, reader, writer, where):
        reader_prefix, writer_prefix = reader.get("prefixItems", []), writer.get("prefixItems", [])
        for i, sub in enumerate(writer_prefix):
            self.check(reader_prefix[i] if i < len(reader_prefix) else reader.get("items", True), sub,
                       "%s[%d]" % (where, i))
        if "items" in writer or not writer_prefix:
            for i in range(len(writer_prefix), len(reader_prefix)):
                self.check(reader_prefix[i], writer.get("items", True), "%s[%d]" % (where, i))
            self.check(reader.get("items", True), writer.get("items", True), where + "[]")

    def bounds(self, reader, writer, where):
        for key, wider in (("minLength", lambda r, w: r > w), ("minItems", lambda r, w: r > w),
                           ("minimum", lambda r, w: r > w), ("exclusiveMinimum", lambda r, w: r > w)):
            if key in reader and ("const" not in writer and "enum" not in writer):
                if key not in writer or wider(reader[key], writer[key]):
                    self.problems.append("%s: the reader's %s is %r, the writer's %r" % (where, key, reader[key], writer.get(key)))
        for key in ("maxLength", "maxItems", "maximum", "exclusiveMaximum"):
            if key in reader and ("const" not in writer and "enum" not in writer):
                if key not in writer or reader[key] < writer[key]:
                    self.problems.append("%s: the reader's %s is %r, the writer's %r" % (where, key, reader[key], writer.get(key)))
        if "pattern" in reader and writer.get("pattern") != reader["pattern"] and "enum" not in writer and "const" not in writer:
            self.problems.append("%s: the reader's pattern is %s, the writer's %s" % (where, reader["pattern"], writer.get("pattern")))


def compare(reader, reader_root, writer, writer_root, lenient, where, gated=()):
    run = Compare(reader_root, writer_root, lenient, gated)
    run.check(reader, writer, where)
    return run.problems


def ref(name):
    return {"$ref": "#/$defs/" + name}


def breaks(new, old, old_name="the previous release"):
    """Every incompatibility between the contract `new` and a release copy `old`, in both directions."""
    found = []
    new_wire, old_wire = new["wire"], old["wire"]
    gated = {k for k in new_wire.get("gated", {}) if not k.startswith("x-")}
    for name, old_end in old_wire["endpoints"].items():
        end = new_wire["endpoints"].get(name)
        if end is None:
            found.append("old runner, new server: %s is gone (%s)" % (name, old_end["path"]))
            continue
        if (end["method"], end["path"]) != (old_end["method"], old_end["path"]):
            found.append("both ways: %s moved from %s %s" % (name, old_end["method"], old_end["path"]))
            continue
        if "request" in old_end:
            for p in compare(ref(end["request"]), new["requests"], ref(old_end["request"]), old["requests"], False,
                             name + ".request"):
                found.append("old runner, new server: " + p)
            for p in compare(ref(old_end["request"]), old["requests"], ref(end["request"]), new["requests"], False,
                             name + ".request", gated):
                if not any(p.startswith(g) for g in gated):
                    found.append("new runner, old server (list it under gated once the runner waits for it): " + p)
        answers = [("answer", end.get("answer"), old_end.get("answer"))] + [
            ("stream." + frame, end.get("stream", {}).get(frame), old_frame)
            for frame, old_frame in old_end.get("stream", {}).items()]
        for label, current, previous in answers:
            if previous is None:
                continue
            if current is None:
                found.append("old runner, new server: %s.%s is gone" % (name, label))
                continue
            for p in compare(ref(previous), old_wire, ref(current), new_wire, True, name + "." + label):
                found.append("old runner, new server: " + p)
            for p in compare(ref(current), new_wire, ref(previous), old_wire, True, name + "." + label):
                found.append("new runner, old server: " + p)
    for name in new_wire["endpoints"]:
        if name not in old_wire["endpoints"] and name not in gated:
            found.append("new runner, old server (list it under gated once the runner copes without it): "
                         "%s is new since %s" % (name, old_name))
    error, old_error = {"$ref": "#/$defs/" + new_wire["error"]}, {"$ref": "#/$defs/" + old_wire["error"]}
    for p in compare(old_error, old_wire, error, new_wire, True, "error"):
        found.append("old runner, new server: " + p)
    for p in compare(error, new_wire, old_error, old_wire, True, "error"):
        found.append("new runner, old server: " + p)
    return found


# --- the command ---------------------------------------------------------------------------------------------------

def snapshot(version, contract=CONTRACT, directory=RELEASES):
    """Keep the current contract and its fixtures as release `version`'s copy."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("A release copy is named for a plain release (1.2.3), not " + version)
    target = Path(directory) / version
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ("wire.json", "requests.json"):
        shutil.copy2(Path(contract) / name, target / name)
    shutil.copytree(Path(contract) / FIXTURES, target / FIXTURES)
    # A copy outside the window is no longer anyone's promise.
    inside = {path for _, path in window(releases(directory))}
    for _, path in releases(directory):
        if path not in inside:
            shutil.rmtree(path)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write-requests", action="store_true", help="regenerate requests.json from backend/models.py")
    parser.add_argument("--snapshot", metavar="VERSION", help="keep the current contract as this release's copy")
    args = parser.parse_args(argv)
    if args.write_requests:
        (CONTRACT / "requests.json").write_text(render(generate_requests()))
        print("Wrote", (CONTRACT / "requests.json").relative_to(ROOT))
        return 0
    if args.snapshot:
        print("Kept", snapshot(args.snapshot).relative_to(ROOT))
        return 0
    try:
        current = load()
    except (OSError, ValueError) as e:
        print("The runner wire contract cannot be read:", e)
        return 2
    if stripped(current["requests"]) != stripped(generate_requests()):
        print("contracts/runner/requests.json is stale: run python scripts/check_wire_contract.py --write-requests")
        return 1
    kept = window(releases())
    if not kept:
        print("No release copy to compare with under", RELEASES.relative_to(ROOT))
        return 2
    failed = False
    for version, path in kept:
        name = ".".join(map(str, version))
        found = breaks(current, load(path), name)
        print("%s: %s" % (name, "compatible both ways" if not found else "%d break(s)" % len(found)))
        for line in found:
            print("  " + line)
        failed = failed or bool(found)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
