"""The published Tico starter org charts the CEO Bot may use to discuss team structure.

These checked-in model files are the published catalog. This read never consults the
install's imported company plan or another company's bots, people or work.
"""
import json
from pathlib import Path


_MODELS = ("everything", "local", "services", "software", "store")
_ROOT = Path(__file__).parent / "company_file"


def published():
    """Return only the public recommendation fields from the bundled chart models."""
    out = []
    for key in _MODELS:
        model = json.loads((_ROOT / f"model_{key}.json").read_text(encoding="utf-8"))
        out.append({"model": model["model"], "name": model["name"], "about": model["about"],
                    "seats": [{k: seat[k] for k in ("team", "template", "title") if k in seat}
                              for seat in model["seats"]]})
    return out
