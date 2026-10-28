import * as THREE from '../vendor/three/three.module.min.js';
import { RoundedBoxGeometry } from '../vendor/three/RoundedBoxGeometry.js';
import { mergeGeometries } from '../vendor/three/BufferGeometryUtils.js';
import { EffectComposer } from '../vendor/three/EffectComposer.js';
import { RenderPass } from '../vendor/three/RenderPass.js';
import { UnrealBloomPass } from '../vendor/three/UnrealBloomPass.js';
import { OutputPass } from '../vendor/three/OutputPass.js';

// A floating island at dusk. One storey holds six desks. "campus" gives each computer its own
// building; "tower" stacks every computer's storeys into one tower. Selecting a storey brings it
// down to the ground like an elevator (storeys above fly off, storeys below sink) and shows it from
// above with name tags.
export function createBuilding(data, preferences = {}, openMember = () => {}) {
const controller = new AbortController();
const on = (target, type, fn, options = {}) => target.addEventListener(type, fn, {...options, signal: controller.signal});
const resources = new Set(), mats = new Map(), geos = new Map();
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const small = () => innerWidth < 560, portrait = () => innerWidth / innerHeight < .8;
const TOWER = preferences.layout === 'tower', PER_FLOOR = 6, H = 3.5, GROUND = .65;
const palettes = [
  {color:0x79c9b3,rug:0x8fb39b,wall:0xe6f0e2,deep:0x2f7a6a}, {color:0xf0c786,rug:0xc9b08a,wall:0xf5ecd6,deep:0xb4783a},
  {color:0xaca5d3,rug:0xa79ec2,wall:0xebe7f3,deep:0x6a5fa8}, {color:0x8fbcd8,rug:0x95b4c3,wall:0xe0eef1,deep:0x3f7ca3}
];
const hash = s => { let h = 2166136261; for (const c of String(s)) { h ^= c.codePointAt(0); h = Math.imul(h, 16777619); } return h >>> 0; };
const rand = seed => () => { seed = (seed + 0x6D2B79F5) >>> 0; let t = Math.imul(seed ^ (seed >>> 15), seed | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };

// ---------------------------------------------------------------- layout: storeys, then buildings
const FLOORS = [], BUILDINGS = [];
data.groups.forEach((group, gi) => {
  const parts = Math.max(1, Math.ceil(group.members.length / PER_FLOOR));
  for (let part = 0; part < parts; part++) FLOORS.push({id: FLOORS.length, group, gi, part, parts, key: group.key + ':' + part,
    members: group.members.slice(part * PER_FLOOR, (part + 1) * PER_FLOOR), ...palettes[gi % 4]});
});
if (TOWER) BUILDINGS.push({id: 0, x: 0, z: 0, floors: FLOORS, name: data.company, ...palettes[0]});
else {
  const n = data.groups.length, columns = small() ? Math.min(2, Math.ceil(Math.sqrt(n / 2))) : Math.min(3, Math.ceil(Math.sqrt(n))), rows = Math.ceil(n / columns);
  data.groups.forEach((group, gi) => BUILDINGS.push({id: gi, group, name: group.name, ...palettes[gi % 4],
    x: ((gi % columns) - (columns - 1) / 2) * 18, z: (Math.floor(gi / columns) - (rows - 1) / 2) * 17,
    floors: FLOORS.filter(f => f.gi === gi)}));
}
for (const b of BUILDINGS) b.floors.forEach((f, level) => Object.assign(f, {building: b, level, anim: {y: 0, s: 1, z: 0, vis: true}}));
for (const b of BUILDINGS) Object.assign(b, {anim: {s: 1, vis: true}, roofAnim: {y: 0, s: 1, vis: true}, baseAnim: {s: 1, vis: true}});
const xs = BUILDINGS.map(b => b.x), zs = BUILDINGS.map(b => b.z);
const campusWidth = Math.max(...xs) - Math.min(...xs) + (TOWER ? 20 : 18), campusDepth = Math.max(...zs) - Math.min(...zs) + (TOWER ? 16 : 17);
const maxHeight = Math.max(...BUILDINGS.map(b => b.floors.length)) * H + 3;
const state = {floor: null, t: 0, clock: 0, hover: null, selected: null, zoom: 1, yaw: .36, pitch: .42, drag: false, idle: 0};

// ---------------------------------------------------------------- renderer, sky and bloom
const renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'high-performance'});
const ratio = Math.min(devicePixelRatio, small() ? 2 : 1.6);
renderer.setPixelRatio(ratio); renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.shadowMap.autoUpdate = false; renderer.shadowMap.needsUpdate = true;
renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.2;
$('#world').replaceChildren(renderer.domElement);
renderer.domElement.setAttribute('aria-label', TOWER ? 'One tower with a storey for each computer.' : 'Campus. Each computer is a building with its bots and human collaborators.');
renderer.domElement.setAttribute('role', 'img'); renderer.domElement.setAttribute('aria-describedby', 'scene-instructions');
const scene = new THREE.Scene();
{
  // Dusk: deep teal overhead to a warm peach horizon.
  const cv = document.createElement('canvas'); cv.width = 64; cv.height = 512; const ctx = cv.getContext('2d');
  const g = ctx.createLinearGradient(0, 0, 0, 512);
  [[0, '#0c2333'], [.45, '#1d4752'], [.78, '#3f6e70'], [1, '#9c7f7a']].forEach(([s, c]) => g.addColorStop(s, c));
  ctx.fillStyle = g; ctx.fillRect(0, 0, 64, 512);
  const sky = new THREE.CanvasTexture(cv); sky.colorSpace = THREE.SRGBColorSpace; scene.background = sky; resources.add(sky);
}
const camera = new THREE.OrthographicCamera(-20, 20, 15, -15, .1, 1000);
scene.add(new THREE.HemisphereLight(0xcfe7f0, 0x6a8a74, 1.55));
const sun = new THREE.DirectionalLight(0xffdcb0, 2.2); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
sun.shadow.normalBias = .04; sun.shadow.bias = -.0002; sun.shadow.radius = 3; scene.add(sun, sun.target);
const fill = new THREE.DirectionalLight(0x8fb4ff, .9); fill.position.set(16, 14, -18); scene.add(fill);
function aimSun(cx, cy, cz, size) {
  sun.target.position.set(cx, cy, cz); sun.position.set(cx - 22, cy + 38, cz + 28);
  Object.assign(sun.shadow.camera, {left: -size, right: size, top: size, bottom: -size, near: .1, far: 170});
  sun.shadow.camera.updateProjectionMatrix(); renderer.shadowMap.needsUpdate = true;
}
let composer = null, bloom = null;
try {
  const target = new THREE.WebGLRenderTarget(1, 1, {type: THREE.HalfFloatType, samples: 4});
  composer = new EffectComposer(renderer, target); composer.setPixelRatio(ratio); composer.setSize(innerWidth, innerHeight);
  composer.addPass(new RenderPass(scene, camera));
  bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), .7, .55, 1.05); composer.addPass(bloom);
  composer.addPass(new OutputPass());
} catch (error) { composer = null; console.warn('Overview glow unavailable', error); }
const world = new THREE.Group(), landscape = new THREE.Group(), paths = new THREE.Group(); scene.add(world); world.add(landscape, paths);
const characters = [], hitMeshes = [], movers = [], clouds = [];

// ---------------------------------------------------------------- primitives
function material(color, opts = {}) { const key = String(color) + JSON.stringify(opts); if (!mats.has(key)) mats.set(key, new THREE.MeshStandardMaterial({color, roughness: .74, metalness: 0, ...opts})); return mats.get(key); }
// A light source: brighter than white, so the bloom picks it up and nothing else does.
function glow(color, k = 3) { const key = 'glow' + color + ':' + k; if (!mats.has(key)) mats.set(key, new THREE.MeshBasicMaterial({color: new THREE.Color(color).multiplyScalar(k)})); return mats.get(key); }
function mesh(g, geo, mat, x, y, z, shadow = true) { const m = new THREE.Mesh(geo, typeof mat === 'number' || typeof mat === 'string' ? material(mat) : mat); m.position.set(x, y, z); m.castShadow = shadow; m.receiveShadow = true; g.add(m); resources.add(geo); resources.add(m.material); return m; }
function box(g, x, y, z, w, h, d, c, r = .04) { const key = `b:${w}:${h}:${d}:${r}`; if (!geos.has(key)) geos.set(key, r ? new RoundedBoxGeometry(w, h, d, 2, Math.min(r, w / 3, h / 3, d / 3)) : new THREE.BoxGeometry(w, h, d)); return mesh(g, geos.get(key), c, x, y, z); }
function ball(g, x, y, z, r, c, scale, detail = 1) { const key = 'ball' + detail; if (!geos.has(key)) geos.set(key, detail ? new THREE.SphereGeometry(1, 20, 14) : new THREE.SphereGeometry(1, 10, 8)); const m = mesh(g, geos.get(key), c, x, y, z); m.scale.set(r * (scale?.[0] || 1), r * (scale?.[1] || 1), r * (scale?.[2] || 1)); return m; }
function cyl(g, x, y, z, rt, rb, h, c, sides = 16) { const key = `c:${rt}:${rb}:${h}:${sides}`; if (!geos.has(key)) geos.set(key, new THREE.CylinderGeometry(rt, rb, h, sides)); return mesh(g, geos.get(key), c, x, y, z); }
function rod(g, a, b, r, c) { const aa = new THREE.Vector3(...a), bb = new THREE.Vector3(...b), v = bb.clone().sub(aa); const m = cyl(g, ...aa.clone().add(bb).multiplyScalar(.5).toArray(), r, r, v.length(), c, 6); m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), v.normalize()); return m; }
function group(parent, x = 0, y = 0, z = 0, own = false) { const g = new THREE.Group(); g.position.set(x, y, z); if (own) g.userData.own = true; parent.add(g); return g; }
const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
function sign(g, label, x, y, z, w, h, fg, weight = 600) {
  const cw = 1024, ch = Math.max(48, Math.round(cw * h / w)), cv = document.createElement('canvas'); cv.width = cw; cv.height = ch;
  const ctx = cv.getContext('2d'); let font = Math.round(ch * .7); ctx.font = `${weight} ${font}px ${FONT}`;
  while (ctx.measureText(label).width > cw - 40 && font > 10) ctx.font = `${weight} ${--font}px ${FONT}`;
  ctx.fillStyle = fg; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(label, cw / 2, ch / 2);
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4; resources.add(tex);
  return mesh(g, new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({map: tex, transparent: true, depthWrite: false}), x, y, z, false);
}
function softTexture(stops) {
  const cv = document.createElement('canvas'); cv.width = cv.height = 128; const ctx = cv.getContext('2d');
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64); stops.forEach(([s, c]) => g.addColorStop(s, c));
  ctx.fillStyle = g; ctx.fillRect(0, 0, 128, 128); const t = new THREE.CanvasTexture(cv); resources.add(t); return t;
}
const blobMat = new THREE.MeshBasicMaterial({map: softTexture([[0, 'rgba(0,0,0,.5)'], [.55, 'rgba(0,0,0,.22)'], [1, 'rgba(0,0,0,0)']]), transparent: true, depthWrite: false});
const blushMat = new THREE.MeshBasicMaterial({color: 0xf4a4a0, transparent: true, opacity: .6});
const dotTex = softTexture([[0, 'rgba(255,255,255,1)'], [.25, 'rgba(255,255,255,.8)'], [1, 'rgba(255,255,255,0)']]);
resources.add(blobMat); resources.add(blushMat);

