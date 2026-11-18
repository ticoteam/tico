# pyright: strict
"""The answers of the Docs routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "InternalDocRow": obj(
            {
                "id": "s",
                "path": "s",
                "title": "s",
                "updated_at": "s",
                "updated_by_id": "s",
                "locked": "b",
                "version": "i",
            },
            updated_by_name={"type": "string", "description": "Display name of updated_by"},
        ),
        "InternalDoc": obj(
            {
                "id": "s",
                "path": "s",
                "title": "s",
                "body": "s",
                "locked": "b",
                "version": "i",
                "created_by_id": "s",
                "created_at": "s",
                "updated_by_id": "s",
                "updated_at": "s",
                "archived": "b",
            },
            created_by_name={"type": "string"},
            updated_by_name={"type": "string", "description": "Display name of updated_by"},
        ),
        "InternalDocList": obj({"docs": items(ref("InternalDocRow")), "next_cursor": "n"}),
        "InternalDocResult": obj({"doc": ref("InternalDoc")}),
        "InternalDocVersion": obj(
            {"version": "i", "title": "s", "path": "s", "actor": "s", "created_at": "s", "note": "s"},
            actor_name={"type": "string"},
            size={"type": "integer"},
            current={"type": "boolean"},
        ),
        "InternalDocVersions": obj({"doc": "s", "versions": items(ref("InternalDocVersion"))}),
        "InternalDocVersionResult": obj(
            {
                "version": obj(
                    {
                        "version": "i",
                        "title": "s",
                        "path": "s",
                        "body": "s",
                        "actor": "s",
                        "created_at": "s",
                        "note": "s",
                    },
                    actor_name={"type": "string"},
                )
            }
        ),
        "LinkedDoc": obj(
            {
                "id": "s",
                "title": "s",
                "url": "s",
                "kind": {"enum": ["website", "google_drive", "google_doc", "notion", "github", "other"]},
                "host": "s",
                "description": "s",
                "added_by": "s",
                "created_at": "s",
                "updated_at": "s",
            },
            added_by_name={"type": "string"},
        ),
        "LinkedDocList": obj({"linked": items(ref("LinkedDoc"))}),
        "LinkedDocResult": obj({"linked": ref("LinkedDoc")}),
        "DocSource": obj(
            {
                "id": "s",
                "title": "s",
                "url": "s",
                "kind": "s",
                "provider": "s",
                "description": "s",
                "added_by": "s",
                "repo": "s",
                "reference": "s",
                "reference_override": "s",
                "path": "s",
                "status": {
                    "enum": [
                        "link_only",
                        "queued",
                        "preparing",
                        "updating",
                        "ready",
                        "empty",
                        "failed",
                        "access_needed",
                    ]
                },
                "last_checked_at": "n",
                "doc_count": "i",
                "skipped": "i",
                "error_code": "s",
            }
        ),
        "DocSourceList": obj({"sources": items(ref("DocSource")), "next_cursor": "n"}),
        "DocSourceResult": obj({"source": ref("DocSource")}),
        "SourceDocumentList": obj(
            {
                "source": ref("DocSource"),
                "documents": items(obj({"id": "s", "title": "s", "path": "s", "commit": "s"})),
                "next_cursor": "n",
            }
        ),
        "ExternalHit": obj(
            {
                "type": {"enum": ["external"]},
                "id": "s",
                "title": "s",
                "path": "s",
                "section": "s",
                "excerpt": "s",
                "score": "f",
                "trust": {"enum": ["untrusted_external"]},
                "source_id": "s",
                "source_title": "s",
                "source_url": "s",
                "commit": "s",
                "last_checked_at": "n",
                "citation_url": "s",
            },
            collection={"type": "string"},
        ),
        "ExternalDocResult": obj(
            {
                "doc": obj(
                    {
                        "id": "s",
                        "type": {"enum": ["external"]},
                        "title": "s",
                        "path": "s",
                        "content": "s",
                        "trust": {"enum": ["untrusted_external"]},
                        "source_id": "s",
                        "source_title": "s",
                        "source_url": "s",
                        "commit": "s",
                        "last_checked_at": "n",
                        "citation_url": "s",
                        "reference_notice": "s",
                    }
                )
            }
        ),
        "DocsSearchResults": obj(
            {
                "results": items(
                    {
                        "oneOf": [
                            ref("ExternalHit"),
                            obj(
                                {
                                    "type": {"enum": ["internal"]},
                                    "id": "s",
                                    "path": "s",
                                    "title": "s",
                                    "excerpt": "s",
                                    "score": "f",
                                },
                                required=["type", "id", "path", "title", "excerpt", "score"],
                                section={"type": "string"},
                                anchor={"type": "string"},
                                collection={"type": "string"},
                            ),
                            obj(
                                {
                                    "type": {"enum": ["linked"]},
                                    "id": "s",
                                    "title": "s",
                                    "url": "s",
                                    "kind": "s",
                                    "description": "s",
                                    "score": "f",
                                }
                            ),
                            obj(
                                {
                                    "type": {"enum": ["manual"]},
                                    "id": "s",
                                    "title": "s",
                                    "path": "s",
                                    "url": "s",
                                    "excerpt": "s",
                                    "score": "f",
                                },
                                required=["type", "id", "title", "path", "url", "excerpt", "score"],
                                section={"type": "string"},
                                anchor={"type": "string"},
                                collection={"type": "string"},
                            ),
                        ]
                    }
                )
            }
        ),
        "DocSearch": obj({"query": "s", "results": "a", "has_more": "b", "mode": "s"}),
        "Librarian": obj(
            {"available": "b", "state": "s", "bot": "s", "name": "s", "can_turn_on": "b"},
            required=["available", "state", "bot", "name", "can_turn_on"],
        ),
        "DocsAsked": obj(
            {
                "conversation_id": "s",
                "message_id": "s",
                "results": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Matching internal, linked and manual docs, as GET /api/v2/docs/search?collection=all",
                },
            },
            required=["conversation_id", "message_id", "results"],
        ),
        "DocsConversations": obj(
            {
                "conversations": items(
                    obj({"id": "s", "title": "s", "created_at": "s", "last_message_at": "n", "closed_at": "n"})
                ),
                "next_offset": {"type": ["integer", "null"]},
            }
        ),
        "DocsReopened": obj({"conversation_id": "s"}),
        "LibrarianResearchFiled": obj({"task_id": "s", "bot": "s", "kind": "s", "target": "s"}),
        "LibrarianMarketSetup": obj(
            {
                "can_answer": "b",
                "answers": obj({"sell": "s", "buyers": "s", "competitors": "s", "watch": "s"}),
                "step": "i",
                "skipped": "b",
                "task": {"type": ["object", "null"]},
            }
        ),
        "LibrarianSetup": obj(
            {
                "available": "b",
                "docs": obj({"show": "b", "empty": "b", "skipped": "b", "task": {"type": ["object", "null"]}}),
                "market": ref("LibrarianMarketSetup"),
            }
        ),
    }
)
