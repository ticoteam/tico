/* ui/app/parts-kit.ts — The markup of the Sheets look's shared parts (styles/parts.css), and its one formatter
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   A page part (ui/parts.json, sheets-kit), not shell code: a cold open's JavaScript is at its ceiling
   (backend/ui_build.py SHELL_JS_CEILING), and no page draws with this yet. A page part that draws with it lists
   sheets-kit in its deps; shell code can call it once the shell has room and this file leaves parts.json.

   PK.<part>(...) returns the part's HTML as a string: the classes of parts.css, in the order it expects. A page builds
   its markup from these, so a part looks and behaves the same wherever it is used. Nothing here reads the app's state
   or touches the document, except tabsSync and the tick that keeps ages moving.

   Text and markup: a string argument is text and is escaped here. Where a slot may hold markup (another part), pass
   {html: '...'}; an `attrs` option is markup too (' data-id="7" id="x"'), written by the caller, escaped by the caller.
   Icons are names in the app's Material Symbols font (ui/vendor/fonts/icons.txt lists the ones it has).
   Motion: a part that is pressed, pops open or arrives carries m-press, m-pop or m-enter (styles/motion.css).

   The formatter: PK.age "now, 6m, 3h, Tue, Nov 13"; PK.date "Tue, Nov 11, 10:15 AM"; PK.duration "42s, 2m 5s, 1h 4m";
   PK.money "$1,240". Each wraps the app's own (ui/app/format.ts), so the two looks never disagree about a time. */
'use strict';

// Text, or markup the caller made (another part).
type PkText = string | {html: string};
// Who an avatar shows. A person is a circle and a bot a rounded square; Tico is the Dot. `hue` overrides the one the
// name gives; `live` is working now.
interface PkWho {
  name: string;
  kind?: 'person' | 'bot' | 'tico';
  hue?: number;
  photo?: string;
  live?: boolean;
}
type PkAvSize = 16 | 22 | 28 | 36;
// What most parts take last: more classes, and more attributes (markup).
interface PkMore {
  cls?: string;
  attrs?: string;
}
interface PkButton extends PkMore {
  label: string;
  // the one recommended answer, or the one filled button of a bar
  fill?: boolean;
}
interface PkTaskRow {
  title: string;
  owner: PkWho;
  when: WhenInput;
  href?: string;
  needs?: boolean;
  stopped?: boolean;
  late?: boolean;
  blocks?: string;
}
interface PkThreadRow {
  title: string;
  when: WhenInput;
  href?: string;
  who?: PkWho;
  icon?: string;
  needs?: boolean;
  unread?: number;
}
interface PkMessage {
  from: PkWho;
  when?: WhenInput;
  mine?: boolean;
  text?: string;
  list?: string[];
  code?: string;
  file?: {name: string; meta?: string; href?: string};
  live?: boolean;
  cont?: boolean;
  failed?: boolean;
}
// A question with its answers, or a bot's work waiting for a yes.
interface PkAnswer {
  q?: string;
  choices?: PkButton[];
  name?: string;
  unsure?: boolean;
  why?: string;
  no?: PkButton;
  yes?: PkButton;
}
interface PkTab extends PkMore {
  name: string;
  on?: boolean;
  href?: string;
  // ok: a tick. ask: the Dot, it waits on you. none: a hollow ring, nothing there yet.
  mark?: 'ok' | 'ask' | 'none';
}
interface PkMenuItem extends PkMore {
  label: string;
  icon?: string;
  on?: boolean;
  disabled?: boolean;
}
interface PkSlot {
  id: string;
  label: string;
  icon: string;
  href: string;
  count?: number;
  flow?: boolean;
}

const PK_WEEKDAY = new Intl.DateTimeFormat(DATE_LOCALE, {weekday: 'short'});
const PK_MONEY = new Map<string, Intl.NumberFormat>();
// The tab rows whose scrolling is already watched.
const PK_TAB_ROWS = new WeakSet<HTMLElement>();
// An answer this long does not share a line with another.
const PK_ANSWER_LONG = 22;