// ---------------------------------------------------------------- props
function plant(g, x, y, z, size = 1, pot = 0xd7a788) { const p = group(g, x, y, z); p.scale.setScalar(size); cyl(p, 0, .22, 0, .25, .2, .44, pot); cyl(p, 0, .45, 0, .2, .2, .03, 0x675c48); for (let i = 0; i < 6; i++) { const t = i * 2.4, yy = .62 + (i % 3) * .2; const l = ball(p, Math.cos(t) * .17, yy, Math.sin(t) * .17, .23, [0x8fbe75, 0x72a95f, 0xa8c986][i % 3], [.62, 1.6, .7], 0); l.rotation.z = Math.sin(t) * .6; l.rotation.x = Math.cos(t) * .6; } return p; }
function tree(g, x, y, z, s = 1, tone = 0) { const t = group(g, x, y, z); t.scale.setScalar(s); const leaf = [[0x93c187, 0xaad49a, 0x85b779], [0xeba3b9, 0xf6c3d0, 0xdc8ea7], [0xa8cf7e, 0xc0dd95, 0x92bd6b]][tone % 3]; cyl(t, 0, 1, 0, .12, .18, 2, 0x9b7a5e, 7); ball(t, 0, 2.3, 0, .88, leaf[0], [1, 1.2, 1]); ball(t, .5, 2.05, .15, .56, leaf[1]); ball(t, -.42, 2.15, -.1, .62, leaf[2]); ball(t, .1, 2.95, -.05, .52, leaf[1]); return t; }
function bush(g, x, y, z, s = 1) { for (const [dx, dz, r] of [[0, 0, .42], [.38, .1, .3], [-.34, .08, .32]]) ball(g, x + dx * s, y + r * .6 * s, z + dz * s, r * s, 0x8cc47c, [1, .8, 1], 0); }
function lamp(g, x, y, z) { cyl(g, x, y + .8, z, .04, .06, 1.6, 0x4f6f66, 8); ball(g, x, y + 1.66, z, .16, glow(0xffd9a0, 3.2), null, 0); cyl(g, x, y + 1.85, z, .02, .2, .1, 0x4f6f66, 10); }
function screen(g, x, y, z, w = 1.02) { const s = group(g, x, y, z); box(s, 0, 0, 0, w, .66, .1, 0x3f5c62, .06); box(s, 0, .015, .056, w - .1, .54, .012, glow(0xa9e0cf, 1.05), .02); box(s, 0, -.39, 0, .075, .17, .075, 0x687f78, 0); box(s, 0, -.48, .06, .4, .05, .3, 0x91a196, .02); return s; }
function desk(g, x, z, c) { const d = group(g, x, 0, z); box(d, 0, 1.03, 0, 2.5, .16, 1.25, c, .08); for (const xx of [-1.02, 1.02]) box(d, xx, .48, 0, .12, .96, 1.05, 0xcabda0, .03); screen(d, 0, 1.62, -.25); box(d, 0, 1.13, .25, .68, .045, .23, 0xd4ddca, .02); cyl(d, .92, 1.2, -.05, .095, .085, .19, 0xe59b7a, 10); return d; }
function couch(g, x, z, c) { const co = group(g, x, 0, z); box(co, 0, .34, 0, 2.55, .47, 1, c, .15); box(co, 0, .83, -.4, 2.6, .86, .26, c, .1); for (const xx of [-1.15, 1.15]) box(co, xx, .69, 0, .28, .68, 1.08, c, .09); for (const xx of [-.64, .05, .7]) box(co, xx, .61, .04, .6, .16, .72, 0xf0e4c6, .07); return co; }
function bookshelf(g, x, z, deep) { const s = group(g, x, .25, z); box(s, 0, .5, 0, 1.6, 1, .42, 0xc9a77f, .04); const r = rand(hash(x + ':' + z)); for (const y of [.3, .72]) { box(s, 0, y - .14, .02, 1.48, .03, .38, 0xb48e66, 0); let bx = -.66; while (bx < .6) { const w = .07 + r() * .07, h = .2 + r() * .1; box(s, bx + w / 2, y - .12 + h / 2, .03, w, h, .28, [0xe9826c, 0x6fb3c9, 0xf2c45f, deep, 0x9fd18c, 0xc49ae0][Math.floor(r() * 6)], .01); bx += w + .012; } } return s; }
function cooler(g, x, z) { box(g, x, .7, z, .5, .9, .5, 0xeef0ea, .08); cyl(g, x, 1.38, z, .19, .19, .5, 0x7cc4e8, 14); ball(g, x - .26, .95, z, .035, glow(0x9fe3ff, 2.4), null, 0); }
function arcade(g, x, z, deep) { const a = group(g, x, .25, z); a.rotation.y = -Math.PI / 2; box(a, 0, .75, 0, .72, 1.5, .62, deep, .06); box(a, 0, 1.12, .27, .56, .44, .06, 0x111a22, .03); box(a, 0, 1.12, .305, .48, .36, .01, glow(0xff7ad9, 1.5), .01); box(a, 0, .78, .36, .62, .08, .24, 0x2b3a45, .02); ball(a, -.13, .84, .4, .04, glow(0xffd166, 2.5), null, 0); ball(a, .1, .84, .42, .035, glow(0x7cf0c0, 2.5), null, 0); box(a, 0, 1.55, .2, .68, .14, .2, glow(0x9ff3ff, 1.6), .03); return a; }
function floorLamp(g, x, z) { cyl(g, x, .95, z, .025, .025, 1.4, 0x5c6b66, 6); cyl(g, x, .27, z, .2, .22, .04, 0x5c6b66, 14); cyl(g, x, 1.75, z, .16, .26, .3, glow(0xffe0b0, 1.6), 16); }
function beanbag(g, x, z, c) { ball(g, x, .48, z, .48, c, [1, .62, 1]); ball(g, x, .68, z - .18, .32, c, [1.1, .9, .7], 0); }
function sconce(g, x, y, z) { box(g, x, y, z, .26, .26, .04, 0x8a8f7d, .02); ball(g, x, y, z + .06, .1, glow(0xffd59a, 3), [1, 1, .6], 0); }

