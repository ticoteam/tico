# pyright: strict
"""The answers of the stable v2 contract, declared once in code: one TypedDict per answer, one module per tag. A route
names its answer on its decorator (`**api_v2("Tasks", "listTasks", "...", TaskList)`), FastAPI writes its schema into
the document, and backend/openapi_v2.py keeps only the stable routes. Handlers still return plain dicts and orjson
encodes them (backend/fast_json.py): the TypedDict describes the wire, it never builds or checks it at runtime.

Tags move here from the hand-written JSON Schema in backend/api_schemas/ one at a time; a tag's module there is deleted
when its routes name answers from here. Moved so far: Tasks, Products, Webhooks. An answer that still refers to a
hand-written one does it through the references in _base.py.
"""
