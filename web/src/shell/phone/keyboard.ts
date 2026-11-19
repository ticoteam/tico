/* The on-screen keyboard, from visualViewport (platforms.md 2.3). iOS does not shrink the layout viewport for the
   keyboard; it shrinks the visual one and may pan it. `kb` is the height hidden under the keys and `top` is the pan,
   so a fixed layer shifted down by `top` and ending `kb` above its bottom fills exactly what can be seen. */

export type Keys = { kb: number; top: number; up: boolean };

const UP = 120; // ignores the iPad's shortcut bar

export function watchKeyboard(onChange: (keys: Keys) => void): () => void {
  const vv = window.visualViewport;
  if (!vv) return () => {};
  let raf = 0;
  let blur = 0;
  let last = '';
  const apply = () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const top = Math.max(0, Math.round(vv.offsetTop));
      // times scale so a pinch zoom does not read as a keyboard
      const kb = Math.max(0, Math.round(window.innerHeight - vv.height * vv.scale));
      const keys = { kb, top, up: kb > UP };
      const key = `${kb}:${top}`;
      if (key !== last) {
        last = key;
        onChange(keys);
      }
    });
  };
  const later = () => {
    clearTimeout(blur);
    blur = window.setTimeout(apply, 50);
  };
  vv.addEventListener('resize', apply);
  vv.addEventListener('scroll', apply);
  window.addEventListener('focusin', apply);
  window.addEventListener('focusout', later);
  apply();
  return () => {
    cancelAnimationFrame(raf);
    clearTimeout(blur);
    vv.removeEventListener('resize', apply);
    vv.removeEventListener('scroll', apply);
    window.removeEventListener('focusin', apply);
    window.removeEventListener('focusout', later);
  };
}

const AT_BOTTOM = 48;

/** Scrollers in `el` that sit at their end now, so they can be put back there after the layout changes. */
export function stuckToBottom(el: Element | null): HTMLElement[] {
  if (!el) return [];
  return [...el.querySelectorAll<HTMLElement>('[data-scroll], .chat-body')].filter(
    (s) => s.scrollHeight - s.scrollTop - s.clientHeight < AT_BOTTOM,
  );
}
