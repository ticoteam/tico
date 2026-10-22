### Added
- **Routines: one flat, searchable list you can tune.** Settings > Routines now shows Tico's own jobs as one list with no
  group headings and a search box that matches name, trigger, action and department ("done" finds what touches done
  tasks). Each row reads "WHEN every hour THEN check stale tickets", with one department tag (Engineering for built-ins,
  from the org chart). An owner or admin switches the six jobs that use AI or reach people (stale ticket review, Still
  wanted?, KPI stale asks, nightly learnings, Health scan, ask go-ahead) on or off, picks every 15 minutes, hourly, every
  4 hours or daily at a time (AI jobs are never offered every 15 minutes; nightly learnings picks its time), and runs
  them now under the same caps (a stale review still makes at most 30 Decide calls an hour). Health scan stays
  owner-only; members see the list without controls; the server enforces both. Nightly learnings gets an off switch, on
  by default. The Stale ticket review and Start fresh when the topic changes switches moved here from Settings >
  General > Team with their values unchanged; the old spot links here. `PUT /api/v2/system-routines/{key}`,
  `POST /api/v2/system-routines/{key}/run`.
