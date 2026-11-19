import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useEffect, useState, type ReactNode } from 'react';
import { run } from '@/actions';
import { useActor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { Search } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { OwnerButton as KitOwnerButton } from '@/kit/OwnerButton';
import { toast } from '@/kit/Toast';
import { pageOwnersQuery } from './queries';

/** A teammate's face from the directory. `bot:<slug>`, `human:<id>`, a bare slug or id all resolve. */
export function Face({ actor, name, size = 22 }: { actor: string | null | undefined; name?: string | null; size?: 22 | 28 }) {
  const who = useActor(actor);
  if (!actor && !name) return null;
  const kind = who?.kind ?? (actor?.startsWith('bot:') ? 'agent' : 'person');
  return <Avatar kind={kind} name={who?.name ?? name ?? 'Teammate'} hue={who?.hue} live={who?.working} size={size} />;
}

/** A face and the name beside it (`.doc-owner`, a fact's value). */
export function Who({ actor, name, className = 'doc-owner' }: { actor: string | null | undefined; name?: string | null; className?: string }) {
  const who = useActor(actor);
  const shown = who?.name ?? name;
  if (!shown) return <span className={className} />;
  return (
    <span className={className}>
      <Face actor={actor} name={shown} />
      <span>{shown}</span>
    </span>
  );
}

/**
 * The page owner's face in the head (backend/page_owners.py: docs and contacts default to the Librarian, skills to
 * BotOps, tools to the Tools owner). It opens their chat beside the page; nothing when the page has no owner.
 */
export function OwnerButton({ page }: { page: 'docs' | 'skills' | 'contacts' | 'tools' }) {
  const bot = useQuery(pageOwnersQuery).data?.[page]?.bot ?? null;
  return <ChatWith actor={bot && !bot.includes(':') ? `bot:${bot}` : bot} />;
}

/** A teammate's face that opens their chat beside the page (a file's maker, a page's owner). */
export function ChatWith({ actor }: { actor: string | null }) {
  const who = useActor(actor);
  // `chat` is the shell's, in no route's search schema: built as an address, it keeps the page's own filters.
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      if (actor) q.set('chat', actor);
      return `${l.pathname}?${q.toString()}`;
    },
  });
  if (!actor) return null;
  const kind = who?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person');
  return <KitOwnerButton name={who?.name ?? 'the owner'} kind={kind} hue={who?.hue} live={who?.working} href={href} />;
}

/** The head's search: a button until pressed, then the field. Typing narrows the page after a short pause. */
export function Find({ label, value, onFind }: { label: string; value: string | undefined; onFind: (q: string | undefined) => void }) {
  const [open, setOpen] = useState(!!value);
  const [words, setWords] = useState(value ?? '');
  // Cleared or set from elsewhere (Tico, Back): the field follows the address.
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setWords(value ?? '');
  }
  useEffect(() => {
    if (words.trim() === (value ?? '')) return;
    const t = setTimeout(() => onFind(words.trim() || undefined), 250);
    return () => clearTimeout(t);
  }, [words, value, onFind]);
  if (!open) return <IconButton icon="search" label="Search" onClick={() => setOpen(true)} />;
  return <Search label={label} value={words} onValueChange={setWords} placeholder="Search" autoFocus />;
}

/** Run an action from a control and say why when it is refused (`run` itself shows nothing). */
export async function press(id: string, input: unknown, failed = 'That did not work'): Promise<boolean> {
  const r = await run(id, input, { via: 'click' });
  if (!r.ok) toast({ text: r.error === 'not_allowed' ? 'You cannot do that here' : failed, kind: 'failed' });
  return r.ok;
}

