import type { CSSProperties, ReactNode } from 'react';
import { capCount } from '@/kit/Count';
import './Avatar.css';

// Tico's own face is the Dot in its `.av-dot` frame; the kit may not import the Dot, so the caller passes it in `dot`.
export type AvatarKind = 'person' | 'agent';
export type AvatarSize = 16 | 22 | 28 | 36;

export type AvatarProps = {
  kind: AvatarKind;
  name: string;
  /** The teammate's hue (0-360); the tint comes from avatar.css. */
  hue?: number;
  /** Two letters; taken from the name when left out. A 16px avatar shows none. */
  initials?: string;
  size?: AvatarSize;
  /** Working now: the ink ring travels round it. */
  live?: boolean;
  /** Beside the name already (a menu row, a link labelled with it): hidden from screen readers and no hover title. */
  decorative?: boolean;
  /** Tico's face: a still `<Dot>` from `@/tico/dot`, drawn in the `.av-dot` frame instead of initials. */
  dot?: ReactNode;
  className?: string;
};

function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? `${words[0]![0]}${words[1]![0]}` : (words[0] ?? '').slice(0, 2);
  return letters.toUpperCase();
}

/** A person is a circle, an agent a rounded square (parts-kit.js av()). */
export function Avatar({ kind, name, hue = 0, initials, size = 22, live, decorative, dot, className }: AvatarProps) {
  const label = live ? `${name}, working now` : name;
  const a11y = decorative ? { 'aria-hidden': true as const } : { role: 'img', 'aria-label': label, title: label };
  if (dot)
    return (
      <span className={className ? `av-dot ${className}` : 'av-dot'} style={{ '--a': `${size}px` } as CSSProperties} {...a11y}>
        {dot}
      </span>
    );
  const cls = ['av', `av-${size}`, kind === 'person' ? 'is-person' : 'is-agent', live && 'is-live', className]
    .filter(Boolean)
    .join(' ');
  return (
    <span className={cls} style={{ '--h': hue } as CSSProperties} {...a11y}>
      {size > 16 ? (initials ?? initialsOf(name)) : null}
    </span>
  );
}

export type FacesProps = {
  people: readonly Omit<AvatarProps, 'size'>[];
  size?: AvatarSize;
  /** How many faces show before the rest become "+N". */
  max?: number;
};

/** Overlapping faces, then a count of the rest in the one count style. */
export function Faces({ people, size = 22, max = 4 }: FacesProps) {
  const shown = people.length > max ? people.slice(0, max - 1) : people;
  const rest = people.length - shown.length;
  return (
    <span className="faces">
      {shown.map((p, i) => (
        <Avatar key={`${p.name}:${i}`} {...p} size={size} />
      ))}
      {rest > 0 && (
        <span className="count faces-n" aria-label={`${rest} more`}>
          +{capCount(rest)}
        </span>
      )}
    </span>
  );
}
