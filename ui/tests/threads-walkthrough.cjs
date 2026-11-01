// A walkthrough of the whole threads feature on a real hub (ui/tests/support/real-hub.cjs): each journey runs at desktop
// (1440x900) and at phone width (390x844), and screenshots every screen it reaches. Nothing here asserts copy; the pictures
// are read by a person. What the script does check, and records in results.json:
//   - a step that fails (the screen is shot as FAILED-<step>, and the journey goes on with the next step);
//   - a page error raised by the app;
//   - sideways scrolling at phone width;
//   - the words "channel" and "room", raw ids and JSON showing in the visible text.
// Screenshots go to TICO_SHOTS, or $RUNNER_TEMP/remote/threads-walkthrough on a RunsOn VM (its folder is uploaded as
// the run's artifact), or .remote-runs/threads-walkthrough here. Run: node ui/tests/threads-walkthrough.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

const REPO = path.join(__dirname, '..', '..');
const SHOTS = process.env.TICO_SHOTS || path.join(process.env.RUNNER_TEMP ? path.join(process.env.RUNNER_TEMP, 'remote')
  : path.join(REPO, '.remote-runs'), 'threads-walkthrough');
const WAIT = t(15000);
const PLAN = 'Launch is Tuesday';
const SECRET = 'Pricing war room only';
const KICKOFF = 'Kickoff notes';
const RISKS = 'Sub question about the risks';
const HOME_NOTE = 'Standup moves to ten';
const BOT = 'builder-claude';
const HELD = 'Q4 price list: 40 percent off';

fs.mkdirSync(SHOTS, {recursive: true});
const results = [], audit = [], pageErrors = [];
let seq = 0;

const sleep = ms => new Promise(resolve => setTimeout(resolve, t(ms)));

// One API call as a teammate, by token (as the existing real-hub tests make them).
async function api(hub, teammate, method, p, body) {
  const r = await fetch(hub.url + '/api' + p, {method, headers: {Authorization: 'Bearer ' + hub.tokens[teammate],
    'content-type': 'application/json', ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})},
    body: body === undefined ? undefined : JSON.stringify(body)});
  const json = await r.json().catch(() => ({}));
  assert.ok(r.status < 300, `${method} ${p} as ${teammate}: ${r.status} ${JSON.stringify(json)}`);
  return json;
}

// Builder-claude: everyone may see it, only Dana may read or write its Home (as real-hub.cjs sets it up).
async function restrictBot(hub) {
  const current = await api(hub, 'dana', 'GET', `/v2/bots/${BOT}/access`);
  const everyone = {everyone: true, people: [], teams: [], bots: []};
  const dana = {everyone: false, people: ['dana'], teams: [], bots: []};
  await api(hub, 'dana', 'PUT', `/v2/bots/${BOT}/access`, {see: everyone, read: dana, write: dana, revision: current.revision});
}

// Priya's Home: only she reads it, so a peer reads it only as her manager (people.manages).
async function restrictPerson(hub) {
  const current = await api(hub, 'dana', 'GET', '/v2/humans/priya/access');
  const everyone = {everyone: true, people: [], teams: [], bots: []};
  const priya = {everyone: false, people: ['priya'], teams: [], bots: []};
  await api(hub, 'dana', 'PUT', '/v2/humans/priya/access', {see: everyone, read: priya, write: priya, revision: current.revision});
}

