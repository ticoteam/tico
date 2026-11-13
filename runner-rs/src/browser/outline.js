// The page as a bot reads it (`tico browser read`), run in the runner's isolated world, never the page's own: the
// visible text in reading order, and each element a person could click, type into or pick from, numbered. The
// elements stay in this world's `__ticoRefs` until the next read or navigation, so `click e12` finds the same one.
// It reads the DOM only: nothing from the page's script, and nothing of its stored state.
(() => {
  const MAX_ITEMS = 5000;
  const MAX_CHARS = 80000;
  const items = [];
  const refs = [];
  let chars = 0;
  let cut = false;
  let buf = '';
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'TITLE', 'svg',
    'CANVAS', 'VIDEO', 'AUDIO', 'MAP', 'OBJECT', 'EMBED', 'DATALIST', 'OPTION', 'OPTGROUP']);
  const BUTTON_TYPES = new Set(['button', 'submit', 'reset', 'image']);
  const ARIA_ROLES = new Set(['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem',
    'menuitemcheckbox', 'menuitemradio', 'option', 'combobox', 'textbox', 'searchbox', 'slider', 'spinbutton',
    'treeitem']);
  const CHECKED_ROLES = new Set(['checkbox', 'radio', 'switch', 'menuitemcheckbox', 'menuitemradio']);
  const INTERACTIVE = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],' +
    '[role=tab],[role=menuitem],[contenteditable=""],[contenteditable=true]';
  // A field whose value is a secret by its own say-so: only whether it holds one is shown.
  const SECRET_FIELD = /(password|cc-|one-time-code)/i;

  const squash = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  const add = (item, size) => {
    if (cut) return;
    if (items.length >= MAX_ITEMS || chars + size > MAX_CHARS) {
      cut = true;
      return;
    }
    items.push(item);
    chars += size;
  };
  const flush = () => {
    const s = squash(buf);
    buf = '';
    if (s && s !== '-') add({ k: 't', s: clip(s, 2000) }, s.length);
  };
  const styleOf = (el) => {
    const view = el.ownerDocument.defaultView;
    return view ? view.getComputedStyle(el) : null;
  };
  const boxed = (el) => el.getClientRects().length > 0;
  const deepActive = () => {
    let e = document.activeElement;
    for (let i = 0; e && i < 20; i++) {
      if (e.shadowRoot && e.shadowRoot.activeElement) {
        e = e.shadowRoot.activeElement;
        continue;
      }
      if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
        let d = null;
        try {
          d = e.contentDocument;
        } catch (_) {}
        if (!d || !d.activeElement) break;
        e = d.activeElement;
        continue;
      }
      break;
    }
    return e;
  };
  const focused = deepActive();
  // The fields the runner typed a login value into (`fill`): only whether they hold one is shown, whatever their
  // type is now (a "show password" eye makes a password field a text field).
  const filled = new Set((globalThis.__ticoFilled || []).map((r) => r.deref()).filter(Boolean));

  // The text of `root` without what form controls inside it show (a label wrapping its select).
  const textExcept = (root, skip) => {
    let out = '';
    const walk = (n) => {
      if (n === skip) return;
      if (n.nodeType === 3) {
        out += n.data + ' ';
        return;
      }
      if (n.nodeType !== 1) return;
      const t = n.tagName;
      if (t === 'SELECT' || t === 'TEXTAREA' || t === 'INPUT' || t === 'SCRIPT' || t === 'STYLE') return;
      for (const c of n.childNodes) walk(c);
    };
    walk(root);
    return squash(out);
  };
  const byIds = (el, attr) => {
    const ids = squash(el.getAttribute(attr));
    if (!ids) return '';
    const root = el.getRootNode();
    return squash(ids.split(' ').map((id) => {
      const n = (root.getElementById && root.getElementById(id)) || el.ownerDocument.getElementById(id);
      return n ? textExcept(n, el) : '';
    }).join(' '));
  };

  const roleOf = (el) => {
    const tag = el.tagName;
    const aria = squash(el.getAttribute('role')).split(' ')[0].toLowerCase();
    if (tag === 'INPUT') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'hidden') return null;
      if (BUTTON_TYPES.has(t)) return 'button';
      if (t === 'checkbox') return aria === 'switch' ? 'switch' : 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'range') return 'slider';
      if (t === 'file') return 'file';
      return aria === 'combobox' ? 'combobox' : 'textbox';
    }
    if (tag === 'TEXTAREA') return 'textbox';
    if (tag === 'SELECT') return 'select';
    if (tag === 'BUTTON') return ARIA_ROLES.has(aria) && aria !== 'textbox' ? aria : 'button';
    if (tag === 'SUMMARY') return 'button';
    if (ARIA_ROLES.has(aria)) return aria === 'searchbox' ? 'textbox' : aria;
    if (tag === 'A' && el.hasAttribute('href')) return 'link';
    if (el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable)) return 'textbox';
    return null;
  };

  // Something a script made clickable: a click handler named in the markup, a tab stop, or the hand cursor where
  // it starts; only a small one with words, holding no control of its own.
  const clickable = (el, cs) => {
    const tag = el.tagName;
    if (tag === 'LABEL' || tag === 'BODY' || tag === 'HTML') return false;
    const tab = el.getAttribute('tabindex');
    const parent = el.parentElement;
    const pointer = cs.cursor === 'pointer' && !(parent && styleOf(parent) && styleOf(parent).cursor === 'pointer');
    if (!(el.hasAttribute('onclick') || (tab !== null && +tab >= 0) || pointer)) return false;
    if (el.querySelector(INTERACTIVE)) return false;
    const t = squash(el.innerText);
    return (t.length > 0 && t.length <= 150) || !!el.getAttribute('aria-label');
  };

  const nameOf = (el, role) => {
    let n = byIds(el, 'aria-labelledby') || squash(el.getAttribute('aria-label'));
    const tag = el.tagName;
    if (!n && (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA')) {
      if (el.labels && el.labels.length) n = squash(Array.from(el.labels).map((l) => textExcept(l, el)).join(' '));
      const t = (el.type || '').toLowerCase();
      if (!n && tag === 'INPUT' && BUTTON_TYPES.has(t)) {
        n = squash(el.value) || (t === 'image' ? squash(el.alt) : '') ||
          (t === 'submit' ? 'Submit' : t === 'reset' ? 'Reset' : '');
      }
    }
    if (!n && role !== 'textbox' && role !== 'select' && role !== 'combobox') {
      n = squash(el.innerText);
      if (!n) {
        const img = el.querySelector('img[alt]');
        if (img) n = squash(img.alt);
      }
      if (!n) {
        const t = el.querySelector('svg title');
        if (t) n = squash(t.textContent);
      }
    }
    if (!n) n = squash(el.getAttribute('title'));
    return clip(n, 120);
  };

  const states = (el, role) => {
    const st = [];
    const type = (el.type || '').toLowerCase();
    if (role === 'textbox' && el.tagName === 'INPUT' && type && type !== 'text') st.push(type);
    if (role === 'textbox' && el.tagName === 'TEXTAREA') st.push('multiline');
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') st.push('disabled');
    if (CHECKED_ROLES.has(role)) {
      const on = el.tagName === 'INPUT' ? el.checked : el.getAttribute('aria-checked') === 'true';
      st.push(on ? 'checked' : 'unchecked');
    }
    if (el.getAttribute('aria-pressed') === 'true') st.push('pressed');
    const expanded = el.getAttribute('aria-expanded');
    if (expanded === 'true') st.push('expanded');
    if (expanded === 'false') st.push('collapsed');
    if (el.getAttribute('aria-selected') === 'true') st.push('selected');
    if (el.readOnly && role === 'textbox') st.push('read-only');
    if (el.required || el.getAttribute('aria-required') === 'true') st.push('required');
    if (el === focused) st.push('focused');
    return st;
  };

  // Where a link goes, so a bot can cite it (a message's permalink): http(s) only, and never the query or fragment,
  // which may carry a token.
  const address = (a) => {
    try {
      const u = new URL(a.href);
      return u.protocol === 'http:' || u.protocol === 'https:' ? clip(u.origin + u.pathname, 200) : '';
    } catch (_) {
      return '';
    }
  };

  const emit = (el, role, control) => {
    const c = control || el;
    refs.push(el);
    const it = { k: 'e', r: refs.length, role };
    const st = states(c, role);
    let name = nameOf(c, role);
    const ph = squash(c.getAttribute('placeholder'));
    if (!name) name = ph ? clip(ph, 120) : clip(squash(c.getAttribute('name')), 60);
    if (name) it.name = name;
    if (role === 'link' && c.tagName === 'A') {
      const to = address(c);
      if (to) it.to = to;
    }
    if (ph && ph !== name) it.ph = clip(ph, 80);
    const tag = c.tagName;
    if ((role === 'textbox' || role === 'combobox') && (tag === 'INPUT' || tag === 'TEXTAREA')) {
      const secret = filled.has(c) || (c.type || '').toLowerCase() === 'password' ||
        SECRET_FIELD.test(c.autocomplete || '');
      if (secret) {
        if (c.value) st.push('filled');
      } else if (c.value) {
        it.v = clip(c.value, 100);
      }
    } else if (role === 'textbox' && c.isContentEditable && filled.has(c)) {
      if (squash(c.innerText)) st.push('filled');
    } else if (role === 'textbox' && c.isContentEditable) {
      const v = squash(c.innerText);
      if (v) it.v = clip(v, 200);
    } else if (role === 'select' && tag === 'SELECT') {
      const opts = Array.from(c.options);
      const picked = opts.filter((o) => o.selected).map((o) => squash(o.text));
      if (picked.length) it.v = clip(picked.join(', '), 100);
      it.opts = opts.slice(0, 20).map((o) => clip(squash(o.text), 60));
      if (opts.length > 20) it.more = opts.length - 20;
    } else if (role === 'slider' && tag === 'INPUT') {
      it.v = c.value;
    } else if (role === 'file' && c.files && c.files.length) {
      it.v = clip(Array.from(c.files).map((f) => f.name).join(', '), 100);
    }
    if (st.length) it.st = st;
    add(it, (name ? name.length : 0) + 24);
  };

  const kids = (el) => {
    if (el.shadowRoot) return el.shadowRoot.childNodes;
    if (el.tagName === 'SLOT') {
      const assigned = el.assignedNodes({ flatten: true });
      return assigned.length ? assigned : el.childNodes;
    }
    return el.childNodes;
  };

  const frame = (el, visible, depth) => {
    let d = null;
    try {
      d = el.contentDocument;
    } catch (_) {}
    if (d && d.body) {
      flush();
      walk(d.body, visible, depth + 1);
      flush();
      return;
    }
    if (!visible || !boxed(el)) return;
    let host = '';
    try {
      host = new URL(el.src, el.baseURI).host;
    } catch (_) {}
    flush();
    add({ k: 'f', host: host || 'frame' }, 24);
  };

  const walk = (node, visible, depth) => {
    if (cut || depth > 300) return;
    if (node.nodeType === 3) {
      if (visible) buf += node.data;
      return;
    }
    if (node.nodeType === 11) {
      for (const c of node.childNodes) walk(c, visible, depth + 1);
      return;
    }
    if (node.nodeType !== 1) return;
    const el = node;
    if (SKIP.has(el.tagName)) return;
    const cs = styleOf(el);
    if (!cs || cs.display === 'none') return;
    if (el.tagName === 'BR') {
      flush();
      return;
    }
    const shown = cs.visibility === 'visible';
    const d = cs.display;
    const cell = d === 'table-cell';
    const block = !cell && !(d.startsWith('inline') || d === 'contents' || d === 'ruby');
    if (cell) {
      if (squash(buf)) buf += ' | ';
    } else if (block) {
      flush();
    }
    if (d === 'list-item') buf = '- ';
    if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
      frame(el, shown, depth);
      return;
    }
    const level = /^H[1-6]$/.test(el.tagName)
      ? +el.tagName[1]
      : el.getAttribute('role') === 'heading' ? (+el.getAttribute('aria-level') || 2) : 0;
    if (level && shown && !el.querySelector(INTERACTIVE)) {
      flush();
      const s = squash(el.innerText);
      if (s) add({ k: 'h', l: Math.min(level, 6), s: clip(s, 300) }, s.length);
      return;
    }
    let role = roleOf(el);
    let control = null;
    if (!role && el.tagName === 'LABEL' && el.control && !boxed(el.control)) {
      // A styled checkbox: the real one is hidden and its label takes the click.
      const t = (el.control.type || '').toLowerCase();
      if (t === 'checkbox' || t === 'radio') {
        role = t;
        control = el.control;
      }
    }
    if (!role && shown && clickable(el, cs)) role = 'clickable';
    if (role && shown && boxed(el)) {
      flush();
      emit(el, role, control);
      if (role === 'textbox' || role === 'select' || !el.querySelector(INTERACTIVE)) return;
    }
    for (const c of kids(el)) walk(c, shown, depth + 1);
    if (block) flush();
    else if (cell) buf += ' ';
  };

  walk(document.body || document.documentElement, true, 0);
  flush();
  globalThis.__ticoRefs = refs;
  return { items, cut };
})()