// ---------------------------------------------------------------- robot faces: the bot's own symbol, glowing on a CRT
const faceCache = new Map();
let symbolsFont = null, symbolsReady = false;
function neonOf(css) { const c = new THREE.Color(css), hsl = {}; c.getHSL(hsl); return new THREE.Color().setHSL(hsl.h, Math.min(1, hsl.s * .6 + .45), .64); }
function faceTexture(face) {
  const key = JSON.stringify(face); if (faceCache.has(key)) return faceCache.get(key);
  const W = 256, Hh = 192, cv = document.createElement('canvas'); cv.width = W; cv.height = Hh; const ctx = cv.getContext('2d');
  const neon = '#' + neonOf(face.color).getHexString(), tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
  let img = null;
  const draw = () => {
    const bg = ctx.createRadialGradient(W / 2, Hh / 2, 10, W / 2, Hh / 2, W * .62); bg.addColorStop(0, '#12282d'); bg.addColorStop(1, '#03090c');
    ctx.globalCompositeOperation = 'source-over'; ctx.fillStyle = bg; ctx.fillRect(0, 0, W, Hh);
    const S = 118; ctx.shadowColor = neon; ctx.fillStyle = neon;
    if (img?.complete && img.naturalWidth) {
      const tint = document.createElement('canvas'); tint.width = tint.height = S; const tc = tint.getContext('2d');
      tc.drawImage(img, 0, 0, S, S); tc.globalCompositeOperation = 'source-in'; tc.fillStyle = neon; tc.fillRect(0, 0, S, S);
      for (const blur of [30, 12, 0]) { ctx.shadowBlur = blur; ctx.drawImage(tint, (W - S) / 2, (Hh - S) / 2); }
    } else {
      // A symbol missing from the bundled subset would print as its name: such a bot shows initials.
      ctx.font = `${S}px "Tico Symbols"`;
      const glyph = face.glyph && symbolsReady && ctx.measureText(face.glyph).width < S * 1.4;
      if (!glyph) ctx.font = `800 ${face.initials.length > 1 ? 74 : 92}px ${FONT}`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      for (const blur of [30, 12, 0]) { ctx.shadowBlur = blur; ctx.fillText(glyph ? face.glyph : face.initials, W / 2, Hh / 2 + (glyph ? 0 : 4)); }
    }
    ctx.shadowBlur = 0; ctx.fillStyle = 'rgba(0,0,0,.25)'; for (let y = 0; y < Hh; y += 4) ctx.fillRect(0, y, W, 1.5);
    const shine = ctx.createLinearGradient(0, 0, W * .6, Hh * .7); shine.addColorStop(0, 'rgba(255,255,255,.14)'); shine.addColorStop(.5, 'rgba(255,255,255,0)');
    ctx.fillStyle = shine; ctx.fillRect(0, 0, W, Hh); tex.needsUpdate = true;
  };
  if (face.svg) { img = new Image(); img.decoding = 'async'; img.onload = draw; img.src = face.svg; }
  else if (face.glyph) {
    symbolsFont ||= new FontFace('Tico Symbols', 'url(/vendor/fonts/material-symbols-outlined.woff2?v=857eb582667d)').load().then(f => { document.fonts.add(f); symbolsReady = true; return f; });
    symbolsFont.then(draw, () => {});
  }
  draw(); resources.add(tex); faceCache.set(key, tex); return tex;
}

// ---------------------------------------------------------------- characters
const SKIN = [0xf1c6a2, 0xe2b08a, 0xc68b62, 0x9b6745, 0xf5d3b8], HAIR = [0x5a3f30, 0x2f2a28, 0x8a5a3a, 0xd9a35f, 0x6d4b8f, 0xc4573e];
const SHIRT = [0xf09b84, 0x86b6e0, 0xf3c46b, 0x9ccf9e, 0xc6a3e0, 0xf2a7c3];
function character(g, member, x, z, floor) {
  const {id: actorId, name, type, state: activity, detail: message, href} = member, h = hash(actorId), r = rand(h);
  const root = group(g, x, .27, z);
  const ch = {actorId, href, activity, root, name, type, message, floor, index: characters.length, wave: 0, h, head: null, arms: [], eyes: [],
    hop: null, nextHop: 2 + r() * 8, blink: 1 + r() * 4, face: 0, spinA: 0};
  const blob = mesh(root, new THREE.PlaneGeometry(1.1, 1.1), blobMat, 0, .015, 0, false); blob.rotation.x = -Math.PI / 2; blob.userData.keep = true; ch.blob = blob;
  const body = group(root); ch.body = body;
  if (type === 'bot') {
    const face = member.face || {initials: name.slice(0, 2).toUpperCase(), color: '#79c9b3'};
    const tone = new THREE.Color(face.color), shell = tone.clone().lerp(new THREE.Color(0xffffff), .38).getHex(), deep = tone.clone().lerp(new THREE.Color(0x203038), .25).getHex();
    const cream = 0xf3ead6, metal = 0x8f9ea3, neon = neonOf(face.color);
    for (const xx of [-.13, .13]) { box(body, xx, .05, .04, .17, .1, .26, 0x3d4d55, .04); cyl(body, xx, .17, 0, .055, .055, .16, metal, 8); }
    box(body, 0, .45, 0, .52, .44, .38, cream, .12);
    box(body, 0, .45, .19, .52, .1, .02, shell, .02);
    box(body, 0, .5, .2, .3, .16, .03, 0x2c3d44, .03);
    [0xff7a6b, 0xffd166, 0x6ee7b7].forEach((c, i) => ball(body, -.08 + i * .08, .52, .218, .022, glow(c, 2.2), null, 0));
    for (let i = 0; i < 3; i++) box(body, 0, .335 - i * .03, .192, .2, .012, .01, 0xb7aa92, 0);
    cyl(body, 0, .72, 0, .07, .09, .1, metal, 10);
    for (const side of [-1, 1]) {
      const arm = group(body, side * .31, .6, 0, true);
      ball(arm, 0, 0, 0, .085, shell, null, 0); cyl(arm, side * .02, -.15, 0, .04, .04, .24, metal, 8);
      const claw = mesh(arm, new THREE.TorusGeometry(.065, .022, 6, 12, Math.PI * 1.45), deep, side * .02, -.31, 0); claw.rotation.set(Math.PI / 2, 0, -Math.PI * .22 + Math.PI / 2);
      ch.arms.push(arm);
    }
    const head = group(body, 0, .78, 0, true); ch.head = head;
    box(head, 0, .3, 0, .78, .6, .58, shell, .14);
    box(head, 0, .3, .27, .64, .48, .06, 0x1d2a30, .07);
    const faceMat = new THREE.MeshBasicMaterial({map: faceTexture(face)}); ch.faceMat = faceMat;
    const facePlane = mesh(head, new THREE.PlaneGeometry(.56, .42), faceMat, 0, .3, .302, false); facePlane.userData.keep = true;
    for (const side of [-1, 1]) { const ear = cyl(head, side * .41, .3, 0, .09, .09, .07, deep, 14); ear.rotation.z = Math.PI / 2; ball(head, side * .455, .3, 0, .035, glow(neon.getHex(), 2), null, 0); }
    for (let i = 0; i < 3; i++) box(head, -.18 + i * .18, .6, -.08, .1, .025, .18, deep, .01);
    const antenna = group(head, 0, .58, 0, true); ch.antenna = antenna;
    rod(antenna, [0, 0, 0], [0, .26, 0], .016, metal);
    ch.bulbMat = new THREE.MeshBasicMaterial({color: neon.clone()}); ch.neon = neon;
    ball(antenna, 0, .3, 0, .055, ch.bulbMat, null, 0).userData.keep = true;
    root.scale.setScalar(1.2);
  } else {
    const skin = SKIN[h % 5], hair = HAIR[(h >>> 3) % 6], shirt = SHIRT[(h >>> 6) % 6];
    for (const xx of [-.1, .1]) { cyl(body, xx, .2, 0, .075, .07, .3, 0x4a5d73, 8); box(body, xx, .05, .04, .15, .09, .24, 0xf2ece0, .04); }
    box(body, 0, .5, 0, .44, .4, .3, shirt, .14);
    for (const side of [-1, 1]) { const arm = group(body, side * .25, .64, 0, true); box(arm, 0, -.12, 0, .12, .28, .13, shirt, .05); ball(arm, 0, -.29, 0, .065, skin, null, 0); ch.arms.push(arm); }
    const head = group(body, 0, .72, 0, true); ch.head = head;
    ball(head, 0, .3, 0, .33, skin, [1, .95, .95]);
    const style = (h >>> 9) % 3;
    ball(head, 0, .4, -.04, .345, hair, style === 1 ? [1.04, .78, 1.02] : [1.02, .72, 1]);
    if (style === 2) ball(head, 0, .58, -.18, .13, hair, null, 0);
    if (style === 1) for (const side of [-1, 1]) ball(head, side * .28, .18, -.06, .11, hair, [1, 1.6, 1], 0);
    for (const xx of [-.11, .11]) {
      const eye = ball(head, xx, .29, .29, .04, 0x2c2a2a, [1, 1.25, .6], 0); eye.userData.keep = true; ch.eyes.push(eye);
      ball(head, xx * 1.55, .2, .27, .045, blushMat, [1, .6, .4], 0);
    }
    const smile = mesh(head, new THREE.TorusGeometry(.045, .012, 5, 10, Math.PI), 0x8a4b45, 0, .2, .31, false); smile.rotation.z = Math.PI;
    // Some people wear headphones.
    if ((h >>> 12) % 3 === 0) {
      mesh(head, new THREE.TorusGeometry(.36, .03, 6, 18, Math.PI), 0x3b4650, 0, .3, -.02);
      for (const side of [-1, 1]) cyl(head, side * .34, .28, 0, .1, .1, .08, SHIRT[(h >>> 15) % 6], 14).rotation.z = Math.PI / 2;
    }
    root.scale.setScalar(1.12);
  }
  const halo = mesh(root, new THREE.RingGeometry(.42, .5, 32), new THREE.MeshBasicMaterial({color: new THREE.Color(0xffe6a8).multiplyScalar(2), transparent: true, opacity: 0, depthWrite: false}), 0, .03, 0, false);
  halo.rotation.x = -Math.PI / 2; ch.halo = halo; halo.userData.keep = true;
  root.traverse(o => { if (o.isMesh) o.userData.character = ch; });
  characters.push(ch); return ch;
}

