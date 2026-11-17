# pyright: strict
"""The answers of the Files routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import ACTORS, SCHEMAS, items, obj

SCHEMAS.update(
    {
        "VersionReview": obj(
            {
                "note": "n",
                "comment_id": "n",
                "ask": {"oneOf": [ref("TaskReviewAskView"), {"type": "null"}]},
                "answers": items(ref("ReviewAnswer")),
            }
        ),
        "BotFile": obj(
            {
                "id": "s",
                "bot": "s",
                "title": "s",
                "kind": "s",
                "mime": "s",
                "locator": "s",
                "scope": "s",
                "version": "i",
                "state": "s",
                "synced": "b",
                "size": {"type": ["integer", "null"]},
                "name": "n",
                "thumb_blob_id": {
                    "type": ["string", "null"],
                    "description": "set when an image has a small preview at /api/v2/files/{id}/thumb",
                },
                "open": {
                    "type": ["object", "null"],
                    "description": "{type: tico|external, url}: "
                    "a Tico route, or the provider's address; null when nothing can be opened",
                },
                "provider": "s",
                "provider_label": "s",
                "note": "s",
                "source": "s",
                "task_id": "n",
                "task_title": "n",
                "working": "b",
                "github_url": "n",
                "actor": "n",
                "action": "n",
                "first_activity_at": "s",
                "last_activity_at": "s",
                "archived": "b",
            },
            required=[
                "id",
                "bot",
                "title",
                "kind",
                "locator",
                "scope",
                "version",
                "state",
                "synced",
                "open",
                "working",
                "last_activity_at",
            ],
        ),
        "BotFileList": obj(
            {
                "bot": "s",
                "files": items(ref("BotFile")),
                "total": "i",
                "next_cursor": "n",
                "has_more": "b",
                "can_manage": "b",
            },
            required=["bot", "files", "total", "next_cursor", "has_more", "can_manage"],
            actors=ACTORS,
        ),
        "BotFileMentions": obj(
            {
                "bot": "s",
                "files": {
                    "type": "object",
                    "additionalProperties": ref("BotFile"),
                    "description": "{name as sent, trimmed: the file}; a name that is not exactly one file the caller may "
                    "see (none, two or more, or one they may not read) is absent",
                },
            },
            required=["bot", "files"],
        ),
        "BotFileResult": obj({"file": "o", "created": "b"}, required=["file"]),
        "FileActivity": obj({"file": "s", "activity": "a"}, actors=ACTORS),
        "FileVersions": obj({"file": "s", "versions": "a"}),
        "LibraryFile": obj(
            {
                "id": "s",
                "bot": "s",
                "bot_name": "s",
                "title": "s",
                "kind": "s",
                "library_kind": {"type": "string", "enum": ["page", "document", "image", "data", "media", "other"]},
                "kind_label": "s",
                "version": "i",
                "last_activity_at": "s",
                "archived": "b",
                "page": {"type": ["object", "null"], "description": "{cadence, archived} for a page"},
                "pins": {"type": "array", "description": "Where the file is pinned, when pins exist"},
            },
            required=["id", "bot", "title", "kind", "library_kind", "version", "last_activity_at"],
        ),
        "FileLibrary": obj({"files": items(ref("LibraryFile")), "has_more": "b", "next_cursor": "n"}),
        "FileSearchHit": obj(
            {
                "id": "s",
                "title": "s",
                "kind": "s",
                "library_kind": "s",
                "bot": "s",
                "bot_name": "s",
                "version": "i",
                "updated_at": "s",
                "url": "n",
                "page": "b",
                "excerpt": "s",
                "score": "f",
            }
        ),
        "FileSearch": obj({"results": items(ref("FileSearchHit"))}),
        "FileDeleted": obj({"deleted": "s", "versions": "i"}),
    }
)
