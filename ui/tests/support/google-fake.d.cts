// Types for support/google-fake.cjs (the helper stays JavaScript: scripts require it by its .cjs path).
export interface GoogleRow {
  status: string;
  domain: string;
  project_id: string;
  client_id: string;
  scopes: string[];
  delegation_url: string;
  connected_at: string | null;
  checked_at: string | null;
  last_error: string | null;
  probe: unknown;
  generation: number;
  [key: string]: unknown;
}
export interface GoogleState {
  can_admin: boolean;
  workspace: GoogleRow | null;
  pending: GoogleRow | null;
  me?: {mailbox: string; covered_by_workspace: boolean} | null;
  oauth: {configured: boolean; source: string | null; redirect_uri: string; client_id: string | null};
  setup: {base: string; reachable: boolean; newest?: number};
  permissions: {organize_mail: boolean; send_mail: boolean; change_calendar: boolean};
  [key: string]: unknown;
}
export interface GooglePerson {
  person_id: string;
  name: string;
  mailbox: string;
  status: string;
}
export interface GoogleMail {
  configured: boolean;
  mailbox: string;
  status: string;
  syncing: boolean;
  can: string[];
}
export interface GoogleWrite {
  method: string;
  path: string;
  body: unknown;
}
// The answer to a queued POST /workspace/check.
export type GoogleCheck = 'ok' | 'unauthorized' | 'other';
export interface GoogleFake {
  writes: GoogleWrite[];
  checks: GoogleCheck[];
  codes: number;
  gen: number;
  personal: GoogleMail | null;
  peopleReads: number;
  peoplePage: number;
  people: GooglePerson[];
  state: GoogleState;
  answer(path: string, url: string, method?: string, body?: unknown): unknown;
}
export declare function row(status: string, over?: Partial<GoogleRow>): GoogleRow;
export declare function googleFake(): GoogleFake;