// ---------------------------------------------------------------- the floating island
const islandW = campusWidth + 2, islandD = campusDepth + 1.5;
{
  box(landscape, 0, -.3, 0, islandW, .6, islandD, 0x7fb978, .5);
  box(landscape, 0, -1.05, 0, islandW - .3, 1.1, islandD - .3, 0xa47b58, .3);
  // Wildflowers and pebbles scattered on the grass, kept off the building plots and paths.
  const pads = BUILDINGS.map(b => [b.x, b.z]);
  for (let i = 0, fr = rand(31); i < 160; i++) {
    const x = (fr() - .5) * (islandW - 1.5), z = (fr() - .5) * (islandD - 1.5);
    if (pads.some(([px, pz]) => Math.abs(x - px) < 8.4 && Math.abs(z - pz) < 7.6)) continue;
    if (fr() < .8) ball(landscape, x, .05, z, .09 + fr() * .05, [0xfff4c7, 0xf7a8c4, 0xb9a6f2, 0xffd166, 0xffffff][Math.floor(fr() * 5)], null, 0).castShadow = false;
    else ball(landscape, x, .02, z, .14 + fr() * .12, 0xb8b1a0, [1, .5, 1], 0);
  }
  // Terraced rock below, narrowing to a point, with a few glowing crystals.
  const r = rand(7);
  for (let i = 0; i < 4; i++) {
    const k = 1 - (i + 1) * .2;
    box(landscape, (r() - .5) * 1.4, -2.1 - i * 1.25, (r() - .5) * 1.2, islandW * k, 1.35, islandD * k, [0x8c6a50, 0x7a5c47, 0x6c513f, 0x5e4738][i], .5).castShadow = false;
  }
  box(landscape, .4, -7.1, -.2, islandW * .12, 1.2, islandD * .12, 0x544033, .3).castShadow = false;
  for (let i = 0; i < 6; i++) {
    const a = i / 6 * Math.PI * 2 + r(), c = mesh(landscape, new THREE.OctahedronGeometry(.35 + r() * .3, 0), glow([0x7cf0d0, 0x9fd3ff, 0xf6a6e0][i % 3], 1.8), Math.cos(a) * islandW * .3, -3.3 - r() * 2.5, Math.sin(a) * islandD * .3, false);
    c.scale.y = 1.8; c.rotation.y = r() * 3;
  }
}
function pad(b, w, d) {
  const g = group(landscape, b.x, 0, b.z);
  box(g, 0, .2, 0, w, .3, d, 0x9fd08f, .25); box(g, 0, .44, .1, 13.3, .11, 9.6, 0xe0e4cf, .17);
  for (const x of [-(w / 2 - .65), w / 2 - .65]) { tree(g, x, .35, -3.3, .82, (b.id + 1) % 3); tree(g, x, .35, 2.1, .63, b.id % 3); bush(g, x, .35, -.4, 1); }
  box(g, 0, .44, 5.3, 4.4, .12, 1.4, 0xe9e4d0, .08);
  for (const x of [-2.6, 2.6]) lamp(g, x, .35, 5.3);
  return g;
}
for (const b of BUILDINGS) {
  const isle = pad(b, TOWER ? 16 : 15.8, 12.2);
  if (TOWER) for (const [x, z, s, t] of [[-9.9, -7.2, .9, 1], [9.8, -7.1, .75, 2], [-9.6, 7, .7, 2], [9.9, 6.9, .85, 1]]) tree(isle, x, 0, z, s, t);
  const room = b.room = group(world, b.x, GROUND, b.z);
  const roof = b.roof = group(room, 0, b.floors.length * H, 0, true);
  box(roof, 0, 0, -2.65, 12.6, .24, 3.1, 0xe1ead9, .12); box(roof, 0, .15, -2.65, 12.1, .08, 2.7, 0x8fbf81, .12);
  for (const x of [-4.8, .5]) for (const z of [-3.6, -1.5]) box(roof, x, 1.2, z, .1, 2.05, .1, 0xa5c3b1, .02);
  for (let j = 0; j < 2; j++) box(roof, -2.15, 2.25, -3.22 + j * 1.12, 5.6, .12, .93, material(0x2a5f70, {metalness: .45, roughness: .28}), .04);
  for (const z of [-3.25, -1.85]) { box(roof, 3.25, .32, z, 3.3, .35, .72, 0xb6c8a0, .1); for (let j = 0; j < 6; j++) ball(roof, 2 + j * .5, .65, z, .29, j % 2 ? 0x9fd283 : 0x78ba7b, [1, .65, 1], 0); }
  tree(roof, 5.1, .22, -2.5, .58, 1); plant(roof, 1.3, .22, -2.5, .75);
  box(roof, 0, .18, -1.07, 12.45, .05, .06, glow(b.color, 1.6), .02);
  // String lights along the roof garden.
  for (const x of [-6, 6]) rod(roof, [x, .2, -1.1], [x, 1.55, -1.1], .03, 0x7f8f86);
  for (let i = 0; i <= 18; i++) { const u = i / 18; ball(roof, -6 + 12 * u, 1.5 - Math.sin(u * Math.PI) * .45, -1.1, .06, glow([0xffd59a, 0xffa3c4, 0x9ff3d8][i % 3], 3), null, 0); }
  for (const f of b.floors) {
    const floor = f.groupObj = group(room, 0, f.level * H, 0, true);
    box(floor, 0, 0, 0, 12.5, .3, 8.3, 0xe4eadb, .1); box(floor, 0, .19, 0, 12, .08, 7.9, 0xe2cfa9, .035);
    for (let j = 0; j < 12; j++) box(floor, -5.7 + j * 1.03, .237, 0, .013, .008, 7.7, 0xc9bd98, 0);
    box(floor, 0, 1.8, -3.95, 12.3, 3.3, .18, f.wall, .06);
    box(floor, -6.12, 1.78, 0, .16, 3.3, 8.1, 0xd6e4d5, .035);
    for (const x of [-3.8, 0, 3.8]) { box(floor, x, 1.95, -3.81, 2.7, 1.6, .06, 0x5a8f8a, .025); box(floor, x, 1.95, -3.77, 2.57, 1.46, .012, material(0x3d6c8f, {emissive: 0x3d6c8f, emissiveIntensity: .7}), .015); }
    for (const x of [-1.9, 1.9]) sconce(floor, x, 2.45, -3.84);
    for (const x of [-6.1, 6.1]) for (const z of [-3.9, 3.9]) box(floor, x, 1.76, z, .16, 3.5, .16, 0xc5d6c0, .02);
    box(floor, 0, .18, 4.13, 12.35, .06, .07, glow(f.color, 1.5), .02);
    box(floor, 0, .248, .2, 10.7, .012, 6.2, f.rug, .12);
    plant(floor, -5.46, .26, 2.85, .7); plant(floor, 5.55, .26, -2.7, .85);
    bookshelf(floor, -4.55, -3.5, f.deep); cooler(floor, 5.4, 2.55); floorLamp(floor, -5.45, -.5);
    if (f.id % 2) beanbag(floor, 5.25, .5, f.color); else arcade(floor, 5.3, .5, f.deep);
    if (TOWER) { box(floor, -3.4, .05, 4.2, 5.4, .32, .06, 0x264b4c, .04); sign(floor, f.group.name + (f.parts > 1 ? `  ${f.part + 1}/${f.parts}` : ''), -3.4, .05, 4.235, 5.1, .26, '#f3e6be'); }
    // Every identity has a workstation. No representative sampling or fabricated teammates.
    const chars = [];
    f.members.forEach((member, index) => {
      const x = -3.65 + (index % 3) * 3.65, z = index < 3 ? 1.55 : -1.85;
      const work = group(floor, x, .25, z); work.scale.setScalar(.83);
      desk(work, 0, 0, member.type === 'human' ? 0xe6cba2 : 0xcfe2c8);
      chars.push(character(floor, member, x + .05, z + 1.02, f.id));
    });
    if (!f.members.length) { couch(floor, 0, 1, 0x98c8a8); plant(floor, -2.3, .25, 1, .9); sign(floor, 'Ready for your bots', 0, 2.8, -3.82, 5, .5, '#5e7a70'); }
    const ring = mesh(floor, new THREE.BoxGeometry(12.8, .05, 8.6), new THREE.MeshBasicMaterial({color: new THREE.Color(f.color).multiplyScalar(1.15), transparent: true, opacity: 0, depthWrite: false}), 0, .22, 0, false); ring.userData.keep = true;
    const hit = box(floor, 0, H / 2, 0, 12.4, H, 8.1, material(0xffffff, {transparent: true, opacity: 0, depthWrite: false}), 0);
    hit.userData.pickFloor = f.id; hit.userData.keep = true; hit.castShadow = false; hit.receiveShadow = false; hit.visible = false; hitMeshes.push(hit);
    Object.assign(f, {ring, chars});
  }
  // The building's name sits at its foot; it steps aside when a storey comes down.
  const base = b.base = group(room, 0, 0, 4.45, true);
  box(base, 0, .39, 0, 10.7, .77, .12, 0x264b4c, .08);
  sign(base, b.name, 0, .51, .08, 9.7, .43, '#f3e6be');
  const bots = b.floors.flatMap(f => f.members).filter(m => m.type === 'bot').length;
  sign(base, TOWER ? `${data.groups.filter(g => g.kind === 'computer').length} COMPUTERS  /  ${bots} BOTS` : b.group.kind === 'computer' ? `COMPUTER  /  ${bots} BOTS` : 'SHARED SPACE', 0, .16, .09, 4.8, .16, '#8fd6c1');
}
// Stone paths, each with a little delivery robot. They are scenery, not reported bot traffic.
function route(points, closed = false) {
  const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)), closed, 'catmullrom', .05), len = curve.getLength();
  for (let i = 0, n = Math.floor(len / .78); i < n; i++) { const p = curve.getPointAt(i / n); cyl(paths, p.x + Math.sin(i * 2.3) * .08, .06, p.z + Math.cos(i * 1.7) * .08, .3, .32, .1, 0xd8d0b8, 12).castShadow = false; }
  const bot = group(paths, 0, 0, 0, true); box(bot, 0, .34, 0, .5, .38, .5, 0xf4ecd8, .14); box(bot, 0, .42, .24, .32, .14, .04, 0x1d2a30, .03); box(bot, 0, .42, .265, .24, .05, .01, glow(0x7cf0d0, 2.6), .01);
  for (const x of [-.2, .2]) cyl(bot, x, .13, 0, .1, .1, .1, 0x3d4d55, 12).rotation.z = Math.PI / 2;
  rod(bot, [0, .53, 0], [0, .8, -.05], .012, 0x8f9ea3); ball(bot, 0, .82, -.05, .045, glow(0xffb3d1, 3), null, 0);
  bot.scale.setScalar(1.15); movers.push({root: bot, curve, phase: movers.length * .37, speed: 1.4 / len, closed});
}
if (TOWER) route([[-8.6, 0, 6.9], [8.6, 0, 6.9], [8.9, 0, -6.6], [-8.9, 0, -6.6]], true);
else for (let i = 1; i < BUILDINGS.length; i++) {
  const a = BUILDINGS[i - 1], b = BUILDINGS[i], edge = Math.max(...xs) + 8.7;
  route(a.z === b.z ? [[a.x + 2.2, 0, a.z + 6.9], [(a.x + b.x) / 2, 0, a.z + 7.1], [b.x - 2.2, 0, b.z + 6.9]]
    : [[a.x + 2.2, 0, a.z + 6.9], [edge, 0, a.z + 7.4], [edge, 0, b.z + 7], [b.x + 2.2, 0, b.z + 6.9]]);
}
// Clouds drift past below the island's edge; fireflies hover over it.
{
  const r = rand(11);
  for (let i = 0; i < 7; i++) {
    const c = group(world, (r() - .5) * (islandW + 30), -4.5 - r() * 5, (r() - .5) * (islandD + 14), true);
    const n = 3 + Math.floor(r() * 3); for (let j = 0; j < n; j++) ball(c, (j - n / 2) * 1.1, r() * .5, (r() - .5) * .8, 1 + r() * .7, material(0xf6f1ea, {emissive: 0x6a7f99, emissiveIntensity: .25}), [1.3, .7, 1], 0).castShadow = false;
    clouds.push({root: c, speed: .25 + r() * .35});
  }
}
const FIRE = 36, firePos = new Float32Array(FIRE * 3), fireSeed = [];
{ const r = rand(23); for (let i = 0; i < FIRE; i++) fireSeed.push([(r() - .5) * islandW, .8 + r() * (maxHeight * .7), (r() - .5) * islandD, r() * 6.28, .3 + r() * .5]); }
const fireGeo = new THREE.BufferGeometry(); fireGeo.setAttribute('position', new THREE.BufferAttribute(firePos, 3));
const fireflies = new THREE.Points(fireGeo, new THREE.PointsMaterial({map: dotTex, color: new THREE.Color(0xffe7a3).multiplyScalar(2.2), size: 3.6 * ratio, sizeAttenuation: false, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending}));
world.add(fireflies); resources.add(fireGeo); resources.add(fireflies.material);

