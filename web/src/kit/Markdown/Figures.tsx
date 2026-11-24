import { useEffect, useRef, useState } from 'react';
import { IconButton } from '@/kit/IconButton';
import { PictureViewer } from '@/kit/PictureViewer';
import { ChatCode, useSeen } from './Code';
import { drawDiagram } from './diagram';
import { TEX_MAX } from './math';

/* What a settled chat message draws from its text beyond code: a `$$` formula typeset, and a Mermaid diagram as a
   picture. Until each is ready, and whenever it cannot be drawn, its source shows as a code block. */

/** A `$$ … $$` block or a ```math fence: KaTeX, loaded the first time one shows. */
export function ChatMath({ text, settled = true }: { text: string; settled?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<{ text: string; ok: boolean } | null>(null);
  const on = settled && text.length <= TEX_MAX;
  useEffect(() => {
    const el = ref.current;
    if (!on || !el) return;
    let live = true;
    import('./katex').then(
      (k) => {
        if (!live) return;
        try {
          k.typeset(text, el);
          setState({ text, ok: true });
        } catch {
          el.replaceChildren();
          setState({ text, ok: false });
        }
      },
      () => live && setState({ text, ok: false }),
    );
    return () => {
      live = false;
    };
  }, [text, on]);
  const ok = state?.text === text ? state.ok : null;
  return (
    <>
      {/* KaTeX owns this element's children; React leaves it empty */}
      {on && ok !== false && <div ref={ref} className="md-math" hidden={ok !== true} />}
      {ok !== true && <ChatCode text={text} lang="math" settled={false} />}
    </>
  );
}

const darkPage = () => document.documentElement.dataset.theme !== 'light';
/** The well's colour, which the diagram is drawn on. */
const well = (el: Element) => getComputedStyle(el).getPropertyValue('--well').trim();

/** A ```mermaid fence, drawn in the diagram frame (./diagram.ts) once it comes into view; Expand zooms it. */
export function ChatDiagram({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useSeen(ref, true);
  const [pic, setPic] = useState<{ text: string; url: string; width: number; height: number } | 'failed' | null>(null);
  const [at, setAt] = useState<number | null>(null);
  useEffect(() => {
    if (!seen) return;
    let live = true;
    let url = '';
    void drawDiagram(text, darkPage(), ref.current ? well(ref.current) : '').then((d) => {
      if (!live) return;
      if (!d) return setPic('failed');
      url = URL.createObjectURL(new Blob([d.svg], { type: 'image/svg+xml' }));
      setPic({ text, url, width: d.width, height: d.height });
    });
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url); // the viewer is inside this block, so it has let go too
    };
  }, [seen, text]);
  if (!pic || pic === 'failed' || pic.text !== text) return <ChatCode text={text} lang="mermaid" settled={false} boxRef={ref} />;
  return (
    <div ref={ref} className="md-block md-diagram">
      <button type="button" className="md-diagram-face" onClick={() => setAt(0)} aria-label="Expand diagram">
        <img src={pic.url} width={pic.width} height={pic.height} alt="Diagram" />
      </button>
      <span className="md-tools">
        <IconButton icon="expand" label="Expand" size="sm" quiet onClick={() => setAt(0)} />
      </span>
      <PictureViewer local pictures={[{ src: pic.url, name: 'Diagram.svg' }]} at={at} onAtChange={setAt} />
    </div>
  );
}
