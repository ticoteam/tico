/* ui/app/give-to-field.ts — the Give to… field: its button and hidden input, and who may be picked
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Kept with Home so pages draw the field at once; the picker the field opens is the give-to page part
   (ui/app/give-to.ts, ui/parts.json), loaded on the first click so a cold open of Home does not download it (P05b). */
'use strict';

/** One person or bot to give work to. `value` is what a form sends (`human:<id>`, or a bot's bare id, as the list
    always sent); `actor` is `human:<id>` or `bot:<id>`. */
interface GiveToEntry { value: string; actor: string; name: string; slug: string; role: string; kind: 'human' | 'bot'; }
/** What suggestions read from a task: a full task, a list row, or a parent's brief. */
interface GiveToTask {
  requester_id?: string | null;
  title?: string | null;
  request?: {text?: string | null} | null;
  tags?: readonly {owner?: string | null}[] | null;
}
interface GiveToOptions {
  label?: string;
  task?: GiveToTask | null;
  /** The one chosen now (a value or an actor): its row shows ✓. */
  selected?: string;
  /** Who holds it now, when that is not the field's choice (Reassign in Needs you): its row shows ✓ too. */
  current?: string | null | undefined;
  /** Actors not offered (already on the task in that role). */
  exclude?: Iterable<string>;
  /** Shown when everyone is left out by `exclude`. */
  none?: string;
  onPick: (entry: GiveToEntry) => unknown;
}
interface GiveToSuggestion { entry: GiveToEntry; why: string; }
interface GiveToFieldOptions {
  /** The hidden input's name (a form's field) and id (a screen that reads it by id). */
  name?: string;
  id?: string;
  /** Extra attributes for the hidden input, e.g. `data-field="owner"`. */
  attrs?: string;
  label?: string;
  placeholder?: string;
  selected?: string;
  /** Who holds it now: ✓ in the list, without choosing them (Give stays off until a pick). */
  current?: string | null | undefined;
  task?: GiveToTask | null | undefined;
}

const GIVE_TO_RECENT_MAX = 5, GIVE_TO_SUGGEST_MAX = 8;
const giveToActor = (v: string | null | undefined): string => !v ? '' : v.includes(':') ? v : 'bot:' + v;
const giveToIs = (e: GiveToEntry, sel: string | null | undefined): boolean =>
  !!sel && (sel === e.value || sel === e.actor || (e.kind === 'human' && 'human:' + sel === e.actor));

