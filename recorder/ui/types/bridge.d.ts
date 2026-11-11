// The Tauri bridge between the Recorder window and the Rust app (recorder/src/app): every
// #[tauri::command] the page calls, with its arguments and reply, and the events the app emits.
// Names follow the Rust side: Tauri maps a camelCase argument (pausedConfirmed) to the snake_case
// parameter (paused_confirmed). The shapes are the json! values in recorder/src/app/recorder.rs
// (view and its *_view parts), main.rs, update.rs, task_links.rs and screen.rs; keep them in step.
// Erased at build time: the page scripts only `import type` from here.

/** A Tico the Recorder is signed in to (Settings' `connections`). */
export interface Connection {
  id: string;
  name: string;
  /** The address without https://, or '' when it is the name. */
  host: string;
  url: string;
  default: boolean;
}
/** An org of the Tico desktop app on this Mac (`view.desktop_orgs`). */
export interface DesktopOrg {
  name: string;
  url: string;
  email: string;
  active: boolean;
  connected: boolean;
}
/** Where a meeting can be saved, and whether it already is. */
export interface Target {
  id: string;
  name: string;
  already: boolean;
}
export interface OrgRef {
  id: string;
  name: string;
}

// ---- status ----
export interface StreamStatus {
  channel: string;
  /** connecting, live, retrying, stopped or off. */
  state: string;
  error: string;
  dropped_ms: number;
  reconnects: number;
}
export interface Source {
  channel: string;
  label: string;
  name: string;
  level: number;
  no_signal: boolean;
}
export interface RecordingStatus {
  id: string;
  elapsed_ms: number;
  phase: string;
  error: string;
  sources: Source[];
  stream: StreamStatus[];
}
export interface ScreenStatus {
  capture_id: string;
  meeting: string;
  title: string;
  destination: string;
  active: boolean;
  source: string;
  phase: string;
  error: string;
}
export interface Speech {
  source: 'tico' | 'own' | 'none';
  stream: boolean;
  transcribe: boolean;
  tico: string;
}
/** update::Updates::status: the app's own update state, added to `status` by `view`. */
export interface UpdateStatus {
  /** none, checking, downloading, ready, installed, current or error. */
  state: string;
  version: string;
  notes: string;
  blocked: string;
  error: string;
  progress: number | null;
  current: string;
  location: string;
  movable: boolean;
  prompt: boolean;
  held: boolean;
}
export interface Status {
  screen: ScreenStatus | null;
  busy: boolean;
  demo: boolean;
  error: string;
  message: string;
  signing_in: boolean;
  connect: string | null;
  /** A call or calendar event from an org just signed in to, waiting for Record. */
  record_prompt?: { title: string } | null;
  first_run: boolean;
  suggested: string;
  recording: RecordingStatus | null;
  meeting: string | null;
  sending: { id: string; label: string } | null;
  profile: boolean;
  notice: string | null;
  speaker_permission: boolean;
  speaker_access: boolean | null;
  speech: Speech;
  update: UpdateStatus;
  /** Never sent: the page reads it as a fallback for a build that put the desktop orgs here. */
  desktop_orgs?: DesktopOrg[];
}

// ---- library ----
export interface CalendarEvent {
  title?: string;
  start: string;
  end: string;
}
export interface LibraryRow {
  id: string;
  title: string;
  started_at: string;
  duration_ms: number;
  recording: boolean;
  paused_warning: boolean;
  status: { label: string; tone: string } | null;
  sync_error: string;
  sent: boolean;
  can_send: boolean;
  local: boolean;
  org: { id: string; name: string; url: string } | null;
  live_org: string | null;
  targets: Target[];
  /** A Tico call's recording: Tico files it when the call ends, so it is never saved from here. */
  call?: boolean;
}
export interface Library {
  rows: LibraryRow[];
  events: CalendarEvent[];
  calendar_stale: boolean;
  connected: boolean;
}

// ---- settings ----
export interface Settings {
  microphone: string;
  microphones: string[];
  meeting_audio: boolean;
  transcription_url: string;
  transcription_model: string;
  transcription_language: string;
  calendar: boolean;
  calendar_available: boolean;
  connections: Connection[];
  recording: boolean;
  key_saved: boolean;
  streaming: boolean;
  streaming_model: string;
  xai_saved: boolean;
  speech_source: string;
  tico: string | null;
}
/** SettingsForm: what Save sends. Every field is serde(default) on the Rust side. */
export interface SettingsForm {
  microphone?: string;
  meeting_audio?: boolean;
  transcription_url?: string;
  transcription_model?: string;
  transcription_language?: string;
  calendar?: boolean;
  key?: string;
  streaming?: boolean;
  streaming_model?: string;
  xai_key?: string;
  speech_source?: string;
}

