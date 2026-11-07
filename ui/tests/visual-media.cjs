// Video and audio cards in a conversation (Visual replies 3/4): ui/app/visual-media.js inside the real card frame
// (visual-cards.js), on a bare page. Neither starts by itself (preload metadata, no autoplay, the isolated address, never a blob or the signed-in file
// URL); seeking far into a long clip asks for that part by range instead of the whole file; starting one clip pauses
// the other; an address that expires mid-play is swapped for the card's newest one at the same position, still
// playing; a playing clip keeps playing through a thread redraw (visualSwap); a type the device can't play, or a clip
// it can't decode, shows the reason and Download; a portrait video fits under the cap instead of being cropped.
// Every request is intercepted; the clips are WAV files made here, so the test needs no media files.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// A mono 8 kHz 16-bit WAV of `seconds` of a quiet tone: 16 KB a second.
function wav(seconds) {
  const rate = 8000, n = rate * seconds, data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(800 * Math.sin(i / 8)), i * 2);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8); h.write('fmt ', 12);
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24);
  h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}
const LONG = wav(120);                          // ~1.9 MB, two minutes
const SHORT = wav(20);
const CHUNK = 64 * 1024;                        // the stand-in server answers at most this much per request

const APP = 'https://app.test', VIS = 'https://visuals.test';
const UI = f => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true,
    args: ['--autoplay-policy=no-user-gesture-required']});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, serviceWorkers: 'block'});
    const errors = [], served = [], expired = new Set(), hold = new Set(), holding = [];
    page.on('pageerror', e => errors.push(e.message));
    const bodies = {'long-1': LONG, 'long-2': LONG, 'short-1': SHORT, 'bad-1': Buffer.from('not a wav file at all'.repeat(40)),
      'short-2': SHORT, 'video-1': SHORT, 'tall-1': SHORT, 'race-1': SHORT, 'race-2': SHORT, 'zero-1': LONG, 'zero-2': LONG,
      'still-1': LONG, 'still-2': LONG};     // a video element plays a sound-only clip; enough for its controls and seeking
    await page.route(`${VIS}/**`, route => {
      const req = route.request(), token = new URL(req.url()).pathname.split('/').pop(), body = bodies[token];
      const range = req.headers().range || '';
      const asked = {token, range};
      served.push(asked);
      const answer = () => {
        if (!body || expired.has(token)) return route.fulfill({status: 403, body: ''});
        const m = /bytes=(\d+)-(\d*)/.exec(range);
        if (!m) return route.fulfill({status: 200, headers: {'content-type': 'audio/wav', 'accept-ranges': 'bytes'}, body});
        const start = Number(m[1]), end = Math.min(m[2] ? Number(m[2]) : body.length - 1, start + CHUNK - 1, body.length - 1);
        asked.bytes = end - start + 1;
        return route.fulfill({status: 206, body: body.subarray(start, end + 1), headers: {'content-type': 'audio/wav',
          'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${body.length}`}});
      };
      if (hold.has(token)) return void holding.push(answer);     // answered when the test releases it
      return answer();
    });
    const signedIn = [];        // anything the cards ask of the app's own (signed-in) file addresses
    await page.route(`${APP}/**`, route => (new URL(route.request().url()).pathname.startsWith('/api/') && signedIn.push(route.request().url()), route.fulfill({status: 200, contentType: 'text/html', body: '<!doctype html><main id="thread"></main>'})));
    await page.goto(`${APP}/chat`);
    // The real frame and players, on a page with only what they read from the rest of the app (core.js's esc, API).
    await page.addStyleTag({content: `:root{--line:#ccc;--surface:#fff;--surface2:#eee;--muted:#666} ${UI('styles/task-files.css')} ${UI('styles/task-modal.css')} ${UI('styles/visual-cards.css')} ${UI('styles/visual-media.css')}`});
    await page.addScriptTag({content: `'use strict';
      const API = '/api';
      const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]);`});
    await page.addScriptTag({content: UI('app/visual-cards.js')});
    await page.addScriptTag({content: UI('app/visual-media.js')});
    // A thread drawn the way chat and task comments draw it: every message's cards, swapped in whole (visualSwap).
    await page.addScriptTag({content: `
      window.messages = [];
      window.draw = () => visualSwap(document.getElementById('thread'), messages.map(m =>
        '<div class="msg" data-m="' + m.id + '">' + visualCardsHTML(m.files, {key: m.id, chips: rest => rest.map(f => '<a class="pill">' + esc(f.name) + '</a>').join('')}) + '</div>').join(''));
      window.card = (key, f) => { const [id] = key.split(':'); messages.push({id, files: [f]}); draw(); return document.querySelector('[data-visual-key="' + key + '"]'); };`});
    assert.deepEqual(await page.evaluate(() => [typeof VISUAL_BODY.video, typeof VISUAL_BODY.audio, typeof VISUAL_WIRE.audio]),
      ['function', 'function', 'function'], 'video and audio register with the frame');

    const file = (id, extra) => ({file_id: id, version: 1, name: `${id}.wav`, title: `Clip ${id}`, kind: 'audio',
      content_type: 'audio/wav', size: LONG.length, url: `/api/v2/files/${id}?v=1`, src: `${VIS}/visuals/${id}-1`, ...extra});
    await page.evaluate(([a, b, v]) => { card('m1:long@1', a); card('m1b:short@1', b); card('m2:v@1', v); },
      [file('long', {src: `${VIS}/visuals/long-1`}), file('short', {src: `${VIS}/visuals/short-1`, size: SHORT.length}),
       {file_id: 'v', version: 1, name: 'cut.mp4', title: 'The cut', kind: 'video', content_type: 'video/mp4', width: 1280, height: 720,
        url: '/api/v2/files/v?v=1', src: `${VIS}/visuals/video-1`, poster_url: `${VIS}/visuals/video-1-poster`}]);

    // ---- test 12: nothing plays by itself; the players are the task-files ones, on the isolated address
    const long = page.locator('[data-visual-key="m1:long@1"] audio'), short = page.locator('[data-visual-key="m1b:short@1"] audio');
    await long.evaluate(m => m.readyState >= 1 || new Promise(r => m.addEventListener('loadedmetadata', r, {once: true})));
    for (const sel of ['audio', 'video']) {
      for (const m of await page.locator(`.vcard ${sel}`).all()) {
        assert.equal(await m.getAttribute('autoplay'), null, `${sel} has no autoplay`);
        assert.equal(await m.getAttribute('preload'), 'metadata');
        assert.equal(await m.getAttribute('controls'), '');
        assert.match(await m.getAttribute('src'), /^https:\/\/visuals\.test\/visuals\/[\w-]+$/, `${sel} loads from the isolated address`);
      }
    }
    const video = page.locator('.vcard video');
    {
      assert.equal(await video.count(), 1, 'an MP4 the browser may play gets a player');
      assert.equal(await video.getAttribute('playsinline'), '');
      assert.equal(await video.getAttribute('poster'), `${VIS}/visuals/video-1-poster`);
      assert.match(await video.evaluate(m => m.parentElement.getAttribute('style')), /^aspect-ratio:1280\/720;/, 'the card holds the video\'s shape');
    }
    await page.waitForTimeout(500);
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('audio, video')].map(m => m.paused)),
      (await page.locator('audio, video').all()).map(() => true), 'nothing is playing after the page settles');
    assert.ok(served.filter(s => !s.token.endsWith('-poster')).every(s => s.range), 'every media request is a range request');

    // ---- test 13: seeking far into the long clip asks for that part, never the whole file
    await long.evaluate(m => m.play());
    await long.evaluate(m => new Promise(r => { m.addEventListener('seeked', r, {once: true}); m.currentTime = 90; }));
    const asked = served.filter(s => s.token === 'long-1');
    const from = asked.map(s => Number((/bytes=(\d+)/.exec(s.range) || [])[1] || 0));
    assert.ok(from.some(n => n > LONG.length / 2), `a range request starts past the middle (${from.join(', ')})`);
    const fetched = asked.reduce((sum, s) => sum + (s.bytes || 0), 0);
    assert.ok(fetched < LONG.length / 2, `seeking fetched ${fetched} of ${LONG.length} bytes, not the whole file`);
    assert.equal(await long.evaluate(m => m.paused), false);

    // ---- test 14: starting another clip pauses the first
    await short.evaluate(m => m.play());
    await page.waitForFunction(() => document.querySelector('[data-visual-key="m1:long@1"] audio').paused);
    assert.equal(await short.evaluate(m => m.paused), false, 'the clip just started plays');
    // ---- test 14: the thread redraws (a poll, a new message) while it plays: the same player keeps playing, in place,
    // where it was. Both ways the frame redraws: moving the card in place (moveBefore), and where a browser has no
    // moveBefore (Safari, the desktop app's webviews), taking it out and putting it back.
    await short.evaluate(m => { m.dataset.mark = 'kept'; });
    assert.equal(await page.evaluate(() => typeof Element.prototype.moveBefore), 'function', 'this browser moves nodes in place');
    for (const [way, at] of [['moveBefore', 8], ['no moveBefore', 14]]) {
      await short.evaluate((m, at) => new Promise(r => { m.addEventListener('seeked', r, {once: true}); m.currentTime = at; }), at);
      const kept = await page.evaluate(([way, n]) => {
        const mb = Element.prototype.moveBefore;
        if (way === 'no moveBefore') delete Element.prototype.moveBefore;
        try { messages.push({id: 'm9' + n, files: [{file_id: 'x' + n, version: 1, name: 'notes.txt', kind: 'file'}]}); draw(); }
        finally { Element.prototype.moveBefore = mb; }
        const m = document.querySelector('[data-visual-key="m1b:short@1"] audio');
        return [m.dataset.mark, m.currentTime, m.paused];
      }, [way, at]);
      assert.equal(kept[0], 'kept', `${way}: the redraw kept the playing audio element`);
      assert.ok(kept[1] >= at && !kept[2], `${way}: right after the redraw it is at ${kept[1]}s (not before ${at}s) and playing`);
      await page.waitForTimeout(400);
      const later = await short.evaluate(m => [m.dataset.mark, m.currentTime, m.paused]);
      assert.ok(later[0] === 'kept' && later[1] > kept[1] && !later[2], `${way}: it plays on from ${kept[1]}s (${later})`);
    }
    await short.evaluate(m => m.pause());

    // ---- an address that runs out mid-play: the card's newest address takes over, same place, still playing
    await long.evaluate(m => m.play());
    await page.evaluate(() => { messages[0].files[0].src = 'https://visuals.test/visuals/long-2'; draw(); });   // the next read's address
    assert.equal(await page.locator('[data-visual-key="m1:long@1"]').getAttribute('data-fresh-src'), 'https://visuals.test/visuals/long-2');
    assert.equal(await long.evaluate(m => m.paused), false, 'the redraw did not stop it');
    expired.add('long-1');
    await long.evaluate(m => new Promise(r => { m.addEventListener('seeking', r, {once: true}); m.currentTime = 30; }));
    await page.waitForFunction(() => document.querySelector('[data-visual-key="m1:long@1"] audio')?.src.endsWith('/long-2'), null, {timeout: 10000});
    await page.waitForFunction(() => { const m = document.querySelector('[data-visual-key="m1:long@1"] audio'); return m.readyState >= 1 && !m.paused && m.currentTime >= 29; }, null, {timeout: 10000});
    assert.ok(served.some(s => s.token === 'long-2'), 'the new address was asked');
    assert.equal(await page.locator('[data-visual-key="m1:long@1"]').getAttribute('data-state'), 'ready', 'no failed card while a newer address works');
    await long.evaluate(m => m.pause());
    // the same when the address runs out with nothing pending: the player itself goes back to where it was
    await short.evaluate(m => m.play());
    await short.evaluate(m => new Promise(r => { m.addEventListener('seeked', r, {once: true}); m.currentTime = 12; }));
    await page.evaluate(() => { messages[1].files[0].src = 'https://visuals.test/visuals/short-2'; draw(); });
    await short.evaluate(m => m.dispatchEvent(new Event('error')));
    await page.waitForFunction(() => { const m = document.querySelector('[data-visual-key="m1b:short@1"] audio');
      return m.src.endsWith('/short-2') && m.readyState >= 1 && !m.paused && m.currentTime >= 11.5; }, null, {timeout: 10000});
    await short.evaluate(m => m.pause());

    // ---- recovery never overrules the person: a clip reloading from its newer address stays paused when another
    // clip was started meanwhile (the newer address answers only after the other clip is playing)
    await page.evaluate(f => card('m6:race@1', f) && true, file('race', {src: `${VIS}/visuals/race-1`, size: SHORT.length}));
    const raced = page.locator('[data-visual-key="m6:race@1"] audio');
    await raced.evaluate(m => m.readyState >= 1 || new Promise(r => m.addEventListener('loadedmetadata', r, {once: true})));
    await raced.evaluate(m => m.play());
    await page.evaluate(() => { messages.find(m => m.id === 'm6').files[0].src = 'https://visuals.test/visuals/race-2'; draw(); });
    hold.add('race-2');
    await raced.evaluate(m => m.dispatchEvent(new Event('error')));
    await page.waitForFunction(() => document.querySelector('[data-visual-key="m6:race@1"] audio').src.endsWith('/race-2'));
    for (let i = 0; !holding.length && i < 100; i++) await page.waitForTimeout(50);
    assert.ok(holding.length, 'the newer address was asked and is held');
    await short.evaluate(m => m.play());
    hold.delete('race-2');
    holding.splice(0).forEach(answer => answer());
    await raced.evaluate(m => m.readyState >= 1 || new Promise(r => m.addEventListener('loadedmetadata', r, {once: true})));
    await page.waitForTimeout(300);
    assert.deepEqual(await page.evaluate(() => [document.querySelector('[data-visual-key="m6:race@1"] audio').paused,
      document.querySelector('[data-visual-key="m1b:short@1"] audio').paused]), [true, false], 'the clip started last keeps playing');
    await short.evaluate(m => m.pause());
    // ---- a seek back to the start is a place too: an address that runs out on that seek recovers at the start,
    // playing if it was, paused if it was
    for (const [key, id, playing] of [['m7:zero@1', 'zero', true], ['m8:still@1', 'still', false]]) {
      await page.evaluate(([k, f]) => card(k, f) && true, [key, file(id, {src: `${VIS}/visuals/${id}-1`})]);
      const clip = page.locator(`[data-visual-key="${key}"] audio`);
      await clip.evaluate(m => m.readyState >= 1 || new Promise(r => m.addEventListener('loadedmetadata', r, {once: true})));
      await page.evaluate(([k, id]) => { messages.find(m => m.id === k.split(':')[0]).files[0].src = `https://visuals.test/visuals/${id}-2`; draw(); }, [key, id]);
      if (playing) await clip.evaluate(m => m.play());
      await clip.evaluate(m => new Promise(r => { m.addEventListener('seeked', r, {once: true}); m.currentTime = 40; }));
      await page.waitForTimeout(300);
      // the old address fails on the seek itself, before the clip has shown any time past the start
      await clip.evaluate(m => { m.addEventListener('seeking', () => m.dispatchEvent(new Event('error')), {once: true}); m.currentTime = 0; });
      await page.waitForFunction(id => { const m = [...document.querySelectorAll('audio')].find(a => a.src.endsWith(`/${id}-2`)); return m && m.readyState >= 1; }, id);
      await page.waitForTimeout(300);
      const [paused, at] = await clip.evaluate(m => [m.paused, m.currentTime]);
      assert.equal(paused, !playing, `the ${playing ? 'playing' : 'paused'} clip is ${playing ? 'still playing' : 'still paused'}`);
      assert.ok(at < 2, `the ${playing ? 'playing' : 'paused'} clip came back at the start, not at ${at}s`);
      if (playing) await clip.evaluate(m => m.pause());
    }

    // ---- test 24: an address that runs out with no newer one shows the reason and Download (never a broken player)
    expired.add('long-2');
    await long.evaluate(m => { m.currentTime = 110; });
    await page.locator('[data-visual-key="m1:long@1"][data-state="failed"]').waitFor({timeout: 10000});
    assert.match(await page.locator('[data-visual-key="m1:long@1"] .vcard-note').textContent(), /^Couldn't load this audio\. Check the connection, or download it\./);
    assert.equal(await page.locator('[data-visual-key="m1:long@1"] .vcard-note a[download]').getAttribute('href'), '/api/v2/files/long?v=1', 'Download is the signed-in download');

    // ---- test 24: a clip the device can't decode, and a type it can't play at all
    await page.evaluate(f => card('m3:bad@1', f), file('bad', {src: `${VIS}/visuals/bad-1`}));
    await page.locator('[data-visual-key="m3:bad@1"][data-state="failed"]').waitFor({timeout: 10000});
    assert.match(await page.locator('[data-visual-key="m3:bad@1"] .vcard-note').textContent(), /^This device can't play WAV audio\. Open in file viewer\s*Download$/);
    await page.evaluate(() => card('m4:odd@2', {file_id: 'odd', version: 2, name: 'old.mov', title: 'Old', kind: 'video',
      content_type: 'video/quicktime', url: '/api/v2/files/odd?v=2', src: 'https://visuals.test/visuals/odd-2'}));
    assert.equal(await page.locator('[data-visual-key="m4:odd@2"] video').count(), 0, 'no player for a type the device can\'t play');
    assert.match(await page.locator('[data-visual-key="m4:odd@2"] .vcard-note').textContent(), /^This device can't play MOV video\./);
    assert.equal(await page.locator('[data-visual-key="m4:odd@2"] .vcard-note a[download]').getAttribute('download'), 'old.mov');
    assert.ok(!served.some(s => s.token === 'odd-2'), 'nothing is fetched for a type the device can\'t play');

    // ---- test 6: a portrait video is narrower, never taller than the cap or cropped; nothing scrolls sideways
    await page.evaluate(() => card('m5:tall@1', {file_id: 'tall', version: 1, name: 'tall.mp4', title: 'Phone clip', kind: 'video',
      content_type: 'video/mp4', width: 1080, height: 1920, url: '/api/v2/files/tall?v=1', src: 'https://visuals.test/visuals/tall-1',
      poster_url: '/api/v2/files/tall/poster?v=1'}));     // a poster that is not on the isolated address is left out
    assert.equal(await page.locator('[data-visual-key="m5:tall@1"] video').getAttribute('poster'), null);
    const box = await page.locator('[data-visual-key="m5:tall@1"] .vcard-body').boundingBox();
    const cap = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--vcard-cap')) / 100 * innerHeight || 480);
    {
      assert.equal(await page.locator('[data-visual-key="m5:tall@1"] video').count(), 1);
      assert.ok(box.height <= cap + 1, `the portrait video's card body is ${box.height}px, under the ${cap}px cap`);
      const clip = await page.locator('[data-visual-key="m5:tall@1"] .tf-media').boundingBox();
      assert.ok(Math.abs(clip.width / clip.height - 1080 / 1920) < 0.02 && clip.height <= box.height + 1, 'it keeps its shape, inside the card');
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scroll at phone width');

    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#thread video, #thread audio, #thread img')]
      .flatMap(m => [m.src, m.poster].filter(u => u && !/^https:\/\/visuals\.test\/visuals\//.test(u)))), [], 'media load only from the isolated address (no blob:, no /api)');
    assert.deepEqual(signedIn, [], 'no card media is asked of the signed-in /api file address');
    assert.deepEqual(errors, []);
    console.log('visual-media: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
