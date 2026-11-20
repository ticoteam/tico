import { describe, expect, it } from 'vitest';
import { anyCallLive, badgeFor, cleanUserAgent, dotBitmap } from './badge';
import * as C from './companies';
import { doneFromPortal, issuerCookie, parsePicks } from './portal';
import * as V from './validate';

describe('companies.json', () => {
  it('drops bad entries and repeated origins, and keeps a valid active one', () => {
    const file = C.parseCompanies({
      version: 1,
      active: 'cmissing',
      companies: [
        { id: 'cacme', url: 'https://acme.tico.team', name: 'Acme', email: 'a@acme.example', lastUrl: 'https://acme.tico.team/flow' },
        { id: 'cdup', url: 'https://acme.tico.team' },
        { id: 'BAD ID', url: 'https://beta.tico.team' },
        { id: 'chttp', url: 'http://tico.example.com' },
        { id: 'cpath', url: 'https://beta.tico.team/' },
        { id: 'clast', url: 'https://gamma.tico.team', lastUrl: 'https://evil.example/' },
      ],
    });
    expect(file.companies.map((c) => c.id)).toEqual(['cacme', 'clast']);
    expect(file.active).toBe('cacme');
    expect(file.companies[0]!.partition).toBe('persist:company-cacme');
    expect(file.companies[1]!.lastUrl).toBeUndefined();
    expect(C.parseCompanies({ version: 2, companies: [] })).toEqual(C.emptyFile());
  });

  it('adds, finds, switches and removes', () => {
    const first = C.addCompany(C.emptyFile(), 'https://acme.tico.team', 'c1');
    expect(first.existed).toBe(false);
    let { file } = first;
    ({ file } = C.addCompany(file, 'https://beta.tico.team', 'c2'));
    expect(file.active).toBe('c2');
    expect(C.addCompany(file, 'https://acme.tico.team', 'c3')).toMatchObject({ existed: true, company: { id: 'c1' }, file: { active: 'c1' } });
    file = C.setLastUrl(file, 'c1', 'https://beta.tico.team/');
    expect(file.companies[0]!.lastUrl).toBeUndefined();
    file = C.removeCompany(file, 'c2');
    expect(file).toMatchObject({ active: 'c1', companies: [{ id: 'c1' }] });
    expect(() => C.addCompany(file, 'https://x.tico.team', '../x')).toThrow();
  });

  it('moves today’s orgs.json across once, keeping the open one', () => {
    let n = 0;
    const file = C.migrateOrgs(
      {
        version: 1,
        active: 'org-2',
        orgs: [
          { id: 'org-1', url: 'https://acme.tico.team/', name: 'Acme', email: 'a@acme.example', store: 'x' },
          { id: 'org-2', url: 'https://beta.tico.team/', name: 'Beta' },
          { id: 'org-3', url: 'not an address' },
        ],
      },
      () => `c${++n}`,
    );
    expect(file?.companies.map((c) => [c.url, c.name])).toEqual([
      ['https://acme.tico.team', 'Acme'],
      ['https://beta.tico.team', 'Beta'],
    ]);
    expect(file?.active).toBe('c2');
    expect(C.migrateOrgs({ orgs: [] }, () => 'c')).toBeNull();
  });
});

