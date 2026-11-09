// Types for support/threads-fake.cjs (the helper stays JavaScript: scripts require it by its .cjs path).
export interface ThreadsMessage {
  id: string;
  from_actor?: string;
  body: string;
  created?: string;
  [key: string]: unknown;
}
export type ThreadsRows = ThreadsMessage[] | (() => ThreadsMessage[]);
// What a script posts to a threads route: the text of a message, or the fields of a new DM.
export interface ThreadsPost {
  text?: string;
  [key: string]: unknown;
}
export interface ThreadsFakeOptions {
  bot: string;
  me?: string;
  messages?: ThreadsRows;
  home?: ThreadsRows;
  sent?: ThreadsPost[];
}
// The JSON for a threads route, or undefined for any other path.
export type ThreadsAnswer = (path: string, url: string | URL, method?: string, body?: unknown) => unknown;
export declare function threadsFake(options: ThreadsFakeOptions): ThreadsAnswer;
