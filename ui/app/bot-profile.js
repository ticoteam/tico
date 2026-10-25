/* ui/app/bot-profile.js — A bot's profile: what wakes it and which tasks it takes (backend/profiles.py)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The picker reads its choices and their one-line descriptions from GET /v2/profiles, so a profile the server adds
// shows with no UI change. The labels here are for history rows and a picker drawn before that answer lands.
const PROFILE_LABELS = {doer: 'Doer', reviewer: 'Reviewer', lead: 'Lead', responder: 'Responder', monitor: 'Monitor', custom: 'Custom'};
const PROFILE_PULLS = [['all', 'Any open task'], ['review', 'Review and QA only'], ['none', 'Nothing']];
const PROFILE_TAKES = [['all', 'Any task'], ['review', 'Review and QA only'], ['watcher', "Its watcher's tasks"], ['none', 'None']];
let PROFILE_LIST = null, PROFILE_LOAD = null;
async function profileChoices() {
  if (PROFILE_LIST) return PROFILE_LIST;
  PROFILE_LOAD ||= get('/v2/profiles').then(d => (PROFILE_LIST = d.profiles || [])).finally(() => { PROFILE_LOAD = null; });
  return PROFILE_LOAD;
}
const profileLabel = name => PROFILE_LIST?.find(p => p.name === name)?.label || PROFILE_LABELS[name] || name || 'None';
const profileHour = h => `${String(h).padStart(2, '0')}:00`;

function profileFieldsHTML(e, choices) {
  const p = e.profile || {}, name = p.name || 'doer', manage = settingsCanManageBot(e), off = manage ? '' : ' disabled';
  const list = choices?.length ? choices : [{name, label: p.label || profileLabel(name), description: p.description || ''}];
  const opt = (v, label, cur) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(label)}</option>`;
  const description = list.find(c => c.name === name)?.description || p.description || '';
  const lead = name === 'lead' ? `<label class="profile-field"><span class="muted">Digest at</span><select class="settings-inline-select" data-profile-key="digest_hour"${off}
      aria-label="Digest hour for ${esc(empName(e.name))}">${Array.from({length: 24}, (_, h) => opt(h, profileHour(h), p.digest_hour ?? 9)).join('')}</select></label>` : '';
  const custom = name === 'custom' ? `<div class="profile-custom" data-profile-custom>
      <label class="profile-field"><span class="muted">Pulls</span><select class="settings-inline-select" data-profile-key="pulls"${off}>${PROFILE_PULLS.map(([v, l]) => opt(v, l, p.pulls)).join('')}</select></label>
      <label class="profile-field"><span class="muted">Takes</span><select class="settings-inline-select" data-profile-key="takes"${off}>${PROFILE_TAKES.map(([v, l]) => opt(v, l, p.takes)).join('')}</select></label>
      <label class="sysr-switch">${peopleSwitch('data-profile-key="sweeps"', !!p.sweeps, !manage, 'Sweeps')}<span>Sweeps</span></label>
      <a href="#/settings/rules?bot=${encodeURIComponent(e.name)}" data-profile-rules>Wake rules</a></div>` : '';
  return `<select class="settings-inline-select" data-bot-profile="${esc(e.name)}" data-current="${esc(name)}" aria-label="Profile for ${esc(empName(e.name))}"${off}>
      ${list.map(c => opt(c.name, c.label, name)).join('')}</select>${lead}${custom}
    <p class="muted profile-words" data-profile-description>${esc(description)}</p>${p.summary ? `<p class="muted profile-words" data-profile-summary>${esc(p.summary)}</p>` : ''}`;
}
function profileRowHTML(e) {
  return `<div class="sb-row"><span>Profile</span><div class="profile-box" data-bot-profile-box="${esc(e.name)}">${profileFieldsHTML(e, null)}</div><span></span></div>`;
}
async function profileMount(rows, slug) {
  const box = rows?.querySelector(`[data-bot-profile-box="${CSS.escape(slug)}"]`); if (!box) return;
  let choices;
  try { choices = await profileChoices(); } catch { return; }
  const e = S.emps.find(row => row.name === slug);
  if (!e || !box.isConnected || box.contains(document.activeElement)) return;
  box.innerHTML = profileFieldsHTML(e, choices);
}

// One save for the editor and a branch: the bot's revision goes with it. Leaving Custom deletes the rules it copied;
// the server answers 409 until that is confirmed, and the confirm says how many.
async function profileSave(slug, body, revision) {
  try { return await post(`/v2/bots/${encodeURIComponent(slug)}/profile`, {...body, expected_revision: revision}); }
  catch (error) {
    if (error.status !== 409 || error.body?.error?.code === 'version_conflict' || body.confirm_drop_rules) throw error;
    let count = 0;
    try { count = (await get(`/v2/bots/${encodeURIComponent(slug)}/profile`)).rules || 0; } catch {}
    const what = count ? `${count} wake rule${count === 1 ? '' : 's'}` : 'its own wake rules';
    if (!confirm(`Leaving Custom deletes ${what} of ${empName(slug)}'s. Go on?`)) return null;
    return post(`/v2/bots/${encodeURIComponent(slug)}/profile`, {...body, expected_revision: revision, confirm_drop_rules: true});
  }
}
async function profileChange(target) {
  const box = target.closest('[data-bot-profile-box]'), slug = box?.dataset.botProfileBox;
  const e = S.emps.find(row => row.name === slug); if (!e) return;
  const current = e.profile?.name || 'doer';
  let body;
  if (target.matches('[data-bot-profile]')) {
    if (target.value === target.dataset.current) return;
    body = {profile: target.value};
  } else {
    const key = target.dataset.profileKey;
    body = {profile: current, [key]: key === 'sweeps' ? target.checked : key === 'digest_hour' ? Number(target.value) : target.value};
  }
  box.querySelectorAll('select, input').forEach(el => { el.disabled = true; });
  try {
    const out = await profileSave(slug, body, e.revision);
    if (out) toast(body.profile === current ? 'Profile updated' : `${empName(slug)} is now ${profileLabel(body.profile)}`);
  } catch (error) { toast(error.message, true); }
  await loadSettings();      // the revision moved, or the picker goes back to what is saved
  if (box.isConnected) box.innerHTML = profileFieldsHTML(S.emps.find(row => row.name === slug) || e, PROFILE_LIST);
}
// A history row's value: the profile and, when it set any, its own numbers.
function profileHistoryWords(value) {
  if (!value?.profile) return 'none';
  const o = value.options || {}, extra = [];
  if (o.digest_hour != null) extra.push('digest ' + profileHour(o.digest_hour));
  if (o.batch_seconds != null) extra.push(`batch ${Math.round(o.batch_seconds / 60)} min`);
  if (o.takes) extra.push('takes ' + o.takes);
  return [profileLabel(value.profile), ...extra].join(' · ');
}
