"""Check a task POST body against the server's own body model, for fake-server browser tests.

    python3 ui/tests/support/task_body_check.py /v2/tasks/t1/move '{"to": "closed", "version": 3}'

The models are the routes' own: POST /api/v2/tasks/{id} takes models.TaskUpdate (backend/app.py update_task) and
POST /api/v2/tasks/{id}/move takes models.TaskMove (backend/task_moves_routes.py task_move). Prints nothing when the
body is accepted, else the server's 422 detail, one line per error. A fake server that answers every body would pass
a request the real one refuses (task #189: Stop sent {close: true} to the edit route).
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic import ValidationError  # noqa: E402

from backend import models  # noqa: E402

ROUTES = [
    (re.compile(r"^/v2/tasks/[^/]+$"), models.TaskUpdate),
    (re.compile(r"^/v2/tasks/[^/]+/move$"), models.TaskMove),
]


def main(path: str, body: str) -> None:
    model = next((m for pattern, m in ROUTES if pattern.match(path)), None)
    if model is None:
        sys.exit(f"no body model known for POST {path}")
    try:
        model.model_validate(json.loads(body))
    except ValidationError as e:
        for err in e.errors():
            print(f"{'.'.join(map(str, err['loc']))}: {err['msg']}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
