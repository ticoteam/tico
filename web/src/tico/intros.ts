/* Each page's first-visit intro (plan: setup walkthrough v4, Part 3): one line in Tico's chat, up to three spotlights on
   the page's own elements, and a chip only where it is an action the app already has. It plays once per person per
   page (preference intros.seen) and again on Tico's "Show me how this works" chip.
   Plain data and a small store that Intro.tsx, Tico's sheet and its chips read. */

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
  /** The card offers to ask the page's owner agent for one real addition: sent only on that press. */
  owner?: boolean;
}

/* The Agent bar open beside the page, or its tucked edge when it is folded. */
const AGENT_BAR: Beat = { sel: '.pane.chat-2, .shell-edge[data-edge="chat"]', label: 'Talk to its agent' };

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
  },
  teammate: { name: 'a teammate', line: 'Hand work to a person in one tap.' },
  team: {
    name: 'Team',
    line: 'Add owners and managers any time. Managers can be people or agents.',
    beats: [{ sel: '[data-target="org:add"]', label: 'Add owners and managers' }],
    chips: [{ label: 'Add teammate', action: 'shell.addTeammate', input: {} }],
  },
  company: { name: 'Company', line: 'Your company at a glance. Each section has its own agent.' },
  pipeline: { name: 'the pipeline editor', line: 'Shape how work moves: its stages, and who does each.' },
  'needs-you': { name: 'Needs you', line: 'Everything that waits on you, in one place.' },
  roadmap: { name: 'Roadmap', line: 'What comes now, next and later, and what shipped.' },
  updates: { name: 'Updates', line: 'What changed across the company.' },
  tags: { name: 'Tags', line: 'Tags gather tasks from every pipeline.' },
  learned: { name: 'Learned', line: 'What Tico has learned about how you work.' },
  calendar: { name: 'Calendar', line: 'Meetings, launches and deadlines land here. Agents add them as they plan.' },
  files: { name: 'Files', line: 'Files attach to a task, and whoever can see the task can see them.' },
  docs: { name: 'Docs', line: 'Agents write plans and reports here. You can too.' },
  threads: { name: 'Threads', line: 'Every conversation with people and agents, in one place.' },
  settings: { name: 'Settings', line: 'Company settings: people, agents, plans, billing. Your own settings are under your name.' },
};

const COMPANY = new Set(['strategy', 'growth', 'products', 'risk', 'listening', 'finance', 'rules', 'routines', 'usage']);
const LIBRARY = new Set(['contacts', 'tools', 'skills', 'files', 'docs']);
const THREAD_TABS = new Set(['catch-up', 'mail', 'messaging']);
const SINGLE = new Set(['calendar', 'needs-you', 'roadmap', 'learned']);

/** The intro an address has: an INTROS key, `who` for a teammate's page (agent or person, from the directory), or
    null (a task, a thread, a doc, a call, and `/`, which only passes on to Tasks). A page's tabs are its own. */
