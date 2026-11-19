// The one adapter place (architecture.md 7): every server answer a component would find awkward becomes the app's
// shape here, once. The API keeps `bot`; the app says agent. Live patchers call the same functions on event payloads.
import type { Me } from '@/api/schema';

/** Who is signed in. `person` and `owner` are people; `agent` is an agent's own token. */
export interface SignedIn {
  id: string;
  email: string;
  role: 'person' | 'owner' | 'agent' | 'other';
  /** An agent caller's slug; null for a person. */
  slug: string | null;
}

const ROLES: Record<string, SignedIn['role']> = { human: 'person', owner: 'owner', bot: 'agent' };

export function adaptMe(me: Me): SignedIn {
  return { id: me.actor, email: me.email, role: ROLES[me.role] ?? 'other', slug: me.slug ?? null };
}
