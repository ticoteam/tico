#!/usr/bin/env node
// Changed pixels between screenshot pairs, counted in Chromium's canvas (the browser the screenshots come from, so no
// image library is needed). A channel differing by no more than TOLERANCE counts as the same pixel, so anti-aliasing
// noise does not read as a change. scripts/release_shots.py calls it once per release:
//
//   node scripts/screenshots/compare.js pairs.json results.json
//
// pairs.json: [{"key": "...", "before": "/abs/before.png", "after": "/abs/after.png"}]
// results.json: [{"key", "width", "height", "sizeMismatch", "changed", "total", "pct"}], pct is 0-100.
// TICO_BROWSER_CHANNEL works as for index.js: empty for Playwright's Chromium (CI), 'chrome' for your own Chrome.
const fs = require('node:fs');

const TOLERANCE = 2;

// Runs in the page: both images decoded onto canvases, then compared pixel by pixel.
function compareImages({before, after, tolerance}) {
  const load = src => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('a screenshot did not decode'));
    img.src = src;
  });
  const pixels = img => {
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const context = canvas.getContext('2d', {willReadFrequently: true});
    context.drawImage(img, 0, 0);
    return context.getImageData(0, 0, canvas.width, canvas.height).data;
  };
  return Promise.all([load(before), load(after)]).then(([a, b]) => {
    const width = a.naturalWidth;
    const height = a.naturalHeight;
    const total = width * height;
    if (width !== b.naturalWidth || height !== b.naturalHeight) {
      return {width, height, sizeMismatch: true, changed: total, total, pct: 100};
    }
    const da = pixels(a);
    const db = pixels(b);
    let changed = 0;
    for (let i = 0; i < da.length; i += 4) {
      if (Math.abs(da[i] - db[i]) > tolerance || Math.abs(da[i + 1] - db[i + 1]) > tolerance
        || Math.abs(da[i + 2] - db[i + 2]) > tolerance || Math.abs(da[i + 3] - db[i + 3]) > tolerance) {
        changed++;
      }
    }
    return {width, height, sizeMismatch: false, changed, total, pct: total ? (100 * changed) / total : 0};
  });
}

async function main() {
  const [pairsFile, resultsFile] = process.argv.slice(2);
  if (!pairsFile || !resultsFile) throw new Error('usage: compare.js pairs.json results.json');
  const pairs = JSON.parse(fs.readFileSync(pairsFile, 'utf8'));
  const {chromium} = require('playwright');
  const channel = process.env.TICO_BROWSER_CHANNEL ?? 'chrome';
  const browser = await chromium.launch({channel: channel || undefined, headless: true});
  const results = [];
  try {
    const page = await browser.newPage();
    await page.setContent('<!doctype html><title>compare</title>');
    for (const pair of pairs) {
      const [before, after] = [pair.before, pair.after].map(file => 'data:image/png;base64,' + fs.readFileSync(file).toString('base64'));
      const result = await page.evaluate(compareImages, {before, after, tolerance: TOLERANCE});
      results.push({key: pair.key, ...result});
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(resultsFile, JSON.stringify(results, null, 2) + '\n');
}

main().catch(error => { console.error(error); process.exit(1); });
