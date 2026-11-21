import { usePref } from '@/prefs';

/* Which page an address is (master plan P5): its key and name for `from_page` on every message written there
   (backend/page_owners.py check_from), and who answers in the shell's Agent bar:
   - 'page': a company page, owned by backend/page_owners.py PAGES[page] (an item's own owner first, then the page's,
     then the General Manager);
   - 'tico': a personal page (Calendar, Mail, Needs you, Learned, Account, Flow, your threads): its chat is Tico itself;
   - null: the page is a chat already (a teammate's page, a thread, a task, a call) or not a page (the style guide).
   Every route maps here; web/src/shell/pageOwner.test.ts walks them all. */

export interface PageAt {
  /** The route key: a PAGES key when `owner` is 'page'. */
  page: string;
  label: string;
  owner: 'page' | 'tico' | null;
  /** What is open there (a doc, a pipeline, a task); its name is the page's own (its h1). */
  item?: { kind: string; id: string };
}

const decode = (s: string) => {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
};

const page = (key: string, label: string, item?: PageAt['item']): PageAt => ({ page: key, label, owner: 'page', ...(item && { item }) });
const tico = (key: string, label: string): PageAt => ({ page: key, label, owner: 'tico' });
const chat = (key: string, label: string, item?: PageAt['item']): PageAt => ({ page: key, label, owner: null, ...(item && { item }) });

const COMPANY: Record<string, [string, string]> = {
  strategy: ['strategy', 'Strategy'],
  products: ['products', 'Products'],
  finance: ['finance', 'Finance'],
  growth: ['growth', 'Growth'],
  risk: ['risk', 'Risk'],
  rules: ['rules', 'Rules'],
  routines: ['automations', 'Routines'],
  listening: ['listening', 'Listening'],
  usage: ['usage', 'Usage'],
};
const LIBRARY: Record<string, [string, string]> = {
  docs: ['docs', 'Docs'],
  files: ['docs', 'Files'],
  contacts: ['contacts', 'Contacts'],
  skills: ['skills', 'Skills'],
  tools: ['tools', 'Tools'],
};
const SETTINGS: Record<string, [string, string]> = { computers: ['computers', 'Computers'], changelog: ['changelog', 'Changelog'] };
/** A map's own entry for an address part: never one the object inherits ('constructor', '__proto__'). */
const own = (map: Record<string, [string, string]>, key: string | undefined) => (key !== undefined && Object.hasOwn(map, key) ? map[key] : undefined);

/** The page an address is; `search` (the address's search) names a file open over it (`?file=`, files.view), which is
    then what is open there. */
export function pageAt(path: string, search = ''): PageAt {
  const at = mapped(path);
  const file = new URLSearchParams(search).get('file');
  return file ? { ...at, item: { kind: 'file', id: file.slice(0, 200) } } : at;
}

function mapped(path: string): PageAt {
  const [top, a, b] = path.replace(/^\/next(?=\/|$)/, '').split('/').filter(Boolean).map(decode);
  switch (top) {
    case undefined:
      return page('tasks', 'Tasks');
    case 'tasks':
      if (!a || a === 'all') return b && b !== 'edit' ? chat('task', 'Task', { kind: 'task', id: b }) : page('tasks', 'Tasks');
      if (b && b !== 'edit') return chat('task', 'Task', { kind: 'task', id: b });
      return page('tasks', 'Tasks', { kind: 'pipeline', id: a });
    case 'task':
      return a ? chat('task', 'Task', { kind: 'task', id: a }) : page('tasks', 'Tasks');
    case 'flow':
      return a ? chat('task', 'Task', { kind: 'task', id: a }) : tico('flow', 'Needs you');
    case 'roadmap':
      return page('roadmap', 'Roadmap');
    case 'updates':
      return page('updates', 'Updates');
    case 'company': {
      if (a === 'strategy' && b === 'goals') return page('goals', 'Goals');
      const [key, label] = own(COMPANY, a) ?? ['company', 'Company'];
      return page(key, label);
    }
    case 'library': {
      if (a && !own(LIBRARY, a)) return page('docs', 'Docs', { kind: 'doc', id: a });
      const [key, label] = own(LIBRARY, a ?? 'docs')!;
      return page(key, label);
    }
    case 'tags':
      return page('tags', 'Tags', a ? { kind: 'tag', id: a } : undefined);
    case 'settings': {
      if (a === 'account') return tico('account', 'Account');
      if (a === 'style-guide') return chat('style-guide', 'Style guide');
      const [key, label] = own(SETTINGS, a) ?? ['settings', 'Settings'];
      return page(key, label);
    }
    case 'calendar':
      return tico('calendar', 'Calendar');
    case 'learned':
      return tico('learned', 'Learned');
    case 'threads':
      if (a === 'messaging') return page('messaging', 'Messaging');
      if (a === 'mail') return tico('mail', 'Mail');
      if (!a || a === 'catch-up') return tico('threads', 'Threads');
      return chat('thread', 'Thread', { kind: 'thread', id: a });
    case 'team':
      return chat('teammate', 'Teammate', a ? { kind: 'teammate', id: a } : undefined);
    case 'call':
      return chat('call', 'Call', a ? { kind: 'call', id: a } : undefined);
    default:
      // dev pages, and an address no route has (its page says Not found)
      return chat(top.slice(0, 40), 'Page');
  }
}

/** The page context every message carries (`from_page`): the item's name is the page's own name, its h1. */
export interface FromPage {
  page: string;
  label: string;
  item?: { kind: string; id: string; name: string };
}
export function fromPage(at: PageAt, name: string): FromPage {
  const item = at.item && { ...at.item, name: (name || at.item.id).slice(0, 200) };
  return { page: at.page, label: at.label, ...(item && { item }) };
}

/* The Agent bar's fold, per page per person, on this device: `{page: true}` folded by hand, `false` opened by hand.
   A page with no hand choice is open on a wide window (C.BAR_OPEN_AT and up) and folded under it and on a phone. An
   ask from the owner that waits on you opens it until you fold it again. */
const KEY = 'owner-bar.v1';
type Folds = Record<string, Record<string, boolean>>;

export function useBarFold(me: string | undefined, key: string): [boolean | undefined, (folded: boolean) => void] {
  const [all, setAll] = usePref<Folds>(KEY, {});
  const mine = (me && all && typeof all === 'object' ? all[me] : undefined) ?? {};
  const set = (folded: boolean) => {
    if (!me) return;
    setAll({ ...all, [me]: { ...mine, [key]: folded } });
  };
  return [typeof mine[key] === 'boolean' ? mine[key] : undefined, set];
}

/** Folded or open now: an ask waiting opens it; else the person's choice for this page; else open on a wide window. */
export const barFolded = (hand: boolean | undefined, ask: boolean, wide: boolean): boolean => (ask ? false : (hand ?? !wide));
