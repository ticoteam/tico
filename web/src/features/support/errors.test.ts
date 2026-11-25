import type { ErrorEvent } from '@sentry/browser';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';

const sdk = vi.hoisted(() => ({
  init: vi.fn(),
  captureException: vi.fn(),
  globalHandlersIntegration: vi.fn(() => ({ name: 'GlobalHandlers' })),
}));
const config = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('@sentry/browser', () => sdk);
vi.mock('./queries', () => ({ observabilityConfig: vi.fn(async () => config.value) }));

const DSN = 'https://abc123@o1.ingest.us.sentry.io/42';
const ID = 'tico:' + 'a'.repeat(64);

async function fresh() {
  vi.resetModules();
  return import('./errors');
}

const own = (file: string) => `${location.origin}/next/assets/${file}`;

describe('error reports', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    config.value = {};
  });

  it('loads nothing and keeps nothing when the server names no project', async () => {
    const errors = await fresh();
    errors.reportError(new Error('early'), { source: 'page_fault' });
    await errors.startErrorReports();
    errors.reportError(new Error('late'), { source: 'page_fault' });
    expect(sdk.init).not.toHaveBeenCalled();
    expect(sdk.captureException).not.toHaveBeenCalled();
  });

  it('sends a fault reported before it started, with its route and the refusal labels', async () => {
    config.value = { sentry_dsn: DSN, environment: 'production', release: 'v0.6.90', distinct_id: ID };
    const errors = await fresh();
    const refused = new ApiError(502, 'http_502', 'Bad Gateway', true);
    errors.reportError(refused, { source: 'page_fault', route: '/_app/tasks/$id' });
    await errors.startErrorReports();
    expect(sdk.init).toHaveBeenCalledWith(
      expect.objectContaining({
        dsn: DSN,
        environment: 'production',
        release: 'v0.6.90',
        defaultIntegrations: false,
        dataCollection: { userInfo: false },
        maxBreadcrumbs: 0,
      }),
    );
    expect(sdk.captureException).toHaveBeenCalledWith(refused, {
      tags: { source: 'page_fault', route: '/_app/tasks/$id', status: '502', code: 'http_502' },
    });
  });

  it('refuses a DSN that is not a Sentry ingest address', async () => {
    config.value = { sentry_dsn: 'https://abc@evil.example/1', environment: 'production' };
    const errors = await fresh();
    await errors.startErrorReports();
    expect(sdk.init).not.toHaveBeenCalled();
  });

  it('sends only code labels: type, our own frames and known tags; a message only for TypeError', async () => {
    config.value = { sentry_dsn: DSN, environment: 'production', distinct_id: ID };
    const errors = await fresh();
    await errors.startErrorReports();
    const event = (type: string, value: string): ErrorEvent => ({
      type: undefined,
      event_id: 'e'.repeat(32),
      message: 'secret text',
      request: { url: `${location.origin}/tasks/12?q=secret` },
      breadcrumbs: [{ message: 'clicked secret' }],
      extra: { body: 'secret' },
      contexts: { other: { value: 'secret' } },
      tags: { source: 'page_fault', route: '/_app/tasks/$id', other: 'secret' },
      exception: {
        values: [
          {
            type,
            value,
            stacktrace: {
              frames: [
                { filename: 'https://cdn.example/x.js', function: 'theirs', lineno: 1, colno: 1 },
                { filename: `${own('index-abc.js')}?token=secret`, function: 'Wt', lineno: 3, colno: 9 },
                { filename: 'chrome-extension://abc/inject.js', function: 'ext', lineno: 2, colno: 2 },
              ],
            },
          },
        ],
      },
    });
    const plain = errors.scrub(event('Error', 'customer Ana owes $40'), 'production', 'v1');
    expect(plain).toEqual({
      type: undefined,
      event_id: 'e'.repeat(32),
      timestamp: undefined,
      platform: 'javascript',
      level: 'error',
      environment: 'production',
      release: 'v1',
      user: { id: ID },
      tags: { app: 'tico-web', source: 'page_fault', route: '/_app/tasks/$id' },
      exception: {
        values: [
          {
            type: 'Error',
            stacktrace: {
              frames: [
                { filename: 'app:///next/assets/index-abc.js', abs_path: 'app:///next/assets/index-abc.js', function: 'Wt', lineno: 3, colno: 9, in_app: true },
              ],
            },
          },
        ],
      },
    });
    expect(JSON.stringify(plain)).not.toMatch(/secret|Ana|\$40/);
    const typed = errors.scrub(event('TypeError', "Cannot read properties of undefined (reading 'id')"), 'production', undefined);
    expect(typed?.exception?.values?.[0]?.value).toBe("Cannot read properties of undefined (reading 'id')");
    const missing = errors.scrub(event('TypeError', 'Failed to fetch dynamically imported module: https://acme.tico.team/next/assets/Tasks-1a2b.js'), 'production', undefined);
    expect(missing?.exception?.values?.[0]?.value).toBe('Failed to fetch dynamically imported module: <url>');
    expect(errors.scrub({ type: undefined, message: 'no exception' }, 'production', undefined)).toBeNull();
  });
});
