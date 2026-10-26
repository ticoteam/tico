/* Explicit telemetry only. No DOM content, URLs, request bodies or error messages cross this boundary. */
'use strict';
(function (root) {
  'use strict';
  const ENVIRONMENTS = new Set(['development', 'test', 'staging', 'production']);
  const ROUTES = new Set(['overview', 'chat', 'notes', 'meetings', 'recordings', 'tasks', 'board', 'issues', 'recurring', 'settings', 'credentials', 'inbox', 'changelog', 'docs', 'runs', 'goals']);
  const WORKFLOWS = new Set(['navigation', 'chat', 'task', 'approval', 'notes', 'recording', 'settings']);
  const ACTIONS = new Set(['view', 'send', 'create', 'update', 'decide', 'save', 'start', 'stop', 'retry', 'delete', 'transition']);
  const OUTCOMES = new Set(['started', 'completed', 'failed']);
  const EVENTS = new Set(['workflow_started', 'workflow_completed', 'workflow_failed', 'route_viewed', 'active_time']);
  const SEND_OUTCOMES = new Set(['slow', 'sent', 'unconfirmed', 'timeout', 'network', 'refused']);
  function sendProperties(properties) {
    const p = properties || {}, clean = {};
    if (!SEND_OUTCOMES.has(p.outcome)) return null;
    clean.outcome = p.outcome;
    for (const [key, max] of [['duration_ms', 86400000], ['retries', 100], ['status', 599]]) {
      if (Number.isFinite(p[key]) && p[key] >= 0) clean[key] = Math.round(Math.min(p[key], max));
    }
    return clean;
  }
  const opaque = value => typeof value === 'string' && /^tico:[a-f0-9]{64}$/.test(value);
  function routeTemplate(hash) {
    const path = String(hash || '').split(/[?#]/).filter(Boolean)[0] || '/overview';
    const parts = path.split('/').filter(Boolean);
    if (parts[0] === 'bot') return '/bot/:bot';
    return ROUTES.has(parts[0]) ? '/' + parts[0] : '/unknown';
  }
  function workflow(path) {
    // Match full paths; unknown operations are deliberately untracked.
    const routes = [
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
  function safeProperties(properties) {
    const p = properties || {}, out = {};
    if (p.app === 'tico') out.app = 'tico';
    if (ENVIRONMENTS.has(p.environment)) out.environment = p.environment;
    if (WORKFLOWS.has(p.workflow)) out.workflow = p.workflow;
    if (ACTIONS.has(p.action)) out.action = p.action;
    if (OUTCOMES.has(p.outcome)) out.outcome = p.outcome;
    if (typeof p.route === 'string' && (p.route === '/bot/:bot' || p.route === '/unknown' || ROUTES.has(p.route.slice(1)) && p.route.startsWith('/'))) out.route = p.route;
    for (const key of ['duration_ms', 'active_duration_ms']) {
      if (Number.isFinite(p[key]) && p[key] >= 0) out[key] = Math.round(Math.min(p[key], 86400000));
    }
    return out;
  }
  const displayName = value => typeof value === 'string' && value.trim().length > 0 && value.trim().length <= 254 && !/[\x00-\x1f\x7f]/.test(value) ? value.trim() : '';
  function scrubPosthog(event, identity, environment, key, personDisplayName) {
    if (!event || !opaque(identity) || !ENVIRONMENTS.has(environment) || !EVENTS.has(event.event) || !/^phc_[A-Za-z0-9_-]+$/.test(key || '')) return null;
    return {event: event.event, timestamp: new Date(), properties: {...safeProperties(event.properties), app: 'tico', environment,
      token: key, distinct_id: identity, $process_person_profile: true, $geoip_disable: true,
      ...(event.event === 'route_viewed' && displayName(personDisplayName) ? {$set: {name: displayName(personDisplayName)}} : {})}};
  }
  function scrubSentry(event, identity, environment, release) {
    if (!opaque(identity) || !ENVIRONMENTS.has(environment)) return null;
    const frames = (event.exception?.values || []).flatMap(value => value.stacktrace?.frames || []).slice(-30)
      .map(frame => {
        // Only known static application filenames, no origin/query/hash/function/source text.
        const match = /(?:^|\/)(app\/(?:pill|chat|api|session)\.js|app\.bundle\.js|index\.html|observability\.js|observability-vendors\.js|docs-ask\.js|docs-page\.js|docs-search\.js|docs-pr-chat\.js|changelog\.js)(?:[?#].*)?$/.exec(frame.filename || '');
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
  function effectiveConfig(value) {
    const next = value || {};
    if (!opaque(next.distinct_id) || !ENVIRONMENTS.has(next.environment) || next.app !== 'tico') return null;
    const posthog = /^phc_[A-Za-z0-9_-]+$/.test(next.posthog_key || '') &&
      ['https://us.i.posthog.com', 'https://eu.i.posthog.com'].includes(next.posthog_host);
    const sentry = typeof next.sentry_dsn === 'string' && /^https:\/\/[a-fA-F0-9]+@(?:[a-z0-9-]+\.)*ingest(?:\.[a-z]{2})?\.sentry\.io\/[0-9]+$/.test(next.sentry_dsn);
    if (!posthog && !sentry) return null;
    return {app:'tico', distinct_id:next.distinct_id, environment:next.environment,
      posthog_key:posthog ? next.posthog_key : '', posthog_host:posthog ? next.posthog_host : '',
      sentry_dsn:sentry ? next.sentry_dsn : '',
      person_display_name:posthog ? displayName(next.person_display_name) : '',
      release:typeof next.release === 'string' && /^[A-Za-z0-9._-]{1,100}$/.test(next.release) ? next.release : ''};
  }
  function create(options = {}) {
    const now = options.now || (() => performance.now());
    const visible = options.visible || (() => !root.document.hidden);
    const load = options.load || (() => import('/assets/observability-vendors.js'));
    const fetchConfig = options.fetchConfig || (() => root.fetch('/api/v2/observability', {cache: 'no-store', credentials: 'same-origin'}));
    let identity = null, config = {}, adapters = null, generation = 0, initializing = null;
    let last = now(), lastInput = last, active = 0, wasVisible = visible(), page = null;
    function tick() {
      const current = now();
      if (wasVisible) active += Math.max(0, Math.min(current, lastInput + 60000) - last);
      last = current; wasVisible = visible();
      return current;
    }
    function activity() { tick(); lastInput = now(); }
    function emit(name, properties) {
      try { if (identity && adapters) adapters.capture(name, safeProperties({...properties, app:'tico', environment:config.environment})); } catch (_) { /* telemetry must never affect work */ }
    }
    function endPage() {
      const end = tick();
      if (page) emit('active_time', {workflow:'navigation', action:'view', outcome:'completed', route:page.route,
        duration_ms:end-page.start, active_duration_ms:active-page.active});
      page = null;
    }
    function route(hash) {
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
    async function run(path, operation) {
      const meta = workflow(path), owner = identity, version = generation;
      if (!meta || !owner) return operation();
      const start = tick(), initialActive = active;
      emit('workflow_started', {...meta, outcome:'started'});
      try {
        const result = await operation();
        const end = tick();
        if (identity === owner && version === generation) emit(result?.ok === false ? 'workflow_failed' : 'workflow_completed', {...meta,
          outcome:result?.ok === false ? 'failed' : 'completed', duration_ms:end-start, active_duration_ms:active-initialActive});
        return result;
      } catch (error) {
        const end = tick();
        if (identity === owner && version === generation) emit('workflow_failed', {...meta, outcome:'failed', duration_ms:end-start, active_duration_ms:active-initialActive});
        throw error;
      }
    }
    function response(status) { if (status === 401 || status === 403) reset(); }
    // Bind diagnostics to this authenticated generation. A late send may not
    // attach its event to a different account after sign-out or config changes.
    function chatSend() {
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
    const client = create(); root.TicoObservability = client;
    for (const event of ['pointerdown', 'keydown', 'scroll', 'touchstart']) root.addEventListener(event, client.activity, {passive:true, capture:true});
    root.document.addEventListener('visibilitychange', () => { client.visibility(); if (root.document.hidden) client.flush(); });
    root.addEventListener('pagehide', client.flush);
    // Covers Cloudflare Access logout and explicit logout links without reading their query strings.
    root.document.addEventListener('click', event => {
      const link = event.target.closest?.('a[href]');
      if (link && /\/(?:logout|cdn-cgi\/access\/logout)(?:[?#]|$)/.test(link.getAttribute('href'))) client.reset();
    }, true);
    root.setInterval(client.flush, 30000);
  }
})(typeof window === 'undefined' ? globalThis : window);
