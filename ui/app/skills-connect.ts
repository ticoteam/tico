/* ui/app/skills-connect.ts — #/skills/connect?code=: approve or deny the skills helper on a person's own computer
   (clients/skill_helper.mjs setup, backend/skill_devices.py). Classic script, globals shared with ui/app/. */
'use strict';

// The device code /v2/skills/devices/codes/{code} answers with. get() and post() are untyped JS, so the shape is declared.
interface SkillsDeviceCode { label?: string | null; user_code?: string | null; }

// The `!`: the markup this file just drew always has the element; a missing one throws on first use, as the old lookup did.
function skillsEl<T extends Element>(root: ParentNode, selector: string): T { return root.querySelector<T>(selector)!; }

let SKILLS_CONNECT_LOAD = 0;

async function pageSkillsConnect() {
  const load = ++SKILLS_CONNECT_LOAD;
  const code = new URLSearchParams(S.route.split('?')[1] || '').get('code') || '';
  skillsEl<HTMLElement>(document, '#main').innerHTML = `<div class="skills-connect"><h1>Connect Claude Code</h1><div id="skills-connect-body"><div class="empty">Loading…</div></div></div>`;
  const body = (): HTMLElement | null => document.querySelector<HTMLElement>('#skills-connect-body');
  let shown: SkillsDeviceCode;
  try { shown = await get(`/v2/skills/devices/codes/${encodeURIComponent(code)}`, {fresh: true}); }
  catch (e) { const node = body(); if (load === SKILLS_CONNECT_LOAD && node) node.innerHTML = `<div class="err">${esc(errorMessage(e, ''))}</div>`; return; }
  const current = body();
  if (load !== SKILLS_CONNECT_LOAD || !current) return;
  current.innerHTML = `<form class="skills-connect-form">
      <p><strong>${esc(shown.label || 'This computer')}</strong> · <code>${esc(shown.user_code)}</code></p>
      <label class="skills-connect-check"><input type="checkbox" name="reporting">Share which skills I use</label>
      <div class="row"><button class="primary" type="submit">Approve</button><button class="ghost" type="button" data-deny>Deny</button></div>
    </form>`;
  const form = skillsEl<HTMLFormElement>(current, 'form');
  const decide = async (approve: boolean) => {
    form.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = true; });
    const path = `/v2/skills/devices/codes/${encodeURIComponent(code)}/${approve ? 'approve' : 'deny'}`;
    try {
      await post(path, {reporting_enabled: approve && skillsEl<HTMLInputElement>(form, '[name=reporting]').checked});
      const node = body(); if (node) node.innerHTML = `<p>${approve ? 'Connected. Return to your terminal.' : 'Denied.'}</p>`;
    } catch (e) {
      toast(errorMessage(e, ''), true);
      form.querySelectorAll<HTMLButtonElement>('button').forEach(b => { b.disabled = false; });
    }
  };
  form.onsubmit = event => { event.preventDefault(); void decide(true); };
  skillsEl<HTMLButtonElement>(form, '[data-deny]').onclick = () => void decide(false);
}
