import { useRouter } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import type { ToolIndex } from '@/api/schema';
import { toast } from '@/kit/Toast';
import { rereadTools } from './queries';
import type { GithubReturn, ToolsSearch } from './search';

/* Back from GitHub (backend/github_app.py): the server sends the browser to Tools › GitHub with `?github=<word>`. The
   page says how it went once and takes the word out of the address, so a reload or Back does not repeat it. The address
   is never proof: a success shows only when the tool index, read again, says GitHub is on. */

const PENDING = 'GitHub has not finished the install. Install the app on GitHub.';

/** What each word that reports a problem says, with the next step. */
export const GITHUB_WHY: Partial<Record<GithubReturn, string>> = {
  pending: PENDING,
  declined: 'GitHub access was declined. Nothing changed.',
  finish: 'GitHub sent the setup back without your sign-in. Finish it below, or connect again.',
  again: 'GitHub is already connected, so nothing changed. If GitHub made a second app just now, delete it there.',
  expired: 'That GitHub link is used or too old. Connect again.',
};

const DONE: Partial<Record<GithubReturn, string>> = { connected: 'Connected', installed: 'Installed', pending: 'Connected', authorized: 'Authorized' };

/** The GitHub row's tone in the index: `ok` installed, `wait` made but not installed, `idle` or none not connected. */
export const githubTone = (index: ToolIndex | undefined) => index?.rows.find((r) => r.kind === 'github')?.status.tone;

/** Reads GitHub's return once: a toast when the server shows it worked, else what went wrong for the GitHub tool's
    panel. Returns that (or null). */
export function useGithubReturn(search: ToolsSearch): string | null {
  const router = useRouter();
  const [problem, setProblem] = useState<string | null>(null);
  const seen = useRef(false);
  const word = search.github;
  useEffect(() => {
    if (word === undefined) {
      seen.current = false;
      return;
    }
    if (seen.current) return; // StrictMode runs this twice before the cleaned address lands
    seen.current = true;
    const known = word !== 'other';
    void router.navigate({
      to: '.',
      search: ((prev: Record<string, unknown>) => ({ ...prev, ...(known ? { tool: 'github' } : {}), github: undefined })) as never,
      replace: true,
    });
    if (!known) return;
    void rereadTools().then(
      (index) => {
        const tone = githubTone(index);
        const done = DONE[word];
        // Authorized needs the App kept; the others need it installed.
        if (done && (word === 'authorized' ? tone === 'ok' || tone === 'wait' : tone === 'ok')) toast({ text: done, name: 'GitHub' });
        else if (GITHUB_WHY[word]) setProblem(GITHUB_WHY[word]);
        else if (word !== 'authorized') setProblem(tone === 'wait' ? PENDING : 'GitHub is not connected yet.');
      },
      () => setProblem(GITHUB_WHY[word] ?? null),
    );
  }, [word, router]);
  return problem;
}
