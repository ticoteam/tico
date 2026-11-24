/* Each page's first-visit intro (plan: setup walkthrough v4, Part 3): one line in Tico's chat, up to three spotlights on
   the page's own elements, and a chip only where it is an action the app already has. It plays once per person per
   page (preference intros.seen) and again on "Show me how this works". Plain data and a small store: the page's ⋯
   (pagekinds PageMore) reads it with no React of Tico's. */

export interface Beat {
  /** The element it points at: the first match on the page; a beat whose element is not there is skipped. */
  sel: string;
  label: string;
}

export interface IntroChip {
  label: string;
  action: string;
  input: Record<string, unknown>;
}

export interface Intro {
  name: string;
  line: string;
  beats?: readonly Beat[];
  chips?: readonly IntroChip[];
  /** After the beats, the page's owner agent proposes one real addition (once, with a connected plan). */
  owner?: boolean;
}

const AGENT_BAR: Beat = { sel: '.pane.chat-2', label: 'Talk to its agent' };

export const INTROS: Readonly<Record<string, Intro>> = {
  tasks: {
    name: 'Tasks',
    line: 'Everything your agents made, in order of what moves the goal.',
    beats: [
      { sel: '.col-flow, .nav-flow', label: 'Flow: one by one' },
      { sel: '[data-target="tasks:new"]', label: 'Ask for something new' },
    ],
  },
  strategy: { name: 'Strategy', line: 'The strategy is the top of everything. Change it and the work changes.', beats: [AGENT_BAR], owner: true },
  growth: {
    name: 'Growth',
    line: 'Every section is an agent and a page.',
    beats: [AGENT_BAR, { sel: '.pane.page', label: 'Or work on its page' }],
    owner: true,
  },
  products: { name: 'Products', line: 'What you sell, watched by an agent.', beats: [AGENT_BAR], owner: true },
  risk: { name: 'Risk', line: 'What could stop you winning, found before it bites.', beats: [AGENT_BAR], owner: true },
  listening: { name: 'Listening', line: 'What customers say everywhere, sorted into themes.', beats: [AGENT_BAR], owner: true },
  contacts: { name: 'Contacts', line: 'Every customer and relationship gets its own agent that remembers everything.', beats: [AGENT_BAR], owner: true },
  finance: {
    name: 'Finance',
    line: 'Bring in your books. Get a P&L, and give agents budgets.',
    beats: [{ sel: '.page-tabs a[href*="tab=budgets"]', label: 'Agents spend only what you budget' }],
    chips: [{ label: 'Connect bank', action: 'finance.addSource', input: {} }],
  },
  // The constitution is a locked doc at the top of Rules, not a page of its own: its beat is here.
  rules: {
    name: 'Rules',
    line: 'Rules between agents, with you, and with outsiders. Each shows who made it.',
    beats: [{ sel: '[data-target="constitution"]', label: 'Above every rule: agents can’t change it' }],
  },
  routines: { name: 'Routines', line: 'Put repeat work on a schedule.' },
  usage: { name: 'Usage', line: 'Two meters: your plan runs agents; the credit runs rule checks.' },
  tools: {
    name: 'Tools',
    line: 'Connect a tool once. Agents use it, but never see the password.',
    beats: [{ sel: '[data-target^="tool:"]', label: 'Tico holds the keys' }],
  },
  skills: {
    name: 'Skills',
    line: 'Save how your company does a job. Every agent follows it.',
    beats: [{ sel: '[data-target="skills:new"]', label: 'Save one' }],
  },
  agent: {
    name: 'an agent',
    line: 'Coach an agent from its own work. Its page updates as you talk.',
    beats: [{ sel: '.pane.page', label: 'Its goal and its work' }],
    owner: true,
  },
  teammate: { name: 'a teammate', line: 'Hand work to a person in one tap.' },
  team: {
    name: 'Team',
    line: 'Add owners and managers any time. Managers can be people or agents.',
    beats: [{ sel: '[data-target="org:add"]', label: 'Add owners and managers' }],
    chips: [{ label: 'Add teammate', action: 'shell.addTeammate', input: {} }],
  },
  calendar: { name: 'Calendar', line: 'Meetings, launches and deadlines land here. Agents add them as they plan.' },
  files: { name: 'Files', line: 'Drop anything here. Every agent can read it.' },
  docs: { name: 'Docs', line: 'Agents write plans and reports here. You can too.' },
  threads: { name: 'Threads', line: 'Every conversation with people and agents, in one place.' },
  settings: { name: 'Settings', line: 'Company settings: people, agents, plans, billing. Your own settings are under your name.' },
};

