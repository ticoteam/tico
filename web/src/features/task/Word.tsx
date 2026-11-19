import { useQuery } from '@tanstack/react-query';
import { Empty } from '@/kit/Empty';
import { wordQuery, type WordRun } from './queries';

/* A Word file drawn readable in the browser (the owner, Nov 19: "so he can see them, with Download beside"): its
   words, headings, lists and bold, on paper. Close to print, not exact, so Download stays in the file's bar. The
   browser's own unzip (DecompressionStream) and XML reader do the work: no library. */

const WORD = /^application\/vnd\.openxmlformats-officedocument\.wordprocessingml\.document/;
export const isWord = (name: string, mime?: string): boolean => WORD.test(mime ?? '') || /\.docx$/i.test(name);

function Runs({ runs }: { runs: WordRun[] }) {
  return runs.map((r, n) => {
    const text = r.i ? <i>{r.text}</i> : r.text;
    return r.b ? <b key={n}>{text}</b> : <span key={n}>{text}</span>;
  });
}

/** A Word file read from `url`, drawn as one sheet of paper. */
export function WordPages({ url, name }: { url: string; name: string }) {
  const q = useQuery(wordQuery(url));
  if (q.isError) return <Empty icon="alert" title={`${name} did not load`} />;
  if (!q.data) return <div className="tv-bones" role="status" aria-busy="true" aria-label="Loading the page" />;
  return (
    <div className="ap-scroll">
      <div className="ap-desk">
        <article className="ap-paper ap-sheet" aria-label={name}>
          {q.data.map((p, n) =>
            p.kind === 'h' ? (
              <h3 key={n}>
                <Runs runs={p.runs} />
              </h3>
            ) : p.kind === 'li' ? (
              <p key={n} className="li">
                <Runs runs={p.runs} />
              </p>
            ) : (
              <p key={n}>
                <Runs runs={p.runs} />
              </p>
            ),
          )}
        </article>
      </div>
    </div>
  );
}
