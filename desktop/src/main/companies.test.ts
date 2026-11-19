import { describe, expect, it } from 'vitest';
import { badgeFor, cleanUserAgent } from './badge';
import * as C from './companies';
import { issuerCookie, parsePicks } from './portal';
import * as V from './validate';

describe('companies.json', () => {
  it('drops bad entries and repeated origins, and keeps a valid active one', () => {
    const file = C.parseCompanies({
      version: 1,
      active: 'cmissing',
      companies: [
        { id: 'cacme', url: 'https://acme.tico.team', name: 'Acme', email: 'a@acme.example', lastUrl: 'https://acme.tico.team/next/flow' },
        { id: 'cdup', url: 'https://acme.tico.team' },
        { id: 'BAD ID', url: 'https://beta.tico.team' },
        { id: 'chttp', url: 'http://tico.example.com' },
        { id: 'cpath', url: 'https://beta.tico.team/next/' },
        { id: 'clast', url: 'https://gamma.tico.team', lastUrl: 'https://evil.example/next/' },
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
    file = C.setLastUrl(file, 'c1', 'https://beta.tico.team/next/');
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
    expect(V.urlOnOriginArg('https://acme.tico.team/next/threads/1', origin)).toBe('https://acme.tico.team/next/threads/1');
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
      () => V.urlOnOriginArg('https://evil.example/next/', origin),
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

  it('lets only the top frame on the company origin call', () => {
    expect(V.senderAllowed('https://acme.tico.team/next/', true, origin)).toBe(true);
    expect(V.senderAllowed('https://acme.tico.team/next/', false, origin)).toBe(false);
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
  it('copies only the issuer host’s own cookies', () => {
    expect(issuerCookie('.accounts.tico.team', 'accounts.tico.team')).toBe(true);
    expect(issuerCookie('tico.team', 'accounts.tico.team')).toBe(false);
    expect(issuerCookie('x.accounts.tico.team', 'accounts.tico.team')).toBe(false);
  });
});
