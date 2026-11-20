import { Dialog } from '@base-ui/react/dialog';
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type RefObject } from 'react';
import { ctxFor, registry, run } from '@/actions';
import { Avatar } from '@/kit/Avatar';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Dot } from '@/tico/dot';
import * as copy from './copy';
import { loadProviders, providers, type Lead, type SearchHit } from './providers';
import { gather, highlight, remember, RECENT, type Group } from './results';
import './search.css';
import { caught, closeSearch, released, useSearchState } from './state';

/* The palette from pages/8-settings/more.html?screen=search: it drops from the menu's head and covers the menu and the
   page, which dim; Tico stays lit, since the last row hands the words to it. Base UI's dialog directly, not kit's:
   that one is a titled modal with a foot of buttons over a scrim that would dim Tico too. */
export function SearchDialog() {
  const { open, q, n } = useSearchState();
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && closeSearch()}>
      <Dialog.Portal>
        {/* keyed by each open, so the field starts again from what it was opened with */}
        <Palette key={n} opened={q} />
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Drop from the visible menu sheet's top edge. */
function dropAt(): CSSProperties {
  const r = document.querySelector('.shell-frame .nav')?.getBoundingClientRect();
  return r?.width ? { left: Math.round(r.left), top: Math.round(r.top) } : {};
}

/** On a phone the keyboard covers the page's bottom (iOS shrinks only the visual viewport, and may pan it): the
    palette keeps inside what can be seen. */
function useVisible(el: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const vv = window.visualViewport;
    const node = el.current;
    if (!vv || !node) return;
    const put = () => {
      node.style.setProperty('--vv-h', `${Math.round(vv.height)}px`);
      node.style.setProperty('--vv-top', `${Math.max(0, Math.round(vv.offsetTop))}px`);
    };
    put();
    vv.addEventListener('resize', put);
    vv.addEventListener('scroll', put);
    return () => {
      vv.removeEventListener('resize', put);
      vv.removeEventListener('scroll', put);
    };
  }, [el]);
}

function Palette({ opened }: { opened: string }) {
  // anything typed between the tap (or Cmd+K) and this field drawing went to the catcher (state.ts)
  const [q, setQ] = useState(() => opened + caught());
  const [results, setResults] = useState<{ query: string; groups: Group[] }>({ query: opened, groups: [] });
  const [selected, setSelected] = useState<string | null>();
  const [at] = useState(dropAt);
  const field = useRef<HTMLInputElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  useVisible(popup);

  // the cursor moves from the catcher to the field as it draws, so the keyboard a tap raised stays up
  useLayoutEffect(() => {
    const f = field.current;
    if (!f) return;
    f.focus({ preventScroll: true });
    f.setSelectionRange(f.value.length, f.value.length);
    released();
  }, []);

  useEffect(() => {
    const app = document.querySelector('.shell-frame');
    app?.classList.add('is-search');
    return () => app?.classList.remove('is-search');
  }, []);

  // People and pages answer at once; the features' providers load on the first open and are kept after.
  const [from, setFrom] = useState(() => providers);
  useEffect(() => {
    let live = true;
    void loadProviders().then((all) => live && setFrom(all));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const stop = new AbortController();
    const show = (g: Group[]) => {
      if (stop.signal.aborted) return;
      setResults({ query: q, groups: g });
    };
    void gather(q, from, registry.values(), ctxFor('search'), stop.signal, show).then(show);
    return () => stop.abort();
  }, [q, from]);

  const groups = results.query === q ? results.groups : [];
  const ask = q.trim() && registry.has('tico.ask') ? q.trim() : '';
  const hits = groups.flatMap((g) => g.hits);
  const count = hits.length + (ask ? 1 : 0);
  const on = selected === null && ask ? hits.length : Math.max(0, hits.findIndex((hit) => hit.key === selected));
  const starts = groups.map((_, g) => groups.slice(0, g).reduce((sum, x) => sum + x.hits.length, 0));

  const changeQuery = (value: string) => {
    setQ(value);
    setSelected(undefined);
  };
  const select = (i: number) => setSelected(hits[i]?.key ?? null);
  const choose = (i: number) => {
    closeSearch();
    const hit = hits[i];
    if (hit) {
      remember(hit);
      void run(hit.run.id, hit.run.input, { via: 'search' });
    } else if (ask) void run('tico.ask', { text: ask }, { via: 'search' });
  };
  const keys = (e: KeyboardEvent) => {
    if (!count) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const next = (on + (e.key === 'ArrowDown' ? 1 : count - 1)) % count;
      select(next);
      document.getElementById(`pal-${next}`)?.scrollIntoView?.({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      choose(on);
    }
  };
  const option = (i: number) => ({
    id: `pal-${i}`,
    role: 'option',
    'aria-selected': i === on,
    onPointerMove: () => select(i),
    onClick: () => choose(i),
  });

  const row = (hit: SearchHit, i: number) => (
    <div key={hit.key} className={i === on ? 'sr is-on' : 'sr'} {...option(i)}>
      <LeadOf lead={hit.lead} />
      <span>{highlight(hit.title, q).map((p, k) => (p.hit ? <mark key={k}>{p.text}</mark> : p.text))}</span>
      <em>{hit.meta ?? ''}</em>
    </div>
  );

  return (
    <Dialog.Popup ref={popup} className="pal is-drop" aria-label={copy.label} style={at} initialFocus={field}>
      <label className="pal-field" data-target="search:field">
        <Icon name="search" />
        <input
          ref={field}
          className="pal-text"
          type="text"
          value={q}
          placeholder={copy.label}
          aria-label={copy.label}
          role="combobox"
          aria-expanded={count > 0}
          aria-controls="pal-list"
          aria-activedescendant={count ? `pal-${on}` : undefined}
          autoComplete="off"
          onChange={(e) => changeQuery(e.target.value)}
          onKeyDown={keys}
        />
        {q && <IconButton icon="close" label={copy.clear} size="sm" onClick={() => changeQuery('')} />}
      </label>
      <div className="pal-list" id="pal-list" role="listbox" aria-label={copy.label}>
        {groups.map((g, gi) => (
          <div key={g.name} role="group" aria-label={g.name} className="pal-set">
            <div className="pal-group t-label">
              <span>{g.name}</span>
              {g.name !== RECENT && <span className="count">{g.hits.length}</span>}
            </div>
            {g.hits.map((h, k) => row(h, (starts[gi] ?? 0) + k))}
          </div>
        ))}
        {q.trim() && !hits.length && (
          <p className="sr-none">
            {copy.nothing} <b>{q.trim()}</b>
          </p>
        )}
        {ask && (
          <div className={on === hits.length ? 'sr pal-ask is-on' : 'sr pal-ask'} {...option(hits.length)}>
            <Avatar kind="agent" name="Tico" size={22} decorative dot={<Dot size={19} still />} />
            <span>
              {copy.ask} <b>{ask}</b>
            </span>
            <em />
          </div>
        )}
      </div>
    </Dialog.Popup>
  );
}

function LeadOf({ lead }: { lead: Lead | undefined }) {
  if (!lead) return <span />;
  if ('icon' in lead)
    return (
      <span className="tl-kind">
        <Icon name={lead.icon} />
      </span>
    );
  const f = lead.face;
  return (
    <span className={f.bot ? 'av av-22 is-bot' : 'av av-22'} style={{ '--h': f.hue } as CSSProperties} title={f.name}>
      {f.initials}
    </span>
  );
}
