// Built-in rules only (design-fidelity.md 3.1). Copied mock CSS keeps strict colors and weights; new files also keep
// sizes, radii, tracking, layers and spacing on the scale. `font:` belongs to type.css; elsewhere only `inherit`.
const WEIGHTS = /^var\(--w-(text|strong|title)\)$/;
const FONT = ['inherit'];
const COPIED = ['src/styles/parts.css', 'src/styles/shell.css', 'src/styles/dot.css', 'src/styles/avatar.css', 'src/styles/late.css', 'src/shell/phone/phone.css'];
const SPACE = [/^(0|auto|inherit)$/, /^((var\(--(s|pad|gap|page)-[a-z0-9-]+\)|0|auto|calc\(.*\))\s*)+$/];

export default {
  ignoreFiles: ['src/styles/tokens.css', 'src/styles/tokens-app.css'],
  rules: {
    'color-no-hex': true, // a mask's #000 gets a disable comment ending "-- mask"
    'color-named': ['never', { ignoreProperties: ['/mask/'] }],
    'function-disallowed-list': ['rgb', 'rgba', 'hsl', 'hsla', 'oklch', 'lab', 'lch'],
    'property-disallowed-list': ['text-transform', 'font-variant-caps'],
    'selector-disallowed-list': ['/\\[data-theme/'],
    'declaration-property-value-disallowed-list': { '/.*/': ['/var\\(--danger/'] },
    'declaration-property-value-allowed-list': {
      font: FONT,
      'font-weight': [WEIGHTS, 'inherit'],
      'font-size': ['/^var\\(--t-[a-z0-9-]+\\)$/', 'inherit'],
      'font-family': ['/^var\\(--font-(text|display|code)\\)$/', 'inherit'],
      'letter-spacing': ['/^var\\(--track-[a-z]+\\)$/', '0'],
      'border-radius': ['/^var\\(--r-[a-z-]+\\)$/', '50%', '0', 'inherit'],
      'z-index': ['/^var\\(--z-[a-z]+\\)$/', 'auto'],
      '/^(margin|padding|gap|row-gap|column-gap)(-.*)?$/': SPACE,
    },
  },
  overrides: [
    { files: COPIED, rules: { 'declaration-property-value-allowed-list': { font: FONT, 'font-weight': [WEIGHTS, 'inherit'] } } },
    { files: ['src/styles/type.css'], rules: { 'declaration-property-value-allowed-list': { 'font-weight': [WEIGHTS, 'inherit'] } } },
    { files: ['src/styles/avatar.css'], rules: { 'function-disallowed-list': ['rgb', 'rgba', 'hsl', 'hsla'] } },
  ],
};
