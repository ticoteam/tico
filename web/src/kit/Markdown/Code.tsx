import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { IconButton } from '@/kit/IconButton';
import { toast } from '@/kit/Toast';
import { Viewer } from '@/kit/Viewer';
import { parseDiff } from './diff';
import type { Piece } from './highlight';

/* A chat's code blocks: plain text first, coloured once the message has settled and the block has come into view
   (live text never colours, so a streaming reply is not coloured again on each word). Each carries its language, Copy,
   Wrap and Expand to the full-screen layer. A ```diff block is a unified diff in the app's greys. Never cut. */

/** True from the first time the element comes near the screen, while `on`. */
export function useSeen(ref: RefObject<Element | null>, on: boolean) {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!on || seen || !el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && setSeen(true), { rootMargin: '200px' });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, on, seen]);
  return seen;
}

/** A phone wraps code by default (its screen is narrow); a desktop scrolls it sideways. */
const wrapsFirst = () => typeof document !== 'undefined' && document.documentElement.dataset.layout === 'cards';

function copy(text: string) {
  return (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject(new Error('no clipboard'))).then(
    () => toast({ text: 'Copied' }),
    () => toast({ text: "Couldn't copy", kind: 'failed' }),
  );
}

function useHighlight(text: string, lang: string, on: boolean) {
  const [lines, setLines] = useState<{ text: string; lang: string; lines: Piece[][] } | null>(null);
  useEffect(() => {
    if (!on || !lang) return;
    let live = true;
    import('./highlight')
      .then((h) => h.highlight(text, lang))
      .then(
        (got) => live && got && setLines({ text, lang, lines: got }),
        () => undefined, // stays plain text
      );
    return () => {
      live = false;
    };
  }, [text, lang, on]);
  return on && lines?.text === text && lines.lang === lang ? lines.lines : null;
}

function Pieces({ lines }: { lines: Piece[][] }) {
  return lines.map((line, i) => (
    <span key={i}>
      {line.map((p, j) => (p.kind ? <span key={j} className={`hl-${p.kind}`}>{p.text}</span> : p.text))}
      {i < lines.length - 1 && '\n'}
    </span>
  ));
}

interface FrameProps {
  /** What the author named it (`ts`, `diff`), shown at its top. */
  label: string;
  /** What Copy copies. */
  text: string;
  /** The block's body; `full` in the expanded layer. */
  body: (wrap: boolean, full: boolean) => ReactNode;
  className?: string;
  boxRef?: RefObject<HTMLDivElement | null>;
}

/** The well a block sits in: its language and tools in a slim head, the body under it. */
function Frame({ label, text, body, className, boxRef }: FrameProps) {
  const [wrap, setWrap] = useState(wrapsFirst);
  const [open, setOpen] = useState(false);
  const tools = (
    <>
      <IconButton icon="wrap" label="Wrap" size="sm" quiet on={wrap} onClick={() => setWrap((w) => !w)} />
      <IconButton icon="copy" label="Copy" size="sm" quiet onClick={() => copy(text)} />
    </>
  );
  return (
    <div ref={boxRef} className={['md-block', 'md-code', className].filter(Boolean).join(' ')}>
      <div className="md-code-head">
        <span className="md-lang">{label}</span>
        <span className="md-tools">
          {tools}
          <IconButton icon="expand" label="Expand" size="sm" quiet onClick={() => setOpen(true)} />
        </span>
      </div>
      {body(wrap, false)}
      {open && (
        <Viewer open onClose={() => setOpen(false)} title={label || 'Code'} actions={tools}>
          <div className={['vw-scroll', 'md-code', 'is-full', className].filter(Boolean).join(' ')}>{body(wrap, true)}</div>
        </Viewer>
      )}
    </div>
  );
}

const wrapClass = (wrap: boolean) => (wrap ? 'is-wrap' : 'is-nowrap');

/** A fenced code block: `settled` once the message is whole. */
export function ChatCode({ text, lang = '', settled = true, boxRef }: { text: string; lang?: string; settled?: boolean; boxRef?: RefObject<HTMLDivElement | null> }) {
  const own = useRef<HTMLDivElement>(null);
  const ref = boxRef ?? own;
  const seen = useSeen(ref, settled && !!lang);
  const lines = useHighlight(text, lang, settled && seen);
  return (
    <Frame
      label={lang}
      text={text}
      boxRef={ref}
      body={(wrap) => (
        <pre className={wrapClass(wrap)}>
          <code>{lines ? <Pieces lines={lines} /> : text}</code>
        </pre>
      )}
    />
  );
}

/** A ```diff block, unified: + and − in the gutter, added lines in full ink, removed ones struck through and dim. */
export function ChatDiff({ text }: { text: string }) {
  const lines = parseDiff(text);
  return (
    <Frame
      label="diff"
      text={text}
      className="md-diff"
      body={(wrap) => (
        <pre className={wrapClass(wrap)}>
          <code>
            {lines.map((l, i) => (
              <span key={i} className={`dl is-${l.kind}`}>
                <span className="dl-sign" aria-hidden={!l.sign || undefined}>
                  {l.sign}
                </span>
                <span className="dl-text">{l.text || ' '}</span>
              </span>
            ))}
          </code>
        </pre>
      )}
    />
  );
}
