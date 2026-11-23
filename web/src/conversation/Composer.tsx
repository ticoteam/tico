import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { directoryQuery, type Actor } from '@/data';
import { Avatar } from '@/kit/Avatar';
import { IconButton } from '@/kit/IconButton';
import { Icon } from '@/kit/Icon';
import { MenuItemBody } from '@/kit/Menu';
import { readPref, writePref } from '@/prefs';
import { mentionAt, mentionMatches, mentionText } from './mentions';

const DRAFT_KEEP_MS = 30 * 86_400_000;

interface Draft {
  text: string;
  at: number;
}

/** A draft per person and conversation (architecture.md 4, drafts): `tico-next:draft.v1:<person>:<conversation>`. */
const draftKey = (who: string, id: string) => `draft.v1:${who}:${id}`;

export function readDraft(who: string, id: string): string {
  const d = readPref<Draft | null>(draftKey(who, id), null);
  return d && typeof d.text === 'string' && Date.now() - d.at < DRAFT_KEEP_MS ? d.text : '';
}

export function writeDraft(who: string, id: string, text: string): void {
  writePref<Draft | null>(draftKey(who, id), text ? { text, at: Date.now() } : null);
}

export interface ComposerProps {
  /** The conversation: its draft is kept under this id. */
  id: string;
  /** The signed-in person (the draft's owner). */
  me: string;
  /** Who it goes to: "Message Tico". */
  to: string;
  /** An optimistic send returns nothing. A send without a pending row resolves to whether it accepted the draft. */
  onSend: (text: string, files: File[]) => void | Promise<boolean>;
  /** Words to put back in the box (a failed message taken back); a new object each time. */
  fill?: { text: string } | null;
  /** The box's words in place of "Message <to>" ("Reply to Sam Lee"). */
  label?: string;
  /** false: no files (a mail reply attaches none). */
  attach?: boolean;
  /** false: the chat has not loaded enough to keep a pending message. */
  canSend?: boolean;
  /** false: no names after @ (a box whose words reach no teammate). */
  mentions?: boolean;
  /** Actor ids the @ list shows ahead of everyone else (a task's people and agents). */
  mentionFirst?: readonly string[];
  /** The only actor ids the @ list may name (a direct chat's members: the server refuses an @ of anyone outside). */
  mentionOnly?: readonly string[];
}

const NOBODY: readonly string[] = [];
const allActors = (d: { actors: Actor[] }) => d.actors;

/** Where you type. Enter sends, Shift+Enter breaks the line; the draft survives a reload. An @ opens the names of the
    people and agents the viewer may see: arrows move, Enter or Tab picks, Escape closes. */
