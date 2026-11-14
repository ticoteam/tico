/* ui/app/bot-profile.ts — A bot's profile: what wakes it and which tasks it takes (backend/profiles.py)
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

type BpChoice = import('../types/api').ProfileChoice;
type BpChoices = import('../types/api').ProfileChoices;
type BpState = import('../types/api').BotProfileState;
type BpOption = Pick<BpChoice, 'name' | 'label' | 'description'>;
// The profile a bot row carries: the saved choice, its one-line words, and its own numbers when it set any.
interface BpProfile { name?: string; label?: string; description?: string; summary?: string; digest_hour?: number | null; pulls?: string | null; takes?: string | null; sweeps?: boolean | null; }
interface BpBot { id: string; can_manage?: boolean; profile?: BpProfile | null; }
// A history row's value for the profile field.
interface BpHistoryValue { profile?: string | null; options?: { digest_hour?: number | null; takes?: string | null } | null; }

const PROFILE_PULLS: Array<[string, string]> = [['all', 'Any open task'], ['review', 'Review and QA only'], ['none', 'Nothing']];
const PROFILE_TAKES: Array<[string, string]> = [['all', 'Any task'], ['review', 'Review and QA only'], ['watcher', "Its watcher's tasks"], ['none', 'None']];
async function profileChoices(): Promise<BpChoice[]> {
  if (PROFILE_LIST) return PROFILE_LIST;
  PROFILE_LOAD ||= get<BpChoices>('/v2/profiles').then(d => (PROFILE_LIST = d.profiles || [])).finally(() => { PROFILE_LOAD = null; });
  return PROFILE_LOAD;
}
const profileLabel = (name: string): string => PROFILE_LIST?.find(p => p.name === name)?.label || PROFILE_LABELS[name] || name || 'None';
const profileHour = (h: number): string => `${String(h).padStart(2, '0')}:00`;

function profileFieldsHTML(e: BpBot, choices: BpOption[] | null): string {
  const p: BpProfile = e.profile || {}, name = p.name || 'doer', manage = settingsCanManageBot(e), off = manage ? '' : ' disabled';
  const list: BpOption[] = choices?.length ? choices : [{name, label: p.label || profileLabel(name), description: p.description || ''}];
  const opt = (v: string | number, label: string, cur: unknown): string => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(label)}</option>`;
  const description = list.find(c => c.name === name)?.description || p.description || '';
  const lead = name === 'lead' ? `<label class="profile-field"><span class="muted">Digest at</span><select class="settings-inline-select" data-profile-key="digest_hour"${off}
      aria-label="Digest hour for ${esc(empName(e.id))}">${Array.from({length: 24}, (_, h) => opt(h, profileHour(h), p.digest_hour ?? 9)).join('')}</select></label>` : '';
  const custom = name === 'custom' ? `<div class="profile-custom" data-profile-custom>
      <label class="profile-field"><span class="muted">Pulls</span><select class="settings-inline-select" data-profile-key="pulls"${off}>${PROFILE_PULLS.map(([v, l]) => opt(v, l, p.pulls)).join('')}</select></label>
      <label class="profile-field"><span class="muted">Takes</span><select class="settings-inline-select" data-profile-key="takes"${off}>${PROFILE_TAKES.map(([v, l]) => opt(v, l, p.takes)).join('')}</select></label>
      <label class="sysr-switch">${peopleSwitch('data-profile-key="sweeps"', !!p.sweeps, !manage, 'Sweeps')}<span>Sweeps</span></label>
      <a href="${AUTOMATIONS}?q=${encodeURIComponent(botDisplayName(e.id))}" data-profile-rules>Automations</a></div>` : '';
  return `<select class="settings-inline-select" data-bot-profile="${esc(e.id)}" data-current="${esc(name)}" aria-label="Profile for ${esc(empName(e.id))}"${off}>
      ${list.map(c => opt(c.name, c.label, name)).join('')}</select>${lead}${custom}
    <p class="muted profile-words" data-profile-description>${esc(description)}</p>${p.summary ? `<p class="muted profile-words" data-profile-summary>${esc(p.summary)}</p>` : ''}`;
}
function profileRowHTML(e: BpBot): string {
  return `<div class="sb-row"><span>Profile</span><div class="profile-box" data-bot-profile-box="${esc(e.id)}">${profileFieldsHTML(e, null)}</div><span></span></div>`;
}
async function profileMount(rows: ParentNode | null | undefined, slug: string): Promise<void> {
  const box = rows?.querySelector(`[data-bot-profile-box="${CSS.escape(slug)}"]`); if (!box) return;
  let choices: BpOption[];
  try { choices = await profileChoices(); } catch { return; }
  const e = botRow(slug);
  if (!e || !box.isConnected || box.contains(document.activeElement)) return;
  box.innerHTML = profileFieldsHTML(e, choices);
}

// One save for the editor and a branch: the bot's revision goes with it. Leaving Custom deletes the rules it copied;
// the server answers 409 until that is confirmed, and the confirm says how many.
async function profileSave(slug: string, body: Record<string, unknown>, revision: number | undefined): Promise<BpState | null> {
  try { return await post(`/v2/bots/${encodeURIComponent(slug)}/profile`, {...body, expected_revision: revision}); }
  catch (error) {
    if (errorStatus(error) !== 409 || errorCode(error) === 'version_conflict' || body.confirm_drop_rules) throw error;
    let count = 0;
    try { count = (await get<BpState>(`/v2/bots/${encodeURIComponent(slug)}/profile`)).rules || 0; } catch {}
    const what = count ? `${count} wake rule${count === 1 ? '' : 's'}` : 'its own wake rules';
    if (!await settingsConfirm({title: 'Leave Custom?', ok: 'Leave Custom', text: `Leaving Custom deletes ${what} of ${empName(slug)}'s.`})) return null;
    return post(`/v2/bots/${encodeURIComponent(slug)}/profile`, {...body, expected_revision: revision, confirm_drop_rules: true});
  }
}
// The bot editor holds a profile change until Save bot (settingsEditBot): the save it will run, given the bot's
// revision, or null when the box shows what is saved. Picking Custom shows its own fields once saved.
function profileStaged(target: Element): ((rev: number) => Promise<number>) | null {
  const box = target.closest<HTMLElement>('[data-bot-profile-box]'), slug = box?.dataset.botProfileBox ?? '';
  const select = box?.querySelector<HTMLSelectElement>('[data-bot-profile]');
  if (!box || !select || !botRow(slug)) return null;
  const initial = (el: HTMLInputElement | HTMLSelectElement): string | boolean | undefined => {
    if (el instanceof HTMLInputElement && el.type === 'checkbox') return el.defaultChecked;
    return [...(el instanceof HTMLSelectElement ? el.options : [])].find(o => o.defaultSelected)?.value ?? (el instanceof HTMLInputElement ? el.defaultValue : undefined);
  };
  const valueOf = (el: HTMLInputElement | HTMLSelectElement): string | boolean => el instanceof HTMLInputElement && el.type === 'checkbox' ? el.checked : el.value;
  const words = box.querySelector('[data-profile-description]'), chosen = PROFILE_LIST?.find(c => c.name === select.value);
  if (words && chosen) words.textContent = chosen.description || '';
  const body: Record<string, unknown> = {profile: select.value};
  if (select.value === initial(select)) box.querySelectorAll<HTMLInputElement | HTMLSelectElement>('[data-profile-key]').forEach(el => {
    const key = el.dataset.profileKey ?? '';
    if (String(valueOf(el)) !== String(initial(el))) body[key] = key === 'digest_hour' ? Number(el.value) : valueOf(el);
  });
  if (select.value === initial(select) && Object.keys(body).length === 1) return null;
  return async rev => {
    const out = await profileSave(slug, body, rev);
    if (!out) throw new Error('Profile not changed: Custom keeps its wake rules. Pick Custom again, or press Save bot to ask again.');
    return out.revision ?? rev + 1;
  };
}
// A history row's value: the profile and, when it set any, its own numbers.
function profileHistoryWords(value: BpHistoryValue | null | undefined): string {
  if (!value?.profile) return 'none';
  const o = value.options || {}, extra: string[] = [];
  if (o.digest_hour != null) extra.push('digest ' + profileHour(o.digest_hour));
  if (o.takes) extra.push('takes ' + o.takes);
  return [profileLabel(value.profile), ...extra].join(' · ');
}
