import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/client';
import type { ModelSubscriptionList } from '@/api/schema';
import { screenFor, type SetupRecord } from './queries';
import { resumeStep, setupStep, stepBefore } from './steps';

const err = (status: number, signIn: string | null = null) => new ApiError(status, 'x', 'Not on the list', false, signIn);

describe('where a person lands @s1155 @s1156 @s1157 @s1195', () => {
  it('names the card for what the server answered', () => {
    expect(screenFor(null)).toBeNull();
    expect(screenFor(err(401, '/auth/sign-in'))).toBe('signin');
    expect(screenFor(err(401))).toBe('signed-out');
    expect(screenFor(err(403))).toBe('refused');
    expect(screenFor(err(0))).toBe('down');
    expect(screenFor(new TypeError('fetch failed'))).toBe('down');
  });
});

describe('first run steps @s1171', () => {
  const record = (company_name: string): SetupRecord => ({
    names: { company_name, app_name: '', assistant_name: '', owner_name: '', team_domain: '', website: '' },
    answers: {},
    selected: {},
    completed: null,
    needed: true,
  });
  const subs = (...subscriptions: Partial<ModelSubscriptionList['subscriptions'][number]>[]) =>
    ({ kinds: [], subscriptions }) as ModelSubscriptionList;

  it('resumes at the first step not done', () => {
    expect(resumeStep(record(' '), subs({ status: 'working' }))).toBe('name');
    expect(resumeStep(record('Acme'), subs())).toBe('model');
    expect(resumeStep(record('Acme'), subs({ status: 'working' }))).toBe('team');
    expect(resumeStep(record('Acme'), subs({ status: 'needs_reconnect' }))).toBe('model');
  });

  it('walks back to the step before, and reads only known steps from the address', () => {
    expect(stepBefore('team')).toBe('model');
    expect(stepBefore('paste')).toBe('model');
    expect(stepBefore('name')).toBe('path');
    expect(setupStep('model')).toBe('model');
    expect(setupStep('welcome')).toBeNull();
  });
});
