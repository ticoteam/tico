# pyright: strict
"""The answers of the Repositories routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "Repository": obj(
            {
                "full_name": "s",
                "enabled": "b",
                "bot_repo": "b",
                "default_branch": "n",
                "setup_command": "n",
                "setup_source": "n",
                "reachable": "b",
                "last_seen_at": "n",
            }
        ),
        "ProductRepositoryPreview": obj(
            {
                "org": "s",
                "name": "s",
                "repository": "s",
                "visibility": {"enum": ["private"]},
                "auto_init": "b",
                "capability": {"enum": ["available", "missing", "unknown", "not_installed"]},
                "capability_detail": "s",
            }
        ),
        "ProductRepositoryResult": obj(
            {
                "repository": "s",
                "html_url": "s",
                "visibility": {"enum": ["private"]},
                "auto_init": "b",
                "installation_access": {"enum": ["available", "owner_action_required", "unverified"]},
                "note": "s",
            }
        ),
        "RepositoryList": obj(
            {
                "repositories": items(ref("Repository")),
                "new_bot_default": {"enum": ["own", "all"]},
                "github_connected": "b",
                "github_installed": "b",
            }
        ),
        # The workflow options are present only when on; a request's access defaults to write.
        "RepositoryGrant": obj(
            {"full_name": "s", "access": {"enum": ["read", "write"]}, "run_workflows": "b", "edit_workflows": "b"},
            required=["full_name"],
        ),
        "BotRepositories": obj(
            {
                "mode": {"enum": ["own", "all", "chosen"]},
                "all_access": {"enum": ["read", "write"]},
                "chosen": items(ref("RepositoryGrant")),
                "all_repos": items(ref("TickedRepository")),
                "effective": items(ref("RepositoryGrant")),
            }
        ),
        # Only the options that are on are present.
        "TickedRepository": obj(
            {"full_name": "s", "run_workflows": "b", "edit_workflows": "b"}, required=["full_name"]
        ),
    }
)