/* The mock's kind glyphs (tools.js G, files.js KIND): one glyph in ink, never letters. */
const svg = (d: ReactNode) => (
  <svg className="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {d}
  </svg>
);
const GLYPH: Record<string, ReactNode> = {
  api: svg(
    <>
      <path d="M9 4.5c-2 0-2.8.9-2.8 2.6v2.3c0 1.3-.7 2.2-2.2 2.6 1.5.4 2.2 1.3 2.2 2.6v2.3c0 1.7.8 2.6 2.8 2.6" />
      <path d="M15 4.5c2 0 2.8.9 2.8 2.6v2.3c0 1.3.7 2.2 2.2 2.6-1.5.4-2.2 1.3-2.2 2.6v2.3c0 1.7-.8 2.6-2.8 2.6" />
    </>,
  ),
  mcp: svg(
    <>
      <path d="M9 3.5v4M15 3.5v4" />
      <path d="M6.5 7.5h11V11a5.5 5.5 0 0 1-11 0z" />
      <path d="M12 16.5v4" />
    </>,
  ),
  site: svg(
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17" />
      <path d="M12 3.5c2.6 2.4 3.9 5.2 3.9 8.5s-1.3 6.1-3.9 8.5c-2.6-2.4-3.9-5.2-3.9-8.5s1.3-6.1 3.9-8.5z" />
    </>,
  ),
  email: svg(
    <>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2.5" />
      <path d="M4.5 8.5l7.5 5 7.5-5" />
    </>,
  ),
  at: svg(
    <>
      <circle cx="12" cy="12" r="3.5" />
      <path d="M15.5 8.5v4.6c0 1.5.9 2.4 2.2 2.4 1.6 0 2.8-1.4 2.8-3.5a8.5 8.5 0 1 0-3.4 6.8" />
    </>,
  ),
  mail: svg(
    <>
      <path d="M3.5 18.5V11a4 4 0 0 1 8 0v7.5" />
      <path d="M7.5 7h9a4 4 0 0 1 4 4v7.5h-17" />
      <path d="M15.5 11.5v-7h3" />
    </>,
  ),
  hook: svg(
    <>
      <path d="M13.5 4.5h6v6" />
      <path d="M19.5 4.5l-8 8" />
      <path d="M10 6.5H6.5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V14" />
    </>,
  ),
  key: svg(
    <>
      <circle cx="8" cy="15.5" r="4" />
      <path d="M11 12.5l8.5-8.5" />
      <path d="M16.5 7l2.5 2.5" />
    </>,
  ),
  cli: svg(
    <>
      <rect x="3.5" y="5" width="17" height="14" rx="2.5" />
      <path d="M7.5 10l3 2.5-3 2.5" />
      <path d="M13 15h3.5" />
    </>,
  ),
};

/** The server's tool kinds onto the mock's glyphs. */
const TOOL_GLYPH: Record<string, string> = {
  api: 'api',
  website: 'site',
  mailbox: 'email',
  number: 'phone',
  address: 'mail',
  slack: 'at',
  github: 'cli',
  meetings: 'site',
  listening: 'email',
  webhook: 'hook',
  declared: 'key',
};

/** What kind of tool this is, as one glyph in the small tile (tools.css `.kind`). */
export function KindTile({ kind, mcp, big }: { kind: string; mcp?: boolean; big?: boolean }) {
  const g = mcp ? 'mcp' : (TOOL_GLYPH[kind] ?? 'key');
  return (
    <span className={big ? 'kind is-28' : 'kind'} aria-hidden="true">
      {g === 'phone' ? <Icon name="call" /> : GLYPH[g]}
    </span>
  );
}

/** A tool kind's glyph alone, for a menu row (tools.js addMenu). */
export function ToolGlyph({ kind }: { kind: string }) {
  return kind === 'mcp' ? GLYPH.mcp : GLYPH[TOOL_GLYPH[kind] ?? 'key'];
}

/** A file's or source's kind as a lead glyph. */
export function KindGlyph({ kind }: { kind: string }) {
  if (kind === 'image' || kind === 'media') return <Icon name="square" />;
  if (kind === 'data') return GLYPH.cli;
  if (kind === 'page') return <Icon name="library" />;
  if (kind === 'site' || kind === 'website') return GLYPH.site;
  if (kind === 'repo' || kind === 'github') return GLYPH.cli;
  return <Icon name="file" />;
}

/** Tico holds the key: a small lock, never a badge (tools.css `.held`). */
export function Held() {
  return (
    <span className="held" title="Tico holds the key">
      <Icon name="lock" />
    </span>
  );
}

/** A section's quiet "N more" row (tools.css `.stage-more`). */
export function More({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className="stage-more" onClick={onClick}>
      {children}
    </button>
  );
}

/** "Tue, Nov 17, 4:20 PM" */
export const when = (at: string | null | undefined) =>
  at ? new Date(at).toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';

/** "1.2 MB" */
export function size(bytes: number | null | undefined) {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let n = bytes / 1024;
  let u = 0;
  while (n >= 1024 && u < units.length - 1) {
    n /= 1024;
    u++;
  }
  return `${n < 10 ? n.toFixed(1) : Math.round(n)} ${units[u]}`;
}
