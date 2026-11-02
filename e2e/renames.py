"""The rename map a codemod applies, read by the e2e kinds (see e2e/README.md "Renames").

YAML with these sections:
    fields:     {old_key: new_key}          golden JSON keys, at any depth (the API contract)
    columns:    {"table.old": new_column}   a column renamed in place (table named by its old name)
    tables:     {old_table: new_table}      a table renamed
    api_fields: {old_key: new_key}          as `fields`: an API name that is no column's name
    api_kept:   [key, ...]                  a column's old name the API still says somewhere: not derived into `fields`
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
CODEMOD_SECTIONS = {"base", "applied_waves", "values", "waves", "collisions", "api_fields", "api_kept"}
EMPTY = {section: {} for section in SECTIONS}


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


def apply_fields(value, fields):
    """The golden with its keys renamed (old -> new), at every depth. Values are left as they are."""
    if not fields:
        return value
    if isinstance(value, dict):
        return {fields.get(k, k): apply_fields(v, fields) for k, v in value.items()}
    if isinstance(value, list):
        return [apply_fields(v, fields) for v in value]
    return value
