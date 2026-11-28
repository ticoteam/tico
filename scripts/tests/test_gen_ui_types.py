"""scripts/gen_ui_types.py: the web app's schema.d.ts, and its ApiOperations map that types web/src/api/client.ts's
`operation`. A small hand-made document stands in for backend/openapi_v2.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gen_ui_types as gen  # noqa: E402

JSON = 'application/json'


def answer(media, schema=None):
    return {'description': 'ok', 'content': {media: {'schema': schema or {}}}}


THING = {'$ref': '#/components/schemas/Thing'}
DOCUMENT = {
    'components': {
        'schemas': {
            'Empty': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'Thing': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']},
        }
    },
    'paths': {
        '/api/v2/things': {
            'post': {
                'operationId': 'createThing',
                'requestBody': {'required': True, 'content': {JSON: {'schema': THING}}},
                'responses': {'201': answer(JSON, THING)},
            }
        },
        '/api/v2/things/{tid}': {
            'delete': {
                'operationId': 'deleteThing',
                'parameters': [{'in': 'path', 'name': 'tid', 'required': True, 'schema': {'type': 'string'}}],
                'requestBody': {
                    'content': {JSON: {'schema': {'anyOf': [{'$ref': '#/components/schemas/Empty'}, {'type': 'null'}]}}}
                },
                'responses': {'204': {'description': 'gone'}},
            }
        },
        '/api/v2/uploads': {
            'post': {
                'operationId': 'upload',
                'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {}}}},
                'responses': {'200': answer(JSON, THING)},
            }
        },
        '/api/v2/events': {'get': {'operationId': 'events', 'responses': {'200': answer('text/event-stream')}}},
        '/api/v2/files/{fid}': {
            'get': {
                'operationId': 'fileBytes',
                'parameters': [{'in': 'path', 'name': 'fid', 'required': True, 'schema': {'type': 'string'}}],
                'responses': {'200': answer('application/octet-stream')},
            }
        },
        '/api/v2/pages': {
            'post': {
                'operationId': 'publishPage',
                'requestBody': {'required': True, 'content': {'text/html': {'schema': {}}}},
                'responses': {'200': answer(JSON, THING)},
            }
        },
    },
}


def test_operations_carry_non_200_answers_optional_bodies_and_closed_empty_objects():
    text = gen.build(DOCUMENT)
    assert 'export type CreateThingResponse = Thing;' in text  # a 201 answer, not void
    assert 'export type Empty = Record<string, never>;' in text  # closed: no field fits
    assert 'export type DeleteThingResponse = void;' in text  # 204: nothing comes back
    assert (
        '  "POST /api/v2/things": { path: never; query: never; body: CreateThingBody; bodyRequired: true; '
        'response: CreateThingResponse };'
    ) in text
    assert (
        '  "DELETE /api/v2/things/{tid}": { path: { tid: string; }; query: never; body: DeleteThingBody; '
        'bodyRequired: false; response: DeleteThingResponse };'
    ) in text
    assert '  "POST /api/v2/uploads": { path: never; query: never; body: UploadBody; bodyRequired: true;' in text


def test_operations_leave_out_what_the_json_client_cannot_carry():
    """A stream, file bytes or a raw upload would be read wrong by `request` (JSON or text, after the whole body)."""
    operations = gen.build(DOCUMENT).split('export interface ApiOperations {')[1]
    for name in ('GET /api/v2/events', 'GET /api/v2/files/{fid}', 'POST /api/v2/pages'):
        assert '"%s"' % name not in operations
    assert 'export type EventsResponse = string;' in gen.build(DOCUMENT)  # its own types stay for hand-made calls
