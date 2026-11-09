// Types for support/redesign-e2e.cjs (the helper stays JavaScript: the journeys require it by its .cjs path).
import type {Browser, Page} from 'playwright';

// A hub from support/real-hub.cjs: the app on a fresh database, with its teammates' Bearer tokens.
export interface Hub {
  url: string;
  seeds: string[];
  skipped: string[];
  tokens: Record<string, string>;
  runner: string;
  stop(): Promise<void>;
}

// A journey's one pass: a fresh hub, the browser, the viewport it runs at.
export interface PassContext {
  hub: Hub;
  browser: Browser;
  phone: boolean;
  tag: 'phone' | 'desktop';
}
export type Pass = (ctx: PassContext) => Promise<void>;

// A page opened by open(): page.errors holds its uncaught errors.
export type RedesignPage = Page & {errors: string[]};

// The API's answer: the status and the parsed body (null when there is none).
export interface ApiReply<T = unknown> {
  status: number;
  body: T;
}

export interface AutomationRule {
  when: {field: string; op: string; value: string}[];
}
export interface Automation {
  id: string;
  name: string;
  version: number;
  steps: {rules: AutomationRule[]}[];
  [key: string]: unknown;
}

export declare const t: (ms: number) => number;
export declare function run(name: string, seeds: string[], pass: Pass, options?: {only?: 'phone' | 'desktop' | null}): Promise<void>;
export declare function open(browser: Browser, hub: Hub, who: string,
  options?: {phone?: boolean; hash?: string; time?: Date | number | string | null; timezone?: string}): Promise<RedesignPage>;
export declare function go(page: Page, hash: string): Promise<void>;
export declare function api<T = unknown>(page: Page, method: string, p: string, body?: unknown): Promise<ApiReply<T>>;
export declare function ok<T = unknown>(page: Page, method: string, p: string, body?: unknown): Promise<T>;
export declare function automationNamed<T = Automation>(page: Page, name: string): Promise<T | undefined>;
export declare function sideways(page: Page): Promise<boolean>;
export declare function shot(page: Page, name: string): Promise<void>;
