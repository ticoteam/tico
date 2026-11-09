// Types for support/load.cjs (the helper stays JavaScript: the specs require it by its .cjs path).
import type {BrowserContext, Page} from 'playwright';
export declare const SLOWDOWN: number;
export declare function t(ms: number): number;
export declare function applyTimeouts(contextOrPage: BrowserContext | Page): void;
