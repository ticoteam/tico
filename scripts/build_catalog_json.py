#!/usr/bin/env python3
"""Build the setup page's role list from the templates.

    backend/company_file/roles.json
                          every role template the tico.team/setup page offers (`hub template list` less the helpers),
                          with its summary and when to suggest it, for the setup page's draft engine

    python3 scripts/build_catalog_json.py           # rewrite it
    python3 scripts/build_catalog_json.py --check   # exit 1 when it is stale (a test runs this)

Run it after changing a card. Tico HQ is not in this repository (it is in ticoteam/tico-services), and
builds its own copy of the catalog, the recommender and the draft engine from a pinned Tico release; the Tico server
builds its own template list at run time with backend/recruit_rank.py `build()`.
"""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
OUT_ROLES = ROOT / "backend" / "company_file" / "roles.json"


def cards(catalog_dir=TEMPLATES / "catalog"):
    out = []
    for card in sorted(Path(catalog_dir).glob("*/card.yaml")):
        try:
            document = yaml.safe_load(card.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(document, dict):
            out.append(document)
    return out


def _strings(value):
    return [str(item) for item in value if isinstance(item, (str, int, float)) and str(item).strip()] \
        if isinstance(value, list) else []


def roles(documents):
    """The setup page's role list: every card `hub template list` serves except the helpers, which sit outside the
    org chart. Built-in (`required`) cards are the `core` department and the assistant reads as "Assistant", as the
    page shows them. `tools` are the prerequisites besides hub; `recommend_when` leaves out `always`."""
    out = []
    for card in documents:
        template = str(card.get("template") or "").strip()
        if not template or card.get("kind") == "helper":
            continue
        slug = str(card.get("slug") or template).strip()
        routine = card.get("first_routine") if isinstance(card.get("first_routine"), dict) else {}
        name = str(card.get("name") or template)
        prerequisites = card.get("prerequisites") if isinstance(card.get("prerequisites"), list) else []
        out.append({
            "slug": slug, "template": template, "name": "Assistant" if "{{" in name else name,
            "department": "core" if card.get("required") else str(card.get("group") or card.get("department") or ""),
            "lead": bool(card.get("lead")), "core": bool(card.get("required")),
            "summary": str(card.get("summary") or ""), "when": str(card.get("when") or ""),
            "tags": _strings(card.get("tags")), "pains": _strings(card.get("pains")),
            "recommend_when": [item for item in _strings(card.get("recommend_when")) if item != "always"],
            "first_routine": " · ".join(str(routine.get(key) or "") for key in ("title", "cadence")) if routine else "",
            "tools": sorted({str(item.get("tool")) for item in prerequisites
                             if isinstance(item, dict) and item.get("tool") and item.get("tool") != "hub"}),
            "default": str(card.get("suggest") or "") == "default" and not card.get("required"),
        })
    out.sort(key=lambda role: role["slug"])
    return {"roles": out}


def build():
    """{path: text} for every file this script writes."""
    return {OUT_ROLES: json.dumps(roles(cards()), indent=1, ensure_ascii=False) + "\n"}


def main(argv):
    outputs = build()
    if "--check" in argv:
        stale = [str(path.relative_to(ROOT)) for path, text in outputs.items()
                 if not path.is_file() or path.read_text() != text]
        if stale:
            print("stale: " + ", ".join(stale) + "; run python3 scripts/build_catalog_json.py", file=sys.stderr)
            return 1
        return 0
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print("%d files: %s" % (len(outputs), ", ".join(sorted(str(path.relative_to(ROOT)) for path in outputs))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
