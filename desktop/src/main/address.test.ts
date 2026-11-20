import { describe, expect, it } from 'vitest';
import { appUrl, checkAddress, isAppUrl, localSignInUrl } from './address';
import { classifyHealth } from './health';

describe('server addresses', () => {
  it('takes a Cloud name, a host, or a pasted link, and keeps only the origin', () => {
    expect(checkAddress('acme')).toEqual({ ok: true, origin: 'https://acme.tico.team' });
    expect(checkAddress(' tico.example.com ')).toEqual({ ok: true, origin: 'https://tico.example.com' });
    expect(checkAddress('https://acme.tico.team/tasks/eng?chat=1#x')).toEqual({ ok: true, origin: 'https://acme.tico.team' });
    expect(checkAddress('https://tico.example.com:8443')).toEqual({ ok: true, origin: 'https://tico.example.com:8443' });
  });

  it('allows plain HTTP only for this computer', () => {
    expect(checkAddress('http://127.0.0.1:8765')).toEqual({ ok: true, origin: 'http://127.0.0.1:8765' });
    expect(checkAddress('http://localhost:8765/next/')).toEqual({ ok: true, origin: 'http://localhost:8765' });
    expect(checkAddress('http://tico.example.com').ok).toBe(false);
  });

  it('takes a local copy’s sign-in link only on loopback over http, at exactly its path', () => {
    for (const host of ['127.0.0.1:8766', 'localhost:8766', '[::1]:8766']) {
      const link = `http://${host}/api/v2/local-signin?token=t0k&next=/`;
      expect(localSignInUrl(` ${link} `), host).toBe(link);
      expect(checkAddress(link), host).toEqual({ ok: true, origin: `http://${host}` });
    }
    expect(localSignInUrl('http://127.0.0.1:8766/api/v2/local-signin?token=t0k#x')).toBe('http://127.0.0.1:8766/api/v2/local-signin?token=t0k');
    for (const bad of [
      'https://acme.tico.team/api/v2/local-signin?token=t',
      'http://tico.example.com/api/v2/local-signin?token=t',
      'http://127.0.0.1.evil.example/api/v2/local-signin?token=t',
      'https://127.0.0.1:8766/api/v2/local-signin?token=t',
      'http://u:p@127.0.0.1:8766/api/v2/local-signin?token=t',
      'http://127.0.0.1:8766/api/v2/local-signin',
      'http://127.0.0.1:8766/api/v2/logout?token=t',
      'http://127.0.0.1:8766/api/v2/local-signin/?token=t',
      'http://127.0.0.1:8766/next/api/v2/local-signin?token=t',
      'http://127.0.0.1:8766/api/v2/%6cocal-signin?token=t',
      'http://127.0.0.1:8766/',
      'not a url',
    ]) {
      expect(localSignInUrl(bad), bad).toBeNull();
    }
  });

  it('refuses other schemes, credentials and junk', () => {
    for (const bad of ['', 'ftp://tico.example.com', 'file:///etc/passwd', 'javascript:alert(1)', 'https://u:p@tico.example.com', 'https://']) {
      expect(checkAddress(bad).ok, bad).toBe(false);
    }
    expect(checkAddress('x'.repeat(3000)).ok).toBe(false);
  });

  it('knows the app’s own pages', () => {
    expect(appUrl('https://acme.tico.team')).toBe('https://acme.tico.team/');
    expect(isAppUrl('https://acme.tico.team/tasks', 'https://acme.tico.team')).toBe(true);
    // a place kept from when the app was at /next/: the server sends it on to the same page at /
    expect(isAppUrl('https://acme.tico.team/next/tasks', 'https://acme.tico.team')).toBe(true);
    expect(isAppUrl('https://acme.tico.team/api/v2/logout', 'https://acme.tico.team')).toBe(false);
    expect(isAppUrl('https://acme.tico.team/auth/login', 'https://acme.tico.team')).toBe(false);
    expect(isAppUrl('https://evil.example/', 'https://acme.tico.team')).toBe(false);
  });
});

describe('server check', () => {
  const origin = 'https://acme.tico.team';
  it('passes a Tico health answer and a Cloudflare Access login redirect', () => {
    expect(classifyHealth(origin, 200, null, { ok: true, service: 'tico' })).toEqual({ ok: true });
    expect(classifyHealth(origin, 302, 'https://team.cloudflareaccess.com/cdn-cgi/access/login/acme.tico.team?kid=1', null)).toEqual({ ok: true });
  });
  it('fails anything else', () => {
    expect(classifyHealth(origin, 200, null, { service: 'other' }).ok).toBe(false);
    expect(classifyHealth(origin, 302, 'https://evil.example/cdn-cgi/access/login/x', null).ok).toBe(false);
    expect(classifyHealth(origin, 500, null, null).ok).toBe(false);
  });
});