const PK = {
  text: (v: PkText | null | undefined): string => v == null ? '' : typeof v === 'string' ? esc(v) : v.html,
  cls: (base: string, more?: string): string => more ? `${base} ${more}` : base,

  // ---- the formatter
  // How old: "now", "6m", "3h" under a day (format.ts ago), the weekday for the six days before today ("Tue"), then
  // the date ("Nov 13"). A time ahead says when it is, as ago does.
  age(when: WhenInput): string {
    const words = ago(when), d = dateOf(when);
    if (!d || !words || /^(now|\d+[mh])$/.test(words)) return words;
    const off = dayOffset(d);
    return off <= -1 && off >= -6 ? PK_WEEKDAY.format(d) : words;
  },
  // An age that keeps moving while the page stays open; the full date is its hover.
  ageSpan(when: WhenInput, cls = 's-row-age'): string {
    // a bare day stays a bare day, so it goes on reading as its day
    const stamp = typeof when === 'string' ? when : dateOf(when)?.toISOString() ?? '';
    return `<span class="${cls}" data-age="${esc(stamp)}" title="${esc(timeWords(when))}">${esc(PK.age(when))}</span>`;
  },
  ageTick(root: ParentNode = document): void {
    for (const node of root.querySelectorAll<HTMLElement>('[data-age]')) {
      const words = PK.age(node.dataset.age);
      if (node.textContent !== words) node.textContent = words;
    }
  },
  // A date with its time: "Today, 10:15 AM", "Tue, Nov 11, 10:15 AM".
  date: (when: WhenInput): string => timeWords(when),
  // A day alone: "Today", "Tue, Nov 11".
  day: (when: WhenInput): string => dayWords(when, {weekday: true}),
  // How long, from seconds: "42s", "2m 5s" (format.ts durationWords), and past an hour "1h 4m".
  duration(seconds: number | string | null | undefined): string {
    if (seconds == null || seconds === '') return '';
    const s = Math.max(0, Math.round(+seconds));
    if (s < 3600) return durationWords(s);
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
    return m ? `${h}h ${m}m` : `${h}h`;
  },
  // Money: whole units ("$1,240") unless cents are asked for ("$1,240.50"). '' for a missing or unreadable amount.
  money(amount: number | string | null | undefined, {cents = false, currency = 'USD'}: {cents?: boolean; currency?: string} = {}): string {
    const n = Number(amount);
    if (amount == null || amount === '' || !Number.isFinite(n)) return '';
    const key = currency + (cents ? '.' : '');
    let f = PK_MONEY.get(key);
    if (!f) {
      const digits = cents ? 2 : 0;
      try { f = new Intl.NumberFormat(DATE_LOCALE, {style: 'currency', currency, minimumFractionDigits: digits, maximumFractionDigits: digits}); }
      catch { f = new Intl.NumberFormat(DATE_LOCALE, {minimumFractionDigits: digits, maximumFractionDigits: digits}); }
      PK_MONEY.set(key, f);
    }
    return f.format(n);
  },

  // ---- marks
  ico: (name: string, cls = ''): string => `<span class="${PK.cls('s-ico', cls)}" aria-hidden="true">${esc(name)}</span>`,
  // The Dot is Tico. Only a live one moves, and one per screen is live.
  dot: (size: number, live = false): string => ldotHTML({state: live ? 'idle' : 'still', size}),
  // Small and named, the Dot says "waits on you".
  flow: (size = 12, live = false): string => `<span class="s-flow" role="img" aria-label="Waits on you">${PK.dot(size, live)}</span>`,
  av(who: PkWho, size: PkAvSize = 22): string {
    const live = who.live === true;
    if (who.kind === 'tico') return `<span class="s-av-dot" style="--a:${size}px" title="${esc(who.name)}">${PK.dot(Math.round(size * .86), live)}</span>`;
    const person = who.kind === 'person';
    const hue = who.hue ?? (person ? personHue(who.name) : avHue(who.name));
    const face = who.photo ? `<img src="${esc(who.photo)}" alt="" loading="lazy" decoding="async">`
      : size <= 16 ? '' : esc(person ? personInitials(who.name) : avInitials(who.name));
    return `<span class="s-av s-av-${size} ${person ? 'is-person' : 'is-bot'}" style="--h:${hue}" title="${esc(who.name)}${live ? ', working now' : ''}">`
      + `${face}${live ? '<i class="s-ring"><i></i></i>' : ''}</span>`;
  },
  // Several teammates: spaced, each with its initials, then a count of the rest. Tight, they are 16px chips that may
  // overlap, because a chip has no initials to cut.
  avStack(people: PkWho[], {max = 4, tight = false}: {max?: number; tight?: boolean} = {}): string {
    const shown = people.slice(0, max), rest = people.length - shown.length;
    return `<span class="s-avs${tight ? ' is-tight' : ''}">${shown.map(p => PK.av(p, tight ? 16 : 22)).join('')}`
      + `${rest > 0 ? `<span class="s-avs-more">+${rest}</span>` : ''}</span>`;
  },
  // A pill is grey (of interest) or amber (something is wrong). Nothing else is amber.
  pill: (text: string, warn = false): string => `<span class="s-pill${warn ? ' s-pill-warn' : ''}">${esc(text)}</span>`,
  late: (): string => PK.pill('Late', true),
  // Stopped or paused: the glyph alone in a list, with its word elsewhere.
  stopped: (word = ''): string => word ? `<span class="s-state"><span class="s-stuck" aria-hidden="true"></span>${esc(word)}</span>`
    : '<span class="s-stuck" role="img" aria-label="Stopped"></span>',
  count: (n: number | string, pill = false): string => `<span class="s-count${pill ? ' s-count-pill' : ''}">${esc(n)}</span>`,
  typing: (): string => '<span class="s-typing" aria-hidden="true"><i></i><i></i><i></i></span>',

  // ---- controls
  ib: (icon: string, label: string, {cls = '', attrs = ''}: PkMore = {}): string =>
    `<button class="${PK.cls('s-ib m-press', cls)}" type="button" aria-label="${esc(label)}" title="${esc(label)}"${attrs}>${PK.ico(icon)}</button>`,
  btn: ({label, fill = false, cls = '', attrs = ''}: PkButton, inner = ''): string =>
    `<button class="${PK.cls(fill ? 's-btn s-btn-fill m-press' : 's-btn m-press', cls)}" type="button"${attrs}>${inner}${esc(label)}</button>`,
  // A label over a control; `error` is at most four words.
  field(label: string, control: string, error = ''): string {
    return `<label class="s-f"><span class="s-f-label">${esc(label)}</span>${control}`
      + `${error ? `<span class="s-f-err">${PK.ico('error')}${esc(error)}</span>` : ''}</label>`;
  },
  // A setting that is on or off, pressed by its whole row.
  toggle: (label: string, on: boolean, {cls = '', attrs = ''}: PkMore = {}): string =>
    `<label class="s-opt"><span>${esc(label)}</span><button class="${PK.cls(on ? 's-switch is-on' : 's-switch', cls)}" type="button" role="switch" aria-checked="${on}"${attrs}><i></i></button></label>`,

  // ---- facts, the empty state, the answer
  // A label and its value on one line; every fact on a page shares the label's column.
  fact: (label: string, value: PkText, {cls = '', attrs = ''}: PkMore = {}): string =>
    `<div class="${PK.cls('s-fact', cls)}"${attrs}><span class="s-fact-k">${esc(label)}</span><span class="s-fact-v">${PK.text(value)}</span></div>`,
  // The one empty state: one line and one plain button.
  empty: (line: string, button?: PkButton): string =>
    `<div class="s-empty"><p class="s-empty-line">${esc(line)}</p>${button ? PK.btn({...button, fill: false}) : ''}</div>`,
  // The one answer part. With q: the Dot, the question and its answers as buttons, the recommended one filled; long
  // answers, or more than three, stand one under another. Without: a bot's work waiting for a yes.
  answer(o: PkAnswer, {cls = '', attrs = ''}: PkMore = {}): string {
    if (o.q != null) {
      const choices = o.choices ?? [];
      const stack = choices.length > 3 || choices.some(c => c.label.length > PK_ANSWER_LONG);
      return `<section class="${PK.cls('s-answer is-ask' + (stack ? ' is-stack' : ''), cls)}" aria-label="Answer"${attrs}>`
        + `<p class="s-an-q">${PK.flow()}<span>${esc(o.q)}</span></p><div class="s-an-row">${choices.map(c => PK.btn(c)).join('')}</div></section>`;
    }
    const why = o.why ? `<p class="s-an-why"><span>Decision model</span>${esc(o.why)}</p>` : '';
    const unsure = o.unsure ? `<button class="s-pill s-an-pill${o.why ? ' is-on' : ''}" type="button" aria-expanded="${o.why ? 'true' : 'false'}" title="Decision model">Not sure</button>` : '';
    return `<section class="${PK.cls('s-answer', cls)}" aria-label="Answer"${attrs}>${why}<div class="s-an-row">${PK.flow()}<b class="s-an-name">${esc(o.name ?? '')}</b>${unsure}`
      + `<span class="s-grow"></span>${PK.btn(o.no ?? {label: 'Send back'})}${PK.btn({...(o.yes ?? {label: 'Approve'}), fill: true})}</div></section>`;
  },

  // ---- rows
  navItem: (icon: string, label: string, href: string, {right = '', active = false, cls = '', attrs = ''}: PkMore & {right?: string; active?: boolean} = {}): string =>
    `<a class="${PK.cls(active ? 's-nav-item is-active' : 's-nav-item', cls)}" href="${esc(href)}"${active ? ' aria-current="page"' : ''}${attrs}>${PK.ico(icon)}<span class="s-nav-label">${esc(label)}</span>${right}</a>`,
  navFlow: (n: number): string => `<span class="s-nav-flow">${PK.flow()}<b>${n}</b></span>`,
  treeRow: (who: PkWho, href: string, {needs = false, n = 0, active = false, cls = '', attrs = ''}: PkMore & {needs?: boolean; n?: number; active?: boolean} = {}): string =>
    `<a class="${PK.cls(active ? 's-tree-row is-active' : 's-tree-row', cls)}" href="${esc(href)}"${attrs}>${PK.av(who, 22)}<span class="s-tree-name">${esc(who.name)}</span>`
    + `${needs ? PK.flow() : ''}${n ? `<span class="s-tree-n">${n}</span>` : ''}</a>`,
  // A team in the tree. Closed, it shows its faces as chips and whether any of them waits on you.
  treeTeam: (name: string, people: PkWho[], {open = false, needs = false, cls = '', attrs = ''}: PkMore & {open?: boolean; needs?: boolean} = {}): string =>
    `<button class="${PK.cls(open ? 's-tree-team is-open' : 's-tree-team', cls)}" type="button" aria-expanded="${open}"${attrs}><span class="s-tree-chev">${PK.ico('chevron_right')}</span>`
    + `<span class="s-tree-name">${esc(name)}</span>${open ? '' : (needs ? PK.flow() : '') + PK.avStack(people, {tight: true})}</button>`,
  // A task: its mark in the margin (waits on you, or stopped), its title, its pills, who has it, how old.
  taskRow(t: PkTaskRow, {cls = '', attrs = ''}: PkMore = {}): string {
    const mark = t.needs ? PK.flow() : t.stopped ? PK.stopped() : '';
    const pills = (t.late ? PK.late() : '') + (t.blocks ? PK.pill('Blocks ' + t.blocks) : '');
    return `<a class="${PK.cls('s-row s-task', cls)}" href="${esc(t.href ?? '#')}" title="${esc(t.title)}"${attrs}><span class="s-row-mark">${mark}</span>`
      + `<span class="s-row-title">${esc(t.title)}</span><span class="s-row-pills">${pills}</span>${PK.av(t.owner, 22)}${PK.ageSpan(t.when)}</a>`;
  },
  // A row that is still loading; `width` is the title bar's, in percent.
  skRow: (width: number): string => `<div class="s-row s-task is-skeleton" aria-hidden="true"><span class="s-row-mark"></span><span class="s-sk" style="width:${Math.round(width)}%"></span>`
    + '<span></span><span class="s-sk s-sk-av"></span><span class="s-sk s-sk-age"></span></div>',
  threadRow(t: PkThreadRow, {cls = '', attrs = ''}: PkMore = {}): string {
    const lead = t.who ? PK.av(t.who, 22) : `<span class="s-tl-kind">${PK.ico(t.icon ?? 'forum')}</span>`;
    const right = t.needs ? PK.flow() : t.unread ? PK.count(t.unread, true) : '';
    return `<a class="${PK.cls('s-row s-tl-row' + (t.unread || t.needs ? ' is-unread' : ''), cls)}" href="${esc(t.href ?? '#')}"${attrs}>${lead}`
      + `<span class="s-row-title">${esc(t.title)}</span>${PK.ageSpan(t.when)}<span class="s-tl-slot">${right}</span></a>`;
  },
  whoRow: (who: PkWho, href: string, {needs = false, n = 0, cls = '', attrs = ''}: PkMore & {needs?: boolean; n?: number} = {}): string =>
    `<a class="${PK.cls('s-row s-who', cls)}" href="${esc(href)}"${attrs}>${PK.av(who, 22)}<span class="s-row-title">${esc(who.name)}</span>`
    + `<span class="s-tl-slot">${needs ? PK.flow() : ''}</span><span class="s-count">${n || ''}</span></a>`,
  // A task in the task view's list: `mark` is markup (a tick, the Dot, the stopped glyph).
  li: (t: PkTaskRow, {mark = '', on = false, cls = '', attrs = ''}: PkMore & {mark?: string; on?: boolean} = {}): string =>
    `<a class="${PK.cls(on ? 's-li is-on' : 's-li', cls)}" href="${esc(t.href ?? '#')}" title="${esc(t.title)}"${on ? ' aria-current="true"' : ''}${attrs}><span class="s-li-mark">${mark}</span>`
    + `<span class="s-li-main"><span class="s-li-title">${esc(t.title)}</span>${t.late ? PK.late() : ''}</span><span class="s-li-who">${PK.av(t.owner, 22)}${PK.ageSpan(t.when)}</span></a>`,

  // ---- conversation
  fileChip: (name: string, meta = '', href = '#', {cls = '', attrs = ''}: PkMore = {}): string =>
    `<a class="${PK.cls('s-file', cls)}" href="${esc(href)}"${attrs}>${PK.ico('description')}<span>${esc(name)}</span>${meta ? `<em>${esc(meta)}</em>` : ''}</a>`,
  // A message, in full. Mine sits right in a bubble; one that did not send says so and offers Retry.
  msg(m: PkMessage): string {
    const text = (m.text ? `<p>${esc(m.text)}${m.live ? PK.typing() : ''}</p>` : '') + (m.list ? `<ul>${m.list.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '');
    if (m.mine) {
      return `<div class="s-msg is-mine${m.cont ? ' is-cont' : ''}${m.failed ? ' is-failed' : ''}"><div class="s-mine-col"><div class="s-bubble">${text}</div>`
        + `${m.failed ? `<div class="s-msg-fail">${PK.ico('error')}<span>Not sent</span><button type="button">Retry</button></div>` : ''}</div></div>`;
    }
    const extra = (m.code ? `<pre><code>${esc(m.code)}</code></pre>` : '') + (m.file ? PK.fileChip(m.file.name, m.file.meta ?? '', m.file.href ?? '#') : '');
    const meta = m.cont ? '' : `<div class="s-msg-meta"><b>${esc(m.from.name)}</b>${m.when ? `<time class="s-time">${esc(PK.age(m.when))}</time>` : ''}</div>`;
    return `<div class="s-msg${m.cont ? ' is-cont' : ''}${m.live ? ' is-live' : ''}">${m.cont ? '<span class="s-av-gap s-av-22"></span>' : PK.av(m.from, 22)}`
      + `<div class="s-msg-main">${meta}${text}${extra}</div></div>`;
  },
  // The message box as it stands: its prompt, or the words typed so far.
  composer: (to: string, {text = '', cls = '', attrs = ''}: PkMore & {text?: string} = {}): string =>
    `<div class="${PK.cls(text ? 's-composer is-typing' : 's-composer', cls)}"${attrs}><span class="s-composer-text">${esc(text || 'Message ' + to)}</span>`
    + `${PK.ib('add', 'Attach a file', {cls: 's-ib-sm'})}<button class="s-send m-press${text ? ' is-ready' : ''}" type="button" aria-label="Send">${PK.ico('arrow_forward')}</button></div>`,
  scope: (text: string): string => `<span class="s-scope">${esc(text)}<button type="button" aria-label="Clear">${PK.ico('close')}</button></span>`,
  chatHead: (who: PkWho): string => `<header class="s-side-head">${PK.av(who, 28)}<b>${esc(who.name)}</b><span class="s-grow"></span>${PK.ib('close', 'Close chat', {cls: 's-ib-quiet'})}</header>`,

  // ---- tabs
  // One row that never wraps. Links when the tabs have an href (pages of one thing), else buttons (views of one page).
  // Call tabsSync on the row once it is in the page, and again whenever the chosen tab changes.
  tabs(tabs: PkTab[], label: string, {cls = '', attrs = ''}: PkMore = {}): string {
    const links = tabs.some(t => t.href != null);
    const row = tabs.map(t => PK.tab(t, links)).join('') + '<i class="s-tabs-line" aria-hidden="true"></i>';
    return links ? `<nav class="${PK.cls('s-tabs', cls)}" aria-label="${esc(label)}"${attrs}>${row}</nav>`
      : `<div class="${PK.cls('s-tabs', cls)}" role="tablist" aria-label="${esc(label)}"${attrs}>${row}</div>`;
  },
  tab(t: PkTab, link = t.href != null): string {
    const mark = t.mark === 'ok' ? PK.ico('check') : t.mark === 'ask' ? PK.flow(10) : t.mark === 'none' ? '<i class="s-hollow"></i>' : '';
    const cls = PK.cls(t.on ? 's-tab is-on' : 's-tab', t.cls), inner = `${mark}<span>${esc(t.name)}</span>`, attrs = t.attrs ?? '';
    return link ? `<a class="${cls}" href="${esc(t.href ?? '#')}"${t.on ? ' aria-current="page"' : ''}${attrs}>${inner}</a>`
      : `<button class="${cls}" type="button" role="tab" aria-selected="${t.on === true}"${attrs}>${inner}</button>`;
  },
  // Puts the line under the chosen tab and marks which edges have more to scroll to. The first call only places the
  // line; from the second it slides. Measures once per call, on the caller's turn: no observer, nothing waits on it.
  tabsSync(row: HTMLElement): void {
    const on = row.querySelector<HTMLElement>('.s-tab.is-on');
    if (on) {
      const pad = parseFloat(getComputedStyle(on).paddingLeft) || 0;
      row.style.setProperty('--tab-x', `${on.offsetLeft + pad}px`);
      row.style.setProperty('--tab-w', String(Math.max(0, on.offsetWidth - pad * 2)));
    }
    row.classList.toggle('is-live', !!on);
    const edges = (): void => {
      row.classList.toggle('is-more-start', row.scrollLeft > 1);
      row.classList.toggle('is-more-end', row.scrollLeft + row.clientWidth < row.scrollWidth - 1);
    };
    edges();
    if (PK_TAB_ROWS.has(row)) return;
    PK_TAB_ROWS.add(row);
    row.addEventListener('scroll', edges, {passive: true});
  },

  // ---- menus, dialogs, toasts
  // null is a rule. A destructive item is the last one, under a rule; it has no colour of its own.
  menu(items: (PkMenuItem | null)[], {cls = '', attrs = ''}: PkMore = {}): string {
    const rows = items.map(item => item === null ? '<hr class="s-menu-rule">'
      : `<button class="${PK.cls('s-menu-item' + (item.on ? ' is-on' : ''), item.cls)}" type="button" role="menuitem"${item.disabled ? ' disabled' : ''}${item.attrs ?? ''}>`
        + `${item.icon ? PK.ico(item.icon) : ''}<span>${esc(item.label)}</span>${item.on ? PK.ico('check') : ''}</button>`);
    return `<div class="${PK.cls('s-menu m-pop', cls)}" role="menu"${attrs}>${rows.join('')}</div>`;
  },
  // The one dialog: a title, at most one line under it, a body, and its buttons with the filled one last. `id` names
  // it for its title. `step` ("2 of 3") makes it the wider, stepped one. Put it in PK.scrim, or use a <dialog>
  // (tag: 'dialog') and showModal().
  dialog({id, title, text = '', body = '', buttons = '', step = '', tag = 'div', cls = '', attrs = ''}:
      PkMore & {id: string; title: string; text?: string; body?: string; buttons?: string; step?: string; tag?: 'div' | 'dialog'}): string {
    const role = tag === 'dialog' ? '' : ' role="dialog" aria-modal="true"';
    return `<${tag} class="${PK.cls('s-modal m-pop' + (step ? ' is-steps' : ''), cls)}" id="${esc(id)}"${role} aria-labelledby="${esc(id)}-title"${attrs}>`
      + `<h2 class="s-modal-title" id="${esc(id)}-title">${esc(title)}</h2>${text ? `<p class="s-modal-text">${esc(text)}</p>` : ''}${body}`
      + `<div class="s-modal-row">${step ? `<span class="s-modal-step">${esc(step)}</span>` : ''}${buttons}</div></${tag}>`;
  },
  scrim: (inner: string, soft = false): string => `<div class="s-scrim${soft ? ' is-soft' : ''}">${inner}</div>`,
  // One line, with at most one action. failed and offline carry the amber mark and are announced at once.
  toast(text: string, {action = '', kind = 'plain', attrs = ''}: {action?: string; kind?: 'plain' | 'done' | 'failed' | 'offline'; attrs?: string} = {}): string {
    const wrong = kind === 'failed' || kind === 'offline';
    const mark = wrong ? PK.ico(kind === 'failed' ? 'error' : 'warning') : kind === 'done' ? PK.ico('check') : '';
    return `<div class="s-toast m-enter${kind === 'plain' ? '' : ' is-' + kind}${action ? '' : ' is-plain'}" role="${wrong ? 'alert' : 'status'}"${attrs}>${mark}<span>${esc(text)}</span>`
      + `${action ? `<button type="button">${esc(action)}</button>` : ''}</div>`;
  },

  // ---- task view
  // Where a task stands: its pipeline, its stage, who has it and for how long.
  where: (pipeline: string, stage: string, owner: PkWho, when: WhenInput, late = false): string =>
    `<div class="s-tv-where"><span>${esc(pipeline)}</span>${PK.ico('chevron_right')}<span>${esc(stage)}</span>${PK.av(owner, 22)}${PK.ageSpan(when)}${late ? PK.late() : ''}</div>`,
  // The head of the task view's list: which list, the Flow toggle with its count, Close; and with Flow down, the place
  // in the run.
  listHead(name: string, {flow = 0, on = false, at = 0}: {flow?: number; on?: boolean; at?: number} = {}): string {
    const run = on ? `<div class="s-li-run"><span><b>${at}</b> of ${flow}</span><span class="s-grow"></span>${PK.ib('expand_more', 'Previous', {cls: 's-ib-sm s-flip'})}${PK.ib('expand_more', 'Next', {cls: 's-ib-sm'})}</div>` : '';
    return `<header class="s-li-head"><button class="s-li-pick" type="button">${esc(name)}${PK.ico('expand_more')}</button><span class="s-grow"></span>`
      + `<button class="s-li-flow${on ? ' is-on' : ''}" type="button" aria-pressed="${on}" title="Flow">${PK.flow()}Flow<b>${flow}</b></button>${PK.ib('close', 'Close')}</header>${run}`;
  },

  // ---- a phone
  // A sub-page's head: Back, its title, at most two icons (markup, PK.ib), then ⋯.
  subHead: (title: string, {icons = [], more = true}: {icons?: string[]; more?: boolean} = {}): string =>
    `<header class="s-sub-head">${PK.ib('chevron_left', 'Back')}<h1 class="s-sub-title">${esc(title)}</h1>${icons.slice(0, 2).join('')}${more ? PK.ib('more_horiz', 'More') : ''}</header>`,
  // The five-slot bar: two slots, the Dot, two slots. `active` is a slot's id, or 'assistant'.
  bar(slots: [PkSlot, PkSlot, PkSlot, PkSlot], active: string, assistantHref: string): string {
    const slot = (s: PkSlot): string => `<a class="s-slot${s.id === active ? ' is-active' : ''}" href="${esc(s.href)}"${s.id === active ? ' aria-current="page"' : ''}><span class="s-slot-ico">${PK.ico(s.icon)}`
      + `${s.count ? `<i class="s-slot-n${s.flow ? ' is-flow' : ''}">${s.count}</i>` : ''}</span><span>${esc(s.label)}</span></a>`;
    return `<nav class="s-bar" aria-label="Main">${slot(slots[0])}${slot(slots[1])}<a class="s-slot s-slot-dot${active === 'assistant' ? ' is-active' : ''}" href="${esc(assistantHref)}" aria-label="Assistant">`
      + `${PK.dot(30, true)}</a>${slot(slots[2])}${slot(slots[3])}</nav>`;
  },
};

// Ages on the page keep moving, as the old look's do (format.ts agoTick).
setInterval(() => { if (!document.hidden) PK.ageTick(); }, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) PK.ageTick(); });
