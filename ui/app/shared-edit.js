/* Shared drafts and authorized place subscriptions for meeting items and task pages. */
'use strict';

function sharedDraft() {
  return {patch: {}, dirty: {}, flight: null, error: '', timer: 0, version: null, current: null};
}
const sharedDraftValue = (item, key, group = '') => (group ? item?.[group]?.[key] : item?.[key]) ?? '';
function sharedDraftPatch(draft, key, value, group = '') {
  if (!(key in draft.dirty)) draft.dirty[key] = sharedDraftValue(draft.base, key, group);
  if (group) (draft.patch[group] ||= {})[key] = value;
  else draft.patch[key] = value;
}
function sharedDraftMerge(a, b) {
  return {...a, ...b, ...((a.detail || b.detail) ? {detail: {...a.detail, ...b.detail}} : {})};
}
function sharedDraftAccepted(draft, patch, item) {
  for (const [group, fields] of [['', Object.keys(patch).filter(k => k !== 'detail')], ['detail', Object.keys(patch.detail || {})]]) {
    for (const key of fields) {
      if (key in (group ? draft.patch[group] || {} : draft.patch)) draft.dirty[key] = sharedDraftValue(item, key, group);
      else delete draft.dirty[key];
    }
  }
  // An invalidation can arrive before the write's answer. Never roll its base backwards.
  draft.base = Number(draft.base?.version) > Number(item.version) ? draft.base : item;
  draft.version = item.version; draft.current = null;
}
async function sharedDraftSave(draft, options) {
  clearTimeout(draft.timer);
  if (draft.flight || draft.sending || !Object.keys(draft.patch).length) return draft.flight?.catch(() => {});
  const patch = draft.patch;
  draft.patch = {}; draft.error = ''; draft.inflightPatch = patch;
  draft.flight = Promise.resolve().then(() => options.write(patch, draft.version));
  try { sharedDraftAccepted(draft, patch, await draft.flight); }
  catch (error) {
    draft.patch = sharedDraftMerge(patch, draft.patch); draft.error = error.message;
    const current = await options.current?.(error);
    if (current) { draft.current = current; draft.version = current.version; draft.base = current; }
  }
  finally { draft.flight = null; draft.inflightPatch = null; }
  if (!draft.error && Object.keys(draft.patch).length && !draft.sending) return sharedDraftSave(draft, options);
  await options.settled?.();
}
const sharedEdited = (actor, name = actorLabel) => actor ? `${name(actor)} edited · just now` : '';

function sharedPlaceStop(state) {
  const shared = state.shared;
  if (!shared) return;
  state.shared = null;
  shared.source?.close(); clearInterval(shared.timer); clearInterval(shared.safety);
  document.removeEventListener('visibilitychange', shared.visibility);
  window.removeEventListener('pagehide', shared.leave);
  shared.leave();
}
function sharedPlaceStart(state, options) {
  const {id, place, topic, filter, changed, gone, present} = options;
  if (state.shared?.place === place) return;
  sharedPlaceStop(state);
  const shared = state.shared = {id, place, session: newRequestId()};
  const current = () => state.shared === shared;
  const lost = error => { if (current() && [403, 404].includes(error.status)) gone(); };
  const presence = async () => {
    try {
      const reply = await get('/v2/presence?place=' + encodeURIComponent(place), {fresh: true});
      if (!current()) return;
      const others = (reply.actors || []).filter(a => a.actor !== 'human:' + S.me?.id);
      present(others.length ? `${others.map(a => a.name).join(', ')} ${others.length === 1 ? 'is' : 'are'} here` : '');
    } catch (error) { lost(error); }
  };
  const heartbeat = async leave => {
    try {
      await post('/v2/presence', {place, session_id: shared.session, gone: leave}, undefined, {keepalive: leave});
      if (!leave && current()) await presence();
    } catch (error) { if (!leave) lost(error); }
  };
  shared.leave = () => { void heartbeat(true); };
  const refresh = () => { if (current()) { getForget(); changed(); void presence(); } };
  shared.visibility = () => { void heartbeat(document.hidden); if (!document.hidden) refresh(); };
  document.addEventListener('visibilitychange', shared.visibility);
  window.addEventListener('pagehide', shared.leave);
  shared.timer = setInterval(() => { if (!document.hidden && current()) void heartbeat(false); }, 15000);
  shared.safety = setInterval(() => { if (!document.hidden) refresh(); }, 180000);
  if (!document.hidden) void heartbeat(false);
  if (typeof EventSource !== 'undefined') {
    const query = new URLSearchParams({topics: topic + ',presence', [filter]: id, place});
    const source = shared.source = new EventSource(API + '/v2/events?' + query);
    source.addEventListener(topic, event => {
      if (!current()) return;
      const data = JSON.parse(event.data);
      if (data.gone) { gone(); return; }
      getForget(); changed(data);
    });
    source.addEventListener('presence', () => { if (current()) void presence(); });
    source.addEventListener('ready', refresh);
    source.addEventListener('reset', refresh);
    source.addEventListener('expired', () => { if (current()) gone(); });
  }
}
