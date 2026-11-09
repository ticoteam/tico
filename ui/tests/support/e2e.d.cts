// Types for support/e2e.cjs (the helper stays JavaScript: the end-to-end journeys require it by its .cjs path).
import type {Browser, Page} from 'playwright';
import type {ApiReply, Hub, Pass, PassContext} from './redesign-e2e.cjs';

export type {Hub, PassContext};

// What watch() keeps on a page: its errors, its failed API answers, and the ones the journey expects.
export interface Watch {
  errors: string[];
  failed: {method: string; path: string; status: number | string}[];
  allowed: {status: number; path: RegExp; method: string | null}[];
  known: {pattern: RegExp; why: string}[];
}

export type WatchedPage = Page & {watch: Watch};

export declare const SEEDS: string[];
export declare function journey(name: string, pass: Pass, seeds?: string[], options?: {only?: 'phone' | 'desktop' | null}): Promise<void>;
export declare function open(browser: Browser, hub: Hub, who: string,
  options?: {phone?: boolean; hash?: string; timezone?: string}): Promise<WatchedPage>;
export declare function watch(page: Page): WatchedPage;
export declare function expectFailure(page: Page, status: number, path: string | RegExp, method?: string | null): void;
export declare function expectError(page: Page, pattern: RegExp, why: string): void;
export declare function assertClean(page: Page, where: string): void;
export declare function call<T = unknown>(hub: Hub, who: string, method: string, path: string, body?: unknown): Promise<ApiReply<T>>;
export declare function must<T = unknown>(hub: Hub, who: string, method: string, path: string, body?: unknown, what?: string): Promise<T>;
export declare function until<T>(check: () => Promise<T> | T, what: string, ms?: number): Promise<NonNullable<T>>;
export declare function drawn(page: Page, selector: string, ready: (text: string) => boolean, what: string): Promise<string>;
export {go, shot, sideways, t} from './redesign-e2e.cjs';

// The test scripts' lib is DOM without DOM.Iterable (ui/tests/tsconfig.json), so a NodeList or an HTMLCollection does not
// spread. The scripts run in Chrome, where both do: say so here, once, for every script.
declare global {
  interface NodeListOf<TNode extends Node> extends Iterable<TNode> {}
  interface HTMLCollectionBase extends Iterable<Element> {}
  interface DOMTokenList extends Iterable<string> {}
}
