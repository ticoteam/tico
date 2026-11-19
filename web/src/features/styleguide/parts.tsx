import type { ReactNode } from 'react';
import { Avatar, type AvatarSize } from '@/kit/Avatar';

/* The guide's own small parts: an area with its rule, a specimen with its name, a list of named states. Everything they
   hold is the app's. */

export function Area({ id, title, rule, children }: { id: string; title: string; rule: string; children: ReactNode }) {
  return (
    <section className="sg-sec" id={`sg-${id}`} aria-labelledby={`sg-${id}-h`}>
      <header className="sg-h">
        <h2 id={`sg-${id}-h`} className="t-hall">
          {title}
        </h2>
        <p>{rule}</p>
      </header>
      {children}
    </section>
  );
}

/** A part, then its name. */
export function Sp({ name, children }: { name: ReactNode; children: ReactNode }) {
  return (
    <figure className="sg-sp">
      {children}
      <figcaption>{name}</figcaption>
    </figure>
  );
}

/** Specimens side by side. */
export function Specimens({ children, top = false }: { children: ReactNode; top?: boolean }) {
  return <div className={top ? 'sg-row is-top' : 'sg-row'}>{children}</div>;
}

/** Each state's name, then the part at the page's width. */
export function Named({ rows, className }: { rows: readonly (readonly [ReactNode, ReactNode])[]; className?: string }) {
  return (
    <div className={className ? `sg-list ${className}` : 'sg-list'}>
      {rows.map(([name, part], i) => (
        <Pair key={i} name={name}>
          {part}
        </Pair>
      ))}
    </div>
  );
}

function Pair({ name, children }: { name: ReactNode; children: ReactNode }) {
  return (
    <>
      <span className="sg-cap">{name}</span>
      <div className="sg-cell">{children}</div>
    </>
  );
}

/** A token's value, read from the stylesheet the app ships (filled by `fillTokens`). */
export function Tok({ name, as = 'raw' }: { name: string; as?: 'hex' | 'px' | 'raw' }) {
  return <span data-token={name} data-fmt={as} />;
}

/** Writes every token value and type step under `root` from the computed styles, in the theme each sits in. */
export function fillTokens(root: HTMLElement) {
  root.querySelectorAll<HTMLElement>('[data-token]').forEach((el) => {
    const scope = el.closest<HTMLElement>('[data-theme]') ?? document.documentElement;
    const v = getComputedStyle(scope).getPropertyValue(el.dataset.token ?? '').trim();
    el.textContent = !v ? '' : el.dataset.fmt === 'px' ? String(parseFloat(v)) : el.dataset.fmt === 'hex' ? v.toUpperCase() : v;
  });
  root.querySelectorAll<HTMLElement>('.ty').forEach((row) => {
    const s = row.querySelector<HTMLElement>('.ty-s');
    const out = row.querySelector<HTMLElement>('.ty-v');
    if (!s || !out) return;
    const cs = getComputedStyle(s);
    const size = parseFloat(cs.fontSize);
    const line = parseFloat(cs.lineHeight);
    out.textContent = Number.isFinite(size) && Number.isFinite(line) ? `${size} / ${Math.round(line)}, ${cs.fontWeight}` : '';
  });
}

/* Made-up teammates for the specimens. */
type Mate = { kind: 'person' | 'agent'; name: string; initials: string; hue: number };
export const MATES = {
  maya: { kind: 'person', name: 'Maya Okafor', initials: 'MO', hue: 250 },
  priya: { kind: 'person', name: 'Priya Nair', initials: 'PN', hue: 30 },
  jonas: { kind: 'person', name: 'Jonas Berg', initials: 'JB', hue: 150 },
  qa: { kind: 'agent', name: 'QA Owner', initials: 'QA', hue: 320 },
  b1: { kind: 'agent', name: 'Builder One', initials: 'B1', hue: 200 },
  b2: { kind: 'agent', name: 'Builder Two', initials: 'B2', hue: 230 },
  rev: { kind: 'agent', name: 'Reviewer', initials: 'RV', hue: 10 },
  em: { kind: 'agent', name: 'Engineering Manager', initials: 'EM', hue: 280 },
} satisfies Record<string, Mate>;

export function Face({ who, size = 22, live }: { who: keyof typeof MATES; size?: AvatarSize; live?: boolean }) {
  const m = MATES[who];
  return <Avatar kind={m.kind} name={m.name} initials={m.initials} hue={m.hue} size={size} live={live} />;
}

const NOW = Date.now();
/** A moment `min` minutes before the page loaded. */
export const ago = (min: number) => new Date(NOW - min * 60_000);
