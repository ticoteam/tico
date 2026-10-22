"""A normalized starter plan shared by setup, Hosted and the installed app.

Transport contains plan data only, never chat, browser state or authority. The
compressed value travels beside the install's secrets, not in command arguments.
"""
import base64
import binascii
import gzip
import json
import zlib

from . import draft as D
from .keys import scrub_all
from .yamlfile import from_yaml, to_yaml

MAX_BYTES = 524288          # a 300-seat plan, normalized, with room (about 180 KB at 300 bots)
MAX_PACKED = 7168


def load(source):
    if isinstance(source, str):
        import yaml
        draft = from_yaml(source)
        document = yaml.safe_load(source)
    elif isinstance(source, dict):
        document = source
        draft = D.check(scrub_all({k: v for k, v in source.items() if k != "models"})[0])
    else:
        raise D.DraftError("the starter plan must be a company file")
    if any(b["existing"] for b in draft["bots"]):
        raise D.DraftError("Connect existing agents in Tico after importing the starter team")
    # A coworker comes along only as the person who holds a seat; they join the team in Tico, never by import.
    holders = {b.get("person") for b in draft["bots"]}
    if any(not p["owner"] and p["id"] not in holders for p in draft["people"]):
        raise D.DraftError("This import supports the owner, AI employees and the people who hold seats; add other coworkers in Tico first")
    if draft["rules"]["mode"] != "defaults":
        raise D.DraftError("Review custom rules in Tico; this starter import uses the default rules")
    # Match the app's setup fields before a cloud installer creates anything.
    if (not draft["company"]["name"].strip() or len(draft["company"]["name"]) > 100
            or len(draft["company"]["about"]) > 2000
            or any(len(person["name"]) > 100 for person in draft["people"])):
        raise D.DraftError("Use a company/owner name up to 100 characters and a company description up to 2000 characters")
    saved = {k: draft[k] for k in D.TOP}
    if "models" in document:
        models = document["models"]
        if not isinstance(models, dict) or models.get("source") not in ("gateway", "own"):
            raise D.DraftError("the company file's model choices are not supported")
        saved["models"] = scrub_all(models)[0]
    try:
        raw = canonical(saved)
    except (ValueError, TypeError, RecursionError):
        raise D.DraftError("the company file contains unsupported values") from None
    if len(raw.encode()) > MAX_BYTES:
        raise D.DraftError("the company file is too large")
    return saved


def canonical(saved):
    return json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def file_text(source):
    """A scrubbed company file for a provisioning worker's private local file."""
    saved = load(source)
    text = to_yaml({k: saved[k] for k in D.TOP})
    if "models" in saved:
        text += "models: " + canonical(saved["models"]) + "\n"
    return text


def pack(source):
    data = base64.b64encode(gzip.compress(canonical(load(source)).encode(), mtime=0)).decode("ascii")
    if len(data) > MAX_PACKED:
        raise D.DraftError("This company file is too large for automatic setup. Run setup again without --company-file; "
                           "then, on the first Welcome step in Tico, under \"Already chose your team?\", choose the "
                           "file in \"Company YAML file\" and click \"Use this team\".")
    return data


def unpack(data):
    try:
        if not isinstance(data, str) or len(data) > MAX_PACKED:
            raise ValueError()
        compressed = base64.b64decode(data, validate=True)
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(compressed, MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError()
        return load(json.loads(raw))
    except (ValueError, TypeError, binascii.Error, zlib.error, RecursionError):
        raise D.DraftError("the saved starter plan is invalid or too large; provide the company file again") from None
