import { describe, expect, it } from 'vitest';
import { parseDeepLink } from './deeplink';
import { decideNavigation, decideNewWindow, keepOnCall, signInOver, type NavContext } from './navigation';
import { permissionAllowed } from './permissions';

const ctx: NavContext = {
  origin: 'https://acme.tico.team',
  signingIn: false,
  others: [{ id: 'cother', origin: 'https://beta.tico.team' }],
  filesOrigin: 'https://files.acme.example',
};

describe('navigation in a company view', () => {
  it('stays on the company origin and starts a sign-in chain at /auth/login', () => {
    expect(decideNavigation('https://acme.tico.team/next/tasks', ctx)).toEqual({ kind: 'stay' });
    expect(decideNavigation('https://acme.tico.team/auth/login?next=/next/', ctx)).toEqual({ kind: 'sign-in' });
  });

  it('keeps the whole sign-in chain in the view, then ends it back on the company', () => {
    expect(decideNavigation('https://login.provider.example/authorize', ctx).kind).toBe('external');
    expect(decideNavigation('https://login.provider.example/authorize', { ...ctx, signingIn: true })).toEqual({ kind: 'stay' });
    expect(decideNavigation('http://login.provider.example/authorize', { ...ctx, signingIn: true }).kind).toBe('block');
    expect(signInOver('https://acme.tico.team/next/', ctx.origin)).toBe(true);
    expect(signInOver('https://acme.tico.team.evil.example/', ctx.origin)).toBe(false);
  });

  it('starts sign-in only at the Cloudflare Access login for this host', () => {
    expect(decideNavigation('https://team.cloudflareaccess.com/cdn-cgi/access/login/acme.tico.team?kid=1', ctx)).toEqual({ kind: 'sign-in' });
    expect(decideNavigation('https://team.cloudflareaccess.com/cdn-cgi/access/login/other.example', ctx).kind).toBe('external');
    expect(decideNavigation('https://team.cloudflareaccess.com/cdn-cgi/access/logout', ctx).kind).toBe('external');
    expect(decideNavigation('https://team.cloudflareaccess.com/cdn-cgi/access/callback', { ...ctx, signingIn: true })).toEqual({ kind: 'stay' });
    expect(decideNavigation('https://cloudflareaccess.com.evil.example/cdn-cgi/access/login/acme.tico.team', ctx).kind).toBe('external');
  });

  it('switches to another known company, and never shows the files origin top level', () => {
    expect(decideNavigation('https://beta.tico.team/next/flow', ctx)).toEqual({ kind: 'switch', companyId: 'cother', url: 'https://beta.tico.team/next/flow' });
    expect(decideNavigation('https://files.acme.example/page.html', ctx)).toEqual({ kind: 'block' });
  });

  it('starts a sign-in chain only at the exact sign-in path', () => {
    for (const path of ['/next/auth/login', '/x/auth/login', '/auth/login/', '/auth/loginx']) {
      expect(decideNavigation(`https://acme.tico.team${path}`, ctx), path).toEqual({ kind: 'stay' });
    }
  });

  it('keeps companies and the files origin apart in the middle of a sign-in chain too', () => {
    const chain = { ...ctx, signingIn: true };
    expect(decideNavigation('https://beta.tico.team/next/', chain)).toEqual({ kind: 'switch', companyId: 'cother', url: 'https://beta.tico.team/next/' });
    expect(decideNavigation('https://files.acme.example/page.html', chain)).toEqual({ kind: 'block' });
  });

  it('opens another company at a link only when it is one of its app pages, never its API or sign-in', () => {
    for (const path of ['/api/v2/logout', '/auth/login?next=/next/', '/auth/signed-out', '/API/v2/logout', '/%61pi/v2/logout', '//api/v2/logout', '/next/..%2Fapi/v2/logout']) {
      const raw = `https://beta.tico.team${path}`;
      expect(decideNavigation(raw, ctx), path).toEqual({ kind: 'switch', companyId: 'cother' });
      expect(decideNewWindow(raw, ctx), path).toEqual({ kind: 'switch', companyId: 'cother' });
      expect(decideNavigation(raw, { ...ctx, signingIn: true }), path).toEqual({ kind: 'switch', companyId: 'cother' });
    }
  });

  it('hands the browser https and mail links only', () => {
    expect(decideNavigation('https://example.com/a', ctx)).toEqual({ kind: 'external', url: 'https://example.com/a' });
    expect(decideNavigation('mailto:a@example.com', ctx).kind).toBe('external');
    for (const raw of ['http://example.com', 'file:///etc/passwd', 'javascript:alert(1)', 'tel:123', 'smb://host/share', 'not a url']) {
      expect(decideNavigation(raw, ctx), raw).toEqual({ kind: 'block' });
    }
  });

  it('gives no page a new window', () => {
    expect(decideNewWindow('https://example.com/', ctx)).toEqual({ kind: 'external', url: 'https://example.com/' });
    expect(decideNewWindow('https://beta.tico.team/next/', ctx).kind).toBe('switch');
    expect(decideNewWindow('about:blank', ctx)).toEqual({ kind: 'block' });
    expect(decideNewWindow('data:text/html,hi', ctx)).toEqual({ kind: 'block' });
  });
});

