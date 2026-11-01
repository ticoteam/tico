/* ui/app/settings-sidebar.js — Settings > General > Sidebar: which optional sections and Operations tabs show for the whole
   company, and its business type. Display only: data, API, tools and permissions don't change. Owner and Admins change it
   (PUT /api/v2/company/sidebar); everyone else reads it. What's chosen rides on /api/v2/config, and ui/app/redesign.js applies it.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const SB_BUSINESS_TYPES = [['field_service', 'Field service'], ['restaurant', 'Restaurant or food'], ['retail', 'Retail or online store'], ['manufacturer', 'Manufacturer'],
  ['construction', 'Construction or contractor'], ['multi_location_services', 'Multi-location services'], ['software', 'Software or professional services'], ['other', 'Other']];

async function renderSettingsSidebar() {
  const host = $('#set-sidebar');
  if (!host) return;
  // Who may change it is the server's answer; until that route answers, the same check the Settings page makes.
  const read = await get('/v2/company/sidebar', {fresh: true}).catch(() => null);
  if (!document.body.contains(host)) return;
  const admin = read ? !!read.may_change : settingsIsAdmin(), hidden = rdHiddenKeys();
  const sw = (key, label, child = false) => `<label class="sb-row${child ? ' sb-child' : ''}"><span>${esc(label)}</span><input type="checkbox" role="switch" class="people-switch" data-sb-key="${esc(key)}" aria-label="${esc(label)}"${hidden.has(key) ? '' : ' checked'}${admin ? '' : ' disabled'}></label>`;
  host.innerHTML = `<div class="sb-rows">${RD_SECTIONS.map(([key, label]) => sw(key, label)
      + (key === 'operations' ? OPS_TABS.map(([tab, name]) => sw(opsTabKey(tab), name, true)).join('') : '')).join('')}</div>
    <label class="sb-row sb-type"><span>Business type</span><select data-sb-type aria-label="Business type"${admin ? '' : ' disabled'}>${grOpts(SB_BUSINESS_TYPES, S.config.business_type || '', '—')}</select></label>
    ${admin ? '' : `<p class="settings-cell-note" data-sb-who>${esc((w => w[0].toUpperCase() + w.slice(1))(read?.who_may_change || 'the owner or an admin'))} can change this.</p>`}
    <p class="settings-cell-note err" role="status" data-sb-status hidden></p>`;
  if (!admin) return;
  const status = host.querySelector('[data-sb-status]');
  const save = async (body, undo) => {
    status.hidden = true;
    host.querySelectorAll('input,select').forEach(el => { el.disabled = true; });
    try {
      const out = await put('/v2/company/sidebar', body);
      // Fields the server answers with win: choosing a business type may settle what's hidden.
      S.config = {...S.config, ...body, ...(out?.hidden_sections !== undefined ? {hidden_sections: out.hidden_sections} : {}), ...(out?.business_type !== undefined ? {business_type: out.business_type} : {})};
      redesignSections();
    } catch (e) {
      undo();
      status.textContent = e.status === 404 ? 'Not available yet' : e.message || 'Not saved';
      status.hidden = false;
    } finally { host.querySelectorAll('input,select').forEach(el => { el.disabled = false; }); }
  };
  host.querySelectorAll('[data-sb-key]').forEach(box => {
    box.onchange = () => {
      const keys = rdHiddenKeys();
      if (box.checked) keys.delete(box.dataset.sbKey); else keys.add(box.dataset.sbKey);
      void save({hidden_sections: [...keys].sort()}, () => { box.checked = !box.checked; });
    };
  });
  const type = host.querySelector('[data-sb-type]'), was = type.value;
  type.onchange = () => void save({business_type: type.value}, () => { type.value = S.config.business_type || was; });
}
