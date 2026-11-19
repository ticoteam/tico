/* ui/app/thread-call-start.ts — Start a call and carry its timing into the meeting window.
   Loaded with thread-call-live on Start or Join. The shell opens any blank tab in the click itself. */
'use strict';

type CallStartMark = 'click' | 'created' | 'window-opened' | 'boot-done' | 'token' | 'connected' | 'mic-published' | 'first-remote';
interface CallStartTiming {
  marks: Partial<Record<CallStartMark, number>>;
  id?: string;
  timer?: number;
  reported?: boolean;
  membersUnavailable?: true;
}
const CALL_START_KEY = 'tico.call-start';
const CALL_START_MAX_AGE = 600_000;
const CALL_START_MARKS: CallStartMark[] = ['click', 'created', 'window-opened', 'boot-done', 'token', 'connected', 'mic-published', 'first-remote'];
type CallStartHandoff = [id: string, click: number, created: number, opened: number, membersUnavailable?: true];
const CALL_MEMBERS_WARNING = 'Could not check for a bot. Tap its face to add it.';

function callStartMark(timing: CallStartTiming | undefined, mark: CallStartMark): void {
  if (!timing || timing.marks[mark] !== undefined) return;
  const at = performance.now();
  timing.marks[mark] = performance.timeOrigin + at;
  performance.mark('tico:call-start:' + mark);
  if (timing.marks['mic-published'] !== undefined && timing.marks['first-remote'] !== undefined) callStartReport(timing);
}
// The browser's new tab and the desktop webview have their own clocks. Use their common time origin conversion.
// Reading a handoff consumes it so a reload cannot reuse its timing or replay the warning.
function callStartHandoffs(): CallStartHandoff[] {
  const rows = JSON.parse(localStorage.getItem(CALL_START_KEY) || '[]') as CallStartHandoff[];
  const now = performance.timeOrigin + performance.now();
  return rows.filter(([, click, created, opened]) =>
    [click, created, opened].every(Number.isFinite)
    && click >= now - CALL_START_MAX_AGE && created >= click && opened >= created && opened <= now);
}
function callStartSend(id: string, timing: CallStartTiming): void {
  timing.id = id;
  const {click, created, 'window-opened': opened} = timing.marks;
  if (click === undefined || created === undefined || opened === undefined) return;
  try {
    const rows = callStartHandoffs().filter(([key]) => key !== id);
    const row: CallStartHandoff = [id, click, created, opened];
    if (timing.membersUnavailable) row.push(true);
    localStorage.setItem(CALL_START_KEY, JSON.stringify([...rows.slice(-7), row]));
  } catch { /* storage is optional for timing */ }
}
function callStartTake(id: string): CallStartTiming | undefined {
  try {
    const rows = callStartHandoffs();
    const row = rows.find(([key]) => key === id);
    if (!row) return undefined;
    localStorage.setItem(CALL_START_KEY, JSON.stringify(rows.filter(([key]) => key !== id)));
    const [, click, created, opened, membersUnavailable] = row;
    return {id, marks: {click, created, 'window-opened': opened}, ...(membersUnavailable === true ? {membersUnavailable} : {})};
  } catch { return undefined; }
}
function callStartReport(timing: CallStartTiming | undefined): void {
  if (!timing || timing.reported) return;
  timing.reported = true;
  clearTimeout(timing.timer);
  const click = timing.marks.click;
  if (click === undefined) return;
  const marks = Object.fromEntries(CALL_START_MARKS.map(mark => {
    const at = timing.marks[mark];
    return [mark, at === undefined ? null : Math.max(0, Math.round(at - click))];
  }));
  window.ticoCallStartTiming = marks;
  console.info('[call-start] ' + JSON.stringify(marks));
  if (timing.id && nativeHas('callStartTiming')) {
    try { Promise.resolve(nativeHandler('callStartTiming')?.postMessage({call: timing.id, marks})).catch(() => {}); } catch { /* shell gone */ }
  }
}

function callStartOpen(call: CallRecord, attempt: CallStartAttempt, timing?: CallStartTiming): boolean {
  if (timing) {
    callStartMark(timing, 'window-opened');
    callStartSend(call.id, timing);
  }
  if (callWindowOpen(call, attempt.tab)) { attempt.tab = null; return true; }
  if (timing) callStartTake(call.id);
  return false;
}