describe('the small call window', () => {
  const call = { ...ctx, companyId: 'cacme', callPath: '/next/call/r1' };
  const nav = (url: string, c = call) => keepOnCall(url, decideNavigation(url, c), c);
  const pop = (url: string) => keepOnCall(url, decideNewWindow(url, call), call);

  it('stays on its call, and opens the company’s other pages in the main window', () => {
    expect(nav('https://acme.tico.team/next/call/r1?window=1')).toEqual({ kind: 'stay' });
    expect(nav('https://acme.tico.team/next/task/412')).toEqual({ kind: 'switch', companyId: 'cacme', url: 'https://acme.tico.team/next/task/412' });
    expect(pop('https://acme.tico.team/next/docs/plan')).toEqual({ kind: 'switch', companyId: 'cacme', url: 'https://acme.tico.team/next/docs/plan' });
  });

  it('sends outside https pages and files to the browser, and follows nothing else', () => {
    expect(pop('https://example.com/a')).toEqual({ kind: 'external', url: 'https://example.com/a' });
    expect(nav('https://acme.tico.team/api/files/9')).toEqual({ kind: 'external', url: 'https://acme.tico.team/api/files/9' });
    expect(pop('http://example.com/a').kind).toBe('block');
    expect(nav('javascript:alert(1)').kind).toBe('block');
  });

  it('leaves a sign-in chain and other companies to the usual rules', () => {
    expect(nav('https://acme.tico.team/auth/login?next=/next/')).toEqual({ kind: 'sign-in' });
    expect(nav('https://acme.tico.team/auth/callback')).toEqual({ kind: 'stay' });
    expect(nav('https://acme.tico.team/next/', { ...call, signingIn: true })).toEqual({ kind: 'stay' });
    expect(nav('https://beta.tico.team/next/flow').kind).toBe('switch');
    expect(nav('https://beta.tico.team/next/flow')).toMatchObject({ companyId: 'cother' });
  });
});

describe('permissions', () => {
  const top = { requestingUrl: 'https://acme.tico.team/next/call/1', isMainFrame: true };
  it('grants the microphone, camera, notifications to the company’s own top frame', () => {
    expect(permissionAllowed('media', { ...top, mediaTypes: ['audio'] }, ctx.origin)).toBe(true);
    expect(permissionAllowed('media', { ...top, mediaTypes: ['audio', 'video'] }, ctx.origin)).toBe(true);
    expect(permissionAllowed('notifications', top, ctx.origin)).toBe(true);
  });
  it('refuses frames, other origins and everything else', () => {
    expect(permissionAllowed('media', { ...top, isMainFrame: false, mediaTypes: ['audio'] }, ctx.origin)).toBe(false);
    expect(permissionAllowed('media', { requestingUrl: 'https://files.acme.example/', isMainFrame: true, mediaTypes: ['audio'] }, ctx.origin)).toBe(false);
    for (const p of ['geolocation', 'clipboard-read', 'hid', 'usb', 'serial', 'openExternal', 'display-capture', 'midi']) {
      expect(permissionAllowed(p, top, ctx.origin), p).toBe(false);
    }
  });
});

describe('tico:// links', () => {
  const companies = [
    { id: 'cacme', origin: 'https://acme.tico.team' },
    { id: 'cbeta', origin: 'https://beta.tico.team' },
  ];
  it('opens a full link in its company, or offers to add an unknown server', () => {
    const u = encodeURIComponent('https://beta.tico.team/next/threads/t1?x=1');
    expect(parseDeepLink(`tico://open?u=${u}`, companies, 'cacme')).toEqual({ kind: 'open', companyId: 'cbeta', url: 'https://beta.tico.team/next/threads/t1?x=1' });
    expect(parseDeepLink(`tico://open?u=${encodeURIComponent('https://new.example/next/')}`, companies, 'cacme')).toEqual({ kind: 'add', server: 'https://new.example' });
    expect(parseDeepLink(`tico://open?u=${encodeURIComponent('javascript:alert(1)')}`, companies, 'cacme')).toEqual({ kind: 'focus' });
    // a link to its API (sign-out) or sign-in only brings that company forward
    expect(parseDeepLink(`tico://open?u=${encodeURIComponent('https://beta.tico.team/api/v2/logout')}`, companies, 'cacme')).toEqual({ kind: 'open', companyId: 'cbeta' });
    expect(parseDeepLink(`tico://open?u=${encodeURIComponent('https://beta.tico.team/auth/login')}`, companies, 'cacme')).toEqual({ kind: 'open', companyId: 'cbeta' });
  });
  it('maps today’s tico://<path> onto the active company’s /next/, and never off it', () => {
    expect(parseDeepLink('tico://meetings/abc', companies, 'cacme')).toEqual({ kind: 'open', companyId: 'cacme', url: 'https://acme.tico.team/next/meetings/abc' });
    expect(parseDeepLink('tico://..%2F..%2Fapi/v2/logout', companies, 'cacme')?.kind).not.toBe('open');
    const dots = parseDeepLink('tico://a/../../api/v2/logout', companies, 'cacme');
    expect(dots?.kind !== 'open' || !!dots.url?.startsWith('https://acme.tico.team/next/')).toBe(true);
    expect(parseDeepLink('tico:////evil.example/x', companies, 'cacme')).toMatchObject({ url: 'https://acme.tico.team/next/evil.example/x' });
    expect(parseDeepLink('tico://', companies, 'cacme')).toEqual({ kind: 'focus' });
    expect(parseDeepLink('https://acme.tico.team/', companies, 'cacme')).toBeNull();
  });
});