export function introKey(path: string): string | null {
  const [top, a, b] = path.replace(/^\/next(?=\/|$)/, '').split('/').filter(Boolean);
  if (top === undefined) return null;
  if (SINGLE.has(top)) return a ? null : top;
  switch (top) {
    case 'tasks':
      // the board, any pipeline's, and a pipeline's editor; a task is a chat
      return b === 'edit' ? 'pipeline' : b ? null : 'tasks';
    case 'company':
      if (!a) return 'company';
      if (a === 'strategy' && b) return b === 'org' ? 'team' : 'strategy';
      return COMPANY.has(a) ? a : null;
    case 'library':
      return !a ? 'docs' : !b && LIBRARY.has(a) ? a : null;
    case 'threads':
      return !a || THREAD_TABS.has(a) ? 'threads' : null;
    case 'settings':
      return a === 'style-guide' ? null : 'settings';
    case 'updates':
    case 'tags':
      return top;
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

/* ---------------------------------------------------------------- what is playing */

/** The intro showing now. Replaced only when it starts, moves between Tico's chat and a card, or goes: the
    spotlight's beat is its own snapshot, so a beat moving never draws Tico's chat again. */
export interface Shown {
  key: string;
  /** When it started: the chat message's time and id. */
  at: string;
  replay: boolean;
  where: Where;
}

/** Where the card shows: in Tico's chat while it is open; over the page's foot while nothing covers the page (Tico
    folded or off, a phone's page up); nowhere for now while a phone's other card (the agent's chat, the menu) is up. */
export type Where = 'chat' | 'float' | 'none';

/** The spotlight on now (-1: none). `solo`: one beat pressed again in the card, then none. */
export interface BeatNow {
  n: number;
  solo: boolean;
}

const NO_BEAT: BeatNow = { n: -1, solo: false };
let shown: Shown | null = null;
let beat: BeatNow = NO_BEAT;
let here: string | null = null;
let replays = 0;
const heard = new Set<() => void>();
const tell = () => {
  for (const fn of heard) fn();
};

export const subscribe = (fn: () => void) => {
  heard.add(fn);
  return () => heard.delete(fn);
};
export const nowShown = () => shown;
export const nowBeat = () => beat;
export const hereKey = () => here;
export const replayCount = () => replays;

export function play(key: string, replay: boolean, where: Where): void {
  shown = { key, at: new Date().toISOString(), replay, where };
  beat = INTROS[key]?.beats?.length && !isPhone() ? { n: 0, solo: false } : NO_BEAT;
  tell();
}

export function stop(): void {
  if (!shown) return;
  shown = null;
  beat = NO_BEAT;
  tell();
}

/** The card moves between Tico's chat and the page's foot as Tico's sheet (or a phone's card) opens and folds. */
export function setWhere(where: Where): void {
  if (!shown || shown.where === where) return;
  shown = { ...shown, where };
  tell();
}

/** The next spotlight, or none after the last (or after a beat pressed alone). */
export function nextBeat(): void {
  if (!shown) return;
  const n = INTROS[shown.key]?.beats?.length ?? 0;
  beat = beat.solo || beat.n + 1 >= n ? NO_BEAT : { n: beat.n + 1, solo: false };
  tell();
}

/** One beat again, pressed in the card. */
export function spot(n: number): void {
  if (!shown || isPhone()) return;
  beat = { n, solo: true };
  tell();
}

/** The open page's intro for this person (Intro.tsx resolves a teammate's page from the directory). */
export function setHere(key: string | null): void {
  if (here === key) return;
  here = key;
  tell();
}

/** "Show me how this works": the open page's intro again. False when this page has none. */
export function askReplay(): boolean {
  if (!here) return false;
  replays += 1;
  tell();
  return true;
}

/** The phone's cards (shell Frame sets the layout on the root before the first paint): the card, no spotlights. */
export const isPhone = () => typeof document !== 'undefined' && document.documentElement.dataset.layout === 'cards';

/** What covers the page now: a phone's card up ('phone:none|tico|menu|chat', PhoneCards `data-card`), or on a
    computer whether Tico's sheet is drawn and not tucked to its edge ('open' or 'shut'). */
export function sheetsNow(): string {
  if (isPhone()) return `phone:${document.querySelector<HTMLElement>('.phone')?.dataset.card ?? 'none'}`;
  return document.querySelector('[data-sheet="tico"]:not([data-shut])') ? 'open' : 'shut';
}

/** Where the card goes, from what covers the page and whether Tico's chat can show it (on, with a room). */
export function whereFor(sheets: string, chat: boolean): Where {
  if (sheets === 'phone:none') return 'float';
  if (sheets === 'phone:tico' || sheets === 'open') return chat ? 'chat' : sheets === 'open' ? 'float' : 'none';
  return sheets === 'shut' ? 'float' : 'none';
}
