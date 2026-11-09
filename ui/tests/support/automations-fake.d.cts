// Types for support/automations-fake.cjs (the helper stays JavaScript: the automation specs require it by its .cjs path).
import type {Browser, Page} from 'playwright';

export interface Viewport {
  width: number;
  height: number;
}

// One write the fake hub received (anything but GET). `body` is the parsed JSON the page sent.
export interface Write {
  method: string;
  path: string;
  body: Record<string, unknown>;
}

// The fixture a spec's tweak edits before the fake hub serves it. Rows are plain objects: a spec that reads one field
// casts the row to its own shape.
export interface AutomationsFixture {
  me: {id: string; name: string; [key: string]: unknown};
  people: Record<string, unknown>[];
  automations: Record<string, unknown>[];
  versions: Record<string, Record<string, unknown>[]>;
  [key: string]: unknown;
}

// The fake hub's state: what it serves, and the rows a write changes. Specs read the parts they need, so each part is
// typed loosely here; a spec that looks inside one casts it to its own interface.
export interface AutomationsDb {
  list: Record<string, unknown>[];
  versions: Record<string, Record<string, unknown>[]>;
  runs: Record<string, Record<string, unknown>[]>;
  conv: {id: string; kind: string; title: string} | null;
  messages: Record<string, unknown>[];
  held: Record<string, {wait: Promise<void>; fail: boolean}>;
  // Set when the page is opened with `maintenance` and `heartbeat` (see openAutomations).
  maint: Record<string, unknown>;
  heart: Record<string, unknown>;
}

export interface OpenAutomationsOptions {
  viewport?: Viewport;
  hash?: string;
  viewer?: string;
  tweak?: ((fx: AutomationsFixture) => void) | null;
  draft?: boolean;
  proposal?: boolean;
  system?: boolean;
  maintenance?: boolean;
  heartbeat?: boolean;
}

export interface AutomationsPage {
  page: Page;
  errors: string[];
  writes: Write[];
  db: AutomationsDb;
  me: Record<string, unknown>;
  // Holds the request for `id` (an automation, or 'maintenance', or the Heartbeat) until the returned release() runs.
  hold: (id: string, fail?: boolean) => () => void;
}

export declare const PHONE: Viewport;
export declare const DESKTOP: Viewport;
export declare function openAutomations(browser: Browser, options?: OpenAutomationsOptions): Promise<AutomationsPage>;
export declare function filledTemplate(templateId: string, owner: string, inbox: string): Record<string, unknown>;
export declare function sideways(page: Page): Promise<boolean>;
export declare function shot(page: Page, name: string): Promise<void>;