// Merge opaque static parts by material. Groups flagged `own` move on their own and are merged separately.
const nested = (o, root) => { for (let p = o.parent; p && p !== root; p = p.parent) if (p.userData.own) return true; return false; };
function batchStatic(root, owner = null) {
  root.updateWorldMatrix(true, true);
  const inverse = root.matrixWorld.clone().invert(), batches = new Map(), originals = [];
  root.traverse(o => {
    if (!o.isMesh || (!owner && o.userData.character) || o.userData.keep || o.material.transparent || nested(o, root)) return;
    const key = o.material.uuid + ':' + o.castShadow;
    if (!batches.has(key)) batches.set(key, {material: o.material, cast: o.castShadow, geos: []});
    const geo = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry.clone();
    geo.applyMatrix4(new THREE.Matrix4().multiplyMatrices(inverse, o.matrixWorld));
    batches.get(key).geos.push(geo); originals.push(o);
  });
  for (const b of batches.values()) {
    const geo = mergeGeometries(b.geos, false); b.geos.forEach(g => g.dispose()); if (!geo) continue;
    const m = new THREE.Mesh(geo, b.material); m.castShadow = b.cast; m.receiveShadow = true; root.add(m); resources.add(geo);
    if (owner) m.userData.character = owner;
  }
  originals.forEach(o => o.removeFromParent());
}
for (const b of BUILDINGS) { batchStatic(b.roof); batchStatic(b.base); b.floors.forEach(f => batchStatic(f.groupObj)); }
for (const ch of characters) { if (ch.antenna) batchStatic(ch.antenna, ch); [ch.head, ...ch.arms, ch.body].forEach(p => batchStatic(p, ch)); }
batchStatic(landscape); batchStatic(paths); movers.forEach(m => batchStatic(m.root)); clouds.forEach(c => batchStatic(c.root));

// ---------------------------------------------------------------- tweens
const ease = {
  inOut: p => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2, out: p => 1 - Math.pow(1 - p, 3), in: p => p * p * p,
  back: p => { const c = 1.5; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2); }, inBack: p => { const c = 1.4; return (c + 1) * p * p * p - c * p * p; },
};
const tweens = [];
function tween(target, to, {dur = .8, curve = ease.inOut, delay = 0, start = null, done = null} = {}) {
  for (const tw of tweens) if (tw.target === target) { for (const k in to) delete tw.to[k]; if (!Object.keys(tw.to).length) tw.dead = true; }
  if (reduced.matches) { start?.(); Object.assign(target, to); done?.(); return; }
  tweens.push({target, to: {...to}, from: null, t0: state.clock + delay, dur, curve, start, done});
}
function runTweens() {
  for (let i = tweens.length - 1; i >= 0; i--) {
    const tw = tweens[i]; if (tw.dead) { tweens.splice(i, 1); continue; }
    if (state.clock < tw.t0) continue;
    if (!tw.from) { tw.start?.(); tw.from = {}; for (const k in tw.to) tw.from[k] = tw.target[k]; }
    const p = Math.min(1, (state.clock - tw.t0) / tw.dur), e = tw.curve(p);
    for (const k in tw.to) tw.target[k] = tw.from[k] + (tw.to[k] - tw.from[k]) * e;
    if (p >= 1) { tweens.splice(i, 1); tw.done?.(); }
  }
}
function applyAnims() {
  for (const b of BUILDINGS) {
    b.room.scale.setScalar(Math.max(1e-4, b.anim.s)); b.room.visible = b.anim.vis && b.anim.s > .002;
    b.roof.position.y = b.floors.length * H + b.roofAnim.y; b.roof.scale.setScalar(Math.max(1e-4, b.roofAnim.s)); b.roof.visible = b.roofAnim.vis && b.roofAnim.s > .002;
    b.base.scale.setScalar(Math.max(1e-4, b.baseAnim.s)); b.base.visible = b.baseAnim.vis && b.baseAnim.s > .002;
    for (const f of b.floors) { const a = f.anim, o = f.groupObj; o.position.set(0, f.level * H + a.y, a.z); o.scale.setScalar(Math.max(1e-4, a.s)); o.visible = a.vis && a.s > .002; }
  }
}
const hop = (ch, height = .32, delay = 0) => { ch.hop = {t0: state.clock + delay, dur: .5 + height * .4, height}; };

// ---------------------------------------------------------------- camera
const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
const cam = {x: 0, y: 3, z: 0, yaw: .36, pitch: .42, scale: 40};
let camTween = null;
function desiredView() {
  const aspect = innerWidth / innerHeight;
  if (state.floor === null) {
    const width = islandW * Math.cos(state.yaw) + islandD * Math.abs(Math.sin(state.yaw)) + 5;
    const height = (maxHeight + 5) * Math.cos(state.pitch) + islandD * Math.sin(state.pitch) + 6;
    return {x: 0, y: maxHeight * .3 - 2, z: 0, yaw: state.yaw, pitch: state.pitch, scale: Math.max(height, width / aspect) * (small() ? 1.06 : 1) / state.zoom};
  }
  const b = FLOORS[state.floor].building;
  // A portrait screen looks along the room's long side, so its short side must fit across.
  const scale = (portrait() ? Math.max(13.5, 10.4 / aspect) : Math.max(9.6, 14 / aspect)) * (small() ? 1.06 : 1.12) / state.zoom;
  return {x: b.x, y: GROUND + .9, z: b.z + .2, yaw: state.yaw, pitch: state.pitch, scale};
}
function flyCamera(dur = 1.15) { if (!reduced.matches) camTween = {from: {...cam}, t0: state.clock, dur}; }
function updateCamera(dt) {
  const d = desiredView();
  if (camTween) {
    const p = Math.min(1, (state.clock - camTween.t0) / camTween.dur), e = ease.inOut(p), f = camTween.from;
    for (const k of ['x', 'y', 'z', 'yaw', 'pitch', 'scale']) cam[k] = f[k] + (d[k] - f[k]) * e;
    cam.scale *= 1 + Math.sin(p * Math.PI) * .12;            // a gentle pull-back mid-flight
    if (p >= 1) camTween = null;
  } else {
    const k = reduced.matches ? 1 : 1 - Math.exp(-dt * 9);
    for (const key of ['x', 'y', 'z', 'yaw', 'pitch', 'scale']) cam[key] += (d[key] - cam[key]) * k;
  }
  // The campus breathes: a slow sway while nobody is touching it.
  const sway = state.floor === null && !reduced.matches ? Math.sin(state.t * .18) * .035 * Math.min(1, state.idle / 3) : 0;
  const yaw = cam.yaw + sway, distance = Math.max(90, campusWidth + campusDepth + maxHeight);
  camera.position.set(cam.x + Math.sin(yaw) * Math.cos(cam.pitch) * distance, cam.y + Math.sin(cam.pitch) * distance, cam.z + Math.cos(yaw) * Math.cos(cam.pitch) * distance);
  camera.lookAt(cam.x, cam.y, cam.z);
  const aspect = innerWidth / innerHeight; camera.left = -cam.scale * aspect / 2; camera.right = cam.scale * aspect / 2; camera.top = cam.scale / 2; camera.bottom = -cam.scale / 2; camera.updateProjectionMatrix();
}
const project = v => { const p = v.clone().project(camera); return {x: (p.x + 1) * innerWidth / 2, y: (1 - p.y) * innerHeight / 2}; };

