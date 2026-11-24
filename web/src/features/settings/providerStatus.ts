import { fmtWhen } from '@/lib';
import type { Subscription } from './queries';

// Matches backend agent_subscriptions._usage(), which blocks a plan at >= 99.5%.
export const USAGE_LIMIT_PCT = 99.5;

export function limitReset(s: Subscription) {
  const reset = s.usage?.resets_at;
  return reset && Date.parse(reset) > Date.now() ? fmtWhen(reset) : '';
}

export const needsReconnect = (s: Subscription) => s.status === 'needs_reconnect' || s.expired;

export const atUsageLimit = (s: Subscription) => s.usage?.used_pct != null && s.usage.used_pct >= USAGE_LIMIT_PCT;

export const providerReady = (s: Subscription) => !s.problem && !needsReconnect(s) && s.available !== false;

export function providerBadge(s: Subscription) {
  if (s.problem) return { label: 'Problem', detail: s.problem };
  if (needsReconnect(s)) return { label: 'Reconnect needed', detail: s.status_detail };
  const hold = s.usage?.quota_hold_until;
  if (hold) return { label: `At limit until ${fmtWhen(hold)}` };
  if (atUsageLimit(s)) {
    const reset = limitReset(s);
    return reset ? { label: 'At limit', detail: `Resets ${reset}` } : { label: 'At limit' };
  }
  const busy = s.usage?.capacity_until;
  if (busy) return { label: `Busy until ${fmtWhen(busy)}` };
  if (s.usage?.near) return { label: 'Near limit' };
  if (s.in_use_on.length) return { label: 'In use' };
  return { label: s.next_in_rotation ? 'Next' : 'Ready' };
}
