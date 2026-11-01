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

  const orgs = [
    { id: 'acme', name: 'Acme' },
    { id: 'team', name: 'Tico Team' },
  ];
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
  const chatEmpty = { connected: false, can_message: true, private: false, messages: [], replying: [], thinking: false, bots: 0, error: '', retry: '', ready: true };
  const msg = (id, name, text, minutesAgo, extra = {}) => ({ id, name, text, time: iso(-minutesAgo), bot: false, me: false, private: false, ...extra });
  const peoplePrivate = {
    rows: [{ kind: 'person', name: 'Me', me: true }],
    stage: 'invite', orgs, org: 'Acme', team: false, started: false, can_share: true, blocked: '', candidates: [],
  };

  // ---- A long meeting with several speakers ----
  const lines = [
    ['Me', true, "Okay, let's start. The goal today is to agree on how the recorder hands action items to bots."],
    ['Dana Reyes', false, 'Before that, can we look at the onboarding numbers? Activation went up after the setup page shipped.'],
    ['Dana Reyes', false, 'About a third of new companies finish setup in the first session now.'],
    ['Me', true, 'Nice. That matches what the Librarian summarized yesterday.'],
    ['Sam Okafor', false, 'For the recorder, I think every drafted item should stay unselected until someone picks an owner.'],
    ['Mara Novak', false, 'Agreed. And sending should be one item at a time, as a private task with the quote attached.'],
    ['Me', true, 'Right, nothing goes to Tico until a person sends it. Sam, can you check the task link format?'],
    ['Sam Okafor', false, "Yes, I'll confirm the transcript link opens at the quoted moment."],
    ['Dana Reyes', false, 'One concern: meeting audio on speakers echoes into the mic. Are we dropping duplicates?'],
    ['Me', true, 'We drop mic words that repeat meeting audio within a second.'],
    ['Mara Novak', false, "Good. Then the remaining work is the calendar prompt. I'll draft the copy for the Record button."],
    ['Sam Okafor', false, 'Also the Library should flag recordings whose delivery failed, without losing the local copy.'],
    ['Me', true, "Let's make that an action item for the Head of Engineering."],
    ['Dana Reyes', false, 'And someone should update the recovery plan doc with the two-source capture test.'],
    ['Me', true, "I'll send that one to the Librarian; it owns the docs."],
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

  // ---- Action items ----
  const items = [
    {
      id: 'i1', title: 'Confirm the transcript link opens at the quoted moment', quote: "I'll confirm the transcript link opens at the quoted moment.",
      at_ms: 172000, owner: 'human:sam', owner_name: 'Sam Okafor', sent: false, sent_to: '', task_url: '', sending: false, can_send: true, error: '',
    },
    {
      id: 'i2', title: 'Flag recordings whose delivery failed in the Library', quote: 'The Library should flag recordings whose delivery failed, without losing the local copy.',
      at_ms: 268000, owner: 'bot:head-of-engineering', owner_name: 'Head of Engineering', sent: false, sent_to: '', task_url: '', sending: false, can_send: true, error: '',
    },
    {
      id: 'i3', title: 'Draft the calendar prompt copy for the Record button', quote: "I'll draft the copy for the Record button.",
      at_ms: 244000, owner: '', owner_name: '', sent: false, sent_to: '', task_url: '', sending: false, can_send: false, error: '',
    },
    {
      id: 'i4', title: 'Update the recording recovery plan with the two-source capture test', quote: 'Someone should update the recovery plan doc with the two-source capture test.',
      at_ms: 316000, owner: 'bot:librarian', owner_name: 'Librarian', sent: true, sent_to: 'Librarian', task_url: 'https://tico.example.com/tasks/8a1f', sending: false, can_send: false, error: '',
    },
  ];

  function meeting(over = {}) {
    return {
      id: 'm1', title: 'Recorder hand-off review', started_at: iso(-95), duration_ms: 1296000, state: 'stopped',
      recording_here: false, title_editable: true, sent: false, connected: true, send_blocked: null,
      targets: [{ id: 'acme', name: 'Acme', already: false }, { id: 'team', name: 'Tico Team', already: false }],
      live: null, can_finish: false, place: 'Private · on this Mac', sync_error: '', setup: [], audio: clone(audioOk),
      actions: { mode: 'ready', org: 'Acme', orgs, multiple: true, can_change: true, draft_status: '', items: clone(items), waiting: 3, owners },
      notes: { text: 'Decided: one item at a time, private tasks, quote attached.\nFollow up on hosted pricing cap next week.', editable: true },
      people: clone(peoplePrivate), chat: clone(chatEmpty),
      ...over,
    };
  }

  const libraryRows = [
    { id: 'm1', title: 'Recorder hand-off review', started_at: iso(-95), duration_ms: 1296000, recording: false, status: null, sync_error: '', sent: false, can_send: true },
    { id: 'm2', title: 'Weekly product sync', started_at: iso(-200), duration_ms: 2710000, recording: false, status: { label: 'Live in Tico', tone: 'accent' }, sync_error: '', sent: false, can_send: true },
    { id: 'm3', title: 'Hosting pricing with Dana', started_at: iso(-60 * 24 - 30), duration_ms: 1840000, recording: false, status: { label: 'In Tico', tone: 'accent' }, sync_error: '', sent: true, can_send: false },
    { id: 'm4', title: 'Customer call · Northwind', started_at: iso(-60 * 26), duration_ms: 3320000, recording: false, status: { label: 'Transcribing 3', tone: 'muted' }, sync_error: '', sent: false, can_send: true },
    { id: 'm5', title: 'Mic check', started_at: iso(-60 * 24 * 3), duration_ms: 42000, recording: false, status: { label: 'No signal', tone: 'amber' }, sync_error: '', sent: false, can_send: true },
    { id: 'm6', title: 'Design pass notes', started_at: iso(-60 * 24 * 4), duration_ms: 960000, recording: false, status: { label: 'In Tico', tone: 'accent' }, sync_error: '', sent: true, can_send: false },
  ].map((row) => ({ ...row, targets: [{ id: 'acme', name: 'Acme', already: row.sent }, { id: 'team', name: 'Tico Team', already: false }] }));
  const events = [
    { title: 'Standup · Engineering', start: iso(25), end: iso(40) },
    { title: 'Investor update prep', start: iso(150), end: iso(180) },
  ];

  const settings = {
    microphone: '', microphones: ['MacBook Pro Microphone', 'Studio Display Microphone', 'AirPods Pro'], meeting_audio: true,
    transcription_url: 'https://openrouter.ai/api/v1', transcription_model: 'elevenlabs/scribe-v2', transcription_language: '',
    calendar: true, calendar_available: true,
    connections: [{ id: 'acme', name: 'Acme', host: 'tico.example.com', default: true }, { id: 'team', name: 'Tico Team', host: 'hub.tico.team', default: false }],
    recording: false, key_saved: true, streaming: true, streaming_model: 'grok-voice-transcribe-2.0', xai_saved: true,
  };

  const status = {
    busy: false, demo: false, error: '', message: '', signing_in: false, connect: null, first_run: false,
    suggested: '', recording: null, meeting: null, sending: null,
    // Speech through the signed-in Tico; `none` before sign-in without keys of your own.
    speech: { source: 'tico', stream: true, transcribe: true, tico: 'Acme' },
    update: { state: 'none', version: '', notes: '', blocked: '' },
  };
  const state = {
    status, library: { rows: libraryRows, events, calendar_stale: false, connected: true },
    settings, meetings: { m1: meeting() }, transcripts: { m1: { id: 'm1', blocks: longBlocks } }, notices: {},
  };

  // ---- Scenes ----
  const live = meeting({
    id: 'm2', title: 'Weekly product sync', started_at: iso(-200), duration_ms: 2710000,
    live: 'Team can join', can_finish: true, place: 'Shared live · Acme',
    targets: [{ id: 'acme', name: 'Acme', already: false }],
    people: {
      rows: [
        { kind: 'person', name: 'Me', me: true },
        { kind: 'person', name: 'Dana Reyes' },
        { kind: 'person', name: 'Sam Okafor', actor: 'human:sam', removable: true },
        { kind: 'bot', name: 'Librarian' },
        { kind: 'bot', name: 'Head of Engineering' },
        { kind: 'person', name: 'Dana (guest)', local: true },
      ],
      stage: 'add', orgs, org: 'Acme', team: true, started: true, can_share: true, blocked: '',
      candidates: owners.filter((o) => !['bot:librarian', 'bot:head-of-engineering', 'human:sam', 'human:dana'].includes(o.actor)),
    },
    // Two people and two bots; the Head of Engineering has taken a turn and is writing.
    chat: {
      ...chatEmpty, connected: true, bots: 2,
      messages: [
        msg('c1', 'Dana Reyes', 'Can someone pull the activation numbers from last week before we decide?', 9),
        msg('c2', 'Librarian', 'Activation was 34% of new companies finishing setup in the first session, up from 21%. Source: the onboarding dashboard, Oct 22–28.', 8.5, { bot: true }),
        msg('c3', 'You', 'Thanks. @Head of Engineering, what would it take to flag failed deliveries in the Library?', 6, { me: true }),
        msg('c4', 'Sam Okafor', 'I can take the transcript link check.', 5),
        msg('c5', 'Head of Engineering', 'A status column on the summary plus a retry button on the row. About a day, mostly tests.', 4.5, { bot: true }),
        msg('c6', 'Head of Engineering', 'I can open the task now if you want it this week.', 4.4, { bot: true }),
        msg('c7', 'Dana Reyes', 'Yes, please do. And keep the local copy no matter what.', 2),
      ],
      replying: ['Head of Engineering'],
    },
  });
  // Bot-turn notices (`live.notices`), newest last: waits, a turn put off, an empty reply asked again, a failure.
  const at = (minutesAgo) => now - minutesAgo * 60000;
  const liveNotices = [
    { id: 'n1', kind: 'waiting', bot: 'Librarian', text: 'Librarian is waiting for its computer to update before it can reply.', at: at(8.8) },
    { id: 'n2', kind: 'deferred', bot: 'Head of Engineering', text: 'Head of Engineering will reply after the answer it is giving.', at: at(4.45) },
    { id: 'n3', kind: 'retried', bot: 'Head of Engineering', text: 'Empty reply, asked again.', at: at(4.42) },
    { id: 'n4', kind: 'failed', bot: 'Marketing Lead', text: "Marketing Lead couldn't make a private copy of its repository, so it can't reply in this meeting.", at: at(1) },
  ];
  // Live text while recording: finished turns, streamed far-end speakers (S1, S2), and a partial tail.
  const liveTurns = [
    { speaker: 'Me', me: true, start_ms: 2000, paragraphs: ['Thanks for joining. Quick one today: the streaming rollout.'], streamed: false },
    { speaker: 'S1', me: false, start_ms: 9000, paragraphs: ['Words now show up about a second after they are spoken, even on long calls.'], streamed: true },
    { speaker: 'S2', me: false, start_ms: 17000, paragraphs: ['And the chunked pass still replaces them, so the saved transcript stays accurate.'], streamed: true },
    { speaker: 'Me', me: true, start_ms: 26000, paragraphs: ['Good. What happens when the connection drops?'], streamed: true },
    { speaker: 'S1', me: false, start_ms: 31000, paragraphs: ['It reconnects on its own and the recorded audio is complete either'], streamed: true, partial: true },
  ];
  const recordingMeeting = meeting({
    id: 'm0', title: 'Streaming rollout check-in', started_at: iso(-1), duration_ms: 34000, state: 'recording', recording_here: true,
    title_editable: true, targets: [], audio: { ...clone(audioOk), pending: 1 },
    actions: {
      mode: 'ready', org: 'Acme', orgs, multiple: true, can_change: true, draft_status: '', waiting: 1, owners,
      items: [{
        id: 'r1', title: 'Write up how live text reconnects after a drop', quote: 'What happens when the connection drops?', at_ms: 26000,
        owner: 'bot:head-of-engineering', owner_name: 'Head of Engineering', sent: false, sent_to: '', task_url: '', sending: false, can_send: true, error: '',
      }],
    },
    notes: { text: '', editable: true },
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

  const scenes = {
    library: () => { state.status.demo = false; },
    empty: () => {
      Object.assign(state.status, { first_run: true, suggested: '', speech: { source: 'none', stream: false, transcribe: false, tico: '' } });
      state.library = { rows: [], events: [], calendar_stale: false, connected: false };
      state.settings = { ...settings, connections: [], calendar: false, calendar_available: false, key_saved: false, xai_saved: false, streaming: false };
    },
    signing: () => {
      Object.assign(state.status, { first_run: true, signing_in: true, busy: true, speech: { source: 'none', stream: false, transcribe: false, tico: '' } });
      state.status.suggested = 'tico.example.com';
      state.library = { rows: [], events: [], calendar_stale: false, connected: false };
    },
    meeting: () => {},
    transcript: () => {},
    live: () => { state.meetings.m2 = live; state.transcripts.m2 = { id: 'm2', blocks: longBlocks.slice(0, 22) }; },
    // A live meeting whose sent lines can be edited (acknowledged), removed, or only read (not yet sent).
    shared: () => {
      state.meetings.m2 = live;
      // A second shared meeting with the same line numbers, to check drafts never cross meetings.
      const second = state.library.rows.find((row) => row.id === 'm2');
      state.library.rows.push({ ...second, id: 'm3', title: 'Second shared call' });
      state.meetings.m3 = { ...live, id: 'm3', title: 'Second shared call' };
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
      state.transcripts.m3 = { id: 'm3', live: true, blocks: blocks.map((block) => ({ ...block })) };
    },
    recording: () => {
      state.status.recording = recordingStatus(streamLive);
      state.library.rows.unshift({ id: 'm0', title: 'Streaming rollout check-in', started_at: iso(-1), duration_ms: 0, recording: true, status: { label: 'Recording', tone: 'red' }, sync_error: '', sent: false, can_send: false, targets: [] });
      state.meetings.m0 = recordingMeeting;
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
    error: () => {
      state.library.rows[1].status = { label: 'Tico sync needs attention', tone: 'amber' };
      state.library.rows[1].sync_error = 'Tico sync needs attention: Tico returned 502. Audio, transcript, and notes remain saved on this Mac.';
      state.library.rows[3].status = { label: 'Needs retry', tone: 'amber' };
      state.meetings.m1 = meeting({
        sync_error: 'Tico sync needs attention: Tico returned 502 Bad Gateway. Audio, transcript, and notes remain saved on this Mac.',
        setup: ["This Tico doesn't support this yet. Update Tico to use it."],
        audio: {
          attention: true, warning: 'Meeting audio stopped delivering frames for 3 s.', pending: 0, failed: 2, finding_speakers: false,
          inputs: [{ label: 'Microphone', state: 'OK', ok: true }, { label: 'Meeting audio', state: '2 silent chunks', ok: false }],
          failures: ['04:12 Meeting audio · HTTP 429 Too Many Requests', '04:55 Meeting audio · HTTP 429 Too Many Requests'], more: 0,
        },
        actions: { ...meeting().actions, items: clone(items).map((it) => (it.id === 'i2' ? { ...it, error: 'Sending failed: Tico returned 503. Try again.' } : it)) },
      });
    },
    // Private questions to Tico: marked "Only you", one still being answered.
    'chat-private': () => {
      state.meetings.m1 = meeting({
        chat: {
          ...chatEmpty, private: true, thinking: true,
          messages: [
            msg('p1', 'You', 'What did we decide about sending action items?', 12, { me: true, private: true }),
            msg('p2', 'Tico', 'One item at a time, as a private task to the bot or person you pick. Each task carries the quote and a link to the transcript.\n\nNothing goes to Tico until you send it.', 12, { bot: true, private: true }),
            msg('p3', 'You', 'Who owns the recovery plan update?', 0, { me: true, private: true, pending: true }),
          ],
        },
      });
    },
    // A meeting not in Tico yet: the first message would connect it, private to you.
    'chat-first': () => {},
    settings: () => {},
    // Own providers: the key fields show.
    'settings-own': () => { state.status.speech = { source: 'own', stream: true, transcribe: true, tico: 'Acme' }; },
    // This Tico transcribes but has no live text yet.
    'settings-nostream': () => { state.status.speech = { source: 'tico', stream: false, transcribe: true, tico: 'Acme' }; },
    // The live meeting with bot-turn notices between the messages.
    notices: () => {
      scenes.live();
      state.notices.m2 = clone(liveNotices);
    },
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
      m.chat.messages[1].name = 'Librarian for the Engineering Knowledge Base and Docs';
      m.people.rows.push({ kind: 'person', name: 'Maria-Fernanda Gonçalves de Albuquerque', local: true });
      state.library.rows[0].title = 'A very long recording title that keeps going past the edge of the window to check truncation';
    },
    // People being added: one on its way, and the reason the last one was refused.
    adding: () => {
      scenes.live();
      const p = state.meetings.m2.people;
      p.rows.push({ kind: 'bot', name: 'Marketing Lead', actor: 'bot:marketing-lead', pending: true });
      p.candidates = p.candidates.filter((c) => c.actor !== 'bot:marketing-lead');
      p.error = 'Not added: Server returned HTTP 403 (forbidden). Check the connection or retry.';
    },
    // The top bar's stack as reported: Me, then a bot, then a person.
    avatars: () => {
      scenes.live();
      const rows = state.meetings.m2.people.rows;
      state.meetings.m2.people.rows = [rows[0], rows[3], rows[2], rows[1], rows[4]];
    },
    mentions: () => {
      state.meetings.m1.notes.text = 'Decided: one item at a time, private tasks, quote attached.\n@Sam Okafor confirms the transcript link; @Librarian files the recovery plan.\nFollow up on hosted pricing cap next week.';
    },
    // Send on a live meeting: it finishes the meeting in Tico first, then files it.
    sending: () => {
      scenes.live();
      state.status.busy = true;
      state.status.sending = { id: 'm2', label: 'Finishing…' };
    },
    connect: () => { state.status.connect = 'https://tico.example.com'; },
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
    edit_shared_line: ({ id, seq, text }) => {
      const block = state.transcripts[id].blocks.find((b) => b.seq === seq);
      Object.assign(block, { paragraphs: [text], edited: true });
    },
    remove_shared_line: ({ id, seq }) => {
      const block = state.transcripts[id].blocks.find((b) => b.seq === seq);
      Object.assign(block, { paragraphs: ['[removed]'], edited: true, removed: true });
    },
    view: ({ have }) => {
      const id = state.status.meeting;
      const rec = state.status.recording;
      return {
        status: clone(state.status),
        live: { notices: clone(state.notices[id] || []) },
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
      a.items.unshift({ id: `n${Date.now()}`, title, quote: '', at_ms: 0, owner: '', owner_name: '', sent: false, sent_to: '', task_url: '', sending: false, can_send: false, error: '' });
      a.waiting++;
    },
    edit_item: ({ id, title }) => { open().actions.items.find((i) => i.id === id).title = title; },
    set_owner: ({ id, owner }) => {
      const it = open().actions.items.find((i) => i.id === id);
      Object.assign(it, { owner, owner_name: owners.find((o) => o.actor === owner)?.name || owner, can_send: true });
    },
    remove_item: ({ id }) => {
      const a = open().actions;
      a.items = a.items.filter((i) => i.id !== id);
      a.waiting = a.items.filter((i) => !i.sent).length;
    },
    send_item: ({ id }) => {
      const a = open().actions;
      const it = a.items.find((i) => i.id === id);
      Object.assign(it, { sent: true, sent_to: it.owner_name, task_url: 'https://tico.example.com/tasks/new', can_send: false });
      a.items = [...a.items.filter((i) => !i.sent), ...a.items.filter((i) => i.sent)];
      a.waiting = a.items.filter((i) => !i.sent).length;
      toast(`Sent to ${it.owner_name}`);
    },
    delete_recording: ({ id }) => {
      state.library.rows = state.library.rows.filter((r) => r.id !== id);
      if (state.status.meeting === id) state.status.meeting = null;
    },
    // Like the Rust side: stop, wait for the last words, finish a live meeting, then file it.
    send_meeting: ({ id, destination }) => {
      const row = state.library.rows.find((r) => r.id === id);
      const m = state.meetings[id];
      const name = orgs.find((o) => o.id === destination)?.name;
      const steps = [];
      if (state.status.recording?.id === id) steps.push('Stopping…', 'Transcribing…');
      if (m?.can_finish) steps.push('Finishing…');
      steps.push('Sending…');
      if (state.status.recording?.id === id) commands.stop_recording();
      state.status.busy = true;
      steps.forEach((label, i) => setTimeout(() => (state.status.sending = { id, label }), i * 600));
      setTimeout(() => {
        state.status.sending = null;
        state.status.busy = false;
        if (m) Object.assign(m, { sent: true, can_finish: false, live: m.live ? 'Ended in Tico' : null });
        if (row) Object.assign(row, { status: { label: 'In Tico', tone: 'accent' }, sent: true });
        toast(`Sent to ${name}`);
      }, steps.length * 600);
    },
    ask: ({ question }) => {
      const chat = open().chat;
      chat.messages.push(msg(`p${Date.now()}`, 'You', question, 0, { me: true, private: true }));
      chat.thinking = true;
      setTimeout(() => {
        chat.thinking = false;
        chat.messages.push(msg(`p${Date.now()}`, 'Tico', 'Here is what the transcript says about that.', 0, { bot: true, private: true }));
      }, 600);
    },
    // Like the Rust side: a meeting not in Tico yet is connected first, private to you.
    chat_send: ({ text }) => {
      const m = open();
      if (!m.chat.connected) {
        Object.assign(m, { live: 'Private in Tico', place: 'Private live · Acme' });
        m.chat.connected = true;
        m.people = { ...m.people, started: true, stage: 'add' };
        const row = state.library.rows.find((r) => r.id === m.id);
        if (row) row.status = { label: 'Live in Tico', tone: 'accent' };
      }
      m.chat.messages.push(msg(`c${Date.now()}`, 'You', text, 0, { me: true }));
    },
    set_chat_private: ({ private: value }) => { open().chat.private = value; },
    save_settings: ({ form }) => {
      const { key, xai_key, speech_source, ...rest } = form;
      Object.assign(state.settings, rest);
      if (speech_source) state.status.speech = { ...state.status.speech, source: speech_source };
      if (key) state.settings.key_saved = true;
      if (xai_key) state.settings.xai_saved = true;
      toast('Settings saved');
    },
    set_audio: ({ microphone, meeting: on }) => Object.assign(state.settings, { microphone, meeting_audio: on }),
    dismiss_error: () => { state.status.error = ''; },
    dismiss_notice: () => { state.status.notice = ''; },
    dismiss_connect: () => { state.status.connect = null; },
    confirm_connect: () => { state.status.connect = null; state.status.signing_in = true; },
    sign_in: () => { state.status.signing_in = true; },
    start_adding: () => { open().people.stage = 'add'; },
    // Shows at once as being added; Tico says yes a moment later.
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