// ---- transcript ----
export interface Block {
  speaker: string;
  me: boolean;
  start_ms: number;
  paragraphs: string[];
  partial?: boolean;
  streamed?: boolean;
  /** A line shared live: Tico's number for it; null for speech not sent yet. */
  seq?: number | null;
  edited?: boolean;
  removed?: boolean;
  editable?: boolean;
}
export interface Transcript {
  id: string;
  live?: boolean;
  shared?: boolean;
  blocks: Block[];
}

// ---- meeting ----
export interface Audio {
  attention: boolean;
  warning: string;
  pending: number;
  failed: number;
  finding_speakers: boolean;
  inputs: { label: string; state: string; ok: boolean }[];
  failures: string[];
  more: number;
}
export interface Owner {
  actor: string;
  name: string;
  bot: boolean;
}
/** A task already linked in Tico (`actions.linked.tasks`). */
export interface LinkedTask {
  task_id: string;
  title: string;
  number: number | null;
  status: string;
  pending: boolean;
  error: string;
}
/** An item of a saved meeting, as Tico's /api/v2/meetings/{id}/items returns it. */
export interface SharedItem {
  id: string;
  version: number;
  text: string;
  detail?: Record<string, string>;
  /** proposed, pushed or dismissed. */
  status: string;
  /** task or doc. */
  section: string;
  updated_by?: string;
  result_ref?: string;
  proposed_task_id?: string;
  proposed_number?: number | string;
  quote?: string;
  at_ms?: number | null;
}
export interface SharedItems {
  sections?: Record<string, SharedItem[]>;
  presence?: { actors?: { actor: string; name: string }[] };
  audience?: string[] | null;
  can_send?: boolean;
  gone?: boolean;
}
/** A local item whose saved copy closed while it had unsaved edits. */
export interface ClosedDraft {
  item: SharedItem;
  text: string;
  detail: Record<string, string>;
}
export interface Item {
  id: string;
  unsaved: boolean;
  server_item_id: string | null;
  title: string;
  number: number | null;
  status: string | null;
  proposed_task_id: string | null;
  proposed_number: number | null;
  due: string;
  body: string;
  /** task, document or link. */
  kind: string;
  document: string;
  quote: string;
  at_ms: number;
  owner: string;
  owner_name: string;
  sent: boolean;
  pending: boolean;
  sent_to: string;
  closed_draft: ClosedDraft | null;
  task_id: string;
  task_url: string;
  can_link: boolean;
  sending: boolean;
  send_blocked: string | null;
  paused_warning: boolean;
  can_send: boolean;
  error: string;
}
export interface Actions {
  linked: { tasks?: LinkedTask[]; next_cursor?: string | null } | null;
  linked_browser: string | null;
  /** Opaque: compared as JSON to tell a stale task picker. */
  link_scope: unknown;
  can_link: boolean;
  actor: string | null;
  saved: boolean;
  shared: SharedItems | null;
  shared_url: string | null;
  shared_scope: string;
  mode: 'signin' | 'choose' | 'ready';
  org: string;
  orgs: OrgRef[];
  multiple: boolean;
  can_change: boolean;
  draft_status: string;
  skipped_links: number;
  items: Item[];
  waiting: number;
  owners: Owner[];
}
export interface PersonRow {
  kind: 'person' | 'bot';
  name: string;
  me?: boolean;
  actor?: string;
  removable?: boolean;
  pending?: boolean;
  local?: boolean;
}
export interface People {
  rows: PersonRow[];
  stage: string;
  orgs: OrgRef[];
  org: string;
  team: boolean;
  started: boolean;
  can_share: boolean;
  blocked: string;
  candidates: Owner[];
  error: string;
}
export interface ChatMessage {
  id: string;
  name: string;
  bot?: boolean;
  me: boolean;
  private: boolean;
  text: string;
  time?: string;
  pending?: boolean;
}
export interface Chat {
  connected: boolean;
  can_message: boolean;
  private: boolean;
  messages: ChatMessage[];
  replying: string[];
  thinking: boolean;
  bots: number;
  error: string;
  retry: string;
  ready: boolean;
}
export interface Meeting {
  id: string;
  link_ready: boolean;
  can_share_screen: boolean;
  title: string;
  started_at: string;
  duration_ms: number;
  state: string;
  recording_here: boolean;
  title_editable: boolean;
  sent: boolean;
  connected: boolean;
  send_blocked: string | null;
  targets: Target[];
  live_org: string | null;
  team: boolean;
  kept_local: boolean;
  /** Who can join, once shared live: Team can join, Private in Tico or Ended in Tico. */
  live: string | null;
  live_url: string | null;
  paused_warning: boolean;
  /** live, paused, ended or ''. */
  live_state: string;
  can_control: boolean;
  can_discard: boolean;
  discarded: boolean;
  discard_pending: boolean;
  can_finish: boolean;
  /** A Tico call's recording: Tico files it when the call ends, so Stop has no Finish. */
  call?: boolean;
  place: string;
  sync_error: string;
  setup: string[];
  audio: Audio;
  actions: Actions;
  notes: { text: string; editable: boolean };
  people: People;
  chat: Chat;
}

