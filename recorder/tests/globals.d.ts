// Browser globals the recorder test scripts read and write on window. Ambient and type-only: emits nothing.
interface PreviewItem {
  id?: string;
  title?: string;
  task_url?: string;
  owner?: string;
  owner_name?: string;
  can_send?: boolean;
  sent?: boolean;
  task_id?: string;
  closed_draft?: {item: SharedItem; text: string; detail: {owner?: string; body?: string}};
}
interface SharedItem {
  version: number;
  id: string;
  section: string;
  text: string;
  status: string;
  updated_by?: string;
  result_ref?: string;
  proposed_task_id?: string;
  proposed_number?: number;
  detail: {owner?: string; due?: string; body?: string};
}
interface SharedArgs {
  action: string;
  body: {version: number; text?: string; detail?: Record<string, unknown>};
  id?: string;
}
interface PreviewShared {
  can_send: boolean;
  audience?: string[];
  presence?: {actors: {actor: string; name: string}[]};
  sections: {task: SharedItem[]};
}
interface PreviewActions {
  items: PreviewItem[];
  saved?: boolean;
  actor?: string;
  shared_url?: string;
  shared: PreviewShared;
  shared_scope?: string;
  can_link?: boolean;
  link_scope?: string[];
  linked?: {tasks: {task_id: string; title: string; number?: number | null; status: string}[]} | null;
  linked_browser?: string;
}
interface PreviewMeeting {
  live_url: string;
  people: {org: string; team: boolean};
  state?: string;
  recording_here?: boolean;
  actions: PreviewActions;
}
interface PendingFixture {
  a: PreviewActions;
  calls: number;
  inflight: number;
  max: number;
  fail: boolean;
  hold?: Promise<void> | null;
  release?(value?: unknown): void;
  original?: Window['__TAURI__']['core']['invoke'];
  sent?: SharedItem;
  lastSend?: unknown;
  adopted?: SharedItem;
  linkAction?: string;
  linkBody?: unknown;
}
interface Window {
  __PREVIEW__: {state: {status: {recording: unknown; update: {held: boolean; blocked: string}}; meetings: Record<string, PreviewMeeting>}};
  __TAURI__: {core: {invoke(cmd: string, args?: unknown): Promise<unknown>}};
  opened: (string | undefined)[];
  moves: string[];
  copiedText?: string;
  saved?: Record<string, string>;
  pendingFixture: PendingFixture;
}
