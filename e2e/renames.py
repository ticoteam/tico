"""The rename map a codemod applies, read by the e2e kinds (see e2e/README.md "Renames").

YAML with these sections:
    fields:     {old_key: new_key}          golden JSON keys, at any depth (the API contract)
    columns:    {"table.old": new_column}   a column renamed in place (table named by its old name)
    tables:     {old_table: new_table}      a table renamed
    api_fields: {old_key: new_key}          as `fields`: an API name that is no column's name
    api_kept:   [key, ...]                  a column's old name the API still says somewhere: not derived into `fields`
    golden_fields: {golden: {old: new}}     as `fields`, for one golden that says a name differently from the rest;
                                            "parent.key" is the key under that parent, ".key" the key at the top,
                                            "key!other" the key unless the object also has `other`
    golden_added: {golden: [parent.key]}    keys a fresh answer gained, left out before comparing with an old golden
A renamed column renames the API field of its name, so `fields` also takes every column's old name, unless columns of
that name went different ways or `api_kept` keeps it. scripts/cleanup/renames.yaml has more sections, about the codemod
itself (its base, its waves); they say nothing about the goldens.
The map is read from the file given to `--rename-map`, or from TICO_E2E_RENAME_MAP when a test runs on its own.
"""
import os
from pathlib import Path

import yaml

ENV = "TICO_E2E_RENAME_MAP"
SECTIONS = ("fields", "columns", "tables")
CODEMOD_SECTIONS = {"base", "applied_waves", "values", "waves", "collisions", "api_fields", "api_kept", "golden_fields",
                    "golden_added"}
EMPTY = {**{section: {} for section in SECTIONS}, "golden_fields": {}, "golden_added": {}}


def load(path):
    data = yaml.safe_load(Path(path).read_text()) or {}
    unknown = set(data) - set(SECTIONS) - CODEMOD_SECTIONS
    if unknown:
        raise ValueError(f"{path}: unknown rename sections {sorted(unknown)}; expected {list(SECTIONS)}")
    out = {section: dict(data.get(section) or {}) for section in SECTIONS}
    for section in SECTIONS:
        for old, new in out[section].items():
            if not isinstance(new, str) or not new or old == new:
                raise ValueError(f"{path}: {section}.{old} must map to a different non-empty name")
    out["fields"] = {**_column_fields(out["columns"], data.get("api_kept") or ()), **(data.get("api_fields") or {}),
                     **out["fields"]}
    out["golden_fields"] = data.get("golden_fields") or {}
    out["golden_added"] = data.get("golden_added") or {}
    return out


def _column_fields(columns, kept):
    """The golden keys the renamed columns rename: each old name that became one new name, and is not kept."""
    went = {}
    for column, new in columns.items():
        went.setdefault(column.partition(".")[2], set()).add(new)
    return {old: next(iter(news)) for old, news in went.items() if len(news) == 1 and old not in kept}


def active():
    path = os.environ.get(ENV)
    return load(path) if path else EMPTY


def golden_fields(rename_map, golden):
    """The keys one golden's names go through."""
    return {**rename_map["fields"], **rename_map["golden_fields"].get(golden, {})}


def apply_fields(value, fields, parent=""):
    """The golden with its keys renamed (old -> new), at every depth. Values are left as they are."""
    if not fields:
        return value
    if isinstance(value, dict):
        return {_new_key(k, value, parent, fields): apply_fields(v, fields, k) for k, v in value.items()}
    if isinstance(value, list):
        return [apply_fields(v, fields, parent) for v in value]
    return value


def _new_key(key, node, parent, fields):
    if f"{parent}.{key}" in fields:
        return fields[f"{parent}.{key}"]
    for old, new in fields.items():
        if old.startswith(key + "!") and old[len(key) + 1:] not in node:
            return new
    return fields.get(key, key)


def drop_added(value, keys, parent=""):
    """A fresh answer without the keys it gained ("parent.key"), to compare it with a golden from before them."""
    if not keys:
        return value
    if isinstance(value, dict):
        return {k: drop_added(v, keys, k) for k, v in value.items() if f"{parent}.{k}" not in keys}
    if isinstance(value, list):
        return [drop_added(v, keys, parent) for v in value]
    return value
