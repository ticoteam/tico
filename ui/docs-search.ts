/* What the Docs page knows about a doc's kind, and the one search that covers both kinds
   (internal docs and linked docs, GET /api/v2/docs/search). The kind detection mirrors the server's
   (backend/docs.py detect_kind) so a person sees what a link will be filed as while they type it. */
'use strict';
(function (root) {
  'use strict';
  const svg = body => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  const KINDS = {
    website: {label: 'Website', icon: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.9 5.6 3.9 9S14.6 18.4 12 21c-2.6-2.6-3.9-5.6-3.9-9S9.4 5.6 12 3z"/>')},
    google_drive: {label: 'Google Drive', icon: svg('<path d="M9 4h6l6 10.5-3 5.5H6l-3-5.5z"/><path d="M9 4l6 10.5h6M3 14.5h12M9 4 3 14.5"/>')},
    google_doc: {label: 'Google Doc', icon: svg('<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M9.5 12h6M9.5 15.5h6"/>')},
    notion: {label: 'Notion', icon: svg('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 16V8l6 8V8"/>')},
    github: {label: 'GitHub', icon: svg('<path d="M9 19c-4 1.5-4-2-6-2m12 5v-3.9a3.4 3.4 0 0 0-1-2.6c3.3-.4 6.8-1.6 6.8-7.3A5.7 5.7 0 0 0 19.3 4.3 5.3 5.3 0 0 0 19.2 1s-1.3-.4-4.2 1.5a14.4 14.4 0 0 0-7.6 0C4.5.6 3.2 1 3.2 1a5.3 5.3 0 0 0-.1 3.3A5.7 5.7 0 0 0 1.7 8.2c0 5.6 3.5 6.9 6.8 7.3a3.4 3.4 0 0 0-1 2.6V22"/>')},
    other: {label: 'Link', icon: svg('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>')},
  };
  const OTHER_HOSTS = ['dropbox.com', 'box.com', 'sharepoint.com', 'onedrive.live.com', '1drv.ms', 'atlassian.net', 'confluence.com',
    'figma.com', 'airtable.com', 'coda.io', 'slab.com', 'quip.com', 'gitbook.io', 'readme.io', 'slite.com', 'clickup.com', 'monday.com',
    'asana.com', 'lucid.app', 'miro.com', 'loom.com', 'guru.com', 'getguru.com', 'helpjuice.com', 'zendesk.com', 'intercom.com'];
  const LOCK = svg('<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>');

  function parse(value) {
    const text = String(value || '').trim();
    if (!text || /\s/.test(text)) return null;
    try { return new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : 'https://' + text); } catch { return null; }
  }
  function hostOf(value) {
    const url = parse(value);
    return url ? url.hostname.replace(/^www\./, '').toLowerCase() : '';
  }
  function kindOf(value) {
    const url = parse(value);
    if (!url || !/^https?:$/.test(url.protocol)) return '';
    const host = url.hostname.replace(/^www\./, '').toLowerCase(), path = url.pathname.toLowerCase();
    const is = (...names) => names.some(n => host === n || host.endsWith('.' + n));
    if (host === 'docs.google.com') return path.startsWith('/document') ? 'google_doc' : 'google_drive';
    if (host === 'sites.google.com') return 'website';
    if (is('drive.google.com')) return 'google_drive';
    if (is('notion.so', 'notion.site')) return 'notion';
    if (['github.com', 'raw.githubusercontent.com', 'gist.github.com'].includes(host)) return 'github';
    if (is(...OTHER_HOSTS)) return 'other';
    return 'website';
  }
  function sourceScope(value) {
    const url = parse(value);
    if (!url || !['github.com', 'www.github.com'].includes(url.hostname)) return null;
    let path;
    try { path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, '').split('/'); }
    catch { return null; }
    if (path.length < 2 || (path.length > 2 && !['tree', 'blob'].includes(path[2]))) return null;
    return {repo: path.slice(0, 2).join('/').replace(/\.git$/, ''), tail: path.slice(3).join('/')};
  }
  const kind = name => KINDS[name] || KINDS.other;
  // The words of a query as the server reads them (letters and digits), for highlighting and emptiness.
  const terms = query => (String(query).toLowerCase().match(/\w+/g) || []).slice(0, 12);

  // One search over both kinds, newest request wins: a slow answer for "pri" never replaces the one for "pricing".
  let ticket = 0;
  async function run(get, query) {
    const mine = ++ticket;
    if (!terms(query).length) return {results: [], stale: false};
    const found = await get('/v2/docs/search?limit=30&q=' + encodeURIComponent(query));
    return {results: found.results || [], stale: mine !== ticket};
  }

  root.DocsSearch = {KINDS, LOCK, kind, kindOf, hostOf, sourceScope, terms, run};
  if (typeof module !== 'undefined') module.exports = root.DocsSearch;
})(typeof globalThis !== 'undefined' ? globalThis : this);
