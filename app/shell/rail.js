// The org rail: one button per organization, the open one ringed, and + to add one. Right-click an org for
// Remove. The list comes from the app (rail_orgs) and stays in this page; org pages never see it.
const { invoke } = window.__TAURI__.core;
const list = document.querySelector('#orgs');

const host = url => { try { return new URL(url).hostname; } catch { return url || ''; } };
// Two letters: the first of each of the first two words of the name, or the start of the address.
function initials(org) {
  const words = (org.name || '').trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
  const word = words[0] || host(org.url).split('.')[0] || '?';
  return word.slice(0, 2).toUpperCase();
}
// A stable color per address (FNV-1a over the host), one of eight that read on both themes.
function tone(url) {
  let h = 0x811c9dc5;
  for (const ch of host(url)) h = Math.imul(h ^ ch.charCodeAt(0), 0x01000193);
  return (h >>> 0) % 8;
}

// Resting on another org for a moment builds its page hidden (rail_hover), so a click shows it at once; leaving it
// lets the app close that page again if no click comes.
const HOVER_MS = 150;
let hoverTimer = 0, hovered = '';
function leave() {
  clearTimeout(hoverTimer);
  if (!hovered) return;
  hovered = '';
  invoke('rail_hover', { id: null }).catch(() => {});
}

function render(orgs) {
  // The buttons are replaced, so the one under the pointer gets no pointerleave.
  leave();
  list.replaceChildren(...orgs.map(org => {
    const li = document.createElement('li');
    const button = document.createElement('button');
    const label = org.name || host(org.url);
    const n = tone(org.url);
    button.type = 'button';
    button.className = 'org';
    button.textContent = initials(org);
    button.style.setProperty('--b', `var(--c${n}b)`);
    button.style.setProperty('--f', `var(--c${n}f)`);
    // Two lines: the org, then who is signed in to it.
    button.title = `${label}\n${org.email || 'Not signed in'}`;
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-current', String(!!org.active));
    button.addEventListener('click', () => { if (!org.active) invoke('rail_switch', { id: org.id }).catch(() => {}); });
    button.addEventListener('pointerenter', () => {
      if (org.active) return;
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => {
        hovered = org.id;
        invoke('rail_hover', { id: org.id }).catch(() => {});
      }, HOVER_MS);
    });
    button.addEventListener('pointerleave', leave);
    button.addEventListener('contextmenu', event => {
      event.preventDefault();
      invoke('rail_menu', { id: org.id }).catch(() => {});
    });
    li.append(button);
    return li;
  }));
}

const refresh = () => invoke('rail_orgs').then(render).catch(() => {});
document.querySelector('#add').addEventListener('click', () => invoke('rail_add').catch(() => {}));
document.addEventListener('contextmenu', event => event.preventDefault());
window.__TAURI__.event.listen('orgs-changed', refresh);
refresh();
