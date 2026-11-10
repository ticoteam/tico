// Browser globals the recorder test scripts read and write on window. Type-only: emits nothing. A module, not an ambient
// augmentation of the global Window: each test types its own window with
//   import type {RecorderWindow} from './globals.d.ts';
//   declare const window: Window & typeof globalThis & RecorderWindow;
// so recorder/ui/preview/shoot.mts, in the same program, can type the same globals its own way.
export interface PreviewItem {
  id?: string;
  title?: string;
  task_url?: string;
  owner?: string;
  owner_name?: string;
  can_send?: boolean;
  sent?: boolean;
  task_id?: string;
  closed_draft?: {item: SharedItem; text: string; detail: {owner?: string | undefined; body?: string}};
}
export interface SharedItem {
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
export interface SharedArgs {
  action: string;
  body: {version: number; text?: string; detail?: Record<string, unknown>};
  id?: string;
}
export interface PreviewShared {
  can_send: boolean;
  audience?: string[];
  presence?: {actors: {actor: string; name: string}[]};
  sections: {task: [SharedItem, ...SharedItem[]]}; // the fixtures always hold one shared task
}
export interface PreviewActions {
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
export interface PreviewMeeting {
  live_url: string;
  people: {org: string; team: boolean};
  state?: string;
  recording_here?: boolean;
  actions: PreviewActions;
}
export interface PendingFixture {
  a: PreviewActions;
  calls: number;
  inflight: number;
  max: number;
  fail: boolean;
  hold?: Promise<void> | null;
  release?(value?: unknown): void;
  original?: RecorderWindow['__TAURI__']['core']['invoke'];
  sent?: SharedItem;
  lastSend?: unknown;
  adopted?: SharedItem;
  linkAction?: string;
  linkBody?: unknown;
}
export interface RecorderWindow {
  __PREVIEW__: {state: {status: {recording: unknown; update: {held: boolean; blocked: string}}; meetings: Record<string, PreviewMeeting> & {m1: PreviewMeeting}}}; // the mock always seeds m1
  __TAURI__: {core: {invoke(cmd: string, args?: unknown): Promise<unknown>}};
  opened: (string | undefined)[];
  moves: string[];
  copiedText?: string;
  saved?: Record<string, string>;
  pendingFixture: PendingFixture;
}
