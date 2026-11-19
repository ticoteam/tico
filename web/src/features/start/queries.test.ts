import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import type { GetNeedsYouResponse, ModelSubscriptionList, NeedsYouItem } from '@/api/schema';
import { queryClient } from '@/data';
import { adaptNeeds, loginQuery, needHref, needsSetup, readLogin, screenFor, startConnect, type SetupRecord } from './queries';
import { resumeStep, setupStep, stepBefore } from './steps';

const err = (status: number, signIn: string | null = null) => new ApiError(status, 'x', 'Not on the list', false, signIn);
const need = (extra: Partial<NeedsYouItem>): NeedsYouItem => ({ id: 'n1', kind: 'task', line: 'Approve the plan', since: '2026-01-02T10:00:00Z', ...extra });

describe('where a person lands @s1155 @s1156 @s1157 @s1195', () => {
  it('names the card for what the server answered', () => {
    expect(screenFor(null)).toBeNull();
    expect(screenFor(err(401, '/auth/sign-in'))).toBe('signin');
    expect(screenFor(err(401))).toBe('signed-out');
    expect(screenFor(err(403))).toBe('refused');
    expect(screenFor(err(0))).toBe('down');
    expect(screenFor(new TypeError('fetch failed'))).toBe('down');
  });

  it('owes the first run only when the config says so', () => {
    expect(needsSetup(null)).toBe(false);
    expect(needsSetup({ onboarding_needed: true } as never)).toBe(true);
  });
});

describe('needs', () => {
  it('opens a task, else its thread', () => {
    expect(needHref(need({ task_id: 't 1' }))).toBe('/task/t%201');
    expect(needHref({ ...need({ kind: 'paused' }), task_ids: ['t2', 't3'] })).toBe('/task/t2');
    expect(needHref(need({ kind: 'mention', root_id: 'r1', conversation_id: 'c1' }))).toBe('/threads/r1');
    expect(needHref(need({ kind: 'question', conversation_id: 'c1' }))).toBe('/threads/c1');
    expect(needHref(need({}))).toBeUndefined();
  });

  it('keeps one row per item, with who asked', () => {
    const got = adaptNeeds({ count: 3, items: [need({ task_id: 't1', from_id: 'bot:ada' }), need({ id: 'n2', kind: 'ask', task_id: 't2' })] } as GetNeedsYouResponse);
    expect(got.count).toBe(3);
    expect(got.items.map((n) => [n.id, n.title, n.href, n.from])).toEqual([
      ['n1', 'Approve the plan', '/task/t1', 'bot:ada'],
      ['n2', 'Approve the plan', '/task/t2', undefined],
    ]);
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
  const subs = (n: number) => ({ kinds: [], subscriptions: Array.from({ length: n }, () => ({})) }) as unknown as ModelSubscriptionList;

  it('resumes at the first step not done', () => {
    expect(resumeStep(record(' '), subs(1))).toBe('name');
    expect(resumeStep(record('Acme'), subs(0))).toBe('model');
    expect(resumeStep(record('Acme'), subs(1))).toBe('team');
  });

  it('walks back to the step before, and reads only known steps from the address', () => {
    expect(stepBefore('team')).toBe('model');
    expect(stepBefore('paste')).toBe('model');
    expect(stepBefore('name')).toBe('path');
    expect(setupStep('model')).toBe('model');
    expect(setupStep('welcome')).toBeNull();
  });
});

describe('a model sign-in in the first run', () => {
  afterEach(() => vi.unstubAllGlobals());
  const CODE = 'WDJB-MJHT';
  const PAGE = `https://auth.example/device?user_code=${CODE}`;

  it('caches the state only; the code and page go back to the caller', async () => {
    queryClient.clear();
    vi.stubGlobal('fetch', async (url: string) => {
      const body = String(url).endsWith('/connect')
        ? { id: 'l1', runner_id: 'r1', state: 'waiting', url: PAGE, code: CODE }
        : { id: 'l1', runner_id: 'r1', state: 'waiting', url: PAGE, code: CODE, lines: [`Enter ${CODE}`], accepts_code: false };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    await startConnect('codex_login');
    const read = await readLogin();
    expect(read?.code).toBe(CODE);
    expect(read?.url).toBe(PAGE);
    expect(queryClient.getQueryData(loginQuery.queryKey)).toEqual({ id: 'l1', runner_id: 'r1', state: 'waiting', kind: 'codex_login', accepts_code: false });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
  });
});