describe('bridge arguments', () => {
  const origin = 'https://acme.tico.team';
  it('accepts only the shapes the bridge promises', () => {
    expect(V.companyIdArg('c0a1b2')).toBe('c0a1b2');
    expect(V.callIdArg('0b8f7a52-1c3e-4c1a-9d1e-2f3a4b5c6d7e')).toBe('0b8f7a52-1c3e-4c1a-9d1e-2f3a4b5c6d7e');
    expect(V.countArg(12)).toBe(12);
    expect(V.urlOnOriginArg('https://acme.tico.team/threads/1', origin)).toBe('https://acme.tico.team/threads/1');
    expect(V.selfInfoArg({ name: 'Maya', email: 'm@acme.example', signedOut: false })).toEqual({ name: 'Maya', email: 'm@acme.example', signedOut: false });
    expect(V.filesOriginArg('https://files.acme.example', origin)).toBe('https://files.acme.example');
    expect(V.serverPrefillArg('ftp://x')).toBeUndefined();
  });

  it('throws on anything else', () => {
    const bad: Array<() => unknown> = [
      () => V.companyIdArg('../etc'),
      () => V.companyIdArg({}),
      () => V.callIdArg('a/b'),
      () => V.countArg(-1),
      () => V.countArg(1.5),
      () => V.countArg('3'),
      () => V.countArg(100000),
      () => V.urlOnOriginArg('https://evil.example/', origin),
      () => V.urlOnOriginArg('javascript:alert(1)', origin),
      () => V.selfInfoArg({ name: 'x', email: 'y', signedOut: false, extra: 1 }),
      () => V.selfInfoArg({ name: 'x'.repeat(201), email: '', signedOut: false }),
      () => V.filesOriginArg(origin, origin),
      () => V.filesOriginArg('http://files.example', origin),
      () => V.filesOriginArg('https://files.example/path', origin),
      () => V.boolArg('true'),
    ];
    for (const f of bad) expect(f).toThrow(V.BadArgument);
  });

  it('takes a page to switch to only from the company itself, and only an app page', () => {
    const beta = 'https://beta.tico.team';
    // another company: the address is ignored, the company opens where it was
    expect(V.switchUrlArg(`${beta}/api/v2/logout`, 'cacme', 'cbeta', beta)).toBeUndefined();
    expect(V.switchUrlArg(`${beta}/next/flow`, 'cacme', 'cbeta', beta)).toBeUndefined();
    // its own main window: an app page on its own origin
    expect(V.switchUrlArg(`${origin}/next/task/412`, 'cacme', 'cacme', origin)).toBe(`${origin}/next/task/412`);
    expect(V.switchUrlArg(undefined, 'cacme', 'cacme', origin)).toBeUndefined();
    for (const path of ['/api/v2/logout', '/auth/login', '/auth', '/%61pi/v2/logout', '//auth/login', '/next/..%2fapi/v2/logout']) {
      expect(() => V.switchUrlArg(`${origin}${path}`, 'cacme', 'cacme', origin), path).toThrow(V.BadArgument);
    }
    expect(() => V.switchUrlArg(`${beta}/next/`, 'cacme', 'cacme', origin)).toThrow(V.BadArgument);
  });

  it('tells a page about the other companies only what the switcher draws', () => {
    const file: C.CompaniesFile = {
      version: 1,
      active: 'cbeta',
      companies: [
        { id: 'cacme', url: origin, partition: 'persist:company-cacme', name: 'Acme', email: 'm@acme.example' },
        { id: 'cbeta', url: 'https://beta.tico.team', partition: 'persist:company-cbeta', name: '', email: 'm@beta.example' },
      ],
    };
    const refs = C.companyRefs(file, 'cacme', (id) => ({ signedOut: id === 'cbeta', needs: id === 'cbeta' ? 3 : 1 }));
    expect(refs).toEqual([
      { id: 'cacme', origin, name: 'Acme', email: 'm@acme.example', active: false, self: true, signedOut: false, needs: 1 },
      { id: 'cbeta', origin: '', name: 'beta.tico.team', email: '', active: true, self: false, signedOut: true, needs: 3 },
    ]);
  });

  it('sends to a view only while it shows its own company', () => {
    expect(V.onOwnOrigin('https://acme.tico.team/', origin)).toBe(true);
    expect(V.onOwnOrigin('https://accounts.google.com/o/oauth2/auth', origin)).toBe(false);
    expect(V.onOwnOrigin('https://team.cloudflareaccess.com/cdn-cgi/access/login/acme.tico.team', origin)).toBe(false);
    expect(V.onOwnOrigin('about:blank', origin)).toBe(false);
    expect(V.onOwnOrigin('', origin)).toBe(false);
  });

  it('lets only the top frame on the company origin call', () => {
    expect(V.senderAllowed('https://acme.tico.team/', true, origin)).toBe(true);
    expect(V.senderAllowed('https://acme.tico.team/', false, origin)).toBe(false);
    expect(V.senderAllowed('https://files.acme.example/x', true, origin)).toBe(false);
    expect(V.senderAllowed('https://team.cloudflareaccess.com/cdn-cgi/access/login/acme.tico.team', true, origin)).toBe(false);
    expect(V.senderAllowed(null, true, origin)).toBe(false);
  });
});