/** A role in one short line: a bot's first sentence, a person's title. */
function giveToRoleLine(text: string | null | undefined): string {
  const para = (String(text || '').trim().split(/\n\s*\n/)[0] || '').replace(/^#+\s*/, '').replace(/\s+/g, ' ').trim();
  const first = para.match(/^.*?[.!?](?=\s|$)/);
  return (first ? first[0] : para).trim();
}

/** Everyone who may be picked: people not hidden, then bots not archived. The Assistant stays out (everyone's own
    private one; it stays when it is the one chosen), but its helpers are listed: work is given to BotOps, the
    Librarian, the Rules Owner and the Strategy Owner by name, and a built-in bot's id is its slug, so the sidebar's
    isHiddenBot would hide them here. People first, then bots, each A–Z. */
function giveToEntries(selected = ''): GiveToEntry[] {
  const byName = (a: GiveToEntry, b: GiveToEntry) => a.name.localeCompare(b.name, undefined, {sensitivity: 'base'});
  const people: GiveToEntry[] = (S.people || []).filter(p => !p.hidden).map(p => ({value: 'human:' + p.id, actor: 'human:' + p.id,
    name: String(p.name || p.id), slug: '', role: giveToRoleLine(p.title), kind: 'human' as const})).sort(byName);
  const bots: GiveToEntry[] = (S.emps || []).filter(e => e.status !== 'archived' && (e.id !== assistantBot() || [e.id, 'bot:' + e.id].includes(selected)))
    .map(e => {
      const about = e as typeof e & {role?: string | null; description?: string | null};
      return {value: e.id, actor: 'bot:' + e.id, name: String(e.display_name || e.slug || e.id), slug: String(e.slug || e.id),
        role: giveToRoleLine(about.role || about.description), kind: 'bot' as const};
    }).sort(byName);
  return [...people, ...bots];
}

let GIVE_TO_SEQ = 0;


// ---- the field: a button that opens the picker, with a hidden input that holds the pick for its form or screen
/** A field's hidden input, just before its button. */
const giveToInput = (btn: HTMLButtonElement) => btn.previousElementSibling as HTMLInputElement | null;
const GIVE_TO_TASKS = new Map<string, GiveToTask | null>();
function giveToButtonInner(e: GiveToEntry | undefined, placeholder: string): string {
  return e ? `<span class="gv-face" aria-hidden="true">${actorFace(e.actor, 18)}</span><span class="gv-name">${esc(e.name)}</span>` : `<span class="gv-name">${esc(placeholder)}</span>`;
}
/** The field in place of a native list. Closed with nothing chosen it reads the placeholder in muted text (no ✓);
    after a pick, the name. A pick sets the hidden input and fires its input and change events. */
function giveToFieldHTML({name = '', id = '', attrs = '', label = 'Give to', placeholder = 'Give to…', selected = '', current = '', task = null}: GiveToFieldOptions = {}): string {
  const key = String(++GIVE_TO_SEQ);
  GIVE_TO_TASKS.set(key, task);
  if (GIVE_TO_TASKS.size > 50) GIVE_TO_TASKS.delete(GIVE_TO_TASKS.keys().next().value!);
  const cur = selected ? giveToEntries(selected).find(e => giveToIs(e, selected)) : undefined;
  return `<span class="gv-field"><input type="hidden"${name ? ` name="${esc(name)}"` : ''}${id ? ` id="${esc(id)}"` : ''}${attrs ? ' ' + attrs : ''} value="${esc(cur?.value || '')}">`
    + `<button type="button" class="gv-btn${cur ? '' : ' empty'}" data-give-to="${key}" data-give-label="${esc(label)}" data-give-placeholder="${esc(placeholder)}"${current ? ` data-give-current="${esc(current)}"` : ''} aria-haspopup="dialog" aria-expanded="false" aria-label="${esc(label)}: ${esc(cur?.name || 'nobody chosen')}">${giveToButtonInner(cur, placeholder)}</button></span>`;
}
function giveToFieldSet(btn: HTMLButtonElement, e: GiveToEntry): void {
  const input = giveToInput(btn);
  btn.classList.remove('empty');
  btn.innerHTML = giveToButtonInner(e, '');
  btn.setAttribute('aria-label', `${btn.dataset.giveLabel || 'Give to'}: ${e.name}`);
  if (!input) return;
  input.value = e.value;
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
}
/** Opens a field's picker, as a click on it does (a form that is sent with nobody chosen). */
function giveToFieldOpen(btn: HTMLButtonElement): void {
  const input = giveToInput(btn);
  giveToOpen(btn, {label: btn.dataset.giveLabel || 'Give to', task: GIVE_TO_TASKS.get(btn.dataset.giveTo || '') || null, selected: input?.value || '', current: btn.dataset.giveCurrent, onPick: e => giveToFieldSet(btn, e)});
}
/** The click whose press closed the picker does not open it again (once, and only soon after that press). */
function giveToJustClosed(anchor: HTMLElement): boolean {
  const at = Number(anchor.dataset.gtClosed || 0);
  delete anchor.dataset.gtClosed;
  return Date.now() - at < 1000;
}
document.addEventListener('click', ev => {
  const btn = ev.target instanceof Element ? ev.target.closest<HTMLButtonElement>('button[data-give-to]') : null;
  if (!btn || btn.disabled || giveToJustClosed(btn)) return;
  giveToFieldOpen(btn);
});