const COMPANY = new Set(['strategy', 'growth', 'products', 'risk', 'listening', 'finance', 'rules', 'routines', 'usage']);
const LIBRARY = new Set(['contacts', 'tools', 'skills', 'files', 'docs']);

/** The intro an address has: an INTROS key, `who` for a teammate's page (agent or person, from the directory), or null. */
export function introKey(path: string): string | null {
  const [top, a, b] = path.replace(/^\/next(?=\/|$)/, '').split('/').filter(Boolean);
  switch (top) {
    case undefined:
      return 'tasks';
    case 'tasks':
      // the board, any pipeline's; a task or a pipeline's editor is not
      return b ? null : 'tasks';
    case 'company':
      if (a === 'strategy' && b === 'org') return 'team';
      return a && !b && COMPANY.has(a) ? a : null;
    case 'library':
      return !a ? 'docs' : !b && LIBRARY.has(a) ? a : null;
    case 'calendar':
    case 'threads':
    case 'settings':
      return a ? null : top;
    case 'team':
      return a && !b ? 'who' : null;
    default:
      return null;
  }
}

/** Whose page `/team/<who>` is: the address's part, decoded. */
export function teamWho(path: string): string | undefined {
  const m = /^(?:\/next)?\/team\/([^/]+)\/?$/.exec(path);
  if (!m) return undefined;
  try {
    return decodeURIComponent(m[1]!);
  } catch {
    return m[1];
  }
}

/** What a person types to Tico to see the open page's intro again: answered here, with nothing sent. */
export const isShowMe = (text: string) => /^\s*(please\s+)?show me how (this|it) works\s*[.!?]*\s*$/i.test(text);

/* ---------------------------------------------------------------- what is playing */

export interface Playing {
  key: string;
  /** When it started: the chat message's time and id. */
  at: string;
  replay: boolean;
  /** Where the line shows: in Tico's chat, or a card over the page (a phone, or Tico off). */
  float: boolean;
  /** The spotlight on now, or -1. `solo`: one beat pressed again in the card, then none. */
  beat: number;
  solo: boolean;
}

/** The first-visit starter waiting for the beats to end: one ask from the page's owner (Intro.tsx). */
export interface Proposal {
  key: string;
  /** The play it came with. */
  at: string;
  path: string;
  /** The agent whose own page it is (an agent's page); else the page's owner. */
  agent?: string;
}

let playing: Playing | null = null;
let proposal: Proposal | null = null;
let replays = 0;
const heard = new Set<() => void>();
const tell = () => {
  for (const fn of heard) fn();
};

export const subscribe = (fn: () => void) => {
  heard.add(fn);
  return () => heard.delete(fn);
};
export const nowPlaying = () => playing;
export const nowProposal = () => proposal;

export function propose(next: Proposal | null): void {
  proposal = next;
  tell();
}
export const replayCount = () => replays;

export function play(key: string, replay: boolean, float: boolean): void {
  const beats = INTROS[key]?.beats?.length ?? 0;
  playing = { key, at: new Date().toISOString(), replay, float, beat: beats && !isPhone() ? 0 : -1, solo: false };
  tell();
}

export function stop(): void {
  if (!playing) return;
  playing = null;
  tell();
}

/** The next spotlight, or none after the last (or after a beat pressed alone). */
export function nextBeat(): void {
  if (!playing) return;
  const n = INTROS[playing.key]?.beats?.length ?? 0;
  const beat = playing.solo || playing.beat + 1 >= n ? -1 : playing.beat + 1;
  playing = { ...playing, beat, solo: false };
  tell();
}

/** One beat again, pressed in the card. */
export function spot(beat: number): void {
  if (!playing || isPhone()) return;
  playing = { ...playing, beat, solo: true };
  tell();
}

/** "Show me how this works": the open page's intro again. False when this page has none. */
export function askReplay(path = window.location.pathname): boolean {
  if (!introKey(path)) return false;
  replays += 1;
  tell();
  return true;
}

/** The phone's cards (shell Frame sets the layout on the root before the first paint): the card, no spotlights. */
export const isPhone = () => typeof document !== 'undefined' && document.documentElement.dataset.layout === 'cards';
