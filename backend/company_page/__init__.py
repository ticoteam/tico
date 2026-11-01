"""The Company page: scheduled editions of a company's updates for outside audiences, on a read-only site that is not
the app. docs/company-page.md is the user's description.

Modules, and who may call what. Everything outside this package goes through these names:

  schema.py      COMPANY_PAGE_SCHEMA, applied by hubdb.MIGRATIONS.
  settings.py    get(c) -> dict; set_transparency(c, actor, value); update(c, actor, **fields). TRANSPARENCY = closed,
                 private, open. A change that takes anything away calls release.rebuild(c, reason) in the same step.
  audiences.py   list_all(c); get(c, id); create/update/delete(c, actor, ...); sources(c, id); members(c, id).
  disclosure.py  DATA_CLASSES; MODES = show, rounded, range, delayed, hidden (missing rule = hidden); PRESETS;
                 rule_for(c, audience_id, data_class, ref_id=None); apply_preset(c, actor, audience_id, name);
                 set_rule(c, actor, audience_id, ...), which writes a disclosure_revisions row;
                 current_revision(c, audience_id); is_loosening(old_rule, new_rule).
  stats.py       project(c, audience_id, at) -> {"stats": [Stat], "held": [{"id", "label", "reason"}]}. The one way any
                 KPI or Finance value leaves the install. Stat fields are exactly those of the view model below.
                 facts(c, audience_id, period_start, period_end) -> the filtered structured facts an edition may use.
                 board_config_hash(c, audience_id); board_approved(c, audience_id) -> bool.
  editions.py    draft(store, audience_id, author, period_start, period_end); edit(c, who, id, body);
                 check(store, id); request_approval(c, who, id); publish(c, who, id, approval_id); retract(c, who, id,
                 note); correct(c, who, id, body, note); stream(c, audience_ids, before=None, limit=10) -> [Update].
  schedule.py    next_due(audience, after, zone) -> datetime; tick(store, at); next_wake(c, at) -> datetime or None.
  render.py      page(view: dict) -> str; signed_out(view: dict) -> str; feed(view: dict) -> str. Pure functions of
                 the view model, no database, no I/O.
  release.py     public_view(c, at) -> dict; rebuild(store_or_c, reason); current_dir(settings) -> Path or None.
                 Rendered files are the only way public content leaves.
  readers.py     add/remove(c, actor, ...); import_csv; invites; resolve_login(c, issuer, sub, email); start_session;
                 session_reader(c, token); audiences_of(c, reader_id); reader_view(c, reader_id, at, before=None).
  reader_app.py  The reader origin: the route allowlist, its sign-in and page routes.
  api.py, api_editions.py, api_readers.py   register(app, ...): the /api/v2 routes for owners and admins, below.

Routes (owners and admins write; every list is paged and byte-capped):

  api.py          GET, PATCH /api/v2/company-page; GET /api/v2/company-page/preview?audience=ID (the rendered page);
                  GET, POST /api/v2/audiences; PATCH, DELETE /api/v2/audiences/{id};
                  GET, PUT /api/v2/audiences/{id}/disclosure; POST /api/v2/audiences/{id}/disclosure/preset;
                  GET, POST /api/v2/audiences/{id}/sources; DELETE /api/v2/audiences/{id}/sources/{source_id};
                  GET, POST /api/v2/company-page/stats; PATCH, DELETE /api/v2/company-page/stats/{id};
                  GET /api/v2/audiences/{id}/stats (the projection and what is held)
  api_editions.py GET, POST /api/v2/editions; GET, PATCH /api/v2/editions/{id};
                  POST /api/v2/editions/{id}/check, /approval, /publish, /retract, /correct, /override;
                  POST /api/v2/audiences/{id}/stats/approval
  api_readers.py  GET, POST /api/v2/readers; DELETE /api/v2/readers/{id}; POST /api/v2/readers/import;
                  GET, POST /api/v2/audiences/{id}/invites; DELETE /api/v2/audiences/{id}/invites/{invite_id};
                  GET /api/v2/reader-messages

Config: settings.reader_origin (TICO_READER_ORIGIN) and settings.company_page_dir (TICO_COMPANY_PAGE_DIR; by default the
folder company-page-public in the data directory). Inside it: releases/ID/ and a relative `current` symlink, swapped
atomically.

The view model every surface renders. No other field may reach a reader, and each is a plain string, number or list:

  {"company": {"name", "logo_url", "mission", "badge": "open"|"public"|"private", "accent", "updated_at"},
   "audience": {"kind", "name"},
   "stats": [{"id", "label", "definition", "chart": "number"|"line"|"bars", "period", "as_of", "display", "stale": bool,
              "series": [{"period", "value": number or null, "display"}]}],
   "updates": [{"anchor", "title", "author": {"name", "kind": "bot"|"person"|"company"}, "period", "published_at",
                "version", "correction_note", "sections": [{"key": "highlights"|"lowlights"|"numbers"|"asks"|"next",
                                                            "items": [{"body", "ask_id": str or null}]}]}],
   "next_before": str or null}
"""
