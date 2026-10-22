### Added
- **Visuals show without a visual address.** A company with no separate visual address now sees pictures, video and
  audio inline in chat and task conversations, served from the app's own address as media only. A visual page or an
  SVG is a card with its title, a still preview when there is one, and **Open**, which shows it in the file viewer;
  pages and SVG are never served inline from the app's own address. There is no switch: Settings › General shows one
  read-only line, "Visuals: full" or "Visuals: pictures, video and audio only (no separate visual address)".
  `TICO_VISUAL_SAME_ORIGIN` is no longer read, and a local install uses the same fallback. A `TICO_VISUAL_ORIGIN` on
  the app's own hostname, with any scheme or port, now stops the server; without it such a setup gets the fallback.
