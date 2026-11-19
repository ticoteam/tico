// Machine status uses live runner presence ahead of a stale cloud lifecycle record, at desktop and phone widths.
import type * as PageSupport from './support/page.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {uiFile} = require('./support/page.cts') as PageSupport.Api;

// Defined by app/computer-detail.ts and app/cloud-machines.ts, which the page loads below.
declare function machineNowWords(machine: unknown): string;
declare function cloudMachineComputerHTML(machine: unknown): string;

interface Machine {
  last_seen_at: string;
  online: boolean;
  cloud_machine: {state: string; restarting?: boolean};
  turn_counts?: Record<string, number>;
  revoked_at?: string;
}

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      await page.setContent('<div id="status"></div>');
      await page.addScriptTag({content: `const esc = value => String(value); var CLOUD_ACCESS = null;`});
      // cloud-machines is TypeScript: uiFile serves its compiled file (npm run build:ui). The shell's names
      // (pages-shell.ts, SETTINGS_PRESENCE among them) load first, as in the app.
      for (const script of ['app/pages-shell.ts', 'app/computer-detail.ts', 'app/settings-devices.ts', 'app/cloud-machines.ts']) {
        await page.addScriptTag({path: uiFile(script)});
      }
      await page.addScriptTag({content: `SETTINGS_PRESENCE.at = Date.now();`});
      const status = await page.evaluate(() => {
        const machine: Machine = {last_seen_at: new Date().toISOString(), online: true, cloud_machine: {state: 'stopped'}};
        const live = machineNowWords(machine);
        machine.turn_counts = {running: 1};
        const busy = machineNowWords(machine);
        machine.turn_counts = {};
        machine.last_seen_at = new Date(Date.now() - 300000).toISOString();
        machine.online = false; // the server's flag: no contact inside its online window
        const asleep = machineNowWords(machine);
        machine.cloud_machine.state = 'starting';
        const offline = machineNowWords(machine);
        machine.cloud_machine.restarting = true;
        const restarting = machineNowWords(machine);
        machine.revoked_at = new Date().toISOString();
        const revoked = machineNowWords(machine);
        // The hub's employee payload reconciles a stale lifecycle state with live runner contact.
        document.querySelector('#status')!.innerHTML = cloudMachineComputerHTML({cloud_machine: {
          mode: 'cloud', placement: 'own', state: 'running', size: 'default', machine_size: 'default'
        }});
        return {live, busy, asleep, offline, restarting, revoked};
      });
      assert.deepEqual(status, {live: 'idle', busy: '1 running', asleep: 'asleep', offline: 'offline',
        restarting: 'Restarting', revoked: 'revoked'});
      assert.match(await page.locator('#status').innerText(), /Own cloud machine · Running · Default/);
      // Out of credit is each machine's own word: with the company frozen, the machine HQ refused says so with what
      // waits, and another that is only asleep stays asleep.
      await page.addScriptTag({content: `CLOUD_ACCESS = {access: 'frozen', reason: 'out_of_credit'};`});
      const credit = await page.evaluate(() => {
        const old = new Date(Date.now() - 300000).toISOString();
        return {
          refused: machineNowWords({last_seen_at: old, online: false, turn_counts: {queued: 3}, cloud_machine: {state: 'stopped', problem: 'Out of credit'}}),
          asleep: machineNowWords({last_seen_at: old, online: false, cloud_machine: {state: 'stopped', problem: ''}}),
        };
      });
      assert.deepEqual(credit, {refused: 'Out of credit · 3 queued', asleep: 'asleep'});
      await page.close();
    }
    console.log('computer status ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