describe('badge and user agent', () => {
  it('sums every company’s count, and shows ! for a signed-out one when nothing is counted', () => {
    expect(badgeFor([{ needs: 2, signedOut: false }, { needs: 3, signedOut: false }])).toEqual({ count: 5, text: '5' });
    expect(badgeFor([{ needs: 0, signedOut: false }, { needs: 4, signedOut: true }])).toEqual({ count: 0, text: '!' });
    expect(badgeFor([])).toEqual({ count: 0, text: '' });
  });
  it('draws the Windows overlay as a round jade dot', () => {
    const size = 16;
    const px = dotBitmap(size);
    expect(px.length).toBe(size * size * 4);
    const at = (x: number, y: number) => [...px.subarray((y * size + x) * 4, (y * size + x) * 4 + 4)];
    expect(at(8, 8)).toEqual([0x71, 0x9c, 0x4f, 255]); // BGRA, opaque in the middle
    expect(at(0, 0)).toEqual([0, 0, 0, 0]); // clear in the corners
  });
  it('knows a call is live in any company’s page, not only the small window', () => {
    expect(anyCallLive(false, [{ callLive: false }, { callLive: true }])).toBe(true);
    expect(anyCallLive(true, [{ callLive: false }])).toBe(true);
    expect(anyCallLive(false, [{ callLive: false }, { callLive: false }])).toBe(false);
    expect(anyCallLive(false, [])).toBe(false);
  });
  it('drops the Electron and app tokens', () => {
    const ua = 'Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Tico/0.1.0 Chrome/140.0.0.0 Electron/45.4.1 Safari/537.36';
    expect(cleanUserAgent(ua, 'Tico')).toBe('Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 TicoDesktop/3');
  });
});

describe('Tico Cloud sign-in', () => {
  const portal = 'https://app.tico.dev';
  it('reads the picks from the done page', () => {
    const done = `${portal}/desktop/orgs/done?issuer=${encodeURIComponent('https://accounts.tico.team')}&org=${encodeURIComponent('https://acme.tico.team/')}&org=${encodeURIComponent('https://acme.tico.team')}&org=${encodeURIComponent('http://evil.example')}`;
    expect(parsePicks(portal, done)).toEqual({ issuer: 'https://accounts.tico.team', servers: ['https://acme.tico.team'] });
  });
  it('refuses a missing, doubled, Google or portal issuer, and another page', () => {
    const at = (q: string) => `${portal}/desktop/orgs/done?${q}`;
    expect(parsePicks(portal, at('org=https%3A%2F%2Facme.tico.team'))).toBeNull();
    expect(parsePicks(portal, at('issuer=https%3A%2F%2Fa.example&issuer=https%3A%2F%2Fb.example'))).toBeNull();
    expect(parsePicks(portal, at('issuer=https%3A%2F%2Faccounts.google.com'))).toBeNull();
    expect(parsePicks(portal, at('issuer=https%3A%2F%2Fapp.tico.dev'))).toBeNull();
    expect(parsePicks(portal, at('issuer=https%3A%2F%2Fa.example%2Fpath'))).toBeNull();
    expect(parsePicks(portal, 'https://evil.example/desktop/orgs/done?issuer=https%3A%2F%2Fa.example')).toBeNull();
  });
  it('takes a done page only when the portal’s own page asked for it', () => {
    const done = `${portal}/desktop/orgs/done?issuer=${encodeURIComponent('https://accounts.tico.team')}&org=${encodeURIComponent('https://acme.tico.team')}`;
    expect(doneFromPortal(portal, done, portal, `${portal}/desktop/orgs`)).toBe(true);
    // a provider's or any other page reached during sign-in, by a link, a script or a redirect
    expect(doneFromPortal(portal, done, 'https://evil.example', 'https://evil.example/x')).toBe(false);
    expect(doneFromPortal(portal, done, portal, 'https://evil.example/redirect')).toBe(false);
    expect(doneFromPortal(portal, done, 'https://evil.example', `${portal}/desktop/orgs`)).toBe(false);
    expect(doneFromPortal(portal, done, null, `${portal}/desktop/orgs`)).toBe(false);
    expect(doneFromPortal(portal, done, portal, null)).toBe(false);
    expect(doneFromPortal(portal, `${portal}/desktop/orgs`, portal, `${portal}/desktop/orgs`)).toBe(false);
  });
  it('copies only the issuer host’s own cookies', () => {
    expect(issuerCookie('.accounts.tico.team', 'accounts.tico.team')).toBe(true);
    expect(issuerCookie('tico.team', 'accounts.tico.team')).toBe(false);
    expect(issuerCookie('x.accounts.tico.team', 'accounts.tico.team')).toBe(false);
  });
});
