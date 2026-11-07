// Machine status uses live runner presence ahead of a stale cloud lifecycle record, at desktop and phone widths.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({viewport: {width, height: 900}});
      await page.setContent('<div id="status"></div>');
      await page.addScriptTag({content: `const SETTINGS_PRESENCE = {at: Date.now()}; const esc = value => String(value);`});
      for (const script of ['computer-detail', 'settings-devices', 'cloud-machines']) {
        await page.addScriptTag({path: path.join(__dirname, '../app', script + '.js')});
      }
      const status = await page.evaluate(() => {
        const machine = {last_seen_at: new Date().toISOString(), cloud_machine: {state: 'stopped'}};
        const live = machineNowWords(machine);
        machine.turn_counts = {running: 1};
        const busy = machineNowWords(machine);
        machine.turn_counts = {};
        machine.last_seen_at = new Date(Date.now() - 300000).toISOString();
        const asleep = machineNowWords(machine);
        machine.cloud_machine.state = 'starting';
        const offline = machineNowWords(machine);
        machine.revoked_at = new Date().toISOString();
        const revoked = machineNowWords(machine);
        // The hub's employee payload reconciles a stale lifecycle state with live runner contact.
        document.querySelector('#status').innerHTML = cloudMachineComputerHTML({cloud_machine: {
          mode: 'cloud', placement: 'own', state: 'running', size: 'default', machine_size: 'default'
        }});
        return {live, busy, asleep, offline, revoked};
      });
      assert.deepEqual(status, {live: 'idle', busy: '1 running', asleep: 'asleep', offline: 'offline', revoked: 'revoked'});
      assert.match(await page.locator('#status').innerText(), /Own cloud machine · Running · Default/);
      await page.close();
    }
    console.log('computer status ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
