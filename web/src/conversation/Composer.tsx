import { useRef, useState, type KeyboardEvent } from 'react';
import { IconButton } from '@/kit/IconButton';
import { Icon } from '@/kit/Icon';
import { readPref, writePref } from '@/prefs';

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
  /** Send; the box clears at once and the message shows in the list. */
  onSend: (text: string, files: File[]) => void;
  /** Words to put back in the box (a failed message taken back); a new object each time. */
  fill?: { text: string } | null;
  /** The box's words in place of "Message <to>" ("Reply to Sam Lee"). */
  label?: string;
  /** false: no files (a mail reply attaches none). */
  attach?: boolean;
}

/** Where you type. Enter sends, Shift+Enter breaks the line; the draft survives a reload. */
export function Composer({ id, me, to, onSend, fill, label: own, attach = true }: ComposerProps) {
  const [text, setText] = useState(() => readDraft(me, id));
  const [files, setFiles] = useState<File[]>([]);
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [shownFor, setShownFor] = useState(`${me}:${id}`);
  const [filled, setFilled] = useState(fill);

  // Another conversation in the same box: its own draft.
  if (shownFor !== `${me}:${id}`) {
    setShownFor(`${me}:${id}`);
    setText(readDraft(me, id));
    setFiles([]);
  }
  if (fill !== filled) {
    setFilled(fill);
    if (fill) setText(fill.text);
  }

  const ready = text.trim().length > 0 || files.length > 0;

  const send = () => {
    if (!ready) return;
    onSend(text.trim(), files);
    setText('');
    setFiles([]);
    writeDraft(me, id, '');
    box.current?.focus();
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    send();
  };

  const label = own ?? `Message ${to}`;
  return (
    <>
      {files.map((f, i) => (
        <button key={`${f.name}:${i}`} type="button" className="file" aria-label={`Remove ${f.name}`} title={`Remove ${f.name}`}
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
          onChange={(e) => {
            setText(e.target.value);
            writeDraft(me, id, e.target.value);
          }}
          onKeyDown={onKey}
        />
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
            <IconButton icon="attach" label="Attach a file" size="sm" onClick={() => picker.current?.click()} />
          </>
        )}
        <button className={ready ? 'send is-ready' : 'send'} type="button" aria-label="Send" disabled={!ready} onClick={send}>
          <Icon name="send" />
        </button>
      </div>
    </>
  );
}
