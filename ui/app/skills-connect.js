/* ui/app/skills-connect.js — #/skills/connect?code=: approve or deny the skills helper on a person's own computer
   (clients/skill_helper.mjs setup, backend/skill_devices.py). Classic script, globals shared with ui/app/. */
'use strict';

const SKILLS_CONNECT = '#/skills/connect';
let SKILLS_CONNECT_LOAD = 0;

async function pageSkillsConnect() {
  const load = ++SKILLS_CONNECT_LOAD;
  const code = new URLSearchParams(S.route.split('?')[1] || '').get('code') || '';
  $('#main').innerHTML = `<div class="skills-connect"><h1>Connect Claude Code</h1><div id="skills-connect-body"><div class="empty">Loading…</div></div></div>`;
  const body = () => $('#skills-connect-body');
  let shown;
  try { shown = await get(`/v2/skills/devices/codes/${encodeURIComponent(code)}`, {fresh: true}); }
  catch (e) { if (load === SKILLS_CONNECT_LOAD && body()) body().innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  if (load !== SKILLS_CONNECT_LOAD || !body()) return;
  body().innerHTML = `<form class="skills-connect-form">
      <p><strong>${esc(shown.label || 'This computer')}</strong> · <code>${esc(shown.user_code)}</code></p>
      <label class="skills-connect-check"><input type="checkbox" name="reporting">Share which skills I use</label>
      <div class="row"><button class="primary" type="submit">Approve</button><button class="ghost" type="button" data-deny>Deny</button></div>
    </form>`;
  const form = body().querySelector('form');
  const decide = async approve => {
    form.querySelectorAll('button').forEach(b => { b.disabled = true; });
    const path = `/v2/skills/devices/codes/${encodeURIComponent(code)}/${approve ? 'approve' : 'deny'}`;
    try {
      await post(path, {reporting_enabled: approve && form.reporting.checked});
      if (body()) body().innerHTML = `<p>${approve ? 'Connected. Return to your terminal.' : 'Denied.'}</p>`;
    } catch (e) {
      toast(e.message, true);
      form.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  };
  form.onsubmit = event => { event.preventDefault(); void decide(true); };
  form.querySelector('[data-deny]').onclick = () => void decide(false);
}
