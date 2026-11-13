/* Shared drafts and authorized place subscriptions for meeting items and task pages. */
'use strict';

type SharedValues = Record<string, unknown>;
// An item as the server returns it: its version, and its fields (a `detail` group holds the editable details).
interface SharedItem { version: number | null }
interface SharedDraft {
  patch: SharedValues; dirty: SharedValues; flight: Promise<unknown> | null; error: string; timer: number;
  version: number | null; current: SharedItem | null; base?: SharedItem | undefined;
  sending?: boolean; closed?: unknown; closedAcknowledged?: boolean; inflightPatch?: SharedValues | null;
}
interface SharedDraftOptions {
  write: (patch: SharedValues, version: number | null) => Promise<SharedItem>;
  current?: (error: unknown) => unknown;
  settled?: () => unknown;
}
interface SharedPlaceHandle {
  id: string; place: string; session: string;
  source?: EventSource; timer?: number; safety?: number;
  visibility?: () => void; leave?: () => void;
}
interface SharedPlaceState { shared?: SharedPlaceHandle | null | undefined }
interface SharedPlaceOptions {
  id: string; place: string; topic: string; filter: string;
  changed: (data?: unknown) => void; gone: () => void; present: (words: string) => void;
}
interface SharedPresence { actors?: {actor: string; name: string}[] }

function sharedRecord(value: unknown): value is SharedValues { return typeof value === 'object' && value !== null; }
function sharedItem(value: unknown): value is SharedItem { return typeof value === 'object' && value !== null; }

function sharedDraft() {
  return {patch: {}, dirty: {}, flight: null, error: '', timer: 0, version: null, current: null};
}
const sharedDraftValue = (item: unknown, key: string, group = ''): unknown => {
  const holder = group ? (sharedRecord(item) ? item[group] : undefined) : item;
  return (sharedRecord(holder) ? holder[key] : undefined) ?? '';
};
function sharedDraftPatch(draft: SharedDraft, key: string, value: unknown, group = ''): void {
  if (!(key in draft.dirty)) draft.dirty[key] = sharedDraftValue(draft.base, key, group);
  if (group) {
    const nested = draft.patch[group];
    (sharedRecord(nested) ? nested : (draft.patch[group] = {}))[key] = value;
  }
  else draft.patch[key] = value;
}
function sharedDraftMerge<A, B>(a: A, b: B): A & B {
  const detailA = sharedRecord(a) ? a.detail : undefined, detailB = sharedRecord(b) ? b.detail : undefined;
  return {...a, ...b, ...((detailA || detailB) ? {detail: {...(sharedRecord(detailA) ? detailA : {}), ...(sharedRecord(detailB) ? detailB : {})}} : {})};
}
function sharedDraftAccepted(draft: SharedDraft, patch: SharedValues, item: SharedItem): void {
  const groups: [string, string[]][] = [['', Object.keys(patch).filter(k => k !== 'detail')], ['detail', Object.keys(sharedRecord(patch.detail) ? patch.detail : {})]];
  for (const [group, fields] of groups) {
    const pending = group ? draft.patch[group] : draft.patch;
    for (const key of fields) {
      if (sharedRecord(pending) && key in pending) draft.dirty[key] = sharedDraftValue(item, key, group);
      else delete draft.dirty[key];
    }
  }
  // An invalidation can arrive before the write's answer. Never roll its base backwards.
  draft.base = Number(draft.base?.version) > Number(item.version) ? draft.base : item;
  draft.version = item.version; draft.current = null;
}
async function sharedDraftSave(draft: SharedDraft, options: SharedDraftOptions): Promise<unknown> {
  clearTimeout(draft.timer);
  if (draft.closed || draft.closedAcknowledged || draft.flight || draft.sending || !Object.keys(draft.patch).length) return draft.flight?.catch(() => {});
  const patch = draft.patch;
  draft.patch = {}; draft.error = ''; draft.inflightPatch = patch;
  const flight = Promise.resolve().then(() => options.write(patch, draft.version));
  draft.flight = flight;
  try { sharedDraftAccepted(draft, patch, await flight); }
  catch (error) {
    draft.patch = sharedDraftMerge(patch, draft.patch); draft.error = errorMessage(error, '');
    const current = await options.current?.(error);
    if (sharedItem(current)) { draft.current = current; draft.version = current.version; draft.base = current; }
  }
  finally { draft.flight = null; draft.inflightPatch = null; }
  if (!draft.error && Object.keys(draft.patch).length && !draft.sending) return sharedDraftSave(draft, options);
  await options.settled?.();
}
const sharedEdited = (actor: string | undefined, name: (actor: string) => string = actorLabel): string => actor ? `${name(actor)} edited · just now` : '';

function sharedPlaceStop(state: SharedPlaceState): void {
  const shared = state.shared;
  if (!shared) return;
  state.shared = null;
  shared.source?.close(); clearInterval(shared.timer); clearInterval(shared.safety);
  if (shared.visibility) document.removeEventListener('visibilitychange', shared.visibility);
  if (shared.leave) window.removeEventListener('pagehide', shared.leave);
  shared.leave?.();
}
function sharedPlaceStart(state: SharedPlaceState, options: SharedPlaceOptions): void {
  const {id, place, topic, filter, changed, gone, present} = options;
  if (state.shared?.place === place) return;
  sharedPlaceStop(state);
  // A bot's read-only view (backend/app_view.py) says nowhere that it is here and follows no live changes: the page
  // stays as it was read, for a screenshot. Its refused heartbeat must not read as losing the place.
  if (S.me?.read_only_view) return;
  const shared: SharedPlaceHandle = state.shared = {id, place, session: newRequestId()};
  const current = () => state.shared === shared;
  const lost = (error: unknown) => {
    const status = typeof error === 'object' && error !== null && 'status' in error ? error.status : undefined;
    if (current() && typeof status === 'number' && [403, 404].includes(status)) gone();
  };
  const presence = async () => {
    try {
      const reply = await get<SharedPresence>('/v2/presence?place=' + encodeURIComponent(place), {fresh: true});
      if (!current()) return;
      const others = (reply.actors || []).filter(a => a.actor !== 'human:' + S.me?.id);
      present(others.length ? `${others.map(a => a.name).join(', ')} ${others.length === 1 ? 'is' : 'are'} here` : '');
    } catch (error) { lost(error); }
  };
  const heartbeat = async (leave: boolean) => {
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
    source.addEventListener(topic, (event: MessageEvent) => {
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
