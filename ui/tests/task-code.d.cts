// Types for support/task-code.cjs (the helper stays JavaScript: its scripts require it by its .cjs path).
// Fixture rows are typed by the fields the scripts read; the rest stay unknown.
import type {Browser, Page} from 'playwright';
import type {Task} from './tasks-page.cjs';

export interface Write {
  method: string;
  p: string;
  body: unknown;
}

export interface Fixture {
  tasks: Task[];
  tree: (Task & {children: Task[]})[];
  runView: Record<string, unknown>;
}

export interface Opened {
  page: Page;
  errors: string[];
  writes: Write[];
  data: Fixture & {comments?: unknown[]; events?: unknown[]; files?: unknown[]};
}

export interface OpenOptions {
  viewport?: {width: number; height: number};
  theme?: string;
  at?: string;
  mover?: boolean;
  rich?: boolean;
}

export declare function open(browser: Pick<Browser, 'newPage'>, options?: OpenOptions): Promise<Opened>;
