import { fmtWhen } from '@/lib';
import type { Subscription } from './queries';

export function limitReset(s: Subscription) {
  const reset = s.usage?.resets_at;
  return reset && Date.parse(reset) > Date.now() ? fmtWhen(reset) : '';
}

export const needsReconnect = (s: Subscription) => s.status === 'needs_reconnect' || s.expired;

export const providerReady = (s: Subscription) => !s.problem && !needsReconnect(s) && !(s.usage?.used_pct === 100 && limitReset(s));

export function providerBadge(s: Subscription, next: boolean) {
  if (s.problem) return { label: 'Problem', detail: s.problem };
  if (needsReconnect(s)) return { label: 'Reconnect needed', detail: s.status_detail };
  const reset = limitReset(s);
  if (s.usage?.used_pct === 100 && reset) return { label: `At limit until ${reset}` };
  if (s.usage?.near) return { label: 'Near limit' };
  if (s.in_use_on.length) return { label: 'In use' };
  return { label: next ? 'Next' : 'Ready' };
}
