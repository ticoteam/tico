/* The market map's graph (ui/market-page.ts mountMarket): a canvas of who connects to whom, laid out by a small force
   simulation, drawn in the theme's colours, panned with a drag and zoomed with the wheel. A click opens the node's note. */
'use strict';

function mountGraph(canvas, rows, edges) {
  let nodes = [], byId = new Map(), links = [];
  function replace(rows, edges, parentId = null) {
    const old = byId, parent = old.get(parentId);
    nodes = rows.slice(0, 300).map((row, index) => {
      const kept = old.get(row.id);
      const angle = index * 2.399963;
      const radius = (parent ? 12 : 18) * Math.sqrt(index + 1);
      return {id: row.id, name: row.name, type: row.type, tier: row.tier,
        r: 2.4 + Math.min(5.5, Math.sqrt(row.degree || 0) * 1.7),
        x: kept?.x ?? (row.id === parentId ? 0 : (parent?.x || 0) + Math.cos(angle) * radius),
        y: kept?.y ?? (row.id === parentId ? 0 : (parent?.y || 0) + Math.sin(angle) * radius * 0.72),
        vx: 0, vy: 0, pinned: !!kept || row.id === parentId};
    });
    byId = new Map(nodes.map(node => [node.id, node]));
    links = edges.map(edge => ({a: byId.get(edge.src), b: byId.get(edge.dst), rel: edge.rel})).filter(link => link.a && link.b);
    const self = byId.get(marketSelfId());
    if (self && !old.has(self.id)) { self.x = 0; self.y = 0; }
  }
  replace(rows, edges);
  let focus = null;
  let hover = null;
  let ticks = 0, settled = 0, raf = 0;
  let running = true;
  const view = {x: 0, y: 0, k: 1};
  let drag = null;

  function step() {
    // Beyond nearby cells repulsion is negligible; the grid avoids all-pairs work.
    const grid = new Map(), cellSize = 100;
    for (const node of nodes) {
      const key = Math.floor(node.x / cellSize) + ',' + Math.floor(node.y / cellSize);
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key).push(node);
    }
    for (const node of nodes) {
      if (node.pinned) continue;
      const cx = Math.floor(node.x / cellSize), cy = Math.floor(node.y / cellSize);
      for (let x = cx - 1; x <= cx + 1; x++) for (let y = cy - 1; y <= cy + 1; y++) {
        for (const other of grid.get(x + ',' + y) || []) {
          if (other === node) continue;
          const dx = node.x - other.x, dy = node.y - other.y;
          const dist = Math.max(2, Math.hypot(dx, dy));
          const force = 420 / (dist * dist * dist);
          node.vx += dx * force; node.vy += dy * force;
        }
      }
    }
    for (const link of links) {
      let dx = link.b.x - link.a.x;
      let dy = link.b.y - link.a.y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const force = (dist - 96) * 0.012;
      dx /= dist; dy /= dist;
      link.a.vx += dx * force; link.a.vy += dy * force;
      link.b.vx -= dx * force; link.b.vy -= dy * force;
    }
    let speed = 0;
    for (const node of nodes) {
      if (node.pinned) { node.vx = 0; node.vy = 0; continue; }
      if (node.id === marketSelfId()) { node.x = 0; node.y = 0; node.vx = 0; node.vy = 0; continue; }
      node.vx -= node.x * 0.008;
      node.vy -= node.y * 0.008;
      node.vx *= 0.72; node.vy *= 0.72;
      node.x += Math.max(-12, Math.min(12, node.vx));
      node.y += Math.max(-12, Math.min(12, node.vy));
      speed = Math.max(speed, Math.abs(node.vx), Math.abs(node.vy));
    }
    ticks++;
    settled = speed < 0.08 ? settled + 1 : 0;
  }
  function invalidate() {
    if (running && !document.hidden && !raf) raf = requestAnimationFrame(draw);
  }

  function neighbors() {
    const near = new Set();
    if (!focus) return near;
    near.add(focus);
    for (const link of links) {
      if (link.a.id === focus) near.add(link.b.id);
      if (link.b.id === focus) near.add(link.a.id);
    }
    return near;
  }

  // The theme's colours (ui/styles/tokens.css), read once a frame so a theme switch repaints at once.
  let pal = {};
  function palette() {
    const cs = getComputedStyle(canvas), v = name => cs.getPropertyValue(name).trim();
    pal = {bg: v('--bg') || '#121417', ink: v('--ink') || '#e6e9ee', soft: v('--g7') || '#b6bec9', muted: v('--muted') || '#8a93a0',
      line: v('--g5') || '#5b6470', ok: v('--ok') || '#6cc48f', accent: v('--accent') || '#45c09c'};
  }
  function color(node, near) {
    if (node.id === focus || near.has(node.id)) return pal.ink;
    if (node.type === 'segment') return pal.ok;
    if (node.tier === 'core') return pal.ink;
    if (node.type === 'channel') return pal.muted;
    return pal.soft;
  }

  function draw() {
    raf = 0;
    if (!running || document.hidden) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width * ratio));
    const height = Math.max(1, Math.floor(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width; canvas.height = height;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    palette();
    ctx.fillStyle = pal.bg;
    ctx.fillRect(0, 0, rect.width, rect.height);
    if (ticks < 180 && settled < 8) step();
    const near = neighbors();
    ctx.save();
    ctx.translate(rect.width / 2 + view.x, rect.height / 2 + view.y);
    ctx.scale(view.k, view.k);
    for (const link of links) {
      const hot = focus && (link.a.id === focus || link.b.id === focus);
      ctx.beginPath();
      ctx.moveTo(link.a.x, link.a.y);
      ctx.lineTo(link.b.x, link.b.y);
      ctx.strokeStyle = hot ? pal.ink : pal.line;
      ctx.globalAlpha = hot ? 0.55 : 0.35;
      ctx.lineWidth = hot ? 1.15 : 0.8;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    for (const node of nodes) {
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.id === focus ? node.r + 1.6 : node.r, 0, Math.PI * 2);
      ctx.fillStyle = color(node, near);
      ctx.fill();
    }
    ctx.font = '12px Inter, system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    // A small graph names every node; a big one its core, the focus and its neighbours. Each label sits on a halo of
    // the background so a line under it never runs through the words.
    // Labels go in order of importance, each to the right, left, above or below its node, wherever it covers no
    // label already drawn and no other node; a minor one with no free spot is left out.
    const few = nodes.length <= 40;
    const rank = node => node.id === focus || node.id === hover ? 0 : near.has(node.id) ? 1 : node.tier === 'core' || node.id === marketSelfId() ? 2 : 3;
    const placed = [], h = 14;
    const hits = (box, self) => placed.some(o => box.l < o.r && box.r > o.l && box.t < o.b && box.b > o.t)
      || nodes.some(n => n !== self && n.x + n.r > box.l && n.x - n.r < box.r && n.y + n.r > box.t && n.y - n.r < box.b);
    ctx.lineJoin = 'round';
    for (const node of [...nodes].sort((a, b) => rank(a) - rank(b)).slice(0, 60)) {
      if (!few && rank(node) === 3) continue;
      const w = ctx.measureText(node.name).width, gap = node.r + 5;
      const spots = [[node.x + gap, node.y], [node.x - gap - w, node.y], [node.x - w / 2, node.y - gap - 4], [node.x - w / 2, node.y + gap + 4]]
        .map(([x, y]) => ({x, y, box: {l: x - 2, r: x + w + 2, t: y - h / 2, b: y + h / 2}}));
      const spot = spots.find(o => !hits(o.box, node)) || (rank(node) < 3 ? spots[0] : null);
      if (!spot) continue;
      placed.push(spot.box);
      ctx.strokeStyle = pal.bg; ctx.lineWidth = 3.5; ctx.globalAlpha = 0.9;
      ctx.strokeText(node.name, spot.x, spot.y);
      ctx.globalAlpha = 1;
      ctx.fillStyle = node.id === focus || node.id === hover ? pal.ink : pal.soft;
      ctx.fillText(node.name, spot.x, spot.y);
    }
    ctx.restore();
    if (ticks < 180 && settled < 8) invalidate();
  }
  const resize = new ResizeObserver(invalidate);
  resize.observe(canvas);
  const theme = new MutationObserver(invalidate);
  theme.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme', 'class', 'style']});
  const visibility = () => { if (document.hidden) { cancelAnimationFrame(raf); raf = 0; } else invalidate(); };
  const media = matchMedia('(prefers-color-scheme: dark)');
  media.addEventListener('change', invalidate);
  document.addEventListener('visibilitychange', visibility);
  invalidate();

  function world(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - rect.width / 2 - view.x) / view.k,
      y: (event.clientY - rect.top - rect.height / 2 - view.y) / view.k,
    };
  }
  function hit(point) {
    let found = null;
    let best = 14 / view.k;
    for (const node of nodes) {
      const dist = Math.hypot(node.x - point.x, node.y - point.y);
      if (dist < best) { best = dist; found = node; }
    }
    return found;
  }
  canvas.onmousemove = event => {
    if (drag && drag.moved) {
      view.x = drag.vx + (event.clientX - drag.x);
      view.y = drag.vy + (event.clientY - drag.y);
      invalidate();
      return;
    }
    if (drag) drag.moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 3;
    const node = hit(world(event));
    hover = node ? node.id : null;
    canvas.style.cursor = node ? 'pointer' : 'grab';
    invalidate();
  };
  canvas.onmousedown = event => {
    drag = {x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, moved: false, node: hit(world(event))};
  };
  const release = () => {
    if (!drag) return;
    const picked = drag.node;
    const moved = drag.moved;
    drag = null;
    if (!moved && picked) location.hash = marketHref(picked.id);
  };
  window.addEventListener('mouseup', release);
  const wheel = event => {
    event.preventDefault();
    const next = Math.min(2.4, Math.max(0.45, view.k * (event.deltaY < 0 ? 1.08 : 0.92)));
    view.k = next;
    invalidate();
  };
  canvas.addEventListener('wheel', wheel, {passive: false});

  return {
    update(rows, edges, id) {
      replace(rows, edges, id);
      focus = byId.has(id) ? id : null;
      const parent = byId.get(id);
      if (parent) { view.x = -parent.x * view.k; view.y = -parent.y * view.k; }
      else { view.x = 0; view.y = 0; view.k = 1; }
      ticks = 0; settled = 0; invalidate();
    },
    focus(id) { focus = byId.has(id) ? id : null; invalidate(); },
    stop() {
      running = false; cancelAnimationFrame(raf);
      resize.disconnect(); theme.disconnect();
      media.removeEventListener('change', invalidate);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('mouseup', release);
      canvas.removeEventListener('wheel', wheel);
      canvas.onmousemove = canvas.onmousedown = null;
    },
  };
}
