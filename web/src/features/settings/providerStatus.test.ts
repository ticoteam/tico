import { describe, expect, it } from 'vitest';
import { atUsageLimit, providerBadge } from './providerStatus';
import type { Subscription } from './queries';

const base: Subscription = {
  id: 'sub-1',
  kind: 'claude_login',
  label: 'Claude plan',
  name: "Ana's Claude plan",
  in_use_on: [],
  computers: [],
  owner_id: 'human:p1',
  owner_name: 'Ana Test',
  added_by: 'human:p1',
  added_by_name: 'Ana Test',
  status: 'working',
  status_at: null,
  status_detail: '',
  expired: false,
  runtime: 'claude',
  preview: 'ana@acme.example',
  usage: null,
  order: 1,
  revision: 1,
  created_at: '',
  updated_at: '',
};

describe('providerStatus', () => {
  it('marks Claude plans at 100% or 99.5% as At limit even when near is set and no quota hold exists', () => {
    const resets_at = '2099-07-01T00:00:00Z';
    const full: Subscription = {
      ...base,
      usage: { used_pct: 100, window: 'week', cost_usd_month: null, resets_at, near: true },
    };
    const threshold: Subscription = {
      ...base,
      usage: { used_pct: 99.5, window: '5h', cost_usd_month: null, resets_at: null, near: true },
    };

    expect(atUsageLimit(full)).toBe(true);
    expect(providerBadge(full).label).toBe('At limit');
    expect(providerBadge(full).detail).toMatch(/^Resets /);

    expect(atUsageLimit(threshold)).toBe(true);
    expect(providerBadge(threshold)).toEqual({ label: 'At limit' });
  });

  it('marks ChatGPT plans at 100% as At limit instead of Ready or Next', () => {
    const chatgpt: Subscription = {
      ...base,
      kind: 'codex_login',
      runtime: 'codex',
      label: 'ChatGPT plan',
      next_in_rotation: true,
      usage: { used_pct: 100, window: 'week', cost_usd_month: null, resets_at: '2099-07-01T00:00:00Z' },
    };
    expect(atUsageLimit(chatgpt)).toBe(true);
    expect(providerBadge(chatgpt).label).toBe('At limit');
    expect(providerBadge(chatgpt).detail).toMatch(/^Resets /);
  });

  it('keeps Problem, Reconnect needed, and quota_hold_until ahead of usage limit', () => {
    const usage = { used_pct: 100, window: 'week' as const, cost_usd_month: null, resets_at: '2099-07-01T00:00:00Z' };
    expect(providerBadge({ ...base, problem: 'Billing failed', usage })).toEqual({ label: 'Problem', detail: 'Billing failed' });
    expect(providerBadge({ ...base, status: 'needs_reconnect', status_detail: 'Signed out', usage })).toEqual({ label: 'Reconnect needed', detail: 'Signed out' });
    expect(providerBadge({ ...base, usage: { ...usage, quota_hold_until: usage.resets_at } }).label).toMatch(/^At limit until /);
  });

  it('does not flag plans below 99.5% as At limit', () => {
    const near: Subscription = {
      ...base,
      usage: { used_pct: 99.4, window: 'week', cost_usd_month: null, resets_at: null, near: true },
    };
    expect(atUsageLimit(near)).toBe(false);
    expect(providerBadge(near)).toEqual({ label: 'Near limit' });
  });
});