export function Composer({ id, me, to, onSend, fill, label: own, attach = true, canSend = true, mentions = true, mentionFirst = NOBODY, mentionOnly }: ComposerProps) {
  const [text, setText] = useState(() => readDraft(me, id));
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [shownFor, setShownFor] = useState(`${me}:${id}`);
  const [filled, setFilled] = useState(fill);
  const [caret, setCaret] = useState<number | null>(null);
  // The @ whose list was closed (Escape, a pick): it stays closed until another @ is typed.
  const [shut, setShut] = useState<number | null>(null);
  const [active, setActive] = useState(0);
  const placeAt = useRef<number | null>(null);
  const listId = useId();

  const at = mentions && caret !== null ? mentionAt(text, caret) : null;
  const asking = at !== null && at.start !== shut;
  // Read only once an @ is typed; the shell has usually read the directory already.
  const everyone = useQuery({ ...directoryQuery, select: allActors, enabled: asking }).data;
  const names = asking && everyone ? mentionMatches(everyone, at.query, me, mentionFirst, mentionOnly) : [];
  const open = names.length > 0;
  const current = Math.min(active, names.length - 1);

  useLayoutEffect(() => {
    if (placeAt.current === null || !box.current) return;
    box.current.setSelectionRange(placeAt.current, placeAt.current);
    placeAt.current = null;
  });

  // The row under the arrow keys stays in view when the list scrolls (a phone's rows are taller).
  useLayoutEffect(() => {
    if (open) document.getElementById(`${listId}-${current}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [open, current, listId]);

  // New words in the box (another conversation's draft, words put back): no @ list until the caret is placed again.
  const forget = () => {
    setCaret(null);
    setShut(null);
    setActive(0);
  };
  // Another conversation in the same box: its own draft.
  if (shownFor !== `${me}:${id}`) {
    setShownFor(`${me}:${id}`);
    setText(readDraft(me, id));
    setFiles([]);
    forget();
  }
  if (fill !== filled) {
    setFilled(fill);
    if (fill) {
      setText(fill.text);
      forget();
    }
  }

  const ready = canSend && (text.trim().length > 0 || files.length > 0);

  const send = () => {
    if (!ready || sending) return;
    const clear = () => {
      setText('');
      setFiles([]);
      forget();
    };
    const accepted = onSend(text.trim(), files);
    if (!accepted) {
      clear();
      writeDraft(me, id, '');
      box.current?.focus();
      return;
    }
    setSending(true);
    void accepted.then((ok) => {
      if (ok) {
        clear();
        if (readDraft(me, id) === text) writeDraft(me, id, '');
      }
      setSending(false);
    }, () => setSending(false));
  };

  const type = (next: string, at: number | null) => {
    setText(next);
    writeDraft(me, id, next);
    setCaret(at);
    setActive(0);
    if (shut !== null && (at === null || mentionAt(next, at)?.start !== shut)) setShut(null);
  };

  const pick = (who: Actor) => {
    if (!at || caret === null) return;
    // The whole @word goes, through a selection and the rest of the word after it (a name's letters, dots and dashes,
    // never into the next @); one space follows the name.
    const from = Math.max(caret, box.current?.selectionEnd ?? caret);
    const after = text.slice(from);
    let word = /^[\w.-]*/.exec(after)![0];
    // a full stop or dash that ends the sentence is not part of the name
    if (!after.slice(word.length) || /^\s/.test(after.slice(word.length))) word = word.replace(/[.-]+$/, '');
    const rest = after.slice(word.length);
    // no space before punctuation or a space already there; the caret goes past that space
    const put = /^[\s,;:!?.)\]}'"]/.test(rest) ? mentionText(who) : `${mentionText(who)} `;
    const next = text.slice(0, at.start) + put + rest;
    const end = at.start + put.length + (/^\s/.test(rest) ? 1 : 0);
    type(next, end);
    setShut(at.start);
    placeAt.current = end;
    box.current?.focus();
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (open && !e.nativeEvent.isComposing) {
      const step = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
      if (step) {
        e.preventDefault();
        setActive((current + step + names.length) % names.length);
        return;
      }
      if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
        e.preventDefault();
        pick(names[current]!);
        return;
      }
      if (e.key === 'Escape') {
        // Closes the list, not the sheet or dialog the box sits in.
        e.preventDefault();
        e.stopPropagation();
        setShut(at!.start);
        return;
      }
    }
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    send();
  };

  const label = own ?? `Message ${to}`;
  return (
    <>
      {files.map((f, i) => (
        <button key={`${f.name}:${i}`} type="button" className="file composer-file" aria-label={`Remove ${f.name}`} title={`Remove ${f.name}`} disabled={sending}
          onClick={() => setFiles(files.filter((_, j) => j !== i))}>
          <Icon name="file" />
          <span>{f.name}</span>
        </button>
      ))}
      <div className={text ? 'composer is-typing' : 'composer'}>
        <textarea
          ref={box}
          className="composer-text"
          rows={1}
          aria-label={label}
          placeholder={label}
          value={text}
          readOnly={sending}
          onChange={(e) => type(e.target.value, e.target.selectionStart)}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
          onBlur={() => setCaret(null)}
          onKeyDown={onKey}
          // A combobox while its names are open (as @github/combobox-nav does), a plain text box otherwise.
          role={open ? 'combobox' : undefined}
          aria-autocomplete={mentions ? 'list' : undefined}
          aria-haspopup={open ? 'listbox' : undefined}
          aria-controls={open ? listId : undefined}
          aria-expanded={open ? true : undefined}
          aria-activedescendant={open ? `${listId}-${current}` : undefined}
        />
        {open && (
          <div id={listId} className="menu mention-pop" role="listbox" aria-label="Mention">
            {names.map((a, i) => (
              <a
                key={a.id}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === current}
                data-highlighted={i === current ? '' : undefined}
                // The box keeps the focus (and a phone its keyboard) while a name is tapped.
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(a)}
              >
                <MenuItemBody label={a.name} lead={<Avatar kind={a.kind} name={a.name} hue={a.hue} size={22} decorative />} />
              </a>
            ))}
          </div>
        )}
        {attach && (
          <>
            <input
              ref={picker}
              type="file"
              multiple
              tabIndex={-1}
              aria-hidden="true"
              onChange={(e) => {
                const picked = [...(e.target.files ?? [])];
                e.target.value = '';
                if (picked.length) setFiles([...files, ...picked].slice(0, 10));
              }}
            />
            <IconButton icon="attach" label="Attach a file" size="sm" disabled={sending} onClick={() => picker.current?.click()} />
          </>
        )}
        <button className={ready ? 'send is-ready' : 'send'} type="button" aria-label="Send" disabled={!ready || sending} onClick={send}>
          <Icon name="send" />
        </button>
      </div>
    </>
  );
}
