import type * as Tasks from './tasks-page.cts';
import type {Page} from 'playwright';
// Task privacy in the task window, on the v3 fixture (ui/tests/tasks-page.cts); every request is intercepted.
// - Visible to (one row, task #409): the requester may make a private task the team's; a private task lists requester,
//   owner and readers, × takes a reader off, + adds one; a company task says Team.
// - Followers: each name with its level when not live; Follow / Following for you, × for others.
// - A task filed under a private parent is private (the create form sends it); a parent the reader cannot open is
//   named only "a private task", never its title or id, and is not a link.
// - A parent is fixed when the task is filed: Part of links to it and offers nothing to add, change or take off; the
//   Related menu offers only the relations the server writes.
// - A bot's "Create private tasks by default" and update subscriptions save from its settings.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const path = require('node:path') as typeof import('node:path');
const {html} = require('./support/page.cts');
const {open, task} = require('./tasks-page.cts') as Tasks.Api;
declare function openTaskCreate(owner: string, opts?: {parent: {id: string; title: string; private: boolean}}): void;
declare function loadSettings(): Promise<void>;
declare function settingsEditBot(name: string): void;
type Body = Record<string, unknown>;
interface Follower {actor: string; notify: string; reason: string}
// The icon font is subset (ui/vendor/fonts/icons.txt); a ligature missing from that list renders as a stray glyph.
{
  const subset = fs.readFileSync(path.join(__dirname, '../vendor/fonts/icons.txt'), 'utf8').split('\n').filter(Boolean);
  for (const name of ['chevron_right', ...[...String(html).matchAll(/class="nav-icon" aria-hidden="true">([a-z_]+)</g)].map(m => m[1]!)])
    assert(subset.includes(name), `icon ${name} is not in ui/vendor/fonts/icons.txt (run scripts/build-icon-font.py)`);
  assert.deepEqual(subset, [...subset].sort(), 'icons.txt must stay alphabetical for Google Fonts');
}
const SECRET = 'Salary bands 2027';
const fixtures = () => [
  task('t-11', 'Draft the newsletter', 'spec', {owner_id: 'bot:writer', private: true, readers: ['human:sam'],
    followers: [{actor: 'bot:writer', notify: 'live', reason: 'owner'}, {actor: 'human:sam', notify: 'digest', reason: 'manual'}]}),
  task('t-12', 'Write the copy', 'spec', {owner_id: 'bot:writer'}),
  // Filed from private work: the server sends the parent with nothing of it but "a private task" (task_relations.py).
  task('t-13', 'Pick the photos', 'spec', {owner_id: 'bot:writer',
    relations: {parent: [{id: null, title: 'a private task', status: null, owner_id: null, direction: 'out', private: true}]}}),
  task('t-14', 'Proof the copy', 'spec', {owner_id: 'bot:writer',
    relations: {parent: [{id: 't-12', title: 'Write the copy', status: 'open', owner_id: 'bot:writer', direction: 'out'}]}}),
  // Postal mail filed it on Ana's rights (backend/sources.py filers): she stays its requester.
  task('t-15', 'Answer the summons', 'spec', {owner_id: 'bot:writer', private: true, filer: 'postal-mail'}),
];
const BOTS = [['pm', 'PM'], ['writer', 'Writer']].map(([name, display_name]) => ({name, display_name, host: 'keeper', status: 'active',
  state: 'active', can_chat: true, private_tasks_default: name === 'pm', update_subscriptions: name === 'pm' ? ['templates'] : []}));

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const tasks = fixtures();
    const posted: {p: string; body: Body}[] = [];
    // Readers, followers and the bot definition have their own routes; they go in before the app opens.
    const routes = async (page: Page) => {
      await page.route(url => /^\/api\/v2\/tasks\/[^/]+\/readers$/.test(url.pathname), route => {
        const p = new URL(route.request().url()).pathname, body = route.request().postDataJSON() as {add?: string[]; remove?: string[]};
        posted.push({p, body});
        const t = tasks.find(x => x.id === decodeURIComponent(p.split('/')[4]!))!;
        t.readers = [...(t.readers || []).filter(a => !(body.remove || []).includes(a)), ...(body.add || [])];
        return route.fulfill({contentType: 'application/json', body: JSON.stringify({private: true, requester_id: t.requester_id, owner_id: t.owner_id, readers: t.readers})});
      });
      await page.route(url => /^\/api\/v2\/tasks\/[^/]+\/followers$/.test(url.pathname), route => {
        const p = new URL(route.request().url()).pathname, body = route.request().postDataJSON() as {actor?: string; following: boolean; notify?: string};
        posted.push({p, body});
        const t = tasks.find(x => x.id === decodeURIComponent(p.split('/')[4]!))! as {followers?: Follower[]; id: string};
        const who = body.actor || 'human:ana', had = (t.followers || []).find(f => f.actor === who);
        t.followers = (t.followers || []).filter(f => f.actor !== who);
        if (body.following) t.followers.push({actor: who, notify: body.notify || had?.notify || 'live', reason: had?.reason || 'manual'});
        const mine = t.followers.find(f => f.actor === 'human:ana');
        return route.fulfill({contentType: 'application/json', body: JSON.stringify({task_id: t.id, following: !!mine, notify: mine?.notify ?? null, followers: t.followers})});
      });
      await page.route(url => /^\/api\/v2\/bots\/[^/]+\/definition$/.test(url.pathname), route => {
        if (route.request().method() !== 'POST') return route.fallback();
        posted.push({p: new URL(route.request().url()).pathname, body: route.request().postDataJSON()});
        return route.fulfill({contentType: 'application/json', body: '{}'});
      });
    };
    const {page, errors, posts} = await open(browser, {hash: '#/task/t-11', tasks, needs: [], bots: BOTS, beforeGoto: routes});
    const win = page.locator('#task-modal[open]');

    // Visible to: Private. Ana asked for it, so she may make it the team's.
    const seen = win.locator('[data-prop-row="visible"]');
    await seen.locator('[data-prop="visible"]').waitFor();
    assert.equal(await seen.getAttribute('data-visible'), 'private');

    // Private · names: requester, owner and one reader; only the reader comes off; + adds one (not the owner).
    await seen.locator('[data-prop-clear-reader="human:sam"]').waitFor();
    assert.equal(await seen.locator('.prop-chip').count(), 3, 'requester, owner and one reader');
    assert.equal(await seen.locator('.prop-x').count(), 1, 'only a reader comes off');
    await seen.locator('[data-prop-clear-reader="human:sam"]').click();
    await page.waitForFunction(() => !document.querySelector('#task-modal [data-prop-clear-reader]'));
    assert.deepEqual(posted.at(-1), {p: '/api/v2/tasks/t-11/readers', body: {remove: ['human:sam']}});
    await seen.locator('[data-prop="readers"]').click();
    const pick = page.locator('#task-modal .prop-pop [data-prop-pick="bot:pm"]');
    await pick.waitFor();
    assert.equal(await page.locator('#task-modal .prop-pop [data-prop-pick="bot:writer"]').count(), 0, 'the owner is not offered');
    await pick.click();
    await seen.locator('[data-prop-clear-reader="bot:pm"]').waitFor();
    assert.deepEqual(posted.at(-1), {p: '/api/v2/tasks/t-11/readers', body: {add: ['bot:pm']}});

    // Followers: the owner live (no level), Sam at Digest; Follow, then a level; × takes Sam off.
    const fol = win.locator('[data-prop-row="followers"]');
    const followerWords = () => fol.locator('.prop-chip .prop-txt').allTextContents();
    const named = await followerWords();
    assert.equal(named.length, 2, 'the owner and one chosen follower');
    assert.match(named[1]!, / · Digest$/, 'a level shows when it is not live');
    assert.doesNotMatch(named[0]!, /·/, 'live shows no level');
    await fol.locator('[data-prop-follow]').click();
    await fol.locator('[data-prop="follow-me"]').waitFor();
    assert.deepEqual(posted.at(-1), {p: '/api/v2/tasks/t-11/followers', body: {following: true}});
    await fol.locator('[data-prop="follow-me"]').click();
    await page.locator('#task-modal .prop-pop [data-prop-pick="muted"]').click();
    await fol.locator('[data-prop="follow-me"][aria-label="Following: Muted"]').waitFor();
    assert.deepEqual(posted.at(-1)!.body, {following: true, notify: 'muted'});
    await fol.locator('[data-prop-unfollow="human:sam"]').click();
    await fol.locator('[data-prop-unfollow="human:sam"]').waitFor({state: 'detached'});
    assert.deepEqual(posted.at(-1)!.body, {actor: 'human:sam', following: false});

    // Making it the team's sends private: false with the version.
    await seen.locator('[data-prop="visible"]').click();
    await page.locator('#task-modal .prop-pop [data-prop-pick="team"]').click();
    await page.locator('#task-modal [data-prop-row="visible"][data-visible="team"]').waitFor();
    const edit = posts.find(x => x.p === '/api/v2/tasks/t-11')!;
    assert.equal(edit.body['private'], false);

    // A company task: Team, no people to add.
    await page.evaluate(() => { location.hash = '#/task/t-12'; });
    await page.locator('#task-modal[open][data-task="t-12"] [data-prop-row="visible"]', {hasText: 'Team'}).waitFor();
    assert.equal(await win.locator('[data-prop-row="visible"] [data-prop="readers"]').count(), 0);
    // No parent: nothing to add one with (backend/task_relations.py relate refuses a parent).
    assert.equal(await win.locator('[data-prop-row="parent"] [data-prop="parent"]').count(), 0, 'no Add parent');
    assert.match(await win.locator('[data-prop-row="parent"]').innerText(), /None/);

    // Filed by Postal mail on Ana's rights: Asked by names Postal mail; Who can see still lists Ana, not Postal mail.
    await page.evaluate(() => { location.hash = '#/task/t-15'; });
    const filed = page.locator('#task-modal[open][data-task="t-15"]');
    await filed.locator('[data-prop-row="asker"]').waitFor();
    assert.match(await filed.locator('[data-prop-row="asker"]').innerText(), /Postal mail/);
    const filedSeen = await filed.locator('[data-prop-row="visible"]').innerText();
    assert.doesNotMatch(filedSeen, /Postal mail/, 'the filer is not someone who can see it');
    assert.match(filedSeen, /You/, 'the requester still can');

    // A parent the reader can open: a link to it, nothing to change or take off.
    await page.evaluate(() => { location.hash = '#/task/t-14'; });
    const up = page.locator('#task-modal[open][data-task="t-14"] [data-prop-row="parent"]');
    await up.waitFor();
    assert.equal(await up.locator('[data-open-task="t-12"]').count(), 1, 'links to the parent');
    assert.equal(await up.locator('[data-prop-clear="parent"], [data-prop="parent"]').count(), 0, 'no change or remove');
    // The Related menu: only what relate writes.
    // An empty Related section is under ⋯ › Activity and links (task #409).
    await page.locator('#task-modal[open][data-task="t-14"] [data-task-more]').click();
    await page.locator('#task-modal .prop-pop').getByRole('menuitem', {name: 'Activity and links'}).click();
    await page.locator('#task-modal[open][data-task="t-14"] [data-related-add]').click();
    const kinds = await page.locator('#task-modal .prop-pop [data-prop-pick]').evaluateAll(els => els.map(e => (e as HTMLElement).dataset['propPick']));
    assert.deepEqual(kinds, ['related', 'duplicate_of', 'follow_up']);
    await page.keyboard.press('Escape');

    // A parent the reader cannot open: "a private task", no link, nothing of it in the window.
    await page.evaluate(() => { location.hash = '#/task/t-13'; });
    const parent = page.locator('#task-modal[open][data-task="t-13"] [data-prop-row="parent"]');
    await parent.waitFor();
    assert.match(await parent.innerText(), /a private task/);
    assert.equal(await parent.locator('[data-open-task], a').count(), 0, 'not a link');
    assert.doesNotMatch(await page.locator('#task-modal').innerHTML(), new RegExp(SECRET + '|data-open-task="null"'));

    // Filed under a private parent: the task is private.
    await page.evaluate(() => { location.hash = '#/tasks'; });
    await page.evaluate(() => openTaskCreate('bot:writer', {parent: {id: 't-11', title: 'Draft the newsletter', private: true}}));
    const create = page.locator('#task-create[open]');
    await create.locator('[name=title]').fill('Pick the font');
    await create.locator('[name=body]').fill('For the newsletter.');
    await create.locator('[type=submit]').click();
    await page.waitForFunction(() => !(document.querySelector('#task-create') as HTMLDialogElement).open);
    const made = posts.find(x => x.p === '/api/v2/tasks')!.body;
    assert.equal(made['private'], true, 'a private parent makes it private');
    assert.equal(made['parent'], 't-11');

    // A bot's settings: private by default, and its update subscriptions; only a change is sent.
    await page.evaluate(() => { location.hash = '#/settings'; });
    await page.locator('#bot-editor').waitFor({state: 'attached'});
    await page.evaluate(async () => { await loadSettings(); settingsEditBot('pm'); });
    const settings = page.locator('#pane-settings #bot-editor');
    await settings.locator('[name=private_tasks_default]').waitFor();
    assert(await settings.locator('[name=private_tasks_default]').isChecked());
    assert(await settings.locator('[name=update_templates]').isChecked());
    assert(!(await settings.locator('[name=update_tico]').isChecked()));
    await page.setViewportSize({width: 390, height: 844});
    await settings.locator('[name=update_tico]').check();
    await settings.locator('[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#bot-editor [data-bot-status]')?.textContent === 'Saved');
    assert.deepEqual(posted.find(x => x.p === '/api/v2/bots/pm/definition')!.body['update_subscriptions'], ['templates', 'tico']);
    assert.deepEqual(errors, []);
    console.log('task privacy: Visible to (Private · names, Team), Followers, a hidden parent named only "a private task", private under a private parent, bot defaults: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