// ---- view ----
/** The hash of each part the page holds; a part comes back only when its hash differs. */
export interface Have {
  library: string;
  meeting: string;
  transcript: string;
  settings: string;
}
export interface Part<T> {
  hash: string;
  data: T;
}
export interface LiveNotice {
  id: string;
  kind: string;
  bot: string;
  text: string;
  at: string;
}
export interface View {
  status: Status;
  live: { notices: LiveNotice[] };
  desktop_orgs: DesktopOrg[];
  library: Part<Library> | null;
  settings: Part<Settings> | null;
  meeting: Part<Meeting | null> | null;
  transcript: Part<Transcript | null> | null;
}

// ---- replies of the commands that return something the page reads ----
export interface PickerTask {
  id: string;
  number: number | null;
  title: string;
  status: string;
  owner_id: string;
}
export interface TaskPage {
  tasks: PickerTask[];
  next_offset: number | null;
}
export interface MeetingLink {
  url: string;
  private?: boolean;
  only_you?: boolean;
  copied: boolean;
}
/** shared_item: Tico's reply, or the item as Tico holds it when the version was stale. */
export interface SharedReply {
  item?: SharedItem;
  pushed?: boolean;
  conflict?: SharedItem | null;
  error?: string;
}
export interface MoveReply {
  error?: string;
  ask?: string;
}

