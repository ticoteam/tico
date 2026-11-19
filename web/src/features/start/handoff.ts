import { reasonIn, type Result } from '@/actions';
import type { GitHubAppSetup } from '@/api/schema';

/* The hand-off to GitHub: a form posted to GitHub's "new app" page with the manifest, as the server's own hand-off page
   does (backend/github_app.py manifest_form). The browser leaves; GitHub comes back to the server, which saves the App
   and sends the browser on. Only links to github.com over https are ever followed. */

export const isGithubPage = (url: unknown): url is string => typeof url === 'string' && /^https:\/\/github\.com\//.test(url);

/* The setup's single-use state, kept in this tab only, so a setup GitHub sent back with no session can be finished
   here (POST /api/v2/github/app/finish). It works only with this owner's session, for an hour. Never in the address
   or the cache. */
const KEY = 'tico.githubSetup';
const STATE = /^[\w-]{1,200}$/;

export function pendingSetup(): string | null {
  try {
    const v = sessionStorage.getItem(KEY);
    return v && STATE.test(v) ? v : null;
  } catch {
    return null;
  }
}

export function forgetSetup(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked: nothing was kept
  }
}

function rememberSetup(state: string): void {
  try {
    if (STATE.test(state)) sessionStorage.setItem(KEY, state);
  } catch {
    // storage blocked: the setup still goes; only finishing from this tab is lost
  }
}

export const refused = (code: string, reason: string) => Object.assign(new Error(reason), { code: `${code}: ${reason}` });

/** What a refused press says: the server's reason, else a short line. */
export function reasonOf(r: Pick<Result, 'error' | 'why'>): string {
  const why = reasonIn(r);
  if (r.error === 'not_allowed' && !why) return 'Only the owner can do that';
  return why ?? 'That did not go through. Try again.';
}

/** The lost-key refusal: the owner is asked before anything is replaced. */
export const keyLost = (error: string | undefined) => !!error?.startsWith('github_key_missing');

/** Posts the setup to GitHub; the page leaves. */
export function handOff(setup: GitHubAppSetup): void {
  if (!isGithubPage(setup.action)) throw refused('github_setup', 'GitHub did not give a setup page');
  rememberSetup(setup.state);
  const form = document.createElement('form');
  form.method = 'post';
  form.action = setup.action;
  form.hidden = true;
  const field = document.createElement('input');
  field.type = 'hidden';
  field.name = 'manifest';
  field.value = JSON.stringify(setup.manifest);
  form.append(field);
  document.body.append(form);
  form.submit();
}

/** Leaves for a GitHub page the server named. */
export function goToGithub(url: unknown): void {
  if (!isGithubPage(url)) throw refused('github_link', 'GitHub did not give a page');
  window.location.assign(url);
}
