import { Link, type LinkProps } from '@tanstack/react-router';
import type { ReactNode } from 'react';
import { useActor } from '@/data';
import { Avatar, type AvatarSize } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';

/* Small parts the task view repeats: a face by actor id, a link to an app address. */

/** A person's or an agent's face, by any id form the directory answers. */
export function Face({ id, size = 22, decorative }: { id: string | null | undefined; size?: AvatarSize; decorative?: boolean }) {
  const a = useActor(id);
  if (!id) return null;
  return <Avatar kind={a?.kind ?? (id.startsWith('bot:') ? 'agent' : 'person')} name={a?.name ?? id.slice(id.indexOf(':') + 1)} hue={a?.hue} size={size} live={a?.working} decorative={decorative} />;
}

/** A name by actor id, for text beside a face. */
export function NameOf({ id }: { id: string | null | undefined }) {
  const a = useActor(id);
  return <>{a?.name ?? (id ? id.slice(id.indexOf(':') + 1) : '')}</>;
}

/** The met mark, or the hollow ring of not yet. */
export function Mark({ met }: { met: boolean }) {
  return met ? (
    <span className="dt-mark" title="Met">
      <Icon name="check" />
    </span>
  ) : (
    <span className="dt-mark" title="Not yet">
      <i className="hollow" />
    </span>
  );
}

/** An app link built from a resolved address (the router's own types want a route id). */
export function Go({ to, search, className, children, ...rest }: { to: string; search?: Record<string, string | undefined>; className?: string; children: ReactNode; title?: string; 'aria-label'?: string }) {
  return (
    <Link to={to as LinkProps['to']} search={(search ?? {}) as never} className={className} {...rest}>
      {children}
    </Link>
  );
}
