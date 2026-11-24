import katex from 'katex';
import 'katex/dist/katex.min.css';

/* Typesetting for a chat's `$$` blocks, loaded the first time one shows (its stylesheet and fonts come with it). No
   trusted commands (`\href`, `\includegraphics`, raw classes and styles show as errors), and caps on macro expansion
   and sizes, so a hostile formula cannot hang the page or cover it. */
export function typeset(tex: string, into: HTMLElement) {
  katex.render(tex, into, {
    displayMode: true,
    throwOnError: true,
    trust: false,
    strict: 'ignore',
    maxExpand: 200,
    maxSize: 20,
  });
}