// ---------------------------------------------------------------- views
const floorName = f => f.parts > 1 ? `${f.group.name} · ${f.part + 1} of ${f.parts}` : f.group.name;
$('#floors').replaceChildren();
for (const f of FLOORS) {
  const button = document.createElement('button'); button.className = 'scene-access'; button.dataset.floor = f.id; button.dataset.computer = f.group.key;
  button.textContent = floorName(f); button.setAttribute('aria-label', floorName(f) + ', ' + f.members.length + ' teammates'); $('#floors').append(button);
}
function mood(ch) {
  if (ch.type === 'human') return 'human';
  if (String(ch.message).startsWith('Stuck')) return 'stuck';
  if (!data.fresh) return 'unknown';
  return ['running', 'needs', 'paused', 'planned', 'restricted', 'unknown'].includes(ch.activity) ? (ch.activity === 'planned' ? 'paused' : ch.activity) : 'idle';
}
function tags() {
  $('#people').replaceChildren(); characters.forEach(c => c.tag = null);
  characters.filter(c => c.floor === state.floor).forEach((ch, i) => {
    const button = document.createElement('button'); button.className = 'tag'; button.dataset.person = ch.actorId; button.dataset.state = mood(ch); button.dataset.type = ch.type;
    button.style.animationDelay = (reduced.matches ? 0 : .8 + i * .07) + 's';
    button.innerHTML = '<i></i><span></span>'; button.lastChild.textContent = ch.name.replace(/ · Your branch$/, '');
    button.setAttribute('aria-label', ch.name + ' · ' + ch.message); button.onclick = () => sayHi(ch); ch.tag = button; $('#people').append(button);
  });
}
function setHover(id) { state.hover = id; $$('[data-floor]').forEach(b => b.classList.toggle('hovered', Number(b.dataset.floor) === id)); }
function closeSpeech() { state.selected = null; $('#speech').hidden = true; }
function subtitle(f) { const m = f.members; return m.filter(x => x.type === 'human').length + ' people · ' + m.filter(x => x.type === 'bot').length + ' bots · ' + m.filter(x => x.state === 'running').length + ' running'; }
// Storeys move like an elevator: the chosen one settles on the ground, those above lift away and
// those below sink into the island. Going back, everything returns to its place.
function stage(id, instant) {
  const F = FLOORS[id];
  const go = (target, to, opts = {}) => instant ? (opts.start?.(), Object.assign(target, to), opts.done?.()) : tween(target, to, opts);
  BUILDINGS.forEach((b, bi) => {
    if (!F) {
      const delay = .05 + bi * .07;
      go(b.anim, {s: 1}, {dur: .6, curve: ease.back, delay, start: () => b.anim.vis = true});
      go(b.baseAnim, {s: 1}, {dur: .45, curve: ease.back, delay: delay + .45, start: () => b.baseAnim.vis = true});
      b.floors.forEach(f => {
        const fromSky = f.anim.y > 2;
        go(f.anim, {y: 0, s: 1, z: 0}, {dur: .72, curve: fromSky ? ease.out : ease.back, delay: delay + f.level * .06, start: () => f.anim.vis = true});
      });
      go(b.roofAnim, {y: 0, s: 1}, {dur: .7, curve: ease.out, delay: delay + b.floors.length * .06, start: () => b.roofAnim.vis = true});
      return;
    }
    if (b !== F.building) {
      const dist = Math.hypot(b.x - F.building.x, b.z - F.building.z);
      go(b.anim, {s: 0}, {dur: .45, curve: ease.inBack, delay: dist * .006, done: () => b.anim.vis = false});
      return;
    }
    const g = -F.level * H;
    go(b.anim, {s: 1}, {dur: .3, start: () => b.anim.vis = true});
    go(b.baseAnim, {s: 0}, {dur: .35, curve: ease.inBack, done: () => b.baseAnim.vis = false});
    for (const f of b.floors) {
      const away = !f.anim.vis;
      if (f === F) go(f.anim, {y: g, s: 1, z: 0}, {dur: away ? .8 : .95, curve: away ? ease.back : ease.inOut, delay: away ? .18 : .1, start: () => f.anim.vis = true});
      else if (f.level > F.level) {
        const to = {y: g + 20 + (f.level - F.level) * 3, s: 1, z: 0};
        if (away) Object.assign(f.anim, to); else go(f.anim, to, {dur: .6, curve: ease.in, delay: (b.floors.length - f.level) * .04, done: () => f.anim.vis = false});
      } else {
        const to = {y: g - 1.5, s: 0, z: 0};
        if (away) Object.assign(f.anim, to); else go(f.anim, to, {dur: .55, curve: ease.inBack, delay: .05, done: () => f.anim.vis = false});
      }
    }
    const roofTo = {y: g + 26, s: 1};
    if (!b.roofAnim.vis) Object.assign(b.roofAnim, roofTo); else go(b.roofAnim, roofTo, {dur: .55, curve: ease.in, done: () => b.roofAnim.vis = false});
  });
}
function setFloor(id, instant = false) {
  if (id !== null && !FLOORS[id]) id = null;
  if (id === state.floor && !instant) return;
  const wasCampus = state.floor === null;
  state.floor = id; state.zoom = 1;
  state.yaw = id === null ? (small() ? .14 : .36) : (wasCampus ? (portrait() ? Math.PI / 2 : 0) : state.yaw); state.pitch = id === null ? (small() ? .58 : .42) : 1.02;
  setHover(null); closeSpeech(); $('#hover-label').hidden = true;
  characters.forEach(c => { if (c.floor !== id) { c.face = 0; c.root.rotation.y = 0; c.head?.rotation.set(0, 0, 0); } });
  $('#floors').hidden = id !== null; $('#floor-heading').hidden = id === null; $('#back').hidden = id === null; $('#people').hidden = id === null;
  document.body.classList.toggle('inside', id !== null); tags();
  $('#campus-hint').textContent = id === null ? 'Select a floor to look inside' : 'Select a teammate · Drag to turn';
  const f = FLOORS[id];
  if (f) {
    const b = f.building;
    $('#floor-title').textContent = f.group.name;
    $('#floor-number').textContent = (f.group.kind === 'computer' ? 'Computer' : 'Shared space') + (b.floors.length > 1 ? ` · Floor ${f.level + 1} of ${b.floors.length}` : '');
    $('#floor-subtitle').textContent = subtitle(f);
    $('#floor-up').disabled = f.level >= b.floors.length - 1; $('#floor-down').disabled = f.level === 0;
    $('#floor-step').hidden = b.floors.length < 2;
    aimSun(b.x, GROUND + 1, b.z, 9);
    // Everyone on the storey greets the visitor once it lands.
    if (!instant) f.chars.forEach((ch, i) => hop(ch, .3, .95 + i * .09));
  } else aimSun(0, 0, 0, Math.max(islandW, islandD, maxHeight) * .72 + 4);
  stage(id, instant);
  if (!instant) flyCamera(wasCampus !== (id === null) ? 1.2 : .9);
  preferences.floorId = f ? f.key : null;
}
$('#floor-up').onclick = () => { const f = FLOORS[state.floor]; const next = f && f.building.floors[f.level + 1]; if (next) setFloor(next.id); };
$('#floor-down').onclick = () => { const f = FLOORS[state.floor]; const next = f && f.building.floors[f.level - 1]; if (next) setFloor(next.id); };
$$('[data-floor]').forEach(b => { b.onclick = () => setFloor(+b.dataset.floor); b.onfocus = () => setHover(+b.dataset.floor); b.onblur = () => setHover(null); });
// Keyboard users return to the storey they left; a pointer click leaves focus alone.
$('#back').onclick = e => { const id = state.floor; setFloor(null); if (!e.detail) $(`[data-floor="${id}"]`)?.focus({preventScroll: true}); };
function sayHi(ch, cheer = true) {
  if (state.floor !== ch.floor) setFloor(ch.floor);
  state.selected = ch;
  if (cheer) { ch.wave = 2.4; hop(ch, .45); ch.spinA = 0; tween(ch, {spinA: Math.PI * 2}, {dur: .7, curve: ease.inOut, done: () => ch.spinA = 0}); }
  $('#speech-type').textContent = (ch.type === 'human' ? 'Person' : 'Bot') + ' · ' + FLOORS[ch.floor].group.name;
  $('#speech-name').textContent = ch.name;
  $('#speech-message').textContent = ch.type === 'human' ? (FLOORS[ch.floor].group.kind === 'commons' ? 'Human teammate. Presence is not tracked.' : 'Works with bots on this computer. Presence is not tracked.') : (!data.fresh ? 'Status is unavailable. Reconnecting…' : ch.message);
  $('#speech-open').hidden = !ch.href; $('#speech-open').onclick = () => openMember(ch.actorId); $('#speech').hidden = false;
}
$('#speech-close').onclick = closeSpeech;
function pick(ev) {
  pointer.set(ev.clientX / innerWidth * 2 - 1, -ev.clientY / innerHeight * 2 + 1); raycaster.setFromCamera(pointer, camera);
  if (state.floor === null) {
    hitMeshes.forEach(m => m.visible = true); const hits = raycaster.intersectObjects(hitMeshes, false); hitMeshes.forEach(m => m.visible = false);
    const hit = hits.find(h => FLOORS[h.object.userData.pickFloor].anim.vis); return hit ? {floor: hit.object.userData.pickFloor} : null;
  }
  const f = FLOORS[state.floor], hits = raycaster.intersectObjects(f.chars.map(c => c.root), true);
  if (hits.length) return {character: hits[0].object.userData.character};
  const room = raycaster.intersectObject(f.groupObj, true).find(h => !h.object.material?.transparent);
  return room ? {room: true} : null;
}
let down = null;
on(renderer.domElement, 'pointerdown', e => { down = {x: e.clientX, y: e.clientY, yaw: state.yaw, pitch: state.pitch}; state.drag = false; state.idle = 0; renderer.domElement.setPointerCapture(e.pointerId); });
on(renderer.domElement, 'pointermove', e => {
  state.idle = 0;
  if (down) {
    const dx = e.clientX - down.x, dy = e.clientY - down.y; if (Math.hypot(dx, dy) > 6) state.drag = true;
    if (state.drag) {
      camTween = null; const inside = state.floor !== null;
      state.yaw = inside ? down.yaw - dx * .005 : THREE.MathUtils.clamp(down.yaw - dx * .004, -.65, .85);
      state.pitch = THREE.MathUtils.clamp(down.pitch + dy * .003, inside ? .55 : .08, inside ? 1.45 : .75); $('#hover-label').hidden = true;
    }
  } else if (e.pointerType === 'mouse') {
    const hit = pick(e); if (state.floor === null) setHover(hit?.floor ?? null); renderer.domElement.style.cursor = hit?.floor !== undefined || hit?.character ? 'pointer' : 'grab';
    const label = hit?.character ? hit.character.name + ' · ' + hit.character.message : hit?.floor !== undefined ? floorName(FLOORS[hit.floor]) : null;
    $('#hover-label').hidden = !label;
    if (label) { $('#hover-label').textContent = label; $('#hover-label').style.left = THREE.MathUtils.clamp(e.clientX, 100, innerWidth - 100) + 'px'; $('#hover-label').style.top = e.clientY + 'px'; }
  }
});
on(renderer.domElement, 'pointerup', e => {
  if (down && !state.drag) { const hit = pick(e); if (hit?.floor !== undefined) setFloor(hit.floor); else if (hit?.character) sayHi(hit.character); else if (!hit && state.floor !== null && !state.selected) setFloor(null); else closeSpeech(); }
  down = null; state.drag = false;
});
on(renderer.domElement, 'pointercancel', () => { down = null; state.drag = false; });
on(renderer.domElement, 'pointerleave', () => { $('#hover-label').hidden = true; if (!down) setHover(null); });
on(renderer.domElement, 'wheel', e => { e.preventDefault(); camTween = null; state.idle = 0; state.zoom = THREE.MathUtils.clamp(state.zoom * Math.exp(-e.deltaY * .0008), .7, 2.4); }, {passive: false});
on(document, 'keydown', e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'Escape') { if (state.selected) closeSpeech(); else if (state.floor !== null) $('#back').click(); }
  if (state.floor !== null && e.key === 'ArrowUp') { e.preventDefault(); $('#floor-up').click(); }
  if (state.floor !== null && e.key === 'ArrowDown') { e.preventDefault(); $('#floor-down').click(); }
  if (/^[1-9]$/.test(e.key) && +e.key <= FLOORS.length) setFloor(+e.key - 1);
  if (e.key === '0') setFloor(null);
});
on(window, 'resize', () => { renderer.setSize(innerWidth, innerHeight); composer?.setSize(innerWidth, innerHeight); });
on(renderer.domElement, 'webglcontextlost', e => { e.preventDefault(); controller.abort(); cancelAnimationFrame(frameId); window.dispatchEvent(new Event('overview-context-lost')); });

