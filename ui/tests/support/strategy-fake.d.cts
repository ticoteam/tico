// Types for support/strategy-fake.cjs (the helper stays JavaScript: the strategy specs require it by its .cjs path).
import type {Browser, Page} from 'playwright';

export interface Viewport {
  width: number;
  height: number;
}

export interface StrategyWrite {
  method: string;
  path: string;
  body: Record<string, unknown>;
}

export interface StrategyDb {
  people: Record<string, unknown>[];
  emps: Record<string, unknown>[];
  groups: Record<string, unknown>[];
  needs: Record<string, unknown>[];
  goals: Record<string, unknown>[];
}

export interface OpenStrategyOptions {
  viewport?: Viewport;
  hash?: string;
  viewer?: string;
  briefing?: boolean;
  listening?: number;
  market?: boolean;
}

export interface StrategyPage {
  page: Page;
  errors: string[];
  writes: StrategyWrite[];
  db: StrategyDb;
  me: Record<string, unknown>;
}

export declare const PHONE: Viewport;
export declare const DESKTOP: Viewport;
export declare function openStrategy(browser: Browser, options?: OpenStrategyOptions): Promise<StrategyPage>;
export declare function sideways(page: Page): Promise<boolean>;
export declare function shot(page: Page, name: string): Promise<void>;