// The words and shapes a person should not see. Snippets are short, so the report can quote them.
const COPY = /\b(channels?|rooms?)\b/gi;
const RAW_ID = /\b(?:t|h|m|msg|call|gi|bot|human|person|team)[-_:][0-9a-f]{8,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}\b/gi;
const JSON_SHAPE = /[{\[]\s*"[a-z_]+"\s*:/g;
function scanText(text, shot) {
  const found = [];
  for (const [kind, re] of [['copy', COPY], ['raw-id', RAW_ID], ['json', JSON_SHAPE]]) {
    for (const m of text.matchAll(re)) {
      const at = m.index;
      found.push({shot, kind, text: text.slice(Math.max(0, at - 50), at + m[0].length + 30).replace(/\s+/g, ' ')});
      if (found.length > 40) return found;
    }
  }
  return found;
}

// A screenshot of the page as it is now. Phone shots also record any sideways scroll.
async function snap(page, label, phone) {
  await sleep(500);
  const name = `${String(++seq).padStart(3, '0')}-${label}-${phone ? 'phone' : 'desktop'}`;
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
  const scan = await page.evaluate(() => ({
    text: document.body.innerText || '',
    sideways: document.documentElement.scrollWidth - window.innerWidth,
  }));
  if (phone && scan.sideways > 1) audit.push({shot: name, kind: 'sideways', text: `${scan.sideways}px wider than the phone`});
  audit.push(...scanText(scan.text, name));
  return name;
}

// One step inside a journey. A failure shoots the screen as it is and records it, and the journey goes on.
async function step(page, label, phone, name, fn) {
  try {
    await fn();
    results.push({journey: name, step: label, ok: true});
  } catch (error) {
    const message = String(error.message || error).split('\n')[0];
    results.push({journey: name, step: label, ok: false, error: message});
    try { await snap(page, 'FAILED-' + label.replace(/\W+/g, '-'), phone); } catch { /* the page is gone */ }
  }
}

// A journey at both widths. `who` is the teammate; `body(page, phone, name)` runs the steps.
async function journey(browser, hub, name, who, body, {phones = [false, true]} = {}) {
  for (const phone of phones) {
    let page;
    try {
      page = await pageAs(browser, hub, who, {phone});
    } catch (error) {
      results.push({journey: name, step: 'open the page', ok: false, error: String(error.message).split('\n')[0]});
      continue;
    }
    page.on('pageerror', error => pageErrors.push({journey: name, phone, error: String(error.message).split('\n')[0]}));
    page.on('dialog', dialog => dialog.accept());
    try {
      await body(page, phone, name + (phone ? ' (phone)' : ' (desktop)'));
    } catch (error) {
      results.push({journey: name, step: 'journey', ok: false, error: String(error.message).split('\n')[0]});
    } finally {
      await page.context().close().catch(() => {});
    }
  }
}

const shell = (page, route, waitFor = '#main') => async () => {
  await page.goto(page.url().split('#')[0] + route);
  await page.locator(waitFor).first().waitFor({state: 'attached', timeout: WAIT});
  await sleep(600);
};

(async () => {
  const hub = await startHub({seeds: ['org', 'groups', 'connection', 'toured']});
  let browser;
  const S = {};
  try {
    // ------------------------------------------------------------------ the world the journeys start from
    S.plan = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Launch plan', audience: 'company', members: [], body: PLAN});
    S.kickoff = await api(hub, 'dana', 'POST', `/v2/threads/${S.plan.id}/messages`, {text: KICKOFF, kind: 'say'});
    S.risks = await api(hub, 'dana', 'POST', `/v2/threads/${S.plan.id}/messages`,
      {text: RISKS, kind: 'say', parent_message_id: S.kickoff.id});
    S.draft = await api(hub, 'dana', 'POST', `/v2/threads/${S.plan.id}/messages`, {text: 'Price list draft v1', kind: 'say'});
    S.secret = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Pricing war room', audience: 'members', members: [], body: SECRET});
    const groups = await api(hub, 'dana', 'GET', '/v2/groups');
    S.eng = (groups.groups || groups).find(g => g.name === 'Engineering');
    if (S.eng) {
      S.teamThread = await api(hub, 'dana', 'POST', '/v2/threads', {title: 'Engineering standup', audience: 'team',
        audience_group_id: S.eng.id, members: [], body: 'Standup notes go here.'});
    }
    S.dm = await api(hub, 'dana', 'POST', '/v2/threads/dm', {members: ['human:priya'], body: 'Can you take the pricing deck?'});
    await api(hub, 'priya', 'POST', `/v2/threads/${S.plan.id}/messages`, {text: 'Omar, can you check the risks list before Tuesday?', kind: 'say'});
    const priyaHome = await api(hub, 'priya', 'GET', '/v2/threads/home/' + encodeURIComponent('human:priya'));
    await api(hub, 'priya', 'POST', `/v2/threads/${priyaHome.id}/messages`, {text: HOME_NOTE, kind: 'say'});
    await restrictBot(hub);
    await restrictPerson(hub);
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

    // ------------------------------------------------------------------ dashboard and Company tab
    await journey(browser, hub, 'dashboard', 'dana', async (page, phone, name) => {
      await step(page, 'my dashboard', phone, name, shell(page, '#/home'));
      await step(page, 'my dashboard shot', phone, name, () => snap(page, 'my-dashboard', phone));
      await step(page, 'company tab', phone, name, async () => {
        await shell(page, '#/overview')();
        await snap(page, 'company-tab', phone);
      });
      await step(page, 'add a card', phone, name, async () => {
        await shell(page, '#/home')();
        await page.locator('[data-dash-add="card"]').first().click();
        const dialog = page.locator('dialog[open], [role="dialog"]').first();
        await dialog.waitFor({timeout: WAIT});
        await snap(page, 'add-card-dialog', phone);
        await dialog.locator('input[type="radio"], select').first().click().catch(() => {});
        await dialog.locator('button[type="submit"], button.primary').last().click();
        await sleep(800);
        await snap(page, 'card-added', phone);
      });
      await step(page, 'arrange cards', phone, name, async () => {
        await page.locator('button', {hasText: /arrange|edit layout|reorder|done/i}).first().click({timeout: WAIT});
        await snap(page, 'arranging', phone);
      });
    });

    // ------------------------------------------------------------------ sidebar: threads, DMs, unread, Needs attention
    await journey(browser, hub, 'sidebar', 'omar', async (page, phone, name) => {
      await step(page, 'sidebar threads', phone, name, async () => {
        await shell(page, '#/tasks', '#th-list')();
        await page.locator('#th-list .th-row-title', {hasText: 'Launch plan'}).first().waitFor({timeout: WAIT});
        await snap(page, 'sidebar-threads', phone);
      });
      await step(page, 'threads page', phone, name, async () => {
        await shell(page, '#/threads')();
        await snap(page, 'threads-page', phone);
      });
      await step(page, 'dms page', phone, name, async () => {
        await shell(page, '#/threads?view=dms')();
        await snap(page, 'dms-page', phone);
      });
    });

    // ------------------------------------------------------------------ new thread: private, then to a team and the company
    await journey(browser, hub, 'new thread', 'dana', async (page, phone, name) => {
      await step(page, 'open new thread', phone, name, async () => {
        await shell(page, '#/threads')();
        await page.locator('[data-thp-new]').click();
        await page.locator('dialog[open]').first().waitFor({timeout: WAIT});
        await snap(page, 'new-thread-dialog', phone);
      });
      await step(page, 'private by default', phone, name, async () => {
        const checked = await page.locator('dialog[open] input[name="audience"]:checked').getAttribute('value');
        assert.equal(checked, 'members', 'a new thread is private to its members');
      });
      await step(page, 'create it', phone, name, async () => {
        const dialog = page.locator('dialog[open]').first();
        await dialog.locator('input[type="text"], input:not([type])').first().fill('Q4 pricing draft');
        await dialog.locator('textarea').first().fill('Draft the Q4 price list for review.');
        await dialog.locator('button[type="submit"]').click();
        await page.waitForURL(/#\/thread\//, {timeout: WAIT});
        await page.locator('.thv-composer .p-text').waitFor({timeout: WAIT});
        await snap(page, 'new-thread-created', phone);
      });
      await step(page, 'make it public to a team', phone, name, async () => {
        await page.locator('[data-thv-menu]').first().click();
        await page.locator('.thv-pop [role="menuitem"]', {hasText: 'Who can read'}).click();
        const dialog = page.locator('dialog[open]').first();
        await dialog.waitFor({timeout: WAIT});
        await snap(page, 'who-can-read-private', phone);
        await dialog.locator('input[name="audience"][value="team"]').check();
        await snap(page, 'who-can-read-team-picked', phone);
        await dialog.locator('button[type="submit"]').click();
        await page.locator('dialog[open]').first().waitFor({state: 'detached', timeout: WAIT}).catch(() => {});
        await snap(page, 'thread-shared-to-team', phone);
      });
      await step(page, 'make it public to the company', phone, name, async () => {
        await page.locator('[data-thv-menu]').first().click();
        await page.locator('.thv-pop [role="menuitem"]', {hasText: 'Who can read'}).click();
        const dialog = page.locator('dialog[open]').first();
        await dialog.waitFor({timeout: WAIT});
        await dialog.locator('input[name="audience"][value="company"]').check();
        await dialog.locator('button[type="submit"]').click();
        await sleep(800);
        await snap(page, 'thread-shared-to-company', phone);
      });
    });

    // ------------------------------------------------------------------ a thread with sub-threads, and Make task
    await journey(browser, hub, 'sub-threads', 'dana', async (page, phone, name) => {
      await step(page, 'thread with inline sub-threads', phone, name, async () => {
        await shell(page, `#/thread/${S.plan.id}`, '.thv-composer')();
        await page.locator('.th-subhead', {hasText: KICKOFF}).first().waitFor({timeout: WAIT});
        await snap(page, 'thread-with-subthreads', phone);
      });
      await step(page, 'tap into the sub-thread', phone, name, async () => {
        await page.locator('.th-subhead', {hasText: KICKOFF}).first().click();
        await page.waitForURL(new RegExp('#/thread/' + S.risks.thread_id), {timeout: WAIT});
        await page.getByText(RISKS).waitFor({timeout: WAIT});
        await snap(page, 'subthread-page-breadcrumb', phone);
      });
      await step(page, 'reply in sub-thread', phone, name, async () => {
        await shell(page, `#/thread/${S.plan.id}`, '.thv-composer')();
        await page.locator('[data-th-more]').first().click();
        await page.locator('.thv-pop [role="menuitem"]', {hasText: 'Reply in sub-thread'}).click();
        await page.locator('.th-reply-text').first().fill('Reply from the walkthrough.');
        await page.locator('[data-th-reply-send]').first().click();
        await page.getByText('Reply from the walkthrough.').first().waitFor({timeout: WAIT});
        await snap(page, 'reply-in-subthread', phone);
      });
      await step(page, 'make task from a message', phone, name, async () => {
        await page.locator('[data-th-more]').first().click();
        await page.locator('.thv-pop [role="menuitem"]', {hasText: 'Make task'}).click();
        const dialog = page.locator('dialog[open]').first();
        await dialog.waitFor({timeout: WAIT});
        await snap(page, 'make-task-dialog', phone);
        await dialog.locator('button[type="submit"]').click();
        await sleep(800);
        await snap(page, 'make-task-done', phone);
      });
    });

    // ------------------------------------------------------------------ bot page: Home and DM, and its Access editor
    await journey(browser, hub, 'bot page', 'dana', async (page, phone, name) => {
      await step(page, 'bot Home', phone, name, async () => {
        await shell(page, `#/bot/${BOT}/home`, '#bot-work')();
        await page.locator('.bot-talk-switch [data-talk="home"]').waitFor({timeout: WAIT});
        await snap(page, 'bot-home', phone);
      });
      await step(page, 'bot DM', phone, name, async () => {
        await page.locator('.bot-talk-switch [data-talk="dm"]').click();
        await sleep(800);
        await snap(page, 'bot-dm', phone);
      });
      await step(page, 'bot Access editor', phone, name, async () => {
        await shell(page, `#/bot/${BOT}/home`, '#bot-access-card')();
        await page.locator('#bot-access-card').scrollIntoViewIfNeeded();
        await snap(page, 'bot-access-card', phone);
        await page.locator('#bot-access-card button', {hasText: /^edit$/i}).first().click();
        await page.locator('dialog[open]').first().waitFor({timeout: WAIT});
        await snap(page, 'bot-access-editor', phone);
        await page.keyboard.press('Escape');
      });
    });

    // ------------------------------------------------------------------ person page: Home, DM, Access editor
    await journey(browser, hub, 'person page', 'dana', async (page, phone, name) => {
      await step(page, 'person Home', phone, name, async () => {
        await shell(page, '#/person/priya', '#person-home-thread')();
        await page.locator('#person-home').waitFor({timeout: WAIT});
        await snap(page, 'person-home', phone);
      });
      await step(page, 'person DM', phone, name, async () => {
        await shell(page, `#/thread/${S.dm.id}`, '.thv-composer')();
        await snap(page, 'person-dm', phone);
      });
      await step(page, 'person Access editor', phone, name, async () => {
        await shell(page, '#/person/priya', '#person-access')();
        await page.locator('#person-access-edit').click();
        await page.locator('dialog[open]').first().waitFor({timeout: WAIT});
        await snap(page, 'person-access-editor', phone);
        await page.keyboard.press('Escape');
      });
    });

    // ------------------------------------------------------------------ group page: Home and Access editor
    await journey(browser, hub, 'group page', 'dana', async (page, phone, name) => {
      if (!S.eng) { results.push({journey: name, step: 'find the Engineering group', ok: false, error: 'no group named Engineering'}); return; }
      await step(page, 'group Home', phone, name, async () => {
        await shell(page, '#/team/' + encodeURIComponent(S.eng.id))();
        await snap(page, 'group-home', phone);
      });
      await step(page, 'group Access editor', phone, name, async () => {
        const section = page.locator('section, .card').filter({hasText: 'Access'}).first();
        await section.getByRole('button', {name: /^edit$/i}).first().click();
        await page.locator('dialog[open]').first().waitFor({timeout: WAIT});
        await snap(page, 'group-access-editor', phone);
        await page.keyboard.press('Escape');
      });
    });

    // ------------------------------------------------------------------ see-only and no-access views, as Omar
    await journey(browser, hub, 'see only', 'omar', async (page, phone, name) => {
      await step(page, 'bot he may only see', phone, name, async () => {
        await shell(page, `#/bot/${BOT}`, '#bot-work')();
        await snap(page, 'bot-see-only', phone);
      });
      await step(page, 'person whose Home he may not read', phone, name, async () => {
        await shell(page, '#/person/priya', '#person-access')();
        await snap(page, 'person-see-only', phone);
      });
    });
    await journey(browser, hub, 'no access', 'omar', async (page, phone, name) => {
      await step(page, 'members-only thread by URL', phone, name, async () => {
        await shell(page, `#/thread/${S.secret.id}`, 'body')();
        await page.getByText('Not found').waitFor({timeout: WAIT});
        assert.ok(!(await page.content()).includes(SECRET), 'the members-only text is not on the page');
        await snap(page, 'no-access-404', phone);
      });
    });

    // ------------------------------------------------------------------ held message and held edit (an org rule through the API)
    let ruleMade = false;
    await journey(browser, hub, 'held message and edit', 'dana', async (page, phone, name) => {
      await step(page, 'set up the rule', phone, name, async () => {
        if (ruleMade) return;
        await api(hub, 'dana', 'POST', '/v2/rules', {kind: 'internal', text: 'Do not post the Q4 price list in company threads',
          who: {everyone: true}});
        ruleMade = true;
      });
      await step(page, 'held message', phone, name, async () => {
        await shell(page, `#/thread/${S.plan.id}`, '.thv-composer')();
        await page.locator('.thv-composer .p-text').fill(HELD);
        await page.locator('.thv-composer .p-send').click();
        await sleep(2500);
        await snap(page, 'held-message', phone);
      });
      await step(page, 'held edit', phone, name, async () => {
        const row = page.locator('[data-message]', {hasText: 'Price list draft v1'}).first();
        await row.locator('[data-th-more]').click();
        await page.locator('.thv-pop [role="menuitem"]', {hasText: 'Edit'}).click();
        await row.locator('textarea').first().fill(HELD);
        await page.locator('[data-th-edit-save]').first().click();
        await sleep(2500);
        await snap(page, 'held-edit', phone);
      });
    });

    // ------------------------------------------------------------------ calls: start (Dana), join (Omar), end, action items
    for (const phone of [false, true]) {
      const name = 'call' + (phone ? ' (phone)' : ' (desktop)');
      let dana, omar;
      try {
        dana = await pageAs(browser, hub, 'dana', {phone});
        omar = await pageAs(browser, hub, 'omar', {phone: false});
        dana.on('dialog', d => d.accept());
        omar.on('dialog', d => d.accept());
        await step(dana, 'start a call', phone, name, async () => {
          await shell(dana, `#/thread/${S.plan.id}`, '.thv-composer')();
          await dana.locator('button[data-call="start"]').click();
          await sleep(3000);
          await snap(dana, 'call-started', phone);
        });
        await step(omar, 'join the call', false, name, async () => {
          await shell(omar, `#/thread/${S.plan.id}`, '.thv-composer')();
          await omar.locator('button[data-call="join"]').click();
          await sleep(3000);
          await snap(omar, 'call-joined', false);
        });
        await step(dana, 'end the call', phone, name, async () => {
          await dana.locator('button[data-cb="end"]').click();
          await sleep(2000);
          await snap(dana, 'call-ended', phone);
        });
        await step(dana, 'action items', phone, name, async () => {
          await shell(dana, `#/thread/${S.plan.id}`, '.thv-composer')();
          await snap(dana, 'call-action-items', phone);
        });
      } catch (error) {
        results.push({journey: name, step: 'journey', ok: false, error: String(error.message).split('\n')[0]});
      } finally {
        await dana?.context().close().catch(() => {});
        await omar?.context().close().catch(() => {});
      }
    }

    // ------------------------------------------------------------------ Connect: Slack, Other companies, search, Catch up, Make task
    await journey(browser, hub, 'connect', 'dana', async (page, phone, name) => {
      await step(page, 'connections page', phone, name, async () => {
        await shell(page, '#/library/connections', '#cn-slack')();
        await snap(page, 'connect-page', phone);
      });
      await step(page, 'Slack section', phone, name, async () => {
        await page.locator('#cn-slack').scrollIntoViewIfNeeded();
        await snap(page, 'connect-slack', phone);
      });
      await step(page, 'Other companies section', phone, name, async () => {
        await page.locator('#cn-peers').scrollIntoViewIfNeeded();
        await snap(page, 'connect-other-companies', phone);
      });
      await step(page, 'search threads', phone, name, async () => {
        await shell(page, '#/threads')();
        await page.locator('#th-search').click();
        await page.locator('#thp-q').fill('Launch');
        await sleep(1200);
        await snap(page, 'thread-search', phone);
      });
      await step(page, 'Catch up', phone, name, async () => {
        await shell(page, `#/thread/${S.plan.id}`, '.thv-composer')();
        const control = page.getByText(/catch.?up/i);
        assert.ok(await control.count() > 0, 'no "Catch up" control on the thread page');
        await control.first().click();
        await snap(page, 'catch-up', phone);
      });
    });

    console.log('threads-walkthrough: done');
  } catch (error) {
    console.error('threads-walkthrough: setup failed:', error);
    results.push({journey: 'setup', step: 'world', ok: false, error: String(error.message || error).split('\n')[0]});
  } finally {
    const failed = results.filter(r => !r.ok);
    const report = {shots: SHOTS, shots_taken: seq, failed, page_errors: pageErrors, audit, results};
    fs.writeFileSync(path.join(SHOTS, 'results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({shots: SHOTS, shots_taken: seq, steps: results.length, failed: failed.length,
      page_errors: pageErrors.length, audit: audit.length}, null, 2));
    for (const f of failed) console.log('FAILED', f.journey, '|', f.step, '|', f.error);
    for (const a of audit) console.log('AUDIT', a.shot, a.kind, '|', a.text);
    for (const p of pageErrors) console.log('PAGE ERROR', p.journey, '|', p.error);
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