type None = Record<string, never>;
/** Every command: its arguments (camelCase, as invoke sends them) and its reply. */
export interface Commands {
  view: { args: { have: Have }; reply: View };
  shared_item: {
    args: { id?: string | null; action?: 'send' | 'link' | null; body?: Record<string, unknown> };
    reply: SharedReply;
  };
  task_picker: { args: { query: string; kind?: string; offset: number }; reply: TaskPage };
  task_types: { args: None; reply: OrgRef[] };
  sign_out: { args: { id: string }; reply: null };
  meeting_link: { args: { id: string; destination: string; confirmed: boolean; copy: boolean }; reply: MeetingLink };
  share_screen: { args: { id: string; expectedTeam: boolean }; reply: null };
  update_check: { args: None; reply: UpdateStatus };
  update_install: { args: None; reply: UpdateStatus };
  move_to_applications: { args: { how: string }; reply: MoveReply };
  // The commands! block in main.rs: each reply is what the Recorder method returns, as JSON.
  more_task_links: { args: None; reply: unknown };
  link_selected_tasks: { args: { ids: string[] }; reply: unknown };
  accept_link_item: { args: { id: string }; reply: unknown };
  unlink_item: { args: { id: string }; reply: unknown };
  stop_screen: { args: None; reply: unknown };
  /** The new recording's id, or null when it did not start. */
  record: { args: { event: CalendarEvent | null }; reply: string | null };
  open_meeting: { args: { id: string }; reply: unknown };
  close_meeting: { args: None; reply: unknown };
  stop_recording: { args: None; reply: unknown };
  rename: { args: { title: string }; reply: unknown };
  finish_live: { args: None; reply: unknown };
  control_live: { args: { action: string }; reply: unknown };
  edit_shared_line: { args: { id: string; seq: number; text: string }; reply: unknown };
  remove_shared_line: { args: { id: string; seq: number }; reply: unknown };
  retry: { args: { id: string }; reply: unknown };
  set_notes: { args: { notes: string }; reply: unknown };
  add_item: { args: { title: string }; reply: unknown };
  edit_item: { args: { id: string; title: string }; reply: unknown };
  set_owner: { args: { id: string; owner: string }; reply: unknown };
  remove_item: { args: { id: string }; reply: unknown };
  send_item: { args: { id: string; owner: string; pausedConfirmed?: boolean }; reply: unknown };
  choose_org: { args: { id: string }; reply: unknown };
  change_org: { args: None; reply: unknown };
  load_roster: { args: None; reply: unknown };
  start_adding: { args: None; reply: unknown };
  choose_people_org: { args: { id: string }; reply: unknown };
  pick: { args: { actor: string }; reply: unknown };
  add_participant: { args: { name: string }; reply: unknown };
  remove_access: { args: { actor: string }; reply: unknown };
  set_team: { args: { team: boolean }; reply: unknown };
  ask: { args: { question: string }; reply: unknown };
  chat_send: { args: { text: string }; reply: unknown };
  set_chat_private: { args: { private: boolean }; reply: unknown };
  send_meeting: { args: { id: string; destination: string; pausedConfirmed?: boolean }; reply: unknown };
  delete_recording: { args: { id: string }; reply: unknown };
  share_live: { args: { destination: string; team: boolean; actors: string[] }; reply: unknown };
  save_meeting: {
    args: { id: string; destination: string; audience: string; title: string; pausedConfirmed?: boolean };
    reply: unknown;
  };
  keep_local: { args: { id: string }; reply: unknown };
  save_settings: { args: { form: SettingsForm }; reply: unknown };
  set_audio: { args: { microphone: string; meeting: boolean }; reply: unknown };
  sign_in: { args: { address: string }; reply: unknown };
  sign_in_desktop: { args: { url?: string | null }; reply: unknown };
  make_default: { args: { id: string }; reply: unknown };
  import_legacy: { args: { directory: string }; reply: unknown };
  confirm_connect: { args: None; reply: unknown };
  dismiss_connect: { args: None; reply: unknown };
  confirm_record: { args: None; reply: unknown };
  dismiss_record: { args: None; reply: unknown };
  dismiss_error: { args: None; reply: unknown };
  open_url: { args: { url: string }; reply: unknown };
  dismiss_notice: { args: None; reply: unknown };
  allow_speaker_names: { args: None; reply: unknown };
  dismiss_speaker_names: { args: None; reply: unknown };
  set_visible: { args: { visible: boolean }; reply: unknown };
}
export type Command = keyof Commands;
export type ArgsOf<K extends Command> = Commands[K]['args'];
export type ReplyOf<K extends Command> = Commands[K]['reply'];
/** A command without arguments may leave them out. */
export type InvokeArgs<K extends Command> = None extends ArgsOf<K> ? [args?: ArgsOf<K>] : [args: ArgsOf<K>];
/** invoke, typed by the command map. */
export type Invoke = <K extends Command>(cmd: K, ...args: InvokeArgs<K>) => Promise<ReplyOf<K>>;

/** What the app emits: `changed` (the view may differ), `level` (ten times a second while recording). */
export interface Events {
  changed: number;
  level: { id: string; elapsed_ms: number; sources: { channel: string; level: number }[] };
  'screen-preview': { capture_id: string; url: string | null };
  'open-updates': number;
}
export type Listen = <E extends keyof Events>(
  name: E,
  callback: (event: { payload: Events[E] }) => void,
) => Promise<() => void>;

/** window.__TAURI__ (withGlobalTauri): untyped at the edge; Invoke types each call. */
export interface Tauri {
  core: { invoke: (cmd: string, args?: object) => Promise<unknown> };
  event: { listen: Listen };
}

/** The window as the page scripts share it: icons.ts sets `icon`, update.ts the update hooks, app.ts `ticoRedraw`. */
export type RecorderWindow = Window &
  typeof globalThis & {
    __TAURI__: Tauri;
    icon: (name: string, size?: number) => string;
    ticoUpdate?: (update: UpdateStatus | undefined) => void;
    ticoUpdateRow?: (update: UpdateStatus | undefined) => string;
    ticoRedraw?: () => void;
  };
