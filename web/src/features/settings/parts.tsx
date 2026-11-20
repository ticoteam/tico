import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { useSyncExternalStore, type ReactNode } from 'react';
import { refusal, run } from '@/actions';
import { useActor, useDirectory, type Actor, type Directory } from '@/data';
import { Icon, type IconName } from '@/kit/Icon';
import { OwnerButton } from '@/kit/OwnerButton';
import { Switch } from '@/kit/Switch';
import { toast } from '@/kit/Toast';
import { chatAddress } from '@/lib';
import { pageOwnersQuery } from './queries';

/* The small parts every section shares, ported from pages/8-settings/settings.js: a line (label left, its control
   right), a switch line, a quiet verb, a mark and a word, a thin meter. Each control saves at once: no Save. */

/** Run an action from a click; a refusal says so in a toast (the write has already been taken back). */
export async function act(id: string, input: unknown): Promise<boolean> {
  const r = await run(id, input, { via: 'click' });
  if (!r.ok) toast(refusal(r, "Couldn't save"));
  return r.ok;
}

const onVisibility = (fn: () => void) => {
  document.addEventListener('visibilitychange', fn);
  return () => document.removeEventListener('visibilitychange', fn);
};

/** Whether this tab is on screen: a timer waits while it is not. */
export function useVisible(): boolean {
  return useSyncExternalStore(onVisibility, () => document.visibilityState !== 'hidden', () => true);
}

/** An actor without its status line: all a settings row draws of them. */
export type Face = Omit<Actor, 'status' | 'working'>;
const face = ({ status: _s, working: _w, ...a }: Actor): Face => a;
const facesOf = (d: Directory) => Object.fromEntries(Object.entries(d.byId).map(([k, a]) => [k, face(a)]));
const everyoneOf = (d: Directory) => d.actors.map(face);
const agentsOf = (d: Directory) => d.actors.filter((a) => a.kind === 'agent').map(face);
const NO_FACES: Record<string, Face> = {};
const NONE: Face[] = [];

/** Everyone, by each id form the directory answers, without status lines: an agent's status event does not redraw
    the reader. Empty until the directory is read. */
export const useFaces = (): Record<string, Face> => useDirectory(facesOf) ?? NO_FACES;

/** Everyone, people first, without status lines. */
export const useEveryone = (): Face[] => useDirectory(everyoneOf) ?? NONE;

/** Every agent, without status lines. */
export const useAgentFaces = (): Face[] => useDirectory(agentsOf) ?? NONE;

/** What a section says to a member when its read is for owners and admins (the kind's no-access body). */
export const NO_ACCESS = { text: 'Owners and admins only.' };

export { isForbidden } from './main';

/** A label and the control that changes it, on the facts grid. */
export function Line({ label, target, children }: { label: string; target?: string; children: ReactNode }) {
  return (
    <div className="fact" data-target={target}>
      <span>{label}</span>
      <span className="fact-v">{children}</span>
    </div>
  );
}

/** A label and a switch at the line's right edge; the whole line presses it. */
export function SwitchLine({
  label,
  checked,
  onChange,
  target,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
  target?: string;
  disabled?: boolean;
}) {
  return (
    <div className="fact set-sw" data-target={target}>
      <Switch label={<span>{label}</span>} checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </div>
  );
}

/** An action that is not a button until you point at it. */
export function Verb({ children, onClick, quiet }: { children: ReactNode; onClick: () => void; quiet?: boolean }) {
  return (
    <button type="button" className={quiet ? 'verb is-quiet' : 'verb'} onClick={onClick}>
      {children}
    </button>
  );
}

/** Something is wrong, said in ink and weight (no red, no amber); fine is the same shape, quiet. */
export function Word({ children, fine, icon }: { children: ReactNode; fine?: boolean; icon?: IconName }) {
  return (
    <span className={fine ? 'word is-fine' : 'word'}>
      <Icon name={icon ?? (fine ? 'check' : 'alert')} />
      {children}
    </span>
  );
}

/** A thin bar for a share: used of a plan, spent of a limit. */
export function Meter({ pct, over }: { pct: number; over?: boolean }) {
  const p = Math.max(0, Math.min(100, pct));
  return (
    <span className={over ? 'meter is-over' : 'meter'} role="img" aria-label={`${Math.round(pct)}%`}>
      <i style={{ width: `${p.toFixed(1)}%` }} />
    </span>
  );
}

/** The foot of a section or an opened row: quiet verbs on a line, what cannot be taken back among them. */
export function Foot({ children, open }: { children: ReactNode; open?: boolean }) {
  return <div className={open ? 'open-foot' : 'foot'}>{children}</div>;
}

/** Dollars: whole above 100, cents below. */
/** Whole dollars, for a table of spend ("$1,284", "$40"; more.js): cents only below a dollar. */
export function dollars(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n > 0 && n < 1) return usd(n);
  return '$' + Math.round(n).toLocaleString('en-US');
}

export function usd(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n > 0 && n < 0.01) return '<$0.01';
  return '$' + n.toLocaleString('en-US', n >= 100 ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Whole micro-dollars to dollars and cents, in integers so a statement never rounds wrong (old app billingCents). */
export function micros(m: number): string {
  const sign = m < 0 ? '-' : '';
  const cents = Math.floor((Math.abs(m) + 5000) / 10000);
  return `${sign}$${Math.floor(cents / 100).toLocaleString('en-US')}.${String(cents % 100).padStart(2, '0')}`;
}

/** The page owner's face in the head (General › Page owners): it opens their chat beside the page. Nothing when the
    page has no owner. The same as Company's (features/company/parts.tsx PageOwner); both belong in the kit. */
export function PageOwner({ page }: { page: string }) {
  const bot = useQuery(pageOwnersQuery).data?.[page]?.bot ?? null;
  const actor = bot && !bot.includes(':') ? `bot:${bot}` : bot;
  const who = useActor(actor);
  // `chat` is the shell's, in no route's search schema: built as an address, it keeps the page's own search.
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      if (actor) q.set('chat', chatAddress(actor, 'agent'));
      return `${l.pathname}?${q.toString()}`;
    },
  });
  if (!actor) return null;
  return <OwnerButton name={who?.name ?? 'the owner'} kind="agent" hue={who?.hue} live={who?.working} href={href} target={`chat:${chatAddress(actor, 'agent')}`} />;
}
