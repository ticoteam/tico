// Amber text in the light theme reads at AA: --wait-ink is the text colour, --wait stays for fills, dots and borders.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const styles = path.join(__dirname, '../styles');
const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
const tokens = fs.readFileSync(path.join(styles, 'tokens.css'), 'utf8');
const ink = tokens.match(/--wait-ink:(#[0-9a-f]{6})/)[1];
for (const bg of ['#ffffff', '#fbf1dc']) assert.ok(ratio(ink, bg) >= 4.5, `${ink} on ${bg}: ${ratio(ink, bg).toFixed(2)}`);
for (const file of fs.readdirSync(styles).filter((f) => f.endsWith('.css'))) {
  const css = fs.readFileSync(path.join(styles, file), 'utf8');
  assert.ok(!/(^|[^-\w])color:var\(--wait\)/.test(css), `${file}: amber text uses --wait; use --wait-ink`);
}
console.log('PASS: amber text uses --wait-ink at AA contrast');
