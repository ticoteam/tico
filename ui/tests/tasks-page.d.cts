// Types for support/tasks-page.cjs (the helper stays JavaScript: its scripts require it by its .cjs path).
// The fixtures are the fake server's rows: the fields the scripts read are typed, the rest stay unknown.
import type {Browser, Page, Route} from 'playwright';

export interface Task {
  id: string;
  title: string;
  body?: string;
  status?: string;
  owner_id?: string | null;
  requester_id?: string | null;
  updated_at?: string;
  created_at?: string;
  version: number;
  labels?: string[];
  links?: unknown[];
  holds?: {kind: string; reason?: string | null; since?: string | null; until?: string; by?: string}[];
  note?: string | null;
  due_at?: string | null;
  ask?: {id: string; body: string; sender_id: string; recipient_id: string} | null;
  person_ask?: string | null;
  waiting_on?: string | null;
  relations?: Record<string, unknown[]>;
  [field: string]: unknown;
}

export interface Posted {
  p: string;
  body: Record<string, unknown>;
}

export interface OpenOptions {
  viewport?: {width: number; height: number};
  theme?: string;
  hash?: string;
  failIds?: string[];
  prefs?: unknown;
  mover?: boolean;
  types?: unknown[];
  local?: Record<string, string>;
  people?: unknown;
  baseTasks?: Task[] | null;
  extraTasks?: Task[];
  byNeed?: boolean;
  clock?: unknown;
  activePage?: ((route: Route, rows: Task[], offset: number, limit: number) => unknown) | null;
  bots?: unknown[];
  beforeGoto?: ((page: Page) => unknown) | null;
  needsLive?: boolean;
  waitForNeeds?: boolean;
}

export interface Opened {
  page: Page;
  errors: string[];
  posts: Posted[];
  tasks: Task[];
  pref: () => unknown;
  conflictNextUpdate: (id: string) => void;
}

export declare function open(browser: Browser, options?: OpenOptions): Promise<Opened>;
export declare function fixtures(): Task[];
export declare function moreView(page: Page, view: string): Promise<void>;

// The app's classic-script globals, read inside page.evaluate. Declared once here, since several scripts read the same ones.
// Each is typed by the fields a script reads; a function the app defines takes its arguments loosely (unknown).
interface TaskLiveDraft {
  base: Task;
  patch: Record<string, unknown>;
  flight?: unknown;
  error?: string | null;
  timer?: number;
}
interface TaskLive {
  id: string;
  identity?: string;
  owner?: string;
  shared?: boolean;
  reading?: boolean;
  draft: TaskLiveDraft;
  fields: Record<string, {version: number}>;
}
// The task modal: an element with the app's expando properties on it.
interface TaskModalElement extends HTMLDialogElement {
  drawSeq?: number;
  liveTask?: Task;
  taskRail?: unknown;
  taskLive?: TaskLive;
  close(): void;
}
interface NeedsDecideState {
  busy?: boolean;
  lockUntil?: number;
  pending?: unknown;
  data: Map<string, {comments?: unknown[]}>;
}

declare global {
  // The DOM.Iterable members that tsconfig's lib (DOM) leaves out: the browser's collections are iterable.
  interface HTMLCollectionBase {
    [Symbol.iterator](): ArrayIterator<Element>;
  }
  interface NodeListOf<TNode extends Node> {
    [Symbol.iterator](): ArrayIterator<TNode>;
  }
  var TASKS_ST: {
    view: string;
    tasks: Task[];
    peek: string;
    loading: boolean;
    side?: string;
    pendingFinish?: unknown;
    peekVia?: unknown;
    [field: string]: unknown;
  };
  var tasksLoad: (state: unknown, opts?: unknown) => Promise<unknown>;
  var tasksRender: (state: unknown) => void;
  var tasksNeedsQueue: (state: unknown) => boolean;
  var tasksHeadFit: (state: unknown) => void;
  var taskPeekClose: (state: unknown) => void;
  var v2Refresh: () => Promise<unknown>;
  var v2MessageHTML: (message: unknown) => string;
  var myActor: () => string;
  var needsFirstIn: () => boolean;
  var needsTake: (...args: unknown[]) => unknown;
  var taskAsksRead: (...args: unknown[]) => unknown;
  var taskSaveQueued: (...args: unknown[]) => unknown;
  var taskModal: () => TaskModalElement;
  var taskModalShow: (task: unknown, ...rest: unknown[]) => Promise<unknown>;
  var taskLiveStop: (...args: unknown[]) => unknown;
  var taskLiveStorageKey: (...args: unknown[]) => string;
  var TASK_EDIT_DRAFTS: Map<string, TaskLiveDraft>;
  var TASK_EDIT_STORAGE: string;
  var TASK_DRAFTS: Map<string, unknown>;
  var TASK_MODAL_BACK: boolean;
  var TASK_TYPES: unknown[];
  var TASK_TYPES_READ: boolean;
  var DEC: NeedsDecideState | null;
  var DEC_OUTBOX: Set<unknown>;
  var DEC_BACK: boolean;
  var decideOpen: (key?: string) => boolean;
  var decideStop: () => void;
  var decideAct: (...args: unknown[]) => unknown;
  var decideCards: (items: unknown) => {key: string; [field: string]: unknown}[];
  var decideCardKey: (item: unknown) => string;
  var decideFromPerson: (...args: unknown[]) => unknown;
  var decideWriteBody: (card: unknown, what: string | undefined, index: number | undefined, note: string | undefined) => {writes: {path: string; body: Record<string, unknown>}[]};
  var decideStepsOnly: (card: unknown) => boolean;
  var approvalPayloadNode: (item: unknown) => HTMLElement;
  var staleDecide: (...args: unknown[]) => Promise<unknown>;
  var qcardQuestion: (item: unknown) => string;
  var qcardSub: (item: unknown) => string;
  var qcardBoxHTML: (task: unknown) => string;
  var qcardBodyHTML: (...args: unknown[]) => string;
  var qcardActionsHTML: (...args: unknown[]) => string;
  var qcardFallbackHTML: (item: unknown) => string;
  // Marks a script sets on a drawn element to see later whether the same element is still on screen.
  interface Element {
    __drawn?: boolean;
    __kept?: boolean;
  }
  var S: {
    me: {id: string; mover?: boolean; role?: string};
    v2: {needs: {id: string; kind: string; rows: unknown[]; ask?: {refs: {questions: {options?: {label: string}[]; [field: string]: unknown}[]}}; [field: string]: unknown}[]; [field: string]: unknown};
    route: string;
    config?: {environment_id?: string};
    v?: unknown;
    [field: string]: unknown;
  };
  var SESSION: {out: boolean; seen: boolean; identity: string; [field: string]: unknown};
  var SESSION_RETURN: string;
  var sessionKeep: () => void;
  var sessionReturn: () => void;
  var signedOut: (...args: unknown[]) => unknown;
  // Expandos a script sets on window for the page to read back.
  var pwned: number | undefined;
  var taskSources: (EventTarget & {closed?: boolean; url?: string})[] | undefined;
  var taskSafety: (() => void)[] | undefined;
  var propertySave: unknown;
  var oldView: TaskLive | undefined;
  var get: (path: string, opts?: unknown) => Promise<unknown>;
  var leavingLive: TaskLive | undefined;
  var __staleAnswer: unknown;
}

export {};
