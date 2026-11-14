/* ui/app/github-nudge.ts — The nudge to connect GitHub, where a bot's work is kept. While GitHub is not connected and
   installed, a bot someone made (its page) and a live "Build a bot" task (BotOps's) say that the work is backed up
   nowhere, and offer the one fix: the owner gets Connect GitHub (Tools › GitHub, ui/github-connect.js); anyone else is
   asked to ask the owner. It goes as soon as GitHub is connected. The team's answer is read once per page
   (GET /v2/repositories, which any teammate may read): a draw after the first fills its card in the same tick, so a
   redraw does not flicker. Visiting Tools › GitHub forgets the answer; a failed read is asked again on the next draw.
   Nothing polls, and nothing is sent to anyone.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What GET /v2/repositories says about GitHub for the team (backend/repositories.py listing).
interface GithubNudgeTeam {
  github_connected?: boolean;
  github_installed?: boolean;
}

// The fields of a task the nudge reads (the task record the task modal and the task route draw).
interface GithubNudgeTask {
  id: string | number;
  owner_id?: string | null;
  title?: string;
  status?: string;
}


// The team's answer, read once per page: `ghNudgeRead` is the read in flight or done (it resolves to true while GitHub
// is missing); `ghNudgeAnswer` is that answer once it is in, so a draw can fill its card without waiting.
let ghNudgeRead: Promise<boolean> | null = null;
let ghNudgeAnswer: boolean | null = null;

// Visiting Tools › GitHub, including the return from the GitHub install, asks again on the next draw.
window.addEventListener('hashchange', () => {
  if (!location.hash.startsWith('#/tools/github')) return;
  ghNudgeRead = null;
  ghNudgeAnswer = null;
});

// The card: the owner's Connect GitHub button, or the ask to the owner for anyone else.
function ghNudgeHTML(name: string, owner: boolean, ownerName: string): string {
  const sentence = owner
    ? `Connect GitHub to back up ${esc(name)}'s work.`
    : `Ask ${esc(ownerName || 'the owner')} to connect GitHub to back up ${esc(name)}'s work.`;
  return `<section class="gh-nudge" role="status" aria-label="GitHub"><span>${sentence}</span>${
    owner ? '<a class="primary" href="#/tools/github" data-gh-connect>Connect GitHub</a>' : ''}</section>`;
}

// Whether GitHub is missing for the team: no App, or one that was never installed. A failed read shows nothing.
function ghNudgeMissingRead(): Promise<boolean> {
  if (!ghNudgeRead) {
    const read: Promise<boolean> = get<GithubNudgeTeam>('/v2/repositories').then(
      team => {
        const missing = !(team.github_connected && team.github_installed);
        if (ghNudgeRead === read) ghNudgeAnswer = missing;
        return missing;
      },
      () => {
        if (ghNudgeRead === read) ghNudgeRead = null;
        return false;
      },
    );
    ghNudgeRead = read;
  }
  return ghNudgeRead;
}

// The bot's page: the card under its banners, for a bot someone made (a built-in bot is the company's own).
async function githubNudgeBot(slug: string): Promise<void> {
  if (isBuiltInBot(slug)) return;
  const missing = ghNudgeAnswer ?? (await ghNudgeMissingRead());
  if (!missing) return;
  const host = document.getElementById('bot-github-host');
  if (!host || !host.isConnected || host.dataset.bot !== slug) return;   // the page moved on while the read was out
  host.innerHTML = ghNudgeHTML(botDisplayName(slug), S.me?.role === 'owner', settingsOwnerName());
}



// A Build a bot task's page: the card in its host, filled once the read says GitHub is missing.
async function githubNudgeTask(dialog: HTMLElement, task: GithubNudgeTask): Promise<void> {
  const name = ghNudgeBuildName(task);
  if (!name) return;
  const missing = ghNudgeAnswer ?? (await ghNudgeMissingRead());
  if (!missing) return;
  const host = dialog.querySelector<HTMLElement>('[data-gh-nudge-task]');
  if (!host || host.dataset.task !== String(task.id)) return;   // the dialog drew another task meanwhile
  host.innerHTML = ghNudgeHTML(name, S.me?.role === 'owner', settingsOwnerName());
}
