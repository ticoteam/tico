/* Explicit telemetry only. No DOM content, URLs, request bodies or error messages cross this boundary. */
'use strict';
// Global names: `module` is the CommonJS export a Node test reads this file through (the else branch below runs in a page).
declare var module: {exports?: unknown} | undefined;
(function (root: typeof globalThis) {
  'use strict';
  // Shapes read here. Telemetry fields are checked one by one (safeProperties, scrubPosthog, scrubSentry) before they leave.
  type Props = Record<string, unknown>;
  // The config the server sends (effectiveConfig checks each field before the page uses it).
  interface Config {
    app?: string | undefined;
    distinct_id?: string | undefined;
    environment?: string | undefined;
    posthog_key?: string | undefined;
    posthog_host?: string | undefined;
    sentry_dsn?: string | undefined;
    person_display_name?: string | undefined;
    release?: string | undefined;
  }
  // The PostHog capture event, as far as scrubPosthog reads it.
  interface PosthogEvent {
    event?: string | undefined;
    properties?: Props | null | undefined;
  }
  // The Sentry error event, as far as scrubSentry reads it.
  interface SentryEvent {
    event_id?: string | undefined;
    exception?: {values?: {stacktrace?: {frames?: {filename?: string; lineno?: unknown; colno?: unknown}[]}}[]};
    tags?: {source?: string | undefined};
    extra?: Props | null | undefined;
  }
  // What the vendor bundle's connect() returns (ui/observability-vendors.js).
  interface Adapters {
    identify(id: string | undefined): void;
    capture(name: string, properties: Props): void;
    chatSend(properties: Props): void;
    reset(): void;
  }
  interface Vendor {
    connect(config: Config, filters: {posthog(event: PosthogEvent): unknown; sentry(event: SentryEvent): unknown}): Adapters;
  }
  interface CreateOptions {
    now?: () => number;
    visible?: () => boolean;
    load?: () => Promise<Vendor>;
    fetchConfig?: () => Promise<Response>;
  }
  interface Page {
    route: string;
    start: number;
    active: number;
  }
  const ENVIRONMENTS = new Set<unknown>(['development', 'test', 'staging', 'production']);
  const ROUTES = new Set<unknown>(['overview', 'chat', 'notes', 'meetings', 'recordings', 'tasks', 'board', 'issues', 'recurring', 'settings', 'credentials', 'inbox', 'changelog', 'docs', 'runs', 'goals']);
  const WORKFLOWS = new Set<unknown>(['navigation', 'chat', 'task', 'approval', 'notes', 'recording', 'settings']);
  const ACTIONS = new Set<unknown>(['view', 'send', 'create', 'update', 'decide', 'save', 'start', 'stop', 'retry', 'delete', 'transition']);
  const OUTCOMES = new Set<unknown>(['started', 'completed', 'failed']);
  const EVENTS = new Set<unknown>(['workflow_started', 'workflow_completed', 'workflow_failed', 'route_viewed', 'active_time']);
  const SEND_OUTCOMES = new Set<unknown>(['slow', 'sent', 'unconfirmed', 'timeout', 'network', 'refused']);
  // True for a value a person or Sentry sends as a failed result (`ok: false`).
  const failed = (value: unknown): boolean => typeof value === 'object' && value !== null && 'ok' in value && value.ok === false;
  function sendProperties(properties: Props | null | undefined): Props | null {
    const p: Props = properties || {}, clean: Props = {};
    if (!SEND_OUTCOMES.has(p.outcome)) return null;
    clean.outcome = p.outcome;
    const limits: [string, number][] = [['duration_ms', 86400000], ['retries', 100], ['status', 599]];
    for (const [key, max] of limits) {
      const value = p[key];
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) clean[key] = Math.round(Math.min(value, max));
    }
    return clean;
  }
  const opaque = (value: unknown): boolean => typeof value === 'string' && /^tico:[a-f0-9]{64}$/.test(value);
  function routeTemplate(hash: unknown): string {
    const path = String(hash || '').split(/[?#]/).filter(Boolean)[0] || '/overview';
    const parts = path.split('/').filter(Boolean);
    if (parts[0] === 'bot') return '/bot/:bot';
    return ROUTES.has(parts[0]) ? '/' + parts[0] : '/unknown';
  }
  function workflow(path: string): {workflow: string; action: string | undefined} | null {
    // Match full paths; unknown operations are deliberately untracked.
    const routes: [RegExp, string, string | null][] = [
      [/^\/v2\/(?:uploads\/)?chat\/[^/?#]+$/, 'chat', 'send'],
      [/^\/v2\/(?:tasks|conversations)\/[^/?#]+\/(?:chat|messages)$/, 'chat', 'send'],
      [/^\/v2\/page-chat$/, 'chat', 'send'],
      [/^\/v2\/(?:uploads\/)?tasks$/, 'task', 'create'],
      [/^\/v2\/tasks\/[^/?#]+$/, 'task', 'update'],
      [/^\/v2\/approvals\/[^/?#]+$/, 'approval', 'decide'],
      [/^\/notes$/, 'notes', 'save'],
      [/^\/v2\/meetings\/import$/, 'meeting', 'import'],
      [/^\/meetings\/[^/?#]+\/(delete|send)$/, 'meeting', null],
      [/^\/v2\/bots\/[^/?#]+\/transitions$/, 'settings', 'transition'],
      [/^\/v2\/bots(?:\/[^/?#]+\/definition)?$/, 'settings', 'update'],
    ];
    for (const [pattern, name, action] of routes) {
      const match = pattern.exec(path);
      if (match) return {workflow: name, action: action || match[1]};
    }
    return null;
  }
  function safeProperties(properties: Props | null | undefined): Props {
    const p: Props = properties || {}, out: Props = {};
    if (p.app === 'tico') out.app = 'tico';
    if (ENVIRONMENTS.has(p.environment)) out.environment = p.environment;
    if (WORKFLOWS.has(p.workflow)) out.workflow = p.workflow;
    if (ACTIONS.has(p.action)) out.action = p.action;
    if (OUTCOMES.has(p.outcome)) out.outcome = p.outcome;
    if (typeof p.route === 'string' && (p.route === '/bot/:bot' || p.route === '/unknown' || ROUTES.has(p.route.slice(1)) && p.route.startsWith('/'))) out.route = p.route;
    for (const key of ['duration_ms', 'active_duration_ms']) {
      const value = p[key];
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) out[key] = Math.round(Math.min(value, 86400000));
    }
    return out;
  }
  // True when the text has a control character: a code unit from 0x00 to 0x1f, or 0x7f.
  const hasControl = (text: string): boolean => {
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i);
      if (code <= 0x1f || code === 0x7f) return true;
    }
    return false;
  };
  const displayName = (value: unknown): string => typeof value === 'string' && value.trim().length > 0 && value.trim().length <= 254 && !hasControl(value) ? value.trim() : '';
  function scrubPosthog(event: PosthogEvent | null | undefined, identity: unknown, environment: unknown, key: string | undefined, personDisplayName: unknown) {
    if (!event || !opaque(identity) || !ENVIRONMENTS.has(environment) || !EVENTS.has(event.event) || !/^phc_[A-Za-z0-9_-]+$/.test(key || '')) return null;
    return {event: event.event, timestamp: new Date(), properties: {...safeProperties(event.properties), app: 'tico', environment,
      token: key, distinct_id: identity, $process_person_profile: true, $geoip_disable: true,
      ...(event.event === 'route_viewed' && displayName(personDisplayName) ? {$set: {name: displayName(personDisplayName)}} : {})}};
  }
  function scrubSentry(event: SentryEvent, identity: unknown, environment: unknown, release: string | undefined) {
    if (!opaque(identity) || !ENVIRONMENTS.has(environment)) return null;
    const frames = (event.exception?.values || []).flatMap(value => value.stacktrace?.frames || []).slice(-30)
      .map(frame => {
        // Only known static application filenames, no origin/query/hash/function/source text.
        const match = /(?:^|\/)(app\/(?:pill|chat|api|session)\.(?:js|ts)|app\.bundle(?:\.\d+)?\.js|index\.html|observability\.(?:js|ts)|observability-vendors\.js|docs-page\.(?:js|ts)|docs-search\.(?:js|ts)|docs-pr-chat\.(?:js|ts)|changelog\.(?:js|ts))(?:[?#].*)?$/.exec(frame.filename || '');
        return match ? {filename: match[1], ...(Number.isInteger(frame.lineno) ? {lineno: frame.lineno} : {}),
          ...(Number.isInteger(frame.colno) ? {colno: frame.colno} : {})} : null;
      }).filter(Boolean);
    const send = event.tags?.source === 'chat_send' ? sendProperties(event.extra) : null;
    return {event_id: /^[a-f0-9]{32}$/.test(event.event_id || '') ? event.event_id : undefined,
      platform: 'javascript', level: 'error', environment,
      ...(typeof release === 'string' && /^[A-Za-z0-9._-]{1,100}$/.test(release) ? {release} : {}), user: {id: identity}, tags: {app: 'tico'},
      ...(send ? {tags: {app: 'tico', source: 'chat_send', outcome: send.outcome}, extra: send,
        fingerprint: ['chat_send', send.outcome]} : {}),
      exception: {values: [{type: send ? 'ChatSend' : 'FrontendError',
        value: send ? `Chat send ${send.outcome}` : 'Frontend operation failed', stacktrace: {frames}}]}};
  }
  function effectiveConfig(value: Config | null | undefined): Config | null {
    const next: Config = value || {};
    if (!opaque(next.distinct_id) || !ENVIRONMENTS.has(next.environment) || next.app !== 'tico') return null;
    const posthog = /^phc_[A-Za-z0-9_-]+$/.test(next.posthog_key || '') &&
      ['https://us.i.posthog.com', 'https://eu.i.posthog.com'].includes(next.posthog_host ?? '');
    const sentry = typeof next.sentry_dsn === 'string' && /^https:\/\/[a-fA-F0-9]+@(?:[a-z0-9-]+\.)*ingest(?:\.[a-z]{2})?\.sentry\.io\/[0-9]+$/.test(next.sentry_dsn);
    if (!posthog && !sentry) return null;
    return {app:'tico', distinct_id:next.distinct_id, environment:next.environment,
      posthog_key:posthog ? next.posthog_key : '', posthog_host:posthog ? next.posthog_host : '',
      sentry_dsn:sentry ? next.sentry_dsn : '',
      person_display_name:posthog ? displayName(next.person_display_name) : '',
      release:typeof next.release === 'string' && /^[A-Za-z0-9._-]{1,100}$/.test(next.release) ? next.release : ''};
  }
  // The vendor bundle the server serves at this path (built from ui/observability-vendors.js). Its shape is Vendor above.
  const VENDOR_PATH = '/assets/observability-vendors.js';
  function create(options: CreateOptions = {}) {
    const now = options.now || (() => performance.now());
    const visible = options.visible || (() => !root.document.hidden);
    const load: () => Promise<Vendor> = options.load || (() => import(VENDOR_PATH));
    const fetchConfig = options.fetchConfig || (() => root.fetch('/api/v2/observability', {cache: 'no-store', credentials: 'same-origin'}));
    let identity: string | null | undefined = null, config: Config = {}, adapters: Adapters | null = null, generation = 0, initializing: Promise<void> | null = null;
    let last = now(), lastInput = last, active = 0, wasVisible = visible(), page: Page | null = null;
    function tick() {
      const current = now();
      if (wasVisible) active += Math.max(0, Math.min(current, lastInput + 60000) - last);
      last = current; wasVisible = visible();
      return current;
    }
    function activity() { tick(); lastInput = now(); }
    function emit(name: string, properties: Props) {
      try { if (identity && adapters) adapters.capture(name, safeProperties({...properties, app:'tico', environment:config.environment})); } catch (_) { /* telemetry must never affect work */ }
    }
    function endPage() {
      const end = tick();
      if (page) emit('active_time', {workflow:'navigation', action:'view', outcome:'completed', route:page.route,
        duration_ms:end-page.start, active_duration_ms:active-page.active});
      page = null;
    }
    function route(hash: string | undefined) {
      const template = routeTemplate(hash);
      if (!identity || page?.route === template) return;
      endPage(); page = {route:template, start:now(), active};
      emit('route_viewed', {workflow:'navigation', action:'view', outcome:'completed', route:template});
    }
    function flush() {
      if (!page) return;
      const template = page.route; endPage(); page = {route:template, start:now(), active};
    }
    function reset() {
      generation++; identity = null; page = null; config = {};
      try { adapters?.reset(); } catch (_) {}
      adapters = null; active = 0; last = lastInput = now(); wasVisible = visible();
    }
    async function start() {
      if (initializing) return initializing;
      let version = generation;
      initializing = (async () => {
        try {
          const response = await fetchConfig();
          if (!response.ok) { if (version === generation) reset(); return; }
          const next = effectiveConfig(await response.json());
          if (version !== generation) return;
          if (!next) { reset(); return; }
          if (identity && JSON.stringify(config) === JSON.stringify(next)) return;
          reset(); const token = generation; version = token;
          const vendor = await load();
          if (token !== generation) return;
          config = next; identity = next.distinct_id;
          adapters = vendor.connect(config, {
            posthog: event => token === generation ? scrubPosthog(event, next.distinct_id, next.environment, next.posthog_key, next.person_display_name) : null,
            sentry: event => token === generation ? scrubSentry(event, next.distinct_id, next.environment, next.release) : null,
          });
          adapters.identify(identity);
          activity(); route(root.location?.hash);
        } catch (_) { if (version === generation) reset(); }
      })();
      try { await initializing; } finally { initializing = null; }
    }
    async function run<T>(path: string, operation: () => Promise<T>): Promise<T> {
      const meta = workflow(path), owner = identity, version = generation;
      if (!meta || !owner) return operation();
      const start = tick(), initialActive = active;
      emit('workflow_started', {...meta, outcome:'started'});
      try {
        const result = await operation();
        const end = tick();
        if (identity === owner && version === generation) emit(failed(result) ? 'workflow_failed' : 'workflow_completed', {...meta,
          outcome:failed(result) ? 'failed' : 'completed', duration_ms:end-start, active_duration_ms:active-initialActive});
        return result;
      } catch (error) {
        const end = tick();
        if (identity === owner && version === generation) emit('workflow_failed', {...meta, outcome:'failed', duration_ms:end-start, active_duration_ms:active-initialActive});
        throw error;
      }
    }
    function response(status: number) { if (status === 401 || status === 403) reset(); }
    // Bind diagnostics to this authenticated generation. A late send may not
    // attach its event to a different account after sign-out or config changes.
    function chatSend(): (properties: Props | null | undefined) => void {
      const owner = identity, token = generation;
      return properties => {
        try {
          const clean = sendProperties(properties);
          if (owner && identity === owner && token === generation && clean) adapters?.chatSend(clean);
        } catch (_) { /* reporting must never affect sending */ }
      };
    }
    return {start, reset, route, run, response, chatSend, activity, visibility:tick, flush};
  }
  const exports = {create, routeTemplate, workflow, safeProperties, scrubPosthog, scrubSentry};
  if (typeof module !== 'undefined' && module.exports) module.exports = exports;
  else {
    // Object.assign sets the property the way `root.TicoObservability = client` does; typeof globalThis does not carry the Window member.
    const client = create(); Object.assign(root, {TicoObservability: client});
    for (const event of ['pointerdown', 'keydown', 'scroll', 'touchstart']) root.addEventListener(event, client.activity, {passive:true, capture:true});
    root.document.addEventListener('visibilitychange', () => { client.visibility(); if (root.document.hidden) client.flush(); });
    root.addEventListener('pagehide', client.flush);
    // Covers Cloudflare Access logout and explicit logout links without reading their query strings.
    root.document.addEventListener('click', event => {
      const target = event.target;
      const link = target instanceof Element ? target.closest('a[href]') : null;
      if (link && /\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(link.getAttribute('href') ?? '')) client.reset();
    }, true);
    root.setInterval(client.flush, 30000);
  }
})(typeof window === 'undefined' ? globalThis : window);