// ---------------------------------------------------------------- links: a message or handoff between two desks
// An arc rises from one teammate to the other and a light travels along it, then the arc fades. With reduced
// motion the arc appears whole and fades without the travelling light.
const LINK_LIFE = 2.6, LINK_MAX = 8, links = [];
const LINK_COLOR = {message: 0x8ff0d4, handoff: 0xffc56b};
const linkUp = new THREE.Vector3(0, 1.9, 0);
function deskOf(id, other) {
  // A person can sit in several buildings: use the seat nearest the other end.
  const seats = characters.filter(c => c.actorId === id && FLOORS[c.floor].anim.vis && (state.floor === null || c.floor === state.floor));
  if (!other || seats.length < 2) return seats[0] || null;
  const there = other.root.getWorldPosition(new THREE.Vector3());
  return seats.reduce((a, b) => a.root.getWorldPosition(tmp).distanceTo(there) <= b.root.getWorldPosition(new THREE.Vector3()).distanceTo(there) ? a : b);
}
function dropLink(link) {
  link.group.removeFromParent(); link.geo.dispose(); link.mat.dispose(); link.dotMat.dispose();
  links.splice(links.indexOf(link), 1);
}
function link(fromId, toId, kind = 'message') {
  if (!fromId || !toId || fromId === toId) return false;
  const a0 = deskOf(fromId), b = deskOf(toId, a0), a = b ? deskOf(fromId, b) : a0;
  if (!a || !b) return false;
  world.updateWorldMatrix(true, true);
  const start = world.worldToLocal(a.root.getWorldPosition(new THREE.Vector3()).add(linkUp));
  const end = world.worldToLocal(b.root.getWorldPosition(new THREE.Vector3()).add(linkUp));
  const mid = start.clone().add(end).multiplyScalar(.5); mid.y += 1.2 + start.distanceTo(end) * .32;
  const curve = new THREE.QuadraticBezierCurve3(start, mid, end), color = LINK_COLOR[kind] || LINK_COLOR.message;
  const geo = new THREE.TubeGeometry(curve, 48, .08, 8, false);
  const mat = new THREE.MeshBasicMaterial({color: new THREE.Color(color).multiplyScalar(3), transparent: true, opacity: 0, depthWrite: false});
  const dotMat = new THREE.MeshBasicMaterial({color: new THREE.Color(color).multiplyScalar(4), transparent: true, opacity: 1, depthWrite: false});
  const group = new THREE.Group(), tube = new THREE.Mesh(geo, mat), dot = new THREE.Mesh(geos.get('ball1') || new THREE.SphereGeometry(1, 10, 8), dotMat);
  if (!geos.has('ball1')) geos.set('ball1', dot.geometry);
  dot.scale.setScalar(.22); dot.visible = !reduced.matches; group.add(tube, dot); world.add(group);
  while (links.length >= LINK_MAX) dropLink(links[0]);
  links.push({from: fromId, to: toId, kind, group, geo, mat, dotMat, dot, curve, t0: state.clock});
  if (!reduced.matches) { hop(a, .22); hop(b, .22, .5); }
  return true;
}
function animateLinks() {
  for (const l of [...links]) {
    const p = (state.clock - l.t0) / LINK_LIFE;
    if (p >= 1) { dropLink(l); continue; }
    l.mat.opacity = reduced.matches ? (p < .8 ? .85 : (1 - p) * 4.25) : Math.min(1, p * 6) * (p < .7 ? .85 : (1 - p) / .3 * .85);
    if (l.dot.visible) { const u = Math.min(1, p / .55); l.curve.getPointAt(u, l.dot.position); l.dotMat.opacity = u < 1 ? 1 : Math.max(0, 1 - (p - .55) * 4); }
  }
}

