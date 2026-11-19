import { useEffect, useLayoutEffect, useRef } from 'react';
import { List, type PageTab } from '@/pagekinds';
import { fillTokens } from './parts';
import { ChatArea, ColorArea, ControlsArea, MarksArea, MotionArea, PagesArea, RowsArea, SheetsArea, TypeArea } from './sections';
import './styleguide.css';

export type GuideArea = 'color' | 'type' | 'marks' | 'rows' | 'controls' | 'chat' | 'sheets' | 'motion' | 'pages';
export type StyleGuidePageProps = { at?: string };

const PATH = '/settings/style-guide';
const AREAS: readonly (readonly [GuideArea, string])[] = [
  ['color', 'Color'],
  ['type', 'Type'],
  ['marks', 'Marks'],
  ['rows', 'Rows'],
  ['controls', 'Controls'],
  ['chat', 'Chat'],
  ['sheets', 'Sheets'],
  ['motion', 'Motion'],
  ['pages', 'Pages'],
];
/* Color is the page's first area, so its tab is the bare address. */
const TABS: PageTab[] = AREAS.map(([id, label]) => (id === 'color' ? { label, to: PATH } : { label, to: PATH, search: { at: id } }));

/** The Style guide: the target design, drawn with the app's own parts, tokens and page-kind slots, so it cannot drift. */
export function StyleGuidePage({ at }: StyleGuidePageProps) {
  const all = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const root = all.current;
    if (!root) return;
    const fill = () => fillTokens(root);
    fill();
    let live = true;
    void document.fonts?.ready.then(() => live && fill());
    const watch = new MutationObserver(fill);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-layout'] });
    return () => {
      live = false;
      watch.disconnect();
    };
  }, []);

  useEffect(() => {
    if (at) document.getElementById(`sg-${at}`)?.scrollIntoView?.({ block: 'start', behavior: 'auto' });
  }, [at]);

  return (
    <List
      title="Style guide"
      path={PATH}
      tabs={TABS}
      className="sg-page"
      sections={
        <div ref={all} className="sg-all">
          <ColorArea />
          <TypeArea />
          <MarksArea />
          <RowsArea />
          <ControlsArea />
          <ChatArea />
          <SheetsArea />
          <MotionArea />
          <PagesArea />
        </div>
      }
    />
  );
}
