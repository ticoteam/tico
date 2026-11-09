// Types for support/real-hub.cjs (the helper stays JavaScript: the real-hub specs require it by its .cjs path).
import type {Browser, Page} from 'playwright';

export interface HubOptions {
  seeds?: string[];
  env?: Record<string, string>;
}

export interface Hub {
  url: string;
  seeds: string[];
  skipped: string[];
  // Each teammate id ("ana", "dana", "engineering-lead", ...) to its Bearer token.
  tokens: Record<string, string>;
  // The world's runner token, to claim a bot's run as its computer would.
  runner: string;
  // Kills the hub process this call started.
  stop: () => Promise<void>;
}

export interface PageAsOptions {
  phone?: boolean;
  timezone?: string;
}

export declare const PHONE: {
  viewport: {width: number; height: number};
  isMobile: boolean;
  hasTouch: boolean;
  userAgent: string;
};
export declare const DESKTOP: {viewport: {width: number; height: number}};
export declare function startHub(options?: HubOptions): Promise<Hub>;
// The caller closes the page with page.context().close().
export declare function pageAs(browser: Browser, hub: Hub, teammate: string, options?: PageAsOptions): Promise<Page>;
