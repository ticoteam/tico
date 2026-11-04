import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildPlan, parseSrt, validateEdit } from '../edit.mjs';
import { parseSize } from '../lib.mjs';

const example = JSON.parse(readFileSync(new URL('../examples/edit.json', import.meta.url), 'utf8'));
const withAudio = () => ({ duration: 6, hasAudio: true });
const base = { size: '1920x1080', clips: [{ src: 'a.mp4', out: 6 }, { src: 'b.mp4', out: 4 }] };

test('sizes: presets and WxH, odd sizes refused', () => {
  assert.deepEqual(parseSize('9:16'), [1080, 1920]);
  assert.deepEqual(parseSize('1:1'), [1080, 1080]);
  assert.deepEqual(parseSize('1280x720'), [1280, 720]);
  assert.throws(() => parseSize('1281x720'), /even/);
  assert.throws(() => parseSize('vertical'), /bad size/);
});

test('validateEdit: shipped example is valid, bad clips are caught', () => {
  assert.deepEqual(validateEdit(example), []);
  assert.ok(validateEdit({ clips: [{ src: 'a.mp4', image: 'b.png', duration: 1 }] }).length > 0);
  assert.ok(validateEdit({ clips: [{ src: 'a.mp4', in: 5, out: 2 }] }).some((e) => /after in/.test(e)));
  assert.ok(validateEdit({ ...base, fps: 0 }).some((e) => /fps/.test(e)));
  assert.ok(validateEdit({ ...base, captions: { srt: 'a.srt', ass: 'b.ass' } }).length > 0);
});

test('crossfade: clips overlap by the fade and the total length is the sum minus the fade', () => {
  const plan = buildPlan({ ...base, clips: [{ src: 'a.mp4', out: 6 }, { src: 'b.mp4', out: 4, fade: 0.5 }] },
    { probe: withAudio, baseDir: '/x' });
  assert.match(plan.graph, /xfade=transition=fade:duration=0\.500:offset=5\.500\[vx1\]/);
  assert.match(plan.graph, /acrossfade=d=0\.500/);
  assert.equal(plan.duration, 9.5);
});

test('hard cut: fade 0 joins with concat and adds lengths', () => {
  const plan = buildPlan(base, { probe: withAudio, baseDir: '/x' });
  assert.match(plan.graph, /concat=n=2:v=1:a=0\[vc1\]/);
  assert.doesNotMatch(plan.graph, /xfade/);
  assert.equal(plan.duration, 10);
});

test('clip without an audio stream gets silence of the same length', () => {
  const plan = buildPlan(base, { probe: (f) => ({ duration: 6, hasAudio: f.endsWith('a.mp4') }), baseDir: '/x' });
  assert.match(plan.graph, /anullsrc=[^;]*atrim=duration=4\.000[^;]*\[a1\]/);
});

test('voice and music: music ducks under the voice and the mix is loudness-normalised', () => {
  const plan = buildPlan({ ...base, voice: { src: 'v.wav' }, music: { src: 'm.wav', volume: 0.2 },
    loudness: -14 }, { probe: withAudio, baseDir: '/x' });
  assert.match(plan.graph, /\[music_raw\]\[voice_key\]sidechaincompress/);
  assert.match(plan.graph, /amix=inputs=3/);
  assert.match(plan.graph, /loudnorm=I=-14/);
  assert.ok(plan.args.includes('-stream_loop'), 'music loops to fill the length');
});

test('captions: SRT is burned with a frame-scaled style and copied into the work folder', () => {
  const plan = buildPlan({ ...base, captions: { srt: 'caps/en.srt' } }, { probe: withAudio, baseDir: '/x' });
  assert.match(plan.graph, /subtitles=captions\.srt:force_style='FontName=Noto Sans,FontSize=70/);
  assert.deepEqual(plan.copies, [{ from: '/x/caps/en.srt', to: 'captions.srt' }]);
  assert.match(plan.graph, /\[vout\]/);
});

test('9:16 captions sit higher than 16:9 captions (bottom safe zone)', () => {
  const tall = buildPlan({ ...base, size: '9:16', captions: { srt: 'c.srt' } }, { probe: withAudio, baseDir: '/x' });
  const wide = buildPlan({ ...base, captions: { srt: 'c.srt' } }, { probe: withAudio, baseDir: '/x' });
  const margin = (g) => Number(/MarginV=(\d+)/.exec(g)[1]);
  assert.ok(margin(tall.graph) > margin(wide.graph));
});

test('parseSrt reads cue times and multi-line text', () => {
  const cues = parseSrt('1\n00:00:01,500 --> 00:00:03,000\nHello\nthere\n\n2\n00:01:00.250 --> 00:01:02,000\nBye\n');
  assert.deepEqual(cues.map((c) => [c.start, c.end, c.text]), [[1.5, 3, 'Hello\nthere'], [60.25, 62, 'Bye']]);
});

test('output arguments: H.264, yuv420p, faststart, length pinned', () => {
  const plan = buildPlan(example, { probe: (f) => ({ duration: 8, hasAudio: false }), baseDir: '/x', out: 'o.mp4' });
  const pairs = plan.args.join(' ');
  assert.match(pairs, /-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p/);
  assert.match(pairs, /-movflags \+faststart o\.mp4$/);
  assert.equal(plan.size[0], 1920);
});
