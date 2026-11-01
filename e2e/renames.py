"""The rename map a codemod applies, read by the e2e kinds (see e2e/README.md "Renames").

YAML with up to three sections:
    fields:  {old_key: new_key}          golden JSON keys, at any depth (the API contract)
    columns: {"table.old": new_column}   a column renamed in place (table named by its old name)
    tables:  {old_table: new_table}      a table renamed
The map is read from the file given to `--rename-map`, or from TICO_E2E_RENAME_MAP when a test runs on its own.
"""
import os
from pathlib import Path

import yaml

ENV = "TICO_E2E_RENAME_MAP"
SECTIONS = ("fields", "columns", "tables")
EMPTY = {section: {} for section in SECTIONS}


def load(path):
    data = yaml.safe_load(Path(path).read_text()) or {}
    unknown = set(data) - set(SECTIONS)
    if unknown:
        raise ValueError(f"{path}: unknown rename sections {sorted(unknown)}; expected {list(SECTIONS)}")
    out = {section: dict(data.get(section) or {}) for section in SECTIONS}
    for section in SECTIONS:
        for old, new in out[section].items():
            if not isinstance(new, str) or not new or old == new:
                raise ValueError(f"{path}: {section}.{old} must map to a different non-empty name")
    return out


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
