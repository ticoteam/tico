// Types for support/mailboxes-fake.cjs (the helper stays JavaScript: scripts require it by its .cjs path).
import type {Browser, Page} from 'playwright';
import type {GoogleFake} from './google-fake.cts';

export interface Box {
  id: string;
  address: string;
  provider: string;
  kind: string;
  person_id: string | null;
  person_name: string;
  connection: string;
  status: string;
  state: string;
  level: string;
  routines: unknown[];
  used_by: {people: number; bots: number; names: string[]};
  synced_at: string | null;
  newest_epoch: number | null;
  sync: string;
  sync_error: string;
  [key: string]: unknown;
}
export interface Access {
  id: string | null;
  grantee_type: string;
  grantee_id: string;
  name: string;
  level: string;
  implicit: boolean;
}
export interface Routine {
  id: string;
  name: string;
  enabled: boolean;
  can_edit: boolean;
}
export interface MailboxWrite {
  method: string;
  path: string;
  body: unknown;
}
export interface MailboxesFake {
  admin: boolean;
  me: string;
  writes: MailboxWrite[];
  n: number;
  may: {read: boolean; organize: boolean; send: boolean; change_calendar: boolean};
  state: {
    mailboxes: Box[];
    accesses: Record<string, Access[]>;
    routines: Record<string, Routine[]>;
    candidates: {type: string; id: string; name: string}[];
  };
  // The JSON a route answers ({status, body} for a refusal), or undefined for any other path.
  answer(path: string, method?: string, body?: unknown): unknown;
}
export declare function box(id: string, address: string, over?: Partial<Box>): Box;
export declare function mailboxesFake(options?: {admin?: boolean; me?: string}): MailboxesFake;
export declare function shot(page: Page, file: string, theme?: string): Promise<void>;
export interface OpenOptions {
  hash?: string;
  ready?: string | undefined;
  width?: number;
  height?: number;
  automations?: {refuse?: string};
  extra?: (path: string, method: string, body: unknown, url: URL) => unknown;
  mail?: Record<string, unknown[]>;
  skip?: boolean;
  theme?: string;
}
export declare function open(browser: Browser, fake: MailboxesFake, google: GoogleFake, options?: OpenOptions):
  Promise<{page: Page; seen: {errors: string[]}}>;
