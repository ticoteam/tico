/* ui/app/slash-commands.js — The "/" menu in a bot's chat box: Tico's own commands and the ones the bot's harness
   takes as-is, its own repository's skills and commands among them. Typing "/" at the start opens it; it narrows as you type.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Tico's own commands, for when the server has not listed this chat's yet (or is too old to). The server's list,
// from GET /v2/conversations/{id}/goal, replaces it and adds what the bot's harness takes.
const SLASH_TICO = [
  {name: 'goal', args: '<objective>', help: 'Pin a goal the bot works toward', kind: 'tico', sub: ['pause', 'resume', 'clear', 'edit']},
  {name: 'new', help: 'New chat', kind: 'tico'},
  {name: 'task', args: '<title>', help: 'Make a task for this bot', kind: 'tico'},
  {name: 'branch', help: 'Make my branch', kind: 'tico'},
  {name: 'help', help: 'Show commands', kind: 'tico'},
];
// Make my branch is offered on a shared bot that has no branch of the viewer's yet.
function slashBranchAllowed(slug) {
  const bot = botRow(slug);
  const original = bot?.shared_from || slug;
  return !!botRow(original)?.shared
    && !(S.emps || []).some(e => e.shared_from === original && e.owner_id === S.me?.id);
}
function slashCommands(slug) {
  const state = V2C?.slug === slug ? V2C : null;
  const list = state?.commands || SLASH_TICO;
  return list.filter(c => c && c.name
    && (c.name !== 'goal' || state?.goalSupported)
    && (c.name !== 'branch' || slashBranchAllowed(slug)));
}
// One row per command, plus a row per sub-command once a space follows its name (/goal pause).
function slashEntries(slug, typed) {
  const t = typed.toLowerCase(), spaced = /\s/.test(t), rows = [];
  for (const c of slashCommands(slug)) {
    const base = '/' + c.name;
    if (!spaced) rows.push({fill: base + (c.args ? ' ' : ''), label: base, args: c.args || '', help: c.help || '', kind: c.kind});
    else for (const s of c.sub || []) rows.push({fill: `${base} ${s}`, label: `${base} ${s}`, args: '', help: '', kind: c.kind});
  }
  // A command typed out in full needs no menu; the rest match from the start, then anywhere in the name.
  const open = rows.filter(r => r.label !== t.trimEnd()), starts = open.filter(r => r.label.startsWith(t));
  if (spaced) return starts;
  return starts.concat(open.filter(r => !starts.includes(r) && t.length > 1 && r.label.includes(t.slice(1))));
}

// Attach the menu to a composer. It sits just above the box; keys it takes never reach the composer's own.
function slashAttach(P) {
  const box = pq(P, '.p-text'), menu = document.createElement('div');
  menu.className = 'slash-menu'; menu.hidden = true;
  menu.setAttribute('role', 'listbox'); menu.setAttribute('aria-label', 'Commands');
  menu.id = 'slash-menu-' + Math.random().toString(36).slice(2, 8);
  P.el.prepend(menu);
  box.setAttribute('aria-controls', menu.id);
  const S_ = P.slash = {rows: [], pick: 0, closedFor: null};
  const close = () => { menu.hidden = true; S_.rows = []; box.removeAttribute('aria-activedescendant'); box.setAttribute('aria-expanded', 'false'); };
  const draw = () => {
    menu.innerHTML = S_.rows.map((r, i) => `<button type="button" role="option" id="${menu.id}-${i}" class="slash-row${i === S_.pick ? ' on' : ''}" aria-selected="${i === S_.pick}" data-slash="${i}">
      <span class="slash-name">${esc(r.label)}</span>${r.args ? `<span class="slash-args">${esc(r.args)}</span>` : ''}<span class="slash-help">${esc(r.help)}</span></button>`).join('');
    box.setAttribute('aria-activedescendant', `${menu.id}-${S_.pick}`);
    menu.querySelector('.on')?.scrollIntoView({block: 'nearest'});
  };
  const update = () => {
    const text = box.value;
    // Only a first line that starts with "/"; Esc keeps it shut until the text changes.
    if (!text.startsWith('/') || text.includes('\n') || text === S_.closedFor) return close();
    S_.closedFor = null;
    S_.rows = slashEntries(P.slug, text);
    if (!S_.rows.length) return close();
    S_.pick = Math.min(S_.pick, S_.rows.length - 1);
    menu.hidden = false; box.setAttribute('aria-expanded', 'true');
    draw();
  };
  const choose = i => {
    const r = S_.rows[i]; if (!r) return;
    box.value = r.fill; box.focus(); box.setSelectionRange(r.fill.length, r.fill.length);
    box.dispatchEvent(new Event('input', {bubbles: true}));
  };
  P.slashUpdate = update;
  box.addEventListener('input', () => { S_.pick = 0; update(); });
  box.addEventListener('click', update);
  box.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== box) close(); }, 150));
  // Captured on the composer, so Return picks a command instead of sending the half-typed one.
  P.el.addEventListener('keydown', ev => {
    if (ev.target !== box || menu.hidden || ev.isComposing) return;
    const n = S_.rows.length, take = () => { ev.preventDefault(); ev.stopPropagation(); };
    if (ev.key === 'ArrowDown') { take(); S_.pick = (S_.pick + 1) % n; draw(); }
    else if (ev.key === 'ArrowUp') { take(); S_.pick = (S_.pick - 1 + n) % n; draw(); }
    else if ((ev.key === 'Enter' && !ev.shiftKey && !ev.metaKey && !ev.ctrlKey) || ev.key === 'Tab') { take(); choose(S_.pick); }
    else if (ev.key === 'Escape') { take(); S_.closedFor = box.value; close(); }
  }, true);
  menu.addEventListener('pointerdown', ev => ev.preventDefault());   // the box keeps focus (and a phone its keyboard)
  menu.addEventListener('click', ev => { const b = ev.target.closest('[data-slash]'); if (b) choose(+b.dataset.slash); });
}

// A typed "/command" in a bot chat. True when it was handled here; 'plain' when it goes to the bot as ordinary
// text; false when it is not a command at all, or not one for this composer.
function slashRun(P, text) {
  if (P.mode !== 'chat' || !P.slug || !/^\/[a-z0-9_-]/i.test(text)) return false;
  // A bot's own skills and commands may be namespaced (/docs:publish).
  const [, word, rest = ''] = text.match(/^\/([a-z0-9_:-]+)(?:\s+([\s\S]*))?$/i) || [];
  if (!word) return 'plain';
  const name = word.toLowerCase(), arg = rest.trim();
  const cmd = slashCommands(P.slug).find(c => c.name === name);
  const scope = sessionScope(pq(P, '.p-text'));
  const done = () => { sessionAcknowledge(pq(P, '.p-text'), scope); pillClear(P, true); P.slash && (P.slash.closedFor = null); return true; };
  if (cmd?.kind === 'harness') {
    if (!isKeeper(P.slug)) return 'plain';
    void v2ChatSend(P, text, P.slug, {}, {command: true});
    return true;
  }
  if (name === 'goal' && cmd) {
    const sub = arg.toLowerCase();
    if (!arg) { done(); chatGoalEdit(true); return true; }
    if (sub === 'edit') { done(); chatGoalEdit(true); return true; }
    if (['pause', 'resume', 'clear'].includes(sub)) { done(); void chatGoalAct(sub); return true; }
    const objective = /^edit\s/i.test(arg) ? arg.slice(5).trim() : arg;
    void chatGoalSave(objective.slice(0, GOAL_MAX)).then(ok => { if (ok && pq(P, '.p-text').value.trim() === text) done(); });
    return true;
  }
  if (name === 'new' && cmd) { done(); void slashNewChat(P.slug); return true; }
  if (name === 'task' && cmd) {
    if (!arg) { pq(P, '.p-text').value = '/task '; return true; }
    pq(P, '.p-text').value = arg;                       // what the task path acknowledges and clears
    void (isKeeper(P.slug) ? v2PillTask(P, arg) : pillTask(P, arg));
    return true;
  }
  if (name === 'branch' && cmd) {
    done();
    const bot = botRow(P.slug);
    void botBranchCreate(bot?.shared_from || P.slug);
    return true;
  }
  if (name === 'help') { done(); slashHelp(P); return true; }
  // /clear is Tico's own session reset unless the harness listed its own.
  if (name === 'clear') return false;
  return 'plain';
}
function slashHelp(P) {
  const out = pq(P, '.p-cmd'); if (!out) return;
  out.innerHTML = `<div class="cmd-out slash-help-out">${slashCommands(P.slug).map(c =>
    `<div><code>/${esc(c.name)}${c.args ? ' ' + esc(c.args) : ''}</code> <span class="muted">${esc(c.help || '')}</span></div>`).join('')}</div>`;
  out.hidden = false;
  out.onclick = () => { out.hidden = true; out.innerHTML = ''; out.onclick = null; };
}
// The server archives this person's room with the bot; the next message starts a fresh one.
async function slashNewChat(slug) {
  try {
    await post(`/v2/chat/${encodeURIComponent(slug)}/new`, {});
    CHAT_CACHE.delete(slug);
    if (BOT?.slug === slug) await v2ChatLoad(slug);
  } catch (e) { toast(e.message, true); }
}
