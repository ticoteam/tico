// "Use a server address": does the address answer as a Tico server within 8 seconds? The check asks /healthz, the
// one route that answers without sign-in (backend/app.py), with no cookies and no redirects, as today's app does
// (app/src/config.rs). A server behind Cloudflare Access answers with its login redirect instead; that passes, since
// the sign-in then happens in the window.
import { hostOf } from './address';

export type Health = { ok: true } | { ok: false; error: string };

export function accessLogin(status: number, location: string | null): boolean {
  if (status < 300 || status > 399 || !location) return false;
  try {
    const url = new URL(location);
    return url.protocol === 'https:' && url.hostname.endsWith('.cloudflareaccess.com') && url.pathname.startsWith('/cdn-cgi/access/login/');
  } catch {
    return false;
  }
}

export function classifyHealth(origin: string, status: number, location: string | null, body: unknown): Health {
  if (accessLogin(status, location)) return { ok: true };
  if (status >= 200 && status < 300 && body && typeof body === 'object' && (body as { service?: unknown }).service === 'tico') {
    return { ok: true };
  }
  return { ok: false, error: `${hostOf(origin)} did not answer as a Tico server.` };
}

export async function checkServer(origin: string, fetchImpl: typeof fetch = fetch): Promise<Health> {
  let response: Response;
  try {
    response = await fetchImpl(`${origin}/healthz`, {
      redirect: 'manual',
      credentials: 'omit',
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    return { ok: false, error: `Could not reach ${hostOf(origin)}.` };
  }
  const body: unknown = await response.json().catch(() => null);
  return classifyHealth(origin, response.status, response.headers.get('location'), body);
}
