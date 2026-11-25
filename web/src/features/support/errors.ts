/* Errors to the install's Sentry project, when its server names one (GET /api/v2/observability, backend/observability.py
   browser_config). The rule is the server's own reports' rule: code labels, never data. An event carries the error's
   type, the frames in our own files (file name, line, column, function), the page's route template, the server's code
   and status for a refused read, and, for a TypeError or ReferenceError only, the engine's message with any address
   cut out (it names code, not data). No address, query, cookie, header, body, breadcrumb, typed text or any other message leaves the page. Off (no
   DSN), nothing more loads: the SDK is its own chunk, fetched only when reporting is on. */

import type * as SentryBrowser from '@sentry/browser';
import type { ErrorEvent, StackFrame } from '@sentry/browser';
import { ApiError } from '@/api/client';
import { observabilityConfig } from './queries';

// The window's own errors and rejections come through the SDK's handlers, tagged `error` by scrub.
export type ErrorSource = 'page_fault';

interface Labels {
  source: ErrorSource;
  /** The router's route id (`/_app/tasks/$id`): a template, never the address. */
  route?: string;
}

type Sdk = typeof SentryBrowser;

const DSN = /^https:\/\/[a-fA-F0-9]+@(?:[a-z0-9-]+\.)*ingest(?:\.[a-z]{2})?\.sentry\.io\/[0-9]+$/;
const ENVIRONMENTS = new Set(['development', 'staging', 'production', 'test']);
const RELEASE = /^[A-Za-z0-9._-]{1,100}$/;
const IDENT = /^[A-Za-z_$][\w$.<>]{0,99}$/;
const CODE = /^[a-z][a-z0-9_]{0,63}$/;
const ROUTE = /^\/[A-Za-z0-9_$/.-]{0,200}$/;
// Our own built files only: the app's chunks (`/next/assets/<name>-<hash>.js`), or the dev server's sources.
const OWN_FILE = /^\/(?:next\/assets\/[A-Za-z0-9_.-]+\.js|src\/[A-Za-z0-9_$./-]+\.tsx?)$/;
// Messages the engine writes about code (`Cannot read properties of undefined (reading 'id')`).
const CODE_MESSAGES = new Set(['TypeError', 'ReferenceError']);
const EARLY_MAX = 10;

let sdk: Sdk | null = null;
let identity = '';
// idle until main.tsx starts it; off when the server names no project (or the SDK did not load)
let state: 'idle' | 'starting' | 'off' | 'on' = 'idle';
// The SDK's own chunk is on its way: its failing is not the app's (features/notices/recovery.ts lets it be).
let loading = false;
// Reports made before the SDK loaded (a page that fails on its first load), sent once it has.
const early: [unknown, Labels][] = [];

/** Reads the server's settings once and, when they name a Sentry project, loads the SDK and reports from then on. */
export async function startErrorReports(): Promise<void> {
  if (state !== 'idle') return;
  state = 'starting';
  const config = await observabilityConfig();
  const dsn = typeof config.sentry_dsn === 'string' && DSN.test(config.sentry_dsn) ? config.sentry_dsn : '';
  const environment = typeof config.environment === 'string' && ENVIRONMENTS.has(config.environment) ? config.environment : '';
  if (!dsn || !environment) return stop();
  identity = typeof config.distinct_id === 'string' && /^tico:[a-f0-9]{64}$/.test(config.distinct_id) ? config.distinct_id : '';
  const release = typeof config.release === 'string' && RELEASE.test(config.release) ? config.release : undefined;
  let loaded: Sdk;
  loading = true;
  try {
    loaded = await import('@sentry/browser');
  } catch {
    return stop(); // its chunk did not load (blocked, or a release meanwhile): no reports from this page
  } finally {
    loading = false;
  }
  loaded.init({
    dsn,
    environment,
    release,
    // Only the window's own error and rejection handlers: no breadcrumbs, no fetch or history wrapping, no sessions.
    defaultIntegrations: false,
    integrations: [loaded.globalHandlersIntegration()],
    // no user info: Sentry would otherwise infer the visitor's IP address
    dataCollection: { userInfo: false },
    maxBreadcrumbs: 0,
    sendClientReports: false,
    tracesSampleRate: 0,
    beforeBreadcrumb: () => null,
    beforeSend: (event) => scrub(event, environment, release),
  });
  sdk = loaded;
  state = 'on';
  for (const [error, labels] of early.splice(0)) send(error, labels);
}

/** The SDK's chunk is loading now: a file that fails meanwhile may be it, which only turns reporting off. */
export const loadingReporter = (): boolean => loading;

function stop(): void {
  state = 'off';
  early.length = 0;
}

/** One error, with the labels the window's handlers cannot know. Before reporting is on, kept for when it is. */
export function reportError(error: unknown, labels: Labels): void {
  if (state === 'on') send(error, labels);
  else if (state !== 'off' && early.length < EARLY_MAX) early.push([error, labels]);
}

function send(error: unknown, { source, route }: Labels): void {
  const tags: Record<string, string> = { source };
  if (route && ROUTE.test(route)) tags.route = route;
  if (error instanceof ApiError) {
    tags.status = String(error.status);
    if (CODE.test(error.code)) tags.code = error.code;
  }
  sdk?.captureException(error, { tags });
}

/** The event rebuilt from its code labels; anything not named here is dropped. Exported for its tests. */
export function scrub(event: ErrorEvent, environment: string, release: string | undefined): ErrorEvent | null {
  const values = event.exception?.values ?? [];
  if (!values.length) return null;
  const last = values[values.length - 1]!;
  const type = last.type && IDENT.test(last.type) ? last.type : 'Error';
  const frames = values
    .flatMap((v) => v.stacktrace?.frames ?? [])
    .map(ownFrame)
    .filter((f): f is StackFrame => f !== null)
    .slice(-30);
  // An engine message may still name an address (a module that did not load): never the address.
  const value =
    CODE_MESSAGES.has(type) && typeof last.value === 'string' ? last.value.replace(/[a-z][a-z0-9+.-]*:\/\/\S*/gi, '<url>').slice(0, 200) : undefined;
  const given = event.tags ?? {};
  const tags: Record<string, string> = { app: 'tico-web', source: 'error' };
  for (const key of ['source', 'route', 'status', 'code'] as const) {
    const v = given[key];
    if (typeof v === 'string' && v) tags[key] = v;
  }
  return {
    type: undefined,
    event_id: event.event_id,
    timestamp: event.timestamp,
    platform: 'javascript',
    level: 'error',
    environment,
    ...(release ? { release } : {}),
    ...(identity ? { user: { id: identity } } : {}),
    tags,
    exception: { values: [{ type, ...(value ? { value } : {}), stacktrace: { frames } }] },
  };
}

function ownFrame(frame: StackFrame): StackFrame | null {
  let path = '';
  try {
    const url = new URL(frame.filename ?? '', location.href);
    if (url.origin === location.origin) path = url.pathname;
  } catch {
    return null;
  }
  if (!OWN_FILE.test(path)) return null;
  return {
    filename: `app://${path}`,
    abs_path: `app://${path}`,
    ...(frame.function && IDENT.test(frame.function) ? { function: frame.function } : {}),
    ...(Number.isInteger(frame.lineno) ? { lineno: frame.lineno } : {}),
    ...(Number.isInteger(frame.colno) ? { colno: frame.colno } : {}),
    in_app: true,
  };
}