// ---------------------------------------------------------------- animation
let last = 0, frameId = 0, frames = 0, loadingTimer = 0;
const up = new THREE.Vector3(0, 1.85, 0), tmp = new THREE.Vector3(), stuckColor = new THREE.Color(0xffb547);
const facing = () => Math.atan2(Math.sin(state.yaw), Math.cos(state.yaw)) * .85;
function animateCharacter(ch, dt, t, still, inside) {
  const m = mood(ch), working = m === 'running', i = ch.index, here = state.floor === ch.floor;
  ch.halo.material.opacity += ((state.selected === ch ? .9 : 0) - ch.halo.material.opacity) * .2;
  ch.halo.scale.setScalar(1 + Math.sin(t * 4) * .05);
  if (ch.type === 'bot') {
    const dim = m === 'paused' || m === 'restricted' ? .3 : m === 'unknown' ? .55 : 1;
    // Screens flicker while working and blink off for a moment now and then.
    ch.blink -= dt; if (ch.blink < -.09) ch.blink = 2.5 + (ch.h % 50) / 10;
    const off = ch.blink < 0 && !still ? .25 : 1;
    const flicker = working && !still ? .92 + Math.sin(t * 23 + i) * .05 + Math.sin(t * 3.1 + i) * .04 : 1;
    ch.faceMat.color.setScalar(1.7 * dim * flicker * off);
    const pulse = m === 'stuck' ? (Math.sin(t * 6) > 0 ? 1 : .15) : m === 'needs' ? .55 + .45 * Math.sin(t * 2.4 + i) : working ? .6 + .4 * Math.sin(t * 5 + i) : m === 'idle' ? .75 + .25 * Math.sin(t * 1.4 + i) : .12;
    ch.bulbMat.color.copy(m === 'stuck' || m === 'needs' ? stuckColor : ch.neon).multiplyScalar(3 * (still ? 1 : pulse));
  } else {
    ch.blink -= dt; if (ch.blink < -.12) ch.blink = 2 + (ch.h % 40) / 10;
    const s = ch.blink < 0 && !still ? .12 : 1; ch.eyes.forEach(e => e.scale.y = .05 * s);
  }
  if (still) return;
  if (here) {
    ch.head.rotation.x += ((inside ? -.32 : 0) - ch.head.rotation.x) * .08;
    ch.face += ((inside ? facing() : 0) - ch.face) * .08;
    ch.head.rotation.y = ch.type === 'bot' && !working ? Math.sin(t * .6 + i * 1.7) * .3 : 0;
  }
  ch.root.rotation.y = ch.face + ch.spinA;
  // Now and then everyone does a happy little hop, with squash and stretch.
  ch.nextHop -= dt; if (ch.nextHop < 0) { ch.nextHop = 6 + (ch.h % 90) / 10; if (!ch.hop) hop(ch, working ? .18 : .24); }
  let y = working ? Math.abs(Math.sin(t * 5 + i)) * .05 : Math.sin(t * 1.6 + i) * .012, sy = 1 + (working ? 0 : Math.sin(t * 1.6 + i) * .015);
  if (ch.hop && state.clock >= ch.hop.t0) {
    const p = (state.clock - ch.hop.t0) / ch.hop.dur;
    if (p >= 1) ch.hop = null;
    else {
      const air = THREE.MathUtils.clamp((p - .15) / .7, 0, 1);
      y += Math.sin(air * Math.PI) * ch.hop.height;
      sy = p < .15 ? 1 - Math.sin(p / .15 * Math.PI) * .18 : p > .85 ? 1 - Math.sin((p - .85) / .15 * Math.PI) * .14 : 1 + Math.sin(air * Math.PI) * .1;
    }
  }
  ch.body.position.y = y; ch.body.scale.set(1 / Math.sqrt(sy), sy, 1 / Math.sqrt(sy));
  ch.blob.scale.setScalar(1 - y * .9);
  if (ch.antenna) ch.antenna.rotation.z = Math.sin(t * 7 + i) * .05 + (ch.hop ? Math.sin(t * 25) * .2 : 0);
  ch.wave = Math.max(0, ch.wave - dt);
  const [left, right] = ch.arms;
  if (right) { right.rotation.z = ch.wave > 0 ? 2.4 + Math.sin(t * 12) * .35 : working ? Math.sin(t * 9 + i) * .25 : ch.hop ? .5 : 0; right.rotation.x = working && ch.wave <= 0 ? -.6 : 0; }
  if (left) { left.rotation.z = working ? -Math.sin(t * 9 + i + 1.4) * .25 : ch.hop ? -.5 : 0; left.rotation.x = working ? -.6 : 0; }
}
function animate(now) {
  frameId = 0; if (document.hidden) { last = 0; return; } const dt = last ? Math.min(.05, (now - last) / 1000) : .016; last = now;
  // Read the live query: some iframe browsers update matches without delivering a change event.
  const still = reduced.matches;
  state.clock += dt; state.idle += dt; if (!still) state.t += dt; const t = state.t, inside = state.floor !== null;
  const moving = tweens.length > 0;
  runTweens();
  for (const f of FLOORS) {
    const lit = state.hover === f.id && !inside;
    f.anim.z += ((lit ? .55 : 0) - f.anim.z) * .2;
    f.ring.material.opacity += ((lit ? .8 : 0) - f.ring.material.opacity) * .18;
  }
  applyAnims(); updateCamera(dt);
  world.position.y = still ? 0 : Math.sin(t * .5) * (inside ? .02 : .14);
  for (const ch of characters) if (FLOORS[ch.floor].anim.vis) animateCharacter(ch, dt, t, still, inside);
  for (const m of movers) {
    const raw = t * m.speed + m.phase, pos = m.closed ? raw % 1 : 1 - Math.abs((raw % 2) - 1);   // open routes go back and forth
    m.root.position.copy(m.curve.getPointAt(pos)); m.root.position.y = .17 + Math.abs(Math.sin(t * 9 + m.phase * 10)) * .03;
    const tan = m.curve.getTangentAt(pos), back = !m.closed && (raw % 2) > 1; m.root.rotation.y = Math.atan2(tan.x, tan.z) + (back ? Math.PI : 0);
  }
  animateLinks();
  for (const c of clouds) { c.root.position.x += c.speed * dt * (still ? 0 : 1); if (c.root.position.x > islandW / 2 + 16) c.root.position.x = -islandW / 2 - 16; }
  for (let i = 0; i < FIRE; i++) { const [x, y, z, ph, sp] = fireSeed[i]; firePos[i * 3] = x + Math.sin(t * sp + ph) * 1.2; firePos[i * 3 + 1] = y + Math.sin(t * sp * 1.3 + ph * 2) * .6; firePos[i * 3 + 2] = z + Math.cos(t * sp * .8 + ph) * 1.2; }
  fireGeo.attributes.position.needsUpdate = true; fireflies.material.opacity = inside ? .3 : .9;
  if (inside) for (const ch of FLOORS[state.floor].chars) {
    if (!ch.tag) continue; const p = project(ch.root.getWorldPosition(tmp).add(up));
    ch.tag.style.transform = `translate(${p.x.toFixed(1)}px,${p.y.toFixed(1)}px) translate(-50%,-100%)`;
  }
  if (state.selected) {
    const p = project(state.selected.root.getWorldPosition(tmp).add(new THREE.Vector3(0, 2.3, 0)));
    $('#speech').style.left = THREE.MathUtils.clamp(p.x, 125, innerWidth - 125) + 'px'; $('#speech').style.top = THREE.MathUtils.clamp(p.y, 270, innerHeight - 50) + 'px';
  }
  // Shadows follow anything that moves: storeys in flight, and the people on the storey in view.
  if (moving || inside || camTween) renderer.shadowMap.needsUpdate = true;
  composer ? composer.render(dt) : renderer.render(scene, camera); frames++;
  // Expose actual render progress only to the synthetic browser check; production has no per-frame DOM writes.
  if (window.__TICO_OVERVIEW_TEST_PROBE__) Object.assign(window.__TICO_OVERVIEW_TEST_PROBE__, {frame: frames, time: t, links: links.map(l => [l.from, l.to, l.kind])});
  if (frames === 2) { $('#loading').classList.add('gone'); loadingTimer = setTimeout(() => $('#loading').hidden = true, 250); document.body.dataset.sceneReady = 'true'; }
  frameId = requestAnimationFrame(animate);
}
on(document, 'visibilitychange', () => { if (!document.hidden && !frameId) { last = 0; frameId = requestAnimationFrame(animate); } });

// ---------------------------------------------------------------- first frame
const remembered = FLOORS.findIndex(f => f.key === preferences.floorId);
setFloor(remembered >= 0 ? remembered : null, true);
Object.assign(cam, desiredView());
if (remembered < 0 && !reduced.matches) {
  // Entrance: the island rises into view, then each building stacks up storey by storey.
  cam.yaw += .45; cam.scale *= 1.3; cam.pitch += .12; flyCamera(1.6);
  landscape.position.y = -3; tween(landscape.position, {y: 0}, {dur: 1, curve: ease.out});
  BUILDINGS.forEach((b, bi) => {
    const step = Math.min(.12, .9 / b.floors.length), d0 = .25 + bi * .12;
    b.floors.forEach(f => { f.anim.vis = false; f.anim.y = 9 + f.level; tween(f.anim, {y: 0}, {dur: .55, curve: ease.back, delay: d0 + f.level * step, start: () => f.anim.vis = true}); });
    b.roofAnim.vis = false; b.roofAnim.y = 10; tween(b.roofAnim, {y: 0}, {dur: .55, curve: ease.back, delay: d0 + b.floors.length * step, start: () => b.roofAnim.vis = true});
    b.baseAnim.s = 0; tween(b.baseAnim, {s: 1}, {dur: .5, curve: ease.back, delay: d0 + b.floors.length * step + .2});
  });
  characters.forEach((ch, i) => hop(ch, .35, 1.1 + (i % 12) * .05));
}
applyAnims(); renderer.compile(scene, camera);
frameId = requestAnimationFrame(animate);
return {
  link,
  clearLinks() { while (links.length) dropLink(links[0]); },
  select(key) { const id = FLOORS.findIndex(f => f.group.key === key); setFloor(id >= 0 ? id : null); },
  update(next) {
    data = next;
    const members = new Map(next.groups.flatMap(g => g.members).map(m => [m.id, m]));
    for (const f of FLOORS) { const g = next.groups.find(x => x.key === f.group.key); if (g) f.members = g.members.slice(f.part * PER_FLOOR, (f.part + 1) * PER_FLOOR); }
    for (const ch of characters) {
      const member = members.get(ch.actorId); if (!member) continue;
      ch.activity = member.state; ch.message = member.detail; ch.href = member.href;
      if (ch.tag) { ch.tag.dataset.state = mood(ch); ch.tag.setAttribute('aria-label', ch.name + ' · ' + ch.message); }
    }
    if (state.floor !== null) $('#floor-subtitle').textContent = subtitle(FLOORS[state.floor]);
    if (state.selected) sayHi(state.selected, false);
  },
  dispose() {
    controller.abort(); clearTimeout(loadingTimer); cancelAnimationFrame(frameId); while (links.length) dropLink(links[0]);
    scene.traverse(o => { if (o.geometry) resources.add(o.geometry); if (o.material) { resources.add(o.material); if (o.material.map) resources.add(o.material.map); } });
    geos.forEach(g => resources.add(g)); mats.forEach(m => resources.add(m)); resources.forEach(r => r.dispose?.());
    composer?.dispose(); bloom?.dispose();
    renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove(); $('#people').replaceChildren();
  }
};
}
