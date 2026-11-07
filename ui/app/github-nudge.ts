/* ui/app/github-nudge.ts — The nudge to connect GitHub, where a bot's work is kept. While GitHub is not connected and
   installed, a bot someone made (its page) and a "Build a bot" task (BotOps's) say that the work is backed up nowhere,
   and offer the one fix: the owner gets Connect GitHub (Tools › GitHub, ui/github-connect.js); anyone else is asked to
   ask the owner. It goes as soon as GitHub is connected. One read (GET /v2/repositories, which any teammate may read)
   when the page draws; nothing polls, and nothing is sent to anyone.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// What GET /v2/repositories says about GitHub for the team (backend/repositories.py listing).
interface GithubNudgeTeam {
  github_connected?: boolean;
  github_installed?: boolean;
}

// A task BotOps files to build a bot: "Build a bot: <name>", owned by BotOps (playbooks/set-up-a-bot.md).
const GH_NUDGE_BUILD_PREFIX = 'Build a bot: ';

// The card: the owner's Connect GitHub button, or the ask to the owner for anyone else.
function ghNudgeHTML(name: string, owner: boolean, ownerName: string): string {
  const sentence = owner
    ? `Connect GitHub to back up ${esc(name)}'s work.`
    : `Ask ${esc(ownerName || 'the owner')} to connect GitHub to back up ${esc(name)}'s work.`;
  return `<section class="gh-nudge" role="status" aria-label="GitHub"><span>${sentence}</span>${
    owner ? '<a class="primary" href="#/tools/github" data-gh-connect>Connect GitHub</a>' : ''}</section>`;
}

// Whether GitHub is missing for the team: no App, or one that was never installed. A failed read shows nothing.
async function ghNudgeMissing(): Promise<boolean> {
  try {
    const team = await get<GithubNudgeTeam>('/v2/repositories');
    return !(team.github_connected && team.github_installed);
  } catch {
    return false;
  }
}

// The bot's page: the card under its banners, for a bot someone made (a built-in bot is the company's own).
async function githubNudgeBot(slug: string): Promise<void> {
  if (isBuiltInBot(slug) || !(await ghNudgeMissing())) return;
  const host = document.getElementById('bot-github-host');
  if (!host || !host.isConnected || host.dataset.bot !== slug) return;   // the page moved on while the read was out
  const owner = S.me?.role === 'owner';
  host.innerHTML = ghNudgeHTML(botDisplayName(slug), owner, settingsOwnerName());
}

// The host a "Build a bot" task's page draws, empty for any other task.
function githubNudgeTaskHostHTML(task: {id: string | number; owner_id?: string; title?: string}): string {
  return ghNudgeBuildName(task) ? `<div class="gh-nudge-host" data-gh-nudge-task data-task="${esc(String(task.id))}"></div>` : '';
}

// The bot a Build a bot task is making, by its name in the title ('' for any other task).
function ghNudgeBuildName(task: {owner_id?: string; title?: string}): string {
  const title = String(task.title ?? '');
  if (task.owner_id !== 'bot:botops' || !title.startsWith(GH_NUDGE_BUILD_PREFIX)) return '';
  return title.slice(GH_NUDGE_BUILD_PREFIX.length).trim();
}

// A Build a bot task's page: the card in its host, filled once the read says GitHub is missing.
async function githubNudgeTask(dialog: HTMLElement, task: {id: string | number; owner_id?: string; title?: string}): Promise<void> {
  const name = ghNudgeBuildName(task);
  if (!name || !(await ghNudgeMissing())) return;
  const host = dialog.querySelector<HTMLElement>('[data-gh-nudge-task]');
  if (!host || host.dataset.task !== String(task.id)) return;   // the dialog drew another task meanwhile
  const owner = S.me?.role === 'owner';
  host.innerHTML = ghNudgeHTML(name, owner, settingsOwnerName());
}