async function callStartThread(view: ThvView, id: string, kind: 'start' | 'meet', bot: string, memberThread: boolean, attempt: CallStartAttempt): Promise<void> {
  const {gen, timing} = attempt;
  if (gen !== CALL_GEN || view.id !== id) return;
  try {
    if (memberThread && !view.memberList) {
      const path = thvPath(id) + '/members?limit=3';
      type Members = {members?: {actor: string}[]; next_cursor?: string | null};
      const members = await get<Members>(path)
        .catch(() => gen === CALL_GEN ? get<Members>(path, {fresh: true}) : null)
        .catch(() => null);
      if (gen !== CALL_GEN) return;
      bot = callMemberBot(members?.members, !!members?.next_cursor);
      if (!members) timing.membersUnavailable = true;
    }
    const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(id)}/calls`,
      bot ? {provider: 'tico', bots: [bot]} : {provider: kind === 'meet' ? 'google_meet' : 'auto'});
    const call = made?.call || made;
    if (!call?.id) return;
    callStartMark(timing, 'created');
    if (gen === CALL_GEN && timing.membersUnavailable) {
      toast(call.provider === 'google_meet' ? 'Could not check for a bot before starting the call.' : CALL_MEMBERS_WARNING, true);
    }
    if (call.provider === 'google_meet') {
      CALL_THREADS.set(id, {...CALL_THREADS.get(id), call});
      const tab = attempt.tab; attempt.tab = null;
      callMeetLanded(call, tab, call.join_url || '');
      return;
    }
    if (gen !== CALL_GEN) {
      // A late Start owes its seat back unless a Join of the same call is in it or on its way.
      if (CALL_JOINING.get(call.id)) {
        if (!CALL_SETTLED.has(call.id)) CALL_OWED.add(call.id);
      } else if (CALL?.id !== call.id) callSeatLeave(call.id);
      return;
    }
    if (callStartOpen(call, attempt, timing)) {
      CALL_THREADS.set(id, {...CALL_THREADS.get(id), call}); callViewKeep(call);
      return;
    }
    await callJoin(id, call, threadTitle(view.thread), gen, timing);
  } catch (e) {
    if (gen !== CALL_GEN) return;
    if (callCode(e) === 'call_in_progress') { void callLoad(view); toast('A call already started here'); return; }
    callNoted(id, e);
  }
}

// Call a bot from anywhere (the org chart): the viewer's DM with it (made when there is none), a Tico Meet call there
// with the bot in it, joined here. A live call in that DM is joined instead.
async function callStartBot(slug: string, {gen, timing}: CallStartAttempt): Promise<void> {
  if (gen !== CALL_GEN) return;
  try {
    const r = await get<ThvThread & {thread?: ThvThread}>('/v2/threads/dm?with=' + encodeURIComponent('bot:' + slug), {fresh: true});
    const t = (r?.thread !== undefined ? r.thread : r) || null;
    if (!t?.id || gen !== CALL_GEN) return;
    let call: CallRecord | null | undefined, started = false;
    try {
      const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(t.id)}/calls`, {provider: 'tico', bots: [slug]});
      call = made?.call || made; started = true;
      if (call?.id) callStartMark(timing, 'created');
    } catch (e) {
      if (callCode(e) !== 'call_in_progress') throw e;
      const list = await get<CallListReply>(`/v2/threads/${encodeURIComponent(t.id)}/calls?limit=1`, {fresh: true});
      call = (list.calls || []).find(c => c.state === 'live');
    }
    if (!call?.id) return;
    if (call.provider === 'google_meet') { await callMeetJoin(call); return; }
    // Hidden or overtaken while the call was being made: the seat the start took goes back.
    if (gen !== CALL_GEN) { if (started && CALL?.id !== call.id) callSeatLeave(call.id); return; }
    await callJoin(t.id, call, threadTitle(t), gen, started ? timing : undefined);
  } catch (e) {
    if (gen === CALL_GEN) toast(callErrorMessage(e, 'Could not call'), true);
  }
}
// Call teammates from anywhere (the Meetings page's Call now): one teammate is the viewer's DM with them (made when there
// is none), more are a new thread with them all, named for the call's title, else for who is in it (a group needs a
// name). A Tico Meet call starts there with the bots among them, and its meeting window opens and joins it, as the
// header's Start does: the blank tab opens now, in the click, before anything is awaited. A live call already in that
// DM is joined instead. Resolves to whether a call opened; a refusal is thrown for the caller to show.
async function callStartTeammates(actors: string[], title: string, attempt: CallStartAttempt): Promise<boolean> {
  const {gen, timing} = attempt;
  if (gen !== CALL_GEN) return false;
  const bots = actors.filter(a => a.startsWith('bot:')).map(a => a.slice(4));
  const t = await callThreadWith(actors, title);
  if (!t?.id || gen !== CALL_GEN) return false;
  let call: CallRecord | null | undefined, started = false;
  try {
    const made: CallRecord | null = await post(`/v2/threads/${encodeURIComponent(t.id)}/calls`,
      {provider: 'tico', ...(bots.length ? {bots} : {}), ...(title ? {title} : {})});
    call = made?.call || made; started = true;
    if (call?.id) callStartMark(timing, 'created');
  } catch (e) {
    if (callCode(e) !== 'call_in_progress') throw e;
    const list = await get<CallListReply>(`/v2/threads/${encodeURIComponent(t.id)}/calls?limit=1`, {fresh: true});
    call = (list.calls || []).find(c => c.state === 'live');
  }
  if (!call?.id) return false;
  if (call.provider === 'google_meet') {
    const tab = attempt.tab; attempt.tab = null;
    await callMeetJoin(call, tab || undefined);
    return true;
  }
  // Hidden or overtaken while the call was being made: the seat the start took goes back.
  if (gen !== CALL_GEN) { if (started && CALL?.id !== call.id) callSeatLeave(call.id); return false; }
  const startTiming = started ? timing : undefined;
  if (callStartOpen(call, attempt, startTiming)) { callViewKeep(call); return true; }
  await callJoin(t.id, call, threadTitle(t), gen, startTiming);
  return true;
}
