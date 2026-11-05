// Preview only: a stand-in for Tauri's `invoke` with fixture data shaped like the Rust `view()`.
// Open ui/preview/index.html?scene=<name> from a local web server. Never bundled into the app
// (tauri.conf.json embeds ui/app only).
(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const scene = params.get('scene') || 'library';
  const now = Date.now();
  const iso = (offsetMin) => new Date(now + offsetMin * 60000).toISOString();
  const clone = (value) => JSON.parse(JSON.stringify(value));

  // Orgs as the Rust side names them: id, name, and the Tico they live on (the tile colour comes from the host).
  const orgs = [
    { id: 'team', name: 'Tico Team', url: 'https://hub.tico.team' },
    { id: 'tidy', name: 'TIDY', url: 'https://tidy.tico.page' },
  ];
  const orgRef = (id) => { const o = orgs.find((x) => x.id === id); return o && { id: o.id, name: o.name, url: o.url }; };
  const owners = [
    { actor: 'bot:librarian', name: 'Librarian', bot: true },
    { actor: 'bot:head-of-engineering', name: 'Head of Engineering', bot: true },
    { actor: 'bot:marketing-lead', name: 'Marketing Lead', bot: true },
    { actor: 'human:dana', name: 'Dana Reyes', bot: false },
    { actor: 'human:sam', name: 'Sam Okafor', bot: false },
    { actor: 'human:mara', name: 'Mara Novak', bot: false },
    { actor: 'bot:tico-support', name: 'Tico Support', bot: true },
    { actor: 'human:tina', name: 'Tina Alvarez', bot: false },
    { actor: 'human:martin', name: 'Martin Diaz', bot: false },
    { actor: 'human:justin', name: 'Justin Kim', bot: false },
  ];
  const audioOk = {
    attention: false, warning: '', pending: 0, failed: 0, finding_speakers: false,
    inputs: [{ label: 'Microphone', state: 'OK', ok: true }, { label: 'Meeting audio', state: 'OK', ok: true }],
    failures: [], more: 0,
  };
  // Private questions to Tico ride on `chat`; the meeting chat itself is not shown.
  const chatEmpty = { connected: false, can_message: true, private: true, messages: [], replying: [], thinking: false, bots: 0, error: '', retry: '', ready: true };
  const msg = (id, name, text, minutesAgo, extra = {}) => ({ id, name, text, time: iso(-minutesAgo), bot: false, me: false, private: false, ...extra });
  const peopleLocal = {
    rows: [{ kind: 'person', name: 'Me', me: true }],
    stage: 'invite', orgs, org: 'Tico Team', team: false, started: false, can_share: true, blocked: '', error: '', candidates: [],
  };
  const targets = (already = '') => orgs.map((o) => ({ id: o.id, name: o.name, already: o.id === already }));

  // ---- A long meeting with several speakers ----
  const lines = [
    ['Me', true, "Okay, let's start. The goal today is to agree on how the recorder hands tasks to bots."],
    ['Dana Reyes', false, 'Before that, can we look at the onboarding numbers? Activation went up after the setup page shipped.'],
    ['Dana Reyes', false, 'About a third of new companies finish setup in the first session now.'],
    ['Me', true, 'Nice. That matches what the Librarian summarized yesterday.'],
    ['Sam Okafor', false, 'For the recorder, I think every proposed task should wait until someone picks an owner.'],
    ['Mara Novak', false, 'Agreed. And creating should be one task at a time, private, with the quote attached.'],
    ['Me', true, 'Right, nothing goes to Tico until a person creates it. Sam, can you check the task link format?'],
    ['Sam Okafor', false, "Yes, I'll confirm the transcript link opens at the quoted moment."],
    ['Dana Reyes', false, 'One concern: meeting audio on speakers echoes into the mic. Are we dropping duplicates?'],
    ['Me', true, 'We drop mic words that repeat meeting audio within a second.'],
    ['Mara Novak', false, "Good. Then the remaining work is the calendar prompt. I'll draft the copy for the Record button."],
    ['Sam Okafor', false, 'Also the Library should flag recordings whose delivery failed, without losing the local copy.'],
    ['Me', true, "Let's make that a task for the Head of Engineering."],
    ['Dana Reyes', false, 'And someone should update the recovery plan doc with the two-source capture test.'],
    ['Me', true, "I'll give that one to the Librarian; it owns the docs."],
    ['Mara Novak', false, 'Last thing: pricing for hosted. We said 1.25x on provider cost with a cap per feature.'],
    ['Dana Reyes', false, "Let's keep the cap visible in the admin console so we can tune it."],
    ['Me', true, 'Great. I think that covers it. Thanks everyone.'],
  ];
  const longBlocks = [];
  for (let round = 0; round < 3; round++) {
    lines.forEach(([speaker, me, text], i) => {
      longBlocks.push({ speaker, me, start_ms: (round * lines.length + i) * 24000 + 4000, paragraphs: [text] });
    });
  }

  // ---- Tasks: proposals from the meeting, and the ones already created in Tico ----
  const item = (over) => ({
    kind: 'task', document: '', quote: '', at_ms: 0, owner: '', owner_name: '', sent: false, sent_to: '', task_url: '', task_id: '',
    sending: false, can_send: false, error: '', paused_warning: false, ...over,
  });
  const items = [
    item({ id: 'i1', title: 'Confirm the transcript link opens at the quoted moment', quote: "I'll confirm the transcript link opens at the quoted moment.", at_ms: 172000, owner: 'human:sam', owner_name: 'Sam Okafor', can_send: true }),
    item({ id: 'i2', title: 'Flag recordings whose delivery failed in the Library', quote: 'The Library should flag recordings whose delivery failed, without losing the local copy.', at_ms: 268000, owner: 'bot:head-of-engineering', owner_name: 'Head of Engineering', can_send: true }),
    item({ id: 'i3', title: 'Draft the calendar prompt copy for the Record button', quote: "I'll draft the copy for the Record button.", at_ms: 244000 }),
    item({ id: 'i4', kind: 'document', document: 'Recovery plan', title: 'Add the two-source capture test to the recovery plan', quote: 'Someone should update the recovery plan doc with the two-source capture test.', at_ms: 316000, owner: 'bot:librarian', owner_name: 'Librarian', sent: true, sent_to: 'Tico Team', task_url: 'https://hub.tico.team/#/tasks/2417', task_id: '2417' }),
  ];

  function meeting(over = {}) {
    return {
      id: 'm1', title: 'Recorder hand-off review', started_at: iso(-95), duration_ms: 1296000, state: 'stopped',
      recording_here: false, title_editable: true, sent: false, connected: true, send_blocked: null, paused_warning: false,
      targets: targets(), live: null, live_org: '', live_state: '', live_url: '', kept_local: false,
      can_control: false, can_discard: false, discarded: false, discard_pending: false,
      place: 'On this Mac', sync_error: '', setup: [], audio: clone(audioOk),
      actions: { mode: 'ready', can_link: true, link_scope: ['m1','team'], actor: 'human:mara', org: 'Tico Team', orgs, multiple: true, can_change: true, draft_status: '', items: clone(items), waiting: 3, owners },
      notes: { text: 'Decided: one task at a time, private, quote attached.\nFollow up on hosted pricing cap next week.', editable: true },
      people: clone(peopleLocal), chat: clone(chatEmpty),
      ...over,
    };
  }

  const libraryRows = [
    { id: 'm1', title: 'Recorder hand-off review', started_at: iso(-95), duration_ms: 1296000, local: true, org: null, live_org: '', status: null, sent: false, can_send: true },
    { id: 'm2', title: 'Weekly product sync', started_at: iso(-200), duration_ms: 2710000, local: false, org: orgRef('team'), live_org: 'team', status: { label: 'Live', tone: 'live' }, sent: false, can_send: true },
    { id: 'm3', title: 'Hosting pricing with Dana', started_at: iso(-60 * 24 - 30), duration_ms: 1840000, local: false, org: orgRef('team'), live_org: '', status: { label: 'In Tico', tone: 'muted' }, sent: true, can_send: false },
    { id: 'm4', title: 'Customer call · Northwind', started_at: iso(-60 * 26), duration_ms: 3320000, local: true, org: null, live_org: '', status: { label: 'Transcribing 3', tone: 'muted' }, sent: false, can_send: true },
    { id: 'm5', title: 'Turnover checklist review', started_at: iso(-60 * 24 * 3), duration_ms: 1420000, local: false, org: orgRef('tidy'), live_org: '', status: { label: 'In Tico', tone: 'muted' }, sent: true, can_send: false },
    { id: 'm6', title: 'Design pass notes', started_at: iso(-60 * 24 * 4), duration_ms: 960000, local: true, org: null, live_org: '', status: null, sent: false, can_send: true },
  ].map((row) => ({ recording: false, sync_error: '', paused_warning: false, ...row, targets: targets(row.sent ? row.org?.id : '') }));
  const events = [
    { title: 'Standup · Engineering', start: iso(25), end: iso(40) },
    { title: 'Investor update prep', start: iso(150), end: iso(180) },
  ];

  const connections = orgs.map((o, i) => ({ id: o.id, name: o.name, url: o.url, host: o.url.replace('https://', ''), default: i === 0 }));
  const settings = {
    microphone: '', microphones: ['MacBook Pro Microphone', 'Studio Display Microphone', 'AirPods Pro'], meeting_audio: true,
    transcription_url: 'https://openrouter.ai/api/v1', transcription_model: 'elevenlabs/scribe-v2', transcription_language: '',
    calendar: true, calendar_available: true, connections,
    recording: false, key_saved: true, streaming: true, streaming_model: 'grok-voice-transcribe-2.0', xai_saved: true,
  };
  // The Tico desktop app's signed-in orgs (`view.desktop_orgs`), when this Mac has it.
  const desktopOrgs = [
    { name: 'Tico Team', url: 'https://hub.tico.team', email: 'chris@tico.team', active: true, connected: true },
    { name: 'TIDY', url: 'https://tidy.tico.page', email: 'chris@tidy.com', active: false, connected: true },
    { name: 'Acme Lights', url: 'https://acme-lights.tico.page', email: 'chris@acmelights.example', active: false, connected: false },
  ];

  const status = {
    busy: false, demo: false, error: '', message: '', signing_in: false, connect: null, first_run: false,
    suggested: '', recording: null, meeting: null, sending: null,
    // Speech through the signed-in Tico; `none` before sign-in without keys of your own.
    speech: { source: 'tico', stream: true, transcribe: true, tico: 'Tico Team' },
    update: { state: 'none', version: '', notes: '', blocked: '' },
  };
  const state = {
    status, library: { rows: libraryRows, events, calendar_stale: false, connected: true }, desktop_orgs: [],
    settings, meetings: { m1: meeting() }, transcripts: { m1: { id: 'm1', blocks: longBlocks } }, notices: {},
  };

  // ---- Scenes ----
  const liveMeeting = (over = {}) => meeting({
    id: 'm2', title: 'Weekly product sync', started_at: iso(-200), duration_ms: 2710000,
    live: 'Team can join', live_org: 'team', live_state: 'live', live_url: 'https://hub.tico.team/#/meetings/live/m2',
    can_control: true, can_discard: true, place: 'Shared live · Tico Team', targets: targets(),
    people: {
      rows: [
        { kind: 'person', name: 'Me', me: true },
        { kind: 'person', name: 'Dana Reyes' },
        { kind: 'person', name: 'Sam Okafor', actor: 'human:sam', removable: true },
        { kind: 'bot', name: 'Librarian' },
        { kind: 'bot', name: 'Head of Engineering' },
      ],
      stage: 'add', orgs, org: 'Tico Team', team: true, started: true, can_share: true, blocked: '', error: '',
      candidates: owners.filter((o) => !['bot:librarian', 'bot:head-of-engineering', 'human:sam', 'human:dana'].includes(o.actor)),
    },
    // Two private questions to Tico, answered between the turns they followed.
    chat: {
      ...chatEmpty, connected: true,
      messages: [
        msg('p1', 'You', 'What did we decide about creating tasks?', 195, { me: true, private: true }),
        msg('p2', 'Tico', 'One task at a time, private, to the bot or person you pick. Each carries the quote and a link to the transcript.', 194.8, { bot: true, private: true }),
      ],
    },
    ...over,
  });
  // Live text while recording: finished turns, streamed far-end speakers (S1, S2), and a partial tail.
  const liveTurns = [
    { speaker: 'Me', me: true, start_ms: 2000, paragraphs: ['Thanks for joining. Quick one today: the streaming rollout.'], streamed: false },
    { speaker: 'S1', me: false, start_ms: 9000, paragraphs: ['Words now show up about a second after they are spoken, even on long calls.'], streamed: true },
    { speaker: 'S2', me: false, start_ms: 17000, paragraphs: ['And the chunked pass still replaces them, so the saved transcript stays accurate.'], streamed: true },
    { speaker: 'Me', me: true, start_ms: 26000, paragraphs: ['Good. What happens when the connection drops?'], streamed: true },
    { speaker: 'S1', me: false, start_ms: 31000, paragraphs: ['It reconnects on its own and the recorded audio is complete either'], streamed: true, partial: true },
  ];
  const recordingMeeting = () => meeting({
    id: 'm0', title: 'Streaming rollout check-in', started_at: iso(-1), duration_ms: 34000, state: 'recording', recording_here: true,
    title_editable: true, audio: { ...clone(audioOk), pending: 1 },
    actions: {
      mode: 'ready', actor: 'human:mara', org: 'Tico Team', orgs, multiple: true, can_change: true, draft_status: '', waiting: 1, owners,
      items: [
        item({ id: 'r1', title: 'Write up how live text reconnects after a drop', quote: 'What happens when the connection drops?', at_ms: 26000, owner: 'bot:head-of-engineering', owner_name: 'Head of Engineering', can_send: true }),
        item({ id: 'r2', title: 'Check the chunked pass replaces streamed words', quote: 'And the chunked pass still replaces them', at_ms: 17000 }),
      ],
    },
    notes: { text: 'Streaming: ~1s lag. Reconnect story next.', editable: true },
  });
  const recordingStatus = (stream) => ({
    id: 'm0', elapsed_ms: 34000, phase: 'recording', error: '',
    sources: [
      { channel: 'me', label: 'Mic', name: 'MacBook Pro Microphone', level: 0.62, no_signal: false },
      { channel: 'meeting', label: 'Meeting', name: 'System audio', level: 0.41, no_signal: false },
    ],
    stream,
  });
  const streamLive = [
    { channel: 'me', state: 'live', error: '', dropped_ms: 0, reconnects: 0 },
    { channel: 'meeting', state: 'live', error: '', dropped_ms: 0, reconnects: 0 },
  ];
  const signedOut = () => {
    Object.assign(state.status, { first_run: true, suggested: '', speech: { source: 'none', stream: false, transcribe: false, tico: '' } });
    state.library = { rows: [], events: [], calendar_stale: false, connected: false };
    state.settings = { ...settings, connections: [], calendar: false, calendar_available: false, key_saved: false, xai_saved: false, streaming: false };
  };

  const scenes = {
    library: () => {},
    // First run with the desktop app's orgs on this Mac: one button signs in.
    signin: () => { signedOut(); state.desktop_orgs = clone(desktopOrgs).map((org) => ({ ...org, connected: false })); },
    // First run without the desktop app: type a Tico address.
    'signin-address': () => signedOut(),
    empty: () => signedOut(),
    signing: () => { signedOut(); Object.assign(state.status, { signing_in: true, busy: true, suggested: 'hub.tico.team' }); },
    meeting: () => {},
    transcript: () => {},
    recording: () => {
      state.status.recording = recordingStatus(streamLive);
      state.library.rows.unshift({ id: 'm0', title: 'Streaming rollout check-in', started_at: iso(-1), duration_ms: 0, recording: true, local: true, org: null, live_org: '', status: { label: 'Recording', tone: 'red' }, sync_error: '', sent: false, can_send: false, targets: [] });
      state.meetings.m0 = recordingMeeting();
      state.transcripts.m0 = { id: 'm0', live: true, blocks: liveTurns };
    },
    retrying: () => {
      scenes.recording();
      state.status.recording = recordingStatus([
        { channel: 'me', state: 'live', error: '', dropped_ms: 0, reconnects: 0 },
        { channel: 'meeting', state: 'retrying', error: 'Connection reset by peer', dropped_ms: 1800, reconnects: 2 },
      ]);
    },
    // A mic that has picked nothing up for a while: one plain line above the dock, dismissible.
    notice: () => {
      scenes.recording();
      state.status.notice = "Your mic isn't picking anything up. Check the microphone in Settings.";
      state.status.recording.sources[0].level = 0;
      state.status.recording.sources[0].no_signal = true;
    },
    // Shared live with the team: the top bar says who can join.
    live: () => { state.meetings.m2 = liveMeeting(); state.transcripts.m2 = { id: 'm2', blocks: longBlocks.slice(0, 22) }; },
    paused: () => { scenes.live(); Object.assign(state.meetings.m2, { live_state: 'paused', place: 'Paused · Tico Team' }); },
    // Shared live with only the people added.
    'live-private': () => {
      scenes.live();
      const m = state.meetings.m2;
      m.live = 'Private in Tico';
      m.people.team = false;
    },
    // A live meeting whose sent lines can be edited (acknowledged), removed, or only read (not yet sent).
    shared: () => {
      state.meetings.m2 = liveMeeting();
      const lines = [
        ['Me', true, 'Hello there, shall we start with the release?', 1, false, false, true],
        ['Sam Okafor', false, 'Yes. The recorder change is in review.', 2, false, false, true],
        ['Sam Okafor', false, 'It should ship on Thursday if QA passes.', 3, true, false, false],
        ['Me', true, 'Still being sent to Tico.', 4, false, false, false],
      ];
      const blocks = lines.map(([speaker, me, text, seq, edited, removed, editable], i) => ({
        speaker, me, start_ms: 60000 + i * 20000, paragraphs: [removed ? '[removed]' : text], partial: false,
        streamed: false, seq, edited, removed, editable,
      }));
      state.transcripts.m2 = { id: 'm2', live: true, blocks };
    },
    // Discarded while live: Tico is still being told.
    discarding: () => { scenes.live(); Object.assign(state.meetings.m2, { discarded: true, discard_pending: true, kept_local: true, live: null, can_control: false, can_discard: false }); },
    error: () => {
      state.library.rows[1].status = { label: 'Needs retry', tone: 'amber' };
      state.library.rows[1].sync_error = 'Tico returned 502. Audio, transcript, and notes remain saved on this Mac.';
      state.library.rows[3].status = { label: 'Needs retry', tone: 'amber' };
      state.meetings.m1 = meeting({
        sync_error: 'Tico returned 502 Bad Gateway. Audio, transcript, and notes remain saved on this Mac.',
        setup: ["This Tico doesn't support this yet. Update Tico to use it."],
        audio: {
          attention: true, warning: 'Meeting audio stopped delivering frames for 3 s.', pending: 0, failed: 2, finding_speakers: false,
          inputs: [{ label: 'Microphone', state: 'OK', ok: true }, { label: 'Meeting audio', state: '2 silent chunks', ok: false }],
          failures: ['04:12 Meeting audio · HTTP 429 Too Many Requests', '04:55 Meeting audio · HTTP 429 Too Many Requests'], more: 0,
        },
        actions: { ...meeting().actions, items: clone(items).map((it) => (it.id === 'i2' ? { ...it, error: 'Tico returned 503. Try again.' } : it)) },
      });
    },
    // Private questions to Tico, one still being answered.
    ask: () => {
      state.meetings.m1 = meeting({
        chat: {
          ...chatEmpty, thinking: true,
          messages: [
            msg('p1', 'You', 'What did we decide about creating tasks?', 80, { me: true, private: true }),
            msg('p2', 'Tico', 'One task at a time, private, to the bot or person you pick. Each carries the quote and a link to the transcript.\n\nNothing goes to Tico until you create it.', 79.8, { bot: true, private: true }),
            msg('p3', 'You', 'Who owns the recovery plan update?', 0, { me: true, private: true, pending: true }),
          ],
        },
      });
    },
    // A proposal that may quote paused speech: Create asks twice.
    'paused-task': () => {
      scenes.live();
      const it = state.meetings.m2.actions.items.find((i) => i.id === 'i1');
      it.paused_warning = true;
      state.meetings.m2.paused_warning = true;
    },
    settings: () => {},
    // Own providers: the key fields show.
    'settings-own': () => { state.status.speech = { source: 'own', stream: true, transcribe: true, tico: 'Tico Team' }; },
    // This Tico transcribes but has no live text yet.
    'settings-nostream': () => { state.status.speech = { source: 'tico', stream: false, transcribe: true, tico: 'Tico Team' }; },
    // An update waiting for the recording to end (update.js draws the banner).
    update: () => {
      scenes.recording();
      state.status.update = { state: 'ready', version: '0.6.0', notes: 'Live text through your Tico.', blocked: 'After this recording' };
    },
    'update-error': () => { state.status.update = { state: 'error', version: '0.6.0', notes: '', blocked: '' }; },
    // Long names and titles everywhere they show.
    long: () => {
      scenes.live();
      const m = state.meetings.m2;
      m.title = 'Quarterly planning with the platform, infrastructure and developer experience teams (EMEA + Americas)';
      m.actions.items[1].owner_name = 'Head of Engineering, Platform and Developer Experience';
      m.people.rows.push({ kind: 'person', name: 'Maria-Fernanda Gonçalves de Albuquerque', local: true });
      state.library.rows[0].title = 'A very long recording title that keeps going past the edge of the window to check truncation';
    },
    // People being added once live: one on its way, and the reason the last one was refused.
    adding: () => {
      scenes.live();
      const p = state.meetings.m2.people;
      p.rows.push({ kind: 'bot', name: 'Marketing Lead', actor: 'bot:marketing-lead', pending: true });
      p.candidates = p.candidates.filter((c) => c.actor !== 'bot:marketing-lead');
      p.error = 'Not added: Server returned HTTP 403 (forbidden). Check the connection or retry.';
    },
    mentions: () => {
      state.meetings.m1.notes.text = 'Decided: one task at a time, private, quote attached.\n@Sam Okafor confirms the transcript link; @Librarian files the recovery plan.\nFollow up on hosted pricing cap next week.';
    },
    // Save on a live meeting: it ends the meeting in Tico first, then files it.
    sending: () => {
      scenes.live();
      state.status.busy = true;
      state.status.sending = { id: 'm2', label: 'Saving…' };
    },
    connect: () => { state.status.connect = 'https://hub.tico.team'; },
    demo: () => { state.status.demo = true; },
  };
  (scenes[scene] || scenes.library)();
  if (params.get('theme')) document.documentElement.dataset.theme = params.get('theme');

  // ---- view() with the same hash protocol as the Rust side ----
  const hash = (value) => {
    const text = JSON.stringify(value);
    let h = 5381;
    for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0;
    return h.toString(16);
  };
  const part = (data, have) => {
    if (data === null || data === undefined) return null;
    const h = hash(data);
    return h === have ? null : { hash: h, data };
  };
  const toast = (message) => { state.status.message = message; setTimeout(() => (state.status.message = ''), 6000); };
  const open = () => state.meetings[state.status.meeting];
  const commands = {
    view: ({ have }) => {
      const id = state.status.meeting;
      for (const m of Object.values(state.meetings)) m.can_share_screen = !m.sent && !m.discarded && (!m.live_state || m.live_state === 'live');
      return {
        status: clone(state.status),
        desktop_orgs: clone(state.desktop_orgs),
        library: part(state.library, have.library),
        settings: part(state.settings, have.settings),
        meeting: part(id ? state.meetings[id] || meeting({ id, title: state.library.rows.find((r) => r.id === id)?.title || 'Meeting' }) : null, have.meeting),
        transcript: part(id ? state.transcripts[id] || { id, blocks: [] } : null, have.transcript),
      };
    },
    open_meeting: ({ id }) => { state.status.meeting = id; return {}; },
    close_meeting: () => { state.status.meeting = null; },
    record: () => {
      scenes.recording();
      return 'm0';
    },
    stop_recording: () => {
      state.status.recording = null;
      const m = state.meetings.m0;
      if (m) Object.assign(m, { state: 'stopped', recording_here: false });
      const row = state.library.rows.find((r) => r.id === 'm0');
      if (row) Object.assign(row, { recording: false, status: { label: 'Transcribing 1', tone: 'muted' } });
    },
    rename: ({ title }) => { open().title = title; },
    set_notes: ({ notes }) => { open().notes.text = notes; },
    add_item: ({ title }) => {
      const a = open().actions;
      a.items.push(item({ id: `n${Date.now()}`, title }));
      a.waiting++;
    },
    edit_item: ({ id, title }) => { open().actions.items.find((i) => i.id === id).title = title; },
    set_owner: ({ id, owner }) => {
      const it = open().actions.items.find((i) => i.id === id);
      Object.assign(it, { owner, owner_name: owners.find((o) => o.actor === owner)?.name || owner, can_send: true });
    },
    choose_org: () => { open().actions.mode = 'ready'; },
    remove_item: ({ id }) => {
      const a = open().actions;
      a.items = a.items.filter((i) => i.id !== id);
      a.waiting = a.items.filter((i) => !i.sent).length;
    },
    // Create: the proposal becomes a private task in Tico, with its number.
    task_picker: ({ query, kind, offset }) => ({ tasks: offset ? [] : [
      { id: 't42', number: 42, title: 'Review pricing', status: 'open', type: 'general' },
      { id: 't43', number: 43, title: 'Test recovery', status: 'review', type: 'bug' },
      { id: 't44', number: 44, title: 'Property manager bots: pricing page', status: 'doing', type: 'general' },
      { id: 't45', number: 45, title: 'Fix pricing rounding', status: 'open', type: 'bug' },
    ].filter((t) => (!kind || t.type === kind) && (!query || (/^#?\d+$/.test(query.trim()) ? String(t.number) === query.trim().replace('#', '') : query.toLowerCase().split(/\s+/).every((w) => t.title.toLowerCase().includes(w))))), next_offset: null }),
    task_types: () => [{ id: 'general', name: 'General' }, { id: 'bug', name: 'Bug' }],
    link_selected_tasks: ({ ids }) => {
      for (const id of ids) open().actions.items.push(item({ id, task_id: id, number: id.slice(1), title: id === 't42' ? 'Review pricing' : 'Test recovery', status: 'open', kind: 'link', sent: true }));
    },
    unlink_item: ({ id }) => { open().actions.items = open().actions.items.filter((i) => i.id !== id); },
    edit_task_detail: ({ id, due, body }) => Object.assign(open().actions.items.find((i) => i.id === id), { due, body }),
    accept_link_item: ({ id }) => Object.assign(open().actions.items.find((i) => i.id === id), { sent: true, kind: 'link', task_id: 't42', number: 42, title: 'Review pricing' }),
    send_item: ({ id }) => {
      const m = open();
      const a = m.actions;
      const it = a.items.find((i) => i.id === id);
      const n = String(2418 + a.items.filter((i) => i.sent).length);
      Object.assign(it, { sent: true, sent_to: orgs.find((o) => o.id === (m.live_org || 'team')).name, task_id: n, task_url: `https://hub.tico.team/#/tasks/${n}`, can_send: false });
      a.waiting = a.items.filter((i) => !i.sent).length;
      toast(`Task #${n} created`);
    },
    // As the Recorder does: capture stops first, and a live share not saved to Tico leaves it too.
    delete_recording: ({ id }) => {
      if (state.status.recording?.id === id) commands.stop_recording();
      if (state.meetings[id]?.live && !state.meetings[id].sent) commands.keep_local({ id });
      state.library.rows = state.library.rows.filter((r) => r.id !== id);
      if (state.status.meeting === id) state.status.meeting = null;
    },
    // Names to add come from the org it would go to.
    choose_people_org: ({ id }) => {
      const m = open();
      m.people.org = orgs.find((o) => o.id === id)?.name || '';
      m.people.candidates = [];
      setTimeout(() => (m.people.candidates = clone(owners)), 300);
    },
    // Like the Rust side: shares it live in that org, private to the people picked unless the team can join.
    share_live: ({ destination, team, actors }) => {
      const m = open();
      const org = orgs.find((o) => o.id === destination);
      state.status.busy = true;
      setTimeout(() => {
        state.status.busy = false;
        Object.assign(m, { live: team ? 'Team can join' : 'Private in Tico', live_org: destination, live_state: 'live', can_control: true, can_discard: true, place: `Shared live · ${org.name}`, live_url: `${org.url}/#/meetings/live/${m.id}` });
        m.people.team = team;
        m.people.org = org.name;
        for (const actor of actors) {
          const who = owners.find((o) => o.actor === actor);
          m.people.rows.push({ kind: who.bot ? 'bot' : 'person', name: who.name, actor, removable: !who.bot });
        }
        m.people.candidates = owners.filter((o) => !actors.includes(o.actor));
        const row = state.library.rows.find((r) => r.id === m.id);
        if (row) Object.assign(row, { status: { label: 'Live', tone: 'live' }, live_org: destination, org: orgRef(destination), local: false });
      }, 500);
    },
    meeting_link: ({ id, destination }) => {
      const org = orgs.find((o) => o.id === destination) || orgs[0];
      return { url: `${org.url}/#/meetings/live/${id}`, private: !state.meetings[id]?.people.team, copied: true, only_you: false };
    },
    share_screen: ({ id }) => { state.status.screen = { meeting: id, title: state.meetings[id]?.title, active: true, phase: 'Sharing', source: 'Selected window', error: '' }; },
    stop_screen: () => { if (state.status.screen) state.status.screen.active = false; },
    control_live: ({ action }) => {
      const m = open();
      m.live_state = action === 'pause' ? 'paused' : 'live';
    },
    // Discard live share: Tico forgets it; the recording stays on this Mac.
    keep_local: ({ id }) => {
      const m = state.meetings[id];
      if (m) Object.assign(m, { live: null, live_org: '', live_state: '', kept_local: true, can_control: false, can_discard: false });
      const row = state.library.rows.find((r) => r.id === id);
      if (row) Object.assign(row, { status: null, live_org: '', org: null, local: true });
      toast('Kept on this Mac');
    },
    // Like the Rust side: stop, wait for the last words, end a live meeting, then file it where it was chosen.
    save_meeting: ({ id, destination, audience, title }) => {
      const row = state.library.rows.find((r) => r.id === id);
      const m = state.meetings[id];
      if (title) { if (m) m.title = title; if (row) row.title = title; }
      if (destination === 'local') {
        if (m?.live) commands.keep_local({ id });
        if (state.status.recording?.id === id) commands.stop_recording();
        state.status.meeting = null;
        return;
      }
      if (!['team', 'participants'].includes(audience)) throw new Error('Choose who can see it');
      const org = orgs.find((o) => o.id === destination);
      const steps = [];
      if (state.status.recording?.id === id) steps.push('Stopping…', 'Transcribing…');
      if (m?.live) steps.push('Ending…');
      steps.push('Saving…');
      if (state.status.recording?.id === id) commands.stop_recording();
      state.status.busy = true;
      steps.forEach((label, i) => setTimeout(() => (state.status.sending = { id, label }), i * 600));
      setTimeout(() => {
        state.status.sending = null;
        state.status.busy = false;
        if (m) Object.assign(m, { sent: true, live: null, live_state: 'ended', can_control: false, can_discard: false });
        if (row) Object.assign(row, { status: { label: 'In Tico', tone: 'muted' }, sent: true, local: false, org: orgRef(destination), live_org: '', audience });
        if (m) m.audience = audience;
        toast(`Saved to ${org.name}`);
      }, steps.length * 600);
    },
    ask: ({ question }) => {
      const chat = open().chat;
      chat.messages.push(msg(`p${Date.now()}`, 'You', question, 0, { me: true, private: true, pending: true }));
      chat.thinking = true;
      setTimeout(() => {
        chat.thinking = false;
        chat.messages = chat.messages.map((m) => ({ ...m, pending: false }));
        chat.messages.push(msg(`p${Date.now()}`, 'Tico', 'Here is what the transcript says about that.', 0, { bot: true, private: true }));
      }, 600);
    },
    edit_shared_line: ({ id, seq, text }) => {
      const block = state.transcripts[id].blocks.find((b) => b.seq === seq);
      Object.assign(block, { paragraphs: [text], edited: true });
    },
    remove_shared_line: ({ id, seq }) => {
      const block = state.transcripts[id].blocks.find((b) => b.seq === seq);
      Object.assign(block, { paragraphs: ['[removed]'], edited: true, removed: true });
    },
    save_settings: ({ form }) => {
      const { key, xai_key, speech_source, ...rest } = form;
      Object.assign(state.settings, rest);
      if (speech_source) state.status.speech = { ...state.status.speech, source: speech_source };
      if (key) state.settings.key_saved = true;
      if (xai_key) state.settings.xai_saved = true;
      toast('Settings saved');
    },
    make_default: ({ id }) => { state.settings.connections.forEach((c) => (c.default = c.id === id)); },
    set_audio: ({ microphone, meeting: on }) => Object.assign(state.settings, { microphone, meeting_audio: on }),
    dismiss_error: () => { state.status.error = ''; },
    dismiss_notice: () => { state.status.notice = ''; },
    dismiss_connect: () => { state.status.connect = null; },
    confirm_connect: () => { state.status.connect = null; state.status.signing_in = true; },
    sign_in: () => { state.status.signing_in = true; },
    // The desktop app's orgs become this Recorder's orgs.
    // Allow on one org's row signs in to that org; with no url, every org not signed in yet.
    sign_in_desktop: ({ url } = {}) => {
      if (!state.desktop_orgs.length) throw new Error('No Tico desktop app on this Mac');
      state.desktop_orgs.forEach((org) => { if (!url || org.url === url) org.connected = true; });
      state.settings = clone(settings);
      state.library = { rows: [], events, calendar_stale: false, connected: true };
      Object.assign(state.status, { first_run: false, speech: { source: 'tico', stream: true, transcribe: true, tico: 'Tico Team' } });
    },
    // Once live, a pick shows at once as being added; Tico says yes a moment later.
    pick: ({ actor }) => {
      const p = open().people;
      const who = owners.find((o) => o.actor === actor);
      const row = { kind: who.bot ? 'bot' : 'person', name: who.name, actor, pending: true };
      p.error = '';
      p.rows.push(row);
      p.candidates = p.candidates.filter((c) => c.actor !== actor);
      setTimeout(() => {
        delete row.pending;
        if (!who.bot) row.removable = true;
      }, 900);
    },
    remove_access: ({ actor }) => { const p = open().people; p.rows = p.rows.filter((r) => r.actor !== actor); },
    add_participant: ({ name }) => { open().people.rows.push({ kind: 'person', name, local: true }); },
    set_team: ({ team }) => { open().people.team = team; open().live = team ? 'Team can join' : 'Private in Tico'; },
    open_url: () => toast('Opened in your browser'),
  };
  // The app pushes `changed` and `level`; the preview fakes both on timers.
  const listeners = {};
  setInterval(() => (listeners.changed || []).forEach((cb) => cb({ payload: 0 })), 300);
  setInterval(() => {
    const rec = state.status.recording;
    if (!rec) return;
    rec.elapsed_ms += 100;
    const payload = { id: rec.id, elapsed_ms: rec.elapsed_ms, sources: rec.sources.map((src) => ({ channel: src.channel, level: src.level })) };
    (listeners.level || []).forEach((cb) => cb({ payload }));
  }, 100);
  window.__TAURI__ = {
    event: {
      listen: async (name, cb) => { (listeners[name] = listeners[name] || []).push(cb); return () => {}; },
    },
    core: {
      invoke: async (cmd, args = {}) => {
        const run = commands[cmd];
        const result = run ? run(args) : undefined;
        return result === undefined ? {} : clone(result);
      },
    },
  };
  window.__PREVIEW__ = { scene, state };
})();
