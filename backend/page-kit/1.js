/* Page kit 1 (backend/visuals.py KIT): display tags that draw an agent's page in Tico's look. Frozen once released: a
   page version keeps the kit it was published with (bot_file_versions.page_kit), so a change is a new file, 2.js.
   Values are the app's (web/src/styles/tokens.css, kit Chart/Table/Pill) as of kit 1, dark and light. Each tag shows
   its own inner HTML until this runs, so a page reads without it. */
(function () {
  if (!window.customElements || customElements.get('tico-stat')) return;
  var d = document.documentElement;
  var CSS =
    'html{--tk-font:"Atkinson Hyperlegible Next",system-ui,-apple-system,"Segoe UI",sans-serif;' +
    '--tk-ink:#F2F2F2;--tk-ink-2:#C8C8C8;--tk-muted:#9C9C9C;--tk-faint:#737373;--tk-line:#262626;--tk-line-strong:#3A3A3A;' +
    '--tk-hover:#212121;--tk-stack:#474747;--tk-stack-top:#6C6C6C;--tk-on-ink:#111111;--tk-warn:#E2B25A;' +
    '--tk-warn-line:rgba(226,178,90,.45);--tk-chart:160px;--tk-fig:26px}' +
    'html[data-tico-kit=light]{--tk-ink:#111111;--tk-ink-2:#3B3B3B;--tk-muted:#5E5E5E;--tk-faint:#878787;--tk-line:#E4E4E4;' +
    '--tk-line-strong:#CECECE;--tk-hover:#F3F3F3;--tk-stack:#BDBDBD;--tk-stack-top:#8D8D8D;--tk-on-ink:#FFFFFF;' +
    '--tk-warn:#8A5A10;--tk-warn-line:rgba(138,90,16,.5)}' +
    'html[data-tico-view=card]{--tk-chart:96px;--tk-fig:22px}' +
    'tico-stat,tico-chart,tico-table,tico-list{display:block;font-family:var(--tk-font);font-size:14px;line-height:1.45;color:var(--tk-ink)}' +
    'tico-stat{display:inline-block;vertical-align:top;min-width:112px;margin:0 24px 12px 0}' +
    'tico-chart{margin:0 0 16px}' +
    'tico-badge{display:inline-block;height:20px;padding:0 7px;border-radius:6px;font:400 12px/20px var(--tk-font);' +
    'color:var(--tk-ink-2);box-shadow:inset 0 0 0 1px var(--tk-line-strong);white-space:nowrap;vertical-align:1px}' +
    'tico-badge[tone=warn]{color:var(--tk-warn);box-shadow:inset 0 0 0 1px var(--tk-warn-line)}' +
    'tico-badge[tone=strong]{background:var(--tk-ink);color:var(--tk-on-ink);box-shadow:none}' +
    'tico-table{overflow-x:auto;margin:0 0 16px}' +
    'tico-table table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}' +
    'tico-table :is(th,td){height:34px;padding:0 12px;text-align:left;white-space:nowrap}' +
    'tico-table th{font-size:13px;font-weight:400;color:var(--tk-muted);border-bottom:1px solid var(--tk-line-strong)}' +
    'tico-table td{border-bottom:1px solid var(--tk-line)}' +
    'tico-table tbody tr:last-child td{border-bottom:0}' +
    'tico-table tr:hover td{background:var(--tk-hover)}' +
    'tico-table :is(th,td)[data-tico-num]{text-align:right}' +
    'tico-table a,tico-list a{color:inherit;text-decoration:none}' +
    'tico-table a:hover,tico-list a:hover{text-decoration:underline}' +
    'tico-list{margin:0 0 16px}' +
    'tico-list :is(ul,ol){list-style:none;margin:0;padding:0}' +
    'tico-list li{display:flex;align-items:center;gap:8px;min-height:34px;padding:0 8px;border-bottom:1px solid var(--tk-line)}' +
    'tico-list li:last-child{border-bottom:0}' +
    'tico-list li>:is(tico-badge,small,time){margin-left:auto;flex:none;color:var(--tk-muted);font-size:12px}' +
    'tico-list li>tico-badge{color:var(--tk-ink-2)}' +
    'tico-list li>:is(tico-badge,small,time)+:is(tico-badge,small,time){margin-left:0}';
  var SHADOW =
    ':host{display:block}' +
    '.l,.cap{font-size:13px;color:var(--tk-muted)}' +
    '.v{font-size:var(--tk-fig);font-weight:650;letter-spacing:-.018em;line-height:1.2;font-variant-numeric:tabular-nums}' +
    '.t{margin-left:8px;font-size:13px;font-weight:550;letter-spacing:0;color:var(--tk-ink-2)}' +
    '.s{font-size:12px;color:var(--tk-muted)}' +
    '.cap{margin:0 0 8px}' +
    '.bars{display:grid;grid-template-columns:minmax(0,max-content) minmax(40px,1fr) auto;column-gap:12px;row-gap:4px;align-items:center}' +
    '.n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}' +
    '.k{display:block;height:10px}' +
    '.k i{display:block;height:100%;min-width:2px;border-radius:999px;background:var(--tk-stack-top)}' +
    '.k i.top{background:var(--tk-ink-2)}' +
    '.c{text-align:right;font-size:13px;font-variant-numeric:tabular-nums}' +
    'svg{display:block;width:100%;overflow:visible}' +
    'svg text{font-size:12px;fill:var(--tk-muted);font-family:var(--tk-font)}' +
    'svg text.now{fill:var(--tk-ink);font-weight:550}' +
    '.ln{fill:none;stroke:var(--tk-ink-2);stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}' +
    '.base{stroke:var(--tk-line-strong);stroke-width:1}' +
    '.pt{fill:var(--tk-ink)}';

  var style = document.createElement('style');
  style.textContent = CSS;
  (document.head || d).appendChild(style);

  // Light on a light background. With none (the reporter darkens a page that chose none, unless it chose its own dark
  // text, which then sits on the white canvas), light under dark text.
  function theme() {
    var bg = '';
    [document.body, d].some(function (e) {
      var c = e && getComputedStyle(e).backgroundColor;
      if (c && c !== 'transparent' && c !== 'rgba(0, 0, 0, 0)') return (bg = c);
    });
    var n = ((bg || getComputedStyle(document.body || d).color || '').match(/[0-9.]+/g) || [0, 0, 0]).map(Number),
      bright = 0.299 * n[0] + 0.587 * n[1] + 0.114 * n[2] >= 128;
    d.setAttribute('data-tico-kit', bg ? (bright ? 'light' : 'dark') : bright ? 'dark' : 'light');
  }
  theme();
  if (window.matchMedia) {
    var mq = matchMedia('(prefers-color-scheme: dark)');
    if (mq.addEventListener) mq.addEventListener('change', theme);
  }

  function el(tag, cls, text, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  var NS = 'http://www.w3.org/2000/svg';
  function svg(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    parent.appendChild(e);
    return e;
  }

  // "Label 10" -> {label, text: "10", value: 10}. The value is the last figure: "$1,200", "4.5%", "12k".
  // "1.2k" is 1200 and "3M" 3,000,000; "−50" (a typeset minus) is -50.
  var POINT = /^(.*?)\s*([-+\u2212]?[$€£]?\d[\d,]*(?:\.\d+)?\s*[%kKmM]?)\s*$/;
  var SCALE = { k: 1e3, m: 1e6 };
  function point(s) {
    var m = POINT.exec(String(s).replace(/\s+/g, ' ').trim());
    if (!m) return null;
    var t = m[2].replace('\u2212', '-'),
      v = parseFloat(t.replace(/[^\d.+-]/g, '')) * (SCALE[t.slice(-1).toLowerCase()] || 1);
    return isFinite(v) ? { label: m[1], text: m[2], value: v } : null;
  }

  // A tag drawn from its attributes into its own shadow: the page's CSS never reaches inside it, and its inner HTML is
  // what shows without the kit.
  class Drawn extends HTMLElement {
    connectedCallback() {
      if (!this.shadowRoot) {
        var root = this.attachShadow({ mode: 'open' });
        el('style', null, SHADOW, root);
        this.box = el('div', null, null, root);
      }
      this.draw();
    }
    attributeChangedCallback() {
      if (this.box) this.draw();
    }
  }

  class Stat extends Drawn {
    static get observedAttributes() {
      return ['label', 'value', 'sub', 'trend'];
    }
    draw() {
      var b = this.box,
        label = this.getAttribute('label'),
        sub = this.getAttribute('sub'),
        trend = this.getAttribute('trend');
      b.textContent = '';
      if (label) el('div', 'l', label, b);
      var v = el('div', 'v', this.hasAttribute('value') ? this.getAttribute('value') : this.textContent.trim(), b);
      if (trend) {
        var sign = /^\s*([+−-])/.exec(trend);
        el('span', 't', sign ? (sign[1] === '+' ? '▲ ' : '▼ ') + trend.replace(/^\s*[+−-]\s*/, '') : trend, v);
      }
      if (sub) el('div', 's', sub, b);
    }
  }

  class Chart extends Drawn {
    static get observedAttributes() {
      return ['kind', 'data', 'label'];
    }
    connectedCallback() {
      super.connectedCallback();
      var self = this;
      // Drawn from its rows, it draws again when they change; a line fits its width.
      if (!this.watch && window.MutationObserver) {
        this.watch = new MutationObserver(function () {
          self.draw();
        });
        this.watch.observe(this, { childList: true, subtree: true, characterData: true });
        if (window.ResizeObserver)
          new ResizeObserver(function () {
            var now = Math.round(self.getBoundingClientRect().width);
            if (self.width && now && Math.abs(now - self.width) > 4) self.draw();
          }).observe(this);
      }
    }
    draw() {
      var b = this.box,
        label = this.getAttribute('label');
      b.textContent = '';
      this.width = 0;
      var raw = this.hasAttribute('data')
        ? this.getAttribute('data').split(/,(?!\d)/)
        : Array.prototype.map.call(this.querySelectorAll('li'), function (li) {
            return li.textContent;
          });
      var pts = raw.map(point).filter(Boolean);
      if (label) el('div', 'cap', label, b);
      if (!pts.length) return;
      var values = pts.map(function (p) {
          return p.value;
        }),
        max = Math.max.apply(null, values);
      if (this.getAttribute('kind') !== 'line') {
        var g = el('div', 'bars', null, b);
        pts.forEach(function (p) {
          el('span', 'n', p.label, g).title = p.label;
          var bar = el('i', p.value === max ? 'top' : '', null, el('span', 'k', null, g));
          bar.style.width = (max > 0 ? Math.max(0, (p.value / max) * 100) : 0) + '%';
          el('span', 'c', p.text, g);
        });
        return;
      }
      var w = (this.width = Math.max(160, Math.round(this.getBoundingClientRect().width) || 320)),
        h = parseInt(getComputedStyle(this).getPropertyValue('--tk-chart'), 10) || 160,
        top = 18,
        foot = 20,
        min = Math.min.apply(null, values),
        span = max - min || 1,
        step = pts.length > 1 ? (w - 8) / (pts.length - 1) : 0,
        xy = pts.map(function (p, i) {
          return [4 + i * step, top + (1 - (p.value - min) / span) * (h - top - foot)];
        }),
        first = pts[0],
        end = pts[pts.length - 1],
        at = xy[xy.length - 1],
        s = svg('svg', { viewBox: '0 0 ' + w + ' ' + h, height: h, role: 'img' }, b);
      s.setAttribute('aria-label', pts.map(function (p) { return p.label + ' ' + p.text; }).join(', '));
      svg('line', { class: 'base', x1: 0, x2: w, y1: h - foot + 4, y2: h - foot + 4 }, s);
      svg('path', { class: 'ln', d: 'M' + xy.map(function (q) { return q[0].toFixed(1) + ' ' + q[1].toFixed(1); }).join('L') }, s);
      svg('circle', { class: 'pt', cx: at[0], cy: at[1], r: 3 }, s);
      svg('text', { class: 'now', x: at[0], y: at[1] - 8, 'text-anchor': pts.length > 1 ? 'end' : 'start' }, s).textContent = end.text;
      svg('text', { x: 0, y: h - 2 }, s).textContent = first.label;
      if (pts.length > 1) {
        svg('text', { x: xy[0][0], y: xy[0][1] - 8 }, s).textContent = first.text;
        svg('text', { x: w, y: h - 2, 'text-anchor': 'end' }, s).textContent = end.label;
      }
    }
  }
  customElements.define('tico-stat', Stat);
  customElements.define('tico-chart', Chart);
  // The card and full views draw a line at their own heights.
  addEventListener('hashchange', function () {
    Array.prototype.forEach.call(document.querySelectorAll('tico-chart'), function (c) {
      if (c.box) c.draw();
    });
  });

  // A table's figures sit at the right, as the app's tables draw them.
  var FIGURE = /^[-+−]?[$€£]?\d[\d,]*(\.\d+)?\s*[%kKmM]?$/;
  function figures(t) {
    Array.prototype.forEach.call(t.querySelectorAll('td,th'), function (c) {
      c.toggleAttribute('data-tico-num', c.tagName === 'TD' && FIGURE.test(c.textContent.trim()));
    });
    // A heading sits over its figures: a column whose body cells are all figures (or empty).
    Array.prototype.forEach.call(t.querySelectorAll('th'), function (th) {
      var col = th.cellIndex,
        table = th.closest('table');
      var cells = table
        ? Array.prototype.filter.call(table.querySelectorAll('tr'), function (r) { return r.cells[col] && r.cells[col].tagName === 'TD'; })
        : [];
      th.toggleAttribute(
        'data-tico-num',
        cells.length > 0 && cells.every(function (r) { return r.cells[col].hasAttribute('data-tico-num') || !r.cells[col].textContent.trim(); })
      );
    });
  }
  customElements.define(
    'tico-table',
    class extends HTMLElement {
      connectedCallback() {
        var self = this;
        figures(this);
        // A table a page's own script fills after this runs is aligned once it lands.
        if (!this.watch && window.MutationObserver) {
          this.watch = new MutationObserver(function () {
            figures(self);
          });
          this.watch.observe(this, { childList: true, subtree: true, characterData: true });
        }
      }
    }
  );
  customElements.define('tico-list', class extends HTMLElement {});
  customElements.define('tico-badge', class extends HTMLElement {});
})();
