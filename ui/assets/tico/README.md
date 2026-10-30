# Tico brand

The icon and wordmark are the ones on tico.team. Three SVGs are the source; everything else is
generated from them.

- `tico-mark.svg`: the square icon, a dark rounded tile (#172221) with an off-white "ti" and a green dot.
  The tab favicon uses it directly.
- `tico-wordmark.svg` / `tico-wordmark-reversed.svg`: the full-width "tico" logo, dark ink for light
  backgrounds and off-white for dark ones. The first-run setup and the sign-in pages show it while the
  app is still called Tico (the UI swaps in the reversed one in dark mode); a company that names its app
  sees the name as text instead.
- The dot over the "i" is the Liquid Dot's jade, flat: `#4f9c71` on light, `#56a578` reversed and on the tile.
  In the app the live dot is CSS (`ui/styles/liquid-dot.css`).
- `tico-glyph.svg`: the "ti" and dot in black, no tile. It is the assistant's avatar (a CSS mask, drawn
  white on a dark disc) and the macOS menu bar template image.

Generated, do not edit by hand:

- `tico-1024.png`: the tile inset on Apple's grid, the master for the desktop app icon (`scripts/app.sh`).
- `tico-slack.png`: full-bleed 512 px square for the Slack app's icon, uploaded by hand in the Slack app settings.
- `../favicon-32.png`, `../apple-touch-icon.png` (180 px, full-bleed), `../icon-192.png`, `../icon-512.png`,
  `../icon-maskable-512.png` (mark inside the safe zone; the manifest marks it `maskable`).
- `app/icons/*`: the desktop bundle's icons (`icon.icns`, `icon.ico` and the PNGs `tauri.conf.json` lists).

Regenerate after changing an SVG:

    bash scripts/build-brand-icons.sh

It needs `rsvg-convert` (`brew install librsvg`) and, for `icon.icns`, macOS's `iconutil`. Then rebuild the
desktop app with `scripts/app.sh build`. A per-environment desktop app can still carry its own icon: put
`icon.png` in that environment's directory (docs/environments.md).
